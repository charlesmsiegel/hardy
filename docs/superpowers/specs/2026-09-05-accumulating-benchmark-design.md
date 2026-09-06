# Accumulating benchmark: run a batch now, fold later batches into one score

Date: 2026-09-05

## Problem

The corpus holds 1166 entries, of which 11 are `status: active` (the vetted
commutative-algebra set) and 1155 are `status: candidate`. More candidates
become active as they are checked — Atiyah–Macdonald chapters 2–11 are still
to harvest. We want to benchmark Claude Haiku 4.5 (`claude-haiku-4-5`) against
the active set now, then keep adding a few dozen entries at a time and fold
each batch into one growing score, keeping every per-problem value for
re-aggregation.

Four things stand in the way today.

**A run is refused before it spends.** `run_set` computes `staleness` over the
whole corpus (`evals/runner.py:181-187`) before `select` narrows anything, and
`evals/baseline.json` covers 20 entries against the corpus's 1166. All three
staleness objections fire — missing statement digests, no procedure digest,
entries not matching the problem list. `--only` does not dodge this, because
the gate runs before selection.

**The baseline sweep is all-or-nothing.** `sweep()` iterates every entry in the
problem set, and `evals baseline` has no selection flag. Carry-forward spares
unchanged entries, but a newly harvested batch of 1146 would be swept in full
the first time.

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

Three mechanisms this design builds on rather than replaces.

**The baseline is already incremental.** `run_baseline` reads the existing
`--out` file as `prior` (`evals/commands.py`), and `sweep()` carries forward
every entry whose statement digest is unchanged, gated by `reusable()` on the
environment and procedure digests matching (`evals/sweep.py`).

**Staleness is already per entry.** `staleness` reports only on the ids its
caller passes as `statement_digests` / `problem_ids` (`evals/sweep.py`); its
docstring says why — "a whole-corpus hash would call every measurement stale
when one statement is corrected". The full-corpus demand is `run_set`'s choice
of arguments, not the gate's design.

**The run-affecting-code digest already exists, for the sweep.**
`procedure_digest_of` (`evals/sweep.py:280`) hashes `__version__`, the
line-ending-normalised bytes of a declared source set, the tactic set, and the
budgets. Its docstring states the reason: `__version__` is fixed at 0.1.0
across every checkout, so hashing the deciding modules is the only way to tell
that two measurements came from the same code. This design extends that
pattern to the results side.

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

**The source set is a denylist, not an allowlist.** `_run_source_digests()`
digests every `src/hardy/**/*.py` except an explicit exclusion list, using the
same line-ending normalisation `_digest_source` applies so a CRLF checkout of
one commit does not disagree with an LF one.

The exclusion list starts as:

- `hardy/__main__.py` — a console-script shim.
- `hardy/cas_driver.py` — reached by no run path.
- `hardy/evals/viewer.py` — the corpus review viewer.
- `hardy/tui/**` — the interactive shell.

An allowlist was tried first and rejected on evidence: a hand-written list
drawn from the obvious imports omitted `closers` (the closer ladder, which
decides whether a proof closes), `usage` (which computes the very token counts
being aggregated), and ten more — `audit`, `compaction`, `models`, `latency`,
`summary`, `cas`, `cas_tools`, `cas_export`, `storage`, `workflow`, `loop`,
`domain`, `codex_runtime`. A derived import closure was tried second and
rejected too: it reaches 79 of the package's 81 modules, because
`evals/runner.py` imports `runtime_factory` from `cli.py`, which is a hub.

The denylist inverts the failure mode. Omitting a deciding module becomes
impossible, because inclusion is the default; excluding one is a deliberate,
reviewable line that a reader can challenge.

The prompt *templates* stay separate keys rather than folding into the source
digest: `PROMPT_SET_SHA256` and `BATCH_PROMPT_SET_SHA256` are taken over
template text (`source("batch/system")`, `source("batch/task")`), and keeping
them distinct says which input moved when the digest changes. The `prompts/`
Python that renders and interpolates those templates is *not* covered by
either hash, and is picked up by the source denylist — which is the reason the
denylist is needed rather than a list of "the prompt files".

**Move the two run hooks out of `cli.py`.** `runtime_factory` (`cli.py:162`)
and `build_prove_workflow` decide how a run is executed, so `cli.py` would
otherwise have to be in the digest, and every edit to argument parsing for an
unrelated command would stale the pool and force a re-run of everything
already benchmarked. Move both into a small `hardy/wiring.py`, leaving
re-exports in `cli.py` so existing imports keep working, and exclude `cli.py`
from the digest. Behaviour is unchanged; this is a targeted improvement in
service of the pooling key, not a general refactor.

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

### 2. Selection: naming the batch, and defaulting to what is left

Selection is the primary interface. A control agent drives these commands and
can name exact entries, so the CLI must accept an explicit set precisely, and
must default to the obvious remainder when given none.

**The default selection is the unevaluated active entries** — entries at
`status: active` that carry no row under the current pooling key. For
`evals baseline` the analogous default is the active entries carrying no
baseline row. Neither command's default reaches a `candidate` entry.

"Unevaluated" is defined as *no* row for that id under the key. Topping up an
entry that has some but not all of `--repeats` rows is out of scope: the pool
already refuses a duplicate `(id, repeat)`, and a partial top-up would need
repeat numbering to be negotiated across scoreboards. An entry either has been
run under this condition or has not.

Establishing that set means reading the existing scoreboards. Both commands
take `--scoreboards` (already present on `run`) and consider every board under
it whose pooling key matches the one this invocation would produce; boards
under a different key are ignored, because their rows could not be pooled with
this run's anyway.

**Naming a set explicitly.** `--only <ids>` stays, and gains `--only-file
<path>` (one id per line, `-` for stdin) so a control agent can hand over a
few hundred ids without a command line long enough to hit the Windows limit.
`--status <name>` (repeatable) selects by status. Given together they
intersect. An id that names no entry is refused, as `select` already refuses
one today — a selection silently narrowed by ignoring a typo is the failure
this prevents.

**Seeing the set without running it.** `hardy evals todo` prints what the
default selection would be, as JSON on stdout: the unevaluated active ids, the
ids lacking a baseline row, the pooling key it computed, and the boards it
counted as already-evaluated. This is the control agent's read path, and it
spends nothing. Every refusal message from `run` and `baseline` names ids, so
the agent can act on them without parsing prose.

**Selection on the sweep.** `--status`, `--only` and `--only-file` narrow which
entries `sweep()` visits. Entries not selected simply have no baseline row;
carry-forward keeps the rows already there.

**Scope the gate to the selection.** In `run_set`, move `select` above
`staleness` and pass only the selected entries' ids, digests and expectations.
The gate then demands baseline coverage of exactly what is about to run,
which is what it exists to guarantee — a row's tier and its twin's
mechanical-falsity come from its own baseline entry, and an entry that is not
run needs none.

`select` reads `baseline.entries[entry.id].tier` only when a `--tiers` filter
is given (`evals/runner.py:154`). With a partial baseline that lookup can
miss, so it becomes a guarded lookup that refuses by name — "`--tiers` needs a
baseline row for: ..." — rather than raising `KeyError`.

**Record coverage honestly.** `floor` is computed over `baseline.entries`
(`evals/scoreboard.py:235-249`), so a partial baseline makes every floor a
floor over the swept subset. Two denominators are added, `floor["baselined"]`
and `floor["active_baselined"]`, so those numbers can be read against what was
actually swept.

This matters most for `floor["active_unwitnessed"]`, which counts only entries
that have a baseline row. It is a caveat about statements that rest on the
human read alone — A3 cannot see vacuity — and the spec requires it reported
rather than hidden (§7). A partial baseline would silently undercount it.
Undercounting a caveat is worse than undercounting a score.

The workflow becomes: promote a few dozen entries to active → `hardy evals
todo` to see what is outstanding → `hardy evals baseline` (sweeps the active
entries lacking a row) → `hardy evals run --label haiku-45-batchN` (runs the
unevaluated active entries) → `hardy evals pool haiku-45-*`. Each step accepts
an explicit id set when the control agent wants to name one instead. The
1146-entry sweep never happens; the first sweep is the 11 active entries,
about 530 elaborations.

### 3. `hardy evals pool`

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

### 4. Parallelism

**Budgets are frozen; workers are the only knob.** `wall_backstop_seconds` is
`max(config.lean_timeout, WALL_BACKSTOP_FLOOR)` = 600.0 at the default
`lean_timeout` of 180.0, and that value is an input to `procedure_digest_of`.
Raising budgets for headroom would invalidate the whole baseline
carry-forward, and — since `limits` enters `run_procedure_digest` — un-pool
every prior run. So `lean_timeout` stays 180.0 and the backstop stays 600.0,
chosen once and never changed.

On 32 cores this leaves ample headroom: contention would have to stretch a
tactic elaboration by roughly two orders of magnitude to move an attempt
between `closed` and `timed_out`.

Worker defaults, both overridable and both recorded:

- `hardy evals baseline --workers 8` — CPU-bound Lean elaboration.
- `hardy evals run --workers 4` — model latency is network-bound, but each row
  still spawns Lean.

Batch-at-a-time working makes this less urgent than it first appeared — a few
dozen entries is a short sweep — but a batch of 50 is still 2,400
elaborations, and run batches grow.

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

### 5. Totals

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
- editing an excluded module leaves the digest unchanged; editing any other
  module changes it;
- `evals baseline --status active` sweeps only active entries and carries the
  rest forward;
- a run selecting only baselined entries passes the gate while the corpus
  holds thousands of unbaselined ones;
- `--tiers` against an unbaselined entry refuses by name rather than raising;
- the default selection excludes entries already run under the current key,
  and includes them again once the key changes;
- the default selection reaches no `candidate` entry;
- `--only-file` and stdin accept the same set `--only` does, and a typo'd id
  is refused by name from either;
- `evals todo` emits parseable JSON and spends nothing;
- an interrupted parallel board's rows are a prefix of `select()` order, and a
  finished one is in exact order;
- token fields survive the `Row` round-trip;
- `evals check` re-derives the totals, and a tampered total is caught.

## Order of work

1. Move `runtime_factory` and `build_prove_workflow` into `hardy/wiring.py`,
   with re-exports; no behaviour change.
2. `run_procedure_digest`, its denylist source set, and its `Condition` field.
3. Token fields on `Row`; `totals` on `Aggregates`; `validate_scoreboard`
   re-derivation.
4. Selection on both commands (`--only`, `--only-file`, `--status`), the
   unevaluated-active default, `hardy evals todo`, scoped staleness in
   `run_set`, and coverage denominators on `floor`.
5. `hardy evals pool`.
6. `--workers` on both commands, with ordered-prefix writing.
7. Sweep the 11 active entries, run them under Haiku 4.5, and pool.

Steps 1–6 are offline and testable without spending model time or Lean hours.

## Out of scope

- Promoting candidates to active. That is corpus work, governed by the
  `ingest-corpus` skill.
- Changing what `evals check` verifies, beyond re-deriving the new fields.
- Any change to the tactic set, prompts or budgets — each would stale the
  baseline and un-pool prior runs, which is the mechanism working as intended.
- Splitting `cli.py` further than the two run hooks named above.
