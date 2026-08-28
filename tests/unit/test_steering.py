"""Steering: what a session learns from the shape of its own tool use.

A failing run made 53 `save_lean` calls, 21 of them in a row, and none landed.
Each refusal was local; nothing said "stop saving and check".
"""

from __future__ import annotations

import json

from hardy import chat as hardy_chat

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
    """The brake promises "until `check_lean` passes on this path" -- and a
    green check only ever means the source it was actually handed, so this
    saves the same source the check just vouched for."""
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)
    assert _check(session, source=GREEN).ok

    result = _save(session, source=GREEN)

    assert "consecutive saves" not in result.output


def test_an_unrelated_green_check_does_not_lift_the_brake(session) -> None:
    """`check_lean` elaborates whatever `source` it is handed, never the file
    at `path` -- so a check on an unrelated one-line source must not lift a
    brake earned by repeated failures of a different, much larger source.
    Reproduces the confirmed sequence: four refused saves of one failing
    source, one unrelated green `check_lean` on the same path, and the fifth
    save of the original failing source is still braked."""
    for _ in range(session.SAVE_STREAK_LIMIT):
        assert not _save(session, source=UNREGISTERED).ok
    assert _check(session, source=GREEN).ok

    result = _save(session, source=UNREGISTERED)

    assert not result.ok
    assert "consecutive saves" in result.output


def test_a_new_turn_clears_the_streak(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    list(session.stream("again"))

    assert session._save_streak == {}


def test_a_path_spelled_differently_shares_the_streak(session) -> None:
    """`Main.lean` and `./Main.lean` name the same workspace file, and the
    streak that brakes repeated refusals must count them together rather
    than resetting every time the model's spelling changes."""
    for _ in range(session.SAVE_STREAK_LIMIT):
        assert not _save(session, path="Main.lean").ok

    result = _save(session, path="./Main.lean")

    assert not result.ok
    assert "consecutive saves" in result.output


def test_another_path_is_not_braked(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    result = _save(session, path="Other.lean")

    assert "consecutive saves" not in result.output


def test_the_tally_survives_a_turn(session) -> None:
    _save(session)
    list(session.stream("again"))

    assert session._tool_tally["save_lean"] == [1, 0]


def _events(session):
    path = session.workspace / "transcript.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_fresh_workspace_with_no_tool_calls_gets_no_block(session) -> None:
    assert session._steering_block() == ""


def _assumption_request(**overrides):
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


def test_the_block_is_not_suppressed_when_only_assumptions_were_approved(session, fake_lean) -> None:
    """The omission is meant to spare a genuinely empty first turn, not the
    one session shape the block most needs to report: three axioms approved
    and nothing else saved. `_tool_tally` used to be seeded only for
    `save_lean`/`check_lean`, so `no_tools` read "no save or check call" as
    "no tool call at all" and silenced the block entirely."""
    for index in range(3):
        # `_dispatch`, not `_tool` directly: the tally this test is pinning is
        # kept by `_dispatch`, the same path every real tool call takes.
        result = session._dispatch(
            "request_assumption", _assumption_request(formal_name=f"axiom{index}", latex_name=f"A{index}")
        )
        assert result.ok

    block = session._steering_block()

    assert block != ""
    assert "approved assumptions: 3" in block


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

    assert "tex files not yet reached from writeup.tex: completion_status.tex" in block


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


def test_a_tex_file_that_is_not_utf8_does_not_abort_the_block(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "bad.tex").write_bytes(b"\xff\xfe")

    block = session._steering_block()

    assert "tex files not yet reached from writeup.tex:" in block
    assert "bad.tex" in block


def test_one_unreadable_tex_file_does_not_hide_the_others(session, monkeypatch) -> None:
    """A `.tex` file that cannot be read at all -- unlike `bad.tex` above,
    whose bytes are merely not UTF-8 -- used to fail the whole `sources`
    comprehension and hide every orphan, including the one in a file that
    read just fine."""
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "orphan.tex").write_text("x", encoding="utf-8")
    (session.tex_root / "unreadable.tex").write_text("x", encoding="utf-8")

    real_read_text = hardy_chat.read_text

    def flaky(base, relative, **kwargs):
        if str(relative) == "unreadable.tex":
            raise OSError("permission denied")
        return real_read_text(base, relative, **kwargs)

    monkeypatch.setattr(hardy_chat, "read_text", flaky)

    unreached = session._unreached_tex()

    assert unreached == ["orphan.tex"]


def test_a_broken_obligations_call_does_not_abort_the_turn(session, monkeypatch) -> None:
    """`_steering_block` runs at the top of `stream()`, before the `user`
    event for the turn is recorded. A failure inside it -- here forced on
    `_obligations`, but the same is true of `lean_workspace.sources()` and
    `_saved_theorems()` -- must degrade to no block rather than take the
    turn down with it."""
    _save(session)  # so `no_tools` is False and `_obligations` is reached

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(session, "_obligations", boom)

    assert session._steering_block() == ""

    list(session.stream("hi"))

    kinds = [event["type"] for event in _events(session)]
    assert "user" in kinds
