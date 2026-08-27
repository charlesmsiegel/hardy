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
# How deep the size estimate walks before it stops. A bound is needed at all
# because a container can hold itself, and the estimate must not become the
# unbounded traversal it exists to prevent. Past it the answer is "too large",
# never "small": a cutoff that returned zero read seven nested singleton lists
# around a multi-gigabyte string as nothing to worry about and handed them to
# plain `repr`, which is the allocation this whole path exists to avoid.
_ESTIMATE_DEPTH = 20
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
    if depth >= _ESTIMATE_DEPTH:
        # Deeper than this is not measured, and unmeasured is not small.
        return limit + 1
    if type(value) not in _SIZED:  # noqa: E721 -- exact type only
        # Genuinely unknown: an object's repr cannot be sized without running
        # it. Zero, so a value with a perfectly ordinary repr still takes the
        # exact path -- this is the residual the docstring names, not a cutoff.
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
_WIN32_STD = {1: -11, 2: -12}


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
            _redirect_win32_handle(fd)
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
        for fd in (1, 2):
            with contextlib.suppress(Exception):
                (sys.stdout if fd == 1 else sys.stderr).flush()
            with contextlib.suppress(OSError):
                os.write(fd, begin)
        return marker

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
            out_text, out_exact = out.text()
            err_text, err_exact = err.text()
            captured = (
                out_text,
                err_text,
                out.truncated
                or err.truncated
                or out.stray
                or err.stray
                or not (out_exact and err_exact),
            )
            for stream in self.streams.values():
                stream.marker = b""
                # Cleared only now that it has been reported. Anything arriving
                # from here on belongs to the next cell's account of what it
                # could not keep.
                stream.stray = False
            return captured

    def write_reply(self, payload: bytes) -> None:
        header = f"{len(payload):0{HEADER_BYTES}d}".encode("ascii")
        data = header + payload
        while data:
            written = os.write(self.protocol_fd, data)
            data = data[written:]


# CPython's default repr -- ` at 0xADDR>` -- says the type and the address and
# nothing else. The address is not state, but neither is anything else in that
# string: `Box()` with `payload = 1` and `Box()` with `payload = 2` render
# identically, so a namespace holding one cannot be told from a namespace
# holding the other. Normalising the address away made those two agree and
# called the rebuild faithful, which is the failure the digest exists to catch.
#
# So an opaque repr is not normalised, it is *refused*: the namespace has no
# fingerprint, the parent reads that as "not compared", and the rebuild is
# reported unverified. The cost is real and is the honest one -- a session that
# binds a plain instance or a `def`-defined function cannot have its state
# checked across a restart, and now says so instead of claiming otherwise.
_OPAQUE = re.compile(r" at 0x[0-9a-fA-F]+>")
# Set to the trailing value while the cell that produced it is being
# described, and to `_UNSET` at the start of every cell. `_` is skipped by the
# digest only while it holds that value, which is exactly the cell whose
# `value_repr` already carries it.
#
# Two ways of getting this wrong were found before it settled here. Skipping
# the name unconditionally hid a cell that assigned `_` itself --
# `_ = random.random()` has no `value_repr` at all, so `_ = 1` and `_ = 2`
# fingerprinted alike. Remembering the object across cells then hid mutations
# to it: `[]` displayed, and a later `_.append(random.random())` leaves the
# same object at the same identity holding something new. Clearing the memory
# every cell answers both -- the skip lasts exactly as long as the claim that
# justifies it.
_UNSET: object = object()
LAST_DISPLAYED: object = _UNSET


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
        value = namespace[name]
        if name == "_" and value is LAST_DISPLAYED:
            continue
        if name in baseline and baseline[name] is value:
            continue
        rendered, truncated = bounded_repr(value, limit)
        if truncated or _OPAQUE.search(rendered):
            # Neither a prefix nor a default repr is a fingerprint. Two values
            # agreeing for the first `limit` bytes hash alike, and two
            # instances of the same class render identically whatever they
            # hold -- so either would let a rebuild holding the wrong one be
            # called faithful, which is the exact failure the digest exists to
            # prevent. No digest at all is the honest answer: the parent reads
            # an empty one as "not compared" and says so.
            return ""
        digest.update(repr(name).encode("utf-8", errors="backslashreplace"))
        digest.update(b"\0")
        digest.update(rendered.encode("utf-8", errors="backslashreplace"))
        digest.update(b"\0")
    # A name the preamble bound and a cell removed contributes nothing to the
    # loop above, so a namespace missing it fingerprinted exactly like one
    # still holding it: `del symbols; 1 / 0` followed by an accepted `pass`
    # rebuilt with `symbols` quietly back and was called faithful. Absence is
    # as much a change as a new value, and is hashed as one.
    for name in sorted((key for key in baseline if key not in namespace), key=repr):
        digest.update(repr(name).encode("utf-8", errors="backslashreplace"))
        digest.update(b"\0deleted\0")
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

    global LAST_DISPLAYED
    LAST_DISPLAYED = _UNSET
    marker = capture.begin()
    status, value_repr, oversized, failure = "ok", "", False, ""
    try:
        if parsed.body:
            exec(compile(parsed, CELL_FILENAME, "exec"), namespace)
        if trailing is not None:
            value = eval(compile(trailing, CELL_FILENAME, "eval"), namespace)
            if value is not None:
                namespace["_"] = value
                value_repr, oversized = bounded_repr(value, limit)
                # The skip below is justified by `value_repr` carrying this
                # value, so it lasts only while `value_repr` is the whole of
                # it. A trailing `[0] * 5000 + [random.random()]` is reported
                # from its prefix, and skipping `_` as well would leave the
                # differing tail in neither the repr nor the digest -- a
                # replay could rebuild a different last element and match
                # both. Left unset, `_` goes through the digest, truncates
                # there too, and the fingerprint is withheld.
                if not oversized:
                    LAST_DISPLAYED = value
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
    captured_out, captured_err, overran = capture.settle(marker)
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
