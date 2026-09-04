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

    def run(self, request, terminal):
        self.requests.append(request)
        self.terminals.append(terminal)
        return SimpleNamespace(phase=SimpleNamespace(value=self._phase))

    def cancel(self):
        self.cancelled += 1


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
    """Nothing may freeze a claim nobody read."""
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
