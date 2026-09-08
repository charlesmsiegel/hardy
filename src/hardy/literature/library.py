"""Guarded immutable paper records, source archives and shared request budget."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..layout import LayoutError, guard_for, read_bytes, read_text
from ..storage import FileLock

from .metadata import (
    LOCK_SECONDS,
    QUERY_TTL_SECONDS,
    ArxivError,
    ArxivId,
    PaperRecord,
    digest,
    SourceFile,
    SourceManifest,
    SOURCE_DIR,
    SOURCE_MANIFEST,
    SOURCE_ARCHIVE,
    _coherent,
    _entries,
)

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
            # Asked of the staging tree rather than of `extraction.files`,
            # because a *directory* takes the name just as a file does and is
            # not reported as a file: `archive.bin/y.tex` extracted cleanly
            # and then met `IsADirectoryError` on the write below -- an
            # `OSError`, which no caller of this module catches, so the turn
            # ended instead of the refusal being read. `lexists` rather than
            # `exists` so a name is a name whatever it points at.
            if any(
                os.path.lexists(staging / name)
                for name in (SOURCE_MANIFEST, SOURCE_ARCHIVE)
            ):
                # The manifest is Hardy's own claim about the tree. An archive
                # carrying a file of that name would either overwrite it or be
                # overwritten by it, and either way one of the two would be
                # read as the other.
                raise ArxivError(
                    f"the archive contains an entry named {SOURCE_MANIFEST} or "
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

