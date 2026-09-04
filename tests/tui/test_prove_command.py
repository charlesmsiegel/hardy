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

    def run(self, request, terminal):
        self.requests.append(request)
        self.terminals.append(terminal)
        return SimpleNamespace(phase=SimpleNamespace(value=self._phase))


@pytest.fixture
def staged(monkeypatch):
    from hardy.tui import prove

    recorder = Recorder()
    monkeypatch.setattr(prove, "run", lambda config, claim, terminal, backend="claude":
                        recorder.run(SimpleNamespace(text=claim, model=config.model), terminal))
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
