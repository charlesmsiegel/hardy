# The corpus: a classified, versioned problem set that can grow to thousands and tell a mathematician which model to use

## Problem

Hardy's problem set is twenty entries in `evals/problems.json`, and it was built
to answer one question: does the interactive slice work end to end. It answers
that. It cannot answer the question we now want, which is:

> Is Opus better than GPT at commutative algebra? Is Qwen better at real
> analysis? Which model should *I* use, for *my* field?

Four things stand between the current set and that question. A fifth, which
the section that follows describes, bounds how far the answer can reach.

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

## A gap the dataset does not depend on: the runner is single-backend

`run_set_command` refuses any backend but `claude` outright
(`runner.py:279-284`): *"the evals runner drives the Claude backend only: the
batch runner, the canonical reader and staged tool-event counting are
Claude-shaped."*

**So no cross-provider comparison can be run today.** "Is Opus better than GPT
at commutative algebra" needs two providers; the pipeline drives one.

This does not block the work below, and is deliberately **not** sequenced
first. Building the corpus is the priority — evaluation is what the project is
for, and the dataset is what makes evaluation possible — so the multi-backend
runner is phase 4 in §11, scheduled to move up once the corpus exists. Naming
it here rather than in the phase list is a matter of honesty about what the
Goal promises versus what the pipeline currently delivers.

The limit is narrower than it first appears, and worth stating precisely:
`--model` varies freely *within* the Claude backend. **Opus against Sonnet
against Haiku is reachable today** — same backend, different models — and that
is a genuine multi-model comparison exercising C2, C3 and C7 in full. What is
blocked is specifically *cross-provider*: GPT, Qwen, and anything else needing
a second backend.

Two consequences:

1. **`compare` requiring `backend` equality would forbid the motivating
   comparison** even once the runner allows it. The compared intervention is
   `(backend, model)` together, not `model` alone: a cross-provider comparison
   necessarily varies runtime as well as weights, and that is an *inseparable
   confound to record*, not a reason to refuse the pair. `compare` therefore
   treats cross-backend comparisons as a declared mode that labels the
   confound, while still requiring every other non-model condition to match.
2. **Phase 4 in §11** is making the runner genuinely multi-backend: a second
   canonical reader, backend-shaped tool-event counting, and a grading path
   that is not Claude-specific. Its size is unknown and it is not scoped here.

Until it lands, every eval in §7 is reachable across Anthropic models —
the whole A-group, and C1 through C7 — but the roster stops at one provider.
That is a real instrument, and it is narrower than the Goal describes.

## Goal

A problem corpus that:

- carries a real classification, so results slice by mathematical field;
- is a standalone, versioned, publishable dataset that outlives Hardy;
- holds thousands of entries without quadratic loads or whole-file staleness;
- separates *statements* (invariant) from *measurements* (provenance-bearing);
- can say "these two models are not distinguishable at this corpus size"
  instead of printing a leaderboard that isn't there;
- can measure fixture-assisted uplift per field and level, which bounds how
  much of a field's difficulty is Mathlib's rather than the model's — without
  claiming to have separated the two (§7 C6).

**This is an instrument with two outputs, not a benchmark with one.** It
measures models, and it measures the formal library those models depend on.
The second has its own constituency. Stated at the strength the evidence
supports, it asks *where does supplying the curriculum's own prior results
change what a model can prove, field by field and level by level* — which is
a bounded, actionable question for a Mathlib maintainer, and is weaker than
"does Mathlib cover the standard curriculum" (§7 C6 says why). The schema
below is shaped so the second output is a byproduct of collecting the first
rather than a separate effort.

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
  sources.json                 # texts: citation, level, fields, survey status
  analysis-plan.json           # §8: hypothesis family and adjustment
  tombstones.json              # §2.2: every id ever issued (append-only)
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

`analysis-plan.json` and `tombstones.json` are corpus data, not Hardy
configuration: the plan decides whether a published ranking may be emitted
(§8) and the registry decides whether an id may be issued (§2.2), so both are
hashed into the manifest digest (§3) and both travel with the dataset. Model
scoreboards do **not** live here — they are per-run Hardy artifacts under
`DEFAULT_SCOREBOARDS`, and `measurements/` holds only the corpus-wide Lean
measurements that a third party could recompute.

`evals/problems.json` and `evals/baseline.json` move here; `DEFAULT_PROBLEMS`
and `DEFAULT_BASELINE` re-point. Extracting the dataset for publication is
`cp -r corpus/` from the first commit onward — the A→B migration (code into
its own package) becomes a Python-only move with no data behind it.

**Sharding by MSC 2-digit class** is chosen now rather than later for three
reasons: re-sharding an existing corpus is a migration; per-field authoring
phases become independent working files rather than one contended file; and it
is the shape a published dataset wants anyway. The loader concatenates shards
and validates globally, so nothing downstream sees the split.

**The shard is derived, never stored.** `13.json` holds the entries whose
primary code begins `13`; there is no `shard` field on `Entry`, because a
stored shard is a derived value living in the corpus, against the rule below.
Sharding at 2-digit granularity is a filing decision and says nothing about how
precisely an entry is classified — which is why §2's validator requires the
stored code to be finer than the shard it lands in.

**The corpus holds statements only.** No tier, no discrimination, no solve
rate. Anything measured lives in `measurements/` keyed by entry id. This is the
single rule that keeps the dataset portable: a tier is a fact about one tactic
ladder against one Mathlib revision on one machine, not a property of a
theorem.

## 2. Entry schema

Added to `Entry`:

| Field | Type | Meaning |
|---|---|---|
| `title` | `str \| None` | the result's common name ("Hilbert's Nullstellensatz"), when it has one. Display and dedup only — see §12. Distinct from `name`, which is the Lean identifier |
| `msc` | `tuple[str, ...]`, **non-empty** | MSC2020 codes, primary first, **each strictly finer than its 2-digit class** |
| `arxiv_override` | `str \| None`, **validated** | when the derived mapping is wrong (arithmetic geometry is MSC 14G but `math.NT`) |
| `override_reason` | `str \| None` | required when `arxiv_override` is set |
| `difficulty` | `Literal["routine","substantial","qualifying","research-adjacent"]` | human difficulty prior |
| `occurrences` | `tuple[Occurrence, ...]` | where this result appears; **first is primary**. Empty when authored |
| `status` | `Literal["candidate","active","retired"]` | lifecycle |
| `retired_reason` | `str \| None` | required when retired |
| `rationale` | `str \| None` | required when `occurrences` is empty; what an authored entry is meant to state and why |
| `witness` | `str \| None` | Lean term instantiating the hypotheses (A6) |
| `witness_note` | `str \| None` | required when `witness` is null — why none can be produced; without a field to hold it the §7 validator cannot tell a justified unwitnessed entry from an unexplained one |
| `review` | `Review \| None` | reviewer, date, and what was read — statement, `input`, origin, `msc` and reporting group, **`expected` and `twin_of`**; required for `status: "active"` |
| `audit` | `Audit \| None` | a spot-audit verdict bound to the measurement panel that triggered it (§9.2); while pending, its field emits no ranking claim |
| `fixtures` | `tuple[str, ...]` | fixture ids; empty until phase 3, but digested from phase 1 |

Removed: `area`. The twenty existing entries are hand-mapped to MSC codes as
part of phase 1.

`difficulty` is the weakest field here. Four levels is a guess, and the
vocabulary should be revisited after roughly fifty real entries are tagged
rather than defended. It is a *subjective per-problem* prior, deliberately
distinct from the per-source `level` of §2.1 — an easy exercise can appear in a
graduate text. Where the two disagree, `level` is what C6 reports on, because
it is auditable.

Never added: `tier`, `discrimination`, solve rates, or anything else measured.

New validators, in the spirit of `tier_must_follow_its_closers`
(`sweep.py:180`) — a schema that refuses states it cannot justify:

- `msc` is non-empty — the unknown-code validator succeeds vacuously on `[]`,
  which would leave an entry with no primary code, no shard, and no value for
  `field_of`;
- unknown MSC codes are rejected against the vendored list;
- **every code is strictly finer than its own 2-digit class.** `13` is *itself*
  a valid MSC2020 entry (13-XX), so a vendored-list check alone accepts
  `msc: ["13"]` — and everything downstream still works: `field_of` resolves,
  `--msc 13` matches, the shard is found. The precision is simply gone, and
  recovering it means re-tagging every entry by hand, which is the cost phase
  1's ordering exists to avoid. A code must therefore carry at least its
  division (`13A`), with the leaf preferred (`13A15`). A bare class is what a
  tagger writes when they did not look;
- `status == "retired"` requires `retired_reason`;
- an entry with empty `occurrences` requires `rationale` — otherwise it can
  never pass the §2.2 review gate and is silently unreportable forever;
- **`status: "active"` requires a `review` record whose recorded digests match
  the entry's current ones.** Prose describing the review is not a gate: with
  only a bare `status`, a contributor can mark an unreviewed entry active, or
  edit a reviewed one and leave the flag set, and §8 puts it straight into a
  headline. Binding the record to the statement, `input` and origin digests
  makes an edit demote the entry to `candidate` automatically. The record binds
  the **classification** too: a wrong-but-syntactically-valid MSC code passes
  every taxonomy validator, so without it a faithfully-reviewed entry could be
  moved to an unrelated field and keep its approval while contributing to the
  wrong field headline — and field attribution is the headline claim. It binds
  `expected` and `twin_of` for a sharper version of the same problem: flipping
  an entry between a true theorem and a false twin changes what a correct model
  response *is*. `prompt_digest` forces the run to be repeated, but a stale
  approval would let C5 score a correct proof of a genuinely true statement as
  a failure to refuse. The review must attest the twin relationship, not just
  the mathematics;
- `arxiv_override` requires `override_reason`, **and is validated against the
  mapping table's codomain** — an unconstrained string would let `"math.AC "`
  or an invented class through, where it becomes a distinct value for
  `--arxiv` selection and reporting. That is precisely the free-form
  classification failure this taxonomy exists to remove, reintroduced through
  the escape hatch;
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
| `surveyed` | per-field survey status: which MSC fields this text has been *exhaustively* read for, and at what corpus version |

`surveyed` exists because the canonicity denominator is meaningless without it.
Citation data and coverage claims cannot distinguish a text fully read for
MSC 13 from one merely registered or skimmed, so "4 of 6 surveyed texts" would
not be reproducible and adding a half-read book could move the core/peripheral
split. Only fully-surveyed source/field pairs count toward the denominator.

An `Occurrence` is `(source_id, locator)`, where `locator` is a **non-empty
tuple of non-negative integers** — `(chapter, section, item)` — compared
lexicographically. The constraints are load-bearing rather than tidiness: an
empty tuple sorts before every non-empty one and `(-1,)` sorts before any real
chapter, so an unconstrained tuple lets malformed provenance satisfy the
"strictly earlier" antecedent gate (§9.0) without naming any earlier result,
admitting an unjustified assumption into B4 and C6. Locators are validated
before they are ever compared.

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
favourably, and fixture sets — and therefore the measured uplift — would
inflate with every book added. Since the antecedent policy sets the scale of
C6 (§9.0), the permissive reading would let corpus composition move the
headline number. The primary occurrence pins it.

**Occurrences are outside every digest.** Adding a citation does not change
what the theorem says, so it must not invalidate any measurement. But an
occurrence that counts toward canonicity is not inert either: it moves an entry
between peripheral and core and so changes the separated C6 result. Since
promotion (§2.2) reads only the primary origin, an erroneous secondary citation
would keep the entry's approval while shifting its classification. So each
occurrence counted toward canonicity carries its own lightweight **citation
check** — a recorded confirmation that this result really does appear at that
locator in that text — separate from the promotion review and cheap enough to
do at survey time. Uncounted occurrences (unsurveyed texts) need none.
Expect this list to be edited often as more texts are surveyed; that editing
has to be free.

Three things fall out:

1. **`level` stratifies the uplift report** (§7 C6). A graduate text demands
   antecedents a first course never would; aggregating the two reads as a
   difference in uplift when it is a difference in level. Stratified, the same
   data says something sharper and defensible: *"supplying prior results
   changes little at first-course level in commutative algebra, and a great
   deal at graduate level."*
2. **`locator` ordering makes the antecedent policy machine-checkable**
   (§9.0). A prose citation cannot be compared; `(3, 2, 12)` can.
3. **Canonicity is a signal, free — counted over distinct `source_id`.** A
   theorem in five of six standard texts is core curriculum; one in a single
   text is specialised. It is *not* `len(occurrences)`: a result stated in a
   chapter and reused in three later ones yields four occurrences in one book,
   which would read as core on a single source. The signal is the count of
   distinct sources, against a recorded **surveyed-source denominator** — "4 of
   6 texts surveyed for MSC 13" — because a bare count is meaningless without
   knowing how many books were looked at. Numerator and denominator must range
   over the *same* population: only occurrences in source/field pairs marked
   fully `surveyed` for this entry's field count toward the numerator. Counting
   every citation against a surveyed-only denominator mixes populations and can
   put the numerator above it, misclassifying entries as core and shifting the
   C6 core/peripheral split.
   This is directly load-bearing for the claim in the Goal — *does Mathlib
   cover the **standard** curriculum* — so C6 reports core and peripheral
   uplift separately rather than pooling them. It is only meaningful once a
   field's texts have actually been surveyed for occurrences, which is
   per-field work in phase 3, not a property of the first entry written.

Corpus composition (phase 3) should where possible draw each field from **two
texts at different levels**. The within-field difference between them is
informative on its own and costs nothing beyond choosing the second book.

Semantic deduplication stays a human job: `corpus check` can flag entries whose
`conclusion` normalises identically, but it cannot recognise that two
differently-phrased statements are the same theorem. The `occurrences` list is
the mechanism for recording the merge once a human makes it.

### 2.2 Lifecycle: candidate, active, retired

`status` distinguishes `candidate` from `active` from `retired`, and only
`active` entries reach a headline number (§8).

**Promotion requires a human to read the canonical Lean statement against
`input` and the entry's stated origin, and record that they did.** The
mechanical gate establishes that a statement elaborates and how automation
behaves; it establishes nothing about whether the Lean proposition faithfully
represents the source problem. A mistranslated or silently weakened theorem
passes every mechanical check, discriminates between models, and lands in a
field headline — and auditing only the highest-discrimination items does not
catch it, because a faithfully-wrong statement need not discriminate unusually.

The origin the review reads against depends on the entry. For a harvested
entry it is the primary occurrence. An **authored** entry has empty
`occurrences` and so no primary to read against; it carries a required
`rationale` recording what it is meant to state and why it was written, and the
review reads the Lean against that. Without that path an authored entry could
never satisfy the gate and would sit at `candidate` forever, silently excluded
from every aggregate. The gate applies to both; only the document differs.

This is **not** the reference-proof gate considered and rejected in "Decisions
taken before design" — it is a read, not a proof, and does not require the
reviewer to be able to prove the theorem. Entries stay `candidate` and out of
reporting until someone does it, which makes the backlog visible rather than
silent.

**Id permanence** falls out of the lifecycle: a retired entry stays in its
shard with `status: "retired"` rather than being deleted, so an id can never be
reused. But that is a *policy*, and validating uniqueness across the present
shards cannot enforce it: delete a retired row and the check sees only the new
claimant and accepts it, breaking every external citation. So ids are validated
against a machine-checked **tombstone registry** — a record of every id ever
issued — not merely against the current corpus. "Append-only" is itself
enforced, not asserted: CI compares the registry against the merge base and
**rejects any removal or mutation of an issued id**. Without that, a
contributor could delete a tombstone, bump the version, reuse the id, and pass
every current-state uniqueness check — recreating precisely the broken external
citation the registry exists to prevent.

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
manifest digest**, a hash over every content file:

- every problem shard;
- `sources.json`, including the survey-completion record;
- the taxonomy tables and reporting groups;
- every versioned fixture file — once phase 3 edits a fixture's statement under
  a stable id, the assumptions of every dependent entry change, and a manifest
  omitting them could establish neither reproducibility nor tampering;
- `analysis-plan.json`, which fixes the hypothesis family, the primary
  comparison and the multiplicity adjustment, and so decides whether a ranking
  may be emitted at all;
- `tombstones.json`, without which a removed or altered id-history entry
  verifies clean in a published copy of `corpus/`, defeating exactly the
  portable citation guarantee §2.2 claims.

CI additionally diffs these files against the merge base and requires the
matching version and changelog entry when any of them moved.

**`measurements/` is deliberately outside that list.** Re-sweeping a baseline
against a new Mathlib revision or a different host changes no content and must
not demand a corpus version bump — the separation between invariant statements
and provenance-bearing measurements (§1) is the whole reason the two live in
different files, and a version gate that fired on measurement refreshes would
manufacture releases whose content never changed.

**Version numbers cannot express measurement validity, and must not be asked
to.** A patch that corrects one statement invalidates measurements for that one
id and leaves the rest perfectly good. So:

Staleness is decided by **component digests**, and each measurement records
the subset it actually depends on. One monolithic digest per entry cannot work:
editing a shared fixture would invalidate A1–A3, A6 and B1 for every dependent
entry, none of which loads fixtures at all, forcing large re-sweeps and model
re-runs whose outcomes cannot change.

| Component | Covers |
|---|---|
| `statement_digest` | `name`, `binders`, `conclusion`, `imports`, `witness`, `witness_note` |
| `fixture_set_digest` | the resolved contents of this entry's fixtures, transitively |
| `prompt_digest` | `statement_digest` plus `input`, `expected`, `twin_of` |
| `environment_digest` | Lean version, Mathlib revision, lake manifest, host |
| `procedure_digest` | Hardy's source revision or build id, the tactic ladder, and the sweep budgets |

Which measurement depends on which:

| Measurement | Depends on |
|---|---|
| A1, A2, A3, A6 | statement + environment + procedure |
| A4, A5 | statement + fixture-set + environment + procedure |
| B1, B2, B3 | prompt + environment + procedure (and its `Condition`) |
| B4 | prompt + fixture-set + environment + procedure |

Four things this arrangement decides, each for its own reason:

**The witness is in `statement_digest`.** Editing a valid witness into one the
kernel rejects would otherwise leave the cached A6 pass looking current and
bypass the non-vacuity gate entirely.

**`fixture_set_digest` follows the pointer, not the id.** An edit to a
referenced fixture's statement under a stable id changes the assumptions of
every dependent problem; digesting ids alone would leave A4, A5 and B4
apparently fresh. Transitive resolution requires the fixture dependency graph
to be **acyclic** — a cycle either recurses forever or forces an
order-dependent fallback yielding unstable identities — and `corpus check`
rejects one.

**`expected` and `twin_of` are in `prompt_digest`** because they *shape the
run* rather than describe it: under a staged condition true entries run staged
while twins run batch under separate limits (`runner.py:219-225`). Correcting
an entry between the two changes the mode it executes in. `input` is in there
for the same class of reason — `_batch_runner` passes it to the model as
`informal_claim` (`runner.py:252`) and staged runs use it as the request, so
rewording it can change solve behaviour while the Lean statement is untouched.

**`environment_digest` exists because recording provenance is not the same as
governing reuse.** `Baseline` already stores the Lean version, Mathlib
revision, lake manifest and host — but storing them does not make them decide
staleness. A Mathlib upgrade changes elaboration, automation tiers, fixture
checks and witness acceptance; if only the statement and Hardy's identity are
compared, every cached A-group result matches and the incremental sweep serves
results measured against a library that no longer exists. §1 already says a
tier is "a fact about one tactic ladder against one Mathlib revision on one
machine"; the dependency set has to say the same thing.

**`procedure_digest` exists because the corpus and the library are not the only
things that can change.** `Baseline` records the Lean environment, the ladder, the
budgets and the host — but nothing identifying Hardy itself, while `Condition`
carries `hardy_version` and `source_revision` for model runs. A fix to the
elaboration wrapper, the sweep logic, the axiom parser or the witness checker
would therefore leave every cached A-group result looking current although the
code that produced and interpreted it has changed. Recording Hardy's identity
in the A-group closes an asymmetry the B-group never had.

Every measurement stores the components that govern it, so staleness is a
per-entry, per-component comparison rather than a per-file one. This replaces
`baseline.json`'s `problems_sha256`.

**Incremental *sweeping* follows; incremental *model reuse* does not.** Sweep
only entries whose governing components changed or have no measurement: at
5,000 entries and ~24 tactic attempts of ~17s each a full sweep is roughly 570
hours serially, so this is the difference between tractable and not. The
B-group has no equivalent. A scoreboard is an immutable per-run artifact
carrying one `Condition`, so a corrected entry cannot be re-run and spliced
back: the fresh row and the untouched rows would belong to different runs, and
no single condition would truthfully describe the result. Digests still tell
you *which* B-group rows went stale — that is worth having — but acting on it
means re-running the whole condition. A condition-preserving cache with a
materialization step that re-verifies every reused row's code, environment and
prompt identity would change this; it is not designed here, and nothing below
assumes it.

**`fixtures` must be digested from phase 1.** Reserving the field but adding it
to any digest in phase 3 would re-invalidate every measurement in the project.

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

## 6. Selection, export, and the runtime warning

Selection filters move into `select()` (`runner.py:129`) so every command
shares them: `--msc 13` (prefix match, catching `13A15`), `--arxiv math.AC`,
alongside existing tier/twin filters and new `--difficulty`, `--status`,
`--source` and `--level`. `--source` matches **any** occurrence, so "every
problem in Atiyah–Macdonald" works regardless of which text was primary;
`--level` reads the **primary** occurrence, because that is what C6 stratifies
on. The asymmetry is deliberate and is the one place a reader will expect these
two flags to behave alike.

A shared `describe_selection()` prints the count and an honest upper bound on
**runtime** — deliberately not on spend. The mode limits bound elapsed time,
turns and Lean checks; none of them bounds money. A monetary ceiling would need
per-provider token limits and current pricing, neither of which the runner
records, so an expensive run could sit under any threshold derived from these
values. The bound is labelled as runtime everywhere it appears, and a real
spend gate is left unbuilt rather than faked.

The runtime bound is **derived per row from the mode's own limits**, not from
`wall_seconds` alone: batch rows are governed by `max_turns`/`wall_seconds`,
but staged runs reject `--wall-seconds` outright and are governed by
`active_seconds`, `proof_seconds` and `official_checks`, while a twin inside a
staged condition runs batch under `twin_wall_seconds` (`runner.py:225`,
`runner.py:320-322`). A single-formula estimate has no valid value for a mixed
staged selection. `export` prints the bound; `run` additionally requires
`--yes` above a threshold — a coarse guard on a long run, not a spend cap.

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
| A6 | Non-vacuity check | a stored witness term, kernel-checked, instantiates the hypotheses | new |

**A3 does not detect vacuity, though it looks as if it should.** If `P` is
vacuously true because its hypotheses are impossible or overstrong, then `¬P`
is false and the ladder finds no closer — the sweep comes back clean on exactly
the broken entry one would expect it to catch. A3's real job
is sign errors and statements that are refutably false.

Vacuity needs A6, and A6 needs a persisted artifact. "A witness instance
elaborates" is not a specification: it names neither an input nor anything
stored, and cannot be implemented or tested. `Entry` holds only a raw
`binders` string, and
for dependent binders like `(n : Nat) (h : n > 0)` merely elaborating the
binders proves nothing about whether compatible values exist. So an entry
carries a **`witness`**: a Lean term instantiating its hypotheses, stored in
the corpus, inside `statement_digest` (§3), and checked by the kernel like any
other proof. Where no witness can be produced mechanically — a genuine
possibility for existence-heavy hypotheses — the entry records `witness: null`
with a required `witness_note`, and that fact is reported rather than hidden:
an unwitnessed entry is one where nothing but the §2.2 human read stands
between a vacuous statement and a field headline.

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
| C3 | Item discrimination | variance across models; writes the spot-audit queue (§9.2) and difficulty strata — **never a filter on the scored corpus** |
| C4 | Tier profile per field | how much of a field Mathlib's automation already covers |
| C5 | Contamination signal | twin **semantic-refusal** rate against true-statement solve rate; exhaustion and rejected attempts reported separately |
| C6 | Fixture-assisted uplift | `B4 − B1` per field, **stratified by primary-source `level`**, core and peripheral reported apart |
| C7 | Ceiling and floor census | items no model solves and items all models solve, reported as strata rather than removed |

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
reads a level difference as a difference in uplift. Stratified, C6 says where
supplying the curriculum's own prior results changes what a model can prove,
at first-course level and at graduate level. That is a bounded, actionable
finding for a Mathlib maintainer; it is *not* the statement that Mathlib covers
one level and not the other, and stratification does not license upgrading it
into one. Core results (present in most surveyed texts) and peripheral ones are
reported apart for the same reason — pooling them lets an uplift concentrated
in specialised material read as one spanning the curriculum.

C5 must not read "failed to prove" as "recognised the claim is false", and
Hardy's existing `refused` is not yet narrow enough to carry that reading.
`refused` covers terminal in `{no_proof_submitted, axioms_rejected}`, and a run
that attempts `by sorry` lands there too
(`tests/unit/test_evals_scoreboard.py:49-51`). Such a row establishes only that
no clean proof was accepted — which the kernel guarantees for any genuinely
false twin regardless of what the model believed — so counting it as
contamination evidence would score an abandoned or invalid proof attempt the
same as a model that saw through the statement.

C5 therefore needs an explicit **semantic refusal**: the model asserting the
statement is false or unprovable, not merely failing to land a proof. Until the
harness emits that signal, the three terminal classes are reported side by side
— semantic refusal, rejected attempt (`axioms_rejected`, holes), and exhaustion
(`turn_limit`, `wall_clock_limit`, which `FEATURES.md:1237` already says is not
a refusal) — and only the first carries the contamination reading.

## 8. Aggregation and comparison

`scoreboard.py` gains a `FieldAggregate` beside `TierAggregate`, reusing the
existing `wilson()` (`scoreboard.py:155`). Reporting is restricted to tier ≥ 2
and `status == "active"`: automation-solvable, candidate and retired entries
never reach a headline number. A **pending spot-audit** (§9.2) withholds the
affected field's ranking claim entirely rather than dropping the flagged entry
from the sample — dropping it would filter on the outcome being measured.

**`invalid` rows are excluded from ranking-capable reports, not counted as
failures.** `_tier_aggregate` puts every true row in `n` but only `solved` rows
in the numerator (`scoreboard.py:189-201`), so a row marked `invalid` — an
unreadable or unauditable artifact, a harness fault — is scored exactly as a
model that failed to prove the theorem. Reusing that machinery unchanged would
let harness corruption depress C1 and manufacture C2 discordance that looks
like a model difference. Invalid items are reported as missing measurements
with their own count, and a field whose invalid rate exceeds a declared
threshold cannot carry a ranking claim at all.

**Repeats collapse to one item-level outcome before any interval or test.**
Scoreboards store one row per repeat and the existing aggregator counts rows,
so feeding rows straight into `wilson()` or McNemar treats correlated attempts
at the same theorem as independent samples: 125 problems at 10 repeats would
present as 1,250 observations, narrowing every interval by roughly a factor of
three and manufacturing ranking claims that the data does not support. Each
`(model, entry)` pair yields exactly one declared outcome — the rule for
declaring it is fixed in the analysis plan and recorded — and the unit of
analysis is the item. Where repeat-level variation is itself the question (B3),
it is reported as a separate per-item statistic, not as extra sample size.

**Items are not independent either, and the intervals must say so.** Phase 3
draws a field's ~125 entries from as few as two texts, so exercises sharing a
chapter, a prerequisite chain, or an author's habits succeed and fail together.
Collapsing repeats removes one source of dependence and leaves this one
untouched: a plain Wilson interval or McNemar test over 125 correlated items is
narrower than the effective sample size supports, and can emit a field ranking
the data does not carry. Intervals and tests are therefore **clustered on the
source text**, and every field report states its effective sample size beside
its item count. Where a field's entries come from a single text, no ranking
claim is available from it at all — one cluster is one observation, however
many exercises it contains.

`evals/compare.py` is new, because comparison is a different concern from
scoring one run and `scoreboard.py` is already 622 lines.

**`Condition` gains `fixtures_enabled` first.** Nothing in the recorded
condition separates B1 from B4: fixture references live on the entry and in its
digest, so a bare run and a fixtured run over the same corpus would carry
identical condition and prompt identity and be indistinguishable afterwards.
And a blanket condition-equality rule would make C6 impossible — it would
demand every non-model condition match, while C6 exists precisely to compare
two runs that differ in fixtures. So the flag is explicit — and the exception
is **scoped to one comparison kind, not blanket**. A blanket exception would
let a bare scoreboard for model A be paired against a fixtured one for model B
and emit a ranking that credits the fixture intervention to the model.
`compare` therefore takes the comparison kind explicitly: a **C2** cross-model
comparison requires `fixtures_enabled` to be *equal*, while a **C6** uplift
comparison requires it to *differ* and requires model identity to be *equal*.
Every other non-model condition must match in both.

`hardy evals compare <scoreboard>... --by field`:

1. Refuses scoreboards that differ in **any non-model experimental
   condition** other than the declared `fixtures_enabled` exception, and not
   merely in corpus and environment. `Condition` carries
   `backend`, `mode`, both prompt-set hashes, `hardy_version`,
   `source_revision`, `limits`, `repeats` and `selection` (`runner.py:35-57`),
   and must additionally carry the **canonical reader**. That reader is chosen
   outside `Condition` today — `staged.py:191` takes
   `config.faithfulness_model` — while `scoreboard.py:152` turns its verdict
   into `solved` versus `solved_other`. Two scoreboards with identical
   conditions can therefore have been graded by different readers, and the
   disagreement lands in the very outcome C2 compares. The reader's model,
   backend and procedure identity join the condition, or the equality gate is
   decorative for staged runs.
   Equality of `source_revision` is not enough when both sides record `None`,
   which the runner does for a source tree without `.git`: two stripped
   snapshots from different commits of the same unreleased `hardy_version`
   would pass every check while the runner or grading logic differed. A
   comparison requires a *known* revision on both sides, or a recorded
   immutable build digest standing in for it;
   a staged Opus run at a larger budget against a batch GPT run would differ
   in most of them, and the whole difference would be attributed to the
   models. The paired item set must be identical too — same ids, same
   `prompt_digest` — since a comparison over different items is not a paired
   comparison at all. Identical is not sufficient: two runs interrupted at the
   same prefix have identical item sets, and the existing validator accepts an
   interrupted exact prefix. Because shards are ordered by MSC class, a prefix
   is not a representative sample of a field. A ranking claim requires
   *complete* scoreboards covering the analysis plan's full selected set;
   anything short of that is labelled partial and reported as exploratory.
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
   is supposed to bind. The family cannot be "whatever this invocation was
   passed": the CLI takes an arbitrary subset of scoreboards, so running each
   pair separately would make every family size one and license exactly the
   unadjusted claims the gate exists to refuse. So the family and the primary
   comparison live in a **versioned analysis plan** committed alongside the
   corpus, naming the models and fields in advance; the adjustment is computed
   against that plan however the runs are split across invocations, and a
   comparison outside the plan is reported as exploratory and never as a
   ranking.

   Naming models and fields is not enough. The plan must also bind **mode,
   limits, repeats, fixture condition, selection, and the repeat-to-item
   outcome rule**. Pairwise condition equality only guarantees that the two
   scoreboards in one comparison match each other: an analyst can run the same
   planned pair under several internally-consistent budgets, or under two
   collapse rules, and publish whichever crosses the threshold, while the
   multiplicity denominator still counts one model/field hypothesis. Either the
   full analysis condition is fixed in advance, or every configuration tried
   enters the family.
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

What counts as a reasonable antecedent sets the scale of the entire uplift
measurement. If the rule varies by field, the cross-field
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

**What the mechanical check enforces.** Because `locator` is an ordered tuple
(§2.1), `corpus check` verifies for every fixture attached to an entry that the
fixture has an occurrence in the entry's **primary** `source_id` at a locator
**strictly less than** the entry's primary locator. Rejected, therefore: a
fixture reaching forward in the text (assuming a later result to prove an
earlier exercise), one that appears only in some other book, and one sharing
the entry's own locator.

The ordering is strict because "prior result" means prior. A multi-part
exercise's own sibling lemma shares its locator, is not earlier curriculum, and
can materially shorten B4 while A5 still passes — A5 only tests that the fixed
ladder does not close the goal, not that the fixture is a legitimate
antecedent. Where a text's parts genuinely need ordering, the locator gains a
subitem component rather than the comparison being loosened.

The check runs against the primary occurrence specifically, not against any
occurrence. Both the entry and the fixture may appear in several texts, and an
existential over all of them would let the choice of books relax the policy
(§2.1). The primary is what the entry was harvested from, so it is the reader
whose competence is being modelled. An **authored** entry has no primary
occurrence at all, and so cannot be subject to this check: authored entries are
therefore ineligible to carry fixtures, and an entry with empty `occurrences`
and a non-empty `fixtures` is rejected.

**What it does not enforce.** The check establishes only that a fixture occurs
*somewhere earlier* in the primary text. It cannot enforce "prefer the same
chapter, reach earlier only when needed," so two curators can attach very
different amounts of distant background and both pass. That moves B4 and C6.
The invariant **narrows** curator judgement to a checkable envelope; it does
not remove it, and it should not be described as if it did.
What closes the remaining gap is disclosure rather than mechanism: a fixture
from **outside the entry's own chapter** requires a persisted justification,
reviewed with the entry, recording why the nearer material was insufficient.
Same-chapter antecedents need none.

**What no rule here can fix**: the *books* are not uniform. Textbook difficulty
varies and cannot be controlled for. That variance is a level effect, not a
per-model one — both models face the same book — so within-field model
comparison, the headline claim, is unaffected. It contaminates cross-field
claims, already the weaker statement, and it contaminates C6, which is why C6
is reported stratified by `level` rather than aggregated.

### 9.1 Mechanism

**An antecedent never goes in `Entry.binders`.** The tempting approach is
exactly that — a missing lemma as a hypothesis binder, sound by construction
and needing no new machinery — and it destroys the measurement silently.
`binders` are part of the canonical declaration and are injected into *every*
run, B1 included. A hypothesis-encoded antecedent is
therefore present in the supposedly bare condition: B1 stops testing the
theorem against stock Mathlib, `B4 − B1` goes to zero for exactly the gaps that
mechanism was meant to cover, and the entry silently states a stronger theorem
than its source did. The soundness argument was correct and irrelevant; the
harness, not the logic, is what decides this.

So both gaps route through `corpus/fixtures/`, and the difference is only in
what a fixture contains:

- **Missing lemma → a fixture stating it**, injected *only* under
  `fixtures_enabled`. Sound in the same way a hypothesis would have been, and
  now actually absent from B1.
- **Missing definition or structure → a fixture carrying a real preamble**,
  possibly including `axiom`. You cannot hypothesise a definition Mathlib
  lacks. This is the small dangerous case.

`binders` continues to hold what the source theorem itself stated, and nothing
else. A validator enforces the separation: an entry's `binders` may not
mention a fixture's declared name.

The guards already exist and are strict. `verifier.py:43` sets
`ALLOWED_AXIOMS = audit.STANDARD`; `sorryAx` and every non-standard axiom are
refused (`verifier.py:203-216`); `lean.py`'s `FORBIDDEN_TOKEN` bans a submitted
proof from declaring `axiom` or `opaque` at all. `verifier.py:202` records that
the eval path deliberately passes no approved assumptions: "a staged run is
nobody's place to widen the trust base."

A fixture set is therefore an explicit, **per-entry, corpus-declared** widening
of that allowlist. Four gates, the first three reusing the existing sweep:

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
   locator **strictly before** the entry's primary locator (§9.0). A fixture
   from later in that text, sharing the entry's locator, or absent from it
   entirely, is rejected.

Fixtures are shared and referenced by id — the same missing lemma blocks many
problems — and carry their own statements, checked as problems are. When
Mathlib later gains a lemma, the fixture retires, and *that retirement is a
datum*: the fixture library becomes a running record of what Mathlib lacks per
field, harvested as a byproduct.

That retirement needs a lifecycle the schema does not yet have: entries hold
only fixture ids, so once Mathlib gains a replacement the fixtured condition
would keep injecting the obsolete assumption indefinitely. Fixtures need their
own `status`, a retirement reason, and the Mathlib revision that superseded
them, plus a rule for whether dependent entries must drop the reference or
skip it. **Deliberately left to phase 3** rather than specified now — it cannot
be designed well before any fixture exists, and nothing in phases 1-2 depends
on it. Recorded here so it is not rediscovered as a surprise.

### 9.2 The spot-audit queue

**High discrimination is not evidence of validity.** A degenerate statement is
one a sharp model closes by exploiting the degeneracy while a careful model
grinds at the mathematics — it discriminates strongly and measures nothing. So
the top-discrimination items feed a **spot-audit queue** for human review. That
is a handful of entries, not 500.

A queue that only exists in prose withholds nothing. The promotion `review`
(§2.2) happens before any model has run and cannot speak to a pattern only
visible afterwards, and no validator can retract an entry that is already
`active` when C3 later flags it. So the queue is persisted:

- C3 places an entry in the queue by writing a **pending** `audit` record
  naming the measurement panel — the models, conditions and corpus version —
  whose discrimination triggered it.
- A pending audit **withholds the whole ranking for that field**, and does not
  drop the entry from the sample. Excluding the flagged entries individually
  would be exactly the outcome-dependent filtering rejected in "Decisions taken
  before design": C3 selects them *because* they discriminate most, so removing
  them changes the paired table, can suppress a real difference, and can
  reverse which model is favoured — before any human has found anything wrong
  with them. Descriptive reports continue; ranking claims wait.
- A human resolves it to `sound`, in which case the entry returns to full
  eligibility, or to `broken`, in which case it is retired with a reason.
- The record binds the panel it was raised against. A later panel that flags
  the same entry raises a fresh audit rather than inheriting a verdict reached
  about different models.

## 10. Testing

Hermetic except where Lean is genuinely required (already gated behind
`--acknowledge-unsafe-execution`).

**Schema and taxonomy**

- every corpus MSC code has a mapping; the mapping table is well-formed; a
  reporting group covers every 2-digit class in use
- rejected: an unknown MSC code, an empty `msc`, a retirement without a reason,
  an override without a reason, `"math.AC "` and any invented arXiv class
- `msc: ["13"]` is rejected and `msc: ["13A15"]` accepted — a code must be
  finer than its own 2-digit class; the shard an entry lands in is computed
  from `msc[0][:2]` and no `shard` field exists to disagree with it
- a false twin whose MSC drifts from its target is rejected
- every `source_id` in every occurrence exists in `sources.json`; every source
  carries a `level`; the survey-completion record exists for any field whose
  canonicity denominator is reported
- canonicity: a theorem at four locations in one book counts as one source; an
  occurrence in a registered-but-unsurveyed text counts toward neither
  numerator nor denominator, so the numerator can never exceed it

**Digests and staleness**

- editing `conclusion`, `binders`, `imports`, or `witness` changes
  `statement_digest`
- editing a referenced fixture's *statement* under a stable id changes
  `fixture_set_digest` transitively but **not** `statement_digest`, so A4/A5/B4
  go stale while A1–A3, A6 and B1 stay fresh
- changing Hardy's source revision or the tactic ladder changes
  `procedure_digest`, and bumping the Mathlib revision changes
  `environment_digest`; either invalidates every A-group measurement, including
  ones whose statement never moved
- editing `input` changes `prompt_digest` but **not** `statement_digest`, so
  B-group measurements go stale while A-group measurements stay fresh
- flipping `expected` between `true` and `false` changes `prompt_digest`, since
  it changes the mode the entry executes in
- adding or reordering `occurrences` does not change either digest — but
  reordering changes which occurrence is primary, and so can change the
  antecedent verdict
- a cyclic fixture dependency graph is rejected rather than recursed

**Lifecycle and provenance**

- a `candidate` entry never reaches a headline aggregate
- `status: "active"` without a `review` record is rejected; editing a reviewed
  entry's `conclusion` — or its `msc` — invalidates the record and demotes the
  entry without anyone touching `status`
- an authored entry (empty `occurrences`) with a `rationale` can be promoted;
  one without cannot; one carrying fixtures is rejected outright
- an entry whose `witness` fails the kernel is rejected; `witness: null`
  without a `witness_note` is rejected
- an occurrence counted toward canonicity requires a citation check; adding an
  unchecked one to a surveyed text is rejected rather than silently moving the
  entry from peripheral to core
- C5 counts only semantic refusals: a run terminating in `axioms_rejected`, or
  one whose proof carried a hole, is reported as a rejected attempt and never
  as contamination evidence
- flipping `expected` or `twin_of` on a reviewed entry invalidates its `review`
  and demotes it, so a stale approval cannot let C5 score a correct proof of a
  true statement as a failure to refuse
- a pending `audit` withholds its field's ranking claim while the flagged entry
  stays in the sample — the paired table is unchanged, so no filtering on the
  outcome occurs; resolving `sound` releases the ranking, `broken` retires the
  entry; a fresh panel raises a new audit rather than inheriting a verdict
  reached about different models
- the deliberately vacuous entry is **not** caught by A3 — the negation sweep
  finds no closer, which is the point of §7's correction — and **is** caught by
  A6
- ids: unique across shards; reusing an id recorded in `tombstones.json` is
  rejected; CI rejects a diff that removes or mutates an issued id
- a shard edited without a version bump fails CI's merge-base diff;
  `corpus_version` matches the changelog head and the manifest digest

**Antecedents and fixtures**

- a fixture is rejected when it sits *after* its entry's primary locator, when
  it shares that locator exactly, and when it occurs only in a non-primary text
- a fixture whose own *primary* is elsewhere but which occurs in the entry's
  primary at an earlier locator is **accepted**
- a cross-chapter fixture without a persisted justification is rejected
- `()` and `(-1,)` are rejected before any ordering comparison; tuples of
  unequal length compare lexicographically as intended
- an entry whose `binders` mention a fixture's declared name is rejected — an
  antecedent must never reach the bare condition
- axioms: a constructive proof reporting none of the standard three is
  accepted; a proof reporting an undeclared axiom is refused

**Selection and export**

- every emitted shell line runs as written — a unique `--label` per row and the
  acknowledgement present, since bare `run --only` lines are refused
- `describe_selection()` labels its bound as runtime, never spend, and derives
  a staged selection's bound from `active_seconds`/`proof_seconds`/
  `official_checks` rather than a `wall_seconds` that does not exist for it
- `--source` matches any occurrence while `--level` reads the primary

**Aggregation and comparison**

- known counts reproduce hand-computed Wilson bounds
- intervals cluster on the source text: a field whose entries come from one
  text yields no ranking claim, and a two-text field reports an effective
  sample size below its item count
- 125 items at 10 repeats produce an interval computed from 125 observations,
  not 1,250
- `invalid` rows are reported as missing measurements, not as failures, and a
  field over the declared invalid threshold carries no ranking claim
- `compare` refuses scoreboards differing in `mode`, `limits`, `repeats`, or
  either prompt-set hash even when corpus and environment agree; refuses two
  scoreboards that both record `source_revision: None`; and refuses two that
  were graded by different canonical readers
- a plan naming only models and fields is rejected: mode, limits, repeats,
  fixture condition, selection and the outcome rule must all be bound, or every
  configuration tried counts in the family
- a C2 pair differing in `fixtures_enabled` is refused; a C6 pair differing in
  `fixtures_enabled` *and* model is refused; a C6 pair differing only in
  `fixtures_enabled` is accepted
- an interrupted scoreboard, even one whose item set matches exactly, yields a
  partial report and never a ranking
- synthetic discordance exercises both paths: refusal when the CI crosses zero,
  a claim when it does not
- splitting one planned family across separate invocations yields the same
  multiplicity adjustment as running it in one
- C6's level-aggregated form is not reachable through the reporting API

**Review editor**

- `title` appears in the editor and in dedup, and is absent from the assembled
  request — the model never receives it, though `name`, the Lean identifier,
  necessarily does
- a `faithful` verdict promotes `candidate` → `active`; an `unfaithful` verdict
  demotes and records a reason; neither retires the entry
- a write that would fail `corpus check` is refused by the editor with the same
  message the CLI gives
- editing `conclusion` reports the demotion and stale-measurement count before
  saving, and saving without confirmation does nothing
- an edit through the editor requires a `corpus_version` bump and changelog
  entry exactly as a hand edit does

**Scale**

- a generated 5,000-entry corpus loads within a time bound, guarding the
  quadratic regression

## 11. Phases

1. **Schema and scale.** Taxonomy module with reporting groups, vendored MSC
   list and mapping, `Entry` fields (including `fixtures`, reserved and
   digested), the **component digests** of §3, `corpus_version` with the
   manifest binding and changelog, tombstone registry, sharded layout,
   `Counter`/index fixes, migration of the existing twenty — including
   rewriting their `input` with inline LaTeX (§12.3) — `corpus check` and
   `corpus report`, A6's non-vacuity check, `environment_digest` and
   `procedure_digest` in the A-group record, and the candidate→active review
   workflow (§2.2). No new problems.
2. **Reporting.** `FieldAggregate` over item-level outcomes, selection filters,
   mode-aware `describe_selection()`, `export` including the runnable wrapper,
   and `compare` with the refusal contract, condition equality and the
   multiplicity adjustment.
3. **Authoring.** The review editor of §12 first, then the corpus itself, field
   by field: MSC 13, 26/28, 20, 15 — each entry through the faithfulness review
   before it is reportable. Fixture
   behaviour (A4, A5, B4, C6) lands here, on the schema slot reserved in
   phase 1.
4. **Multi-backend runner.** The work described under "A gap the dataset does
   not depend on", above. Deliberately *after* the corpus rather than before
   it: evaluation is the priority, the dataset is what makes evaluation
   possible, and every eval in §7 runs across Anthropic models without it.
   Scheduled to move up once phase 3 has delivered the corpus.
5. **Feedback.** Discrimination, difficulty strata, ceiling/floor census, and
   the persisted spot-audit queue (§9.2) with its ranking-withholding gate.
   Requires at least three model runs before it means anything — and feeds
   audit, never a filter on the scored corpus.

Phase 1 precedes phase 3 deliberately: settling the tagging, digest and shard
layout before 500 entries exist is what prevents a hand re-tag.

## 12. The review editor

Phase 3 puts ~500 entries through a human faithfulness read (§2.2), and the
Risks section calls that review the project's bottleneck and weak link. §12 is
the interface to it.

**This is not new machinery.** "Mark as checked" writes the `review` record the
schema already defines; the queue that feeds it is the set of `candidate`
entries §8 already excludes from reporting. What is missing is a surface where
a mathematician can see a theorem and judge it, rather than reading raw JSON.

### 12.1 What a reviewer sees

One entry at a time:

| Pane | Content |
|---|---|
| Title | `title`, when the result has one — "Hilbert's Nullstellensatz" |
| Statement | `input`, rendered — see §12.2 |
| Lean | the assembled declaration, exactly as `declaration()` builds it |
| Provenance | primary occurrence rendered as a citation, plus the other occurrences |
| Fixtures | each resolved fixture's statement and locator, with its A4/A5 verdicts |
| Witness | the `witness` term and its kernel verdict, or the `witness_note` |

The question the reviewer answers is narrow and answerable: *does the Lean say
what the statement says, as the source stated it?* Not *is it true*, and not
*can I prove it* — §2.2 is a read, not a proof.

### 12.2 `title` must never reach the model

`title` earns its place twice over. It removes ambiguity for the reviewer,
which is what motivated it. And two entries sharing a title is a merge signal,
which partly mechanises the semantic deduplication §2.1 otherwise leaves
entirely to human judgement.

It is deliberately **not** `Entry.name`. That field is the Lean identifier —
`SqrtTwoIrrational` — which `declaration()` assembles into the theorem the
model is asked to prove, and which sits inside `statement_digest`. `title` is a
different thing with a different lifetime: prose, optional, display-only.

**`title` never enters `input`, the prompt, or any digest.** "Prove Hilbert's
Nullstellensatz" is a retrieval cue: it converts the task from doing
mathematics into recalling a named theorem, and would inflate scores for
exactly the memorisation the twins in B2 exist to detect. Note the asymmetry
with `name`, which the model *does* see — a Lean identifier is a label the
declaration cannot omit, while a prose title is a hint the problem never
needed. This is the kind of constraint that erodes quietly, so it is a stated
rule and a test asserts `title` is absent from the assembled request.

### 12.3 Rendered mathematics lives in `input`

A reviewer needs to see

> For any ideal $I \subseteq k[x_1,\dots,x_n]$, the ideal of the zero locus is
> the radical: $I(V(I)) = \sqrt{I}$.

not a Lean expression and not ASCII prose. The rendering **is `input`**:
`input` carries LaTeX inline and the editor renders it with KaTeX.

The alternative — a separate `latex` field beside `input` — would create a
second stored representation of one theorem, free to drift from both the prose
and the Lean. Drift between representations is precisely what the faithfulness
review exists to catch, so the design should not manufacture a third place for
it. Models read LaTeX at least as well as ASCII mathematics, so nothing is lost
on the prompt side.

The twenty existing entries are rewritten this way during phase 1's migration.
That is the cheap moment: `input` sits inside `prompt_digest`, so rewording it
once entries carry measurements costs a re-run of the whole condition (§3).

### 12.4 Flagging reuses the review verdict

A reviewer who spots a broken statement may not be certain enough to retire it.
Rather than a fourth lifecycle state, the `review` record carries a verdict:

- `faithful` — promotes `candidate` → `active`.
- `unfaithful` — demotes to `candidate` with the reason recorded, and the entry
  leaves every headline until someone fixes and re-reviews it.

Retirement stays what §2.2 makes it: a deliberate act with a
`retired_reason`, not a side effect of one reviewer's doubt.

### 12.5 It is a server, and that is a departure

`ARCHITECTURE.html` and `docs/codemap.html` are static generated artifacts. An
*editor* writes back, and a static file cannot. So `hardy evals corpus browse`
starts a **localhost** server that reads the shards and writes edits to them —
a local development tool, bound to loopback, with no authentication and no
deployment story. Saying so here prevents someone later treating it as a
service.

Two rules on the write path:

1. **Every write passes the same validators as `corpus check`.** The editor
   must not be able to produce a corpus the CLI would reject — including the
   MSC granularity rule of §2 and the antecedent checks of §9.0.
2. **Consequences are shown before saving.** Editing `conclusion` invalidates
   that entry's review and stales its A-group measurements; editing `input`
   stales the B-group, which §3 notes cannot be repaired incrementally. The
   editor states *"this edit demotes 1 entry and stales 4 measurements"* and
   requires confirmation. That turns the digest design from an invisible
   constraint into a visible one at the moment it bites.

Edits are ordinary corpus changes: they bump `corpus_version` and require a
changelog entry like any other, per §3.

### 12.6 Scope

The MVP is the **review queue** and nothing else: one entry, the panes of
§12.1, two verdicts, a reason box, next. That is what the phase 3 bottleneck
actually needs.

Coverage dashboards, arbitrary field editing, filtering, the audit queue view
and the effective-sample-size display are all useful and all deferred. They are
how a tool becomes a project. Ship the queue, review a hundred entries through
it, and let that say what else is worth building.

**Placed at the start of phase 3**, not phase 1: its value scales with corpus
size, so building it before entries exist is premature, while authoring 500
entries through a human gate without it is needlessly painful.

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
  unaudited, and gates reportability for 500 entries. §12 makes it faster, not
  more reliable: nothing here measures reviewer agreement, and a second reader
  on a sample would, and is not planned.
- **The editor can corrupt the corpus faster than hand-editing could.** Its
  validators and its consequence preview are what stand between a fast review
  loop and a fast damage loop. Both are load-bearing, neither is optional.
- **Unwitnessed entries rest on the human read alone.** Where A6 has no
  mechanical witness, nothing but §2.2 stands between a vacuous statement and a
  field headline. The count of such entries is reported, not hidden.
- **The same-chapter rule narrows curator judgement but does not remove it.**
  §9.0's mechanical gate enforces "earlier in the primary text," not "prefer
  the same chapter." The cross-chapter justification requirement puts the
  remaining discretion on the record rather than eliminating it.
- **A one-entry correction still costs a full model re-run.** Component digests
  detect B-group staleness per entry but cannot repair it: a scoreboard is an
  immutable per-run artifact under one `Condition`, so corrected rows cannot be
  spliced back. At corpus scale this makes statement corrections expensive in a
  way baseline re-sweeps are not.
- **Clustering costs more power than it looks.** Drawing a field from two texts
  means roughly two clusters, not 125 independent items. Honest clustered
  intervals will be much wider than the item count suggests, and single-text
  fields yield no ranking at all — this is the right answer statistically and a
  real constraint on corpus composition, arguing for more texts per field than
  the two phase 3 currently plans.
- **Semantic refusal is not yet emitted.** C5's contamination reading needs a
  signal the harness does not produce; until it does, C5 reports three terminal
  classes side by side and the contamination number is unavailable rather than
  approximated.
- **No spend gate exists.** `describe_selection()` bounds runtime, not money;
  the runner records neither token ceilings nor pricing. A long, expensive run
  can pass any threshold derived from the current limits.
- **Fixtures widen the trust base.** Every gate in §9 is a mitigation, not a
  proof. A wrong fixture that is consistent and not too strong will silently
  mismeasure its entries, and only the spot-audit queue stands behind it.
