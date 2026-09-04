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
