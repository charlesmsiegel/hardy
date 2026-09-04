"""`/prove` from inside the session, through the terminal rather than stdio (#85)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hardy.tui import handlers
from hardy.tui.ports import State
from hardy.tui.prove import UiTerminal, problem_slug


class Recorder:
    """A workflow that records what it was asked and answers with a manifest."""

    def __init__(self, phase: str = "completed"):
        self.requests: list = []
        self.terminals: list = []
        self._phase = phase
        self.cancelled = 0
        self.abandoned = 0

    def run(self, request, terminal):
        self.requests.append(request)
        self.terminals.append(terminal)
        return SimpleNamespace(phase=SimpleNamespace(value=self._phase))

    def cancel(self):
        self.cancelled += 1

    def abandon(self):
        self.abandoned += 1


@pytest.fixture
def staged(monkeypatch):
    from hardy.tui import prove

    recorder = Recorder()

    def run(config, claim, terminal, *, backend="claude", ready=None):
        if ready is not None:
            ready(recorder)
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    return recorder


def test_the_slug_is_the_same_one_hardy_prove_uses():
    assert problem_slug("Every group of order 15 is cyclic") == (
        "every-group-of-order-15-is-cyclic"
    )
    assert problem_slug("!!!") == "theorem"


async def test_prove_runs_the_staged_workflow_on_the_typed_claim(ui, settings, staged):
    await handlers.handle_prove(ui, "every group of order 15 is cyclic", State(config=settings, session=None))
    assert staged.requests[0].text == "every group of order 15 is cyclic"
    assert str(settings.runs_root) in ui.text


async def test_prove_runs_on_the_sessions_live_model(ui, settings, staged):
    """`/model` moves `State.config`; a staged run must follow it."""
    import dataclasses

    moved = dataclasses.replace(settings, model="claude-sonnet-5")
    await handlers.handle_prove(ui, "a claim", State(config=moved, session=None))
    assert staged.requests[0].model == "claude-sonnet-5"


async def test_prove_with_no_argument_asks_for_the_claim(ui, settings, staged):
    ui.lines = ["Sylow's first theorem"]
    await handlers.handle_prove(ui, "", State(config=settings, session=None))
    assert staged.requests[0].text == "Sylow's first theorem"


async def test_an_empty_claim_is_refused_rather_than_run(ui, settings, staged):
    await handlers.handle_prove(ui, "", State(config=settings, session=None))
    assert staged.requests == []
    assert "nonempty theorem statement" in ui.text


async def test_a_failed_run_reports_rather_than_ending_the_session(ui, settings, monkeypatch):
    from hardy.tui import prove

    def explode(*args, **kwargs):
        raise RuntimeError("no Lean here")

    monkeypatch.setattr(prove, "run", explode)
    state = State(config=settings, session=None)
    assert await handlers.handle_prove(ui, "a claim", state) is state
    assert "no Lean here" in ui.text


async def test_prove_is_refused_while_a_turn_is_running(settings):
    from hardy.tui import dispatch

    registry = handlers.build_registry()
    outcome = dispatch.classify("/prove x", registry, turn_running=True)
    assert outcome.kind == "refused"


def test_the_approval_question_uses_the_selector_rather_than_a_typed_word(ui):
    ui.choices = [1]                                    # "Revise"
    assert UiTerminal(ui.from_thread).choose_approval() == "revise"
    assert "The formalization above" in ui.asked


def test_an_abandoned_approval_prompt_cancels_rather_than_approves(ui):
    """Nothing may freeze a claim nobody read -- and walking away from the
    question is not the same as reading it and refusing it, which is the
    "Cancel" row and is recorded as a rejection."""
    import pytest as _pytest

    with _pytest.raises(KeyboardInterrupt):
        UiTerminal(ui.from_thread).choose_approval()


def test_the_cancel_row_is_still_a_judgement_rather_than_an_abandonment(ui):
    ui.choices = [2]                                    # "Cancel"
    assert UiTerminal(ui.from_thread).choose_approval() == "cancel"


def test_the_unsafe_execution_warning_is_the_same_one_the_command_line_gives(ui):
    from hardy.cli import ConsoleTerminal

    said: list[str] = []
    ConsoleTerminal(input_fn=lambda prompt: "", output=said.append).acknowledge_unsafe_execution()
    ui.lines = ["I UNDERSTAND"]
    assert UiTerminal(ui.from_thread).acknowledge_unsafe_execution() is True
    for line in said:
        for part in line.split("\n"):
            assert part in ui.text


def test_a_refused_acknowledgement_is_not_an_acknowledgement(ui):
    ui.lines = ["sure"]
    assert UiTerminal(ui.from_thread).acknowledge_unsafe_execution() is False


def test_an_escaped_acknowledgement_prompt_refuses(ui):
    assert UiTerminal(ui.from_thread).acknowledge_unsafe_execution() is False


async def test_cancelling_prove_stops_the_staged_workflow_itself(ui, settings, monkeypatch):
    """Cancelling the await cannot raise inside the worker, so the handle has to
    come back out: otherwise the provider call goes on billing for a run nobody
    is waiting for, and only Lean and LaTeX ever hear about the Esc."""
    import asyncio

    from hardy.tui import prove

    recorder = Recorder()
    started = asyncio.Event()

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        started.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(prove, "run", run)
    interrupted: list[int] = []
    monkeypatch.setattr(
        "hardy.tui.handlers.process.interrupt_children", lambda: interrupted.append(1)
    )
    with pytest.raises(asyncio.CancelledError):
        await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert recorder.cancelled == 1
    assert interrupted == [1]                 # and the children, as before


async def test_a_workflow_too_old_to_be_cancelled_does_not_break_the_press(
    ui, settings, monkeypatch
):
    import asyncio

    from hardy.tui import prove

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(SimpleNamespace())              # no `cancel`
        raise asyncio.CancelledError

    monkeypatch.setattr(prove, "run", run)
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: None)
    with pytest.raises(asyncio.CancelledError):
        await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))


async def test_esc_reaches_the_staged_run_rather_than_only_the_session(ui, settings, monkeypatch):
    """Esc against a command calls `_stop_command`, which reaches the SESSION's
    children -- right for `/cas`, whose cell is a child, and wrong for a staged
    run, whose provider call is not. The handler has to publish its own stop."""
    from hardy.tui import prove

    recorder = Recorder()
    interrupted: list[int] = []
    monkeypatch.setattr(
        "hardy.tui.handlers.process.interrupt_children", lambda: interrupted.append(1)
    )

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        # The press lands here, while the run is in flight, exactly as Esc does.
        assert ui.stopper is not None, "nothing was published for Esc to reach"
        assert ui.stopper() is True
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert recorder.cancelled == 1
    assert interrupted == [1]
    assert ui.stopper is None, "the stopper outlived the command"


async def test_esc_during_the_workflow_build_still_stops_the_run(ui, settings, monkeypatch):
    """Building the workflow identifies Lean and Tectonic, so a press very
    plausibly lands before anything is published. The run must not then start."""
    from hardy.tui import prove

    recorder = Recorder()
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: None)

    def run(config, claim, terminal, *, backend="claude", ready=None):
        # Pressed while the builder was still working: nothing is published yet.
        assert ui.stopper() is True
        ready(recorder)                     # published only afterwards
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert recorder.cancelled == 1, "the press before publication was lost"


async def test_the_stopper_is_cleared_even_when_the_run_fails(ui, settings, monkeypatch):
    from hardy.tui import prove

    monkeypatch.setattr(prove, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert ui.stopper is None


async def test_prove_can_actually_be_used_in_a_plain_session(settings, monkeypatch):
    """The test below asserts which thread the workflow runs on, and that is all
    it asserts -- so it passed while `/prove` raised `RuntimeError` at the first
    prompt in every plain session, because the fake never touched the terminal.
    This one drives the real facade, which is the thing that broke.
    """
    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    said: list[str] = []
    # "1" is the Approve row. The plain selector asks for a number, and an
    # earlier version of this test answered "approve": not a digit, so the
    # selector returned None, the terminal read that as a cancellation, and the
    # assertion below accepted it because it allowed any of the three words.
    answers = iter(["I UNDERSTAND", "1"])
    picked: list[str] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        # What the staged workflow does first, through the same facade.
        assert terminal.acknowledge_unsafe_execution() is True
        picked.append(terminal.choose_approval())
        return SimpleNamespace(phase=SimpleNamespace(value="completed"))

    monkeypatch.setattr(prove, "run", run)
    ui = PlainUi(said.append, lambda prompt: next(answers, ""))
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert picked == ["approve"], "the plain selector did not take the answer"
    assert any("Artifacts" in line for line in said)


async def test_the_plain_session_runs_the_workflow_inline(settings, monkeypatch):
    """A worker's `input()` cannot be unblocked by a Ctrl+C delivered to the
    main thread, and the plain terminal facade reads with `input()`: the
    handler was cancelled, the read stayed pending, and `asyncio.run` waited on
    the executor. Inline, Ctrl+C raises inside `workflow.run`, which has
    handled it since long before `/prove`."""
    import threading

    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    where: list[str] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        where.append(threading.current_thread().name)
        return SimpleNamespace(phase=SimpleNamespace(value="completed"))

    monkeypatch.setattr(prove, "run", run)
    ui = PlainUi(lambda text: None, lambda prompt: "")
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert where == [threading.current_thread().name], "the plain session used a worker"


async def test_the_real_shell_still_uses_a_worker(ui, settings, monkeypatch):
    import threading

    from hardy.tui import prove

    where: list[str] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        where.append(threading.current_thread().name)
        return SimpleNamespace(phase=SimpleNamespace(value="completed"))

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert where != [threading.current_thread().name], "the terminal ran it inline"


async def test_the_press_refuses_further_stages_before_it_returns(ui, settings, monkeypatch):
    """Setting the flag is an `Event` and costs nothing; the rest of `cancel`
    waits for the tool gate and the provider worker, which is minutes. Deferring
    both let the worker pass its next check and open one more billable stage
    after the terminal had said the run was stopping."""
    from hardy.tui import prove

    recorder = Recorder()
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: None)

    # Observed rather than asserted in place: `handle_prove` catches `Exception`
    # to keep a failed run from ending the session, so an `assert` inside this
    # callback is swallowed and the test can never fail.
    refused_by_then: list[int] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        ui.stopper()
        # The instant the press returns, before any teardown thread can have
        # run: the workflow must already be refusing stages.
        refused_by_then.append(recorder.abandoned)
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))
    assert refused_by_then == [1], "the run was not refused until a thread ran"


def test_abandon_is_the_instantaneous_half_of_cancel():
    """`ProveWorkflow.cancel` still refuses stages; it just blocks afterwards."""
    from hardy import workflow as workflow_module

    built = workflow_module.ProveWorkflow.__new__(workflow_module.ProveWorkflow)
    import threading as _threading

    built._cancelled = _threading.Event()
    built._runtime_in_flight = None
    built._thread_in_flight = None
    built.abandon()
    assert built._cancelled.is_set(), "abandon did not refuse further stages"

    again = workflow_module.ProveWorkflow.__new__(workflow_module.ProveWorkflow)
    again._cancelled = _threading.Event()
    again._runtime_in_flight = None
    again._thread_in_flight = None
    again.cancel()
    assert again._cancelled.is_set(), "cancel stopped refusing stages"


def test_an_abandoned_revision_prompt_cancels_rather_than_revising_with_nothing(ui):
    """An empty revision is a revision: the workflow loops and opens another
    billable formalization turn with nothing new to say. On the console this
    prompt is a bare `input()`, so Ctrl+C in it raises and the run finalizes as
    a cancellation; the two surfaces have to agree about abandoning it."""
    import pytest as _pytest

    with _pytest.raises(KeyboardInterrupt):
        UiTerminal(ui.from_thread).revision_text()


def test_a_typed_revision_is_still_a_revision(ui):
    ui.lines = ["Use an explicit Nat domain."]
    assert UiTerminal(ui.from_thread).revision_text() == "Use an explicit Nat domain."


def test_abandoning_the_acknowledgement_still_refuses_rather_than_raising(ui):
    """Only the revision prompt is flagged: a refused acknowledgement and a
    cancelled run are different facts, and the manifest records them so."""
    assert UiTerminal(ui.from_thread).acknowledge_unsafe_execution() is False


async def test_ctrl_c_reaches_an_inline_plain_run(settings, monkeypatch):
    """`asyncio.run` installs its own SIGINT handler: the first Ctrl+C cancels
    the main task rather than raising, and a task blocked in synchronous code
    does not learn it was cancelled until that code returns -- so the press did
    nothing while the run went on spending."""
    import os
    import signal

    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    recorder = Recorder()
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: None)
    refused: list[int] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        # What the terminal's user does: one Ctrl+C, delivered for real.
        os.kill(os.getpid(), signal.SIGINT)
        refused.append(recorder.abandoned)
        return SimpleNamespace(phase=SimpleNamespace(value="cancelled"))

    monkeypatch.setattr(prove, "run", run)
    ui = PlainUi(lambda line: None, lambda prompt: "")

    # It does not escape: the press is meant to stop the command, not the
    # session. What it must do is raise INTO the synchronous run, which is what
    # the unreached line below records.
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))

    assert refused == [], "the press did not interrupt the synchronous run"


def test_the_press_guard_restores_the_previous_handler():
    import signal

    from hardy.tui.handlers import _pressing

    before = signal.getsignal(signal.SIGINT)
    with _pressing(lambda: True):
        assert signal.getsignal(signal.SIGINT) is not before
    assert signal.getsignal(signal.SIGINT) is before


async def test_a_second_press_kills_what_the_first_only_asked(ui, settings, monkeypatch):
    """The stopper answered true on every press, so the shell returned before
    reaching any escalation and the documented second Esc never happened -- the
    user could press it all day while a Lean child that ignores interrupts ran
    out its timeout."""
    from hardy.tui import prove

    recorder = Recorder()
    asked: list[str] = []
    monkeypatch.setattr(
        "hardy.tui.handlers.process.interrupt_children", lambda: asked.append("asked") or 1
    )
    monkeypatch.setattr(
        "hardy.tui.handlers.process.stop_children", lambda: asked.append("killed") or 1
    )

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        ui.stopper()              # the first press
        ui.stopper()              # and the second
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))

    assert asked == ["asked", "killed"]
    # The second press escalates rather than abandoning a second time: the run
    # is already refusing stages, and what is left is the child that will not.
    assert recorder.abandoned == 1


async def test_a_press_before_the_workflow_exists_does_not_end_the_session(
    settings, monkeypatch
):
    """`_pressing` raises into the plain-mode run, and the workflow handles it
    once there IS one. Before `ready` publishes a workflow -- Lean and Tectonic
    still being identified -- there is nothing to finalize, and
    `KeyboardInterrupt` is not an `Exception`, so it went on to end the whole
    session: a press meant to stop one command took the conversation with it."""
    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    said: list[str] = []
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: None)

    def run(config, claim, terminal, *, backend="claude", ready=None):
        raise KeyboardInterrupt          # the press, while the build is going

    monkeypatch.setattr(prove, "run", run)
    ui = PlainUi(said.append, lambda prompt: "")

    state = await handlers.handle_prove(
        ui, "a claim", State(config=settings, session=None)
    )

    assert state is not None, "the handler let the interrupt end the session"
    assert any("cancelled before it started" in line for line in said)


async def test_one_press_before_the_run_starts_is_not_an_escalation(settings, monkeypatch):
    """`_pressing`'s handler presses before it raises, so pressing again in the
    handler counted a single Ctrl+C as the documented SECOND press -- one
    interrupt killed the identification child instead of asking it to stop."""
    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    killed: list[str] = []
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: 1)
    monkeypatch.setattr(
        "hardy.tui.handlers.process.stop_children", lambda: killed.append("killed") or 1
    )
    monkeypatch.setattr("hardy.tui.handlers.process.resume_children", lambda: None)

    def run(config, claim, terminal, *, backend="claude", ready=None):
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)   # one press, while the build runs

    monkeypatch.setattr(prove, "run", run)

    await handlers.handle_prove(
        PlainUi(lambda line: None, lambda prompt: ""),
        "a claim",
        State(config=settings, session=None),
    )

    assert killed == [], "one press escalated straight to killing the child"


async def test_a_cancelled_run_does_not_leave_later_commands_stopped(
    ui, settings, monkeypatch
):
    """`interrupt_children` sets a process-wide stop level cleared only when a
    model turn starts, so a cancelled `/prove` left every later `/doctor`,
    `/import` or `/prove` killing its own first child on sight."""
    from hardy.tui import prove

    recorder = Recorder()
    lifted: list[str] = []
    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: 1)
    monkeypatch.setattr(
        "hardy.tui.handlers.process.resume_children", lambda: lifted.append("lifted")
    )

    def run(config, claim, terminal, *, backend="claude", ready=None):
        ready(recorder)
        ui.stopper()
        return recorder.run(SimpleNamespace(text=claim, model=config.model), terminal)

    monkeypatch.setattr(prove, "run", run)
    await handlers.handle_prove(ui, "a claim", State(config=settings, session=None))

    assert lifted == ["lifted"], "the stop outlived the command it belonged to"


async def test_a_second_press_does_not_interrupt_the_finalization_the_first_one_started(
    settings, monkeypatch
):
    """The first press raises into the inline workflow; the second must not.

    By the second press the workflow is already unwinding through its
    cancellation path and finalizing -- writing the terminal event and hashing
    the run directory. Raising again from inside that handler abandons
    `_finalize` half done and `handle_prove` catches it as though the press had
    landed before the run started, leaving a run directory on disk with no
    manifest describing it. `stop` still escalates on every press; only the
    raise is once.
    """
    from hardy.tui import prove
    from hardy.tui.plain import PlainUi

    monkeypatch.setattr("hardy.tui.handlers.process.interrupt_children", lambda: 1)
    monkeypatch.setattr("hardy.tui.handlers.process.stop_children", lambda: 1)
    monkeypatch.setattr("hardy.tui.handlers.process.resume_children", lambda: None)

    finalized: list[str] = []

    def run(config, claim, terminal, *, backend="claude", ready=None):
        import os
        import signal

        try:
            os.kill(os.getpid(), signal.SIGINT)      # the first press
        except KeyboardInterrupt:
            # Standing in for the workflow's own cancellation path, which
            # finalizes the run from inside this handler.
            os.kill(os.getpid(), signal.SIGINT)      # the second, mid-teardown
            finalized.append("manifest written")
            return None
        finalized.append("never interrupted at all")
        return None

    monkeypatch.setattr(prove, "run", run)

    await handlers.handle_prove(
        PlainUi(lambda line: None, lambda prompt: ""),
        "a claim",
        State(config=settings, session=None),
    )

    assert finalized == ["manifest written"]
