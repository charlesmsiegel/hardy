# M7 — Search Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M7 from `docs/superpowers/specs/2026-07-21-m7-search-strategies-design.md` — the pluggable `Strategy` seam with the shared `StrategyBudget` meter (reservation-based, deadline-enforced), five strategies (iterative-repair baseline extracted from M1, hybrid cheap-closers, sketch-and-discharge over the M6 ledger, best-first tactic search on pickled proof states, diverse parallel attempts), proof-state pickling (the M1 debt), failed-attempt lesson distillation, and the contemporaneous comparison harness — ending at M7's exit criterion: at least one strategy beats contemporaneously re-run iterative repair on solve rate at equal budget, with the corrected confidence interval excluding zero, logged in the regression tracker.

**Architecture:** Strategies implement one protocol (`hardy.strategy.base.Strategy`) and are selected by config (`RunConfig.strategy` + `strategy_params`), never by code. All budget dimensions flow through one `StrategyBudget` meter: additive dimensions (tokens, turns, cost, Lean CPU) are reserve-and-settle (atomic, synchronous under asyncio, so concurrent branches can never collectively overshoot); wall clock is a monotonic deadline enforced on in-flight work by wrapping every model call and Lean command. Enforcement lives in a metered client layer (`MeteredRuntime`, `MeteredSession`) — strategy code never touches the meter's internals. The Prove workflow's proof phase becomes `strategy.prove(...)` with a harness-owned validator (authoritative final `check_proof` + axiom audit + suspicious-closer scan over the tactic trajectory) injected into the seam. Pickling copies proof-state snapshots out to harness-owned storage and stages them into destination workers via the existing trusted docker side-channels; snapshots carry their declaration prefix, its hash, and the base package generation id. The comparison harness interleaves strategies per item under a recorded seed, checks resolved model revisions, and applies a predeclared paired statistical decision rule with multiple-comparison correction.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); no new dependencies (the decision rule uses `math.comb` and `random.Random` — no scipy). The M0 REPL pool/sandbox, M1 tools/runtime/workflow, M2 eval harness, M5 runtimes, and M6 hole ledger as landed.

**Scope note:** M7 only. No learned scoring/value models (model self-scores only), no premise retrieval or cross-theorem memory (M8), no hole-scheduling changes inside the M6 loop, no distributed multi-host parallelism, no `duper` by default (config flag only, off).

## Global Constraints

(from the M7 spec — every task's requirements implicitly include these)

- **Strategy is config, not code**: `RunConfig.strategy: str` + `strategy_params: dict` select and parameterize; the tracking entry records both.
- **One shared meter per theorem**, covering every budget dimension the run advertises; enforcement lives in the meter/client layer, never in strategy code.
- **Additive dimensions (tokens, turns, cost, Lean CPU) are reservation-based and atomic**: reserve an upper estimate before each model call or Lean command, settle actual usage and refund the difference; a call whose reservation fails does not start.
- **Wall clock is a monotonic run deadline, not an additive reservation** — parallel branches running through the same 10 s advance wall time by 10, not 30 — and it is enforced **on in-flight work**: every model call, Lean command, and validator invocation runs wrapped in the remaining deadline and is cancelled at expiry.
- **Lean CPU is enforced during the command, not just settled after**: a command is killed at its reserved allowance.
- **A cancelled branch's in-flight model call keeps its full reservation** unless provider-confirmed final usage arrives.
- **Final `check_proof` is the sole success authority in every strategy** — search bookkeeping (best-first paths, sketch assembly) is never trusted; the harness-owned validator's re-check is that authority, and every strategy returns proved only through it.
- **Closers spend zero model tokens.**
- **Sketch holes go through the M6 ledger** (`layer="kernel"`, provenance `sketch`); discharge commits through a single serialized applier in deterministic order (M6's blast-radius logic assumes serial patches).
- **Every strategy has a degraded-but-functional sequential path** on runtimes without parallelism (M5 minimal loop); parallelism is queried via capability flags, never runtime names.
- **Failed attempts become distilled lessons, never replayed transcripts**; lesson lists are capped with drop-oldest and recorded in the trajectory.
- **Comparisons are contemporaneous only**: same harness commit (dirty tree refused), same model (resolved immutable revision checked across every linked run — mismatch or absence *invalidates* the comparison), same environment, same item set, same `StrategyBudget`; `iterative` is always implicitly included as baseline; the historical M2 number is never referenced.
- **The global closer pre-pass is disabled in every comparison arm** — the pre-pass *is* the hybrid strategy's implementation and competes as a strategy, not as ambient plumbing.
- **One model throughout comparisons**: `strategy_params` containing model overrides (e.g. `subgoal_model`) are rejected in exit-criterion comparisons.
- **A win needs statistical evidence**: predeclared decision rule fixed in config before the run — paired per-item outcomes, a paired test (McNemar for solve/no-solve, or bootstrap over items) with a confidence interval, and a multiple-comparison correction; a point-estimate win records as *inconclusive*.
- **Pickles restore the proof state only**: each snapshot carries the harness-owned declaration prefix, its hash, and the base package generation id; migration replays the prefix and verifies the hash *before* unpickling; staged pickles are copied in via the trusted host-side channel, never mounted; the per-run store is size-capped and cleaned at end of run; snapshots register as generation references for M4's garbage collector.
- **Test tiers unchanged**: unit (default, CI), `lean`, `tex`, `docker`, `model` (never CI).

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's plan is re-reviewed against reality when the milestone starts. At plan-writing time **only M0 is implemented code** (`src/hardy/lean/*`, `src/hardy/latex/*`, `src/hardy/sandbox/*`, `tests/fake_repl.py`); M1 exists as a plan; M2, M5, M6 exist as specs. Build order is numeric, so by M7-start M1–M6 should be landed code — every signature below **must be re-checked against the landed code** before executing any task, and any drift resolved in favor of the implemented code (implemented code > plan > spec). Interfaces consumed, with the exact signatures this plan assumes:

**From the M1 plan (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`) — plan-only today:**

1. `hardy.lean.session.ProofSession` (M1 Task 3): `check(code, timeout=None) -> CheckOutcome` with `CheckOutcome(verdict: ProofVerdict, env: int | None)`; `tactic(tactic, proof_state, timeout=None) -> TacticOutcome` with `TacticOutcome(ok, proof_state, goals, error)`; `goal(proof_state) -> str | None`; `known_states() -> list[int]`; `states_lost: bool`; `command_in(code, env, timeout=None) -> CommandResponse | None`; `STATE_LOST_MSG` constant; internal `_worker: PoolWorker | None`, `_states: dict[int, str]`, `_ensure_worker()`. Tasks 3 and 8 extend this class (metered wrapping duck-types it; pickling adds `send_raw`/`replay`/`adopt_state`/`base_env`/`container_name`).
2. `hardy.lean.pool.ReplPool.lease() -> _SessionLease` (M1 Task 3): async context manager yielding a `ProofSession`; cancellation-safe (exit always returns or retires the worker). This is the `session_factory` the spec requires — `pool.lease` is passed as the factory. Also `_acquire`/`_release` refactor and `PoolWorker(repl, base_env, spec, commands_run)`.
3. `hardy.agent.runtime` (M1 Task 7): `RunConfig(model, max_turns, max_tokens_total=None, wall_clock_s, prompt_version, runtime="claude_sdk")`; `TrajectoryEvent(kind: Literal["assistant_text","tool_call","tool_result","usage"], at, text, tool_name, arguments, content, is_error, input_tokens, output_tokens)`; `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped)`; `AgentRuntime` protocol `async run(task, system_prompt, tools, config) -> Trajectory`. **This plan modifies `RunConfig`** (adds `strategy`, `strategy_params`) **and the `TrajectoryEvent.kind` Literal** (adds `"lesson"`) — additive, flagged as a delta below.
4. `hardy.agent.budget.BudgetMeter` (M1 Task 8): `phase_config(base: RunConfig) -> RunConfig | None`, `settle(trajectory)`, `spent_turns`, `spent_tokens`, `elapsed_s()`, `exhausted_kind()`. M7 does **not** replace it: the Prove workflow still owns one `BudgetMeter` across phases; Task 7 derives the proof phase's `StrategyBudget` from `phase_config(...)`'s remaining allowance and settles the strategy's spend back via a synthetic `Trajectory`. The generalization the specs README names ("M7 generalizes its reserve-and-settle into the shared strategy meter") is `StrategyBudget` (Task 1).
5. `hardy.agent.claude_sdk.estimate_tokens(text) -> int` (M1 Task 9): `max(1, len(text) // 3)` — reused for reservation upper estimates.
6. `hardy.tools.registry` (M1 Task 1): `ToolResult(content, is_error=False)`, `ToolDef(name, description, input_model, handler)`, `ToolRegistry`.
7. `hardy.tools.statement.FrozenStatement(name, header)` with `splice(body) -> f"{header} := {body}"`, and `hardy.tools.lean_tools.make_prove_registry(session, statement, attempts: list[str], wins: list[tuple[str, int]])` (M1 Task 4). The iterative strategy builds this registry over a `MeteredSession` — the metered wrapper mirrors `check`/`tactic`/`goal`/`known_states` exactly so the registry's handlers work unchanged (verify the handlers touch nothing else).
8. `hardy.prompts.get_prompt(name) -> str` (M1 Task 10); Task 4 adds `strategy_v1` templates through the same lookup.
9. `hardy.workflows.prove` (M1 Task 14): `ProveConfig`, `ProveResult`, `async prove(claim, *, pool, runtime, config, results_dir, run_id)`, phases as plain module functions, `wins[-1] == (source, env)` convention. **Task 7 rewrites phase 3** and extends `ProveConfig`/`Manifest`. `hardy.workflows.audit.audit_axioms(session, name, env) -> AuditResult(passed, reason?, axioms?)` (M1 Task 12; exact `AuditResult` fields must be re-checked). `hardy.workflows.persist.Manifest/publish/slugify` (M1 Task 13).
10. `tests/fake_runtime.FakeRuntime(scripts=[[...]])` (M1 Task 7) and the M1 extensions to `tests/fake_repl.py` (tactic magic `TACTIC_ERROR`/`TACTIC_GOALS`, `#print axioms` fixtures).

**From the M2 spec (`2026-07-21-m2-evaluation-harness-design.md`) — spec-only today:**

11. `hardy.eval.benchmark.BenchmarkItem(id, statement, header, domain, split)` and `load_minif2f(path)`. `ProveGoal` (Task 2) maps `BenchmarkItem.header -> preamble`, `BenchmarkItem.statement -> statement`; note the naming collision with M1's `FrozenStatement.header` (which is the *theorem line*) — the mapping table in Task 2 is the resolution.
12. `hardy.eval.runner`: benchmark mode (formalize/faithfulness/writeup skipped), per-attempt `EvalResult` streamed to tracking, `EvalConfig(run_config: RunConfig, attempts_per_item, item_timeout_s, parallelism, benchmark, split)`. **Assumed callable for Task 14:** a per-attempt entry point of the shape `async run_attempt(item: BenchmarkItem, config: EvalConfig, *, pool, runtime, attempt_index: int) -> EvalResult`. The M2 plan may have named/shaped this differently — the comparison harness isolates this behind one adapter function (`_run_one` in `hardy/eval/compare_strategies.py`) so only that glue changes.
13. `hardy.eval.tracking`: append-only `eval_results/runs.jsonl` under interprocess lock; dirty-tree refusal (assumed helper `require_clean_tree()` or equivalent — Task 14 falls back to `git status --porcelain` via subprocess if no helper landed); per-response resolved model revision accumulated in the tracking entry (assumed field `model_revisions: list[str]` or equivalent on the run record — the comparison harness reads whatever field M2 landed and invalidates on multiplicity/absence); corpus digest.
14. `hardy.eval.anticheat.validate(item, submitted_source, trajectory, session) -> AntiCheatReport` — the eval-mode validator wraps this; suspicious-closer classification (`native_decide` = warning flag, not failure).
15. M2's **CPU-sampling monitor** (container cgroup `cpu.stat` / psutil sampling during execution). Task 3's `MeteredSession` defines a `CommandCpuMonitor` protocol and ships a wall-clock×cpu-cap fallback that genuinely enforces the allowance (the sandbox CPU cap bounds cpu ≤ wall × cpus); **wiring the M2 monitor in as the precise implementation is an explicit re-validation point** — if M2 landed a reusable monitor, adapt it behind the protocol.

**From the M5 spec (`2026-07-21-m5-runtime-abstraction-design.md`) — spec-only today:**

16. `hardy.agent.capabilities.RuntimeCapabilities(native_tool_calls, subagents, context_compaction, token_usage_reported)`. **Conflict flag:** the M7 spec says parallel strategies query "`parallelism`-relevant capabilities", but M5's `RuntimeCapabilities` has no parallelism flag. Resolution (Task 12): add `parallel_runs: bool = True` to `RuntimeCapabilities` (minimal-loop adapter reports `False` when its client cannot serve concurrent runs); strategies read it via `getattr(runtime, "capabilities", None)` and degrade to sequential when absent or `False`.
17. `RunConfig.max_cost_usd` + per-model pricing (M5): `StrategyBudget`'s `cost_usd` dimension is only advertised when the run advertises it; `MeteredRuntime` takes `price_per_1k_tokens_usd` and **raises at construction** if the cost dimension is advertised with no pricing (mirrors M5's "cost-capped run with no pricing entry is rejected up front"). If M5 landed provider-reported cost or a richer pricing table, route it through the same constructor seam.
18. `create_runtime(config) -> AgentRuntime` (M5) — the comparison harness builds runtimes from config alone.

**From the M6 spec (`2026-07-21-m6-critique-repair-design.md`) — spec-only today:**

19. `hardy.holes.ledger.HoleLedger` + `Hole(id, location, description, status, layer, reopen_count, history, justification, patch_refs)` with statuses `open/patched/verified-closed/dismissed/abandoned`, harness-owned transitions, JSONL event persistence (`results/<slug>/holes.jsonl`). **Assumed API for the sketch adapter (Task 11):** `HoleLedger.create(location, description, layer, provenance) -> Hole` and `HoleLedger.transition(hole_id, status, **meta)`. This is the plan's one deliberately late-bound seam: sketch code talks to a narrow `SketchLedgerPort` protocol; `M6SketchLedger` (the port's M6-backed implementation) is the only class touching M6 names and is re-shaped against the landed M6 API at execution time. `MemorySketchLedger` keeps sketch unit tests decoupled.
20. M6's `note` tool (agent scratchpad): the sketch plan phase persists the plan through it **when the workflow provides it** (`SketchStrategy` accepts an optional `note_tool: ToolDef`); absent, the plan text is captured from the plan run's `final_text` and recorded in `StrategyResult.detail` — both satisfy the spec's "the `note` tool persists it" once M6 wires the tool in.

**From M0 (implemented code — verified, not assumed):**

21. `LeanRepl.send(request: dict, timeout=None) -> dict` (raw framed request — the pickling wire path), `run_command`, `run_tactic`; `WorkerSpec(argv, cwd, env, reset_argv, cleanup_argv)` — **Task 8 adds `container_name: str | None = None`** (additive; `sandboxed_worker_spec` in `launch.py` already mints the name, it just isn't recorded on the spec). The tar-over-stdout artifact pattern and `_docker_client_env()` (in `hardy/latex/compile.py`) and the `docker exec`/`docker kill` side-channels (in `launch.py`) are the sandbox-transfer precedents Task 9 follows.
22. **Vendor repl pickling commands** (`vendor/repl`): assumed wire shapes `{"pickleTo": <path>, "proofState": <id>}` → tactic-shaped response, and `{"unpickleProofStateFrom": <path>, "env": <id>?}` → `{"proofState": <id>, "goals": [...]}`. **Verify against the pinned `vendor/repl` revision's README/source before Task 8** — if the command names or response shapes differ, change only `hardy/lean/pickle.py`'s two wire functions and the fake-repl fixtures.

**From M4 (spec-only; consumed indirectly):**

23. Base package generation ids and the `Papers.*` garbage collector: `SnapshotStore` registers/drops generation references through a `GenerationRefs` protocol (`add_ref(generation_id, ref_id)` / `drop_ref(generation_id, ref_id)`) with a no-op default; generation-specific pool leases (spec: "the pool supports generation-specific leases") are consumed only when a snapshot carries a `generation_id` — for goals with no `Papers.*` imports (`generation_id=None`, the entire M7 eval path) nothing M4-specific runs. Re-validate the M4 GC's actual registration API at execution; wire it into `M4GenerationRefs` (one small adapter, Task 9).

**File-placement deltas from the spec** (recorded here so implementers don't treat them as drift — same focused-file precedent as M1's `persist.py` delta):

- The spec's `base.py` holds "Strategy protocol, StrategyBudget, StrategyResult". The meter is the largest single piece of M7; it gets its own `hardy/strategy/budget.py`. `base.py` keeps the protocol, `ProveGoal`, `Verdict`, `StrategyResult`, and the registry.
- The enforcement layer ("enforcement lives in the meter/client layer, not in strategy code") gets its own `hardy/strategy/metered.py` (`MeteredRuntime`, `MeteredSession`) — it is the client layer.
- The comparison harness's logic lives in importable `hardy/eval/compare_strategies.py` (+ statistics in `hardy/eval/compare.py`); `scripts/compare_strategies.py` is a thin CLI over it — same script-vs-module split M2 uses for `run_eval.py`.
- The snapshot store (Task 9) lives in `hardy/lean/pickle.py` alongside the wire wrappers, per the spec's file list.

## File Structure

```
src/hardy/strategy/__init__.py        — imports strategy modules (self-registration) + re-exports
src/hardy/strategy/budget.py          — StrategyBudget, Reservation, BudgetSpent, BudgetExpired
src/hardy/strategy/base.py            — ProveGoal, Verdict, StrategyResult, Strategy protocol,
                                        registry (register/create/names), scan_suspicious
src/hardy/strategy/metered.py         — MeteredRuntime, MeteredSession, CommandCpuMonitor
src/hardy/strategy/lessons.py         — Lesson, LessonBook, distill
src/hardy/strategy/closers.py         — closer_sequence, try_closers_state, try_closers_goal
src/hardy/strategy/iterative.py       — IterativeStrategy (baseline) + HybridClosersStrategy ("closers")
src/hardy/strategy/sketch.py          — SketchStrategy, SketchLedgerPort, MemorySketchLedger, M6SketchLedger
src/hardy/strategy/bestfirst.py       — SearchNode, BestFirstStrategy, proposal parsing
src/hardy/strategy/parallel.py        — ParallelStrategy, BranchSpec, PausableRuntime
src/hardy/lean/pickle.py              — pickle_state/unpickle_state wire wrappers, Snapshot,
                                        SnapshotStore, GenerationRefs (+ docker copy helpers)
src/hardy/lean/session.py             — MODIFY: send_raw, replay, adopt_state, base_env, container_name
src/hardy/lean/pool.py                — MODIFY: WorkerSpec.container_name (additive field)
src/hardy/lean/launch.py              — MODIFY: sandboxed_worker_spec records container_name
src/hardy/agent/runtime.py            — MODIFY: RunConfig.strategy/strategy_params; "lesson" event kind
src/hardy/agent/capabilities.py       — MODIFY: RuntimeCapabilities.parallel_runs (M5 file)
src/hardy/prompts/strategy_v1.py      — PROPOSE_TACTICS_V1, SKETCH_PLAN_V1, SKETCH_SKELETON_V1,
                                        SUBGOAL_V1, DISTILL_V1
src/hardy/prompts/__init__.py         — MODIFY: register the strategy_v1 templates
src/hardy/workflows/prove.py          — MODIFY: phase 3 -> strategy seam; pre-pass; validator
src/hardy/workflows/persist.py        — MODIFY: Manifest strategy fields (additive)
src/hardy/eval/compare.py             — mcnemar_exact_p, paired_bootstrap_ci, holm_bonferroni,
                                        DecisionRule, ComparisonOutcome, decide
src/hardy/eval/compare_strategies.py  — run_comparison orchestration (interleave, revision check,
                                        comparison record)
scripts/compare_strategies.py         — CLI over run_comparison (exit-criterion entry point)
tests/stub_session.py                 — StubSession/StubLease (duck-typed ProofSession, no pool)
tests/fake_repl.py                    — MODIFY: pickleTo/unpickleProofStateFrom magic
tests/test_strategy_budget.py
tests/test_strategy_base.py
tests/test_metered.py
tests/test_lessons.py
tests/test_closers.py
tests/test_iterative.py
tests/test_prove_strategy.py          — prove.py rewiring (plus targeted edits to tests/test_prove.py)
tests/test_pickle.py
tests/test_snapshots.py
tests/test_bestfirst.py
tests/test_sketch.py
tests/test_parallel.py
tests/test_strategy_conformance.py
tests/test_compare_stats.py
tests/test_compare_strategies.py
tests/test_integration_pickle.py      — @pytest.mark.lean (two real workers)
tests/test_integration_closers.py     — @pytest.mark.lean
tests/test_integration_sketch.py      — @pytest.mark.lean (canned skeleton, real kernel)
```

**Test tiers:** unit (default, CI), `lean`, `tex`, `docker`, `model` — as in M0–M2. No new markers.

---

### Task 1: `StrategyBudget` — the shared strategy meter (`budget.py`)

**Files:**
- Create: `src/hardy/strategy/__init__.py` (empty for now; populated in Task 12)
- Create: `src/hardy/strategy/budget.py`
- Test: `tests/test_strategy_budget.py`

**Interfaces:**
- Consumes: nothing from Hardy (leaf module; stdlib + pydantic).
- Produces (every later task builds on these exact names):
  - `BudgetSpent(tokens: int = 0, turns: int = 0, cost_usd: float = 0.0, lean_cpu_s: float = 0.0, wall_clock_s: float = 0.0)` (pydantic).
  - `BudgetRemaining(tokens: int | None, turns: int | None, cost_usd: float | None, lean_cpu_s: float | None, wall_s: float)` — `None` = dimension not advertised (unlimited).
  - `BudgetExpired(Exception)` — raised when the wall deadline cancels in-flight work.
  - `Reservation` with `.tokens/.turns/.cost_usd/.lean_cpu_s` (the reserved amounts), `settle(*, tokens=0, turns=0, cost_usd=0.0, lean_cpu_s=0.0) -> None` (records actual, refunds the rest; exactly once), `forfeit() -> None` (keeps the **full** reservation as spent — the cancelled-call rule).
  - `StrategyBudget(*, wall_clock_s: float, tokens: int | None = None, turns: int | None = None, cost_usd: float | None = None, lean_cpu_s: float | None = None, clock: Callable[[], float] = time.monotonic)` with:
    - `try_reserve(*, tokens: int = 0, turns: int = 0, cost_usd: float = 0.0, lean_cpu_s: float = 0.0) -> Reservation | None` — **synchronous, no awaits, therefore atomic under asyncio**; `None` when any advertised dimension can't fund the request or the wall deadline has passed.
    - `remaining() -> BudgetRemaining` — net of held reservations (concurrent viewers see reserved amounts as gone).
    - `remaining_wall_s() -> float`, `expired() -> bool`, `exhausted() -> str | None` (`"wall_clock"`, `"tokens"`, `"turns"`, `"cost_usd"`, `"lean_cpu_s"`, or `None`).
    - `spent() -> BudgetSpent` (wall_clock_s = elapsed since construction).
    - `async run_with_deadline(awaitable)` — `asyncio.wait_for` against the remaining deadline; `TimeoutError` becomes `BudgetExpired` (the awaited work is cancelled).
- The injectable `clock` makes wall-clock tests deterministic (same pattern as M1's `BudgetMeter`).

**Behavior contract (from the spec, restated):**
- Only additive dimensions go through reservations; the wall deadline is monotonic and shared.
- `settle` with actual > reserved is **recorded honestly** (the spec's clipping caveat: settlement never hides overshoot; it makes the next reservation fail instead).
- Once a dimension's available balance (limit − spent − held) hits zero, further reservations of that dimension fail — this is what serializes branch launches when the remainder can no longer fund concurrent reservations.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategy_budget.py
import asyncio

import pytest

from hardy.strategy.budget import BudgetExpired, BudgetSpent, StrategyBudget


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def meter(**kw) -> StrategyBudget:
    defaults = dict(wall_clock_s=100.0, clock=FakeClock())
    defaults.update(kw)
    return StrategyBudget(**defaults)


def test_remaining_reflects_limits_and_unadvertised_none():
    m = meter(tokens=1000, turns=10)
    r = m.remaining()
    assert (r.tokens, r.turns) == (1000, 10)
    assert r.cost_usd is None and r.lean_cpu_s is None   # not advertised
    assert r.wall_s == 100.0


def test_reserve_holds_and_settle_refunds():
    m = meter(tokens=1000)
    res = m.try_reserve(tokens=400)
    assert res is not None
    assert m.remaining().tokens == 600          # held while in flight
    res.settle(tokens=150)
    assert m.remaining().tokens == 850          # 250 refunded
    assert m.spent().tokens == 150


def test_reservation_failure_does_not_start_the_call():
    m = meter(tokens=100)
    assert m.try_reserve(tokens=101) is None
    assert m.remaining().tokens == 100          # nothing held


def test_concurrent_reservations_cannot_overshoot():
    m = meter(tokens=100)
    first = m.try_reserve(tokens=60)
    second = m.try_reserve(tokens=60)           # only 40 left: must fail
    assert first is not None and second is None
    first.settle(tokens=10)
    assert m.try_reserve(tokens=60) is not None  # refund freed the balance


def test_settle_overshoot_recorded_and_blocks_future():
    m = meter(tokens=100)
    res = m.try_reserve(tokens=50)
    res.settle(tokens=90)                        # adapter overshoot: honest
    assert m.spent().tokens == 90
    assert m.try_reserve(tokens=20) is None      # only 10 left
    assert m.exhausted() is None                 # 10 > 0: not yet exhausted
    res2 = m.try_reserve(tokens=10)
    res2.settle(tokens=10)
    assert m.exhausted() == "tokens"


def test_forfeit_keeps_full_reservation():
    m = meter(tokens=100)
    res = m.try_reserve(tokens=70)
    res.forfeit()                                # cancelled in-flight call
    assert m.spent().tokens == 70
    assert m.remaining().tokens == 30


def test_settle_twice_raises():
    m = meter(tokens=100)
    res = m.try_reserve(tokens=10)
    res.settle(tokens=5)
    with pytest.raises(RuntimeError):
        res.settle(tokens=1)
    with pytest.raises(RuntimeError):
        res.forfeit()


def test_wall_deadline_and_exhausted_kind():
    clock = FakeClock()
    m = StrategyBudget(wall_clock_s=50.0, tokens=10, clock=clock)
    assert not m.expired()
    clock.now = 51.0
    assert m.expired()
    assert m.exhausted() == "wall_clock"
    assert m.try_reserve(tokens=1) is None       # expired meter reserves nothing
    assert m.remaining_wall_s() == 0.0           # floored, never negative


def test_multi_dimension_reserve_all_or_nothing():
    m = meter(tokens=100, turns=1)
    res = m.try_reserve(tokens=50, turns=1)
    assert res is not None
    # turns now exhausted by the hold: a joint request must fail atomically
    assert m.try_reserve(tokens=1, turns=1) is None
    assert m.remaining().tokens == 50            # failed reserve held nothing


def test_spent_includes_elapsed_wall():
    clock = FakeClock()
    m = StrategyBudget(wall_clock_s=100.0, clock=clock)
    clock.now = 12.5
    assert m.spent() == BudgetSpent(wall_clock_s=12.5)


async def test_run_with_deadline_cancels_and_raises_budget_expired():
    m = StrategyBudget(wall_clock_s=0.05)        # real clock: tiny deadline
    with pytest.raises(BudgetExpired):
        await m.run_with_deadline(asyncio.sleep(30))


async def test_run_with_deadline_passes_result_through():
    m = StrategyBudget(wall_clock_s=60.0)

    async def work():
        return 42

    assert await m.run_with_deadline(work()) == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/strategy/budget.py
"""The shared strategy meter (M7 spec, base.py section).

One meter per theorem, shared by every branch/subgoal/inner run of a
strategy. Additive dimensions (tokens, turns, cost, Lean CPU) are
reservation-based: the enforcement layer reserves an upper estimate
BEFORE each model call or Lean command, then settles actual usage and
refunds the difference — check-then-spend would let concurrent branches
all observe the same balance and collectively overshoot. try_reserve is
synchronous (no awaits), so it is atomic under asyncio's cooperative
scheduling. Wall clock is the exception: a monotonic run deadline, not a
reservation — three branches running through the same 10 s advance the
theorem's wall time by 10, not 30 — enforced on in-flight work via
run_with_deadline. Enforcement lives in hardy.strategy.metered, never in
strategy code."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

_DIMS = ("tokens", "turns", "cost_usd", "lean_cpu_s")


class BudgetSpent(BaseModel):
    tokens: int = 0
    turns: int = 0
    cost_usd: float = 0.0
    lean_cpu_s: float = 0.0
    wall_clock_s: float = 0.0


class BudgetRemaining(BaseModel):
    tokens: int | None
    turns: int | None
    cost_usd: float | None
    lean_cpu_s: float | None
    wall_s: float


class BudgetExpired(Exception):
    """The wall deadline cancelled in-flight work."""


class Reservation:
    """Held allowances for one in-flight call. settle() exactly once
    (records actual spend, refunds the rest) or forfeit() exactly once
    (a cancelled call keeps its full reservation — provider-side
    generation may have run to completion without reporting usage)."""

    def __init__(self, budget: "StrategyBudget", amounts: dict[str, float]):
        self._budget = budget
        self._amounts = amounts
        self._done = False
        for dim in _DIMS:
            setattr(self, dim, amounts[dim])

    def _finish(self, actual: dict[str, float]) -> None:
        if self._done:
            raise RuntimeError("reservation already settled or forfeited")
        self._done = True
        self._budget._settle(self._amounts, actual)

    def settle(
        self,
        *,
        tokens: int = 0,
        turns: int = 0,
        cost_usd: float = 0.0,
        lean_cpu_s: float = 0.0,
    ) -> None:
        self._finish(
            {
                "tokens": tokens,
                "turns": turns,
                "cost_usd": cost_usd,
                "lean_cpu_s": lean_cpu_s,
            }
        )

    def forfeit(self) -> None:
        self._finish(dict(self._amounts))


class StrategyBudget:
    def __init__(
        self,
        *,
        wall_clock_s: float,
        tokens: int | None = None,
        turns: int | None = None,
        cost_usd: float | None = None,
        lean_cpu_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limits: dict[str, float | None] = {
            "tokens": tokens,
            "turns": turns,
            "cost_usd": cost_usd,
            "lean_cpu_s": lean_cpu_s,
        }
        self._spent: dict[str, float] = {dim: 0.0 for dim in _DIMS}
        self._held: dict[str, float] = {dim: 0.0 for dim in _DIMS}
        self._wall_clock_s = wall_clock_s
        self._clock = clock
        self._start = clock()

    # -- wall clock (deadline, never a reservation) -----------------------

    def remaining_wall_s(self) -> float:
        return max(0.0, self._wall_clock_s - (self._clock() - self._start))

    def expired(self) -> bool:
        return self.remaining_wall_s() <= 0.0

    async def run_with_deadline(self, awaitable: Awaitable):
        remaining = self.remaining_wall_s()
        if remaining <= 0.0:
            # Close the coroutine so an unstarted call never leaks a warning.
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise BudgetExpired("wall-clock budget exhausted")
        try:
            return await asyncio.wait_for(awaitable, remaining)
        except TimeoutError:
            raise BudgetExpired(
                f"wall-clock budget exhausted after {self._wall_clock_s}s"
            ) from None

    # -- additive dimensions (reserve-and-settle) -------------------------

    def _available(self, dim: str) -> float | None:
        limit = self._limits[dim]
        if limit is None:
            return None
        return limit - self._spent[dim] - self._held[dim]

    def try_reserve(
        self,
        *,
        tokens: int = 0,
        turns: int = 0,
        cost_usd: float = 0.0,
        lean_cpu_s: float = 0.0,
    ) -> Reservation | None:
        if self.expired():
            return None
        want = {
            "tokens": float(tokens),
            "turns": float(turns),
            "cost_usd": cost_usd,
            "lean_cpu_s": lean_cpu_s,
        }
        for dim, amount in want.items():
            available = self._available(dim)
            if available is not None and amount > available:
                return None  # all-or-nothing: nothing was held
        for dim, amount in want.items():
            self._held[dim] += amount
        return Reservation(self, want)

    def _settle(self, reserved: dict[str, float], actual: dict[str, float]) -> None:
        for dim in _DIMS:
            self._held[dim] -= reserved[dim]
            self._spent[dim] += actual[dim]

    # -- observation ------------------------------------------------------

    def remaining(self) -> BudgetRemaining:
        def net(dim: str) -> float | None:
            available = self._available(dim)
            return None if available is None else max(0.0, available)

        tokens = net("tokens")
        turns = net("turns")
        return BudgetRemaining(
            tokens=None if tokens is None else int(tokens),
            turns=None if turns is None else int(turns),
            cost_usd=net("cost_usd"),
            lean_cpu_s=net("lean_cpu_s"),
            wall_s=self.remaining_wall_s(),
        )

    def exhausted(self) -> str | None:
        if self.expired():
            return "wall_clock"
        for dim in _DIMS:
            available = self._available(dim)
            if available is not None and available <= 0.0:
                return dim
        return None

    def spent(self) -> BudgetSpent:
        return BudgetSpent(
            tokens=int(self._spent["tokens"]),
            turns=int(self._spent["turns"]),
            cost_usd=self._spent["cost_usd"],
            lean_cpu_s=self._spent["lean_cpu_s"],
            wall_clock_s=self._clock() - self._start,
        )
```

Also create empty `src/hardy/strategy/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_budget.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/strategy/ tests/test_strategy_budget.py
git commit -m "feat: shared strategy budget meter — atomic reservations, wall deadline"
```

---

### Task 2: The strategy seam (`base.py`) + `RunConfig` strategy axis

**Files:**
- Create: `src/hardy/strategy/base.py`
- Modify: `src/hardy/agent/runtime.py` (add `RunConfig.strategy`/`strategy_params`; extend `TrajectoryEvent.kind` with `"lesson"`)
- Test: `tests/test_strategy_base.py`

**Interfaces:**
- Consumes: `BudgetSpent`, `StrategyBudget` (Task 1); `TrajectoryEvent`, `RunConfig`, `AgentRuntime` (M1); `ProofSession` (M1, type only).
- Produces (exact names every strategy and the workflow use):
  - `ProveGoal(name: str, statement: str, preamble: str = "")` with `full_statement() -> str` (preamble + newline + statement when preamble is non-empty) and `splice(body: str) -> str` (`full_statement() + " := " + body`). Mapping: Prove workflow → `ProveGoal(name=frozen.name, statement=frozen.header)`; benchmark mode → `ProveGoal(name=item.id-derived name, statement=item.statement, preamble=item.header)`.
  - `Verdict(passed: bool, flags: list[str] = [], detail: str = "")`.
  - `ValidateFn = Callable[[str, ProofSession, Sequence[TrajectoryEvent]], Awaitable[Verdict]]` — the harness-owned downstream validator; `events` is the producing branch's event stream, passed at validation time.
  - `SessionFactory = Callable[[], AsyncContextManager[ProofSession]]` — `pool.lease` satisfies it.
  - `StrategyResult(proved: bool, source: str | None = None, verdict: Verdict | None = None, budget_spent: BudgetSpent, events: list[TrajectoryEvent] = [], detail: dict = {})`.
  - `Strategy` protocol: attribute `name: str`; `async prove(self, goal: ProveGoal, *, session_factory: SessionFactory, runtime: AgentRuntime, config: RunConfig, budget: StrategyBudget, validate: ValidateFn) -> StrategyResult`.
  - Registry: `register_strategy(name: str, factory: Callable[[dict], Strategy])`, `create_strategy(name: str, params: dict) -> Strategy` (raises `KeyError` with the known names on an unknown strategy), `strategy_names() -> list[str]`.
  - `scan_suspicious(source: str, events: Sequence[TrajectoryEvent]) -> list[str]` — lexical `native_decide` token scan of the source **and** of every `run_tactic` tool-call's arguments in the event stream (a `native_decide` invoked via `run_tactic` may not appear in the final source). Returns e.g. `["native_decide"]`, deduplicated.
- `RunConfig` gains `strategy: str = "iterative"` and `strategy_params: dict = {}` — config, not code, selects and parameterizes; the M2 config hash picks both up automatically (they are ordinary pydantic fields).
- `TrajectoryEvent.kind` gains `"lesson"` (Task 4 emits it; recorded-in-trajectory is a spec requirement).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategy_base.py
import pytest

from hardy.agent.runtime import RunConfig, TrajectoryEvent
from hardy.strategy.base import (
    ProveGoal,
    StrategyResult,
    Verdict,
    create_strategy,
    register_strategy,
    scan_suspicious,
    strategy_names,
)
from hardy.strategy.budget import BudgetSpent


def test_prove_goal_splice_without_preamble():
    goal = ProveGoal(name="t", statement="theorem t : True")
    assert goal.full_statement() == "theorem t : True"
    assert goal.splice("trivial") == "theorem t : True := trivial"


def test_prove_goal_splice_with_benchmark_preamble():
    goal = ProveGoal(
        name="amc_1",
        statement="theorem amc_1 : 1 + 1 = 2",
        preamble="import Mathlib\nset_option maxHeartbeats 400000",
    )
    assert goal.full_statement() == (
        "import Mathlib\nset_option maxHeartbeats 400000\ntheorem amc_1 : 1 + 1 = 2"
    )
    assert goal.splice("by norm_num").endswith("theorem amc_1 : 1 + 1 = 2 := by norm_num")


def test_strategy_result_defaults():
    result = StrategyResult(proved=False, budget_spent=BudgetSpent())
    assert result.source is None and result.verdict is None
    assert result.events == [] and result.detail == {}


def test_registry_register_create_and_unknown():
    class Dummy:
        name = "dummy"

        def __init__(self, params):
            self.params = params

        async def prove(self, goal, **kw):  # pragma: no cover - shape only
            raise NotImplementedError

    register_strategy("dummy", lambda params: Dummy(params))
    strategy = create_strategy("dummy", {"x": 1})
    assert strategy.params == {"x": 1}
    assert "dummy" in strategy_names()
    with pytest.raises(KeyError, match="dummy"):   # message lists known names
        create_strategy("nope", {})


def test_registry_duplicate_rejected():
    register_strategy("dupe", lambda p: object())
    with pytest.raises(ValueError, match="dupe"):
        register_strategy("dupe", lambda p: object())


def test_scan_suspicious_source_token():
    assert scan_suspicious("theorem t : P := by native_decide", []) == ["native_decide"]
    # substring inside an identifier is not a token hit
    assert scan_suspicious("theorem native_decider : P := by simp", []) == []


def test_scan_suspicious_trajectory_run_tactic():
    events = [
        TrajectoryEvent(
            kind="tool_call", at=0.1, tool_name="run_tactic",
            arguments={"tactic": "native_decide", "proof_state": 0},
        )
    ]
    # not in the final source, only in the trajectory: still flagged
    assert scan_suspicious("theorem t : P := by simp", events) == ["native_decide"]


def test_scan_suspicious_deduplicates():
    events = [
        TrajectoryEvent(
            kind="tool_call", at=0.1, tool_name="run_tactic",
            arguments={"tactic": "native_decide"},
        )
    ]
    flags = scan_suspicious("x := by native_decide", events)
    assert flags == ["native_decide"]


def test_runconfig_strategy_axis_defaults():
    cfg = RunConfig(model="m", max_turns=5, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    assert cfg.strategy == "iterative"
    assert cfg.strategy_params == {}


def test_trajectory_event_lesson_kind_accepted():
    event = TrajectoryEvent(kind="lesson", at=1.0, text="ring needs CommRing")
    assert event.kind == "lesson"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.base'`

- [ ] **Step 3: Modify `runtime.py`** (two additive edits)

In `src/hardy/agent/runtime.py`:

```python
# RunConfig gains the strategy axis (M7): config, not code, selects and
# parameterizes the search strategy; both fields land in the M2 config hash.
class RunConfig(BaseModel):
    model: str
    max_turns: int
    max_tokens_total: int | None = None
    wall_clock_s: float
    prompt_version: str
    runtime: str = "claude_sdk"
    strategy: str = "iterative"
    strategy_params: dict = {}
```

(Keep every field M5 added — the two new lines go at the end of the class; do not reorder or remove existing fields.)

```python
# TrajectoryEvent.kind gains "lesson" (M7 lesson distillation records into
# the trajectory for later analysis):
    kind: Literal["assistant_text", "tool_call", "tool_result", "usage", "lesson"]
```

- [ ] **Step 4: Write `base.py`**

```python
# src/hardy/strategy/base.py
"""The strategy seam (DESIGN.md Component 4; M7 spec base.py).

A Strategy is selected and parameterized by config (RunConfig.strategy +
strategy_params) and invoked by the Prove workflow's proof phase (and,
unchanged, by benchmark mode). The harness injects: a session factory
(the pool's cancellation-safe lease), the agent runtime, the shared
StrategyBudget meter, and the downstream validator — the sole success
authority. Strategies return a StrategyResult whose events fold into the
run's Trajectory so M2 metrics and telemetry see strategy-internal work."""

import re
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from hardy.agent.runtime import AgentRuntime, RunConfig, TrajectoryEvent
from hardy.lean.session import ProofSession
from hardy.strategy.budget import BudgetSpent, StrategyBudget


class ProveGoal(BaseModel):
    """The harness-owned statement + header (the operand Prove/eval pass
    around). `statement` is the bodyless theorem line(s) (M1's
    FrozenStatement.header); `preamble` is a benchmark item's verbatim
    imports/options block (M2's BenchmarkItem.header), empty in Prove."""

    name: str
    statement: str
    preamble: str = ""

    def full_statement(self) -> str:
        if self.preamble:
            return f"{self.preamble}\n{self.statement}"
        return self.statement

    def splice(self, body: str) -> str:
        return f"{self.full_statement()} := {body}"


class Verdict(BaseModel):
    passed: bool
    flags: list[str] = []
    detail: str = ""


ValidateFn = Callable[
    [str, ProofSession, Sequence[TrajectoryEvent]], Awaitable[Verdict]
]
SessionFactory = Callable[[], AbstractAsyncContextManager[ProofSession]]


class StrategyResult(BaseModel):
    proved: bool
    source: str | None = None
    verdict: Verdict | None = None
    budget_spent: BudgetSpent
    events: list[TrajectoryEvent] = []
    detail: dict = {}


@runtime_checkable
class Strategy(Protocol):
    name: str

    async def prove(
        self,
        goal: ProveGoal,
        *,
        session_factory: SessionFactory,
        runtime: AgentRuntime,
        config: RunConfig,
        budget: StrategyBudget,
        validate: ValidateFn,
    ) -> StrategyResult: ...


# -- registry (config, not code, selects the strategy) ---------------------

_REGISTRY: dict[str, Callable[[dict], Strategy]] = {}


def register_strategy(name: str, factory: Callable[[dict], Strategy]) -> None:
    if name in _REGISTRY:
        raise ValueError(f"duplicate strategy name: {name}")
    _REGISTRY[name] = factory


def create_strategy(name: str, params: dict) -> Strategy:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown strategy {name!r}; known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name](dict(params))


def strategy_names() -> list[str]:
    return sorted(_REGISTRY)


# -- suspicious-closer scan (shared by validators) -------------------------

_NATIVE_DECIDE_RE = re.compile(r"(?<![A-Za-z0-9_'.])native_decide(?![A-Za-z0-9_'.])")


def scan_suspicious(
    source: str, events: Sequence[TrajectoryEvent]
) -> list[str]:
    """Flag native_decide in the candidate source OR anywhere in the
    producing branch's run_tactic trajectory — a native_decide invoked via
    run_tactic may not appear literally in the final source. Flags are
    warnings (M2 discipline), never automatic failures."""
    flags: list[str] = []
    if _NATIVE_DECIDE_RE.search(source):
        flags.append("native_decide")
    for event in events:
        if event.kind != "tool_call" or event.tool_name != "run_tactic":
            continue
        tactic = (event.arguments or {}).get("tactic", "")
        if _NATIVE_DECIDE_RE.search(str(tactic)) and "native_decide" not in flags:
            flags.append("native_decide")
    return flags
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_strategy_base.py tests/test_runtime.py -v`
Expected: all PASS (existing runtime tests stay green — the new fields default)

- [ ] **Step 6: Commit**

```bash
git add src/hardy/strategy/base.py src/hardy/agent/runtime.py tests/test_strategy_base.py
git commit -m "feat: Strategy protocol, ProveGoal, registry; RunConfig strategy axis"
```

---
### Task 3: The enforcement layer (`metered.py`) — `MeteredRuntime` + `MeteredSession`

**Files:**
- Create: `src/hardy/strategy/metered.py`
- Test: `tests/test_metered.py`

**Interfaces:**
- Consumes: `StrategyBudget`/`Reservation`/`BudgetExpired` (Task 1); `AgentRuntime`/`RunConfig`/`Trajectory`/`TrajectoryEvent` (M1); `estimate_tokens` (M1 `hardy.agent.claude_sdk`); `ProofSession`/`CheckOutcome`/`TacticOutcome` (M1).
- Produces (every strategy calls through these — strategy code never touches the meter's reserve/settle directly):
  - `MeteredRuntime(runtime: AgentRuntime, budget: StrategyBudget, base: RunConfig, *, price_per_1k_tokens_usd: float | None = None, response_reserve_per_turn: int = 1024, min_useful_tokens: int = 512)` with:
    - `async run(task: str, system_prompt: str, tools: ToolRegistry, *, turns: int, model: str | None = None) -> Trajectory | None` — `None` means the reservation failed or budget is gone (**the call was never issued**); raises `BudgetExpired` when the wall deadline cancels an in-flight call (the reservation is **forfeited** — cancelled calls keep their full reservation).
    - `events: list[TrajectoryEvent]` — accumulated across every `run`, in order.
  - `CommandCpuMonitor` protocol: `async bounded(call: Callable[[float], Awaitable[T]], allowance_s: float) -> tuple[T, float]` — runs `call(effective_timeout_s)`, enforcing the CPU allowance **during** execution, returning the result and measured CPU seconds. Default `WallClockCpuMonitor(cpus: float = 1.0)` enforces via the timeout bound (`allowance_s / cpus`) — sound because the sandbox CPU cap guarantees cpu ≤ wall × cpus — and charges `min(elapsed × cpus, allowance)`. **Re-validation point:** swap in an adapter over M2's cgroup-sampling monitor for measured (not estimated) charge; the protocol is the seam.
  - `MeteredSession(session: ProofSession, budget: StrategyBudget, *, cpu_per_command_s: float = 60.0, cpu_monitor: CommandCpuMonitor | None = None)` with:
    - `async check(code, timeout=None) -> CheckOutcome | None` and `async tactic(tactic, proof_state, timeout=None) -> TacticOutcome | None` — `None` = reservation failed (command never sent). Effective timeout = min(caller timeout or session default, CPU-allowance bound, remaining wall).
    - Pass-throughs mirroring `ProofSession` exactly so M1's `make_prove_registry` handlers work unchanged over a `MeteredSession`: `goal(proof_state)`, `known_states()`, `states_lost` (property), plus `session` (the wrapped `ProofSession`, for validators and pickling).
- Reservation policy (the spec's "upper estimate for the call"):
  - Model call: turns reserved = `min(turns, remaining.turns)` (when advertised); tokens reserved = `min(estimate_tokens(system_prompt + task) + response_reserve_per_turn × turns_reserved, remaining.tokens)` — refused (return `None`) when the clip leaves less than `min_useful_tokens`; cost reserved = tokens_reserved × price / 1000 when the cost dimension is advertised. The inner `RunConfig` caps the run at exactly the reserved amounts (`max_turns`, `max_tokens_total`) and the remaining wall — this is how "each inner call receives only the meter's remaining allowances as its config" (spec) is implemented under concurrency: reserved chunk, not full remainder.
  - Lean command: `lean_cpu_s` reserved = `min(cpu_per_command_s, remaining.lean_cpu_s)` when advertised (refused at ≤ 0); enforced during the command by the monitor; settled at measured (or estimated-capped) usage.
  - Constructing a `MeteredRuntime` for a budget that advertises `cost_usd` **without** a price raises `ValueError` (M5's "cost-capped run with no pricing entry is rejected up front").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metered.py
import asyncio
import sys

import pytest

from hardy.agent.runtime import RunConfig, Trajectory
from hardy.lean.pool import ReplPool
from hardy.strategy.budget import BudgetExpired, StrategyBudget
from hardy.strategy.metered import MeteredRuntime, MeteredSession
from hardy.tools.registry import ToolRegistry
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]


def cfg(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=99, wall_clock_s=600.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


def reg() -> ToolRegistry:
    return ToolRegistry([])


# -- MeteredRuntime ----------------------------------------------------------

async def test_run_reserves_settles_and_accumulates_events():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, turns=10)
    fake = FakeRuntime(scripts=[[{"text": "one"}], [{"text": "two"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    traj = await mrt.run("task", "sys", reg(), turns=3)
    assert traj is not None and traj.final_text == "one"
    # FakeRuntime charges turns=len(script)=1, tokens=10; refund happened
    assert budget.spent().turns == 1
    assert budget.spent().tokens == 10
    assert budget.remaining().turns == 9
    await mrt.run("task2", "sys", reg(), turns=3)
    assert [e.text for e in mrt.events if e.kind == "assistant_text"] == ["one", "two"]


async def test_run_caps_inner_config_at_reserved_chunk():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, turns=4)
    fake = FakeRuntime(scripts=[[{"text": "x"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    await mrt.run("task", "sys", reg(), turns=10)      # asked 10, only 4 remain
    inner = fake.calls[0]["config"]
    assert inner.max_turns == 4                        # clipped to remaining
    assert inner.max_tokens_total is not None
    assert inner.max_tokens_total <= 10_000


async def test_reservation_failure_never_issues_the_call():
    budget = StrategyBudget(wall_clock_s=600.0, turns=0)
    fake = FakeRuntime(scripts=[[{"text": "never"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    assert await mrt.run("task", "sys", reg(), turns=1) is None
    assert fake.calls == []                            # script unconsumed


async def test_token_floor_refuses_useless_run():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100)   # < min_useful 512
    fake = FakeRuntime(scripts=[[{"text": "never"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    assert await mrt.run("task", "sys", reg(), turns=1) is None
    assert fake.calls == []


async def test_deadline_cancels_inflight_and_forfeits_reservation():
    class SlowRuntime:
        async def run(self, task, system_prompt, tools, config):
            await asyncio.sleep(30)

    budget = StrategyBudget(wall_clock_s=0.05, tokens=10_000, turns=10)
    mrt = MeteredRuntime(SlowRuntime(), budget, cfg())
    with pytest.raises(BudgetExpired):
        await mrt.run("task", "sys", reg(), turns=2)
    # cancelled in-flight call keeps its FULL reservation
    assert budget.spent().turns == 2
    assert budget.spent().tokens >= 512


async def test_cancellation_forfeits_reservation():
    started = asyncio.Event()

    class HangingRuntime:
        async def run(self, task, system_prompt, tools, config):
            started.set()
            await asyncio.sleep(30)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, turns=10)
    mrt = MeteredRuntime(HangingRuntime(), budget, cfg())
    task = asyncio.ensure_future(mrt.run("task", "sys", reg(), turns=2))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.spent().turns == 2                   # full reservation kept


def test_cost_dimension_without_price_rejected():
    budget = StrategyBudget(wall_clock_s=600.0, cost_usd=1.0)
    with pytest.raises(ValueError, match="pricing"):
        MeteredRuntime(FakeRuntime(scripts=[]), budget, cfg())


async def test_cost_reserved_and_settled():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, cost_usd=1.0)
    fake = FakeRuntime(scripts=[[{"text": "x"}]])
    mrt = MeteredRuntime(fake, budget, cfg(), price_per_1k_tokens_usd=10.0)
    await mrt.run("task", "sys", reg(), turns=1)
    # actual: 10 tokens at $10/1k = $0.10
    assert budget.spent().cost_usd == pytest.approx(0.10)
    assert budget.remaining().cost_usd == pytest.approx(0.90)


# -- MeteredSession ----------------------------------------------------------

async def with_pool_session(fn):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await fn(session)
    finally:
        await pool.close()


async def test_check_reserves_and_settles_lean_cpu():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=600.0, lean_cpu_s=100.0)
        metered = MeteredSession(session, budget, cpu_per_command_s=30.0)
        out = await metered.check("theorem t : True := trivial")
        assert out is not None and out.verdict.complete
        spent = budget.spent().lean_cpu_s
        assert 0.0 < spent <= 30.0                    # settled at measured, <= allowance
        assert budget.remaining().lean_cpu_s == pytest.approx(100.0 - spent)

    await with_pool_session(body)


async def test_check_refused_when_cpu_exhausted():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=600.0, lean_cpu_s=0.0)
        metered = MeteredSession(session, budget)
        assert await metered.check("anything") is None

    await with_pool_session(body)


async def test_cpu_allowance_bounds_the_command_duration():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=600.0, lean_cpu_s=1.0)
        metered = MeteredSession(session, budget, cpu_per_command_s=1.0)
        import time
        t0 = time.monotonic()
        out = await metered.check("HANG")             # fake never responds
        elapsed = time.monotonic() - t0
        assert elapsed < 10.0                          # killed at the allowance
        assert out is not None and out.verdict.failure == "timeout"
        assert budget.spent().lean_cpu_s <= 1.0

    await with_pool_session(body)


async def test_unadvertised_cpu_needs_no_reservation():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=600.0)   # no lean_cpu dimension
        metered = MeteredSession(session, budget)
        out = await metered.check("theorem t : True := trivial")
        assert out is not None and out.verdict.complete

    await with_pool_session(body)


async def test_session_passthroughs_mirror_proof_session():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=600.0)
        metered = MeteredSession(session, budget)
        await metered.check("theorem t : True := by sorry")
        assert metered.goal(0) == "⊢ True"
        assert metered.known_states() == [0]
        assert metered.states_lost is False
        result = await metered.tactic("TACTIC_GOALS", proof_state=0)
        assert result is not None and result.ok and result.proof_state == 1
        assert metered.session is session

    await with_pool_session(body)


async def test_expired_wall_refuses_commands():
    async def body(session):
        budget = StrategyBudget(wall_clock_s=0.0)
        metered = MeteredSession(session, budget)
        assert await metered.check("anything") is None

    await with_pool_session(body)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metered.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.metered'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/strategy/metered.py
"""The enforcement layer: every model call and Lean command a strategy
makes goes through these wrappers, which reserve from the shared
StrategyBudget before the call, run it under the remaining wall deadline,
and settle actual usage after. Strategy code never touches reserve/settle
directly (spec: enforcement lives in the meter/client layer).

Cancelled in-flight model calls keep their FULL reservation — task
cancellation doesn't necessarily stop provider-side generation or return
usage, so settling cancelled calls at zero would understate real spend.

Lean CPU is enforced DURING the command via CommandCpuMonitor. The
default WallClockCpuMonitor bounds cpu by wall x cpus (sound under the
sandbox CPU cap) and charges the capped estimate; M2's cgroup-sampling
monitor slots in behind the same protocol for measured charging."""

import time
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from hardy.agent.claude_sdk import estimate_tokens
from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory, TrajectoryEvent
from hardy.lean.session import CheckOutcome, ProofSession, TacticOutcome
from hardy.strategy.budget import BudgetExpired, StrategyBudget
from hardy.tools.registry import ToolRegistry

T = TypeVar("T")


class MeteredRuntime:
    def __init__(
        self,
        runtime: AgentRuntime,
        budget: StrategyBudget,
        base: RunConfig,
        *,
        price_per_1k_tokens_usd: float | None = None,
        response_reserve_per_turn: int = 1024,
        min_useful_tokens: int = 512,
    ):
        if budget.remaining().cost_usd is not None and price_per_1k_tokens_usd is None:
            raise ValueError(
                "budget advertises cost_usd but no pricing was provided; "
                "a cost cap without pricing is unenforceable"
            )
        self._runtime = runtime
        self._budget = budget
        self._base = base
        self._price = price_per_1k_tokens_usd
        self._response_reserve = response_reserve_per_turn
        self._min_useful = min_useful_tokens
        self.events: list[TrajectoryEvent] = []

    def _cost_of(self, tokens: int) -> float:
        return 0.0 if self._price is None else tokens * self._price / 1000.0

    async def run(
        self,
        task: str,
        system_prompt: str,
        tools: ToolRegistry,
        *,
        turns: int,
        model: str | None = None,
    ) -> Trajectory | None:
        remaining = self._budget.remaining()
        if remaining.wall_s <= 0.0:
            return None
        want_turns = turns if remaining.turns is None else min(turns, remaining.turns)
        if want_turns < 1:
            return None
        want_tokens = (
            estimate_tokens(system_prompt + task)
            + self._response_reserve * want_turns
        )
        if remaining.tokens is not None:
            want_tokens = min(want_tokens, remaining.tokens)
            if want_tokens < self._min_useful:
                return None
        reservation = self._budget.try_reserve(
            tokens=want_tokens,
            turns=want_turns,
            cost_usd=self._cost_of(want_tokens),
        )
        if reservation is None:
            return None
        inner = self._base.model_copy(
            update={
                "model": model or self._base.model,
                "max_turns": want_turns,
                "max_tokens_total": (
                    want_tokens if remaining.tokens is not None else None
                ),
                "wall_clock_s": self._budget.remaining_wall_s(),
            }
        )
        try:
            trajectory = await self._budget.run_with_deadline(
                self._runtime.run(task, system_prompt, tools, inner)
            )
        except (BudgetExpired, BaseException):
            # Deadline expiry AND caller cancellation both leave an in-flight
            # provider call with unconfirmed usage: keep the full reservation.
            reservation.forfeit()
            raise
        reservation.settle(
            tokens=trajectory.tokens_used,
            turns=trajectory.turns,
            cost_usd=self._cost_of(trajectory.tokens_used),
        )
        self.events.extend(trajectory.events)
        return trajectory


class CommandCpuMonitor(Protocol):
    async def bounded(
        self, call: Callable[[float], Awaitable[T]], allowance_s: float
    ) -> tuple[T, float]: ...


class WallClockCpuMonitor:
    """Fallback CPU enforcement: bound the command's wall time so that
    cpu <= wall x cpus can never exceed the allowance (the sandbox's CPU
    cap makes the inequality hold), and charge the capped estimate.
    Swap in an adapter over M2's cgroup-sampling monitor for measured
    charging — same protocol."""

    def __init__(self, cpus: float = 1.0):
        self._cpus = max(0.1, cpus)

    async def bounded(
        self, call: Callable[[float], Awaitable[T]], allowance_s: float
    ) -> tuple[T, float]:
        timeout = allowance_s / self._cpus
        start = time.monotonic()
        result = await call(timeout)
        elapsed = time.monotonic() - start
        return result, min(elapsed * self._cpus, allowance_s)


class MeteredSession:
    """ProofSession wrapper mirroring check/tactic/goal/known_states/
    states_lost exactly, so M1's make_prove_registry handlers run
    unchanged over it. check/tactic return None when the reservation
    failed (the command was never sent)."""

    def __init__(
        self,
        session: ProofSession,
        budget: StrategyBudget,
        *,
        cpu_per_command_s: float = 60.0,
        cpu_monitor: CommandCpuMonitor | None = None,
    ):
        self.session = session
        self._budget = budget
        self._cpu_per_command = cpu_per_command_s
        self._monitor = cpu_monitor or WallClockCpuMonitor()

    def _cpu_allowance(self) -> float | None:
        """None = dimension not advertised; float = allowance to reserve."""
        remaining = self._budget.remaining().lean_cpu_s
        if remaining is None:
            return None
        return min(self._cpu_per_command, remaining)

    async def _metered_command(
        self,
        run: Callable[[float | None], Awaitable[T]],
        timeout: float | None,
    ) -> T | None:
        wall = self._budget.remaining_wall_s()
        if wall <= 0.0:
            return None
        allowance = self._cpu_allowance()
        if allowance is None:
            # No CPU dimension: enforce only the wall deadline.
            effective = wall if timeout is None else min(timeout, wall)
            return await run(effective)
        if allowance <= 0.0:
            return None
        reservation = self._budget.try_reserve(lean_cpu_s=allowance)
        if reservation is None:
            return None

        async def bounded_call(monitor_timeout: float) -> T:
            effective = min(monitor_timeout, wall)
            if timeout is not None:
                effective = min(effective, timeout)
            return await run(effective)

        try:
            result, cpu_used = await self._monitor.bounded(bounded_call, allowance)
        except BaseException:
            reservation.forfeit()
            raise
        reservation.settle(lean_cpu_s=cpu_used)
        return result

    async def check(
        self, code: str, timeout: float | None = None
    ) -> CheckOutcome | None:
        return await self._metered_command(
            lambda t: self.session.check(code, timeout=t), timeout
        )

    async def tactic(
        self, tactic: str, proof_state: int, timeout: float | None = None
    ) -> TacticOutcome | None:
        return await self._metered_command(
            lambda t: self.session.tactic(tactic, proof_state, timeout=t), timeout
        )

    # -- pass-throughs (registry handlers and strategies use these) -------

    def goal(self, proof_state: int) -> str | None:
        return self.session.goal(proof_state)

    def known_states(self) -> list[int]:
        return self.session.known_states()

    @property
    def states_lost(self) -> bool:
        return self.session.states_lost
```

**Implementation note (`make_prove_registry` compatibility):** M1's prove-registry handlers call `session.check(...)` / `session.tactic(...)` and read `CheckOutcome`/`TacticOutcome`. `MeteredSession` returns `None` on a failed reservation where `ProofSession` never does — Task 6 wraps registry construction so a `None` renders as an actionable "budget exhausted — stop calling tools and summarize" tool error. If the landed M1 handlers touch other `ProofSession` members, mirror those too (re-validation point).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metered.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/strategy/metered.py tests/test_metered.py
git commit -m "feat: metered enforcement layer — reserved model calls, CPU-bounded Lean commands"
```

---

### Task 4: Lessons (`lessons.py`) + strategy prompts

**Files:**
- Create: `src/hardy/strategy/lessons.py`
- Create: `src/hardy/prompts/strategy_v1.py`
- Modify: `src/hardy/prompts/__init__.py` (register the new templates in the version lookup)
- Test: `tests/test_lessons.py`

**Interfaces:**
- Consumes: `MeteredRuntime` (Task 3), `TrajectoryEvent` with `"lesson"` kind (Task 2), `get_prompt` (M1), `ToolRegistry` (M1).
- Produces:
  - `Lesson(text: str, origin: str)` (pydantic).
  - `LessonBook(cap: int = 20)` with `add(text: str, origin: str) -> None` (drop-oldest beyond cap), `render() -> str` (`""` when empty; otherwise a numbered "Lessons from failed attempts" block for prompt injection), `__len__`, and `events: list[TrajectoryEvent]` (one `kind="lesson"` event per add — the trajectory record; **not** dropped when the lesson rotates out of the book, so telemetry keeps the full history).
  - `async distill(mrt: MeteredRuntime, failure_context: str, origin: str, book: LessonBook, *, max_chars: int = 300) -> Lesson | None` — one cheap summarization call (`turns=1`, empty tool registry, `DISTILL_V1` prompt); `None` when the budget refuses the call (a failed distillation never blocks the strategy). The result is truncated to `max_chars` and added to the book.
  - Prompt templates in `strategy_v1.py`, registered under these names in the M1 prompt lookup: `"distill_v1"`, `"propose_tactics_v1"`, `"sketch_plan_v1"`, `"sketch_skeleton_v1"`, `"subgoal_v1"`.

- [ ] **Step 1: Write the prompt templates**

```python
# src/hardy/prompts/strategy_v1.py
"""Versioned prompt templates for M7 strategies. Same discipline as
prove_v1: templates are constants, looked up by name, recorded in configs
by version string so prompt changes are visible in tracking entries."""

DISTILL_V1 = """You are distilling one failed proof attempt into a single \
reusable lesson for later attempts on the same theorem.

Failure context:
{failure_context}

Reply with ONE sentence (max 40 words) stating what failed and why, in a \
form useful to a future attempt (e.g. "`ring` fails here because the goal \
is not in a commutative ring"). No preamble, no advice lists — one sentence."""

PROPOSE_TACTICS_V1 = """You are doing best-first tactic search in Lean 4 \
(Mathlib available). Current goal state:

{goals}

{lessons}
Previously failed tactics on THIS state (do not repeat them):
{failed}

Propose exactly {k} candidate next tactics, most promising first. One per \
line, in the format `SCORE TACTIC` where SCORE is a confidence in [0,1], \
e.g.:
0.9 simp [Nat.add_comm]
0.4 induction n
No other text."""

SKETCH_PLAN_V1 = """Write an informal proof plan for this Lean 4 theorem:

{statement}

{lessons}
Number the main steps (3-8 steps). Each step should be one provable claim. \
Be concrete about the mathematical facts used. Plain text only."""

SKETCH_SKELETON_V1 = """Render the proof plan below as a Lean 4 term-mode \
proof body for the theorem, decomposed with `have` steps whose proofs are \
all `sorry`. Submit it with the submit_skeleton tool. The harness owns the \
theorem statement — you produce ONLY the body (what follows `:=`).

Theorem:
{statement}

Plan:
{plan}

Rules: every leaf proof is `sorry`; the final line closes the goal from \
the `have`s; no additional declarations, no imports, no comments."""

SUBGOAL_V1 = """Prove one subgoal of a larger Lean 4 proof. The enclosing \
skeleton is fixed; you fill exactly one hole.

Overall plan:
{plan}

Your subgoal (hypotheses above the turnstile are available):
{goal}

{lessons}
Use the check_subgoal tool to submit candidate proof text for this hole \
(a tactic block like `by ...` or a term). Iterate on the errors it \
returns until it reports the hole is closed, then stop."""
```

- [ ] **Step 2: Register the templates**

In `src/hardy/prompts/__init__.py`, extend the M1 name→template table (whatever its landed shape — the M1 plan has `get_prompt(name)` over a dict) with:

```python
from .strategy_v1 import (
    DISTILL_V1,
    PROPOSE_TACTICS_V1,
    SKETCH_PLAN_V1,
    SKETCH_SKELETON_V1,
    SUBGOAL_V1,
)

_PROMPTS.update({
    "distill_v1": DISTILL_V1,
    "propose_tactics_v1": PROPOSE_TACTICS_V1,
    "sketch_plan_v1": SKETCH_PLAN_V1,
    "sketch_skeleton_v1": SKETCH_SKELETON_V1,
    "subgoal_v1": SUBGOAL_V1,
})
```

(Adapt the mechanical registration to the landed lookup structure; the five names are the contract.)

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_lessons.py
from hardy.agent.runtime import RunConfig
from hardy.prompts import get_prompt
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.lessons import LessonBook, distill
from hardy.strategy.metered import MeteredRuntime
from tests.fake_runtime import FakeRuntime


def cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=99, wall_clock_s=600.0,
                     prompt_version="prove_v1")


def test_book_caps_with_drop_oldest():
    book = LessonBook(cap=3)
    for i in range(5):
        book.add(f"lesson {i}", origin="test")
    assert len(book) == 3
    rendered = book.render()
    assert "lesson 2" in rendered and "lesson 4" in rendered
    assert "lesson 0" not in rendered and "lesson 1" not in rendered
    # trajectory events keep the FULL history, including rotated-out lessons
    assert len(book.events) == 5
    assert all(e.kind == "lesson" for e in book.events)


def test_empty_book_renders_empty():
    assert LessonBook().render() == ""


def test_render_is_a_numbered_block():
    book = LessonBook()
    book.add("ring fails: not a CommRing", origin="bestfirst")
    out = book.render()
    assert "Lessons from failed attempts" in out
    assert "1." in out and "ring fails" in out


async def test_distill_makes_one_cheap_call_and_adds():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, turns=10)
    fake = FakeRuntime(scripts=[[{"text": "`ring` fails: goal not in a CommRing"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    book = LessonBook()
    lesson = await distill(mrt, "check_proof error: ring failed", "iterative", book)
    assert lesson is not None and "CommRing" in lesson.text
    assert len(book) == 1
    assert fake.calls[0]["tool_names"] == []          # no tools on the cheap call
    assert budget.spent().turns == 1                  # exactly one turn spent


async def test_distill_budget_refusal_returns_none():
    budget = StrategyBudget(wall_clock_s=600.0, turns=0)
    fake = FakeRuntime(scripts=[[{"text": "never"}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    book = LessonBook()
    assert await distill(mrt, "context", "x", book) is None
    assert len(book) == 0 and fake.calls == []


async def test_distill_truncates_to_max_chars():
    budget = StrategyBudget(wall_clock_s=600.0, tokens=10_000, turns=10)
    fake = FakeRuntime(scripts=[[{"text": "x" * 1000}]])
    mrt = MeteredRuntime(fake, budget, cfg())
    book = LessonBook()
    lesson = await distill(mrt, "ctx", "x", book, max_chars=100)
    assert lesson is not None and len(lesson.text) == 100


def test_strategy_prompts_registered():
    for name in ("distill_v1", "propose_tactics_v1", "sketch_plan_v1",
                 "sketch_skeleton_v1", "subgoal_v1"):
        assert "{" in get_prompt(name)                # a real template came back
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_lessons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.lessons'`

- [ ] **Step 5: Write the implementation**

```python
# src/hardy/strategy/lessons.py
"""Distilled failure context (M7 spec lessons.py): after a failed
attempt/branch/subgoal, one cheap summarization call produces a distilled
lesson appended to the run's lesson list; strategies inject current
lessons into subsequent agent runs INSTEAD of replaying transcripts.
Capped count with drop-oldest; every lesson is recorded in the trajectory
(kind="lesson") even after it rotates out of the injected book."""

import time
from collections import deque

from pydantic import BaseModel

from hardy.agent.runtime import TrajectoryEvent
from hardy.prompts import get_prompt
from hardy.strategy.metered import MeteredRuntime
from hardy.tools.registry import ToolRegistry


class Lesson(BaseModel):
    text: str
    origin: str


class LessonBook:
    def __init__(self, cap: int = 20):
        self._lessons: deque[Lesson] = deque(maxlen=cap)
        self.events: list[TrajectoryEvent] = []

    def add(self, text: str, origin: str) -> None:
        self._lessons.append(Lesson(text=text, origin=origin))
        self.events.append(
            TrajectoryEvent(kind="lesson", at=time.monotonic(), text=text)
        )

    def render(self) -> str:
        if not self._lessons:
            return ""
        lines = ["Lessons from failed attempts (do not repeat these mistakes):"]
        for i, lesson in enumerate(self._lessons, 1):
            lines.append(f"{i}. {lesson.text}")
        return "\n".join(lines) + "\n"

    def __len__(self) -> int:
        return len(self._lessons)


async def distill(
    mrt: MeteredRuntime,
    failure_context: str,
    origin: str,
    book: LessonBook,
    *,
    max_chars: int = 300,
) -> Lesson | None:
    prompt = get_prompt("distill_v1").format(failure_context=failure_context)
    trajectory = await mrt.run(
        "Distill this failure into one lesson.", prompt, ToolRegistry([]), turns=1
    )
    if trajectory is None or not trajectory.final_text.strip():
        return None
    text = trajectory.final_text.strip()[:max_chars]
    book.add(text, origin=origin)
    return Lesson(text=text, origin=origin)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_lessons.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/strategy/lessons.py src/hardy/prompts/ tests/test_lessons.py
git commit -m "feat: lesson distillation with capped drop-oldest book + strategy prompts"
```

---
### Task 5: Cheap closers (`closers.py`) + the `StubSession` test helper

**Files:**
- Create: `src/hardy/strategy/closers.py`
- Create: `tests/stub_session.py`
- Test: `tests/test_closers.py`

**Interfaces:**
- Consumes: `MeteredSession` (Task 3 — duck-typed: anything with `check`/`tactic` returning `CheckOutcome | None` / `TacticOutcome | None`), `ProveGoal` (Task 2).
- Produces:
  - `closer_sequence(enable_duper: bool = False) -> tuple[str, ...]` — `("simp", "omega", "aesop", "exact?")`, plus `"duper"` appended only when the flag is on (external dependency, never default).
  - `async try_closers_state(session, proof_state: int, *, per_tactic_timeout: float = 10.0, enable_duper: bool = False) -> str | None` — tries the sequence against a proof state via `session.tactic`; returns the **closing tactic** on the first attempt that succeeds with zero remaining goals, `None` when all fail or the budget refuses a command (`session.tactic` returned `None`). Short-circuits: later closers are never tried after a success.
  - `async try_closers_goal(session, goal: ProveGoal, *, per_tactic_timeout: float = 15.0, enable_duper: bool = False) -> str | None` — tries `goal.splice(f"by {tactic}")` via `session.check`; returns the **full winning source** on the first complete verdict, else `None`.
  - Zero model tokens by construction: neither function takes a runtime — there is nothing to spend tokens with. Lean CPU/wall spend is metered by the `MeteredSession` they run through.
  - `tests/stub_session.py: StubSession` — duck-typed scripted session (no pool, no subprocess) used by strategy unit tests: `StubSession(tactics: dict[str, TacticOutcome] | None = None, checks: list[CheckOutcome] | None = None)`; unknown tactics fail with an error; `checks` pop in order (empty list ⇒ every check completes); records `tactic_calls: list[tuple[str, int]]` and `check_calls: list[str]`; `StubLease(session)` / `stub_factory(session)` provide the `SessionFactory` shape.

- [ ] **Step 1: Write the stub helper**

```python
# tests/stub_session.py
"""Duck-typed scripted stand-in for ProofSession in strategy unit tests:
deterministic, no pool, no subprocess. MeteredSession wraps it exactly
like a real ProofSession (it only calls check/tactic/goal/known_states/
states_lost). tactic outcomes are keyed by tactic text; check outcomes
pop from a list (empty list = every check completes cleanly)."""

from hardy.lean.feedback import ProofVerdict
from hardy.lean.session import CheckOutcome, TacticOutcome


class StubSession:
    def __init__(
        self,
        *,
        tactics: dict[str, TacticOutcome] | None = None,
        checks: list[CheckOutcome] | None = None,
        goals: dict[int, str] | None = None,
    ):
        self.tactics = dict(tactics or {})
        self.checks = list(checks or [])
        self._goals = dict(goals or {})
        self.tactic_calls: list[tuple[str, int]] = []
        self.check_calls: list[str] = []
        self.states_lost = False

    async def check(self, code: str, timeout: float | None = None) -> CheckOutcome:
        self.check_calls.append(code)
        if self.checks:
            return self.checks.pop(0)
        return CheckOutcome(verdict=ProofVerdict(complete=True), env=1)

    async def tactic(
        self, tactic: str, proof_state: int, timeout: float | None = None
    ) -> TacticOutcome:
        self.tactic_calls.append((tactic, proof_state))
        out = self.tactics.get(tactic)
        if out is None:
            return TacticOutcome(ok=False, error=f"tactic '{tactic}' failed")
        if out.ok and out.proof_state is not None:
            self._goals[out.proof_state] = "\n".join(out.goals)
        return out

    def goal(self, proof_state: int) -> str | None:
        return self._goals.get(proof_state)

    def known_states(self) -> list[int]:
        return sorted(self._goals)

    def adopt_state(self, proof_state: int, goal: str) -> None:
        self._goals[proof_state] = goal


class StubLease:
    def __init__(self, session: StubSession):
        self._session = session

    async def __aenter__(self) -> StubSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def stub_factory(session: StubSession):
    return lambda: StubLease(session)
```

(Verify `ProofVerdict(complete=...)` construction against the landed `hardy.lean.feedback` — M0 code, should hold.)

- [ ] **Step 2: Write the failing closers tests**

```python
# tests/test_closers.py
from hardy.lean.feedback import ProofVerdict
from hardy.lean.session import CheckOutcome, TacticOutcome
from hardy.strategy.base import ProveGoal
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.closers import closer_sequence, try_closers_goal, try_closers_state
from hardy.strategy.metered import MeteredSession
from tests.stub_session import StubSession

GOAL = ProveGoal(name="t", statement="theorem t : True")


def closes(state: int = 5) -> TacticOutcome:
    return TacticOutcome(ok=True, proof_state=state, goals=[])


def progresses(state: int = 6) -> TacticOutcome:
    return TacticOutcome(ok=True, proof_state=state, goals=["⊢ leftover"])


def metered(stub: StubSession) -> MeteredSession:
    return MeteredSession(stub, StrategyBudget(wall_clock_s=600.0))


def test_sequence_fixed_and_duper_gated():
    assert closer_sequence() == ("simp", "omega", "aesop", "exact?")
    assert closer_sequence(enable_duper=True) == (
        "simp", "omega", "aesop", "exact?", "duper",
    )


async def test_state_short_circuits_on_first_closer():
    stub = StubSession(tactics={"omega": closes()})
    result = await try_closers_state(metered(stub), 0)
    assert result == "omega"
    tried = [t for t, _ in stub.tactic_calls]
    assert tried == ["simp", "omega"]              # aesop/exact? never tried


async def test_state_progress_without_closure_is_not_success():
    stub = StubSession(tactics={"simp": progresses()})
    assert await try_closers_state(metered(stub), 0) is None
    tried = [t for t, _ in stub.tactic_calls]
    assert tried == ["simp", "omega", "aesop", "exact?"]


async def test_state_budget_refusal_stops_early():
    budget = StrategyBudget(wall_clock_s=600.0, lean_cpu_s=0.0)  # refuses all
    stub = StubSession(tactics={"simp": closes()})
    session = MeteredSession(stub, budget)
    assert await try_closers_state(session, 0) is None
    assert stub.tactic_calls == []                 # nothing was ever sent


async def test_goal_returns_full_winning_source():
    incomplete = CheckOutcome(
        verdict=ProofVerdict(complete=False), env=None
    )
    complete = CheckOutcome(verdict=ProofVerdict(complete=True), env=3)
    stub = StubSession(checks=[incomplete, complete])
    result = await try_closers_goal(metered(stub), GOAL)
    assert result == "theorem t : True := by omega"
    assert stub.check_calls == [
        "theorem t : True := by simp",
        "theorem t : True := by omega",
    ]


async def test_goal_all_fail_returns_none():
    incomplete = CheckOutcome(verdict=ProofVerdict(complete=False), env=None)
    stub = StubSession(checks=[incomplete] * 4)
    assert await try_closers_goal(metered(stub), GOAL) is None
    assert len(stub.check_calls) == 4
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_closers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.closers'`

- [ ] **Step 4: Write the implementation**

```python
# src/hardy/strategy/closers.py
"""Hybrid automation (M7 spec closers.py): a fixed sequence of cheap
kernel-side closers tried before any model tokens are spent. Zero model
tokens by construction — no runtime is ever passed here. Used three ways:
the workflow's global pre-pass (config flag, default on — but disabled in
every strategy-comparison arm, where the pre-pass IS the hybrid
strategy's implementation); sketch, on every subgoal before agent turns;
best-first, as free frontier expansion. duper stays behind a config flag
(external dependency)."""

from hardy.strategy.base import ProveGoal

_BASE_SEQUENCE = ("simp", "omega", "aesop", "exact?")


def closer_sequence(enable_duper: bool = False) -> tuple[str, ...]:
    return _BASE_SEQUENCE + ("duper",) if enable_duper else _BASE_SEQUENCE


async def try_closers_state(
    session,
    proof_state: int,
    *,
    per_tactic_timeout: float = 10.0,
    enable_duper: bool = False,
) -> str | None:
    """Try the closer sequence against a proof state. Returns the closing
    tactic on the first attempt that leaves zero goals; None when all
    fail or the budget refuses a command."""
    for tactic in closer_sequence(enable_duper):
        result = await session.tactic(
            tactic, proof_state, timeout=per_tactic_timeout
        )
        if result is None:
            return None  # reservation failed: budget gone, stop trying
        if result.ok and not result.goals:
            return tactic
    return None


async def try_closers_goal(
    session,
    goal: ProveGoal,
    *,
    per_tactic_timeout: float = 15.0,
    enable_duper: bool = False,
) -> str | None:
    """Try `by <closer>` as a complete proof of the goal. Returns the full
    winning source on the first complete kernel verdict; None otherwise."""
    for tactic in closer_sequence(enable_duper):
        source = goal.splice(f"by {tactic}")
        outcome = await session.check(source, timeout=per_tactic_timeout)
        if outcome is None:
            return None
        if outcome.verdict.complete:
            return source
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_closers.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/strategy/closers.py tests/stub_session.py tests/test_closers.py
git commit -m "feat: cheap-closer sequence (state and whole-goal) + scripted stub session"
```

---

### Task 6: The baseline (`iterative.py`) — M1's loop behind the interface, + the `closers` hybrid strategy

**Files:**
- Create: `src/hardy/strategy/iterative.py`
- Test: `tests/test_iterative.py`

**Interfaces:**
- Consumes: everything above — `Strategy` seam types (Task 2), `MeteredRuntime`/`MeteredSession` (Task 3), `LessonBook`/`distill` (Task 4), `try_closers_goal` (Task 5); from M1: `make_prove_registry(session, statement, attempts, wins)`, `FrozenStatement`, `get_prompt("prove_v1")`.
- Produces:
  - `IterativeStrategy(params: dict)` — `name = "iterative"`. Params: `turns_per_round: int | None = None` (None ⇒ each round asks for all remaining turns — one round, exactly M1's single prove phase run when budgets match), `lesson_cap: int = 20`, `prompt_suffix: str = ""` (parallel-branch diversity hook), `cpu_per_command_s: float = 60.0`.
  - `HybridClosersStrategy(params)` — `name = "closers"`: `try_closers_goal` first (zero model tokens); on success, validate and return; on failure, delegate the remaining budget to an inner `IterativeStrategy(params)`. This is the spec's hybrid-automation candidate competing **as a strategy**.
  - Both self-register: `register_strategy("iterative", ...)`, `register_strategy("closers", ...)` at module import.
  - `_registry_for(metered, frozen, attempts, wins) -> ToolRegistry` — module-level helper wrapping M1's `make_prove_registry` over the `MeteredSession`; it post-wraps each `ToolDef.handler` so a `None` from a metered call renders as the tool error `"budget exhausted — stop calling tools and summarize what remains"` (M1 handlers never see `None` from a raw `ProofSession`).

**Behavior contract:**
1. Loop: agent run (prove registry, `prove_v1` prompt + `prompt_suffix` + current lessons) → if `wins` non-empty, `validate(wins[-1][0], session, events-so-far)`; verdict passed → return proved (flags carried on the verdict, never blocking — M2 classifies them as warnings). Verdict failed → distill a lesson from the verdict detail and loop. No win → distill from the trajectory's final text/errors and loop.
2. Every loop iteration checks `budget.exhausted()`; a `None` from `MeteredRuntime.run` or `BudgetExpired` ends the loop; the strategy **returns** (never raises) an unproved `StrategyResult` with `budget_spent=budget.spent()` and all accumulated events (runtime events + lesson events).
3. The whole strategy runs inside one `session_factory()` lease (`async with`), so cancellation always returns the worker (spec's lease contract).
4. `StrategyResult.events` = `mrt.events + book.events`; `detail` records `{"rounds": n, "attempts": len(attempts)}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_iterative.py
import sys

from hardy.agent.runtime import RunConfig
from hardy.lean.pool import ReplPool
from hardy.strategy.base import ProveGoal, Verdict, create_strategy
from hardy.strategy.budget import StrategyBudget
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
GOAL = ProveGoal(name="t", statement="theorem t : True")


def cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=99, wall_clock_s=600.0,
                     prompt_version="prove_v1")


async def run_strategy(name, params, runtime, budget, validate):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        strategy = create_strategy(name, params)
        return await strategy.prove(
            GOAL, session_factory=pool.lease, runtime=runtime,
            config=cfg(), budget=budget, validate=validate,
        )
    finally:
        await pool.close()


def passing_validate():
    async def validate(source, session, events):
        return Verdict(passed=True)
    return validate


async def test_happy_path_proves_via_validator():
    fake = FakeRuntime(scripts=[
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "done"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_strategy("iterative", {}, fake, budget, passing_validate())
    assert result.proved
    assert result.source == "theorem t : True := trivial"
    assert result.verdict is not None and result.verdict.passed
    assert result.budget_spent.turns >= 1
    assert any(e.kind == "tool_call" for e in result.events)


async def test_validation_failure_distills_lesson_and_retries():
    fake = FakeRuntime(scripts=[
        # round 1: a win that the validator rejects
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "attempt 1"}],
        # distillation call
        [{"text": "the axiom audit rejected the proof"}],
        # round 2: wins again, validator accepts now
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "attempt 2"}],
    ])
    verdicts = [Verdict(passed=False, detail="audit: bad axiom"),
                Verdict(passed=True)]

    async def validate(source, session, events):
        return verdicts.pop(0)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_strategy(
        "iterative", {"turns_per_round": 5}, fake, budget, validate,
    )
    assert result.proved
    # the second prove round saw the distilled lesson in its prompt
    second_round = fake.calls[2]
    assert "audit rejected" in second_round["system_prompt"]
    assert any(e.kind == "lesson" for e in result.events)


async def test_budget_out_returns_honest_unproved():
    fake = FakeRuntime(scripts=[[{"text": "no tool calls, no win"}]])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=1)
    result = await run_strategy("iterative", {"turns_per_round": 1}, fake,
                                budget, passing_validate())
    assert not result.proved
    assert result.source is None
    assert result.budget_spent.turns >= 1
    assert result.detail["rounds"] == 1


async def test_zero_budget_never_calls_the_model():
    fake = FakeRuntime(scripts=[[{"text": "never"}]])
    budget = StrategyBudget(wall_clock_s=600.0, turns=0)
    result = await run_strategy("iterative", {}, fake, budget, passing_validate())
    assert not result.proved
    assert fake.calls == []


async def test_prompt_suffix_injected():
    fake = FakeRuntime(scripts=[
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "done"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, turns=50)
    await run_strategy("iterative", {"prompt_suffix": "Prefer induction."},
                       fake, budget, passing_validate())
    assert "Prefer induction." in fake.calls[0]["system_prompt"]


async def test_hybrid_closers_wins_without_model_tokens():
    # fake repl: every check completes, so the first closer splice wins
    fake = FakeRuntime(scripts=[[{"text": "never needed"}]])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_strategy("closers", {}, fake, budget, passing_validate())
    assert result.proved
    assert result.source == "theorem t : True := by simp"
    assert budget.spent().tokens == 0                 # zero model tokens
    assert fake.calls == []
    assert result.detail["closed_by_prepass"] is True


async def test_hybrid_falls_back_to_iterative(monkeypatch):
    # force the closer pre-pass to fail, then the inner iterative must run
    import hardy.strategy.iterative as it_mod

    async def no_closers(session, goal, **kw):
        return None

    monkeypatch.setattr(it_mod, "try_closers_goal", no_closers)
    fake = FakeRuntime(scripts=[
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "done"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_strategy("closers", {}, fake, budget, passing_validate())
    assert result.proved
    assert result.detail["closed_by_prepass"] is False
    assert len(fake.calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_iterative.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.iterative'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/strategy/iterative.py
"""M1's iterative-repair loop, extracted behind the Strategy interface —
the baseline every comparison measures against — plus the hybrid
cheap-closers strategy (closers first, then iterative on what remains).

The loop is M1's prove phase generalized: one agent run per round against
the prove registry; a kernel win goes to the harness validator (the sole
success authority); a rejected win or a winless round becomes a distilled
lesson injected into the next round's prompt — never a replayed
transcript."""

import functools

from hardy.prompts import get_prompt
from hardy.strategy.base import (
    ProveGoal,
    SessionFactory,
    Strategy,
    StrategyResult,
    ValidateFn,
    register_strategy,
)
from hardy.strategy.budget import BudgetExpired, StrategyBudget
from hardy.strategy.closers import try_closers_goal
from hardy.strategy.lessons import LessonBook, distill
from hardy.strategy.metered import MeteredRuntime, MeteredSession
from hardy.tools.lean_tools import make_prove_registry
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult
from hardy.tools.statement import FrozenStatement

_BUDGET_MSG = "budget exhausted — stop calling tools and summarize what remains"


def _registry_for(metered, frozen, attempts, wins) -> ToolRegistry:
    """M1's prove registry over a MeteredSession, with None-from-budget
    rendered as an actionable tool error (M1 handlers never see None)."""
    inner = make_prove_registry(metered, frozen, attempts, wins)
    wrapped = ToolRegistry([])
    for tool in inner:
        async def handler(args, _tool=tool):
            try:
                return await _tool.handler(args)
            except (TypeError, AttributeError) as exc:
                # a metered None propagated into an M1 handler
                if "NoneType" in str(exc):
                    return ToolResult(content=_BUDGET_MSG, is_error=True)
                raise
        wrapped.add(ToolDef(
            name=tool.name, description=tool.description,
            input_model=tool.input_model, handler=handler,
        ))
    return wrapped


class IterativeStrategy:
    name = "iterative"

    def __init__(self, params: dict):
        self._turns_per_round: int | None = params.get("turns_per_round")
        self._lesson_cap: int = params.get("lesson_cap", 20)
        self._prompt_suffix: str = params.get("prompt_suffix", "")
        self._cpu_per_command: float = params.get("cpu_per_command_s", 60.0)

    async def prove(
        self,
        goal: ProveGoal,
        *,
        session_factory: SessionFactory,
        runtime,
        config,
        budget: StrategyBudget,
        validate: ValidateFn,
    ) -> StrategyResult:
        frozen = FrozenStatement(name=goal.name, header=goal.full_statement())
        book = LessonBook(cap=self._lesson_cap)
        mrt = MeteredRuntime(runtime, budget, config)
        rounds = 0
        attempts: list[str] = []
        async with session_factory() as session:
            metered = MeteredSession(
                session, budget, cpu_per_command_s=self._cpu_per_command
            )
            try:
                while budget.exhausted() is None:
                    wins: list[tuple[str, int]] = []
                    registry = _registry_for(metered, frozen, attempts, wins)
                    remaining_turns = budget.remaining().turns
                    ask = self._turns_per_round or remaining_turns or 40
                    prompt = (
                        get_prompt("prove_v1").format(statement=frozen.header)
                        + ("\n" + self._prompt_suffix if self._prompt_suffix else "")
                        + "\n" + book.render()
                    )
                    trajectory = await mrt.run(
                        f"Prove: {frozen.header}", prompt, registry, turns=ask
                    )
                    if trajectory is None:
                        break
                    rounds += 1
                    if wins:
                        source, _env = wins[-1]
                        verdict = await budget.run_with_deadline(
                            validate(source, session, mrt.events)
                        )
                        if verdict.passed:
                            return StrategyResult(
                                proved=True, source=source, verdict=verdict,
                                budget_spent=budget.spent(),
                                events=mrt.events + book.events,
                                detail={"rounds": rounds, "attempts": len(attempts)},
                            )
                        await distill(
                            mrt, f"validation rejected the proof: {verdict.detail}",
                            "iterative", book,
                        )
                    else:
                        await distill(
                            mrt,
                            "the attempt ended without a kernel-complete proof; "
                            f"final assistant note: {trajectory.final_text[:500]}",
                            "iterative", book,
                        )
            except BudgetExpired:
                pass  # deadline hit mid-call: fall through to the honest result
        return StrategyResult(
            proved=False, budget_spent=budget.spent(),
            events=mrt.events + book.events,
            detail={"rounds": rounds, "attempts": len(attempts)},
        )


class HybridClosersStrategy:
    """The cheap-closer pre-pass competing as a strategy (M7 spec: in
    comparisons the global pre-pass is disabled in every arm, and this
    candidate IS the pre-pass, falling back to iterative)."""

    name = "closers"

    def __init__(self, params: dict):
        self._params = params
        self._enable_duper: bool = params.get("enable_duper", False)
        self._inner = IterativeStrategy(params)

    async def prove(
        self, goal, *, session_factory, runtime, config, budget, validate
    ) -> StrategyResult:
        async with session_factory() as session:
            metered = MeteredSession(session, budget)
            try:
                source = await try_closers_goal(
                    metered, goal, enable_duper=self._enable_duper
                )
                if source is not None:
                    verdict = await budget.run_with_deadline(
                        validate(source, session, [])
                    )
                    if verdict.passed:
                        return StrategyResult(
                            proved=True, source=source, verdict=verdict,
                            budget_spent=budget.spent(),
                            detail={"closed_by_prepass": True},
                        )
            except BudgetExpired:
                return StrategyResult(
                    proved=False, budget_spent=budget.spent(),
                    detail={"closed_by_prepass": False},
                )
        result = await self._inner.prove(
            goal, session_factory=session_factory, runtime=runtime,
            config=config, budget=budget, validate=validate,
        )
        result.detail["closed_by_prepass"] = False
        return result


register_strategy("iterative", lambda params: IterativeStrategy(params))
register_strategy("closers", lambda params: HybridClosersStrategy(params))
```

**Implementation note on `_registry_for`:** the clean long-term fix is for M1's handlers to accept an optional "budget guard" — but M7 must not change M1 tool semantics for non-strategy callers. If at execution time the landed `make_prove_registry` handlers turn out to guard `None` themselves (or the wrapper's exception-sniffing proves brittle in review), replace the wrapper with a `MeteredSession` mode that returns a synthetic `CheckOutcome(verdict=failure_verdict("timeout"))`-style outcome carrying `_BUDGET_MSG` — the test `test_zero_budget_never_calls_the_model` pins the observable behavior either way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_iterative.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/strategy/iterative.py tests/test_iterative.py
git commit -m "feat: iterative-repair baseline + hybrid closers strategy behind the seam"
```

---

### Task 7: Prove workflow rewiring — phase 3 becomes `strategy.prove(...)`

**Files:**
- Modify: `src/hardy/workflows/prove.py` (phase 3, config, validator, pre-pass)
- Modify: `src/hardy/workflows/persist.py` (`Manifest` gains `strategy`, `strategy_params`, `strategy_flags` — additive, defaulted)
- Modify: `tests/test_prove.py` (the `cfg()` helper gains `closer_prepass=False`; **no other test edits** — every M1 assertion must keep passing)
- Test: `tests/test_prove_strategy.py`

**Interfaces:**
- Consumes: `create_strategy` (Task 2 registry, populated by Task 6), `StrategyBudget` (Task 1), `try_closers_goal`/`MeteredSession` (Tasks 3/5), `scan_suspicious`/`ProveGoal`/`Verdict` (Task 2); M1's `prove()` internals (meter, phases, publish), `audit_axioms`.
- Produces:
  - `ProveConfig` gains: `strategy: str = "iterative"`, `strategy_params: dict = {}`, `closer_prepass: bool = True`, `lean_cpu_s: float | None = None` (per-theorem Lean CPU budget — advertised on the strategy meter only when set).
  - `make_prove_validator(goal: ProveGoal) -> ValidateFn` (module-level in `prove.py`) — the harness-owned downstream validator: (1) **authoritative re-check**: `session.check(source)`; not complete → failed verdict; (2) `audit_axioms(session, goal.name, env)`; failed → failed verdict with the audit reason; (3) `scan_suspicious(source, events)` → flags on a passing verdict. Every strategy's "final `check_proof` is the sole success authority" runs through this.
  - Phase 3 becomes: build `ProveGoal` from the frozen statement → optional closer pre-pass (`closer_prepass` and **not** benchmark-comparison mode — the comparison harness always passes `closer_prepass=False`) → `create_strategy(config.strategy, config.strategy_params)` → `strategy.prove(goal, session_factory=pool.lease, runtime=runtime, config=phase_run_config, budget=strategy_budget, validate=make_prove_validator(goal))` → settle the spend back into the M1 `BudgetMeter` via a synthetic `Trajectory`; `result.events` are appended to the run's trajectory record.
  - `ProveResult` unchanged; `formalization_status` = `"verified"` when the strategy proved and its verdict passed (flags recorded in `manifest.strategy_flags`, never demoting — M2's flags-are-warnings discipline; the *audit* failing inside the validator means the verdict never passes, so the M1 "partially formalized" demotion path is now "strategy returned unproved with audit detail", recorded in `manifest.audit`... **no**: keep M1's exact grading semantics — see behavior contract 4).

**Behavior contract (each clause carries a test):**
1. Default config runs `IterativeStrategy` and the happy path publishes identically to M1 (same files, same manifest keys plus the new strategy fields).
2. `closer_prepass=True` and a goal the closers solve: zero agent scripts consumed for the prove phase; outcome `proved`; manifest records `"strategy": "prepass:closers"` for the pre-pass win.
3. Unknown `config.strategy` fails fast with the known-names `KeyError` **before** any phase runs (config validation at entry).
4. Grading: strategy proved + verdict passed → `formalization_status="verified"`; strategy returned a source but unproved (e.g. audit rejected every candidate) → `"partially formalized"` with the last verdict detail in `manifest.audit`; no source → `"not formalized"`. Honest-labeling invariant: a not-proved result still ships the compile-checked `.tex` (M1 phase 5 unchanged).
5. The strategy's spend is settled into the run meter: after the prove phase, `meter.spent_turns`/`spent_tokens` include `result.budget_spent`; the writeup phase sees only the remainder.
6. `manifest.strategy` = `config.strategy`, `manifest.strategy_params` = `config.strategy_params`, `manifest.strategy_flags` = verdict flags (tracking-entry visibility for the config axis).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prove_strategy.py
import sys
from pathlib import Path

import pytest

from hardy.latex.compile import CompileResult
from hardy.lean.pool import ReplPool
from hardy.workflows import prove as prove_mod
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
CLAIM = "the square root of 2 is irrational"
STMT = "theorem sqrt2_irr : True"


@pytest.fixture
def ok_compile(monkeypatch):
    def fake_compile(source: str, staging: Path) -> CompileResult:
        return CompileResult(success=True, pdf_path=staging / "main.pdf")
    monkeypatch.setattr(prove_mod, "_compile_fn_local", lambda: fake_compile)
    return fake_compile


def cfg(**kw) -> ProveConfig:
    defaults = dict(model="m", max_turns=100, wall_clock_s=600.0,
                    sandbox_tex=False, closer_prepass=False)
    defaults.update(kw)
    return ProveConfig(**defaults)


async def run_prove(runtime, tmp_path, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        return await prove(CLAIM, pool=pool, runtime=runtime, config=cfg(**kw),
                           results_dir=tmp_path, run_id="r1")
    finally:
        await pool.close()


def pre_phases():
    return [
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "proposed"}],
        [{"text": "VERDICT: faithful"}],
    ]


def writeup():
    return [[{"tool": "write_latex",
              "arguments": {"title": "T", "informal_proof": "P."}},
             {"text": "w"}]]


async def test_default_strategy_happy_path_matches_m1(tmp_path, ok_compile):
    scripts = pre_phases() + [
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
    ] + writeup()
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "proved"
    assert result.formalization_status == "verified"
    manifest = (result.published_path / "manifest.json").read_text()
    assert '"strategy"' in manifest and "iterative" in manifest


async def test_prepass_closes_without_agent_prove_phase(tmp_path, ok_compile):
    # fake repl completes every check, so the first closer splice wins
    scripts = pre_phases() + writeup()          # NO prove-phase script at all
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path, closer_prepass=True)
    assert result.outcome == "proved"
    manifest = (result.published_path / "manifest.json").read_text()
    assert "prepass:closers" in manifest
    assert len(fake.calls) == 3                 # formalize + skeptic + writeup


async def test_unknown_strategy_fails_fast(tmp_path, ok_compile):
    fake = FakeRuntime(scripts=[])
    with pytest.raises(KeyError, match="nope"):
        await run_prove(fake, tmp_path, strategy="nope")
    assert fake.calls == []                     # no phase ran


async def test_unproved_with_source_is_partially_formalized(
    tmp_path, ok_compile, monkeypatch
):
    from hardy.workflows.audit import AuditResult

    async def failing_audit(session, name, env):
        return AuditResult(passed=False, reason="non-standard axioms: ['evil']")

    monkeypatch.setattr(prove_mod, "audit_axioms", failing_audit)
    scripts = pre_phases() + [
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "attempt"}],
    ] + writeup()
    fake = FakeRuntime(scripts=scripts)
    # small turn budget so the strategy cannot loop past the rejected round
    result = await run_prove(fake, tmp_path, max_turns=8)
    assert result.outcome == "not_proved"
    assert result.formalization_status == "partially formalized"
    manifest = (result.published_path / "manifest.json").read_text()
    assert "evil" in manifest


async def test_strategy_spend_settles_into_run_meter(tmp_path, ok_compile):
    scripts = pre_phases() + [
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
    ] + writeup()
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    manifest = (result.published_path / "manifest.json").read_text()
    import json
    budgets = json.loads(manifest)["budgets"]
    assert budgets["turns"] >= 4                # all four phases settled


async def test_strategy_flags_recorded_not_demoting(tmp_path, ok_compile):
    scripts = pre_phases() + [
        [{"tool": "check_proof", "arguments": {"proof": "by native_decide"}},
         {"text": "proved"}],
    ] + writeup()
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path)
    assert result.outcome == "proved"
    assert result.formalization_status == "verified"     # flag warns, never demotes
    manifest = (result.published_path / "manifest.json").read_text()
    assert "native_decide" in manifest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prove_strategy.py -v`
Expected: FAIL — `TypeError: ProveConfig() got unexpected keyword 'closer_prepass'` (or equivalent)

- [ ] **Step 3: Modify `persist.py`** — add to `Manifest` (defaulted, so M1 call sites are untouched):

```python
    strategy: str | None = None
    strategy_params: dict = {}
    strategy_flags: list[str] = []
```

- [ ] **Step 4: Rewrite `prove.py` phase 3**

Add to `ProveConfig`:

```python
    strategy: str = "iterative"
    strategy_params: dict = {}
    closer_prepass: bool = True
    lean_cpu_s: float | None = None
```

Add the validator and the new phase (replacing M1's `--- Phase 3: prove ---` and `--- Phase 4: audit ---` blocks; imports at top: `from hardy.strategy.base import ProveGoal, Verdict, create_strategy, scan_suspicious`, `from hardy.strategy.budget import StrategyBudget`, `from hardy.strategy.closers import try_closers_goal`, `from hardy.strategy.metered import MeteredSession`, `from hardy.agent.runtime import Trajectory`, and `import hardy.strategy  # noqa: F401  (self-registration)`):

```python
def make_prove_validator(goal: ProveGoal):
    """The harness-owned downstream validator injected into the strategy
    seam: authoritative final check_proof + fail-closed axiom audit +
    suspicious-closer scan over source AND the producing branch's tactic
    trajectory. Every strategy's success runs through this."""

    async def validate(source, session, events) -> Verdict:
        outcome = await session.check(source)
        if outcome.env is None or not outcome.verdict.complete:
            return Verdict(passed=False, detail="final check_proof incomplete")
        audit = await audit_axioms(session, goal.name, outcome.env)
        if not audit.passed:
            return Verdict(passed=False, detail=f"axiom audit: {audit.reason}")
        return Verdict(passed=True, flags=scan_suspicious(source, events))

    return validate


def _spend_as_trajectory(spent) -> Trajectory:
    """Settle a strategy's meter spend back into the run-level BudgetMeter."""
    return Trajectory(
        events=[], turns=spent.turns, tokens_used=spent.tokens,
        wall_clock_s=spent.wall_clock_s, final_text="", stopped="completed",
    )
```

Inside `prove()` — validate the strategy name **first** (before any phase):

```python
    strategy = create_strategy(config.strategy, config.strategy_params)
```

then replace phases 3–4 with:

```python
        # --- Phase 3: strategy-pluggable proof search --------------------
        formalization_status = "not formalized"
        strategy_flags: list[str] = []
        strategy_name_used = config.strategy
        winning_source: str | None = None
        winning_detail: str | None = None
        if outcome is None:
            goal = ProveGoal(name=box.frozen.name, statement=box.frozen.header)
            phase_cfg = meter.phase_config(_base_run_config(config, "prove"))
            if phase_cfg is None:
                outcome = "budget_exhausted"
            else:
                sbudget = StrategyBudget(
                    wall_clock_s=phase_cfg.wall_clock_s,
                    tokens=phase_cfg.max_tokens_total,
                    turns=phase_cfg.max_turns,
                    lean_cpu_s=config.lean_cpu_s,
                )
                validate = make_prove_validator(goal)
                if config.closer_prepass:
                    metered = MeteredSession(session, sbudget)
                    prepass_source = await try_closers_goal(metered, goal)
                    if prepass_source is not None:
                        verdict = await validate(prepass_source, session, [])
                        if verdict.passed:
                            winning_source = prepass_source
                            strategy_flags = verdict.flags
                            strategy_name_used = "prepass:closers"
                            formalization_status = "verified"
                if winning_source is None:
                    result = await strategy.prove(
                        goal,
                        session_factory=pool.lease,
                        runtime=runtime,
                        config=phase_cfg,
                        budget=sbudget,
                        validate=validate,
                    )
                    trajectories.append(Trajectory(
                        events=result.events, turns=0, tokens_used=0,
                        wall_clock_s=0.0, final_text="",
                        stopped="completed",
                    ))
                    if result.proved and result.verdict is not None:
                        winning_source = result.source
                        strategy_flags = result.verdict.flags
                        formalization_status = "verified"
                    elif result.source is not None:
                        formalization_status = "partially formalized"
                        winning_detail = (
                            result.verdict.detail if result.verdict else None
                        )
                meter.settle(_spend_as_trajectory(sbudget.spent()))
```

and downstream, adapt M1's persistence glue: `wins`-based decisions become `winning_source`-based (`files[f"{slug}.lean"] = winning_source` when `outcome == "proved"`; `outcome = "proved" if winning_source and formalization_status == "verified" else ...` mirrors M1's `wins` logic); `audit_record` becomes `{"detail": winning_detail}` when a partially-formalized candidate was rejected; `Manifest(...)` gains `strategy=strategy_name_used, strategy_params=config.strategy_params, strategy_flags=strategy_flags`. Keep the pre-pass **inside** the existing `async with pool.lease() as session:` block (the session already held for formalization); the strategy itself takes fresh leases via `session_factory` — with a size-1 pool the workflow must **release its lease before** `strategy.prove(...)` runs, so restructure: close the formalize-phase `async with` block before phase 3, and open a short-lived lease for the pre-pass/validator instead. (M1's single-lease layout was an implementation convenience, not a contract; test `test_default_strategy_happy_path_matches_m1` over the size-1 fake pool pins that the restructure deadlock-frees phase 3.)

- [ ] **Step 5: Update `tests/test_prove.py`**

In the `cfg()` helper only, add `closer_prepass=False` to `defaults` (the fake REPL completes every check, so the default-on pre-pass would otherwise solve every goal before the scripted prove phase and break M1's script accounting). Every assertion stays untouched.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_prove_strategy.py tests/test_prove.py -v`
Expected: all PASS (M1's suite green with the one-line helper change)

- [ ] **Step 7: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/hardy/workflows/prove.py src/hardy/workflows/persist.py tests/test_prove.py tests/test_prove_strategy.py
git commit -m "feat: strategy-pluggable prove phase with harness validator and closer pre-pass"
```

---
### Task 8: Proof-state pickling, part 1 — wire commands + `ProofSession` extensions

**Files:**
- Modify: `src/hardy/lean/pool.py` (`WorkerSpec.container_name: str | None = None` — additive field)
- Modify: `src/hardy/lean/launch.py` (`sandboxed_worker_spec` records the minted `name` on the spec)
- Modify: `src/hardy/lean/session.py` (add `send_raw`, `replay`, `adopt_state`, `base_env`, `container_name`)
- Create: `src/hardy/lean/pickle.py` (wire wrappers only in this task)
- Modify: `tests/fake_repl.py` (pickle magic — extensions only)
- Test: `tests/test_pickle.py` (plus `tests/test_pool.py` and `tests/test_session.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `LeanRepl.send(request, timeout) -> dict` (M0, implemented), `ProofSession` internals (M1: `_worker`, `_states`, `_worker_died`, `_ensure_worker`, `command_in`), `WorkerSpec` (M0).
- Produces:
  - `WorkerSpec.container_name: str | None` — `None` for direct workers; the sandbox container's name otherwise (the trusted side-channels already address it; now it's recorded).
  - `ProofSession.send_raw(request: dict, timeout: float | None = None) -> dict | None` — raw framed request through the leased worker; `None` on worker death (death handling identical to `command_in`: `_worker_died()` runs, states clear). Increments `commands_run` (recycling budgets see pickling work).
  - `ProofSession.base_env: int | None` (property; `None` when no worker held).
  - `ProofSession.replay(commands: list[str], timeout: float | None = None) -> int | None` — runs the commands in order, **env-chained** (`command_in(cmd, env=prev_env)`), returns the final env id; `None` on any error message, fatal message, or worker death. Empty list returns `base_env`. This is the declaration-prefix replay primitive.
  - `ProofSession.adopt_state(proof_state: int, goal: str) -> None` — records an externally-created proof state (an unpickle result) in the session table so `tactic()`/`goal()` accept it.
  - `ProofSession.container_name: str | None` (property; from the worker's spec).
  - `hardy.lean.pickle.pickle_state(session, proof_state: int, worker_path: str, timeout: float = 120.0) -> bool` — sends `{"pickleTo": worker_path, "proofState": proof_state}`; True iff the response carries no error/fatal message. **Wire-shape re-validation point** (plan assumption 22): confirm command names against the pinned `vendor/repl` before implementing; only this function and `unpickle_state` change if they differ.
  - `hardy.lean.pickle.unpickle_state(session, worker_path: str, env: int | None = None, timeout: float = 120.0) -> tuple[int, list[str]] | None` — sends `{"unpickleProofStateFrom": worker_path}` (+ `"env"` when given); on success returns `(proof_state, goals)` **and** calls `session.adopt_state(...)`; `None` on failure.

- [ ] **Step 1: Extend the fake REPL**

In `tests/fake_repl.py`, add two branches to `main()`'s dispatch, between the `"tactic"` branch and the final `else` (host process, so worker paths are host paths — exactly the direct-worker case the unit tier exercises):

```python
        elif "pickleTo" in req and "proofState" in req:
            from pathlib import Path
            target = Path(req["pickleTo"])
            if "UNWRITABLE" in str(target):
                resp = {"message": "pickle failed: cannot write"}
            else:
                target.write_text(f"state:{req['proofState']}")
                resp = {"proofState": req["proofState"], "goals": []}
        elif "unpickleProofStateFrom" in req:
            from pathlib import Path
            source = Path(req["unpickleProofStateFrom"])
            if source.exists():
                resp = {"proofState": 90, "goals": ["⊢ " + source.read_text()]}
            else:
                resp = {"message": "unpickle failed: no such file"}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_pickle.py
import sys

from hardy.lean.pickle import pickle_state, unpickle_state
from hardy.lean.pool import ReplPool, WorkerSpec

FAKE = [sys.executable, "tests/fake_repl.py"]


async def make_pool() -> ReplPool:
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    return pool


def test_worker_spec_container_name_defaults_none():
    spec = WorkerSpec(argv=["x"])
    assert spec.container_name is None


def test_sandboxed_spec_records_container_name():
    from hardy.lean.launch import sandboxed_worker_spec
    spec = sandboxed_worker_spec()
    assert spec.container_name is not None
    assert spec.container_name.startswith("hardy-repl-")
    assert spec.cleanup_argv == ["docker", "kill", spec.container_name]


async def test_pickle_roundtrip_direct_worker(tmp_path):
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")   # state 0
            path = str(tmp_path / "s0.olean")
            assert await pickle_state(session, 0, path) is True
            restored = await unpickle_state(session, path)
            assert restored is not None
            state, goals = restored
            assert state == 90 and goals == ["⊢ state:0"]
            # adopted: tactic() accepts the restored id
            result = await session.tactic("anything", proof_state=90)
            assert result.ok
    finally:
        await pool.close()


async def test_pickle_failure_reported_not_raised(tmp_path):
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            assert await pickle_state(session, 0, "UNWRITABLE/x") is False
    finally:
        await pool.close()


async def test_unpickle_missing_file_returns_none(tmp_path):
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("ok")
            assert await unpickle_state(session, str(tmp_path / "nope")) is None
    finally:
        await pool.close()


async def test_replay_chains_envs():
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("ok")
            env = await session.replay(["cmd one", "cmd two"])
            assert env is not None and env >= 2       # fake increments per cmd
            assert await session.replay([]) == session.base_env
    finally:
        await pool.close()


async def test_replay_error_returns_none():
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("ok")
            assert await session.replay(["ERROR"]) is None
    finally:
        await pool.close()


async def test_send_raw_death_returns_none_and_invalidates():
    pool = await make_pool()
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            assert await session.send_raw({"cmd": "DIE"}) is None
            assert session.states_lost
    finally:
        await pool.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_pickle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.lean.pickle'`

- [ ] **Step 4: Modify `pool.py` and `launch.py`**

`pool.py` — add to `WorkerSpec`:

```python
    # The sandbox container's name (None for direct workers): the pickling
    # side-channels (docker exec/cp) address the container by it, exactly
    # like reset_argv/cleanup_argv already do.
    container_name: str | None = None
```

`launch.py` — in `sandboxed_worker_spec`, add `container_name=name` to the returned `WorkerSpec(...)` call.

- [ ] **Step 5: Extend `session.py`**

Add to `ProofSession` (after `command_in`):

```python
    @property
    def base_env(self) -> int | None:
        return None if self._worker is None else self._worker.base_env

    @property
    def container_name(self) -> str | None:
        if self._worker is None:
            return None
        return self._worker.spec.container_name

    def adopt_state(self, proof_state: int, goal: str) -> None:
        """Record an externally-created proof state (an unpickle result)
        so tactic()/goal() accept it."""
        self._states[proof_state] = goal

    async def send_raw(
        self, request: dict, timeout: float | None = None
    ) -> dict | None:
        """Raw framed request on the leased worker (the pickling wire
        path). None on worker death — callers must fail closed."""
        worker = await self._ensure_worker()
        worker.commands_run += 1
        try:
            return await worker.repl.send(request, timeout=timeout)
        except (ReplTimeout, ReplDied, LeanReplError):
            await self._worker_died()
            return None

    async def replay(
        self, commands: list[str], timeout: float | None = None
    ) -> int | None:
        """Run commands in order, env-chained from base_env; the final env
        id, or None on any error (declaration-prefix replay: a prefix that
        does not rebuild cleanly must never receive an unpickle)."""
        await self._ensure_worker()
        env = self.base_env
        for code in commands:
            resp = await self.command_in(code, env=env, timeout=timeout)
            if resp is None or resp.message is not None or resp.env is None:
                return None
            if any(m.severity == "error" for m in resp.messages):
                return None
            env = resp.env
        return env
```

- [ ] **Step 6: Write `pickle.py` (wire wrappers)**

```python
# src/hardy/lean/pickle.py
"""Proof-state pickling (M7 — the debt M1 deferred): wrappers over the
community repl's pickle commands, plus (Task 9) the harness-owned
snapshot store that moves pickles across workers.

Wire shapes assumed (re-validate against the pinned vendor/repl):
  {"pickleTo": <path>, "proofState": <id>}      -> tactic-shaped response
  {"unpickleProofStateFrom": <path>, "env"?: n} -> {"proofState": n, "goals": [...]}
A pickle restores the proof state ONLY — not declarations, instances, or
options its creating commands added — which is why Task 9's snapshots
carry a declaration prefix and replay it before unpickling."""

from hardy.lean.session import ProofSession


async def pickle_state(
    session: ProofSession,
    proof_state: int,
    worker_path: str,
    timeout: float = 120.0,
) -> bool:
    resp = await session.send_raw(
        {"pickleTo": worker_path, "proofState": proof_state}, timeout=timeout
    )
    if resp is None:
        return False
    return "message" not in resp or resp.get("message") is None


async def unpickle_state(
    session: ProofSession,
    worker_path: str,
    env: int | None = None,
    timeout: float = 120.0,
) -> tuple[int, list[str]] | None:
    request: dict = {"unpickleProofStateFrom": worker_path}
    if env is not None:
        request["env"] = env
    resp = await session.send_raw(request, timeout=timeout)
    if resp is None or resp.get("message") is not None:
        return None
    proof_state = resp.get("proofState")
    if proof_state is None:
        return None
    goals = [str(g) for g in resp.get("goals", [])]
    session.adopt_state(proof_state, "\n".join(goals))
    return proof_state, goals
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_pickle.py tests/test_pool.py tests/test_session.py -v`
Expected: all PASS (M0/M1 suites untouched and green)

- [ ] **Step 8: Commit**

```bash
git add src/hardy/lean/pickle.py src/hardy/lean/pool.py src/hardy/lean/launch.py src/hardy/lean/session.py tests/fake_repl.py tests/test_pickle.py
git commit -m "feat: proof-state pickle wire wrappers + session replay/adopt primitives"
```

---

### Task 9: Proof-state pickling, part 2 — `SnapshotStore` (harness-owned storage, migration, generation refs)

**Files:**
- Modify: `src/hardy/lean/pickle.py` (add `Snapshot`, `SnapshotStore`, `GenerationRefs`, docker copy helpers)
- Test: `tests/test_snapshots.py`

**Interfaces:**
- Consumes: Task 8's wire wrappers and session extensions; the docker side-channel precedents (M0 `launch.py`/`compile.py`).
- Produces:
  - `GenerationRefs` protocol: `add_ref(generation_id: str, ref_id: str) -> None`, `drop_ref(generation_id: str, ref_id: str) -> None`; `NullGenerationRefs` no-op default. (**Re-validation point**, plan assumption 23: wire an `M4GenerationRefs` adapter over the landed M4 GC registration API; snapshots must be *durable* references — registered before the snapshot is usable, dropped at store cleanup.)
  - `Snapshot(id: str, prefix: list[str], prefix_hash: str, generation_id: str | None, host_path: Path, size: int)` (pydantic) — `prefix` is the ordered harness-owned command list that built the state's environment beyond base imports (skeleton `have`s, helper definitions, assumed-paper imports); `prefix_hash = sha256("\n\x00".join(prefix))`.
  - `SnapshotStore(root: Path, *, max_bytes: int = 512 * 1024 * 1024, gen_refs: GenerationRefs | None = None)` with:
    - `async save(session: ProofSession, proof_state: int, *, prefix: list[str], generation_id: str | None = None) -> Snapshot | None` — pickles to the worker's scratch (`/scratch/<id>.olean` sandboxed; a store-owned host path for direct workers), **immediately copies the pickle out to harness-owned storage** (`docker exec <name> tar -cf - -C /scratch <id>.olean` streamed over stdout under a byte cap — the same tar-over-stdout pattern the TeX compiler uses; direct workers already wrote the host path). Registers the generation ref **before returning** the snapshot. `None` on any failure or when the store's size cap would be exceeded (the frontier prunes, never crashes).
    - `async restore(session: ProofSession, snapshot: Snapshot) -> tuple[int, list[str]] | None` — verifies `sha256` of `snapshot.prefix` against `snapshot.prefix_hash` (a corrupted record must never replay), **replays the prefix** on the destination lease (`session.replay`) and only then stages the pickle in (**trusted host-side copy**: `docker cp <host_path> <name>:/scratch/<id>.olean` — mount-based staging is impossible, warm containers pre-exist the per-run store) and `unpickle_state`s from it in the replayed env. `None` on hash mismatch, replay failure, copy failure, or unpickle failure — callers prune the node and log the loss.
    - `cleanup() -> None` — deletes the store directory and drops every registered generation ref (end-of-run cleanup).
    - `used_bytes: int` property.
  - Module-level (monkeypatch seams for unit tests; real subprocess in the `lean`/`docker` tiers): `async _docker_stream_out(container: str, worker_path: str, dest: Path, cap_bytes: int) -> bool` and `async _docker_copy_in(container: str, src: Path, worker_path: str) -> bool` — both run the docker CLI via `asyncio.create_subprocess_exec` with `_docker_client_env()`-style scrubbed env, bounded timeout, and never raise.
- Direct workers (`container_name is None`): `save` passes the store path itself as `worker_path` (the worker is a host process); `restore` unpickles straight from `host_path`. This is the unit-tier path and the real path for direct-launch pools.
- **Intentional mid-run generation upgrade restarts the frontier explicitly** (spec): the store exposes no re-pinning; a caller that changes generations builds a new store.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_snapshots.py
import sys

import pytest

import hardy.lean.pickle as pickle_mod
from hardy.lean.pickle import Snapshot, SnapshotStore
from hardy.lean.pool import ReplPool

FAKE = [sys.executable, "tests/fake_repl.py"]


async def make_pool(size: int = 1) -> ReplPool:
    pool = ReplPool(size=size, argv=FAKE, imports="import Fake")
    await pool.start()
    return pool


class RecordingRefs:
    def __init__(self):
        self.added: list[tuple[str, str]] = []
        self.dropped: list[tuple[str, str]] = []

    def add_ref(self, generation_id, ref_id):
        self.added.append((generation_id, ref_id))

    def drop_ref(self, generation_id, ref_id):
        self.dropped.append((generation_id, ref_id))


async def test_save_and_restore_direct_worker(tmp_path):
    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=[])
            assert snap is not None
            assert snap.host_path.exists() and snap.size > 0
            restored = await store.restore(session, snap)
            assert restored is not None
            state, goals = restored
            assert state == 90                       # fake's unpickle id
            assert (await session.tactic("t", proof_state=state)).ok
    finally:
        await pool.close()
        store.cleanup()


async def test_restore_replays_prefix_before_unpickle(tmp_path):
    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=["def helper := 1"])
            assert snap is not None
            # kill the worker: states gone, replacement has no helper
            await session.check("DIE")
            assert session.states_lost
            restored = await store.restore(session, snap)
            assert restored is not None
    finally:
        await pool.close()
        store.cleanup()


async def test_restore_refuses_hash_mismatch(tmp_path):
    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=["def helper := 1"])
            tampered = Snapshot(**{**snap.model_dump(),
                                   "prefix": ["def evil := 2"]})
            assert await store.restore(session, tampered) is None
    finally:
        await pool.close()
        store.cleanup()


async def test_restore_fails_when_prefix_replay_errors(tmp_path):
    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=["ERROR"])
            # save records the prefix verbatim; replay of ERROR fails
            assert await store.restore(session, snap) is None
    finally:
        await pool.close()
        store.cleanup()


async def test_size_cap_prunes_new_snapshots(tmp_path):
    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps", max_bytes=1)   # absurdly small
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            assert await store.save(session, 0, prefix=[]) is None
    finally:
        await pool.close()
        store.cleanup()


async def test_generation_refs_registered_and_dropped(tmp_path):
    pool = await make_pool()
    refs = RecordingRefs()
    store = SnapshotStore(tmp_path / "snaps", gen_refs=refs)
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=[], generation_id="gen-7")
            assert snap is not None
            assert refs.added == [("gen-7", snap.id)]
    finally:
        await pool.close()
    store.cleanup()
    assert refs.dropped == [("gen-7", snap.id)]


async def test_sandboxed_paths_use_docker_side_channels(tmp_path, monkeypatch):
    """Sandboxed workers stream the pickle out via tar-over-stdout and
    stage it back in via docker cp — verified against monkeypatched
    side-channel seams (the real CLI is the docker-tier's job)."""
    calls = []

    async def fake_stream_out(container, worker_path, dest, cap_bytes):
        calls.append(("out", container, worker_path))
        dest.write_bytes(b"pickled")
        return True

    async def fake_copy_in(container, src, worker_path):
        calls.append(("in", container, worker_path))
        return True

    monkeypatch.setattr(pickle_mod, "_docker_stream_out", fake_stream_out)
    monkeypatch.setattr(pickle_mod, "_docker_copy_in", fake_copy_in)

    pool = await make_pool()
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as session:
            # pretend the (fake, host-process) worker is sandboxed
            session._worker.spec = session._worker.spec.model_copy(
                update={"container_name": "hardy-repl-test"}
            )
            await session.check("theorem t : True := by sorry")
            snap = await store.save(session, 0, prefix=[])
            assert snap is not None
            assert snap.host_path.read_bytes() == b"pickled"
            await store.restore(session, snap)
            kinds = [c[0] for c in calls]
            assert kinds == ["out", "in"]
            assert all(c[1] == "hardy-repl-test" for c in calls)
            # in-container path lives under the between-check-wiped /scratch
            assert all(c[2].startswith("/scratch/") for c in calls)
    finally:
        await pool.close()
        store.cleanup()
```

**Note on the sandboxed-path test:** the fake worker is a host process, so its in-container `pickleTo` path (`/scratch/...`) is what the *fake* writes to the host filesystem root — which fails. The monkeypatched seams intercept the transfer, but `pickle_state` still runs against the fake. The fake's `UNWRITABLE` guard covers explicit failure; for this test the fake's pickle branch must tolerate an unwritable `/scratch` path by treating a leading `/scratch/` as `tmp`-relative. **Add to the fake's pickle branch** (before `target.write_text`):

```python
            if str(target).startswith("/scratch/"):
                import tempfile
                target = Path(tempfile.gettempdir()) / target.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_snapshots.py -v`
Expected: FAIL — `ImportError: cannot import name 'SnapshotStore'`

- [ ] **Step 3: Extend `pickle.py`**

Append to `src/hardy/lean/pickle.py`:

```python
import asyncio
import hashlib
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

_PICKLE_CAP_BYTES = 64 * 1024 * 1024   # one snapshot's tar stream cap
_DOCKER_TIMEOUT_S = 60.0


class GenerationRefs(Protocol):
    def add_ref(self, generation_id: str, ref_id: str) -> None: ...
    def drop_ref(self, generation_id: str, ref_id: str) -> None: ...


class NullGenerationRefs:
    def add_ref(self, generation_id: str, ref_id: str) -> None: ...
    def drop_ref(self, generation_id: str, ref_id: str) -> None: ...


def prefix_hash(prefix: list[str]) -> str:
    return hashlib.sha256("\n\x00".join(prefix).encode()).hexdigest()


class Snapshot(BaseModel):
    id: str
    prefix: list[str]
    prefix_hash: str
    generation_id: str | None = None
    host_path: Path
    size: int


async def _run_docker(argv: list[str], timeout: float) -> tuple[int | None, bytes]:
    """Run a trusted docker CLI command; (returncode, stdout). Never raises."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    for var in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
                "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "HOME"):
        if var in os.environ:
            env[var] = os.environ[var]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None, b""
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None, b""
    return proc.returncode, stdout


async def _docker_stream_out(
    container: str, worker_path: str, dest: Path, cap_bytes: int
) -> bool:
    """Copy a completed pickle out of the container's /scratch to
    harness-owned storage — tar-over-stdout, the same trusted pattern the
    TeX compiler uses for artifacts. Size-capped; any other tar member is
    ignored."""
    directory, name = worker_path.rsplit("/", 1)
    code, stdout = await _run_docker(
        ["docker", "exec", container, "tar", "-cf", "-", "-C", directory, name],
        _DOCKER_TIMEOUT_S,
    )
    if code != 0 or not stdout or len(stdout) > cap_bytes:
        return False
    import io
    try:
        with tarfile.open(fileobj=io.BytesIO(stdout)) as tar:
            for member in tar.getmembers():
                if member.name == name and member.isreg():
                    handle = tar.extractfile(member)
                    if handle is not None:
                        dest.write_bytes(handle.read())
                        return True
    except tarfile.TarError:
        return False
    return False


async def _docker_copy_in(container: str, src: Path, worker_path: str) -> bool:
    """Trusted host-side copy into the worker's EXISTING /scratch (docker
    cp against the named container — the same side-channel mechanism
    reset_argv uses). Mounts are not an option: warm containers pre-exist
    the per-run store, and Docker cannot add a mount to a running
    container. The copy lands in scratch, which the between-check wipe
    already manages."""
    code, _ = await _run_docker(
        ["docker", "cp", str(src), f"{container}:{worker_path}"],
        _DOCKER_TIMEOUT_S,
    )
    return code == 0


class SnapshotStore:
    """Per-run, host-owned pickle storage. A pickle left in a container's
    /scratch dies with the worker (and is wiped between checks) — gone
    exactly when recovery needs it — so every completed pickle is copied
    out here immediately, and staged back into a destination worker's
    scratch on restore. Size-capped; cleaned at end of run. Snapshots
    holding a generation_id register as durable generation references
    (a worker lease alone is not durable — the worker can die after the
    snapshot is stored)."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 512 * 1024 * 1024,
        gen_refs: GenerationRefs | None = None,
    ):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._refs = gen_refs or NullGenerationRefs()
        self._registered: list[tuple[str, str]] = []
        self.used_bytes = 0

    async def save(
        self,
        session,
        proof_state: int,
        *,
        prefix: list[str],
        generation_id: str | None = None,
    ) -> Snapshot | None:
        snap_id = uuid.uuid4().hex[:12]
        container = session.container_name
        if container is None:
            # Direct worker: it is a host process — pickle straight into
            # the store, no transfer needed.
            host_path = self._root / f"{snap_id}.olean"
            if not await pickle_state(session, proof_state, str(host_path)):
                return None
        else:
            worker_path = f"/scratch/{snap_id}.olean"
            if not await pickle_state(session, proof_state, worker_path):
                return None
            host_path = self._root / f"{snap_id}.olean"
            if not await _docker_stream_out(
                container, worker_path, host_path, _PICKLE_CAP_BYTES
            ):
                return None
        try:
            size = host_path.stat().st_size
        except OSError:
            return None
        if self.used_bytes + size > self._max_bytes:
            host_path.unlink(missing_ok=True)
            return None
        self.used_bytes += size
        snapshot = Snapshot(
            id=snap_id, prefix=list(prefix), prefix_hash=prefix_hash(prefix),
            generation_id=generation_id, host_path=host_path, size=size,
        )
        if generation_id is not None:
            self._refs.add_ref(generation_id, snap_id)
            self._registered.append((generation_id, snap_id))
        return snapshot

    async def restore(self, session, snapshot: Snapshot) -> tuple[int, list[str]] | None:
        # 1. Verify the recorded prefix before anything replays: a
        #    corrupted/tampered record must never rebuild an environment.
        if prefix_hash(snapshot.prefix) != snapshot.prefix_hash:
            return None
        # 2. Replay the declaration prefix on the destination lease FIRST —
        #    a pickle restores the proof state only, and nodes referencing
        #    skeleton helpers would fail or silently misbehave on a
        #    pristine worker.
        env = await session.replay(snapshot.prefix)
        if env is None:
            return None
        # 3. Stage the pickle into the destination worker and unpickle in
        #    the replayed env.
        container = session.container_name
        if container is None:
            worker_path = str(snapshot.host_path)
        else:
            worker_path = f"/scratch/{snapshot.id}.olean"
            if not await _docker_copy_in(container, snapshot.host_path, worker_path):
                return None
        return await unpickle_state(session, worker_path, env=env)

    def cleanup(self) -> None:
        for generation_id, ref_id in self._registered:
            self._refs.drop_ref(generation_id, ref_id)
        self._registered.clear()
        shutil.rmtree(self._root, ignore_errors=True)
        self.used_bytes = 0
```

Note: `replay([])` returns `base_env`, so a prefix-free snapshot unpickles in the pristine base — no special case. **Generation-pinned leases** (a destination lease resolving an *older* `Papers.*` generation): out of `SnapshotStore`'s hands by design — the caller (best-first, Task 10) requests its leases; when M4's generation-specific leases land, the caller passes a generation-pinned `session_factory`. The store's contract is only: verify, replay, stage, unpickle, and keep the generation reference alive.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_snapshots.py tests/test_pickle.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/lean/pickle.py tests/fake_repl.py tests/test_snapshots.py
git commit -m "feat: snapshot store — prefix-verified pickle migration across workers"
```

---
### Task 10: Best-first tactic search (`bestfirst.py`)

**Files:**
- Create: `src/hardy/strategy/bestfirst.py`
- Test: `tests/test_bestfirst.py`

**Interfaces:**
- Consumes: seam types (Task 2), `MeteredRuntime`/`MeteredSession` (Task 3), `LessonBook` (Task 4), `try_closers_state` (Task 5), `SnapshotStore`/`Snapshot` (Task 9), `get_prompt("propose_tactics_v1")` (Task 4).
- Produces:
  - `SearchNode(proof_state: int, goals: list[str], score: float, depth: int, tactic_path: list[str], epoch: int, snapshot: Snapshot | None = None, failed: list[str] = [])` (pydantic; `failed` = node lessons — tactics that failed on this state, injected into re-proposals).
  - `parse_proposals(text: str, k: int) -> list[tuple[float, str]]` — parses `SCORE TACTIC` lines; unparsable lines degrade to score 0.5 with the whole line as the tactic; scores clamped to [0, 1]; at most `k`.
  - `BestFirstStrategy(params)` — `name = "bestfirst"`. Params: `k: int = 4`, `depth_penalty: float = 0.1`, `max_depth: int = 32`, `per_tactic_timeout: float = 15.0`, `snapshots: bool = True`, `snapshot_dir: str | None = None` (default: a fresh temp dir, cleaned in `finally`), `lesson_cap: int = 20`, `requeue_decay: float = 0.5`.
  - Self-registers: `register_strategy("bestfirst", ...)`.

**Behavior contract:**
1. Root: `check(goal.splice("by sorry"))`; the first sorry's `proof_state`/`goal` seed the frontier (score 1.0, depth 0, empty path). No sorry back (or budget refusal) → unproved immediately.
2. Expansion of the popped best node: **closers first, free** (`try_closers_state`); a closer that closes yields a terminal path with zero model tokens. Otherwise **one model call proposes k tactics** with self-assigned scores (`propose_tactics_v1`, `turns=1`, no tools); each proposal is applied via `tactic()`: successes become children (`score = model_score − depth_penalty × child_depth + 0.001` when the child's total goal text shrank — the goal-size tie-break), failures append to the node's `failed` list. A node with at least one failure and depth < `max_depth` is **re-queued** at `score × requeue_decay` so re-proposals see (and are told not to repeat) its `failed` list.
3. Children are snapshotted (`snapshots=True`) with `prefix=[goal.preamble]` when a preamble exists, else `[]` — the statement itself lives in the spliced source, but preamble imports/options must survive worker migration.
4. Worker death (`session.states_lost` observed after any call) bumps the strategy's epoch; a popped node from an older epoch is **restored** from its snapshot on the (fresh) leased worker via `SnapshotStore.restore` — success rewrites its `proof_state`/`epoch`; failure (or no snapshot) **prunes the node with the loss logged** (an event + `detail["pruned"]` count).
5. Terminal (a path reaching zero goals): assemble `goal.splice("by\n  " + "\n  ".join(path))` and hand it to `validate` — **the final check is the sole success authority; search bookkeeping is never trusted**. A rejected terminal becomes a lesson and the search continues.
6. Budget out (`exhausted()`, a `None` from any metered call, or `BudgetExpired`): return unproved with `detail["best_path"]` (the deepest/best tactic path seen) and `detail["frontier_size"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bestfirst.py
from hardy.agent.runtime import RunConfig
from hardy.lean.feedback import ProofVerdict
from hardy.lean.messages import Pos, Sorry
from hardy.lean.session import CheckOutcome, TacticOutcome
from hardy.strategy.base import ProveGoal, Verdict, create_strategy
from hardy.strategy.bestfirst import parse_proposals
from hardy.strategy.budget import StrategyBudget
from tests.fake_runtime import FakeRuntime
from tests.stub_session import StubSession, stub_factory

GOAL = ProveGoal(name="t", statement="theorem t : True")


def cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=99, wall_clock_s=600.0,
                     prompt_version="prove_v1")


def sorried_check(state: int = 0, goal: str = "⊢ True") -> CheckOutcome:
    return CheckOutcome(
        verdict=ProofVerdict(
            complete=False,
            sorries=[Sorry(pos=Pos(line=1, column=0), goal=goal,
                           proof_state=state)],
        ),
        env=1,
    )


def closes(state: int) -> TacticOutcome:
    return TacticOutcome(ok=True, proof_state=state, goals=[])


def progresses(state: int, goals: list[str]) -> TacticOutcome:
    return TacticOutcome(ok=True, proof_state=state, goals=goals)


def passing_validate():
    async def validate(source, session, events):
        return Verdict(passed=True)
    return validate


async def run_bestfirst(stub, fake, budget, validate, params=None):
    strategy = create_strategy("bestfirst", params or {"snapshots": False})
    return await strategy.prove(
        GOAL, session_factory=stub_factory(stub), runtime=fake,
        config=cfg(), budget=budget, validate=validate,
    )


def test_parse_proposals_well_formed_and_degraded():
    text = "0.9 simp [Nat.add_comm]\nnot-a-score induction n\n1.7 omega\n"
    parsed = parse_proposals(text, k=4)
    assert parsed[0] == (0.9, "simp [Nat.add_comm]")
    assert parsed[1] == (0.5, "not-a-score induction n")   # degraded line
    assert parsed[2] == (1.0, "omega")                     # clamped
    assert len(parse_proposals(text, k=2)) == 2


async def test_closer_expansion_wins_with_zero_model_tokens():
    stub = StubSession(checks=[sorried_check()], tactics={"omega": closes(5)})
    fake = FakeRuntime(scripts=[])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_bestfirst(stub, fake, budget, passing_validate())
    assert result.proved
    assert result.source == "theorem t : True := by\n  omega"
    assert budget.spent().tokens == 0
    assert fake.calls == []


async def test_failed_proposals_requeue_with_node_lessons():
    stub = StubSession(checks=[sorried_check()], tactics={"norm_num": closes(7)})
    fake = FakeRuntime(scripts=[
        [{"text": "0.9 ring"}],          # round 1: ring fails on the stub
        [{"text": "0.8 norm_num"}],      # round 2: must see ring in `failed`
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_bestfirst(stub, fake, budget, passing_validate(),
                                 params={"snapshots": False, "k": 1})
    assert result.proved
    assert result.source == "theorem t : True := by\n  norm_num"
    assert "ring" in fake.calls[1]["system_prompt"]        # node lesson injected


async def test_multi_step_path_assembles_in_order():
    stub = StubSession(
        checks=[sorried_check(0, "⊢ a ∧ b")],
        tactics={"constructor": progresses(1, ["⊢ a", "⊢ b"]),
                 "trivial": closes(2)},
    )
    fake = FakeRuntime(scripts=[[{"text": "0.9 constructor"}]])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    # closers fail everywhere except: after constructor, the closer pass
    # tries the sequence on state 1 — give 'trivial' via aesop instead:
    stub.tactics["aesop"] = closes(2)
    result = await run_bestfirst(stub, fake, budget, passing_validate())
    assert result.proved
    assert result.source == "theorem t : True := by\n  constructor\n  aesop"


async def test_rejected_terminal_is_never_shipped():
    stub = StubSession(checks=[sorried_check()], tactics={"omega": closes(5)})
    fake = FakeRuntime(scripts=[])

    async def rejecting_validate(source, session, events):
        return Verdict(passed=False, detail="audit rejected")

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_bestfirst(stub, fake, budget, rejecting_validate)
    assert not result.proved                       # bookkeeping never trusted
    assert result.detail["best_path"] == ["omega"]


async def test_budget_out_records_partial():
    stub = StubSession(checks=[sorried_check()])   # nothing closes
    fake = FakeRuntime(scripts=[])                 # no proposal scripts either
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=0)
    result = await run_bestfirst(stub, fake, budget, passing_validate())
    assert not result.proved
    assert "best_path" in result.detail
    assert result.budget_spent.tokens == 0


async def test_stale_node_without_snapshot_is_pruned():
    stub = StubSession(checks=[sorried_check()])
    fake = FakeRuntime(scripts=[[{"text": "0.9 ring"}]])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)

    # ring "fails" AND flips states_lost, simulating a worker death
    async def dying_tactic(tactic, proof_state, timeout=None):
        stub.states_lost = True
        return TacticOutcome(ok=False, error="worker crash")

    stub.tactic = dying_tactic
    result = await run_bestfirst(stub, fake, budget, passing_validate(),
                                 params={"snapshots": False, "k": 1})
    assert not result.proved
    assert result.detail["pruned"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bestfirst.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.bestfirst'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/strategy/bestfirst.py
"""Best-first tactic search (M7 spec bestfirst.py): a frontier of proof
states in a priority queue; expansion is closers-first (free), then one
model call proposing k scored tactics. Failed tactics become node lessons
so re-proposals don't repeat them. The frontier outlives worker recycling
via SnapshotStore (proof-state ids are per-process — the M1 limitation
this milestone pays down); a node whose worker died restores on a fresh
lease, and unrestorable nodes are pruned with the loss logged. A terminal
path is NEVER trusted: the harness validator's final check_proof is the
sole success authority."""

import heapq
import itertools
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel

from hardy.agent.runtime import TrajectoryEvent
from hardy.lean.pickle import Snapshot, SnapshotStore
from hardy.prompts import get_prompt
from hardy.strategy.base import (
    ProveGoal,
    StrategyResult,
    register_strategy,
)
from hardy.strategy.budget import BudgetExpired, StrategyBudget
from hardy.strategy.closers import try_closers_state
from hardy.strategy.lessons import LessonBook
from hardy.strategy.metered import MeteredRuntime, MeteredSession
from hardy.tools.registry import ToolRegistry


class SearchNode(BaseModel):
    proof_state: int
    goals: list[str]
    score: float
    depth: int
    tactic_path: list[str]
    epoch: int
    snapshot: Snapshot | None = None
    failed: list[str] = []


def parse_proposals(text: str, k: int) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        try:
            score = float(parts[0])
            tactic = parts[1].strip() if len(parts) > 1 else ""
        except ValueError:
            score, tactic = 0.5, line
        if tactic:
            out.append((min(max(score, 0.0), 1.0), tactic))
        if len(out) == k:
            break
    return out


class BestFirstStrategy:
    name = "bestfirst"

    def __init__(self, params: dict):
        self._k: int = params.get("k", 4)
        self._depth_penalty: float = params.get("depth_penalty", 0.1)
        self._max_depth: int = params.get("max_depth", 32)
        self._per_tactic_timeout: float = params.get("per_tactic_timeout", 15.0)
        self._snapshots: bool = params.get("snapshots", True)
        self._snapshot_dir: str | None = params.get("snapshot_dir")
        self._lesson_cap: int = params.get("lesson_cap", 20)
        self._requeue_decay: float = params.get("requeue_decay", 0.5)

    async def prove(
        self, goal: ProveGoal, *, session_factory, runtime, config, budget,
        validate,
    ) -> StrategyResult:
        book = LessonBook(cap=self._lesson_cap)
        mrt = MeteredRuntime(runtime, budget, config)
        extra_events: list[TrajectoryEvent] = []
        store: SnapshotStore | None = None
        if self._snapshots:
            root_dir = Path(self._snapshot_dir or tempfile.mkdtemp(prefix="hardy-bfs-"))
            store = SnapshotStore(root_dir)
        prefix = [goal.preamble] if goal.preamble else []
        counter = itertools.count()
        frontier: list[tuple[float, int, SearchNode]] = []
        epoch = 0
        pruned = 0
        best_path: list[str] = []
        try:
            async with session_factory() as session:
                metered = MeteredSession(session, budget)

                def note_death() -> None:
                    nonlocal epoch
                    if session.states_lost:
                        epoch += 1

                def push(node: SearchNode) -> None:
                    heapq.heappush(frontier, (-node.score, next(counter), node))

                async def snapshot_of(state: int) -> Snapshot | None:
                    if store is None:
                        return None
                    return await store.save(session, state, prefix=prefix)

                # -- root ---------------------------------------------------
                out = await metered.check(goal.splice("by sorry"))
                if out is None or not out.verdict.sorries:
                    return self._unproved(budget, mrt, book, extra_events,
                                          best_path, pruned, len(frontier))
                root_sorry = out.verdict.sorries[0]
                push(SearchNode(
                    proof_state=root_sorry.proof_state, goals=[root_sorry.goal],
                    score=1.0, depth=0, tactic_path=[], epoch=epoch,
                    snapshot=await snapshot_of(root_sorry.proof_state),
                ))

                while frontier and budget.exhausted() is None:
                    _, _, node = heapq.heappop(frontier)
                    if len(node.tactic_path) > len(best_path):
                        best_path = list(node.tactic_path)
                    # -- stale node: restore or prune ----------------------
                    if node.epoch != epoch:
                        restored = (
                            None if node.snapshot is None
                            else await store.restore(session, node.snapshot)
                        )
                        if restored is None:
                            pruned += 1
                            extra_events.append(TrajectoryEvent(
                                kind="lesson", at=time.monotonic(),
                                text=f"bestfirst: pruned node at depth "
                                     f"{node.depth} (state lost, restore failed)",
                            ))
                            continue
                        node.proof_state, node.goals = restored[0], restored[1]
                        node.epoch = epoch
                    # -- closers: free expansion ---------------------------
                    closer = await try_closers_state(
                        metered, node.proof_state,
                        per_tactic_timeout=self._per_tactic_timeout,
                    )
                    note_death()
                    if closer is not None:
                        path = node.tactic_path + [closer]
                        won = await self._try_terminal(
                            goal, path, session, mrt, budget, validate, book)
                        if won is not None:
                            return won
                        best_path = max(best_path, path, key=len)
                        continue
                    if node.epoch != epoch:
                        push(node)          # death during closers: retry later
                        continue
                    if node.depth >= self._max_depth:
                        continue
                    # -- one model call proposes k tactics -----------------
                    prompt = get_prompt("propose_tactics_v1").format(
                        goals="\n".join(node.goals),
                        lessons=book.render(),
                        failed="\n".join(node.failed) or "(none)",
                        k=self._k,
                    )
                    trajectory = await mrt.run(
                        "Propose tactics.", prompt, ToolRegistry([]), turns=1
                    )
                    if trajectory is None:
                        break
                    had_failure = False
                    for model_score, tactic in parse_proposals(
                        trajectory.final_text, self._k
                    ):
                        result = await metered.tactic(
                            tactic, node.proof_state,
                            timeout=self._per_tactic_timeout,
                        )
                        note_death()
                        if result is None:
                            break
                        if node.epoch != epoch:
                            break           # state died mid-expansion
                        if not result.ok:
                            had_failure = True
                            if tactic not in node.failed:
                                node.failed.append(tactic)
                            continue
                        child_path = node.tactic_path + [tactic]
                        if not result.goals:
                            won = await self._try_terminal(
                                goal, child_path, session, mrt, budget,
                                validate, book)
                            if won is not None:
                                return won
                            best_path = max(best_path, child_path, key=len)
                            continue
                        shrink = 0.001 if (
                            sum(map(len, result.goals)) < sum(map(len, node.goals))
                        ) else 0.0
                        push(SearchNode(
                            proof_state=result.proof_state, goals=result.goals,
                            score=model_score
                            - self._depth_penalty * (node.depth + 1) + shrink,
                            depth=node.depth + 1, tactic_path=child_path,
                            epoch=epoch,
                            snapshot=await snapshot_of(result.proof_state),
                        ))
                    if had_failure and node.epoch == epoch:
                        node.score *= self._requeue_decay
                        push(node)          # re-proposals see node.failed
        except BudgetExpired:
            pass
        finally:
            if store is not None:
                store.cleanup()
        return self._unproved(budget, mrt, book, extra_events, best_path,
                              pruned, len(frontier))

    async def _try_terminal(
        self, goal, path, session, mrt, budget, validate, book
    ) -> StrategyResult | None:
        source = goal.splice("by\n  " + "\n  ".join(path))
        verdict = await budget.run_with_deadline(
            validate(source, session, mrt.events)
        )
        if verdict.passed:
            return StrategyResult(
                proved=True, source=source, verdict=verdict,
                budget_spent=budget.spent(), events=mrt.events + book.events,
                detail={"path": path},
            )
        book.add(
            f"assembled path {' ; '.join(path)} was rejected: {verdict.detail}",
            origin="bestfirst",
        )
        return None

    def _unproved(self, budget, mrt, book, extra_events, best_path, pruned,
                  frontier_size) -> StrategyResult:
        return StrategyResult(
            proved=False, budget_spent=budget.spent(),
            events=mrt.events + book.events + extra_events,
            detail={"best_path": best_path, "pruned": pruned,
                    "frontier_size": frontier_size},
        )


register_strategy("bestfirst", lambda params: BestFirstStrategy(params))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bestfirst.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/strategy/bestfirst.py tests/test_bestfirst.py
git commit -m "feat: best-first tactic search with snapshot-backed frontier recovery"
```

---

### Task 11: Sketch-and-discharge (`sketch.py`)

**Files:**
- Create: `src/hardy/strategy/sketch.py`
- Test: `tests/test_sketch.py`

**Interfaces:**
- Consumes: seam types (Task 2), `MeteredRuntime`/`MeteredSession` (Task 3), `LessonBook` (Task 4), `try_closers_state` (Task 5), prompts (Task 4), `ToolDef`/`ToolRegistry`/`ToolResult` (M1); the M6 hole ledger **through a port** (plan assumption 19).
- Produces:
  - `count_sorries(body: str) -> int` and `replace_nth_sorry(body: str, n: int, replacement: str) -> str` — lexical `sorry` token scan (boundary-checked, zero-based n; raises `IndexError` past the count).
  - `SketchLedgerPort` protocol: `open_hole(index: int, goal: str, proof_state: int | None) -> str`; `mark_patched(hole_id: str, proof: str) -> None`; `mark_verified(hole_id: str) -> None`; `reopen(hole_id: str) -> None`; `abandon(hole_id: str, reason: str) -> None`.
  - `MemorySketchLedger` — in-memory port (unit tests; also the fallback when no ledger is injected): records `opened/patched/verified/reopened/abandoned` lists.
  - `m6_ledger_port(ledger) -> SketchLedgerPort` — the M6-backed port: holes enter as `layer="kernel"`, provenance `sketch` (`open` status); `mark_patched` → `patched`, `mark_verified` → `verified-closed`, `reopen` → `open` (reopen counter increments per M6's transition table), `abandon` → `abandoned` with the reason. **Written against the landed M6 `HoleLedger` API at execution time** (the assumed shape is `ledger.create(location=..., description=..., layer="kernel", provenance="sketch") -> Hole` and `ledger.transition(hole_id, status, **meta)`); this function is the only code in M7 touching M6 names.
  - `SketchStrategy(params)` — `name = "sketch"`. Params: `subgoal_model: str | None = None` (cheaper model for subgoals — **rejected by the comparison harness** in exit-criterion runs), `parallel: int = 1` (discharge concurrency, capped by runtime capabilities — degrades to 1), `plan_turns: int = 8`, `skeleton_turns: int = 8`, `subgoal_turns: int = 15`, `max_skeleton_rounds: int = 3`, `lesson_cap: int = 20`, `ledger: SketchLedgerPort | None = None` (workflow injects the M6-backed port; `None` → `MemorySketchLedger`), `note_tool: ToolDef | None = None` (M6's note tool for plan persistence when the workflow provides it — plan assumption 20).
  - Self-registers: `register_strategy("sketch", ...)`.

**Behavior contract:**
1. **Plan:** one agent run (`sketch_plan_v1`, `note_tool` registry when provided, else empty registry); the plan is the run's `final_text`, recorded in `detail["plan"]`. Budget refusal → unproved immediately.
2. **Skeleton:** up to `max_skeleton_rounds` agent runs against a one-tool registry (`submit_skeleton(body: str)`); the handler checks `goal.splice(body)` through the `MeteredSession`: elaboration errors → actionable tool error (agent retries in-run); accepted when error-free (sorries expected — a sorry-free complete body is accepted as a zero-hole skeleton). The accepted check's sorries (goal + proof-state id each) become **planned holes through the ledger port** (`layer="kernel"`, provenance `sketch` — the M6-backed port encodes that).
3. **Discharge** — each hole independently: cheap closers first (`try_closers_state` on the hole's proof state; a closing tactic discharges as `by <tactic>` with zero model tokens), then a scoped agent run (`subgoal_v1`, seeded with the plan + the hole's goal + current lessons; `model=subgoal_model` when set) against a one-tool registry `check_subgoal(proof: str)` whose handler splices the candidate into **only this hole** and checks the full source: accepted when error-free and this hole's sorry is gone. Discharge runs **concurrently up to `min(parallel, hole_count)`** — each worker takes its own `session_factory()` lease, so real concurrency is capped by pool size — and degrades to sequential when the runtime's capabilities report `parallel_runs=False` (absent capabilities ⇒ allowed; the M5 minimal loop will report `False`). Parallelism applies to the *search only*: **patch application and ledger transitions are serialized in deterministic hole-index order** after discharge completes (M6's blast-radius logic assumes serial patches; when the M6 loop later streams re-critique through here, this applier is the integration point).
4. **Assemble & verify:** discharged bodies splice into the skeleton (index order); if every hole discharged, the assembled source goes to `validate` — the only success authority. Passed → all holes `mark_verified`, proved. Failed (non-composing, e.g. metavariable leakage) → every patched hole `reopen`s, unproved with `detail["partial_skeleton"]`. Undischarged holes after per-hole budget → unproved with the partial skeleton (remaining `sorry`s intact — *partially formalized*, honest by construction) and those holes left open in the ledger.
5. All failure paths return honest `StrategyResult`s with `budget_spent=budget.spent()`; `BudgetExpired` anywhere is caught at the top and produces the partial result.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sketch.py
from hardy.agent.runtime import RunConfig
from hardy.lean.feedback import ProofVerdict
from hardy.lean.messages import Pos, Sorry
from hardy.lean.session import CheckOutcome
from hardy.strategy.base import ProveGoal, Verdict, create_strategy
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.sketch import (
    MemorySketchLedger,
    count_sorries,
    replace_nth_sorry,
)
from tests.stub_session import StubSession, stub_factory
from tests.fake_runtime import FakeRuntime

GOAL = ProveGoal(name="t", statement="theorem t : True")
BODY = "have h1 : True := sorry\nh1"


def cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=99, wall_clock_s=600.0,
                     prompt_version="prove_v1")


def sorried(states: list[int]) -> CheckOutcome:
    return CheckOutcome(
        verdict=ProofVerdict(
            complete=False,
            sorries=[Sorry(pos=Pos(line=i + 1, column=0), goal=f"⊢ G{i}",
                           proof_state=s) for i, s in enumerate(states)],
        ),
        env=1,
    )


def complete() -> CheckOutcome:
    return CheckOutcome(verdict=ProofVerdict(complete=True), env=2)


def passing_validate():
    async def validate(source, session, events):
        return Verdict(passed=True)
    return validate


async def run_sketch(stub, fake, budget, validate, params=None):
    params = dict(params or {})
    params.setdefault("ledger", MemorySketchLedger())
    strategy = create_strategy("sketch", params)
    result = await strategy.prove(
        GOAL, session_factory=stub_factory(stub), runtime=fake,
        config=cfg(), budget=budget, validate=validate,
    )
    return result, params["ledger"]


def test_sorry_token_scan():
    assert count_sorries(BODY) == 1
    assert count_sorries("sorryAx sorry sorry'") == 1      # tokens only
    assert replace_nth_sorry("a sorry b sorry", 1, "X") == "a sorry b X"
    assert replace_nth_sorry(BODY, 0, "by simp") == (
        "have h1 : True := by simp\nh1"
    )


async def test_happy_path_closer_discharges_hole():
    from hardy.lean.session import TacticOutcome
    stub = StubSession(
        checks=[sorried([0]),      # skeleton elaboration
                sorried([0])],     # discharge worker re-elaboration
        tactics={"simp": TacticOutcome(ok=True, proof_state=9, goals=[])},
    )
    fake = FakeRuntime(scripts=[
        [{"text": "Plan: prove True directly."}],                  # plan
        [{"tool": "submit_skeleton", "arguments": {"body": BODY}},  # skeleton
         {"text": "submitted"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, ledger = await run_sketch(stub, fake, budget, passing_validate())
    assert result.proved
    assert result.source == GOAL.splice(replace_nth_sorry(BODY, 0, "by simp"))
    assert ledger.opened == ["h-000"] and ledger.verified == ["h-000"]
    assert result.detail["plan"].startswith("Plan:")


async def test_skeleton_error_retried_in_run():
    stub = StubSession(checks=[
        CheckOutcome(verdict=ProofVerdict(
            complete=False,
            errors=[__import__("hardy.lean.messages", fromlist=["Message"])
                    .Message(severity="error", pos=Pos(line=1, column=0),
                             data="unknown identifier")],
        ), env=None),
        sorried([0]),
        sorried([0]),
    ])
    from hardy.lean.session import TacticOutcome
    stub.tactics["simp"] = TacticOutcome(ok=True, proof_state=9, goals=[])
    fake = FakeRuntime(scripts=[
        [{"text": "plan"}],
        [{"tool": "submit_skeleton", "arguments": {"body": "bad sorry"}},
         {"tool": "submit_skeleton", "arguments": {"body": BODY}},
         {"text": "second try"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, _ = await run_sketch(stub, fake, budget, passing_validate())
    assert result.proved


async def test_non_composing_assembly_reopens_holes():
    from hardy.lean.session import TacticOutcome
    stub = StubSession(
        checks=[sorried([0]), sorried([0])],
        tactics={"simp": TacticOutcome(ok=True, proof_state=9, goals=[])},
    )
    fake = FakeRuntime(scripts=[
        [{"text": "plan"}],
        [{"tool": "submit_skeleton", "arguments": {"body": BODY}},
         {"text": "s"}],
    ])

    async def rejecting_validate(source, session, events):
        return Verdict(passed=False, detail="metavariable leakage")

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, ledger = await run_sketch(stub, fake, budget, rejecting_validate)
    assert not result.proved
    assert ledger.reopened == ["h-000"]
    assert "partial_skeleton" in result.detail


async def test_undischarged_hole_ships_partial_skeleton():
    stub = StubSession(checks=[sorried([0]), sorried([0]),
                               sorried([0]), sorried([0])])
    fake = FakeRuntime(scripts=[
        [{"text": "plan"}],
        [{"tool": "submit_skeleton", "arguments": {"body": BODY}},
         {"text": "s"}],
        [{"text": "cannot solve this subgoal"}],   # subgoal agent: no discharge
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, ledger = await run_sketch(stub, fake, budget, passing_validate())
    assert not result.proved
    assert "sorry" in result.detail["partial_skeleton"]
    assert ledger.verified == [] and ledger.patched == []


async def test_subgoal_model_override_forwarded():
    stub = StubSession(checks=[sorried([0]), sorried([0]),
                               complete()])       # check_subgoal splice passes
    fake = FakeRuntime(scripts=[
        [{"text": "plan"}],
        [{"tool": "submit_skeleton", "arguments": {"body": BODY}},
         {"text": "s"}],
        [{"tool": "check_subgoal", "arguments": {"proof": "by trivial"}},
         {"text": "closed"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, _ = await run_sketch(stub, fake, budget, passing_validate(),
                                 params={"subgoal_model": "cheap-model"})
    assert result.proved
    subgoal_call = fake.calls[2]
    assert subgoal_call["config"].model == "cheap-model"


async def test_two_holes_commit_in_index_order():
    from hardy.lean.session import TacticOutcome
    two = "have h1 : A := sorry\nhave h2 : B := sorry\nexact ⟨h1, h2⟩"
    stub = StubSession(
        checks=[sorried([0, 1]),           # skeleton
                sorried([0, 1]),           # worker for hole 0
                sorried([0, 1])],          # worker for hole 1
        tactics={"simp": TacticOutcome(ok=True, proof_state=9, goals=[])},
    )
    fake = FakeRuntime(scripts=[
        [{"text": "plan"}],
        [{"tool": "submit_skeleton", "arguments": {"body": two}},
         {"text": "s"}],
    ])
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result, ledger = await run_sketch(stub, fake, budget, passing_validate(),
                                      params={"parallel": 2})
    assert result.proved
    assert ledger.patched == ["h-000", "h-001"]    # deterministic index order
    assert ledger.verified == ["h-000", "h-001"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sketch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.sketch'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/strategy/sketch.py
"""Sketch-and-discharge (M7 spec sketch.py): plan -> have/sorry skeleton
-> planned holes in the M6 ledger -> independent discharge (closers
first, then a scoped agent run, optionally a cheaper model, optionally
parallel up to pool size) -> assemble and verify. The final check on the
assembled source is the only success authority; subgoal proofs that don't
compose reopen their holes. Failed subgoals after budget produce a
partially formalized result — honest by construction.

Parallelism applies to the SEARCH, not the bookkeeping: proof bodies are
found concurrently, but patch application and ledger transitions run
serially in deterministic hole-index order (M6's blast radius assumes
serial patches)."""

import asyncio
import re
from typing import Protocol

from pydantic import BaseModel

from hardy.prompts import get_prompt
from hardy.strategy.base import ProveGoal, StrategyResult, register_strategy
from hardy.strategy.budget import BudgetExpired
from hardy.strategy.closers import try_closers_state
from hardy.strategy.lessons import LessonBook
from hardy.strategy.metered import MeteredRuntime, MeteredSession
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult

_SORRY_RE = re.compile(r"(?<![A-Za-z0-9_'.])sorry(?![A-Za-z0-9_'.])")


def count_sorries(body: str) -> int:
    return len(_SORRY_RE.findall(body))


def replace_nth_sorry(body: str, n: int, replacement: str) -> str:
    matches = list(_SORRY_RE.finditer(body))
    match = matches[n]                       # IndexError past the count: a bug
    return body[: match.start()] + replacement + body[match.end():]


# -- ledger port ------------------------------------------------------------

class SketchLedgerPort(Protocol):
    def open_hole(self, index: int, goal: str, proof_state: int | None) -> str: ...
    def mark_patched(self, hole_id: str, proof: str) -> None: ...
    def mark_verified(self, hole_id: str) -> None: ...
    def reopen(self, hole_id: str) -> None: ...
    def abandon(self, hole_id: str, reason: str) -> None: ...


class MemorySketchLedger:
    def __init__(self):
        self.opened: list[str] = []
        self.patched: list[str] = []
        self.verified: list[str] = []
        self.reopened: list[str] = []
        self.abandoned: list[tuple[str, str]] = []

    def open_hole(self, index: int, goal: str, proof_state: int | None) -> str:
        hole_id = f"h-{index:03d}"
        self.opened.append(hole_id)
        return hole_id

    def mark_patched(self, hole_id: str, proof: str) -> None:
        self.patched.append(hole_id)

    def mark_verified(self, hole_id: str) -> None:
        self.verified.append(hole_id)

    def reopen(self, hole_id: str) -> None:
        self.reopened.append(hole_id)

    def abandon(self, hole_id: str, reason: str) -> None:
        self.abandoned.append((hole_id, reason))


def m6_ledger_port(ledger) -> SketchLedgerPort:
    """M6-backed port: sketch sorries are PLANNED holes (M6 spec: layer
    'kernel', provenance 'sketch'), discharged through the Repair
    machinery's statuses. Re-shape against the landed M6 HoleLedger API
    at execution time — this function is the only M7 code touching M6
    names."""

    class _Port:
        def open_hole(self, index, goal, proof_state):
            hole = ledger.create(
                location=f"sketch-sorry-{index}",
                description=f"planned subgoal {index}: {goal}",
                layer="kernel", provenance="sketch",
            )
            return hole.id

        def mark_patched(self, hole_id, proof):
            ledger.transition(hole_id, "patched", patch=proof)

        def mark_verified(self, hole_id):
            ledger.transition(hole_id, "verified-closed")

        def reopen(self, hole_id):
            ledger.transition(hole_id, "open")

        def abandon(self, hole_id, reason):
            ledger.transition(hole_id, "abandoned", reason=reason)

    return _Port()


# -- registries -------------------------------------------------------------

class _SkeletonInput(BaseModel):
    body: str


class _SubgoalInput(BaseModel):
    proof: str


class _Hole(BaseModel):
    index: int
    goal: str
    proof_state: int
    hole_id: str


class SketchStrategy:
    name = "sketch"

    def __init__(self, params: dict):
        self._subgoal_model: str | None = params.get("subgoal_model")
        self._parallel: int = params.get("parallel", 1)
        self._plan_turns: int = params.get("plan_turns", 8)
        self._skeleton_turns: int = params.get("skeleton_turns", 8)
        self._subgoal_turns: int = params.get("subgoal_turns", 15)
        self._max_skeleton_rounds: int = params.get("max_skeleton_rounds", 3)
        self._lesson_cap: int = params.get("lesson_cap", 20)
        self._ledger: SketchLedgerPort = params.get("ledger") or MemorySketchLedger()
        self._note_tool: ToolDef | None = params.get("note_tool")

    def _concurrency(self, runtime, holes: int) -> int:
        caps = getattr(runtime, "capabilities", None)
        if caps is not None and not getattr(caps, "parallel_runs", True):
            return 1        # degraded-but-functional path (minimal loop)
        return max(1, min(self._parallel, holes))

    async def prove(
        self, goal: ProveGoal, *, session_factory, runtime, config, budget,
        validate,
    ) -> StrategyResult:
        book = LessonBook(cap=self._lesson_cap)
        mrt = MeteredRuntime(runtime, budget, config)
        detail: dict = {}
        try:
            async with session_factory() as session:
                metered = MeteredSession(session, budget)

                # -- 1. plan -------------------------------------------
                plan_tools = ToolRegistry(
                    [self._note_tool] if self._note_tool else []
                )
                plan_traj = await mrt.run(
                    "Write the informal proof plan.",
                    get_prompt("sketch_plan_v1").format(
                        statement=goal.full_statement(), lessons=book.render()
                    ),
                    plan_tools, turns=self._plan_turns,
                )
                if plan_traj is None:
                    return self._unproved(budget, mrt, book, detail)
                plan = plan_traj.final_text
                detail["plan"] = plan

                # -- 2. skeleton ---------------------------------------
                accepted: dict = {}

                async def submit_skeleton(args: _SkeletonInput) -> ToolResult:
                    body = args.body
                    outcome = await metered.check(goal.splice(body))
                    if outcome is None:
                        return ToolResult(content="budget exhausted", is_error=True)
                    errors = [
                        m.data for m in outcome.verdict.errors
                    ] if outcome.verdict.errors else []
                    if errors:
                        return ToolResult(
                            content="skeleton does not elaborate:\n"
                            + "\n".join(errors[:5]),
                            is_error=True,
                        )
                    accepted["body"] = body
                    accepted["sorries"] = list(outcome.verdict.sorries)
                    return ToolResult(
                        content=f"skeleton accepted with "
                                f"{len(outcome.verdict.sorries)} subgoal(s)"
                    )

                skeleton_registry = ToolRegistry([ToolDef(
                    name="submit_skeleton",
                    description="Submit the have/sorry-decomposed proof body.",
                    input_model=_SkeletonInput, handler=submit_skeleton,
                )])
                for _ in range(self._max_skeleton_rounds):
                    traj = await mrt.run(
                        "Render the plan as a have/sorry skeleton.",
                        get_prompt("sketch_skeleton_v1").format(
                            statement=goal.full_statement(), plan=plan
                        ),
                        skeleton_registry, turns=self._skeleton_turns,
                    )
                    if traj is None or "body" in accepted:
                        break
                if "body" not in accepted:
                    return self._unproved(budget, mrt, book, detail)
                body: str = accepted["body"]
                holes = [
                    _Hole(index=i, goal=s.goal, proof_state=s.proof_state,
                          hole_id=self._ledger.open_hole(i, s.goal, s.proof_state))
                    for i, s in enumerate(accepted["sorries"])
                    if s.proof_state is not None
                ]
                detail["holes"] = len(holes)

            # -- 3. discharge (own leases; the skeleton lease is released
            #       first so a size-1 pool can serve the workers) --------
            discharged: dict[int, str] = {}
            total = count_sorries(body)
            semaphore = asyncio.Semaphore(self._concurrency(runtime, len(holes)))

            async def discharge(hole: _Hole) -> None:
                async with semaphore:
                    async with session_factory() as wsession:
                        wmetered = MeteredSession(wsession, budget)
                        out = await wmetered.check(goal.splice(body))
                        if out is None:
                            return
                        mine = [s for s in out.verdict.sorries
                                if s.proof_state is not None]
                        if hole.index >= len(mine):
                            return
                        state = mine[hole.index].proof_state
                        closer = await try_closers_state(wmetered, state)
                        if closer is not None:
                            discharged[hole.index] = f"by {closer}"
                            return

                        async def check_subgoal(args: _SubgoalInput) -> ToolResult:
                            candidate = replace_nth_sorry(
                                body, hole.index, args.proof
                            )
                            outcome = await wmetered.check(goal.splice(candidate))
                            if outcome is None:
                                return ToolResult(content="budget exhausted",
                                                  is_error=True)
                            errors = [m.data for m in outcome.verdict.errors]
                            if errors:
                                return ToolResult(
                                    content="does not close the hole:\n"
                                    + "\n".join(errors[:5]),
                                    is_error=True,
                                )
                            if count_sorries(candidate) >= total:
                                return ToolResult(
                                    content="the hole is still sorry",
                                    is_error=True,
                                )
                            discharged[hole.index] = args.proof
                            return ToolResult(content="hole closed")

                        registry = ToolRegistry([ToolDef(
                            name="check_subgoal",
                            description="Check candidate proof text for this hole.",
                            input_model=_SubgoalInput, handler=check_subgoal,
                        )])
                        await mrt.run(
                            f"Discharge subgoal {hole.index}.",
                            get_prompt("subgoal_v1").format(
                                plan=plan, goal=hole.goal, lessons=book.render()
                            ),
                            registry, turns=self._subgoal_turns,
                            model=self._subgoal_model,
                        )

            await asyncio.gather(*(discharge(h) for h in holes))

            # -- serialized applier: deterministic hole-index order -----
            final_body = body
            offset_safe = sorted(discharged)          # ascending index
            applied: list[_Hole] = []
            undischarged = [h for h in holes if h.index not in discharged]
            for index in reversed(offset_safe):        # splice back-to-front:
                # earlier replacements must not shift later sorry indices
                final_body = replace_nth_sorry(
                    final_body, index, discharged[index]
                )
            for hole in holes:                         # ledger: ascending order
                if hole.index in discharged:
                    self._ledger.mark_patched(hole.hole_id, discharged[hole.index])
                    applied.append(hole)

            # -- 4. assemble & verify -----------------------------------
            source = goal.splice(final_body)
            if undischarged:
                for hole in undischarged:
                    book.add(f"subgoal {hole.index} undischarged: {hole.goal}",
                             origin="sketch")
                detail["partial_skeleton"] = source
                return self._unproved(budget, mrt, book, detail)
            async with session_factory() as vsession:
                verdict = await budget.run_with_deadline(
                    validate(source, vsession, mrt.events)
                )
            if verdict.passed:
                for hole in applied:
                    self._ledger.mark_verified(hole.hole_id)
                return StrategyResult(
                    proved=True, source=source, verdict=verdict,
                    budget_spent=budget.spent(),
                    events=mrt.events + book.events, detail=detail,
                )
            for hole in applied:                       # non-composing: reopen
                self._ledger.reopen(hole.hole_id)
            detail["partial_skeleton"] = source
            book.add(f"assembled skeleton rejected: {verdict.detail}",
                     origin="sketch")
            return self._unproved(budget, mrt, book, detail)
        except BudgetExpired:
            return self._unproved(budget, mrt, book, detail)

    def _unproved(self, budget, mrt, book, detail) -> StrategyResult:
        return StrategyResult(
            proved=False, budget_spent=budget.spent(),
            events=mrt.events + book.events, detail=detail,
        )


register_strategy("sketch", lambda params: SketchStrategy(params))
```

**Implementation notes:**
- **Ordering subtlety pinned by `test_sorry_token_scan` + `test_two_holes_commit_in_index_order`:** splicing is done **back-to-front** (an earlier textual replacement would shift later sorry occurrence indices), while ledger transitions run in ascending hole-index order — the deterministic order the spec demands.
- The discharge worker re-elaborates the skeleton in its own session because proof-state ids are per-worker; `mine[hole.index]` assumes the repl reports sorries in positional order (it does — they come back in source order; the `lean`-tier test in Task 15 pins this against the real repl).
- The workflow (Task 7) does not yet inject an M6 ledger — `prove()` passes `strategy_params` through, and the M6 loop integration (post-M7) supplies `ledger=m6_ledger_port(...)`. The port keeps that a one-line change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sketch.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/strategy/sketch.py tests/test_sketch.py
git commit -m "feat: sketch-and-discharge with ledger-ported planned holes"
```

---
### Task 12: Diverse parallel attempts (`parallel.py`) + the `parallel_runs` capability + registry wiring

**Files:**
- Create: `src/hardy/strategy/parallel.py`
- Modify: `src/hardy/agent/capabilities.py` (M5 file: `RuntimeCapabilities` gains `parallel_runs: bool = True`; the minimal-loop adapter reports `False` — see plan assumption 16)
- Modify: `src/hardy/strategy/__init__.py` (import the five strategy modules for self-registration; re-export the seam)
- Test: `tests/test_parallel.py`, `tests/test_strategy_conformance.py`

**Interfaces:**
- Consumes: seam types (Task 2), `create_strategy` (Tasks 6/10/11 registrations), `StrategyBudget` (Task 1), `Verdict` (Task 2).
- Produces:
  - `BranchSpec(strategy: str = "iterative", params: dict = {}, prompt_suffix: str = "", temperature: float | None = None)` (pydantic).
  - `PausableRuntime(inner: AgentRuntime, gate: asyncio.Event)` — awaits `gate.wait()` before delegating each `run` (cleared gate = siblings paused: **no new calls launched** while a provisional winner validates); passes `capabilities` through via `getattr`.
  - `ParallelStrategy(params)` — `name = "parallel"`. Params: `n: int = 3` (used when `branches` absent), `branches: list[dict] | None = None` (per-branch `BranchSpec` overrides — the spec's optional strategy mix), `min_wall_to_launch_s: float = 30.0`, plus a default diversity schedule for auto-built branches: temperatures `[0.2, 0.7, 1.0, ...]` (cycled) and rotating prompt-variant suffixes.
  - `__init__.py` exports: `ProveGoal`, `Strategy`, `StrategyResult`, `StrategyBudget`, `Verdict`, `create_strategy`, `register_strategy`, `strategy_names` — and imports `iterative`, `sketch`, `bestfirst`, `parallel` (closers strategies register from `iterative`) so `import hardy.strategy` populates the registry.

**Behavior contract:**
1. Branches are independent inner strategy runs (default inner: `iterative`) sharing **nothing but the goal and the meter** — the shared `StrategyBudget` is what makes n branches at budget B cost B total, not n·B.
2. Diversity: each auto-built branch gets a distinct `prompt_suffix`; `temperature` is applied through `config.provider_params` **only when the landed `RunConfig` has that field** (M5) — otherwise recorded in `detail` and skipped (re-validation point 17).
3. First `check_proof` success in a branch = **provisional** winner: the parallel-owned wrapped validator clears the gate (siblings pause — no new model calls) and runs the full downstream validation. Only a fully validated, **unflagged** winner cancels the rest. A **rejected** candidate re-sets the gate (siblings resume) and its branch continues. A candidate that validates **with flags** re-sets the gate too and is retained as the **fallback** — returned proved (flags on its verdict) only if budget expires without an unflagged proof; a flagged win never discards an in-flight clean one.
4. Cancellation is safe by construction: workers recycle through the pool's lease discipline; a cancelled branch's in-flight model call keeps its full reservation (Task 3's `MeteredRuntime` forfeits on cancellation).
5. Degradation: capabilities report `parallel_runs=False` → branches run **sequentially** (same semantics, `detail["mode"]="sequential"`); new branches (parallel or sequential) stop launching when `remaining().wall_s < min_wall_to_launch_s` or the meter can no longer fund a minimal run (`detail["branches_launched"]` records how many started).
6. Result: clean winner > flagged fallback > unproved; `detail` records mode, branches launched, and the winning branch index.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parallel.py
import asyncio
from types import SimpleNamespace

import pytest

from hardy.agent.runtime import RunConfig
from hardy.lean.feedback import ProofVerdict
from hardy.lean.session import CheckOutcome
from hardy.strategy.base import ProveGoal, Verdict, create_strategy
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.parallel import PausableRuntime
from tests.fake_runtime import FakeRuntime
from tests.stub_session import StubSession, stub_factory

GOAL = ProveGoal(name="t", statement="theorem t : True")


def cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=99, wall_clock_s=600.0,
                     prompt_version="prove_v1")


class SequentialFake(FakeRuntime):
    """FakeRuntime with capabilities forcing the sequential degraded path
    (deterministic script consumption across branches)."""
    capabilities = SimpleNamespace(parallel_runs=False)


def win_script():
    return [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
            {"text": "won"}]


async def run_parallel(fake, budget, validate, params=None):
    strategy = create_strategy("parallel", params or {"n": 2})
    stub = StubSession()
    return await strategy.prove(
        GOAL, session_factory=stub_factory(stub), runtime=fake,
        config=cfg(), budget=budget, validate=validate,
    )


async def test_first_clean_winner_stops_remaining_branches():
    fake = SequentialFake(scripts=[win_script()])   # branch 1 never needs one

    async def validate(source, session, events):
        return Verdict(passed=True)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_parallel(fake, budget, validate)
    assert result.proved and result.verdict.flags == []
    assert result.detail["mode"] == "sequential"
    assert result.detail["winning_branch"] == 0
    assert len(fake.calls) == 1                     # branch 1 never launched


async def test_rejected_candidate_resumes_and_second_win_accepted():
    fake = SequentialFake(scripts=[
        win_script(),                                # branch 0 round 1: rejected
        [{"text": "lesson"}],                        # branch 0 distill
        win_script(),                                # branch 0 round 2: accepted
    ])
    verdicts = [Verdict(passed=False, detail="audit says no"),
                Verdict(passed=True)]

    async def validate(source, session, events):
        return verdicts.pop(0)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
    result = await run_parallel(
        fake, budget, validate,
        params={"n": 2, "branches": [{"params": {"turns_per_round": 5}},
                                     {"params": {"turns_per_round": 5}}]},
    )
    assert result.proved and result.verdict.passed


async def test_flagged_winner_retained_as_fallback_not_immediate():
    fake = SequentialFake(scripts=[
        win_script(),                # branch 0: flagged win -> fallback
        [{"text": "lesson"}],        # branch 0 distill (keeps searching)
        [{"text": "no more wins"}],  # branch 0 round 2: nothing
    ])

    async def validate(source, session, events):
        return Verdict(passed=True, flags=["native_decide"])

    # turns: 2 (round1) + 1 (distill) + 1 (round2) = 4 -> exhausted; branch 1
    # cannot launch; the flagged fallback ships, honestly flagged.
    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=4)
    result = await run_parallel(
        fake, budget, validate,
        params={"n": 2, "branches": [{"params": {"turns_per_round": 2}},
                                     {"params": {"turns_per_round": 2}}]},
    )
    assert result.proved
    assert result.verdict.flags == ["native_decide"]
    assert result.detail["fallback_used"] is True


async def test_shared_meter_bounds_total_spend_and_launches():
    fake = SequentialFake(scripts=[
        [{"text": "branch 0 burns budget"}],
        [{"text": "distill"}],
    ])

    async def validate(source, session, events):     # never reached
        return Verdict(passed=True)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=2)
    result = await run_parallel(fake, budget, validate, params={"n": 3})
    assert not result.proved
    assert result.detail["branches_launched"] == 1   # meter starved the rest
    assert budget.spent().turns <= 2


async def test_wall_threshold_stops_new_branches():
    fake = SequentialFake(scripts=[[{"text": "x"}], [{"text": "d"}]])

    async def validate(source, session, events):
        return Verdict(passed=True)

    budget = StrategyBudget(wall_clock_s=10.0, tokens=100_000, turns=50)
    result = await run_parallel(
        fake, budget, validate,
        params={"n": 3, "min_wall_to_launch_s": 600.0,   # threshold > budget
                "branches": [{"params": {"turns_per_round": 1}}] * 3},
    )
    assert result.detail["branches_launched"] == 1


async def test_branch_diversity_prompt_suffixes_differ():
    fake = SequentialFake(scripts=[
        [{"text": "b0"}], [{"text": "d0"}],
        [{"text": "b1"}], [{"text": "d1"}],
    ])

    async def validate(source, session, events):
        return Verdict(passed=True)

    budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=6)
    await run_parallel(
        fake, budget, validate,
        params={"n": 2, "branches": None,
                "min_wall_to_launch_s": 0.0},
    )
    prove_prompts = [c["system_prompt"] for c in fake.calls
                     if "Prove" in c["task"] or "prove" in c["task"].lower()]
    assert len(prove_prompts) >= 2
    assert prove_prompts[0] != prove_prompts[1]      # distinct variants


async def test_pausable_runtime_gates_new_calls():
    gate = asyncio.Event()
    inner_calls = []

    class Inner:
        async def run(self, task, system_prompt, tools, config):
            inner_calls.append(task)
            return None

    pausable = PausableRuntime(Inner(), gate)
    run_task = asyncio.ensure_future(pausable.run("t", "s", None, cfg()))
    await asyncio.sleep(0.05)
    assert inner_calls == []                         # cleared gate blocks
    gate.set()
    await run_task
    assert inner_calls == ["t"]
```

```python
# tests/test_strategy_conformance.py
"""Protocol conformance for all five registered strategies (spec testing
strategy: 'strategy protocol conformance (all five)'). Deep behavior is
covered per-strategy; this pins the shared surface."""

import inspect

import hardy.strategy  # noqa: F401  — populates the registry
from hardy.strategy.base import Strategy, create_strategy, strategy_names

EXPECTED = {"iterative", "closers", "sketch", "bestfirst", "parallel"}


def test_all_five_registered():
    assert EXPECTED <= set(strategy_names())


def test_conformance_surface():
    for name in sorted(EXPECTED):
        strategy = create_strategy(name, {})
        assert isinstance(strategy, Strategy)         # runtime_checkable
        assert strategy.name == name
        signature = inspect.signature(strategy.prove)
        assert set(signature.parameters) >= {
            "goal", "session_factory", "runtime", "config", "budget", "validate",
        }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parallel.py tests/test_strategy_conformance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.strategy.parallel'`

- [ ] **Step 3: Modify `capabilities.py`**

In `src/hardy/agent/capabilities.py`, add to `RuntimeCapabilities`:

```python
    # M7: whether the adapter can serve concurrent run() invocations
    # (parallel strategies query this and degrade to sequential when
    # False). The minimal loop reports False; SDK/Strands report True.
    parallel_runs: bool = True
```

and set `parallel_runs=False` in the minimal-loop adapter's reported capabilities (one keyword in its `RuntimeCapabilities(...)` construction).

- [ ] **Step 4: Write `parallel.py`**

```python
# src/hardy/strategy/parallel.py
"""Diverse parallel attempts (M7 spec parallel.py): n independent inner
strategy runs (default iterative) sharing nothing but the goal and the
meter. The first kernel success is only a PROVISIONAL winner: siblings
pause (no new calls) while the candidate runs full downstream validation;
only a validated, unflagged winner cancels the rest. A rejected candidate
resumes the siblings; a flagged-but-valid one resumes them too and is
retained as the fallback result. The shared meter makes n branches at
budget B cost B total — the equal-budget comparison depends on it."""

import asyncio
from types import SimpleNamespace

from pydantic import BaseModel

from hardy.strategy.base import (
    ProveGoal,
    StrategyResult,
    Verdict,
    create_strategy,
    register_strategy,
)
from hardy.strategy.budget import BudgetExpired, StrategyBudget

_TEMPERATURES = (0.2, 0.7, 1.0)
_VARIANTS = (
    "",
    "Try a different decomposition than the obvious one.",
    "Prefer computation-heavy tactics (norm_num, decide on small goals, omega).",
)


class BranchSpec(BaseModel):
    strategy: str = "iterative"
    params: dict = {}
    prompt_suffix: str = ""
    temperature: float | None = None


class PausableRuntime:
    """Delegates run() only while the gate is set — the pause siblings
    obey during a provisional winner's validation."""

    def __init__(self, inner, gate: asyncio.Event):
        self._inner = inner
        self._gate = gate

    @property
    def capabilities(self):
        return getattr(self._inner, "capabilities", None)

    async def run(self, task, system_prompt, tools, config):
        await self._gate.wait()
        return await self._inner.run(task, system_prompt, tools, config)


class ParallelStrategy:
    name = "parallel"

    def __init__(self, params: dict):
        self._n: int = params.get("n", 3)
        self._branches: list[dict] | None = params.get("branches")
        self._min_wall: float = params.get("min_wall_to_launch_s", 30.0)

    def _specs(self) -> list[BranchSpec]:
        if self._branches is not None:
            return [BranchSpec(**b) for b in self._branches]
        return [
            BranchSpec(
                prompt_suffix=_VARIANTS[i % len(_VARIANTS)],
                temperature=_TEMPERATURES[i % len(_TEMPERATURES)],
            )
            for i in range(self._n)
        ]

    @staticmethod
    def _branch_config(config, spec: BranchSpec):
        if spec.temperature is None or not hasattr(config, "provider_params"):
            return config          # temperature needs M5's provider_params
        merged = dict(getattr(config, "provider_params") or {})
        merged["temperature"] = spec.temperature
        return config.model_copy(update={"provider_params": merged})

    def _can_launch(self, budget: StrategyBudget) -> bool:
        remaining = budget.remaining()
        if remaining.wall_s < self._min_wall:
            return False
        if remaining.tokens is not None and remaining.tokens < 1024:
            return False
        if remaining.turns is not None and remaining.turns < 1:
            return False
        return True

    async def prove(
        self, goal: ProveGoal, *, session_factory, runtime, config, budget,
        validate,
    ) -> StrategyResult:
        caps = getattr(runtime, "capabilities", None)
        parallel_ok = caps is None or getattr(caps, "parallel_runs", True)
        specs = self._specs()
        gate = asyncio.Event()
        gate.set()
        state = SimpleNamespace(
            winner=None, winner_branch=None, fallback=None, launched=0,
        )
        winner_found = asyncio.Event()

        def gated_validate_for(branch_index: int):
            async def gated_validate(source, session, events) -> Verdict:
                gate.clear()                     # pause siblings' NEW calls
                try:
                    verdict = await validate(source, session, events)
                except BaseException:
                    gate.set()
                    raise
                if verdict.passed and not verdict.flags:
                    state.winner = (source, verdict)
                    state.winner_branch = branch_index
                    winner_found.set()           # clean winner: cancel the rest
                    return verdict
                if verdict.passed and verdict.flags:
                    if state.fallback is None:
                        state.fallback = (source, verdict)
                    gate.set()                   # resume: hunt a clean proof
                    return Verdict(
                        passed=False,
                        detail="candidate valid but flagged "
                               f"({', '.join(verdict.flags)}); retained as "
                               "fallback, continuing for an unflagged proof",
                    )
                gate.set()                       # rejected: branch continues
                return verdict

            return gated_validate

        async def run_branch(branch_index: int, spec: BranchSpec) -> None:
            inner = create_strategy(
                spec.strategy,
                {**spec.params, "prompt_suffix": spec.prompt_suffix
                 or spec.params.get("prompt_suffix", "")},
            )
            await inner.prove(
                goal,
                session_factory=session_factory,
                runtime=PausableRuntime(runtime, gate),
                config=self._branch_config(config, spec),
                budget=budget,
                validate=gated_validate_for(branch_index),
            )

        try:
            if parallel_ok:
                tasks: list[asyncio.Task] = []
                try:
                    for i, spec in enumerate(specs):
                        if not self._can_launch(budget):
                            break
                        state.launched += 1
                        tasks.append(asyncio.ensure_future(run_branch(i, spec)))
                    if tasks:
                        waiter = asyncio.ensure_future(winner_found.wait())
                        done, _ = await asyncio.wait(
                            [*tasks, waiter],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        while not winner_found.is_set() and any(
                            not t.done() for t in tasks
                        ):
                            done, _ = await asyncio.wait(
                                [*tasks, waiter],
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if all(t.done() for t in tasks):
                                break
                        waiter.cancel()
                finally:
                    for task in tasks:
                        task.cancel()            # cancellation-safe leases +
                    for task in tasks:           # forfeited reservations
                        try:
                            await task
                        except (asyncio.CancelledError, BudgetExpired):
                            pass
            else:
                for i, spec in enumerate(specs):
                    if winner_found.is_set() or not self._can_launch(budget):
                        break
                    state.launched += 1
                    try:
                        await run_branch(i, spec)
                    except BudgetExpired:
                        break
        except BudgetExpired:
            pass

        mode = "parallel" if parallel_ok else "sequential"
        if state.winner is not None:
            source, verdict = state.winner
            return StrategyResult(
                proved=True, source=source, verdict=verdict,
                budget_spent=budget.spent(),
                detail={"mode": mode, "branches_launched": state.launched,
                        "winning_branch": state.winner_branch,
                        "fallback_used": False},
            )
        if state.fallback is not None:
            source, verdict = state.fallback
            return StrategyResult(
                proved=True, source=source, verdict=verdict,
                budget_spent=budget.spent(),
                detail={"mode": mode, "branches_launched": state.launched,
                        "winning_branch": None, "fallback_used": True},
            )
        return StrategyResult(
            proved=False, budget_spent=budget.spent(),
            detail={"mode": mode, "branches_launched": state.launched,
                    "fallback_used": False},
        )


register_strategy("parallel", lambda params: ParallelStrategy(params))
```

**Implementation note:** branch events are inside the inner strategies' results, which this compositor discards for cancelled branches — the meter (spend) and the trajectory events of the *winning* branch survive via the validator path and `budget.spent()`. If review wants full sibling telemetry, thread a shared event sink through `strategy_params`; `detail` already records launch/cancel counts. Winner `StrategyResult.events` are empty here by design — the winning source, verdict, and spend are the contract; a follow-up in the M6-loop integration can lift inner events.

- [ ] **Step 5: Populate `hardy/strategy/__init__.py`**

```python
# src/hardy/strategy/__init__.py
"""Strategy registry: importing this package registers all five built-in
strategies (iterative, closers, sketch, bestfirst, parallel)."""

from hardy.strategy import bestfirst, iterative, parallel, sketch  # noqa: F401
from hardy.strategy.base import (  # noqa: F401
    ProveGoal,
    Strategy,
    StrategyResult,
    Verdict,
    create_strategy,
    register_strategy,
    strategy_names,
)
from hardy.strategy.budget import BudgetSpent, StrategyBudget  # noqa: F401
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_parallel.py tests/test_strategy_conformance.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/strategy/parallel.py src/hardy/strategy/__init__.py src/hardy/agent/capabilities.py tests/test_parallel.py tests/test_strategy_conformance.py
git commit -m "feat: diverse parallel attempts with provisional-winner validation gating"
```

---

### Task 13: The predeclared decision rule (`hardy/eval/compare.py`)

**Files:**
- Create: `src/hardy/eval/compare.py`
- Test: `tests/test_compare_stats.py`

**Interfaces:**
- Consumes: stdlib only (`math.comb`, `random.Random`) — no scipy dependency.
- Produces:
  - `mcnemar_exact_p(b: int, c: int) -> float` — exact two-sided binomial McNemar on the discordant pairs (b = baseline-only solves, c = strategy-only solves); `1.0` when `b + c == 0`.
  - `paired_bootstrap_ci(diffs: list[float], *, iters: int = 10_000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]` — percentile CI of the mean per-item difference.
  - `holm_bonferroni(pvals: list[tuple[str, float]], alpha: float = 0.05) -> dict[str, bool]` — step-down correction across however many strategies were compared.
  - `DecisionRule(test: Literal["mcnemar", "bootstrap"] = "mcnemar", alpha: float = 0.05, bootstrap_iters: int = 10_000, seed: int = 0)` — **fixed in config before the run** (it is part of the comparison record).
  - `ComparisonOutcome(strategy: str, baseline_solves: int, strategy_solves: int, b_only: int, c_only: int, p_value: float | None, ci_low: float | None, ci_high: float | None, corrected_significant: bool, verdict: Literal["win", "loss", "inconclusive"])`.
  - `decide_all(baseline: list[bool], candidates: dict[str, list[bool]], rule: DecisionRule) -> list[ComparisonOutcome]` — paired per-item outcomes; the correction spans all candidates; `verdict == "win"` **only** when corrected-significant and the strategy out-solves the baseline (`c_only > b_only`, or CI strictly above zero for bootstrap); a bare point-estimate advantage is `"inconclusive"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compare_stats.py
import pytest

from hardy.eval.compare import (
    ComparisonOutcome,
    DecisionRule,
    decide_all,
    holm_bonferroni,
    mcnemar_exact_p,
    paired_bootstrap_ci,
)


def test_mcnemar_exact_known_values():
    assert mcnemar_exact_p(0, 0) == 1.0
    assert mcnemar_exact_p(5, 5) == 1.0                       # perfectly balanced
    # 0 vs 10 discordant: p = 2 * C(10,0)/2^10 = 2/1024
    assert mcnemar_exact_p(0, 10) == pytest.approx(2 / 1024)
    assert mcnemar_exact_p(10, 0) == mcnemar_exact_p(0, 10)   # symmetric


def test_bootstrap_ci_deterministic_and_sane():
    diffs = [1.0] * 20                                        # constant diffs
    low, high = paired_bootstrap_ci(diffs, seed=7)
    assert low == high == 1.0
    diffs = [1.0, -1.0] * 10
    low, high = paired_bootstrap_ci(diffs, seed=7)
    assert low < 0 < high                                     # straddles zero
    assert paired_bootstrap_ci(diffs, seed=7) == paired_bootstrap_ci(diffs, seed=7)


def test_holm_stepdown():
    assert holm_bonferroni([("a", 0.01), ("b", 0.03)], alpha=0.05) == {
        "a": True, "b": True,
    }
    # first fails at alpha/2 -> everything after fails too (step-down)
    assert holm_bonferroni([("a", 0.03), ("b", 0.2)], alpha=0.05) == {
        "a": False, "b": False,
    }
    assert holm_bonferroni([], alpha=0.05) == {}


def test_decide_all_win_needs_significance_not_point_estimate():
    baseline = [False] * 12
    strong = [True] * 12                                      # 12 strategy-only
    weak = [True] + [False] * 11                              # 1 strategy-only
    outcomes = decide_all(
        baseline, {"strong": strong, "weak": weak}, DecisionRule()
    )
    by_name = {o.strategy: o for o in outcomes}
    assert by_name["strong"].verdict == "win"
    assert by_name["strong"].corrected_significant
    assert by_name["weak"].verdict == "inconclusive"          # point estimate only
    assert by_name["weak"].c_only == 1


def test_decide_all_loss_detected():
    baseline = [True] * 12
    worse = [False] * 12
    [outcome] = decide_all(baseline, {"worse": worse}, DecisionRule())
    assert outcome.verdict == "loss"


def test_decide_all_length_mismatch_rejected():
    with pytest.raises(ValueError, match="paired"):
        decide_all([True, False], {"x": [True]}, DecisionRule())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compare_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.compare'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/eval/compare.py
"""The predeclared decision rule for strategy comparisons (M7 spec):
with stochastic sampling on a small eval subset, SOME strategy will beat
the baseline by luck — a win therefore requires paired per-item outcomes,
a paired test with a confidence interval, and a multiple-comparison
correction. 'Beats iterative repair' means the corrected result excludes
chance; a point-estimate win records as inconclusive, and the honest
response is more attempts/items, never a declared victory."""

from math import comb
from random import Random
from typing import Literal

from pydantic import BaseModel


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided binomial McNemar over discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2.0 * tail)


def paired_bootstrap_ci(
    diffs: list[float], *, iters: int = 10_000, seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float]:
    rng = Random(seed)
    n = len(diffs)
    means = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(iters)
    )
    low_index = int((alpha / 2) * iters)
    high_index = min(iters - 1, int((1 - alpha / 2) * iters))
    return means[low_index], means[high_index]


def holm_bonferroni(
    pvals: list[tuple[str, float]], alpha: float = 0.05
) -> dict[str, bool]:
    """Step-down Holm correction: sort ascending; the i-th (0-based) of m
    p-values must clear alpha/(m-i); the first failure fails the rest."""
    result: dict[str, bool] = {}
    ordered = sorted(pvals, key=lambda kv: kv[1])
    m = len(ordered)
    failed = False
    for i, (name, p) in enumerate(ordered):
        if not failed and p <= alpha / (m - i):
            result[name] = True
        else:
            failed = True
            result[name] = False
    return result


class DecisionRule(BaseModel):
    test: Literal["mcnemar", "bootstrap"] = "mcnemar"
    alpha: float = 0.05
    bootstrap_iters: int = 10_000
    seed: int = 0


class ComparisonOutcome(BaseModel):
    strategy: str
    baseline_solves: int
    strategy_solves: int
    b_only: int
    c_only: int
    p_value: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    corrected_significant: bool
    verdict: Literal["win", "loss", "inconclusive"]


def decide_all(
    baseline: list[bool],
    candidates: dict[str, list[bool]],
    rule: DecisionRule,
) -> list[ComparisonOutcome]:
    partials: dict[str, dict] = {}
    pvals: list[tuple[str, float]] = []
    for name, solved in candidates.items():
        if len(solved) != len(baseline):
            raise ValueError(
                f"{name}: outcomes must be paired per-item with the baseline "
                f"({len(solved)} vs {len(baseline)})"
            )
        b_only = sum(1 for base, cand in zip(baseline, solved) if base and not cand)
        c_only = sum(1 for base, cand in zip(baseline, solved) if cand and not base)
        entry: dict = {
            "b_only": b_only, "c_only": c_only,
            "baseline_solves": sum(baseline), "strategy_solves": sum(solved),
        }
        if rule.test == "mcnemar":
            p = mcnemar_exact_p(b_only, c_only)
            entry["p_value"] = p
            pvals.append((name, p))
        else:
            diffs = [float(cand) - float(base)
                     for base, cand in zip(baseline, solved)]
            low, high = paired_bootstrap_ci(
                diffs, iters=rule.bootstrap_iters, seed=rule.seed,
                alpha=rule.alpha,
            )
            entry["ci_low"], entry["ci_high"] = low, high
            # map the CI to a pseudo-p for Holm ordering: 0 if it excludes
            # zero, else 1 (the correction then simply gates on exclusion)
            pvals.append((name, 0.0 if (low > 0 or high < 0) else 1.0))
        partials[name] = entry
    significant = holm_bonferroni(pvals, alpha=rule.alpha)
    outcomes: list[ComparisonOutcome] = []
    for name, entry in partials.items():
        is_significant = significant.get(name, False)
        if not is_significant:
            verdict = "inconclusive"
        elif entry["c_only"] > entry["b_only"]:
            verdict = "win"
        else:
            verdict = "loss"
        outcomes.append(ComparisonOutcome(
            strategy=name, corrected_significant=is_significant,
            verdict=verdict, **entry,
        ))
    return outcomes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_compare_stats.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/compare.py tests/test_compare_stats.py
git commit -m "feat: predeclared paired decision rule with Holm correction"
```

---

### Task 14: The contemporaneous comparison harness (`compare_strategies.py`)

**Files:**
- Create: `src/hardy/eval/compare_strategies.py`
- Create: `scripts/compare_strategies.py`
- Test: `tests/test_compare_strategies.py`

**Interfaces:**
- Consumes: `DecisionRule`/`decide_all`/`ComparisonOutcome` (Task 13); `RunConfig` with strategy axis (Task 2); M2's runner/tracking through **injected adapters** (plan assumptions 12–13 — only the CLI glue binds real ones).
- Produces:
  - `reject_model_overrides(strategy_params: dict) -> None` — raises `ValueError` on any key (recursively) ending in `"model"` with a truthy value ("same model" applies to nested strategy work; heterogeneous-model configs are a separate experiment axis, never the headline comparison).
  - `ComparisonConfig(strategies: list[str], per_strategy_params: dict[str, dict] = {}, budget: dict, rule: DecisionRule = DecisionRule(), seed: int = 0, benchmark: str = "minif2f", split: str = "valid", items_limit: int | None = None, allow_dirty: bool = False)` — `budget` holds the one shared `StrategyBudget` construction kwargs (equal budget per (item, strategy) attempt).
  - `ComparisonRecord(created_at: str, git_sha: str, seed: int, strategies: list[str], budget: dict, rule: DecisionRule, schedule: list[list[str]], outcomes: list[ComparisonOutcome], per_item: dict[str, list[bool]], model_revisions: list[str], valid: bool, invalid_reason: str | None, run_ids: dict[str, str])` — one JSON line appended to `eval_results/comparisons.jsonl`.
  - `async run_comparison(config: ComparisonConfig, base_run_config: RunConfig, *, items: list, run_attempt, tracking_append, git_sha, out_path: Path) -> ComparisonRecord` where the injected callables are:
    - `run_attempt(item, run_config, budget_kwargs) -> dict` returning at least `{"solved": bool, "model_revision": str | None}` — the adapter over M2's runner (assumption 12); **it must run with `closer_prepass=False`** (the harness passes it and the test pins it),
    - `tracking_append(strategy: str, results: list[dict]) -> str` returning the per-strategy tracking-run id (adapter over M2 tracking),
    - `git_sha() -> tuple[str, bool]` — `(sha, dirty)`.
- **Behavior contract:**
  1. Refuses a dirty tree unless `allow_dirty` (and `allow_dirty` runs are marked non-baseline in the record: `valid=False`, reason `"dirty tree"` — an exit-criterion comparison is never dirty).
  2. `"iterative"` is always included as baseline (prepended if missing); duplicates removed order-preserved.
  3. Per-item strategy order is **randomized under `Random(seed)`** and recorded in `schedule` — interleaved execution follows the schedule item-by-item (never strategy-by-strategy back-to-back).
  4. Every attempt's resolved model revision is collected; more than one distinct revision — or any attempt without one — sets `valid=False` with the reason (**invalidates**, not merely surfaces).
  5. `reject_model_overrides` runs on every strategy's params before anything executes.
  6. The record embeds the predeclared `rule`, per-strategy tracking-run ids, per-item paired outcomes, and the decision outcomes; it is appended as one fsynced JSON line.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compare_strategies.py
import json
from pathlib import Path

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.compare import DecisionRule
from hardy.eval.compare_strategies import (
    ComparisonConfig,
    reject_model_overrides,
    run_comparison,
)


def base_cfg() -> RunConfig:
    return RunConfig(model="m", max_turns=50, wall_clock_s=300.0,
                     prompt_version="prove_v1")


def items(n=8):
    return [{"id": f"item-{i}"} for i in range(n)]


def make_run_attempt(solve_table, revision="rev-1"):
    calls = []

    async def run_attempt(item, run_config, budget_kwargs):
        calls.append({"item": item["id"], "strategy": run_config.strategy,
                      "prepass": budget_kwargs.get("closer_prepass", None)})
        solved = solve_table.get((item["id"], run_config.strategy), False)
        return {"solved": solved, "model_revision": revision}

    run_attempt.calls = calls
    return run_attempt


def tracking_append(strategy, results):
    return f"run-{strategy}"


def clean_git():
    return ("abc123", False)


def config(**kw) -> ComparisonConfig:
    defaults = dict(strategies=["bestfirst"], budget={"wall_clock_s": 300.0},
                    rule=DecisionRule(), seed=42)
    defaults.update(kw)
    return ComparisonConfig(**defaults)


def test_reject_model_overrides_recursive():
    reject_model_overrides({"k": 4})                       # fine
    with pytest.raises(ValueError, match="subgoal_model"):
        reject_model_overrides({"subgoal_model": "cheap"})
    with pytest.raises(ValueError, match="model"):
        reject_model_overrides({"branches": [{"params": {"subgoal_model": "x"}}]})


async def test_dirty_tree_refused(tmp_path):
    with pytest.raises(RuntimeError, match="dirty"):
        await run_comparison(
            config(), base_cfg(), items=items(),
            run_attempt=make_run_attempt({}), tracking_append=tracking_append,
            git_sha=lambda: ("abc", True), out_path=tmp_path / "c.jsonl",
        )


async def test_baseline_always_included_and_schedule_randomized(tmp_path):
    attempt = make_run_attempt({})
    record = await run_comparison(
        config(), base_cfg(), items=items(8), run_attempt=attempt,
        tracking_append=tracking_append, git_sha=clean_git,
        out_path=tmp_path / "c.jsonl",
    )
    assert record.strategies[0] == "iterative"             # implicit baseline
    assert all(sorted(order) == sorted(record.strategies)
               for order in record.schedule)
    assert len(set(tuple(o) for o in record.schedule)) > 1  # not fixed round-robin
    # deterministic under the recorded seed
    attempt2 = make_run_attempt({})
    record2 = await run_comparison(
        config(), base_cfg(), items=items(8), run_attempt=attempt2,
        tracking_append=tracking_append, git_sha=clean_git,
        out_path=tmp_path / "c2.jsonl",
    )
    assert record2.schedule == record.schedule


async def test_prepass_disabled_in_every_arm(tmp_path):
    attempt = make_run_attempt({})
    await run_comparison(
        config(), base_cfg(), items=items(2), run_attempt=attempt,
        tracking_append=tracking_append, git_sha=clean_git,
        out_path=tmp_path / "c.jsonl",
    )
    assert all(call["prepass"] is False for call in attempt.calls)


async def test_revision_mismatch_invalidates(tmp_path):
    revisions = iter(["rev-1", "rev-2"] * 50)

    async def run_attempt(item, run_config, budget_kwargs):
        return {"solved": False, "model_revision": next(revisions)}

    record = await run_comparison(
        config(), base_cfg(), items=items(4), run_attempt=run_attempt,
        tracking_append=tracking_append, git_sha=clean_git,
        out_path=tmp_path / "c.jsonl",
    )
    assert record.valid is False
    assert "revision" in record.invalid_reason


async def test_missing_revision_invalidates(tmp_path):
    async def run_attempt(item, run_config, budget_kwargs):
        return {"solved": False, "model_revision": None}

    record = await run_comparison(
        config(), base_cfg(), items=items(2), run_attempt=run_attempt,
        tracking_append=tracking_append, git_sha=clean_git,
        out_path=tmp_path / "c.jsonl",
    )
    assert record.valid is False


async def test_outcomes_and_record_written(tmp_path):
    table = {(f"item-{i}", "bestfirst"): True for i in range(8)}
    record = await run_comparison(
        config(), base_cfg(), items=items(8),
        run_attempt=make_run_attempt(table), tracking_append=tracking_append,
        git_sha=clean_git, out_path=tmp_path / "c.jsonl",
    )
    assert record.valid is True
    [outcome] = record.outcomes
    assert outcome.strategy == "bestfirst"
    assert outcome.verdict == "win"                        # 8 vs 0, corrected
    assert record.run_ids == {"iterative": "run-iterative",
                              "bestfirst": "run-bestfirst"}
    line = (tmp_path / "c.jsonl").read_text().strip()
    assert json.loads(line)["valid"] is True


async def test_model_override_rejected_before_running(tmp_path):
    attempt = make_run_attempt({})
    with pytest.raises(ValueError, match="model"):
        await run_comparison(
            config(per_strategy_params={"sketch": {"subgoal_model": "cheap"}},
                   strategies=["sketch"]),
            base_cfg(), items=items(2), run_attempt=attempt,
            tracking_append=tracking_append, git_sha=clean_git,
            out_path=tmp_path / "c.jsonl",
        )
    assert attempt.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compare_strategies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.compare_strategies'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/eval/compare_strategies.py
"""The exit-criterion harness (M7 spec compare_strategies.py): one eval
configuration + a strategy list (iterative always implicitly the
baseline), run CONTEMPORANEOUSLY — same harness commit (dirty tree
refused), same model (resolved immutable revision checked across every
linked attempt; mismatch or absence INVALIDATES the comparison), same
environment, same items, same StrategyBudget. Execution interleaves
strategies across the item list with per-item order randomized under a
recorded seed — a fixed round-robin would let provider throttling, cache
warmth, or drifting load correlate with strategy identity. The historical
M2 number is never referenced. The global closer pre-pass is disabled in
every arm. A win is decided by the predeclared rule (hardy.eval.compare),
never a point estimate."""

import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from hardy.agent.runtime import RunConfig
from hardy.eval.compare import ComparisonOutcome, DecisionRule, decide_all


def reject_model_overrides(strategy_params: dict) -> None:
    """'Same model' applies to nested strategy work (subgoal_model,
    per-branch models), not just the outer config — an apparent strategy
    win could actually be a model change."""

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("model") and value:
                    raise ValueError(
                        f"model override {'.'.join(path + [key])}={value!r} is "
                        "not allowed in an exit-criterion comparison; "
                        "heterogeneous-model configs are a separate experiment"
                    )
                walk(value, path + [key])
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, path + [str(i)])

    walk(strategy_params, [])


class ComparisonConfig(BaseModel):
    strategies: list[str]
    per_strategy_params: dict[str, dict] = {}
    budget: dict
    rule: DecisionRule = DecisionRule()
    seed: int = 0
    benchmark: str = "minif2f"
    split: str = "valid"
    items_limit: int | None = None
    allow_dirty: bool = False


class ComparisonRecord(BaseModel):
    created_at: str
    git_sha: str
    seed: int
    strategies: list[str]
    budget: dict
    rule: DecisionRule
    schedule: list[list[str]]
    outcomes: list[ComparisonOutcome]
    per_item: dict[str, list[bool]]
    model_revisions: list[str]
    valid: bool
    invalid_reason: str | None = None
    run_ids: dict[str, str] = {}


def _append_record(out_path: Path, record: ComparisonRecord) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def run_comparison(
    config: ComparisonConfig,
    base_run_config: RunConfig,
    *,
    items: list,
    run_attempt,
    tracking_append,
    git_sha,
    out_path: Path,
) -> ComparisonRecord:
    # -- preconditions, all before anything runs --------------------------
    for name, params in config.per_strategy_params.items():
        reject_model_overrides(params)
    sha, dirty = git_sha()
    if dirty and not config.allow_dirty:
        raise RuntimeError(
            "dirty working tree: a contemporaneous comparison must run one "
            "identifiable harness commit (commit or stash, or pass "
            "--allow-dirty for a non-baseline exploratory run)"
        )
    strategies = ["iterative"] + [
        s for s in dict.fromkeys(config.strategies) if s != "iterative"
    ]
    if config.items_limit is not None:
        items = items[: config.items_limit]

    # -- randomized-per-item interleaved schedule -------------------------
    rng = random.Random(config.seed)
    schedule: list[list[str]] = []
    for _ in items:
        order = list(strategies)
        rng.shuffle(order)
        schedule.append(order)

    # -- run --------------------------------------------------------------
    per_item: dict[str, list[bool]] = {s: [False] * len(items) for s in strategies}
    per_strategy_results: dict[str, list[dict]] = {s: [] for s in strategies}
    revisions: set[str | None] = set()
    for item_index, (item, order) in enumerate(zip(items, schedule)):
        for strategy in order:
            run_config = base_run_config.model_copy(update={
                "strategy": strategy,
                "strategy_params": config.per_strategy_params.get(strategy, {}),
            })
            result = await run_attempt(
                item, run_config,
                {**config.budget, "closer_prepass": False},
            )
            revisions.add(result.get("model_revision"))
            per_item[strategy][item_index] = bool(result.get("solved"))
            per_strategy_results[strategy].append(
                {"item": item, "strategy": strategy, **result}
            )

    run_ids = {
        strategy: tracking_append(strategy, per_strategy_results[strategy])
        for strategy in strategies
    }

    # -- validity: one resolved model revision, or no comparison ----------
    valid, reason = True, None
    if dirty:
        valid, reason = False, "dirty tree (allow_dirty run; non-baseline)"
    elif None in revisions:
        valid, reason = False, (
            "provider exposed no resolved model revision for some attempts"
        )
    elif len(revisions) != 1:
        valid, reason = False, (
            f"model revision changed mid-comparison: {sorted(revisions)}"
        )

    outcomes = decide_all(
        per_item["iterative"],
        {s: per_item[s] for s in strategies if s != "iterative"},
        config.rule,
    )
    record = ComparisonRecord(
        created_at=datetime.now(timezone.utc).isoformat(),
        git_sha=sha, seed=config.seed, strategies=strategies,
        budget=config.budget, rule=config.rule, schedule=schedule,
        outcomes=outcomes, per_item=per_item,
        model_revisions=sorted(r for r in revisions if r is not None),
        valid=valid, invalid_reason=reason, run_ids=run_ids,
    )
    _append_record(out_path, record)
    return record
```

- [ ] **Step 4: Write the CLI**

```python
#!/usr/bin/env python3
# scripts/compare_strategies.py
"""M7 exit-criterion entry point: contemporaneous strategy comparison on
the eval subset. Model-tier — needs credentials, the Lean toolchain, and
sandbox images; never CI.

Wires the real M2 adapters into hardy.eval.compare_strategies:
- items from the vendored benchmark loader,
- run_attempt over the M2 runner in benchmark mode (statement verbatim,
  no formalize/faithfulness/writeup, closer_prepass=False, anti-cheat as
  the validator, per-response resolved model revision captured),
- tracking_append over M2's runs.jsonl store,
- git_sha over `git rev-parse HEAD` + `git status --porcelain`.
Re-validate the exact M2 call shapes here (plan assumptions 12-13) —
this file is the only glue that binds them."""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from hardy.agent.runtime import RunConfig
from hardy.eval.compare import DecisionRule
from hardy.eval.compare_strategies import ComparisonConfig, run_comparison


def git_sha() -> tuple[str, bool]:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                capture_output=True, text=True,
                                check=True).stdout.strip())
    return sha, dirty


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="+", required=True,
                        help="candidates (iterative is always the baseline)")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--items-limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--wall-clock-s", type=float, default=900.0)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--lean-cpu-s", type=float, default=None)
    parser.add_argument("--benchmark", default="minif2f")
    parser.add_argument("--split", default="valid")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--out", type=Path,
                        default=Path("eval_results/comparisons.jsonl"))
    args = parser.parse_args()

    # ---- real adapters (M2) — re-validate shapes at execution ----------
    from hardy.eval.benchmark import load_minif2f  # noqa: PLC0415
    from scripts._compare_adapters import (  # noqa: PLC0415
        make_run_attempt, make_tracking_append,
    )
    # scripts/_compare_adapters.py is written in this task against the
    # landed M2 runner/tracking APIs: make_run_attempt builds the pool +
    # runtime once, runs one benchmark-mode attempt per call with the
    # given RunConfig and budget kwargs (closer_prepass honored), and
    # returns {"solved", "model_revision", "tokens", "lean_cpu_s"}.

    items = load_minif2f(Path("benchmarks") / args.benchmark)
    items = [i for i in items if getattr(i, "split", args.split) == args.split]

    budget = {"wall_clock_s": args.wall_clock_s, "turns": args.max_turns}
    if args.max_tokens is not None:
        budget["tokens"] = args.max_tokens
    if args.lean_cpu_s is not None:
        budget["lean_cpu_s"] = args.lean_cpu_s

    config = ComparisonConfig(
        strategies=args.strategies, budget=budget,
        rule=DecisionRule(), seed=args.seed, benchmark=args.benchmark,
        split=args.split, items_limit=args.items_limit,
        allow_dirty=args.allow_dirty,
    )
    base = RunConfig(model=args.model, max_turns=args.max_turns,
                     max_tokens_total=args.max_tokens,
                     wall_clock_s=args.wall_clock_s,
                     prompt_version="prove_v1")
    record = await run_comparison(
        config, base, items=items,
        run_attempt=make_run_attempt(base),
        tracking_append=make_tracking_append(config),
        git_sha=git_sha, out_path=args.out,
    )

    print(f"comparison valid: {record.valid}"
          + (f" ({record.invalid_reason})" if record.invalid_reason else ""))
    for outcome in record.outcomes:
        print(f"  {outcome.strategy:10s} verdict={outcome.verdict:12s} "
              f"solves={outcome.strategy_solves} vs "
              f"baseline={outcome.baseline_solves} "
              f"(b={outcome.b_only}, c={outcome.c_only}, "
              f"p={outcome.p_value})")
    won = record.valid and any(o.verdict == "win" for o in record.outcomes)
    print("EXIT CRITERION:", "MET" if won else "NOT MET")
    if not won and any(o.verdict == "inconclusive" for o in record.outcomes):
        print("(inconclusive: the honest response is more items/attempts, "
              "not a declared victory)")
    return 0 if won else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

`scripts/_compare_adapters.py` is written in this same task against the **landed** M2 runner/tracking APIs (assumptions 12–13); its contract is fully specified by the injected-callable signatures above and pinned by `tests/test_compare_strategies.py` (which exercises `run_comparison` with fakes) — the adapter file itself is exercised only by the `model` tier, exactly like M1's `_default_client_factory`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_compare_strategies.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/eval/compare_strategies.py scripts/compare_strategies.py scripts/_compare_adapters.py tests/test_compare_strategies.py
git commit -m "feat: contemporaneous comparison harness — seeded interleave, revision guard, decision rule"
```

---

### Task 15: `lean`-tier integration — real-kernel pickling, closers, and sketch

**Files:**
- Test: `tests/test_integration_pickle.py`, `tests/test_integration_closers.py`, `tests/test_integration_sketch.py` (all `@pytest.mark.lean`)

**Interfaces:**
- Consumes: the full M7 stack over the real REPL (`hardy.lean.launch.repl_argv/repl_env/LEAN_PROJECT`, M0 setup).
- Produces: the spec's `lean`-tier coverage — pickling round-trip **across two real workers**, closers actually closing trivial goals, a scripted (canned-skeleton) sketch discharged and assembled through the real kernel. Also the wire-shape verification point for plan assumption 22: if these tests fail on command names, fix `pickle.py`'s two wire functions, nothing else.

- [ ] **Step 1: Write the pickling round-trip test**

```python
# tests/test_integration_pickle.py
"""Real-kernel pickling: a proof state pickled on one worker restores on
ANOTHER worker (prefix replayed first) and remains provable there."""

import pytest

from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pickle import SnapshotStore
from hardy.lean.pool import ReplPool

pytestmark = pytest.mark.lean


@pytest.fixture
async def pool():
    p = ReplPool(size=2, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                 imports="import Mathlib.Tactic")
    await p.start()
    yield p
    await p.close()


async def test_roundtrip_across_two_workers(pool, tmp_path):
    store = SnapshotStore(tmp_path / "snaps")
    try:
        async with pool.lease() as first:
            out = await first.check("theorem m7_pickle : 1 + 1 = 2 := by sorry")
            [state] = first.known_states()
            snap = await store.save(first, state, prefix=[])
            assert snap is not None
        async with pool.lease() as second:      # a different (or recycled) worker
            restored = await store.restore(second, snap)
            assert restored is not None
            new_state, goals = restored
            assert any("2" in g for g in goals)
            result = await second.tactic("norm_num", proof_state=new_state)
            assert result.ok and result.goals == []
    finally:
        store.cleanup()


async def test_prefix_replay_restores_helper_context(pool, tmp_path):
    store = SnapshotStore(tmp_path / "snaps")
    prefix = ["def m7_helper : Nat := 2"]
    try:
        async with pool.lease() as first:
            env = await first.replay(prefix)
            assert env is not None
            resp = await first.command_in(
                "theorem m7_uses : m7_helper = 2 := by sorry", env=env
            )
            assert resp is not None and resp.sorries
            state = resp.sorries[0].proof_state
            first.adopt_state(state, resp.sorries[0].goal)
            snap = await store.save(first, state, prefix=prefix)
            assert snap is not None
        async with pool.lease() as second:
            restored = await store.restore(second, snap)
            assert restored is not None         # helper replayed before unpickle
            result = await second.tactic("rfl", proof_state=restored[0])
            assert result.ok
    finally:
        store.cleanup()
```

- [ ] **Step 2: Write the closers integration test**

```python
# tests/test_integration_closers.py
import pytest

from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.strategy.base import ProveGoal
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.closers import try_closers_goal, try_closers_state
from hardy.strategy.metered import MeteredSession

pytestmark = pytest.mark.lean


@pytest.fixture
async def pool():
    p = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                 imports="import Mathlib.Tactic")
    await p.start()
    yield p
    await p.close()


async def test_closers_close_trivial_goals(pool):
    async with pool.lease() as session:
        metered = MeteredSession(session, StrategyBudget(wall_clock_s=300.0))
        out = await metered.check("theorem m7_c1 : 2 + 2 = 4 := by sorry")
        [state] = session.known_states()
        tactic = await try_closers_state(metered, state, per_tactic_timeout=30.0)
        assert tactic is not None                    # simp or omega gets it


async def test_closers_whole_goal(pool):
    async with pool.lease() as session:
        metered = MeteredSession(session, StrategyBudget(wall_clock_s=300.0))
        goal = ProveGoal(name="m7_c2", statement="theorem m7_c2 : 1 ≤ 2")
        source = await try_closers_goal(metered, goal, per_tactic_timeout=30.0)
        assert source is not None
        assert source.startswith("theorem m7_c2")
```

- [ ] **Step 3: Write the scripted-sketch integration test**

```python
# tests/test_integration_sketch.py
"""A canned skeleton discharged and assembled through the real kernel —
no model. FakeRuntime supplies the plan and skeleton; closers discharge
the holes; the real validator (final check) is the success authority."""

import pytest

from hardy.agent.runtime import RunConfig
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.strategy.base import ProveGoal, create_strategy
from hardy.strategy.budget import StrategyBudget
from hardy.strategy.sketch import MemorySketchLedger
from hardy.workflows.prove import make_prove_validator
from tests.fake_runtime import FakeRuntime

pytestmark = pytest.mark.lean

GOAL = ProveGoal(name="m7_sketch", statement="theorem m7_sketch : 2 + 2 = 4 ∧ 1 ≤ 2")
BODY = ("have h1 : 2 + 2 = 4 := sorry\n"
        "have h2 : 1 ≤ 2 := sorry\n"
        "exact ⟨h1, h2⟩")


async def test_canned_sketch_end_to_end():
    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT, env=repl_env(),
                    imports="import Mathlib.Tactic")
    await pool.start()
    try:
        ledger = MemorySketchLedger()
        strategy = create_strategy("sketch", {"ledger": ledger})
        fake = FakeRuntime(scripts=[
            [{"text": "Plan: split the conjunction; both parts are numeric."}],
            [{"tool": "submit_skeleton", "arguments": {"body": BODY}},
             {"text": "submitted"}],
        ])
        budget = StrategyBudget(wall_clock_s=600.0, tokens=100_000, turns=50)
        result = await strategy.prove(
            GOAL, session_factory=pool.lease, runtime=fake,
            config=RunConfig(model="none", max_turns=50, wall_clock_s=600.0,
                             prompt_version="prove_v1"),
            budget=budget, validate=make_prove_validator(GOAL),
        )
        assert result.proved                       # closers discharged both
        assert "sorry" not in result.source
        assert ledger.verified == ["h-000", "h-001"]
    finally:
        await pool.close()
```

- [ ] **Step 4: Run the lean tier**

Run: `pytest -m lean tests/test_integration_pickle.py tests/test_integration_closers.py tests/test_integration_sketch.py -v` (on a host with the toolchain; `scripts/setup_lean.sh` completed)
Expected: all PASS. If the pickling tests fail on unknown-command errors, fix the wire shapes in `hardy/lean/pickle.py` per the pinned `vendor/repl` (assumption 22) and re-run.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration_pickle.py tests/test_integration_closers.py tests/test_integration_sketch.py
git commit -m "test: lean-tier integration — cross-worker pickling, closers, canned sketch"
```

---

### Task 16: Exit criterion — the contemporaneous comparison run

**Files:**
- No new source files (Task 14's script is the entry point); results land in `eval_results/` (committed per M2's discipline).

**Interfaces:**
- Consumes: the full M7 stack + M2 eval + a real model.
- Produces: the milestone's exit evidence — a `ComparisonRecord` in `eval_results/comparisons.jsonl` with `valid=true` and at least one `verdict="win"`, plus the per-strategy tracking entries it links.

**The criterion (from the spec, verbatim discipline):** at least one strategy beats iterative repair on solve rate at equal budget — iterative repair **re-run contemporaneously** under the same M7 code, model, environment, and eval configuration (never the historical M2 number) — with the per-strategy comparison logged in the regression tracker, and "beats" meaning the **corrected** decision rule says `win`, not a bigger point estimate.

- [ ] **Step 1: Full verification sweep**

```bash
pytest -m "not lean and not tex and not docker and not model"   # CI tier: PASS
pytest -m lean -v                                               # toolchain host: PASS
pytest -m docker -v                                             # sandbox images: PASS
```

Expected: all green. A clean tree (`git status --porcelain` empty) — the comparison refuses anything else.

- [ ] **Step 2: Run the comparison (model tier)**

```bash
python scripts/compare_strategies.py \
    --strategies closers bestfirst sketch parallel \
    --model claude-sonnet-5 \
    --items-limit 50 --seed 0 \
    --wall-clock-s 900 --max-turns 40
```

Expected output shape:

```
comparison valid: True
  closers    verdict=...          solves=... vs baseline=... (b=..., c=..., p=...)
  bestfirst  verdict=...          ...
  sketch     verdict=...          ...
  parallel   verdict=...          ...
EXIT CRITERION: MET
```

- [ ] **Step 3: If NOT MET — the honest loop**

An `inconclusive` best candidate means **more items/attempts under the same predeclared rule** (`--items-limit` up, or attempts via the M2 config), never a rule change after seeing results and never a declared victory. Iterate strategy *parameters* only alongside a fresh comparison run (each run is a fresh record; the rule stays fixed within a run). If no strategy wins after honest scaling, M7 is **not complete** — surface the recorded inconclusive comparisons rather than shipping.

- [ ] **Step 4: Commit the evidence**

```bash
git add eval_results/
git commit -m "eval: M7 exit criterion — contemporaneous strategy comparison record"
```

M7 is **complete** only when `scripts/compare_strategies.py` prints `EXIT CRITERION: MET` from a `valid=true` record — a corrected-significant win over contemporaneous iterative repair at equal budget, logged in the tracker.

---

## Self-Review

Checked against the spec after drafting:

1. **Spec coverage.** `base.py` seam (protocol, `ProveGoal`, validator injection with events-at-validation-time, `session_factory` lease contract, `StrategyResult` events) → Tasks 1–2; `StrategyBudget` semantics — additive reservations atomic, wall as deadline enforced on in-flight work, Lean CPU enforced during the command, degradation reads, serialize-on-starvation → Tasks 1/3 (starvation-serialization exercised in Task 12's `test_shared_meter_bounds_total_spend_and_launches`); registration via `RunConfig.strategy`/`strategy_params` → Task 2; prove phase 3 → Task 7; `closers.py` (sequence, duper flag, three usages, pre-pass flag + disabled-in-comparisons) → Tasks 5/7/14; `sketch.py` (plan/skeleton/ledger holes/closers-first discharge/cheaper model/parallel search with serialized applier/assemble-verify/reopen/partial honesty) → Task 11; `bestfirst.py` (frontier, k proposals with self-scores, depth penalty + goal-size tie-break, node lessons, pickling with prefix+hash+generation, prune-on-loss, final-check authority) → Tasks 9–10; `parallel.py` (diversity, provisional winner pause/validate, flagged fallback, cancelled-reservation retention, shared meter, capability degradation) → Task 12; `lessons.py` (distill, cap drop-oldest, trajectory recording) → Task 4; `pickle.py` (copy-out to harness storage, docker-cp stage-in, no mounts, size cap, cleanup, generation refs, prefix replay before unpickle) → Tasks 8–9; `compare_strategies.py` (contemporaneous, dirty-tree refusal, seeded per-item randomized interleave, revision-mismatch invalidation, comparison record, predeclared rule + correction, model-override rejection, baseline implicit) → Tasks 13–14; testing strategy's unit/`lean`/`model` tiers → per-task tests, Task 15, Task 16.
2. **Known deferrals inside M7, recorded:** parallel winner's sibling event telemetry (Task 12 note); M4 generation-pinned leases consumed only via the `GenerationRefs`/factory seams (assumption 23 — inert until a snapshot carries a generation id); sketch's streaming applier + scoped re-critique is the M6-loop integration point (Task 11 note); `scripts/_compare_adapters.py` bound to landed M2 APIs at execution (Task 14, mirroring M1's `_default_client_factory` precedent).
3. **Type consistency.** `StrategyBudget`/`Reservation`/`BudgetSpent` flow Task 1 → 3/6/7/10/11/12; `ProveGoal.full_statement/splice` Task 2 → 5/6/10/11; `Verdict(passed, flags, detail)` Task 2 → 6/7/11/12; `MeteredRuntime.run(..., turns=, model=)` Task 3 → 4/6/10/11; `MeteredSession.check/tactic -> ... | None` Task 3 → 5/6/10/11; `SnapshotStore.save/restore` Task 9 → 10/15; `try_closers_state -> str | None` (tactic) vs `try_closers_goal -> str | None` (full source) Task 5 → 6/7/10/11; `decide_all`/`DecisionRule`/`ComparisonOutcome` Task 13 → 14/16; `session.replay/adopt_state/send_raw/base_env/container_name` Task 8 → 9/15.
4. **Placeholder scan.** Two deliberate execution-time-bound pieces, both explicitly contracted and test-pinned rather than TBD: `m6_ledger_port` (assumed M6 API named, port protocol is the real seam, `MemorySketchLedger` keeps every sketch test independent) and `scripts/_compare_adapters.py` (injected-callable signatures fully specified; `run_comparison` unit-tested against fakes; adapter exercised by the `model` tier). Everything else carries concrete code.
5. **Re-validation reminder:** the **Plan assumptions** section is the first thing to execute — diff every listed signature against the landed M1–M6 code before Task 1.

## Status

- [ ] Not started — plan awaits review gates and PR.
