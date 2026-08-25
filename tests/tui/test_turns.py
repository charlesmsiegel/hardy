"""Esc cancels an in-flight turn, and claims no more than it really did.

`SlowSession.send` blocks on a real `threading.Event`, so the worker thread
genuinely has a turn in flight while the box is driven -- this is what proves
`_run_turn` moved the call off the event loop instead of merely deferring it.

It also answers anyway once released, ignoring the cancellation entirely. That
is deliberate: it stands for the part cancelling cannot reach, so the tests
here see what the terminal does when work the user stopped lands regardless.

This file also pins the shell's key bindings: newline is Shift+Enter (both
the vt100/xterm and kitty-protocol encodings, `shell.py`'s `_SHIFT_ENTER`
patch), a trailing `\\` is the fallback, and plain `escape` is `eager=True`
now that Alt+Enter -- Escape+Enter on the wire -- is no longer bound. That
last change is why `blast` alone is enough for every test here: earlier,
Escape had to be non-eager (Alt+Enter needed it as a chord), which meant a
lone Escape only resolved after prompt_toolkit's own ~1.5s ambiguous-key
window, and in that window Escape immediately followed by `/` was swallowed
whole by Emacs's `M-/` binding (`key_binding/bindings/emacs.py:300`) --
pressing Esc then typing `/status` lost the slash. `eager=True` makes an
exact match win the instant it is seen, with no such window to fall into, so
there is no longer anything to protect against by pacing input apart: sending
Escape and whatever follows it in the very same input batch is now exactly
the right way to drive this shell, not a hazard to route around.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import types
from io import StringIO
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.models import TurnEvent
from hardy.tui import handlers, shell

from .conftest import Streams
from .nested_render import assert_no_outer_render_during_nested

# Comfortably past `Application.ttimeoutlen` (0.5s): the one remaining
# ambiguity is at the vt100-parser byte level (is a lone "\x1b" the start of
# some other escape sequence?), which is unrelated to this shell's own key
# bindings and needs a real flush when Escape is sent with nothing else
# queued behind it in the same input batch.
_ESCAPE_ALONE_PAUSE = 0.7


class SlowSession(Streams):
    """Blocks until released, so a turn is genuinely in flight."""

    def __init__(self):
        self.release = threading.Event()
        self.abandoned: list[str] = []
        self.switched: list[str] = []
        self.sent_text: list[str] = []

    def send(self, text: str) -> str:
        self.sent_text.append(text)
        self.release.wait(timeout=5)
        return "late reply"

    def switch_model(self, model: str) -> None:
        self.switched.append(model)

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


async def wait_for_turn_to_settle(built, *, timeout: float = 5.0) -> None:
    """Poll rather than guess a fixed delay before sending Ctrl+C.

    Added for Task 11's double-tap Ctrl+C: a single press while a turn is
    still running now only warns (`_interrupt`'s first-press branch) instead
    of exiting. `state.turn_running` only flips back to `False` once
    `_run_turn`'s task -- on the loop -- notices the executor future
    resolved and runs its `finally`, a real cross-thread handoff, not
    instantaneous, so guessing a fixed pause below that risks the same
    non-exit this polls to avoid.
    """
    elapsed = 0.0
    step = 0.02
    while built._state.turn_running and elapsed < timeout:
        await asyncio.sleep(step)
        elapsed += step


async def _wait_for_render(buffer: StringIO, text: str, timeout: float = 30.0) -> None:
    """Wait until `text` has actually been drawn, rather than assuming it has.

    Polled against the output buffer because that is the only place the answer
    exists: prompt_toolkit renders on its own schedule, and the alternative --
    reading the buffer once and hoping -- is a race that a loaded runner loses.
    Silent on timeout: the caller's own assertion is the error message worth
    reading, and raising here would replace it with a worse one.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end and text not in buffer.getvalue():
        await asyncio.sleep(0.01)


async def blast(settings, session, keys: str, until: str | None = None) -> tuple[int, str]:
    """Send `keys`, deferring a trailing Ctrl+C until any turn it started
    has settled.

    Every caller in this file ends `keys` with `"\\x03"`, or not at all
    (`/exit` calls `app.exit()` itself and needs no Ctrl+C). A *single*
    Ctrl+C while a turn is still running only warns now (Task 11's
    double-tap policy) rather than exiting -- and every caller here sends
    its whole input as one synchronous batch, which prompt_toolkit processes
    in one pass with no event-loop turn in between, so `state.turn_running`
    is still true by the time a same-batch trailing Ctrl+C is read. Sent
    that way, the app would never exit and `.run()` would never return: the
    exact hang this function exists to prevent. Splitting the trailing
    `"\\x03"` off and sending it only once `wait_for_turn_to_settle` sees the
    turn has finished keeps every single-Ctrl+C-exits test here true to its
    original intent (nothing about what starts the turn changes -- only
    when the final Ctrl+C lands) without ever risking a second, real press
    reaching the un-mocked `os._exit` none of these tests expect.

    `until` names a string the caller needs *rendered* before the trailing
    Ctrl+C is sent. `wait_for_turn_to_settle` waits for the turn, which is not
    the same thing: a notice the escalation path draws afterwards can still be
    unflushed when the app exits and the buffer is read. A test asserting on
    that notice then fails while the behaviour it is about -- the escalation --
    demonstrably happened, which is how this file went red on CI with
    `session.escalated == 1` passing one line above the failure.

    Releases `session.release` (if the session has one) from a background
    timer, in case nothing else in a given test ever does -- `.run()` drives
    `asyncio.run()`, whose own shutdown joins the default executor, so an
    un-released `SlowSession.send` would otherwise stall this call on its
    own multi-second wait.
    """
    release = getattr(session, "release", None)
    if release is not None:
        threading.Timer(0.3, release.set).start()
    content, has_ctrl_c, _ = keys.partition("\x03")
    assert not has_ctrl_c or keys == content + "\x03", (
        "blast() only knows how to defer a *trailing* Ctrl+C"
    )
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.05)
            pipe.send_text(content)
            if has_ctrl_c:
                await asyncio.sleep(0.05)  # let `content` actually be read first
                await wait_for_turn_to_settle(built)
                if until is not None:
                    await _wait_for_render(buffer, until)
                pipe.send_text("\x03")
            code = await task
    return code, buffer.getvalue()


async def test_escape_records_the_abandonment(settings):
    # A space, not "\x1b\x03" directly: `blast` defers the trailing Ctrl+C
    # until the turn settles (see its docstring), which would otherwise
    # leave a lone Escape isolated with nothing to disambiguate it against,
    # needing the vt100 parser's own ~0.5s flush -- a race against
    # `wait_for_turn_to_settle` that can resolve first and send Ctrl+C
    # before Escape ever fires. A harmless space right after it (silently
    # self-inserted into the box; nothing here checks the box's contents)
    # is peek-ahead-resolved instantly, the same way `\x03` used to.
    session = SlowSession()
    await blast(settings, session, "prove something\r\x1b \x03")
    # Cancelled now, not merely walked away from -- but recorded under the same
    # reason, because why a turn ended is what a trajectory has to show.
    assert session.cancelled == ["user_pressed_escape"]


async def test_escape_says_it_cancelled_without_overclaiming(settings):
    """Esc stops the turn and interrupts the children it started -- but only
    the ones there were. This session reports that it stopped nothing, so the
    notice falls back to the older, weaker promise: a file a tool call has
    already written stays written, and the wording says only what is true."""
    session = SlowSession()
    _, written = await blast(settings, session, "prove something\r\x1b \x03")
    assert "cancelled" in written.lower()
    assert "may still finish" in written


class InterruptibleSession(SlowSession):
    """A session with children to stop, and one that will not be talked down.

    `cancel` reports how many it interrupted, as `MathematicsSession.cancel`
    does; `escalate` is what the second press reaches.
    """

    def __init__(self, running: int = 1):
        super().__init__()
        self.running = running
        self.escalated = 0

    def cancel(self, reason: str = "user_cancelled") -> int:
        super().cancel(reason)
        return self.running

    def escalate(self) -> int:
        self.escalated += 1
        return self.running


async def test_escape_says_it_interrupted_the_work_in_flight(settings):
    session = InterruptibleSession()
    _, written = await blast(settings, session, "prove something\r\x1b \x03")
    assert session.cancelled == ["user_pressed_escape"]
    assert "interrupted" in written
    # And that a second press is the way out, because an interrupt a child
    # refuses is otherwise indistinguishable from one nothing happened to.
    assert "esc again" in written


async def test_a_second_escape_escalates_from_interrupt_to_kill(settings):
    session = InterruptibleSession()
    _, written = await blast(settings, session, "prove something\r\x1b \x1b \x03", until="killed")
    assert session.escalated == 1
    # The turn is cancelled once, not twice: the second press is about the
    # child that did not stop, not about the turn, which already has.
    assert session.cancelled == ["user_pressed_escape"]
    assert "killed" in written
    # It says what the kill costs. A CAS kernel loses every value in it, and a
    # user who is about to spend that is owed the price before they see it.
    assert "lost its state" in written


async def test_a_second_escape_with_nothing_left_running_says_so(settings):
    session = InterruptibleSession(running=0)
    _, written = await blast(settings, session, "prove something\r\x1b \x1b \x03")
    assert session.escalated == 1
    assert "nothing left to stop" in written


async def test_escape_does_not_escalate_a_session_that_cannot(settings):
    """A session with no `escalate` is not an error and not a crash -- the
    press simply reports that there is nothing more to do."""
    session = SlowSession()
    _, written = await blast(settings, session, "prove something\r\x1b \x1b \x03")
    assert "nothing left to stop" in written


async def test_escape_abandons_instantly_without_waiting_out_a_timeout(settings):
    """Before this fix-round, a lone Escape took on the order of 1.5s to
    resolve: the Alt+Enter chord forced plain `escape` to be non-eager, so
    the key processor had to wait out its own ~1.0s `timeoutlen` flush to
    decide a lone Escape was not the start of a longer match. `eager=True`
    means an exact match wins the instant it is seen, without needing to
    consider whether a longer one might apply at all -- so a fresh Escape
    with nothing typed behind it (this must be sent as its own input event,
    isolated from anything else, or the *next* key would resolve the
    ambiguity for free by simply not extending any known chord, which
    proves nothing about `eager`) is abandoned within well under a second,
    not the old ~1.5s.
    """
    buffer = StringIO()
    session = SlowSession()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.1)
            pipe.send_text("prove something\r")
            await asyncio.sleep(0.2)
            started = time.monotonic()
            pipe.send_text("\x1b")  # sent alone: nothing else in this input event
            # Comfortably above the vt100 parser's own unrelated ~0.5s flush
            # (see the module docstring), comfortably below the old ~1.5s
            # combined figure a non-eager escape needed.
            await asyncio.sleep(0.8)
            elapsed = time.monotonic() - started
            assert session.cancelled == ["user_pressed_escape"], (
                f"not cancelled within {elapsed:.2f}s -- escape is no longer resolving eagerly"
            )
            session.release.set()
            await asyncio.sleep(0.2)
            pipe.send_text("\x03")
            await task


async def test_escape_immediately_followed_by_slash_no_longer_loses_it(settings):
    """The regression this whole change exists to remove. Escape and `/model`
    sent in the very same input batch used to have the `/` swallowed by
    Emacs's `M-/` binding during the old ambiguous-key window, so `dispatch.
    classify` saw the bare text "model" -- not a command at all -- and
    refused it with the *wrong* message ("A turn is still running. Wait for
    it to finish.", the plain-text refusal) instead of the command-specific
    one. With no window to fall into, the slash survives, `/model` is
    classified as a command, and it is refused for being unsafe in flight,
    not for looking like ordinary text.
    """
    session = SlowSession()
    _, written = await blast(settings, session, "prove something\r\x1b/model\r\x03")
    assert session.switched == []
    assert "/model cannot run while a turn is still running." in written
    assert "Wait for it to finish" not in written  # the old, wrong, plain-text refusal


async def test_status_is_allowed_while_a_turn_is_in_flight(settings):
    """Non-vacuous on purpose: the startup banner also prints the workspace
    path, so asserting only that would pass even if /status's own handler
    never ran. "A turn is still running." is written by `handle_status`
    itself, and only when `state.turn_running` is true. Also exercises the
    Escape-then-slash fix from the command side: /status is `safe_in_flight`,
    so if the slash were lost this would fall through to the plain-text
    refusal instead of actually running the command.
    """
    session = SlowSession()
    _, written = await blast(settings, session, "prove something\r\x1b/status\r\x03")
    assert "A turn is still running." in written
    assert str(settings.workspace) in written


@pytest.mark.parametrize(
    "sequence",
    ["\x1b[27;2;13~", "\x1b[13;2u"],
    ids=["xterm-modifyOtherKeys", "kitty-protocol"],
)
async def test_shift_enter_inserts_a_newline_not_a_submit(settings, sequence):
    """Both encodings prompt_toolkit's own table would otherwise flatten onto
    plain Enter (`Keys.ControlM`) before any binding ever saw the difference:
    the vt100/xterm "modifyOtherKeys" form and the kitty keyboard protocol
    form. `shell.py` extends `ANSI_SEQUENCES` for both; this sends the raw
    escape bytes for each and checks the buffer was not submitted early.

    Parametrised rather than looped. Every other test here drives exactly one
    `Shell`, and pytest-asyncio gives each test its own event loop; a loop that
    ran two whole app lifecycles on one event loop was the only place in this
    file where a second `Application` started on a loop the first had already
    used, and it failed on Linux for that reason -- the first app left a
    background task pending, and the exception that followed was handled by
    prompt_toolkit's own event-loop hook, which prints and then *waits for
    ENTER* (`application.py:1026`). That wait swallowed the keystrokes, so the
    second sequence was never delivered and `sent_text` was empty: a failure
    that looked exactly like a broken key binding but was not one. One
    lifecycle per test, and each encoding now names itself when it fails.
    """
    session = SlowSession()
    code, written = await blast(settings, session, f"one{sequence}two\r\x03")
    # Reported, not just compared: the two ways this fails look identical
    # from `sent_text` alone. A turn that was submitted echoes its own user
    # line, so text on screen with nothing sent means the newline was lost,
    # while a bare banner means the keys never reached the box at all.
    assert session.sent_text == ["one\ntwo"], (
        f"sequence {sequence!r} did not insert a newline: "
        f"exit={code} sent={session.sent_text!r} screen={written!r}"
    )


async def test_plain_enter_still_submits(settings):
    session = SlowSession()
    await blast(settings, session, "prove something\r\x03")
    assert session.sent_text == ["prove something"]


async def test_a_trailing_backslash_still_continues_a_line(settings):
    """The fallback for terminals that never emit a Shift+Enter sequence at
    all. `\\` then Enter must turn into a newline, not a submit."""
    session = SlowSession()
    await blast(settings, session, "one\\\rtwo\r\x03")
    assert session.sent_text == ["one\ntwo"]


def _blast_before_startup(settings, session, keys: str) -> tuple[int, str]:
    """Send `keys` into the pipe *before* the `Shell` (or its loop) exists at
    all, then drive it through `.run()` -- a fresh, synchronous `asyncio.run()`
    -- rather than `blast`'s task-on-an-already-running-loop plus a paced
    `pipe.send_text` after a real `asyncio.sleep`. This is the original
    (pre-Task-11, commit 1201477) `blast` shape exactly, restored as its own
    helper for the one test below that still needs it -- see that test's
    docstring for why even this faithful a reconstruction no longer detects
    what it was written to detect, and why it is kept anyway.
    """
    release = getattr(session, "release", None)
    if release is not None:
        threading.Timer(0.3, release.set).start()
    buffer = StringIO()
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            code = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            ).run()
    return code, buffer.getvalue()


def test_a_turn_started_right_before_exit_is_never_silently_dropped(settings):
    """Fix round 1's critical bug, pinned directly: `/exit` in the same input
    batch as the Enter that starts a turn used to race that turn's own first
    scheduled step on the loop. `asyncio.to_thread` is a coroutine and does
    not call `executor.submit()` until its `await` line actually runs, and
    that line only runs once the turn's task gets a turn on the loop -- if
    the app has already exited by then, prompt_toolkit cancels the task
    first, and `session.send` is never called at all. Not even the "user"
    transcript line records that the turn happened. That was the original,
    real bug (task 10 report), and this asserts the behavior the fix commits
    to: `session.sent_text == ["prove something"]` and `session.abandoned ==
    ["app_exited"]` together, never the "neither" the original bug produced.

    Uses `_blast_before_startup`, the exact pre-Task-11 (commit 1201477)
    `blast` shape, not the shared `blast` this file uses everywhere else --
    restored here as the most faithful reconstruction available of the
    original repro's timing.

    Fix-round-2 finding, stated plainly per the review's own allowance
    ("strengthen this, or say why it cannot"): even this exact reconstruction
    -- original test timing *and* a byte-for-byte revert of `_submit_key`/
    `_run_turn` to their pre-1201477 shape (`_run_turn`'s own `await
    asyncio.to_thread(...)` line doing the submission, not a synchronous
    `run_in_executor` call in the key binding) -- no longer reproduces the
    "neither call happened" failure. 15 runs across two independently-probed
    revert shapes (one keeping the fixed `_run_turn`'s signature via an
    `asyncio.ensure_future` shim, one matching the original single-indirection
    shape exactly) all left `session.sent_text == ["prove something"]` and
    `session.abandoned == ["app_exited"]` intact -- `_run_turn`'s task
    reliably wins its first scheduled turn on the loop before `/exit`'s task
    reaches `app.exit()`, regardless. Removing the `_drain_prompt_requests`
    background task `run_async` now also creates (added after 1201477, so
    absent from the original repro) made no difference either, ruling out
    the one candidate cause tried directly. Something about the accumulated
    scheduling shape of this shell no longer opens the specific window
    layer 1 closed, and nothing here pins down exactly what changed to close
    it. This test is kept anyway, run as its own faithful reconstruction of
    the original scenario and still correctly describing the desired
    behavior under it -- but it cannot currently be relied on as a
    regression guard for layer 1's specific removal. That guard is
    `test_a_turn_task_cancelled_before_its_first_step_is_still_recorded`
    below, which forces the shape deterministically (via monkeypatching
    `Application.create_background_task`) rather than depending on real
    scheduling timing, and does still fail correctly if `run_async`'s
    `_pending_future` backstop is removed.
    """
    session = SlowSession()
    _blast_before_startup(settings, session, "prove something\r/exit\r")
    assert session.sent_text == ["prove something"]
    assert session.abandoned == ["app_exited"]


async def test_a_turn_task_cancelled_before_its_first_step_is_still_recorded(settings):
    """The narrower case the test above cannot reach on its own: `_run_turn`'s
    own `except asyncio.CancelledError` (`shell.py`) only runs if that task
    gets at least one turn on the loop before being cancelled. Verified
    directly in fix round 1 (a small standalone repro, not committed): a task
    cancelled before its very first scheduled step skips its entire coroutine
    body, `try`/`except`/`finally` included, so nothing inside `_run_turn`
    itself can ever fire in that case. The only thing that still can is
    `run_async`'s own `finally`, which checks `_pending_future` directly --
    this test forces exactly that shape by monkeypatching
    `Application.create_background_task` to cancel the `_run_turn` task the
    instant it is created, before control ever returns to the event loop.

    `session.sent_text` still ends up populated here -- layer 1 (fix round 1)
    submits to the executor synchronously in `_submit_key`, *before*
    `_run_turn`'s task is even created, so cancelling that task can no
    longer prevent `session.send` from being called at all (that decoupling
    is the whole point of layer 1). What proves `_run_turn`'s own body never
    ran is that its first line -- echoing the user's text into the
    transcript -- never happens either: "prove something" never appears in
    the captured output as an echoed line, only as the raw bytes typed into
    the box.
    """
    session = SlowSession()
    # This test drives the shell by hand rather than through `blast`, so it
    # can install the monkeypatch below before the app starts. It never
    # releases `session.release` at all: `state.turn_running` never resets
    # in this exact scenario regardless (the only code that would reset it
    # is inside `_run_turn`'s own task, which -- that being the entire point
    # of this test -- never runs a single line), so Ctrl+C cannot be used to
    # leave here even in principle, and a release timer racing this test's
    # own check of `_pending_future.done()` would only risk the backstop
    # seeing the turn as already resolved and skipping the recording this
    # test exists to prove. The task is driven directly and cancelled
    # afterwards instead, as cleanup, not as anything under test; the
    # session's own thread is left blocked and is joined, harmlessly, at
    # whatever later point something (or nothing) sets its `Event`.
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            original_create = built._app.create_background_task

            def cancel_run_turn_before_it_ever_starts(coroutine):
                task = original_create(coroutine)
                if coroutine.cr_code is shell.Shell._run_turn.__code__:
                    task.cancel()
                return task

            built._app.create_background_task = cancel_run_turn_before_it_ever_starts
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.1)
            pipe.send_text("prove something\r")

            # Poll for the actual event rather than sleeping a fixed guess:
            # `_pending_future` is set synchronously by `_submit_key`, in the
            # very same call that invokes the monkeypatched
            # `create_background_task` above (which cancels the `_run_turn`
            # task before this returns) -- so seeing it set is a
            # deterministic signal that both have already happened, not a
            # timing that might or might not have landed first.
            for _ in range(250):
                if built._pending_future is not None:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError('"prove something\\r" was never processed')

            # `_run_turn`'s first line echoes "> prove something" into the
            # transcript; its total absence from the rendered output proves
            # the coroutine body never ran at all, not merely that it
            # errored or was interrupted part-way through.
            assert "> prove something" not in buffer.getvalue()

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # `run_async`'s own backstop records "app_exited" in its own
            # `finally`, which only runs once the app itself has actually
            # finished unwinding -- awaited above, not merely requested --
            # so checking it only after `await task` returns is what makes
            # this deterministic rather than a race against that `finally`.
            assert session.abandoned == ["app_exited"]

    # `session.send` is still blocked on its own executor thread (nothing
    # here ever set `session.release`, deliberately -- see above), and that
    # thread is non-daemon. Releasing it now, once every assertion this test
    # cares about has already run, lets it wind down in milliseconds instead
    # of leaving pytest-asyncio's own per-test loop teardown to join it after
    # its full 5s timeout.
    session.release.set()


async def test_the_reply_of_a_stopped_turn_is_tagged_when_it_lands(settings):
    """The brief singles this out: Esc does not drop the reply, it tags it as
    belonging to the turn that was stopped once it lands. Still true with real
    cancellation: `SlowSession` answers anyway, exactly as a backend that
    cannot be interrupted would. Every other Escape test in
    this file pairs Escape with an immediate Ctrl+C, so none of them ever
    leave the app open long enough to actually observe this happening.

    Escape is sent alone here, in its own input batch with nothing queued
    behind it, specifically so its firing can be sequenced *before* the
    session is released -- that still needs `_ESCAPE_ALONE_PAUSE` to clear
    the vt100 parser's own (unrelated) byte-level ambiguity.
    """
    buffer = StringIO()
    session = SlowSession()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.1)
            pipe.send_text("prove something\r")
            await asyncio.sleep(0.2)
            pipe.send_text("\x1b")
            await asyncio.sleep(_ESCAPE_ALONE_PAUSE)  # let the lone Escape resolve and fire
            session.release.set()  # the app is still open when the reply lands
            await asyncio.sleep(0.3)
            pipe.send_text("\x03")
            code = await task
    assert code == 0
    assert "this turn was stopped; it had already replied:" in buffer.getvalue()
    assert "late reply" in buffer.getvalue()


async def test_spinner_ticking_does_not_paint_under_a_nested_prompt(settings):
    """The spinner's periodic `self._app.invalidate()` is exactly the hazard
    `assert_no_outer_render_during_nested` exists to catch: an outer app
    repainting while a nested prompt owns the screen. A turn stays in flight
    (via `SlowSession`) while a nested `ask_line` prompt is open and several
    spinner ticks (0.1s each) elapse, so this proves the invalidate is safe
    only because it happens under `in_terminal()`, not by assumption.
    """
    buffer = StringIO()
    session = SlowSession()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )

            async def driver() -> None:
                await asyncio.sleep(0.1)
                pipe.send_text("prove something\r")
                await asyncio.sleep(0.3)  # the turn starts, spinner ticking
                task = asyncio.ensure_future(built.ask_line("Name: "))
                await asyncio.sleep(0.4)  # several spinner ticks while nested
                pipe.send_text("ok\r")
                answer = await task
                session.release.set()
                await asyncio.sleep(0.2)
                pipe.send_text("\x03")
                assert answer == "ok"

            with assert_no_outer_render_during_nested():
                driving = asyncio.ensure_future(driver())
                code = await built.run_async()
                await driving
    assert code == 0


class RacingSession(Streams):
    """Reports which thread started the turn, and whether a cancellation
    that arrived with the Enter survived into the turn itself."""

    def __init__(self):
        self.release = threading.Event()
        self.started_on = ""
        self.cancelled_when_read = None
        self._cancelled = False
        self.abandoned: list[str] = []

    def stream(self, text: str):
        # The real session and runtime both reset here, eagerly. Where "here"
        # runs is the whole point of this fake.
        self.started_on = threading.current_thread().name
        self._cancelled = False
        return self._events()

    def _events(self):
        self.release.wait(timeout=5)
        self.cancelled_when_read = self._cancelled
        yield TurnEvent("reply", text="late reply")

    def cancel(self, reason: str = "user_cancelled") -> None:
        self._cancelled = True

    def switch_model(self, model: str) -> None: ...

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


async def test_a_turn_is_started_on_the_thread_that_sequenced_it(settings):
    """Enter and a lone Escape resolve in the very same input batch, with no
    event-loop turn in between (see this module's docstring). Starting the
    turn -- which is what clears the per-turn cancellation flags -- therefore
    has to happen in the Enter handler itself. Left to the worker, the reset
    would land *after* `_abandon` had cancelled and quietly undo it, leaving
    the model running while the transcript said the turn had stopped.

    Only the waiting belongs on the worker, and `cancelled_when_read` proves
    the cancellation was still in force once it got there.
    """
    session = RacingSession()
    await blast(settings, session, "prove something\r\x1b \x03")
    assert session.started_on == threading.current_thread().name
    assert session.cancelled_when_read is True


async def test_a_turn_the_app_exit_cut_off_keeps_the_words_it_drew(settings):
    """`/exit` or Ctrl+D mid-turn cancels this task, and the painter is still
    holding the line it was wrapping -- a line is emitted only once no further
    delta can change it.

    The tail flush sits after `_run_turn`'s `finally`, which this path never
    reaches: it re-raises so cancellation still propagates. Ordinary stream
    failures and plain-mode Ctrl+C both keep that tail, so text the user has
    already watched arrive must not vanish here either.

    Driven directly rather than through `blast`: whether the first event is
    drawn before the exit lands is a race when both keys share one input batch,
    and this is about what happens once it has been.
    """
    drawn: list[str] = []
    session = SlowSession()
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            built._echo = drawn.extend
            arrivals: asyncio.Queue = asyncio.Queue()
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            turn = asyncio.ensure_future(built._run_turn("prove it", future, arrivals))
            # No newline, so the painter cannot settle this line yet.
            arrivals.put_nowait(TurnEvent("text", text="The kernel accepted"))
            await asyncio.sleep(0.05)
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
            future.cancel()

    assert any("The kernel accepted" in line for line in drawn), (
        "the streamed tail was dropped when the app went away"
    )
    assert session.abandoned == ["app_exited"]


class CasCommandSession(Streams):
    """A session whose `/cas` cell blocks, and which counts what Esc reaches.

    Shaped like `MathematicsSession`: `cas.run` is what `handle_cas` calls, and
    `interrupt_work`/`escalate` are what the shell reaches for. The interrupt
    releases the cell, because that is what interrupting a real kernel does --
    the driver answers the signal and the call returns.
    """

    def __init__(self):
        self.release = threading.Event()
        self.running = threading.Event()
        self.interrupted = 0
        self.escalated = 0
        self.resumed = 0
        # Order matters as much as counts: a resume *after* the press erases it.
        self.events: list[str] = []
        self.abandoned: list[str] = []
        # `cas.session.interrupt` is what the cancellation path reaches for,
        # and `interrupt_work` is what Esc reaches for. Both stop the cell, so
        # both count.
        self.cas = types.SimpleNamespace(
            run=self._run,
            session=types.SimpleNamespace(interrupt=self._interrupt_cell),
        )
        self.workspace = Path(".")

    def _run(self, source: str, *, author: str):
        self.running.set()
        self.release.wait(timeout=5)
        return types.SimpleNamespace(
            restart_note="", stdout="", stderr="", value_repr="stopped", note=""
        )

    def _interrupt_cell(self) -> bool:
        self.interrupted += 1
        self.events.append("interrupt")
        self.release.set()
        return True

    def interrupt_work(self) -> int:
        self._interrupt_cell()
        return 1

    def escalate(self) -> int:
        self.escalated += 1
        self.release.set()
        return 1

    def resume_work(self) -> None:
        self.resumed += 1
        self.events.append("resume")

    def send(self, text: str) -> str:
        return "unused"

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


async def drive(settings, session, batches, *, timeout: float = 5.0):
    """Send key batches in stages, waiting between them.

    `blast` sends everything at once, which is right for testing what a single
    input batch resolves to. This is for the opposite: a press that must land
    while something started by an earlier press is genuinely in flight.
    """
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            )
            task = asyncio.ensure_future(built.run_async())
            await asyncio.sleep(0.05)
            for keys, wait_for in batches:
                pipe.send_text(keys)
                if wait_for is not None:
                    end = time.monotonic() + timeout
                    while time.monotonic() < end and not wait_for():
                        await asyncio.sleep(0.01)
                    assert wait_for(), f"never became true after {keys!r}"
                else:
                    await asyncio.sleep(0.1)
            code = await task
    return code, buffer.getvalue()


async def test_escape_interrupts_a_human_cas_cell(settings):
    """A cell the human started is as long-running as one the model sent, and
    goes to the same locked kernel. It used to be unreachable: the handler ran
    on the event loop, so the loop that had to read the Esc was the one the
    cell was blocking, and the key handler bailed out because no *turn* was
    running."""
    session = CasCommandSession()
    _, written = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    assert session.interrupted == 1
    # No turn was cancelled, because none was running: a command is not a turn,
    # and recording one would claim the model had been stopped.
    assert session.abandoned == []
    assert "interrupted" in written


async def test_a_second_escape_during_a_command_escalates(settings):
    session = CasCommandSession()
    # The first press releases this fake's cell, so the second has to land in
    # the same batch to reach a command still marked as running.
    _, written = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("\x1b \x1b ", lambda: session.escalated == 1),
            ("\x03", None),
        ],
    )
    assert session.interrupted == 1
    assert session.escalated == 1
    assert "lost its state" in written


async def test_a_command_in_flight_refuses_a_second_one(settings):
    """The input box is live while the cell runs now, which it was not when the
    cell blocked the loop. Two cells at once would interleave in the one locked
    kernel that both the human and the model go through."""
    session = CasCommandSession()
    _, written = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("/cas 2+2\r", None),
            ("\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    assert "cannot run while a command is still running" in written


async def test_a_command_in_flight_refuses_a_model_turn(settings):
    session = CasCommandSession()
    _, written = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("prove something\r", None),
            ("\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    assert "A command is still running" in written


async def test_a_safe_command_does_not_steal_a_running_cells_ownership(settings):
    """`/status` is deliberately allowed to run alongside a long cell. With a
    flag rather than a count, the short command finishing cleared the state
    belonging to the cell, after which Esc found nothing to stop and the
    runaway was unreachable."""
    session = CasCommandSession()
    _, written = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            # Runs and finishes while the cell is still going.
            ("/status\r", None),
            ("\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    assert session.interrupted == 1, "Esc could not reach the cell after /status"
    assert "Session" in written  # /status really did run


async def test_a_safe_command_does_not_lift_the_stop(settings):
    """A stop stays in force after Esc so that work admitted a moment earlier
    cannot start its child behind the press. `/status` is explicitly permitted
    during a cancelled turn, so lifting the stop there would undo exactly that
    protection."""
    session = CasCommandSession()
    _, _ = await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("\x1b ", lambda: session.interrupted == 1),
            ("/status\r", None),
            ("\x03", None),
        ],
    )
    # One resume, from `/cas` itself at the start. `/status` running after the
    # press must not add another: it is explicitly permitted during a cancelled
    # turn, so lifting the stop there would undo the protection.
    assert session.resumed == 1, "a safe command lifted the stop"


async def test_an_owning_command_lifts_the_stop(settings):
    session = CasCommandSession()
    await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    # `/cas` is not safe-in-flight: it starts new work, so it lifts a stop left
    # over from before it, or its own first cell would be stopped on sight.
    assert session.resumed == 1


async def test_ctrl_c_during_a_cell_interrupts_the_worker(settings):
    """Cancelling the await does not stop the worker. Without a signal the cell
    runs on, and the shell then blocks closing the session while `asyncio.run`
    blocks joining its executor -- both until `cas_cell_seconds`."""
    session = CasCommandSession()
    await drive(
        settings,
        session,
        [
            ("/cas 1+1\r", session.running.is_set),
            ("\x03", lambda: session.interrupted == 1),
        ],
    )
    assert session.interrupted == 1


async def test_escape_in_the_same_batch_as_the_command_is_not_erased(settings):
    """Enter on `/cas` and Esc resolve in one input batch, before the scheduled
    handler runs a line. Lifting the stop inside the handler would erase the
    press that had already been recorded against this very command, and the
    cell would start as though nothing had been pressed."""
    session = CasCommandSession()
    await drive(
        settings,
        session,
        [
            ("/cas 1+1\r\x1b ", lambda: session.interrupted == 1),
            ("\x03", None),
        ],
    )
    # The stop is lifted when the command is admitted, and the press lands
    # after it. The other order leaves the press with nothing to hold.
    assert session.events == ["resume", "interrupt"]
