# Accumulating benchmark: run a batch now, fold later batches into one score

Date: 2026-09-05

## Problem

The corpus holds 1166 entries, of which 11 are `status: active` (the vetted
commutative-algebra set) and 1155 are `status: candidate`. More candidates
become active as they are checked — Atiyah–Macdonald chapters 2–11 are still
to harvest. We want to benchmark Claude Haiku 4.5 (`claude-haiku-4-5`) against
the active set now, and fold each later batch into one growing score, keeping
every per-problem value for re-aggregation.

Three things stand in the way today.

**A run is refused before it spends.** `run_set` computes `staleness` over the
whole corpus (`evals/runner.py:181-187`) before `select` narrows anything, and
`evals/baseline.json` covers 20 entries against the corpus's 1166. All three
staleness objections fire — missing statement digests, no procedure digest,
entries not matching the problem list. `--only` does not dodge this, because
the gate runs before selection.

**Nothing accumulates.** A scoreboard is "one condition on one day": `run_set`
refuses when `scoreboards_root / label` exists (`evals/runner.py:189`), and
`evals check` re-derives exactly one scoreboard. There is no notion of
combining two.

**Tokens are recorded but discarded.** Each run's `result.json` carries
`input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens` and
`cost_usd` (`acceptance.py:706`), but `batch_row` and `staged_row` keep only
`cost_usd`, `exchanges`, `turns` and `wall_seconds`, and `Aggregates` computes
medians (`MEDIAN_FIELDS`, `evals/scoreboard.py:30`) — never sums.

## What already works

Two mechanisms this design builds on rather than replaces.

**The baseline is already incremental.** `run_baseline` reads the existing
`--out` file as `prior` (`evals/commands.py`), and `sweep()` carries forward
every entry whose statement digest is unchanged, gated by `reusable()` on the
environment and procedure digests matching (`evals/sweep.py`). A harvest
therefore sweeps only the new entries. Only the *first* sweep over the 1146
uncovered entries is expensive.

**The run-affecting-code digest already exists, for the sweep.**
`procedure_digest_of` (`evals/sweep.py:280`) hashes `__version__`, the
line-ending-normalised bytes of a declared `DECIDING_SOURCES` set, the tactic
set, and the budgets. Its docstring states the reason: `__version__` is fixed
at 0.1.0 across every checkout, so hashing the deciding modules is the only
way to tell that two measurements came from the same code. This design extends
that same pattern to the results side.

`environment_digest_of` already covers `lean_version`, `lean_commit`,
`mathlib_revision` and `lake_manifest_sha256` (`domain.py`,
`EnvironmentIdentity`).

## Pooling criterion

An earlier per-problem result still counts in the pooled score when the model,
the prompts, Lean, Mathlib and the run-affecting Hardy code are unchanged. The
bar is that the pooled result must be indistinguishable from one long
sequential run made at a single moment, had every sample been ready and the
compute been available.

## Design

### 1. Run identity

Add a sibling to `procedure_digest_of` on the run side, in `evals/runner.py`:

```python
run_procedure_digest = digests.procedure_digest({
    "hardy_version": __version__,
    "source": list(_run_source_digests()),
    "staged_prompt_set_sha256": PROMPT_SET_SHA256,
    "batch_prompt_set_sha256": BATCH_PROMPT_SET_SHA256,
    "model": condition.model,
    "mode": condition.mode,
    "limits": condition.limits,
})
```

`RUN_DECIDING_SOURCES` names the modules that can change a run's outcome:
`evals/runner.py`, `evals/staged.py`, `evals/scoreboard.py` (row derivation),
`runner.py` (the prover loop), `lean.py`, `chat.py`, `claude_runtime.py` and
`acceptance.py` (grading). Digested with the same line-ending normalisation
`_digest_source` applies, so a CRLF checkout of one commit does not disagree
with an LF one.

The prompt templates are deliberately *not* in this list: they are already
covered by `staged_prompt_set_sha256` and `batch_prompt_set_sha256`, which are
separate keys in the same digest. Hashing them twice would add nothing and
would obscure which input moved when the digest changes.

The set is deliberately conservative: editing a comment in one of these
modules stales the pool. That is the same trade `procedure_digest_of` already
makes, for the same reason — the opposite error silently pools measurements
produced by different code.

`run_procedure_digest` is recorded on `Condition`.

**The pooling key is `(run_procedure_digest, environment_digest)`.**

Excluded from the key, each for a stated reason:

- `problems_sha256` — must drift; entries are being added by design.
- `baseline_sha256` — drifts as the baseline grows incrementally. The pool
  instead checks the baseline's own `environment_digest` and
  `procedure_digest`, which is the property that matters: `reusable()`
  guarantees any carried-forward tier was measured under a matching
  environment and procedure. Comparing the file hash would reject compatible
  baselines and would not, on its own, establish compatibility either.
- `source_revision` — kept as provenance, not identity. This is what allows a
  corpus-harvest commit, which cannot change how an entry is proved, to leave
  the pool intact.

### 2. `hardy evals pool`

A read-only command. It never mutates a scoreboard.

Given a set of labels it:

1. Validates each scoreboard with the existing `validate_scoreboard` — the
   audit is reused, not reimplemented.
2. Computes each board's pooling key and **refuses** if any two differ,
   naming the field that differs rather than reporting "incompatible".
3. **Refuses** on a duplicate `(id, repeat)` across boards. The same entry run
   twice under one condition is a fact to report, not a tie to break silently.
4. Runs the existing `aggregate()` over the union of rows, taking `active_ids`
   and the tier and floor denominators from the *current* corpus and baseline,
   and recording which corpus and baseline it used.
5. Writes `evals/pools/<name>/pool.json` holding the full per-problem table
   and the aggregate, and prints the headline.

The pool is a derived view, recomputable from the scoreboards at any time.

The workflow becomes: harvest → `hardy evals baseline` (sweeps only new
entries) → promote entries to active → `hardy evals run --label
haiku-45-batch2` → `hardy evals pool haiku-45-*`.

### 3. Parallelism

**Budgets are frozen; workers are the only knob.** `wall_backstop_seconds` is
`max(config.lean_timeout, WALL_BACKSTOP_FLOOR)` = 600.0 at the default
`lean_timeout` of 180.0, and that value is an input to `procedure_digest_of`.
Raising budgets for headroom would invalidate the whole baseline
carry-forward, and — since `limits` enters `run_procedure_digest` — un-pool
every prior run. So `lean_timeout` stays 180.0 and the backstop stays 600.0,
chosen once before the first sweep and never changed.

On 32 cores this leaves ample headroom: contention would have to stretch a
tactic elaboration by roughly two orders of magnitude to move an attempt
between `closed` and `timed_out`.

Worker defaults, both overridable and both recorded:

- `hardy evals baseline --workers 8` — CPU-bound Lean elaboration.
- `hardy evals run --workers 4` — model latency is network-bound, but each row
  still spawns Lean.

**Ordered-prefix writing** keeps the audit contract in `evals run`. Results
slot into an indexed array at their position in `select()` order; after each
completion the runner persists only the completed **contiguous prefix**. An
interrupted board is then a genuine prefix, satisfying the check at
`evals/scoreboard.py:409`, and a finished board is in exact run order,
satisfying `evals/scoreboard.py:421`. `evals check` needs no change and keeps
its full force.

The sweep needs no equivalent care: `sweep()` builds a dict keyed by entry id,
and the elaborate calls are independent — separate Lean processes over a
read-only project.

Every row records the `workers` value it ran under, so `wall_seconds` is
self-describing rather than a bare number a later reader mistakes for serial
time.

### 4. Totals

- Add `input_tokens`, `output_tokens`, `cache_read_tokens` and
  `cache_write_tokens` to `Row`, read from the same `usage` dict `cost_usd`
  already comes from in `batch_row` and `staged_row`.
- Add a `totals` block to `Aggregates`: each of the four token sums, summed
  `cost_usd`, summed `wall_seconds`, and a **coverage count** — how many rows
  contributed and how many held no value. `wall_seconds` is `None` on invalid
  rows, and a total that silently skips rows is worse than one that says how
  many it skipped.
- `validate_scoreboard` must re-derive the totals, since `evals check`
  re-derives every figure a scoreboard states; new fields left unaudited would
  be the one part of the scoreboard nobody checks.
- `evals pool` sums the same way across boards and labels the wall figure
  "measured under N-way concurrency", refusing to present it as a serial
  figure.

### Which numbers survive concurrency

Outcomes, token counts and cost are unaffected by worker count, given frozen
budgets with headroom; they are the headline figures. Summed `wall_seconds`
under N workers overstates serial wall clock by a contention-dependent amount
and is reported as such, never as "how long this would take serially".

## Testing

Test-driven, against the existing `tests/unit/test_evals_*.py`:

- two synthetic scoreboards with a matching key pool cleanly;
- a board whose `run_procedure_digest` differs is refused, and the message
  names the differing field;
- a duplicate `(id, repeat)` across boards is refused;
- an interrupted parallel board's rows are a prefix of `select()` order, and a
  finished one is in exact order;
- token fields survive the `Row` round-trip;
- `evals check` re-derives the totals, and a tampered total is caught.

## Order of work

1. `run_procedure_digest` and its `Condition` field.
2. Token fields on `Row`; `totals` on `Aggregates`; `validate_scoreboard`
   re-derivation.
3. `hardy evals pool`.
4. `--workers` on `evals baseline` and `evals run`, with ordered-prefix
   writing.
5. First full baseline sweep over the 1166-entry corpus.
6. The 11-entry Haiku 4.5 run, and the first pool.

Steps 1–3 are offline and testable without spending model time or Lean hours.

## Out of scope

- Promoting candidates to active. That is corpus work, governed by the
  `ingest-corpus` skill.
- Changing what `evals check` verifies, beyond re-deriving the new fields.
- Any change to the tactic set, prompts or budgets — each would stale the
  baseline and un-pool prior runs, which is the mechanism working as intended.
