"""Append-only delegation journal; the tree is a replay, not a cache.

One JSONL journal per workspace, hash-chained like the ledger's transactions and
guarded by an OS lock. Each delegation owns a RunStore directory beside it for
artifacts and its trajectory. Recovery after a crash marks work that was active
as `unknown` with every usage dimension unknown: interrupted work is neither
success, cancellation, exhaustion, nor never-started work.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    Delegation,
    DelegationEvent,
    DelegationSpec,
    DelegationState,
    ResourceUsage,
    WorkerResult,
)
from hardy.workflows.storage import RunStore

DIRECTORY = "delegations"
JOURNAL = "journal.jsonl"

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

INTERRUPTIBLE = frozenset({DelegationState.ACTIVE, DelegationState.WAITING, DelegationState.PAUSED})


class DelegationTree:
    """Derived state over one journal replay; read-only."""

    def __init__(self, delegations: dict[str, Delegation], usage: dict[str, ResourceUsage],
                 events: tuple[DelegationEvent, ...], cancel_requests: frozenset[str] = frozenset()) -> None:
        self.delegations: Mapping[str, Delegation] = MappingProxyType(delegations)
        self.usage_reported: Mapping[str, ResourceUsage] = MappingProxyType(usage)
        self.events = events
        self._cancel_requests = cancel_requests

    def cancel_requested(self, id: str) -> bool:
        self.get(id)
        return id in self._cancel_requests

    @property
    def revision(self) -> int:
        return len(self.events)

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(d.id for d in self.delegations.values() if d.parent_id is None)

    def get(self, id: str) -> Delegation:
        try:
            return self.delegations[id]
        except KeyError:
            raise ValueError(f"unknown delegation: {id}") from None

    def children(self, id: str) -> tuple[str, ...]:
        return self.get(id).children

    def descendants(self, id: str) -> tuple[str, ...]:
        found: list[str] = []
        pending = list(self.children(id))
        while pending:
            child = pending.pop(0)
            found.append(child)
            pending.extend(self.children(child))
        return tuple(found)

    def ancestors(self, id: str) -> tuple[str, ...]:
        chain = []
        parent = self.get(id).parent_id
        while parent is not None:
            chain.append(parent)
            parent = self.get(parent).parent_id
        return tuple(chain)


def _replay(events: tuple[DelegationEvent, ...]) -> DelegationTree:
    delegations: dict[str, Delegation] = {}
    usage: dict[str, ResourceUsage] = {}
    cancel_requests: set[str] = set()
    for event in events:
        id = event.delegation_id
        if event.kind == "delegation.created":
            if id in delegations:
                raise ValueError(f"journal creates delegation twice: {id}")
            parent = event.payload.get("parent_id")
            if parent is not None and parent not in delegations:
                raise ValueError(f"journal names unknown parent {parent!r} for {id}")
            spec = DelegationSpec.model_validate(event.payload["spec"])
            root, depth = (id, 0) if parent is None else (
                delegations[parent].root_id, delegations[parent].depth + 1)
            delegations[id] = Delegation.create(id, spec, parent_id=parent,
                                                created_at=str(event.payload["created_at"]),
                                                root_id=root, depth=depth)
            usage[id] = ResourceUsage()
            if parent is not None:
                delegations[parent] = delegations[parent].model_copy(
                    update={"children": (*delegations[parent].children, id)})
            continue
        if id not in delegations:
            raise ValueError(f"journal event for unknown delegation: {id}")
        current = delegations[id]
        if event.kind == "usage.reported":
            usage[id] = usage[id] + ResourceUsage.model_validate(event.payload["usage"])
            continue
        if event.kind == "cancel.requested":
            if current.terminal:
                raise ValueError(f"journal requests cancellation of terminal delegation {id}")
            cancel_requests.add(id)
            continue
        if event.kind == "budget.released" and not current.terminal:
            raise ValueError(f"journal releases the reservation of non-terminal delegation {id}")
        if event.kind in _STATE_EVENTS:
            if current.terminal:
                raise ValueError(f"journal continues terminal delegation {id} with {event.kind}")
            update: dict[str, Any] = {"state": _STATE_EVENTS[event.kind]}
            if "result" in event.payload:
                update["result"] = WorkerResult.model_validate(event.payload["result"])
            if "reason" in event.payload:
                update["terminal_reason"] = str(event.payload["reason"])
            if event.kind == "delegation.recovered":
                update["terminal_reason"] = "interrupted"
                usage[id] = usage[id] + ResourceUsage(unknown=DIMENSIONS)
            delegations[id] = current.model_copy(update=update)
            continue
        if event.kind == "delegation.progress" and current.terminal:
            raise ValueError(f"journal reports progress on terminal delegation {id}")
        if event.kind == "delegation.context":
            delegations[id] = current.model_copy(update={
                key: str(event.payload[key]) for key in _CONTEXT_KEYS if key in event.payload})
    return DelegationTree(delegations, usage, events, frozenset(cancel_requests))


class DelegationStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def _guard(self, *, create: bool) -> WriteGuard:
        root = WriteGuard(self.workspace, create=create)
        return WriteGuard(root.directory / DIRECTORY, create=create)

    @staticmethod
    def _lock(guard: WriteGuard) -> FileLock:
        return FileLock(guard.reserve("journal.lock"))

    @staticmethod
    def _read(guard: WriteGuard) -> tuple[DelegationEvent, ...]:
        if not (guard.directory / JOURNAL).exists():
            return ()
        with guard.open(JOURNAL, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        events: list[DelegationEvent] = []
        previous = None
        for number, line in enumerate(lines):
            raw = json.loads(line)
            if not isinstance(raw, dict) or set(raw) != {"event", "digest"}:
                raise ValueError("delegation journal entry has an invalid envelope")
            event = DelegationEvent.model_validate(raw["event"])
            if event.sequence != number or event.previous != previous or event.digest != raw["digest"]:
                raise ValueError("delegation journal sequence or content digest mismatch")
            events.append(event)
            previous = event.digest
        return tuple(events)

    def events(self) -> tuple[DelegationEvent, ...]:
        if not (self.workspace / DIRECTORY).exists():
            return ()
        guard = self._guard(create=False)
        with self._lock(guard):
            return self._read(guard)

    def tree(self) -> DelegationTree:
        return _replay(self.events())

    def append(self, delegation_id: str, kind: str, payload: dict[str, Any], *,
               now: datetime | None = None) -> DelegationEvent:
        guard = self._guard(create=True)
        with self._lock(guard):
            events = self._read(guard)
            event = DelegationEvent(
                sequence=len(events), previous=events[-1].digest if events else None,
                delegation_id=delegation_id, kind=kind,
                timestamp=(now or datetime.now(UTC)).isoformat(), payload=payload,
            )
            # Validated before it is written: the journal never holds an event
            # its own replay would refuse.
            _replay((*events, event))
            line = json.dumps({"event": event.model_dump(mode="json"), "digest": event.digest},
                              ensure_ascii=False, allow_nan=False) + "\n"
            with guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def artifacts(self, delegation_id: str) -> RunStore:
        self.tree().get(delegation_id)
        guard = self._guard(create=True)
        path = guard.directory / delegation_id
        run_id = uuid5(NAMESPACE_URL, f"hardy:delegation:{delegation_id}")
        if path.exists():
            return RunStore.open(path, run_id=run_id)
        path.mkdir(parents=True)
        return RunStore(path, run_id)

    def cancel_subtree(self, id: str, *, reason: str) -> tuple[str, ...]:
        """Request cancellation of a node and every live descendant, deepest first.

        Queued work has no executor to wait for and is cancelled outright.
        Active work only receives the request; whoever runs it ends it. The
        store records; it does not stop threads.
        """
        tree = self.tree()
        requested = []
        for node in (*reversed(tree.descendants(id)), id):
            delegation = tree.get(node)
            if delegation.terminal or tree.cancel_requested(node):
                continue
            self.append(node, "cancel.requested", {"reason": reason})
            if delegation.state is DelegationState.QUEUED:
                self.append(node, "delegation.cancelled", {"reason": reason})
            requested.append(node)
        return tuple(requested)

    def release(self, id: str) -> None:
        """Return a terminal node's reservation to its parent. Idempotent."""
        tree = self.tree()
        delegation = tree.get(id)
        if not delegation.terminal:
            raise ValueError(f"cannot release the reservation of non-terminal delegation {id}")
        if any(e.kind == "budget.released" and e.delegation_id == id for e in tree.events):
            return None
        self.append(id, "budget.released", {})
        return None

    def recover(self, *, now: str) -> tuple[Delegation, ...]:
        """Mark every active, waiting or paused delegation interrupted. Idempotent."""
        recovered = []
        for delegation in self.tree().delegations.values():
            if delegation.state in INTERRUPTIBLE:
                self.append(delegation.id, "delegation.recovered",
                            {"reason": "interrupted", "recovered_at": now})
                recovered.append(self.tree().get(delegation.id))
        return tuple(recovered)
