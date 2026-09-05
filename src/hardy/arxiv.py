"""Polite arXiv fetching, and an immutable versioned record of what came back.

Two promises, and they are separate.

**Polite.** arXiv asks for no more than one request every three seconds from
one caller, and Hardy is one caller however many sessions the machine is
running: the interval is enforced through a timestamp on disk rather than an
attribute in memory, so two `hardy` processes throttle each other. Every
query's answer is cached, and a paper already held is never fetched again --
the cheapest way to be polite is not to ask.

**Immutable.** A record is written under the *versioned* identifier arXiv
itself reported (`2401.12345v2`, never `2401.12345`), with the digest of the
bytes it holds. Admission is atomic and one-way: once `2401.12345v2` is in the
library it is never rewritten, and `2401.12345v3` is a new record beside it
rather than an overwrite. That is what makes "the paper says X" checkable
later -- an unversioned citation is a citation of whatever the author has
uploaded since.

Two things are stored, and a citation rests on the first alone. The metadata
and abstract come from the API and are what `cite_paper` vouches for. The
*source bundle* is fetched separately, on request, and unpacked under the
rules in `archives.py`: normalised paths, no links, quotas, temporary staging,
and one rename. Its bytes are kept beside the tree they unpacked to, so the
manifest's claim about where the files came from is checkable rather than
asserted.

Nothing extracted is executed, compiled, or handed to TeX -- the files are
text to read and to inventory. Hardy still has no process isolation (#84), and
defensive unpacking bounds what an archive can do to the filesystem; it is not
a sandbox and does not make running the contents safe.

Nothing here is trusted. The response is third-party XML: it is size-bounded
before parsing, refused outright if it carries a DOCTYPE (an entity bomb needs
one), and every field is read as text with no markup meaning.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import shutil
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .domain import FrozenModel
from .layout import LayoutError, guard_for, read_bytes, read_text
from .storage import FileLock

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


class PaperLibrary:
    """The records on disk, and the rules that keep them immutable.

    Machine-local by design. The library is a cache of third-party bytes and
    is never committed; what travels with a clone is the bibliography, whose
    entries carry the digest of what was read. A clone with an empty library
    can still be told which bytes a citation was made against.
    """

    def __init__(self, root: Path, *, throttle: Path | None = None) -> None:
        self.root = root
        self.records = root / "records"
        self.queries = root / "queries"
        # The records and the cache belong to a project root; the request
        # clock does not. arXiv sees one caller per machine however many
        # project roots that machine holds, so a clock kept beside the cache
        # gave two sessions on two roots two budgets and the machine-wide
        # promise was not kept. Defaulted to the library's own root, which is
        # what a test wants and what a caller who has not thought about it
        # gets; `paper_tools.build_runtime` passes the user-level directory.
        self.throttle = throttle if throttle is not None else root
        self.state_path = self.throttle / "state.json"
        self.lock_path = self.throttle / "state.lock"

    def path_for(self, identifier: ArxivId) -> Path:
        return self.records / identifier.storage_name

    def _guard(self, relative: str):
        """A write guard for one file in the library, proven component by
        component from the tooling directory down.

        `Layout.ensure` proves `.hardy` and stops there, because it cannot
        know what a tool will put inside it. So `papers/`, `records/` and
        every directory below them are proven here instead, at the moment of
        the write -- a repository that ships `.hardy/papers -> /etc` or
        `.hardy/papers/records -> ~` would otherwise have a `mkdir` and a
        `write` follow the link and land downloaded bytes outside the project.
        Chained from `self.root.parent` rather than from `self.root`, because
        a guard on a directory can only speak for that directory's own name:
        proving `papers` needs `.hardy` above it.
        """
        return guard_for(self.root.parent, f"{self.root.name}/{relative}", create=True)

    def _read(self, relative: str) -> str:
        """Read one library file through the same proof a write gets."""
        return read_text(self.root, relative)

    def _throttle_guard(self) -> tuple[Any, str]:
        """A guard for one file in the throttle directory, proven as any other."""
        return guard_for(self.throttle.parent, f"{self.throttle.name}/state", create=True)

    def lock_target(self) -> Path:
        """The throttle lock's path, with the directory holding it proven.

        The lock was the one library file reached without a guard, and
        `FileLock` creates the parent directory if it is missing. Pointed
        through `.hardy/papers -> somewhere`, that is Hardy making a directory
        wherever the link leads, before any guarded call had a chance to
        refuse it. Proven first, so there is nothing to point at.
        """
        guard, _ = self._throttle_guard()
        return guard.path("state.lock")

    def holds(self, identifier: ArxivId) -> bool:
        return identifier.versioned and (self.path_for(identifier) / "record.json").is_file()

    def read(self, identifier: ArxivId) -> PaperRecord:
        """The stored record, checked against its own digest.

        Verified on every read rather than on admission alone: admission
        proves what was written, and a citation is a claim about what is
        there now. A record whose content has moved underneath it is refused
        outright -- silently serving edited bytes under a digest that no
        longer describes them is exactly the failure the digest is for.
        """
        if not identifier.versioned:
            raise ArxivError(f"{identifier} names no version; nothing can be held under it")
        held = f"records/{identifier.storage_name}"
        try:
            record = PaperRecord.model_validate_json(self._read(f"{held}/record.json"))
            stored = read_bytes(self.root, f"{held}/content.txt")
        except (OSError, ValueError) as error:
            raise ArxivError(f"the stored record for {identifier} could not be read: {error}") from error
        # Asked before the digests, so that a record carrying no response
        # digest is told it carries none. It is also covered BY the content
        # digest now, which would otherwise catch a blanked field first and
        # report the generic "this has been edited" -- true, but less use to
        # whoever has to work out what is wrong with the file.
        #
        # Required, not merely compared when present. An empty digest was an
        # opt-out: blank the field in `record.json` and any `response.xml`
        # became acceptable, leaving a record that reads and cites without the
        # provenance it claims to carry.
        if not record.response_sha256:
            raise ArxivError(
                f"the record stored under {identifier} carries no response digest, so "
                "nothing says which bytes its metadata was read from"
            )
        # BOTH have to match the digest, and checking only the first was a
        # hole: `read_paper` serves `record.content()`, regenerated from the
        # record's own fields, so an edit to the title or the abstract in
        # `record.json` changed what a reader is served while `content.txt`
        # went on matching its digest untouched. The digest is a claim about
        # what Hardy will hand back, so it is checked against what Hardy will
        # hand back.
        #
        # Against the file's bytes, not against text decoded from it. A
        # text-mode read turns `\r\n` back into `\n`, so a `content.txt`
        # whose line endings had been rewritten -- by a Windows text-mode
        # write, by a checkout, by an editor -- passed a comparison that was
        # supposed to establish the file had not moved.
        if (
            hashlib.sha256(stored).hexdigest() != record.content_sha256
            or digest(record.content()) != record.content_sha256
        ):
            raise ArxivError(
                f"the stored record for {identifier} does not match its recorded digest; "
                "the library has been edited and this record can no longer be read or cited"
            )
        # The digests say the record is internally consistent. They say nothing
        # about it being THIS record: a directory holding another paper's
        # `record.json` and `content.txt` -- an interrupted move, a hand-copied
        # cache, a restored backup -- passes both comparisons, and then
        # `read_paper(A)` serves B and `cite_paper(A)` records B under A's
        # name. The identifier a record is filed under has to be the
        # identifier it claims.
        # The response the record was parsed out of, checked against the
        # digest the record carries for it. Without this the record could go
        # on claiming its metadata came from one untouched API response while
        # that file was edited or deleted underneath it -- a provenance claim
        # nothing stood behind.
        try:
            response = read_bytes(self.root, f"{held}/response.xml")
        except (OSError, LayoutError) as error:
            raise ArxivError(
                f"the stored response for {identifier} could not be read: {error}"
            ) from error
        if hashlib.sha256(response).hexdigest() != record.response_sha256:
            raise ArxivError(
                f"the stored response for {identifier} does not match its recorded digest; "
                "this record no longer says where its metadata came from"
            )
        # And the metadata is re-derived from that response rather than merely
        # accompanied by it. Until this, the two digests proved the response
        # was untouched and that the record agreed with `content.txt` -- and
        # nothing at all connected the two, so editing `record.json` and
        # `content.txt` together and recomputing the content digest served
        # fabricated authors and titles under a response that was still
        # genuinely arXiv's. A record's claim is that these fields came out of
        # those bytes; that claim is now checked rather than asserted.
        #
        # Rebuilt with the record's own `source_url` and `fetched_at`, which
        # the feed does not carry: those two are covered instead by
        # `content_sha256`, which is the digest a bibliography entry takes to
        # another machine. Comparing the rebuilt content digest covers every
        # field `_entry` reads in one comparison.
        try:
            reparsed = _entries(response, record.source_url, record.fetched_at)
        except ArxivError as error:
            raise ArxivError(
                f"the stored response for {identifier} can no longer be read as the feed "
                f"its record was parsed from: {error}"
            ) from error
        rebuilt = next((one for one in reparsed if one.arxiv_id == record.arxiv_id), None)
        # The WHOLE record, not its content digest. `content()` does not
        # render `primary_category`, `abs_url` or `content_bytes`, so those
        # three could be edited with every digest still agreeing -- a record
        # serving fields it had not re-derived from anything, which is the one
        # thing this comparison exists to rule out. Comparing the models
        # themselves also means a field added later is covered the day it is
        # added rather than the day someone remembers this list.
        if rebuilt is None or rebuilt != record:
            raise ArxivError(
                f"the record stored under {identifier} is not what its own response says; "
                "its metadata has been edited away from the bytes it claims to come from"
            )
        if str(record.identifier) != str(identifier):
            raise ArxivError(
                f"the record stored under {identifier} says it is {record.arxiv_id}; "
                "refusing to serve one paper under another's identifier"
            )
        _coherent(identifier, record)
        return record

    def stored(self) -> tuple[str, ...]:
        """Every versioned identifier the library holds, sorted."""
        if not self.records.is_dir():
            return ()
        return tuple(
            sorted(
                child.name.replace("_", "/")
                for child in self.records.iterdir()
                if not child.is_symlink() and (child / "record.json").is_file()
            )
        )

    def holds_source(self, identifier: ArxivId) -> bool:
        return identifier.versioned and (
            self.path_for(identifier) / SOURCE_DIR / SOURCE_MANIFEST
        ).is_file()

    def source_manifest(self, identifier: ArxivId) -> SourceManifest:
        """The manifest for a held source tree, checked against its own archive.

        Three separate things are established here, and each of them was a way
        a source tree could lie. The manifest has to be about *this* paper, or
        a directory moved between records serves one paper's source under
        another's name. The archive it names has to be present, because a
        manifest whose bundle is gone is a provenance claim with nothing
        behind it. And the bundle's bytes have to hash to what the manifest
        says, so that "these files came out of that download" is checkable
        rather than asserted -- anyone holding the record can re-extract it
        and compare.

        The individual files are re-hashed by `read_source`, on the one file
        being served, rather than here.
        """
        held = f"records/{identifier.storage_name}/{SOURCE_DIR}"
        try:
            manifest = SourceManifest.model_validate_json(self._read(f"{held}/{SOURCE_MANIFEST}"))
        except (OSError, ValueError, LayoutError) as error:
            raise ArxivError(
                f"the stored source manifest for {identifier} could not be read: {error}"
            ) from error
        if manifest.arxiv_id != str(identifier):
            raise ArxivError(
                f"the source stored under {identifier} says it belongs to {manifest.arxiv_id}; "
                "refusing to serve one paper's source under another's identifier"
            )
        try:
            archive = read_bytes(self.root, f"{held}/{SOURCE_ARCHIVE}")
        except (OSError, LayoutError) as error:
            raise ArxivError(
                f"the stored archive for {identifier} could not be read: {error}"
            ) from error
        if hashlib.sha256(archive).hexdigest() != manifest.archive_sha256:
            raise ArxivError(
                f"the stored archive for {identifier} does not match the digest its manifest "
                "names; this source tree no longer says which download it came from"
            )
        return manifest

    def read_source(
        self, identifier: ArxivId, path: str, manifest: SourceManifest | None = None
    ) -> str:
        """One text file out of a held source tree, checked before it is served.

        Two separate refusals, because they are two different failures. A path
        the manifest does not name is not part of what was admitted -- a file
        planted in the directory afterwards, or a traversal out of it -- and
        is refused whether or not it exists. A path the manifest names whose
        bytes no longer hash to what was recorded has been edited since
        admission, and the digest exists precisely so that is not served
        silently.
        """
        manifest = self.source_manifest(identifier) if manifest is None else manifest
        wanted = manifest.find(path)
        if wanted is None:
            raise ArxivError(
                f"{path!r} is not in the source of {identifier}; the files it holds are "
                f"{[item.path for item in manifest.files][:20]}"
            )
        if not wanted.text:
            raise ArxivError(
                f"{path!r} is not text ({wanted.size} bytes), so there is nothing to read; "
                "Hardy stores it but does not decode it"
            )
        held = f"records/{identifier.storage_name}/{SOURCE_DIR}"
        try:
            stored = read_bytes(self.root, f"{held}/{path}")
        except (OSError, LayoutError) as error:
            raise ArxivError(f"{path!r} could not be read: {error}") from error
        if hashlib.sha256(stored).hexdigest() != wanted.sha256:
            raise ArxivError(
                f"{path!r} does not match the digest it was admitted under; the source tree "
                f"for {identifier} has been edited and can no longer be read"
            )
        return stored.decode("utf-8", errors="replace")

    def source_texts(self, identifier: ArxivId) -> dict[str, str]:
        """Every readable file in a held source tree, by path.

        Each one goes through `read_source`, so a tree with one edited file
        refuses rather than quietly returning the rest.
        """
        manifest = self.source_manifest(identifier)
        return {
            item.path: self.read_source(identifier, item.path, manifest)
            for item in manifest.files
            if item.text
        }

    def admit_source(
        self,
        identifier: ArxivId,
        archive: bytes,
        *,
        source_url: str,
        fetched_at: str,
        limits: Any | None = None,
    ) -> SourceManifest:
        """Unpack a source bundle into the library, or refuse it whole.

        The unpacking rules are `archives.extract`'s and are documented there.
        What this adds is the same two properties the metadata record has:
        extraction happens in a temporary directory *beside* the target and
        lands with one rename, so a refused archive never leaves a partial
        `source/` behind; and a tree already held is never rewritten, because
        an assumption minted against one reading of a paper must not find
        different bytes under it later.

        Nothing extracted is executed, compiled, or handed to TeX. These are
        files to read and to inventory. Hardy has no process isolation yet
        (#84), and the defensive unpacking here is a bound on what an archive
        can do to the filesystem -- not a sandbox, and not a licence to run
        what it contains.
        """
        from . import archives  # local: `archives` is only needed by this path

        if not identifier.versioned:
            raise ArxivError("a source may only be admitted under a versioned identifier")
        if not self.holds(identifier):
            raise ArxivError(
                f"there is no record for {identifier}, so nothing says where a source tree "
                "under that name came from; fetch the paper first"
            )
        if self.holds_source(identifier):
            return self.source_manifest(identifier)
        guard, name = self._guard(f"records/{identifier.storage_name}/{SOURCE_DIR}")
        target = guard.reserve(name)
        staging = Path(tempfile.mkdtemp(prefix=".staging-source-", dir=guard.directory))
        try:
            extraction = archives.extract(
                archive, staging, **({} if limits is None else {"limits": limits})
            )
            if any(item.path in (SOURCE_MANIFEST, SOURCE_ARCHIVE) for item in extraction.files):
                # The manifest is Hardy's own claim about the tree. An archive
                # carrying a file of that name would either overwrite it or be
                # overwritten by it, and either way one of the two would be
                # read as the other.
                raise ArxivError(
                    f"the archive contains a file named {SOURCE_MANIFEST} or "
                    f"{SOURCE_ARCHIVE}, which are the names Hardy's own manifest and stored "
                    "bundle take; refusing it"
                )
            manifest = SourceManifest(
                arxiv_id=str(identifier),
                kind=extraction.kind,
                archive_sha256=hashlib.sha256(archive).hexdigest(),
                archive_bytes=len(archive),
                source_url=source_url,
                fetched_at=fetched_at,
                files=tuple(
                    SourceFile(path=item.path, size=item.size, sha256=item.sha256, text=item.text)
                    for item in extraction.files
                ),
            )
            (staging / SOURCE_ARCHIVE).write_bytes(archive)
            (staging / SOURCE_MANIFEST).write_bytes(
                (manifest.model_dump_json(indent=2) + "\n").encode("utf-8")
            )
            try:
                os.replace(staging, target)
            except OSError:
                # Another process admitted the same source between the check
                # above and this rename. Whoever landed first holds it, which
                # is the outcome this method wants anyway.
                if (target / SOURCE_MANIFEST).is_file():
                    return self.source_manifest(identifier)
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return self.source_manifest(identifier)

    def admit(self, record: PaperRecord, response: bytes) -> PaperRecord:
        """Put a record in the library, or keep the one already there.

        Staged in a temporary directory beside the target and moved into
        place with a single rename, so a crash halfway through leaves either
        the whole record or none of it -- never a `record.json` describing a
        `content.txt` that was never written.

        An identifier already held wins. Not "the newer fetch wins": a
        citation that resolved to one set of bytes yesterday has to resolve to
        the same bytes today, and arXiv's own guarantee is that a *versioned*
        identifier is fixed. If the two ever disagree, the stored one is what
        was cited and the new one is news -- reported by the caller, not
        written over the record.
        """
        identifier = record.identifier
        if not identifier.versioned:
            raise ArxivError("a record may only be admitted under a versioned identifier")
        target = self.path_for(identifier)
        if (target / "record.json").is_file():
            return self.read(identifier)
        # Proven before anything is created, and the guard's own directory is
        # what the staging tree is made in -- so a `records` that is a symlink
        # is refused here rather than followed by the `mkdtemp` below.
        guard, name = self._guard(f"records/{identifier.storage_name}")
        target = guard.reserve(name)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=guard.directory))
        try:
            # Bytes, not text. `write_text` opens in text mode, which on
            # Windows turns every `\n` into `\r\n` -- so the file on disk
            # would not be the bytes `content_sha256` was taken over, and a
            # digest that does not identify what is stored is not a digest.
            # The text-mode read translated it back, so nothing complained:
            # the record was consistent with itself and wrong about the file,
            # which is the failure this store exists to make impossible.
            (staging / "content.txt").write_bytes(record.content().encode("utf-8"))
            (staging / "response.xml").write_bytes(response)
            (staging / "record.json").write_bytes(
                (record.model_dump_json(indent=2) + "\n").encode("utf-8")
            )
            try:
                os.replace(staging, target)
            except OSError:
                # Another process admitted the same paper between the check
                # above and this rename -- `os.replace` will not replace a
                # non-empty directory. That is the outcome this method wants
                # anyway: whoever got there first holds the record.
                if (target / "record.json").is_file():
                    return self.read(identifier)
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return self.read(identifier)

    def _cached_body(self, key: str) -> bytes | None:
        """The bytes stored under `key`, whatever their age, or None.

        Age-blind on purpose: this answers "is this still the entry I read",
        which an expiry check would confuse with "is it still worth serving".
        """
        try:
            payload = json.loads(self._read(f"queries/{key}.json"))
            return str(payload["body"]).encode("utf-8")
        except (OSError, ValueError, KeyError, TypeError, LayoutError):
            return None

    def cached_query(
        self, key: str, *, now: float, ttl: float = QUERY_TTL_SECONDS
    ) -> tuple[bytes, float] | None:
        """The stored answer to this query and when it was obtained, if fresh.

        The timestamp comes back with the body because a record parsed out of
        a cached response was fetched when the CACHE was filled, not when it
        was read. Stamping it with the read time made `fetched_at` say the
        bytes arrived at a moment they did not -- which matters exactly when
        it is checked: an admission that failed on a full disk and succeeded
        on a retry an hour later.
        """
        try:
            payload = json.loads(self._read(f"queries/{key}.json"))
            fetched = float(payload["fetched_at"])
            body = str(payload["body"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        # `json.loads` accepts a bare `NaN`, and `float` keeps it. Every
        # comparison against a NaN is false, so a corrupted entry passed both
        # freshness tests as fresh and `_stamp` then raised `ValueError` --
        # which is not the `ArxivError` the caller catches, so the entry was
        # never dropped and every search or fetch for that URL failed
        # identically forever after.
        if not math.isfinite(fetched):
            return None
        age = now - fetched
        # A negative age is a clock that moved backwards, and it used to pass
        # this check -- so the entry stayed "fresh" for however long the clock
        # had jumped, well past the day it promises, and an unversioned fetch
        # went on resolving to a version arXiv had already superseded. The
        # throttle treats the same jump as "no idea"; so does this.
        if age < 0 or age > ttl:
            return None
        return body.encode("utf-8"), fetched

    def query_lock(self, key: str) -> Path:
        """The lock for one cache entry, with the directory holding it proven.

        Per KEY rather than the library-wide lock, and that is not tidiness:
        `_throttle` holds the library lock while it sleeps out the interval,
        and the cache recheck it runs under that lock calls `drop_query`. One
        lock for both would be this process waiting on itself.

        Nothing waits on this lock for long -- what it covers is a comparison
        and an unlink -- so it never stands between a caller and the network.
        """
        guard, _ = self._guard(f"queries/{key}.json")
        return guard.path(f"{key}.lock")

    def cache_query(self, key: str, body: bytes, *, now: float) -> None:
        # Under the same lock the conditional drop takes, and NOT written
        # without it. Without the lock the comparison there is still racing a
        # write: a process that has established the cached bytes are the ones
        # it rejected can be overtaken between that and the unlink, and delete
        # a good answer somebody else had just put there. `required=False`
        # with a fallback that writes anyway is that race with extra steps --
        # it is the timed-out writer who supplies the replacement to destroy.
        #
        # So the invariant is that a cache key is only ever changed by a
        # process holding it, and the cost of that is losing THIS answer when
        # the key is busy for five whole seconds. Cheap: the answer is in hand
        # for this call either way, and what is lost is one cache entry that
        # the next request fetches again.
        guard, name = self._guard(f"queries/{key}.json")
        with FileLock(self.query_lock(key), timeout=LOCK_SECONDS, required=False) as lock:
            if not lock.held:
                return
            guard.write_bytes(
                name,
                json.dumps(
                    {"fetched_at": now, "body": body.decode("utf-8", errors="replace")},
                    ensure_ascii=False,
                ).encode("utf-8"),
            )

    def drop_query(self, key: str, *, body: bytes | None = None) -> None:
        """Forget one cached answer, or the particular one that was bad.

        For a body that turned out not to be an answer at all. A cached
        maintenance page would otherwise be served for the whole TTL, so every
        retry of a search would fail identically for a day after arXiv had
        recovered.

        `body` names which bytes were judged unreadable. Without it the
        deletion is of whatever is at the key now, and two processes meeting
        the same bad entry raced: the first dropped it, fetched, and cached a
        good answer; the second -- still holding the bad bytes it had parsed
        -- then deleted that, and went to the network for something already on
        disk. A bad entry is dropped once, by the process that read it.
        """
        try:
            # Compared and deleted under one lock, and neither without it.
            # The two were separate, unlocked steps: a process that had
            # established the cached bytes were the ones it rejected could be
            # overtaken between them -- somebody else drops, refetches, caches
            # a good answer -- and then delete that, turning a cached success
            # into another request and possibly into a network failure.
            #
            # Nothing happens when the lock cannot be taken. A five-second
            # wait on a lock covering one comparison and one unlink means
            # somebody live is stuck holding the key -- a dead holder releases
            # it, since the kernel holds it rather than a file anyone has to
            # judge -- and leaving a bad entry for them to drop is better than
            # deleting whatever is there without having compared it.
            guard, name = self._guard(f"queries/{key}.json")
            with FileLock(self.query_lock(key), timeout=LOCK_SECONDS, required=False) as lock:
                if not lock.held:
                    return
                if body is not None and self._cached_body(key) not in (None, body):
                    return
                guard.unlink(name, missing_ok=True)
        except (OSError, LayoutError):
            return

    def last_request(self) -> float:
        try:
            when = float(json.loads(read_text(self.throttle, "state.json"))["last_request"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0
        # A `NaN` survives `json.loads` and `float`, and every comparison
        # against one is false -- so the interval arithmetic below produced no
        # wait at all and the request went out at once, past the one promise
        # this file exists to keep. Reported as no record, which is what the
        # clause above already does for a state file that cannot be read: the
        # spacing is re-established from the next `note_request` rather than
        # guessed at.
        return when if math.isfinite(when) else 0.0

    def note_request(self, when: float) -> None:
        """Record that a request is being made, before it is made.

        Written first on purpose. A request recorded only on success lets a
        run of failures hammer arXiv at whatever rate the failures come back
        -- which is the moment a service least wants to be hammered.
        """
        guard, _ = self._throttle_guard()
        guard.write_bytes("state.json", json.dumps({"last_request": when}).encode("utf-8"))


#: `(url, timeout)`, plus a keyword `limit` for a caller that accepts more
#: than an API response. Passed as a keyword and only when it differs from the
#: default, so a double written for the API alone -- `lambda url, timeout:
#: ...` -- still satisfies the protocol for every call that does not need one.
Transport = Callable[..., bytes]


def _http(url: str, timeout: float, limit: int | None = None) -> bytes:
    """Read a whole response under one deadline, size-bounded.

    `limit` is what the caller will accept, defaulting to an API response's
    bound. A source bundle is served by a different endpoint and is allowed
    to be much larger, so the bound travels with the request rather than
    being a property of this module.

    The same shape as `retrieval._fetch_url` and for the same reason: a
    per-read socket timeout is not a bound on the transfer, because a server
    dripping one byte at a time resets it forever.
    """
    bound = MAX_RESPONSE_BYTES if limit is None else limit
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        opened = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https endpoint
    except urllib.error.HTTPError as error:
        raise ArxivError(f"arXiv answered HTTP {error.code} {error.reason}") from error
    except (OSError, http.client.HTTPException) as error:
        raise ArxivError(f"arXiv could not be reached: {error}") from error
    # The read loop needs its own handler, not only `urlopen`'s. A connection
    # that times out, resets, or is closed mid-body raises after the response
    # object exists, and that escaped every caller: the tool dispatcher
    # catches `ArxivError` and argument errors, so an ordinary network
    # failure halfway through a response ended the turn instead of coming
    # back as a failed tool call.
    #
    # `HTTPException` beside `OSError`, because the commonest way for that to
    # happen is not a socket error at all: arXiv answers chunked, and a
    # connection closed mid-chunk raises `http.client.IncompleteRead`, which
    # descends from `HTTPException` and would have walked straight through an
    # `OSError` handler.
    with opened as response:
        chunks: list[bytes] = []
        received = 0
        wanted = bound + 1
        try:
            while received < wanted:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ArxivError(
                        f"arXiv exceeded its {timeout:g}s deadline with {received} bytes read"
                    )
                # The socket keeps whatever timeout `urlopen` was given, so a
                # read begun just under the deadline could block for another
                # full timeout -- a 30s request occupying the tool for nearly
                # 60. Narrowing it to what is left of the deadline before each
                # read makes the bound the one this function advertises.
                # Best-effort: it reaches through the response's internals, so
                # a runtime that does not expose them falls back to the old
                # one-read overshoot rather than failing the transfer.
                _narrow(response, remaining)
                chunk = response.read(min(READ_CHUNK_BYTES, wanted - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
        except (OSError, http.client.HTTPException) as error:
            raise ArxivError(
                f"the arXiv response failed after {received} bytes: {error}"
            ) from error
    body = b"".join(chunks)
    if len(body) > bound:
        raise ArxivError(f"the arXiv response exceeds {bound} bytes")
    return body


class ArxivClient:
    """Search and fetch, throttled, cached, and recorded.

    `transport`, `clock` and `sleep` are injected so the throttle, the cache
    and the admission rules can be tested without a network or a wall clock.
    A test that had to wait three seconds per request would be a test nobody
    runs.
    """

    def __init__(
        self,
        library: PaperLibrary,
        *,
        transport: Transport = _http,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        interval: float = MIN_INTERVAL_SECONDS,
        lock_timeout: float | None = None,
    ) -> None:
        self.library = library
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._timeout = timeout
        self._interval = interval
        # Long enough for another process to finish one interval and hand the
        # lock over, and never shorter than that: a timeout under the interval
        # would give up exactly when the other process was doing the waiting
        # this lock exists to coordinate.
        self._lock_timeout = lock_timeout if lock_timeout is not None else interval * 4 + 5

    def search(self, query: str, limit: int = 10) -> tuple[PaperRecord, ...]:
        """Papers matching `query`, newest first, from cache when possible.

        The records that come back are *not* admitted to the library. A
        search result is a lead: the version it names is the current one at
        the moment of asking, and the moment it is written down as a citation
        it has to be pinned by a deliberate fetch instead.
        """
        text = str(query).strip()
        if not text:
            raise ArxivError("a paper search needs a query")
        bounded = max(1, min(int(limit), MAX_RESULTS))
        url = self._url(
            {
                "search_query": text,
                "start": "0",
                "max_results": str(bounded),
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
        found, _ = self._entries_for(url)
        return found[:bounded]

    def fetch(self, raw: str) -> tuple[PaperRecord, bool]:
        """The immutable record for `raw`, and whether it was already held.

        A versioned identifier the library holds costs no request at all --
        the record cannot have changed, so asking would be asking a question
        whose answer is on disk. An unversioned one has to be resolved, since
        only arXiv knows which version is current; the answer is cached like
        any other query, so resolving the same stem twice in a day is one
        request rather than two.
        """
        identifier = parse_id(raw)
        if self.library.holds(identifier):
            return self.library.read(identifier), True
        url = self._url({"id_list": str(identifier), "max_results": "1"})
        found, body = self._entries_for(url)
        if not found:
            raise ArxivError(f"arXiv returned no paper for {identifier}")
        record = found[0]
        resolved = record.identifier
        # An unversioned request is a request to be told which version is
        # current -- so the version may differ, and the STEM may not. Checking
        # only the versioned case left `fetch_paper 2401.00001` willing to
        # accept `2401.99999v1` from a proxy, a poisoned cache, or a service
        # having a bad day, and to hand it back as the paper that was asked
        # for.
        if resolved.stem != identifier.stem or (
            identifier.versioned and str(resolved) != str(identifier)
        ):
            # Not an answer to this question, so not an answer worth keeping
            # for a day: dropped from the cache before the refusal, or every
            # retry would reuse the same wrong response.
            #
            # By identity, like the malformed-body drop. Unqualified, this
            # removed whatever was under the key rather than the bytes it had
            # just rejected: a neighbour that met the same wrong answer, threw
            # it away and cached a good one had its replacement deleted here,
            # so the next request went to the network for something that was
            # on disk a moment ago -- and could fail there.
            self.library.drop_query(_key(url), body=body)
            raise ArxivError(
                f"asked arXiv for {identifier} and it answered with {resolved}; "
                "refusing to store one paper under another's identifier"
            )
        if self.library.holds(resolved):
            return self.library.read(resolved), True
        return self.library.admit(record, body), False

    def fetch_source(self, raw: str) -> tuple[SourceManifest, bool]:
        """The paper's source bundle, unpacked into the library.

        Only for a paper already held, and only under a versioned identifier:
        the record is what says where these bytes came from, and "the source
        of 2401.12345" is a moving target in exactly the way a versioned
        record exists to rule out.

        A tree already held costs no request at all -- a versioned bundle
        cannot change, so asking would be asking a question whose answer is on
        disk. Otherwise the download takes an ordinary throttle slot: arXiv
        sees one caller, and a source fetch is a request like any other.

        Deliberately not cached as a query. The query cache stores bodies as
        text in JSON, which an archive is not, and a bundle that unpacked
        successfully is already on disk under its manifest.
        """
        identifier = parse_id(raw)
        if not identifier.versioned:
            raise ArxivError(
                f"{identifier} names no version, and a paper's source differs between "
                f"versions; fetch_paper {identifier} first and ask for the version it names"
            )
        if not self.library.holds(identifier):
            raise ArxivError(
                f"{identifier} has not been fetched, so Hardy has no record to file a source "
                "tree under. Call fetch_paper first."
            )
        if self.library.holds_source(identifier):
            return self.library.source_manifest(identifier), True
        url = f"{SOURCE_ENDPOINT}{identifier}"
        self._throttle()
        body = self._transport(url, self._timeout, limit=MAX_ARCHIVE_BYTES)
        now = self._clock()
        return (
            self.library.admit_source(
                identifier, body, source_url=url, fetched_at=_stamp(now)
            ),
            False,
        )

    def _url(self, parameters: dict[str, str]) -> str:
        return f"{ENDPOINT}?{urllib.parse.urlencode(parameters)}"

    def _entries_for(self, url: str) -> tuple[tuple[PaperRecord, ...], bytes]:
        """The records for `url`, and the bytes they were read out of.

        A body is admitted to the cache only once it has been established to
        be an answer. Caching first and parsing afterwards was the bug: a
        maintenance page, a proxy error, or a truncated feed was stored under
        the query's key and served for the full day, so every retry failed
        identically long after arXiv had recovered. A cached body that no
        longer parses is dropped for the same reason and asked again.
        """
        key = _key(url)
        cached = self.library.cached_query(key, now=self._clock())
        if cached is not None:
            body, fetched = cached
            try:
                # Stamped with when the bytes arrived, not when they were read
                # back: a record admitted from the cache says where it came
                # from and when, and both have to be true.
                return _entries(body, url, _stamp(fetched)), body
            except ArxivError:
                self.library.drop_query(key, body=body)
        # Asked again on the way out of the wait, and asked INSIDE the lock,
        # between the waiting and the reservation. Two processes wanting the
        # same uncached query both miss above and both queue on the throttle;
        # the first fills the cache while the second is still sleeping out the
        # interval, and without this the second woke up and asked arXiv for
        # something already on disk -- a duplicate request in exactly the
        # multi-process case the lock exists for.
        #
        # Inside rather than after, because a reservation is a claim on the
        # next slot and doing anything between claiming it and using it lets
        # the order slip: a process delayed there has already written its
        # timestamp, so a second can wait its three seconds, reserve, and fire
        # first, leaving the two real requests closer together than the
        # interval. Deciding not to fetch before reserving keeps the two
        # adjacent.
        served: list[tuple[tuple[PaperRecord, ...], bytes]] = []

        def _already_answered() -> bool:
            cached = self.library.cached_query(key, now=self._clock())
            if cached is None:
                return False
            body, fetched = cached
            try:
                served.append((_entries(body, url, _stamp(fetched)), body))
            except ArxivError:
                self.library.drop_query(key, body=body)
                return False
            return True

        # A reservation is a claim on the next slot, and it is only worth
        # anything if the request that claimed it is the next one out. A
        # process descheduled between reserving and transporting lets another
        # reserve and fire in front of it, and the two real requests then land
        # closer together than the interval both of them waited out. So the
        # claim is checked at the moment it is used: if somebody else has
        # reserved since, this one queues again rather than firing on a slot
        # that is no longer its own. Bounded, because losing the race three
        # times is a reason to stop insisting on being next -- see below --
        # rather than to keep giving way until nobody else wants a slot.
        #
        # WHERE THIS STOPS. The check is immediately before the transport and
        # cannot be joined to it: a process descheduled between the two still
        # fires on a slot that moved while it was off the CPU. Closing that
        # needs the lock held across the request itself, and the lock is
        # `required=False` with a timeout, so a process holding it for the
        # length of a network call pushes every other Hardy on the machine
        # into firing UNSYNCHRONISED once its wait expires. That trades a
        # window of a few instructions for a failure mode with no spacing at
        # all, which is the wrong way round.
        held = False
        for _ in range(3):
            reserved = self._throttle(_already_answered)
            if reserved is None:
                return served[0]
            if self.library.last_request() == reserved:
                held = True
                break
        if not held:
            # Giving way three times is a reason to stop insisting on being
            # next, not a reason to stop waiting. So the claim is made one
            # more time and NOT checked again: `_throttle` waits out the
            # interval under the lock and stamps on the way out, so this
            # request is still spaced from whoever went last by the same
            # mechanism as every other one. What is given up is only the
            # guarantee of being next, which is what was starving it.
            #
            # An earlier version slept the remaining interval here without
            # the lock and then transported. That was worse than what it
            # replaced: the sleep is seconds long, and anybody could reserve
            # and fire inside it, so the request that had waited longest went
            # out with no claim on the slot at all.
            reserved = self._throttle(_already_answered)
            if reserved is None:
                return served[0]
        body = self._transport(url, self._timeout)
        now = self._clock()
        # Parsed before it is cached, so the refusal below leaves nothing
        # behind to be served again.
        found = _entries(body, url, _stamp(now))
        self.library.cache_query(key, body, now=now)
        return found, body

    def _throttle(self, answered: Callable[[], bool] | None = None) -> float | None:
        """Wait out arXiv's interval, and say whether a request is still wanted.

        `answered` is asked once, under the lock, after the wait and before
        the reservation: it is the caller's chance to notice that somebody
        else answered the same question while this process was asleep. True
        means no request is made and no slot is claimed, and None comes back.

        Otherwise the timestamp reserved comes back, so the caller can check
        at the moment it transports that the slot is still its own.

        The clock is on disk, so two Hardy processes on one machine share one
        budget -- but a timestamp on disk is not on its own a mutex, and
        reading it, waiting, and writing it back as three separate steps let
        two idle processes both read the old value, both compute no wait, and
        both fire at once. The whole read-wait-reserve sequence therefore
        happens under a lock file, which is what makes the spacing hold
        between processes rather than only within one.

        The lock is not required: if another process is holding it for longer
        than the timeout, this falls back to the unsynchronised sequence
        rather than refusing to fetch. What is at stake here is politeness,
        and trading a real failure for a possible discourtesy is the wrong way
        round.

        A clock that has jumped backwards -- the file written under a
        different wall clock, or by a machine whose time was corrected -- is
        treated as "no idea", which waits the full interval rather than
        sleeping until a timestamp in the future.
        """
        with FileLock(
            self.library.lock_target(),
            timeout=self._lock_timeout,
            required=False,
        ):
            now = self._clock()
            since = now - self.library.last_request()
            if since < 0 or since >= self._interval:
                wait = 0.0 if since >= self._interval else self._interval
            else:
                wait = self._interval - since
            if wait > 0:
                self._sleep(wait)
            if answered is not None and answered():
                return None
            reserved = self._clock()
            self.library.note_request(reserved)
        return reserved


def _narrow(response: Any, seconds: float) -> None:
    """Give the response's socket `seconds` for its next read, if it has one."""
    for attribute in ("fp", "raw", "_sock"):
        response = getattr(response, attribute, None)
        if response is None:
            return
    try:
        response.settimeout(max(0.001, seconds))
    except (OSError, AttributeError, ValueError):
        return


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
