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
    """
    return textwrap.wrap(line, ABSTRACT_COLUMNS, break_long_words=True) or [""]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        if str(record.identifier) != str(identifier):
            raise ArxivError(
                f"the record stored under {identifier} says it is {record.arxiv_id}; "
                "refusing to serve one paper under another's identifier"
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
                if not child.is_symlink() and (child / "record.json").is_file()
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

    def cache_query(self, key: str, body: bytes, *, now: float) -> None:
        guard, name = self._guard(f"queries/{key}.json")
        guard.write_bytes(
            name,
            json.dumps(
                {"fetched_at": now, "body": body.decode("utf-8", errors="replace")},
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def drop_query(self, key: str) -> None:
        """Forget one cached answer.

        For a body that turned out not to be an answer at all. A cached
        maintenance page would otherwise be served for the whole TTL, so every
        retry of a search would fail identically for a day after arXiv had
        recovered.
        """
        try:
            guard, name = self._guard(f"queries/{key}.json")
            guard.unlink(name, missing_ok=True)
        except (OSError, LayoutError):
            return

    def last_request(self) -> float:
        try:
            return float(json.loads(read_text(self.throttle, "state.json"))["last_request"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0

    def note_request(self, when: float) -> None:
        """Record that a request is being made, before it is made.

        Written first on purpose. A request recorded only on success lets a
        run of failures hammer arXiv at whatever rate the failures come back
        -- which is the moment a service least wants to be hammered.
        """
        guard, _ = self._throttle_guard()
        guard.write_bytes("state.json", json.dumps({"last_request": when}).encode("utf-8"))


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
        wanted = MAX_RESPONSE_BYTES + 1
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
            self.library.drop_query(_key(url))
            raise ArxivError(
                f"asked arXiv for {identifier} and it answered with {resolved}; "
                "refusing to store one paper under another's identifier"
            )
        if self.library.holds(resolved):
            return self.library.read(resolved), True
        return self.library.admit(record, body), False

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
                self.library.drop_query(key)
        self._throttle()
        # Asked again on the way out of the wait. Two processes wanting the
        # same uncached query both miss above and both queue on the throttle;
        # the first fills the cache while the second is still sleeping out the
        # interval, and without this the second woke up and asked arXiv for
        # something already on disk -- a duplicate request in exactly the
        # multi-process case the lock exists for. This does not make the miss
        # atomic: the transport is deliberately outside the lock, since
        # serialising every request machine-wide is a different and much
        # larger promise than spacing them. It removes the waste that the
        # waiting itself creates.
        cached = self.library.cached_query(key, now=self._clock())
        if cached is not None:
            body, fetched = cached
            try:
                return _entries(body, url, _stamp(fetched)), body
            except ArxivError:
                self.library.drop_query(key)
        body = self._transport(url, self._timeout)
        now = self._clock()
        # Parsed before it is cached, so the refusal below leaves nothing
        # behind to be served again.
        found = _entries(body, url, _stamp(now))
        self.library.cache_query(key, body, now=now)
        return found, body

    def _throttle(self) -> None:
        """Wait out arXiv's interval, counting from the last request anywhere.

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
            self.library.note_request(self._clock())


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
