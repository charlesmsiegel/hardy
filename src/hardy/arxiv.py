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

What is stored is the metadata and the abstract, which is what the arXiv API
serves. The full source bundle is deliberately not fetched: unpacking a
third-party archive safely is its own piece of work (`FEATURES.md`, "treat
downloaded archives as hostile"), Hardy still has no process isolation to fall
back on, and a half-done version of that is worse than none. So `read_paper`
serves an abstract and says so, rather than implying a full text it does not
have.

Nothing here is trusted. The response is third-party XML: it is size-bounded
before parsing, refused outright if it carries a DOCTYPE (an entity bomb needs
one), and every field is read as text with no markup meaning.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .domain import FrozenModel
from .storage import atomic_write_bytes

ENDPOINT = "https://export.arxiv.org/api/query"
#: arXiv's own request: one call every three seconds from a given caller.
MIN_INTERVAL_SECONDS = 3.0
#: How long a cached search stays an answer. A day, because arXiv publishes
#: once a day: a shorter window spends requests to learn nothing, and a longer
#: one hides a paper that has since appeared.
QUERY_TTL_SECONDS = 24 * 60 * 60
#: A single API response. Generous for an Atom feed of fifty entries and far
#: under what a compressed bomb would need to matter.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RESULTS = 50
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
    entry carrying it is a claim someone else can check.
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
        authors = ", ".join(self.authors)
        head = [
            f"arXiv:{self.arxiv_id}",
            f"Title: {self.title}",
            f"Authors: {authors}",
        ]
        if self.categories:
            head.append(f"Categories: {', '.join(self.categories)}")
        if self.published:
            head.append(f"Submitted: {self.published}")
        if self.updated and self.updated != self.published:
            head.append(f"This version: {self.updated}")
        if self.doi:
            head.append(f"DOI: {self.doi}")
        if self.journal_ref:
            head.append(f"Journal reference: {self.journal_ref}")
        return "\n".join(head) + "\n\nAbstract\n" + self.abstract + "\n"

    def summary(self) -> str:
        """One line, for a search result the reader is scanning."""
        if not self.authors:
            return f"{self.arxiv_id}  {self.title}"
        who = self.authors[0] + (" et al." if len(self.authors) > 1 else "")
        return f"{self.arxiv_id}  {self.title} ({who})"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PaperLibrary:
    """The records on disk, and the rules that keep them immutable.

    Machine-local by design. The library is a cache of third-party bytes and
    is never committed; what travels with a clone is the bibliography, whose
    entries carry the digest of what was read. A clone with an empty library
    can still be told which bytes a citation was made against.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.records = root / "records"
        self.queries = root / "queries"
        self.state_path = root / "state.json"

    def path_for(self, identifier: ArxivId) -> Path:
        return self.records / identifier.storage_name

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
        directory = self.path_for(identifier)
        try:
            record = PaperRecord.model_validate_json(
                (directory / "record.json").read_text(encoding="utf-8")
            )
            content = (directory / "content.txt").read_text(encoding="utf-8")
        except (OSError, ValueError) as error:
            raise ArxivError(f"the stored record for {identifier} could not be read: {error}") from error
        if digest(content) != record.content_sha256:
            raise ArxivError(
                f"the stored content for {identifier} does not match its recorded digest; "
                "the library has been edited and this record can no longer be cited"
            )
        return record

    def stored(self) -> tuple[str, ...]:
        """Every versioned identifier the library holds, sorted."""
        if not self.records.is_dir():
            return ()
        return tuple(
            sorted(
                child.name.replace("_", "/")
                for child in self.records.iterdir()
                if (child / "record.json").is_file()
            )
        )

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
        self.records.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.records))
        try:
            (staging / "content.txt").write_text(record.content(), encoding="utf-8")
            (staging / "response.xml").write_bytes(response)
            (staging / "record.json").write_text(
                record.model_dump_json(indent=2) + "\n", encoding="utf-8"
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

    def cached_query(self, key: str, *, now: float, ttl: float = QUERY_TTL_SECONDS) -> bytes | None:
        """The stored answer to this query, if it is still fresh."""
        path = self.queries / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched = float(payload["fetched_at"])
            body = str(payload["body"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if now - fetched > ttl:
            return None
        return body.encode("utf-8")

    def cache_query(self, key: str, body: bytes, *, now: float) -> None:
        atomic_write_bytes(
            self.queries / f"{key}.json",
            json.dumps(
                {"fetched_at": now, "body": body.decode("utf-8", errors="replace")},
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def last_request(self) -> float:
        try:
            return float(json.loads(self.state_path.read_text(encoding="utf-8"))["last_request"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def note_request(self, when: float) -> None:
        """Record that a request is being made, before it is made.

        Written first on purpose. A request recorded only on success lets a
        run of failures hammer arXiv at whatever rate the failures come back
        -- which is the moment a service least wants to be hammered.
        """
        atomic_write_bytes(
            self.state_path, json.dumps({"last_request": when}).encode("utf-8")
        )


Transport = Callable[[str, float], bytes]


def _http(url: str, timeout: float) -> bytes:
    """Read a whole response under one deadline, size-bounded.

    The same shape as `retrieval._fetch_url` and for the same reason: a
    per-read socket timeout is not a bound on the transfer, because a server
    dripping one byte at a time resets it forever.
    """
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        opened = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https endpoint
    except urllib.error.HTTPError as error:
        raise ArxivError(f"arXiv answered HTTP {error.code} {error.reason}") from error
    except OSError as error:
        raise ArxivError(f"arXiv could not be reached: {error}") from error
    with opened as response:
        chunks: list[bytes] = []
        received = 0
        wanted = MAX_RESPONSE_BYTES + 1
        while received < wanted:
            if time.monotonic() >= deadline:
                raise ArxivError(f"arXiv exceeded its {timeout:g}s deadline with {received} bytes read")
            chunk = response.read(min(READ_CHUNK_BYTES, wanted - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
    body = b"".join(chunks)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ArxivError(f"the arXiv response exceeds {MAX_RESPONSE_BYTES} bytes")
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
    ) -> None:
        self.library = library
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._timeout = timeout
        self._interval = interval

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
        return _entries(self._get(url), url, self._stamp())[:bounded]

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
        body = self._get(url)
        found = _entries(body, url, self._stamp())
        if not found:
            raise ArxivError(f"arXiv returned no paper for {identifier}")
        record = found[0]
        resolved = record.identifier
        if identifier.versioned and str(resolved) != str(identifier):
            raise ArxivError(
                f"asked arXiv for {identifier} and it answered with {resolved}; "
                "refusing to store one paper under another's identifier"
            )
        if self.library.holds(resolved):
            return self.library.read(resolved), True
        return self.library.admit(record, body), False

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds")

    def _url(self, parameters: dict[str, str]) -> str:
        return f"{ENDPOINT}?{urllib.parse.urlencode(parameters)}"

    def _get(self, url: str) -> bytes:
        """The body for `url`, from the cache or from arXiv at a polite pace."""
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        now = self._clock()
        cached = self.library.cached_query(key, now=now)
        if cached is not None:
            return cached
        self._throttle()
        body = self._transport(url, self._timeout)
        self.library.cache_query(key, body, now=self._clock())
        return body

    def _throttle(self) -> None:
        """Wait out arXiv's interval, counting from the last request anywhere.

        The clock is on disk, so two Hardy processes on one machine share one
        budget. A clock that has jumped backwards -- the file written under a
        different wall clock, or by a machine whose time was corrected -- is
        treated as "no idea", which waits the full interval rather than
        sleeping until a timestamp in the future.
        """
        now = self._clock()
        since = now - self.library.last_request()
        if since < 0 or since >= self._interval:
            wait = 0.0 if since >= self._interval else self._interval
        else:
            wait = self._interval - since
        if wait > 0:
            self._sleep(wait)
        self.library.note_request(self._clock())


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
    if re.search(r"<!DOCTYPE", text[:4096], re.IGNORECASE):
        raise ArxivError("the arXiv response carries a DOCTYPE declaration; refusing to parse it")
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ArxivError(f"the arXiv response was not valid XML: {error}") from error


def _entry(
    entry: ElementTree.Element,
    source_url: str,
    fetched_at: str,
    response_digest: str,
) -> PaperRecord | None:
    """One `<entry>` as a record, or None when it is not a paper.

    arXiv answers a query for a malformed id with an entry whose `id` is an
    error URL and whose title is "Error". Reading that as a paper would put a
    record named `api/errors` in the library, so an entry whose id is not an
    arXiv identifier is dropped -- the caller reports "no paper", which is
    what happened.
    """
    raw_id = _text(entry.find(f"{ATOM}id"))
    try:
        identifier = parse_id(raw_id)
    except ArxivError:
        return None
    if not identifier.versioned:
        # arXiv's `<id>` is always versioned. One that is not means the feed
        # is not what this code was written against, and guessing a version
        # would put a record under an identifier arXiv never used.
        return None
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
