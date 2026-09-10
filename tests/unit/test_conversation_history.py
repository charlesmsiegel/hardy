"""Conversation branches select evidence; they never rewind mathematical state."""
import json
from copy import deepcopy

import pytest

from hardy.workflows.interactive.record import SessionRecord


def append(record, kind, text="", **fields):
    record._record({"type": kind, "message": {"content": text}, **fields})
    return record.history().active_leaf


def test_history_refresh_preserves_guarded_filesystem_refusal(tmp_path, monkeypatch):
    """The history owner must not relabel a refused path as malformed JSON.

    Inject the guard's read refusal so this boundary also runs on hosts where
    creating an actual symlink requires privileges. test_chat covers the link.
    """
    from hardy.foundation.files import LayoutError

    record = SessionRecord(tmp_path)
    append(record, "user", "before")
    history = record._history
    record.transcript_path.write_text("changed outside Hardy\n", encoding="utf-8")
    before = record.transcript_path.read_bytes()

    def refuse(*args, **kwargs):
        raise LayoutError("transcript is a symlink; refusing to read or write through it")

    monkeypatch.setattr(record._workspace_guard, "open", refuse)
    with pytest.raises(LayoutError, match="symlink"):
        append(record, "user", "after")
    assert record._history is history
    assert record.transcript_path.read_bytes() == before


def test_legacy_ids_survive_restart_without_rewriting_and_new_events_have_parents(tmp_path):
    record = SessionRecord(tmp_path)
    legacy = b'{"type":"user","message":{"content":"original"}}\n'
    record.transcript_path.write_bytes(legacy)
    parent = record.history().active_leaf
    assert parent.startswith("legacy:")
    child = append(record, "assistant", "answer")
    assert record.transcript_path.read_bytes().startswith(legacy)
    restarted = SessionRecord(tmp_path)
    history = restarted.history()
    assert history.active_leaf == child
    assert [entry.entry_id for entry in history.path()] == [parent, child]
    assert history.entries[-1].parent_id == parent


def test_atomic_branch_cursor_restarts_and_spend_counts_all_branches_once(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    parent = append(record, "user", "shared")
    abandoned = append(record, "result", cost_usd=0.25, session_id="before-fork")
    original = record.transcript_path.read_bytes()
    transition = record.branch(parent, expected_leaf=abandoned, action="abandon", summary="The estimate failed.")
    assert record.transcript_path.read_bytes().startswith(original)
    restarted = SessionRecord(tmp_path)
    restarted.load()
    history = restarted.history()
    assert history.active_leaf == transition
    assert history.epoch == transition
    assert [e.entry_id for e in history.path()] == [parent, transition]
    summary = history.path()[-1].event()["summary"]
    assert summary == {"text": "The estimate failed.", "author": "human", "status": "unverified", "from_leaf": abandoned}
    append(restarted, "result", cost_usd=0.5, session_id="after-fork")
    usage = restarted._recover_spend()
    assert usage == restarted._recover_spend()
    assert restarted.local["usage_recovered_turns"] == 2
    assert usage.cost_usd == 0.75


def test_unknown_and_stale_parents_refused_before_append(tmp_path):
    record = SessionRecord(tmp_path)
    parent = append(record, "assistant", "partial", partial=True, checkpoint=True, block_id="block")
    leaf = append(record, "assistant", "complete", block_id="block")
    before = record.transcript_path.read_bytes()
    for target, expected, error in [("missing", leaf, "Unknown"), (leaf, parent, "changed")]:
        with pytest.raises(ValueError, match=error):
            record.branch(target, expected_leaf=expected)
        assert record.transcript_path.read_bytes() == before


def test_fork_at_checkpoint_replays_partial_without_later_completion(tmp_path):
    record = SessionRecord(tmp_path)
    parent = append(record, "assistant", "partial", partial=True, checkpoint=True, block_id="block")
    leaf = append(record, "assistant", "later completion", block_id="block")
    record.branch(parent, expected_leaf=leaf)
    replay = json.loads(record.history().replay())
    assert replay[0]["partial"] is True
    assert replay[0]["checkpoint"] is True
    assert "later completion" not in json.dumps(replay)


def test_provider_thread_cannot_cross_a_crash_after_branch_transition(tmp_path):
    record = SessionRecord(tmp_path)
    record.load()
    parent = append(record, "user", "shared")
    leaf = append(record, "assistant", "old answer")
    record._remember_thread("old-thread")
    assert record._carried_thread() == "old-thread"
    record.branch(parent, expected_leaf=leaf)
    restarted = SessionRecord(tmp_path)
    restarted.load()
    assert restarted._carried_thread() is None
    restarted._remember_thread("new-thread")
    assert restarted._carried_thread() == "new-thread"


def test_other_record_instance_cannot_attach_late_events_or_threads_after_fork(tmp_path):
    old = SessionRecord(tmp_path)
    old.load()
    parent = append(old, "user", "shared")
    leaf = append(old, "assistant", "old answer")
    newer = SessionRecord(tmp_path)
    newer.load()
    newer.branch(parent, expected_leaf=leaf)
    before = newer.transcript_path.read_bytes()
    # Reading the new tree must not rebind an already-running writer.
    old.history()
    with pytest.raises(ValueError, match="branch changed"):
        old._record({"type": "assistant", "message": {"content": "late old answer"}})
    with pytest.raises(ValueError, match="branch changed"):
        old._remember_thread("old-provider")
    assert newer.transcript_path.read_bytes() == before


def test_two_instances_racing_the_same_fork_cursor_commit_only_one(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    one, two = SessionRecord(tmp_path), SessionRecord(tmp_path)
    parent = append(one, "user", "shared")
    leaf = append(one, "assistant", "old answer")
    barrier = Barrier(2)

    def fork(record):
        barrier.wait(timeout=5)
        try:
            return record.branch(parent, expected_leaf=leaf)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fork, (one, two)))
    assert sum(result is not None for result in results) == 1
    assert len([e for e in one._recorded() if e["type"] == "conversation_branch"]) == 1


def test_path_replay_deduplicates_completed_blocks_and_retains_unfinished_tools(tmp_path):
    record = SessionRecord(tmp_path)
    append(record, "assistant", "part", partial=True, block_id="b")
    append(record, "tool_started", name="check_lean", call_id="c")
    parent = append(record, "assistant", "complete", block_id="b")
    abandoned = append(record, "user", "EXCLUDED SIBLING")
    record.branch(parent, expected_leaf=abandoned)
    events = json.loads(record.history().replay())
    assert not any(e.get("partial") for e in events)
    assert any(e.get("call_id") == "c" for e in events)
    assert "EXCLUDED SIBLING" not in json.dumps(events)
    assert "complete" in json.dumps(events)


def test_history_snapshots_are_detached_and_forged_ancestry_is_refused(tmp_path):
    record = SessionRecord(tmp_path)
    append(record, "user", "original")
    snapshot = record.history()
    event = snapshot.entries[0].event()
    event["message"]["content"] = "changed"
    assert snapshot.entries[0].event()["message"]["content"] == "original"
    raw = list(record._recorded())[0]
    raw["parent_id"] = raw["entry_id"]
    record.transcript_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SessionRecord(tmp_path).history()


def test_real_session_fork_rebuilds_runtime_from_exact_visible_path_and_preserves_context(tmp_path):
    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path, FakeChatRuntime(["first answer"]))
    chat.send("SHARED QUESTION")
    parent = chat.record.history().active_leaf
    chat.send("EXCLUDED QUESTION")
    old_runtime = chat.runtime
    chat.state["assumptions"] = [{"formal_name": "Approved", "lean_statement": "True",
                                  "paper": {"id": "source-v1", "module": "Papers.Approved"}}]
    chat._save_state()
    state = deepcopy(chat.state)
    old_bytes = chat.record.transcript_path.read_bytes()
    chat.abandon_conversation(parent, "Human diagnosis: this argument was circular.")
    assert chat.runtime is not old_runtime
    assert chat.runtime.session_id is None
    assert chat.state == state
    assert chat.record.transcript_path.read_bytes().startswith(old_bytes)
    context = [e for e in chat.record._recorded() if e["type"] == "branch_context"][-1]
    assert context["text"] in chat.runtime.context["system_prompt"]
    assert "SHARED QUESTION" in context["text"]
    assert "EXCLUDED QUESTION" not in context["text"]
    assert "Human diagnosis" in context["text"]
    assert '"status":"unverified"' in context["text"]
    restarted = session(tmp_path, FakeChatRuntime([]))
    assert restarted.runtime.session_id is None
    replay = [e for e in restarted.record._recorded() if e["type"] == "branch_context"][-1]
    assert "EXCLUDED QUESTION" not in replay["text"]
    assert restarted.state == state


def test_real_session_refuses_fork_during_an_unconsumed_turn(tmp_path):
    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path, FakeChatRuntime(["answer"]))
    parent = chat.record.history().active_leaf
    events = chat.stream("pending")
    with pytest.raises(ValueError, match="running"):
        chat.fork_conversation(parent)
    events.close()
    chat.fork_conversation(parent)


def test_live_provider_worker_refused_and_failed_rebuild_cannot_reuse_old_runtime(tmp_path):
    from types import SimpleNamespace

    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path, FakeChatRuntime(["answer"]))
    chat.send("shared")
    parent = chat.conversation_tree().active_leaf
    chat.runtime.worker = SimpleNamespace(is_alive=lambda: True)
    before = chat.record.transcript_path.read_bytes()
    with pytest.raises(ValueError, match="worker is still running"):
        chat.fork_conversation(parent)
    assert chat.record.transcript_path.read_bytes() == before
    del chat.runtime.worker

    def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    chat._make_runtime = fail
    with pytest.raises(RuntimeError, match="provider unavailable"):
        chat.fork_conversation(parent)
    with pytest.raises(ValueError, match="reopen"):
        chat.stream("must not reach the old provider")
    assert session(tmp_path, FakeChatRuntime([])).runtime.session_id is None


def test_dropped_stream_releases_branch_guard_after_its_generator_is_closed(tmp_path):
    import gc

    from test_chat import FakeChatRuntime, session

    chat = session(tmp_path, FakeChatRuntime(["answer"]))
    events = chat.stream("shared")
    next(events)
    parent = chat.conversation_tree().active_leaf
    del events
    gc.collect()
    chat.fork_conversation(parent)


def test_real_api_runtime_replays_selected_context_without_adopting_native_messages(tmp_path):
    import sys

    from test_api_runtime import Block, FakeClient, Reply

    from hardy.agents.api import AnthropicProvider, ApiRuntime
    from hardy.workflows.interactive.session import MathematicsSession

    clients = []

    def make(model=None, **context):
        client = FakeClient([Reply([Block(type="text", text="exact answer")]) for _ in range(3)])
        clients.append(client)
        return ApiRuntime(model or "test-model", provider=AnthropicProvider("test-model", client=client), **context)

    chat = MathematicsSession(tmp_path, make, (sys.executable,), (sys.executable,), lambda proposal: False)
    chat.send("SHARED API QUESTION")
    parent = chat.conversation_tree().active_leaf
    chat.send("EXCLUDED API QUESTION")
    old = chat.runtime
    chat.fork_conversation(parent)
    assert chat.runtime is not old
    assert chat.runtime.conversation == []
    chat.send("NEW API QUESTION")
    request = clients[-1].sent[-1]
    context = [e for e in chat.record._recorded() if e["type"] == "branch_context"][-1]
    assert context["text"] in request["system"]
    assert "SHARED API QUESTION" in request["system"]
    assert "EXCLUDED API QUESTION" not in json.dumps(request)
    assert "NEW API QUESTION" in json.dumps(request["messages"])
    chat.switch_model("replacement-model")
    chat.send("AFTER MODEL SWITCH")
    request = clients[-1].sent[-1]
    serialized = json.dumps(request, default=lambda value: value.__dict__)
    assert serialized.count("NEW API QUESTION") == 1
    assert "EXCLUDED API QUESTION" not in serialized


def test_branch_replay_bound_refuses_without_changing_cursor(tmp_path, monkeypatch):
    from hardy.workflows.interactive import history

    record = SessionRecord(tmp_path)
    parent = append(record, "user", "large visible history " * 20)
    before = record.transcript_path.read_bytes()
    monkeypatch.setattr(history, "REPLAY_LIMIT", 100)
    with pytest.raises(ValueError, match="replay limit"):
        record.branch(parent, expected_leaf=parent)
    assert record.transcript_path.read_bytes() == before


def test_backend_that_ignores_provider_threads_receives_the_whole_active_branch(tmp_path):
    from test_api_runtime import Block, FakeClient, Reply
    from test_chat import FakeChatRuntime, session

    from hardy.agents.api import AnthropicProvider, ApiRuntime

    chat = session(tmp_path, FakeChatRuntime(["answer"]))
    chat.send("shared")
    chat.fork_conversation(chat.conversation_tree().active_leaf)
    chat.send("POST FORK NATIVE TURN")
    assert chat._carried_thread() is not None
    client = FakeClient([Reply([Block(type="text", text="done")])])

    def make(model=None, **context):
        return ApiRuntime("test-model", provider=AnthropicProvider("test-model", client=client), **context)

    chat._make_runtime = make
    chat.switch_model("api")
    chat.send("new API turn")
    assert "POST FORK NATIVE TURN" in client.sent[-1]["system"]


def test_accounting_is_not_model_context_and_abandoned_attempts_do_not_return_in_summary(tmp_path):
    from test_chat import FakeChatRuntime, session

    from hardy.documents.export import build

    chat = session(tmp_path, FakeChatRuntime(["answer"]))
    chat.send("SHARED CONVERSATION")
    parent = chat.conversation_tree().active_leaf
    chat._record({"type": "tool", "name": "check_lean", "arguments": {},
                  "result": {"ok": False, "output": "ABANDONED TOOL DIAGNOSTIC"}})
    chat._record({"type": "result", "cost_usd": 123.4567, "session_id": "provider-id"})
    chat.abandon_conversation(parent, "Human lesson to retain.")
    assert "cost_usd" not in chat.conversation_tree().replay()
    assert "ABANDONED TOOL DIAGNOSTIC" not in str(chat.summary())
    material = chat.export_material()
    assert "ABANDONED TOOL DIAGNOSTIC" not in json.dumps(material["transcript"])
    assert "active conversation path" in material["conversation_notice"]
    assert "all branches" in material["conversation_notice"]
    page = build(material)
    assert "active conversation path" in page
    assert "Human lesson (unverified, not proof)" in page
    assert "Human lesson to retain." in page
    assert "ABANDONED TOOL DIAGNOSTIC" not in page


def test_model_replay_omits_accounting_on_the_selected_path(tmp_path):
    record = SessionRecord(tmp_path)
    parent = append(record, "result", cost_usd=0.25, session_id="native-secret-state")
    record.branch(parent, expected_leaf=parent)
    assert "cost_usd" not in record.history().replay()
    assert "native-secret-state" not in record.history().replay()
