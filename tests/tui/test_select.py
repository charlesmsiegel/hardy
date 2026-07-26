from __future__ import annotations

import asyncio
import io

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.widgets import Frame, TextArea

from hardy.tui import select
from hardy.tui.ports import Choice

from .nested_render import assert_no_outer_render_during_nested

ROWS = [
    Choice("a", "alpha", "first"),
    Choice("b", "beta"),
    Choice("c", "gamma"),
]


async def drive(keys: str, *, current: int = 0):
    # `choose()` is required to accept explicit input/output rather than lean on
    # the ambient app session -- a later caller opens this widget from a tool
    # thread, and `create_app_session()` does not survive
    # `asyncio.run_coroutine_threadsafe` (contextvars don't cross threads). Tests
    # drive it the same way production must: input/output passed straight in.
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        return await select.choose("Pick", ROWS, current=current, input=pipe, output=DummyOutput())


async def test_enter_takes_the_row_under_the_pointer():
    assert (await drive("\r")).value == "a"


async def test_down_then_enter_takes_the_second_row():
    assert (await drive("\x1b[B\r")).value == "b"


async def test_up_stops_at_the_top():
    assert (await drive("\x1b[A\x1b[A\r")).value == "a"


async def test_the_pointer_starts_where_it_is_told():
    assert (await drive("\r", current=2)).value == "c"


async def test_a_number_key_selects_immediately():
    assert (await drive("2")).value == "b"


async def test_escape_cancels():
    assert await drive("\x1b") is None


async def test_zero_selects_nothing():
    """Accelerators are 1-9 only; a two-digit row can never be read this way."""
    assert (await drive("0\r")).value == "a"


async def test_number_keys_map_to_their_own_rows():
    """Guards the accelerator loop's closure capture.

    `_bindings` binds each number key in a `for` loop with `index: int = offset`
    as a default argument, which binds early at function-definition time. If
    that default were dropped in a refactor, `index` would late-bind to
    whatever `offset` held when the loop finished, and every number key would
    select the same (last) row instead of its own.
    """
    assert (await drive("1")).value == "a"
    assert (await drive("3")).value == "c"


async def _height_and_rendered_lines(rows, subtitle, monkeypatch):
    """Run choose() to completion and measure window height vs rendered lines."""
    created: list[Application] = []

    class Recording(Application):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(select, "Application", Recording)
    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        await select.choose("Pick", rows, subtitle=subtitle, input=pipe, output=DummyOutput())

    (application,) = created
    (window,) = application.layout.find_all_windows()
    text = "".join(fragment[1] for fragment in window.content.text())
    return window.height, text.count("\n") + 1


@pytest.mark.parametrize("subtitle", ["", "why this is being asked"])
@pytest.mark.parametrize("count", [1, 3, 6])
async def test_window_height_equals_rendered_line_count(count, subtitle, monkeypatch):
    """The window must be sized for exactly the lines render() produces.

    An off-by-one here is the displacement bug: content one line taller than
    the window (a trailing newline, an uncounted subtitle) scrolls the list a
    row and draws the first row over the title. Sized from the same data
    render() uses, asserted for both subtitle states and for rows with and
    without notes (a note shares its row's line).
    """
    rows = [Choice(str(i), f"row {i}", "a note" if i % 2 else "") for i in range(count)]
    height, rendered_lines = await _height_and_rendered_lines(rows, subtitle, monkeypatch)
    assert height == rendered_lines


async def test_multiline_label_or_note_cannot_outgrow_the_window(monkeypatch):
    """A newline smuggled into a row field must not add a rendered line.

    The height is a static formula, so it is only correct if no field can span
    lines. `label` and `note` are unvalidated strings that can carry
    user-originated text (`model_rows` puts a user-typed model identity in a
    label), and an embedded newline would make the content one line taller
    than the window -- the same displacement class this module's other tests
    pin, through another door. render() collapses whitespace, keeping the
    formula true by construction.
    """
    rows = [
        Choice("a", "alpha\nsplit across lines"),
        Choice("b", "beta", "note line1\nline2"),
    ]
    height, rendered_lines = await _height_and_rendered_lines(rows, "", monkeypatch)
    assert height == rendered_lines


async def test_no_outer_render_lands_under_an_open_selector():
    """Drives choose() the way production will: off a queue fed by a sync binding.

    A synchronous key binding invalidates its (outer) application the moment
    the handler returns; the postponed redraw then lands after the selector's
    first paint and drags the real cursor out from under the selector's
    renderer, displacing every later paint by a row. choose() must suspend the
    outer application (`in_terminal()`) for its lifetime. This is the test that
    would have caught the spike's thread-selector displacement.
    """
    with create_pipe_input() as pipe:
        output = Vt100_Output(
            io.StringIO(), lambda: Size(rows=24, columns=80), term="xterm", enable_cpr=False
        )
        with create_app_session(input=pipe, output=output):
            requests: asyncio.Queue[None] = asyncio.Queue()
            picked: list[Choice | None] = []
            keys = KeyBindings()

            @keys.add("c-t")
            def _open(event) -> None:  # synchronous on purpose: the failing shape
                requests.put_nowait(None)

            @keys.add("c-c")
            def _quit(event) -> None:
                event.app.exit(result=None)

            outer = Application(
                layout=Layout(HSplit([Frame(TextArea(height=2, prompt="> "))])),
                key_bindings=keys,
                full_screen=False,
            )

            async def serve() -> None:
                while True:
                    await requests.get()
                    # No input/output: production callers on the loop inherit
                    # the ambient session, exactly like this.
                    picked.append(await select.choose("Pick", ROWS))

            async def drive_keys() -> None:
                await asyncio.sleep(0.15)
                pipe.send_text("\x14")  # ctrl-t: open the selector via the queue
                await asyncio.sleep(0.35)  # the postponed outer redraw lands in here
                pipe.send_text("\r")  # accept the first row
                await asyncio.sleep(0.2)
                pipe.send_text("\x03")  # quit the outer app

            # Raises on a mid-selector outer render, and also raises if the
            # selector never rendered under the outer app (no vacuous pass).
            with assert_no_outer_render_during_nested():
                tasks = [asyncio.create_task(serve()), asyncio.create_task(drive_keys())]
                try:
                    await outer.run_async()
                finally:
                    for task in tasks:
                        task.cancel()

    # The helper itself fails if no nested render was observed, so a vacuous
    # pass is impossible; this only confirms the selector answered.
    assert picked == [ROWS[0]], "sanity: the selector must actually have run and answered"
