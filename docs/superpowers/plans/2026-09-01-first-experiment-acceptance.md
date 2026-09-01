# First experiment acceptance test — plan

**Goal:** Produce committed evidence that Hardy's honesty guarantees hold
against a real model, a real pinned Mathlib, and a real TeX toolchain on a
problem that needs more than one lemma — and a test that re-runs it.

**Spec:** the implementation brief for the first experiment acceptance test
(the "four runs" brief), read with `FEATURES.md` § First experiment
acceptance test.

## Decisions taken

- **#81 lands first, in this branch.** Results record the toolchain by
  revision: `lean.environment_identity` asks the Lean the run invokes for
  its version and commit; `writeup.tectonic_version` asks the binary; the
  installers pin one Lean release and one Mathlib tag and write the project
  from those pins; `hardy doctor` reports drift. What is not done: the TeX
  *package set* is pinned only through the Tectonic bundle digest, which is
  what the staged path compiles with.
- **The problem** is "sqrt 2 + sqrt 3 is irrational": one intermediate fact
  the model has to state (sqrt 6 is irrational, or that a rational's square
  is rational), one Mathlib lemma it has to find
  (`irrational_sqrt_natCast_iff` or `Nat.Prime.irrational_sqrt`), and the
  `Real.sqrt` algebra between them. Not in the set before; not a one-liner.
- **Multi-file saving is not exercised** by runs 1–4 and the test says so.
  Only the interactive session has `save_lean` with a path and the
  rebuild-dependents refusal; `batch` has four tools over one file and
  `prove` is single-file by construction. Exercising it means a recorded
  interactive run, which is a separate deliverable if wanted.
- **Run 3 is a batch run**, because the staged workflow grades every
  unverified run `partial` — which the brief calls a hard failure for a
  false claim. That is a finding about the loop, recorded in FEATURES.md,
  not changed here (#23 owns the loop).
- **The staged manifest states its spend** (`usage`, schema version 5)
  because the brief requires cost and the four counters present or null on
  every run, and version 4 left every staged run's usage empty.

## Steps

- [x] Toolchain in this environment: elan (Lean v4.33.1), a Lake project
      pinned to Mathlib v4.33.1 with the prebuilt cache, Tectonic 0.16.9,
      pdflatex for the doctor check.
- [x] #81: identity from the machine, pins in the installers, doctor drift
      check, docs.
- [x] Staged manifest `usage` from the runtime's ledger.
- [x] The problem in `acceptance/problems.json`, its packaged copy, and
      `examples/sqrt-two-plus-sqrt-three.json` for the batch surface;
      `hardy accept` no longer insists on exactly two problems.
- [x] `hardy accept --recorded DIR...`: `validate_batch_consistency` for a
      batch directory, `validate_run_consistency` plus the live-run
      obligations for a staged one. No model, no network, no toolchain.
- [x] `tests/integration/test_acceptance_live.py` behind `HARDY_LIVE=1`,
      four runs, artifacts kept under `HARDY_RECORD_DIR`.
- [ ] Run the four runs; keep the artifacts under `acceptance/recorded/`;
      a hermetic test that audits the committed copies and pins each run's
      terminal reason.
- [ ] `FEATURES.md` § First experiment acceptance test: what the runs showed,
      including anything that differed from the fake-process assumptions.
- [ ] Commit, push, PR.

## Out of scope

Growing the set beyond this problem (#102's precondition), comparing
configurations (#102), and any change to the loop (#23).
