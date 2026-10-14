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
    """A turn is counted where it STARTS, which is a `user` event.

    This test used to build its fixture out of `type: "turn"` events and
    assert those were the turns -- encoding the very bug it was meant to
    guard. `turns.py` writes `type: "turn"` only for the terminal markers
    (cancelled, abandoned), so a chat with two successful exchanges reported
    0 and cancelling one pushed it to 1.
    """
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "user", "timestamp": 100.0}),
            json.dumps({"type": "assistant", "timestamp": 101.0}),
            json.dumps({"type": "user", "timestamp": 102.0}),
            json.dumps({"type": "assistant", "timestamp": 103.5}),
        ]) + "\n",
        encoding="utf-8",
    )
    row = {r["id"]: r for r in chats.overview(problem)}["main"]
    assert row["turns"] == 2
    assert row["last_activity"] == 103.5


def test_overview_does_not_count_a_cancellation_marker_as_a_turn(tmp_path: Path) -> None:
    """`type: "turn"` is written when a turn is walked away from.

    Counting it inverted the meaning of the number: a cancelled turn made the
    count go UP while the exchange it belonged to was never counted at all.
    """
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "user", "timestamp": 100.0}),
            json.dumps({"type": "turn", "status": "abandoned", "timestamp": 101.0}),
            json.dumps({"type": "turn", "status": "cancelled", "timestamp": 102.0}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert {r["id"]: r for r in chats.overview(problem)}["main"]["turns"] == 1


def test_overview_does_not_count_a_hardy_note_as_a_turn(tmp_path: Path) -> None:
    """A browser project switch and an editor save are recorded as `user`
    events by the same convention a turn is, and start no turn.
    `starts_turn: False` is what tells them apart -- the same predicate
    `panels.session.transcript` and `chat/tree.js` apply, so all three agree
    on what a turn is."""
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "user", "timestamp": 100.0}),
            json.dumps({"type": "user", "starts_turn": False, "timestamp": 101.0}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert {r["id"]: r for r in chats.overview(problem)}["main"]["turns"] == 1


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


def test_overview_does_not_report_an_exact_count_for_a_damaged_transcript(tmp_path: Path) -> None:
    """A damaged line makes the count unreliable, not merely smaller.

    Skipping the bad line and returning the count of what did parse would
    print an undercount that looks exactly like a genuine turn count --
    issue #169. Nothing distinguishes "2 turns, and a line we could not
    read" from "2 turns, full stop" once the number is on the page, so a
    transcript that is not fully readable reports `None` (not reported),
    the same as one that cannot be opened at all, rather than a number a
    reader would take as exact.
    """
    problem = tmp_path / "sylow"
    problem.mkdir(parents=True)
    (problem / "transcript.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": 1.0}) + "\nnot json at all\n"
        + json.dumps({"type": "user", "timestamp": 2.0}) + "\n",
        encoding="utf-8",
    )
    row = {r["id"]: r for r in chats.overview(problem)}["main"]
    assert row["turns"] is None and row["last_activity"] is None


def test_overview_reads_a_named_chats_transcript_not_the_main_one(tmp_path: Path) -> None:
    problem = tmp_path / "sylow"
    chat = chats.create_chat(problem, "Write-up")
    (problem / "chats" / chat.id / "transcript.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": 5.0}) + "\n", encoding="utf-8"
    )
    rows = {r["id"]: r for r in chats.overview(problem)}
    assert rows[chat.id]["turns"] == 1
    assert rows[chat.id]["last_activity"] == 5.0
    assert rows["main"]["turns"] is None


def test_overview_caches_unchanged_transcripts_and_detects_changes(tmp_path, monkeypatch):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('{"type":"user","timestamp":1}\n')
    original = chats.read_text
    reads = []
    def counted(*args, **kwargs):
        reads.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(chats, 'read_text', counted)
    assert chats.overview(tmp_path)[0]['turns'] == 1
    assert chats.overview(tmp_path)[0]['turns'] == 1
    assert len(reads) == 1
    transcript.write_text('{"type":"user","timestamp":1}\n' * 2)
    assert chats.overview(tmp_path)[0]['turns'] == 2
    transcript.write_text('bad\n')
    assert chats.overview(tmp_path)[0]['turns'] is None
    replacement = tmp_path / 'replacement'
    replacement.write_text('{"type":"user","timestamp":3}\n')
    replacement.replace(transcript)
    assert chats.overview(tmp_path)[0]['last_activity'] == 3


def test_activity_cache_detects_same_size_edit_with_restored_mtime(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('{"type":"user","timestamp":1}\n')
    assert chats.overview(tmp_path)[0]['turns'] == 1
    info = transcript.stat()
    transcript.write_text('{"type":"note","timestamp":2}\n')
    os.utime(transcript, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert transcript.stat().st_size == info.st_size
    assert chats.overview(tmp_path)[0]['turns'] == 0
    assert chats.overview(tmp_path)[0]['last_activity'] == 2


def test_activity_cache_does_not_hide_replaced_symlink(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('{"type":"user"}\n')
    assert chats.overview(tmp_path)[0]['turns'] == 1
    transcript.unlink()
    outside = tmp_path / 'outside'
    outside.write_text('{"type":"user"}\n')
    transcript.symlink_to(outside)
    assert chats.overview(tmp_path)[0]['turns'] is None


@pytest.mark.parametrize("updated_change_time", [1, 2])
def test_windows_activity_cache_uses_change_time_when_stat_identity_is_unchanged(tmp_path, monkeypatch, updated_change_time):
    from types import SimpleNamespace

    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('{"type":"user","timestamp":1}\n')
    info = transcript.stat()
    # Windows ctime is creation time; a restored mtime leaves all stat fields unchanged.
    original_resolve = chats.resolve_named_child
    def frozen_stat(*args):
        original_resolve(*args)  # Keep the real confinement proof.
        return SimpleNamespace(stat=lambda: info)
    monkeypatch.setattr(chats, 'resolve_named_child', frozen_stat)
    monkeypatch.setattr(chats, '_WINDOWS', True, raising=False)
    change_time = [1]
    monkeypatch.setattr(chats, '_windows_change_time', lambda handle: change_time[0], raising=False)
    original_read = chats.read_text
    reads = []
    def counted(*args):
        reads.append(args)
        return original_read(*args)
    monkeypatch.setattr(chats, 'read_text', counted)
    assert chats.overview(tmp_path)[0]['turns'] == 1
    assert chats.overview(tmp_path)[0]['turns'] == 1
    assert len(reads) == 1
    transcript.write_text('{"type":"note","timestamp":2}\n')
    change_time[0] = updated_change_time
    assert chats.overview(tmp_path)[0]['turns'] == 0
    assert chats.overview(tmp_path)[0]['last_activity'] == 2
    assert len(reads) == 2


def test_windows_change_time_queries_basic_info_for_existing_handle(monkeypatch, tmp_path):
    import ctypes
    import sys
    from types import SimpleNamespace

    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_bytes(b'')
    calls = []
    def query(handle, info_class, pointer, size):
        info = pointer._obj
        assert size == 40
        assert type(info).ChangeTime.offset == 24
        assert type(info).FileAttributes.offset == 32
        calls.append((handle, info_class))
        info.CreationTime = 10
        info.ChangeTime = 123456789
        return 1
    def load(name, **options):
        assert name == 'kernel32' and options == {'use_last_error': True}
        return SimpleNamespace(GetFileInformationByHandleEx=query)
    monkeypatch.setattr(ctypes, 'WinDLL', load, raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: fd + 100))
    with transcript.open('rb') as handle:
        assert chats._windows_change_time(handle) == 123456789
        assert calls == [(handle.fileno() + 100, 0)]


def test_windows_activity_query_failure_does_not_return_cached_count(tmp_path, monkeypatch):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('{"type":"user"}\n')
    assert chats.overview(tmp_path)[0]['turns'] == 1
    monkeypatch.setattr(chats, '_WINDOWS', True)
    def failed(handle):
        raise OSError('native metadata query failed')
    monkeypatch.setattr(chats, '_windows_change_time', failed)
    assert chats.overview(tmp_path)[0]['turns'] is None
