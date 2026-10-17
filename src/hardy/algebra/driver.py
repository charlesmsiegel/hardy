"""A persistent Python kernel, spoken to in length-prefixed JSON.

Hardy drives SymPy through this rather than by scraping a REPL prompt. A
REPL's framing is a guess about where one answer ends; a byte count is not.
Every reply carries its length in a fixed ten-byte header, so a cell that
prints something resembling a prompt cannot be mistaken for the end of output.

Two behaviours here exist because `exec` alone would get them wrong. A bare
final expression — `groebner(F, x, y)` — has its value discarded by `exec`,
and no display hook fires, so the value would be invisible; the trailing
expression is therefore split off and evaluated separately. And that value is
bound to `_`, as an interactive interpreter does, which is what lets Hardy
answer an over-large result with a summary and still leave the model a way to
reach the whole thing.

Capture is taken at the *descriptor* level, not by rebinding `sys.stdout`.
A cell is untrusted code that may call `os.system`, spawn a subprocess, or
reach a native library, and none of those go through `sys.stdout`: they write
to file descriptor 1, which is the descriptor carrying this protocol. Those
bytes used to land in front of a length-prefixed reply, where the parent read
a non-numeric header, called the kernel desynchronised, and discarded a
session's whole state over a helper's chatter. So the protocol keeps a private
duplicate of the original descriptor and fds 1 and 2 are replaced by pipes
this process drains itself.

Draining is what makes the cap real as well. Accumulating a cell's output in
a `StringIO` and clipping afterwards bounds what is *reported* and not what is
*held*: a cell printing in a loop grew the buffer without limit, so an
advertised 256 KiB cap could still cost gigabytes of resident memory. The
drain threads retain at most the cap and read past it, which is the same
shape the parent's own pipe readers have always had.

This module imports nothing from Hardy but `rendering`, which describes the
values a cell leaves behind and imports nothing from Hardy either. It is
executed as a child process and must keep working when the rest of the package
is not importable.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from typing import BinaryIO

from hardy.algebra.rendering import bounded_repr, clip, clip_jointly, state_digest

HEADER_BYTES = 10
CELL_FILENAME = "<hardy-cell>"

# How long `_Capture.settle` waits for its own end marker to come back around
# the pipe. It is written by this process into a pipe this process drains, so
# it arrives immediately in every ordinary case; the wait exists for the one
# where a child the cell spawned still holds the write end and has queued
# output ahead of it.
SETTLE_SECONDS = 5.0
# How much one drain of a captured descriptor reads at a time.
READ_BYTES = 65_536


# Set when an interrupt arrives with no cell to abandon: between cells, or
# while the frame for the next one is still being read. It cannot simply be
# ignored. A press that lands while Hardy is writing a frame is signalled the
# moment the frame is out, and the child may not have picked the frame up yet
# -- swallowing the signal there would leave the cell to run on with the only
# press already spent, until Hardy gave up waiting and killed the kernel,
# which is the loss interrupting exists to avoid. Remembered instead, and
# answered by the cell it arrived alongside.
#
# It is a fallback, not the record: a frame that carries `stopping` is Hardy's
# own word on whether a stop is in force, and it decides on its own (see
# `main`). The flag settles the frames it is on; this remembers a signal only
# for a frame that says nothing.
PENDING_INTERRUPT = False


def _interrupted_reply() -> dict:
    """What a cell Hardy stopped before it ran reports."""
    return {
        "status": "interrupted",
        "stdout": "",
        "stderr": "interrupted before the cell ran",
        "value_repr": "",
        "capture_truncated": False,
    }


# The signals a stop arrives as: `SIGINT` on POSIX, and `SIGBREAK` on Windows,
# which is what `CTRL_BREAK_EVENT` raises in the target.
_STOP_SIGNALS = tuple(
    found
    for found in (getattr(signal, name, None) for name in ("SIGINT", "SIGBREAK"))
    if found is not None
)


def _remember(_signum, _frame) -> None:
    """Record a stop instead of raising it. Installed while reading a frame."""
    global PENDING_INTERRUPT
    PENDING_INTERRUPT = True


def _handle_stops_by(handler) -> None:
    for number in _STOP_SIGNALS:
        with contextlib.suppress(OSError, ValueError):
            signal.signal(number, handler)


# The console event `signal_interrupt` sends on Windows. `CTRL_C_EVENT` cannot
# be aimed at a process group, so a break is what a stop arrives as.
_CTRL_BREAK_EVENT = 1
# Held for the life of the process: the console calls this from a thread of
# its own, and a callback that has been collected is a call into freed memory.
_CONSOLE_HANDLER = None


def redirect_console_breaks() -> None:
    """Make a console break wake a cell the way Ctrl+C would. Windows only.

    The C runtime turns `CTRL_BREAK_EVENT` into `SIGBREAK`, and a Python
    handler for that runs at the next bytecode boundary and not before. A
    `SIGINT` is different: Python's own console handler also sets the event
    that `time.sleep`, lock acquisition and every other blocking wait on
    Windows are waiting on, so Ctrl+C wakes them and a break does not. A
    cell asleep in `time.sleep(300)` therefore took the interrupt 300
    seconds later, long after Hardy had given up on the kernel and dropped
    the namespace the interrupt exists to keep.

    A console control handler runs on its own thread the instant the event
    arrives. It raises `SIGINT` from there -- through Python's C-level
    handler, which sets that event -- and answers True so the runtime's own
    handler does not deliver `SIGBREAK` behind it: one press is one
    `KeyboardInterrupt`, and the `_STOP_SIGNALS` dance above sees it as it
    always has, deferred between cells and raised inside one.
    """
    global _CONSOLE_HANDLER
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes
        from ctypes import wintypes

        prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def handler(event: int) -> bool:
            if event != _CTRL_BREAK_EVENT:
                return False
            signal.raise_signal(signal.SIGINT)
            return True

        callback = prototype(handler)
        set_handler = ctypes.windll.kernel32.SetConsoleCtrlHandler
        set_handler.argtypes = (prototype, wintypes.BOOL)
        set_handler.restype = wintypes.BOOL
        if set_handler(callback, True):
            _CONSOLE_HANDLER = callback


def read_exact(stream: BinaryIO, count: int) -> bytes | None:
    """Read exactly `count` bytes, or None if the stream ended first.

    Reading is done with the stop signal *deferred* rather than raised. There
    is no placement of a `try` that would be safe otherwise: the exception can
    land between `read` returning and the bytes being counted, and those bytes
    are then lost with it -- half a frame, and a protocol that never
    resynchronises. Deferring means no statement in here can be interrupted at
    all, and the stop is answered by the cell whose frame this is reading.
    """
    chunks: list[bytes] = []
    while count > 0:
        chunk = stream.read(count)
        if not chunk:
            return None
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


class _Stream:
    """One captured descriptor, fenced at both ends by markers in the pipe.

    Retention stops at the cap; reading does not. A cell whose helper never
    stops writing must not be able to block on a full pipe, and a marker
    written after the flood must still be found, so the scan runs over a
    rolling window rather than over what was kept.

    Two markers, not one, and for the same reason the sentinel backends use
    two. Arming a capture only says "start keeping things now" -- it says
    nothing about what is already sitting in the OS pipe unread, so a helper
    from the previous cell that wrote a moment before the next cell began had
    its bytes retained as that cell's output, with nothing marked incomplete.
    A begin marker written into the pipe at arm time is ordered behind exactly
    those bytes, so finding it is proof that everything in front of it belongs
    to somebody else.

    What no marker can fix is a helper still writing *while* the next cell
    runs: those bytes land inside the fence and are indistinguishable from the
    cell's own. That needs isolation, not framing.
    """

    def __init__(self, read_fd: int, limit: int) -> None:
        self.read_fd = read_fd
        self.limit = limit
        self.kept = bytearray()
        self.pending = bytearray()
        self.truncated = False
        self.begin = b""
        self.marker = b""
        self.seen_begin = False
        self.done = False
        # Whether anything was discarded that belongs to no cell of this
        # capture: written between cells, ahead of the begin marker, or behind
        # the end marker. It is what `capture_truncated` reports.
        self.stray = False
        # The descriptor is gone: a cell closed it, or the pipe broke. Nothing
        # more will ever arrive, so a wait for this cell's marker would be a
        # wait for something that cannot come.
        self.closed = False

    def arm(self, begin: bytes, marker: bytes) -> None:
        """Start a cell's capture, keeping nothing until `begin` comes round."""
        self.kept.clear()
        self.pending.clear()
        self.truncated = False
        self.begin = begin
        self.marker = marker
        self.seen_begin = False
        self.done = False
        # `stray` is deliberately *not* cleared here. Bytes that arrived and
        # were drained between cells set it before this cell existed, and this
        # cell is the first one in a position to report them; `settle` clears
        # it once it has. The begin marker covers the other half -- bytes
        # written before the arm but still sitting unread in the pipe.

    def feed(self, chunk: bytes) -> None:
        if self.done or not self.marker:
            # Between cells, or after this cell's end marker was found. A pipe
            # orders the writes already made; it says nothing about a child
            # the cell spawned that prints after the cell has returned. Those
            # bytes belong to a cell whose record is already written, so they
            # are dropped rather than pinned on whoever runs next -- and the
            # dropping is *recorded*, because output Hardy discarded is
            # exactly what `capture_truncated` exists to admit to.
            self.stray = True
            return
        self.pending.extend(chunk)
        if not self.seen_begin and not self._open_the_fence():
            return
        at = self.pending.find(self.marker)
        if at != -1:
            self._retain(self.pending[:at])
            # One `os.read` can carry the marker *and* what a helper wrote
            # straight after it. Clearing the buffer dropped that tail with
            # nothing recorded, so whether the next cell was told anything had
            # been discarded came down to how the pipe happened to chunk.
            # It is the same discard as the between-cells one and is admitted
            # to the same way.
            if len(self.pending) > at + len(self.marker):
                self.stray = True
            self.pending.clear()
            self.done = True
            return
        # A marker can straddle two reads, so everything but a marker's width
        # short of the end is safe to retire from the scan window now.
        keep = len(self.marker) - 1
        if len(self.pending) > keep:
            self._retain(self.pending[: len(self.pending) - keep])
            del self.pending[: len(self.pending) - keep]

    def _open_the_fence(self) -> bool:
        """Discard everything ahead of the begin marker. True once it is past.

        Anything in front of it was written before this cell was armed and is
        somebody else's, so it is dropped and admitted to rather than kept.
        """
        at = self.pending.find(self.begin)
        if at != -1:
            if at > 0:
                self.stray = True
            del self.pending[: at + len(self.begin)]
            self.seen_begin = True
            return True
        # The begin marker never arrived -- its write failed, or the descriptor
        # was replaced by a cell -- but the end marker did. Waiting for a fence
        # post that is not coming would cost this cell the whole settle grace
        # and then report an empty capture, so the cell is taken unfenced and
        # said to be incomplete.
        if self.marker in self.pending:
            self.stray = True
            self.seen_begin = True
            return True
        keep = max(len(self.begin), len(self.marker)) - 1
        if len(self.pending) > keep:
            self.stray = True
            del self.pending[: len(self.pending) - keep]
        return False

    def _retain(self, data: bytes) -> None:
        room = max(0, self.limit - len(self.kept))
        self.kept.extend(data[:room])
        if len(data) > room:
            self.truncated = True

    @property
    def lossy(self) -> bool:
        """Whether anything this capture should report was not kept."""
        return self.truncated or self.stray

    def disarm(self) -> None:
        """Stop keeping anything, once this cell's capture has been reported."""
        self.marker = b""
        # Cleared only now that it has been reported. Anything arriving from
        # here on belongs to the next cell's account of what it could not keep.
        self.stray = False

    def take_what_arrived(self) -> None:
        """Keep the unterminated remainder, and say the capture is not whole."""
        if self.pending:
            self._retain(bytes(self.pending))
            self.pending.clear()
        self.stray = True

    def text(self) -> tuple[str, bool]:
        """The captured text, and whether it is exactly what was written.

        `backslashreplace`, not `replace`. A helper or a native library is
        free to emit bytes that are not UTF-8, and `replace` collapses every
        one of them to the same U+FFFD -- so a cell writing `b"\xff"` and a
        replay writing `b"\xfe"` compared equal and the export reported
        verification of output that had in fact changed.

        The escape keeps those two apart, and it still is not a faithful
        encoding: `b"\xff"` and the four ASCII bytes `b"\\xff"` both render
        as `\xff`, so it is one-way rather than reversible. A record is a JSON
        string and cannot hold arbitrary bytes at all, so the honest report is
        not a cleverer escape but the admission that this capture is not
        exactly comparable -- which is what the second element says, and what
        `capture_truncated` carries to the parent.
        """
        raw = bytes(self.kept)
        try:
            return raw.decode("utf-8"), True
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="backslashreplace"), False


# Win32 keeps the handles a new process inherits in its own table, and
# `os.dup2` moves only the C-runtime descriptor. A cell that spawns a child
# without naming its streams -- `os.system`, or `subprocess` with no `stdout`
# -- would therefore hand that child the *original* handle on native Windows,
# and its output would land on the protocol descriptor after all: the exact
# failure descriptor capture exists to prevent, still reachable on one
# platform. `SetStdHandle` is the other half of the redirect there.
_WIN32_STD = {0: -10, 1: -11, 2: -12}


def _redirect_win32_handle(fd: int) -> None:
    """Point Win32's own standard handle at whatever `fd` now refers to.

    A no-op everywhere but native Windows, and best effort even there: a
    failure leaves the capture exactly as good as it was before, which is the
    POSIX behaviour and no worse.

    Unverified on a real Windows machine -- Hardy has no Windows CI, and
    Macaulay2 has no Windows build to run one against. It is the documented
    call for this, guarded so that being wrong costs nothing.
    """
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        # Declared rather than inferred. An undeclared `ctypes` call marshals
        # a Python int as a C `int`, and a HANDLE on 64-bit Windows is
        # pointer-sized -- so the redirect could be handed a truncated handle,
        # fail, and leave a cell's helpers writing onto the protocol
        # descriptor with nothing having noticed.
        set_std_handle = ctypes.windll.kernel32.SetStdHandle
        set_std_handle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
        set_std_handle.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        if not set_std_handle(wintypes.DWORD(_WIN32_STD[fd]), handle):
            raise OSError(ctypes.get_last_error(), "SetStdHandle refused the redirect")


def _protocol_input():
    """Hardy's frames, on a descriptor that no child of a cell inherits.

    They arrive on descriptor 0, and a helper a cell starts without naming
    its stdin inherits that -- so it could read the next frame as its own
    input, and the protocol would never resynchronise. On Windows it is worse
    before it is anything: a child that inherits the pipe cannot start while
    this process is blocked reading it. A pending synchronous read holds the
    pipe's file object, and the child's C runtime, which asks that same
    object what kind of file it is while setting up its own stdin, waits for
    the read to return. A helper that printed "after its cell" therefore did
    not start until the *next* cell's frame arrived, and printed into that
    cell -- and a session that spawned a helper and then sat idle left it
    frozen for as long as the idleness lasted. Descriptor 0 goes to the null
    device, and the frames are read from a private duplicate, exactly as
    `_Capture` keeps the protocol's replies off descriptor 1.
    """
    protocol = os.dup(0)
    with contextlib.suppress(OSError, AttributeError):
        os.set_inheritable(protocol, False)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    _redirect_win32_handle(0)
    return os.fdopen(protocol, "rb")


class _Capture:
    """fds 1 and 2, replaced by pipes this process drains for itself.

    The original descriptor 1 is duplicated first and handed back for the
    protocol, so a reply is written somewhere no cell can reach: `os.write(1,
    ...)`, a subprocess, and a native library all land in the capture pipe
    with everything else a cell printed.
    """

    def __init__(self, limit: int) -> None:
        self.protocol_fd = os.dup(1)
        with contextlib.suppress(OSError, AttributeError):
            os.set_inheritable(self.protocol_fd, False)
        # A fatal fault in the driver itself -- not a cell, the driver -- used
        # to be undiagnosable: its traceback went to `sys.stderr`, backed by
        # descriptor 2, which the loop below is about to redirect into this
        # process's own private capture pipe. Written there, between cells,
        # `_Stream.feed` discards it (nothing is armed to keep it) -- so the
        # parent's `kernel_died` record carried an empty `stderr` and the
        # death could not be explained (issue #311). Saved before the
        # redirect, the same way `protocol_fd` is, so `main`'s last-resort
        # handler has somewhere real to put a traceback: the parent's own
        # stderr pipe, which is what `kernel.stderr_text()` reads. `os.dup` is
        # already non-inheritable from 3.4 on; `set_inheritable` is belt and
        # suspenders, as it is for `protocol_fd` above.
        self.saved_stderr = os.dup(2)
        with contextlib.suppress(OSError, AttributeError):
            os.set_inheritable(self.saved_stderr, False)
        self._changed = threading.Condition()
        self.streams: dict[int, _Stream] = {}
        for fd in (1, 2):
            with contextlib.suppress(Exception):
                (sys.stdout if fd == 1 else sys.stderr).flush()
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, fd)
            os.close(write_fd)
            _redirect_win32_handle(fd)
            self.streams[fd] = _Stream(read_fd, limit)
            threading.Thread(target=self._drain, args=(fd,), daemon=True).start()

    def _drain(self, fd: int) -> None:
        stream = self.streams[fd]
        while True:
            try:
                chunk = os.read(stream.read_fd, READ_BYTES)
            except OSError:
                chunk = b""
            if not chunk:
                with self._changed:
                    stream.closed = True
                    self._changed.notify_all()
                return
            with self._changed:
                stream.feed(chunk)
                self._changed.notify_all()

    @staticmethod
    def _write_marker(marker: bytes) -> None:
        """Flush Python output before fencing both OS descriptors."""
        for fd in (1, 2):
            with contextlib.suppress(Exception):
                (sys.stdout if fd == 1 else sys.stderr).flush()
            with contextlib.suppress(OSError):
                os.write(fd, marker)

    def begin(self) -> bytes:
        """Arm both streams for one cell and fence the start of its output.

        The begin marker goes out through the descriptors, like the end one,
        so pipe order does the work: anything a previous cell's helper had
        already written is necessarily in front of it and is dropped rather
        than kept as this cell's.
        """
        nonce = uuid.uuid4().hex
        begin = f"\0«hardy-cell-begin:{nonce}»\0".encode()
        marker = f"\0«hardy-capture:{nonce}»\0".encode()
        with self._changed:
            for stream in self.streams.values():
                stream.arm(begin, marker)
        self._write_marker(begin)
        return marker

    def settle(self, marker: bytes) -> tuple[str, str, bool]:
        """Close the cell's capture and return what both descriptors carried.

        The marker goes out through the descriptors themselves rather than
        being handed to the drain threads directly. A pipe preserves write
        order, so anything the cell wrote -- through `sys.stdout`, through fd
        1, or from a child that inherited it -- is necessarily in front of the
        marker, and finding the marker is proof that all of it has arrived.
        """
        self._write_marker(marker)
        with self._changed:
            self._await_end_markers()
            out, err = self.streams[1], self.streams[2]
            for stream in (out, err):
                # A descriptor a cell closed or replaced ends the wait without
                # the end marker ever arriving, and whatever was already in
                # the scan window would have gone unretained and unreported:
                # `os.write(1, b"VISIBLE"); os.close(1)` recorded empty stdout
                # and called the capture exact. What is there is kept, and the
                # capture says it is not a whole one. `closed` is never reset,
                # so every later cell says the same -- the stream does not
                # come back.
                if not stream.done:
                    stream.take_what_arrived()
            out_text, out_exact = out.text()
            err_text, err_exact = err.text()
            whole = out_exact and err_exact and not (out.lossy or err.lossy)
            for stream in self.streams.values():
                stream.disarm()
            return out_text, err_text, not whole

    def _await_end_markers(self) -> None:
        """Wait, holding the condition, until every stream has seen its end
        marker or closed, or until the settle grace runs out."""
        deadline = time.monotonic() + SETTLE_SECONDS
        while not all(stream.done or stream.closed for stream in self.streams.values()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._changed.wait(remaining)

    def write_reply(self, payload: bytes) -> None:
        header = f"{len(payload):0{HEADER_BYTES}d}".encode("ascii")
        data = header + payload
        while data:
            written = os.write(self.protocol_fd, data)
            data = data[written:]


def run_cell(source: str, namespace: dict, limit: int, capture: _Capture) -> dict:
    """Execute one cell against the persistent namespace and describe what happened."""
    global PENDING_INTERRUPT
    try:
        parsed = ast.parse(source, filename=CELL_FILENAME)
    # Not `except SyntaxError`. `ast.parse` raises far more than that against a
    # cell Hardy did not write: a source deep enough that converting its AST
    # exhausts the recursion limit raises `RecursionError`, not `SyntaxError`
    # (issue #311), and only the latter was ever caught here -- so a flat sum
    # of a few thousand terms, well under the cell's byte cap, killed the
    # kernel and every value the session held. A cell is untrusted input the
    # same way its execution below already treats it: every failure to parse
    # it is this cell's error, never the driver's. Parsing runs no code, so
    # what it can raise is an `Exception`; the one other thing that can land
    # here is a stop, which Hardy asked for and which goes up to be answered
    # with every other interrupt this function can raise into.
    except Exception:
        # Clipped like any other reply: a syntax error quotes the offending
        # source back, and the source is allowed to be 64 KiB.
        return _failed_reply(limit)

    trailing = _split_trailing(parsed)
    marker = capture.begin()
    status, value_repr, oversized, failure = "ok", "", False, ""
    try:
        value_repr, oversized = _evaluate(parsed, trailing, namespace, limit)
    # Hardy asked for this one, so it is reported under its own name rather
    # than as the cell having gone wrong -- and, far more importantly, it is
    # *answered*. Catching it here is what makes an interrupt cost one cell
    # instead of the whole session: the kernel stays up, the namespace keeps
    # everything the earlier cells put in it, and the parent gets a reply
    # rather than waiting out a deadline on a child that will never speak.
    except KeyboardInterrupt:
        status = "interrupted"
        failure = _describe_failure()
    # A cell is untrusted input, and `exit()` raises SystemExit: the kernel
    # has to outlive whatever a cell does, or one stray call ends a session
    # and every value in it.
    except BaseException:
        status = "error"
        failure = _describe_failure()
    # The cell has finished. Stops while collecting its output must preserve
    # what it did, rather than escape as an unrun-cell reply.
    _handle_stops_by(_remember)
    captured_out, captured_err, overran = capture.settle(marker)
    if PENDING_INTERRUPT:
        PENDING_INTERRUPT = False
        status = "interrupted"
        captured_err += "[stop arrived after the cell finished, while its output was being collected]\n"
    # Appended rather than printed. `traceback.print_exc()` writes to
    # `sys.stderr`, which is backed by descriptor 2 -- and a cell is free to
    # close it: `import os; os.close(2); 1 / 0` made the print raise a second
    # exception from inside the handler, which escaped `run_cell` and killed
    # the driver with no reply frame at all. An ordinary failing cell became a
    # lost kernel and a forced rebuild. Formatting it and putting it behind
    # whatever the cell wrote keeps the same order in the record and depends
    # on no descriptor.
    if failure:
        captured_err += failure
    fields, truncated = clip_jointly(
        {"stdout": captured_out, "stderr": captured_err, "value_repr": value_repr},
        limit,
    )
    return {
        "status": status,
        **fields,
        "capture_truncated": truncated or overran or oversized,
    }


def _split_trailing(parsed: ast.Module) -> ast.Expression | None:
    """Take a trailing expression off the cell, to be evaluated rather than run.

    Its value survives that way; anything before it is ordinary statement
    execution.
    """
    if parsed.body and isinstance(parsed.body[-1], ast.Expr):
        return ast.Expression(parsed.body.pop().value)
    return None


def _evaluate(
    parsed: ast.Module, trailing: ast.Expression | None, namespace: dict, limit: int
) -> tuple[str, bool]:
    """Run a cell in the namespace: its value's bounded repr, and whether it was cut.

    Everything a cell raises goes to the caller, which answers it.
    """
    if parsed.body:
        exec(compile(parsed, CELL_FILENAME, "exec"), namespace)
    if trailing is None:
        return "", False
    value = eval(compile(trailing, CELL_FILENAME, "eval"), namespace)
    if value is None:
        return "", False
    namespace["_"] = value
    return bounded_repr(value, limit)


def _failed_reply(limit: int) -> dict:
    """The reply for a cell that failed before it could run: its traceback.

    Called inside the handler for that failure. `_describe_failure` rather
    than a bare `traceback.format_exc()`, so a failure to *format* the
    traceback is answered too rather than escaping in its place.
    """
    stderr, truncated = clip(_describe_failure(), limit)
    return {
        "status": "error",
        "stdout": "",
        "stderr": stderr,
        "value_repr": "",
        "capture_truncated": truncated,
    }


def _describe_failure() -> str:
    """The traceback for the exception being handled, or a note that it is
    unavailable. Never raises: this runs inside an exception handler, and an
    exception escaping it costs the whole kernel."""
    try:
        return traceback.format_exc()
    except BaseException:
        return "a traceback for this cell could not be formatted\n"


def _refuse_to_quit(*_args, **_kwargs):
    raise RuntimeError(
        "exit() and quit() would end the computer algebra session and discard "
        "every value in it. Ask Hardy to reset the session instead."
    )


def main() -> None:
    """Serve Hardy's cells until the frame stream ends or stops being frames.

    `sys.argv[1]` is the capture cap in bytes. Exits 0 when the stream ends or
    desynchronises, and 1 only on a fault in the driver's own plumbing.
    """
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    namespace: dict = {"__name__": "__hardy_cas__", "__builtins__": __builtins__}
    # `exit` is site.Quitter, which closes stdin before raising SystemExit. No
    # handler can undo that: the kernel is deaf from then on and the session's
    # state is gone. Shadowed, because these only mean anything in a REPL that
    # owns its terminal, and this one is spoken to over a pipe.
    namespace["exit"] = namespace["quit"] = _refuse_to_quit
    # Preloaded so a cell can say `groebner(...)` directly, and so the exported
    # script — which emits this same import — reproduces the session.
    with contextlib.suppress(BaseException):
        exec("from sympy import *", namespace)
    # Snapshotted before any cell runs, so `state_digest` can tell what the
    # session put in the namespace from what the preamble did.
    baseline = dict(namespace)

    # A cell's `print` goes through `sys.stdout`, and on Windows that wrapper
    # encodes with the console codepage and writes "\r\n" for every "\n". The
    # capture below reads the descriptor as bytes and the parent decodes them
    # as UTF-8, so the wrapper is told to write what will be read: without
    # this a record carried "\r\n" line endings and U+FFFD for every
    # non-ASCII character a cell printed. Before `_Capture` takes the
    # descriptors, because reconfiguring flushes.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors=stream.errors, newline="\n")

    capture = _Capture(limit)

    stdin = _protocol_input()
    redirect_console_breaks()
    try:
        _cell_loop(stdin, namespace, baseline, limit, capture)
    # The very last resort. Everything a cell's own source can do is answered
    # inside `_cell_loop` -- `run_cell` catches every `BaseException` from
    # parsing or running it, and the frame loop below catches its own protocol
    # errors deliberately. What lands here is a fault in the driver's own
    # plumbing: `read_exact` itself failing is how the tests reach this, and a
    # real one would be a bug in this file. There is no cell to answer and no
    # namespace worth preserving, only a death to explain.
    except BaseException:
        with contextlib.suppress(OSError):
            os.write(
                capture.saved_stderr, _describe_failure().encode("utf-8", "backslashreplace")
            )
        # Writing the traceback and returning left this exit 0: a supervisor
        # watching the exit code, not just the pipe, saw a clean shutdown and
        # never learned the kernel died mid-session. `raise` would let a
        # `finally` elsewhere swallow it; `SystemExit` here cannot be mistaken
        # for a cell's own and is the last thing this function does.
        raise SystemExit(1) from None


def _cell_loop(stdin, namespace: dict, baseline: dict, limit: int, capture: _Capture) -> None:
    """Read frames and answer them, for as long as the parent keeps sending."""
    while True:
        # Deferred across the read, raised across the cell. The cell is the one
        # place a stop *should* interrupt Python directly -- that is what makes
        # it abandon the computation and leave the namespace standing -- and
        # everywhere else it is a flag.
        _handle_stops_by(_remember)
        request = _read_request(stdin)
        if request is None:
            return
        reply = _answer(request, namespace, limit, capture)
        # Taken after every cell, failed ones included: a cell that raised
        # partway through has still changed the namespace, and the parent
        # needs to know whether it did before it decides that replaying only
        # the accepted cells can rebuild this state.
        reply["state_digest"] = _digest_or_nothing(namespace, baseline, limit)
        capture.write_reply(_serialised(reply))


def _read_request(stdin: BinaryIO) -> dict | None:
    """The next frame's request, or None when the stream ended or desynchronised.

    Read and parsed with the stop still deferred, so nothing here can be
    interrupted into killing the kernel.
    """
    header = read_exact(stdin, HEADER_BYTES)
    if header is None:
        return None
    try:
        length = int(header.decode("ascii"))
    except ValueError:
        return None
    payload = read_exact(stdin, length)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _answer(request: dict, namespace: dict, limit: int, capture: _Capture) -> dict:
    """Answer one request: run its cell, or answer it unrun if a stop is in force."""
    global PENDING_INTERRUPT
    # The raising handler goes up *inside* the `try` and comes down in the
    # `finally`, because each switch is itself a window: a signal landing
    # after the handler is installed but before the block is entered, or
    # after the block ends but before deferral is restored, would escape
    # `main` and kill the kernel over a press meant to cost one cell. Hardy
    # signals the instant it has written a frame, so those are exactly the
    # moments an early press arrives.
    #
    # The deferred flag is read *after* the switch, never before. Read
    # first, a signal arriving in between would set it again with nobody
    # left to look, and the cell would run on with the press already spent
    # -- until Hardy gave up waiting and killed the kernel.
    try:
        _handle_stops_by(signal.default_int_handler)
        held = PENDING_INTERRUPT
        PENDING_INTERRUPT = False
        # `stopping` is Hardy's own word that a press is in force as this
        # cell goes out, written into the frame under the same lock that
        # orders presses against sends -- and it decides on its own. It
        # used to be honoured only together with a remembered signal, and
        # the signal is the half that can be late or lost: a shared runner
        # dropped it two runs in three, the cell ran to its deadline, and
        # the kernel was taken with it -- the exact loss a remembered press
        # exists to prevent. Hardy sends no signal for a frame that says
        # `stopping`, so nothing can land in the handler switch below.
        #
        # `held` without `stopping` is a signal that arrived in the moment
        # after the last reply was flushed and before Hardy had read it. It
        # was aimed at a cell that was already over, and Hardy -- which
        # knows whether it still wants a stop -- says it does not.
        # Rejecting this cell for it would stop something nobody asked to
        # stop. A frame with no flag at all is an older parent's, and
        # there the remembered signal is all there is to go on.
        if request.get("stopping", held):
            # The stop reached this kernel before the cell did. Answering
            # without running it is what the parent is waiting for: it gets
            # a framed reply straight away, the namespace is untouched, and
            # the kernel is still here for the next cell.
            return _interrupted_reply()
        return run_cell(str(request.get("source", "")), namespace, limit, capture)
    except KeyboardInterrupt:
        return _interrupted_reply()
    # `run_cell` answers everything a cell's own source can raise. What
    # can still land here is `SystemExit` from somewhere that is not a
    # cell's `exec`/`eval` -- there is none today -- or a bug in this
    # loop's own bookkeeping above. Either way it is reported as this
    # cell's error rather than allowed to end the session over it: no
    # path from a cell's source may cost the loop itself (issue #311).
    except BaseException:
        return _failed_reply(limit)
    finally:
        _handle_stops_by(_remember)


def _digest_or_nothing(namespace: dict, baseline: dict, limit: int) -> str:
    try:
        return state_digest(namespace, baseline, limit)
    except BaseException:
        # A namespace Hardy cannot fingerprint is one it must not claim to
        # have fingerprinted, and it is emphatically not a reason to end a
        # session: an empty digest is the value that means "not compared",
        # which is what a backend with no digest at all reports.
        return ""


def _serialised(reply: dict) -> bytes:
    try:
        return json.dumps(reply, ensure_ascii=False).encode("utf-8")
    except BaseException:
        # `clip`/`clip_jointly` already keep every field in `reply` free of
        # anything strict UTF-8 cannot carry, so this is a defence against
        # what they do not reach -- a cell that replaced `json` itself, for
        # one. Not `json.dumps` again: that is exactly what a cell may have
        # broken. A bare bytes literal, valid JSON and already ASCII, is
        # answered instead, so the kernel survives whatever this was.
        return _SERIALISATION_FAILED_REPLY


# A framed reply that does not depend on `json` working, because the failure
# it answers may be that a cell has monkeypatched it. Valid JSON, plain ASCII,
# and every field `CellOutcome` requires.
_SERIALISATION_FAILED_REPLY = (
    b'{"status": "error", "stdout": "", '
    b'"stderr": "Hardy could not serialise this cell\'s reply", '
    b'"value_repr": "", "capture_truncated": true, "state_digest": ""}'
)


if __name__ == "__main__":
    main()
