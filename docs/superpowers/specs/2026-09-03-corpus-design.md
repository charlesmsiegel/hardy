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
- **No reference proofs.** The entry gate is mechanical. Broken problems are
  expected, and are filtered by discrimination: a statement no model can prove
  and one every model proves both carry zero signal and drop out of reporting.
- **Textbook exercises may be harvested.** The mathematics is not
  copyrightable; `input` must be our own restatement, not the book's prose, and
  no solution-manual proofs are shipped. Provenance is recorded as a citation.

## 1. The corpus is a directory, not a file

```
corpus/
  LICENSE                      # CC-BY-4.0
  SCHEMA.md                    # entry schema, id policy, taxonomy rules
  CHANGELOG.md                 # Keep-a-Changelog, entries cite ids
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
| `msc` | `tuple[str, ...]` | MSC2020 codes, primary first, validated against the vendored list |
| `arxiv_override` | `str \| None` | when the derived mapping is wrong (arithmetic geometry is MSC 14G but `math.NT`) |
| `override_reason` | `str \| None` | required when `arxiv_override` is set |
| `difficulty` | `Literal["routine","substantial","qualifying","research-adjacent"]` | human difficulty prior |
| `provenance` | `str` | `"Atiyah–Macdonald Ex. 3.12"` or `"authored"` |
| `status` | `Literal["candidate","active","retired"]` | lifecycle |
| `retired_reason` | `str \| None` | required when retired |
| `fixtures` | `tuple[str, ...]` | fixture ids; empty until phase 3, but digested from phase 1 |

Removed: `area`. The twenty existing entries are hand-mapped to MSC codes as
part of phase 1.

Never added: `tier`, `discrimination`, solve rates, or anything else measured.

New validators, in the spirit of `tier_must_follow_its_closers`
(`sweep.py:180`) — a schema that refuses states it cannot justify:

- unknown MSC codes are rejected against the vendored list;
- `status == "retired"` requires `retired_reason`;
- `arxiv_override` requires `override_reason`;
- **a false twin inherits its target's primary MSC.** A twin is by construction
  in the same field as the statement it perturbs; letting the two drift is a
  bug that would silently move a result between fields.

**Id permanence** falls out of the lifecycle: a retired entry stays in its
shard with `status: "retired"` rather than being deleted, so an id can never be
reused. External consumers can cite an id forever.

`difficulty` is the weakest part of this schema. Four levels is a guess, and
the vocabulary should be revisited after roughly fifty real entries are tagged
rather than defended.

## 3. Versioning, digests, and what "stale" means

Two version numbers, answering two questions:

- `schema_version` (existing) — the *format*. Bumped when fields change.
- `corpus_version` (new, three-level) — the *content*.
  - **patch** — corrections that do not change membership: a broken statement
    fixed, a typo in `input`, a wrong MSC tag, an entry retired.
  - **minor** — additive: new entries.
  - **major** — breaking: schema change, id semantics, mass re-tagging.

`CHANGELOG.md` cites ids per change; a test asserts `corpus_version` matches
the changelog's top entry.

**Version numbers cannot express measurement validity, and must not be asked
to.** A patch that corrects one statement invalidates measurements for that one
id and leaves the rest perfectly good. So:

Every entry carries a **`statement_digest`** — a hash over `name`, `binders`,
`conclusion`, `imports`, and `fixtures`. Every measurement record stores the
digest it was taken against. Staleness is then a per-entry comparison, not a
per-file one.

This replaces `baseline.json`'s `problems_sha256`. Two consequences follow, and
both are load-bearing at scale:

1. **Sweeps become incremental.** Sweep only entries whose digest has no
   measurement or whose digest has changed. At 5,000 entries and ~24 tactic
   attempts of ~17s each, a full sweep is roughly 570 hours serially;
   incremental sweeping plus parallelism across entries is the difference
   between tractable and not.
2. **`fixtures` must be inside the digest.** Changing a fixture changes what
   the problem means, so it has to invalidate measurements exactly as editing
   the conclusion does. This is why the field is reserved in phase 1 rather
   than added in phase 3 — adding it to the digest later re-invalidates every
   measurement in the project.

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
- A test asserts every MSC code appearing in the corpus has a mapping, so the
  table cannot silently fall behind the corpus.

## 6. Selection, export, and the cost warning

Selection filters move into `select()` (`runner.py:129`) so every command
shares them: `--msc 13` (prefix match, catching `13A15`), `--arxiv math.AC`,
alongside existing tier/twin filters and new `--difficulty` and `--status`.

A shared `describe_selection()` prints the count and an honest upper bound on
cost — `N × repeats × wall_seconds`, rendered as "up to 41 hours". `export`
prints it; `run` additionally requires `--yes` above a threshold, because that
is where the money is spent.

`hardy evals export --msc 13 --format jsonl|shell|markdown` emits one prove
task per selected entry: `jsonl` for programmatic use, `shell` for literal
`hardy evals run --only <id>` lines, `markdown` for reading.

## 7. The evals

The full set of measurements this design plans. Grouped by what is being
measured, because they are three different kinds of thing.

### A. Lean/Mathlib measurements — no model involved

| # | Eval | What it measures | Status |
|---|---|---|---|
| A1 | Elaboration check | the statement typechecks | exists |
| A2 | Automation baseline sweep | 16 singles + 8 chains → tier 0–3 | exists |
| A3 | Negation sweep | ladder against `¬P`; catches vacuity and sign errors | exists |
| A4 | Fixture consistency sweep | ladder against `False` with fixtures in scope | phase 3+ |
| A5 | Fixture strength sweep | ladder against the goal with fixtures in scope; closing means the fixture is too strong | phase 3+ |

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
| C1 | Per-field solve rate | Wilson interval per field, restricted to tier ≥ 2 and `status == "active"` |
| C2 | Paired model comparison | McNemar + CI per field; **refuses a ranking claim when the CI crosses zero** |
| C3 | Item discrimination | variance across models; feeds retirement and the spot-audit queue |
| C4 | Tier profile per field | how much of a field Mathlib's automation already covers |
| C5 | Contamination signal | twin-failure rate against true-statement solve rate |
| C6 | Mathlib coverage gap | `B4 − B1` per field |

C6 is the payoff of running both conditions: it separates model weakness from
Mathlib's coverage deficit, which is the confound that otherwise poisons every
cross-field comparison. It is also a result the Mathlib community can use.

## 8. Aggregation and comparison

`scoreboard.py` gains a `FieldAggregate` beside `TierAggregate`, reusing the
existing `wilson()` (`scoreboard.py:155`). Reporting is restricted to tier ≥ 2
and `status == "active"`: automation-solvable and retired entries never reach a
headline number.

`evals/compare.py` is new, because comparison is a different concern from
scoring one run and `scoreboard.py` is already 622 lines.

`hardy evals compare <scoreboard>... --by field`:

1. Refuses scoreboards that disagree on corpus digest set, baseline, or
   environment. Comparing runs measured against different statements is the
   failure mode most likely to produce a confident wrong answer.
2. Per field and model pair, computes the paired table (both / A-only / B-only
   / neither) and runs McNemar.
3. Reports the difference with a CI, and **refuses to emit a ranking claim when
   the CI crosses zero** — printing instead how many further discordant pairs
   the field needs.
4. Attaches the formalization-standard caveat to any cross-*field* level
   comparison, which (unlike within-field model comparison) does not cancel
   out differences in how carefully each field was formalized.

The refusal in (3) is the design's central honesty gate. At 125 entries per
field a two-model comparison yields on the order of 37 discordant pairs against
the ~70 a significance claim needs; the tool must say so rather than rank.

## 9. Fixtures (structure phase 1, behaviour phase 3+)

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
3. An accepted proof's `#print axioms` must show exactly the standard three
   plus that entry's declared fixtures. Any other axiom means the model widened
   its own trust base; refuse.

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
- corpus check: a deliberately vacuous fixture entry is caught by A3

## 11. Phases

1. **Schema and scale.** Taxonomy module, vendored MSC list and mapping,
   `Entry` fields (including `fixtures`, reserved and digested), digests,
   `corpus_version` and changelog, sharded layout, `Counter`/index fixes,
   migration of the existing twenty, `corpus check` and `corpus report`.
   No new problems.
2. **Reporting.** `FieldAggregate`, selection filters, `describe_selection()`
   and the cost warning, `export`, and `compare` with the refusal contract.
3. **Authoring.** The corpus itself, field by field: MSC 13, 26/28, 20, 15.
   Fixture behaviour (A4, A5, B4, C6) lands here, on the schema slot reserved
   in phase 1.
4. **Feedback.** Discrimination, retirement, spot-audit queue. Requires at
   least three model runs before it means anything.

Phase 1 precedes phase 3 deliberately: settling the tagging, digest and shard
layout before 500 entries exist is what prevents a hand re-tag.

## Risks

- **`difficulty` is a guess.** Expect to revise the vocabulary after ~50 tagged
  entries. Cheap to change while the corpus is small; a migration afterwards.
- **Contamination is mitigated, not solved.** Twins detect memorised proofs
  applied to perturbed statements; they do not detect a model that genuinely
  learned the field from solution manuals. The twin ratio (currently 5 of 20)
  probably wants to rise, but the right number is unknown until C5 has data.
- **Statistical power stays the binding constraint.** Most per-field pairs will
  not separate at first. The honest report is the deliverable; the temptation
  to relax the refusal gate is the thing to resist.
- **Fixtures widen the trust base.** Every gate in §9 is a mitigation, not a
  proof. A wrong fixture that is consistent and not too strong will silently
  mismeasure its entries, and only the spot-audit queue stands behind it.
