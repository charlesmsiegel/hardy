"""Serialized CAS cells, durable replay state, and session budget ownership.

A session is the policy over two owners beside it: `journal.CellJournal`, the
durable log, and `exchange.CellExchange`, the kernel and the stops aimed at it.
What is decided here is what a cell means -- whether it may be built on, how a
dead kernel is rebuilt and whether the rebuild can be trusted, and what the
session has spent.
"""
from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from hardy.algebra.backends import CasBackend
from hardy.algebra.contracts import (
    SOURCE_LIMIT_BYTES,
    Author,
    CasError,
    CellOutcome,
    CellRecord,
    RebuildReport,
    ReplayCheck,
    _why_unverified,
    check_replay,
)
from hardy.algebra.exchange import CellExchange
from hardy.algebra.journal import CellJournal
from hardy.algebra.kernel import Kernel
from hardy.foundation.files import LayoutError
from hardy.workflows.contracts import RunLimits


class KernelState(StrEnum):
    """Where the session's kernel stands, as `cas_state` reports it."""

    # No kernel yet, or one closed with nothing lost in it. The next cell
    # rebuilds from the accepted log without calling that a restart.
    COLD = "cold"
    LIVE = "live"
    # The kernel was lost. The next cell rebuilds and says the kernel restarted.
    DEAD = "dead"
    # The log no longer describes a state that can be rebuilt faithfully, or
    # cannot be written. Nothing runs until a reset.
    POISONED = "poisoned"


def _lost_effects(cells: tuple[CellRecord, ...]) -> list[int]:
    """The cells whose effects a rebuild from the accepted ones would leave out.

    Unaccepted live cells can mutate the namespace before returning.
    Replaying only accepted cells cannot recover those effects, even when
    later accepted cells produce the same output, so they require an explicit
    reset. A cell whose kernel was lost took its effects with it; a record
    from before `kernel_lost` existed counts only for a status that proves it.
    """
    return [
        record.seq
        for record in cells
        if not record.accepted and record.kernel_lost is not True
        and record.status not in {"timeout", "kernel_died"}
    ]


def _check_source(source: str) -> None:
    if not source.strip():
        raise CasError("an empty cell has nothing to execute")
    if len(source.encode("utf-8")) > SOURCE_LIMIT_BYTES:
        raise CasError("cell source exceeds the 64 KiB limit")


def _rebuild_note(report: RebuildReport, *, died: bool) -> str:
    """What the first cell after a rebuild says about it, or "" if nothing ran."""
    notes = []
    if report.replayed and died:
        notes.append(f"[kernel restarted; replayed {report.replayed} cell(s)]")
    elif report.replayed:
        notes.append(
            f"[saved session reopened; replayed {report.replayed} cell(s) "
            "to rebuild its state]"
        )
    if report.unverified:
        # Named individually only when some cells *were* checked. On a backend
        # that records no digest at all, every cell is on the list and
        # printing all of them buries the point.
        which = (
            "no cell's"
            if len(report.unverified) == report.replayed
            else f"cell(s) {list(report.unverified)}'"
        )
        # And which of the reasons, because they point a reader at different
        # things. Saying "there is no state digest" of a cell whose state
        # *was* compared, and whose unread output tail is the actual gap,
        # sends them to look in the wrong place.
        notes.append(
            f"[{which} replay could be fully compared against the "
            f"record: {_why_unverified(report)} The state is reconstructed, not "
            "verified: rerun anything you mean to build on.]"
        )
    return " ".join(notes)


@dataclass(frozen=True)
class _Judgement:
    """Whether a finished cell joins the accepted state, and the notes saying why not."""

    accepted: bool
    notes: tuple[str, ...]


_PERTURBED_NOTE = (
    "[this cell was interrupted but reported success anyway, so it "
    "ran under a signal it may have caught. It is recorded, and its "
    "output is what it produced, but it was not accepted into the "
    "state replay and export rebuild from: without the interrupt it "
    "may not take the same path. Rerun it if you mean to build on it.]"
)
_CLIPPED_NOTE = (
    "[output exceeded cas_output_bytes, so this cell was classified "
    "from a prefix and an error banner could be in the discarded "
    "tail. It ran, and its output is recorded, but it was not "
    "accepted into the state replay and export rebuild from. It did "
    "change the live namespace, and that change is now outside the "
    "accepted set: any later cell that depends on it will diverge on "
    "export and after a kernel restart. Rerun it printing less, or "
    "raise cas_output_bytes and rerun it, before building on it.]"
)
# A second press does not wait to see whether the kernel answers -- and it may
# have: unrun, or even `ok` if the press raced a cell that had just finished.
# Saying it "did not answer" would contradict the record it sits in.
_KILLED_NOTE = (
    "[a second press stopped the kernel, so every value in the "
    "session is gone, whatever this cell answered; the next cell "
    "rebuilds from the accepted ones.]"
)
_UNANSWERED_NOTE = (
    "[the kernel did not answer the interrupt, so it was stopped. "
    "Every value in the session is gone; the next cell rebuilds "
    "from the accepted ones.]"
)


def _judge(outcome: CellOutcome, backend: CasBackend, *, killed: bool) -> _Judgement:
    """Decide whether a finished cell may be built on. `killed` is whether
    Hardy killed the kernel while it was out."""
    status = outcome.status
    # A sentinel backend has no status of its own: Hardy decides whether the
    # cell failed by looking for an error banner in what it printed. When the
    # capture hit `cas_output_bytes` that decision was made from a prefix, and
    # Singular's `? ` banner or Macaulay2's `stdio:...: error:` can be sitting
    # in the tail that was thrown away. Hardy knows the capture was cut, so it
    # must not then assert success: the cell is recorded and reported in
    # full, and kept out of the accepted set that recovery replays and export
    # publishes, where a wrong "ok" would be repeated as fact forever after.
    #
    # The driver protocol is untouched. There the child reports its own
    # status and clips afterwards, so truncation cannot hide a failure -- what
    # it can still hide is a differing tail, which is export's problem and is
    # answered there with an `unverified` verdict.
    unverifiable = outcome.capture_truncated and status == "ok" and not backend.reports_status
    # A cell Hardy signalled that answered `ok` anyway. It really did finish,
    # so it keeps that status -- but it finished under a signal, and a cell
    # (or a library beneath it) that catches one can return normally from a
    # path it would not otherwise have taken. A replay without the signal
    # would then not reproduce it, which is the one thing an accepted cell has
    # to be able to do.
    perturbed = outcome.signalled and status == "ok"
    notes = []
    if perturbed:
        notes.append(_PERTURBED_NOTE)
    if unverifiable:
        notes.append(_CLIPPED_NOTE)
    if outcome.kernel_lost:
        notes.append(_KILLED_NOTE if killed else _UNANSWERED_NOTE)
    return _Judgement(
        accepted=status == "ok" and not unverifiable and not perturbed, notes=tuple(notes)
    )


class CasSession:
    """A durable cell log and the kernel that its accepted cells describe."""

    def __init__(
        self,
        *,
        backend: CasBackend,
        command: Path | None,
        log_path: Path,
        limits: RunLimits,
        cwd: Path | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.backend = backend
        self.command = command
        self.limits = limits
        self.cwd = cwd or log_path.parent
        self.observe = observe
        self.state: KernelState = KernelState.COLD
        self.version: str | None = None
        # This process's spend, and what `cas_session_seconds` bounds: a guard
        # against a runaway computation, not the session's figure. That is
        # `total_spent_seconds`, below.
        self.spent_seconds = 0.0
        # One stateful process behind one stdin stream, reachable from chat,
        # staged runs, and MCP. The lock belongs to the resource, not a caller.
        self._lock = threading.RLock()
        self._exchange = CellExchange(backend, limits.cas_cell_seconds)
        # An unusable absent log remains constructible, but no kernel may run
        # before its directory can be created and its writer lease acquired.
        self._journal = CellJournal(log_path)
        # The session's own figure: every second of CAS wall clock billed to
        # this log, across every process that has opened it. Read back from the
        # last record, which carries the running total as of its append, and
        # from the spend sidecar, which also includes charges made after it;
        # moved with `spent_seconds` from then on. Reported, never enforced: a
        # research workspace may legitimately be open for months, and a
        # lifetime cap attached to it would eventually refuse work for reasons
        # that have nothing to do with the work.
        self.total_spent_seconds = 0.0
        try:
            self.total_spent_seconds = self._journal.own()
        except OSError:
            # Preserve lazy construction when the absent log's parent cannot
            # yet be created. The first operation retries before doing work.
            if self.log_path.exists():
                raise

    @property
    def log_path(self) -> Path:
        """The cell log this session owns; its siblings sit beside it."""
        return self._journal.path

    @property
    def _kernel(self) -> Kernel | None:
        return self._exchange.kernel

    def _require_owner(self) -> None:
        if self._exchange.shutting_down:
            raise CasError("the CAS session is closing")
        if self._journal.closed:
            raise CasError("the CAS session is closed; open a new session before running or writing")
        try:
            if not self._journal.owned:
                self.total_spent_seconds = self._journal.own()
            self._journal.check_lease()
        except (OSError, CasError) as error:
            raise self._poisoned(
                f"the CAS cell log could not be written ({self.log_path}): {error}"
            ) from error

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
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

    def _append(self, record: CellRecord) -> CellRecord:
        """Publish a record, durably first and only then in memory.

        If the log cannot be written the cell has already run, so what the
        kernel holds is no longer described by anything durable: the session
        is poisoned rather than allowed to continue on state it can never
        explain.
        """
        self._require_owner()
        try:
            self._journal.append(record)
        except OSError as error:
            raise self._poisoned(
                f"the CAS cell log could not be written ({self.log_path}): {error}. "
                "The session is poisoned because its live state is no longer recorded. "
                "Reset it to start clean."
            ) from None
        if self.observe is not None:
            self.observe({"type": "cas", "record": record.model_dump(mode="json")})
        return record

    def records(self) -> tuple[CellRecord, ...]:
        """Every durable record, boundaries included."""
        return tuple(self._journal.records)

    @property
    def segment(self) -> int:
        return self._journal.segment

    def accepted(self) -> tuple[CellRecord, ...]:
        return self._journal.accepted()

    def cells(self) -> tuple[CellRecord, ...]:
        return self._journal.cells()

    # -------------------------------------------------------------- kernel

    def _start(self) -> None:
        self._require_owner()
        cap = self.limits.cas_output_bytes
        argv = self.backend.argv(self.command, cap)
        try:
            kernel = Kernel(
                argv, self.cwd, self.backend.retention(cap), self.backend.environment,
                merge_stderr=self.backend.capture_mode == "merged",
            )
        except (OSError, ValueError) as error:
            self.state = KernelState.DEAD
            raise CasError(
                f"could not start the {self.backend.name} kernel "
                f"({' '.join(argv)}): {error}"
            ) from None
        if not self._exchange.adopt(kernel):
            raise CasError("the CAS session is closing")
        self.state = KernelState.LIVE

    def start_kernel(self) -> None:
        """Bring up a kernel with nothing replayed into it.

        For a fresh replay, which feeds cells in itself and judges them against
        some other session's record. This session's own next cell rebuilds
        from its accepted log instead, through `execute`.
        """
        with self._lock:
            self._start()

    def run_unrecorded(self, source: str, seconds: float) -> CellOutcome:
        """One round trip in the running kernel, with nothing appended to the log.

        `seconds` is the deadline applied. A replay compares what comes back
        against a record it already has, so it must not write one of its own.
        """
        with self._lock:
            return self._send(source, seconds)

    def remove_log(self) -> None:
        """Delete this session's cell log, through the guard that wrote it.

        Only for a scratch session: a log anyone will reopen is the record and
        is never deleted. Removed by the same proven directory it was written
        through, not by a path rebuilt somewhere that nothing re-checks.
        """
        self._journal.remove()

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
            started = time.monotonic()
            try:
                if self._kernel is None:
                    self._start()
                outcome = self._send(self.backend.version_source)
            finally:
                self._discard_kernel()
                self.charge(time.monotonic() - started)
            if outcome.status != "ok":
                raise CasError(
                    f"{self.backend.name} kernel did not answer a version query: "
                    f"{(outcome.stderr or outcome.stdout).strip()[:200]}"
                )
            raw = self.backend.parse_version(outcome.value_repr or outcome.stdout)
            self.version = raw.strip().strip("'\"") or "unknown"
            return self.version

    def _send(self, source: str, seconds: float | None = None) -> CellOutcome:
        """One round trip. Assumes the lock and a started kernel.

        `seconds` is the deadline actually applied, which is not always
        `cas_cell_seconds`: a caller with less session budget left than that
        must not be able to buy a full cell's worth of wall clock with it.
        """
        limit = self.limits.cas_cell_seconds if seconds is None else seconds
        return self._exchange.send(source, limit)

    def interrupt(self) -> bool:
        """Stop the cell in flight without stopping the kernel; from any thread.

        Reports whether a cell was actually running to be stopped. See
        `CellExchange.interrupt`.
        """
        return self._exchange.interrupt()

    def resume(self) -> None:
        """Lift a remembered stop, so the next turn's cells may run."""
        self._exchange.resume()

    def escalate(self) -> bool:
        """Stop waiting for the interrupt to be answered, and stop the kernel.

        The second Esc, from any thread; it costs the namespace. See
        `CellExchange.escalate`.
        """
        return self._exchange.escalate()

    def terminate_on_shutdown(self) -> None:
        """Kill even an idle kernel without waiting for the cell execution lock.

        Console closure is process shutdown, rather than an interrupt that
        preserves the namespace. The normal close still releases the lease.
        """
        self._exchange.shut_down()

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
                self._journal.write_spend(self.total_spent_seconds)
            except (OSError, LayoutError) as error:
                raise self._poisoned(
                    f"the CAS spend could not be written ({self._journal.spend_name}): {error}"
                ) from error

    def _cell_seconds(self) -> float:
        """The deadline one round trip may have: the smaller of the two limits.

        A session with one second left must not be able to run a sleeping cell
        for a full `cas_cell_seconds`, or the session limit is not a limit --
        it is only a value consulted before work that ignores it.
        """
        return min(float(self.limits.cas_cell_seconds), self.remaining_seconds)

    # ------------------------------------------------------------- execute

    def _guard(self) -> None:
        self._require_owner()
        if self.state == KernelState.POISONED:
            raise CasError(
                "the CAS session is poisoned: its state could not be rebuilt faithfully. "
                "Reset it to start a clean kernel."
            )
        foreign = self._journal.foreign_backend(self.backend.name)
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
        if (
            self.remaining_seconds <= 0
            or self.remaining_seconds < min(0.1, self.limits.cas_cell_seconds / 10)
        ):
            raise CasError("CAS session budget exhausted")

    def execute(self, source: str, *, author: Author = "model", path: str = "") -> CellRecord:
        """Run one cell, rebuilding the kernel first if there is none, and record it.

        Every cell is recorded, whatever became of it. Only an `ok` Hardy can
        stand behind is accepted into the state recovery and export rebuild
        from; the record's `restart_note` says why when one is not.
        """
        with self._lock:
            self._guard()
            _check_source(source)
            rebuilt = self._rebuild_if_needed()
            started = time.monotonic()
            outcome = self._send(source, self._cell_seconds())
            elapsed = time.monotonic() - started
            self.charge(elapsed)
            # This cell's, not a later one's: `_lock` keeps any other `_send`
            # from clearing it, and `escalate` can no longer set it once
            # `_send` has cleared `_in_flight`.
            judgement = _judge(outcome, self.backend, killed=self._exchange.last_cell_killed())
            kernel_lost = outcome.kernel_lost or outcome.status in {"timeout", "kernel_died"}
            if kernel_lost:
                self._drop_kernel()
            record = CellRecord(
                seq=len(self._journal.records),
                segment=self.segment,
                author=author,
                source=source,
                path=path,
                status=outcome.status,
                accepted=judgement.accepted,
                kernel_lost=kernel_lost,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                capture_mode=outcome.capture_mode,
                value_repr=outcome.value_repr,
                duration_ms=round(elapsed * 1_000),
                spent_ms=round(self.total_spent_seconds * 1_000),
                capture_truncated=outcome.capture_truncated,
                backend=self.backend.name,
                backend_version=self.version or "",
                state_digest=outcome.state_digest,
                restart_note=" ".join(note for note in (rebuilt, *judgement.notes) if note),
            )
            return self._append(record)

    def _rebuild_if_needed(self) -> str:
        """Rebuild the kernel from the accepted cells if there is none; say so.

        `_kernel is None` covers a session that has never run a cell in this
        process as well as one whose kernel died -- including the kernel
        discovery threw away. A merely probed kernel used to leave this false,
        so a reopened workspace listed its accepted cells and then answered
        the next one from an empty namespace.
        """
        if self._kernel is not None and self.state != KernelState.DEAD:
            return ""
        # A death and an ordinary reopen both rebuild, and they are not the
        # same news. Reading "kernel restarted" on the first cell of a session
        # nobody had run yet turns opening a saved workspace into an incident
        # report.
        died = self.state == KernelState.DEAD
        note = _rebuild_note(self._restore(), died=died)
        # The rebuild is billed, so it can be what exhausts the budget.
        self._guard()
        return note

    def _drop_kernel(self) -> None:
        self._exchange.drop()
        self.state = KernelState.DEAD

    def _poisoned(self, message: str) -> CasError:
        """Drop the kernel and mark the session poisoned; the error says why.

        Poisoned means the live state is no longer described by anything a
        rebuild can trust, so nothing runs again until a reset.
        """
        self._drop_kernel()
        self.state = KernelState.POISONED
        return CasError(message)

    def _discard_kernel(self) -> None:
        """Close a kernel that nothing died in, leaving the session cold."""
        self._exchange.drop()
        if self.state != KernelState.POISONED:
            self.state = KernelState.COLD

    def _restore(self) -> RebuildReport:
        """Rebuild live state after a death, and verify what was rebuilt."""
        unreplayable = _lost_effects(self.cells())
        if unreplayable:
            raise self._poisoned(
                "CAS state did not reproduce: unaccepted cell(s) "
                f"{unreplayable} may have changed the live state. "
                "Reset the session to start clean."
            )
        # The start is the session's time too. Unbilled, a recovery cost one
        # kernel start the budget never saw, and `cas_session_seconds` bounded
        # total wall clock plus a start per death rather than total wall clock.
        started = time.monotonic()
        self._start()
        self.charge(time.monotonic() - started)
        checks = {record.seq: self._replay_for_rebuild(record) for record in self.accepted()}
        # Running clean is not the same as recovering. A cell that depends on
        # randomness, time, or the filesystem can succeed here and reconstruct
        # a different value, and every later cell would be built on it.
        diverged = [seq for seq, check in checks.items() if check.verdict == "diverged"]
        if diverged:
            raise self._poisoned(
                "could not rebuild CAS state faithfully: cell(s) "
                f"{diverged} did not reproduce on replay. "
                "Reset the session to start clean."
            )
        return RebuildReport.from_checks(checks)

    def _replay_for_rebuild(self, record: CellRecord) -> ReplayCheck:
        """Replay one accepted cell into the rebuilding kernel, and judge it."""
        # A rebuild is the session's own time. Left unbilled, a session
        # holding expensive accepted cells could time out and replay many
        # minutes of work over and over while the budget never moved.
        if self.remaining_seconds <= 0:
            raise self._poisoned(
                "could not rebuild CAS state: the session budget ran out during "
                "replay. Reset the session to start clean."
            )
        started = time.monotonic()
        outcome = self._send(record.source, self._cell_seconds())
        self.charge(time.monotonic() - started)
        # A replay Hardy signalled establishes nothing about the log, and an
        # `ok` is the dangerous case rather than the safe one: a cell that
        # caught the signal can skip a mutation and still print what it
        # printed before, so `reproduces` would pass over a namespace that
        # differs -- and every later cell would be built on it.
        #
        # Left retryable rather than poisoned. Poisoning says the accepted
        # cells no longer describe a state that can be rebuilt, and nothing
        # here has shown that: the user simply stopped the rebuild. The kernel
        # is dropped, so the next cell tries again.
        if outcome.signalled or outcome.status == "interrupted":
            self._drop_kernel()
            raise CasError(
                "the CAS rebuild was interrupted before it finished. Nothing is "
                "lost -- the saved cells are intact, and the next cell rebuilds "
                "from them again."
            )
        check = check_replay(record, outcome)
        if check.verdict == "failed":
            raise self._poisoned(
                f"could not rebuild CAS state: cell {record.seq} failed on replay. "
                "Reset the session to start clean."
            )
        return check

    def reset(self, *, author: Author = "model") -> None:
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
            self.state = KernelState.COLD
            self._append(
                CellRecord(
                    seq=len(self._journal.records),
                    segment=self.segment + 1,
                    author=author,
                    source="",
                    status="ok",
                    accepted=False,
                    backend=self.backend.name,
                    backend_version=self.version or "",
                    spent_ms=round(self.total_spent_seconds * 1_000),
                )
            )

    def close(self) -> None:
        """Stop the kernel and give up the log; a closed session never reopens."""
        with self._lock:
            if self._journal.closed:
                return
            try:
                self._exchange.drop()
            finally:
                self._journal.close()
