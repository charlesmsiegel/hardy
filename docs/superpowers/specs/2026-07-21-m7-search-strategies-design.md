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
  config, budget: StrategyBudget, validate) -> StrategyResult`, where
  `validate: async (candidate_source, session) -> Verdict` is a
  **harness-owned downstream validator** (M1 axiom audit, plus anti-cheat in
  eval) injected into the seam: parallel strategies need it to hold sibling
  branches paused until a provisional winner passes validation, and without
  the callback they could only return the candidate (ending the invocation and
  killing the siblings) or hard-code the audit path themselves.
  - `ProveGoal`: the harness-owned statement + header (same operand Prove/eval
    already pass around).
  - `session_factory`: `() -> AsyncContextManager[ProofSession]` — the same
    cancellation-safe lease contract M1 requires of workflows: strategies
    acquire every session with `async with`, so a branch cancelled by a
    winner or deadline (parallel strategies cancel constantly) always returns
    or retires its worker — a factory handing out bare acquired sessions
    would let repeated parallel attempts drain the pool and deadlock later
    leases. The pool's size caps real concurrency.
  - `StrategyResult(proved: bool, source: str | None, verdict, budget_spent,
    events)` — events fold into the run's `Trajectory` so M2 metrics and
    telemetry see strategy-internal work (tactic proposals, subgoal outcomes).
- `StrategyBudget(tokens: int | None, turns: int | None, cost_usd: float |
  None, wall_clock_s: float, lean_cpu_s: float | None)` — one shared meter
  object covering **every budget dimension the run advertises**, decremented by
  every model call and Lean command the strategy makes. Turns and monetary
  cost are in the meter for the same reason tokens are: a strategy makes many
  inner `AgentRuntime.run` invocations (plan, skeleton, per-subgoal, branches,
  lesson summaries), and adapter-owned `max_turns`/`max_cost_usd` reset per
  invocation — each inner call therefore receives only the meter's *remaining*
  turn and cost allowances as its config, exactly as M1 does across Prove
  phases. **Wall clock is the exception: a monotonic run deadline, not an
  additive reservation** — three branches running through the same 10 seconds
  advance the theorem's wall time by 10, not 30, and reserve-settle accounting
  on it would spuriously serialize parallel work and rig the equal-wall-budget
  comparison against parallel strategies. Only genuinely additive dimensions
  (tokens, turns, cost, Lean CPU) go through reservations; the deadline is
  enforced **on in-flight work, not just at launch** — every model call, Lean
  command, and validator invocation runs wrapped in the remaining deadline
  and is cancelled at expiry, since a slow call started one second before the
  deadline would otherwise run its full provider timeout past the wall
  budget; strategies read `budget.remaining` to degrade
  (e.g. parallel attempts stop launching new branches below a threshold; sketch
  stops decomposing and tries direct closure). Spending is **reservation-based
  and atomic**: before each model call or Lean command, the enforcement layer
  reserves an allowance from the meter (an upper estimate for the call), then
  settles actual usage and refunds the difference — checking `remaining` and
  spending afterward would let concurrent branches all observe the same balance
  and collectively overshoot by work that clipping cannot recover, quietly
  breaking the equal-budget guarantee. A call whose reservation fails does not
  start, and when the remainder can no longer fund concurrent reservations,
  branch launches serialize. For Lean CPU the reservation is also **enforced
  during the command, not just settled after it**: the M2 CPU-sampling
  monitor kills a command at its reserved allowance (the existing timeout is
  wall-clock and cannot bound CPU) — otherwise a command launched with one
  CPU-second remaining could consume arbitrarily more before returning, and
  settlement can't recover spend that already happened. Enforcement lives in
  the meter/client layer, not in strategy code.
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
  agent turns; by best-first as free frontier expansion. **Strategy
  comparisons disable the global pre-pass in every arm**: with it on, goals
  the closers solve finish before strategy selection and the hybrid candidate
  can only re-run work already charged to it, so the comparison would measure
  nothing — the pre-pass *is* the hybrid strategy's implementation, and it
  competes as a strategy, not as ambient plumbing.

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
   can't parallelize). Parallelism applies to the *search*, not the
   bookkeeping: M6's blast-radius logic assumes serial patches, so discharge
   results are committed through a **single serialized applier** — proof
   bodies are found concurrently, but patch application, ledger transitions,
   and scoped re-critique run one at a time in a deterministic order;
   concurrent commits from stale document snapshots could lose a patch or
   grade the wrong ledger state.
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
  `pickleTo`/`unpickleProofStateFrom` commands, wrapped on `ProofSession`.
  Needed because a frontier outlives worker recycling and may migrate across
  workers (proof-state ids are per-process — the M1 limitation this milestone
  finally pays down). Pickles must survive the worker: a container's `/scratch`
  tmpfs dies with it (and is wiped between checks), so a pickle left there is
  gone exactly when recovery needs it. The wrapper therefore **copies each
  completed pickle out to harness-owned storage** (a per-run host directory
  with a size cap and end-of-run cleanup) immediately after `pickleTo` —
  streamed out of the sandbox the same tar-over-stdout way the TeX compiler
  returns artifacts — and stages it into the destination worker via a **trusted
  host-side copy into that worker's existing `/scratch`** (`docker cp`-style
  side channel against the named container, the same mechanism `reset_argv`
  already uses; size-capped) before `unpickleProofStateFrom`. Mount-based
  staging is not an option: warm pool containers already exist when the per-run
  pickle directory is created, and Docker cannot add a mount to a running
  container — so staged pickles are copied in, never mounted, and the copy
  lands in scratch the worker's between-check wipe already manages. A pickle restores the **proof state
  only** — not declarations, instances, or options the creating commands added
  to their environment — so each snapshot carries the **harness-owned
  declaration prefix** (the ordered commands that built its environment beyond
  base imports: skeleton `have`s, generated helper definitions, assumed-paper
  imports) plus a hash of that prefix. Migration replays the prefix on the
  destination lease and verifies the hash *before* `unpickleProofStateFrom`;
  transferring the pickle alone would make nodes referencing skeleton helpers
  fail or silently behave differently on a pristine worker. A node whose
  worker died unpickles on a fresh lease this way; nodes whose prefix replay
  or unpickle fails are pruned with the loss logged.
- Termination: goals empty at a node → assemble the tactic path, final
  `check_proof` verifies (search bugs must not ship unverified proofs); budget
  out → unproved, best partial path recorded.

### `parallel.py` — diverse parallel attempts

- n branches, each an independent inner strategy run (default: iterative
  repair) with diversity across branches: temperature schedule, prompt-template
  variant, and optionally strategy mix (`strategy_params.branches` lists
  per-branch overrides). Branches share nothing except the goal and the meter.
- The first `check_proof` success makes its branch the **provisional** winner:
  other branches *pause* (no new calls launched) while the candidate runs the
  full downstream validation — axiom audit and, in eval, anti-cheat. Only a
  fully validated, **unflagged** winner cancels the rest: a rejected candidate
  resumes the paused branches, and a candidate that validates but carries
  suspicious-closer flags (M2 classifies `native_decide` as a warning, not an
  automatic failure) also resumes them — it is retained as the *fallback*
  result if budget expires without an unflagged proof, so a flagged win never
  discards an in-flight clean one. (Cancellation
  itself is safe: workers are recycled by the pool's existing discipline.) A cancelled branch's
  **in-flight model call keeps its full reservation** unless provider-confirmed
  final usage arrives — task cancellation doesn't necessarily stop provider-side
  generation or return usage, so settling cancelled calls at zero would
  understate the parallel strategy's real spend. Budget is the *shared* meter
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
  list rather than running strategies back-to-back, **with the per-item
  strategy order randomized under a recorded seed** — a fixed round-robin
  would run the baseline in the same position every item, letting provider
  throttling, cache warmth, or drifting load correlate with strategy identity
  — so environmental drift can't masquerade as a strategy effect. Interleaving cannot
  defend against a provider repointing a mutable model alias mid-comparison,
  so the harness checks the **resolved immutable model revision** (M2 records
  it) across every linked run: a revision mismatch — or a provider that can't
  establish one — **invalidates the comparison** rather than merely being
  surfaced; a statistically significant "win" caused by a weights update is
  exactly what the exit criterion must not record.
- Emits one tracking entry per strategy plus a comparison record
  (`eval_results/comparisons.jsonl`) linking them: per-strategy solve rate,
  cost per solve, budget utilization — the logged per-strategy comparison the
  exit criterion names. The historical M2 number is never referenced.
- **A win needs statistical evidence, not a bigger point estimate:** with
  stochastic sampling on a small eval subset, *some* strategy will beat the
  baseline by luck. The comparison record therefore includes a **predeclared
  decision rule**, fixed in config before the run: paired per-item outcomes
  (each strategy vs. baseline on the same items), a paired test with a
  confidence interval (bootstrap over items, or McNemar for solve/no-solve),
  and a multiple-comparison correction across however many strategies were
  compared. The exit criterion's "beats iterative repair" means the corrected
  interval excludes zero — a point-estimate win records as *inconclusive*,
  and the honest response is more attempts/items, not a declared victory.
- **One model throughout:** the harness rejects `strategy_params` containing
  model overrides (e.g. sketch's `subgoal_model`) in exit-criterion
  comparisons — "same model" applies to nested strategy work, not just the
  outer config, or an apparent strategy win could actually be a model change.
  Heterogeneous-model configurations are a legitimate *separate* experiment
  axis, tracked as such, never the headline strategy comparison.

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
