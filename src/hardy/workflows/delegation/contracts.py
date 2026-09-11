"""Delegation is execution state: bounded work about ledger objects, never the objects.

Theory: a Delegation names exact project refs and the resources it may consume;
its lifecycle is derived from an append-only journal. Leases are ancestor-bounded
reservations; usage is measured and keeps unknown dimensions unknown. Nothing here
reads the ledger, calls a provider, or grants trust.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Self

from pydantic import Field, model_validator

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import Text, VersionRef

DelegationId = str

#: Every resource dimension a lease or a usage record may carry, in one order.
DIMENSIONS = ("cost_usd", "tokens", "provider_calls", "official_checks", "active_seconds")


class DelegationState(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset({
    DelegationState.COMPLETED, DelegationState.PARTIAL, DelegationState.FAILED,
    DelegationState.CANCELLED, DelegationState.EXHAUSTED, DelegationState.UNKNOWN,
})


class CoordinationPolicy(str, Enum):
    INDEPENDENT = "independent"
    SHARED = "shared"
    CELL = "cell"
    COMPETITIVE = "competitive"
    ADVERSARIAL = "adversarial"
    PIPELINE = "pipeline"


class ResourceLease(FrozenModel):
    """A reservation. None means unbounded in that dimension, never zero."""

    cost_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens: int | None = Field(default=None, ge=0, strict=True)
    provider_calls: int | None = Field(default=None, ge=0, strict=True)
    official_checks: int | None = Field(default=None, ge=0, strict=True)
    active_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    def fits_within(self, parent: ResourceLease) -> bool:
        for name in DIMENSIONS:
            mine, theirs = getattr(self, name), getattr(parent, name)
            if theirs is None:
                continue
            if mine is None or mine > theirs:
                return False
        return True

    def _combine(self, other: ResourceLease, sign: int) -> ResourceLease:
        values: dict[str, Any] = {}
        for name in DIMENSIONS:
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine is None or theirs is None:
                values[name] = None
                continue
            result = mine + sign * theirs
            values[name] = max(result, type(result)(0))
        return ResourceLease(**values)

    def __add__(self, other: ResourceLease) -> ResourceLease:
        return self._combine(other, 1)

    def __sub__(self, other: ResourceLease) -> ResourceLease:
        return self._combine(other, -1)


class ResourceDelta(FrozenModel):
    """A signed change to a lease: a tranche when positive, a reclaim when negative."""

    cost_usd: Decimal | None = Field(default=None, allow_inf_nan=False)
    tokens: int | None = Field(default=None, strict=True)
    provider_calls: int | None = Field(default=None, strict=True)
    official_checks: int | None = Field(default=None, strict=True)
    active_seconds: float | None = Field(default=None, allow_inf_nan=False)

    def applied_to(self, lease: ResourceLease) -> ResourceLease:
        values: dict[str, Any] = {}
        for name in DIMENSIONS:
            base, change = getattr(lease, name), getattr(self, name)
            if change is None:
                values[name] = base
            elif base is None:
                raise ValueError(f"{name} is unbounded; a delta needs a bounded lease to change")
            else:
                result = base + change
                if result < 0:
                    raise ValueError(f"{name} cannot fall below zero")
                values[name] = result
        return ResourceLease(**values)

    @property
    def increases(self) -> ResourceLease:
        """The positive part, as a lease a parent must be able to allocate."""
        return ResourceLease(**{name: max(getattr(self, name), type(getattr(self, name))(0))
                                for name in DIMENSIONS if getattr(self, name) is not None})


class ConcurrencyLease(FrozenModel):
    slots: int = Field(ge=1, strict=True)


class ResourceUsage(FrozenModel):
    """Measured consumption. A dimension in `unknown` is liability, not zero."""

    cost_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens: int | None = Field(default=None, ge=0, strict=True)
    provider_calls: int = Field(default=0, ge=0, strict=True)
    official_checks: int = Field(default=0, ge=0, strict=True)
    active_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    unknown: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unknown_dimensions_are_named(self) -> Self:
        bad = set(self.unknown) - set(DIMENSIONS)
        if bad:
            raise ValueError(f"unknown usage dimensions are not resources: {sorted(bad)}")
        for name in self.unknown:
            if name in {"cost_usd", "tokens"} and getattr(self, name) is not None:
                raise ValueError(f"{name} cannot be both stated and unknown")
        return self

    def __add__(self, other: ResourceUsage) -> ResourceUsage:
        unknown = set(self.unknown) | set(other.unknown)
        values: dict[str, Any] = {}
        for name in ("cost_usd", "tokens"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if name in unknown or (mine is None and theirs is None):
                values[name] = None
            else:
                values[name] = (mine or 0) + (theirs or 0)
        for name in ("provider_calls", "official_checks", "active_seconds"):
            values[name] = getattr(self, name) + getattr(other, name)
        return ResourceUsage(**values, unknown=tuple(sorted(unknown)))

    def exceeds(self, lease: ResourceLease) -> tuple[str, ...]:
        """Dimensions where known usage has reached the lease ceiling."""
        over = []
        for name in DIMENSIONS:
            ceiling = getattr(lease, name)
            used = getattr(self, name)
            if ceiling is None or used is None or name in self.unknown:
                continue
            if used >= ceiling and used > 0:
                over.append(name)
        return tuple(over)


class SpawnPolicy(FrozenModel):
    can_spawn: bool = False
    max_children: int = Field(default=0, ge=0, strict=True)
    max_depth: int = Field(default=0, ge=0, strict=True)
    max_child_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1, allow_inf_nan=False)


class DelegationSpec(FrozenModel):
    """What was asked for. Exact refs, policies and leases; no transcript."""

    objective: Text
    project_refs: tuple[VersionRef, ...]
    scope: VersionRef
    context: VersionRef | None = None
    task_mode: Text = "prove"
    lease: ResourceLease
    concurrency: ConcurrencyLease
    spawn: SpawnPolicy = SpawnPolicy()
    coordination: CoordinationPolicy = CoordinationPolicy.INDEPENDENT
    model: str | None = None
    created_by: Text
    notify_human: bool = True
    #: Isolation, enforced at preload and at every retrieval: stable ids this
    #: delegation and its descendants may never see.
    hidden_ids: tuple[str, ...] = ()
    #: Sources made prominent at launch as pointers; their text stays lazy.
    seeded_sources: tuple[str, ...] = ()
    #: An explicit scheduling lane; None lets the scheduler derive one from the task mode.
    lane: str | None = None
    #: Whether the worker needs writable project files: a private overlay and a change set.
    writable: bool = False

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/DelegationSpec/v1",
                            "value": self.model_dump(mode="json")})


class WorkerResult(FrozenModel):
    """The structured terminal product of one worker; artifacts are refs, not transcripts."""

    delegation_id: DelegationId
    status: DelegationState
    synthesis: str
    usage: ResourceUsage
    findings: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    change_set: str | None = None
    terminal_reason: str | None = None

    @model_validator(mode="after")
    def status_is_terminal(self) -> Self:
        if self.status not in TERMINAL_STATES:
            raise ValueError("a worker result carries a terminal status")
        return self


class Delegation(FrozenModel):
    """Derived current state of one execution node."""

    id: DelegationId
    parent_id: DelegationId | None
    root_id: DelegationId
    depth: int = Field(ge=0, strict=True)
    spec: DelegationSpec
    state: DelegationState
    children: tuple[DelegationId, ...] = ()
    created_at: str
    terminal_reason: str | None = None
    problem_core_digest: str | None = None
    research_brief_digest: str | None = None
    context_manifest_id: str | None = None
    result: WorkerResult | None = None

    @property
    def spawn(self) -> SpawnPolicy:
        return self.spec.spawn

    @property
    def coordination(self) -> CoordinationPolicy:
        return self.spec.coordination

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @classmethod
    def create(cls, id: DelegationId, spec: DelegationSpec, *, parent_id: DelegationId | None,
               created_at: str, root_id: DelegationId | None = None, depth: int = 0) -> Delegation:
        return cls(id=id, parent_id=parent_id, root_id=root_id or id, depth=depth, spec=spec,
                   state=DelegationState.QUEUED, created_at=created_at)


class DelegationEvent(FrozenModel):
    """One append-only journal entry; ordering and chaining are the store's."""

    sequence: int = Field(ge=0, strict=True)
    previous: str | None
    delegation_id: DelegationId
    kind: Text
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/event/v1", "value": self.model_dump(mode="json")})
