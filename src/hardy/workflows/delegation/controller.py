"""Nonblocking orchestration: journal, leases, executor and attention, in that order.

The controller creates delegations, reserves their leases under a root the
session owns, launches leaf workers on the executor and settles their
results into the journal. It holds no mathematics and decides none: the
ledger is read to build a problem core and never written here. Every
state transition is a journal event, so a restart replays exactly what
happened and marks what was interrupted as unknown.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from hardy.agents.executor import (
    CancelToken,
    WorkerCancelled,
    WorkerExecutor,
    WorkerHandle,
    WorkerJob,
)
from hardy.literature.metadata import ArxivError, parse_id
from hardy.workflows.contracts import RunPhase
from hardy.workflows.delegation.attention import AttentionInbox
from hardy.workflows.delegation.budget import LeaseLedger, grant
from hardy.workflows.delegation.context import (
    ContextPolicy,
    build_problem_core,
    build_working_set,
    render_launch_prompt,
)
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    Delegation,
    DelegationEvent,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    WorkerResult,
)
from hardy.workflows.delegation.diversity import assign_briefs
from hardy.workflows.delegation.retrieval import VisibilityPolicy, WorkerRetriever
from hardy.workflows.delegation.store import DelegationStore, DelegationTree
from hardy.workflows.delegation.worker import OpenWorker, WorkerLaunch, run_worker
from hardy.workflows.ledger.contracts import VersionRef
from hardy.workflows.ledger.store import LedgerStore

if TYPE_CHECKING:
    from hardy.literature.tools import PaperToolRuntime

#: The synthetic node every user-created job hangs from; it owns the root ceilings.
ROOT_ID = "root"

_TERMINAL_EVENT = {
    DelegationState.COMPLETED: "delegation.completed",
    DelegationState.PARTIAL: "delegation.partial",
    DelegationState.FAILED: "delegation.failed",
    DelegationState.CANCELLED: "delegation.cancelled",
    DelegationState.EXHAUSTED: "delegation.exhausted",
    DelegationState.UNKNOWN: "delegation.recovered",
}


@dataclass(frozen=True)
class RootResources:
    lease: ResourceLease
    slots: int


class DelegationController:
    def __init__(self, store: DelegationStore, ledger: LedgerStore, *, executor: WorkerExecutor,
                 open_worker: OpenWorker, root: RootResources, notify: Callable[[str], None],
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 papers: PaperToolRuntime | None = None) -> None:
        self.store = store
        self.ledger = ledger
        self.executor = executor
        self.papers = papers
        self._open_worker = open_worker
        self.root = root
        self._notify = notify
        self._clock = clock
        self._lock = threading.RLock()
        self._handles: dict[str, WorkerHandle] = {}
        self._inbox = AttentionInbox(store)

    # -- views --------------------------------------------------------------

    def tree(self) -> DelegationTree:
        return self.store.tree()

    def attention(self) -> AttentionInbox:
        return self._inbox

    def status(self) -> dict[str, Any]:
        tree = self.tree()
        ledger = LeaseLedger(tree)
        counts: dict[str, int] = {}
        for delegation in tree.delegations.values():
            if delegation.id == ROOT_ID:
                continue
            counts[delegation.state.value] = counts.get(delegation.state.value, 0) + 1
        root: dict[str, Any] = {}
        if ROOT_ID in tree.delegations:
            root = {
                "lease": ledger.reserved(ROOT_ID).model_dump(mode="json"),
                "usage": ledger.usage(ROOT_ID).model_dump(mode="json"),
                "allocatable": ledger.allocatable(ROOT_ID).model_dump(mode="json"),
                "slots": ledger.slots(ROOT_ID), "slots_in_use": ledger.slots_in_use(ROOT_ID),
            }
        return {
            "counts": counts, "root": root,
            "attention": {"human": len(self._inbox.pending("human")),
                          "main_agent": len(self._inbox.pending("main_agent"))},
        }

    def inspect(self, id: str) -> dict[str, Any]:
        tree = self.tree()
        delegation = tree.get(id)
        ledger = LeaseLedger(tree)
        return {
            "delegation": delegation.model_dump(mode="json"),
            "usage": ledger.usage(id).model_dump(mode="json"),
            "lease": ledger.reserved(id).model_dump(mode="json"),
            "released": ledger.released(id),
            "cancel_requested": tree.cancel_requested(id),
            "attention": [item.model_dump(mode="json") for item in self._inbox.items()
                          if item.delegation_id == id],
            "artifacts": str(self.store.artifacts(id).path),
        }

    # -- lifecycle ----------------------------------------------------------

    def recover(self) -> tuple[Delegation, ...]:
        """Replay after a restart: interrupted work becomes unknown, never success."""
        with self._lock:
            recovered = self.store.recover(now=self._clock().isoformat())
            for delegation in recovered:
                event = next(e for e in reversed(self.store.events())
                             if e.delegation_id == delegation.id and e.kind == "delegation.recovered")
                self._after_terminal(delegation.id, event)
            return recovered

    def _ensure_root(self, scope: VersionRef) -> None:
        tree = self.tree()
        lease = ResourceLease.model_validate(self.root.lease.model_dump())
        if ROOT_ID not in tree.delegations:
            spec = DelegationSpec(objective="session root resources", project_refs=(), scope=scope,
                                  lease=lease, concurrency=ConcurrencyLease(slots=self.root.slots),
                                  created_by="session", notify_human=False)
            self.store.append(ROOT_ID, "delegation.created", {
                "spec": spec.model_dump(mode="json"), "parent_id": None,
                "created_at": self._clock().isoformat()})
            self.store.append(ROOT_ID, "budget.reserved", {"lease": lease.model_dump(mode="json"),
                                                          "slots": self.root.slots})
            return
        ledger = LeaseLedger(tree)
        if ledger.reserved(ROOT_ID) != lease or ledger.slots(ROOT_ID) != self.root.slots:
            # The session's ceilings moved between runs; the root follows them.
            self.store.append(ROOT_ID, "budget.reserved", {"lease": lease.model_dump(mode="json"),
                                                          "slots": self.root.slots})

    def delegate(self, spec: DelegationSpec, *, parent_id: str | None = None) -> Delegation:
        spec = DelegationSpec.model_validate(spec.model_dump())
        if not spec.project_refs:
            raise ValueError("a delegation needs at least one exact project ref")
        target = spec.project_refs[0]
        core = build_problem_core(self.ledger, target, scope=spec.scope)
        # One worker is the direct role; its framing carries the objective as asked.
        (brief,) = assign_briefs(target, 1, task_mode=spec.task_mode, model=spec.model)
        brief = brief.model_copy(update={"framing": f"{brief.framing} Objective: {spec.objective}"})
        policy = ContextPolicy(hidden_ids=spec.hidden_ids, seeded_sources=spec.seeded_sources)
        working = build_working_set(self.ledger, target, spec.scope, brief, policy,
                                    sources=self._source_index(spec.seeded_sources))
        with self._lock:
            self._ensure_root(spec.scope)
            parent = parent_id or ROOT_ID
            tree = self.tree()
            owner = tree.get(parent)
            if owner.terminal:
                raise ValueError(f"parent delegation {parent} is terminal")
            if parent != ROOT_ID:
                policy = owner.spawn
                if not policy.can_spawn or len(owner.children) >= policy.max_children:
                    raise ValueError(f"parent delegation {parent} may not spawn another child")
            lease, _ = grant(tree, parent, spec.lease, requested_slots=spec.concurrency.slots)
            id = f"d-{uuid4().hex[:10]}"
            now = self._clock().isoformat()
            self.store.append(id, "delegation.created", {"spec": spec.model_dump(mode="json"),
                                                         "parent_id": parent, "created_at": now})
            self.store.append(id, "budget.reserved", {"lease": lease.model_dump(mode="json"),
                                                      "slots": spec.concurrency.slots})
            artifacts = self.store.artifacts(id)
            manifest = working.manifest(f"{id}:manifest", problem_core_digest=core.digest,
                                        research_brief_digest=brief.digest)
            artifacts.write_json(PurePosixPath("core.json"), core)
            artifacts.write_json(PurePosixPath("brief.json"), brief)
            artifacts.write_json(PurePosixPath("manifest.json"), manifest)
            self.store.append(id, "delegation.context", {
                "problem_core_digest": core.digest, "research_brief_digest": brief.digest,
                "context_manifest_id": manifest.id})
            retriever = WorkerRetriever(
                self.ledger, self.papers, VisibilityPolicy(hidden_ids=spec.hidden_ids),
                record=lambda event, store=artifacts: store.append(event["kind"], event["payload"],
                                                                   phase=RunPhase.PROVING))
            launch = WorkerLaunch(delegation_id=id, prompt=render_launch_prompt(core, brief, working),
                                  model=spec.model, store=artifacts, lease=lease, retriever=retriever)
            handle = self.executor.submit(WorkerJob(id, lambda token, launch=launch: self._run(launch, token)))
            self._handles[id] = handle
            handle.add_done_callback(self._settle)
            return self.tree().get(id)

    def _source_index(self, paper_ids: tuple[str, ...]) -> dict[str, str]:
        """One index line per seeded source the library holds; never the body."""
        index: dict[str, str] = {}
        if self.papers is None:
            return index
        for paper_id in paper_ids:
            try:
                record = self.papers.library.read(parse_id(paper_id))
            except (ArxivError, OSError, ValueError):
                continue
            authors = ", ".join(record.authors[:3]) + (" et al." if len(record.authors) > 3 else "")
            abstract = " ".join(record.abstract.split())
            index[paper_id] = f"{record.title} ({authors}): {abstract[:200]}"
        return index

    def _run(self, launch: WorkerLaunch, token: CancelToken) -> WorkerResult:
        id = launch.delegation_id
        with self._lock:
            tree = self.tree()
            if tree.get(id).terminal:
                raise WorkerCancelled
            self.store.append(id, "delegation.started", {})
        result = run_worker(launch, self._open_worker, token)
        with self._lock:
            self.store.append(id, "usage.reported", {"usage": result.usage.model_dump(mode="json")})
            event = self.store.append(id, _TERMINAL_EVENT[result.status], {
                "result": result.model_dump(mode="json"),
                **({"reason": result.terminal_reason} if result.terminal_reason else {})})
            self._after_terminal(id, event)
        return result

    def _settle(self, handle: WorkerHandle) -> None:
        """Whatever the job did or failed to do, the journal ends up terminal and released."""
        with self._lock:
            id = handle.name
            tree = self.tree()
            delegation = tree.get(id)
            if not delegation.terminal:
                try:
                    handle.result(0)
                    reason = "worker returned without a terminal record"
                except WorkerCancelled:
                    reason = "cancelled"
                except Exception as error:  # noqa: BLE001 - a job's crash is a journaled failure
                    reason = f"{type(error).__name__}: {error}"
                kind = "delegation.cancelled" if reason == "cancelled" else "delegation.failed"
                event = self.store.append(id, kind, {"reason": reason})
                self._after_terminal(id, event)
            elif not LeaseLedger(tree).released(id):
                self.store.release(id)
            # Last, so `wait` sees a settled journal once the handle is gone.
            self._handles.pop(id, None)

    def _after_terminal(self, id: str, event: DelegationEvent) -> None:
        self.store.release(id)
        item = self._inbox.derive(event, self.tree())
        if item is None:
            return
        self._inbox.record(item)
        try:
            self._notify(item.summary)
        except Exception:  # noqa: BLE001 - a notice that cannot be shown is not delivered
            return
        self._inbox.receipt(item.id, "human", "notify")

    def cancel(self, id: str, *, reason: str = "user") -> tuple[str, ...]:
        with self._lock:
            requested = self.store.cancel_subtree(id, reason=reason)
            for node in requested:
                handle = self._handles.get(node)
                if handle is not None:
                    handle.cancel()
                delegation = self.tree().get(node)
                if delegation.state is DelegationState.CANCELLED:
                    # Queued work the store cancelled outright: settle it now
                    # rather than when the executor gets round to refusing it.
                    event = next(e for e in reversed(self.store.events())
                                 if e.delegation_id == node and e.kind == "delegation.cancelled")
                    self._after_terminal(node, event)
            return requested

    def wait(self, id: str, timeout: float | None = None) -> Delegation:
        """Block until the journal shows a terminal state. For tests and command-line callers."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            delegation = self.tree().get(id)
            if delegation.terminal and id not in self._handles:
                return delegation
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"delegation {id} did not finish within {timeout}s")
            time.sleep(0.02)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True)
