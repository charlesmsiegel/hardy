"""Nonblocking orchestration: journal, leases, executor and attention, in that order.

The controller creates delegations, reserves their leases under a root the
session owns, launches leaf workers on the executor and settles their
results into the journal. It holds no mathematics and decides none: the
ledger is read to build a problem core and never written here. Every
state transition is a journal event, so a restart replays exactly what
happened and marks what was interrupted as unknown.
"""
from __future__ import annotations

import json
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
    ResourceDelta,
    ResourceLease,
    ResourceUsage,
    WorkerResult,
)
from hardy.workflows.delegation.diversity import assign_briefs
from hardy.workflows.delegation.findings import Finding, FindingLedger
from hardy.workflows.delegation.retrieval import VisibilityPolicy, WorkerRetriever
from hardy.workflows.delegation.scheduler import (
    AllocationRequest,
    GraphUrgency,
    Lane,
    Pin,
    PinKind,
    PortfolioConstraints,
    Scheduler,
    SchedulerDecision,
    graph_urgency,
)
from hardy.workflows.delegation.store import DelegationStore, DelegationTree
from hardy.workflows.delegation.worker import OpenWorker, WorkerLaunch, run_worker
from hardy.workflows.ledger.contracts import VersionRef
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.storage import RunStore

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
                 papers: PaperToolRuntime | None = None,
                 constraints: PortfolioConstraints | None = None) -> None:
        self.store = store
        self.ledger = ledger
        self.executor = executor
        self.papers = papers
        self.constraints = constraints or PortfolioConstraints()
        #: Created but not yet holding a slot; the scheduler decides when.
        self._pending: dict[str, WorkerLaunch] = {}
        #: Coordinators attached explicitly, never instantiated on worker count.
        self._coordinators: dict[str, Any] = {}
        self._closed = False
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
        """Replay after a restart: interrupted work becomes unknown, never success.

        Work that was queued and never started is still owed a slot; its launch
        package is rebuilt from the artifacts it was recorded with.
        """
        with self._lock:
            recovered = self.store.recover(now=self._clock().isoformat())
            for delegation in recovered:
                event = next(e for e in reversed(self.store.events())
                             if e.delegation_id == delegation.id and e.kind == "delegation.recovered")
                self._after_terminal(delegation.id, event)
            tree = self.tree()
            ledger = LeaseLedger(tree)
            for delegation in tree.delegations.values():
                if (delegation.state is DelegationState.QUEUED and delegation.parent_id is not None
                        and delegation.id not in self._pending and delegation.id not in self._handles):
                    launch = self._relaunch(delegation, ledger.reserved(delegation.id))
                    if launch is not None:
                        self._pending[delegation.id] = launch
            self._dispatch()
            return recovered

    def _relaunch(self, delegation: Delegation, lease: ResourceLease) -> WorkerLaunch | None:
        artifacts = self.store.artifacts(delegation.id)
        prompt_path = artifacts.path / "prompt.md"
        if not prompt_path.exists():
            return None
        return WorkerLaunch(delegation_id=delegation.id, prompt=prompt_path.read_text(encoding="utf-8"),
                            model=delegation.spec.model, store=artifacts, lease=lease,
                            retriever=self._retriever(delegation.spec, artifacts))

    def _retriever(self, spec: DelegationSpec, artifacts: RunStore) -> WorkerRetriever:
        return WorkerRetriever(
            self.ledger, self.papers, VisibilityPolicy(hidden_ids=spec.hidden_ids),
            record=lambda event, store=artifacts: store.append(event["kind"], event["payload"],
                                                               phase=RunPhase.PROVING))

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
                for ancestor_id in (parent, *tree.ancestors(parent)):
                    ancestor = tree.get(ancestor_id)
                    if ancestor.spawn.can_spawn and owner.depth + 1 - ancestor.depth > ancestor.spawn.max_depth:
                        raise ValueError(f"delegation {ancestor_id} allows no descendants deeper than {ancestor.spawn.max_depth}")
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
            if spec.spawn.can_spawn:
                # An interior node: a container for coordinated children. It
                # runs no worker of its own and is live from creation until a
                # coordinator or the human finishes, retires or cancels it.
                self.store.append(id, "delegation.started", {"interior": True})
                return self.tree().get(id)
            launch = WorkerLaunch(delegation_id=id, prompt=render_launch_prompt(core, brief, working),
                                  model=spec.model, store=artifacts, lease=lease,
                                  retriever=self._retriever(spec, artifacts))
            self._pending[id] = launch
            self._dispatch()
            return self.tree().get(id)

    # -- interior nodes and human steering --------------------------------------

    def finish_subtree(self, id: str, *, synthesis: str, by: str) -> Delegation:
        """Close an interior node with a synthesis; children still running are cancelled."""
        with self._lock:
            node = self.tree().get(id)
            if not node.spawn.can_spawn:
                raise ValueError(f"{id} is a leaf; it finishes through its worker")
            if node.terminal:
                raise ValueError(f"{id} is already terminal")
            for child in node.children:
                if not self.tree().get(child).terminal:
                    self.cancel(child, reason=f"parent {id} finished by {by}")
            result = WorkerResult(delegation_id=id, status=DelegationState.COMPLETED, synthesis=synthesis,
                                  usage=ResourceUsage(), findings=tuple(
                                      f.id for f in FindingLedger(self.store).visible_to(id)))
            event = self.store.append(id, "delegation.completed", {"result": result.model_dump(mode="json"),
                                                                   "reason": f"finished by {by}"})
            self._after_terminal(id, event)
            return self.tree().get(id)

    def pause(self, id: str, *, by: str) -> None:
        with self._lock:
            node = self.tree().get(id)
            if node.state is not DelegationState.QUEUED:
                raise ValueError(f"only queued work can be paused; {id} is {node.state.value}")
            self.store.append(id, "delegation.paused", {"by": by})

    def resume(self, id: str, *, by: str) -> None:
        with self._lock:
            node = self.tree().get(id)
            if node.state is not DelegationState.PAUSED:
                raise ValueError(f"{id} is not paused")
            self.store.append(id, "delegation.resumed", {"by": by})
            self._dispatch()

    def set_lane(self, id: str, lane: Lane, *, by: str) -> None:
        with self._lock:
            self.tree().get(id)
            self.store.append(id, "scheduler.lane_changed", {"lane": lane.value, "by": by})
            self._dispatch()

    def request_human_decision(self, id: str, question: str, *, by: str) -> None:
        with self._lock:
            self.tree().get(id)
            event = self.store.append(id, "coordinator.human_decision_requested", {"question": question, "by": by})
            self._route(id, event)

    def attach_coordinator(self, id: str, coordinator: Any) -> None:
        """Explicit: a coordinator exists because somebody decided one was useful here."""
        with self._lock:
            self.tree().get(id)
            self._coordinators[id] = coordinator

    def coordinator_for(self, id: str) -> Any | None:
        return self._coordinators.get(id)

    # -- scheduling ---------------------------------------------------------

    def _pins(self) -> tuple[Pin, ...]:
        active: dict[tuple[str, str], Pin] = {}
        for event in self.store.events():
            if event.kind == "scheduler.pin":
                pin = Pin.model_validate(event.payload["pin"])
                active[(pin.delegation_id, pin.kind)] = pin
            elif event.kind == "scheduler.unpin":
                active.pop((event.delegation_id, str(event.payload["kind"])), None)
        return tuple(active.values())

    def pins(self) -> tuple[tuple[str, str], ...]:
        return tuple((pin.delegation_id, pin.kind) for pin in self._pins())

    def _lanes(self) -> dict[str, Lane]:
        lanes: dict[str, Lane] = {}
        for event in self.store.events():
            if event.kind == "scheduler.lane_changed":
                lanes[event.delegation_id] = Lane(str(event.payload["lane"]))
        return lanes

    def _scheduler(self) -> Scheduler:
        tree = self.tree()
        snapshot = self.ledger.read()
        cache: dict[str, GraphUrgency | None] = {}

        def urgency(delegation: Delegation) -> GraphUrgency | None:
            if delegation.id not in cache:
                refs = delegation.spec.project_refs
                try:
                    cache[delegation.id] = graph_urgency(snapshot, refs[0]) if refs else None
                except ValueError:
                    cache[delegation.id] = None
            return cache[delegation.id]

        return Scheduler(tree, LeaseLedger(tree), constraints=self.constraints, pins=self._pins(),
                         lanes=self._lanes(), urgency=urgency)

    def _dispatch(self) -> None:
        """Hand free slots to the leaves the scheduler chooses; nothing else starts work."""
        with self._lock:
            free = self.executor.slots - len(self._handles)
            if free <= 0 or not self._pending or self._closed:
                return
            for chosen in self._scheduler().choose(free):
                launch = self._pending.get(chosen.id)
                if launch is None:
                    continue
                try:
                    handle = self.executor.submit(
                        WorkerJob(chosen.id, lambda token, launch=launch: self._run(launch, token)))
                except RuntimeError:
                    # The executor is gone; the work stays queued for a restart to relaunch.
                    return
                self._pending.pop(chosen.id, None)
                self._handles[chosen.id] = handle
                handle.add_done_callback(self._settle)

    def pin(self, id: str, kind: PinKind, *, by: str, value: int | None = None) -> Pin:
        with self._lock:
            self.tree().get(id)
            pin = Pin(delegation_id=id, kind=kind, by=by, value=value)
            self.store.append(id, "scheduler.pin", {"pin": pin.model_dump(mode="json")})
            self._dispatch()
            return pin

    def unpin(self, id: str, kind: PinKind, *, by: str) -> None:
        with self._lock:
            self.tree().get(id)
            self.store.append(id, "scheduler.unpin", {"kind": kind, "by": by})
            self._dispatch()

    def reinforce(self, id: str, delta: ResourceDelta, *, by: str, reason: str) -> SchedulerDecision:
        """A tranche or a reclaim, decided mechanically and journaled with its provenance."""
        with self._lock:
            scheduler = self._scheduler()
            delegation = self.tree().get(id)
            request = AllocationRequest(delegation_id=id, lane=scheduler.lane(delegation), tranche=delta,
                                        requested_by=by, reason=reason)
            decision = scheduler.decide(request)
            decision = decision.model_copy(update={"sequence": self.tree().revision})
            self.store.append(id, "scheduler.decision", {"decision": decision.model_dump(mode="json")})
            if decision.granted is not None:
                self.store.append(id, "budget.reserved", {"lease": decision.resulting.model_dump(mode="json"),
                                                          "slots": scheduler.ledger.slots(id)})
            self._dispatch()
            return decision

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
            self._propose_findings(id, launch)
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
        # A freed slot goes to whatever the scheduler chooses next.
        self._dispatch()

    def _propose_findings(self, id: str, launch: WorkerLaunch) -> None:
        """Every finding the worker recorded reaches its parent; priority ones draw attention."""
        path = launch.store.path / "findings.json"
        if not path.exists():
            return
        ledger = FindingLedger(self.store)
        for raw in json.loads(path.read_text(encoding="utf-8")):
            finding = ledger.propose(Finding.model_validate(raw))
            event = next(e for e in reversed(self.store.events())
                         if e.kind == "finding.proposed" and e.payload["finding"]["id"] == finding.id)
            self._route(id, event)

    def _after_terminal(self, id: str, event: DelegationEvent) -> None:
        self.store.release(id)
        self._route(id, event)

    def _route(self, id: str, event: DelegationEvent) -> None:
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
                self._pending.pop(node, None)
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
            if delegation.terminal and id not in self._handles and id not in self._pending:
                return delegation
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"delegation {id} did not finish within {timeout}s")
            time.sleep(0.02)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self.executor.shutdown(wait=True)
