"""One cell at a time to the live kernel and back, and the stops aimed at it.

Everything here is about a cell in flight and the other threads that may want
it stopped: the chat's Esc, a staged run's cancellation, console shutdown.
Those arrive on threads of their own while the thread running the cell waits
on the kernel, so the state they share is guarded by a lock of its own and
never by the session's.
"""
from __future__ import annotations

import threading
import time

from hardy.algebra.backends import CasBackend
from hardy.algebra.contracts import (
    _ASKED,
    _INSISTED,
    INTERRUPTED,
    TIMED_OUT,
    CasError,
    CellOutcome,
    ReadFailure,
)
from hardy.algebra.kernel import Kernel
from hardy.foundation.process import INTERRUPT_GRACE_SECONDS


def _as_stopped(outcome: CellOutcome, *, signalled: bool, killed: bool) -> CellOutcome:
    """A kernel's reply, corrected by what Hardy knows about stopping it.

    `signalled` is whether Hardy signalled this cell, and `killed` whether it
    killed the kernel while the cell was out. The stream alone can say neither.
    """
    if signalled:
        outcome = outcome.model_copy(update={"signalled": True})
    if killed:
        # A reply read, from a kernel Hardy then killed: the driver answered
        # -- most often the cell unrun, on the frame's stop -- before the kill
        # landed. What it said still stands as the cell's result, but the
        # kernel that said it is gone, and so is its namespace. Left `live`,
        # the session would send the next cell to a dead process and record
        # the toolchain as having fallen over.
        outcome = outcome.model_copy(update={"kernel_lost": True})
    if not signalled and outcome.status == "interrupted":
        # The driver says it caught a `KeyboardInterrupt` and Hardy never sent
        # one, so the cell raised it itself -- `raise KeyboardInterrupt` in the
        # source, or a library that uses it to unwind. That is an ordinary
        # failure of the cell, and recording it as a cancellation would put a
        # user action nobody took into the durable log. The parent is the only
        # side that can tell these apart: the driver has no idea where the
        # signal came from.
        return outcome.model_copy(update={"status": "error"})
    if signalled and outcome.status != "ok":
        # The kernel answered after being asked to stop, so it is alive and
        # its namespace is intact -- but what it answered with is the cell
        # failing, not the cell's result. A sentinel backend has no status of
        # its own (`classify` is Hardy's own inference from an error banner),
        # and the driver's own word for this is already "interrupted"; either
        # way the honest name for a cell the user stopped is that it was
        # stopped. A cell that came back "ok" finished before the signal
        # reached it and keeps its result.
        return outcome.model_copy(update={"status": "interrupted"})
    return outcome



class CellExchange:
    """The current kernel, the cell out to it, and the stop register.

    `cell_seconds` is the configured per-cell limit, so a deadline cut short
    by the session budget can say so.
    """

    def __init__(self, backend: CasBackend, cell_seconds: float) -> None:
        self.backend = backend
        self.cell_seconds = cell_seconds
        self.kernel: Kernel | None = None
        # Both read and written without the session's lock, on purpose: the
        # thread that holds it is the one inside `execute`, waiting on the
        # kernel, and that is precisely the thread an interrupt has to reach.
        # An `Event` rather than a bool because these cross threads with no
        # lock between them.
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
        self._shutting_down = False
        # Whether Hardy itself killed the kernel during the cell in flight --
        # the second press, in `escalate` or applied as the cell goes out.
        # Hardy knows this; the stream cannot say it. A driver told by the
        # frame to stop answers the cell unrun at once, and it can write that
        # answer before the kill is delivered (Windows CI saw exactly this).
        # The kill then waits for the process to exit, but the answer is
        # already in the pipe and stays readable after the process is gone,
        # and `read_reply` returns a complete reply before it looks at the
        # stream's end. Read from the stream alone, that cell looks answered by
        # a live kernel. The same holds for an `escalate` that lands just after
        # a cell has answered. Cleared only as the next frame is built, so it
        # still describes the last cell when `execute` words its record.
        self._killed = False

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    def adopt(self, kernel: Kernel) -> bool:
        """Make `kernel` the one cells go to, unless shutdown has begun.

        A kernel refused here is killed at once: shutdown has already swept,
        and nothing else would ever stop it.
        """
        with self._signal_lock:
            closing = self._shutting_down
            if not closing:
                self.kernel = kernel
        if closing:
            kernel.kill(immediate=True)
        return not closing

    def drop(self) -> None:
        """Kill the kernel, if there is one, and forget it."""
        try:
            if self.kernel is not None:
                self.kernel.kill()
        finally:
            self.kernel = None

    def last_cell_killed(self) -> bool:
        """Whether Hardy killed the kernel while the last cell was out."""
        with self._signal_lock:
            return self._killed

    def send(self, source: str, limit: float) -> CellOutcome:
        """One round trip to the current kernel, never longer than `limit`.

        The caller serialises cells; this is safe beside `interrupt`,
        `escalate` and `shut_down` from other threads.
        """
        kernel = self.kernel
        if kernel is None:
            raise CasError("no CAS kernel is running")
        nonce = f"{time.monotonic_ns():x}"
        self.backend.arm(kernel, nonce)
        frame, sent = self._deliver(kernel, source, nonce)
        try:
            if not sent:
                return self._undelivered(kernel)
            deadline = time.monotonic() + limit
            # The frame, not the source: an echoing interpreter echoes the
            # sentinel statements bracketing the cell as readily as the cell
            # itself, and a multi-line cell's second line onward comes back
            # under a continuation indent rather than a prompt of its own.
            fed = frame.decode("utf-8", errors="replace")
            reply = kernel.read_reply(
                self.backend.extractor(kernel, nonce, fed), deadline, self._interrupted
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
                killed = self._killed
                self._in_flight.clear()
        if not isinstance(reply, tuple):
            return self._unanswered(kernel, reply, limit)
        outcome, consumed = reply
        self.backend.release(kernel, consumed)
        return _as_stopped(outcome, signalled=signalled, killed=killed)

    def _deliver(self, kernel: Kernel, source: str, nonce: str) -> tuple[bytes, bool]:
        """Write the cell's frame, and spend any stop asked for before it went out.

        Returns the frame and whether the kernel took all of it.
        """
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
            self._killed = False
            stopping = self._stop_level > 0
            frame = self.backend.frame(source, nonce, stopping=stopping)
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
            #
            # For the driver protocol the frame itself carried the stop
            # (`stopping`), and the driver answers the cell unrun on that word
            # alone, so no signal follows it: one sent as well would be the
            # race this exists to close -- a signal that is late or lost lets
            # the cell run to its deadline, and one that lands after the
            # driver has already answered arrives with no cell to abandon.
            # The signal still goes out when the press landed while the frame
            # was being written (the frame then says no stop was in force,
            # and the signal is the only carrier left), and for a sentinel
            # backend, whose frame carries nothing.
            if self._stop_level and sent:
                self._interrupted.set()
                if self._stop_level >= _INSISTED:
                    self._killed = True
                    kernel.kill(immediate=True)
                elif not (stopping and self.backend.frame_carries_stop):
                    kernel.interrupt()
        return frame, sent

    def _undelivered(self, kernel: Kernel) -> CellOutcome:
        """The outcome of a frame the kernel did not take all of."""
        if self._interrupted.is_set():
            # The write failed because the second press killed the kernel out
            # from under it -- which is what ends a write to a kernel that has
            # stopped reading. Hardy stopped this, so the record says so;
            # `kernel_died` would blame the toolchain for what Esc did.
            return self.backend.partial(kernel, CellOutcome(
                status="interrupted",
                stderr="the frame was not completely delivered; it was stopped and its state is gone",
                kernel_lost=True,
                signalled=True,
            ))
        return self.backend.partial(
            kernel, CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
        )

    def _unanswered(self, kernel: Kernel, failure: ReadFailure | None, limit: float) -> CellOutcome:
        """The outcome of a cell whose reply never arrived, by why it did not."""
        if failure is INTERRUPTED:
            # Asked to stop and did not answer within the grace. Whatever it is
            # doing, it is not talking, and a kernel that cannot be spoken to
            # cannot be replayed from or built on -- so it goes, exactly as a
            # timed-out one does. This is the case the interrupt exists to
            # avoid, not the one it produces when it works.
            return self.backend.partial(kernel, CellOutcome(
                status="interrupted",
                stderr=(
                    "the cell was interrupted and the kernel did not answer within "
                    f"{INTERRUPT_GRACE_SECONDS:g}s, so it was stopped and its state is gone"
                ),
                kernel_lost=True,
            ))
        if failure is TIMED_OUT:
            return self.backend.partial(kernel, CellOutcome(
                status="timeout",
                stderr=(
                    f"the session budget ended this cell after {limit:g}s"
                    if limit < self.cell_seconds
                    else f"cell exceeded its {limit:g}s limit"
                ),
            ))
        # The stream ended, or desynchronised. One that desynchronised cannot
        # be trusted to be answering the cell we sent, so it is a death rather
        # than a bad answer.
        if self._interrupted.is_set():
            # Died *because* it was signalled. Not every interpreter turns an
            # interrupt into a traceback it can report -- one still starting
            # up has no handler installed yet, and a REPL may simply exit --
            # and a kernel Hardy stopped must not be recorded as one that fell
            # over on its own. The state is gone either way; what changes is
            # which cause the record names.
            return self.backend.partial(kernel, CellOutcome(
                status="interrupted",
                stderr="the cell was interrupted and the kernel did not survive it, so its state is gone",
                kernel_lost=True,
            ))
        return self.backend.partial(
            kernel, CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
        )

    def interrupt(self) -> bool:
        """Stop the cell in flight without stopping the kernel.

        Callable from any thread. Takes `_signal_lock` but never the session's
        own lock -- that is held by the thread inside `execute`, which is the
        thread this exists to reach, and waiting for it would deadlock against
        the very cell being stopped.

        Reports whether a cell was actually running to be stopped, so the
        terminal can say what it did rather than claiming to have stopped
        something that was not there. A press that finds nothing is still
        remembered: the cell it was aimed at may be a microsecond from going
        out, and `send` signals it as soon as it does.

        There is still a window in which the cell finishes just as the signal
        is sent, so the driver survives one arriving with no cell to abandon;
        that is defence in depth, not the design.
        """
        with self._signal_lock:
            self._stop_level = max(self._stop_level, _ASKED)
            if not self._in_flight.is_set():
                return False
            kernel = self.kernel
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
            if not self._shutting_down:
                self._stop_level = 0

    def escalate(self) -> bool:
        """Stop waiting for the interrupt to be answered, and stop the kernel.

        The second Esc. An interrupt is a request, and a cell deep in a library
        that never returns to its interpreter will not hear it; this is the way
        out of waiting for an answer that is not coming. It costs exactly what
        the timeout costs -- the namespace -- which is why it is the second
        press and not the first.

        Like `interrupt`, takes `_signal_lock` but never the session's lock, and refuses
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
            kernel = self.kernel
            if kernel is None:
                return False
            self._interrupted.set()
            self._killed = True
        # The reading thread sees both streams end, and -- because the flag is
        # set -- records the cell as interrupted with the kernel lost, which is
        # what happened. It drops the kernel itself when it gets there; nothing
        # here touches `kernel`, so the two threads cannot race over it.
        #
        # Straight to SIGKILL: this runs on the terminal's event loop, and the
        # graceful teardown's two-second wait would freeze the UI for exactly
        # as long as this press was made to avoid waiting.
        kernel.kill(immediate=True)
        return True

    def shut_down(self) -> None:
        """Kill even an idle kernel, and refuse every kernel from now on.

        Console closure is process shutdown, rather than an interrupt that
        preserves the namespace.
        """
        with self._signal_lock:
            self._shutting_down = True
            self._stop_level = _INSISTED
            self._killed = True
            kernel = self.kernel
        if kernel is not None:
            kernel.kill(immediate=True)
