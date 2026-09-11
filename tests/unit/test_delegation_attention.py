"""Attention is derived from events; human and model deliveries are receipted apart."""
from hardy.workflows.delegation.attention import AttentionInbox, AttentionItem
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    ResourceUsage,
    WorkerResult,
)
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef


def _spec(notify=True):
    return DelegationSpec(objective="prove L17", project_refs=(VersionRef(id="L17", digest="a" * 64),),
                          scope=VersionRef(id="scope", digest="b" * 64), lease=ResourceLease(official_checks=1),
                          concurrency=ConcurrencyLease(slots=1), created_by="human", notify_human=notify)


def _complete(store, id, *, parent=None, notify=True, status=DelegationState.COMPLETED):
    store.append(id, "delegation.created", {"spec": _spec(notify).model_dump(mode="json"),
                                            "parent_id": parent, "created_at": "t"})
    store.append(id, "delegation.started", {})
    result = WorkerResult(delegation_id=id, status=status, synthesis=f"{id} synthesis", usage=ResourceUsage())
    return store.append(id, f"delegation.{status.value}", {"result": result.model_dump(mode="json")})


def test_completion_of_a_user_created_root_derives_one_item_for_both_recipients(tmp_path):
    store = DelegationStore(tmp_path)
    event = _complete(store, "d-1")
    inbox = AttentionInbox(store)
    item = inbox.derive(event, store.tree())
    assert item is not None and item.category == "completion" and not item.sticky
    assert "d-1 synthesis" in item.summary and item.delegation_id == "d-1"
    inbox.record(item)
    assert [i.id for i in inbox.pending("human")] == [item.id]
    assert [i.id for i in inbox.pending("main_agent")] == [item.id]


def test_human_receipt_does_not_clear_the_model_and_vice_versa(tmp_path):
    store = DelegationStore(tmp_path)
    inbox = AttentionInbox(store)
    item = inbox.derive(_complete(store, "d-1"), store.tree())
    inbox.record(item)
    receipt = inbox.receipt(item.id, "human", "notify")
    assert receipt.recipient == "human" and receipt.mode == "notify"
    assert inbox.pending("human") == ()
    assert [i.id for i in inbox.pending("main_agent")] == [item.id]
    inbox.receipt(item.id, "main_agent", "queue", epoch="entry:abc", offset=42)
    assert inbox.pending("main_agent") == ()
    receipts = inbox.receipts(item.id)
    assert {r.recipient for r in receipts} == {"human", "main_agent"}
    assert next(r for r in receipts if r.recipient == "main_agent").transcript_offset == 42


def test_pending_state_survives_restart(tmp_path):
    store = DelegationStore(tmp_path)
    inbox = AttentionInbox(store)
    item = inbox.derive(_complete(store, "d-1"), store.tree())
    inbox.record(item)
    inbox.receipt(item.id, "human", "notify")
    again = AttentionInbox(DelegationStore(tmp_path))
    assert again.pending("human") == ()
    assert [i.id for i in again.pending("main_agent")] == [item.id]


def test_sticky_items_stay_pending_until_handled(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("d-1", "delegation.started", {})
    inbox = AttentionInbox(store)
    recovered = DelegationStore(tmp_path).recover(now="t2")
    event = [e for e in store.events() if e.kind == "delegation.recovered"][-1]
    item = inbox.derive(event, store.tree())
    assert item.sticky and item.actionable and item.category == "interrupted"
    inbox.record(item)
    inbox.receipt(item.id, "main_agent", "queue")
    inbox.receipt(item.id, "human", "notify")
    assert [i.id for i in inbox.pending("main_agent")] == [item.id]
    inbox.handle(item.id, by="human")
    assert inbox.pending("main_agent") == () and inbox.pending("human") == ()
    assert recovered[0].id == "d-1"


def test_progress_and_autonomous_children_derive_nothing(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("root", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("root", "delegation.started", {})
    progress = store.append("root", "delegation.progress", {"note": "still going"})
    child_done = _complete(store, "child", parent="root", notify=False)
    inbox = AttentionInbox(store)
    assert inbox.derive(progress, store.tree()) is None
    assert inbox.derive(child_done, store.tree()) is None


def test_render_for_model_is_bounded_and_counts_the_rest(tmp_path):
    store = DelegationStore(tmp_path)
    inbox = AttentionInbox(store)
    items = []
    for n in range(7):
        item = inbox.derive(_complete(store, f"d-{n}"), store.tree())
        inbox.record(item)
        items.append(item)
    text = inbox.render_for_model(inbox.pending("main_agent"), budget_items=2)
    assert text.startswith("[Hardy delegation attention")
    assert "d-0" in text and "d-1" in text and "d-6" not in text
    assert "5 other" in text
    assert inbox.render_for_model(()) == ""


def test_attention_item_ids_are_deterministic_per_source_event(tmp_path):
    store = DelegationStore(tmp_path)
    inbox = AttentionInbox(store)
    event = _complete(store, "d-1")
    first = inbox.derive(event, store.tree())
    second = AttentionInbox(store).derive(event, store.tree())
    assert isinstance(first, AttentionItem) and first.id == second.id
    inbox.record(first)
    inbox.record(second)                               # recording twice is one item
    assert len(inbox.pending("human")) == 1
