from __future__ import annotations

from pathlib import Path

import pytest

from hardy.foundation.files import LayoutError
from hardy.workflows import layout


def test_main_chat_uses_the_legacy_paths(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow")
    assert paths.chat == layout.DEFAULT_CHAT
    assert paths.transcript == tmp_path / "sylow" / "transcript.jsonl"
    assert paths.transcript_dir == tmp_path / "sylow"
    assert paths.local_state == tmp_path / "sylow" / ".local" / "state.json"
    assert paths.local_chat == tmp_path / "sylow" / ".local"
    assert paths.chats_root == tmp_path / "sylow" / "chats"


def test_another_chat_lives_under_chats(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof")
    assert paths.transcript == tmp_path / "sylow" / "chats" / "lean-proof" / "transcript.jsonl"
    assert paths.transcript_dir == tmp_path / "sylow" / "chats" / "lean-proof"
    assert paths.local_state == tmp_path / "sylow" / ".local" / "chats" / "lean-proof" / "state.json"
    # Shared parts do not move.
    assert paths.record == tmp_path / "sylow" / "session.json"
    assert paths.lean == tmp_path / "sylow" / "lean"
    assert paths.input_history == tmp_path / "sylow" / ".local" / "input-history"


def test_ensure_creates_the_chat_directories(tmp_path: Path) -> None:
    paths = layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof")
    paths.ensure()
    assert paths.transcript_dir.is_dir()
    assert paths.local_chat.is_dir()
    # And the legacy layout is still made for main.
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    assert (tmp_path / "sylow" / ".local").is_dir()


def test_ensure_for_main_does_not_create_chats_dir(tmp_path: Path) -> None:
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    assert not (tmp_path / "sylow" / "chats").exists()


@pytest.mark.parametrize("bad", ["", ".", "..", ".hidden", "a/b", "a\\b", "con", "trailing ", "x:y"])
def test_validate_chat_refuses_bad_names(bad: str) -> None:
    with pytest.raises(LayoutError):
        layout.validate_chat(bad)


def test_validate_chat_accepts_a_slug() -> None:
    assert layout.validate_chat("lean-proof-2") == "lean-proof-2"


def test_session_record_writes_a_chats_transcript(tmp_path: Path) -> None:
    from hardy.workflows.interactive.record import SessionRecord

    problem = tmp_path / "sylow"
    layout.Layout(root=tmp_path, slug="sylow", chat="lean-proof").ensure()
    record = SessionRecord(problem, chat="lean-proof")
    record.load()
    record._record({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert (problem / "chats" / "lean-proof" / "transcript.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert not (problem / "transcript.jsonl").exists()
    record._save_local()
    assert (problem / ".local" / "chats" / "lean-proof" / "state.json").exists()


def test_session_record_main_is_the_legacy_transcript(tmp_path: Path) -> None:
    from hardy.workflows.interactive.record import SessionRecord

    problem = tmp_path / "sylow"
    layout.Layout(root=tmp_path, slug="sylow").ensure()
    record = SessionRecord(problem)
    record.load()
    record._record({"type": "user", "message": {"role": "user", "content": "hi"}})
    assert (problem / "transcript.jsonl").exists()
