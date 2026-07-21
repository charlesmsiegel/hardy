# M7 — Search Strategies — Design Spec

**Milestone goal (DESIGN.md):** sketch-and-discharge, best-first tactic search,
parallel attempts, cheap-closer pre-pass; strategy comparison on the eval set.

**Exit criterion:** at least one strategy beats iterative repair on solve rate at
equal budget — where iterative repair is **re-run contemporaneously** under the
same M7 code, model, environment, and eval configuration (never compared against
the historical M2 number) — with the per-strategy comparison logged in the
regression tracker.

## Context: what M7 builds on

- M1's Prove workflow with iterative repair hardcoded — M7 extracts the strategy
  seam and makes iterative repair the first implementation.
- M1's `ProofSession` (proof states, `run_tactic`) — best-first search lives on
  it; M0's `LeanRepl` pickling gap gets addressed here (see below).
- M2's eval runner/metrics/tracking — strategy is a new config axis; the
  comparison discipline (equal budget, contemporaneous baseline) is enforced by
  the runner.
- M5's capability flags — parallel strategies query `parallelism`-relevant
  capabilities and degrade to sequential on the minimal loop (every strategy must
  have a degraded-but-functional path there).
- M6's hole ledger — sketch-and-discharge's `sorry`s are planned holes discharged
  by the Repair machinery.

## Requirements (from DESIGN.md Component 4)

- Strategy is a **pluggable interface** so strategies can be benchmarked against
  each other.
- The four new strategies: sketch-and-discharge (informal plan → `have`/`sorry`
  skeleton → discharge subgoals independently, possibly parallel, possibly a
  cheaper model on easy subgoals); best-first tactic search (frontier of proof
  states, k tactic proposals per state, ranked); parallel attempts with diversity
  (n independent attempts, first verified proof wins; pass@k is the metric);
  hybrid automation (cheap closers — `simp`, `omega`, `aesop`, `exact?`, `duper`
  — before model tokens on any subgoal).
- Budgets: tokens, wall-clock, Lean CPU per theorem; strategies degrade
  gracefully as budget runs low.
- Context management: summarize failed attempts into distilled lessons, not
  replayed transcripts.

## Architecture

```
hardy/strategy/
  base.py        — Strategy protocol, StrategyBudget, StrategyResult
  iterative.py   — M1's loop, extracted behind the interface (the baseline)
  sketch.py      — sketch-and-discharge
  bestfirst.py   — best-first tactic search
  parallel.py    — diverse parallel attempts
  closers.py     — the cheap-closer pre-pass (used standalone and by others)
  lessons.py     — failed-attempt summarization
hardy/lean/pickle.py — proof-state pickling support (repl pickle/unpickle commands)
scripts/compare_strategies.py — the contemporaneous comparison harness
```

### `base.py` — the strategy seam

- `Strategy` protocol: `async prove(goal: ProveGoal, session_factory, runtime,
  config, budget: StrategyBudget) -> StrategyResult`.
  - `ProveGoal`: the harness-owned statement + header (same operand Prove/eval
    already pass around).
  - `session_factory`: `async () -> ProofSession` — strategies that parallelize
    lease multiple workers; the pool's size caps real concurrency.
  - `StrategyResult(proved: bool, source: str | None, verdict, budget_spent,
    events)` — events fold into the run's `Trajectory` so M2 metrics and
    telemetry see strategy-internal work (tactic proposals, subgoal outcomes).
- `StrategyBudget(tokens: int | None, wall_clock_s: float, lean_cpu_s: float |
  None)` — one shared meter object, decremented by every model call and Lean
  command the strategy makes; strategies read `budget.remaining` to degrade
  (e.g. parallel attempts stop launching new branches below a threshold; sketch
  stops decomposing and tries direct closure). Overspend is clipped by the
  meter, not trusted to strategy code.
- Registration: `RunConfig.strategy: str` + `strategy_params: dict` — config,
  not code, selects and parameterizes; the tracking entry records both.
- The Prove workflow's phase 3 becomes `strategy.prove(...)`; benchmark mode
  passes strategies through unchanged.

### `closers.py` — hybrid automation

- `try_closers(session, proof_state | goal, budget) -> ClosedResult | None`:
  attempts a fixed sequence (`simp`, `omega`, `aesop`, `exact?`; `duper` behind
  a config flag since it's an external dependency) with a small per-tactic
  timeout via the existing `run_tactic` timeout plumbing.
- Zero model tokens. Used three ways: standalone pre-pass before any strategy
  runs (config flag, default on); by sketch on every subgoal before spending
  agent turns; by best-first as free frontier expansion.

### `sketch.py` — sketch-and-discharge

1. **Plan:** an agent run writes the informal proof plan (the `note` tool
   persists it).
2. **Skeleton:** an agent run renders the plan as the harness statement with a
   `have`-decomposed body whose leaves are `sorry`; `check_proof` elaborates it;
   the resulting sorries (goal + proof-state id each) become **planned holes in
   the M6 ledger** (`layer="kernel"`, provenance `sketch`).
3. **Discharge:** each hole independently — cheap closers first, then a scoped
   agent run seeded with the plan, the subgoal's goal state, and relevant
   lessons; subgoals may run on a cheaper model (`strategy_params.subgoal_model`)
   and in parallel up to pool size (degrades to sequential when the runtime/pool
   can't parallelize).
4. **Assemble & verify:** discharged bodies splice into the skeleton; final
   `check_proof` on the assembled source is the only success authority (subgoal
   proofs that don't compose — e.g. metavariable leakage — reopen their holes).
   Failed subgoals after per-hole budget → strategy returns unproved with the
   partial skeleton recorded (a *partially formalized* result, honest by
   construction).

### `bestfirst.py` — best-first tactic search

- Frontier of `SearchNode(proof_state_id, goals, score, depth, tactic_path)` in
  a priority queue; expansion: cheap closers free, then one model call proposes
  k tactics with self-assigned scores; each is `run_tactic`-applied — successes
  become children (score: model score with a depth penalty; heuristic
  tie-breaks on goal-size decrease), failures are recorded as node lessons so
  re-proposals don't repeat them.
- **Proof-state pickling** (`hardy/lean/pickle.py`): the community repl's
  `pickleTo`/`unpickleProofStateFrom` commands, wrapped on `ProofSession`, with
  pickle files on the worker's sandbox scratch. Needed because a frontier
  outlives worker recycling and may migrate across workers (proof-state ids are
  per-process — the M1 limitation this milestone finally pays down). A node
  whose worker died unpickles on a fresh lease; unpicklable nodes are pruned
  with the loss logged.
- Termination: goals empty at a node → assemble the tactic path, final
  `check_proof` verifies (search bugs must not ship unverified proofs); budget
  out → unproved, best partial path recorded.

### `parallel.py` — diverse parallel attempts

- n branches, each an independent inner strategy run (default: iterative
  repair) with diversity across branches: temperature schedule, prompt-template
  variant, and optionally strategy mix (`strategy_params.branches` lists
  per-branch overrides). Branches share nothing except the goal and the meter.
- First kernel-verified success cancels the rest (cancellation is safe: workers
  are recycled by the pool's existing discipline); budget is the *shared* meter
  so n branches at budget B cost B total, not n·B — this is what makes the
  equal-budget comparison meaningful.
- Verification makes majority voting unnecessary (DESIGN); pass@k reporting
  comes from M2 as usual.

### `lessons.py` — distilled failure context

- After a failed attempt/branch/subgoal, one cheap summarization call produces a
  distilled lesson ("`ring` fails here because the goal isn't in a commutative
  ring") appended to the run's lesson list; strategies inject current lessons
  into subsequent agent runs instead of transcripts. Capped count with
  drop-oldest; lessons are recorded in the trajectory for later analysis.

### `compare_strategies.py` — the exit-criterion harness

- Takes one eval configuration + a strategy list (always implicitly including
  `iterative` as baseline); runs all strategies **contemporaneously** — same
  harness commit (refuses a dirty tree), same model, same environment, same
  item set, same `StrategyBudget`; interleaves strategy runs across the item
  list rather than running strategies back-to-back, so environmental drift
  (model updates mid-run, machine load) can't masquerade as a strategy effect.
- Emits one tracking entry per strategy plus a comparison record
  (`eval_results/comparisons.jsonl`) linking them: per-strategy solve rate,
  cost per solve, budget utilization — the logged per-strategy comparison the
  exit criterion names. The historical M2 number is never referenced.

## Key decisions and rationale

- **Shared budget meter object.** Per-strategy budget accounting would drift and
  make "equal budget" unfalsifiable; one meter, decremented at the model-client
  and session layer (not by strategy code), is enforceable.
- **Final `check_proof` as sole success authority in every strategy.** Search
  bookkeeping (best-first paths, sketch assembly) is never trusted — the same
  never-trust-the-loop principle as anti-cheat.
- **Pickling lands here, not M1.** It's real work with a real dependency (repl
  pickle commands + scratch-file plumbing through the sandbox) and only
  best-first needs it; M1 accepting state loss was the right cut.
- **Sketch holes go through the M6 ledger.** DESIGN says sketch is the
  degenerate case of planned holes; sharing the machinery gets discharge
  tracking, abandonment honesty, and reporting for free instead of a parallel
  bookkeeping system.
- **Interleaved comparison runs.** Cheapest defensible answer to "same
  environment": back-to-back runs invite time-correlated confounds the design
  explicitly worries about.

## Testing strategy

- **Unit:** strategy protocol conformance (all five, `FakeRuntime` +
  `fake_repl`); meter decrements/clipping/degradation triggers; closer sequence
  short-circuits; sketch subgoal→ledger wiring, non-composing subgoal reopens,
  partial-skeleton honesty; best-first queue behavior, lesson-on-failed-tactic,
  prune-on-unpicklable; parallel cancellation on first success + shared-meter
  totals; lessons cap; comparison harness refuses dirty tree, interleaves, and
  links tracking entries.
- **`lean`:** pickling round-trip across two real workers; closers actually
  closing trivial goals; a scripted sketch (canned skeleton) discharged and
  assembled through the real kernel.
- **`model`:** the exit criterion via `scripts/compare_strategies.py` on the
  eval subset.

## Out of scope for M7

- Learned scoring/value models for best-first (model self-scores only);
  premise retrieval (M8); cross-theorem memory (M8); hole-scheduling
  improvements in the M6 loop; distributed (multi-host) parallelism; `duper` as
  a default dependency.
