"""The session without a terminal: pipes, CI, dumb terminals, and --plain.

Same registry, same dispatch rules, same banner as the real shell. Only the
drawing differs, which is what keeps `hardy < script.txt` working.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from . import banner, dispatch, stream, transcript
from .handlers import build_registry
from .ports import Choice, State

WIDTH = 80


class PlainUi:
    """A Ui with no event loop. Its `from_thread` calls straight through."""

    def __init__(self, out: Callable[[str], None], read: Callable[[str], str]):
        self._out = out
        self._read = read

    def write(self, text: str, *, style: str = "system") -> None:
        if style == "clear":
            return                                  # nothing to clear
        if style in {"normal", "warning"}:
            self._out(text)
            return
        for line in transcript.notice_lines(text, WIDTH) or [""]:
            self._out(line)

    async def choose(
        self, title, rows: Sequence[Choice], *, current=0, subtitle=""
    ) -> Choice | None:
        self._out("")
        self._out(f"  {title}")
        if subtitle:
            self._out(f"  {subtitle}")
        for number, row in enumerate(rows, start=1):
            mark = "*" if number - 1 == current else " "
            note = f"  {row.note}" if row.note else ""
            self._out(f"  {mark} {number:>3}  {row.label}{note}")
        answer = (await self.ask_line("Choice (number, or blank to cancel): ") or "").strip()
        if not answer.isdigit():
            return None
        index = int(answer)
        return rows[index - 1] if 1 <= index <= len(rows) else None

    async def ask_line(self, prompt: str) -> str | None:
        try:
            return self._read(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    async def confirm(self, question: str) -> bool:
        answer = await self.ask_line(f"{question} [y/N] ")
        return (answer or "").strip().lower() in {"y", "yes"}

    @property
    def from_thread(self) -> Any:
        return _Straight(self)


class _Straight:
    def __init__(self, ui: PlainUi):
        self._ui = ui

    def write(self, text: str, *, style: str = "system") -> None:
        self._ui.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        return asyncio.run(self._ui.choose(title, rows, current=current, subtitle=subtitle))

    def ask_line(self, prompt: str):
        return asyncio.run(self._ui.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        return asyncio.run(self._ui.confirm(question))


def run(
    config,
    session,
    *,
    out: Callable[[str], None] = print,
    read: Callable[[str], str] = input,
    ui_holder: dict[str, Any] | None = None,
) -> int:
    ui = PlainUi(out, read)
    if ui_holder is not None:
        # Populated before the loop starts: `run_session._run_plain`'s
        # `confirm` closure looks this up lazily, since the approval
        # callback has to exist before this `Ui` does (the session needs it
        # at construction), and this is the only `Ui` a tool thread reaching
        # that callback could ever mean.
        ui_holder["ui"] = ui
    for style, text in banner.lines(
        config,
        cas=getattr(session, "cas", None),
        cas_detail=getattr(session, "cas_detail", ""),
    ):
        ui.write(text, style=style)
    out("")

    registry = build_registry()
    state = State(config=config, session=session)
    while not state.done:
        try:
            text = read("> ")
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0

        outcome = dispatch.classify(text, registry, turn_running=False)
        if outcome.kind == "empty":
            continue
        if outcome.kind in {"unknown", "refused"}:
            ui.write(outcome.message, style="error")
            continue
        if outcome.kind == "command":
            state = asyncio.run(outcome.command.handler(ui, outcome.argument, state))
            continue

        for line in transcript.user_lines(outcome.argument, WIDTH):
            out(line)
        painter = stream.TurnPainter(WIDTH)
        # Bound to a name on purpose. Left as the `for` statement's own
        # temporary, a Ctrl+C escaping the loop would drop the last reference
        # and close the generator on the spot -- tearing down the runtime,
        # which interrupts the model and then waits on its worker, before the
        # handler below ever runs. Holding the reference keeps the ordering
        # ours: cancel first, close second.
        events = session.stream(outcome.argument)
        try:
            for event in events:
                for line in painter.draw(event):
                    out(line)
            for line in painter.finish():
                out(line)
        except KeyboardInterrupt:
            # The turn runs synchronously, right here, on the only thread this
            # session has -- unlike the real shell, which moves
            # it to a worker thread precisely so Ctrl+C stays live. A plain
            # `except Exception` does not catch this (`KeyboardInterrupt` is
            # a `BaseException`, not an `Exception`), so it used to escape
            # `run` entirely as an uncaught traceback, leaving the transcript
            # with a `user` event, no reply, and no `turn`/`abandoned`
            # marker -- an abandoned turn indistinguishable from one the user
            # actually waited for. Recorded with its own reason, distinct
            # from the shell's ("user_pressed_escape", "forced_exit",
            # "app_exited"): this is `--plain`'s only way to abandon a turn.
            #
            # Cancelled as well as recorded, now that the runtime can be told
            # to stop -- otherwise the model would go on answering a question
            # this loop has already stopped reading.
            if hasattr(session, "cancel"):
                session.cancel("keyboard_interrupt")
            elif hasattr(session, "record_abandonment"):
                session.record_abandonment("keyboard_interrupt")
            # Only now: the tool gate is shut, so nothing new can start while
            # the runtime is interrupted and its worker joined.
            events.close()
            out("")
            return 0
        except Exception as error:                      # noqa: BLE001 - never lose the session
            # Whatever was streamed before the failure was really said, so it
            # is printed rather than discarded along with the turn.
            for line in painter.finish():
                out(line)
            ui.write(f"{type(error).__name__}: {error}", style="error")
            continue
        out("")
    return 0
