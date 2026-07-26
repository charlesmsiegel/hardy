"""Regression: an outer app must not paint underneath a nested selector.

This drives the exact shape that displaced the thread-opened selector in
`spike_terminal.py`: a *synchronous* key binding posts to a queue and a
loop-owned drainer task opens a nested Application. The sync binding makes
`Binding.call` invalidate the outer app immediately, the postponed redraw lands
after the selector's first paint, and the outer app repaints under the open
selector -- shifting the real cursor one row out from under the nested
renderer. Entirely headless: the defect is an interleaving property, visible
without a terminal even though its *symptom* only shows on one.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.application.run_in_terminal import in_terminal
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.widgets import Frame, TextArea

from .nested_render import assert_no_outer_render_during_nested

ROWS = ["yes", "no", "maybe"]


def _selector() -> Application:
    cursor = {"at": 0}
    keys = KeyBindings()

    @keys.add("up")
    def _up(event) -> None:
        cursor["at"] = max(0, cursor["at"] - 1)

    @keys.add("enter")
    def _pick(event) -> None:
        event.app.exit(result=ROWS[cursor["at"]])

    def render():
        parts = [("bold", "  pick one")]
        for index, row in enumerate(ROWS):
            here = index == cursor["at"]
            parts.append(("", "\n"))
            parts.append(("reverse" if here else "", f"{'>' if here else ' '} {row}"))
        return parts

    return Application(
        layout=Layout(HSplit([Window(FormattedTextControl(render), height=1 + len(ROWS))])),
        key_bindings=keys,
        full_screen=False,
        erase_when_done=True,
    )


async def _run_scenario(*, suspend_outer: bool) -> None:
    """Outer app + queue + drainer; the selector opens off a sync key binding."""
    with create_pipe_input() as pipe:
        output = Vt100_Output(
            io.StringIO(), lambda: Size(rows=24, columns=80), term="xterm", enable_cpr=False
        )
        with create_app_session(input=pipe, output=output):
            requests: asyncio.Queue[None] = asyncio.Queue()
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
                    if suspend_outer:
                        async with in_terminal():
                            await _selector().run_async()
                    else:
                        await _selector().run_async()

            async def drive() -> None:
                await asyncio.sleep(0.15)
                pipe.send_text("\x14")  # ctrl-t: open the selector via the queue
                await asyncio.sleep(0.35)  # the postponed outer redraw lands in here
                pipe.send_text("\x1b[A")  # up at the top row, as reported
                await asyncio.sleep(0.2)
                pipe.send_text("\r")  # close the selector
                await asyncio.sleep(0.2)
                pipe.send_text("\x03")  # quit the outer app

            tasks = [asyncio.create_task(serve()), asyncio.create_task(drive())]
            try:
                await outer.run_async()
            finally:
                for task in tasks:
                    task.cancel()


async def test_sync_binding_selector_paints_under_outer_unless_suspended() -> None:
    # The failing shape: without in_terminal(), the outer app repaints while the
    # selector is open. This is the render that displaced the thread selector.
    with assert_no_outer_render_during_nested(raise_on_violation=False) as broken:
        await _run_scenario(suspend_outer=False)
    assert broken.violations, (
        "expected the unsuspended outer app to repaint under the open selector; "
        "if this stops reproducing, the harness timing no longer models the bug"
    )
    offender = broken.violations[0]
    assert offender.app is offender.running[0], "the intruding painter should be the outer app"

    # The fix: in_terminal() suspends the outer app, so the same drive is clean.
    # The helper raises on any violation, and also raises if the selector never
    # rendered under the outer app -- a vacuous pass is structurally impossible.
    with assert_no_outer_render_during_nested() as fixed:
        await _run_scenario(suspend_outer=True)
    assert not fixed.violations


def test_the_helper_refuses_a_vacuous_pass() -> None:
    """An empty body must fail, not silently prove nothing.

    This helper is the project's only defence against the outer-under-nested
    displacement class; if a refactor ever stopped the driven scenario from
    opening its nested prompt, "no violations" would otherwise still pass.
    """
    with (
        pytest.raises(AssertionError, match="no nested render was observed"),
        assert_no_outer_render_during_nested(),
    ):
        pass

    # The waiver is explicit, never the default.
    with assert_no_outer_render_during_nested(expect_nested=False):
        pass
