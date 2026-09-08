"""arXiv transport and cached acquisition against an explicit paper library."""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from hardy.foundation.locking import FileLock
from hardy.literature.library import PaperLibrary
from hardy.literature.metadata import (
    DEFAULT_TIMEOUT_SECONDS,
    ENDPOINT,
    MAX_ARCHIVE_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_RESULTS,
    MIN_INTERVAL_SECONDS,
    READ_CHUNK_BYTES,
    SOURCE_ENDPOINT,
    USER_AGENT,
    ArxivError,
    PaperRecord,
    SourceManifest,
    Transport,
    _entries,
    _key,
    _stamp,
    parse_id,
)


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

