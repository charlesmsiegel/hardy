"""The double-tap Ctrl+C: a turn cannot be told to stop, so leaving mid-turn
is a deliberate, named choice, not silent data loss.

`Stalled.send` blocks on a real `threading.Event` nobody ever sets, so a turn
genuinely never finishes on its own -- proving the second press does not wait
for it. The wait is bounded (not infinite): `asyncio.run()`'s own shutdown
joins the default executor with up to a 300s timeout
(`asyncio.constants.THREAD_JOIN_TIMEOUT`), and in the one test here where
`os._exit` is mocked to observe its arguments rather than actually ending the
process, that real join is what the test is left waiting on -- so `Stalled`
uses a short bound (not the unmocked, real-`os._exit` production path, which
never reaches this join at all) to keep the suite fast rather than genuinely
open-ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from io import StringIO

from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.tui import handlers, shell

# Long enough that nothing in a passing test could observe it "finishing
# naturally"; short enough that the one test which genuinely waits this out
# (see the module docstring) does not badly slow the suite.
_STALL = 1.5


class Stalled:
    def __init__(self):
        self.abandoned: list[str] = []

    def send(self, text: str) -> str:
        threading.Event().wait(timeout=_STALL)
        return "never"

    def switch_model(self, model): ...

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


async def test_the_first_ctrl_c_refuses_and_names_the_cost(settings, monkeypatch):
    """Does not wait for `.run()` to return -- it should not, and does not,
    exit after one press while a turn is in flight. The task is driven and
    then cancelled directly, as test cleanup, not as anything under test.
    """
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    session = Stalled()
    written: list[str] = []

    with create_pipe_input() as pipe:
        buffer = StringIO()
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            monkeypatch.setattr(built, "write", lambda text, style="system": written.append(text))
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.1)
            pipe.send_text("prove it\r")
            await asyncio.sleep(0.2)
            pipe.send_text("\x03")
            await asyncio.sleep(0.2)

            assert not task.done()  # still alive: one press must not exit
            assert exits == []
            assert any("orphaned" in text for text in written)

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def test_the_second_ctrl_c_records_then_hard_exits(settings, monkeypatch):
    """A non-daemon worker is joined at shutdown, so only `os._exit` is
    immediate -- which is why this specific test (its `os._exit` mocked to
    observe the call rather than actually exit) genuinely waits out `_STALL`;
    see the module docstring."""
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    session = Stalled()
    with create_pipe_input() as pipe:
        buffer = StringIO()
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            pipe.send_text("prove it\r\x03\x03")
            shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            ).run()
    assert session.abandoned == ["forced_exit"]
    assert exits == [130]


def test_ctrl_c_with_no_turn_leaves_at_once(settings, monkeypatch):
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    with create_pipe_input() as pipe:
        buffer = StringIO()
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            pipe.send_text("\x03")
            code = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            ).run()
    assert code == 0 and exits == []
