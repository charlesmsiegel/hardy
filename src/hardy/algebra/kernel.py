"""The bounded pipe protocol and lifetime of one persistent CAS process."""
from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from hardy.algebra.contracts import INTERRUPTED, TIMED_OUT, Extractor, ReadFailure, Reply
from hardy.foundation.process import (
    INTERRUPT_GRACE_SECONDS,
    child_creation,
    child_environment,
    contain,
    kill_group,
    signal_interrupt,
    terminate_group,
    tree_has_members,
)

# How much one drain read takes from a pipe. Also the width of the window the
# end-marker scan keeps beyond the marker itself: a marker can straddle two
# reads, and no read is longer than this.
_READ_CHUNK_BYTES = 4_096
# How often a wait for a reply looks again. Nothing notifies the reader when
# an interrupt is asked for on another thread, so this also bounds how long it
# takes to notice one.
_POLL_SECONDS = 0.05
# How long a polite teardown waits at each step: for the child to exit after
# SIGTERM, to be reaped after SIGKILL, and for a Windows job to release its
# handles.
_SETTLE_SECONDS = 2


def _is_set(event: threading.Event | None) -> bool:
    return event is not None and event.is_set()


def _settle_job(process: subprocess.Popen, readers: list[threading.Thread]) -> None:
    """Wait, briefly, for a terminated Windows job to let go of its handles.

    TerminateJobObject returns before its members have released their
    handles. The venv launcher exiting is not proof its interpreter has
    released cwd; close must settle that job before a rename.
    """
    deadline = time.monotonic() + _SETTLE_SECONDS
    while tree_has_members(process) is True and time.monotonic() < deadline:
        time.sleep(0.01)
    for reader in readers:
        reader.join(timeout=max(0, deadline - time.monotonic()))


class Kernel:
    """One live child, drained by threads so a deadline can always be enforced."""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: Path,
        max_output_bytes: int,
        environment: Mapping[str, str] | None = None,
        *,
        merge_stderr: bool = False,
    ) -> None:
        self.argv = tuple(argv)
        self.max_output_bytes = max_output_bytes
        self.out = bytearray()
        self.err = bytearray()
        self.truncated = False
        self._finished = 0
        self._reader_count = 1 if merge_stderr else 2
        self._marker = b""
        self.marker_seen = False
        self._tail = b""
        self._changed = threading.Condition()
        self._input_lock = threading.Lock()
        self._input_writers = 0
        self._stopped = False
        cwd.mkdir(parents=True, exist_ok=True)
        # Its own process group (see `child_creation`), so an interrupt reaches
        # a cell that shelled out as well as the interpreter that started it,
        # and so nothing aimed at Hardy's own group lands here by accident.
        self.process = subprocess.Popen(
            self.argv,
            cwd=str(cwd),
            env=child_environment(dict(environment or {})),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            **child_creation(),
        )
        # And, on Windows, in a job of its own, which is that platform's
        # process group: without one `kill()` reached the interpreter and
        # nothing a cell had started. Not registered with `tracked`, because
        # the session owns this child rather than the turn that started it
        # (see `interrupt_children`). `contain`'s race is real here: the
        # driver itself starts nothing before its first frame, but on Windows
        # a venv's `python.exe` is a launcher that starts the real interpreter
        # as its child at once. Assignment usually wins that millisecond
        # window; when it loses, the interpreter and everything its cells
        # start are outside the job, and a stop reaches only the launcher, as
        # it did before any job existed. Closing it would take a suspended
        # start (`CREATE_SUSPENDED`, assign, resume), which is not done.
        contain(self.process)
        self._readers: list[tuple[IO[bytes], threading.Thread]] = []
        for pipe, destination in ((self.process.stdout, self.out), (self.process.stderr, self.err)):
            if pipe is not None:
                reader = threading.Thread(target=self._drain, args=(pipe, destination), daemon=True)
                self._readers.append((pipe, reader))
                reader.start()

    def _drain(self, pipe, destination: bytearray) -> None:
        # A stream closed under a `read1` already in flight does not politely
        # return b"" -- it raises, on a thread with nobody to catch it, and
        # Python prints the traceback to stderr as if Hardy had crashed.
        # `_drain_capped` has suppressed exactly this pair since it was
        # written; there is no reason for the two drains to disagree. The
        # stream is over either way, which is what the `finally` records.
        try:
            with contextlib.suppress(OSError, ValueError):
                # read1, not read: a buffered `read(n)` blocks until it has all
                # n bytes or the stream ends, and a persistent kernel never
                # ends. The reply to a small cell would sit in the pipe unread
                # forever.
                while chunk := pipe.read1(_READ_CHUNK_BYTES):
                    with self._changed:
                        self._keep(chunk, destination)
                        self._changed.notify_all()
        finally:
            # The reader owns this stream until EOF, including when a helper
            # escaped the process group and still holds its write end.
            with contextlib.suppress(OSError, ValueError):
                close = getattr(pipe, "close", None)
                if close is not None:
                    close()
            with self._changed:
                self._finished += 1
                self._changed.notify_all()

    def _keep(self, chunk: bytes, destination: bytearray) -> None:
        """Retain what fits under the cap, and scan stdout for the end marker."""
        room = max(0, self.max_output_bytes - len(destination))
        destination.extend(chunk[:room])
        if len(chunk) > room:
            self.truncated = True
        # Retention stops at the cap; scanning does not. A sentinel backend
        # that overran the cap would otherwise never be seen to finish, and a
        # large answer would read as a dead kernel.
        if self._marker and destination is self.out:
            self._tail = (self._tail + chunk)[-(len(self._marker) + _READ_CHUNK_BYTES) :]
            if self._marker in self._tail:
                self.marker_seen = True

    def clear(self, marker: bytes = b"") -> None:
        """Discard everything read so far and scan fresh for `marker`.

        Only for the length path: the driver emits exactly one frame and
        nothing else, so there is never anything worth keeping behind it.
        """
        with self._changed:
            self.out.clear()
            self.err.clear()
            self.truncated = False
            self._marker = marker
            self.marker_seen = False
            self._tail = b""

    def consume(self, upto: int) -> None:
        """Drop the bytes belonging to the cell just answered, keep the rest.

        A prompt printed after the end marker belongs to no cell. It is not
        deleted here on the theory that it might not have arrived yet -- it
        might not have -- but that no longer matters: the *next* cell's begin
        marker, once found, is proof that everything before it, arrived or
        not at the time this runs, is behind it in the stream.
        """
        with self._changed:
            del self.out[:upto]
            self.truncated = False
            self._marker = b""
            self.marker_seen = False
            self._tail = b""

    def rearm(self, marker: bytes) -> None:
        """Scan fresh for the end `marker` without discarding what is in `out`.

        Nothing here needs to guess whether the previous cell's trailing
        prompt has fully arrived: the begin marker this cell is about to send
        settles that by pipe order alone, once the extractor finds it.
        """
        with self._changed:
            self.err.clear()
            self.truncated = False
            self._marker = marker
            self.marker_seen = False
            self._tail = b""

    def send(self, payload: bytes) -> bool:
        """Write `payload` to the child's stdin; False if it did not all go.

        Returns as soon as the kernel is stopped, even with a write still
        blocked on a pipe nobody reads.
        """
        # Claim ownership before touching BufferedWriter. Shutdown must never
        # wait for its IO lock when an escaped helper keeps the input pipe open.
        with self._input_lock:
            stdin = self.process.stdin
            if stdin is None or self._stopped:
                return False
            self._input_writers += 1
        finished = threading.Event()
        sent = False

        def write() -> None:
            # A write that fails leaves `sent` false, and that is the whole
            # report: the caller treats an undelivered frame as a lost kernel.
            nonlocal sent
            try:
                with contextlib.suppress(OSError, ValueError):
                    stdin.write(payload)
                    stdin.flush()
                    sent = True
            finally:
                with self._input_lock:
                    self._input_writers -= 1
                    close = self._stopped and self._input_writers == 0
                if close:
                    with contextlib.suppress(OSError, ValueError):
                        stdin.close()
                finished.set()

        # A helper outside the group can retain the read end even after the
        # interpreter is killed. Leave a wedged writer its handle, without keeping
        # the session caller and its lock waiting for that unrelated process.
        threading.Thread(target=write, daemon=True).start()
        while not finished.wait(0.05):
            with self._input_lock:
                stopped = self._stopped
            if stopped:
                return False
        return sent

    def read_reply(
        self,
        extract: Extractor,
        deadline: float,
        interrupted: threading.Event | None = None,
    ) -> Reply | ReadFailure | None:
        """Wait for a complete reply, the kernel's death, the deadline, or a stop.

        The extractor sees raw bytes. Decoding first would make a partial
        multi-byte character into a replacement character three bytes wide,
        and a length-prefixed frame would then look complete before it was.

        `interrupted` is set by whoever pressed Esc, on their thread, after the
        signal has already gone to the child. It does not end the wait by
        itself: an interrupted kernel is *expected* to answer -- the driver
        turns the signal into a traceback and replies with it, which is what
        leaves the namespace intact -- so the reply is still what this is
        waiting for, only now with a much shorter deadline. `INTERRUPTED` is
        the answer that no reply came within the grace, and it means the kernel
        can no longer be spoken to.
        """
        with self._changed:
            grace_deadline: float | None = None
            # Checked before the interrupt, so a cell that finished in the same
            # instant Esc was pressed is reported as what it did rather than as
            # what the user asked for a moment too late.
            while (found := extract(bytes(self.out))) is None:
                if self._finished >= self._reader_count:
                    return None
                now = time.monotonic()
                if grace_deadline is None and _is_set(interrupted):
                    grace_deadline = now + INTERRUPT_GRACE_SECONDS
                stop_at = deadline if grace_deadline is None else min(deadline, grace_deadline)
                remaining = stop_at - now
                if remaining <= 0:
                    # A stop that was asked for is reported as one even if the
                    # cell's own deadline happened to pass while the kernel was
                    # being given its grace. The user stopped this; calling it
                    # a timeout would credit the limit for what Esc did.
                    return INTERRUPTED if _is_set(interrupted) else TIMED_OUT
                self._changed.wait(min(remaining, _POLL_SECONDS))
            return found

    def stderr_text(self) -> str:
        with self._changed:
            return bytes(self.err).decode("utf-8", errors="replace")

    def stdout_text(self) -> str:
        with self._changed:
            return bytes(self.out).decode("utf-8", errors="replace")

    def interrupt(self) -> bool:
        """Ask the cell in flight to stop, leaving the kernel alive to say so."""
        return signal_interrupt(self.process)

    def kill(self, *, immediate: bool = False) -> None:
        """Stop the kernel. `immediate` skips the polite half.

        The graceful teardown asks with SIGTERM and waits up to two seconds
        before SIGKILL, which is right when a session is closing. It is wrong
        for the second Esc: that runs on the terminal's own event loop, so the
        wait would freeze the UI -- for exactly as long as the press was made
        to avoid waiting.
        """
        with self._input_lock:
            self._stopped = True
        # The group, whether or not the interpreter still leads it. A cell that
        # shelled out leaves its helpers in the group, and an interpreter that
        # took the signal and exited leaves them there with no leader -- still
        # running, and still holding the pipes this session drains. Gating on
        # `poll()` would skip exactly that case, which is why the shared group
        # helpers do not gate on it either.
        if immediate:
            kill_group(self.process)
        else:
            terminate_group(self.process)
        if self.process.poll() is None:
            try:
                # SIGKILL cannot be caught, so the immediate path is only ever
                # waiting to reap; the polite one is waiting on the child.
                self.process.wait(timeout=0 if immediate else _SETTLE_SECONDS)
            except subprocess.TimeoutExpired:
                kill_group(self.process)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=_SETTLE_SECONDS)
        # SIGTERM taking the leader is not proof its helpers stopped.
        kill_group(self.process)
        if os.name == "nt" and not immediate:
            _settle_job(self.process, [reader for _, reader in self._readers])
        self._close_released_streams()

    def _close_released_streams(self) -> None:
        """Close every stream no thread still holds.

        Closing a BufferedReader still owned by read1 would wait without a
        deadline. Its daemon reader closes it itself when the holder exits.
        """
        with self._input_lock:
            streams = [self.process.stdin] if self._input_writers == 0 else []
        streams.extend(pipe for pipe, reader in self._readers if not reader.is_alive())
        for stream in streams:
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()


