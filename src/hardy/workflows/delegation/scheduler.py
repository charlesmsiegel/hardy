"""The mechanical scheduler: readiness, leases, slots, pins and lanes. It decides no mathematics.

Humans and coordinators say what deserves attention; this module says what
authorized ready work may run now and what a resource request may receive
under inherited ceilings. Research value is never one number: lanes are
preserved, an exploration floor keeps non-consensus work alive unless an
authorized policy collapses it, pins outrank heuristics, and graph-derived
urgency orders work inside a lane without ever becoming evidence.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import Field

from hardy.foundation.values import FrozenModel
from hardy.workflows.delegation.budget import LeaseLedger
from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    Delegation,
    DelegationSpec,
    DelegationState,
    ResourceDelta,
    ResourceLease,
)
from hardy.workflows.delegation.findings import FindingLedger
from hardy.workflows.delegation.store import DelegationTree
from hardy.workflows.ledger.contracts import Obligation, ObligationStatus, ProjectItem, VersionRef
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot


class Lane(str, Enum):
    USER_PINNED = "user_pinned"
    EXPLOIT = "exploit"
    EXPLORE = "explore"
    VERIFY = "verify"
    BLOCKER = "blocker"


_VERIFY_MODES = frozenset({"refute", "adjudicate", "critique", "verify"})


def default_lane(spec: DelegationSpec) -> Lane:
    """A lane from what the brief asked for, never from how promising it looks."""
    if spec.lane:
        return Lane(spec.lane)
    if spec.task_mode in _VERIFY_MODES:
        return Lane.VERIFY
    return Lane.EXPLOIT


PinKind = Literal["min_attention", "forbid_spend", "reinforce", "reserve_exploration"]


class Pin(FrozenModel):
    delegation_id: str
    kind: PinKind
    by: str
    value: int | None = None


class PortfolioConstraints(FrozenModel):
    exploration_floor: Decimal = Field(default=Decimal("0.2"), ge=0, le=1, allow_inf_nan=False)
    verify_floor: Decimal = Field(default=Decimal("0.1"), ge=0, le=1, allow_inf_nan=False)
    collapse_exploration: bool = False


class AllocationRequest(FrozenModel):
    delegation_id: str
    lane: Lane
    tranche: ResourceDelta
    slots: int = Field(default=0, ge=0, strict=True)
    requested_by: str
    reason: str
    supporting_refs: tuple[str, ...] = ()


class SchedulerDecision(FrozenModel):
    request: AllocationRequest
    granted: ResourceDelta | None
    slots: int
    refused_because: tuple[str, ...]
    prior: ResourceLease
    resulting: ResourceLease
    sequence: int = 0


class StallSignal(FrozenModel):
    delegation_id: str
    reasons: tuple[str, ...]


class GraphUrgency(FrozenModel):
    """Mechanical signals from the project graph. Ordering only; never evidence."""

    blocked_downstream: int = Field(ge=0, strict=True)
    sole_blocker: bool
    distance_to_goal: int | None = None
    widely_reused_unverified: bool = False

    @property
    def score(self) -> tuple[int, int, int, int]:
        """A sort key, deliberately not a scalar anyone could mistake for promise."""
        return (int(self.sole_blocker), self.blocked_downstream, int(self.widely_reused_unverified),
                -(self.distance_to_goal if self.distance_to_goal is not None else 1_000_000))


def graph_urgency(snapshot: LedgerSnapshot, ref: VersionRef) -> GraphUrgency:
    """Read from `LedgerGraph`: consumers above the item, whether its open work alone blocks them."""
    graph = LedgerGraph(snapshot)
    consumers = [r for r in graph.reverse_closure(ref) if isinstance(snapshot.get(r), ProjectItem)]
    open_here = [o for o in snapshot.current(Obligation)
                 if o.item.id == ref.id and o.status not in {ObligationStatus.RESOLVED, ObligationStatus.DISMISSED}]
    sole = False
    if open_here and consumers:
        sole = all(all(b.item.id == ref.id for b in graph.blockers(consumer)) for consumer in consumers)
    goals = graph.open_goals()
    distance = None
    for goal in goals:
        paths = graph.paths(goal.ref, ref)
        if paths:
            length = min(len(path) - 1 for path in paths)
            distance = length if distance is None else min(distance, length)
    unverified = bool(open_here) and len(consumers) >= 3
    return GraphUrgency(blocked_downstream=len(consumers), sole_blocker=sole, distance_to_goal=distance,
                        widely_reused_unverified=unverified)


Urgency = Callable[[Delegation], GraphUrgency | None]


class Scheduler:
    def __init__(self, tree: DelegationTree, ledger: LeaseLedger, *, constraints: PortfolioConstraints,
                 pins: tuple[Pin, ...], lanes: dict[str, Lane] | None = None,
                 urgency: Urgency | None = None) -> None:
        self.tree = tree
        self.ledger = ledger
        self.constraints = constraints
        self.pins = pins
        self._lanes = dict(lanes or {})
        self._urgency = urgency

    # -- views ------------------------------------------------------------------------

    def lane(self, delegation: Delegation) -> Lane:
        if any(p.delegation_id == delegation.id and p.kind == "reinforce" for p in self.pins):
            return Lane.USER_PINNED
        return self._lanes.get(delegation.id) or default_lane(delegation.spec)

    def _forbidden(self, id: str) -> bool:
        return any(p.delegation_id == id and p.kind == "forbid_spend" for p in self.pins)

    def ready(self) -> tuple[Delegation, ...]:
        """Queued leaves whose ancestors are live and unexhausted and which nobody forbade."""
        ready = []
        for delegation in self.tree.delegations.values():
            if delegation.state is not DelegationState.QUEUED or delegation.parent_id is None:
                continue
            if self._forbidden(delegation.id) or self.ledger.exhausted(delegation.id):
                continue
            if any(self.tree.get(a).terminal or self.tree.cancel_requested(a) for a in self.tree.ancestors(delegation.id)):
                continue
            ready.append(delegation)
        return tuple(ready)

    # -- choosing ---------------------------------------------------------------------

    def _ordered(self, delegations: list[Delegation]) -> list[Delegation]:
        def key(delegation: Delegation):
            signal = self._urgency(delegation) if self._urgency is not None else None
            return (tuple(-x for x in signal.score) if signal is not None else (0, 0, 0, 0), delegation.created_at,
                    delegation.id)
        return sorted(delegations, key=key)

    def choose(self, free_slots: int) -> tuple[Delegation, ...]:
        """Which ready leaves take the free slots: pins, then blockers, then lanes under their floors."""
        if free_slots <= 0:
            return ()
        by_lane: dict[Lane, list[Delegation]] = {lane: [] for lane in Lane}
        for delegation in self.ready():
            by_lane[self.lane(delegation)].append(delegation)
        for lane in by_lane:
            by_lane[lane] = self._ordered(by_lane[lane])
        chosen: list[Delegation] = []

        def take(lane: Lane, count: int) -> None:
            while count > 0 and by_lane[lane] and len(chosen) < free_slots:
                chosen.append(by_lane[lane].pop(0))
                count -= 1

        take(Lane.USER_PINNED, free_slots)
        take(Lane.BLOCKER, free_slots)
        total = self.ledger.slots(self.tree.roots[0]) if self.tree.roots else free_slots
        if not self.constraints.collapse_exploration:
            take(Lane.EXPLORE, max(0, math.ceil(total * self.constraints.exploration_floor)))
        take(Lane.VERIFY, max(0, math.ceil(total * self.constraints.verify_floor)))
        for lane in (Lane.EXPLOIT, Lane.VERIFY, Lane.EXPLORE):
            if lane is Lane.EXPLORE and self.constraints.collapse_exploration and by_lane[Lane.EXPLOIT]:
                continue
            take(lane, free_slots)
        return tuple(chosen[:free_slots])

    # -- tranches ---------------------------------------------------------------------

    def decide(self, request: AllocationRequest) -> SchedulerDecision:
        """Grant a tranche or reclaim inside the parent's allocatable resources; refuse with reasons."""
        delegation = self.tree.get(request.delegation_id)
        prior = self.ledger.reserved(delegation.id)
        reasons: list[str] = []
        if delegation.terminal:
            reasons.append("delegation is terminal")
        if self._forbidden(delegation.id):
            reasons.append("a forbid_spend pin refuses further resources")
        try:
            resulting = request.tranche.applied_to(prior)
        except ValueError as error:
            reasons.append(str(error))
            resulting = prior
        parent = delegation.parent_id
        if parent is not None and not request.tranche.increases.fits_within(self.ledger.allocatable(parent)):
            available = self.ledger.allocatable(parent)
            over = [name for name in DIMENSIONS
                    if getattr(available, name) is not None and getattr(request.tranche.increases, name) is not None
                    and getattr(request.tranche.increases, name) > getattr(available, name)]
            reasons.append(f"tranche exceeds the parent's allocatable resources: {', '.join(over)}")
        used = self.ledger.usage(delegation.id)
        for name in DIMENSIONS:
            ceiling, spent = getattr(resulting, name), getattr(used, name)
            if ceiling is not None and spent is not None and name not in used.unknown and spent > ceiling:
                reasons.append(f"reclaim would leave {name} below recorded usage")
        if request.slots and parent is not None and request.slots > self.ledger.slots(parent):
            reasons.append("slots exceed the parent's concurrency lease")
        if reasons:
            return SchedulerDecision(request=request, granted=None, slots=0, refused_because=tuple(reasons),
                                     prior=prior, resulting=prior)
        return SchedulerDecision(request=request, granted=request.tranche, slots=request.slots,
                                 refused_because=(), prior=prior, resulting=resulting)

    # -- stalls -----------------------------------------------------------------------

    def stalls(self, findings: FindingLedger) -> tuple[StallSignal, ...]:
        """Requests for review, not verdicts: duplicates repeating, or a subtree with nothing left running."""
        signals = []
        by_source: dict[str, dict[str, int]] = {}
        for finding in findings.all():
            counts = by_source.setdefault(finding.source_delegation, {})
            counts[finding.structural_fingerprint] = counts.get(finding.structural_fingerprint, 0) + 1
        for delegation in self.tree.delegations.values():
            reasons = []
            repeated = [n for n in by_source.get(delegation.id, {}).values() if n >= 3]
            if repeated:
                reasons.append(f"repeated duplicate findings ({max(repeated)} identical proposals)")
            children = [self.tree.get(c) for c in delegation.children]
            if children and all(c.terminal for c in children) and not delegation.terminal \
                    and not any(c.state is DelegationState.COMPLETED for c in children):
                reasons.append("every child is terminal without a completion and nothing proposes a continuation")
            if reasons:
                signals.append(StallSignal(delegation_id=delegation.id, reasons=tuple(reasons)))
        return tuple(signals)
