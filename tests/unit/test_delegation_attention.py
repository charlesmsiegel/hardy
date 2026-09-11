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


# -- routing, subscriptions, coalescing, continuations (spec section 16) ------------------

from hardy.workflows.delegation.attention import (  # noqa: E402
    AttentionSubscription,
    DeliveryMode,
    MainContinuation,
    resolve_continuation,
    route,
)
from hardy.workflows.delegation.findings import Finding  # noqa: E402


def _tree_with_cell(store):
    store.append("root", "delegation.created", {"spec": _spec(notify=False).model_dump(mode="json"),
                                                "parent_id": None, "created_at": "t"})
    cell_spec = _spec(notify=True).model_copy(update={"objective": "the cell"})
    store.append("cell", "delegation.created", {"spec": cell_spec.model_dump(mode="json"),
                                                "parent_id": "root", "created_at": "t"})
    store.append("cell", "delegation.started", {})
    child_spec = _spec(notify=False).model_copy(update={"objective": "a route"})
    store.append("w1", "delegation.created", {"spec": child_spec.model_dump(mode="json"),
                                              "parent_id": "cell", "created_at": "t"})
    store.append("w1", "delegation.started", {})
    return store


def _finding_event(store, source, kind, n=0):
    finding = Finding(id=f"{source}:finding:{n}", source_delegation=source, kind=kind, summary=f"{kind} found",
                      payload="p", sequence=n)
    return store.append(source, "finding.proposed", {"finding": finding.model_dump(mode="json")})


def test_default_routing_follows_the_hierarchy(tmp_path):
    store = _tree_with_cell(DelegationStore(tmp_path))
    progress = store.append("w1", "delegation.progress", {"note": "thinking"})
    assert route(progress, store.tree(), ()) is None                                   # leaf progress: local
    child_done = store.append("w1", "delegation.completed", {"reason": "done"})
    assert route(child_done, store.tree(), ()) is None                                 # deep child: parent only
    counter = _finding_event(store, "cell", "counterexample")
    item, mode = route(counter, store.tree(), ())
    assert item.category == "finding" and mode is DeliveryMode.NOTIFY and not item.sticky   # notify, never interrupt
    decision = store.append("cell", "coordinator.human_decision_requested", {"question": "go on?", "by": "c"})
    item, mode = route(decision, store.tree(), ())
    assert item.sticky and item.actionable and mode is DeliveryMode.NOTIFY
    done = store.append("cell", "delegation.completed", {"reason": "finished"})
    item, mode = route(done, store.tree(), ())
    assert item.category == "completion" and mode is DeliveryMode.NOTIFY


def test_subscriptions_override_defaults_but_interrupt_needs_an_explicit_one(tmp_path):
    store = _tree_with_cell(DelegationStore(tmp_path))
    interrupt = AttentionSubscription(owner="human", source="cell", triggers=("counterexample",),
                                      mode=DeliveryMode.INTERRUPT, recipient="both")
    counter = _finding_event(store, "w1", "counterexample")
    assert route(counter, store.tree(), ()) is None                                    # a deep child by default
    item, mode = route(counter, store.tree(), (interrupt,))
    assert mode is DeliveryMode.INTERRUPT and item.delegation_id == "w1" and item.importance == "high"
    other = _finding_event(store, "w1", "reduction", n=1)
    assert route(other, store.tree(), (interrupt,)) is None                            # trigger class matters
    quiet = AttentionSubscription(owner="human", source="cell", triggers=("terminal",), mode=DeliveryMode.QUEUE,
                                  recipient="main_agent")
    child_done = store.append("w1", "delegation.completed", {"reason": "done"})
    assert route(child_done, store.tree(), (quiet,)) is None                           # not individual workers
    cell_done = store.append("cell", "delegation.completed", {"reason": "done"})
    item, mode = route(cell_done, store.tree(), (quiet,))
    assert mode is DeliveryMode.QUEUE                                                   # the subscription's mode wins
    expired = AttentionSubscription(owner="human", source="cell", triggers=("counterexample",),
                                    mode=DeliveryMode.INTERRUPT, recipient="both", expires_on="terminal")
    late = _finding_event(store, "w1", "counterexample", n=2)
    assert route(late, store.tree(), (expired,)) is None                               # source is terminal: expired


def test_routine_progress_coalesces_to_the_terminal_item_without_losing_provenance(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None,
                                               "created_at": "t"})
    store.append("d-1", "delegation.started", {})
    inbox = AttentionInbox(store)
    reduction = _finding_event(store, "d-1", "verified_lemma")
    inbox.record(inbox.derive(reduction, store.tree()))
    failed = store.append("d-1", "delegation.progress", {"note": "proof attempt failed; repairing"})
    assert inbox.derive(failed, store.tree()) is None
    done = store.append("d-1", "delegation.completed", {"result": WorkerResult(
        delegation_id="d-1", status=DelegationState.COMPLETED, synthesis="verified",
        usage=ResourceUsage()).model_dump(mode="json")})
    inbox.record(inbox.derive(done, store.tree()))
    assert len(inbox.pending("main_agent")) == 2
    current = inbox.coalesced_pending("main_agent")
    assert len(current) == 1 and current[0].category == "completion"
    assert current[0].supersedes == (f"attention:{reduction.sequence}",)
    assert len(inbox.items()) == 2                                                      # the derived events remain
    text = inbox.render_for_model(current)
    assert "verified" in text and "1 item" in text


def test_contradictions_and_high_priority_findings_are_never_coalesced_away(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None,
                                               "created_at": "t"})
    store.append("d-1", "delegation.started", {})
    inbox = AttentionInbox(store)
    counter = _finding_event(store, "d-1", "counterexample")
    inbox.record(inbox.derive(counter, store.tree()))
    verified = _finding_event(store, "d-1", "verified_lemma", n=1)
    inbox.record(inbox.derive(verified, store.tree()))
    done = store.append("d-1", "delegation.completed", {"reason": "done"})
    inbox.record(inbox.derive(done, store.tree()))
    current = inbox.coalesced_pending("main_agent")
    assert {item.category for item in current} == {"finding", "completion"}
    kept = [item for item in current if item.category == "finding"]
    assert len(kept) == 2 and all(item.importance == "high" for item in kept)


def test_a_stale_continuation_becomes_a_queued_item_not_a_response(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None,
                                               "created_at": "t"})
    continuation = MainContinuation(id="cont-1", awaiting="d-1", condition="terminal", conversation_epoch="entry:a",
                                    transcript_offset=100, resume_text="carry on with the lemma", created_at="t")
    assert resolve_continuation(continuation, epoch="entry:a", advanced=False) == "start"
    assert resolve_continuation(continuation, epoch="entry:b", advanced=False) == "stale"
    assert resolve_continuation(continuation, epoch="entry:a", advanced=True) == "stale"
    inbox = AttentionInbox(store)
    event = store.append("d-1", "continuation.stale", {"continuation": continuation.model_dump(mode="json")})
    item = inbox.derive(event, store.tree())
    assert item is not None and item.category == "continuation" and not item.sticky
    assert "carry on" in item.summary


def test_a_subscription_recipient_narrows_who_hears(tmp_path):
    """A model-only subscription never notifies the human; a human-only one never reaches the model."""
    store = _tree_with_cell(DelegationStore(tmp_path))
    model_only = AttentionSubscription(owner="human", source="cell", triggers=("reduction",),
                                       mode=DeliveryMode.QUEUE, recipient="main_agent")
    item, mode = route(_finding_event(store, "w1", "reduction"), store.tree(), (model_only,))
    assert item.recipients == ("main_agent",) and mode is DeliveryMode.QUEUE
    human_only = AttentionSubscription(owner="human", source="cell", triggers=("terminal",),
                                       mode=DeliveryMode.NOTIFY, recipient="human")
    done = store.append("cell", "delegation.completed", {"reason": "finished"})
    item, mode = route(done, store.tree(), (human_only,))
    assert item.recipients == ("human",)
    inbox = AttentionInbox(store)
    inbox.record(item)
    assert inbox.pending("main_agent") == () and [i.id for i in inbox.pending("human")] == [item.id]
    both = AttentionSubscription(owner="human", source="cell", triggers=("terminal",),
                                 mode=DeliveryMode.NOTIFY, recipient="main_agent")
    item, _ = route(done, store.tree(), (human_only, both))
    assert item.recipients == ("human", "main_agent")


def test_mixed_subscriptions_keep_a_delivery_mode_per_recipient(tmp_path):
    store = _tree_with_cell(DelegationStore(tmp_path))
    quiet_human = AttentionSubscription(owner="human", source="cell", triggers=("counterexample",),
                                        mode=DeliveryMode.QUEUE, recipient="human")
    loud_model = AttentionSubscription(owner="human", source="cell", triggers=("counterexample",),
                                       mode=DeliveryMode.INTERRUPT, recipient="main_agent")
    item, mode = route(_finding_event(store, "w1", "counterexample"), store.tree(), (quiet_human, loud_model))
    assert mode is DeliveryMode.INTERRUPT and item.recipients == ("human", "main_agent")
    assert item.mode_for("human") is DeliveryMode.QUEUE and item.mode_for("main_agent") is DeliveryMode.INTERRUPT


def test_a_terminal_summary_keeps_a_bounded_excerpt_of_the_synthesis(tmp_path):
    from hardy.workflows.delegation.attention import SUMMARY_EXCERPT_CHARS

    store = DelegationStore(tmp_path)
    store.append("d-long", "delegation.created", {"spec": _spec(True).model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("d-long", "delegation.started", {})
    result = WorkerResult(delegation_id="d-long", status=DelegationState.COMPLETED, synthesis="x" * 50_000,
                          usage=ResourceUsage())
    event = store.append("d-long", "delegation.completed", {"result": result.model_dump(mode="json")})
    item = AttentionInbox(store).derive(event, store.tree())
    assert len(item.summary) < SUMMARY_EXCERPT_CHARS + 200 and "result.json" in " ".join(item.detail_refs)
