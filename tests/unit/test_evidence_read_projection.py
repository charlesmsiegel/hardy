"""Verified evidence is shared only for one pure read, never across requests."""
from __future__ import annotations

import contextvars
import threading

import pytest

from hardy.foundation.journal import Journal, JournalError
from hardy.foundation.values import FrozenModel


class Note(FrozenModel):
    id: str


def journal_at(path):
    return Journal(path / "evidence", types={"Note": Note})


def test_nested_projection_replays_once_then_verifies_the_next_request(tmp_path, monkeypatch):
    journal = journal_at(tmp_path)
    journal.append((Note(id="first"),), expected_revision=0)
    replay, calls = journal._replay, []
    def observed(guard):
        calls.append(guard)
        return replay(guard)
    monkeypatch.setattr(journal, "_replay", observed)
    with journal.read_projection() as first:
        assert journal.read() is first
        with journal.read_projection() as nested:
            assert nested is first and journal.read() is first
    assert len(calls) == 1
    with journal.read_projection() as second:
        assert second == first and second is not first
    assert len(calls) == 2


def test_projection_refuses_mutations_but_does_not_lock_other_threads(tmp_path):
    journal = journal_at(tmp_path)
    journal.append((Note(id="first"),), expected_revision=0)
    errors = []
    def append():
        try:
            journal.append((Note(id="second"),), expected_revision=1)
        except Exception as error:
            errors.append(error)
    with journal.read_projection():
        with pytest.raises(JournalError, match="read-only"):
            journal.append((Note(id="forbidden"),), expected_revision=1)
        writer = threading.Thread(target=append)
        writer.start()
        writer.join(2)
        assert not writer.is_alive() and not errors
        assert journal.read().revision == 1
    with journal.read_projection():
        assert journal.read().revision == 2


def test_exception_and_copied_context_cannot_reuse_an_expired_snapshot(tmp_path):
    journal = journal_at(tmp_path)
    journal.append((Note(id="first"),), expected_revision=0)
    with pytest.raises(RuntimeError), journal.read_projection():
        copied = contextvars.copy_context()
        raise RuntimeError("projection failed")
    journal.append((Note(id="second"),), expected_revision=1)
    assert copied.run(journal.read).revision == 2
    copied.run(journal.append, (Note(id="third"),), expected_revision=2)
    assert journal.read().revision == 3


def test_new_request_rechecks_tampering_even_when_an_earlier_scope_was_valid(tmp_path):
    journal = journal_at(tmp_path)
    journal.append((Note(id="first"),), expected_revision=0)
    with journal.read_projection():
        assert journal.read().revision == 1
    path = tmp_path / "evidence" / "00000000000000000001.json"
    path.write_text(path.read_text().replace("first", "tampered"))
    with pytest.raises(JournalError, match="digest"), journal.read_projection():
        pytest.fail("a damaged journal cannot enter a verified projection")


def test_cached_records_never_cache_source_or_environment_authority(tmp_path):
    from test_project_binding import SOURCE, bind, setup_pair
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    reference = chat.owners.binding_reference(binding)
    with chat.project_read_projection():
        assert chat.owners.read_evidence(reference) is not None
        (tmp_path / "lean" / "Saved.lean").write_text(SOURCE + "-- changed source\n")
        assert chat.owners.read_evidence(reference) is None
        (tmp_path / "lean" / "Saved.lean").write_text(SOURCE)
        assert chat.owners.read_evidence(reference) is not None
        chat.lean_workspace.rebind_environment("different toolchain")
        assert chat.owners.read_evidence(reference) is None


def test_session_mutations_refuse_before_file_or_ledger_changes(tmp_path):
    from test_project_binding import setup_pair

    from hardy.workflows.ledger.store import LedgerStore
    chat, _target, _formal, _saved = setup_pair(tmp_path)
    ledger = LedgerStore(tmp_path).read()
    names = chat.record.snapshot_of("names")
    with chat.project_read_projection():
        for mutation in (
            lambda: chat.project_target("forbidden", "True", formal="lemma forbidden : True"),
            lambda: chat.save_authored("lean/Forbidden.lean", "import Mathlib\nlemma forbidden : True := True.intro\n"),
            lambda: chat.save_authored("tex/forbidden.tex", "Forbidden write"),
            lambda: chat._tool("record_name", {"formal_name": "forbidden", "latex_name": "forbidden", "description": "Forbidden"}),
        ):
            with pytest.raises(JournalError, match="read-only"):
                mutation()
    assert LedgerStore(tmp_path).read() == ledger
    assert chat.record.snapshot_of("names") == names
    assert not (tmp_path / "lean" / "Forbidden.lean").exists()
    assert not (tmp_path / "tex" / "forbidden.tex").exists()


def test_session_pure_read_entry_points_each_replay_once(tmp_path, monkeypatch):
    from test_project_binding import bind, setup_pair
    chat, target, formal, saved = setup_pair(tmp_path)
    binding = bind(chat, target, formal, saved)
    replay, calls = chat.owners.journal._replay, []
    def observed(guard):
        calls.append(guard)
        return replay(guard)
    monkeypatch.setattr(chat.owners.journal, "_replay", observed)
    for operation in (chat.project_catalog, chat.project_workflow_status, chat.project_intake_status,
                      chat.project_referee_report, chat.summary, lambda: chat.project_inspect(target.id),
                      lambda: chat.project_semantic("report", {"target": target.ref.model_dump(), "scope": binding.scope.model_dump()})):
        before = len(calls)
        operation()
        assert len(calls) == before + 1


def test_host_aggregate_reuses_one_verified_snapshot(tmp_path, monkeypatch):
    from test_project_binding import setup_pair
    from test_web_host import FakeOpener
    from web_fakes import make_config, make_registry

    from hardy.app.web.host import WebHost
    config = make_config(tmp_path)
    chat, _target, _formal, _saved = setup_pair(config.layout.problem)
    registry = make_registry(tmp_path)
    registry.add(chat.workspace)
    host = WebHost(config, FakeOpener(tmp_path), lambda *_: chat, projects=registry)
    host.start()
    replay, calls = chat.owners.journal._replay, []
    def observed(guard):
        calls.append(guard)
        return replay(guard)
    monkeypatch.setattr(chat.owners.journal, "_replay", observed)
    try:
        first = host.project_state(expected_problem=chat.workspace)
        assert first["project"] == str(chat.workspace) and not first["errors"]
        assert len(calls) == 1
        host.project_state(expected_problem=chat.workspace)
        assert len(calls) == 2
    finally:
        host.stop()
