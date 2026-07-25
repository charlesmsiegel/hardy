"""A persistent computer algebra session.

Hardy can ask Lean whether a proof is correct but cannot compute anything, so
the model has no way to find out what is worth proving. This module is the
computation: one long-lived kernel per session, a durable log of the cells that
built its state, and the machinery to rebuild that state in a fresh process.

The kernel is persistent because the alternative is not affordable. Replaying
the accumulated script on every call would recompute a Gröbner basis every
turn. So state lives in a running process, and replay is kept for the two jobs
it is actually good at: rebuilding after a kernel dies, and proving that an
exported script reproduces what the session saw.

Two rules here are easy to get wrong and are therefore enforced in this file
rather than by its callers. Every call is serialised, because a kernel is one
stateful process behind one stdin stream and three different bindings can reach
it. And a rebuild compares what it reconstructed against what was recorded,
because a cell that runs without error may still have produced a different
value, and everything executed afterwards would be standing on it.

Nothing here is sandboxed. A cell can call `os.system`, Macaulay2's `run`, or
Singular's `system("sh", ...)`. Run only trusted output in a disposable
environment.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from .domain import FrozenModel, RunLimits
from .process import child_environment

HEADER_BYTES = 10
# A cell is bracketed by two markers, not trailed by one. A pipe preserves
# write order, so whatever the interpreter printed for the *previous* cell is
# necessarily before this cell's begin marker in the stream, however late it
# happens to arrive -- which is what lets the extractor exclude it without
# ever having to guess whether it has fully arrived yet.
SENTINEL_BEGIN = "«hardy-begin:{nonce}»"
SENTINEL_END = "«hardy-end:{nonce}»"
BackendName = Literal["sympy", "singular", "macaulay2"]

# Distinguishable non-answers from a read: the deadline passed, or the stream
# said something that cannot belong to the cell we sent.
TIMED_OUT = object()
DESYNCHRONISED = object()


class CasError(Exception):
    """A CAS call that cannot be answered, phrased for the model that asked."""


class CellOutcome(FrozenModel):
    """What an adapter extracts from one framed reply, before Hardy records it."""

    status: Literal["ok", "error", "kernel_died", "timeout"]
    stdout: str = ""
    stderr: str = ""
    value_repr: str = ""
    capture_truncated: bool = False


class CellRecord(FrozenModel):
    seq: int
    # Incremented by reset. Only the highest segment is live, which is how a
    # reset survives a restart: it is on every record rather than inferred from
    # a sentinel line that a reader would have to know how to recognise.
    segment: int
    author: Literal["model", "human"]
    source: str
    status: Literal["ok", "error", "timeout", "kernel_died"]
    accepted: bool
    stdout: str = ""
    stderr: str = ""
    value_repr: str = ""
    duration_ms: int = 0
    capture_truncated: bool = False
    output_artifact: str | None = None
    # Hardy's own commentary on the cell -- currently only that the kernel was
    # rebuilt before it ran. Deliberately its own field rather than a line
    # prepended to `stdout`: `stdout` is what the kernel produced, and it is
    # what `reproduces` compares and what the export replays. A note mixed into
    # it makes the record unreproducible by construction, which poisons the
    # next rebuild and marks every post-restart cell `diverged` on export.
    restart_note: str = ""


class RebuildReport(FrozenModel):
    replayed: int = 0
    diverged: tuple[int, ...] = ()
    failed: int | None = None
    ok: bool = True


def normalise(text: str) -> str:
    """Compare outputs without being defeated by trailing whitespace."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def reproduces(record: CellRecord, outcome: CellOutcome) -> bool:
    """Whether a replayed cell produced what the live session recorded.

    stderr counts. The notebook preserves it, so a cell whose warnings did not
    reproduce has not reproduced, whatever its stdout says.
    """
    return (
        normalise(outcome.stdout) == normalise(record.stdout)
        and normalise(outcome.stderr) == normalise(record.stderr)
        and normalise(outcome.value_repr) == normalise(record.value_repr)
    )


class SympyBackend:
    """The default backend: Hardy's own interpreter, driven over a byte protocol."""

    name: BackendName = "sympy"
    script_suffix = ".py"
    language = "python"
    kernel_name = "python3"
    framing = "length"
    comment = "#"
    preamble = "from sympy import *"
    version_source = '__import__("sympy").__version__'

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (
            str(command) if command else sys.executable,
            "-u",
            "-m",
            "hardy.cas_driver",
            str(max_output_bytes),
        )

    def frame(self, source: str, nonce: str) -> bytes:
        payload = json.dumps({"source": source}, ensure_ascii=False).encode("utf-8")
        return f"{len(payload):0{HEADER_BYTES}d}".encode("ascii") + payload

    def parse_version(self, sanitized_stdout: str) -> str:
        return sanitized_stdout


class _SentinelBackend:
    """An interpreter reading stdin, framed by a nonce it is asked to echo.

    Less trustworthy than the driver protocol and unavoidable: neither Singular
    nor Macaulay2 offers a way to be spoken to in frames. The nonce is fresh
    per cell so a cell that echoes text cannot forge the end of its own reply.
    """

    framing = "sentinel"
    error_pattern: re.Pattern[str]
    echo: str

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        raise NotImplementedError

    def frame(self, source: str, nonce: str) -> bytes:
        begin = SENTINEL_BEGIN.format(nonce=nonce)
        end = SENTINEL_END.format(nonce=nonce)
        return (
            self.echo.format(marker=begin)
            + "\n"
            + source.rstrip()
            + "\n"
            + self.echo.format(marker=end)
            + "\n"
        ).encode("utf-8")

    def classify(self, stdout: str, stderr: str = "") -> Literal["ok", "error"]:
        found = self.error_pattern.search(stdout) or self.error_pattern.search(stderr)
        return "error" if found else "ok"

    def sanitize(self, stdout: str) -> str:
        """Backend-specific stdout cleanup, applied to a cell's captured body
        before it is recorded. Identity by default; Macaulay2 overrides it."""
        return stdout

    def parse_version(self, sanitized_stdout: str) -> str:
        """Pull the bare version string out of an already-`sanitize`d reply.

        Identity by default. Macaulay2 overrides it: `sanitize` deliberately
        leaves an `o = ` value marker in place for ordinary cells (it is
        meaningful context there), but `probe_version` wants just the value.
        """
        return sanitized_stdout


class SingularBackend(_SentinelBackend):
    name: BackendName = "singular"
    script_suffix = ".sing"
    language = "singular"
    kernel_name = "singular"
    comment = "//"
    preamble = ""
    version_source = 'system("version");'
    echo = 'print("{marker}");'
    # Singular indents its `?` error banner by call-stack depth, not a fixed
    # maximum -- an error raised inside a nested procedure can be indented
    # arbitrarily far. Any run of leading horizontal whitespace counts;
    # newlines are excluded so this stays anchored to one line's own start.
    error_pattern = re.compile(r"(?m)^[ \t]*\? ")

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (str(command) if command else "Singular", "-q")


class Macaulay2Backend(_SentinelBackend):
    name: BackendName = "macaulay2"
    script_suffix = ".m2"
    language = "macaulay2"
    kernel_name = "macaulay2"
    comment = "--"
    preamble = ""
    version_source = 'version#"VERSION"'
    echo = '<< "{marker}" << endl;'
    # Observed verbatim from M2 1.26.06 (CI run 30167266358, "Debug M2 raw
    # transcript"): a division-by-zero cell wrote
    # `stdio:2:1:(3):[1]: error: division by zero` to *stderr*, and a call on
    # an undefined symbol wrote
    # `stdio:2:16:(3):[1]: error: no method for adjacent objects:`. Both carry
    # a `:[N]:` interpreter-depth marker between the `(FRAME)` and `error:`
    # that the original guess did not have, and both landed on stderr, not
    # stdout -- see `classify`/`sanitize` below for how that is handled.
    #
    # The `:[N]:` segment is made optional, not required: both samples came
    # from one M2 build, and a build (or error path) that omits it -- the
    # form the pre-verification guess used -- must still classify as
    # "error". A false negative here is accepted into replayable state and
    # the session rebuilds from a cell that never worked, which is strictly
    # worse than a false positive; there is no cost to the wider pattern.
    error_pattern = re.compile(r"(?m)^stdio:\d+:\d+:\(\d+\)(?::\[\d+\])?: error:")
    # M2 echoes each `iN : ` input prompt (and the source line behind it) even
    # when stdin is not a tty, and prints an `oN` counter before every
    # non-suppressed statement's value. Observed verbatim in the same run: a
    # cell containing `R = QQ[x, y]; f = x^2 + y^2; f` came back as
    # `i2 : R = QQ[x, y]; f = x^2 + y^2; f\n\n      2    2\no4 = x  + y\n\no4 : R`.
    # The `iN :` lines are pure noise -- an echo of source Hardy already has on
    # the `CellRecord` -- and the `oN` counter drifts with how many statements
    # ran before it, which is different for a live session (that has already
    # run a version probe) than for the fresh kernel `replay_in_fresh_kernel`
    # starts for export verification. Left unstripped, a cell that reproduces
    # exactly is still reported `diverged` on the counter alone (confirmed:
    # CI run 30167033381 marked the one cell in
    # `test_an_exported_session_reproduces[macaulay2]` diverged on precisely
    # this transcript). Stripping the prompt lines and blanking the counter
    # digits is the fix for the prompt-noise defect flagged in this task's
    # brief.
    _prompt_line = re.compile(r"(?m)^i\d+ : .*\n")
    _output_counter = re.compile(r"(?m)^o\d+(?=[ :=])")
    # `[ \t]`, not `\s`: `\s` matches newlines too, which would let this
    # cross onto whatever comes after the marker's own line instead of
    # stopping at it.
    _value_marker = re.compile(r"(?m)^o[ \t]*=[ \t]*")

    def sanitize(self, stdout: str) -> str:
        stdout = self._prompt_line.sub("", stdout)
        return self._output_counter.sub("o", stdout)

    def parse_version(self, sanitized_stdout: str) -> str:
        # Confirmed of CI run 30168046413's "Debug sanitized M2 stdout" step:
        # without this, session.version came back as the literal string
        # 'o = 1.26.06' -- the `o = ` value marker `sanitize` leaves in place
        # is exactly right for an ordinary cell but wrong for a version
        # string quoted into an exported script's header comment.
        value = self._value_marker.sub("", sanitized_stdout, count=1).strip()
        # `version#"VERSION"` is a plain string, and the one probe transcript
        # captured directly (CI run 30168174637) showed no further lines --
        # but M2 prints an `o : ClassName` annotation after some result
        # types (confirmed for a ring element: `o4 : R`), and `sanitize`
        # would leave that as `o : String` rather than strip it, same as it
        # leaves `o = ` for an ordinary cell. Taking only the first line is
        # correct either way: the version string itself never contains a
        # newline, so this is a no-op when the annotation line is absent
        # and the fix when it is not.
        first_line, _, _ = value.partition("\n")
        return first_line

    def argv(self, command: Path | None, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        # `-s` was a guess and is obsolete in Macaulay2 1.26.06 (CI run
        # 30166702246: "error: command line option -s is obsolete." killed the
        # kernel before it could answer the version probe). Dropped, not
        # replaced -- there is no confirmed silent-mode equivalent yet.
        return (str(command) if command else "M2", "--no-readline", "-q")


BACKENDS: dict[str, Any] = {
    "sympy": SympyBackend,
    "singular": SingularBackend,
    "macaulay2": Macaulay2Backend,
}


def backend_for(name: str) -> Any:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"unknown cas_backend {name!r}; known backends are {sorted(BACKENDS)}"
        ) from None


class _Kernel:
    """One live child, drained by threads so a deadline can always be enforced."""

    def __init__(self, argv: Sequence[str], cwd: Path, max_output_bytes: int) -> None:
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
        self.process = subprocess.Popen(
            self.argv,
            cwd=str(cwd),
            env=child_environment({}),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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

    def read_reply(self, extract: Callable[[bytes], Any], deadline: float) -> Any:
        """Wait for a complete reply, the kernel's death, or the deadline.

        The extractor sees raw bytes. Decoding first would make a partial
        multi-byte character into a replacement character three bytes wide,
        and a length-prefixed frame would then look complete before it was.
        """
        with self._changed:
            while True:
                found = extract(bytes(self.out))
                if found is not None:
                    return found
                if self._finished >= 2:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return TIMED_OUT
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

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


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
        self.limits = limits
        self.cwd = cwd or log_path.parent
        self.observe = observe
        self.state: Literal["cold", "live", "dead", "poisoned"] = "cold"
        self.version: str | None = None
        self.spent_seconds = 0.0
        # One stateful process behind one stdin stream, reachable from chat,
        # staged runs, and MCP. The lock belongs to the resource, not a caller.
        self._lock = threading.RLock()
        self._kernel: _Kernel | None = None
        self._records: list[CellRecord] = self._load()

    # ------------------------------------------------------------------ log

    def _load(self) -> list[CellRecord]:
        if not self.log_path.exists():
            return []
        records = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(CellRecord.model_validate_json(line))
        return records

    def _append(self, record: CellRecord) -> CellRecord:
        self._records.append(record)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(record.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self.observe is not None:
            self.observe({"type": "cas", "record": record.model_dump(mode="json")})
        return record

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
            self._kernel = _Kernel(argv, self.cwd, retain)
        except (OSError, ValueError) as error:
            self.state = "dead"
            raise CasError(
                f"could not start the {self.backend.name} kernel "
                f"({' '.join(argv)}): {error}"
            ) from None
        self.state = "live"

    def probe_version(self) -> str:
        """Ask the kernel what it is. Doubles as the smoke test: a backend that
        cannot answer this is not a working backend, however present it looks."""
        with self._lock:
            if self._kernel is None:
                self._start()
            outcome = self._send(self.backend.version_source)
            if outcome.status != "ok":
                raise CasError(
                    f"{self.backend.name} kernel did not answer a version query: "
                    f"{(outcome.stderr or outcome.stdout).strip()[:200]}"
                )
            raw = self.backend.parse_version(outcome.value_repr or outcome.stdout)
            self.version = raw.strip().strip("'\"") or "unknown"
            return self.version

    def _extractor(self, nonce: str) -> Callable[[bytes], Any]:
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
                if raw[echoed_end : echoed_end + len(tail)] == tail:
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
            body = self.backend.sanitize(body)
            outcome = CellOutcome(
                # Provisional, and never read: `_send` reclassifies every
                # sentinel reply once stderr has settled, because a Macaulay2
                # error leaves nothing error-shaped on stdout at all.
                # Classifying here as well would only be a second answer to a
                # question that already has one.
                status="ok",
                stdout=body,
                capture_truncated=bool(kernel and kernel.truncated),
            )
            return outcome, consumed

        return extract_sentinel

    def _send(self, source: str) -> CellOutcome:
        """One round trip. Assumes the lock and a started kernel."""
        kernel = self._kernel
        assert kernel is not None
        nonce = f"{time.monotonic_ns():x}"
        if self.backend.framing == "length":
            kernel.clear()
        else:
            end = SENTINEL_END.format(nonce=nonce).encode()
            kernel.rearm(end)
        if not kernel.send(self.backend.frame(source, nonce)):
            return CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
        deadline = time.monotonic() + self.limits.cas_cell_seconds
        reply = kernel.read_reply(self._extractor(nonce), deadline)
        if reply is TIMED_OUT:
            return CellOutcome(
                status="timeout",
                stderr=f"cell exceeded its {self.limits.cas_cell_seconds}s limit",
            )
        if reply is None or reply is DESYNCHRONISED:
            # A stream that desynchronised cannot be trusted to be answering
            # the cell we sent, so it is a death rather than a bad answer.
            return CellOutcome(status="kernel_died", stderr=kernel.stderr_text())
        outcome, consumed = reply
        if self.backend.framing == "sentinel":
            kernel.consume(consumed)
            stderr_text = kernel.stderr_settled()
            # `extract_sentinel` classified on stdout alone, before stderr for
            # this cell was necessarily complete. Reclassify now that both
            # streams are in: confirmed of Macaulay2 (CI run 30167266358),
            # whose errors ("stdio:...: error: ...") land on stderr with
            # nothing error-shaped left on stdout at all, so a stdout-only
            # classification always read a broken M2 cell as "ok".
            status = self.backend.classify(outcome.stdout, stderr_text)
            outcome = outcome.model_copy(update={"stderr": stderr_text, "status": status})
        return outcome

    # ------------------------------------------------------------- execute

    def _guard(self) -> None:
        if self.state == "poisoned":
            raise CasError(
                "the CAS session is poisoned: its state could not be rebuilt faithfully. "
                "Reset it to start a clean kernel."
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
            if self._kernel is None or self.state == "dead":
                report = self._restore()
                if report.replayed:
                    notes = f"[kernel restarted; replayed {report.replayed} cell(s)]"

            started = time.monotonic()
            outcome = self._send(source)
            elapsed = time.monotonic() - started
            self.spent_seconds += elapsed

            status = outcome.status
            truncated = outcome.capture_truncated
            if status in {"timeout", "kernel_died"}:
                self._drop_kernel()
            record = CellRecord(
                seq=len(self._records),
                segment=self.segment,
                author=author,  # type: ignore[arg-type]
                source=source,
                status=status,  # type: ignore[arg-type]
                accepted=status == "ok",
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                value_repr=outcome.value_repr,
                duration_ms=round(elapsed * 1_000),
                capture_truncated=truncated,
                restart_note=notes,
            )
            return self._append(record)

    def _drop_kernel(self) -> None:
        if self._kernel is not None:
            self._kernel.kill()
        self._kernel = None
        self.state = "dead"

    def _restore(self) -> RebuildReport:
        """Rebuild live state after a death, and verify what was rebuilt."""
        self._start()
        pending = self.accepted()
        if not pending:
            return RebuildReport()
        diverged: list[int] = []
        for index, record in enumerate(pending):
            outcome = self._send(record.source)
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
            if not reproduces(record, outcome):
                diverged.append(record.seq)
            if index % 4 == 3:
                self._guard()
        if diverged:
            self.state = "poisoned"
            raise CasError(
                "could not rebuild CAS state faithfully: cell(s) "
                f"{diverged} produced different output on replay. "
                "Reset the session to start clean."
            )
        return RebuildReport(replayed=len(pending))

    def reset(self) -> None:
        """Close the current segment. Nothing is deleted.

        The boundary is itself a `CellRecord` carrying the new segment, so one
        schema describes the whole log and the reset is durable the moment it
        happens rather than when the next cell is written.
        """
        with self._lock:
            self._drop_kernel()
            self.state = "cold"
            self.spent_seconds = 0.0
            self._append(
                CellRecord(
                    seq=len(self._records),
                    segment=self.segment + 1,
                    author="human",
                    source="",
                    status="ok",
                    accepted=False,
                )
            )

    def close(self) -> None:
        with self._lock:
            if self._kernel is not None:
                self._kernel.kill()
            self._kernel = None


def replay_in_fresh_kernel(
    *,
    backend: Any,
    command: Path | None,
    cells: Sequence[CellRecord],
    limits: RunLimits,
    cwd: Path,
    budget_seconds: float | None = None,
) -> list[CellOutcome | None]:
    """Run cells in a throwaway kernel. `None` marks a cell never reached."""
    session = CasSession(
        backend=backend,
        command=command,
        log_path=cwd / "replay-scratch.jsonl",
        limits=limits,
        cwd=cwd,
    )
    outcomes: list[CellOutcome | None] = []
    budget = budget_seconds if budget_seconds is not None else limits.cas_session_seconds
    try:
        session._start()
        spent = 0.0
        for record in cells:
            if spent >= budget:
                outcomes.append(None)
                continue
            started = time.monotonic()
            outcome = session._send(record.source)
            spent += time.monotonic() - started
            outcomes.append(outcome)
            if outcome.status in {"kernel_died", "timeout"}:
                outcomes.extend([None] * (len(cells) - len(outcomes)))
                break
    except CasError:
        outcomes.extend([None] * (len(cells) - len(outcomes)))
    finally:
        session.close()
        (cwd / "replay-scratch.jsonl").unlink(missing_ok=True)
    return outcomes
