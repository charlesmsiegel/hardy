"""One synchronized allocation counter and deadline shared by a proof run.

The owner meters operation starts, not provider tokens or processes in flight.
Reserved views protect later checks without restarting time or granting checks;
branch cancellation stays with the caller rather than cancelling every sibling.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from threading import Lock


class BudgetExhausted(ValueError):
    """The run cannot start another check under its current ceilings."""


def _count(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("check counts must be nonnegative integers")


class CheckBudget:
    def __init__(
        self, *, official_checks: int, active_seconds: float, proof_seconds: float,
        active_elapsed: Callable[[], float] = lambda: 0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        _count(official_checks)
        for value in (active_seconds, proof_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError("time ceilings must be finite and nonnegative")
        self._official_checks = official_checks
        self._active_seconds = active_seconds
        self._proof_seconds = proof_seconds
        self._active_elapsed = active_elapsed
        self._monotonic = monotonic
        self._started = monotonic()
        self._checks = 0
        self._lock = Lock()

    def validate_limits(
        self, *, official_checks: int, active_seconds: float, proof_seconds: float,
    ) -> None:
        if (self._official_checks > official_checks
                or self._active_seconds > active_seconds
                or self._proof_seconds > proof_seconds):
            raise ValueError("shared budget ceilings exceed the task limits")

    @property
    def checks(self) -> int:
        with self._lock:
            return self._checks

    @property
    def remaining_checks(self) -> int:
        with self._lock:
            return self._official_checks - self._checks

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, min(self._active_seconds - self._active_elapsed(),
                            self._proof_seconds - (self._monotonic() - self._started)))

    def _ensure(self, reserve: int) -> None:
        if self._checks >= self._official_checks - reserve or self.remaining_seconds <= 0:
            raise BudgetExhausted("official proof-check budget or deadline exhausted")

    def ensure(self, *, reserve: int = 0) -> None:
        _count(reserve)
        with self._lock:
            self._ensure(reserve)

    def acquire(self, *, reserve: int = 0) -> int:
        """Atomically charge a start, even if the following operation fails."""
        _count(reserve)
        with self._lock:
            self._ensure(reserve)
            self._checks += 1
            return self._checks

    def reserved(self, *, checks: int) -> ReservedBudget:
        return ReservedBudget(self, checks)


class ReservedBudget:
    """A restricted view; all allocations still pass through the owner's lock."""

    def __init__(self, owner: CheckBudget, reserve: int) -> None:
        _count(reserve)
        self._owner = owner
        self._reserve = reserve

    def validate_limits(
        self, *, official_checks: int, active_seconds: float, proof_seconds: float,
    ) -> None:
        self._owner.validate_limits(official_checks=official_checks,
            active_seconds=active_seconds, proof_seconds=proof_seconds)

    @property
    def checks(self) -> int:
        return self._owner.checks

    @property
    def remaining_checks(self) -> int:
        return max(0, self._owner.remaining_checks - self._reserve)

    @property
    def remaining_seconds(self) -> float:
        return self._owner.remaining_seconds

    def ensure(self, *, reserve: int = 0) -> None:
        _count(reserve)
        self._owner.ensure(reserve=self._reserve + reserve)

    def acquire(self, *, reserve: int = 0) -> int:
        _count(reserve)
        return self._owner.acquire(reserve=self._reserve + reserve)

    def reserved(self, *, checks: int) -> ReservedBudget:
        _count(checks)
        return ReservedBudget(self._owner, self._reserve + checks)
