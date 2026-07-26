"""The axiom gate, reached from a genuine SDK tool thread, must not deadlock.

`Shell.from_thread` marshals a prompt from a worker thread onto the loop via
`Shell._prompt_queue` (posted with `loop.call_soon_threadsafe`) and blocks that
thread on a `concurrent.futures.Future`; `Shell._drain_prompt_requests`, a
background task running on the loop, is the only thing that ever awaits the
posted coroutine, which is where the nested `Application` actually gets built.

`ScriptedUi.from_thread` calls `asyncio.run()` per call, so it works from a
genuine worker thread but raises inside an `async def` test that already has a
loop running (a second `asyncio.run()` cannot nest). The same is true of the
real `Shell.from_thread` used here for a different reason -- posting from the
UI thread and blocking there would deadlock, since the drainer could never
run -- so every test that actually exercises a from-thread prompt drives it
from a real `threading.Thread`, not from the test coroutine itself.
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

from .conftest import Streams
from .nested_render import assert_no_outer_render_during_nested

PROPOSAL = {
    "formal_name": "riemann",
    "lean_statement": "True",
    "latex_name": "RH",
    "informal_statement": "the hypothesis",
    "source": "paper",
    "reason": "needed",
}


async def _wait_for_turn_to_settle(built, *, timeout: float = 5.0) -> None:
    """Poll rather than guess a fixed delay before sending Ctrl+C.

    `state.turn_running` only flips to `False` once `_run_turn`'s task (on
    the loop) notices the executor future has resolved and runs its
    `finally` -- a real cross-thread handoff, not instantaneous. Sending
    Ctrl+C before that settles hits the double-tap policy's *first*-press
    branch (a warning, not an exit) even though the tool call already
    answered, which would hang a test that only sends one Ctrl+C.
    """
    elapsed = 0.0
    step = 0.02
    while built._state.turn_running and elapsed < timeout:
        await asyncio.sleep(step)
        elapsed += step


async def test_a_tool_thread_can_ask_and_get_an_answer(settings):
    """The axiom gate is called from an SDK tool thread. It must not deadlock.

    `session.send` runs on the executor thread `_run_turn` submits it to
    (Task 10's `loop.run_in_executor`), which is a genuine worker thread, not
    a simulated one -- so this reaches `cli.confirm_assumption`'s `from_thread`
    call exactly the way a real tool call would.
    """
    from hardy import cli

    answers: list[bool] = []
    buffer = StringIO()

    class Session(Streams):
        def send(self, text: str) -> str:
            confirm = cli.confirm_assumption(the_shell)
            answers.append(confirm(PROPOSAL))
            return "done"

        def switch_model(self, model): ...
        def record_abandonment(self, reason): ...

    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, Session(), handlers.build_registry(), input=pipe, output=output
            )

            async def driver() -> None:
                await asyncio.sleep(0.1)
                pipe.send_text("prove it\r")
                await asyncio.sleep(0.3)  # the turn starts; the selector opens
                pipe.send_text("2\r")  # row 2: "Yes, approve it"
                await _wait_for_turn_to_settle(the_shell)
                pipe.send_text("\x03")

            driving = asyncio.ensure_future(driver())
            code = await the_shell.run_async()
            await driving

    assert code == 0
    assert answers == [True]
    assert "riemann" in buffer.getvalue()


def test_from_thread_refuses_to_be_used_on_the_ui_thread(settings):
    """Using it there would deadlock, so it must fail loudly instead."""
    import pytest

    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            )
            with pytest.raises(RuntimeError, match="UI thread"):
                the_shell.from_thread.confirm("really?")


def test_from_thread_refuses_before_the_app_is_running(settings):
    """A tool thread reaching the gate before the loop exists must fail
    loudly too, rather than hang forever on a queue nothing drains."""
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            )
        errors: list[BaseException] = []

        def call_from_a_real_thread() -> None:
            try:
                the_shell.from_thread.confirm("really?")
            except BaseException as error:  # noqa: BLE001 - captured for the assertion below
                errors.append(error)

        worker = threading.Thread(target=call_from_a_real_thread)
        worker.start()
        worker.join(timeout=5)
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)
        assert "not running" in str(errors[0])


def test_the_approval_declines_by_default(settings):
    from hardy import cli

    class Ui:
        def __init__(self):
            self.written: list[str] = []

        def write(self, text, *, style="system"):
            self.written.append(text)

        def choose(self, title, rows, *, current=0, subtitle=""):
            return None  # Esc

        def ask_line(self, prompt):
            return None

        def confirm(self, question):
            return False

    class Holder:
        from_thread = Ui()

    assert cli.confirm_assumption(Holder())(PROPOSAL) is False


async def test_an_escaped_prompt_from_a_tool_thread_also_declines(settings):
    """A cancelled/Esc'd selector from a real thread, not a fake `Ui`, still
    hard-gates the assumption -- failing open here would be the worst bug in
    the project.

    Escape is sent well after the turn starts (not the shorter pause used
    elsewhere in this file): unlike a digit-row accelerator, a lone Escape
    sent too soon can still land on the *outer* shell (whose own `escape`
    binding is `eager=True` and fires without waiting to see what follows)
    if the axiom selector's own input reader has not attached yet -- proven
    by instrumenting this exact scenario while writing this test: at a
    shorter delay, the outer shell's `_abandon` fired instead of the
    selector's cancel, and the selector was left open with nothing ever
    answering it, hanging the test outright.
    """
    from hardy import cli

    buffer = StringIO()
    answers: list[bool] = []

    class Session(Streams):
        def send(self, text: str) -> str:
            answers.append(cli.confirm_assumption(the_shell)(PROPOSAL))
            return "done"

        def switch_model(self, model): ...
        def record_abandonment(self, reason): ...

    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, Session(), handlers.build_registry(), input=pipe, output=output
            )

            async def driver() -> None:
                await asyncio.sleep(0.1)
                pipe.send_text("prove it\r")
                await asyncio.sleep(1.0)  # let the selector's own reader attach
                pipe.send_text("\x1b")  # Esc cancels the selector
                await _wait_for_turn_to_settle(the_shell)
                pipe.send_text("\x03")

            driving = asyncio.ensure_future(driver())
            code = await the_shell.run_async()
            await driving

    assert code == 0
    assert answers == [False]


async def test_axiom_prompt_from_a_tool_thread_does_not_paint_under_the_spinner(settings):
    """Approval arriving mid-turn is the realistic production case: a prompt
    opened from a tool thread while the spinner is still ticking is exactly
    the hazard `assert_no_outer_render_during_nested` exists to catch.
    """
    from hardy import cli

    buffer = StringIO()
    answers: list[bool] = []
    release = threading.Event()

    class Session(Streams):
        def send(self, text: str) -> str:
            release.wait(timeout=5)
            answers.append(cli.confirm_assumption(the_shell)(PROPOSAL))
            return "done"

        def switch_model(self, model): ...
        def record_abandonment(self, reason): ...

    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, Session(), handlers.build_registry(), input=pipe, output=output
            )

            async def driver() -> None:
                await asyncio.sleep(0.1)
                pipe.send_text("prove it\r")
                await asyncio.sleep(0.3)  # the turn starts, spinner ticking
                release.set()  # let the tool thread reach from_thread.choose
                await asyncio.sleep(0.3)  # the axiom selector opens
                pipe.send_text("2\r")  # "Yes, approve it"
                await _wait_for_turn_to_settle(the_shell)
                pipe.send_text("\x03")

            with assert_no_outer_render_during_nested():
                driving = asyncio.ensure_future(driver())
                code = await the_shell.run_async()
                await driving
    assert code == 0
    assert answers == [True]


async def test_a_request_posted_after_the_drainer_exits_idle_declines_promptly(settings):
    """The critical race: `app.exit()` cancels `_drain_prompt_requests` and
    awaits it with `timeout=None`. If it happens to be idle at
    `self._prompt_queue.get()` -- outside the inner `try` that used to be the
    only cleanup -- the old code let `CancelledError` propagate with nothing
    left to drain whatever a tool thread posts a moment later. Reproduced
    directly here: cancel the drainer while it is genuinely idle, confirm it
    marks the shell closed, then post from a real thread afterward and
    require the answer back promptly, not a hang.
    """
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            the_shell = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            )
            the_shell._loop = asyncio.get_running_loop()
            drainer = asyncio.ensure_future(the_shell._drain_prompt_requests())
            await asyncio.sleep(0.05)  # let it settle at `queue.get()`, genuinely idle
            drainer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(drainer, timeout=2)
            assert the_shell._closed is True

            results: list[bool] = []
            errors: list[BaseException] = []

            def call_from_a_real_thread() -> None:
                try:
                    results.append(the_shell.from_thread.confirm("really?"))
                except BaseException as error:  # noqa: BLE001 - captured for the assertion below
                    errors.append(error)

            worker = threading.Thread(target=call_from_a_real_thread, daemon=True)
            worker.start()
            # Not a plain blocking `worker.join()`: this coroutine runs *on*
            # the loop the worker's `call_soon_threadsafe` needs to reach --
            # blocking this thread synchronously would starve that loop of
            # the very turn it needs to run `_post_or_decline`, an entirely
            # self-inflicted hang unrelated to the fix under test. Running
            # the join on a different thread keeps the loop free.
            await asyncio.wait_for(asyncio.to_thread(worker.join, 2), timeout=3)
            assert not worker.is_alive(), "a request posted after shutdown must not hang"
            assert errors == []
            assert results == [False]


def test_the_from_thread_check_compares_the_owning_loop_not_the_main_thread(settings):
    """Correct today only because `Shell.run()` always drives the loop from
    the main thread -- what actually matters is whether the calling thread
    *is* the one driving this `Shell`'s loop. Drive the loop from a
    background thread and call `from_thread` directly, synchronously, from
    that same thread while its own loop is running: a check keyed on
    `threading.main_thread()` would have let this through and deadlocked
    (the only thread able to run the drainer would instead be blocked on
    `future.result()`), which is exactly why it is the wrong test.
    """
    buffer = StringIO()
    errors: list[BaseException] = []

    def run_on_a_background_thread() -> None:
        async def body() -> None:
            with create_pipe_input() as pipe:
                output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
                with create_app_session(input=pipe, output=output):
                    the_shell = shell.Shell(
                        settings, None, handlers.build_registry(), input=pipe, output=output
                    )
                    the_shell._loop = asyncio.get_running_loop()
                    try:
                        the_shell.from_thread.confirm("really?")
                    except BaseException as error:  # noqa: BLE001 - captured below
                        errors.append(error)

        asyncio.run(body())

    worker = threading.Thread(target=run_on_a_background_thread, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "UI thread" in str(errors[0])


def test_confirm_assumption_declines_when_the_prompt_itself_raises(settings):
    """Every non-approval path must return `False`, including a bug in the
    prompting path -- a broken gate must not be able to fail open.
    """
    from hardy import cli

    class Ui:
        def write(self, text, *, style="system"):
            raise RuntimeError("boom")

        def choose(self, title, rows, *, current=0, subtitle=""):
            raise AssertionError("must not be reached; write already raised")

        def ask_line(self, prompt):
            raise AssertionError("unused")

        def confirm(self, question):
            raise AssertionError("unused")

    class Holder:
        from_thread = Ui()

    assert cli.confirm_assumption(Holder())(PROPOSAL) is False
