"""Delegation values are frozen execution state, never mathematical records."""
from decimal import Decimal

import pytest

from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    CoordinationPolicy,
    Delegation,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    ResourceUsage,
    SpawnPolicy,
    WorkerResult,
)
from hardy.workflows.ledger.contracts import VersionRef


def _ref(id="L17"):
    return VersionRef(id=id, digest="a" * 64)


def test_lease_fits_within_parent_and_refuses_growth():
    parent = ResourceLease(cost_usd=Decimal("10"), official_checks=20, active_seconds=600, provider_calls=50)
    child = ResourceLease(cost_usd=Decimal("4"), official_checks=5, active_seconds=600, provider_calls=10)
    assert child.fits_within(parent)
    assert not ResourceLease(cost_usd=Decimal("11")).fits_within(parent)
    # An unbounded child dimension cannot sit under a bounded parent one.
    assert not ResourceLease(cost_usd=None).fits_within(parent)
    assert ResourceLease(cost_usd=None).fits_within(ResourceLease(cost_usd=None))


def test_lease_arithmetic_keeps_unbounded_dimensions_unbounded():
    a = ResourceLease(cost_usd=Decimal("3"), official_checks=2)
    b = ResourceLease(cost_usd=Decimal("1"), official_checks=1)
    assert (a + b) == ResourceLease(cost_usd=Decimal("4"), official_checks=3)
    assert (a - b) == ResourceLease(cost_usd=Decimal("2"), official_checks=1)
    assert (b - a) == ResourceLease(cost_usd=Decimal("0"), official_checks=0)
    assert (ResourceLease() + b).cost_usd is None


def test_usage_sum_marks_unknown_dimensions_rather_than_zero():
    known = ResourceUsage(cost_usd=Decimal("1.5"), tokens=100, provider_calls=2, official_checks=1, active_seconds=3.0)
    unknown = ResourceUsage(provider_calls=1, unknown=("cost_usd", "tokens"))
    total = known + unknown
    assert total.provider_calls == 3
    assert total.cost_usd is None and total.tokens is None
    assert set(total.unknown) == {"cost_usd", "tokens"}
    assert (known + known).cost_usd == Decimal("3.0")
    with pytest.raises(ValueError):
        ResourceUsage(cost_usd=Decimal("1"), unknown=("cost_usd",))
    with pytest.raises(ValueError):
        ResourceUsage(unknown=("elephants",))


def test_usage_exceeds_lease_only_on_known_dimensions():
    lease = ResourceLease(cost_usd=Decimal("2"), official_checks=3)
    assert ResourceUsage(cost_usd=Decimal("2.5")).exceeds(lease) == ("cost_usd",)
    assert ResourceUsage(unknown=("cost_usd",)).exceeds(lease) == ()
    assert ResourceUsage(official_checks=3).exceeds(lease) == ("official_checks",)
    assert ResourceUsage(official_checks=2).exceeds(lease) == ()


def test_spec_and_delegation_are_frozen_and_validated():
    spec = DelegationSpec(
        objective="prove Lemma 17",
        project_refs=(_ref(),),
        scope=_ref("scope"),
        lease=ResourceLease(official_checks=2),
        concurrency=ConcurrencyLease(slots=1),
        created_by="human",
    )
    delegation = Delegation.create("d-1", spec, parent_id=None, created_at="2026-09-10T00:00:00+00:00")
    assert delegation.root_id == "d-1" and delegation.state is DelegationState.QUEUED
    assert delegation.spawn == SpawnPolicy() and not delegation.spawn.can_spawn
    assert delegation.coordination is CoordinationPolicy.INDEPENDENT
    assert not delegation.terminal
    with pytest.raises(ValueError):
        DelegationSpec(objective="", project_refs=(), scope=_ref("scope"), lease=ResourceLease(),
                       concurrency=ConcurrencyLease(slots=1), created_by="human")
    with pytest.raises(ValueError):
        ConcurrencyLease(slots=0)
    assert spec.digest == spec.model_copy().digest and len(spec.digest) == 64


def test_worker_result_requires_terminal_status():
    with pytest.raises(ValueError):
        WorkerResult(delegation_id="d-1", status=DelegationState.ACTIVE, synthesis="x", usage=ResourceUsage())
    result = WorkerResult(delegation_id="d-1", status=DelegationState.COMPLETED, synthesis="done", usage=ResourceUsage())
    assert result.findings == () and result.change_set is None
