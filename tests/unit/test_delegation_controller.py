"""The controller is nonblocking orchestration over the journal, the executor and the leases."""
import json
import threading
import time
from decimal import Decimal
from uuid import uuid4

import pytest
from delegation_helpers import ScriptedWorkerRuntime, call, seed_lemma, seed_project

from hardy.agents.executor import LocalExecutor
from hardy.agents.usage import Usage
from hardy.workflows.delegation.budget import LeaseLedger, LeaseRefused
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceLease,
)
from hardy.workflows.delegation.controller import ROOT_ID, DelegationController, RootResources
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.worker import OpenedWorker
from hardy.workflows.ledger.store import LedgerStore

FINISH = call("finish", {"status": "completed", "synthesis": "L17 closes by simp."})


def _open(script, *, gate=None, report=None, opened=None, raise_on_open=None):
    def open_worker(launch, dispatch, observe):
        if raise_on_open is not None:
            raise raise_on_open
        runtime = ScriptedWorkerRuntime(script, gate=gate, report=report, dispatch=dispatch, observe=observe)
        usage = {"value": Usage()}

        def observed(event):
            if event.get("type") == "result":
                usage["value"] = usage["value"].record(event)
            observe(event)

        runtime.observe = observed
        if opened is not None:
            opened.append(runtime)
        return OpenedWorker(context_id=f"ctx-{uuid4().hex}", runtime=runtime, usage=lambda: usage["value"])
    return open_worker


def _controller(tmp_path, open_worker, *, checks=4, slots=2, notices=None):
    seed_lemma(tmp_path)
    return DelegationController(
        DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(slots),
        open_worker=open_worker, root=RootResources(lease=ResourceLease(official_checks=checks), slots=slots),
        notify=(notices if notices is not None else []).append,
    )


def _spec(tmp_path, *, checks=1, objective="prove L17"):
    snapshot = LedgerStore(tmp_path).read()
    return DelegationSpec(objective=objective, project_refs=(snapshot.head("L17").ref,),
                          scope=snapshot.head("scope").ref, lease=ResourceLease(official_checks=checks),
                          concurrency=ConcurrencyLease(slots=1), created_by="human")


def test_delegate_returns_before_the_worker_finishes_and_completion_is_journaled(tmp_path):
    started, release = threading.Event(), threading.Event()
    notices = []
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release),
                                             report={"cost_usd": 0.5, "usage": {"input_tokens": 3, "output_tokens": 1}}),
                             notices=notices)
    try:
        delegation = controller.delegate(_spec(tmp_path))
        assert delegation.state in {DelegationState.QUEUED, DelegationState.ACTIVE}
        assert started.wait(5)
        assert controller.tree().get(delegation.id).state is DelegationState.ACTIVE
        assert notices == []
        release.set()
        done = controller.wait(delegation.id, timeout=5)
        assert done.state is DelegationState.COMPLETED
        assert done.result is not None and done.result.synthesis == "L17 closes by simp."
        assert done.problem_core_digest and done.research_brief_digest and done.context_manifest_id
        ledger = LeaseLedger(controller.tree())
        assert ledger.usage(ROOT_ID).cost_usd == Decimal("0.5")
        assert ledger.released(delegation.id)
        assert ledger.allocatable(ROOT_ID).official_checks == 4
        assert len(notices) == 1 and delegation.id in notices[0] and "L17 closes by simp." in notices[0]
        pending = controller.attention().pending("main_agent")
        assert [item.delegation_id for item in pending] == [delegation.id]
        assert controller.attention().pending("human") == ()          # notified => human receipt
        artifacts = controller.store.artifacts(delegation.id)
        assert (artifacts.path / "core.json").exists() and (artifacts.path / "manifest.json").exists()
        manifest = json.loads((artifacts.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["problem_core_digest"] == done.problem_core_digest
        assert [item["selected_by"] for item in manifest["included_items"]][0] == "mandatory"
        assert manifest["preload_budget"] > 0 and manifest["context_policy_digest"]
        prompt = (artifacts.path / "prompt.md").read_text(encoding="utf-8")
        assert "Structural map" in prompt and "Task mode: prove" in prompt and "prove L17" in prompt
    finally:
        controller.shutdown()


def test_refused_lease_leaves_the_journal_unchanged(tmp_path):
    started, release = threading.Event(), threading.Event()
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release)), checks=1)
    try:
        with pytest.raises(LeaseRefused):
            controller.delegate(_spec(tmp_path, checks=2))
        assert set(controller.tree().delegations) == {ROOT_ID}      # only the lazily created root
        before = controller.tree().revision
        with pytest.raises(LeaseRefused):
            controller.delegate(_spec(tmp_path, checks=2))
        assert controller.tree().revision == before
        first = controller.delegate(_spec(tmp_path, checks=1))
        assert started.wait(5)
        with pytest.raises(LeaseRefused):
            controller.delegate(_spec(tmp_path, checks=1))
        release.set()
        controller.wait(first.id, timeout=5)
        # A released reservation makes room again.
        second = controller.delegate(_spec(tmp_path, checks=1))
        assert controller.wait(second.id, timeout=5).state is DelegationState.COMPLETED
    finally:
        release.set()
        controller.shutdown()


def test_two_workers_share_the_root_ceiling_and_never_exceed_it(tmp_path):
    started, release = threading.Event(), threading.Event()
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release)), checks=2, slots=2)
    try:
        a = controller.delegate(_spec(tmp_path, checks=1))
        b = controller.delegate(_spec(tmp_path, checks=1))
        assert started.wait(5)
        with pytest.raises(LeaseRefused):
            controller.delegate(_spec(tmp_path, checks=1))
        release.set()
        for id in (a.id, b.id):
            assert controller.wait(id, timeout=5).state is DelegationState.COMPLETED
        assert LeaseLedger(controller.tree()).allocatable(ROOT_ID).official_checks == 2
    finally:
        controller.shutdown()


def test_cancel_ends_an_active_worker_and_releases_its_lease(tmp_path):
    started, release = threading.Event(), threading.Event()
    notices = []
    controller = _controller(tmp_path, _open(["thinking..."], gate=(started, release)), notices=notices)
    try:
        delegation = controller.delegate(_spec(tmp_path))
        assert started.wait(5)
        assert controller.cancel(delegation.id, reason="user") == (delegation.id,)
        done = controller.wait(delegation.id, timeout=5)
        assert done.state is DelegationState.CANCELLED
        assert LeaseLedger(controller.tree()).released(delegation.id)
        assert notices and "cancelled" in notices[-1]
    finally:
        controller.shutdown()


def test_cancel_of_a_queued_delegation_never_starts_it(tmp_path):
    started, release = threading.Event(), threading.Event()
    opened = []
    controller = _controller(tmp_path, _open(["thinking..."], gate=(started, release), opened=opened),
                             checks=4, slots=1)
    try:
        first = controller.delegate(_spec(tmp_path))
        second = controller.delegate(_spec(tmp_path))
        assert started.wait(5)
        assert controller.tree().get(second.id).state is DelegationState.QUEUED
        controller.cancel(second.id)
        assert controller.tree().get(second.id).state is DelegationState.CANCELLED
        release.set()
        controller.wait(first.id, timeout=5)
        controller.wait(second.id, timeout=5)
        assert len(opened) == 1
        assert LeaseLedger(controller.tree()).released(second.id)
    finally:
        controller.shutdown()


def test_worker_exception_ends_failed_and_open_failure_is_recorded(tmp_path):
    controller = _controller(tmp_path, _open([RuntimeError("provider exploded")]))
    try:
        delegation = controller.delegate(_spec(tmp_path))
        done = controller.wait(delegation.id, timeout=5)
        assert done.state is DelegationState.FAILED and "provider exploded" in done.terminal_reason
        assert LeaseLedger(controller.tree()).released(delegation.id)
    finally:
        controller.shutdown()
    broken = _controller(tmp_path, _open([], raise_on_open=OSError("no network")))
    try:
        delegation = broken.delegate(_spec(tmp_path))
        done = broken.wait(delegation.id, timeout=5)
        assert done.state is DelegationState.FAILED and "no network" in done.terminal_reason
    finally:
        broken.shutdown()


def test_recover_marks_interrupted_work_and_leaves_a_sticky_item(tmp_path):
    seed_lemma(tmp_path)
    store = DelegationStore(tmp_path)
    store.append(ROOT_ID, "delegation.created", {"spec": _spec(tmp_path, checks=4, objective="root").model_dump(mode="json"),
                                                 "parent_id": None, "created_at": "t"})
    store.append("d-old", "delegation.created", {"spec": _spec(tmp_path).model_dump(mode="json"),
                                                 "parent_id": ROOT_ID, "created_at": "t"})
    store.append("d-old", "budget.reserved", {"lease": ResourceLease(official_checks=1).model_dump(mode="json"), "slots": 1})
    store.append("d-old", "delegation.started", {})
    notices = []
    controller = DelegationController(store, LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=_open([FINISH]),
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=notices.append)
    try:
        recovered = controller.recover()
        assert [d.id for d in recovered] == ["d-old"]
        assert controller.tree().get("d-old").state is DelegationState.UNKNOWN
        assert LeaseLedger(controller.tree()).released("d-old")
        pending = controller.attention().pending("main_agent")
        assert pending and pending[0].sticky and pending[0].category == "interrupted"
        assert notices and "interrupted" in notices[0]
        assert controller.recover() == ()
    finally:
        controller.shutdown()


def test_status_and_inspect_are_derived_views(tmp_path):
    controller = _controller(tmp_path, _open([FINISH]))
    try:
        delegation = controller.delegate(_spec(tmp_path))
        controller.wait(delegation.id, timeout=5)
        status = controller.status()
        assert status["counts"]["completed"] == 1 and status["root"]["allocatable"]["official_checks"] == 4
        view = controller.inspect(delegation.id)
        assert view["delegation"]["state"] == "completed"
        assert view["usage"]["provider_calls"] == 1
        assert view["artifacts"].endswith(delegation.id)
        with pytest.raises(ValueError, match="unknown delegation"):
            controller.inspect("nope")
    finally:
        controller.shutdown()


def test_hidden_ids_in_the_spec_are_enforced_at_preload_and_retrieval(tmp_path):
    """Criterion 8 at the controller: the launch package and the worker's queries agree."""
    seed_project(tmp_path)
    script = [call("read_item", {"selector": "A1"}), call("read_item", {"selector": "L12"}),
              call("finish", {"status": "partial", "synthesis": "looked"})]
    controller = _controller(tmp_path, _open(script))
    try:
        snapshot = LedgerStore(tmp_path).read()
        spec = DelegationSpec(objective="prove L17 blind", project_refs=(snapshot.head("L17").ref,),
                              scope=snapshot.head("scope").ref, lease=ResourceLease(official_checks=1),
                              concurrency=ConcurrencyLease(slots=1), created_by="human", hidden_ids=("A1",))
        delegation = controller.delegate(spec)
        controller.wait(delegation.id, timeout=5)
        artifacts = controller.store.artifacts(delegation.id)
        prompt = (artifacts.path / "prompt.md").read_text(encoding="utf-8")
        assert "A1" not in prompt and "L12" in prompt
        events = [json.loads(line) for line in artifacts.trajectory_path.read_text().splitlines()]
        tools = [e["payload"] for e in events if e["kind"] == "tool"]
        assert not tools[0]["result"]["ok"] and "not available" in tools[0]["result"]["output"]
        assert tools[1]["result"]["ok"]
        retrieved = [e["payload"] for e in events if e["kind"] == "context.retrieved"]
        assert retrieved[0]["refused"] == ["A1"]
        manifest = json.loads((artifacts.path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["hidden_selectors"] == ["A1"]
    finally:
        controller.shutdown()


def test_worker_findings_reach_the_parent_and_a_counterexample_draws_attention(tmp_path):
    """Criterion 13 at the controller: proposed, parent-visible, not admitted, and priority-routed."""
    from hardy.workflows.delegation.findings import FindingLedger

    script = [
        call("propose_finding", {"kind": "reduction", "summary": "reduce to L12", "payload": "L17 <= L12",
                                 "related_refs": ["L17"]}),
        call("propose_finding", {"kind": "counterexample", "summary": "a conic bundle breaks L17",
                                 "payload": "the conic bundle over P1", "related_refs": ["L17"]}),
        call("finish", {"status": "completed", "synthesis": "found a counterexample"}),
    ]
    notices = []
    controller = _controller(tmp_path, _open(script), notices=notices)
    try:
        delegation = controller.delegate(_spec(tmp_path))
        done = controller.wait(delegation.id, timeout=5)
        assert len(done.result.findings) == 2
        ledger = FindingLedger(controller.store)
        visible = ledger.visible_to(ROOT_ID)
        assert [f.kind for f in visible] == ["reduction", "counterexample"]
        assert all(f.evidence_profile.value == "speculative" for f in visible)
        assert LedgerStore(tmp_path).read().revision == controller.ledger.read().revision   # nothing admitted
        items = controller.attention().pending("main_agent")
        categories = {item.category for item in items}
        assert "completion" in categories and "finding" in categories
        priority = next(item for item in items if item.category == "finding")
        assert priority.importance == "high" and "counterexample" in priority.summary
        assert any("counterexample" in text for text in notices)
    finally:
        controller.shutdown()



def test_subscriptions_route_modes_and_interrupts_reach_the_hook(tmp_path):
    """Spec 16.7: queue is silent, notify is a notice, interrupt needs a subscription and reaches the hook."""
    from hardy.workflows.delegation.attention import AttentionSubscription, DeliveryMode

    notices, interrupts = [], []
    script = [call("propose_finding", {"kind": "counterexample", "summary": "a conic bundle", "payload": "p",
                                       "related_refs": ["L17"]}),
              call("finish", {"status": "completed", "synthesis": "done"})]
    seed_lemma(tmp_path)
    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=_open(script),
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=notices.append, interrupt=interrupts.append)
    try:
        spec = _spec(tmp_path).model_copy(update={"notify_human": False})       # nothing by default
        quiet = controller.delegate(spec)
        controller.wait(quiet.id, timeout=5)
        assert notices == [] and controller.attention().pending("main_agent") == ()
        controller.subscribe(AttentionSubscription(owner="human", source=ROOT_ID, triggers=("counterexample",),
                                                   mode=DeliveryMode.INTERRUPT, recipient="both"))
        controller.subscribe(AttentionSubscription(owner="human", source=ROOT_ID, triggers=("terminal",),
                                                   mode=DeliveryMode.QUEUE, recipient="main_agent"))
        assert len(controller.subscriptions()) == 2
        loud = controller.delegate(spec)
        controller.wait(loud.id, timeout=5)
        assert len(interrupts) == 1 and interrupts[0].finding_kind == "counterexample"
        assert any("counterexample" in text for text in notices)
        kinds = [e.kind for e in controller.store.events()]
        assert "attention.interrupt_requested" in kinds and "attention.subscribed" in kinds
        receipts = controller.attention().receipts(interrupts[0].id)
        assert [r.mode for r in receipts] == ["interrupt"]
    finally:
        controller.shutdown()


def test_continuations_start_only_when_the_conversation_has_not_moved(tmp_path):
    """Criterion 37."""
    controller = _controller(tmp_path, _open([FINISH]))
    try:
        delegation = controller.delegate(_spec(tmp_path))
        controller.wait(delegation.id, timeout=5)
        cont = controller.record_continuation(delegation.id, condition="terminal", epoch="entry:a", offset=10,
                                              resume_text="now use the lemma")
        assert [c.id for c in controller.continuations()] == [cont.id]
        started = controller.resolve_continuations(epoch="entry:a", advanced_since=lambda offset: False)
        assert [c.resume_text for c in started] == ["now use the lemma"]
        assert controller.continuations() == ()
        stale = controller.record_continuation(delegation.id, condition="terminal", epoch="entry:a", offset=10,
                                               resume_text="stale plan")
        assert controller.resolve_continuations(epoch="entry:b", advanced_since=lambda offset: True) == ()
        assert controller.continuations() == ()
        items = [i for i in controller.attention().pending("main_agent") if i.category == "continuation"]
        assert len(items) == 1 and "stale plan" in items[0].summary and not items[0].sticky
        assert stale.id != cont.id
    finally:
        controller.shutdown()


def test_cancelling_a_cell_ends_it_once_its_active_child_has_settled_and_releases_both(tmp_path):
    """An interior node has no worker to end it: it becomes cancelled when its last child does, never before."""
    from hardy.workflows.delegation.contracts import SpawnPolicy

    started, release = threading.Event(), threading.Event()
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release)), checks=4, slots=1)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=3).model_copy(
            update={"spawn": SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)}))
        child = controller.delegate(_spec(tmp_path, checks=1), parent_id=cell.id)
        queued = controller.delegate(_spec(tmp_path, checks=1).model_copy(update={"concurrency": ConcurrencyLease(slots=1)}),
                                     parent_id=cell.id)
        assert started.wait(5)
        requested = controller.cancel(cell.id)
        assert set(requested) == {cell.id, child.id, queued.id}
        tree = controller.tree()
        assert tree.get(queued.id).state is DelegationState.CANCELLED
        assert tree.get(cell.id).state is DelegationState.ACTIVE            # its child is still ending
        release.set()
        controller.wait(child.id, timeout=5)
        ended = controller.wait(cell.id, timeout=5)
        assert ended.state is DelegationState.CANCELLED
        assert LeaseLedger(controller.tree()).allocatable(ROOT_ID).official_checks == 4
        empty = controller.delegate(_spec(tmp_path, checks=1).model_copy(
            update={"spawn": SpawnPolicy(can_spawn=True, max_children=1, max_depth=1)}))
        assert controller.cancel(empty.id) == (empty.id,)
        assert controller.tree().get(empty.id).state is DelegationState.CANCELLED   # nothing beneath: ends at once
    finally:
        release.set()
        controller.shutdown()


def test_a_blind_cell_hides_its_ids_from_every_descendant_at_preload_and_retrieval(tmp_path):
    """Isolation is inherited: a child spec that names nothing still cannot see what its cell hides."""
    from delegation_helpers import seed_project

    from hardy.workflows.delegation.contracts import SpawnPolicy

    seed_project(tmp_path)
    prompts = {}
    script = [call("read_item", {"selector": "L14"}), call("read_item", {"selector": "D3"}), FINISH]
    opener = _open(script)

    def open_worker(launch, dispatch, observe):
        prompts[launch.delegation_id] = launch.prompt
        return opener(launch, dispatch, observe)

    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=open_worker,
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=lambda text: None)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=2).model_copy(
            update={"hidden_ids": ("L14",), "spawn": SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)}))
        child = controller.delegate(_spec(tmp_path, checks=1), parent_id=cell.id)
        controller.wait(child.id, timeout=5)
        trajectory = [json.loads(line) for line in
                      controller.store.artifacts(child.id).trajectory_path.read_text(encoding="utf-8").splitlines()]
        tools = [e["payload"] for e in trajectory if e["kind"] == "tool"]
        assert not tools[0]["result"]["ok"] and "generic fiber is connected" not in tools[0]["result"]["output"]
        assert tools[1]["result"]["ok"]
        assert "generic fiber is connected" not in prompts[child.id] and "L14" not in prompts[child.id]
    finally:
        controller.shutdown()


def test_a_one_slot_cell_runs_its_children_one_at_a_time_whatever_the_executor_has(tmp_path):
    from hardy.workflows.delegation.contracts import SpawnPolicy

    started, release = threading.Event(), threading.Event()
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release)), checks=6, slots=4)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=4).model_copy(update={
            "spawn": SpawnPolicy(can_spawn=True, max_children=3, max_depth=1), "concurrency": ConcurrencyLease(slots=1)}))
        a = controller.delegate(_spec(tmp_path, checks=1), parent_id=cell.id)
        b = controller.delegate(_spec(tmp_path, checks=1), parent_id=cell.id)
        assert started.wait(5)
        time.sleep(0.3)
        tree = controller.tree()
        assert tree.get(a.id).state is DelegationState.ACTIVE and tree.get(b.id).state is DelegationState.QUEUED
        assert controller.status()["counts"].get("active") == 2                     # the cell and one child
        release.set()
        assert controller.wait(a.id, timeout=5).state is DelegationState.COMPLETED
        assert controller.wait(b.id, timeout=5).state is DelegationState.COMPLETED
    finally:
        release.set()
        controller.shutdown()


def test_queued_and_paused_work_survives_a_restart_from_its_persisted_launch(tmp_path):
    """A launch is persisted when it is queued, not when it starts, so a restart can relaunch it."""
    first = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    first.shutdown()                                                          # nothing can start any more
    queued = first.delegate(_spec(tmp_path, checks=1))
    paused = first.delegate(_spec(tmp_path, checks=1))
    first.pause(paused.id, by="human")
    assert (first.store.artifacts(queued.id).path / "prompt.md").exists()
    again = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    try:
        assert again.recover() == ()                                          # nothing was in flight
        assert again.wait(queued.id, timeout=5).state is DelegationState.COMPLETED
        assert again.tree().get(paused.id).state is DelegationState.PAUSED
        again.resume(paused.id, by="human")
        assert again.wait(paused.id, timeout=5).state is DelegationState.COMPLETED
    finally:
        again.shutdown()


def test_pushed_findings_reach_a_queued_recipient_in_its_prompt_and_an_active_one_at_its_next_tool_result(tmp_path):
    from delegation_helpers import seed_project

    from hardy.workflows.delegation.contracts import SpawnPolicy
    from hardy.workflows.delegation.findings import FindingLedger

    seed_project(tmp_path)
    started, release = threading.Event(), threading.Event()
    prompts = {}
    scripts = {
        "source": [call("propose_finding", {"kind": "reduction", "summary": "reduce to the special fiber",
                                            "payload": "L17 reduces to L12", "related_refs": ["L17"]}), FINISH],
        "active": [call("read_item", {"selector": "D3"}), FINISH],
        "queued": [FINISH],
    }

    def open_worker(launch, dispatch, observe):
        objective = next(k for k in scripts if f"Objective: {k}" in launch.prompt)
        prompts[launch.delegation_id] = launch.prompt
        gate = (started, release) if objective == "active" else None
        runtime = ScriptedWorkerRuntime(scripts[objective], gate=gate, dispatch=dispatch, observe=observe)
        return OpenedWorker(context_id=launch.delegation_id, runtime=runtime, usage=lambda: Usage())

    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(4),
                                      open_worker=open_worker,
                                      root=RootResources(lease=ResourceLease(official_checks=6), slots=4),
                                      notify=lambda text: None)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=4, objective="cell").model_copy(update={
            "spawn": SpawnPolicy(can_spawn=True, max_children=3, max_depth=1), "concurrency": ConcurrencyLease(slots=1)}))
        source = controller.delegate(_spec(tmp_path, checks=1, objective="source"), parent_id=cell.id)
        controller.wait(source.id, timeout=5)
        active = controller.delegate(_spec(tmp_path, checks=1, objective="active"), parent_id=cell.id)
        assert started.wait(5)
        queued = controller.delegate(_spec(tmp_path, checks=1, objective="queued"), parent_id=cell.id)
        assert controller.tree().get(queued.id).state is DelegationState.QUEUED
        ledger = FindingLedger(controller.store)
        for recipient in (active.id, queued.id):
            ledger.promote(f"{source.id}:finding:0", recipient=recipient, mode="push", selector="human",
                           authorized_by=cell.id, reason="react to this")
        release.set()
        controller.wait(active.id, timeout=5)
        controller.wait(queued.id, timeout=5)
        trajectory = [json.loads(line) for line in
                      controller.store.artifacts(active.id).trajectory_path.read_text(encoding="utf-8").splitlines()]
        tools = [e["payload"] for e in trajectory if e["kind"] == "tool"]
        assert "reduce to the special fiber" in tools[0]["result"]["output"]        # the next provider boundary
        assert "reduce to the special fiber" in prompts[queued.id]                  # the launch itself
        assert "reduce to the special fiber" not in prompts[active.id]
        consumed = [e for e in controller.store.events() if e.kind == "context.pushes_consumed"]
        assert {e.delegation_id for e in consumed} == {active.id, queued.id}
    finally:
        release.set()
        controller.shutdown()


def test_finishing_a_cell_waits_for_its_running_child_before_releasing_its_lease(tmp_path):
    """A synthesis is recorded at once, but the cell ends and its lease returns only when the child has."""
    from hardy.workflows.delegation.contracts import SpawnPolicy

    started, release = threading.Event(), threading.Event()
    controller = _controller(tmp_path, _open([FINISH], gate=(started, release)), checks=4, slots=2)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=3).model_copy(
            update={"spawn": SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)}))
        child = controller.delegate(_spec(tmp_path, checks=1), parent_id=cell.id)
        assert started.wait(5)
        pending = controller.finish_subtree(cell.id, synthesis="enough", by="human")
        assert pending.state is DelegationState.ACTIVE and controller.tree().cancel_requested(child.id)
        release.set()
        controller.wait(child.id, timeout=5)
        ended = controller.wait(cell.id, timeout=5)
        assert ended.state is DelegationState.COMPLETED and ended.result.synthesis == "enough"
        assert LeaseLedger(controller.tree()).allocatable(ROOT_ID).official_checks == 4
        kinds = [(e.delegation_id, e.kind) for e in controller.store.events()]
        assert kinds.index((cell.id, "delegation.finish_requested")) < kinds.index((child.id, "delegation.cancelled"))
        assert kinds.index((child.id, "budget.released")) < kinds.index((cell.id, "delegation.completed"))
        assert kinds.index((cell.id, "delegation.completed")) < kinds.index((cell.id, "budget.released"))
    finally:
        release.set()
        controller.shutdown()


def test_a_paused_delegation_is_cancelled_outright_and_cannot_be_resumed(tmp_path):
    controller = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    controller.shutdown()
    paused = controller.delegate(_spec(tmp_path, checks=1))
    controller.pause(paused.id, by="human")
    assert controller.cancel(paused.id) == (paused.id,)
    assert controller.tree().get(paused.id).state is DelegationState.CANCELLED
    with pytest.raises(ValueError):
        controller.resume(paused.id, by="human")


def test_the_launch_is_persisted_before_the_queued_node_is_exposed(tmp_path):
    """A crash between the journal entry and the prompt would otherwise leave a queued node nothing can relaunch."""

    class Crashing(DelegationStore):
        def append(self, delegation_id, kind, payload, *, now=None):
            if kind == "delegation.created" and delegation_id != ROOT_ID:
                assert (self.artifacts(delegation_id).path / "prompt.md").exists(), "prompt must precede the journal"
                raise RuntimeError("simulated crash at the journal")
            return super().append(delegation_id, kind, payload, now=now)

    seed_lemma(tmp_path)
    controller = DelegationController(Crashing(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=_open([FINISH]),
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=lambda text: None)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            controller.delegate(_spec(tmp_path, checks=1))
    finally:
        controller.shutdown()


def test_recovery_releases_a_terminal_reservation_the_dead_process_never_returned(tmp_path):
    seed_lemma(tmp_path)
    controller = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    controller.shutdown()
    stuck = controller.delegate(_spec(tmp_path, checks=2))
    store = controller.store
    store.append(stuck.id, "delegation.started", {})
    store.append(stuck.id, "delegation.completed", {"result": {"delegation_id": stuck.id, "status": "completed",
                                                              "synthesis": "done", "usage": {}}})
    assert not LeaseLedger(store.tree()).released(stuck.id)
    again = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    try:
        assert again.recover() == ()
        assert LeaseLedger(again.tree()).released(stuck.id)
        assert LeaseLedger(again.tree()).allocatable(ROOT_ID).official_checks == 4
    finally:
        again.shutdown()


def test_one_continuation_starts_per_invocation_and_the_rest_stay_due(tmp_path):
    controller = _controller(tmp_path, _open([FINISH]))
    try:
        first = controller.delegate(_spec(tmp_path))
        second = controller.delegate(_spec(tmp_path))
        controller.wait(first.id, timeout=5)
        controller.wait(second.id, timeout=5)
        a = controller.record_continuation(first.id, condition="terminal", epoch="e", offset=0, resume_text="use A")
        b = controller.record_continuation(second.id, condition="terminal", epoch="e", offset=0, resume_text="use B")
        started = controller.resolve_continuations(epoch="e", advanced_since=lambda offset: False, limit=1)
        assert [c.id for c in started] == [a.id] and [c.id for c in controller.continuations()] == [b.id]
        started = controller.resolve_continuations(epoch="e", advanced_since=lambda offset: False, limit=1)
        assert [c.id for c in started] == [b.id] and controller.continuations() == ()
    finally:
        controller.shutdown()


def test_a_child_may_not_take_more_than_its_parents_child_fraction(tmp_path):
    from decimal import Decimal

    from hardy.workflows.delegation.contracts import SpawnPolicy

    controller = _controller(tmp_path, _open([FINISH]), checks=8, slots=2)
    try:
        cell = controller.delegate(_spec(tmp_path, checks=4).model_copy(update={
            "spawn": SpawnPolicy(can_spawn=True, max_children=4, max_depth=1, max_child_fraction=Decimal("0.5"))}))
        with pytest.raises(ValueError, match="max_child_fraction"):
            controller.delegate(_spec(tmp_path, checks=3), parent_id=cell.id)
        child = controller.delegate(_spec(tmp_path, checks=2), parent_id=cell.id)
        assert controller.wait(child.id, timeout=5).state is DelegationState.COMPLETED
    finally:
        controller.shutdown()


def test_the_core_and_the_working_set_describe_one_revision_even_when_the_ledger_moves_between_reads(tmp_path):
    """The launch is built from one snapshot: a head that advances mid-build cannot split core from context."""
    from delegation_helpers import seed_project

    from hardy.workflows.explore import ExploreWorkflow
    from hardy.workflows.ledger import contracts as c

    seen: list[int] = []

    class Advancing(LedgerStore):
        def read(self):
            snapshot = super().read()
            seen.append(snapshot.revision)
            if len(seen) == 1:
                # Another process lands a record right after this read.
                ExploreWorkflow(LedgerStore(tmp_path)).record_item(
                    id="N-late", kind=c.ProjectItemKind.RESEARCH_NOTE, name="Late", statement="arrived mid-build")
            return snapshot

    seed_project(tmp_path)
    controller = DelegationController(DelegationStore(tmp_path), Advancing(tmp_path), executor=LocalExecutor(1),
                                      open_worker=_open([FINISH]),
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=lambda text: None)
    try:
        delegation = controller.delegate(_spec(tmp_path, checks=1))
        controller.wait(delegation.id, timeout=5)
        manifest = json.loads((controller.store.artifacts(delegation.id).path / "manifest.json").read_text(encoding="utf-8"))
        core = json.loads((controller.store.artifacts(delegation.id).path / "core.json").read_text(encoding="utf-8"))
        assert manifest["project_revision"] == core["project_revision"] == seen[0]
        assert LedgerStore(tmp_path).read().revision > seen[0]
    finally:
        controller.shutdown()


def test_an_interrupt_subscribed_by_the_human_alone_never_cancels_the_main_turn(tmp_path):
    from hardy.workflows.delegation.attention import AttentionSubscription, DeliveryMode

    notices, interrupts = [], []
    script = [call("propose_finding", {"kind": "counterexample", "summary": "a conic bundle", "payload": "p",
                                       "related_refs": ["L17"]}), FINISH]
    seed_lemma(tmp_path)
    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=_open(script),
                                      root=RootResources(lease=ResourceLease(official_checks=4), slots=1),
                                      notify=notices.append, interrupt=interrupts.append)
    try:
        spec = _spec(tmp_path).model_copy(update={"notify_human": False})
        first = controller.delegate(spec)
        controller.wait(first.id, timeout=5)
        controller.subscribe(AttentionSubscription(owner="human", source=ROOT_ID, triggers=("counterexample",),
                                                   mode=DeliveryMode.INTERRUPT, recipient="human"))
        controller.subscribe(AttentionSubscription(owner="human", source=ROOT_ID, triggers=("counterexample",),
                                                   mode=DeliveryMode.QUEUE, recipient="main_agent"))
        loud = controller.delegate(spec)
        controller.wait(loud.id, timeout=5)
        assert any("counterexample" in n for n in notices) and interrupts == []
        item = next(i for i in controller.attention().items() if i.delegation_id == loud.id and i.finding_kind == "counterexample")
        assert [r.mode for r in controller.attention().receipts(item.id)] == ["interrupt"]
        assert item.id in {i.id for i in controller.attention().pending("main_agent")}       # queued for the model
    finally:
        controller.shutdown()


def test_a_delegation_with_no_active_time_is_refused_before_anything_is_reserved(tmp_path):
    controller = _controller(tmp_path, _open([FINISH]))
    try:
        before = controller.tree().revision
        with pytest.raises(ValueError, match="active_seconds"):
            controller.delegate(_spec(tmp_path).model_copy(
                update={"lease": ResourceLease(official_checks=1, active_seconds=0.0)}))
        assert controller.tree().revision in {before, before + 2}          # at most the lazily created root
    finally:
        controller.shutdown()


def test_recovery_routes_a_terminal_event_the_dead_process_never_delivered(tmp_path):
    seed_lemma(tmp_path)
    controller = _controller(tmp_path, _open([FINISH]), checks=4, slots=1)
    controller.shutdown()
    stuck = controller.delegate(_spec(tmp_path, checks=1))
    store = controller.store
    store.append(stuck.id, "delegation.started", {})
    store.append(stuck.id, "delegation.completed", {"result": {"delegation_id": stuck.id, "status": "completed",
                                                              "synthesis": "done before the crash", "usage": {}}})
    notices = []
    again = _controller(tmp_path, _open([FINISH]), checks=4, slots=1, notices=notices)
    try:
        again.recover()
        pending = again.attention().pending("main_agent")
        assert [i.delegation_id for i in pending] == [stuck.id] and "done before the crash" in pending[0].summary
        assert any(stuck.id in n for n in notices)
        again.recover()
        assert len(again.attention().items()) == 1                              # idempotent
    finally:
        again.shutdown()
