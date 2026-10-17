"""Bounded execution and descendant checks for exported CAS scripts."""
from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from pathlib import Path
from typing import IO

from hardy.algebra.backends import CasBackend
from hardy.algebra.contracts import CasError
from hardy.algebra.operations import OperationControl
from hardy.foundation.process import (
    INTERRUPT_GRACE_SECONDS,
    can_contain,
    child_creation,
    child_environment,
    contain,
    kill_group,
    signal_interrupt,
    tree_has_members,
)
from hardy.foundation.values import FrozenModel


class ScriptRun(FrozenModel):
    """What running an exported script produced. `returncode` is None on timeout."""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    # Retention stopped at `cas_output_bytes`, so what is above is a prefix.
    # Any verdict drawn from it can only be `unverified`: an unread tail is not
    # evidence of agreement and is not evidence of disagreement either.
    capture_truncated: bool = False
    # The script exited while something it started was still running. What such
    # a run *does* is not bounded by what was observed of it: a descendant with
    # its own stdout keeps every worker here happy, outlives the check, and was
    # free to rewrite the published file after the verdict had been drawn on
    # it. The group is killed before anything reads the artifact back, so the
    # race is closed; this says the run had one, which is not something a
    # verdict can be drawn over.
    left_processes: bool = False
    # Nobody looked, as opposed to nobody was there. Where Hardy could not
    # hold the script's tree -- no job object on Windows, or a query that
    # failed -- it can neither account for what the script started nor stop
    # it, so the artifact-rewriting race above is open for that run, and a
    # verdict of `verified` would be a claim about a file that something the
    # run started is still free to change.
    descendants_unknown: bool = False


def _drain_capped(pipe: IO[bytes], into: bytearray, cap: int, overflowed: list[bool]) -> None:
    """Read a pipe to its end while keeping at most `cap` bytes.

    Reading has to continue past the cap even though retaining does not: a
    child whose pipe stops being read blocks on its next write and never
    reaches the deadline, and the wait for it never returns.
    """
    try:
        while chunk := pipe.read1(65_536):
            room = max(0, cap - len(into))
            into.extend(chunk[:room])
            if len(chunk) > room:
                overflowed[0] = True
    except (OSError, ValueError):
        overflowed[0] = True


def _feed(process: subprocess.Popen, payload: bytes) -> None:
    """Write the script to the child's stdin, then close it.

    Closing is not optional and is not only for the payload's sake: an
    interpreter reading stdin runs until EOF, so a handle left open keeps a
    child alive that has already done everything asked of it.
    """
    stdin = process.stdin
    if stdin is None:
        return
    try:
        # The child may exit, or be killed at the deadline, with the payload
        # part-written. Its own status is the answer; this thread just stops.
        with contextlib.suppress(OSError, ValueError):
            if payload:
                stdin.write(payload)
                stdin.flush()
    finally:
        with contextlib.suppress(OSError, ValueError):
            stdin.close()


# How often the wait for a script looks again at the deadline and at a stop.
_POLL_SECONDS = 0.05
# How long settling a finished run waits for the child to be reaped and for
# each capture thread to let go. Every wait is bounded: a wedged one costs a
# thread, never the export.
_SETTLE_SECONDS = 5


def _launch(backend: CasBackend, command: Path | None, script: Path, cwd: Path) -> subprocess.Popen:
    """Start the script as a reader would, its tree held as one."""
    argv = backend.script_argv(command, script)
    environment = dict(backend.environment)
    # The capture is decoded as UTF-8 below, so a Python child is told to
    # write it. On Windows its stdout would otherwise be encoded with the
    # console codepage, and the markers Hardy prints around the transcript --
    # `«` and `»` -- came back one byte each and decoded to U+FFFD, so every
    # export there was reported `diverged` for not printing its own markers.
    # Not part of `backend.environment`, which the manifest records as the
    # conditions the file was checked under: this is how Hardy reads the
    # output, not a condition the program's behaviour depends on. An
    # interpreter that is not Python ignores it.
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=child_environment(environment),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if backend.capture_mode == "merged" else subprocess.PIPE,
            **child_creation(),
        )
    except (OSError, ValueError) as error:
        raise CasError(
            f"could not run the exported script ({' '.join(argv)}): {error}"
        ) from None
    # Its tree, held as one: a process group on POSIX already, a job object on
    # Windows from here -- with `contain`'s race, which a venv launcher that
    # starts the real interpreter at once makes a real, if narrow, window.
    contain(process)
    return process


class _RunningScript:
    """One launched script: its capture threads, its stdin feed, and its stops."""

    def __init__(self, process: subprocess.Popen, payload: bytes, cap: int) -> None:
        self.process = process
        self.out, self.err = bytearray(), bytearray()
        self.overflowed = [False]
        self.left_behind = False
        # Nobody looked until `_sweep` says otherwise.
        self.unknown = True
        self.stop_requested = threading.Event()
        self.readers = tuple(
            threading.Thread(target=_drain_capped, args=(pipe, buffer, cap, self.overflowed), daemon=True)
            for pipe, buffer in ((process.stdout, self.out), (process.stderr, self.err))
            if pipe is not None
        )
        # Feeding stdin is a third concurrent job, not a preamble to the other
        # two. `subprocess.run(input=...)` multiplexes all three inside
        # `communicate()`; writing the payload straight through on this thread
        # instead re-created the deadlock `communicate` exists to avoid. Once
        # the payload passes the OS pipe buffer and the child is not draining
        # it fast enough, the write blocks -- and it blocks *before* the
        # deadline loop is ever reached, so no timeout applies and nothing can
        # kill the child. That is not a corner: `script_stdin` is how Singular
        # and Macaulay2 are fed, and with a 64 KiB per-cell source cap a
        # handful of cells passes any pipe buffer.
        self.feeder = threading.Thread(target=_feed, args=(process, payload), daemon=True)
        for worker in (*self.readers, self.feeder):
            worker.start()

    def interrupt(self) -> bool:
        self.stop_requested.set()
        if not signal_interrupt(self.process):
            # A GUI host on Windows has no console to deliver the signal.
            # Asking must still end the operation instead of waiting out its
            # full script deadline for an interrupt that never arrived.
            kill_group(self.process)
        return True

    def escalate(self) -> bool:
        self.stop_requested.set()
        kill_group(self.process)
        return True

    def wait(self, timeout: float) -> bool:
        """Wait for the script to exit, a stop's grace to end, or the deadline.

        True only when the deadline is what ended the wait.
        """
        deadline = time.monotonic() + timeout
        grace_deadline = None
        while True:
            now = time.monotonic()
            if self.stop_requested.is_set():
                if grace_deadline is None:
                    grace_deadline = now + INTERRUPT_GRACE_SECONDS
                if now >= grace_deadline:
                    kill_group(self.process)
                    return False
            remaining = deadline - now
            if remaining <= 0:
                return True
            if self._exits_within(min(_POLL_SECONDS, remaining)):
                return False

    def _exits_within(self, seconds: float) -> bool:
        try:
            self.process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return False
        return True

    def settle(self) -> None:
        """End the run and everything it started, then let go of its streams."""
        # Killed first, and only then joined. A writer still blocked on a full
        # pipe is released by the child's death -- the write fails and the
        # thread unwinds -- whereas closing the handle from here would queue
        # behind the very write that is stuck. Every worker is a daemon and
        # every join is bounded, so a wedged one costs a thread.
        if self.process.poll() is None:
            self.process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=_SETTLE_SECONDS)
        if self.stop_requested.is_set():
            kill_group(self.process)
        for worker in (*self.readers, self.feeder):
            worker.join(timeout=_SETTLE_SECONDS)
        # A reader still going is a reader with more to read. A descendant that
        # holds the pipe open and writes after the script itself has exited
        # keeps its worker alive past the join, and snapshotting the buffer
        # there and calling the capture complete let an export compare against
        # a transcript that was still arriving -- and report `verified` for it.
        if any(reader.is_alive() for reader in self.readers):
            self.overflowed[0] = True
        self._sweep()
        self._close_released_streams()

    def _sweep(self) -> None:
        """Stop whatever the script started, having first looked for it.

        Done here, before the caller reads the published file back. A
        descendant that redirected its own output is invisible to every check
        above -- the workers finish, the capture looks complete -- and it
        outlives the run: one that slept and then rewrote `sys.argv[0]` changed
        the artifact after the manifest had recorded its hash, with the verdict
        already `verified`. The group exists to be addressed as one, and the
        run is over.

        Probed before it is killed, because a group with members after the
        leader has been reaped is the evidence, and killing it destroys the
        evidence. What this cannot see is a descendant that left the group with
        `setsid` (or, on Windows, one created to break away from the job).
        Where nothing held the tree the probe answers None, and
        `descendants_unknown` says so, rather than a probe reporting "nothing
        left behind" because nobody looked.
        """
        if can_sweep_descendants():
            members = tree_has_members(self.process)
            self.left_behind = bool(members)
            self.unknown = members is None
        kill_group(self.process)

    def _close_released_streams(self) -> None:
        """Close each stream once its own worker has actually let go of it.

        A drain thread blocked in `pipe.read1` -- because, say, a grandchild
        the child spawned inherited the handle and is still holding it open --
        holds that `BufferedReader`'s lock for as long as the read is stuck;
        `_feed` closes stdin itself and holds its `BufferedWriter`'s lock the
        same way for as long as its write is stuck. Closing any of those same
        handles from here would wait on that same blocked call, with no
        timeout, and the bounded joins in `settle` buy nothing at all. Leaving
        a stream to its wedged worker costs a file handle for as long as that
        thread lives; the child is already dead, so its end of the pipe is
        gone regardless and nothing is kept alive by the wait.
        """
        closing = []
        if not self.readers[0].is_alive():
            closing.append(self.process.stdout)
        if len(self.readers) > 1 and not self.readers[1].is_alive():
            closing.append(self.process.stderr)
        if not self.feeder.is_alive():
            closing.append(self.process.stdin)
        for stream in closing:
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()

    def result(self, *, timed_out: bool) -> ScriptRun:
        return ScriptRun(
            returncode=None if timed_out else self.process.returncode,
            stdout=_decode(bytes(self.out)),
            stderr=_decode(bytes(self.err)),
            timed_out=timed_out,
            capture_truncated=self.overflowed[0],
            left_processes=self.left_behind,
            descendants_unknown=self.unknown,
        )


def run_exported_script(
    *,
    backend: CasBackend,
    command: Path | None,
    script: Path,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
    control: OperationControl | None = None,
) -> ScriptRun:
    """Execute a rendered script the way a reader would, and capture it.

    Not a kernel and not a replay: one process, the whole file, no framing.
    That is the point -- the artifact Hardy publishes is a file somebody runs,
    and until it has been run there is no evidence about what it does.

    Bounded like every other capture Hardy takes. `subprocess.run` with
    `capture_output` grows a buffer until the child stops writing, so a script
    of cells that print heavily would hold its whole transcript in Hardy's
    memory -- and a `MemoryError` raised there would take the export's
    artifacts with it, which is exactly what an export is supposed to survive.
    """
    if control is not None:
        control.check()
    cwd.mkdir(parents=True, exist_ok=True)
    # A line-oriented interpreter is fed the file on stdin, as the session
    # feeds it cells. Everything else gets an immediately closed stdin, so a
    # program that reads it sees EOF instead of blocking until the deadline.
    payload = script.read_bytes() if backend.script_stdin else b""
    running = _RunningScript(
        _launch(backend, command, script, cwd), payload, max(1, max_output_bytes)
    )
    # Keep the target registered while settling capture workers: a leader
    # may exit while a descendant still holds both output pipes open.
    with (control.bind(running.interrupt, running.escalate) if control else contextlib.nullcontext()):
        try:
            if control is not None:
                control.check()
            timed_out = running.wait(timeout)
        finally:
            running.settle()
    if control is not None:
        control.check()
    return running.result(timed_out=timed_out)


def can_sweep_descendants() -> bool:
    """Whether this host can account for and stop a script's children at all.

    POSIX puts the tree in a process group; Windows puts it in a job object
    (`contain`), whose active processes can be counted and terminated as one.
    A capability, not a verdict on one run: a job the host refused to assign
    leaves that run's `tree_has_members` answering None, and the run is then
    `descendants_unknown` on the evidence, not on the platform.

    Not a detail of the sweep but of what a verdict may claim. Reporting
    "nothing left behind" where nobody looked would call an artifact verified
    that a delayed child was still free to rewrite.
    """
    return can_contain()


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


