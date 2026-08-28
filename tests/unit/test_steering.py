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


import json


def _events(session):
    path = session.workspace / "transcript.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_fresh_workspace_with_no_tool_calls_gets_no_block(session) -> None:
    assert session._steering_block() == ""


def test_the_block_counts_theorems_assumptions_and_this_session(session) -> None:
    _save(session)

    block = session._steering_block()

    assert block.startswith("[Hardy workspace state — written by Hardy, not the user]")
    assert "saved theorems: 0 machine-checked, 0 open (resting on a hole)" in block
    assert "approved assumptions: 0" in block
    assert "this session: 1 save_lean calls, 0 accepted; 0 check_lean calls, 0 passed" in block


def test_the_block_names_tex_files_nothing_reaches(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "completion_status.tex").write_text("done", encoding="utf-8")

    block = session._steering_block()

    assert "tex files not reached from writeup.tex: completion_status.tex" in block


def test_the_block_line_is_omitted_when_every_tex_file_is_reached(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")

    assert "tex files not reached" not in session._steering_block()


def test_the_block_precedes_the_user_text_in_the_transcript(session) -> None:
    _save(session)

    list(session.stream("carry on"))

    kinds = [event["type"] for event in _events(session)]
    steering = kinds.index("steering")
    assert kinds[steering + 1] == "user"
    assert _events(session)[steering + 1]["message"]["content"] == "carry on"


def test_the_block_reaches_the_runtime_ahead_of_the_user_text(session_factory) -> None:
    seen = []

    class Runtime:
        model = "fake"

        def stream(self, text):
            seen.append(text)
            return iter(())

        def cancel(self):
            pass

    session = session_factory()
    session._make_runtime = lambda model=None, **context: Runtime()
    session.runtime = Runtime()
    _save(session)

    list(session.stream("carry on"))

    assert seen[-1].startswith("[Hardy workspace state")
    assert seen[-1].endswith("\n\ncarry on")


def test_read_workspace_lists_unreached_tex(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "orphan.tex").write_text("x", encoding="utf-8")

    listing = json.loads(session._dispatch("read_workspace", {}).output)

    assert listing["tex_unreached"] == ["orphan.tex"]
