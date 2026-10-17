"""The durable cell log: one append-only file, owned under a lease, and
mended only where a crash can tear it."""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path

from pydantic import ValidationError

from hardy.algebra.contracts import CasError, CellRecord
from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock, LockTimeout


class CellJournal:
    """`cells.jsonl` and its siblings, read and written only through one guard.

    The guard is the only door the log is read or written through. It matters
    more here than anywhere else in Hardy: `cells.jsonl` is a versioned file a
    clone can ship as a symlink, and `_truncate` opens the log `r+b` and
    TRUNCATES it -- so a followed link would not merely append to whatever it
    named, it would destroy it. `Layout.ensure` cannot help: it runs once at
    startup and never enumerates this file.

    Beside the log sit its lease, which one process holds for as long as it
    owns the log, and the spend sidecar, which carries the session's running
    total of billed wall clock past the last append.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._guard = WriteGuard(path.parent)
        # Every durable record, in order, boundaries included. Only `append`
        # adds to it, and only once the record is on disk.
        self.records: list[CellRecord] = []
        self._lease: FileLock | None = None
        self._lease_identity: tuple[int, int] | None = None
        self._spend_name = path.name + ".spend.json"

    # ---------------------------------------------------------------- lease

    def own(self) -> float:
        """Own the log before reading its history or repairing a torn tail.

        Locking only an append cannot serialize two already-loaded histories:
        their sequence numbers, spend, and live namespaces would still differ.
        The OS lease therefore lasts until `release`, including between cells.

        Returns the session's total spend as the log and its sidecar record it.
        """
        # A deferred session has never loaded history or owned a kernel. Its
        # formerly unusable parent may now be a directory; prove it afresh.
        self._guard = WriteGuard(self.path.parent)
        self._guard.mkdir()
        lease = FileLock(self._guard.reserve(self.path.name + ".lock"), timeout=0)
        try:
            lease.__enter__()
        except LockTimeout as error:
            raise CasError(f"the CAS cell log cannot be owned ({self.path}): {error}") from error
        try:
            info = lease.path.stat()
            self._lease_identity = (info.st_dev, info.st_ino)
            self.records = self._load()
            recorded = self.records[-1].spent_ms / 1_000 if self.records else 0.0
            spent = max(recorded, self._load_spend())
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        self._lease = lease
        return spent

    @property
    def owned(self) -> bool:
        """Whether this process holds the log's lease."""
        return self._lease is not None

    def check_lease(self) -> None:
        """Refuse when the lease file on disk is no longer the one this holds."""
        if self._lease is None:
            raise CasError("the CAS cell log is not owned by this session")
        info = self._guard.reserve(self._lease.path.name).stat()
        if (info.st_dev, info.st_ino) != self._lease_identity:
            raise CasError("the CAS writer lease was replaced; close and reopen the session")

    def release(self) -> None:
        if self._lease is not None:
            self._lease.__exit__(None, None, None)
            self._lease = None

    # ---------------------------------------------------------------- spend

    def _load_spend(self) -> float:
        try:
            with self._guard.open(self._spend_name, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("expected finite nonnegative seconds")
            return float(value)
        except (FileNotFoundError, NotADirectoryError):
            # No sidecar exists. An unusable log parent is refused on write,
            # just as it was before accounting was persisted separately.
            return 0.0
        except (OSError, ValueError, OverflowError) as error:
            raise CasError(f"the CAS spend could not be read ({self._spend_name}): {error}") from error

    def write_spend(self, total_seconds: float) -> None:
        """Persist the running total, or raise `OSError`/`LayoutError`."""
        self._guard.mkdir()
        self._guard.write_bytes(
            self._spend_name,
            (json.dumps(total_seconds, allow_nan=False) + "\n").encode("utf-8"),
        )

    @property
    def spend_name(self) -> str:
        return self._spend_name

    # ------------------------------------------------------------------ log

    def _load(self) -> list[CellRecord]:
        if not self.path.exists():
            return []
        raw = self._repair_interrupted_append(self._read())
        # Bytes, not text: a torn write can cut a multi-byte character in half,
        # and decoding the whole file first would fail on the very record this
        # is here to survive.
        return [CellRecord.model_validate_json(line) for line in raw.split(b"\n") if line.strip()]

    def _repair_interrupted_append(self, raw: bytes) -> bytes:
        """Heal a final record that a crash cut off mid-write.

        Every append is one write of `record + "\\n"` followed by an fsync, so
        a log that does not end in a newline ended in an append that did not
        finish. That is the *only* damage tolerated here: a record with a
        terminator behind it was durable, and a bad one is corruption that must
        still be refused rather than quietly dropped.

        The partial bytes are removed from the file, not merely skipped. The
        log is appended to, so leaving them would glue the next record onto the
        fragment and turn a one-off interruption into a line that is neither
        final nor valid -- permanently unreadable, which is the failure this
        exists to prevent.
        """
        if not raw or raw.endswith(b"\n"):
            return raw
        head, separator, tail = raw.rpartition(b"\n")
        try:
            CellRecord.model_validate_json(tail)
        except ValidationError:
            kept = head + separator
            self._mend(truncate_to=len(kept))
            return kept
        # The record itself arrived; only its terminator was lost. Nothing is
        # discarded -- the newline is supplied so the next append starts on its
        # own line.
        self._mend(truncate_to=None)
        return raw + b"\n"

    def _read(self) -> bytes:
        """Every byte of the log, through the guard.

        Reading is guarded as strictly as writing. A symlinked log read at load
        time would have this session answer from cells recorded somewhere
        outside the project entirely, and only notice when the first append was
        refused -- long after it had replayed a stranger's history as its own.
        """
        with self._guard.open(self.path.name, "rb") as stream:
            return stream.read()

    def _truncate(self, size: int) -> None:
        with self._guard.open(self.path.name, "r+b") as stream:
            stream.truncate(size)
            stream.flush()
            os.fsync(stream.fileno())

    def _terminate(self) -> None:
        with self._guard.open(self.path.name, "ab") as stream:
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _mend(self, *, truncate_to: int | None) -> None:
        """Write the repair to disk, or leave the file alone if it will not take it.

        Best effort on purpose. This runs from `_load`, which runs when a
        session is constructed, so an `OSError` escaping here -- a read-only
        workspace, a full disk -- would stop the session being constructed at
        all. That is the same startup failure a torn record used to cause,
        arriving by a different route, and this method exists to fix that
        failure rather than to relocate it.

        A log that can be read but not repaired still opens: the fragment is
        skipped in memory, and `append` redoes the repair before it writes,
        which is where refusing it turns into a poisoned session rather than
        into a record glued onto a fragment.
        """
        with contextlib.suppress(OSError):
            if truncate_to is None:
                self._terminate()
            else:
                self._truncate(truncate_to)

    def _ensure_terminated(self) -> None:
        """Leave the log ending on a record boundary, or raise `OSError`.

        `_mend` swallows the failure it may hit, so a repair that the
        filesystem refused at load time exists only in memory: `_load` returns
        the healed bytes while the file still ends mid-record. The old claim
        that a filesystem refusing the repair would refuse the append too is
        simply untrue for a transient failure -- ENOSPC, a quota freed a minute
        later, an `fsync` that returned EINVAL once. The next append then lands
        on the fragment and welds it into a line that is neither final nor
        valid, which is exactly the permanently unreadable log
        `_repair_interrupted_append` exists to prevent.

        So the repair is redone here, where it is allowed to fail loudly: this
        runs inside `append`, whose caller has already decided that a log it
        cannot write means a poisoned session rather than a silent one.
        """
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if not size:
            return
        with self._guard.open(self.path.name, "rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return
        # Same rule as at load: a well-formed final record lost only its
        # terminator and is kept, anything else is a fragment and goes. The
        # whole file is read only on this path, which is the damaged one.
        head, separator, tail = self._read().rpartition(b"\n")
        try:
            CellRecord.model_validate_json(tail)
        except ValidationError:
            self._truncate(len(head + separator))
        else:
            self._terminate()

    def append(self, record: CellRecord) -> None:
        """Publish a record, durably first and only then in memory, or raise `OSError`.

        The order matters in a long-lived server. A record added to `records`
        before the write succeeded numbers and anchors everything after it, so
        a workspace that filled up or went read-only would keep answering from
        a history that a restart cannot reconstruct.
        """
        # Through the guard, which re-proves the directory and re-pins it if it
        # had to be recreated: a session whose workspace was deleted underneath
        # it must still be able to record the cell that just ran, and the
        # fresh directory has a fresh inode.
        self._guard.mkdir()
        self._ensure_terminated()
        with self._guard.open(self.path.name, "ab") as stream:
            stream.write(record.model_dump_json().encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.records.append(record)

    def remove(self) -> None:
        """Delete the log. Only for a scratch session's: a log anyone will
        reopen is the record and is never deleted."""
        self._guard.unlink(self.path.name, missing_ok=True)

    # -------------------------------------------------------------- queries

    @property
    def segment(self) -> int:
        """The live segment: the highest any record carries. A reset opens the next."""
        return max((record.segment for record in self.records), default=0)

    def cells(self) -> tuple[CellRecord, ...]:
        """The live segment's cells, accepted or not, without reset boundaries."""
        segment = self.segment
        return tuple(
            record
            for record in self.records
            if record.segment == segment and record.source.strip()
        )

    def accepted(self) -> tuple[CellRecord, ...]:
        """The live segment's accepted cells: what recovery and export rebuild from."""
        return tuple(record for record in self.cells() if record.accepted)
