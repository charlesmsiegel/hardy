# M2 — Evaluation Harness — Design Spec

**Milestone goal (DESIGN.md):** miniF2F runner, anti-cheat validation, metrics +
regression tracking.

**Exit criterion:** a finalized, reproducibility-complete baseline number for the
M1 agent on the usable miniF2F `test` corpus. “Finalized” means the run is
complete, every suspicious-closer result has been adjudicated, and the model,
corpus, harness, configuration, and worker environment all have immutable
identities.

## Context: what M2 builds on

- M1's `AgentRuntime` + `RunConfig` + `Trajectory` and the Prove workflow phases.
- M0's `ReplPool`/`ProofVerdict` for verification and
  `sandboxed_worker_spec` for isolation.
- M1's minimal `#print axioms` audit (`hardy.workflows.audit`), which M2 extends
  into the full anti-cheat suite.

## Decisions from the 2026-07-23 review

1. Suspicious-closer attempts are **provisional**, not certified solves, until a
   human adjudicates them.
2. Adjudications are append-only events recording run, attempt, flag digest,
   reviewer, timestamp, decision, and rationale. Corrections supersede earlier
   events without erasing history.
3. Pending and rejected attempts are excluded from headline pass@1/pass@k.
   Provisional upper-bound metrics are reported separately.
4. An official baseline requires a canonical, immutable model ID. An unpinned
   run remains useful exploratory data but cannot satisfy M2's exit criterion.
5. `valid` is for smoke tests and tuning; the full usable `test` split is the
   official baseline.
6. The corpus is the upstream Lean-4.15-compatible `minif2f_lean4.jsonl` export
   at tag `v4.15.0`, commit
   `638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f`, not mutable `HEAD` and not the
   monolithic `Valid.lean`/`Test.lean` files. The export has one portable header
   per record and an explicit `:= sorry` placeholder; at this pin it contains
   229 usable `valid` and 225 usable `test` records. Commented error records are
   excluded by an explicit manifest and never silently dropped.
7. Every live `decide` or `native_decide` occurrence in submitted source or a
   recorded tactic is flagged. Wall-clock time is too load-sensitive to decide
   whether a goal is “huge”; legitimate small uses are approved through the
   same adjudication path.
8. Per-domain metrics use a versioned, complete annotation manifest. The
   harness never guesses that a competition source such as IMO is a mathematical
   domain.
9. A partial run is retained for diagnosis but is ineligible as a baseline. M2
   is complete only after every configured item × attempt has a terminal record.
10. Run manifests survive interruption, and shared append-only logs use a
    crash-safe interprocess lock with tested stale-owner recovery.
11. Every response usage event must report the configured immutable model ID;
    a missing identity on any response makes the run unpinned.
12. Corpus identity includes the canonical exclusion records, not only the
    usable benchmark items.
13. A stale adjudication event never masks an earlier event that still matches
    the attempt's current flag digest; the latest matching event is effective.
14. The official corpus manifest and semantic digest are reviewed constants;
    self-consistent replacement metadata is still noncanonical.
15. Model immutability comes from an adapter-owned reviewed allowlist, not a
    naming regex. `claude-sonnet-5` remains approved as a documented snapshot.
16. Header imports and preamble retain exact bytes through reconstruction; no
    whitespace normalization is permitted in the hard check.
17. Matrix completion requires exact expected-key equality and uniqueness.
18. Every flagged attempt requires adjudication before finalization, including
    attempts that also fail a hard check.
19. Official worker provenance requires a valid image digest, at least one
    observed launch, and exact digest agreement for every launch.
20. Lean, Mathlib, and REPL pins must equal the complete approved mapping.
21. A lone CPU baseline without a follow-up sample uses the conservative bound.
22. A torn trailing JSONL record is ignored on read and truncated under the next
    exclusive append lock before new data is written.
23. The runner/adjudication module boundary remains runtime-acyclic and is
    covered by import-order regression tests.

## Requirements (from DESIGN.md Component 8)

- Benchmark statements and headers are preserved byte-for-byte after removing
  only the export's terminal `:= sorry` placeholder. Anti-cheat enforces this.
- Anti-cheat runs on every kernel-complete attempt: statement reconstruction,
  lexical `sorry`/`admit` scan, fail-closed axiom audit, and suspicious-closer
  scan of submitted source and recorded tactics.
- Benchmark runs permit only `propext`, `Classical.choice`, and `Quot.sound`;
  they permit no `sorryAx`, unexpected axioms, or `Papers.*` axioms.
- Metrics include certified pass@1/pass@k at fixed budget, provisional upper
  bounds, tokens and Lean CPU per certified solved theorem, makespan,
  utilization, failure kinds, and per-domain breakdowns.
- Every run records a canonical config hash plus immutable code/build, worker,
  model, toolchain, corpus, and annotation identities.
- Pure benchmark mode skips formalization, faithfulness review, and writeup.
  Informal completeness remains *not assessed* before M6.

## Architecture

```
hardy/eval/
  benchmark.py      — BenchmarkItem and corpus/custom loaders
  anticheat.py      — hard validation plus suspicious-closer flags
  adjudication.py   — append-only decisions and effective attempt status
  cpu.py            — in-flight Lean CPU sampling
  runner.py         — item × attempt orchestration and durable run manifests
  metrics.py        — certified/provisional metrics and cost aggregation
  tracking.py       — append-only run index, provenance, and comparison
scripts/
  vendor_minif2f.py — exact-pin corpus vendoring and integrity manifest
  run_eval.py       — run, adjudicate, finalize, and compare commands
benchmarks/minif2f/
  minif2f_lean4.jsonl
  domains.json
  EXCLUSIONS.json
  LICENSE
  SOURCE
```

### `benchmark.py`

- `BenchmarkItem(id, declaration_name, statement, header, domain, split)` holds
  the exact bodyless theorem declaration and exact portable header from one
  active JSONL record.
- `load_minif2f(path)` verifies `SOURCE` digests, parses every JSONL line,
  includes only records whose `formal_statement` begins with a live `theorem`,
  strips exactly one terminal `:= sorry`, and verifies the expected 229/225
  usable counts plus the exact excluded-ID manifest. Any drift fails closed.
- `domains.json` assigns every usable item one reviewed domain from a documented
  taxonomy. Missing, duplicate, or unknown annotations are corpus errors for an
  official run; custom loaders may use `unclassified` only for exploratory runs.
- `corpus_digest` covers IDs, declaration names, headers, statements, domains,
  splits, exclusions, and source revision. Corpus order does not affect it.
- `load_custom(path)` uses the same `BenchmarkItem` contract. PutnamBench and
  ProofNet remain out of scope, so consumers cannot assume miniF2F details.

### `anticheat.py` and `adjudication.py`

`validate(item, submitted_source, trajectory, session, *, winning_env, pool_imports) -> AntiCheatReport` runs every independent check without
short-circuiting:

1. **Statement immutability.** Independently reconstruct the checked source from
   the exact benchmark header/declaration plus a recorded proof body, and require
   byte equality. Containment is insufficient.
2. **`sorry`-free.** Require a complete kernel verdict and scan live Lean tokens
   for `sorry`/`admit`; comments and strings do not count. Malformed lexical
   input fails closed.
3. **Axiom audit.** Run `#print axioms` in the winning environment. Missing audit
   output or any non-standard/paper axiom fails the attempt.
4. **Suspicious closers.** Flag each live `decide`/`native_decide` occurrence in
   source and recorded `run_tactic` calls. Flags do not erase the hard-check
   result, but they make the attempt provisional.

Attempt status is one of `failed`, `provisional`, `certified`, or `rejected`.
Hard-check failure yields `failed`; a hard-pass with flags yields `provisional`;
a hard-pass without flags yields `certified`; adjudication promotes a
provisional attempt to `certified` or demotes it to `rejected`.

`eval_results/adjudications.jsonl` is append-only. Each event binds to the digest
of the complete flag set, so changing an attempt or its flags invalidates old
adjudications. The latest valid event per attempt is effective; history remains
visible. Finalization refuses pending flags.

### `runner.py`

- `EvalConfig` is fully serializable and its canonical JSON SHA-256 is the config
  hash. It includes the benchmark split, attempt count, all budgets, prompt/tool
  versions, and normalized provider request parameters.
- The worker image tag is resolved once to an immutable image digest; every
  initial and replacement worker launches by that digest. Mixed digests
  invalidate the run.
- Eligibility additionally requires at least one observed worker launch and
  exact equality between every observed image and the approved image digest.
- Model-generated Lean runs only in sandboxed workers. Direct workers are allowed
  only for trusted model-free tests and are never baseline-eligible.
- The runtime resolves the configured model through the provider. Official runs
  require a canonical model ID the adapter knows to be immutable; the resolved
  ID on every response must agree. Mutable aliases, missing identity, or mixed
  identities make the run exploratory/ineligible.
- Each item × attempt gets a fresh `ProofSession`. Formalization, faithfulness,
  writeup, cross-attempt memory, and unconfigured retries are disabled.
- Every configured attempt gets exactly one terminal result. A worker crash,
  timeout, or runtime error is an unsolved terminal result, not a dropped sample.
  An orchestrator/process interruption leaves the run manifest `incomplete` and
  therefore ineligible.
- Result count alone is insufficient: the observed item-attempt keys must be
  unique and equal the complete expected key set.
- A run-local manifest is written atomically at start and updated as attempt
  files land. Completed attempts and trajectories survive interruption.
- Lean CPU is sampled during execution. If teardown prevents a final sample, the
  attempt uses the last sample or a conservative elapsed × CPU-cap upper bound,
  marked estimated.
- A successful baseline sample without any successful follow-up is not a CPU
  measurement and uses the same conservative estimated upper bound.

### `metrics.py`

- Certified pass@1/pass@k use only `certified` attempts. `provisional` and
  `rejected` attempts are not solves. A separate provisional upper bound treats
  pending attempts as solves so reviewers can see the possible range.
- Per-solve costs divide resources spent across **all** configured attempts by
  unique certified solved items. Zero certified solves yields `null` cost fields
  plus an explicit marker, never a crash.
- Makespan is the latency metric; summed attempt time is named utilization.
- Per-domain metrics use `domains.json` annotations and include item counts so
  tiny categories cannot be mistaken for robust estimates.
- A report is `finalized` only when the run is complete and no provisional
  attempts remain.

### `tracking.py`

- Each run has an atomic local manifest; finalized summaries append to
  `eval_results/runs.jsonl`. Adjudications append separately. Both shared logs
  flush and fsync complete lines under a crash-safe portable lock.
- Readers recover all complete records before one torn trailing fragment; the
  next exclusive append truncates that fragment before appending.
- Provenance includes config hash/full config, clean Git SHA (or explicit dirty
  digest for exploratory runs), Lean/Mathlib pins, worker image digest, canonical
  model ID and observed response identities, corpus/annotation digests, metrics,
  and attempt paths.
- Finalized `RunRecord` exposes `baseline_eligible: bool` and
  `eligibility_reasons: list[str]`.
- Official-baseline eligibility is explicit and fail-closed: complete attempt
  matrix, all flags adjudicated, clean tree, reproducible sandbox worker, a
  response identity on every usage event with all responses reporting the same
  configured immutable model ID, and matching pinned corpus/toolchain.
  Corpus, domain, Lean, Mathlib, and REPL identities must equal the reviewed M2
  constants; nonempty arbitrary values are not sufficient.
- `--compare` refuses corpus or annotation mismatch, incomplete runs, and invalid
  runs. It surfaces model, image, dirty-tree, and finalization differences.

## Baseline protocol

1. Run unit and Lean tiers and pin the exact worker image.
2. Run a five-item `valid` smoke test; it is permanently labeled smoke data.
3. Freeze and record the complete baseline config, including
   `model="claude-sonnet-5"` (a canonical pinned model ID), budgets, prompt/tool
   versions, and attempt count.
4. Run the full 225-item usable `test` split without tuning on its outcomes.
5. Adjudicate every flagged attempt with recorded rationale.
6. Finalize only after eligibility checks pass; commit the run index,
   adjudications, attempt records, config, and the headline certified pass@1.
7. Verify self-comparison produces no warnings.

“Reproducibility-complete” means another operator can reconstruct the protocol
and environment. It does not promise bit-identical stochastic model samples;
provider seed support or its absence is recorded in the config.

## Key decisions and rationale

- **Certified rather than optimistic headline.** Counting pending flags as solves
  would publish a number before the required review had occurred.
- **Append-only adjudication.** Mutating attempts would destroy the audit trail;
  replacement attempts would change the configured sample.
- **Pinned model required.** A mutable alias cannot support the milestone's
  reproducibility claim. Unpinned runs remain visible but exploratory.
- **Pinned JSONL export.** The monolithic upstream files contain completed proofs
  and import an unvendored module; the tagged JSONL provides explicit portable
  headers and placeholder statements matching Hardy's Lean 4.15 pin.
- **Flag every `decide`.** Wall time is a host-load measurement, not a stable
  measure of goal size. Human adjudication preserves legitimate small uses.
- **Benchmark mode skips formalization/faithfulness/writeup.** The statement is
  already authoritative, and DESIGN exempts pure benchmark mode from writeups.

## Testing strategy

- **Unit:** exact-pin/count/exclusion/domain loader checks; statement and header
  byte-preservation; every hard anti-cheat check; all closer locations;
  append-only adjudication and supersession; certified/provisional metric tables;
  complete/incomplete manifests; crash-safe lock recovery; provenance refusal
  rules; comparison mismatch cases.
- **`lean`:** real axiom audit, small legitimate `decide` becoming provisional,
  and one pinned miniF2F item through the full model-free runner path.
- **`model`:** five-item `valid` smoke run followed by the full `test` baseline,
  adjudication, finalization, and self-comparison.

## Out of scope for M2

- PutnamBench and ProofNet loaders; creating the held-out custom set; writeup
  grading; paper-axiom manifests; strategy comparison beyond run comparison;
  CI model evals; automated suspicious-closer adjudication; telemetry mining;
  resuming an interrupted model run in place (its completed attempts remain
  diagnostic, but the official baseline is rerun from a new run ID).
