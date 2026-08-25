"""Turning a message that is still arriving into whole terminal lines.

`transcript.py` renders a message that is finished. This renders one that is
not, and it exists to keep that module's rule intact while doing so: once a
line is printed the terminal owns it, and nothing here reflows or rewrites it,
because rewriting scrollback is what breaks selection and copy.

So a line is emitted only once no further delta can change it. For the greedy
wrapping `textwrap` does, that is every line of the paragraph in hand except
the last -- appending words never moves a break that has already fallen.
"""

from __future__ import annotations

import textwrap
import time
from collections.abc import Callable
from typing import Any

from . import transcript

INDENT = "  "


class LineWriter:
    """Feed it deltas; it hands back the lines that are now final."""

    def __init__(self, width: int, marker: str = "● "):
        self._width = width
        self._marker = marker
        # The paragraph still arriving, and how many of its wrapped lines have
        # already been handed out. Reset together at every newline.
        self._paragraph = ""
        self._settled = 0
        self._started = False

    def feed(self, text: str) -> list[str]:
        out: list[str] = []
        self._paragraph += text
        while "\n" in self._paragraph:
            line, rest = self._paragraph.split("\n", 1)
            # A paragraph that has met its newline cannot grow, so its last
            # line is final too.
            self._paragraph = line
            out.extend(self._ready(keep_last=False))
            self._paragraph, self._settled = rest, 0
        out.extend(self._ready(keep_last=True))
        return out

    def flush(self) -> list[str]:
        """Whatever is left, once nothing more is coming."""
        # Nothing pending means nothing to draw. A message ending in a newline
        # has already had its last paragraph settled, and emitting the empty
        # remainder would hang a blank line off the end of every reply.
        out = self._ready(keep_last=False) if self._paragraph else []
        self._paragraph, self._settled = "", 0
        return out

    @property
    def wrote_anything(self) -> bool:
        """Whether a marker has been spent -- i.e. whether anything was drawn."""
        return self._started

    def _ready(self, *, keep_last: bool) -> list[str]:
        # A blank line is a paragraph break, and there is nothing to break
        # until something has been said. Without this an answer that opens
        # with a newline spends its marker on an empty line.
        if not self._paragraph and not self._started:
            return []
        wrapped = self._wrap(self._paragraph)
        limit = max(len(wrapped) - 1, 0) if keep_last else len(wrapped)
        lines = wrapped[self._settled : limit]
        # `max`, not assignment: `flush` may run after the same lines were
        # already settled by a `feed`, and must not hand them out twice.
        self._settled = max(self._settled, limit)
        return [self._decorate(line) for line in lines]

    def _decorate(self, line: str) -> str:
        if self._started:
            return f"{INDENT}{line}"
        self._started = True
        return f"{self._marker}{line}" if self._marker else f"{INDENT}{line}"

    def _wrap(self, text: str) -> list[str]:
        limit = max(self._width - len(INDENT), 8)
        return textwrap.wrap(
            text,
            width=limit,
            drop_whitespace=True,
            break_long_words=False,
            # Hardy prints filesystem paths constantly and a hyphen-split path
            # cannot be selected out of the terminal. Same reasoning, and same
            # setting, as `transcript._render`.
            break_on_hyphens=False,
        ) or [""]


def tool_started(name: str) -> str:
    """A tool call, drawn when it starts rather than when it returns.

    This is the line that keeps a three-minute Lean check from reading as a
    hang, and it is why tool boundaries stay legible in a streamed turn: prose
    the model wrote and work Lean or LaTeX did never share a line.
    """
    return f"▸ {name or 'tool'}"


def tool_finished(name: str, ok: bool | None, seconds: float) -> str:
    mark = "✓" if ok or ok is None else "✗"
    return f"{mark} {name or 'tool'} · {seconds:.1f}s"


def notice(line: str) -> str:
    """Hardy speaking, rather than the model.

    Marked differently from prose on purpose: a line saying the workspace owes
    a writeup is worth nothing if it reads as one more thing the model said.
    """
    return f"! {line}"


class TurnPainter:
    """A turn's events, turned into lines. Shared by both terminals.

    The real shell and `--plain` differ in how a line reaches the screen, not
    in what a turn looks like, so the decision of what to draw lives here once.
    """

    def __init__(self, width: int, clock: Callable[[], float] = time.monotonic):
        self._width = width
        self._clock = clock
        self._writer = LineWriter(width)
        # Keyed by invocation, never by name: the SDK can run several calls at
        # once, including two of the same tool, and keying by name would let
        # them overwrite each other's start times and let the first result to
        # land claim that nothing was running any more.
        self._active: dict[str, tuple[str, float]] = {}
        self._reply = ""
        self._spoke = False
        # Whether the whole reply has already been drawn for a backend that
        # never streamed it, so `finish` does not draw it a second time.
        self._drawn = False

    @property
    def running(self) -> str:
        """What the spinner should name. A turn three minutes inside
        `check_lean` should say so, and one inside two calls should not
        pretend it is only inside the last to start."""
        if not self._active:
            return ""
        names = [name for name, _ in sorted(self._active.values(), key=lambda item: item[1])]
        return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"

    def draw(self, event: Any) -> list[str]:
        if event.kind == "text":
            self._spoke = True
            return self._writer.feed(event.text)
        if event.kind == "tool_use":
            self._active[self._key(event)] = (event.name, self._clock())
            # Prose is flushed and the writer replaced, so the model's words and
            # the work Lean or LaTeX did never share a line, and whatever the
            # model says afterwards starts as a fresh message rather than
            # trailing off the paragraph the tool call interrupted.
            lines = self._writer.flush()
            self._writer = LineWriter(self._width)
            return lines + [tool_started(event.name)]
        if event.kind == "tool_result":
            started = self._active.pop(self._key(event), None)
            elapsed = self._clock() - started[1] if started is not None else 0.0
            return [tool_finished(event.name or (started[0] if started else ""), event.ok, elapsed)]
        if event.kind == "reply":
            self._reply = event.text
        if event.kind == "notice":
            # Flushed first, for the same reason a tool call flushes: Hardy's
            # verdict on the turn must not trail off the end of the model's
            # last paragraph as though the model had written it.
            lines = self._writer.flush()
            self._writer = LineWriter(self._width)
            # And a backend that reports no partial text has said nothing yet:
            # its whole reply is still held for `finish`. Drawn here instead,
            # because a line contradicting a claim has to come *after* the
            # claim -- printed before it, it reads as a preamble to a result
            # the reader has not seen yet.
            if not self._spoke and self._reply and not self._drawn:
                lines = lines + transcript.hardy_lines(self._reply, self._width)
                self._drawn = True
            return lines + [notice(line) for line in event.text.splitlines()]
        return []

    @staticmethod
    def _key(event: Any) -> str:
        """The invocation, falling back to the name for a backend that reports
        no call id. One un-identified call at a time still times correctly."""
        return getattr(event, "call_id", "") or event.name

    @property
    def streamed(self) -> bool:
        """Whether any of the reply was drawn as it arrived."""
        return self._spoke

    def finish(self) -> list[str]:
        lines = self._writer.flush()
        self._active.clear()
        if self._spoke or self._drawn:
            return lines
        # Nothing was streamed. A backend that does not report partial text is
        # allowed to exist -- the plain path has to keep working, streaming or
        # not -- so the reply is drawn whole rather than silently dropped.
        return lines + transcript.hardy_lines(self._reply, self._width)
