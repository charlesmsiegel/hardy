"""The inline list a question is asked with.

Not full screen: it renders where the cursor is, so the transcript above stays
in the terminal's own scrollback. One implementation serves both callers --
awaited directly by a command handler on the event loop, or scheduled onto that
loop from a tool thread.
"""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application, in_terminal
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .ports import Choice

# 1-9 only. An accelerator that fires on each keypress can never read a
# two-digit row, because `1` would have selected row 1 before `0` arrived.
ACCELERATORS = "123456789"


def _bindings(rows: Sequence[Choice], cursor: dict[str, int]) -> KeyBindings:
    keys = KeyBindings()

    @keys.add("up")
    def _up(event) -> None:
        cursor["at"] = max(0, cursor["at"] - 1)

    @keys.add("down")
    def _down(event) -> None:
        cursor["at"] = min(len(rows) - 1, cursor["at"] + 1)

    @keys.add("enter")
    def _pick(event) -> None:
        event.app.exit(result=rows[cursor["at"]])

    @keys.add("escape", eager=True)
    @keys.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    for offset, key in enumerate(ACCELERATORS[: len(rows)]):

        @keys.add(key)
        def _jump(event, index: int = offset) -> None:
            event.app.exit(result=rows[index])

    return keys


def _one_line(text: str) -> str:
    """Collapse all whitespace runs -- newlines included -- to single spaces.

    The window height is a static formula, so no row field may ever contribute
    more than one line. `label` and `note` can carry caller- or user-originated
    text (a model identity, for instance), and an embedded newline would make
    the content one line taller than the window -- the displacement bug through
    another door. Collapsing makes the height true by construction; a selector
    is the wrong place to raise over a stray newline.
    """
    return " ".join(text.split())


async def choose(
    title: str,
    rows: Sequence[Choice],
    *,
    current: int = 0,
    subtitle: str = "",
    input=None,
    output=None,
) -> Choice | None:
    if not rows:
        return None
    cursor = {"at": min(max(current, 0), len(rows) - 1)}

    def render() -> FormattedText:
        parts: list[tuple[str, str]] = [("class:select.title", f"  {title}\n")]
        if subtitle:
            parts.append(("class:select.hint", f"  {subtitle}\n"))
        parts.append(("", "\n"))
        for index, row in enumerate(rows):
            here = index == cursor["at"]
            number = f"{index + 1}." if index < len(ACCELERATORS) else "  "
            style = "class:select.row.current" if here else "class:select.row"
            parts.append((style, f"{'❯' if here else ' '} {number} {_one_line(row.label)}"))
            note = _one_line(row.note)
            if note:
                parts.append(("class:select.hint", f"   {note}"))
            parts.append(("", "\n"))
        # No trailing newline: with the window sized exactly to the content, a
        # trailing one would ask for a line the window does not have, and the
        # content would scroll a row -- the same class of displacement as below.
        parts.append(("class:select.hint", "\n  ↑↓ move · 1-9 jump · enter select · esc cancel"))
        return FormattedText(parts)

    # Explicit height, computed from data that cannot change at render time:
    # title + optional subtitle + blank spacer + one line per row + blank
    # spacer + hint. `row.note` shares its row's line, so it adds nothing.
    height = 4 + len(rows) + (1 if subtitle else 0)

    application: Application = Application(
        layout=Layout(HSplit([Window(FormattedTextControl(render), height=height)])),
        key_bindings=_bindings(rows, cursor),
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )
    # Suspend any outer application for the selector's lifetime. Nothing else
    # stops an outer app from repainting underneath an open prompt: a
    # synchronous key binding invalidates its app immediately, the postponed
    # redraw lands after the selector's first paint, and that repaint drags the
    # real cursor out from under the selector's renderer, displacing every
    # later paint by a row. `in_terminal()` is a no-op when no app is running,
    # so callers with explicit input/output (tests) are unaffected.
    async with in_terminal():
        return await application.run_async()
