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

This module imports nothing from Hardy. It is executed as a child process and
must keep working when the rest of the package is not importable.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import itertools
import json
import os
import re
import signal
import sys
import threading
import time
import traceback
import uuid

HEADER_BYTES = 10
CELL_FILENAME = "<hardy-cell>"

# How long `_Capture.settle` waits for its own end marker to come back around
# the pipe. It is written by this process into a pipe this process drains, so
# it arrives immediately in every ordinary case; the wait exists for the one
# where a child the cell spawned still holds the write end and has queued
# output ahead of it.
SETTLE_SECONDS = 5.0


# Set when an interrupt arrives with no cell to abandon: between cells, or
# while the frame for the next one is still being read. It cannot simply be
# ignored. Hardy signals the moment it has written a frame, and the child may
# not have picked it up yet -- swallowing the signal there would leave the cell
# to run on with the only press already spent, until Hardy gave up waiting and
# killed the kernel, which is the loss interrupting exists to avoid. Remembered
# instead, and answered by the cell it arrived alongside.
#
# It cannot poison an unrelated later cell either: Hardy only ever signals a
# kernel it has just given work to.
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


def read_exact(stream, count: int) -> bytes | None:
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


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Truncate here, at the source, rather than in the parent's pipe reader.

    A reply is length-prefixed, so a parent that stopped reading at a byte cap
    could never assemble the frame it was promised — it would wait out the cell
    timeout and lose the kernel over an answer that was merely large. Clipping
    before serialising keeps every envelope parseable.

    `limit` is a count of bytes, because that is what the parent reserves
    retention for and what `cas_output_bytes` promises. Counting characters
    instead let a cell of astral-plane text carry four times the budget past a
    reader sized for one. The cut lands on a UTF-8 boundary: a truncated
    trailing character is dropped rather than emitted as a replacement.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def clip_jointly(fields: dict[str, str], limit: int) -> tuple[dict[str, str], bool]:
    """Fit stdout, stderr and value_repr into one shared byte budget.

    One budget rather than three, because the parent reserves retention for one
    and the frame carries all three. A per-field cap of `limit` each meant a
    cell writing that much to every field built a payload three times the size
    the reader was sized for — a legal cell whose frame could never assemble,
    which reads as a timeout and costs the whole session's state.

    The share is max-min fair: smallest field first, each allowed an equal cut
    of what is left, and whatever a small field does not use passes to the
    others. So a lone large value still gets nearly the whole budget, and three
    large ones each get a third instead of one starving the rest.
    """
    truncated = False
    clipped: dict[str, str] = {}
    remaining = limit
    ordered = sorted(fields.items(), key=lambda item: len(item[1].encode("utf-8")))
    for index, (name, text) in enumerate(ordered):
        share = remaining // (len(ordered) - index)
        clipped[name], cut = clip(text, share)
        truncated = truncated or cut
        remaining -= len(clipped[name].encode("utf-8"))
    return clipped, truncated


# Containers whose length is known without walking them, and whose repr is at
# least one byte per element -- so their size can be bounded from below without
# rendering them, which is what lets an over-large one be diverted before its
# repr is built.
_SIZED = (list, tuple, set, frozenset, dict)
# How deep the size estimate walks before giving up and calling a value small.
# A bound, not a guess about shape: the estimate must not itself be the
# unbounded traversal it exists to prevent.
_ESTIMATE_DEPTH = 6
_BRACKETS = {
    list: ("[", "]"),
    tuple: ("(", ")"),
    set: ("{", "}"),
    frozenset: ("frozenset({", "})"),
    dict: ("{", "}"),
}


def _lower_bound(value: object, limit: int, depth: int = 0) -> int:
    """A cheap lower bound on `len(repr(value))`, capped at `limit + 1`.

    Cheap because it never renders anything and stops the moment the budget is
    already exceeded. A lower bound because anything it cannot measure counts
    as zero: the point is to *prove* a value too large before building its
    repr, never to guess that one is small enough to matter.

    An item count alone was not enough. A one-element list holding a
    multi-gigabyte string has one item and a multi-gigabyte repr, so deciding
    by `len(value)` sent it down the plain `repr` path and built the whole
    thing -- with `MemoryError` caught only after the allocation had already
    been attempted, by which point the OS may have taken the kernel instead.
    """
    if isinstance(value, (str, bytes, bytearray)):
        return min(len(value), limit + 1)
    if depth >= _ESTIMATE_DEPTH or type(value) not in _SIZED:  # noqa: E721 -- exact type
        return 0
    # The separators alone, which is why a container of more than `limit`
    # elements is over budget before a single one of them is looked at.
    total = len(value)
    if total > limit:
        return limit + 1
    for key, entry in _members(value):
        total += _lower_bound(key, limit, depth + 1)
        if entry is not None:
            total += _lower_bound(entry, limit, depth + 1)
        if total > limit:
            return limit + 1
    return total


def _members(value):
    """A container's contents as (item, None) pairs, or a dict's as (key, value)."""
    if isinstance(value, dict):
        return value.items()
    return ((item, None) for item in value)


def bounded_repr(value: object, limit: int) -> tuple[str, bool]:
    """`repr(value)`, without building a gigantic one to then throw away.

    Exact whenever the answer would have fitted: the fast path is plain
    `repr`, so an ordinary value's repr is byte-identical to what an
    interpreter would print. Only a value `_lower_bound` can *prove* too large
    is rendered head-first instead, and it says so in the text.

    What this does not bound is a `__repr__` a cell wrote itself. There is no
    way to ask an object how long its repr will be without running it, and the
    honest answer is that the process, not this function, is the bound there;
    `MemoryError` is caught and reported as the cell failing rather than
    allowed to take the kernel down.

    The second element of the pair is whether the text is a truncation. It is
    not only for the reader: `state_digest` refuses to fingerprint a namespace
    any part of which it could only see a prefix of, because two values sharing
    a prefix would otherwise fingerprint alike.
    """
    try:
        if _lower_bound(value, limit) <= limit:
            return repr(value), False
        if isinstance(value, (str, bytes, bytearray)):
            head, _ = clip(repr(value[:limit]), limit)
            return f"{head} … <{type(value).__name__} of length {len(value)}>", True
        if type(value) in _SIZED:  # noqa: E721 -- exact type only
            return _head_of_container(value, limit), True
        return repr(value), False
    except MemoryError:
        return f"<{type(value).__name__}: its repr did not fit in memory>", True
    except BaseException:  # a cell's own __repr__ is allowed to be broken
        return f"<{type(value).__name__}: repr() raised {sys.exc_info()[0].__name__}>", True


def _head_of_container(value, limit: int) -> str:
    """Render the leading elements of an over-long container and say so."""
    opening, closing = _BRACKETS[type(value)]
    items = value.items() if isinstance(value, dict) else value
    pieces: list[str] = []
    size = 0
    for item in itertools.islice(items, limit):
        if isinstance(value, dict):
            key, entry = item
            piece = f"{bounded_repr(key, limit)[0]}: {bounded_repr(entry, limit)[0]}"
        else:
            piece = bounded_repr(item, limit)[0]
        piece, _ = clip(piece, limit)
        pieces.append(piece)
        size += len(piece) + 2
        if size >= limit:
            break
    body, _ = clip(", ".join(pieces), limit)
    return f"{opening}{body}, … <{len(value)} items>{closing}"


class _Stream:
    """One captured descriptor: bounded retention, and an end marker to find.

    Retention stops at the cap; reading does not. A cell whose helper never
    stops writing must not be able to block on a full pipe, and a marker
    written after the flood must still be found, so the scan runs over a
    rolling window rather than over what was kept.
    """

    def __init__(self, read_fd: int, limit: int) -> None:
        self.read_fd = read_fd
        self.limit = limit
        self.kept = bytearray()
        self.pending = bytearray()
        self.truncated = False
        self.marker = b""
        self.done = False
        # Whether anything arrived while no cell was listening. Carried across
        # `arm` on purpose: it is read by the cell that follows, which is the
        # first one in a position to report it.
        self.stray = False
        # The descriptor is gone: a cell closed it, or the pipe broke. Nothing
        # more will ever arrive, so a wait for this cell's marker would be a
        # wait for something that cannot come.
        self.closed = False

    def arm(self, marker: bytes) -> bool:
        """Start a cell's capture, reporting whether stray bytes preceded it."""
        stray = self.stray
        self.kept.clear()
        self.pending.clear()
        self.truncated = False
        self.marker = marker
        self.done = False
        self.stray = False
        return stray

    def feed(self, chunk: bytes) -> None:
        if self.done or not self.marker:
            # Between cells, or after this cell's marker was found. A pipe
            # orders the writes already made; it says nothing about a child
            # the cell spawned that prints after the cell has returned. Those
            # bytes belong to a cell whose record is already written, so they
            # are dropped rather than pinned on whoever runs next -- and the
            # dropping is *recorded*, because output Hardy discarded is
            # exactly what `capture_truncated` exists to admit to.
            self.stray = True
            return
        self.pending.extend(chunk)
        at = self.pending.find(self.marker)
        if at != -1:
            self._retain(self.pending[:at])
            self.pending.clear()
            self.done = True
            return
        # A marker can straddle two reads, so everything but a marker's width
        # short of the end is safe to retire from the scan window now.
        keep = len(self.marker) - 1
        if len(self.pending) > keep:
            self._retain(self.pending[: len(self.pending) - keep])
            del self.pending[: len(self.pending) - keep]

    def _retain(self, data: bytes) -> None:
        room = max(0, self.limit - len(self.kept))
        self.kept.extend(data[:room])
        if len(data) > room:
            self.truncated = True

    def text(self) -> str:
        return bytes(self.kept).decode("utf-8", errors="replace")


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
        self._changed = threading.Condition()
        self.streams: dict[int, _Stream] = {}
        for fd in (1, 2):
            with contextlib.suppress(Exception):
                (sys.stdout if fd == 1 else sys.stderr).flush()
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, fd)
            os.close(write_fd)
            self.streams[fd] = _Stream(read_fd, limit)
            threading.Thread(target=self._drain, args=(fd,), daemon=True).start()

    def _drain(self, fd: int) -> None:
        stream = self.streams[fd]
        while True:
            try:
                chunk = os.read(stream.read_fd, 65_536)
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

    def begin(self) -> tuple[bytes, bool]:
        """Arm both streams for one cell.

        Returns the marker that ends the cell, and whether anything was
        written while no cell was listening -- a helper the *previous* cell
        left running, whose bytes were discarded because the record they
        belonged to was already written.
        """
        marker = f"\0«hardy-capture:{uuid.uuid4().hex}»\0".encode()
        with self._changed:
            # Every stream armed, then the answers combined. `any` over a
            # generator would stop at the first True and leave the other
            # descriptor unarmed for the whole cell.
            stray = any([stream.arm(marker) for stream in self.streams.values()])
        return marker, stray

    def settle(self, marker: bytes) -> tuple[str, str, bool]:
        """Close the cell's capture and return what both descriptors carried.

        The marker goes out through the descriptors themselves rather than
        being handed to the drain threads directly. A pipe preserves write
        order, so anything the cell wrote -- through `sys.stdout`, through fd
        1, or from a child that inherited it -- is necessarily in front of the
        marker, and finding the marker is proof that all of it has arrived.
        """
        for fd in (1, 2):
            with contextlib.suppress(Exception):
                (sys.stdout if fd == 1 else sys.stderr).flush()
            with contextlib.suppress(OSError):
                os.write(fd, marker)
        with self._changed:
            deadline = time.monotonic() + SETTLE_SECONDS
            while not all(
                stream.done or stream.closed for stream in self.streams.values()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            out, err = self.streams[1], self.streams[2]
            captured = (out.text(), err.text(), out.truncated or err.truncated)
            for stream in self.streams.values():
                stream.marker = b""
            return captured

    def write_reply(self, payload: bytes) -> None:
        header = f"{len(payload):0{HEADER_BYTES}d}".encode("ascii")
        data = header + payload
        while data:
            written = os.write(self.protocol_fd, data)
            data = data[written:]


# A repr carries the address of anything that does not define one of its own,
# and an address is not state: a rebuilt namespace holding an equal object at a
# different address has reproduced, and calling that a divergence would poison
# every session that ever bound a lambda or a plain instance.
#
# Anchored to the shape CPython's default reprs actually use -- ` at 0xADDR`
# immediately before the closing `>` -- and not to bare `0x` digits anywhere.
# The loose form rewrote hexadecimal that was *content*, so `"0x1234"` and
# `"0xabcd"` fingerprinted identically and a rebuild holding the wrong one was
# called faithful. Normalising an address must not normalise a value.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+(?=>)")
# Names that are not the session's state: the interpreter's own, and the
# last-value binding, which the parent already compares as `value_repr`.
_NOT_STATE = frozenset({"_", "exit", "quit"})


def state_digest(namespace: dict, baseline: dict, limit: int) -> str:
    """A fingerprint of everything a cell has put in the namespace.

    Recovery replays the accepted cells and compares what they printed. That
    is not the same as comparing what they *did*: `import random; x =
    random.random()` prints nothing at all, so a replay that rebuilt a
    different `x` reproduced three empty fields and was called faithful, and
    every cell after it was standing on a value nobody had checked. The digest
    is what makes that comparable -- it is recorded per cell and compared on
    replay, so an unobservable change is observable after all.

    `baseline` is the namespace as the preamble left it. A name still bound to
    the very object the preamble bound is skipped, which keeps the digest to
    what the session did rather than to a thousand SymPy exports, and which is
    stable across processes for exactly the names that were never touched.
    Rebinding a preamble name brings it back in; mutating one of those objects
    in place does not, and that is the hole this leaves.
    """
    digest = hashlib.sha256()
    # `repr`, not the name itself: `globals()` accepts a non-string key, and
    # `sorted` over mixed types raises -- which used to lose the whole digest
    # for the namespace rather than describe it.
    for name in sorted(namespace, key=repr):
        if isinstance(name, str) and (name.startswith("__") or name in _NOT_STATE):
            continue
        value = namespace[name]
        if name in baseline and baseline[name] is value:
            continue
        rendered, truncated = bounded_repr(value, limit)
        if truncated:
            # A prefix is not a fingerprint. Two values agreeing for the first
            # `limit` bytes would hash alike, and a rebuild holding the wrong
            # one would be called faithful -- which is the exact failure the
            # digest exists to prevent, reintroduced by the bound that keeps
            # the digest affordable. No digest at all is the honest answer:
            # the parent reads an empty one as "not compared" and says so.
            return ""
        digest.update(repr(name).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(_ADDRESS.sub(" at 0x?", rendered).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def run_cell(source: str, namespace: dict, limit: int, capture: _Capture) -> dict:
    """Execute one cell against the persistent namespace and describe what happened."""
    try:
        parsed = ast.parse(source, filename=CELL_FILENAME)
    except SyntaxError:
        # Clipped like any other reply: a syntax error quotes the offending
        # source back, and the source is allowed to be 64 KiB.
        stderr, truncated = clip(traceback.format_exc(), limit)
        return {
            "status": "error",
            "stdout": "",
            "stderr": stderr,
            "value_repr": "",
            "capture_truncated": truncated,
        }

    # The trailing expression is evaluated rather than executed so its value
    # survives; anything before it is ordinary statement execution.
    trailing = None
    if parsed.body and isinstance(parsed.body[-1], ast.Expr):
        trailing = ast.Expression(parsed.body.pop().value)

    marker, stray = capture.begin()
    status, value_repr, oversized = "ok", "", False
    try:
        if parsed.body:
            exec(compile(parsed, CELL_FILENAME, "exec"), namespace)
        if trailing is not None:
            value = eval(compile(trailing, CELL_FILENAME, "eval"), namespace)
            if value is not None:
                namespace["_"] = value
                value_repr, oversized = bounded_repr(value, limit)
    # Hardy asked for this one, so it is reported under its own name rather
    # than as the cell having gone wrong -- and, far more importantly, it is
    # *answered*. Catching it here is what makes an interrupt cost one cell
    # instead of the whole session: the kernel stays up, the namespace keeps
    # everything the earlier cells put in it, and the parent gets a reply
    # rather than waiting out a deadline on a child that will never speak.
    except KeyboardInterrupt:
        status = "interrupted"
        traceback.print_exc()
    # A cell is untrusted input, and `exit()` raises SystemExit: the kernel
    # has to outlive whatever a cell does, or one stray call ends a session
    # and every value in it.
    except BaseException:
        status = "error"
        traceback.print_exc()
    captured_out, captured_err, overran = capture.settle(marker)
    fields, truncated = clip_jointly(
        {"stdout": captured_out, "stderr": captured_err, "value_repr": value_repr},
        limit,
    )
    return {
        "status": status,
        **fields,
        "capture_truncated": truncated or overran or oversized or stray,
    }


def _refuse_to_quit(*_args, **_kwargs):
    raise RuntimeError(
        "exit() and quit() would end the computer algebra session and discard "
        "every value in it. Ask Hardy to reset the session instead."
    )


def main() -> None:
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

    capture = _Capture(limit)

    global PENDING_INTERRUPT
    stdin = sys.stdin.buffer
    while True:
        # Deferred across the read, raised across the cell. The cell is the one
        # place a stop *should* interrupt Python directly -- that is what makes
        # it abandon the computation and leave the namespace standing -- and
        # everywhere else it is a flag.
        _handle_stops_by(_remember)
        header = read_exact(stdin, HEADER_BYTES)
        if header is None:
            return
        try:
            length = int(header.decode("ascii"))
        except ValueError:
            return
        payload = read_exact(stdin, length)
        if payload is None:
            return
        # Parsed with the stop still deferred, so nothing here can be
        # interrupted into killing the kernel.
        try:
            request = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
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
            if held and request.get("stopping", True):
                # The stop reached this kernel before the cell did. Answering
                # without running it is what the parent is waiting for: it gets
                # a framed reply straight away, the namespace is untouched, and
                # the kernel is still here for the next cell.
                reply = _interrupted_reply()
            else:
                # `held` without `stopping` is a signal that arrived in the
                # moment after the last reply was flushed and before Hardy had
                # read it. It was aimed at a cell that was already over, and
                # Hardy -- which knows whether it still wants a stop -- says it
                # does not. Rejecting this cell for it would stop something
                # nobody asked to stop.
                reply = run_cell(str(request.get("source", "")), namespace, limit, capture)
        except KeyboardInterrupt:
            reply = _interrupted_reply()
        finally:
            _handle_stops_by(_remember)
        # Taken after every cell, failed ones included: a cell that raised
        # partway through has still changed the namespace, and the parent
        # needs to know whether it did before it decides that replaying only
        # the accepted cells can rebuild this state.
        try:
            reply["state_digest"] = state_digest(namespace, baseline, limit)
        except BaseException:
            # A namespace Hardy cannot fingerprint is one it must not claim to
            # have fingerprinted, and it is emphatically not a reason to end a
            # session: an empty digest is the value that means "not compared",
            # which is what a backend with no digest at all reports.
            reply["state_digest"] = ""
        capture.write_reply(json.dumps(reply, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
