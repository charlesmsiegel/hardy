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

#: Dimensions a worker cannot overrun: its timer stops it at `active_seconds`
#: and its `CheckBudget` at `official_checks`. Where its usage there is
#: unknown -- interrupted work -- it is charged its whole lease, which bounds
#: what it could have spent, rather than its parent's whole remainder.
BOUNDED_BY_LEASE = ("official_checks", "active_seconds")

#: States in which a child holds its parent's worker slots. Queued work holds
#: none: there may be more runnable leaves than slots, and which of them run
#: is scheduling, not reservation.
HOLDING_SLOTS = frozenset({DelegationState.ACTIVE, DelegationState.WAITING})


class LeaseRefused(ValueError):
    """The requested reservation does not fit the parent's allocatable resources."""


class LeaseLedger:
    """Read-only lease arithmetic over one replayed tree.

    Two kinds of child are left out of what a parent is charged. A detached
    computation (`delegation.started` with `compute`) reserves nothing and is
    bounded by its tool's own timeout: its usage is its own record's and
    `compute_usage`'s, never its parent's spend. And where a node's
    reservation names an `epoch` -- the root's does, one per session -- a
    child released before that epoch began was an earlier session's spending
    and no longer counts against it. A child still holding a reservation, or
    released since the epoch began, counts whenever it was created. A journal
    written before epochs existed names none, so its root stays cumulative.
    A session that opens while the current epoch's sessions are live joins
    it (`joined` on the reservation) instead of starting another, so
    concurrent sessions on one project share one budget.
    """

    def __init__(self, tree: DelegationTree) -> None:
        self.tree = tree
        self._reserved: dict[str, ResourceLease] = {}
        self._slots: dict[str, int] = {}
        self._released: dict[str, int] = {}
        self._epoch: dict[str, str] = {}
        self._epoch_start: dict[str, int] = {}
        self._epoch_members: dict[str, list[str]] = {}
        self._compute: set[str] = set()
        for event in tree.events:
            id = event.delegation_id
            if event.kind == "budget.reserved":
                self._reserved[id] = ResourceLease.model_validate(event.payload["lease"])
                self._slots[id] = int(event.payload.get("slots", 0))
                epoch = event.payload.get("epoch")
                if epoch is not None and str(epoch) != self._epoch.get(id):
                    # A new epoch starts here; the same one re-reserved (a
                    # ceiling that moved mid-session, or a session joining
                    # it) continues it.
                    self._epoch[id], self._epoch_start[id] = str(epoch), event.sequence
                    self._epoch_members[id] = [str(epoch)]
                joined = event.payload.get("joined")
                if epoch is not None and joined is not None and str(joined) not in self._epoch_members[id]:
                    self._epoch_members[id].append(str(joined))
            elif event.kind == "budget.released":
                self._released.setdefault(id, event.sequence)
            elif event.kind == "delegation.started" and event.payload.get("compute"):
                self._compute.add(id)

    def reserved(self, id: str) -> ResourceLease:
        return self._reserved.get(id, self.tree.get(id).spec.lease)

    def slots(self, id: str) -> int:
        return self._slots.get(id, self.tree.get(id).spec.concurrency.slots)

    def released(self, id: str) -> bool:
        return id in self._released

    def epoch(self, id: str) -> str | None:
        """The epoch the node's reservation is currently in, or None where none was named."""
        return self._epoch.get(id)

    def epoch_members(self, id: str) -> tuple[str, ...]:
        """The owner tokens of the sessions in the current epoch: the one that opened it, then those that joined."""
        return tuple(self._epoch_members.get(id, ()))

    def is_computation(self, id: str) -> bool:
        return id in self._compute

    def _in_epoch(self, parent: str, child: str) -> bool:
        """Whether `child` belongs to `parent`'s current epoch: not released before it began."""
        start = self._epoch_start.get(parent)
        released = self._released.get(child)
        return start is None or released is None or released > start

    def _charged(self, child: str) -> ResourceUsage:
        """What a released child costs its parent: its usage, with unknown checks and seconds bounded by its lease.

        Bounded, not replaced: what the child already reported stands where
        it is more than the lease (a timer stops a worker a little late).
        """
        used, lease = self.usage(child), self.reserved(child)
        stated = {name: max(getattr(used, name) or 0, getattr(lease, name)) for name in used.unknown
                  if name in BOUNDED_BY_LEASE and getattr(lease, name) is not None}
        if not stated:
            return used
        return used.model_copy(update={**stated,
                                       "unknown": tuple(name for name in used.unknown if name not in stated)})

    def _charges(self, id: str) -> tuple[str, ...]:
        """The children whose usage is `id`'s spend: in its epoch and not computations."""
        return tuple(child for child in self.tree.children(id)
                     if child not in self._compute and self._in_epoch(id, child))

    def usage(self, id: str) -> ResourceUsage:
        """Own reported usage plus every charged descendant's; unknown propagates upward.

        A released child contributes what it is charged (`_charged`); a
        computation and a child released before the current epoch contribute
        nothing here (see the class docstring).
        """
        total = self.tree.usage_reported.get(id, ResourceUsage())
        for child in self._charges(id):
            total = total + (self._charged(child) if child in self._released else self.usage(child))
        return total

    def compute_usage(self, id: str) -> ResourceUsage:
        """What the node's detached computations in its current epoch used, for reporting only."""
        total = ResourceUsage()
        for child in self.tree.children(id):
            if child in self._compute and self._in_epoch(id, child):
                total = total + self.usage(child)
        return total

    def child_reservations(self, id: str, *, excluding: str | None = None) -> ResourceLease:
        total = ResourceLease(**{name: 0 for name in DIMENSIONS})
        for child in self.tree.children(id):
            if child in self._released or child == excluding:
                continue
            total = total + self.reserved(child)
        return total

    def allocatable(self, id: str) -> ResourceLease:
        """What a new child may still reserve: lease minus live children minus what is already spent.

        Spent means this node's own direct use plus everything a released
        child consumed: a release returns the unspent part of a reservation,
        never the part that was used. A dimension that usage cannot state,
        here or in a released child, is unknown liability, so nothing further
        is promised in it: the whole remainder is treated as spent rather
        than as available. The exception is a released child's checks and
        seconds, which its lease bounds (`BOUNDED_BY_LEASE`): unknown there,
        it is charged that lease. Computations and children released before
        the node's current epoch are not spent at all.
        """
        return self._allocatable(id, excluding=None)

    def allocatable_excluding(self, id: str, child: str) -> ResourceLease:
        """What `child` may hold in total: the parent's balance with the child's own reservation set aside.

        A child that was created but not yet reserved is counted at its
        requested lease, and a re-reservation is judged as a whole rather
        than as an increase, so both are answered by the same figure.
        """
        return self._allocatable(id, excluding=child)

    def _allocatable(self, id: str, *, excluding: str | None) -> ResourceLease:
        spent = self.tree.usage_reported.get(id, ResourceUsage())
        for child in self._charges(id):
            if child in self._released:
                spent = spent + self._charged(child)
        remaining = self.reserved(id) - self.child_reservations(id, excluding=excluding)
        values: dict[str, Any] = {}
        for name in DIMENSIONS:
            ceiling = getattr(remaining, name)
            if ceiling is None:
                values[name] = None
                continue
            if name in spent.unknown:
                values[name] = type(ceiling)(0)
            else:
                used = getattr(spent, name) or 0
                values[name] = max(ceiling - type(ceiling)(used), type(ceiling)(0))
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
    if requested_slots > ledger.slots(parent_id):
        raise LeaseRefused("requested worker slots exceed the parent's concurrency lease")
    return requested, (ConcurrencyLease(slots=requested_slots) if requested_slots else None)
