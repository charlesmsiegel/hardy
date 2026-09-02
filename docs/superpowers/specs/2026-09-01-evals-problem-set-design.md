# A fixed problem set with a measured automation floor

**Status:** design, awaiting review.
**Origin:** issue #102's precondition. `hardy accept` answers "does Hardy work" on
three problems and four recorded runs. Nothing answers "which Hardy works better",
and with three problems any delta is noise. The set and the comparison machinery
have to grow together, and the set has to come first.
**Related:** #102 (comparable conditions), #75 (cost and turns beside the
verdict), #77 (contemporaneous variants), #23 (the loop; out of scope here),
the acceptance plan `docs/superpowers/plans/2026-09-01-first-experiment-acceptance.md`.

## Problem

A solve rate over a list of theorems says nothing until the list says what a
solve is worth. Competition sets are heavily one-tactic or heavily trained on:
a model that closes forty of them with `exact?` has demonstrated that Mathlib
contains forty theorems. Hardy's ground truth cannot be gamed (the kernel and
the axiom audit decide what was proved), but the *difficulty* of what was
proved is today a matter of opinion.

Three further gaps in the current record:

- **Formalization cost is invisible.** `hardy batch` takes a Lean declaration
  and measures proving; `hardy prove` takes prose and measures formalization
  plus proving. Nothing runs both on the same statement, so the gap between
  them has never been measured.
- **Refusal is a single recorded case.** Run 3 of the acceptance test shows one
  false statement refused (`tests/integration/test_acceptance_live.py:386-430`).
  One case is a demonstration, not a rate.
- **Nothing aggregates across runs.** `hardy accept` accumulates a boolean
  (`src/hardy/cli.py:1285-1314`); no scoreboard, no per-tier figures, no file.

## Design in one paragraph

A committed problem list, each entry carrying the natural-language input, a
canonical Lean statement, and the expected verdict. A **baseline sweep**
that runs a fixed tactic set against every canonical statement under the
pinned toolchain and writes a **tier file**, stamped with the toolchain
identity. A **set runner** that takes the list, a model, and a configuration,
runs each entry through `batch` or `prove`, and writes one run directory per
row plus a **scoreboard**. A **validator** that extends `hardy accept
--recorded` so a committed scoreboard is checked the way the four acceptance
runs are: every row points at a run directory whose artifacts pass, and every
aggregate is recomputed from them. The headline number is the solve rate on
tiers 2 and 3, with the automation floor reported alongside.

## 1. The problem file

`evals/problems.json`, `schema_version: 1`, a list of entries:

```json
{
  "id": "odd-squares-sum-not-square",
  "input": "If a and b are odd integers, then a^2 + b^2 is not a perfect square.",
  "name": "OddSquaresSumNotSquare",
  "binders": "(a b : ℤ) (ha : Odd a) (hb : Odd b)",
  "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)",
  "imports": ["Mathlib"],
  "expected": "true",
  "twin_of": null,
  "source": "classical",
  "area": "number theory"
}
```

- `binders` and `conclusion` are separate so every consumer assembles the
  statement the same way and none parses Lean. The canonical declaration is
  `theorem {name} {binders} : {conclusion}`; the statement as a proposition is
  `∀ {binders}, {conclusion}` (or `{conclusion}` when `binders` is empty); its
  negation is `¬ ({proposition})`. Hypotheses live in binders, never as `→`
  in the conclusion, so single tactics see them in context.
- `expected` is `"true"` or `"false"`. A false entry is a **twin**: `twin_of`
  names the true entry it is a plausible neighbour of (a dropped hypothesis, a
  flipped inequality, a widened range). A true entry has `twin_of: null`.
- `source` is one of `textbook`, `classical`, `mathlib-gap`, `competition`.
  Competition entries are allowed and marked, so a reader can exclude them.
- `id` is a slug (`^[a-z0-9-]+$`), unique; `name` is a Lean identifier,
  unique across the list.
- The file validates on load (`pydantic`, `extra="forbid"`, house style).
  Twins must point at an existing true entry; a true entry may not point.

This list is not `acceptance/problems.json`. The acceptance test stays at one
problem and four recorded runs; this set grows independently.

## 2. The baseline sweep

`hardy evals baseline [--problems evals/problems.json] [--out evals/baseline.json]`

For every entry, and for every twin's negation, the sweep runs a fixed tactic
set against the canonical statement and records what happened. It runs Lean
through `lake env lean --json` in the configured project, the same way the
verifier does, so the identity it stamps is the identity a set run will be
checked against.

### 2.1 The tactic set

Two lists, module constants in `src/hardy/evals/sweep.py`, copied verbatim
into the baseline so a later edit to the code is visible as a stale baseline:

- **Singles:** `simp`, `simp_all`, `omega`, `decide`, `norm_num`, `ring`,
  `field_simp`, `linarith`, `nlinarith`, `positivity`, `tauto`, `aesop`,
  `grind`, `hint`, `exact?`, `apply?`. Not `polyrith`: it needs the network.
  `hint` is counted with the searchers because Mathlib registers `exact?`
  among the tactics it runs.
- **Chains:** short fixed sequences standing in for "a little tactic-level
  planning": `intros; simp_all`, `constructor <;> simp_all`, `simp_all; omega`,
  `norm_num; ring`, `norm_num; linarith`, `field_simp; ring`,
  `by_contra h; push_neg at h; nlinarith`, `intros; aesop`. The list is a
  decision, not a discovery; it is committed so the tiers are reproducible,
  and changing it re-tiers the whole set (§2.4).

### 2.2 Heartbeats first, wall clock second

"Quickly" measured in seconds flaps across containers. `maxHeartbeats` is close
to deterministic for one toolchain. Every attempt runs under a fixed budget,
`heartbeat_budget = 200000` (Lean's default, recorded in the baseline), and
records the heartbeats it used. Wall seconds are recorded too, but the tiers
are decided by heartbeats.

Nothing in the tree touches heartbeats today. The mechanism is Mathlib's
`#count_heartbeats in` (`Mathlib/Util/CountHeartbeats.lean:135`), which wraps
a command in `set_option maxHeartbeats 0`, so it cannot bound anything on its
own; an inner `set_option maxHeartbeats B in` overrides it, and the count is
still taken in the `finally`. Each attempt is therefore

```lean
#count_heartbeats in
set_option maxHeartbeats 200000 in
example (a b : ℤ) (ha : Odd a) (hb : Odd b) : ¬ IsSquare (a ^ 2 + b ^ 2) := by
  nlinarith
```

and the sweep reads `Used N heartbeats` from the information message on that
line. The file sets `set_option Elab.async false` at the top so counts are
attributable to their declaration. The kernel does not count heartbeats
(`decide` can spend minutes there), so every process also has a wall
backstop of `max(config.lean_timeout, 600)` seconds, as the live test's fresh
verifier does.

### 2.3 Two stages, so a closer cannot borrow a neighbour's proof

`exact?`, `apply?` and `hint` cite whatever is in the environment. A named
theorem closed by `simp` two lines up would be a valid citation for `exact?`
on the same statement, which is the false-credit problem `_assumption_probe`
already met (`src/hardy/chat.py:1312-1320`). So:

- **Stage A, one process per entry.** Every single and chain as an anonymous
  `example` with the entry's binders (nothing enters the environment, but
  the entry's own bound variables and hypotheses are in local context, the
  same way stage B's named `theorem` and the actual declaration present
  them). An attempt is a *candidate
  closer* when its lines carry no error, no `unsolved goals`, and no
  `declaration uses 'sorry'` warning. If the process hits the wall backstop,
  the sweep falls back to one process per remaining attempt for that entry,
  so one runaway tactic cannot mark the rest unknown.
- **Stage B, one process per candidate.** The candidate alone, as a named
  `theorem`, followed by `#print axioms`. It is **closed** when elaboration
  succeeds and the printed axioms are within `audit.STANDARD`. Stage B also
  gives each closer its own wall seconds. A candidate that fails
  confirmation is recorded as `unconfirmed`; that should not happen, and the
  baseline command says so on stderr, but it is a record, not an exit code.

Per attempt the baseline records: `status` (`closed`, `failed`,
`heartbeats_exhausted`, `timed_out`, `unconfirmed`), `heartbeats` (from the
count message, `null` when the process died), `seconds` (stage B only),
`axioms` (stage B only), `message` (the first error, truncated).

### 2.4 Tiers, decided by the floor

- **Tier 0:** a single other than `exact?`, `apply?` or `hint` closes it.
  Sanity checks; excluded from the headline.
- **Tier 1:** not tier 0, and `exact?`, `apply?` or `hint` closes it (`hint`
  runs `exact?`). The statement is essentially in Mathlib. Useful for testing
  search, not proving.
- **Tier 2:** no single closes it, a chain does. Tactic-level planning.
- **Tier 3:** nothing closes it. An intermediate statement is required; this
  is where `sqrt 2 + sqrt 3` sits and where the multi-file path matters.

Twins are tiered like everything else (a twin should land in tier 3, since
it is false) and additionally record `negation`: the same sweep on
`¬ (proposition)`. A twin whose negation closes is *mechanically false*, which
the scoreboard reports beside the refusal rate; a twin the sweep closes is
**true**, and a true entry whose negation closes is **false**. Either is a
problem-list bug: the baseline is still written, with `problems: [...]`
naming them, and the command exits 1. A set run refuses a baseline with
problems.

Before the sweep, every canonical statement is elaborated once with `sorry`.
A statement that does not elaborate is a list bug of the same kind.

### 2.5 The baseline file

`evals/baseline.json`, `schema_version: 1`:

```json
{
  "schema_version": 1,
  "created_at": "...",
  "problems_sha256": "<sha256 of evals/problems.json bytes>",
  "environment": {"lean_version": "...", "lean_commit": "...",
                  "mathlib_revision": "...", "lake_manifest_sha256": "...",
                  "imports": ["Mathlib"]},
  "heartbeat_budget": 200000,
  "wall_backstop_seconds": 600.0,
  "singles": ["simp", "..."],
  "chains": ["intros; simp_all", "..."],
  "host": {"platform": "...", "machine": "...", "cpu_count": 8},
  "problems": [],
  "entries": {
    "<id>": {
      "tier": 3,
      "elaborates": true,
      "attempts": {"simp": {"status": "failed", "heartbeats": 812, "...": "..."}},
      "closed_by": [],
      "negation": {"attempts": {"...": "..."}, "closed_by": ["nlinarith"]}
    }
  }
}
```

`environment` is `lean.environment_identity` verbatim, the same
`EnvironmentIdentity` every manifest carries, so equality is one comparison.
`host` is informational: wall seconds are not comparable across hosts and the
file says which one they came from. The baseline refuses to run when the
identity cannot be read, for the reason `runner.identify_toolchain` gives: a
tier file that names a Lean nobody verified is worse than none.

## 3. The set runner

```
hardy evals run --label <name> [--mode batch|staged] [--model M] [--backend claude|codex]
                [--repeats N] [--only id,...] [--tiers 2,3] [--include-twins/--no-twins]
                [--max-turns N] [--wall-seconds S] --acknowledge-unsafe-execution
```

Writes `evals/scoreboards/<label>/scoreboard.json` and, under
`evals/scoreboards/<label>/runs/`, one directory per row.

### 3.1 Refusals before anything runs

- The baseline's `problems_sha256` must equal the current `problems.json`
  bytes, its `singles`/`chains` must equal the code's constants, and its
  `problems` must be empty: a stale or broken baseline cannot tier a run.
- `lean.environment_identity` of the configured project must equal the
  baseline's `environment`, all four fields. Not advisory, unlike
  `doctor._toolchain_pin_check`: the whole point of the tier file is that a
  Mathlib upgrade can turn a tier-3 problem into an `exact?` one-liner
  overnight, so a run under a different Mathlib is not a run on this list.
- `--acknowledge-unsafe-execution` is required. The runner prints
  `runner.WARNING` once, the way the staged terminal makes a user type it,
  because AGENTS.md requires it never be left unsaid and a set run has no
  one to say it to.
- A label that already exists is refused (no `--force`; delete it yourself).

### 3.2 Modes

- **`batch`** (default): each entry becomes a `models.Request` from the
  canonical declaration and `input`, and goes through `runner.run` into
  `runs/<id>/batch-<k>/`. This measures proving.
- **`staged`**: each *true* entry's `input` goes through
  `build_prove_workflow(...).run(ProveRequest(...), terminal)` with
  `runs_root` set to `runs/<id>/staged-<k>/`, so the timestamped run
  directory lands there. The terminal is a non-interactive stand-in that
  acknowledges, approves the first proposal that elaborates, and never
  revises (the live test's `_ApprovingTerminal`, moved into the package). The
  row records `approval: "automatic"`. This measures formalization plus
  proving.

  After the run, the **canonical comparison**: a second independent reader
  session (the faithfulness reader's construction: no tools, its own thread,
  prompt and response schema written beside the verdict and hashed into it)
  is handed the model's frozen Lean signature and the canonical declaration
  and asked whether they are equivalent. It writes `canonical.json`,
  `canonical-prompt.md`, `canonical-schema.json` into `runs/<id>/staged-<k>/`,
  beside the nested run directory and outside the manifest's hash set, so the
  run's own record is untouched. Outcome `agreed`, `disputed`, `unavailable`.

  A twin is **not** run in staged mode. The staged workflow grades every
  unverified run `partial` (`src/hardy/workflow.py:500-514`); that is #23's
  finding and this design does not change the loop. Twins always run through
  `batch`, where the run 3 criterion applies. `--mode staged` with twins
  included runs the true entries staged and the twins batch, and the rows say
  which.

The gap between the batch and staged solve rates on the same entries is the
formalization cost. Runs are sequential; the SDK holds a subscription and
concurrency is out of scope.

### 3.3 Repeats

`--repeats N` (default 1) runs every selected entry N times; rows carry
`repeat: k`. One run per problem is a coin flip, and the aggregates report
per-run rates, not "any of N".

### 3.4 Outcome per row

Mechanical, from the run directory alone:

| expected | outcome | when |
|---|---|---|
| true | `solved` | batch: `terminal_reason == "verified"`, axioms clean. staged: phase `completed`, `kernel_verified`, **and** canonical comparison `agreed`. |
| true | `solved_other` | staged `kernel_verified` but canonical comparison `disputed` or `unavailable`: the model proved something, and the record cannot say it was this. |
| true | `unsolved` | anything else; `terminal_reason` beside it. |
| false | `refused` | the run 3 criterion: terminal in `{no_proof_submitted, axioms_rejected}`, no `submit_proof` with `ok`, every `check_proof` Lean accepted carried a hole. |
| false | `exhausted` | `turn_limit` or `wall_clock_limit`: Hardy stopped waiting, which is not a refusal. |
| false | `graded` | anything else, including `verified`. A harness bug, reported in red. |
| any | `invalid` | `validate_recorded_run` returned findings. |

Each row also carries: `id`, `tier`, `twin_of`, `mode`, `repeat`, `run_dir`
(relative to the scoreboard), `terminal_reason`, `cost_usd`, `exchanges`,
`turns`, `wall_seconds`, `lean_checks`, `search_calls`, `canonical`
(staged only), `approval` (staged only). `lean_checks` counts `check_proof` /
`submit_proof` events (batch) or `lean_check_proof` tool uses (staged);
`search_calls` counts `BATCH_SEARCH` / `STAGED_SEARCH` names, which move from
the live test into `acceptance.py` as module constants. Everything is a count
over the trajectory or a field of the manifest; nothing is estimated.

### 3.5 The scoreboard

`scoreboard.json`, `schema_version: 1`:

- `condition`: `model`, `backend`, `mode`, `staged_prompt_set_sha256`,
  `batch_prompt_set_sha256` (both always recorded: a twin runs `batch` even
  under a staged condition, §3.2), `hardy_version`, `source_revision` (the
  Git commit the run was made from, `-dirty` suffixed when the working tree
  had uncommitted changes, `None` when it could not be identified --
  `hardy_version` alone does not distinguish evals run from different source
  checkouts of the same release), `limits` (`max_turns`, `wall_seconds`),
  `repeats`, `selection` (`only`, `tiers`, `twins`).
- `environment`: the identity, equal to the baseline's.
- `baseline_sha256`, `problems_sha256`: the bytes this run was tiered by.
- `rows`: §3.4.
- `aggregates`: §4.
- `started_at`, `finished_at`, `interrupted: bool`. A run cut short by
  Ctrl-C writes the scoreboard with the rows it has and `interrupted: true`,
  so nothing is lost and nothing pretends to be complete.

A row is written after each run, not at the end: the file on disk is always
the rows so far.

## 4. Aggregates

Per tier (0–3) and per `expected`, over rows in the tier:

- `n`, `solved`, `solve_rate`, and a 95% Wilson interval on it. Thirty to
  fifty entries gives usable error bars; the interval says how usable.
- twins: `refused`, `exhausted`, `graded`, `mechanically_false` (count whose
  baseline `negation.closed_by` is non-empty), and `refusal_rate = refused / n`.
- `solved_other`, `invalid` counts, always shown.
- medians over **solved** rows: `exchanges`, `turns`, `cost_usd` (with the
  count of unreported costs beside it, never folded to zero), `wall_seconds`,
  `search_calls`, `lean_checks`.

Plus `headline`: solve rate and interval over tiers 2 and 3 together, and the
`floor`: how many entries each tier holds and how many of the whole list a
single tactic closes. The two are printed on one line so a tier-0 solve cannot
be mistaken for capability.

Nothing in the aggregate that is not a count or a median over the rows; the
validator recomputes all of it.

## 5. The validator

`hardy evals check evals/scoreboards/<label>` and, hermetically, a test that
walks every committed scoreboard and fails (not skips) on any finding, the way
`tests/integration/test_recorded_acceptance.py` does.

For each scoreboard:

1. `problems_sha256` and `baseline_sha256` match the committed files;
   the baseline's `environment` equals the scoreboard's, and the baseline's
   entries match the current problem list's ids (no extra, none missing).
2. Every row's `run_dir` exists and `validate_recorded_run` returns nothing.
   The existing dispatcher (`src/hardy/acceptance.py:938`) already handles a
   flat batch directory and a parent holding one staged run.
3. The run is the entry's: batch `trajectory.request.declaration` equals the
   assembled canonical declaration, `informal_claim` equals `input`, and
   `imports` equals the entry's; staged `request.md` equals `input`. Its
   recorded toolchain equals the scoreboard's `environment`. Once the audit
   passes, the row is also cross-checked against `condition`: batch's
   recorded model, backend, and turn/wall limits; staged's manifest model,
   prompt-set hash, and `active_seconds`/`proof_seconds`/`official_checks`.
   Only what each kind of record actually carries is checked -- a batch
   trajectory names no prompt hash and no per-check Lean timeout, so
   `condition.batch_prompt_set_sha256` and `condition.limits["lean_timeout"]`
   are not cross-checked against it.
4. Every derived field of the row (§3.4) is recomputed from the run directory
   and must be equal. The run 3 criterion moves from the live test into
   `acceptance.refusal_issues(run_dir)` so the validator and the test share
   one definition.
5. Staged rows: `canonical.json` validates; its `entry_id`,
   `canonical_declaration`, `model_signature`, and prompt are recomputed from
   the entry and the nested run's frozen claim rather than read back from the
   verdict that names them, and its schema hash matches
   `schema_text(CanonicalReview)`; its prompt and schema files still hash to
   what the verdict itself recorded of them; its `claim_sha256` equals the
   run's `manifest.claim_sha256`; and the row's `canonical` equals its
   outcome.
6. `aggregates` recomputed from `rows` must be equal.
7. Every entry in `selection` has `repeats` rows, present in the same order
   `run_set` would produce them, and there are no rows outside the selection.
   When `interrupted`, the rows must instead be an exact prefix of that
   order, not merely a subset of it -- otherwise a scoreboard could delete
   only its failed rows, mark itself interrupted, and pass with an inflated
   solve rate.

The scoreboard is covered by no hash, the same way a manifest is not; the
validator's job is that every figure in it is re-derivable from artifacts
that are.

## 6. Layout

```
evals/
  problems.json
  baseline.json
  scoreboards/<label>/scoreboard.json
  scoreboards/<label>/runs/<id>/batch-<k>/{result.json,trajectory.json,writeup.md,proof.lean}
  scoreboards/<label>/runs/<id>/staged-<k>/<timestamp>-<slug>-<id8>/...
  scoreboards/<label>/runs/<id>/staged-<k>/canonical{.json,-prompt.md,-schema.json}
src/hardy/evals/
  __init__.py
  problems.py    # schema, load, assemble declaration / proposition / negation
  sweep.py       # tactic lists, stage A/B, tiering, baseline file
  runner.py      # set runner, approving terminal, canonical comparison, scoreboard writing
  scoreboard.py  # row derivation, aggregates, validator
```

`.gitattributes` gets `evals/scoreboards/** -text` for the same reason
`acceptance/recorded/**` has it. The `cli.py` subcommand is thin dispatch,
like `accept`. Committed scoreboards are evidence and are committed with
their run directories; a label is one condition on one day.

## 7. Testing

- **Hermetic unit tests** with `tests/fake_lean.py` standing in for Lean:
  problem-file validation (bad slug, duplicate id or name, twin of a twin,
  missing twin target, a true entry with `twin_of` set);
  the count-message parser; tiering from synthetic attempt tables; stage A
  fallback on timeout; the refusal gates (stale baseline, drifted identity,
  non-empty `problems`, missing acknowledgement, existing label); row
  derivation for each outcome in §3.4 from small recorded runs (extending
  `tests/unit/test_recorded_runs.py`'s mutation style); aggregates and Wilson
  intervals on hand-computed cases; the validator's seven checks each broken
  one at a time.
- **Real-toolchain integration tests** (`real_toolchain`, skip without the
  pinned project): the twenty canonical statements elaborate; the sweep on
  two hand-picked entries lands them in the expected tier; `exact?` in stage
  A is not credited with a neighbour's proof.
- **The committed baseline** is checked hermetically for shape and for
  `problems == []`, and its entries are checked against `problems.json` (every
  id present, no extras).
- **Live** (`HARDY_LIVE=1`): one batch row and one staged row end-to-end,
  producing a scoreboard that `hardy evals check` accepts.

## 8. What is not here

- **Comparing two scoreboards** (`hardy evals compare`). Two scoreboards with
  equal `environment` and `problems_sha256` are comparable by construction;
  the delta report is the next piece and depends on having two.
- **Changing the loop** (#23): cheap closers before model turns, harness-owned
  `max_turns`. The list evaluates the loop as it is.
- **Growing the acceptance test.** It stays at one problem, four runs.
- **Concurrency** in set runs, and CI execution of live runs.
- **Automatic problem generation**, twin generation, or pulling from
  miniF2F/PutnamBench loaders (#73). Entries are written by hand and reviewed.

## 9. Where the first baseline is swept

The baseline is only as portable as the pin it is stamped with. The first
sweep runs on a project at the installers' pin (`leanprover/lean4:v4.33.1`,
Mathlib `v4.33.1`, Lean commit `819816b2`), the toolchain the four acceptance
runs were recorded under, so the tier file and the recorded evidence name one
environment. The machine this was designed on had drifted to `v4.33.0-rc1`
and was re-pinned by hand before any sweep; `hardy setup` and the installers
reuse an existing project without re-pinning it, which is a gap to record as
an issue, not a change this design makes.

## Appendix: the first twenty entries

Tiers below are *expectations*; the sweep decides. `Type*` needs Mathlib.

| id | input | name | binders | conclusion | expected | twin_of | source |
|---|---|---|---|---|---|---|---|
| two-plus-two | 2 + 2 = 4. | TwoPlusTwo | | `(2 : ℕ) + 2 = 4` | true | | textbook (tier 0 sanity) |
| sq-sum-ge-two-mul | For real x and y, 2xy ≤ x² + y². | SqSumGeTwoMul | `(x y : ℝ)` | `2 * x * y ≤ x ^ 2 + y ^ 2` | true | | textbook (tier 0 expected) |
| euler-polynomial-small | For every natural number n < 10, n² + n + 41 is prime. | EulerPolynomialSmall | | `∀ n < 10, Nat.Prime (n ^ 2 + n + 41)` | true | | classical |
| sqrt-two-irrational | The square root of 2 is irrational. | SqrtTwoIrrational | | `Irrational (Real.sqrt 2)` | true | | classical (tier 1 expected) |
| prime-order-cyclic | A finite group of prime order is cyclic. | PrimeOrderCyclic | `{G : Type*} [Group G] [Finite G] {p : ℕ} (hp : p.Prime) (h : Nat.card G = p)` | `IsCyclic G` | true | | textbook (tier 1 expected) |
| sqrt-six-irrational | The square root of 6 is irrational. | SqrtSixIrrational | | `Irrational (Real.sqrt 6)` | true | | classical |
| odd-sum | For every natural number n, the sum of the first n odd natural numbers is n². | OddSum | `(n : ℕ)` | `∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2` | true | | textbook |
| six-divides-consecutive | For every natural number n, 6 divides n(n+1)(n+2). | SixDividesConsecutive | `(n : ℕ)` | `6 ∣ n * (n + 1) * (n + 2)` | true | | textbook |
| exponent-two-abelian | A group in which every element squares to the identity is abelian. | ExponentTwoAbelian | `{G : Type*} [Group G] (h : ∀ g : G, g * g = 1) (a b : G)` | `a * b = b * a` | true | | textbook |
| sqrt-two-plus-sqrt-three | The real number √2 + √3 is irrational. | SqrtTwoPlusSqrtThree | | `Irrational (Real.sqrt 2 + Real.sqrt 3)` | true | | classical (tier 3 expected; the acceptance problem) |
| cube-root-two-irrational | A real number whose cube is 2 is irrational. | CubeRootTwoIrrational | `(x : ℝ) (hx : x ^ 3 = 2)` | `Irrational x` | true | | classical |
| odd-squares-sum-not-square | If a and b are odd integers, then a² + b² is not a perfect square. | OddSquaresSumNotSquare | `(a b : ℤ) (ha : Odd a) (hb : Odd b)` | `¬ IsSquare (a ^ 2 + b ^ 2)` | true | | classical |
| am-gm-two | For nonnegative reals x and y, √(xy) ≤ (x + y)/2. | AmGmTwo | `(x y : ℝ) (hx : 0 ≤ x) (hy : 0 ≤ y)` | `Real.sqrt (x * y) ≤ (x + y) / 2` | true | | textbook |
| sum-cubes-square | The sum of the first n cubes is the square of the sum of the first n integers. | SumCubesSquare | `(n : ℕ)` | `∑ i ∈ Finset.range (n + 1), i ^ 3 = (∑ i ∈ Finset.range (n + 1), i) ^ 2` | true | | classical |
| pigeonhole-residues | Among any n + 1 integers, two have the same remainder modulo n (n > 0). | PigeonholeResidues | `(n : ℕ) (hn : 0 < n) (f : Fin (n + 1) → ℤ)` | `∃ i j, i ≠ j ∧ f i % n = f j % n` | true | | textbook |
| sq-sum-le-two-mul | For real x and y, x² + y² ≤ 2xy. | SqSumLeTwoMul | `(x y : ℝ)` | `x ^ 2 + y ^ 2 ≤ 2 * x * y` | false | sq-sum-ge-two-mul | flipped inequality |
| euler-polynomial-all | For every natural number n, n² + n + 41 is prime. | EulerPolynomialAll | `(n : ℕ)` | `Nat.Prime (n ^ 2 + n + 41)` | false | euler-polynomial-small | widened range (fails at 40) |
| order-four-cyclic | Every group of order 4 is cyclic. | OrderFourCyclic | `{G : Type*} [Group G] [Finite G] (h : Nat.card G = 4)` | `IsCyclic G` | false | prime-order-cyclic | dropped primality (Klein four) |
| squares-sum-not-square | For integers a and b, a² + b² is not a perfect square. | SquaresSumNotSquare | `(a b : ℤ)` | `¬ IsSquare (a ^ 2 + b ^ 2)` | false | odd-squares-sum-not-square | dropped hypotheses (3, 4, 5) |
| sqrt-two-plus-sqrt-three-rational | The real number √2 + √3 is rational. | SqrtTwoPlusSqrtThreeRational | | `¬ Irrational (Real.sqrt 2 + Real.sqrt 3)` | false | sqrt-two-plus-sqrt-three | negation (acceptance run 3) |

Fifteen true, five twins. Expected shape after the sweep: two or three in
tier 0, two in tier 1, the rest split between 2 and 3, and every twin in tier
3 with a mechanically closed negation for at least the flipped inequality and
the widened range. If the sweep disagrees, the sweep is right and the
expectation column above is deleted.
