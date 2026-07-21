# M2 — Evaluation Harness — Design Spec

**Milestone goal (DESIGN.md):** miniF2F runner, anti-cheat validation, metrics +
regression tracking.

**Exit criterion:** a reproducible baseline number for the M1 agent.

## Context: what M2 builds on

- M1's `AgentRuntime` + `RunConfig` + `Trajectory` (normalized event record with
  token/turn/wall-clock totals) and the Prove workflow phases.
- M0's `ReplPool`/`ProofVerdict` for verification, `sandboxed_worker_spec` for
  isolation.
- M1's minimal `#print axioms` audit (`hardy.workflows.audit`), which M2 extends
  into the full anti-cheat suite.

## Requirements (from DESIGN.md Component 8)

- Benchmarks provide statements **verbatim — never modified**; anti-cheat enforces
  this.
- Anti-cheat on every "solved" theorem: `sorry`-free; compiles against the
  *original* statement; `#print axioms` shows only the standard three (`propext`,
  `Classical.choice`, `Quot.sound`) — benchmark runs allow *no* paper axioms;
  suspicious closers (`native_decide`, `decide` on huge goals) flagged by scanning
  submitted source **and** the recorded tactic trajectory.
- Metrics: solve rate at fixed budget (pass@1, pass@k), cost per solved theorem
  (tokens + Lean CPU), wall clock, per-domain breakdowns.
- Regression tracking: every run logged with config hash *plus an immutable
  source/build revision* — config alone is insufficient provenance.
- Grading before M6: informal completeness is *not assessed*, never defaulted
  upward. Benchmark mode produces no writeups at all (pure benchmark mode is
  exempt from the output contract; DESIGN Component 8 requires the LaTeX check
  only "outside pure benchmark mode").

## Architecture

```
hardy/eval/
  benchmark.py    — BenchmarkItem, loaders (miniF2F first), held-out set support
  anticheat.py    — the full validation suite (extends M1 audit)
  runner.py       — orchestrates: items × attempts → verified results
  metrics.py      — pass@1/pass@k, cost, wall-clock, per-domain aggregation
  tracking.py     — append-only JSONL result store with provenance
scripts/run_eval.py — CLI entry point: config in, metrics + tracking entry out
benchmarks/minif2f/ — vendored statement set (pinned upstream revision, documented)
```

### `benchmark.py`

- `BenchmarkItem(id: str, statement: str, header: str, domain: str | None,
  split: Literal["valid", "test"])` — `statement` is the Lean `theorem` line(s)
  verbatim from the benchmark; `header` is the item's required imports/options
  block, also verbatim.
- `load_minif2f(path) -> list[BenchmarkItem]` — parses the vendored Lean 4 port of
  miniF2F. The vendored copy is pinned (upstream repo + revision recorded in a
  `SOURCE` file) so results are reproducible; updating the pin is an explicit,
  reviewed change.
- Held-out custom set: same loader contract (`load_custom(path)`), format is one
  file per item with a small YAML/JSON header. Creating the set's *contents* is
  ongoing work, not an M2 code deliverable; the loader and its tests are.
- PutnamBench/ProofNet: out of scope for M2 (post-baseline additions); nothing in
  the loader contract may assume miniF2F specifics.

### `anticheat.py`

`validate(item, submitted_source, trajectory, session) -> AntiCheatReport` — every
check is independent and all run (no short-circuit), so the report lists every
violation:

1. **Statement immutability.** M1's Prove splices the model's proof body into the
   harness-owned statement, so benchmark runs construct the checked source as
   `item.header + item.statement + body`. The check re-verifies the invariant
   defensively: the checked source must contain the benchmark statement byte-for-
   byte (modulo the documented splice point). Belt-and-suspenders, cheap, and it
   protects against future workflow drift.
2. **`sorry`-free.** The final verdict has no sorries *and* the source contains no
   `sorry`/`admit` token (comment/string-stripped lexical scan, not regex-in-place)
   — the kernel result is authoritative but the lexical scan catches smuggling into
   non-elaborated positions.
3. **Axiom audit.** `#print axioms` in the same environment: subset of the standard
   three, no `sorryAx`, and — benchmark mode — **zero** `Papers.*` axioms.
   (Frontier runs report the manifest instead; that path activates in M4.)
4. **Suspicious closers.** Lexical scan of source + scan of `run_tactic` calls in
   the trajectory for `native_decide` (always flagged — it trusts the compiler,
   not the kernel) and `decide` (flagged when the elaboration wall-clock for the
   check exceeds a threshold, as a proxy for "huge goal"). Flags are warnings
   attached to the result, not automatic failures; the runner surfaces them for
   human review and the metrics report counts them separately.

### `runner.py`

- `EvalConfig(run_config: RunConfig, attempts_per_item: int, item_timeout_s: float,
  parallelism: int, benchmark: str, split: str)` — pydantic, fully serializable
  (its canonical-JSON SHA-256 is the config hash).
- For each item × attempt: run the Prove workflow in **benchmark mode** — the
  formalize + faithfulness phases are *skipped* (the statement is given verbatim;
  formalizing it would violate immutability), the writeup phase is skipped, and
  the proof phase runs with the item's budget. Each attempt gets a fresh
  `ProofSession` (base-env isolation makes attempts independent by construction).
- Attempts for pass@k are independent samples: same config except the attempt
  index (recorded), no shared state, no cross-attempt context.
- Failure handling: an attempt that dies (worker crash, runtime error) is recorded
  as an unsolved attempt with its failure kind — never silently dropped, never
  retried outside the configured attempt count.
- Output: `EvalResult` per attempt (item id, solved, anti-cheat report, tokens,
  Lean wall-clock, total wall-clock, trajectory path), streamed to the tracking
  store as attempts finish (a crashed eval run keeps its completed attempts).

### `metrics.py`

- pass@1 and pass@k (unbiased estimator when attempts ≥ k), overall and per-domain.
- Cost per solved theorem: total tokens across *all* attempts (solved and not)
  divided by solves; same for Lean CPU seconds and wall clock — the denominator
  discipline is fixed here so later strategy comparisons (M7) are apples-to-apples.
- Anti-cheat summary: solves with flags reported as a separate line, never blended
  into the headline number.

### `tracking.py`

- Append-only JSONL at `eval_results/runs.jsonl`; one entry per eval run:
  timestamp, config hash, full `EvalConfig`, **git commit SHA of the harness**
  (refusing to log from a dirty working tree unless `--allow-dirty` explicitly
  overrides, in which case the entry is marked dirty), Lean toolchain + Mathlib
  pin, model identifier, metrics blob, and paths to per-attempt results.
- `scripts/run_eval.py --compare <run-id> <run-id>` renders a metrics diff — the
  regression check is *available* from day one; wiring it into CI is deferred
  until eval runs stop needing a live model.

## Key decisions and rationale

- **Benchmark mode skips formalization/faithfulness/writeup.** Alternative: run
  the full pipeline for realism. Rejected: DESIGN is explicit that benchmarks
  provide statements verbatim and that pure benchmark mode is exempt from the
  output contract; running the formalizer on given statements would actively
  violate anti-cheat.
- **Vendor the benchmark, pin the revision.** Alternative: fetch at runtime.
  Rejected: reproducibility is non-negotiable (Component 7), and upstream edits
  would silently change the baseline.
- **Suspicious closers flag, not fail.** `decide` is legitimate on small goals;
  auto-failing would bias the benchmark against honest proofs. Flags + separate
  reporting keep the headline number clean while preserving auditability.
- **Config hash + git SHA both required.** Straight from DESIGN.md: code changes
  behavior without changing configuration, so provenance needs both.

## Testing strategy

- **Unit:** loaders on fixture files; every anti-cheat check with hand-built
  positive/negative cases (statement tampering, smuggled `sorry` in a string vs. a
  comment vs. live code, `sorryAx` in axiom output, `native_decide` in trajectory
  but not source); metrics math against known tables (pass@k estimator edge
  cases); tracking round-trip + dirty-tree refusal (fake git via env/monkeypatch);
  runner orchestration with `FakeRuntime` and `fake_repl` (crash-mid-eval keeps
  completed attempts).
- **`lean`:** axiom audit and suspicious-closer wall-clock proxy against the real
  REPL; one real miniF2F item checked end-to-end with a canned correct proof body
  (no model).
- **`model`:** the actual baseline run (`scripts/run_eval.py`) — the exit
  criterion; run manually, results committed to `eval_results/`.

## Out of scope for M2

- PutnamBench and ProofNet loaders; building the held-out set's contents; writeup
  grading (benchmark mode has no writeups); paper-axiom manifests (M4); strategy
  comparison tooling beyond `--compare` (M7); CI-run evals; distillation/telemetry
  mining (Component 9 beyond what `Trajectory` already records).
