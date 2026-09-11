"""One leaf worker: an independent provider context, bounded tools, a structured result.

The worker owns its conversation, its trajectory and its findings. It never
receives the main session's transcript, dispatcher or tools; the session
opens a fresh runtime for it through the same factory it uses for itself.
What comes back is a WorkerResult and artifacts under the delegation's store,
never a transcript pasted into anyone else's context.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from hardy.agents.contracts import ChatRuntime
from hardy.agents.executor import CancelToken, WorkerCancelled
from hardy.agents.usage import Usage
from hardy.formal.budget import BudgetExhausted, CheckBudget
from hardy.formal.syntax import WorkspacePathError, safe_relative
from hardy.foundation.values import ToolResult
from hardy.prompts import DELEGATION_WORKER_PROMPT
from hardy.workflows.contracts import RunPhase
from hardy.workflows.delegation.contracts import (
    DelegationState,
    ResourceLease,
    ResourceUsage,
    WorkerResult,
)
from hardy.workflows.delegation.findings import Finding
from hardy.workflows.delegation.retrieval import WorkerRetriever
from hardy.workflows.delegation.workspace import WorkspaceOverlay
from hardy.workflows.ledger.contracts import VersionRef
from hardy.workflows.storage import RunStore

if TYPE_CHECKING:
    from hardy.algebra.tools import CasToolRuntime

#: The worker's system prompt, kept with every other prompt under hardy/prompts.
WORKER_SYSTEM_PROMPT = DELEGATION_WORKER_PROMPT

WORKER_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "propose_finding", "description": "Record one structured discovery: a candidate lemma, reduction, counterexample, computation, literature lead, obstruction, failed approach, strategy, question or note. Proposing a finding never resolves anything; it is provenance the mathematician can inspect.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "summary": {"type": "string"}, "payload": {"type": "string"}, "related_refs": {"type": "array", "items": {"type": "string"}}}, "required": ["kind", "summary", "payload"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "finish", "description": "End this delegation with a terminal status (completed, partial or failed) and a short synthesis. Call it exactly once; nothing after it is accepted.", "parameters": {"type": "object", "properties": {"status": {"type": "string"}, "synthesis": {"type": "string"}}, "required": ["status", "synthesis"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_project", "description": "Search the current project ledger by keywords: items, their statements and their trust status as the ledger records it. New results proved since you were launched are visible here; your assigned target does not change.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_item", "description": "Read one project item exactly, by id or id@digest: its statement, trust status and dependencies.", "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_neighborhood", "description": "What one project item depends on and what depends on it, one step out.", "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_literature", "description": "Search the literature for leads. State your intent (matching conclusions, matching hypotheses, counterexamples, stronger theorems, analogues, surveys); leads are pointers and abstracts, never evidence.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "intent": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query", "intent"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_source", "description": "Read a bounded window of one paper's exact text by its versioned id, fetching it first if needed. `start_line` pages; `file` names one source file once the source bundle is held.", "parameters": {"type": "object", "properties": {"paper_id": {"type": "string"}, "start_line": {"type": "integer"}, "file": {"type": "string"}}, "required": ["paper_id"], "additionalProperties": False}}},
]

RETRIEVAL_TOOLS = frozenset({"read_project", "read_item", "read_neighborhood", "search_literature", "read_source"})

WORKSPACE_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "check_lean", "description": "Build one Lean file, and everything in your private overlay that imports it, without keeping it. Charged as one official check.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "source": {"type": "string"}}, "required": ["path", "source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_lean", "description": "Build and keep one Lean file in your private overlay. Nothing here reaches the project until admission re-verifies it against the current head. Charged as one official check.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "source": {"type": "string"}}, "required": ["path", "source"], "additionalProperties": False}}},
]
WORKER_TOOLS += WORKSPACE_TOOLS

CAS_WORKER_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "cas_run", "description": "Run one cell in a computer algebra kernel private to this delegation. State carries over between your own cells only. No computation is evidence.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "cas_state", "description": "List the accepted cells of your private computer algebra kernel.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]
WORKER_TOOLS += CAS_WORKER_TOOLS

CasFactory = Callable[[Path], "CasToolRuntime | None"]

#: Active-time ceilings are enforced by the executor and the lease; the check
#: budget only needs a finite deadline to construct.
_UNBOUNDED_SECONDS = 10.0 ** 9
#: An unbounded check lease, as a count the budget can hold: never zero.
_UNBOUNDED_CHECKS = 10 ** 9

FINISH_STATUSES = {
    "completed": DelegationState.COMPLETED,
    "partial": DelegationState.PARTIAL,
    "failed": DelegationState.FAILED,
}


@dataclass(frozen=True)
class WorkerLaunch:
    delegation_id: str
    prompt: str
    model: str | None
    store: RunStore
    lease: ResourceLease
    retriever: WorkerRetriever | None = None
    overlay: WorkspaceOverlay | None = None
    cas_factory: CasFactory | None = None
    #: Told the worker's live check budget once it exists, so a granted tranche can reach it.
    on_budget: Callable[[CheckBudget], None] | None = None
    #: Findings pushed to this worker since its last provider boundary; rendered into the next one.
    pushes: Callable[[], tuple[Finding, ...]] | None = None


def render_pushes(findings: tuple[Finding, ...]) -> str:
    """Pushed findings as one block a worker reads at its next provider boundary."""
    lines = ["[Hardy delegation push] Findings your cell pushed to you; weigh them before your next step:"]
    for finding in findings:
        detail = " ".join(finding.payload.split())[:240]
        lines.append(f"- {finding.id} ({finding.kind}, {finding.evidence_profile.value}): {finding.summary}"
                     + (f" -- {detail}" if detail else ""))
    return "\n".join(lines)


@dataclass(frozen=True)
class OpenedWorker:
    """An independently opened provider context, like a race attempt."""

    context_id: str
    runtime: ChatRuntime
    usage: Callable[[], Usage | None]


Dispatch = Callable[[str, dict[str, Any]], ToolResult]
Observe = Callable[[dict[str, Any]], None]
OpenWorker = Callable[[WorkerLaunch, Dispatch, Observe], OpenedWorker]


class _NoTime(Exception):
    """A lease dimension was spent before the worker opened; the message names it."""


class _WorkerState:
    """Worker-private, mutable, and thrown away with the worker."""

    def __init__(self, launch: WorkerLaunch, token: CancelToken) -> None:
        self.launch = launch
        self.token = token
        self.findings: list[Finding] = []
        self.finished: tuple[DelegationState, str] | None = None
        seconds = launch.lease.active_seconds if launch.lease.active_seconds is not None else _UNBOUNDED_SECONDS
        checks = launch.lease.official_checks if launch.lease.official_checks is not None else _UNBOUNDED_CHECKS
        self.checks = CheckBudget(official_checks=checks, active_seconds=seconds, proof_seconds=seconds)
        if launch.on_budget is not None:
            launch.on_budget(self.checks)
        #: Set by the active-time timer: the provider was still running when the lease ran out.
        self.deadline_hit = False
        #: One tool call at a time: the SDK may dispatch several concurrently, and the
        #: overlay, the finding sequence and the finish flag are one worker's state.
        self._serial = threading.Lock()
        self.cas: CasToolRuntime | None = None
        self.cas_opened = False

    def tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        with self._serial:
            return self._tool(name, arguments)

    def _tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self.finished is not None:
            return ToolResult(False, "this delegation already called finish; nothing further is accepted")
        if name == "propose_finding":
            exact, plain = self._related(arguments.get("related_refs") or [])
            finding = Finding(
                id=f"{self.launch.delegation_id}:finding:{len(self.findings)}",
                source_delegation=self.launch.delegation_id,
                kind=str(arguments["kind"]), summary=str(arguments["summary"]),
                payload=str(arguments.get("payload") or ""),
                related_refs=exact, related_ids=plain,
                sequence=len(self.findings),
            )
            self.findings.append(finding)
            return ToolResult(True, f"recorded finding {finding.id}")
        if name == "finish":
            status = FINISH_STATUSES.get(str(arguments.get("status", "")).strip().lower())
            if status is None:
                return ToolResult(False, "finish requires status completed, partial or failed")
            self.finished = (status, str(arguments.get("synthesis") or ""))
            return ToolResult(True, f"delegation {self.launch.delegation_id} finished {status.value}")
        if name in RETRIEVAL_TOOLS:
            return self.retrieve(name, arguments)
        if name in {"check_lean", "save_lean"}:
            return self.lean(name, arguments)
        if name in {"cas_run", "cas_state"}:
            return self.algebra(name, arguments)
        return ToolResult(False, f"unknown tool: {name}")

    def _related(self, selectors: list[Any]) -> tuple[tuple[VersionRef, ...], tuple[str, ...]]:
        """`id@digest` is an exact ref; a bare id is pinned to the head this worker can see, or stays an id.

        Pinned here, at the moment of the claim, so a project that advances
        while the worker runs cannot rebind its finding to a statement it
        never read. An id this worker may not see, or that names nothing,
        stays a bare id rather than being resolved on its behalf.
        """
        exact: list[VersionRef] = []
        plain: list[str] = []
        retriever = self.launch.retriever
        for raw in selectors:
            text = str(raw).strip()
            if "@" in text:
                identity, digest = text.rsplit("@", 1)
                exact.append(VersionRef(id=identity, digest=digest))
                continue
            ref = None
            if retriever is not None:
                try:
                    record = retriever.ledger.read().head(text)
                except (ValueError, OSError):
                    record = None
                if record is not None and retriever.policy.permits_ref(record.ref):
                    ref = record.ref
            if ref is None:
                plain.append(text)
            else:
                exact.append(ref)
        return tuple(exact), tuple(plain)

    def lean(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        overlay = self.launch.overlay
        if overlay is None:
            return ToolResult(False, "this delegation has no writable workspace")
        try:
            relative = safe_relative(str(arguments["path"]))
        except WorkspacePathError as error:
            return ToolResult(False, str(error))
        source = str(arguments["source"]).rstrip() + "\n"
        try:
            self.checks.acquire()
        except BudgetExhausted:
            return ToolResult(False, "official check budget exhausted for this delegation; finish with what you have")
        failure = overlay.check(relative, source) if name == "check_lean" else overlay.save(relative, source)
        if failure is not None:
            verb = "check" if name == "check_lean" else "save"
            return ToolResult(False, f"this {verb} breaks {failure.module}, so nothing was written:\n{failure.output}")
        if name == "check_lean":
            return ToolResult(True, f"{relative.as_posix()} builds with its dependents in your overlay; nothing was kept")
        return ToolResult(True, f"{relative.as_posix()} saved in your private overlay (generation {overlay.generation.id})")

    def algebra(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self.cas_opened:
            self.cas_opened = True
            if self.launch.cas_factory is not None:
                self.cas = self.launch.cas_factory(self.launch.store.path / "cas")
        if self.cas is None:
            return ToolResult(False, "no computer algebra kernel is available to this delegation")
        if name == "cas_state":
            return ToolResult(True, self.cas.state().model_dump_json())
        result = self.cas.run(str(arguments["source"]))
        return ToolResult(result.accepted, result.model_dump_json())

    def close(self) -> None:
        if self.cas is not None:
            close = getattr(getattr(self.cas, "session", None), "close", None)
            if close is not None:
                close()

    def retrieve(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        retriever = self.launch.retriever
        if retriever is None:
            return ToolResult(False, "retrieval is not available to this delegation")
        if name == "read_project":
            return retriever.project(str(arguments["query"]), kind=arguments.get("kind"),
                                     limit=int(arguments.get("limit", 10) or 10))
        if name == "read_item":
            return retriever.item(str(arguments["selector"]))
        if name == "read_neighborhood":
            return retriever.neighborhood(str(arguments["selector"]))
        if name == "search_literature":
            return retriever.literature(str(arguments["query"]), intent=str(arguments.get("intent") or ""),
                                        limit=int(arguments.get("limit", 10) or 10))
        file = arguments.get("file")
        return retriever.source_text(str(arguments["paper_id"]), int(arguments.get("start_line", 1) or 1),
                                     None if file is None else str(file))


def _usage_of(usage: Usage | None, *, exchanges: int, seconds: float) -> ResourceUsage:
    """Measured spend; anything the provider did not state stays unknown."""
    if usage is None:
        return ResourceUsage(provider_calls=exchanges, active_seconds=seconds,
                             unknown=("cost_usd", "tokens"))
    unknown = []
    cost = Decimal(str(usage.cost_usd)) if usage.reports.get("cost_usd") else None
    if cost is None:
        unknown.append("cost_usd")
    tokens = usage.total_tokens if usage.counted else None
    if tokens is None:
        unknown.append("tokens")
    return ResourceUsage(cost_usd=cost, tokens=tokens, provider_calls=max(usage.turns, exchanges),
                         active_seconds=seconds, unknown=tuple(unknown))


def run_worker(launch: WorkerLaunch, open_worker: OpenWorker, token: CancelToken, *,
               clock: Callable[[], float] = time.monotonic) -> WorkerResult:
    store = launch.store
    state = _WorkerState(launch, token)
    started = clock()

    def dispatch(name: str, arguments: dict[str, Any]) -> ToolResult:
        if token.cancelled:
            result = ToolResult(False, "the delegation was cancelled before this tool call was made")
            store.append("tool", {"name": name, "arguments": arguments, "result": result.as_dict()},
                         phase=RunPhase.PROVING)
            return result
        call_id = uuid4().hex
        store.append("tool_started", {"name": name, "arguments": arguments, "call_id": call_id},
                     phase=RunPhase.PROVING)
        try:
            result = state.tool(name, arguments)
        except (KeyError, TypeError, ValueError) as error:
            result = ToolResult(False, f"invalid tool call: {error}")
        if launch.pushes is not None:
            # A tool result is a provider boundary: what was pushed since the
            # last one rides along with it rather than interrupting mid-request.
            pushed = launch.pushes()
            if pushed:
                result = ToolResult(result.ok, f"{result.output}\n\n{render_pushes(pushed)}")
        store.append("tool", {"name": name, "arguments": arguments, "result": result.as_dict(),
                              "call_id": call_id}, phase=RunPhase.PROVING)
        return result

    def observe(event: dict[str, Any]) -> None:
        store.append("provider", event, phase=RunPhase.PROVING)

    store.write_text(PurePosixPath("prompt.md"), launch.prompt)
    exchanges = 0
    opened: OpenedWorker | None = None
    deadline: threading.Timer | None = None
    status: DelegationState
    reason: str | None = None
    synthesis = ""
    try:
        token.check()
        if launch.lease.active_seconds is not None and launch.lease.active_seconds <= 0:
            # Nothing to spend: no provider context is opened for a lease that is already over.
            raise _NoTime("active_seconds")
        if launch.lease.provider_calls is not None and launch.lease.provider_calls <= 0:
            raise _NoTime("provider_calls")
        opened = open_worker(launch, dispatch, observe)
        store.append("worker.opened", {"context_id": opened.context_id, "model": getattr(opened.runtime, "model", None)},
                     phase=RunPhase.PROVING)
        token.on_cancel(opened.runtime.cancel)
        if launch.lease.active_seconds is not None:
            # Provider time is spend too: when the lease runs out the runtime is
            # cancelled, and the result says exhausted rather than completed.
            runtime = opened.runtime

            def expire() -> None:
                state.deadline_hit = True
                runtime.cancel()

            deadline = threading.Timer(launch.lease.active_seconds, expire)
            deadline.daemon = True
            deadline.start()
        exchanges = 1
        for event in opened.runtime.stream(launch.prompt):
            if event.kind == "reply":
                store.append("worker.reply", {"text": event.text}, phase=RunPhase.PROVING)
            if token.cancelled or state.deadline_hit:
                break
        if token.cancelled:
            raise WorkerCancelled
        if state.deadline_hit:
            status, reason = DelegationState.EXHAUSTED, "active_seconds lease exhausted while the provider ran"
        elif state.finished is None:
            status, reason = DelegationState.PARTIAL, "no_finish_call"
        else:
            status, synthesis = state.finished
    except _NoTime as spent:
        status, reason = DelegationState.EXHAUSTED, f"{spent} lease exhausted before the provider was opened"
    except WorkerCancelled:
        status, reason = DelegationState.CANCELLED, "cancelled"
    except Exception as error:  # noqa: BLE001 - a worker's failure is a result, not a crash upstream
        status, reason = DelegationState.FAILED, f"{type(error).__name__}: {error}"
        store.append("worker.failed", {"error": reason}, phase=RunPhase.PROVING)
    finally:
        if deadline is not None:
            deadline.cancel()
    usage = None
    if opened is not None:
        try:
            usage = opened.usage()
        except Exception as error:  # noqa: BLE001 - unknown usage is recorded as unknown
            store.append("worker.usage_error", {"error": f"{type(error).__name__}: {error}"},
                         phase=RunPhase.PROVING)
    state.close()
    measured = _usage_of(usage, exchanges=exchanges, seconds=max(0.0, clock() - started))
    measured = measured.model_copy(update={"official_checks": state.checks.checks})
    store.write_json(PurePosixPath("findings.json"), [f.model_dump(mode="json") for f in state.findings])
    change_set_id = None
    artifacts: tuple[str, ...] = ("prompt.md", "findings.json", "result.json")
    if launch.overlay is not None:
        change_set = launch.overlay.change_set()
        store.write_json(PurePosixPath("change_set.json"), change_set)
        change_set_id = change_set.id
        artifacts = (*artifacts, "change_set.json")
    result = WorkerResult(
        delegation_id=launch.delegation_id, status=status, synthesis=synthesis, usage=measured,
        findings=tuple(f.id for f in state.findings), terminal_reason=reason,
        artifacts=artifacts, change_set=change_set_id,
    )
    store.write_json(PurePosixPath("result.json"), result)
    store.append("worker.finished", {"status": status.value, "reason": reason,
                                     "usage": json.loads(measured.model_dump_json())}, phase=RunPhase.PROVING)
    return result
