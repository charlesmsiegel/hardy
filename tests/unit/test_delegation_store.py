"""The journal is append-only and hash-chained; state is replayed, never cached."""
import json
from decimal import Decimal

import pytest

from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    ResourceUsage,
    WorkerResult,
)
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef


def _spec(objective="prove L17"):
    return DelegationSpec(objective=objective, project_refs=(VersionRef(id="L17", digest="a" * 64),),
                          scope=VersionRef(id="scope", digest="b" * 64),
                          lease=ResourceLease(official_checks=2), concurrency=ConcurrencyLease(slots=1),
                          created_by="human")


def _create(store, id, parent=None, objective="prove L17"):
    return store.append(id, "delegation.created", {"spec": _spec(objective).model_dump(mode="json"),
                                                    "parent_id": parent, "created_at": "t"})


def test_created_delegation_is_replayed_after_restart(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    store.append("d-1", "delegation.started", {})
    tree = DelegationStore(tmp_path).tree()
    assert tree.get("d-1").state is DelegationState.ACTIVE
    assert tree.roots == ("d-1",)
    assert tree.revision == 2
    assert DelegationStore(tmp_path / "absent").tree().revision == 0


def test_child_events_link_parent_and_descendants(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "root")
    _create(store, "child", "root", "sub")
    _create(store, "grandchild", "child", "subsub")
    tree = store.tree()
    assert tree.children("root") == ("child",)
    assert tree.descendants("root") == ("child", "grandchild")
    assert tree.ancestors("grandchild") == ("child", "root")
    assert tree.get("grandchild").root_id == "root" and tree.get("grandchild").depth == 2
    with pytest.raises(ValueError, match="unknown parent"):
        _create(store, "orphan", "nope")
    with pytest.raises(ValueError, match="twice"):
        _create(store, "root")
    assert store.tree().revision == 3


def test_terminal_event_records_result_and_refuses_further_progress(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    store.append("d-1", "delegation.started", {})
    result = WorkerResult(delegation_id="d-1", status=DelegationState.COMPLETED, synthesis="proved",
                          usage=ResourceUsage(provider_calls=3, unknown=("cost_usd",)))
    store.append("d-1", "delegation.completed", {"result": result.model_dump(mode="json")})
    tree = store.tree()
    assert tree.get("d-1").result == result and tree.get("d-1").terminal
    with pytest.raises(ValueError, match="terminal"):
        store.append("d-1", "delegation.progress", {"note": "late"})
    with pytest.raises(ValueError, match="terminal"):
        store.append("d-1", "delegation.cancelled", {"reason": "late"})
    with pytest.raises(ValueError, match="unknown delegation"):
        store.append("ghost", "delegation.started", {})


def test_usage_reports_accumulate_per_delegation_and_keep_unknown(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    store.append("d-1", "usage.reported", {"usage": ResourceUsage(cost_usd=Decimal("1"), provider_calls=1).model_dump(mode="json")})
    store.append("d-1", "usage.reported", {"usage": ResourceUsage(provider_calls=1, unknown=("cost_usd",)).model_dump(mode="json")})
    used = store.tree().usage_reported["d-1"]
    assert used.provider_calls == 2 and used.cost_usd is None and used.unknown == ("cost_usd",)


def test_tampered_or_reordered_journal_is_refused(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    store.append("d-1", "delegation.started", {})
    path = tmp_path / "delegations" / "journal.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["event"]["payload"]["created_at"] = "forged"
    path.write_text("\n".join([json.dumps(first), lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="journal"):
        DelegationStore(tmp_path).tree()
    path.write_text("\n".join([lines[1], lines[0]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="journal"):
        DelegationStore(tmp_path).tree()


def test_recovery_marks_interrupted_work_unknown_not_failed_or_cancelled(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "never")
    _create(store, "running")
    store.append("running", "delegation.started", {})
    _create(store, "done")
    store.append("done", "delegation.cancelled", {"reason": "user"})
    recovered = DelegationStore(tmp_path).recover(now="2026-09-10T01:00:00+00:00")
    assert [d.id for d in recovered] == ["running"]
    tree = store.tree()
    assert tree.get("running").state is DelegationState.UNKNOWN
    assert tree.get("running").terminal_reason == "interrupted"
    assert tree.usage_reported["running"].unknown == tuple(sorted(DIMENSIONS))
    assert tree.get("never").state is DelegationState.QUEUED
    assert tree.get("done").state is DelegationState.CANCELLED
    assert tree.get("done").terminal_reason == "user"
    assert DelegationStore(tmp_path).recover(now="t2") == ()


def test_context_digests_are_recorded_on_the_delegation(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    store.append("d-1", "delegation.context", {"problem_core_digest": "c" * 64,
                                               "research_brief_digest": "d" * 64,
                                               "context_manifest_id": "m-1"})
    delegation = store.tree().get("d-1")
    assert delegation.problem_core_digest == "c" * 64
    assert delegation.research_brief_digest == "d" * 64
    assert delegation.context_manifest_id == "m-1"


def test_artifact_store_is_per_delegation_and_reopenable(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "d-1")
    run = store.artifacts("d-1")
    run.append("worker.note", {"text": "hi"}, phase="proving")
    again = DelegationStore(tmp_path).artifacts("d-1")
    assert again.path == run.path and again.trajectory_path.exists()
    assert again.path.parent == tmp_path / "delegations"
    assert store.artifacts("not-yet-journaled").path.parent == tmp_path / "delegations"   # launch precedes journal
    with pytest.raises(ValueError, match="invalid delegation id"):
        store.artifacts("../elsewhere")


def test_cancelling_a_subtree_requests_descendants_deepest_first_and_cancels_queued(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "root")
    store.append("root", "delegation.started", {})
    _create(store, "child", "root", "sub")
    store.append("child", "delegation.started", {})
    _create(store, "grandchild", "child", "subsub")           # never started: queued
    _create(store, "done", "root", "finished")
    store.append("done", "delegation.completed", {"reason": "done"})
    requested = store.cancel_subtree("root", reason="user")
    assert requested == ("grandchild", "child", "root")
    tree = store.tree()
    assert tree.get("grandchild").state is DelegationState.CANCELLED
    assert tree.get("grandchild").terminal_reason == "user"
    assert tree.get("child").state is DelegationState.ACTIVE      # the executor ends it
    assert tree.get("root").state is DelegationState.ACTIVE
    assert tree.get("done").state is DelegationState.COMPLETED
    assert tree.cancel_requested("child") and tree.cancel_requested("root")
    assert not tree.cancel_requested("done")
    # A second request is not journaled twice.
    assert store.cancel_subtree("root", reason="again") == ()


def test_release_requires_a_terminal_node(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "root")
    _create(store, "a", "root")
    store.append("a", "delegation.started", {})
    with pytest.raises(ValueError, match="terminal"):
        store.release("a")
    store.append("a", "delegation.failed", {"reason": "boom"})
    store.release("a")
    assert store.release("a") is None                             # idempotent
    assert [e.kind for e in store.events()].count("budget.released") == 1


def test_recovery_keeps_deliberately_paused_work_paused(tmp_path):
    """A pause is a durable control over queued work, never in flight, so a restart has nothing to doubt."""
    store = DelegationStore(tmp_path)
    _create(store, "p")
    store.append("p", "delegation.paused", {"by": "human"})
    assert DelegationStore(tmp_path).recover(now="t") == ()
    assert store.tree().get("p").state is DelegationState.PAUSED


def test_cancelling_paused_work_ends_it_outright_like_queued_work(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "p")
    store.append("p", "delegation.paused", {"by": "human"})
    assert store.cancel_subtree("p", reason="user") == ("p",)
    assert store.tree().get("p").state is DelegationState.CANCELLED


def test_recovery_keeps_an_interior_cell_live_since_it_ran_no_worker(tmp_path):
    store = DelegationStore(tmp_path)
    _create(store, "cell")
    store.append("cell", "delegation.started", {"interior": True})
    _create(store, "leaf")
    store.append("leaf", "delegation.started", {})
    recovered = DelegationStore(tmp_path).recover(now="t")
    assert [d.id for d in recovered] == ["leaf"]
    tree = store.tree()
    assert tree.get("cell").state is DelegationState.ACTIVE and tree.get("cell").interior
    assert not tree.get("leaf").interior
