"""The scheduler is mechanical: it enforces readiness, leases, slots, pins and lanes; it never judges mathematics.

Criteria 16, 17: fewer physical slots than runnable leaves; pins and an
exploration floor are constraints, not suggestions; tranches are granted and
reclaimed inside inherited ceilings; graph urgency and stall signals order
work without becoming evidence.
"""
from decimal import Decimal

from delegation_helpers import seed_project

from hardy.workflows.delegation.budget import LeaseLedger
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceDelta,
    ResourceLease,
    ResourceUsage,
)
from hardy.workflows.delegation.findings import Finding, FindingLedger
from hardy.workflows.delegation.scheduler import (
    AllocationRequest,
    GraphUrgency,
    Lane,
    Pin,
    PortfolioConstraints,
    Scheduler,
    default_lane,
    graph_urgency,
)
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef
from hardy.workflows.ledger.store import LedgerStore

L17 = VersionRef(id="L17", digest="a" * 64)


def _spec(objective="prove L17", task_mode="prove", checks=1, independence="shared", lane=None):
    return DelegationSpec(objective=objective, project_refs=(L17,), scope=VersionRef(id="scope", digest="b" * 64),
                          task_mode=task_mode, lease=ResourceLease(official_checks=checks),
                          concurrency=ConcurrencyLease(slots=1), created_by="human",
                          **({"lane": lane} if lane else {}))


def _store(tmp_path, *leaves, root_checks=10, root_slots=2):
    """root plus leaves (id, spec) all queued and reserved."""
    store = DelegationStore(tmp_path)
    root = DelegationSpec(objective="root", project_refs=(), scope=VersionRef(id="scope", digest="b" * 64),
                          lease=ResourceLease(official_checks=root_checks),
                          concurrency=ConcurrencyLease(slots=root_slots), created_by="session", notify_human=False)
    store.append("root", "delegation.created", {"spec": root.model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("root", "budget.reserved", {"lease": root.lease.model_dump(mode="json"), "slots": root_slots})
    for id, spec in leaves:
        store.append(id, "delegation.created", {"spec": spec.model_dump(mode="json"), "parent_id": "root", "created_at": "t"})
        store.append(id, "budget.reserved", {"lease": spec.lease.model_dump(mode="json"), "slots": 1})
    return store


def _scheduler(store, *, constraints=None, pins=()):
    tree = store.tree()
    return Scheduler(tree, LeaseLedger(tree), constraints=constraints or PortfolioConstraints(), pins=tuple(pins))


def test_default_lane_follows_the_brief_not_a_score():
    assert default_lane(_spec()) is Lane.EXPLOIT
    assert default_lane(_spec(task_mode="refute")) is Lane.VERIFY
    assert default_lane(_spec(task_mode="adjudicate")) is Lane.VERIFY
    assert default_lane(_spec(lane="explore")) is Lane.EXPLORE
    assert default_lane(_spec(lane="user_pinned")) is Lane.USER_PINNED


def test_ready_excludes_started_terminal_forbidden_and_exhausted_work(tmp_path):
    store = _store(tmp_path, ("a", _spec()), ("b", _spec()), ("c", _spec()), ("d", _spec()), root_checks=3)
    store.append("a", "delegation.started", {})
    store.append("b", "delegation.cancelled", {"reason": "user"})
    ready = _scheduler(store, pins=(Pin(delegation_id="c", kind="forbid_spend", by="human"),)).ready()
    assert [d.id for d in ready] == ["d"]
    store.append("a", "usage.reported", {"usage": ResourceUsage(official_checks=3).model_dump(mode="json")})
    assert _scheduler(store).ready() == ()                     # root exhausted: nothing starts anywhere


def test_fewer_slots_than_runnable_leaves_and_pins_come_first(tmp_path):
    store = _store(tmp_path, ("a", _spec()), ("b", _spec()), ("c", _spec()))
    scheduler = _scheduler(store, pins=(Pin(delegation_id="c", kind="reinforce", by="human"),))
    assert [d.id for d in scheduler.ready()] == ["a", "b", "c"]
    assert [d.id for d in scheduler.choose(1)] == ["c"]
    assert [d.id for d in scheduler.choose(2)] == ["c", "a"]
    assert scheduler.choose(0) == ()


def test_exploration_floor_keeps_a_slot_for_non_consensus_work(tmp_path):
    store = _store(tmp_path, ("x1", _spec()), ("x2", _spec()), ("x3", _spec()),
                   ("e1", _spec(lane="explore")), root_slots=4)
    chosen = _scheduler(store, constraints=PortfolioConstraints(exploration_floor=Decimal("0.25"))).choose(2)
    assert "e1" in [d.id for d in chosen] and len(chosen) == 2
    collapsed = _scheduler(store, constraints=PortfolioConstraints(exploration_floor=Decimal("0.25"),
                                                                    collapse_exploration=True)).choose(2)
    assert [d.id for d in collapsed] == ["x1", "x2"]


def test_blocker_urgency_orders_within_a_lane_but_is_not_evidence(tmp_path):
    store = _store(tmp_path, ("a", _spec(lane="blocker")), ("b", _spec(lane="blocker")))
    urgency = {"a": GraphUrgency(blocked_downstream=1, sole_blocker=False, distance_to_goal=3,
                                 widely_reused_unverified=False),
               "b": GraphUrgency(blocked_downstream=4, sole_blocker=True, distance_to_goal=1,
                                 widely_reused_unverified=False)}
    tree = store.tree()
    scheduler = Scheduler(tree, LeaseLedger(tree), constraints=PortfolioConstraints(), pins=(),
                          urgency=lambda delegation: urgency[delegation.id])
    assert [d.id for d in scheduler.choose(2)] == ["b", "a"]
    assert urgency["b"].score > urgency["a"].score
    assert "evidence" not in GraphUrgency.model_fields


def test_graph_urgency_is_read_from_the_ledger_graph(tmp_path):
    heads = seed_project(tmp_path)
    snapshot = LedgerStore(tmp_path).read()
    l12 = graph_urgency(snapshot, heads["L12"].ref)
    assert l12.blocked_downstream == 2                      # L17 and T1 sit above L12
    assert l12.sole_blocker                                  # its open PROVE is the only blocker on L17
    d3 = graph_urgency(snapshot, heads["D3"].ref)
    assert d3.blocked_downstream >= 4 and not d3.sole_blocker
    assert graph_urgency(snapshot, heads["T1"].ref).blocked_downstream == 0


def test_tranche_grants_stay_inside_the_parent_and_reclaim_never_below_usage(tmp_path):
    store = _store(tmp_path, ("a", _spec(checks=2)), ("b", _spec(checks=2)), root_checks=6)
    store.append("a", "delegation.started", {})
    store.append("a", "usage.reported", {"usage": ResourceUsage(official_checks=1).model_dump(mode="json")})
    scheduler = _scheduler(store)
    more = scheduler.decide(AllocationRequest(delegation_id="a", lane=Lane.EXPLOIT,
                                              tranche=ResourceDelta(official_checks=2), slots=0,
                                              requested_by="coordinator", reason="promising"))
    assert more.granted == ResourceDelta(official_checks=2) and more.refused_because == ()
    assert more.prior.official_checks == 2 and more.resulting.official_checks == 4
    too_much = scheduler.decide(AllocationRequest(delegation_id="a", lane=Lane.EXPLOIT,
                                                  tranche=ResourceDelta(official_checks=3), slots=0,
                                                  requested_by="coordinator", reason="greedy"))
    assert too_much.granted is None and any("official_checks" in why for why in too_much.refused_because)
    reclaim = scheduler.decide(AllocationRequest(delegation_id="a", lane=Lane.EXPLOIT,
                                                 tranche=ResourceDelta(official_checks=-1), slots=0,
                                                 requested_by="coordinator", reason="probe done"))
    assert reclaim.granted == ResourceDelta(official_checks=-1) and reclaim.resulting.official_checks == 1
    below_usage = scheduler.decide(AllocationRequest(delegation_id="a", lane=Lane.EXPLOIT,
                                                     tranche=ResourceDelta(official_checks=-2), slots=0,
                                                     requested_by="coordinator", reason="too far"))
    assert below_usage.granted is None and any("usage" in why for why in below_usage.refused_because)
    forbidden = _scheduler(store, pins=(Pin(delegation_id="a", kind="forbid_spend", by="human"),)).decide(
        AllocationRequest(delegation_id="a", lane=Lane.EXPLOIT, tranche=ResourceDelta(official_checks=1),
                          slots=0, requested_by="coordinator", reason="ignore the human"))
    assert forbidden.granted is None and any("forbid" in why for why in forbidden.refused_because)


def test_stall_signals_from_duplicate_findings_and_dead_subtrees(tmp_path):
    store = _store(tmp_path, ("a", _spec()), ("b", _spec()))
    store.append("a", "delegation.started", {})
    ledger = FindingLedger(store)
    for n in range(3):
        ledger.propose(Finding(id=f"a:finding:{n}", source_delegation="a", kind="note", summary="same",
                               payload="the same observation again", sequence=n))
    store.append("a", "delegation.partial", {"reason": "stuck"})
    store.append("b", "delegation.failed", {"reason": "boom"})
    stalls = {s.delegation_id: s.reasons for s in _scheduler(store).stalls(ledger)}
    assert any("duplicate" in reason for reason in stalls["a"])
    assert any("terminal" in reason for reason in stalls["root"])


def test_decisions_and_pins_are_journaled_by_the_controller(tmp_path):
    """Provenance: action, target, delta, selector, reason, prior and resulting allocation."""
    import threading

    from delegation_helpers import ScriptedWorkerRuntime, call

    from hardy.agents.executor import LocalExecutor
    from hardy.agents.usage import Usage
    from hardy.workflows.delegation.controller import ROOT_ID, DelegationController, RootResources
    from hardy.workflows.delegation.worker import OpenedWorker

    heads = seed_project(tmp_path)
    started, release = threading.Event(), threading.Event()
    opened = []

    def open_worker(launch, dispatch, observe):
        runtime = ScriptedWorkerRuntime([call("finish", {"status": "completed", "synthesis": "ok"})],
                                        gate=(started, release), dispatch=dispatch, observe=observe)
        opened.append(launch.delegation_id)
        return OpenedWorker(context_id=launch.delegation_id, runtime=runtime, usage=lambda: Usage())

    controller = DelegationController(DelegationStore(tmp_path), LedgerStore(tmp_path), executor=LocalExecutor(1),
                                      open_worker=open_worker,
                                      root=RootResources(lease=ResourceLease(official_checks=6), slots=1),
                                      notify=lambda text: None)
    try:
        snapshot = LedgerStore(tmp_path).read()

        def spec(objective):
            return DelegationSpec(objective=objective, project_refs=(heads["L17"].ref,),
                                  scope=snapshot.head("scope").ref, lease=ResourceLease(official_checks=1),
                                  concurrency=ConcurrencyLease(slots=1), created_by="human")

        first = controller.delegate(spec("first"))
        assert started.wait(5)
        second = controller.delegate(spec("second"))
        third = controller.delegate(spec("third"))
        controller.pin(third.id, "reinforce", by="human")
        controller.pin(second.id, "forbid_spend", by="human")
        assert controller.tree().get(second.id).state is DelegationState.QUEUED
        decision = controller.reinforce(first.id, ResourceDelta(official_checks=1), by="human", reason="more")
        assert decision.granted is not None
        assert LeaseLedger(controller.tree()).reserved(first.id).official_checks == 2
        release.set()
        controller.wait(first.id, timeout=5)
        controller.wait(third.id, timeout=5)
        assert opened == [first.id, third.id]                      # the pinned one ran; the forbidden one waits
        assert controller.tree().get(second.id).state is DelegationState.QUEUED
        controller.unpin(second.id, "forbid_spend", by="human")
        controller.wait(second.id, timeout=5)
        kinds = [e.kind for e in controller.store.events()]
        assert "scheduler.decision" in kinds and "scheduler.pin" in kinds and "scheduler.unpin" in kinds
        assert controller.pins() == ((third.id, "reinforce"),)
        assert LeaseLedger(controller.tree()).allocatable(ROOT_ID).official_checks == 6
    finally:
        controller.shutdown()


def test_min_attention_and_reserve_exploration_pins_change_what_is_chosen(tmp_path):
    from hardy.workflows.delegation.contracts import SpawnPolicy

    store = _store(tmp_path, ("x1", _spec()), ("x2", _spec()), ("x3", _spec()),
                   ("e1", _spec(lane="explore")), root_slots=4)
    cell = _spec(objective="cell").model_copy(update={"spawn": SpawnPolicy(can_spawn=True, max_children=2, max_depth=1)})
    store.append("cell", "delegation.created", {"spec": cell.model_dump(mode="json"), "parent_id": "root", "created_at": "t"})
    store.append("cell", "budget.reserved", {"lease": cell.lease.model_dump(mode="json"), "slots": 2})
    store.append("cell", "delegation.started", {})
    for id in ("c1", "c2"):
        store.append(id, "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": "cell", "created_at": "z"})
        store.append(id, "budget.reserved", {"lease": _spec().lease.model_dump(mode="json"), "slots": 1})
    quiet = PortfolioConstraints(exploration_floor=0, verify_floor=0)
    assert [d.id for d in _scheduler(store, constraints=quiet).choose(2)] == ["x1", "x2"]
    attention = _scheduler(store, constraints=quiet, pins=(Pin(delegation_id="cell", kind="min_attention", by="human", value=2),))
    assert [d.id for d in attention.choose(2)] == ["c1", "c2"]                     # the cell's subtree comes first
    one = _scheduler(store, constraints=quiet, pins=(Pin(delegation_id="cell", kind="min_attention", by="human", value=1),))
    assert [d.id for d in one.choose(2)] == ["c1", "x1"]
    explore = _scheduler(store, constraints=quiet, pins=(Pin(delegation_id="root", kind="reserve_exploration", by="human", value=1),))
    assert [d.id for d in explore.choose(2)] == ["e1", "x1"]                       # one slot held for exploration


def test_a_lane_floor_counts_the_workers_already_running_in_it(tmp_path):
    """Three exploration workers active against a floor of two: the freed slot goes to exploitation."""
    store = _store(tmp_path, ("e1", _spec(lane="explore")), ("e2", _spec(lane="explore")),
                   ("e3", _spec(lane="explore")), ("e4", _spec(lane="explore")), ("x1", _spec()), root_slots=4)
    for id in ("e1", "e2", "e3"):
        store.append(id, "delegation.started", {})
    floors = PortfolioConstraints(exploration_floor=Decimal("0.5"), verify_floor=0)
    assert [d.id for d in _scheduler(store, constraints=floors).choose(1)] == ["x1"]
    store.append("e3", "delegation.completed", {"result": {"delegation_id": "e3", "status": "completed",
                                                          "synthesis": "", "usage": {}}})
    assert [d.id for d in _scheduler(store, constraints=floors).choose(1)] == ["x1"]     # two still meet the floor
    store.append("e2", "delegation.completed", {"result": {"delegation_id": "e2", "status": "completed",
                                                          "synthesis": "", "usage": {}}})
    assert [d.id for d in _scheduler(store, constraints=floors).choose(1)] == ["e4"]     # now one short
