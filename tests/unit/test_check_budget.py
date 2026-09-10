"""A run shares reservations and deadlines across strategies and model checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from hardy.formal.budget import BudgetExhausted, CheckBudget


def test_atomic_acquisition_and_reserved_views_share_one_counter():
    budget = CheckBudget(official_checks=4, active_seconds=10, proof_seconds=5)
    branch = budget.reserved(checks=1)
    barrier = Barrier(8)

    def acquire(_):
        barrier.wait()
        try:
            return branch.acquire()
        except BudgetExhausted:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, range(8)))
    assert sorted(value for value in results if value is not None) == [1, 2, 3]
    assert branch.remaining_checks == 0
    assert budget.acquire() == 4
    with pytest.raises(BudgetExhausted):
        budget.acquire()


def test_nested_reservations_and_deadline_do_not_restart():
    ticks = [0.0]
    budget = CheckBudget(official_checks=3, active_seconds=10, proof_seconds=5,
                         monotonic=lambda: ticks[0])
    nested = budget.reserved(checks=1).reserved(checks=1)
    assert nested.acquire() == 1
    with pytest.raises(BudgetExhausted):
        nested.ensure()
    ticks[0] = 5
    assert budget.remaining_seconds == 0
    with pytest.raises(BudgetExhausted):
        budget.acquire()
    assert budget.checks == 1


@pytest.mark.parametrize("field,value", [("official_checks", True), ("official_checks", 1.5),
    ("proof_seconds", float("nan")), ("active_seconds", -1)])
def test_invalid_ceiling_is_rejected(field, value):
    args = dict(official_checks=3, active_seconds=10, proof_seconds=5)
    args[field] = value
    with pytest.raises(ValueError):
        CheckBudget(**args)


def test_active_deadline_and_reserve_validation():
    budget = CheckBudget(official_checks=2, active_seconds=10, proof_seconds=5,
                         active_elapsed=lambda: 10)
    with pytest.raises(BudgetExhausted):
        budget.ensure()
    with pytest.raises(ValueError):
        budget.reserved(checks=-1)


def test_shared_budget_cannot_extend_frozen_task_limits():
    budget = CheckBudget(official_checks=3, active_seconds=10, proof_seconds=5)
    with pytest.raises(ValueError, match="task limits"):
        budget.reserved(checks=1).validate_limits(
            official_checks=2, active_seconds=10, proof_seconds=5)
