"""Derived delegation state, independent of journal I/O and budget admission.

Events describe execution history, not mathematical acceptance. A replay extends
a private copy of the last verified state; only a complete replay becomes a tree.
Budget readers consume that tree without importing the writer that consults them.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any

from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    Delegation,
    DelegationEvent,
    DelegationSpec,
    DelegationState,
    ResourceUsage,
    WorkerResult,
)

_STATE_EVENTS = {
    "delegation.started": DelegationState.ACTIVE,
    "delegation.paused": DelegationState.PAUSED,
    "delegation.resumed": DelegationState.ACTIVE,
    "delegation.waiting": DelegationState.WAITING,
    "delegation.completed": DelegationState.COMPLETED,
    "delegation.partial": DelegationState.PARTIAL,
    "delegation.failed": DelegationState.FAILED,
    "delegation.cancelled": DelegationState.CANCELLED,
    "delegation.exhausted": DelegationState.EXHAUSTED,
    "delegation.recovered": DelegationState.UNKNOWN,
}

_CONTEXT_KEYS = ("problem_core_digest", "research_brief_digest", "context_manifest_id")


class DelegationTree:
    """Derived state over one journal replay; read-only."""

    def __init__(self, delegations: dict[str, Delegation], usage: dict[str, ResourceUsage],
                 events: tuple[DelegationEvent, ...], cancel_requests: frozenset[str] = frozenset()) -> None:
        self.delegations: Mapping[str, Delegation] = MappingProxyType(delegations)
        self.usage_reported: Mapping[str, ResourceUsage] = MappingProxyType(usage)
        self.events = events
        self._cancel_requests = cancel_requests
        self._started: frozenset[str] = frozenset()

    def cancel_requested(self, id: str) -> bool:
        """Whether a live node has been asked to stop, without claiming it has stopped."""
        self.get(id)
        return id in self._cancel_requests

    @property
    def revision(self) -> int:
        return len(self.events)

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(d.id for d in self.delegations.values() if d.parent_id is None)

    def get(self, id: str) -> Delegation:
        """Resolve a recorded execution identity, refusing an unknown node."""
        try:
            return self.delegations[id]
        except KeyError:
            raise ValueError(f"unknown delegation: {id}") from None

    def children(self, id: str) -> tuple[str, ...]:
        """Direct children in the order the journal created them."""
        return self.get(id).children

    def descendants(self, id: str) -> tuple[str, ...]:
        """Breadth-first descendants in creation order within each parent."""
        found: list[str] = []
        pending = list(self.children(id))
        while pending:
            child = pending.pop(0)
            found.append(child)
            pending.extend(self.children(child))
        return tuple(found)

    def ancestors(self, id: str) -> tuple[str, ...]:
        """The parent first, continuing to the root."""
        chain = []
        parent = self.get(id).parent_id
        while parent is not None:
            chain.append(parent)
            parent = self.get(parent).parent_id
        return tuple(chain)

    def detached(self) -> DelegationTree:
        """A caller-owned snapshot whose nested payloads cannot alter cached replay."""
        result = DelegationTree(deepcopy(dict(self.delegations)), deepcopy(dict(self.usage_reported)),
                                deepcopy(self.events), self._cancel_requests)
        result._started = self._started
        return result


class _Replay:
    """Candidate execution state: validation must finish before the writer publishes it."""

    def __init__(self, before: DelegationTree | None) -> None:
        self.delegations = dict(before.delegations) if before else {}
        self.usage = dict(before.usage_reported) if before else {}
        self.cancel_requests = set(before._cancel_requests) if before else set()
        self.started = set(before._started) if before else set()

    def apply(self, event: DelegationEvent) -> None:
        delegation_id = event.delegation_id
        if event.kind == "delegation.created":
            self._create(event)
            return
        if delegation_id not in self.delegations:
            raise ValueError(f"journal event for unknown delegation: {delegation_id}")
        current = self.delegations[delegation_id]
        if event.kind == "usage.reported":
            self.usage[delegation_id] = self.usage[delegation_id] + ResourceUsage.model_validate(event.payload["usage"])
        elif event.kind == "cancel.requested":
            if current.terminal:
                raise ValueError(f"journal requests cancellation of terminal delegation {delegation_id}")
            self.cancel_requests.add(delegation_id)
        elif event.kind == "budget.released" and not current.terminal:
            raise ValueError(f"journal releases the reservation of non-terminal delegation {delegation_id}")
        elif event.kind in _STATE_EVENTS:
            self._transition(event, current)
        elif event.kind == "delegation.progress" and current.terminal:
            raise ValueError(f"journal reports progress on terminal delegation {delegation_id}")
        elif event.kind == "delegation.context":
            self.delegations[delegation_id] = current.model_copy(update={
                key: str(event.payload[key]) for key in _CONTEXT_KEYS if key in event.payload})

    def _create(self, event: DelegationEvent) -> None:
        delegation_id = event.delegation_id
        if delegation_id in self.delegations:
            raise ValueError(f"journal creates delegation twice: {delegation_id}")
        parent = event.payload.get("parent_id")
        if parent is not None and parent not in self.delegations:
            raise ValueError(f"journal names unknown parent {parent!r} for {delegation_id}")
        spec = DelegationSpec.model_validate(event.payload["spec"])
        root, depth = (delegation_id, 0) if parent is None else (
            self.delegations[parent].root_id, self.delegations[parent].depth + 1)
        self.delegations[delegation_id] = Delegation.create(delegation_id, spec, parent_id=parent,
            created_at=str(event.payload["created_at"]), root_id=root, depth=depth)
        self.usage[delegation_id] = ResourceUsage()
        if parent is not None:
            owner = self.delegations[parent]
            self.delegations[parent] = owner.model_copy(update={"children": (*owner.children, delegation_id)})

    def _transition(self, event: DelegationEvent, current: Delegation) -> None:
        delegation_id = event.delegation_id
        if current.terminal:
            raise ValueError(f"journal continues terminal delegation {delegation_id} with {event.kind}")
        update: dict[str, Any] = {"state": _STATE_EVENTS[event.kind]}
        if event.kind == "delegation.started":
            self._start(event, update)
        if event.kind == "delegation.resumed" and delegation_id not in self.started:
            update["state"] = DelegationState.QUEUED     # never started: back to the queue
        if "result" in event.payload:
            update["result"] = WorkerResult.model_validate(event.payload["result"])
        if "reason" in event.payload:
            update["terminal_reason"] = str(event.payload["reason"])
        if event.kind == "delegation.recovered":
            update["terminal_reason"] = "interrupted"
            self.usage[delegation_id] = self.usage[delegation_id] + ResourceUsage(unknown=DIMENSIONS)
        self.delegations[delegation_id] = current.model_copy(update=update)

    def _start(self, event: DelegationEvent, update: dict[str, Any]) -> None:
        self.started.add(event.delegation_id)
        if event.payload.get("interior"):
            update["interior"] = True
        if event.payload.get("owner"):
            update["owner"] = str(event.payload["owner"])


def replay(events: tuple[DelegationEvent, ...], before: DelegationTree | None = None) -> DelegationTree:
    """Apply complete events to a private candidate, retaining every prior event."""
    candidate = _Replay(before)
    for event in events:
        candidate.apply(event)
    tree = DelegationTree(candidate.delegations, candidate.usage, (*before.events, *events) if before else events,
                          frozenset(candidate.cancel_requests))
    tree._started = frozenset(candidate.started)
    return tree
