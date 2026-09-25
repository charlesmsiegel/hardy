"""Leases are ancestor-bounded; usage rolls up; unknown spend is liability."""
from decimal import Decimal

import pytest

from hardy.workflows.delegation.budget import LeaseLedger, LeaseRefused, grant
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    ResourceUsage,
)
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef


def _spec(lease):
    return DelegationSpec(objective="prove", project_refs=(VersionRef(id="L", digest="a" * 64),),
                          scope=VersionRef(id="scope", digest="b" * 64),
                          lease=ResourceLease(**lease), concurrency=ConcurrencyLease(slots=1),
                          created_by="human")


def _tree(store, *edges, slots=1):
    """edges: (id, parent, lease_kwargs)."""
    for id, parent, lease in edges:
        store.append(id, "delegation.created", {"spec": _spec(lease).model_dump(mode="json"),
                                                "parent_id": parent, "created_at": "t"})
        store.append(id, "budget.reserved", {"lease": ResourceLease(**lease).model_dump(mode="json"),
                                             "slots": slots})
    return store.tree()


def _use(store, id, **usage):
    store.append(id, "usage.reported", {"usage": ResourceUsage(**usage).model_dump(mode="json")})


def test_child_reservation_cannot_exceed_parent_allocatable(tmp_path):
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4, "cost_usd": Decimal("10")}),
                 ("a", "root", {"official_checks": 3, "cost_usd": Decimal("6")}))
    ledger = LeaseLedger(tree)
    assert ledger.allocatable("root") == ResourceLease(official_checks=1, cost_usd=Decimal("4"))
    with pytest.raises(LeaseRefused, match="official_checks"):
        grant(tree, "root", ResourceLease(official_checks=2, cost_usd=Decimal("1")), requested_slots=0)
    lease, slots = grant(tree, "root", ResourceLease(official_checks=1, cost_usd=Decimal("4")),
                         requested_slots=0)
    assert lease == ResourceLease(official_checks=1, cost_usd=Decimal("4")) and slots is None


def test_siblings_share_one_parent_lease_and_the_second_is_refused_not_clipped(tmp_path):
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4}), ("a", "root", {"official_checks": 3}))
    with pytest.raises(LeaseRefused):
        grant(tree, "root", ResourceLease(official_checks=3), requested_slots=0)
    assert store.tree().revision == tree.revision


def test_unbounded_request_under_bounded_parent_is_refused(tmp_path):
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4}))
    with pytest.raises(LeaseRefused, match="official_checks"):
        grant(tree, "root", ResourceLease(), requested_slots=0)


def test_grandchild_usage_rolls_up_to_child_and_root(tmp_path):
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 10, "cost_usd": Decimal("10")}),
          ("a", "root", {"official_checks": 6, "cost_usd": Decimal("6")}),
          ("a1", "a", {"official_checks": 2, "cost_usd": Decimal("2")}))
    _use(store, "a1", official_checks=2, cost_usd=Decimal("1.5"), provider_calls=3)
    _use(store, "a", official_checks=1, cost_usd=Decimal("0.5"), provider_calls=1)
    ledger = LeaseLedger(store.tree())
    assert ledger.usage("a1").official_checks == 2
    assert ledger.usage("a") == ResourceUsage(official_checks=3, cost_usd=Decimal("2.0"), provider_calls=4)
    assert ledger.usage("root") == ledger.usage("a")


def test_unknown_grandchild_usage_is_unknown_at_root_and_blocks_that_dimension(tmp_path):
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 10, "cost_usd": Decimal("10")}),
          ("a", "root", {"official_checks": 6, "cost_usd": Decimal("6")}),
          ("a1", "a", {"official_checks": 2, "cost_usd": Decimal("2")}))
    _use(store, "a1", provider_calls=1, unknown=("cost_usd",))
    ledger = LeaseLedger(store.tree())
    assert ledger.usage("root").cost_usd is None and "cost_usd" in ledger.usage("root").unknown
    # The child's own remaining reservation is bounded; its direct usage is unknown, so
    # nothing further may be promised in that dimension by the node that spent it.
    assert ledger.allocatable("a1").cost_usd == Decimal("0")
    assert ledger.allocatable("a1").official_checks == 2
    # Siblings' reservations remain the parent's bound; unknown usage does not manufacture room.
    assert ledger.allocatable("a").cost_usd == Decimal("4")


def test_released_reservation_returns_to_parent(tmp_path):
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4}), ("a", "root", {"official_checks": 3}))
    assert LeaseLedger(tree).allocatable("root").official_checks == 1
    store.append("a", "delegation.completed", {"reason": "done"})
    store.append("a", "budget.released", {})
    assert LeaseLedger(store.tree()).allocatable("root").official_checks == 4


def test_root_exhaustion_is_visible_at_every_descendant(tmp_path):
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 2}), ("a", "root", {"official_checks": 2}),
          ("a1", "a", {"official_checks": 1}))
    _use(store, "a", official_checks=2)
    ledger = LeaseLedger(store.tree())
    assert ledger.exhausted("root") == ("official_checks",)
    assert ledger.exhausted("a1") == ("official_checks",)
    with pytest.raises(LeaseRefused, match="exhausted"):
        grant(store.tree(), "a1", ResourceLease(official_checks=0), requested_slots=0)


def test_concurrency_slots_count_active_work_and_bound_a_child_request(tmp_path):
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 9}), slots=2)
    tree = _tree(store, ("a", "root", {"official_checks": 1}), ("b", "root", {"official_checks": 1}))
    ledger = LeaseLedger(tree)
    assert ledger.slots("root") == 2 and ledger.slots_in_use("root") == 0      # queued work holds no slot
    store.append("a", "delegation.started", {})
    store.append("b", "delegation.started", {})
    ledger = LeaseLedger(store.tree())
    assert ledger.slots_in_use("root") == 2 and ledger.slots_available("root") == 0
    with pytest.raises(LeaseRefused, match="slots"):
        grant(store.tree(), "root", ResourceLease(official_checks=1), requested_slots=3)
    # More runnable leaves than slots is allowed; the scheduler decides who runs.
    _, slots = grant(store.tree(), "root", ResourceLease(official_checks=1), requested_slots=1)
    assert slots == ConcurrencyLease(slots=1)
    store.append("a", "delegation.cancelled", {"reason": "user"})
    assert LeaseLedger(store.tree()).slots_available("root") == 1


def _finish(store, id, **usage):
    from hardy.workflows.delegation.contracts import WorkerResult
    store.append(id, "delegation.started", {})
    _use(store, id, **usage)
    result = WorkerResult(delegation_id=id, status=DelegationState.COMPLETED, synthesis="done",
                          usage=ResourceUsage(**usage))
    store.append(id, "delegation.completed", {"result": result.model_dump(mode="json")})
    store.release(id)


def test_a_released_child_still_counts_what_it_consumed_against_the_parent(tmp_path):
    """Releasing a reservation returns what was not spent; what was spent is gone for good."""
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4}), ("a", "root", {"official_checks": 1}))
    _finish(store, "a", official_checks=1)
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=3)
    with pytest.raises(LeaseRefused, match="official_checks"):
        grant(store.tree(), "root", ResourceLease(official_checks=4), requested_slots=0)
    grant(store.tree(), "root", ResourceLease(official_checks=3), requested_slots=0)
    assert tree.revision < store.tree().revision


def test_a_released_child_with_unknown_usage_leaves_that_dimension_unallocatable(tmp_path):
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 4, "tokens": 1000}), ("a", "root", {"official_checks": 1, "tokens": 100}))
    store.append("a", "delegation.started", {})
    store.append("a", "usage.reported", {"usage": ResourceUsage(official_checks=1, unknown=("tokens",)).model_dump(mode="json")})
    store.append("a", "delegation.completed", {"result": {"delegation_id": "a", "status": "completed", "synthesis": "",
                                                          "usage": ResourceUsage(unknown=("tokens",)).model_dump(mode="json")}})
    store.release("a")
    assert LeaseLedger(store.tree()).allocatable("root") == ResourceLease(official_checks=3, tokens=0)


def test_two_processes_that_both_passed_grant_cannot_both_record_their_reservation(tmp_path):
    """The check is repeated inside the journal lock at append time, so the second recorder is refused."""
    first, second = DelegationStore(tmp_path), DelegationStore(tmp_path)
    _tree(first, ("root", None, {"official_checks": 4}))
    stale = first.tree()                                     # both processes read the same balance
    for store, id in ((first, "a"), (second, "b")):
        lease, _ = grant(stale, "root", ResourceLease(official_checks=3), requested_slots=0)
        store.append(id, "delegation.created", {"spec": _spec({"official_checks": 3}).model_dump(mode="json"),
                                                "parent_id": "root", "created_at": "t"})
        if id == "a":
            store.append(id, "budget.reserved", {"lease": lease.model_dump(mode="json"), "slots": 1})
        else:
            with pytest.raises(LeaseRefused, match="official_checks"):
                store.append(id, "budget.reserved", {"lease": lease.model_dump(mode="json"), "slots": 1})
            # The refused node is retired, as the controller does, so it stops counting at its requested lease.
            store.append(id, "delegation.cancelled", {"reason": "reservation refused"})
            store.release(id)
    assert LeaseLedger(first.tree()).allocatable("root") == ResourceLease(official_checks=1)


def _recover(store, id):
    """A worker that was running when its process died: recovered as unknown, then released."""
    store.append(id, "delegation.started", {})
    store.append(id, "delegation.recovered", {"reason": "interrupted", "recovered_at": "t"})
    store.release(id)


def test_a_recovered_child_is_charged_its_lease_in_checks_and_seconds_not_the_parents_whole_remainder(tmp_path):
    """Issue #196: a worker cannot spend more checks or seconds than its lease
    allowed, so its unknown liability in those dimensions is that lease."""
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 20, "active_seconds": 3600.0}),
          ("a", "root", {"official_checks": 1, "active_seconds": 60.0}))
    _recover(store, "a")
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=19, active_seconds=3540.0)
    assert ledger.exhausted("root") == ()
    grant(store.tree(), "root", ResourceLease(official_checks=19, active_seconds=3540.0), requested_slots=0)


def test_a_recovered_child_still_zeroes_a_dimension_its_lease_does_not_bound_the_spend_in(tmp_path):
    """Tokens are not enforced by the worker the way checks and seconds are: unknown stays liability."""
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 20, "active_seconds": 3600.0, "tokens": 1000}),
          ("a", "root", {"official_checks": 1, "active_seconds": 60.0, "tokens": 100}))
    _recover(store, "a")
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=19, active_seconds=3540.0, tokens=0)
    assert ledger.usage("root").unknown == ("cost_usd", "provider_calls", "tokens")


def _reserve_root(store, lease, epoch=None):
    store.append("root", "budget.reserved", {"lease": ResourceLease(**lease).model_dump(mode="json"), "slots": 1,
                                             **({"epoch": epoch} if epoch else {})})


def test_a_new_root_epoch_forgets_what_the_last_session_released_but_not_what_is_still_live(tmp_path):
    """The root lease is a budget for one session. Children released before the
    session's epoch began are the last session's spending; children still
    holding a reservation, or released during this epoch, are this one's."""
    store = DelegationStore(tmp_path)
    ceiling = {"official_checks": 20, "active_seconds": 3600.0}
    store.append("root", "delegation.created", {"spec": _spec(ceiling).model_dump(mode="json"),
                                                "parent_id": None, "created_at": "t"})
    _reserve_root(store, ceiling, epoch="e1")
    _tree(store, ("spent", "root", {"official_checks": 4, "active_seconds": 600.0}),
          ("lost", "root", {"official_checks": 1, "active_seconds": 60.0}),
          ("queued", "root", {"official_checks": 2, "active_seconds": 120.0}),
          ("late", "root", {"official_checks": 1, "active_seconds": 60.0}))
    _finish(store, "spent", official_checks=4, active_seconds=600.0)
    _recover(store, "lost")
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=12, active_seconds=2760.0)
    _reserve_root(store, ceiling, epoch="e2")                  # the next session opens
    ledger = LeaseLedger(store.tree())
    # `queued` and `late` still hold their reservations; nothing released before e2 counts.
    assert ledger.allocatable("root") == ResourceLease(official_checks=17, active_seconds=3420.0)
    assert ledger.usage("root") == ResourceUsage()
    assert ledger.exhausted("root") == ()
    # Released during e2, `late` charges e2 what it used.
    _finish(store, "late", official_checks=1, active_seconds=30.0)
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=17, active_seconds=3450.0)
    assert ledger.usage("root") == ResourceUsage(official_checks=1, active_seconds=30.0)
    # A ceiling that moves within the session keeps its epoch: nothing is forgotten.
    _reserve_root(store, {"official_checks": 30, "active_seconds": 3600.0}, epoch="e2")
    assert LeaseLedger(store.tree()).allocatable("root").official_checks == 27


def test_a_journal_with_no_epoch_keeps_a_cumulative_root(tmp_path):
    """Backward compatible: a reservation written before epochs existed starts none."""
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 4}), ("a", "root", {"official_checks": 1}))
    _finish(store, "a", official_checks=1)
    _reserve_root(store, {"official_checks": 4})
    assert LeaseLedger(store.tree()).allocatable("root") == ResourceLease(official_checks=3)


def _computation(store, id, seconds):
    """A detached computation as `attach_computation` journals it, finished and released."""
    from hardy.workflows.delegation.contracts import WorkerResult
    nothing = {"cost_usd": Decimal(0), "tokens": 0, "provider_calls": 0, "official_checks": 0, "active_seconds": 0.0}
    spec = _spec(nothing).model_copy(update={"task_mode": "compute"})
    store.append(id, "delegation.created", {"spec": spec.model_dump(mode="json"), "parent_id": "root",
                                            "created_at": "t"})
    store.append(id, "budget.reserved", {"lease": ResourceLease(**nothing).model_dump(mode="json"), "slots": 0})
    store.append(id, "delegation.started", {"owner": "0123456789abcdef", "compute": True})
    usage = ResourceUsage(cost_usd=Decimal(0), tokens=0, active_seconds=seconds)
    _use(store, id, cost_usd=Decimal(0), tokens=0, active_seconds=seconds)
    result = WorkerResult(delegation_id=id, status=DelegationState.COMPLETED, synthesis="ok", usage=usage)
    store.append(id, "delegation.completed", {"result": result.model_dump(mode="json")})
    store.release(id)


def test_detached_computations_draw_nothing_from_the_roots_budget(tmp_path):
    """Issue #201: a computation reserves nothing and is bounded by its tool's
    timeout, so its wall time is reported, not charged."""
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 4, "active_seconds": 600.0}))
    for index in range(6):
        _computation(store, f"c{index}", 100.0)
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=4, active_seconds=600.0)
    assert ledger.exhausted("root") == ()
    assert ledger.usage("root").active_seconds == 0.0
    assert ledger.compute_usage("root").active_seconds == 600.0
    assert ledger.usage("c0").active_seconds == 100.0               # its own record still says so


def test_a_worker_asked_to_compute_is_still_charged(tmp_path):
    """`compute` is also a worker's task mode; only an attached computation is exempt."""
    store = DelegationStore(tmp_path)
    worker = _spec({"official_checks": 1, "active_seconds": 100.0}).model_copy(update={"task_mode": "compute"})
    _tree(store, ("root", None, {"official_checks": 4, "active_seconds": 600.0}))
    store.append("w", "delegation.created", {"spec": worker.model_dump(mode="json"), "parent_id": "root",
                                             "created_at": "t"})
    store.append("w", "budget.reserved", {"lease": worker.lease.model_dump(mode="json"), "slots": 1})
    _finish(store, "w", official_checks=1, active_seconds=100.0)
    ledger = LeaseLedger(store.tree())
    assert ledger.allocatable("root") == ResourceLease(official_checks=3, active_seconds=500.0)
    assert ledger.compute_usage("root") == ResourceUsage()


def test_a_recovered_child_that_reported_an_overrun_is_charged_the_overrun_not_its_lease(tmp_path):
    """The lease bounds what is unknown; it does not forgive what was already reported."""
    store = DelegationStore(tmp_path)
    _tree(store, ("root", None, {"official_checks": 20, "active_seconds": 3600.0}),
          ("a", "root", {"official_checks": 1, "active_seconds": 60.0}))
    store.append("a", "delegation.started", {})
    _use(store, "a", active_seconds=75.0)
    store.append("a", "delegation.recovered", {"reason": "interrupted", "recovered_at": "t"})
    store.release("a")
    assert LeaseLedger(store.tree()).allocatable("root") == ResourceLease(official_checks=19, active_seconds=3525.0)


def test_an_epoch_names_every_session_that_joined_it(tmp_path):
    store = DelegationStore(tmp_path)
    ceiling = {"official_checks": 4}
    _tree(store, ("root", None, ceiling))
    assert LeaseLedger(store.tree()).epoch_members("root") == ()
    _reserve_root(store, ceiling, epoch="e1")
    store.append("root", "budget.reserved", {"lease": ResourceLease(**ceiling).model_dump(mode="json"), "slots": 1,
                                             "epoch": "e1", "joined": "s2"})
    ledger = LeaseLedger(store.tree())
    assert ledger.epoch("root") == "e1" and ledger.epoch_members("root") == ("e1", "s2")
    _reserve_root(store, ceiling, epoch="e3")
    assert LeaseLedger(store.tree()).epoch_members("root") == ("e3",)
