# The corpus: a classified, versioned problem set that can grow to thousands and tell a mathematician which model to use

## Problem

Hardy's problem set is twenty entries in `evals/problems.json`, and it was built
to answer one question: does the interactive slice work end to end. It answers
that. It cannot answer the question we now want, which is:

> Is Opus better than GPT at commutative algebra? Is Qwen better at real
> analysis? Which model should *I* use, for *my* field?

Four things stand between the current set and that question.

**There is no field.** `Entry.area` is `str` with `min_length=1`
(`problems.py:37`) and nothing aggregates on it. Its twenty values are
free-form English — `"number theory"`, `"group theory"`, `"sums"`,
`"analysis"` — assigned by whoever wrote the entry. `scoreboard.py` aggregates
by tier and by nothing else (`_tier_aggregate`, `aggregate`, lines 182-221).

**There is no comparison.** A scoreboard scores one run. "Opus vs. GPT on
commutative algebra" is a statement about two scoreboards, and no code reads
two.

**The set is too small, and the code cannot hold a large one.**
`ProblemSet._consistent` calls `seen.count(x)` inside a comprehension over
every entry, twice (`problems.py:85-89`) — quadratic, on every load. The public
`by_id` is a linear scan (`problems.py:104-108`). `baseline.json` keys its
staleness gate to `problems_sha256`, a hash of the whole file: at twenty
entries a typo fix costs one re-sweep, at five thousand it costs every
measurement in the project.

**Failure is ambiguous.** When no model proves a commutative-algebra theorem,
we cannot presently distinguish "models are weak here" from "Mathlib does not
have the prerequisite" — and the second is a per-field bias that lands
precisely on the axis we want to report.

## Goal

A problem corpus that:

- carries a real classification, so results slice by mathematical field;
- is a standalone, versioned, publishable dataset that outlives Hardy;
- holds thousands of entries without quadratic loads or whole-file staleness;
- separates *statements* (invariant) from *measurements* (provenance-bearing);
- can say "these two models are not distinguishable at this corpus size"
  instead of printing a leaderboard that isn't there;
- can eventually separate model weakness from Mathlib's coverage gaps.

**This is an instrument with two outputs, not a benchmark with one.** It
measures models, and it measures the formal library those models depend on.
The second has its own constituency: stated precisely, it asks *does Mathlib
cover the standard curriculum, field by field, level by level* — a claim a
Mathlib maintainer can act on, unlike "coverage gaps" in the abstract. The
schema below is shaped so the second output is a byproduct of collecting the
first rather than a separate effort.

This design does **not** deliver the corpus. It delivers the schema, the
taxonomy, the scaling fixes, the selection and reporting machinery, and the
reserved structure for fixtures. Authoring the problems is phase 3 and is
human work, per field. Nor does it deliver statistical power: at the sizes
contemplated here, many per-field comparisons will correctly report that they
cannot separate two models, and that is the design working, not failing.

## Decisions taken before design

Recorded because each one closes off alternatives the reader will wonder about.

- **Fields first, breadth later.** Four fields deep — MSC 13 (commutative
  algebra), 26/28 (real analysis and measure), 20 (group theory), 15 (linear
  algebra) — at roughly 125 entries each, rather than thin coverage of all 32
  arXiv classes.
- **MSC2020 is canonical; the arXiv class is derived.** MSC is hierarchical, so
  roll-up is a prefix operation and finer granularity costs nothing later.
  A flat arXiv label can never be refined without re-tagging.
- **No reference proofs.** The entry gate is mechanical plus a human
  faithfulness read (§2.2); nobody must be able to *prove* an entry for it to
  enter. Broken problems are expected.
- **Discrimination diagnoses; it does not filter.** An earlier form of this
  design had zero-discrimination items drop out of reporting, on the reasoning
  that a problem every model solves and one no model solves both carry no
  signal. The reasoning is right about signal and wrong about method: dropping
  items scored by the same models the report then compares selects on the
  dependent variable, which distorts the very solve rates and comparisons it
  feeds. It also destroys the corpus's most valuable property — a theorem
  retired because the first panel all failed can never reveal that a later
  model uniquely solves it, which is exactly the discovery the instrument
  exists to make. So low-discrimination items stay in, as declared difficulty
  strata; only items a human audit finds *broken* are retired, with a reason.
- **Textbook exercises may be harvested.** The mathematics is not
  copyrightable; `input` must be our own restatement, not the book's prose, and
  no solution-manual proofs are shipped. Provenance is recorded structurally
  (§2.1), which the antecedent policy in §9.0 then depends on.

## 1. The corpus is a directory, not a file

```
corpus/
  LICENSE                      # CC-BY-4.0
  SCHEMA.md                    # entry schema, id policy, taxonomy rules
  CHANGELOG.md                 # Keep-a-Changelog, entries cite ids
  sources.json                 # texts: title, edition, level, fields
  problems/
    13.json                    # sharded by MSC 2-digit class
    15.json
    20.json
    26.json
  fixtures/
    fixtures.json              # phase 3+; schema present from phase 1
  taxonomy/
    msc2020.json               # vendored official code list
    msc-to-arxiv.json          # versioned mapping table
  measurements/
    baseline-<mathlib-rev>-<host>.json
```

`evals/problems.json` and `evals/baseline.json` move here; `DEFAULT_PROBLEMS`
and `DEFAULT_BASELINE` re-point. Extracting the dataset for publication is
`cp -r corpus/` from the first commit onward — the A→B migration (code into its
own package) becomes a Python-only move with no data migration behind it.

**Sharding by MSC 2-digit class** is chosen now rather than later for three
reasons: re-sharding an existing corpus is a migration; per-field authoring
phases become independent working files rather than one contended file; and it
is the shape a published dataset wants anyway. The loader concatenates shards
and validates globally, so nothing downstream sees the split.

**The corpus holds statements only.** No tier, no discrimination, no solve
rate. Anything measured lives in `measurements/` keyed by entry id. This is the
single rule that keeps the dataset portable: a tier is a fact about one tactic
ladder against one Mathlib revision on one machine, not a property of a
theorem.

## 2. Entry schema

Added to `Entry`:

| Field | Type | Meaning |
|---|---|---|
| `msc` | `tuple[str, ...]`, **non-empty** | MSC2020 codes, primary first, validated against the vendored list |
| `arxiv_override` | `str \| None` | when the derived mapping is wrong (arithmetic geometry is MSC 14G but `math.NT`) |
| `override_reason` | `str \| None` | required when `arxiv_override` is set |
| `difficulty` | `Literal["routine","substantial","qualifying","research-adjacent"]` | human difficulty prior |
| `occurrences` | `tuple[Occurrence, ...]` | where this result appears; **first is primary**. Empty when authored |
| `status` | `Literal["candidate","active","retired"]` | lifecycle |
| `retired_reason` | `str \| None` | required when retired |
| `fixtures` | `tuple[str, ...]` | fixture ids; empty until phase 3, but digested from phase 1 |

Removed: `area`. The twenty existing entries are hand-mapped to MSC codes as
part of phase 1.

Never added: `tier`, `discrimination`, solve rates, or anything else measured.

New validators, in the spirit of `tier_must_follow_its_closers`
(`sweep.py:180`) — a schema that refuses states it cannot justify:

- `msc` is non-empty — the unknown-code validator succeeds vacuously on `[]`,
  which would leave an entry with no primary code, no shard, and no value for
  `field_of`;
- unknown MSC codes are rejected against the vendored list;
- `status == "retired"` requires `retired_reason`;
- `arxiv_override` requires `override_reason`;
- **a false twin inherits its target's primary MSC.** A twin is by construction
  in the same field as the statement it perturbs; letting the two drift is a
  bug that would silently move a result between fields.

### 2.1 Sources are first-class, and a result occurs in many of them

`corpus/sources.json` keys each text by `source_id`:

| Field | Meaning |
|---|---|
| `title`, `author`, `edition` | citation |
| `level` | `"first-course"` / `"advanced-undergraduate"` / `"graduate"` |
| `msc` | the field(s) the text covers |

An `Occurrence` is `(source_id, locator)`, where `locator` is a tuple of
integers — `(chapter, section, item)` — compared lexicographically.

**A result is one entry with many occurrences, not one entry per book.** The
Nullstellensatz is in most algebraic geometry texts; storing a copy per book
would duplicate the statement, split its measurements across ids, and make the
same theorem look like several problems in every aggregate. So `occurrences`
is a list, and the entry is the theorem.

**The first occurrence is primary and governs.** Everything that needs a single
answer — the `level` C6 stratifies on, the source the antecedent check runs
against — reads the primary. The rest are citations.

This matters because the alternative is worse than it looks. If the antecedent
check were existential over all occurrences ("prior in *some* book that has
both"), an author could reach for whichever text orders the material most
favourably, and fixture sets — and therefore the measured coverage gap — would
inflate with every book added. Since the antecedent policy sets the scale of
C6 (§9.0), the permissive reading would let corpus composition move the
headline number. The primary occurrence pins it.

**Occurrences are deliberately outside `statement_digest`.** Adding a citation
does not change what the theorem says, so it must not invalidate measurements.
Expect this list to be edited often as more texts are surveyed; that editing
has to be free.

Three things fall out:

1. **`level` stratifies the coverage-gap report** (§7 C6). A graduate text
   demands antecedents a first course never would; aggregating the two reads
   as a Mathlib coverage gap when it is a level difference. Stratified, the
   same data says something sharper and true: *"Mathlib covers first-course
   commutative algebra well and graduate-level poorly."*
2. **`locator` ordering makes the antecedent policy machine-checkable**
   (§9.0). A prose citation cannot be compared; `(3, 2, 12)` can.
3. **`len(occurrences)` is a canonicity signal, free.** A theorem in five of
   six standard texts is core curriculum; one in a single text is specialised.
   This is directly load-bearing for the claim in the Goal — *does Mathlib
   cover the **standard** curriculum* — so C6 reports core and peripheral
   coverage separately rather than pooling them. It is only meaningful once a
   field's texts have actually been surveyed for occurrences, which is
   per-field work in phase 3, not a property of the first entry written.

Corpus composition (phase 3) should where possible draw each field from **two
texts at different levels**. The within-field difference between them is
informative on its own and costs nothing beyond choosing the second book.

Semantic deduplication stays a human job: `corpus check` can flag entries whose
`conclusion` normalises identically, but it cannot recognise that two
differently-phrased statements are the same theorem. The `occurrences` list is
the mechanism for recording the merge once a human makes it.

### 2.2 Candidate to active is a human read

`status` distinguishes `candidate` from `active`, and only `active` entries
reach a headline number (§8). What promotes one was left undefined in an
earlier draft, which made the distinction decorative.

**Promotion requires a human to read the canonical Lean statement against
`input` and the primary occurrence, and record that they did.** The mechanical
gate establishes that a statement elaborates and how automation behaves; it
establishes nothing about whether the Lean proposition faithfully represents
the source problem. A mistranslated or silently weakened theorem passes every
mechanical check, discriminates between models, and lands in a field headline.
Auditing only the highest-discrimination items does not catch it, because a
faithfully-wrong statement need not discriminate unusually.

This is **not** the reference-proof gate that was considered and rejected — it
is a read, not a proof, and it does not require the reviewer to be able to
prove the theorem. Entries stay `candidate` and out of reporting until someone
does it, which makes the backlog visible rather than silent.

**Id permanence** falls out of the lifecycle: a retired entry stays in its
shard with `status: "retired"` rather than being deleted, so an id can never be
reused. But that is a *policy*, and validating uniqueness across the present
shards cannot enforce it: delete a retired row and the check sees only the new
claimant and accepts it, breaking every external citation. So ids are validated
against a machine-checked **tombstone registry** — an append-only record of
every id ever issued — not merely against the current corpus.

`difficulty` is the weakest part of this schema. Four levels is a guess, and
the vocabulary should be revisited after roughly fifty real entries are tagged
rather than defended. It is a *subjective per-problem* prior, and is deliberately
distinct from `level` below, which is an *objective per-source* fact — an easy
exercise can appear in a graduate text. Where the two disagree, `level` is the
one to report on, because it is auditable.

## 3. Versioning, digests, and what "stale" means

Two version numbers, answering two questions:

- `schema_version` (existing) — the *format*. Bumped when fields change.
- `corpus_version` (new, three-level) — the *content*.
  - **patch** — corrections that do not change membership: a broken statement
    fixed, a typo in `input`, a wrong MSC tag, an entry retired.
  - **minor** — additive: new entries.
  - **major** — breaking: schema change, id semantics, mass re-tagging.

`CHANGELOG.md` cites ids per change. Asserting only that `corpus_version`
equals the changelog head does not detect an *unversioned* edit — a shard can
change while both strings stay put and the test still passes, which makes a
published version non-reproducible. So the changelog head binds a **corpus
manifest digest** (a hash over every shard plus `sources.json` and the taxonomy
tables), and CI additionally diffs the corpus against the merge base and
requires the matching version and changelog entry when anything moved.

**Version numbers cannot express measurement validity, and must not be asked
to.** A patch that corrects one statement invalidates measurements for that one
id and leaves the rest perfectly good. So:

Each entry carries **two** digests, because Lean measurements and model
measurements are invalidated by different edits.

**`statement_digest`** — over `name`, `binders`, `conclusion`, `imports`, and
the **resolved content digests of every fixture, transitively**. This governs
the A-group (Lean-only) measurements. Digesting fixture *ids* would not be
enough: an edit to a referenced fixture's statement under a stable id changes
the assumptions of every dependent problem while leaving A4, A5 and B4
measurements apparently fresh. The id is a pointer; the digest must follow it.

**`prompt_digest`** — `statement_digest` plus `input`. This governs the B-group
(model) measurements. `input` is not decoration: `_batch_runner` passes it to
the model as `informal_claim` (`runner.py:252`), and staged runs use it as the
request. Rewording or correcting it can materially change solve behaviour, so a
model measurement taken against the old wording is stale even though the Lean
statement is untouched. Baseline staleness stays statement-only, which is why
one digest cannot serve both.

Every measurement record stores the digest that governs it. Staleness is a
per-entry comparison, not a per-file one — this replaces `baseline.json`'s
`problems_sha256`.

Two consequences, both load-bearing at scale:

1. **Sweeps become incremental.** Sweep only entries whose digest has no
   measurement or whose digest has changed. At 5,000 entries and ~24 tactic
   attempts of ~17s each, a full sweep is roughly 570 hours serially;
   incremental sweeping plus parallelism across entries is the difference
   between tractable and not.
2. **`fixtures` must be inside `statement_digest` from phase 1.** Adding it in
   phase 3 would re-invalidate every measurement in the project.

## 4. Scaling fixes (phase 1, not deferred)

- `_consistent`: replace the two `seen.count(x)` comprehensions with a
  `Counter`. Quadratic → linear.
- `ProblemSet`: build an id index once; `by_id` becomes a dict lookup.
- Loader: read and merge shards, validate uniqueness across all of them.
- Measurement files: keyed by entry id and digest, appendable per shard, so a
  sweep of one field does not rewrite the others.

## 5. Taxonomy (`evals/taxonomy.py`)

- `arxiv_of(msc) -> str` via the versioned mapping table.
- `field_of(msc) -> str` — 2-digit roll-up with a human label
  (`"13"` → `"Commutative algebra"`).
- `group_of(msc) -> str` — the **reporting group**, a versioned many-to-one map
  over 2-digit classes. This exists because the four planned fields are not
  four 2-digit classes: "real analysis and measure" is MSC 26 *and* 28. A bare
  2-digit roll-up would report five fields and split the ~125 analysis entries
  into two undersized samples, silently invalidating the per-field sizing and
  the power expectations built on it. Groups are versioned with the taxonomy
  so a regrouping is a visible, dated change rather than a drift.
- A test asserts every MSC code appearing in the corpus has a mapping, so the
  table cannot silently fall behind the corpus.

## 6. Selection, export, and the cost warning

Selection filters move into `select()` (`runner.py:129`) so every command
shares them: `--msc 13` (prefix match, catching `13A15`), `--arxiv math.AC`,
alongside existing tier/twin filters and new `--difficulty`, `--status`,
`--source` and `--level`. `--source` matches **any** occurrence, so "every
problem in Atiyah–Macdonald" works regardless of which text was primary;
`--level` reads the **primary** occurrence, because that is what C6 stratifies
on. The asymmetry is deliberate and is the one place a reader will expect these
two flags to behave alike.

A shared `describe_selection()` prints the count and an honest upper bound on
cost. The bound is **derived per row from the mode's own limits**, not from
`wall_seconds` alone: batch rows are governed by `max_turns`/`wall_seconds`,
but staged runs reject `--wall-seconds` outright and are governed by
`active_seconds`, `proof_seconds` and `official_checks`, while a twin inside a
staged condition runs batch under `twin_wall_seconds` (`runner.py:225`,
`runner.py:320-322`). A single-formula estimate has no valid value for a mixed
staged selection. `export` prints the bound; `run` additionally requires
`--yes` above a threshold, because that is where the money is spent.

`hardy evals export --msc 13 --format jsonl|shell|markdown` emits one prove
task per selected entry: `jsonl` for programmatic use, `markdown` for reading,
and `shell` as a **runnable wrapper script**. The shell format cannot be bare
`hardy evals run --only <id>` lines: `--label` is required
(`commands.py:72`), `run_set_command` refuses without
`--acknowledge-unsafe-execution` (`runner.py:293-296`), and an already-used
label is refused, so repeated lines need distinct ones. The wrapper supplies a
derived unique label per row and carries the acknowledgement once, at the top,
where a human reads it before running.

## 7. The evals

The full set of measurements this design plans. Grouped by what is being
measured, because they are three different kinds of thing.

### A. Lean/Mathlib measurements — no model involved

| # | Eval | What it measures | Status |
|---|---|---|---|
| A1 | Elaboration check | the statement typechecks | exists |
| A2 | Automation baseline sweep | 16 singles + 8 chains → tier 0–3 | exists |
| A3 | Negation sweep | ladder against `¬P`; catches sign errors and refutably-false statements | exists |
| A4 | Fixture consistency sweep | ladder against `False` with fixtures in scope | phase 3+ |
| A5 | Fixture strength sweep | ladder against the goal with fixtures in scope; closing means the fixture is too strong | phase 3+ |
| A6 | Non-vacuity check | hypotheses are satisfiable — a witness instance elaborates | new |

**A3 does not detect vacuity, and an earlier draft of this design wrongly said
it did.** If `P` is vacuously true because its hypotheses are impossible or
overstrong, then `¬P` is false and the ladder finds no closer — the sweep comes
back clean on exactly the broken entry it was supposed to catch. A3's real job
is sign errors and statements that are refutably false. Vacuity needs A6: an
explicit witness that the hypotheses are inhabited, or, where no witness can be
mechanically produced, the human faithfulness review in §2.2.

A2 is also the headline dataset artifact — "N undergraduate problems Lean's
automation cannot touch" — and is published as a measurement file carrying its
ladder, Mathlib revision and host, so a third party can recompute it against
their own ladder and disagree.

### B. Model conditions — one run each

| # | Eval | What it measures | Status |
|---|---|---|---|
| B1 | Bare prove | model proves the theorem against stock Mathlib. **The primary eval.** | exists |
| B2 | Twin (false statement) | model is given the false twin; correct behaviour is failing to prove it. Measures soundness and contamination | exists |
| B3 | Repeats / pass@k | variance across repeats of the same entry | exists (`--repeats`) |
| B4 | Fixtured prove | same theorem with fixtures in scope | phase 3+ |

B1 and B4 answer different questions and neither replaces the other. B1 is
*"can the model do this mathematics in Mathlib as it stands"* — the question a
mathematician choosing a model actually faces, since they must work in real
Mathlib. B4 is *"can the model do this mathematics"*.

### C. Derived reports — computed over runs, no Lean

| # | Report | What it produces |
|---|---|---|
| C1 | Per-field solve rate | Wilson interval per field over **item-level** outcomes, restricted to tier ≥ 2 and `status == "active"` |
| C2 | Paired model comparison | McNemar + CI per field; **refuses a ranking claim when the CI crosses zero** |
| C3 | Item discrimination | variance across models; feeds the spot-audit queue and difficulty strata — **never a filter on the scored corpus** |
| C7 | Ceiling and floor census | items no model solves and items all models solve, reported as strata rather than removed |
| C4 | Tier profile per field | how much of a field Mathlib's automation already covers |
| C5 | Contamination signal | twin **refusal** rate against true-statement solve rate; exhaustion reported separately |
| C6 | Fixture-assisted uplift | `B4 − B1` per field, **stratified by primary-source `level`**, core and peripheral reported apart |

C6 is the payoff of running both conditions, but it is named for what it
measures rather than what we hope it means. **`B4 − B1` is uplift, not
coverage.** A fixture can help because Mathlib lacks the result — the case we
care about — but equally because the model failed to *find* a route that
exists, because the fixture shortened the reasoning, or because it changed the
effective problem. A5 establishes only that the fixed ladder does not
immediately close the goal, not that the fixture supplies exactly one missing
library capability. Reading uplift as coverage requires a per-fixture causal
classification (does Mathlib in fact lack this?) that nothing here performs;
until something does, the headline is uplift and the coverage reading is an
interpretation stated as such. It is
reported **stratified by source `level`, never aggregated** — a graduate text
demands antecedents a first course would not, so an undifferentiated number
reads a level difference as a coverage gap. Stratified, C6 states the claim a
Mathlib maintainer can act on: which fields the library covers to first-course
standard, and which it covers to graduate standard. Core results (present in
most surveyed texts) and peripheral ones are reported apart for the same
reason — pooling them lets a gap in specialised material read as a gap in the
curriculum.

C5 must use the **refusal** criterion, not "failed to prove". Hardy already
distinguishes `refused` (terminal in `{no_proof_submitted, axioms_rejected}`)
from `exhausted` (`turn_limit` or `wall_clock_limit`), and records that a
timeout "is not a refusal" (`FEATURES.md:1237`). Since the kernel prevents any
clean proof of a genuinely false twin, collapsing the two would score a model
that blindly retries until timeout the same as one that recognises the claim is
false — which is precisely the signal C5 exists to read. Exhaustion is reported
beside it, never folded in.

## 8. Aggregation and comparison

`scoreboard.py` gains a `FieldAggregate` beside `TierAggregate`, reusing the
existing `wilson()` (`scoreboard.py:155`). Reporting is restricted to tier ≥ 2
and `status == "active"`: automation-solvable, candidate and retired entries
never reach a headline number.

**Repeats collapse to one item-level outcome before any interval or test.**
Scoreboards store one row per repeat and the existing aggregator counts rows,
so feeding rows straight into `wilson()` or McNemar treats correlated attempts
at the same theorem as independent samples: 125 problems at 10 repeats would
present as 1,250 observations, narrowing every interval by roughly a factor of
three and manufacturing ranking claims that the data does not support. Each
`(model, entry)` pair yields exactly one declared outcome — the rule for
declaring it is fixed per report and recorded — and the unit of analysis is the
item. Where repeat-level variation is itself the question (B3), it is reported
as a separate per-item statistic, not as extra sample size.

`evals/compare.py` is new, because comparison is a different concern from
scoring one run and `scoreboard.py` is already 622 lines.

`hardy evals compare <scoreboard>... --by field`:

1. Refuses scoreboards that differ in **any non-model experimental
   condition**, not merely in corpus and environment. `Condition` carries
   `backend`, `mode`, both prompt-set hashes, `hardy_version`,
   `source_revision`, `limits`, `repeats` and `selection` (`runner.py:35-57`);
   a staged Opus run at a larger budget against a batch GPT run would differ
   in most of them, and the whole difference would be attributed to the
   models. The paired item set must be identical too — same ids, same
   `prompt_digest` — since a comparison over different items is not a paired
   comparison at all.
2. Per field and model pair, computes the paired table (both / A-only / B-only
   / neither) and runs McNemar.
3. Reports the difference with a CI, and **refuses to emit a ranking claim when
   the CI crosses zero**. What it prints instead is a power curve under an
   explicitly stated effect-size assumption — not a bare "needs N more pairs".
   McNemar's significance depends on the *imbalance* between A-only and B-only
   outcomes, not on any fixed count: a handful of one-sided discordances can be
   significant while many evenly-split ones never are, so the additional pairs
   a field needs cannot be computed without assuming how they will split.
4. **Adjusts for multiplicity across the reported family.** Every model pair in
   every field, each gated independently at nominal 5%, makes at least one
   spurious ranking likely well before the four fields and a handful of models
   this design plans — which would defeat the honesty gate precisely where it
   is supposed to bind. A primary comparison is predeclared; everything else
   carries a family-wise or FDR adjustment applied to the intervals before the
   gate reads them.
5. Attaches the formalization-standard caveat to any cross-*field* level
   comparison, which (unlike within-field model comparison) does not cancel
   out differences in how carefully each field was formalized.

The refusal in (3) is the design's central honesty gate. At 125 entries per
field a two-model comparison yields on the order of 37 discordant pairs, short
of the ~70 needed **under the assumption that two thirds of them favour one
model** — the figure is an illustration under a stated effect size, not a
threshold that holds generally. The tool reports the curve and declines to
rank; it does not quote a universal number.

## 9. Fixtures (structure phase 1, behaviour phase 3+)

### 9.0 The antecedent policy

What counts as a reasonable antecedent sets the scale of the entire
coverage-gap measurement. If the rule varies by field, the cross-field
comparison inherits the inconsistency — and unlike the other confounds in this
design, this one is load-bearing for a headline result rather than a caveat on
one. So the rule is fixed, stated once, and applies everywhere:

> **An antecedent is a prior result from the same text — preferring the same
> chapter, reaching earlier only when needed — that Mathlib does not have.**

Three properties make this the right rule rather than merely a rule:

- **It is objective and auditable.** Anyone with the book can check whether it
  was applied. An abstract criterion ("standard background") could not be
  checked by anyone.
- **It mirrors how the problem was meant to be solved**, so what is measured is
  what a competent reader of that chapter should be able to do. That is a
  coherent target, and a more meaningful one than either extreme of "prove it
  from the axioms" or "assume everything up to the answer."
- **The Mathlib intersection keeps it small and makes it legible.** The
  antecedent set is not every prior result; it is only the prior results
  Mathlib lacks. So C6 measures exactly *how much of the standard curriculum up
  to this point Mathlib is missing* — the form of the claim a maintainer can
  act on.

Note the intersection is with the *text's* order, not Mathlib's: a book may
prove in chapter 2 something Mathlib derives much later, or vice versa. The
policy follows the book, because the book is what defines the reader's
competence at that point.

**This is mechanically enforced, not merely documented.** Because `locator` is
an ordered tuple (§2.1), `corpus check` verifies for every fixture attached to
an entry that the fixture has an occurrence in the entry's **primary**
`source_id` at a locator ≤ the entry's primary locator. A fixture reaching
forward in the text — assuming a later result to prove an earlier exercise — is
rejected, as is one that only appears in some other book. This is the invariant
that keeps the antecedent policy uniform across fields without depending on an
author's judgement or a reviewer's diligence.

The check runs against the primary occurrence specifically, not against any
occurrence. Both the entry and the fixture may appear in several texts, and an
existential over all of them would let the choice of books relax the policy
(§2.1). The primary is what the entry was harvested from, so it is the reader
whose competence is being modelled.

Its limit, stated plainly: the rule cannot make the *books* uniform. Textbook
difficulty varies and cannot be controlled for. That variance is a level
effect, not a per-model one — both models face the same book — so within-field
model comparison (the headline claim) is unaffected. It contaminates cross-field
claims, already the weaker statement, and it contaminates C6, which is why C6
is reported stratified by `level` rather than aggregated.

### 9.1 Mechanism

Two gaps need two mechanisms:

- **Missing lemma → a hypothesis binder.** `Entry.binders` already exists and
  is already injected. Sound by construction: a false hypothesis makes one
  theorem vacuous, it does not make the system inconsistent. This covers the
  majority of cases and needs no new machinery.
- **Missing definition or structure → a real preamble**, possibly including
  `axiom`. You cannot hypothesise a definition Mathlib lacks. This is the small
  dangerous case, and it is what `corpus/fixtures/` is for.

The guards already exist and are strict. `verifier.py:43` sets
`ALLOWED_AXIOMS = audit.STANDARD`; `sorryAx` and every non-standard axiom are
refused (`verifier.py:203-216`); `lean.py`'s `FORBIDDEN_TOKEN` bans a submitted
proof from declaring `axiom` or `opaque` at all. `verifier.py:202` records that
the eval path deliberately passes no approved assumptions: "a staged run is
nobody's place to widen the trust base."

A fixture set is therefore an explicit, **per-entry, corpus-declared** widening
of that allowlist. Three gates, all reusing the existing sweep:

1. Fixtures must not prove `False` (A4). Inconsistency is not decidable; this
   catches the ordinary blunder, which is the realistic failure.
2. Fixtures must not close the goal (A5). If the goal now falls to `exact?` or
   `aesop`, the fixture is too strong and the entry measures nothing.
3. An accepted proof's `#print axioms` must be a **subset** of
   `audit.STANDARD` plus that entry's declared fixture axioms — not equal to
   it. `#print axioms` reports only what a proof transitively uses, so an
   equality gate rejects ordinary valid proofs that happen not to need choice,
   and a fully constructive proof reports none of the standard three. Anything
   *outside* that set means the model widened its own trust base; refuse, and
   record the dependencies actually used.
4. Every fixture has an occurrence in the entry's **primary** `source_id` at a
   locator ≤ the entry's primary locator (§9.0). A fixture from later in that
   text, or absent from it entirely, is rejected.

Fixtures are shared and referenced by id — the same missing lemma blocks many
problems — and carry their own statements, checked as problems are. When
Mathlib later gains a lemma, the fixture retires, and *that retirement is a
datum*: the fixture library becomes a running record of what Mathlib lacks per
field, harvested as a byproduct.

**High discrimination is not evidence of validity.** A degenerate statement is
one a sharp model closes by exploiting the degeneracy while a careful model
grinds at the mathematics — it discriminates strongly and measures nothing.
So the top-discrimination items feed a **spot-audit queue** for human review
before they may carry a headline claim. That is a handful of entries, not 500.

## 10. Testing

Hermetic except where Lean is genuinely required (already gated behind
`--acknowledge-unsafe-execution`).

- taxonomy: every corpus MSC code maps; mapping table is well-formed
- schema: unknown MSC rejected; retired-without-reason rejected; override
  without reason rejected; **a twin whose MSC drifts from its target rejected**
- digests: editing `conclusion` changes the digest; editing `fixtures` changes
  the digest; editing `input` does not
- versioning: `corpus_version` matches the changelog head
- sharding: ids unique across shards; a duplicate across two shards is rejected
- scaling: a generated 5,000-entry corpus loads within a time bound (guards the
  quadratic regression)
- aggregation: known counts against hand-computed Wilson bounds
- `compare`: synthetic scoreboards with known discordance, asserting **both**
  the refusal path (CI crosses zero) and the claim path (it does not);
  mismatched-digest scoreboards refused
- vacuity: a deliberately vacuous entry is **not** caught by A3 (the negation
  sweep finds no closer, which is the point of §7's correction) and **is**
  caught by A6's witness check
- digests: editing `input` changes `prompt_digest` but not `statement_digest`,
  so B-group measurements go stale while A-group ones stay fresh; editing a
  referenced fixture's *statement* under a stable id changes both
- axioms: a constructive proof reporting none of the standard three is
  accepted; a proof reporting an undeclared axiom is refused
- lifecycle: a `candidate` entry never reaches a headline aggregate; promotion
  requires a recorded faithfulness review
- tombstones: reusing a deleted retired id is rejected against the registry
- clustering: 125 items at 10 repeats produce an interval computed from 125
  observations, not 1,250
- multiplicity: with several models over four fields, the adjusted gate refuses
  claims the unadjusted gate would emit
- compare: scoreboards differing in `mode`, `limits`, `repeats` or either
  prompt-set hash are refused even when corpus and environment agree
- versioning: a shard edited without a version bump fails CI's merge-base diff
- export: every emitted shell line runs as written — unique `--label`, and the
  acknowledgement present
- antecedent policy: a fixture at a locator *after* its entry's primary is
  rejected; a fixture occurring only in a non-primary text is rejected; a
  fixture whose *primary* is elsewhere but which occurs in the entry's primary
  at an earlier locator is **accepted**; locator tuples of unequal length
  compare lexicographically as intended
- occurrences: reordering `occurrences` changes which is primary and so can
  change the antecedent verdict; adding a citation does **not** change
  `statement_digest` and so does not invalidate measurements
- sources: every `source_id` in every occurrence exists in `sources.json`;
  every source carries a `level`; an entry with `occurrences` empty is treated
  as authored and is exempt from the antecedent check but also ineligible to
  carry fixtures
- C6: aggregating across levels is not reachable through the reporting API —
  the stratified form is the only one available

## 11. Phases

1. **Schema and scale.** Taxonomy module with reporting groups, vendored MSC
   list and mapping, `Entry` fields (including `fixtures`, reserved and
   digested), **both digests**, `corpus_version` with the manifest binding and
   changelog, tombstone registry, sharded layout, `Counter`/index fixes,
   migration of the existing twenty, `corpus check` and `corpus report`,
   A6's non-vacuity check, and the candidate→active review workflow (§2.2).
   No new problems.
2. **Reporting.** `FieldAggregate` over item-level outcomes, selection filters,
   mode-aware `describe_selection()`, `export` including the runnable wrapper,
   and `compare` with the refusal contract, condition equality and the
   multiplicity adjustment.
3. **Authoring.** The corpus itself, field by field: MSC 13, 26/28, 20, 15 —
   each entry through the faithfulness review before it is reportable. Fixture
   behaviour (A4, A5, B4, C6) lands here, on the schema slot reserved in
   phase 1.
4. **Feedback.** Discrimination, difficulty strata, ceiling/floor census, and
   the spot-audit queue. Requires at least three model runs before it means
   anything — and feeds audit, never a filter on the scored corpus.

Phase 1 precedes phase 3 deliberately: settling the tagging, digest and shard
layout before 500 entries exist is what prevents a hand re-tag.

## Risks

- **`difficulty` is a guess.** Expect to revise the vocabulary after ~50 tagged
  entries. Cheap to change while the corpus is small; a migration afterwards.
  `level` is the auditable fallback and is what C6 reports on.
- **Textbook difficulty cannot be controlled for.** The antecedent policy makes
  the *rule* uniform; it cannot make the books uniform. This is a level effect,
  so within-field model comparison is unaffected, but it contaminates
  cross-field claims and C6. Mitigated by stratifying C6 on `level` and by
  drawing each field from two texts at different levels — not eliminated.
- **Contamination is mitigated, not solved.** Twins detect memorised proofs
  applied to perturbed statements; they do not detect a model that genuinely
  learned the field from solution manuals. The twin ratio (currently 5 of 20)
  probably wants to rise, but the right number is unknown until C5 has data.
- **Statistical power stays the binding constraint.** Most per-field pairs will
  not separate at first. The honest report is the deliverable; the temptation
  to relax the refusal gate is the thing to resist.
- **Uplift is not coverage.** C6 measures fixture-assisted uplift. Reading it
  as a Mathlib coverage deficit needs a per-fixture causal classification that
  no eval here performs, and the spec labels it accordingly rather than
  quietly making the stronger claim.
- **The faithfulness review is the bottleneck and the weak link.** It is human,
  unaudited, and gates reportability for 500 entries. Nothing here measures
  reviewer agreement; a second reader on a sample would, and is not planned.
- **Fixtures widen the trust base.** Every gate in §9 is a mitigation, not a
  proof. A wrong fixture that is consistent and not too strong will silently
  mismeasure its entries, and only the spot-audit queue stands behind it.
