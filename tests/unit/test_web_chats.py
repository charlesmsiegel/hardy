from __future__ import annotations

import json
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
