# Delegation / research-swarm backend implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the delegation backend from the delegation/swarm spec as dependency-ordered vertical slices, starting with one nonblocking background worker launched from a live Explore session and growing, on the same machinery, to bounded 1-16 worker trees with leases, isolation, findings, scheduling, overlays, admission and attention delivery.

**Architecture:** Delegation is execution state layered over Hardy's existing project ledger. A new `hardy.workflows.delegation` package owns delegation contracts, an append-only hash-chained journal, lease accounting, context construction, findings/promotion, a mechanical scheduler, an optional coordinator contract, overlays/change sets, admission orchestration and attention routing. A narrow `hardy.agents.executor` owns physical worker slots and cancellation tokens. Workers get independent provider contexts through the session's existing `make_runtime` factory and record their trajectories in `RunStore`s. Mathematics always routes back through `LedgerStore`, `LedgerPolicy`, `ExploreWorkflow`, `LeanWorkspace.stage()` and the existing verifier; nothing here mints evidence or truth.

**Tech Stack:** Python 3.11+, Pydantic v2 `FrozenModel`, `threading`/`concurrent.futures`, `hardy.foundation.locking.FileLock`, `hardy.workflows.storage.RunStore`, `hardy.agents.usage.Usage`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-delegation-swarm-design.md` (read it in full before any task; section numbers below refer to it).

## Global Constraints

- Do not add a new top-level package; orchestration lives under `src/hardy/workflows/delegation/`, the execution substrate under `src/hardy/agents/executor.py` (spec §3).
- No second theorem graph, research ledger, assumption system, evidence model, literature store, proof verifier or publication ontology (spec §1). Mathematical objects are ledger records; `Finding` is never a ledger record (spec §15.2).
- Nothing encodes 16 as a global maximum (spec §1, §6, criterion 40). `LocalExecutor(max_workers)` is a constructor argument.
- The main Explore session is not worker zero; delegation is nonblocking by default (spec §7).
- Every independent worker has its own provider context; reusing a provider thread with a new prompt is not a new worker (spec §14.4).
- Children receive bounded leases; descendants never increase root resources; unknown usage stays unknown liability (spec §6).
- Availability is not preload; omission is not isolation; isolation is enforced at retrieval and promotion (spec §8.5, §11.4).
- Promotion never changes evidence grade; promotion is not admission (spec §11).
- The scheduler is mechanical; no single scalar promisingness score; lanes USER-PINNED / EXPLOIT / EXPLORE / VERIFY-ADVERSARIAL / BLOCKER are preserved (spec §12).
- Coordinator is optional and bounded by an explicit `CoordinatorAuthority`; not instantiated on worker count alone (spec §13).
- Workers never edit authoritative files; they return versioned `ChangeSet`s (spec §14.1). Authoritative admission re-verifies against the current head and reports success only after the ledger transaction commits (spec §15.5, §15.9).
- Model-context injection only at safe provider boundaries: the steering block `TurnCoordinator.stream` prepends before the `user` event (spec §16.4). Human and model deliveries are separately receipted (spec §16.3).
- Module boundaries (`tests/unit/test_module_boundaries.py`): `hardy.workflows.ledger.*` may not reach `workflows.delegation`; `hardy.agents.executor` may not import any controller or `workflows.interactive.session`; capabilities never import `workflows.delegation`.
- Docs: every new page under `docs/` is listed in `docs/README.md`; new settings/commands go in `docs/reference/`; status only in `docs/roadmap.md` (`tests/unit/test_docs.py`).
- Tests run with `uv run --extra test pytest -m "not real_toolchain and not live"`; lint with `uvx ruff check src tests`. Full suite on Windows takes 20+ minutes; run targeted files during a task and the full suite at each slice boundary.
- Commit small and often with the attribution trailer given by the session. Never weaken existing tests.

## Slice map and dependency order

| Slice | Delivers | Spec criteria |
| --- | --- | --- |
| 1 | Contracts, journal store, replay, restart recovery | 1 (partial), 41, 42 (partial) |
| 2 | Lease accounting, recursive cancellation | 5, 6, 41 |
| 3 | `LocalExecutor`, leaf worker, controller, session wiring: **first milestone** | 1, 2 (partial), 4, 31, 32, 40 |
| 4 | Problem core, research brief, context manifest, initial-context builder | 2, 7, 10, 11 |
| 5 | Lazy project/literature retrieval with enforced isolation | 3, 8, 9, 12 |
| 6 | Structured findings, promotion, cross-pollination, duplicates/contradictions | 13, 14, 15 |
| 7 | Mechanical scheduler, lanes, tranches, pins, graph signals | 16, 17 |
| 8 | Coordinator view/plan/authority envelopes | 18, 19, 20 |
| 9 | Private workspace overlays and versioned `ChangeSet`s | 21, 23, 24 |
| 10 | Subtree project-state overlays and admission candidates | 25, 26, 27, 29 |
| 11 | Current-head reconciliation, verification, crash-safe admission | 22, 28, 30 |
| 12 | Attention inbox, notifications, receipts, continuations, coalescing | 33-37 |
| 13 | Minimal inspection/control wiring | 38 |
| 14 | End-to-end acceptance and regression | 39, 42 |

Slices 1-3 are written at step granularity below. Slices 4-14 are written at task granularity with exact files, interfaces, tests and the code that fixes each contract; expand each to step granularity (write failing test, run, implement, run, commit) when its turn arrives, without changing the interfaces recorded here unless an earlier slice forced a change that is then recorded in this plan.

## Facts about the existing code that every task relies on

- `hardy.foundation.values.FrozenModel` is `BaseModel` with `extra="forbid", frozen=True`; `json_digest(value)` is sha256 over compact sorted JSON.
- `LedgerStore(project)` keeps `<project>/ledger/<seq:020d>.json`; `read() -> LedgerSnapshot`; `append(records, *, expected_revision, activate=None, validate=None)` raises `ValueError("stale ledger revision: ...")` on a stale revision. `LedgerSnapshot.get(ref)`, `.head(id)`, `.current(type)`, `.revision`, `.active_context`.
- `LedgerGraph(snapshot)`: `dependency_closure`, `reverse_closure`, `blockers(root, is_resolved=)`, `ready_obligations`, `critical_branches`, `minimal_context(item)`, `context_chain`, `research_neighborhood`.
- `ContextManager(store).render(context)` renders a context as JSON text; `ExploreWorkflow(store)` records items, approaches, counterexamples through the ledger.
- `RunStore(path, run_id)` / `RunStore.open(path, run_id=)`: `write_json(PurePosixPath, value)`, `append(kind, payload, phase=RunPhase)` writes `trajectory.jsonl`; `RunPhase` values include `SETUP, PROVING, COMPLETED, CANCELLED`.
- `hardy.agents.usage.Usage` is an immutable dataclass; `record(event)` folds a provider `result` report; `cost_usd is None` means unreported; `Usage.from_dict`, `as_dict`, `summary()`.
- `RaceStrategy` (`workflows/strategies/race.py`) is the model for independent contexts: `RaceAttempt(context_id, strategy, cancel, usage)`; `_total_usage` sums per-context usage keeping `None` when unknown.
- `MathematicsSession` (`workflows/interactive/session.py`) builds its runtime with `self._make_runtime(model=..., system_prompt=..., specs=..., dispatch=..., cwd=..., session_id=..., observe=...)`; `stream(text)` passes `steering=self._steering_block` to `TurnCoordinator.stream`, which records a `steering` event then the `user` event, then calls `runtime.stream(f"{block}\n\n{text}")`. That call site is the safe model-context boundary.
- Tests build a session with `tests/test_chat.py::session(tmp_path, FakeChatRuntime(script))`; `FakeChatRuntime.stream` yields `TurnEvent`s and dispatches scripted tool calls through `context["dispatch"]`.
- `hardy.foundation.locking.FileLock(path)` and `atomic_write_bytes`; `hardy.foundation.files.WriteGuard(directory, create=)` with `.reserve(name)`, `.open(name, mode)`, `.write_bytes(name, content)`.
- `TurnCoordinator` (`workflows/interactive/turns.py`) owns the tool gate, the cancellation event and usage folding; `TurnPersistence` bundles the record callbacks.
- TUI: `app/tui/handlers.py::build_registry()` lists `Command(name, summary, handler, argument_hint=, safe_in_flight=)`; `docs/reference/session-commands.md` must name every command. `Shell.write` prints under `patch_stdout` and is safe to call from a worker thread; `PlainUi.write` locks.

---

## Slice 1: Core delegation contracts and durable execution store

### Task 1.1: Delegation contracts

**Files:**
- Create: `src/hardy/workflows/delegation/__init__.py` (docstring only)
- Create: `src/hardy/workflows/delegation/contracts.py`
- Test: `tests/unit/test_delegation_contracts.py`

**Interfaces:**
- Produces: `DelegationState`, `TerminalReason`, `ResourceLease`, `ConcurrencyLease`, `ResourceUsage`, `SpawnPolicy`, `CoordinationPolicy`, `DelegationSpec`, `Delegation`, `WorkerResult`, `DelegationEvent`, `DelegationId = str`.

- [ ] **Step 1: Write the failing tests**

```python
"""Delegation values are frozen execution state, never mathematical records."""
from decimal import Decimal

import pytest

from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    CoordinationPolicy,
    Delegation,
    DelegationSpec,
    DelegationState,
    ResourceLease,
    ResourceUsage,
    SpawnPolicy,
    WorkerResult,
)
from hardy.workflows.ledger.contracts import VersionRef


def _ref(id="L17"):
    return VersionRef(id=id, digest="a" * 64)


def test_lease_fits_within_parent_and_refuses_growth():
    parent = ResourceLease(cost_usd=Decimal("10"), official_checks=20, active_seconds=600, provider_calls=50)
    child = ResourceLease(cost_usd=Decimal("4"), official_checks=5, active_seconds=600, provider_calls=10)
    assert child.fits_within(parent)
    assert not ResourceLease(cost_usd=Decimal("11")).fits_within(parent)
    # An unbounded child dimension cannot sit under a bounded parent one.
    assert not ResourceLease(cost_usd=None).fits_within(parent)
    assert ResourceLease(cost_usd=None).fits_within(ResourceLease(cost_usd=None))


def test_lease_arithmetic_keeps_unbounded_dimensions_unbounded():
    a = ResourceLease(cost_usd=Decimal("3"), official_checks=2)
    b = ResourceLease(cost_usd=Decimal("1"), official_checks=1)
    assert (a + b) == ResourceLease(cost_usd=Decimal("4"), official_checks=3)
    assert (a - b) == ResourceLease(cost_usd=Decimal("2"), official_checks=1)
    assert (ResourceLease() + b).cost_usd is None


def test_usage_sum_marks_unknown_dimensions_rather_than_zero():
    known = ResourceUsage(cost_usd=Decimal("1.5"), tokens=100, provider_calls=2, official_checks=1, active_seconds=3.0)
    unknown = ResourceUsage(provider_calls=1, unknown=("cost_usd", "tokens"))
    total = known + unknown
    assert total.provider_calls == 3
    assert total.cost_usd is None and total.tokens is None
    assert set(total.unknown) == {"cost_usd", "tokens"}
    assert (known + known).cost_usd == Decimal("3.0")


def test_usage_exceeds_lease_only_on_known_dimensions():
    lease = ResourceLease(cost_usd=Decimal("2"), official_checks=3)
    assert ResourceUsage(cost_usd=Decimal("2.5")).exceeds(lease) == ("cost_usd",)
    assert ResourceUsage(unknown=("cost_usd",)).exceeds(lease) == ()
    assert ResourceUsage(official_checks=3).exceeds(lease) == ("official_checks",)


def test_spec_and_delegation_are_frozen_and_validated():
    spec = DelegationSpec(
        objective="prove Lemma 17",
        project_refs=(_ref(),),
        scope=_ref("scope"),
        lease=ResourceLease(official_checks=2),
        concurrency=ConcurrencyLease(slots=1),
        created_by="human",
    )
    delegation = Delegation.create("d-1", spec, parent_id=None, created_at="2026-09-10T00:00:00+00:00")
    assert delegation.root_id == "d-1" and delegation.state is DelegationState.QUEUED
    assert delegation.spawn == SpawnPolicy() and not delegation.spawn.can_spawn
    assert delegation.coordination is CoordinationPolicy.INDEPENDENT
    with pytest.raises(ValueError):
        DelegationSpec(objective="", project_refs=(), scope=_ref("scope"), lease=ResourceLease(),
                       concurrency=ConcurrencyLease(slots=1), created_by="human")
    with pytest.raises(ValueError):
        ConcurrencyLease(slots=0)


def test_worker_result_requires_terminal_status():
    with pytest.raises(ValueError):
        WorkerResult(delegation_id="d-1", status=DelegationState.ACTIVE, synthesis="x", usage=ResourceUsage())
    result = WorkerResult(delegation_id="d-1", status=DelegationState.COMPLETED, synthesis="done", usage=ResourceUsage())
    assert result.findings == () and result.change_set is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/test_delegation_contracts.py -q`
Expected: FAIL with `ModuleNotFoundError: hardy.workflows.delegation`

- [ ] **Step 3: Write the contracts**

```python
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


def _bounded(value: Any) -> bool:
    return value is not None


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
        values = {}
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
            if name in unknown or mine is None and theirs is None:
                values[name] = None
            else:
                values[name] = (mine or 0) + (theirs or 0)
        for name in ("provider_calls", "official_checks", "active_seconds"):
            values[name] = getattr(self, name) + getattr(other, name)
        return ResourceUsage(**values, unknown=tuple(sorted(unknown)))

    def exceeds(self, lease: ResourceLease) -> tuple[str, ...]:
        """Dimensions where known usage is at or over the lease ceiling."""
        over = []
        for name in DIMENSIONS:
            ceiling = getattr(lease, name)
            used = getattr(self, name)
            if ceiling is None or used is None or name in self.unknown:
                continue
            if used >= ceiling and not (ceiling == 0 and used == 0 and name in {"cost_usd", "active_seconds"}):
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

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/DelegationSpec/v1", "value": self.model_dump(mode="json")})


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/test_delegation_contracts.py -q`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check src/hardy/workflows/delegation tests/unit/test_delegation_contracts.py
git add src/hardy/workflows/delegation tests/unit/test_delegation_contracts.py
git commit -m "feat(delegation): frozen delegation, lease and usage contracts"
```

### Task 1.2: Append-only delegation journal and replayed tree

**Files:**
- Create: `src/hardy/workflows/delegation/store.py`
- Test: `tests/unit/test_delegation_store.py`

**Interfaces:**
- Consumes: `DelegationEvent`, `Delegation`, `DelegationSpec`, `DelegationState`, `WorkerResult`, `ResourceUsage` from Task 1.1; `FileLock`, `WriteGuard`, `RunStore`.
- Produces: `DelegationStore(workspace)` with `append(delegation_id, kind, payload) -> DelegationEvent`, `events() -> tuple[DelegationEvent, ...]`, `tree() -> DelegationTree`, `artifacts(delegation_id) -> RunStore`, `recover(now) -> tuple[Delegation, ...]`; `DelegationTree` with `.delegations: Mapping[str, Delegation]`, `.get(id)`, `.roots`, `.children(id)`, `.descendants(id)`, `.usage_reported: Mapping[str, ResourceUsage]`, `.revision`.
- Journal kinds fixed here: `delegation.created`, `delegation.started`, `delegation.progress`, `delegation.paused`, `delegation.resumed`, `delegation.completed`, `delegation.partial`, `delegation.failed`, `delegation.cancelled`, `delegation.exhausted`, `delegation.recovered`, `cancel.requested`, `usage.reported`, `budget.reserved`, `budget.released`.

- [ ] **Step 1: Write the failing tests**

```python
"""The journal is append-only and hash-chained; state is replayed, never cached."""
import json
from decimal import Decimal

import pytest

from hardy.workflows.delegation.contracts import (
    ConcurrencyLease, DelegationSpec, DelegationState, ResourceLease, ResourceUsage, WorkerResult,
)
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.ledger.contracts import VersionRef


def _spec(objective="prove L17"):
    return DelegationSpec(objective=objective, project_refs=(VersionRef(id="L17", digest="a" * 64),),
                          scope=VersionRef(id="scope", digest="b" * 64),
                          lease=ResourceLease(official_checks=2), concurrency=ConcurrencyLease(slots=1),
                          created_by="human")


def test_created_delegation_is_replayed_after_restart(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None,
                                               "created_at": "2026-09-10T00:00:00+00:00"})
    store.append("d-1", "delegation.started", {})
    tree = DelegationStore(tmp_path).tree()
    assert tree.get("d-1").state is DelegationState.ACTIVE
    assert tree.roots == ("d-1",)
    assert tree.revision == 2


def test_child_events_link_parent_and_descendants(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("root", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("child", "delegation.created", {"spec": _spec("sub").model_dump(mode="json"), "parent_id": "root", "created_at": "t"})
    store.append("grandchild", "delegation.created", {"spec": _spec("subsub").model_dump(mode="json"), "parent_id": "child", "created_at": "t"})
    tree = store.tree()
    assert tree.children("root") == ("child",)
    assert tree.descendants("root") == ("child", "grandchild")
    assert tree.get("grandchild").root_id == "root" and tree.get("grandchild").depth == 2
    with pytest.raises(ValueError, match="unknown parent"):
        store.append("orphan", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": "nope", "created_at": "t"})


def test_terminal_event_records_result_and_refuses_further_progress(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("d-1", "delegation.started", {})
    result = WorkerResult(delegation_id="d-1", status=DelegationState.COMPLETED, synthesis="proved",
                          usage=ResourceUsage(provider_calls=3, unknown=("cost_usd",)))
    store.append("d-1", "delegation.completed", {"result": result.model_dump(mode="json")})
    tree = store.tree()
    assert tree.get("d-1").result == result and tree.get("d-1").terminal
    with pytest.raises(ValueError, match="terminal"):
        store.append("d-1", "delegation.progress", {"note": "late"})


def test_usage_reports_accumulate_per_delegation_and_keep_unknown(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("d-1", "usage.reported", {"usage": ResourceUsage(cost_usd=Decimal("1"), provider_calls=1).model_dump(mode="json")})
    store.append("d-1", "usage.reported", {"usage": ResourceUsage(provider_calls=1, unknown=("cost_usd",)).model_dump(mode="json")})
    used = store.tree().usage_reported["d-1"]
    assert used.provider_calls == 2 and used.cost_usd is None and used.unknown == ("cost_usd",)


def test_tampered_or_reordered_journal_is_refused(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("d-1", "delegation.started", {})
    path = tmp_path / "delegations" / "journal.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["created_at"] = "forged"
    path.write_text("\n".join([json.dumps(first), lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="journal"):
        DelegationStore(tmp_path).tree()


def test_recovery_marks_interrupted_work_unknown_not_failed_or_cancelled(tmp_path):
    store = DelegationStore(tmp_path)
    for name, started in (("never", False), ("running", True)):
        store.append(name, "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
        if started:
            store.append(name, "delegation.started", {})
    store.append("done", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    store.append("done", "delegation.cancelled", {"reason": "user"})
    recovered = DelegationStore(tmp_path).recover(now="2026-09-10T01:00:00+00:00")
    assert [d.id for d in recovered] == ["running"]
    tree = store.tree()
    assert tree.get("running").state is DelegationState.UNKNOWN
    assert tree.get("running").terminal_reason == "interrupted"
    assert tree.usage_reported["running"].unknown == tuple(sorted(("cost_usd", "tokens", "provider_calls", "official_checks", "active_seconds")))
    assert tree.get("never").state is DelegationState.QUEUED
    assert tree.get("done").state is DelegationState.CANCELLED
    # Recovery is idempotent: a second pass finds nothing interrupted.
    assert DelegationStore(tmp_path).recover(now="t2") == ()


def test_artifact_store_is_per_delegation_and_reopenable(tmp_path):
    store = DelegationStore(tmp_path)
    store.append("d-1", "delegation.created", {"spec": _spec().model_dump(mode="json"), "parent_id": None, "created_at": "t"})
    run = store.artifacts("d-1")
    run.append("worker.note", {"text": "hi"}, phase="proving")
    again = DelegationStore(tmp_path).artifacts("d-1")
    assert again.path == run.path and again.trajectory_path.exists()
    with pytest.raises(ValueError, match="unknown delegation"):
        store.artifacts("missing")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/unit/test_delegation_store.py -q`
Expected: FAIL with `ModuleNotFoundError: hardy.workflows.delegation.store`

- [ ] **Step 3: Implement the store**

```python
"""Append-only delegation journal; the tree is a replay, not a cache.

One JSONL journal per workspace, hash-chained like the ledger's transactions and
guarded by an OS lock. Each delegation owns a RunStore directory beside it for
artifacts and its trajectory. Recovery after a crash marks work that was active
as `unknown` with all usage dimensions unknown: interrupted work is neither
success, cancellation, exhaustion, nor never-started work.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.workflows.delegation.contracts import (
    DIMENSIONS,
    TERMINAL_STATES,
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


class DelegationTree:
    def __init__(self, delegations: dict[str, Delegation], usage: dict[str, ResourceUsage],
                 events: tuple[DelegationEvent, ...]) -> None:
        self.delegations: Mapping[str, Delegation] = MappingProxyType(delegations)
        self.usage_reported: Mapping[str, ResourceUsage] = MappingProxyType(usage)
        self.events = events

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
    for event in events:
        id = event.delegation_id
        if event.kind == "delegation.created":
            if id in delegations:
                raise ValueError(f"journal creates delegation twice: {id}")
            parent = event.payload.get("parent_id")
            if parent is not None and parent not in delegations:
                raise ValueError(f"journal names unknown parent {parent!r} for {id}")
            spec = DelegationSpec.model_validate(event.payload["spec"])
            root, depth = (id, 0) if parent is None else (delegations[parent].root_id, delegations[parent].depth + 1)
            delegations[id] = Delegation.create(id, spec, parent_id=parent, created_at=str(event.payload["created_at"]),
                                                root_id=root, depth=depth)
            usage[id] = ResourceUsage()
            if parent is not None:
                delegations[parent] = delegations[parent].model_copy(update={"children": (*delegations[parent].children, id)})
            continue
        if id not in delegations:
            raise ValueError(f"journal event for unknown delegation: {id}")
        current = delegations[id]
        if event.kind == "usage.reported":
            usage[id] = usage[id] + ResourceUsage.model_validate(event.payload["usage"])
            continue
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
        for key in ("problem_core_digest", "research_brief_digest", "context_manifest_id"):
            if key in event.payload:
                delegations[id] = delegations[id].model_copy(update={key: str(event.payload[key])})
    return DelegationTree(delegations, usage, events)


class DelegationStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    def _guard(self, *, create: bool) -> WriteGuard:
        root = WriteGuard(self.workspace, create=create)
        return WriteGuard(root.directory / DIRECTORY, create=create)

    def _lock(self, guard: WriteGuard) -> FileLock:
        return FileLock(guard.reserve("journal.lock"))

    def _read(self, guard: WriteGuard) -> tuple[DelegationEvent, ...]:
        path = guard.directory / JOURNAL
        if not path.exists():
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
            _replay((*events, event))  # validate before writing; the journal never holds a bad event
            line = json.dumps({"event": event.model_dump(mode="json"), "digest": event.digest},
                              ensure_ascii=False, allow_nan=False) + "\n"
            with guard.open(JOURNAL, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def artifacts(self, delegation_id: str) -> RunStore:
        tree = self.tree()
        tree.get(delegation_id)
        guard = self._guard(create=True)
        path = guard.directory / delegation_id
        run_id = uuid5(NAMESPACE_URL, f"hardy:delegation:{delegation_id}")
        if path.exists():
            return RunStore.open(path, run_id=run_id)
        path.mkdir(parents=True)
        return RunStore(path, run_id)

    def recover(self, *, now: str) -> tuple[Delegation, ...]:
        """Mark every active or waiting delegation interrupted. Idempotent."""
        recovered = []
        tree = self.tree()
        for delegation in tree.delegations.values():
            if delegation.state in {DelegationState.ACTIVE, DelegationState.WAITING, DelegationState.PAUSED}:
                self.append(delegation.id, "delegation.recovered", {"reason": "interrupted", "recovered_at": now})
                recovered.append(self.tree().get(delegation.id))
        return tuple(recovered)
```

Note `now` in `append` is a `datetime` for tests that need a clock; `recover(now=str)` records the string as given. Keep `_replay` the single source of derived state; nothing else derives state from the journal.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/unit/test_delegation_store.py tests/unit/test_delegation_contracts.py -q`
Expected: 13 passed

- [ ] **Step 5: Run adjacent tests and commit**

Run: `uv run --extra test pytest tests/unit/test_module_boundaries.py tests/unit/test_storage.py -q`
Expected: pass

```bash
git add src/hardy/workflows/delegation/store.py tests/unit/test_delegation_store.py
git commit -m "feat(delegation): hash-chained journal with replayed tree and crash recovery"
```

### Task 1.3: List the spec and plan in the documentation map

**Files:**
- Modify: `docs/README.md` (section "Planning and research")

- [ ] **Step 1: Run the currently failing docs test**

Run: `uv run --extra test pytest tests/unit/test_docs.py::test_docs_index_lists_every_page -q`
Expected: FAIL naming `superpowers/specs/2026-09-10-delegation-swarm-design.md`

- [ ] **Step 2: Add the two entries**

Under "## Planning and research" add one bullet linking the spec page (`superpowers/specs/...-delegation-swarm-design.md`, described as the architectural specification for background workers and research swarms over the project ledger) and one bullet linking this plan (`superpowers/plans/...-delegation-swarm-backend.md`, described as the dependency-ordered slices that implement that specification), in the same bullet style as the existing entries.

- [ ] **Step 3: Run docs tests and commit**

Run: `uv run --extra test pytest tests/unit/test_docs.py -q`
Expected: pass

```bash
git add docs/README.md docs/superpowers/plans/2026-09-10-delegation-swarm-backend.md
git commit -m "docs: list the delegation spec and implementation plan"
```

---

## Slice 2: Resource leases, recursive accounting and cancellation

### Task 2.1: Lease ledger over the journal

**Files:**
- Create: `src/hardy/workflows/delegation/budget.py`
- Test: `tests/unit/test_delegation_budget.py`

**Interfaces:**
- Consumes: `DelegationTree`, `ResourceLease`, `ResourceUsage`, `ConcurrencyLease`.
- Produces: `LeaseLedger(tree)` with `reserved(id) -> ResourceLease` (the node's own lease as journaled), `child_reservations(id) -> ResourceLease`, `usage(id) -> ResourceUsage` (own reported + descendants, unknown propagating), `allocatable(id) -> ResourceLease` (= reserved − child reservations − own direct usage, never negative), `exhausted(id) -> tuple[str, ...]` (dimensions over at this node or any ancestor), `slots_in_use(id) -> int`, `slots_available(id) -> int`; `LeaseRefused(ValueError)` and `grant(tree, parent_id, requested: ResourceLease, requested_slots: int) -> tuple[ResourceLease, ConcurrencyLease]` which clips nothing silently: it refuses when the request exceeds `allocatable(parent)`.

- [ ] **Step 1: Write the failing tests** covering: sum of child reservations cannot exceed parent allocatable (refused, not clipped); sibling workers sharing one parent lease (second child refused once the first took the remainder); grandchild consumes the child's lease and its usage rolls up to child and root; unknown usage at a grandchild makes root usage unknown for that dimension and `allocatable` treats the unknown dimension as unavailable (no new reservation in that dimension); a released reservation returns to the parent (`budget.released` after terminal); root exhaustion (`usage.exceeds(lease)`) reports at every descendant; concurrency: `slots_in_use(parent)` counts active and waiting children only (queued leaves hold no slot, so more runnable leaves than slots is allowed and scheduling decides who runs); `grant` refuses a child slot request above the parent's total concurrency lease.

```python
def _tree(store, *edges):
    """edges: (id, parent, lease_kwargs)."""
    for id, parent, lease in edges:
        store.append(id, "delegation.created", {"spec": _spec(lease).model_dump(mode="json"), "parent_id": parent, "created_at": "t"})
        store.append(id, "budget.reserved", {"lease": ResourceLease(**lease).model_dump(mode="json"), "slots": 1})
    return store.tree()


def test_child_reservation_cannot_exceed_parent_allocatable(tmp_path):
    store = DelegationStore(tmp_path)
    tree = _tree(store, ("root", None, {"official_checks": 4, "cost_usd": Decimal("10")}),
                 ("a", "root", {"official_checks": 3, "cost_usd": Decimal("6")}))
    ledger = LeaseLedger(tree)
    assert ledger.allocatable("root") == ResourceLease(official_checks=1, cost_usd=Decimal("4"))
    with pytest.raises(LeaseRefused, match="official_checks"):
        grant(tree, "root", ResourceLease(official_checks=2, cost_usd=Decimal("1")), requested_slots=0)
    lease, slots = grant(tree, "root", ResourceLease(official_checks=1, cost_usd=Decimal("4")), requested_slots=0)
    assert lease == ResourceLease(official_checks=1, cost_usd=Decimal("4"))
```

Write the remaining cases in the same style (one test per bullet above).

- [ ] **Step 2: Run to verify failure** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `budget.py`**

```python
"""Ancestor-bounded reservations and upward-rolling usage, derived from the journal.

Invariants (spec §6) are checked here in code: sum(child reservations) <= parent
allocatable; usage rolls up; unknown usage is unavailable liability, never zero;
reclaimed reservations return to the parent; exhaustion at any ancestor stops
descendants. This module never decides mathematics or runs work.
"""
from __future__ import annotations

from decimal import Decimal

from hardy.workflows.delegation.contracts import (
    DIMENSIONS, ConcurrencyLease, DelegationState, ResourceLease, ResourceUsage,
)
from hardy.workflows.delegation.store import DelegationTree


class LeaseRefused(ValueError):
    """The requested reservation does not fit the parent's allocatable resources."""


class LeaseLedger:
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

    def usage(self, id: str) -> ResourceUsage:
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
        """What a new child may still reserve: lease minus children minus own direct use."""
        own = self.tree.usage_reported.get(id, ResourceUsage())
        direct = ResourceLease(**{
            name: (None if name in own.unknown else getattr(own, name) if getattr(own, name) is not None else 0)
            for name in DIMENSIONS
        })
        remaining = self.reserved(id) - self.child_reservations(id)
        values = {}
        for name in DIMENSIONS:
            ceiling, used = getattr(remaining, name), getattr(direct, name)
            if ceiling is None:
                values[name] = None
            elif used is None:
                values[name] = type(ceiling)(0)  # unknown liability leaves nothing to promise
            else:
                values[name] = max(ceiling - used, type(ceiling)(0))
        return ResourceLease(**values)

    def exhausted(self, id: str) -> tuple[str, ...]:
        over: set[str] = set()
        for node in (id, *self.tree.ancestors(id)):
            over.update(self.usage(node).exceeds(self.reserved(node)))
        return tuple(sorted(over))

    def slots_in_use(self, id: str) -> int:
        return sum(self.slots(child) for child in self.tree.children(id)
                   if self.tree.get(child).state in {DelegationState.ACTIVE, DelegationState.QUEUED, DelegationState.WAITING})

    def slots_available(self, id: str) -> int:
        return max(0, self.slots(id) - self.slots_in_use(id))


def grant(tree: DelegationTree, parent_id: str, requested: ResourceLease, *, requested_slots: int
          ) -> tuple[ResourceLease, ConcurrencyLease | None]:
    ledger = LeaseLedger(tree)
    available = ledger.allocatable(parent_id)
    if not requested.fits_within(available):
        over = [name for name in DIMENSIONS
                if getattr(available, name) is not None
                and (getattr(requested, name) is None or getattr(requested, name) > getattr(available, name))]
        raise LeaseRefused(f"reservation exceeds parent allocatable resources: {', '.join(over)}")
    if ledger.exhausted(parent_id):
        raise LeaseRefused(f"ancestor resources exhausted: {', '.join(ledger.exhausted(parent_id))}")
    if requested_slots > ledger.slots_available(parent_id):
        raise LeaseRefused("requested worker slots exceed the parent's available concurrency")
    return requested, (ConcurrencyLease(slots=requested_slots) if requested_slots else None)
```

Note the `Decimal`/`float`/`int` mix: `type(ceiling)(0)` keeps each dimension's type. `_combine` in `ResourceLease` already floors at zero.

- [ ] **Step 4: Run the tests** — expected pass.
- [ ] **Step 5: Commit** `feat(delegation): lease ledger with ancestor-bounded reservations and unknown liability`.

### Task 2.2: Recursive cancellation and terminal release in the store

**Files:**
- Modify: `src/hardy/workflows/delegation/store.py` (add `cancel_subtree(id, reason, now) -> tuple[str, ...]`, `release(id)`)
- Test: `tests/unit/test_delegation_store.py` (append cases)

**Interfaces:**
- `DelegationStore.cancel_subtree(id, *, reason) -> tuple[str, ...]`: journals `cancel.requested` on the node and every non-terminal descendant, deepest first, then `delegation.cancelled` for any that is `QUEUED` (never started: no executor to wait for). Active ones are left for the executor to finish as `cancelled` (Task 3.x) — the store only records the request. Returns the ids that received a request.
- `DelegationStore.release(id)`: journals `budget.released` once a node is terminal; refused on non-terminal nodes.

- [ ] **Step 1: Tests**: cancelling the root requests cancellation on child and grandchild and immediately cancels the queued grandchild; a terminal child is untouched; already-cancelled nodes do not get a second request; `release` on an active node raises; after release, `LeaseLedger.allocatable(parent)` grows by the child's reservation.
- [ ] **Step 2-4**: implement, run, pass.
- [ ] **Step 5: Commit** `feat(delegation): recursive cancellation requests and reservation release`.

---

## Slice 3: Local executor, one background worker, session wiring (first milestone)

### Task 3.1: `WorkerExecutor` protocol and `LocalExecutor`

**Files:**
- Create: `src/hardy/agents/executor.py`
- Test: `tests/unit/test_executor.py`

**Interfaces:**
- Produces:

```python
class WorkerCancelled(Exception): ...

class CancelToken:
    def __init__(self) -> None: ...            # threading.Event + list of callbacks
    def cancel(self) -> None: ...               # sets the event, runs callbacks once each
    @property
    def cancelled(self) -> bool: ...
    def check(self) -> None: ...                # raises WorkerCancelled when set
    def on_cancel(self, callback: Callable[[], None]) -> None: ...  # runs immediately if already cancelled

@dataclass(frozen=True)
class WorkerJob:
    name: str
    run: Callable[[CancelToken], Any]

class WorkerHandle(Protocol):
    name: str
    token: CancelToken
    def done(self) -> bool: ...
    def result(self, timeout: float | None = None) -> Any: ...   # re-raises WorkerCancelled/errors
    def cancel(self) -> None: ...
    def add_done_callback(self, fn: Callable[[WorkerHandle], None]) -> None: ...

class WorkerExecutor(Protocol):
    slots: int
    def submit(self, job: WorkerJob) -> WorkerHandle: ...
    def active(self) -> int: ...
    def shutdown(self, *, wait: bool) -> None: ...

class LocalExecutor:
    def __init__(self, slots: int, *, thread_name_prefix: str = "hardy-delegation") -> None: ...
```

`LocalExecutor` wraps `ThreadPoolExecutor(max_workers=slots)`. Submitted jobs beyond `slots` wait in the pool queue (logical leaves > physical slots). A queued job that is cancelled before it starts runs nothing: `run` is wrapped so it checks the token first and raises `WorkerCancelled`.

- [ ] **Step 1: Tests**: two jobs on one slot run sequentially (second starts after first ends); `cancel()` on a queued job means its body never runs and `result()` raises `WorkerCancelled`; `cancel()` on a running job fires `on_cancel` callbacks (the job observes the token and stops); `done` callbacks run exactly once on the executor thread; `slots` is whatever was passed (test with 1, 3 and 40 — no cap); `shutdown(wait=True)` returns after running jobs finish.
- [ ] **Step 2-4**: implement, run, pass. Also run `tests/unit/test_module_boundaries.py` (a new `hardy.agents.*` module must not reach controllers).
- [ ] **Step 5: Commit** `feat(agents): local worker executor with cancellation tokens`.

### Task 3.2: Minimal problem core and worker launch package

**Files:**
- Create: `src/hardy/workflows/delegation/context.py` (minimal; Slice 4 extends it)
- Test: `tests/unit/test_delegation_context.py`

**Interfaces:**
- Produces:

```python
class ProblemCore(FrozenModel):
    target: VersionRef
    kind: str                      # ProjectItemKind value
    name: str
    statement: str | None
    context: VersionRef | None
    context_text: str              # ContextManager.render(context) or ""
    scope: VersionRef
    dependencies: tuple[VersionRef, ...]   # dependency_closure heads (exact refs)
    project_revision: int
    task_mode: str
    @property
    def digest(self) -> str: ...   # json_digest over schema-tagged dump

class ResearchBrief(FrozenModel):
    target: VersionRef
    task_mode: str
    framing: str = ""
    reasoning_direction: str = "forward"
    required_methods: tuple[str, ...] = ()
    discouraged_methods: tuple[str, ...] = ()
    forbidden_methods: tuple[str, ...] = ()
    model: str | None = None
    seed: int | None = None
    @property
    def digest(self) -> str: ...

class ContextManifest(FrozenModel):
    id: str
    problem_core_digest: str
    research_brief_digest: str
    project_revision: int
    included_refs: tuple[VersionRef, ...]
    hidden_selectors: tuple[str, ...] = ()
    builder: str = "hardy.delegation.context/v1"

def build_problem_core(store: LedgerStore, target: VersionRef, *, scope: VersionRef, task_mode: str) -> ProblemCore
def render_launch_prompt(core: ProblemCore, brief: ResearchBrief) -> str
```

`build_problem_core` reads the snapshot once, requires the target to be a current `ProjectItem` head (raise `ValueError("stale target")` otherwise), collects `LedgerGraph(snapshot).dependency_closure(target)`, renders the context with `ContextManager(store).render(item.context)` when the item has one, and pins `snapshot.revision`. `render_launch_prompt` begins with the line `[Hardy delegation worker — written by Hardy, not the user]`, then the exact statement, context text, dependency refs (`id@digest[:12]`), then the brief. Two workers on one target with different briefs share `core.digest`.

- [ ] **Step 1: Tests** using `tests/unit/test_explore_research.py::explore`-style seeding (`LedgerStore(tmp_path)`, `Scope(id="scope")`, `ContextManager.create_root`, `ExploreWorkflow.record_item(kind=LEMMA)`): core digest is stable across two builds; changes when the statement changes; refuses a stale ref; two briefs give one core digest and two brief digests; the prompt contains the exact statement and the marker line and never the main transcript.
- [ ] **Step 2-4**: implement, run, pass.
- [ ] **Step 5: Commit** `feat(delegation): minimal problem core, research brief and context manifest`.

### Task 3.3: Leaf worker runner with an independent provider context

**Files:**
- Create: `src/hardy/workflows/delegation/worker.py`
- Test: `tests/unit/test_delegation_worker.py`

**Interfaces:**
- Consumes: `CancelToken`, `RunStore`, `Usage`, `ChatRuntime`, `final_text` (`hardy.agents.contracts`).
- Produces:

```python
WORKER_TOOLS: list[dict[str, Any]]   # two function specs: `propose_finding`, `finish`
# propose_finding(kind: str, summary: str, payload: str, related_refs: list[str]) -> ToolResult
# finish(status: "completed"|"partial"|"failed", synthesis: str) -> ToolResult

@dataclass(frozen=True)
class WorkerLaunch:
    delegation_id: str
    prompt: str                      # render_launch_prompt output
    model: str | None
    store: RunStore                  # the delegation's artifact store
    lease: ResourceLease

@dataclass(frozen=True)
class OpenedWorker:                  # mirrors RaceAttempt
    context_id: str
    runtime: ChatRuntime
    usage: Callable[[], Usage | None]

OpenWorker = Callable[[WorkerLaunch, Callable[[str, dict[str, Any]], ToolResult], Callable[[dict[str, Any]], None]], OpenedWorker]

def run_worker(launch: WorkerLaunch, open_worker: OpenWorker, token: CancelToken, *, clock=time.monotonic) -> WorkerResult
```

`run_worker`:
1. Builds a private `_WorkerState` (findings list, finish status, usage `Usage()`), a `dispatch(name, args)` that records every call to `launch.store` (`tool_started`/`tool` events, `RunPhase.PROVING`), implements the two tools, and refuses calls after `token.cancelled` or after `finish` was called.
2. Calls `open_worker(launch, dispatch, observe)`; `observe` records provider events to the trajectory and folds `result` reports into the usage.
3. Registers `token.on_cancel(runtime.cancel)`.
4. Drains `runtime.stream(launch.prompt)` (one exchange; the provider loop owns tool iteration); on `WorkerCancelled`/`token.cancelled` returns `status=CANCELLED`.
5. Charges `provider_calls` from `usage.turns`; `cost_usd`/`tokens` from the usage or marks them unknown when `reports` lacks them; `active_seconds` from the clock.
6. Writes `result.json` to the store and returns `WorkerResult`. Status: `finish` status if called; else `PARTIAL` with `terminal_reason="no_finish_call"`; exceptions → `FAILED` with the error text.

- [ ] **Step 1: Tests** using a scripted runtime (copy the shape of `tests/test_chat.py::FakeChatRuntime` into `tests/unit/delegation_helpers.py` as `ScriptedWorkerRuntime` plus `scripted_open_worker(scripts_by_marker)`): a script that proposes one finding then finishes yields `COMPLETED` with one finding id and a `result.json`; the worker's `context_id` differs between two launches and each gets its own runtime instance; usage from `result` events lands in `ResourceUsage` and a runtime that reports nothing yields `unknown=("cost_usd","tokens")` with `provider_calls=1`; cancellation before the first event yields `CANCELLED` and `runtime.cancel()` was called; an exception from the runtime yields `FAILED` with the error recorded in the trajectory; tool calls after `finish` are refused and recorded.
- [ ] **Step 2-4**: implement, run, pass.
- [ ] **Step 5: Commit** `feat(delegation): leaf worker runner over an independent provider context`.

### Task 3.4: Attention items and delivery receipts (minimal)

**Files:**
- Create: `src/hardy/workflows/delegation/attention.py`
- Test: `tests/unit/test_delegation_attention.py`

**Interfaces:**

```python
class AttentionItem(FrozenModel):
    id: str
    delegation_id: str
    source_event: int                # journal sequence
    summary: str
    category: str                    # "completion" | "interrupted" | "finding" | "decision" | ...
    importance: str = "normal"       # "low" | "normal" | "high"
    actionable: bool = False
    sticky: bool = False
    related_refs: tuple[VersionRef, ...] = ()
    detail_refs: tuple[str, ...] = ()   # artifact paths
    supersedes: tuple[str, ...] = ()

class DeliveryReceipt(FrozenModel):
    item_id: str
    recipient: str                   # "human" | "main_agent"
    mode: str                        # "queue" | "notify" | "interrupt"
    delivered_at: str
    conversation_epoch: str | None = None
    transcript_offset: int | None = None

class AttentionInbox:
    def __init__(self, store: DelegationStore) -> None: ...
    def derive(self, event: DelegationEvent, tree: DelegationTree) -> AttentionItem | None
        # completion/partial/failed/exhausted/cancelled of a root with notify_human -> item; recovered -> sticky "interrupted"
    def record(self, item: AttentionItem) -> None            # journals "attention.derived"
    def pending(self, recipient: str) -> tuple[AttentionItem, ...]   # derived minus receipted minus handled
    def receipt(self, item_id: str, recipient: str, mode: str, *, epoch: str | None = None, offset: int | None = None) -> DeliveryReceipt  # journals "attention.delivered"
    def handle(self, item_id: str, *, by: str) -> None        # journals "attention.handled"; sticky items leave pending only through this
    def render_for_model(self, items, *, budget_items: int = 5) -> str   # "[Hardy delegation attention — written by Hardy, not the user]" block, at most budget_items lines plus "N other delegations completed normally"
```

Attention events are journaled on the delegation id they concern (kinds `attention.derived`, `attention.delivered`, `attention.handled`), so the store replays them; add these three kinds to `_replay` as no-ops on state.

- [ ] **Step 1: Tests**: completion of a user-created root derives one item; a human receipt does not clear the model's pending list (spec §16.3); the model receipt does; sticky item stays pending until handled; render caps at `budget_items` and reports the remainder count; restart preserves pending state.
- [ ] **Step 2-4**: implement, run, pass.
- [ ] **Step 5: Commit** `feat(delegation): attention items with separate human and model receipts`.

### Task 3.5: `DelegationController` (nonblocking delegate/inspect/cancel)

**Files:**
- Create: `src/hardy/workflows/delegation/controller.py`
- Test: `tests/unit/test_delegation_controller.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class RootResources:
    lease: ResourceLease
    slots: int

class DelegationController:
    def __init__(self, store: DelegationStore, ledger: LedgerStore, *, executor: WorkerExecutor,
                 open_worker: OpenWorker, root: RootResources, notify: Callable[[str], None],
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None
    def recover(self) -> tuple[Delegation, ...]          # store.recover + attention items
    def delegate(self, spec: DelegationSpec, *, parent_id: str | None = None) -> Delegation
    def cancel(self, id: str, *, reason: str = "user") -> tuple[str, ...]
    def inspect(self, id: str) -> dict[str, Any]         # delegation, usage, lease, attention, artifact path
    def status(self) -> dict[str, Any]                   # counts by state, root lease/usage
    def tree(self) -> DelegationTree
    def attention(self) -> AttentionInbox
    def wait(self, id: str, timeout: float | None = None) -> Delegation   # tests/CLI only; the session never calls it during a turn
    def shutdown(self) -> None
```

`delegate` (all under one `threading.Lock`): the root of a top-level delegation is a synthetic `"root"` delegation created lazily with `RootResources` so that every user job reserves from one root; `grant()` from `budget.py` decides the lease (refuse → `LeaseRefused` propagates, nothing journaled); journal `delegation.created`, `budget.reserved`, build `ProblemCore`/`ResearchBrief`/`ContextManifest` (write `manifest.json`, `core.json`, `brief.json` to the artifact store; journal `delegation.context` with the three digests); submit a `WorkerJob` whose `run` does: journal `delegation.started`; `run_worker(...)`; journal `usage.reported` and the terminal event; `release`; derive+record attention; call `notify(text)` when `spec.notify_human`. Cancel: `store.cancel_subtree` then `handle.cancel()` for each active handle. Errors inside the job never escape the executor thread unrecorded: the `finally` journals `delegation.failed` with the error if no terminal event was written.

- [ ] **Step 1: Tests** (scripted worker, `LocalExecutor(2)`, seeded ledger): `delegate` returns before the worker finishes (assert the returned state is `QUEUED`/`ACTIVE` while a barrier holds the worker); completion journals usage, releases the lease, records an attention item and calls `notify` once; a second delegation refused by `LeaseRefused` leaves the journal unchanged; `cancel` on an active worker ends it `CANCELLED` and the lease is released; a worker exception ends `FAILED`; `recover()` on a fresh controller over a journal with an active node yields an `interrupted` sticky item; the root lease is never exceeded when two workers each reserve half.
- [ ] **Step 2-4**: implement, run, pass.
- [ ] **Step 5: Commit** `feat(delegation): nonblocking controller over the executor, journal and attention inbox`.

### Task 3.6: Session wiring — background worker from a live Explore session

**Files:**
- Modify: `src/hardy/workflows/interactive/session.py` (constructor: `delegation_slots: int = 4`, build `self.delegations`; `_open_worker`; `_steering_block` composition; `delegate(...)`)
- Modify: `src/hardy/workflows/interactive/turns.py` (no change if steering composition happens in the session; verify)
- Test: `tests/test_chat_delegation.py`

**Interfaces:**
- `MathematicsSession.delegations: DelegationController` (constructed in `__init__` after `self.runtime`; `store=DelegationStore(workspace)`, `ledger=LedgerStore(workspace)`, `executor=LocalExecutor(delegation_slots)`, `open_worker=self._open_worker`, `root=RootResources(lease=ResourceLease(official_checks=self.limits.official_checks, active_seconds=self.limits.active_seconds), slots=delegation_slots)`, `notify=self._notify`), then `self.delegations.recover()`.
- `MathematicsSession.delegate(target: str, *, objective: str, task_mode: str = "prove", checks: int = 1) -> Delegation`: resolves `target` as `id` or `id@digest` against the workspace ledger heads (reuse `workflows/interactive/project.py::_selected`), uses the head `Scope` (first `snapshot.current(Scope)` or raise), builds the spec with `created_by="human"`, `lease=ResourceLease(official_checks=checks, active_seconds=self.limits.active_seconds)`, `ConcurrencyLease(slots=1)`.
- `MathematicsSession._open_worker(launch, dispatch, observe) -> OpenedWorker`: `runtime = self._make_runtime(model=launch.model, system_prompt=WORKER_SYSTEM_PROMPT, specs=WORKER_TOOLS, dispatch=dispatch, cwd=launch.store.path, session_id=None, observe=observe)`; returns `OpenedWorker(context_id=f"delegation:{launch.delegation_id}:{uuid4().hex}", runtime=runtime, usage=lambda: tracked usage)`. The worker never receives `self._dispatch`, `self._system_prompt`, or the main transcript.
- `MathematicsSession._notify(text)`: appends to `self.notices` (a `collections.deque`) and calls an optional `self.on_notice: Callable[[str], None] | None` hook set by the TUI/plain runner. Nothing here touches the runtime.
- Steering: replace `steering=self._steering_block` in `stream()` with `steering=self._steering_with_attention`, which concatenates `self._steering_block()` and `self.delegations.attention().render_for_model(pending)` and, after the block is computed, records a `main_agent` receipt for each rendered item with the current history epoch and transcript end. Because `TurnCoordinator.stream` records the `steering` event before the `user` event and prepends the block to the provider request, the model sees the compact representation before the human's message (spec §16.3, criterion 32).

- [ ] **Step 1: Write the failing test** (`tests/test_chat_delegation.py`):

```python
def test_background_worker_runs_while_the_main_session_continues(tmp_path):
    """Criterion 1, 4, 31, 32: delegate, keep talking, get the result at the next safe boundary."""
    seed_lemma(tmp_path)                     # LedgerStore(tmp_path): Scope + root context + LEMMA "L17"
    worker_started, release_worker = threading.Event(), threading.Event()
    chat = session_with_worker(tmp_path, main_script=["Hello."], worker_script=[
        call("propose_finding", {"kind": "candidate_proof", "summary": "by simp", "payload": "by simp", "related_refs": ["L17"]}),
        call("finish", {"status": "completed", "synthesis": "L17 closes by simp."}),
    ], worker_gate=(worker_started, release_worker))
    delegation = chat.delegate("L17", objective="prove L17", checks=1)
    assert worker_started.wait(5)
    # Main session is live while the worker is blocked.
    assert chat.send("What next?") == "Hello."
    assert "delegation" not in chat.record.history().replay()      # no worker transcript in the main conversation
    release_worker.set()
    done = chat.delegations.wait(delegation.id, timeout=5)
    assert done.state is DelegationState.COMPLETED
    assert chat.notices and "L17" in chat.notices[-1]
    # Next turn receives the compact attention block ahead of the user text.
    chat.send("Why does that work?")
    events = list(chat._recorded())
    steering = [e for e in events if e["type"] == "steering"][-1]
    user = [e for e in events if e["type"] == "user"][-1]
    assert "[Hardy delegation attention" in steering["text"] and delegation.id in steering["text"]
    assert events.index(steering) < events.index(user)
    assert chat.delegations.attention().pending("main_agent") == ()
```

Plus: `test_worker_has_its_own_provider_context` (the runtime factory saw two builds with different `system_prompt`s and the worker's lacks the workspace manifest); `test_root_check_ceiling_refuses_a_second_worker_beyond_the_limit` (`limits=RunLimits(official_checks=1)`, first delegate takes 1, second raises `LeaseRefused`); `test_restart_recovers_interrupted_delegation` (journal an active node by hand, open a new session, pending has a sticky `interrupted` item and the state is `UNKNOWN`); `test_usage_unknown_stays_unknown` (fake runtime reports no `result` → `usage_reported[id].unknown` includes `cost_usd`).

Helper `session_with_worker(tmp_path, main_script, worker_script, worker_gate)` builds a factory that returns a `FakeChatRuntime(main_script)` when `system_prompt` lacks `"[Hardy delegation worker"` and a gated `FakeChatRuntime(worker_script)` otherwise (the gate sets `worker_started` then waits on `release_worker` before yielding events).

- [ ] **Step 2: Run to verify failure** (`AttributeError: delegate`).
- [ ] **Step 3: Implement the session changes** as specified in Interfaces. Keep the constructor change additive (`delegation_slots: int = 4`). `WORKER_SYSTEM_PROMPT` lives in `workflows/delegation/worker.py` and states the tool contract and that the worker must call `finish`.
- [ ] **Step 4: Run** `tests/test_chat_delegation.py tests/test_chat.py tests/unit/test_interactive_owners.py tests/unit/test_module_boundaries.py tests/unit/test_docs.py` — expected pass.
- [ ] **Step 5: Commit** `feat(interactive): nonblocking background worker from a live session with attention at the next turn`.

### Task 3.7: Plain/TUI notice hook and `/delegate`, `/jobs`, `/cancel` (minimum wiring)

**Files:**
- Modify: `src/hardy/app/tui/handlers.py` (three `Command`s, all `safe_in_flight=True` except `delegate`, which is `False` because it reads the ledger head and the session gate)
- Modify: `src/hardy/app/tui/shell.py` and `src/hardy/app/tui/plain.py` (`session.on_notice = ui.write` after attach)
- Modify: `docs/reference/session-commands.md`
- Test: `tests/tui/test_delegation_commands.py`

`/delegate <item-id> [objective...]` → `session.delegate(...)`, prints id; `/jobs` → `session.delegations.status()` and per-delegation lines; `/cancel <id>` → `session.delegations.cancel(id)`. These are exercised through `ScriptedUi` with a stub session exposing `delegate`, `delegations.status`, `delegations.cancel`.

- [ ] Steps: failing tests → implement → `tests/tui/test_delegation_commands.py tests/unit/test_docs.py` pass → commit `feat(tui): minimal /delegate, /jobs and /cancel wiring`.

### Slice 3 checkpoint

Run the full hermetic suite: `uv run --extra test pytest -m "not real_toolchain and not live" -q` (expect the 5 known environmental failures on Windows and nothing new). Report per the "Commits and checkpoints" contract. Update `docs/roadmap.md` with a "Delegation (D)" section listing D1-D14 with D1-D3 implemented.

---

## Slice 4: Problem core, research brief, context manifest, initial-context builder

**Files:** extend `workflows/delegation/context.py`; create `workflows/delegation/diversity.py`; tests `tests/unit/test_delegation_context.py`, `tests/unit/test_delegation_diversity.py`.

**Interfaces to add:**

```python
class ContextResolution(str, Enum): FULL, STATEMENT, SUMMARY, POINTER
class ContextItem(FrozenModel):
    ref: VersionRef | None; source: str | None   # ledger ref or literature handle
    resolution: ContextResolution; inclusion_reason: str
    selected_by: Literal["mandatory","deterministic","portfolio_planner","user","parent","promotion"]
    trust: str; estimated_tokens: int; preload: bool
class InitialWorkingSet(FrozenModel): items: tuple[ContextItem, ...]; structural_map: str; budget_tokens: int; overflow: bool
class ContextPolicy(FrozenModel): preload_tokens: int = 6000; hidden_selectors: tuple[str,...] = (); pinned_refs: tuple[VersionRef,...] = (); excluded_refs: tuple[VersionRef,...] = ()

def mandatory_kernel(snapshot, target, scope) -> tuple[ContextItem, ...]      # stage A: target, minimal_context declarations/bindings, direct dependencies (STATEMENT), scope/trust
def structural_map(snapshot, target, *, depth: int = 3) -> str                 # stage B: tree text as in spec §8.3
def candidate_pool(snapshot, target, policy) -> tuple[ContextItem, ...]        # stage C: nearby deps/consumers/siblings/examples/notes with reasons
def fit_to_budget(mandatory, chosen, policy) -> InitialWorkingSet              # stage E: never drops mandatory; overflow=True if mandatory alone exceeds
def build_working_set(store, target, scope, brief, policy, *, portfolio: tuple[ContextManifest,...] = ()) -> InitialWorkingSet
```

`ProblemCore` grows: `hypotheses/conclusion` for theorem-shaped items (statement split via existing `formal.syntax` helpers only when a frozen claim exists; otherwise the exact statement), `trusted_assumptions: tuple[VersionRef,...]` (`Scope.allowed_background`), `verified_dependencies` (deps whose PROVE/RESOLVE obligations are accepted per `LedgerPolicy.is_accepted`), `blockers` (`LedgerGraph.blockers`), `capabilities: tuple[str,...]`, `literature_policy: str`, `result_contract: str`. `ContextManifest` grows: `included_items: tuple[ContextItem,...]`, `retrieval_permissions`, `literature_permissions`, `visibility_policy`, `context_policy_digest`, `preload_budget`.

`diversity.py`: `DiversityAxis` enum in spec order; `assign_briefs(target, n, *, task_modes, representations, directions, seeds) -> tuple[ResearchBrief,...]` produces the default moderate portfolio (direct proof, blind proof, falsification, literature/reduction, examples, alternate representation, specialize/generalize, wildcard), records `diversity: tuple[tuple[str,str],...]` per brief; supplemental selection is portfolio-aware: `build_working_set(..., portfolio=)` penalizes items already preloaded by siblings unless mandatory or pinned.

**Tests:** mandatory kernel always precedes selected material and is never dropped (criterion 10); overflow flagged, not truncated; two workers same core digest, different brief digests and non-identical supplemental sets (criteria 7, 11); a hidden selector removes an item from the pool but not from the mandatory kernel unless correctness-critical (then `ValueError`); no model planner is invoked for a single worker (the `planner` callable is `None` by default and asserted uncalled).

Commit per task: `feat(delegation): staged initial context with mandatory kernel first`, `feat(delegation): portfolio-aware research briefs`.

## Slice 5: Lazy retrieval and enforced isolation

**Files:** create `workflows/delegation/retrieval.py`; extend `worker.py` tool set; tests `tests/unit/test_delegation_retrieval.py`, `tests/unit/test_delegation_isolation.py`.

**Interfaces:**

```python
class VisibilityPolicy(FrozenModel):
    hidden_refs: tuple[VersionRef, ...] = ()          # exact
    hidden_ids: tuple[str, ...] = ()                   # every revision of an id
    hidden_findings: tuple[str, ...] = ()
    hidden_sources: tuple[str, ...] = ()               # literature handles
    hidden_approaches: tuple[str, ...] = ()
    def narrowed(self, child: VisibilityPolicy) -> VisibilityPolicy   # union; a child never widens (spec §11.4)
    def permits_ref(self, ref) -> bool; def permits_source(self, id) -> bool; def permits_finding(self, id) -> bool

class WorkerRetriever:
    def __init__(self, ledger: LedgerStore, papers: PaperToolRuntime | None, policy: VisibilityPolicy, *, record: Callable[[dict], None]) -> None
    def project(self, query: str, *, kind: str | None = None, limit: int = 10) -> ToolResult   # current snapshot heads; filters hidden; records context.retrieved
    def item(self, selector: str) -> ToolResult                                                 # exact statement + trust status; refuses hidden
    def neighborhood(self, selector: str) -> ToolResult                                         # deps/consumers via LedgerGraph
    def literature(self, query: str, *, intent: str) -> ToolResult                              # PaperToolRuntime.search filtered by hidden_sources; records intent
    def source_text(self, paper_id: str, start_line: int = 1) -> ToolResult                     # PaperToolRuntime.read; refuses hidden
```

Worker tools grow: `read_project`, `read_item`, `read_neighborhood`, `search_literature`, `read_source`. Every retrieval writes a `context.retrieved` trajectory event with `{selector|query, intent, delivered, refused}` so provenance distinguishes preload from retrieval (criterion 42).

**Tests:** a newly appended verified lemma is discoverable by a running worker while `ProblemCore` stays frozen (criterion 9); hidden ref refused at preload (Slice 4 builder consumes the same policy), at retrieval, and by `promotion` (Slice 6 test references this policy); child policy cannot widen (`narrowed` only adds); seeded source is a POINTER item plus contents map and is retrievable without wholesale injection (criterion 12: preload contains no `read` of the source body); literature intent is recorded per query.

## Slice 6: Structured findings, promotion, cross-pollination

**Files:** create `workflows/delegation/findings.py`, `workflows/delegation/promotion.py`; tests `tests/unit/test_delegation_findings.py`, `tests/unit/test_delegation_promotion.py`.

**Interfaces:**

```python
class FindingKind(str, Enum): CANDIDATE_LEMMA, VERIFIED_LEMMA, REDUCTION, CONSTRUCTION, COUNTEREXAMPLE, COMPUTATION, LITERATURE_LEAD, LITERATURE_RESULT, OBSTRUCTION, FAILED_APPROACH, STRATEGY, QUESTION, NOTE, PROOF_SUBMISSION, FORMALIZATION
class EvidenceProfile(str, Enum): SPECULATIVE, KERNEL_PROOF, REPRODUCIBLE_COMPUTATION, EXACT_SOURCE_SPAN, INDEPENDENT_REPRODUCTION, HUMAN_ENDORSED
class Finding(FrozenModel):
    id: str; source_delegation: str; kind: FindingKind; summary: Text; payload: str
    related_refs: tuple[VersionRef,...]; evidence_refs: tuple[str,...]; evidence_profile: EvidenceProfile
    assumptions: tuple[str,...]; confidence: float | None = None; sequence: int
    @property structural_fingerprint -> str   # json_digest over kind+normalized payload+related_refs
class Visibility(str, Enum): PRIVATE, CELL, PARENT, SELECTED, SWARM
class PromotionRecord(FrozenModel): finding_id, source, recipient, mode: Literal["discoverable","push","upward"], selector: Literal["policy","coordinator","human"], reason, sequence, context_transition: str | None
class FindingLedger:  # journal-backed: "finding.proposed", "finding.promoted", "finding.rejected"
    def propose(self, finding) -> Finding                       # upward to parent is automatic (parent-visible)
    def visible_to(self, delegation_id, policy: VisibilityPolicy) -> tuple[Finding,...]
    def promote(self, finding_id, *, recipient, mode, selector, reason, authorized_by: str) -> PromotionRecord   # refuses when recipient policy hides it or when authorizer is not a common ancestor
    def clusters(self) -> tuple[tuple[Finding,...],...]        # exact fingerprint groups; provenance intact
    def contradictions(self) -> tuple[tuple[Finding, Finding],...]   # COUNTEREXAMPLE vs CANDIDATE/VERIFIED on the same related ref
```

Worker `propose_finding` now builds a `Finding` with `EvidenceProfile.SPECULATIVE` unless it carries an evidence ref produced by Hardy (kernel verification from Slice 9). `push` mode appends the finding to the recipient's `pending_context` (journal `context.promoted`) consumed at the recipient's next provider boundary (worker turn start).

**Tests:** propose reaches the parent only (criterion 13); `discoverable` does not create a push record; `push` is separately recorded with `context_transition` (criterion 14); four identical findings cluster into one presentation group with four provenance ids (criterion 15); a counterexample and a candidate on one ref are listed as a contradiction and `adjudicate(...)` proposes an `ADVERSARIAL` child spec instead of a vote; hidden finding cannot be promoted into a blind branch; promotion never changes `evidence_profile`; a descendant cannot promote across sibling subtrees without an ancestor authorizer.

## Slice 7: Mechanical scheduler and tranches

**Files:** create `workflows/delegation/scheduler.py`; tests `tests/unit/test_delegation_scheduler.py`; controller uses the scheduler to decide which queued leaves get slots.

**Interfaces:**

```python
class Lane(str, Enum): USER_PINNED, EXPLOIT, EXPLORE, VERIFY, BLOCKER
class Pin(FrozenModel): delegation_id: str; kind: Literal["min_attention","forbid_spend","reinforce","reserve_exploration"]; by: str; value: int | None = None
class AllocationRequest(FrozenModel): delegation_id; lane: Lane; tranche: ResourceLease; slots: int; requested_by: str; reason: str; supporting_refs: tuple[str,...]
class SchedulerDecision(FrozenModel): request: AllocationRequest; granted: ResourceLease | None; slots: int; refused_because: tuple[str,...]; prior: ResourceLease; resulting: ResourceLease; sequence: int
class PortfolioConstraints(FrozenModel): exploration_floor: Decimal = Decimal("0.2"); verify_floor: Decimal = Decimal("0.1"); collapse_exploration: bool = False
class StallSignal(FrozenModel): delegation_id; reasons: tuple[str,...]
class Scheduler:
    def __init__(self, tree, ledger: LeaseLedger, *, constraints, pins: tuple[Pin,...], graph_signals: Callable[[VersionRef], GraphUrgency]) -> None
    def ready(self) -> tuple[Delegation,...]                     # queued, parent not exhausted, no forbid_spend pin, isolation prerequisites satisfied
    def choose(self, free_slots: int) -> tuple[Delegation,...]   # precedence: pinned > blocker urgency > lanes under floors > FIFO; never all slots to one lane while floor > 0
    def decide(self, request: AllocationRequest) -> SchedulerDecision
    def stalls(self, findings: FindingLedger) -> tuple[StallSignal,...]
class GraphUrgency(FrozenModel): blocked_downstream: int; sole_blocker: bool; distance_to_goal: int | None; widely_reused_unverified: bool
def graph_urgency(snapshot, ref) -> GraphUrgency   # from LedgerGraph.blockers/reverse_closure/critical_branches
```

**Tests:** three ready leaves, one slot: only one active, the others remain `QUEUED` (criterion 16); a pinned leaf beats a higher-urgency unpinned one; exploration floor keeps one slot for EXPLORE when EXPLOIT keeps requesting; `collapse_exploration=True` lifts it; tranche grant never exceeds `allocatable` and a later request reclaims after a probe ends; graph urgency changes order but never appears in any evidence field (criterion 17); stall detection on repeated duplicate findings.

## Slice 8: Optional model coordinator

**Files:** create `workflows/delegation/coordinator.py`; tests `tests/unit/test_delegation_coordinator.py`.

**Interfaces:**

```python
class CoordinatorAuthority(FrozenModel): may_spawn, may_allocate_within_reserve, may_pause_resume, may_retire, may_make_discoverable, may_push_findings, may_request_admission: bool; max_children, max_depth: int; max_child_fraction: Decimal; approval_threshold_cost_usd: Decimal | None
HANDS_ON_PI, ASSISTED, EXPEDITION: CoordinatorAuthority   # three presets
class ChildSummary(FrozenModel): id, objective, brief_digest, lane, state, usage: ResourceUsage, findings: tuple[str,...], blockers: tuple[str,...], progress: str
class CoordinationView(FrozenModel): subtree, objective, authority, budget: ResourceLease, allocatable: ResourceLease, children: tuple[ChildSummary,...], neighborhood: str, visible_findings: tuple[str,...], isolation: VisibilityPolicy, pins: tuple[Pin,...], events_since: int
class PlanAction(FrozenModel): action: Literal["spawn","tranche","reinforce","pause","resume","retire","lane","verify","adversarial","discoverable","push","promote_up","synthesize","literature","human_decision","noop"]; target: str | None; args: dict
class CoordinationPlan(FrozenModel): view_digest: str; actions: tuple[PlanAction,...]; rationale: str
class ActionOutcome(FrozenModel): action: PlanAction; applied: bool; refused_because: tuple[str,...]
def build_view(controller, subtree_id, *, since: int) -> CoordinationView
def apply_plan(controller, subtree_id, plan, authority) -> tuple[ActionOutcome,...]   # every action validated by budget/scheduler/promotion; refusals recorded
class ModelCoordinator:   # optional; invoked on checkpoints; provider context disposable; state is the journal
    def __init__(self, ask: Callable[[str], str], authority) -> None
    def checkpoint(self, controller, subtree_id, *, since: int) -> tuple[CoordinationPlan, tuple[ActionOutcome,...]]
```

**Tests:** the same eight-child tree with a scripted plan under the three presets yields the expected applied/refused sets (criterion 19); an over-budget `spawn` is refused mechanically (criterion 18); `noop` is a valid plan; view contains child summaries, not transcripts; after replacing the coordinator (new `ModelCoordinator` over the same journal) the next view is identical (criterion 20); the controller does not create a coordinator unless `coordination` is `CELL`/`ADVERSARIAL`/`PIPELINE` or explicitly requested, regardless of child count.

## Slice 9: Private workspace overlays and versioned `ChangeSet`s

**Files:** create `workflows/delegation/workspace.py`; extend `worker.py` with `check_lean`/`stage_lean` tools; tests `tests/unit/test_delegation_workspace.py`.

**Interfaces:**

```python
class OverlayGeneration(FrozenModel): id: str; delegation_id: str; base_project_revision: int; base_workspace_digest: str; parent_generation: str | None; environment: str; created_at: str
class FileChange(FrozenModel): path: str; operation: Literal["create","modify","delete"]; base_digest: str | None; result_digest: str | None; content: str | None
class ChangeSet(FrozenModel): id; delegation_id; base_project_revision; base_workspace_digest; environment; files: tuple[FileChange,...]; verification: tuple[str,...]; proposed_records: tuple[str,...]  # artifact refs to serialized ledger records (Slice 10)
class WorkspaceOverlay:
    @classmethod def snapshot(cls, workspace: LeanWorkspace, *, delegation_id, base_revision, root: Path, parent: OverlayGeneration | None) -> WorkspaceOverlay   # copies sources+build into <delegations>/<id>/overlay/gen-N; immutable base recorded
    def stage(self, relative, source) -> tuple[LeanWorkspace, Callable[[], None]]   # LeanWorkspace.stage on the overlay copy, never on the authoritative tree
    def change_set(self) -> ChangeSet                                             # diff overlay against its base
    def refresh(self, workspace, *, base_revision) -> OverlayGeneration           # explicit rebase: new generation, journaled "workspace.rebased"
    def child_snapshot(self, child_id) -> WorkspaceOverlay                        # immutable inheritance of the parent's private overlay
```

CAS: workers that need algebra get a private `CasSession` built through `cas_tools.build_runtime` with `cwd=<overlay>/cas`; never the session's kernel (criterion 24).

**Tests:** a worker save lands in the overlay and the authoritative `lean/` is byte-identical (criterion 21); a change set records base digests and result digests; a child inherits an immutable snapshot of the parent's overlay (writing to the child leaves the parent unchanged, criterion 23); refresh records a new generation; the worker's CAS session path is under its overlay and a second worker's is different.

## Slice 10: Subtree project-state overlays and admission candidates

**Files:** create `workflows/delegation/overlay.py`, `workflows/delegation/admission.py`; tests `tests/unit/test_delegation_overlay.py`, `tests/unit/test_delegation_admission.py`.

**Interfaces:**

```python
class SubtreeProjectOverlay:
    """A LedgerStore rooted at <delegations>/<id>/ledger whose effective view is authoritative snapshot + ancestor overlays + local records."""
    def __init__(self, base: LedgerStore, local: LedgerStore, ancestors: tuple[SubtreeProjectOverlay,...]) -> None
    def effective(self) -> LedgerSnapshot            # concatenated records; local ids must not collide with base ids (validated)
    def admit_local(self, records, *, expected_local_revision) -> LedgerSnapshot   # same schemas and validate_structure; policy = LedgerPolicy (no acceptance readers => unproved stays unproved)
class AdmissionCandidate(FrozenModel): id; finding_ids: tuple[str,...]; route: Literal["candidate_lemma","verified_proof","new_verified_lemma","approach","failed_approach","counterexample","computation","literature_result","literature_lead","representation","question"]; records: tuple[str,...] (serialized ledger records); change_set: str | None; base_revision: int; target: Literal["local","authoritative"]
class AdmissionOutcome(FrozenModel): candidate_id; action: Literal["created","reused_existing","revised","linked","resolved_obligation","kept_local","rejected","conflicted"]; authoritative_refs: tuple[VersionRef,...]; identity_map: tuple[tuple[str,str],...]; reasons: tuple[str,...]
def route_finding(finding, snapshot) -> AdmissionCandidate      # mechanical routing table from spec §15.2; a LEMMA candidate produces ProjectItem(kind=LEMMA)+Obligation(PROVE)+Obligation(FORMALIZE)
def structural_fingerprint(record: ProjectItem, snapshot) -> str   # kind+statement+context+deps
def find_duplicates(candidate, snapshot) -> tuple[tuple[VersionRef,...], tuple[VersionRef,...]]   # (exact, near) — near uses normalized statement text similarity threshold; never merged
```

Admission of `approach`/`counterexample`/`question` routes call `ExploreWorkflow.start_approach/block_approach/counterexample/ask`; `verified_proof` and `new_verified_lemma` are completed in Slice 11.

**Tests:** local admission uses `ProjectItem`/`Relation`/`Obligation` and the authoritative `ledger/` directory is untouched (criterion 25); unproved auxiliary lemma is `LEMMA` with open `PROVE` (criterion 26); exact duplicate returns `reused_existing` with an identity map; near-duplicate returns a cluster and `kept_local` without identification (criterion 27); four findings → one admitted item, four provenance ids (criterion 29); a candidate whose local id collides with an authoritative id is remapped explicitly.

## Slice 11: Current-head reconciliation and authoritative admission journal

**Files:** extend `workflows/delegation/admission.py`; tests `tests/unit/test_delegation_reconcile.py`.

**Interfaces:**

```python
class AdmissionPhase(str, Enum): PROPOSED, RECONCILED, VERIFICATION_COMPLETE, FILES_PREPARED, FILES_COMMITTED, LEDGER_COMMITTED, COMPLETED, FAILED
class AdmissionAttempt(FrozenModel): id; candidate_id; phase; head_revision; detail: str; sequence: int
def reconcile(change_set: ChangeSet, workspace: LeanWorkspace, *, head_revision: int) -> tuple[ChangeSet, tuple[str,...]]
    # untouched files: transplant; same-file: three-way merge via difflib on base/head/result when both sides changed disjoint hunks; overlapping hunks -> conflict list
class AuthoritativeAdmission:
    def __init__(self, ledger: LedgerStore, workspace: LeanWorkspace, store: DelegationStore, *, verify: Callable[[LeanWorkspace, ChangeSet], tuple[bool, tuple[EvidenceRef,...], str]], policy: LedgerPolicy, decide: Callable[[LedgerSnapshot, Resolution], ArtifactRef | None]) -> None
    def admit(self, candidate: AdmissionCandidate) -> AdmissionOutcome
        # serialized under FileLock(<delegations>/admission.lock): read head -> reconcile -> stage on head (LeanWorkspace.stage) -> verify (fresh) -> mint records with authoritative ids -> policy accept -> commit files -> ledger append(expected_revision=head) -> journal each phase; if the ledger revision moved between read and append, restart from RECONCILED (max 3), never retry the stale transaction
    def recover(self) -> tuple[AdmissionAttempt,...]   # incomplete attempts are reported, not completed; FILES_COMMITTED without LEDGER_COMMITTED => "incomplete admission" sticky attention
```

**Tests:** A and B from revision 100; A lands; B (unrelated file) transplants and is re-verified against the new head, admission succeeds only after the fresh verification (criterion 22, 28); overlapping edits → `conflicted`, both proposals retained as artifacts; a head change during admission triggers re-reconciliation, not a retry of the stale append (stale-revision case); verify failure after files were staged → `FAILED` at `VERIFICATION_COMPLETE`, no file commit; simulated crash after `FILES_COMMITTED` → `recover()` reports incomplete and nothing claims success (criterion 30); new verified lemma gets its authoritative id before the evidence `subject` ref is built (assert the `EvidenceRef.subject` equals the admitted item ref).

Concrete verification uses `tests/fake_lean.py` as the Lean command so the "fresh check" is real-but-cheap, not mocked.

## Slice 12: Events, attention routing, notifications, continuations

**Files:** extend `attention.py`; create `workflows/delegation/events.py` (event kinds table + routing rules); tests extend `tests/unit/test_delegation_attention.py`, add `tests/test_chat_delegation_attention.py`.

**Interfaces to add:**

```python
class DeliveryMode(str, Enum): QUEUE, NOTIFY, INTERRUPT
class AttentionSubscription(FrozenModel): owner; source: str; triggers: tuple[str,...]; mode: DeliveryMode; recipient: Literal["human","main_agent","both"]; expires_on: str | None
class MainContinuation(FrozenModel): id; awaiting: str; condition: str; conversation_epoch: str; transcript_offset: int; created_at: str
def route(event, tree, subscriptions) -> tuple[AttentionItem, DeliveryMode] | None   # default table spec §16.7
class AttentionInbox: coalesce(items) -> items   # supersedes chains for one delegation; contradictions/priority never coalesced
class InterruptRequest: ...   # recorded; the session honours it only at `stream()` start (restart boundary), never mid-request
```

Session: `stream()` checks `delegations.due_interrupt()` before building the turn and, if one is due and authorized by a subscription, records `interrupt.applied` and includes the item in the steering block; a continuation is started only by `session.continue_main()` (application-level) when `MainContinuation.conversation_epoch == history.epoch` and transcript offset unchanged; otherwise it becomes a pending queue item (criterion 37).

**Tests:** progress events stay local (no attention item); completion while a fake main turn is streaming (worker finishes during `stream()` iteration) leaves the in-flight request untouched, notifies the human, and the following turn carries the item (criteria 31, 36); a chain `progress→failed→repaired→verified→completed` coalesces to one current item with `supersedes` listing the rest (criterion 33); contradiction items survive coalescing (34); sticky decision item persists across turns until `handle` (35); interrupt requires a subscription; stale continuation after a human turn is queued, not executed (37); receipts carry epoch and offset (42).

## Slice 13: Minimal inspection/control application wiring

**Files:** `app/tui/handlers.py` (extend `/jobs` with `tree|inspect <id>|pin <id>|isolate <id> <selector>|reinforce <id> <checks>|pause|resume|synthesize <id>|attention|handle <item>`), `app/config.py` (`delegation_workers` setting + env var), `docs/reference/session-commands.md`, `docs/reference/configuration.md`, `docs/reference/on-disk-layout.md` (the `delegations/` directory), `docs/reference/artifacts.md` (journal and per-delegation artifacts); tests `tests/tui/test_delegation_commands.py`, `tests/unit/test_config.py` additions.

Every operation calls the same `DelegationController`/`Scheduler`/`FindingLedger` APIs (spec §19). No new mechanisms.

## Slice 14: End-to-end acceptance and regression

**Files:** `tests/integration/test_delegation_acceptance.py` (hermetic: fake Lean, scripted runtimes, `LocalExecutor(4)`).

Scenarios: (a) one-worker job end to end from `MathematicsSession` including admission of a verified proof through the fake Lean and a resolved `PROVE` obligation in the authoritative ledger; (b) eight-worker tree with one blind cell, one adversarial worker, a coordinator in ASSISTED mode, two conflicting change sets, one exact-duplicate finding pair, root budget exhaustion mid-run and cancellation of a cell; assertions cover every criterion listed in the slice map. Then run the full suite and lint, update `docs/roadmap.md` D-section status, and write the final checkpoint report.

---

## Self-review notes

- Spec coverage: every numbered criterion 1-42 is named in at least one slice's test list; §20 evaluation provenance is served by the journal kinds (`context.retrieved`, `context.promoted`, `finding.*`, `scheduler.*`, `coordinator.*`, `workspace.*`, `admission.*`, `attention.*`), which Slice 14 asserts are all present in the eight-worker scenario.
- Type consistency: `ResourceLease`/`ResourceUsage`/`ConcurrencyLease` names are used identically in Slices 1, 2, 3.5, 7, 8; `VisibilityPolicy` is introduced in Slice 5 and consumed by Slices 4 (as `ContextPolicy.hidden_selectors` are resolved into it), 6 and 8; `Finding` ids are strings everywhere; `DelegationStore.append(delegation_id, kind, payload)` is the only journal writer.
- Deliberate omissions: distributed executors, book ingestion, rich UI. `process.interrupt_children()` is process-wide, so worker cancellation relies on `runtime.cancel()` plus cooperative tokens and per-call Lean timeouts; per-worker child-process interruption is recorded as a known limitation in the Slice 3 report.
