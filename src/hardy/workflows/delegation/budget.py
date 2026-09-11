"""Ancestor-bounded reservations and upward-rolling usage, derived from the journal.

The invariants of spec section 6 are checked here in code, never in prompts:
sum(child reservations) <= parent allocatable; usage rolls up; unknown usage is
unavailable liability, never zero; a released reservation returns to the parent;
exhaustion at any ancestor stops every descendant. This module never decides
mathematics and never runs work.
"""
from __future__ import annotations

from typing import Any

from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    ConcurrencyLease,
    DelegationState,
    ResourceLease,
    ResourceUsage,
)
from hardy.workflows.delegation.store import DelegationTree

#: States in which a child still holds its parent's worker slots.
HOLDING_SLOTS = frozenset({DelegationState.QUEUED, DelegationState.ACTIVE, DelegationState.WAITING})


class LeaseRefused(ValueError):
    """The requested reservation does not fit the parent's allocatable resources."""


class LeaseLedger:
    """Read-only lease arithmetic over one replayed tree."""

    def __init__(self, tree: DelegationTree) -> None:
        self.tree = tree
        self._reserved: dict[str, ResourceLease] = {}
        self._slots: dict[str, int] = {}
        self._released: set[str] = set()
        for event in tree.events:
            if event.kind == "budget.reserved":
                self._reserved[event.delegation_id] = ResourceLease.model_validate(event.payload["lease"])
                self._slots[event.delegation_id] = int(event.payload.get("slots", 0))
            elif event.kind == "budget.released":
                self._released.add(event.delegation_id)

    def reserved(self, id: str) -> ResourceLease:
        return self._reserved.get(id, self.tree.get(id).spec.lease)

    def slots(self, id: str) -> int:
        return self._slots.get(id, self.tree.get(id).spec.concurrency.slots)

    def released(self, id: str) -> bool:
        return id in self._released

    def usage(self, id: str) -> ResourceUsage:
        """Own reported usage plus every descendant's; unknown propagates upward."""
        total = self.tree.usage_reported.get(id, ResourceUsage())
        for child in self.tree.children(id):
            total = total + self.usage(child)
        return total

    def child_reservations(self, id: str) -> ResourceLease:
        total = ResourceLease(**{name: 0 for name in DIMENSIONS})
        for child in self.tree.children(id):
            if child in self._released:
                continue
            total = total + self.reserved(child)
        return total

    def allocatable(self, id: str) -> ResourceLease:
        """What a new child may still reserve: lease minus children minus own direct use.

        A dimension this node's own usage cannot state is unknown liability,
        so nothing further is promised in it: the whole remainder is treated
        as spent rather than as available.
        """
        own = self.tree.usage_reported.get(id, ResourceUsage())
        remaining = self.reserved(id) - self.child_reservations(id)
        values: dict[str, Any] = {}
        for name in DIMENSIONS:
            ceiling = getattr(remaining, name)
            if ceiling is None:
                values[name] = None
                continue
            if name in own.unknown:
                values[name] = type(ceiling)(0)
            else:
                spent = getattr(own, name) or 0
                values[name] = max(ceiling - type(ceiling)(spent), type(ceiling)(0))
        return ResourceLease(**values)

    def exhausted(self, id: str) -> tuple[str, ...]:
        """Dimensions exhausted at this node or any ancestor."""
        over: set[str] = set()
        for node in (id, *self.tree.ancestors(id)):
            over.update(self.usage(node).exceeds(self.reserved(node)))
        return tuple(sorted(over))

    def slots_in_use(self, id: str) -> int:
        return sum(self.slots(child) for child in self.tree.children(id)
                   if self.tree.get(child).state in HOLDING_SLOTS)

    def slots_available(self, id: str) -> int:
        return max(0, self.slots(id) - self.slots_in_use(id))


def grant(tree: DelegationTree, parent_id: str, requested: ResourceLease, *, requested_slots: int,
          ) -> tuple[ResourceLease, ConcurrencyLease | None]:
    """Admit a child reservation under its parent, or refuse; nothing is clipped silently."""
    ledger = LeaseLedger(tree)
    available = ledger.allocatable(parent_id)
    if not requested.fits_within(available):
        over = [name for name in DIMENSIONS
                if getattr(available, name) is not None
                and (getattr(requested, name) is None
                     or getattr(requested, name) > getattr(available, name))]
        raise LeaseRefused(f"reservation exceeds parent allocatable resources: {', '.join(over)}")
    exhausted = ledger.exhausted(parent_id)
    if exhausted:
        raise LeaseRefused(f"ancestor resources exhausted: {', '.join(exhausted)}")
    if requested_slots > ledger.slots_available(parent_id):
        raise LeaseRefused("requested worker slots exceed the parent's available concurrency")
    return requested, (ConcurrencyLease(slots=requested_slots) if requested_slots else None)
