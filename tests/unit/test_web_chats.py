from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hardy.app.web import chats
from hardy.foundation.files import LayoutError


def test_main_is_always_listed_first(tmp_path: Path) -> None:
    found = chats.list_chats(tmp_path / "sylow")
    assert [c.id for c in found] == ["main"]
    assert found[0].title == "main"


def test_create_writes_chat_json_and_lists_by_creation(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    first = chats.create_chat(problem, "Formalize Sylow II")
    second = chats.create_chat(problem, "Write-up")
    assert first.id == "formalize-sylow-ii"
    assert second.id == "write-up"
    meta = json.loads((problem / "chats" / first.id / "chat.json").read_text(encoding="utf-8"))
    assert meta == {"schema": "hardy.chat/v1", "title": "Formalize Sylow II", "created": first.created}
    assert [c.id for c in chats.list_chats(problem)] == ["main", first.id, second.id]


def test_collision_gets_a_numeric_suffix(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    assert chats.create_chat(problem, "Proof").id == "proof"
    assert chats.create_chat(problem, "proof").id == "proof-2"
    assert chats.create_chat(problem, "Proof!").id == "proof-3"


def test_chat_id_for_falls_back_when_the_title_has_no_letters() -> None:
    assert chats.chat_id_for("???", ()) == "chat"
    assert chats.chat_id_for("???", ("chat",)) == "chat-2"
    assert chats.chat_id_for("main", ()) == "main-2"


def test_rename_updates_title_and_refuses_main(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    made = chats.create_chat(problem, "Proof")
    renamed = chats.rename_chat(problem, made.id, "Proof of Sylow II")
    assert renamed.title == "Proof of Sylow II" and renamed.created == made.created
    with pytest.raises(ValueError):
        chats.rename_chat(problem, "main", "x")
    with pytest.raises(ValueError):
        chats.rename_chat(problem, "missing", "x")


def test_directories_without_chat_json_are_ignored(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    (problem / "chats" / "stray").mkdir(parents=True)
    assert [c.id for c in chats.list_chats(problem)] == ["main"]


def test_bad_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(LayoutError):
        chats.rename_chat(tmp_path / "sylow", "../x", "t")
    with pytest.raises(ValueError):
        chats.create_chat(tmp_path / "sylow", "   ")


def test_a_symlinked_chat_json_is_ignored(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    chat_dir = problem / "chats" / "escaped"
    chat_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"schema": "hardy.chat/v1", "title": "Escaped", "created": 1.0}), encoding="utf-8"
    )
    link = chat_dir / "chat.json"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks are not available here")
    assert [c.id for c in chats.list_chats(problem)] == ["main"]


def test_a_symlinked_chats_directory_yields_only_main(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    outside = tmp_path / "outside-chats"
    escaped = outside / "escaped"
    escaped.mkdir(parents=True)
    (escaped / "chat.json").write_text(
        json.dumps({"schema": "hardy.chat/v1", "title": "Escaped", "created": 1.0}), encoding="utf-8"
    )
    link = problem / "chats"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    assert [c.id for c in chats.list_chats(problem)] == ["main"]


def test_a_symlinked_chats_directory_refuses_the_write_too(tmp_path: Path) -> None:
    """Listing already refuses a symlinked `chats/`; writing must refuse it too.

    A single guard on `chats/<id>` proves the leaf against its own RESOLVED
    parent, so `chats -> /elsewhere` passes it and `chat.json` lands outside
    the problem -- where `list_chats` will then never look, because it refuses
    the same link.
    """
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    outside = tmp_path / "outside-chats"
    outside.mkdir()
    try:
        os.symlink(outside, problem / "chats", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")
    with pytest.raises(LayoutError):
        chats.create_chat(problem, "Escaped")
    assert list(outside.iterdir()) == []


def test_a_planted_main_directory_is_not_listed_twice(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    planted = problem / "chats" / "main"
    planted.mkdir(parents=True)
    (planted / "chat.json").write_text(
        json.dumps({"schema": "hardy.chat/v1", "title": "Not the real one", "created": 1.0}), encoding="utf-8"
    )
    assert [c.id for c in chats.list_chats(problem)] == ["main"]
    assert [c.title for c in chats.list_chats(problem)] == ["main"]


def test_a_malformed_created_value_is_ignored_not_fatal(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    good = chats.create_chat(problem, "Good")
    bad_dir = problem / "chats" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "chat.json").write_text(
        json.dumps({"schema": "hardy.chat/v1", "title": "Bad", "created": "oops"}), encoding="utf-8"
    )
    assert [c.id for c in chats.list_chats(problem)] == ["main", good.id]


def test_overview_counts_turns_and_the_latest_timestamp(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "user", "timestamp": 100.0}),
            json.dumps({"type": "turn", "timestamp": 101.0}),
            json.dumps({"type": "assistant", "timestamp": 102.0}),
            json.dumps({"type": "turn", "timestamp": 103.5}),
        ]) + "\n",
        encoding="utf-8",
    )
    row = {r["id"]: r for r in chats.overview(problem)}["main"]
    assert row["turns"] == 2
    assert row["last_activity"] == 103.5


def test_overview_distinguishes_no_transcript_from_an_empty_one(tmp_path: Path) -> None:
    """`None` and `0` are different claims and the page prints them differently.

    A chat with an empty transcript has zero turns -- a measurement. A chat
    whose transcript cannot be read has no turn count to report at all, and
    saying `0` would assert it is empty, which nothing has checked.
    """
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text("", encoding="utf-8")
    rows = {r["id"]: r for r in chats.overview(problem)}
    assert rows["main"]["turns"] == 0
    assert rows["main"]["last_activity"] is None


def test_overview_reports_none_when_there_is_no_transcript_at_all(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    row = {r["id"]: r for r in chats.overview(problem)}["main"]
    assert row["turns"] is None
    assert row["last_activity"] is None


def test_overview_survives_a_damaged_line(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        json.dumps({"type": "turn", "timestamp": 1.0}) + "\nnot json at all\n"
        + json.dumps({"type": "turn", "timestamp": 2.0}) + "\n",
        encoding="utf-8",
    )
    row = {r["id"]: r for r in chats.overview(problem)}["main"]
    assert row["turns"] == 2 and row["last_activity"] == 2.0


def test_overview_reads_a_named_chats_transcript_not_the_main_one(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    chat = chats.create_chat(problem, "Write-up")
    (problem / "chats" / chat.id / "transcript.jsonl").write_text(
        json.dumps({"type": "turn", "timestamp": 5.0}) + "\n", encoding="utf-8"
    )
    rows = {r["id"]: r for r in chats.overview(problem)}
    assert rows[chat.id]["turns"] == 1
    assert rows[chat.id]["last_activity"] == 5.0
    assert rows["main"]["turns"] is None
