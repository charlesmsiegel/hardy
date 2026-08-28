"""Steering: what a session learns from the shape of its own tool use.

A failing run made 53 `save_lean` calls, 21 of them in a row, and none landed.
Each refusal was local; nothing said "stop saving and check".
"""

from __future__ import annotations

UNREGISTERED = "import Mathlib\n\ntheorem Nobody : True := by exact True.intro\n"
GREEN = "import Mathlib\n\nlemma fine : True := by exact True.intro\n"


def _save(session, source=UNREGISTERED, path="Main.lean"):
    return session._dispatch("save_lean", {"source": source, "path": path})


def _check(session, source=GREEN, path="Main.lean"):
    return session._dispatch("check_lean", {"source": source, "path": path})


def test_a_refused_save_is_counted_against_its_path(session) -> None:
    _save(session)

    assert session._save_streak == {"Main.lean": 1}
    assert session._tool_tally["save_lean"] == [1, 0]


def test_the_fourth_consecutive_refused_save_is_braked(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        assert not _save(session).ok

    result = _save(session)

    assert not result.ok
    assert "3 consecutive saves of `Main.lean` have been refused" in result.output
    assert "check_lean" in result.output


def test_the_brake_does_not_climb_the_counter(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT + 2):
        _save(session)

    assert session._save_streak["Main.lean"] == session.SAVE_STREAK_LIMIT


def test_a_green_check_lifts_the_brake(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)
    assert _check(session).ok

    result = _save(session)

    assert "consecutive saves" not in result.output


def test_a_new_turn_clears_the_streak(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    list(session.stream("again"))

    assert session._save_streak == {}


def test_another_path_is_not_braked(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    result = _save(session, path="Other.lean")

    assert "consecutive saves" not in result.output


def test_the_tally_survives_a_turn(session) -> None:
    _save(session)
    list(session.stream("again"))

    assert session._tool_tally["save_lean"] == [1, 0]
