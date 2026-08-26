"""What the session is for, in the user's words, beside every axiom request.

Nobody can judge whether an assumption is too strong without the goal in front
of them. The graded run approved `no_simple_nonabelian_composite_orders` -- the
assignment itself, for 28 of the orders -- after 170 seconds spent reading a
well-argued paragraph with nothing beside it to compare against.

Hardy makes no judgment here and is not meant to. The claim is narrow: a human
is never asked to approve an axiom with the goal off-screen.
"""

from __future__ import annotations

from hardy.tui import handlers


def _request(**overrides):
    request = {
        "formal_name": "sylow",
        "lean_statement": "True",
        "latex_name": "Sylow",
        "informal_statement": "Sylow's theorems",
        "source": "Dummit and Foote",
        "reason": "not in Mathlib",
    }
    request.update(overrides)
    return request


def test_a_goal_round_trips_through_the_record(session) -> None:
    session.set_goal("No finite simple nonabelian group of order < 60.")

    assert session.goal() == "No finite simple nonabelian group of order < 60."


def test_a_goal_survives_reopening_the_project(session_factory, tmp_path) -> None:
    workspace = tmp_path / "reopened"
    session_factory(workspace=workspace).set_goal("A goal.")

    assert session_factory(workspace=workspace).goal() == "A goal."


def test_a_record_written_before_goals_existed_still_opens(session_factory, tmp_path) -> None:
    """`schema_version` stays 2. It refuses records it cannot read, and an
    optional string is readable by construction."""
    workspace = tmp_path / "old"
    workspace.mkdir(parents=True)
    (workspace / "session.json").write_text(
        '{"schema_version": 2, "names": [], "assumptions": []}', encoding="utf-8"
    )

    assert session_factory(workspace=workspace).goal() == ""


def test_the_goal_reaches_the_approval_prompt(session, approvals, fake_lean) -> None:
    session.set_goal("No simple nonabelian group of order < 60.")

    session._tool("request_assumption", _request())

    assert approvals[0]["goal"] == "No simple nonabelian group of order < 60."


def test_an_unset_goal_is_shown_as_unset_rather_than_hidden(
    session, approvals, fake_lean
) -> None:
    session._tool("request_assumption", _request())

    assert approvals[0]["goal"] == ""


def test_the_command_is_registered() -> None:
    assert "goal" in {command.name for command in handlers.build_registry()}


def test_the_command_is_not_safe_in_flight() -> None:
    """Changing what a session is for mid-turn waits, like every other command
    that touches state."""
    goal = next(c for c in handlers.build_registry() if c.name == "goal")

    assert goal.safe_in_flight is False
