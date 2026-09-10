"""Serialized CAS cells, durable replay state, and session budget ownership."""
from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from hardy.algebra.contracts import (
    _ASKED,
    _INSISTED,
    DESYNCHRONISED,
    HEADER_BYTES,
    INTERRUPTED,
    SENTINEL_BEGIN,
    SENTINEL_END,
    TIMED_OUT,
    CasError,
    CellOutcome,
    CellRecord,
    RebuildReport,
    _why_unverified,
    reproduces,
    unobservable,
)
from hardy.algebra.kernel import _Kernel
from hardy.foundation.files import LayoutError, WriteGuard
from hardy.foundation.locking import FileLock, LockTimeout
from hardy.foundation.process import INTERRUPT_GRACE_SECONDS
from hardy.workflows.contracts import RunLimits


class CasSession:
    """A durable cell log and the kernel that its accepted cells describe."""

    def __init__(
        self,
        *,
        backend: Any,
        command: Path | None,
        log_path: Path,
        limits: RunLimits,
        cwd: Path | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.backend = backend
        self.command = command
        self.log_path = log_path
        # The only door the cell log is read or written through. It matters
        # more here than anywhere else in Hardy: `cells.jsonl` is a versioned
        # file a clone can ship as a symlink, and `_truncate_log` opens the log
        # `r+b` and TRUNCATES it -- so a followed link would not merely append
        # to whatever it named, it would destroy it. `Layout.ensure` cannot
        # help: it runs once at startup and never enumerates this file.
        #
        # An unusable absent log remains constructible, but no kernel may run
        # before its directory can be created and its writer lease acquired.
        self._log = WriteGuard(log_path.parent)
        self.limits = limits
        self.cwd = cwd or log_path.parent
        self.observe = observe
        self.state: Literal["cold", "live", "dead", "poisoned"] = "cold"
        self.version: str | None = None
        # This process's spend, and what `cas_session_seconds` bounds: a guard
        # against a runaway computation, not the session's figure. That is
        # `total_spent_seconds`, below.
        self.spent_seconds = 0.0
        # One stateful process behind one stdin stream, reachable from chat,
        # staged runs, and MCP. The lock belongs to the resource, not a caller.
        self._lock = threading.RLock()
        self._kernel: _Kernel | None = None
        # Both read and written without the lock, on purpose: the thread that
        # holds it is the one inside `execute`, waiting on the kernel, and that
        # is precisely the thread an interrupt has to reach. An `Event` rather
        # than a bool because these cross threads with no lock between them.
        #
        # `_in_flight` is what makes an interrupt safe to send at all. A signal
        # delivered between cells lands on a driver blocked reading stdin,
        # where it has no cell to abandon and nothing to answer with, so
        # `interrupt` refuses unless a cell is actually out there.
        self._in_flight = threading.Event()
        self._interrupted = threading.Event()
        # The window between the frame being built and the kernel having read
        # it. The write is to a pipe, so a kernel that has stopped reading --
        # the deaf one this whole change exists for -- lets a large enough cell
        # fill the buffer and block it. Nothing is in flight yet, so `interrupt`
        # has nothing to ask; but `escalate` does, because killing the kernel
        # is what ends the write, and without that a session could hang there
        # with no deadline running and no press that could reach it.
        self._sending = threading.Event()
        # Held across "write the frame and arm the flag" and across "decide
        # whether to signal", so the two cannot interleave. Without it a press
        # landing between the two reached a driver that was still idle: the
        # signal was swallowed by the between-cells handler, the cell went out
        # immediately afterwards, and the only press was already spent -- so
        # the cell ran on until the grace expired and took the kernel with it,
        # which is the exact loss interrupting exists to avoid.
        self._signal_lock = threading.Lock()
        # How hard a stop asked for while no cell was in flight was asked,
        # remembered so the cell about to go out is stopped rather than the
        # press being lost. A level rather than a flag for the same reason the
        # register in `process` keeps one: if both presses land before the cell
        # is sent, a flag would give it the first press's signal and lose the
        # second, so a deaf cell would sit out a grace the user had already
        # declined to wait for. Lifted by `resume`, at the start of the next
        # turn or command, exactly as that register is.
        self._stop_level = 0
        self._records: list[CellRecord] = []
        self._lease: FileLock | None = None
        self._lease_identity: tuple[int, int] | None = None
        self._closed = False
        # The session's own figure: every second of CAS wall clock billed to
        # this log, across every process that has opened it. Read back from the
        # last record, which carries the running total as of its append, and
        # moved with `spent_seconds` from then on. Reported, never enforced: a
        # research workspace may legitimately be open for months, and a
        # lifetime cap attached to it would eventually refuse work for reasons
        # that have nothing to do with the work.
        #
        # The sidecar also includes charges made after the last cell append.
        self._spend_name = log_path.name + ".spend.json"
        self.total_spent_seconds = 0.0
        try:
            self._load_owned_history()
        except OSError:
            # Preserve lazy construction when the absent log's parent cannot
            # yet be created. The first operation retries before doing work.
            if self.log_path.exists():
                raise

    def _load_owned_history(self) -> None:
        """Own the log before reading its history or repairing a torn tail.

        Locking only an append cannot serialize two already-loaded histories:
        their sequence numbers, spend, and live namespaces would still differ.
        The OS lease therefore lasts until close, including between cells.
        """
        # A deferred session has never loaded history or owned a kernel. Its
        # formerly unusable parent may now be a directory; prove it afresh.
        self._log = WriteGuard(self.log_path.parent)
        self._log.mkdir()
        lease = FileLock(self._log.reserve(self.log_path.name + ".lock"), timeout=0)
        try:
            lease.__enter__()
        except LockTimeout as error:
            raise CasError(f"the CAS cell log cannot be owned ({self.log_path}): {error}") from error
        try:
            info = lease.path.stat()
            self._lease_identity = (info.st_dev, info.st_ino)
            self._records = self._load()
            self._restore_spend()
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        self._lease = lease

    def _restore_spend(self) -> None:
        recorded_spend = (
            self._records[-1].spent_ms / 1_000 if self._records else 0.0
        )
        self.total_spent_seconds = max(recorded_spend, self._load_spend())

    def _require_owner(self) -> None:
        if self._closed:
            raise CasError("the CAS session is closed; open a new session before running or writing")
        try:
            if self._lease is None:
                self._load_owned_history()
            assert self._lease is not None
            info = self._log.reserve(self._lease.path.name).stat()
            if (info.st_dev, info.st_ino) != self._lease_identity:
                raise CasError("the CAS writer lease was replaced; close and reopen the session")
        except (OSError, CasError) as error:
            self._drop_kernel()
            self.state = "poisoned"
            raise CasError(f"the CAS cell log could not be written ({self.log_path}): {error}") from error

    def _load_spend(self) -> float:
        try:
            with self._log.open(self._spend_name, "r", encoding="utf-8") as stream:
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

    @contextlib.contextmanager
    def hold(self):
        """Keep the session to one caller for the duration.

        The lock is the kernel's, and `execute` takes it per cell. An export
        is longer than a cell and reads the log and the budget throughout:
        held only per call, a cell could land between its replay and its
        render and the manifest would describe a session that had already
        moved on. Public because the export lives in another module and the
        lock does not.
        """
        with self._lock:
            self._require_owner()
            yield

    # ------------------------------------------------------------------ log

    def _load(self) -> list[CellRecord]:
        if not self.log_path.exists():
            return []
        raw = self._repair_interrupted_append(self._read_log())
        records = []
        for line in raw.split(b"\n"):
            # Bytes, not text: a torn write can cut a multi-byte character in
            # half, and decoding the whole file first would fail on the very
            # record this is here to survive.
            if line.strip():
                records.append(CellRecord.model_validate_json(line))
        return records

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
            self._mend_log(truncate_to=len(kept))
            return kept
        # The record itself arrived; only its terminator was lost. Nothing is
        # discarded -- the newline is supplied so the next append starts on its
        # own line.
        self._mend_log(truncate_to=None)
        return raw + b"\n"

    def _read_log(self) -> bytes:
        """Every byte of the log, through the guard.

        Reading is guarded as strictly as writing. A symlinked log read at load
        time would have this session answer from cells recorded somewhere
        outside the project entirely, and only notice when the first append was
        refused -- long after it had replayed a stranger's history as its own.
        """
        with self._log.open(self.log_path.name, "rb") as stream:
            return stream.read()

    def _truncate_log(self, size: int) -> None:
        with self._log.open(self.log_path.name, "r+b") as stream:
            stream.truncate(size)
            stream.flush()
            os.fsync(stream.fileno())

    def _terminate_log(self) -> None:
        with self._log.open(self.log_path.name, "ab") as stream:
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _mend_log(self, *, truncate_to: int | None) -> None:
        """Write the repair to disk, or leave the file alone if it will not take it.

        Best effort on purpose. This runs from `_load`, which runs from
        `__init__`, so an `OSError` escaping here -- a read-only workspace, a
        full disk -- would stop the session being constructed at all. That is
        the same startup failure a torn record used to cause, arriving by a
        different route, and this method exists to fix that failure rather than
        to relocate it.

        A log that can be read but not repaired still opens: the fragment is
        skipped in memory, and `_append` redoes the repair before it writes,
        which is where refusing it turns into a poisoned session rather than
        into a record glued onto a fragment.
        """
        with contextlib.suppress(OSError):
            if truncate_to is None:
                self._terminate_log()
            else:
                self._truncate_log(truncate_to)

    def _ensure_terminated(self) -> None:
        """Leave the log ending on a record boundary, or raise `OSError`.

        `_mend_log` swallows the failure it may hit, so a repair that the
        filesystem refused at load time exists only in memory: `_load` returns
        the healed bytes while the file still ends mid-record. The old claim
        that a filesystem refusing the repair would refuse the append too is
        simply untrue for a transient failure -- ENOSPC, a quota freed a minute
        later, an `fsync` that returned EINVAL once. The next append then lands
        on the fragment and welds it into a line that is neither final nor
        valid, which is exactly the permanently unreadable log
        `_repair_interrupted_append` exists to prevent.

        So the repair is redone here, where it is allowed to fail loudly: this
        runs inside `_append`, whose caller has already decided that a log it
        cannot write means a poisoned session rather than a silent one.
        """
        try:
            size = self.log_path.stat().st_size
        except FileNotFoundError:
            return
        if not size:
            return
        with self._log.open(self.log_path.name, "rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) == b"\n":
                return
        # Same rule as at load: a well-formed final record lost only its
        # terminator and is kept, anything else is a fragment and goes. The
        # whole file is read only on this path, which is the damaged one.
        head, separator, tail = self._read_log().rpartition(b"\n")
        try:
            CellRecord.model_validate_json(tail)
        except ValidationError:
            self._truncate_log(len(head + separator))
        else:
            self._terminate_log()

    def _append(self, record: CellRecord) -> CellRecord:
        """Publish a record, durably first and only then in memory.

        The order matters in a long-lived server. A record added to `_records`
        before the write succeeded numbers and anchors everything after it, so
        a workspace that filled up or went read-only would keep answering from
        a history that a restart cannot reconstruct. If the log cannot be
        written the cell has already run, so what the kernel holds is no longer
        described by anything durable: the session is poisoned rather than
        allowed to continue on state it can never explain.
        """
        self._require_owner()
        try:
            # Through the guard, which re-proves the directory and re-pins it
            # if it had to be recreated: a session whose workspace was deleted
            # underneath it must still be able to record the cell that just
            # ran, and the fresh directory has a fresh inode.
            self._log.mkdir()
            self._ensure_terminated()
            with self._log.open(self.log_path.name, "ab") as stream:
                stream.write(record.model_dump_json().encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            self._drop_kernel()
            self.state = "poisoned"
            raise CasError(
                f"the CAS cell log could not be written ({self.log_path}): {error}. "
                "The session is poisoned because its live state is no longer recorded. "
                "Reset it to start clean."
            ) from None
        self._records.append(record)
        if self.observe is not None:
            self.observe({"type": "cas", "record": record.model_dump(mode="json")})
        return record

    def records(self) -> tuple[CellRecord, ...]:
        """Every durable record, boundaries included."""
        return tuple(self._records)

    @property
    def segment(self) -> int:
        return max((record.segment for record in self._records), default=0)

    def accepted(self) -> tuple[CellRecord, ...]:
        segment = self.segment
        return tuple(
            record
            for record in self._records
            if record.accepted and record.segment == segment and record.source.strip()
        )

    def cells(self) -> tuple[CellRecord, ...]:
        segment = self.segment
        return tuple(
            record
            for record in self._records
            if record.segment == segment and record.source.strip()
        )

    # -------------------------------------------------------------- kernel

    def _start(self) -> None:
        self._require_owner()
        cap = self.limits.cas_output_bytes
        argv = self.backend.argv(self.command, cap)
        # A length-framed backend clips its own output to `cap`, so the reader
        # keeps headroom: tripping the retention limit there would mean a frame
        # that can never be assembled, and is treated as a broken kernel rather
        # than as a large answer.
        #
        # The factor is six, and it is not slack. `cas_driver.run_cell`
        # budgets stdout, stderr and value_repr *jointly* against `cap`, so a
        # payload carries at most `cap` bytes of captured text -- but it
        # carries them JSON-escaped, and a control byte becomes a six-byte
        # backslash-u escape. A cell printing NUL bytes is legal, so anything
        # smaller is a cap a legal cell can walk past, and walking past it
        # means a frame that never assembles, a cell that waits out the whole
        # timeout, and a kernel dropped with all its state.
        retain = cap if self.backend.framing == "sentinel" else cap * 6 + 65_536
        try:
            self._kernel = _Kernel(
                argv, self.cwd, retain, getattr(self.backend, "environment", {}),
                merge_stderr=self.backend.framing == "sentinel",
            )
        except (OSError, ValueError) as error:
            self.state = "dead"
            raise CasError(
                f"could not start the {self.backend.name} kernel "
                f"({' '.join(argv)}): {error}"
            ) from None
        self.state = "live"

    def probe_version(self) -> str:
        """Ask the kernel what it is, in a kernel that is then thrown away.

        Doubles as the smoke test: a backend that cannot answer this is not a
        working backend, however present it looks.

        The probe is thrown away because it is a cell like any other. For the
        default backend the version source is a trailing expression, so the
        driver binds its value to `_` -- and a session discovered that way
        would start with state no fresh kernel has, so a first cell mentioning
        `_` would work live and fail in export or recovery. Discarding the
        kernel leaves the session cold, and the next cell builds it from the
        accepted log, which is the only state anything else can reconstruct.
        """
        with self._lock:
            self._require_owner()
            if self._kernel is None:
                self._start()
            try:
                outcome = self._send(self.backend.version_source)
            finally:
                self._discard_kernel()
            if outcome.status != "ok":
                raise CasError(
                    f"{self.backend.name} kernel did not answer a version query: "
                    f"{(outcome.stderr or outcome.stdout).strip()[:200]}"
                )
            raw = self.backend.parse_version(outcome.value_repr or outcome.stdout)
            self.version = raw.strip().strip("'\"") or "unknown"
            return self.version

    def _extractor(self, nonce: str, fed: str = "") -> Callable[[bytes], Any]:
        """`fed` is the framed text this cell was sent, for `sanitize` to
        recognise the interpreter's echo of it. Unused by the length path,
        whose driver never echoes anything."""
        if self.backend.framing == "length":

            def extract_length(raw: bytes) -> Any:
                if len(raw) < HEADER_BYTES:
                    return None
                try:
                    length = int(raw[:HEADER_BYTES].decode("ascii"))
                except (ValueError, UnicodeDecodeError):
                    return DESYNCHRONISED
                body = raw[HEADER_BYTES : HEADER_BYTES + length]
                if len(body) < length:
                    return None
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return DESYNCHRONISED
                outcome = CellOutcome(
                    status=payload.get("status", "ok"),
                    stdout=payload.get("stdout", ""),
                    stderr=payload.get("stderr", ""),
                    value_repr=payload.get("value_repr", ""),
                    capture_truncated=bool(payload.get("capture_truncated")),
                    state_digest=str(payload.get("state_digest", "")),
                )
                return outcome, HEADER_BYTES + length

            return extract_length

        begin = SENTINEL_BEGIN.format(nonce=nonce).encode("utf-8")
        end = SENTINEL_END.format(nonce=nonce).encode("utf-8")
        # A backend that echoes stdin (confirmed of Macaulay2: it prints
        # `iN : ` followed by the exact line it was fed, tty or not) writes
        # the marker text a *second* time before the real one: once inside
        # its own echoed source line (`iN : << "«marker»" << endl;`), and
        # only afterwards as the bare line the `<<` statement actually
        # prints. A bare `raw.find(marker)` matches the first, embedded
        # occurrence, and every cell's captured body ends up carrying a
        # fragment of that echoed statement.
        #
        # The two occurrences are told apart by what immediately follows
        # them, not by what precedes them -- a preceding newline is not a
        # reliable signal, since an in-flight prompt from the *previous* cell
        # can legitimately sit directly in front of the real marker too (see
        # `test_state_is_not_polluted_by_the_previous_cells_prompt`, which
        # exists precisely to pin that down). The embedded copy is always
        # immediately followed by the fixed tail of the echo template itself
        # (`" << endl;` for Macaulay2, `");` for Singular) because that is
        # what is on the rest of the line we sent; the real, bare-printed
        # copy never is. Skipping any occurrence with that tail right after
        # it, and continuing the search past it, finds the real one
        # regardless of what backend-specific noise precedes either.
        tail = self.backend.echo.rsplit("{marker}", 1)[1].encode("utf-8")

        def _find_marker(raw: bytes, marker: bytes, after: int) -> int:
            pos = after
            while (hit := raw.find(marker, pos)) != -1:
                echoed_end = hit + len(marker)
                following = raw[echoed_end : echoed_end + len(tail)]
                # A read boundary is not a language boundary. Until a byte
                # distinguishes a bare marker from its echoed source, wait.
                if tail.startswith(following) and len(following) < len(tail):
                    return -1
                if following == tail:
                    pos = echoed_end
                    continue
                return hit
            return -1

        def extract_sentinel(raw: bytes) -> Any:
            kernel = self._kernel
            # A pipe preserves write order, so whatever the previous cell
            # printed -- including a prompt still arriving when this cell was
            # armed -- is necessarily before this cell's begin marker in the
            # stream, however late it happens to show up. Waiting for the
            # begin marker before looking at anything excludes it without
            # ever having to guess whether it has fully arrived.
            begin_at = _find_marker(raw, begin, 0)
            if begin_at == -1:
                return None
            start = begin_at + len(begin)
            # `marker_seen` is the *scanner's* answer, and the scanner is a
            # bare `in` test over a rolling tail: it cannot tell a marker the
            # interpreter printed from a marker the interpreter echoed off its
            # own stdin. Macaulay2 echoes, and it flushes the echoed end-marker
            # statement a statement before it runs it -- so trusting
            # `marker_seen` on its own ends a cell at the echo and drops
            # whatever the cell was still writing. It exists for exactly one
            # case, where the real end marker's bytes were dropped at the
            # retention cap and only the scan could have seen them, so it is
            # only consulted when retention actually overflowed.
            truncated_past_the_marker = (
                kernel is not None and kernel.truncated and kernel.marker_seen
            )
            end_at = _find_marker(raw, end, start)
            if end_at == -1 and not truncated_past_the_marker:
                return None
            if end_at != -1:
                body = raw[start:end_at].decode("utf-8", errors="replace")
                consumed = end_at + len(end)
            else:
                # Retention stopped before the end marker; scanning continued
                # via `marker_seen`, but the bytes themselves are gone.
                body = raw[start:].decode("utf-8", errors="replace")
                consumed = len(raw)
            body = self.backend.sanitize(body, fed)
            outcome = CellOutcome(
                # Both child descriptors share one OS pipe. Errors written
                # before the marker cannot arrive behind it in another drain.
                status=self.backend.classify(body),
                stdout=body,
                capture_mode="merged",
                capture_truncated=bool(kernel and kernel.truncated),
            )
            return outcome, consumed

        return extract_sentinel

    def _send(self, source: str, seconds: float | None = None) -> CellOutcome:
        """One round trip. Assumes the lock and a started kernel.

        `seconds` is the deadline actually applied, which is not always
        `cas_cell_seconds`: a caller with less session budget left than that
        must not be able to buy a full cell's worth of wall clock with it.
        """
        limit = self.limits.cas_cell_seconds if seconds is None else seconds
        kernel = self._kernel
        assert kernel is not None

        def incomplete(**fields: Any) -> CellOutcome:
            # Without a closing marker this is a bounded partial transcript,
            # including framing. Preserve it as diagnostics, never as success.
            if self.backend.framing == "sentinel":
                fields.update(
                    stdout=kernel.stdout_text(), capture_mode="merged",
                    capture_truncated=kernel.truncated,
                )
            return CellOutcome(**fields)

        nonce = f"{time.monotonic_ns():x}"
        if self.backend.framing == "length":
            kernel.clear()
        else:
            end = SENTINEL_END.format(nonce=nonce).encode()
            kernel.rearm(end)
        # The frame is built inside the lock, below, because the bit it carries
        # is the stop level: read outside, a press landing between the read and
        # the lock would be recorded while the frame already said no stop was
        # wanted -- and the driver, told that, would discard the very signal
        # that press sent.
        with self._signal_lock:
            # Cleared before the cell is out there, and only then armed: an
            # interrupt asked for while nothing was running would otherwise
            # stop the *next* cell, which nobody asked to stop.
            self._interrupted.clear()
            frame = self.backend.frame(source, nonce, self._stop_level > 0)
            self._sending.set()
        # Outside the lock. `write` to a full pipe blocks until the kernel
        # reads, and `interrupt` and `escalate` are called from the terminal's
        # own event loop -- so holding the lock across the write would let a
        # kernel that has stopped reading freeze the interface, with the cell's
        # deadline not yet running and the one press that could end it unable
        # to be delivered. A press landing in this window is remembered in
        # `_stop_level` and spent below, at the level it was asked at.
        sent = kernel.send(frame)
        with self._signal_lock:
            self._sending.clear()
            self._in_flight.set()
            # A stop asked for before this cell existed still applies to it:
            # the press was aimed at the work, and the work is now this. It
            # goes out here, under the lock, at the level it was asked at --
            # a second press that landed before the cell did still means kill
            # rather than ask.
            if self._stop_level and sent:
                self._interrupted.set()
                if self._stop_level >= _INSISTED:
                    kernel.kill(immediate=True)
                else:
                    kernel.interrupt()
        try:
            if not sent:
                if self._interrupted.is_set():
                    # The write failed because the second press killed the
                    # kernel out from under it -- which is what ends a write to
                    # a kernel that has stopped reading. Hardy stopped this, so
                    # the record says so; `kernel_died` would blame the
                    # toolchain for what Esc did.
                    return incomplete(
                        status="interrupted",
                        stderr=(
                            "the kernel was not reading its input, so the cell was "
                            "never sent; it was stopped and its state is gone"
                        ),
                        kernel_lost=True,
                        signalled=True,
                    )
                return incomplete(status="kernel_died", stderr=kernel.stderr_text())
            deadline = time.monotonic() + limit
            # The frame, not the source: an echoing interpreter echoes the
            # sentinel statements bracketing the cell as readily as the cell
            # itself, and a multi-line cell's second line onward comes back
            # under a continuation indent rather than a prompt of its own.
            fed = frame.decode("utf-8", errors="replace")
            reply = kernel.read_reply(
                self._extractor(nonce, fed), deadline, self._interrupted
            )
        finally:
            # Both under the lock `interrupt` takes, so a press either lands
            # before this (and is recorded against the cell) or finds nothing
            # in flight and is not. Read here rather than at the bottom of the
            # method: between the reply arriving and this line, a press is
            # still aimed at a cell nobody could yet know was over, and it is
            # resolved conservatively -- the cell is recorded and reported, and
            # simply not accepted, which costs a rerun rather than correctness.
            with self._signal_lock:
                signalled = self._interrupted.is_set()
                self._in_flight.clear()
        if reply is INTERRUPTED:
            # Asked to stop and did not answer within the grace. Whatever it is
            # doing, it is not talking, and a kernel that cannot be spoken to
            # cannot be replayed from or built on -- so it goes, exactly as a
            # timed-out one does. This is the case the interrupt exists to
            # avoid, not the one it produces when it works.
            return incomplete(
                status="interrupted",
                stderr=(
                    "the cell was interrupted and the kernel did not answer within "
                    f"{INTERRUPT_GRACE_SECONDS:g}s, so it was stopped and its state is gone"
                ),
                kernel_lost=True,
            )
        if reply is TIMED_OUT:
            return incomplete(
                status="timeout",
                stderr=f"cell exceeded its {limit:g}s limit",
            )
        if reply is None or reply is DESYNCHRONISED:
            # A stream that desynchronised cannot be trusted to be answering
            # the cell we sent, so it is a death rather than a bad answer.
            if self._interrupted.is_set():
                # Died *because* it was signalled. Not every interpreter turns
                # an interrupt into a traceback it can report -- one still
                # starting up has no handler installed yet, and a REPL may
                # simply exit -- and a kernel Hardy stopped must not be
                # recorded as one that fell over on its own. The state is gone
                # either way; what changes is which cause the record names.
                return incomplete(
                    status="interrupted",
                    stderr=(
                        "the cell was interrupted and the kernel did not survive it, "
                        "so its state is gone"
                    ),
                    kernel_lost=True,
                )
            return incomplete(status="kernel_died", stderr=kernel.stderr_text())
        outcome, consumed = reply
        if self.backend.framing == "sentinel":
            kernel.consume(consumed)
        if signalled:
            outcome = outcome.model_copy(update={"signalled": True})
        if not signalled and outcome.status == "interrupted":
            # The driver says it caught a `KeyboardInterrupt` and Hardy never
            # sent one, so the cell raised it itself -- `raise
            # KeyboardInterrupt` in the source, or a library that uses it to
            # unwind. That is an ordinary failure of the cell, and recording it
            # as a cancellation would put a user action nobody took into the
            # durable log. The parent is the only side that can tell these
            # apart: the driver has no idea where the signal came from.
            outcome = outcome.model_copy(update={"status": "error"})
        elif signalled and outcome.status != "ok":
            # The kernel answered after being asked to stop, so it is alive and
            # its namespace is intact -- but what it answered with is the cell
            # failing, not the cell's result. A sentinel backend has no status
            # of its own (`classify` is Hardy's own inference from an error
            # banner), and the driver's own word for this is already
            # "interrupted"; either way the honest name for a cell the user
            # stopped is that it was stopped. A cell that came back "ok"
            # finished before the signal reached it and keeps its result.
            outcome = outcome.model_copy(update={"status": "interrupted"})
        return outcome

    def interrupt(self) -> bool:
        """Stop the cell in flight without stopping the kernel.

        Callable from any thread. Takes `_signal_lock` but never `_lock` -- the
        latter is held by the thread inside `execute`, which is the thread this
        exists to reach, and waiting for it would deadlock against the very
        cell being stopped.

        Reports whether a cell was actually running to be stopped, so the
        terminal can say what it did rather than claiming to have stopped
        something that was not there. A press that finds nothing is still
        remembered: the cell it was aimed at may be a microsecond from going
        out, and `_send` signals it as soon as it does.

        There is still a window in which the cell finishes just as the signal
        is sent, so the driver survives one arriving with no cell to abandon;
        that is defence in depth, not the design.
        """
        with self._signal_lock:
            self._stop_level = max(self._stop_level, _ASKED)
            if not self._in_flight.is_set():
                return False
            kernel = self._kernel
            if kernel is None:
                return False
            # Set before the signal, not after: the reader must never see a
            # kernel that has already answered the interrupt while the flag
            # that explains the answer is still clear.
            self._interrupted.set()
            return kernel.interrupt()

    def resume(self) -> None:
        """Lift a remembered stop, so the next turn's cells may run.

        The counterpart of `process.resume_children`, called at the same
        moment and for the same reason: a stop that outlived the turn it
        belonged to would interrupt the next turn's first cell on sight.
        """
        with self._signal_lock:
            self._stop_level = 0

    def escalate(self) -> bool:
        """Stop waiting for the interrupt to be answered, and stop the kernel.

        The second Esc. An interrupt is a request, and a cell deep in a library
        that never returns to its interpreter will not hear it; this is the way
        out of waiting for an answer that is not coming. It costs exactly what
        the timeout costs -- the namespace -- which is why it is the second
        press and not the first.

        Like `interrupt`, takes `_signal_lock` but never `_lock`, and refuses
        when nothing is running: killing an idle kernel would leave the session
        holding a dead child it still believed in. The stop is remembered
        either way, so a cell about to go out is stopped rather than the press
        being lost.

        A cell still being *written* counts as running here, though `interrupt`
        refuses it: a kernel that has stopped reading blocks the write with no
        deadline yet running, and killing it is what ends that write. There is
        nothing for the first press to ask of a kernel that is not listening,
        and this is the press that does not ask.
        """
        with self._signal_lock:
            self._stop_level = _INSISTED
            if not (self._in_flight.is_set() or self._sending.is_set()):
                return False
            kernel = self._kernel
            if kernel is None:
                return False
            self._interrupted.set()
        # The reading thread sees both streams end, and -- because the flag is
        # set -- records the cell as interrupted with the kernel lost, which is
        # what happened. It drops the kernel itself when it gets there; nothing
        # here touches `_kernel`, so the two threads cannot race over it.
        #
        # Straight to SIGKILL: this runs on the terminal's event loop, and the
        # graceful teardown's two-second wait would freeze the UI for exactly
        # as long as this press was made to avoid waiting.
        kernel.kill(immediate=True)
        return True

    # -------------------------------------------------------------- budget

    @property
    def remaining_seconds(self) -> float:
        """What is left of `cas_session_seconds`, never negative."""
        return max(0.0, self.limits.cas_session_seconds - self.spent_seconds)

    def charge(self, seconds: float) -> None:
        """Bill CAS wall clock to the session budget.

        Public because the budget covers every kernel a session causes to run,
        not only the cells a caller asked for: the fresh kernel an export
        replays in is the session's own time too, and an export that could
        spend it unbilled would make `cas_session_seconds` describe nothing.

        Both figures move together: the per-process guard and the session's
        durable total. Every charge goes through here so they cannot drift.
        """
        with self._lock:
            self._require_owner()
            seconds = max(0.0, seconds)
            self.spent_seconds += seconds
            self.total_spent_seconds += seconds
            try:
                self._log.mkdir()
                self._log.write_bytes(
                    self._spend_name,
                    (json.dumps(self.total_spent_seconds, allow_nan=False) + "\n").encode("utf-8"),
                )
            except (OSError, LayoutError) as error:
                self._drop_kernel()
                self.state = "poisoned"
                raise CasError(f"the CAS spend could not be written ({self._spend_name}): {error}") from error

    def _cell_seconds(self) -> float:
        """The deadline one round trip may have: the smaller of the two limits.

        A session with one second left must not be able to run a sleeping cell
        for a full `cas_cell_seconds`, or the session limit is not a limit --
        it is only a value consulted before work that ignores it.
        """
        return min(float(self.limits.cas_cell_seconds), self.remaining_seconds)

    # ------------------------------------------------------------- execute

    def _foreign_backend(self) -> str | None:
        """The name on the live segment's records, if it is not this backend's."""
        segment = self.segment
        for record in self._records:
            if record.segment == segment and record.backend and record.backend != self.backend.name:
                return record.backend
        return None

    def _guard(self) -> None:
        self._require_owner()
        if self.state == "poisoned":
            raise CasError(
                "the CAS session is poisoned: its state could not be rebuilt faithfully. "
                "Reset it to start a clean kernel."
            )
        foreign = self._foreign_backend()
        if foreign is not None:
            # Replaying this segment would feed one backend's language to
            # another, and a cell that happens to parse would be worse than one
            # that does not. Refusing leaves a way out: a reset opens a clean
            # segment under the configured backend without deleting anything.
            raise CasError(
                f"this CAS cell log was written by the {foreign} backend, but "
                f"{self.backend.name} is configured. Reset the session to start a "
                "clean segment, or restore the original cas_backend setting."
            )
        if self.spent_seconds >= self.limits.cas_session_seconds:
            raise CasError("CAS session budget exhausted")

    def execute(self, source: str, *, author: str = "model") -> CellRecord:
        with self._lock:
            self._guard()
            if not source.strip():
                raise CasError("an empty cell has nothing to execute")
            if len(source.encode("utf-8")) > 64 * 1024:
                raise CasError("cell source exceeds the 64 KiB limit")

            notes = ""
            # `_kernel is None` covers a session that has never run a cell in
            # this process as well as one whose kernel died -- including the
            # kernel discovery threw away. A merely probed kernel used to leave
            # this false, so a reopened workspace listed its accepted cells and
            # then answered the next one from an empty namespace.
            if self._kernel is None or self.state == "dead":
                # A death and an ordinary reopen both rebuild, and they are not
                # the same news. Reading "kernel restarted" on the first cell of
                # a session nobody had run yet turns opening a saved workspace
                # into an incident report.
                died = self.state == "dead"
                report = self._restore()
                if report.replayed and died:
                    notes = f"[kernel restarted; replayed {report.replayed} cell(s)]"
                elif report.replayed:
                    notes = (
                        f"[saved session reopened; replayed {report.replayed} cell(s) "
                        "to rebuild its state]"
                    )
                if report.unverified:
                    # Named individually only when some cells *were* checked.
                    # On a backend that records no digest at all, every cell is
                    # on the list and printing all of them buries the point.
                    which = (
                        "no cell's"
                        if len(report.unverified) == report.replayed
                        else f"cell(s) {list(report.unverified)}'"
                    )
                    # And which of the two reasons, because they point a reader
                    # at different things. Saying "there is no state digest" of
                    # a cell whose state *was* compared, and whose unread
                    # output tail is the actual gap, sends them to look in the
                    # wrong place.
                    because = _why_unverified(report)
                    notes = (notes + " " if notes else "") + (
                        f"[{which} replay could be fully compared against the "
                        f"record: {because} The state is reconstructed, not "
                        "verified: rerun anything you mean to build on.]"
                    )
                # The rebuild is billed, so it can be what exhausts the budget.
                self._guard()

            started = time.monotonic()
            outcome = self._send(source, self._cell_seconds())
            elapsed = time.monotonic() - started
            self.charge(elapsed)

            status = outcome.status
            truncated = outcome.capture_truncated
            # A sentinel backend has no status of its own: Hardy decides whether
            # the cell failed by looking for an error banner in what it printed.
            # When the capture hit `cas_output_bytes` that decision was made
            # from a prefix, and Singular's `? ` banner or Macaulay2's
            # `stdio:...: error:` can be sitting in the tail that was thrown
            # away. Hardy knows the capture was cut, so it must not then assert
            # success: the cell is recorded and reported in full, and kept out
            # of the accepted set that recovery replays and export publishes,
            # where a wrong "ok" would be repeated as fact forever after.
            #
            # The driver protocol is untouched. There the child reports its own
            # status and clips afterwards, so truncation cannot hide a failure
            # -- what it can still hide is a differing tail, which is export's
            # problem and is answered there with an `unverified` verdict.
            unverifiable = (
                truncated and status == "ok" and self.backend.framing == "sentinel"
            )
            # A cell Hardy signalled that answered `ok` anyway. It really did
            # finish, so it keeps that status -- but it finished under a signal,
            # and a cell (or a library beneath it) that catches one can return
            # normally from a path it would not otherwise have taken. A replay
            # without the signal would then not reproduce it, which is the one
            # thing an accepted cell has to be able to do.
            perturbed = outcome.signalled and status == "ok"
            if perturbed:
                notes = (notes + " " if notes else "") + (
                    "[this cell was interrupted but reported success anyway, so it "
                    "ran under a signal it may have caught. It is recorded, and its "
                    "output is what it produced, but it was not accepted into the "
                    "state replay and export rebuild from: without the interrupt it "
                    "may not take the same path. Rerun it if you mean to build on it.]"
                )
            if unverifiable:
                notes = (notes + " " if notes else "") + (
                    "[output exceeded cas_output_bytes, so this cell was classified "
                    "from a prefix and an error banner could be in the discarded "
                    "tail. It ran, and its output is recorded, but it was not "
                    "accepted into the state replay and export rebuild from. It did "
                    "change the live namespace, and that change is now outside the "
                    "accepted set: any later cell that depends on it will diverge on "
                    "export and after a kernel restart. Rerun it printing less, or "
                    "raise cas_output_bytes and rerun it, before building on it.]"
                )
            if outcome.kernel_lost:
                notes = (notes + " " if notes else "") + (
                    "[the kernel did not answer the interrupt, so it was stopped. "
                    "Every value in the session is gone; the next cell rebuilds "
                    "from the accepted ones.]"
                )
            if status in {"timeout", "kernel_died"} or outcome.kernel_lost:
                self._drop_kernel()
            record = CellRecord(
                seq=len(self._records),
                segment=self.segment,
                author=author,  # type: ignore[arg-type]
                source=source,
                status=status,  # type: ignore[arg-type]
                accepted=status == "ok" and not unverifiable and not perturbed,
                kernel_lost=outcome.kernel_lost or status in {"timeout", "kernel_died"},
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                capture_mode=outcome.capture_mode,
                value_repr=outcome.value_repr,
                duration_ms=round(elapsed * 1_000),
                spent_ms=round(self.total_spent_seconds * 1_000),
                capture_truncated=truncated,
                backend=self.backend.name,
                backend_version=self.version or "",
                state_digest=outcome.state_digest,
                restart_note=notes,
            )
            return self._append(record)

    def _drop_kernel(self) -> None:
        if self._kernel is not None:
            self._kernel.kill()
        self._kernel = None
        self.state = "dead"

    def _discard_kernel(self) -> None:
        """Close a kernel that nothing died in, leaving the session cold."""
        if self._kernel is not None:
            self._kernel.kill()
        self._kernel = None
        if self.state != "poisoned":
            self.state = "cold"

    def _restore(self) -> RebuildReport:
        """Rebuild live state after a death, and verify what was rebuilt."""
        # Unaccepted live cells can mutate the namespace before returning. Replaying
        # only accepted cells cannot recover those effects, even when later
        # accepted cells produce the same output. Require an explicit reset.
        unreplayed = tuple(
            record.seq
            for record in self.cells()
            if not record.accepted and record.kernel_lost is not True
            and record.status not in {"timeout", "kernel_died"}
        )
        if unreplayed:
            self._drop_kernel()
            self.state = "poisoned"
            raise CasError(
                "CAS state did not reproduce: unaccepted cell(s) "
                f"{list(unreplayed)} may have changed the live state. "
                "Reset the session to start clean."
            )
        # The start is the session's time too. Unbilled, a recovery cost one
        # kernel start the budget never saw, and `cas_session_seconds` bounded
        # total wall clock plus a start per death rather than total wall clock.
        started = time.monotonic()
        self._start()
        self.charge(time.monotonic() - started)
        pending = self.accepted()
        if not pending:
            return RebuildReport(unreplayed=unreplayed)
        diverged: list[int] = []
        # A replay that overran the cap compared on a prefix just as a
        # truncated record does, and the truncation can be on either side: an
        # exact original against a replay that printed more still matches on
        # what was retained, with the rest never looked at.
        clipped: list[int] = []
        unrecorded: set[int] = set()
        unfingerprinted: set[int] = set()
        for record in pending:
            # A rebuild is the session's own time. Left unbilled, a session
            # holding expensive accepted cells could time out and replay many
            # minutes of work over and over while the budget never moved.
            if self.remaining_seconds <= 0:
                self._drop_kernel()
                self.state = "poisoned"
                raise CasError(
                    "could not rebuild CAS state: the session budget ran out during "
                    "replay. Reset the session to start clean."
                )
            started = time.monotonic()
            outcome = self._send(record.source, self._cell_seconds())
            self.charge(time.monotonic() - started)
            # A replay Hardy signalled establishes nothing about the log, and
            # an `ok` is the dangerous case rather than the safe one: a cell
            # that caught the signal can skip a mutation and still print what
            # it printed before, so `reproduces` would pass over a namespace
            # that differs -- and every later cell would be built on it.
            #
            # Left retryable rather than poisoned. Poisoning says the accepted
            # cells no longer describe a state that can be rebuilt, and nothing
            # here has shown that: the user simply stopped the rebuild. The
            # kernel is dropped, so the next cell tries again.
            if outcome.signalled or outcome.status == "interrupted":
                self._drop_kernel()
                raise CasError(
                    "the CAS rebuild was interrupted before it finished. Nothing is "
                    "lost -- the saved cells are intact, and the next cell rebuilds "
                    "from them again."
                )
            if outcome.status != "ok":
                self._drop_kernel()
                self.state = "poisoned"
                raise CasError(
                    f"could not rebuild CAS state: cell {record.seq} failed on replay. "
                    "Reset the session to start clean."
                )
            # Running clean is not the same as recovering. A cell that depends
            # on randomness, time, or the filesystem can succeed here and
            # reconstruct a different value, and every later cell would be
            # built on it.
            if outcome.capture_truncated:
                clipped.append(record.seq)
            # Recorded per cell as the replay goes, because the report below
            # needs the replay's half of it and the loop is where the outcome
            # exists. A cell whose replay could not be fingerprinted is
            # unchecked, not divergent.
            if unobservable(record):
                unrecorded.add(record.seq)
            elif not outcome.state_digest:
                unfingerprinted.add(record.seq)
            if not reproduces(record, outcome):
                diverged.append(record.seq)
        if diverged:
            self.state = "poisoned"
            raise CasError(
                "could not rebuild CAS state faithfully: cell(s) "
                f"{diverged} did not reproduce on replay. "
                "Reset the session to start clean."
            )
        # A clean replay is not the same as a checked one. On a backend that
        # carries no state digest, a cell that printed nothing agrees with its
        # record whatever it rebuilt, so the rebuild is reported with those
        # cells named rather than as a rebuild that was verified.
        return RebuildReport(
            replayed=len(pending),
            # A truncated capture belongs here for the same reason a missing
            # digest does: the retained prefixes matched and the discarded
            # tails were never compared, so a cell printing a deterministic
            # prefix over a random tail replays "cleanly" without anything
            # having checked the part that differs. Export already refuses to
            # call such a cell verified; a rebuild says the same now.
            unverified=tuple(
                record.seq
                for record in pending
                if record.seq in unrecorded
                or record.seq in unfingerprinted
                or record.capture_truncated
                or record.seq in set(clipped)
            ),
            digestless=tuple(record.seq for record in pending if record.seq in unrecorded),
            unfingerprintable=tuple(
                record.seq for record in pending if record.seq in unfingerprinted
            ),
            clipped=tuple(
                record.seq
                for record in pending
                if record.capture_truncated or record.seq in set(clipped)
            ),
            unreplayed=unreplayed,
        )

    def reset(self, *, author: str = "model") -> None:
        """Close the current segment. Nothing is deleted.

        The boundary is itself a `CellRecord` carrying the new segment, so one
        schema describes the whole log and the reset is durable the moment it
        happens rather than when the next cell is written.

        A reset clears state, not the bill. `cas_reset` is a tool the model can
        call, so refunding `spent_seconds` here would make the session budget
        advisory: a model near the limit could buy the whole allowance again,
        as often as it liked, by discarding a namespace it no longer needed.

        `author` is carried through for the same reason `execute` carries it.
        A state-destroying action recorded as the human's when a model asked
        for it makes the timeline lie about why earlier definitions vanished.
        """
        with self._lock:
            self._require_owner()
            self._drop_kernel()
            self.state = "cold"
            self._append(
                CellRecord(
                    seq=len(self._records),
                    segment=self.segment + 1,
                    author=author,  # type: ignore[arg-type]
                    source="",
                    status="ok",
                    accepted=False,
                    backend=self.backend.name,
                    backend_version=self.version or "",
                    spent_ms=round(self.total_spent_seconds * 1_000),
                )
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._kernel is not None:
                    self._kernel.kill()
            finally:
                self._kernel = None
                if self._lease is not None:
                    self._lease.__exit__(None, None, None)
                    self._lease = None


