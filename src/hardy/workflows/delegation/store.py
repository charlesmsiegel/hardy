"""Append-only delegation journal; verified replay is memoized within an owner.

One JSONL journal per workspace, hash-chained like the ledger's transactions and
guarded by an OS lock. Each delegation owns a RunStore directory beside it for
artifacts and its trajectory. Recovery after a crash marks work that was active
as `unknown` with every usage dimension unknown: interrupted work is neither
success, cancellation, exhaustion, nor never-started work.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.workflows.delegation.budget import LeaseLedger, LeaseRefused
from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    Delegation,
    DelegationEvent,
    DelegationState,
    ResourceLease,
)
from hardy.workflows.delegation.replay import DelegationTree as DelegationTree
from hardy.workflows.delegation.replay import replay as _replay
from hardy.workflows.storage import RunStore

DIRECTORY = "delegations"
JOURNAL = "journal.jsonl"

#: What a dead process may have been in the middle of. A pause holds no worker and no
#: slot, so it is a control that survives a restart rather than work to doubt.
INTERRUPTIBLE = frozenset({DelegationState.ACTIVE, DelegationState.WAITING})


#: A delegation id is one path segment; the store never joins anything else onto the journal directory.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


def _refuse_overspawn(tree: DelegationTree, event: DelegationEvent) -> None:
    """A parent's frozen spawn policy is re-checked as the child is recorded, under the journal lock."""
    parent = event.payload.get("parent_id")
    if parent is None or parent not in tree.delegations:
        return
    owner = tree.get(parent)
    if owner.spawn.can_spawn and len(owner.children) >= owner.spawn.max_children:
        raise ValueError(f"parent delegation {parent} may not spawn another child: max_children "
                         f"{owner.spawn.max_children} already recorded")


class OwnerToken:
    """A process's claim on the workers it runs, held as an OS lock for as long as it lives.

    Recovery asks whether a token's lock can be taken: if it can, the owner is
    gone and its work is interrupted; if it cannot, another live process is
    still running that work and this one leaves it alone.
    """

    DIRECTORY = "owners"

    def __init__(self, workspace: Path, id: str, lock: FileLock) -> None:
        self.workspace = Path(workspace)
        self.id = id
        self._lock = lock

    @classmethod
    def _path(cls, workspace: Path, id: str) -> Path:
        guard = WriteGuard(WriteGuard(Path(workspace), create=True).directory / DIRECTORY, create=True)
        owners = WriteGuard(guard.directory / cls.DIRECTORY, create=True)
        return owners.reserve(f"{id}.lock")

    @classmethod
    def hold(cls, workspace: Path) -> OwnerToken:
        id = uuid4().hex[:16]
        lock = FileLock(cls._path(workspace, id), timeout=1.0).__enter__()
        return cls(workspace, id, lock)

    @classmethod
    def alive(cls, workspace: Path, id: str) -> bool:
        probe = FileLock(cls._path(workspace, id), timeout=0.0, required=False).__enter__()
        try:
            return not probe.held
        finally:
            if probe.held:
                probe.__exit__(None, None, None)

    def release(self) -> None:
        if self._lock.held:
            self._lock.__exit__(None, None, None)

    def __enter__(self) -> OwnerToken:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _refuse_overdraft(tree: DelegationTree, delegation_id: str, event: DelegationEvent) -> None:
    """A reservation may only take what its parent can still allocate at the moment it is recorded."""
    node = tree.get(delegation_id)
    if node.parent_id is None:
        return
    ledger = LeaseLedger(tree)
    wanted = ResourceLease.model_validate(event.payload["lease"])
    available = ledger.allocatable_excluding(node.parent_id, delegation_id)
    over = []
    for name in DIMENSIONS:
        ceiling, asked = getattr(available, name), getattr(wanted, name)
        if ceiling is None:
            continue
        if asked is None or asked > ceiling:
            over.append(f"{name} ({asked} asked, {ceiling} allocatable)")
    if over:
        raise LeaseRefused(f"reservation exceeds parent allocatable resources: {', '.join(over)}")


class DelegationStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self._cached_tree = _replay(())
        self._signature = None

    @staticmethod
    def _stamp(guard: WriteGuard):
        path = guard.path(JOURNAL)
        if not path.exists():
            return None
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _refresh(self, guard: WriteGuard) -> tuple[DelegationEvent, ...]:
        stamp = self._stamp(guard)
        if stamp != self._signature:
            events = self._read(guard)
            verified = self._cached_tree.events
            if events[:len(verified)] != verified:
                raise ValueError("delegation journal rewrites verified history")
            self._cached_tree = _replay(events)
            self._signature = stamp
        return self._cached_tree.events

    def _snapshot(self) -> DelegationTree:
        return self._cached_tree.detached()

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
            if self._cached_tree.events:
                raise ValueError("delegation journal removes verified history")
            return ()
        guard = self._guard(create=False)
        with self._lock(guard):
            return deepcopy(self._refresh(guard))

    def tree(self) -> DelegationTree:
        if not (self.workspace / DIRECTORY).exists():
            if self._cached_tree.events:
                raise ValueError("delegation journal removes verified history")
            return _replay(())
        guard = self._guard(create=False)
        with self._lock(guard):
            self._refresh(guard)
            return self._snapshot()

    def append(self, delegation_id: str, kind: str, payload: dict[str, Any], *,
               now: datetime | None = None) -> DelegationEvent:
        guard = self._guard(create=True)
        with self._lock(guard):
            return self._append(guard, self._refresh(guard), delegation_id, kind, payload, now=now)

    def append_decided(self, delegation_id: str, kind: str,
                       decide: Callable[[DelegationTree], dict[str, Any] | None], *,
                       now: datetime | None = None) -> DelegationEvent | None:
        """Append what `decide` makes of the current tree, read and written under one lock.

        For an event whose payload depends on the journal itself -- the root's
        epoch -- so another process cannot append between the read and the
        write. `decide` answering None appends nothing.
        """
        guard = self._guard(create=True)
        with self._lock(guard):
            events = self._refresh(guard)
            payload = decide(self._snapshot())
            if payload is None:
                return None
            return self._append(guard, events, delegation_id, kind, payload, now=now)

    def _append(self, guard: WriteGuard, events: tuple[DelegationEvent, ...], delegation_id: str, kind: str,
                payload: dict[str, Any], *, now: datetime | None) -> DelegationEvent:
        """Validate and write one event after `events`; the caller holds the journal lock."""
        event = DelegationEvent(
            sequence=len(events), previous=events[-1].digest if events else None,
            delegation_id=delegation_id, kind=kind,
            timestamp=(now or datetime.now(UTC)).isoformat(), payload=deepcopy(payload),
        )
        # Validated before it is written: the journal never holds an event
        # its own replay would refuse.
        before = self._cached_tree
        after = _replay((event,), before)
        if kind == "budget.reserved":
            # Re-checked here, under the journal's own lock: two processes
            # that both passed `grant` on one stale balance cannot both record.
            _refuse_overdraft(before, delegation_id, event)
        if kind == "delegation.created":
            _refuse_overspawn(before, event)
        line = json.dumps({"event": event.model_dump(mode="json"), "digest": event.digest},
                          ensure_ascii=False, allow_nan=False) + "\n"
        with guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._cached_tree = after
        self._signature = self._stamp(guard)
        return event.model_copy(deep=True)

    def artifacts(self, delegation_id: str) -> RunStore:
        """The per-delegation artifact store, created on first use.

        Not gated on the journal: the launch package is written before the
        `delegation.created` event exposes a queued node, so a crash between
        the two leaves unreferenced artifacts rather than an unlaunchable job.
        """
        if not _ID.fullmatch(delegation_id):
            raise ValueError(f"invalid delegation id: {delegation_id}")
        guard = self._guard(create=True)
        path = guard.directory / delegation_id
        run_id = uuid5(NAMESPACE_URL, f"hardy:delegation:{delegation_id}")
        if path.exists():
            return RunStore.open(path, run_id=run_id)
        path.mkdir(parents=True)
        return RunStore(path, run_id)

    def cancel_subtree(self, id: str, *, reason: str) -> tuple[str, ...]:
        """Request cancellation of a node and every live descendant, deepest first.

        Queued or paused work has no executor to wait for and is cancelled outright.
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
            if delegation.state in {DelegationState.QUEUED, DelegationState.PAUSED}:
                # Nothing runs it, so nothing else will ever end it.
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
        """Mark every active or waiting delegation interrupted; paused work stays paused. Idempotent.

        Work whose owning process still holds its token is another live
        process's, not this one's to retire.
        """
        recovered = []
        for delegation in self.tree().delegations.values():
            # An interior cell runs no worker: it is structure the next process
            # continues, not work the last one was in the middle of.
            if delegation.state in INTERRUPTIBLE and not delegation.interior:
                if delegation.owner is not None and OwnerToken.alive(self.workspace, delegation.owner):
                    continue
                self.append(delegation.id, "delegation.recovered",
                            {"reason": "interrupted", "recovered_at": now})
                recovered.append(self.tree().get(delegation.id))
        return tuple(recovered)
