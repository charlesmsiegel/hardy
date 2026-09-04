"""`/status --full`: the workspace summary, drawn from the artifacts (#100)."""

from __future__ import annotations

from types import SimpleNamespace

from hardy import summary
from hardy.tui import handlers
from hardy.tui.ports import State
from hardy.usage import Usage


def session(**overrides):
    assembled = summary.assemble(
        goal="Classify the Sylow subgroups",
        assumptions=[
            {
                "formal_name": "big",
                "lean_statement": "True",
                "source": "Aschbacher 1.2",
                "reason": "Mathlib does not expose it",
                "status": "user-approved",
                "approved_at": "2026-09-04T10:00:00+00:00",
            }
        ],
        registry=[{"formal_name": "sylow", "latex_name": "thm:sylow", "description": "Sylow"}],
        audit={
            "Work": {
                "status": "clean",
                "declarations": [{"name": "sylow", "axioms": ["propext"]}],
                "forbidden": [],
                "unapproved": [],
                "assumed": [],
            }
        },
        theorems={"sylow": "theorem sylow : True"},
        open_theorems=(),
        obligations=(),
    )
    fields = {
        "usage": Usage(),
        "obligations": tuple,
        "has_theorems": lambda: True,
        "summary": lambda: assembled,
    }
    return SimpleNamespace(**{**fields, **overrides})


async def test_plain_status_does_not_draw_the_summary(ui, settings):
    await handlers.handle_status(ui, "", State(config=settings, session=session()))
    assert "Naming registry" not in ui.text


async def test_status_full_draws_the_workspace_summary(ui, settings):
    await handlers.handle_status(ui, "--full", State(config=settings, session=session()))
    assert "Standing assumptions" in ui.text
    assert "Aschbacher 1.2" in ui.text
    assert "sylow: kernel-verified" in ui.text
    assert "sylow <-> thm:sylow" in ui.text


async def test_status_full_never_puts_spend_inside_the_summary(ui, settings):
    """`usage` is withheld from the model; the summary must not carry it back."""
    await handlers.handle_status(ui, "--full", State(config=settings, session=session()))
    body = ui.text.split("Goal\n", 1)[-1]
    assert "$" not in body


async def test_an_unknown_status_argument_says_so_rather_than_being_ignored(ui, settings):
    await handlers.handle_status(ui, "--everything", State(config=settings, session=session()))
    assert "Unknown: /status --everything" in ui.text
    assert "Model" not in ui.text


async def test_status_full_survives_a_summary_that_cannot_be_read(ui, settings):
    def boom():
        raise OSError("workspace unreadable")

    state = State(config=settings, session=session(summary=boom))
    assert await handlers.handle_status(ui, "--full", state) is state
    assert "could not be read" in ui.text


async def test_status_full_against_a_session_that_has_no_summary(ui, settings):
    state = State(config=settings, session=SimpleNamespace(usage=Usage()))
    await handlers.handle_status(ui, "--full", state)
    assert "No workspace summary is available" in ui.text


async def test_the_work_line_and_the_summary_describe_one_workspace(ui, settings):
    """`/status` is safe in flight, so a `save_lean` landing between two reads
    had one command print "Nothing outstanding" and then list the new debt
    under `Next steps` a few lines below."""
    assembled = summary.assemble(
        goal="",
        assumptions=[],
        registry=[],
        audit={},
        theorems={"sylow": "theorem sylow : True"},
        open_theorems=(),
        obligations=("sylow: the writeup does not quote its Lean statement",),
    )

    def moved():
        raise AssertionError("the obligations were read a second time")

    state = State(
        config=settings,
        session=session(summary=lambda: assembled, obligations=moved),
    )
    await handlers.handle_status(ui, "--full", state)

    assert "Nothing outstanding" not in ui.text
    assert "the writeup does not quote its Lean statement" in ui.text


async def test_a_plain_status_still_asks_the_session_for_its_obligations(ui, settings):
    """Only `--full` gathers a summary, and the ordinary line must not start
    paying for one."""
    asked: list[int] = []
    state = State(
        config=settings,
        session=session(obligations=lambda: asked.append(1) or ()),
    )
    await handlers.handle_status(ui, "", state)

    assert asked == [1]
