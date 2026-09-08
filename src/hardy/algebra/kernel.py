"""The bounded pipe protocol and lifetime of one persistent CAS process."""
from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hardy.algebra.contracts import INTERRUPTED, TIMED_OUT
from hardy.foundation.process import (
    INTERRUPT_GRACE_SECONDS,
    child_creation,
    child_environment,
    kill_group,
    signal_interrupt,
    terminate_group,
)


class _Kernel:
    """One live child, drained by threads so a deadline can always be enforced."""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: Path,
        max_output_bytes: int,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.argv = tuple(argv)
        self.max_output_bytes = max_output_bytes
        self.out = bytearray()
        self.err = bytearray()
        self.truncated = False
        self._finished = 0
        self._marker = b""
        self.marker_seen = False
        self._tail = b""
        self._changed = threading.Condition()
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
            stderr=subprocess.PIPE,
            **child_creation(),
        )
        for pipe, destination in ((self.process.stdout, self.out), (self.process.stderr, self.err)):
            threading.Thread(target=self._drain, args=(pipe, destination), daemon=True).start()

    def _drain(self, pipe, destination: bytearray) -> None:
        # read1, not read: a buffered `read(n)` blocks until it has all n bytes
        # or the stream ends, and a persistent kernel never ends. The reply to
        # a small cell would sit in the pipe unread forever.
        try:
            while chunk := pipe.read1(4_096):
                with self._changed:
                    room = max(0, self.max_output_bytes - len(destination))
                    destination.extend(chunk[:room])
                    if len(chunk) > room:
                        self.truncated = True
                    # Retention stops at the cap; scanning does not. A sentinel
                    # backend that overran the cap would otherwise never be seen
                    # to finish, and a large answer would read as a dead kernel.
                    if self._marker and destination is self.out:
                        self._tail = (self._tail + chunk)[-(len(self._marker) + 4_096) :]
                        if self._marker in self._tail:
                            self.marker_seen = True
                    self._changed.notify_all()
        except (OSError, ValueError):
            # `kill()` closes these pipes, and a `read1` already in flight on
            # one of them does not politely return b"" -- it raises, on a
            # thread with nobody to catch it, and Python prints the traceback
            # to stderr as if Hardy had crashed. `_drain_capped` has caught
            # exactly this pair since it was written; there is no reason for
            # the two drains to disagree. The stream is over either way, which
            # is what the `finally` below records.
            pass
        finally:
            with self._changed:
                self._finished += 1
                self._changed.notify_all()

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
        stdin = self.process.stdin
        if stdin is None:
            return False
        try:
            stdin.write(payload)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def read_reply(
        self,
        extract: Callable[[bytes], Any],
        deadline: float,
        interrupted: threading.Event | None = None,
    ) -> Any:
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
            while True:
                found = extract(bytes(self.out))
                # Checked before the interrupt, so a cell that finished in the
                # same instant Esc was pressed is reported as what it did
                # rather than as what the user asked for a moment too late.
                if found is not None:
                    return found
                if self._finished >= 2:
                    return None
                now = time.monotonic()
                if interrupted is not None and interrupted.is_set() and grace_deadline is None:
                    grace_deadline = now + INTERRUPT_GRACE_SECONDS
                stop_at = deadline if grace_deadline is None else min(deadline, grace_deadline)
                remaining = stop_at - now
                if remaining <= 0:
                    # A stop that was asked for is reported as one even if the
                    # cell's own deadline happened to pass while the kernel was
                    # being given its grace. The user stopped this; calling it
                    # a timeout would credit the limit for what Esc did.
                    if interrupted is not None and interrupted.is_set():
                        return INTERRUPTED
                    return TIMED_OUT
                # Nothing notifies this condition when the interrupt flag is
                # set on another thread, so the poll interval is also what
                # bounds how long it takes to notice one.
                self._changed.wait(min(remaining, 0.05))

    def stderr_text(self) -> str:
        with self._changed:
            return bytes(self.err).decode("utf-8", errors="replace")

    def stderr_settled(self, timeout: float = 0.2, quiet: float = 0.02) -> str:
        """Stderr once it has stopped growing, not just whatever is in yet.

        A sentinel cell's own interpreter is single-threaded: an error for
        the cell is necessarily written to stderr before the interpreter goes
        on to process the end-marker echo that shows up on stdout, which is
        what `read_reply` waits for. But that ordering is *inside the child*
        -- stdout and stderr are two independent pipes drained by two
        independent threads here, and nothing ties their delivery to Hardy
        together. Reading stderr the instant the stdout marker is found (as
        this used to do) can win a race against the drain thread that has not
        yet appended bytes already sitting in the OS pipe, silently reading a
        broken M2 cell as clean.

        `quiet` seconds have to pass with no growth in `self.err`, measured
        against the wall clock -- not "the next wakeup shows no growth",
        which the stdout drain thread's `notify_all()` on every chunk
        defeats: it wakes this wait long before `quiet` has actually
        elapsed, so a between-wakeups check would report "settled" on a
        stdout-driven spurious wakeup microseconds in, never having waited
        at all.
        """
        with self._changed:
            deadline = time.monotonic() + timeout
            last_growth = time.monotonic()
            last_len = len(self.err)
            while True:
                now = time.monotonic()
                if now - last_growth >= quiet:
                    break
                remaining = deadline - now
                if remaining <= 0:
                    break
                self._changed.wait(min(quiet - (now - last_growth), remaining))
                current_len = len(self.err)
                if current_len != last_len:
                    last_len = current_len
                    last_growth = time.monotonic()
            return bytes(self.err).decode("utf-8", errors="replace")

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
                self.process.wait(timeout=0 if immediate else 2)
            except subprocess.TimeoutExpired:
                kill_group(self.process)
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


