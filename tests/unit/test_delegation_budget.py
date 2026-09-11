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
