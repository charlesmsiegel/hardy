"""Paper values, identifiers and metadata parsing; no acquisition or writes."""
from __future__ import annotations

import hashlib
import re
import textwrap
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ..domain import FrozenModel

ENDPOINT = "https://export.arxiv.org/api/query"
#: Where a paper's source bundle comes from. A different service from the
#: API -- it answers bytes rather than a feed -- but the same caller, so it
#: shares the one request interval below.
SOURCE_ENDPOINT = "https://export.arxiv.org/e-print/"
#: arXiv's own request: one call every three seconds from a given caller.
MIN_INTERVAL_SECONDS = 3.0
#: How long a cache write or a conditional drop waits for the key it touches.
#: Short, because what the lock covers is a comparison and one filesystem
#: call: a wait this long means the holder died mid-write, and going ahead
#: unlocked is better than refusing to cache an answer already in hand.
LOCK_SECONDS = 5.0
#: How long a cached search stays an answer. A day, because arXiv publishes
#: once a day: a shorter window spends requests to learn nothing, and a longer
#: one hides a paper that has since appeared.
QUERY_TTL_SECONDS = 24 * 60 * 60
#: A single API response. Generous for an Atom feed of fifty entries and far
#: under what a compressed bomb would need to matter.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
#: A source bundle, which is a different order of thing from a feed: a paper
#: with figures runs to tens of megabytes and is not misbehaving. This bounds
#: the *download*; what the archive may inflate to once unpacked is
#: `archives.Limits`, which is the bound that matters against a bomb.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RESULTS = 50
#: Where a stored abstract is wrapped. Any fixed width would do; what matters
#: is that no line is longer than a bounded read can return whole, so paging
#: through a record can always reach the end of it.
ABSTRACT_COLUMNS = 96
# arXiv asks that a caller identify itself. A version and a project URL is
# what lets them tell Hardy's traffic apart from a scraper's and complain to
# somebody rather than block a subnet.
USER_AGENT = "Hardy/0.1 (+https://github.com/charlesmsiegel/hardy)"

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"

# `2401.12345`, with or without a version, and the pre-2007 `math.GT/0211159`
# spelling that a citation of an older paper still uses.
NEW_STYLE = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?$")
OLD_STYLE = re.compile(r"([a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v(\d+))?$")
# What a human pastes: an abs or pdf URL, or the `arXiv:` prefix from a
# bibliography entry.
STRIP = re.compile(r"^(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/|arxiv:)", re.IGNORECASE)


class ArxivError(RuntimeError):
    """A request Hardy will not make, or an answer it will not read.

    One type for both because a caller does one thing with either: report it.
    Which of the two it was is in the sentence.
    """


@dataclass(frozen=True)
class ArxivId:
    """An arXiv identifier, with its version separated from its stem."""

    stem: str
    version: int | None = None

    def __str__(self) -> str:
        return self.stem if self.version is None else f"{self.stem}v{self.version}"

    @property
    def versioned(self) -> bool:
        return self.version is not None

    @property
    def storage_name(self) -> str:
        """A single directory name for this identifier.

        The pre-2007 spelling carries a `/`, so it cannot be a path component
        as it stands, and turning it into a nested directory would put the
        archive name (`math`) in a position where two records could collide
        with a third thing. One flat name, one record.
        """
        return str(self).replace("/", "_")


def parse_id(raw: str) -> ArxivId:
    """The identifier `raw` denotes, however it was spelled, or a refusal."""
    text = STRIP.sub("", str(raw).strip())
    text = text.removesuffix(".pdf")
    for pattern in (NEW_STYLE, OLD_STYLE):
        found = pattern.fullmatch(text)
        if found:
            return ArxivId(found.group(1), int(found.group(2)) if found.group(2) else None)
    raise ArxivError(
        f"{raw!r} is not an arXiv identifier; expected something like 2401.12345v2 "
        "or math.GT/0211159v1"
    )


class PaperRecord(FrozenModel):
    """One paper, at one version, as it was when Hardy read it.

    `arxiv_id` is always versioned: a record under a bare stem would be a
    record of a moving target, which is the one thing this file exists to
    stop. `content_sha256` is the digest of `content.txt` -- the bytes
    `read_paper` serves and `cite_paper` vouches for -- so a bibliography
    entry carrying it is a claim someone else can check. What it covers
    includes where the bytes came from and when: see `content`.
    """

    schema_version: Literal[1] = 1
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    categories: tuple[str, ...] = ()
    primary_category: str = ""
    published: str = ""
    updated: str = ""
    doi: str | None = None
    journal_ref: str | None = None
    abs_url: str = ""
    #: What Hardy asked for, so the record says where it came from.
    source_url: str = ""
    fetched_at: str = ""
    content_sha256: str = ""
    content_bytes: int = 0
    #: The digest of the untouched API response the record was read out of.
    #: Not the same claim as `content_sha256`: this one says which bytes were
    #: parsed, and survives a change in how they are rendered.
    response_sha256: str = ""

    @property
    def identifier(self) -> ArxivId:
        return parse_id(self.arxiv_id)

    def content(self) -> str:
        """The text this record vouches for, rendered from its own fields."""
        head = [
            f"arXiv:{self.arxiv_id}",
            *_wrapped(f"Title: {self.title}"),
            *_wrapped("Authors: " + ", ".join(self.authors)),
        ]
        if self.categories:
            head.extend(_wrapped(f"Categories: {', '.join(self.categories)}"))
        if self.published:
            head.append(f"Submitted: {self.published}")
        if self.updated and self.updated != self.published:
            head.append(f"This version: {self.updated}")
        if self.doi:
            head.extend(_wrapped(f"DOI: {self.doi}"))
        if self.journal_ref:
            head.extend(_wrapped(f"Journal reference: {self.journal_ref}"))
        # Provenance goes INSIDE the digested text, not merely beside it in
        # `record.json`. `content_sha256` is the claim a bibliography entry
        # carries off to another machine, and until these were part of what
        # it covers, a restored or edited record could say the bytes came
        # from somewhere else, or at another time, and every check still
        # passed: the paper fields were untouched, so both content digests
        # matched, and `response.xml` was untouched, so its digest matched
        # too. A record that is checkable about its mathematics and
        # unfalsifiable about where it came from is not what the digest is
        # for.
        if self.source_url:
            head.extend(_wrapped(f"Source: {self.source_url}"))
        if self.fetched_at:
            head.append(f"Retrieved: {self.fetched_at}")
        if self.response_sha256:
            head.append(f"Response digest: sha256:{self.response_sha256}")
        # EVERY line is wrapped, metadata included, and wrapped HERE rather
        # than where it is displayed, so the digest covers the text a reader
        # is served. `read_paper` pages by line: a line too long for one
        # observation is clipped, and the part left over can never be asked
        # for, because there is no line after it to start from. An abstract
        # can arrive as one enormous line -- and so can an author list, on a
        # paper with three thousand of them.
        body = "\n".join(
            "\n".join(_wrapped(paragraph)) for paragraph in self.abstract.splitlines() or [""]
        )
        return "\n".join(head) + "\n\nAbstract\n" + body + "\n"

    def summary(self) -> str:
        """One line, for a search result the reader is scanning."""
        if not self.authors:
            return f"{self.arxiv_id}  {self.title}"
        who = self.authors[0] + (" et al." if len(self.authors) > 1 else "")
        return f"{self.arxiv_id}  {self.title} ({who})"


def _wrapped(line: str) -> list[str]:
    """One line, cut to a width a bounded read can return whole.

    Hard breaks included: a single unbroken token can be longer than the
    window on its own, and a line nothing will break is a line nothing can
    page past.

    Measured in ENCODED BYTES, not in code points, because the budget it
    exists to fit inside is a byte budget. Ninety-six CJK characters are two
    hundred and eighty-eight bytes, so a line `textwrap` called short enough
    did not fit a small window -- and `truncate` then clipped it and moved on
    to the next line, leaving the clipped tail unreachable by any later
    `read_paper`. A page nothing can turn to is the failure the wrapping is
    for.
    """
    wrapped = textwrap.wrap(line, ABSTRACT_COLUMNS, break_long_words=True) or [""]
    return [piece for one in wrapped for piece in _by_bytes(one)]


def _by_bytes(line: str) -> list[str]:
    """`line` in pieces of at most `ABSTRACT_COLUMNS` encoded bytes."""
    if len(line.encode("utf-8")) <= ABSTRACT_COLUMNS:
        return [line]
    pieces: list[str] = []
    current = ""
    width = 0
    for character in line:
        size = len(character.encode("utf-8"))
        if width + size > ABSTRACT_COLUMNS and current:
            pieces.append(current)
            current, width = "", 0
        current += character
        width += size
    if current:
        pieces.append(current)
    return pieces


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceFile(FrozenModel):
    """One file out of a paper's source bundle, as it was unpacked."""

    path: str
    size: int
    sha256: str
    text: bool


class SourceManifest(FrozenModel):
    """What a paper's source tree is, and which bytes it came out of.

    The archive digest is the claim a reader can check against arXiv itself:
    these files are what that bundle unpacked to, under the rules in
    `archives.py`, at that moment. Nothing here was executed or compiled --
    see `admit_source`.
    """

    schema_version: Literal[1] = 1
    arxiv_id: str
    kind: str
    archive_sha256: str
    archive_bytes: int
    source_url: str = ""
    fetched_at: str = ""
    files: tuple[SourceFile, ...] = ()

    def find(self, path: str) -> SourceFile | None:
        return next((item for item in self.files if item.path == path), None)


#: The directory a record's unpacked source lives in, and the manifest inside
#: it. Named here so the two places that build the path cannot disagree.
SOURCE_DIR = "source"
SOURCE_MANIFEST = "source.json"
#: The bundle itself, kept beside the tree it unpacked to. Without it the
#: manifest's `archive_sha256` is a claim nothing stands behind: the files
#: could be edited and their digests recomputed, and every check would pass
#: while the manifest went on naming a download those bytes never came from.
SOURCE_ARCHIVE = "archive.bin"




#: `(url, timeout)`, plus a keyword `limit` for a caller that accepts more
#: than an API response. Passed as a keyword and only when it differs from the
#: default, so a double written for the API alone -- `lambda url, timeout:
#: ...` -- still satisfies the protocol for every call that does not need one.
Transport = Callable[..., bytes]








def _coherent(identifier: ArxivId, record: PaperRecord) -> None:
    """Refuse a record whose provenance does not hold together.

    The two fields the Atom feed does not carry. `read` re-derives a record
    from its stored response and compares the whole model, which proves every
    other field came out of those bytes -- but the reparse is handed
    `source_url` and `fetched_at` from the record itself, so those two are
    compared with themselves and prove nothing. They are checked against what
    they claim to be instead: a source that is arXiv's API, and a retrieval
    time that is a time.

    WHAT THIS IS NOT. It is not authentication. Nothing computed from the
    library can tell a genuine record from one written by somebody who can
    edit the library: whoever rewrites `record.json` can put a plausible URL
    and a plausible timestamp in it as easily as an implausible one, and can
    recompute every digest afterwards. The claim that travels is the content
    digest a bibliography entry carries to another machine, where the paper
    can be fetched again and the digest recomputed. What is caught here is the
    weaker and commoner thing: a record whose provenance is not even
    internally coherent.

    WHAT IS DELIBERATELY NOT CHECKED. Whether `fetched_at` falls after the
    version's own `published`/`updated` date. It would catch a hand-edited
    stamp only in the case where the editor got the year wrong -- one who
    writes a later date defeats it entirely -- and it would make reading a
    stored record depend on this machine's clock agreeing with arXiv's. A
    container with no battery-backed clock, or a machine whose time has not
    yet been corrected, would then be refused papers it fetched itself, with
    every digest agreeing. Refusing a genuine record is the worse error, and
    a check anyone can evade at no cost is not worth buying it with.
    """
    if not record.source_url.startswith(f"{ENDPOINT}?"):
        raise ArxivError(
            f"the record stored under {identifier} says its metadata came from "
            f"{record.source_url[:200]!r}, which is not arXiv's API"
        )
    try:
        datetime.fromisoformat(record.fetched_at)
    except ValueError as error:
        raise ArxivError(
            f"the record stored under {identifier} does not say when it was "
            f"retrieved: {record.fetched_at[:64]!r} is not a timestamp"
        ) from error


def _stamp(when: float) -> str:
    """One epoch time as the string a record records it under."""
    return datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")


def _key(url: str) -> str:
    """The cache key for one request URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _entries(body: bytes, source_url: str, fetched_at: str) -> tuple[PaperRecord, ...]:
    """Read an Atom feed into records, refusing anything that is not one."""
    text = _decoded(body)
    root = _parsed(text)
    # An Atom feed, or nothing. A maintenance page or a proxy's error is
    # perfectly good XML with no `<entry>` in it, and reading that as "arXiv
    # matched nothing" files a service that did not answer under "the
    # literature does not have it" -- the same conflation `search_tools`
    # refuses for a Lean search that timed out.
    if root.tag != f"{ATOM}feed":
        raise ArxivError(
            f"the arXiv response is not an Atom feed (its root element is {root.tag!r}); "
            "this is not a report that nothing matched"
        )
    response_digest = hashlib.sha256(body).hexdigest()
    records = []
    for entry in root.findall(f"{ATOM}entry"):
        record = _entry(entry, source_url, fetched_at, response_digest)
        if record is not None:
            records.append(record)
    return tuple(records)


def _decoded(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ArxivError(f"the arXiv response was not valid UTF-8: {error}") from error


def _parsed(text: str) -> ElementTree.Element:
    """Parse the feed, refusing a document that could be a bomb.

    `xml.etree` expands internal entities, so a few hundred bytes of nested
    definitions can become gigabytes in memory. A DOCTYPE is what an entity
    bomb needs and what an arXiv feed never has, so the presence of one is
    grounds to refuse the whole response rather than to start parsing it.
    """
    # The WHOLE response, not its first few kilobytes: a document may put any
    # amount of legal whitespace and comment before its DOCTYPE, and a prefix
    # search is defeated by padding. The response is already size-bounded, so
    # scanning all of it is bounded too -- unlike the expanded tree, which the
    # network limit says nothing about.
    if re.search(r"<!DOCTYPE", text, re.IGNORECASE):
        raise ArxivError("the arXiv response carries a DOCTYPE declaration; refusing to parse it")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ArxivError(f"the arXiv response was not valid XML: {error}") from error


#: The `<id>` arXiv gives the entry it returns instead of a paper. That entry
#: is an answer, not a malformed one, which is why it is the only thing
#: `_entry` is allowed to drop.
ERROR_ID = "/api/errors"


def _entry(
    entry: ElementTree.Element,
    source_url: str,
    fetched_at: str,
    response_digest: str,
) -> PaperRecord | None:
    """One `<entry>` as a record, or None when arXiv said there is no paper.

    arXiv answers a query for a malformed id with an entry whose `id` is an
    error URL and whose title is "Error". That is an answer -- "no such
    paper" -- so it is dropped and the caller reports exactly that.

    Anything else that cannot be read raises. Dropping it silently reported a
    shorter list as the whole result, or "arXiv matched nothing" when every
    entry was rejected, and a search that quietly omits what it could not
    interpret is the conflation this module refuses everywhere else: a
    shortened list is indistinguishable from a search that found fewer.
    """
    raw_id = _text(entry.find(f"{ATOM}id"))
    if ERROR_ID in raw_id:
        return None
    try:
        identifier = parse_id(raw_id)
    except ArxivError as error:
        raise ArxivError(
            f"the arXiv response contains an entry whose id {raw_id!r} is not an arXiv "
            "identifier; refusing to report the rest as the whole answer"
        ) from error
    if not identifier.versioned:
        # arXiv's `<id>` is always versioned. One that is not means the feed
        # is not what this code was written against, and guessing a version
        # would put a record under an identifier arXiv never used.
        raise ArxivError(
            f"the arXiv response contains an entry whose id {raw_id!r} names no version; "
            "refusing to report the rest as the whole answer"
        )
    title = _collapsed(_text(entry.find(f"{ATOM}title")))
    authors = tuple(
        _collapsed(_text(author.find(f"{ATOM}name")))
        for author in entry.findall(f"{ATOM}author")
    )
    abstract = _text(entry.find(f"{ATOM}summary")).strip()
    primary = entry.find(f"{ARXIV}primary_category")
    categories = tuple(
        str(category.get("term", "")).strip()
        for category in entry.findall(f"{ATOM}category")
        if category.get("term")
    )
    # A well-formed id is not a well-formed entry. Each of these was read with
    # a `""` fallback, so a truncated or half-written response produced a
    # record with a blank title and no byline that `fetch_paper` stored and
    # `cite_paper` would put in front of a reader -- a citation to a paper
    # nothing describes. An answer that cannot be interpreted is reported as
    # that rather than filed as a paper.
    missing = [
        name
        for name, value in (
            ("title", title),
            ("summary", abstract),
            ("author", tuple(one for one in authors if one)),
        )
        if not value
    ]
    if missing:
        raise ArxivError(
            f"the arXiv entry for {identifier} has no {', '.join(missing)}; "
            "refusing to store a record of a paper it does not describe"
        )
    record = PaperRecord(
        arxiv_id=str(identifier),
        title=title,
        authors=tuple(name for name in authors if name),
        abstract=abstract,
        categories=categories,
        primary_category=str(primary.get("term", "")).strip() if primary is not None else "",
        published=_text(entry.find(f"{ATOM}published")).strip(),
        updated=_text(entry.find(f"{ATOM}updated")).strip(),
        doi=_text(entry.find(f"{ARXIV}doi")).strip() or None,
        journal_ref=_text(entry.find(f"{ARXIV}journal_ref")).strip() or None,
        abs_url=f"https://arxiv.org/abs/{identifier}",
        source_url=source_url,
        fetched_at=fetched_at,
        response_sha256=response_digest,
    )
    # The digest is over the rendered content, which is built from the fields
    # -- so it can only be computed once the record exists.
    return record.model_copy(
        update={
            "content_sha256": digest(record.content()),
            "content_bytes": len(record.content().encode("utf-8")),
        }
    )


def _text(element: ElementTree.Element | None) -> str:
    return "" if element is None or element.text is None else element.text


def _collapsed(text: str) -> str:
    """One line. Atom wraps a long title across several, indentation and all."""
    return " ".join(text.split())
