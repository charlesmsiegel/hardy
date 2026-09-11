# The corpus

This page explains what the Hardy corpus holds, why it holds only that, and
which of its schema decisions are load-bearing, for a reader who is about to add
statements to it or to read a number measured over it. The field-by-field guide
to the data itself is [`corpus/SCHEMA.md`](../../corpus/SCHEMA.md); this page is
the reasoning behind it. What the measurements over it look like on disk is
[the artifacts reference](../reference/artifacts.md), and the commands that read
and check it are in [the CLI reference](../reference/cli.md). How those
measurements are decided, and what each of their numbers is worth, is
[the evaluation page](evaluation.md).

This page supersedes the retired corpus design specification; code comments
that cite that specification by section number (`spec §N`) refer to the design
it recorded, whose substance is here.

## An instrument with two outputs

The corpus is a classified, versioned set of mathematical statements formalised
against Mathlib. It measures models, and it measures the formal library those
models depend on, and the second output is not a side effect of the first: it
has its own constituency. A Mathlib maintainer wants to know where supplying a
curriculum's own prior results changes what a model can prove, field by field
and level by level. That question is bounded and actionable. It is also weaker
than "does Mathlib cover the standard curriculum", which is the claim the same
data invites and does not support, so nothing here is shaped to make the
stronger claim easy to reach.

The schema is arranged so the second output falls out of collecting the first.
An entry records where the result appears in the literature, at what level, and
at what position in the text, because those are the facts a library measurement
needs and a statement collection can carry for free.

## The corpus holds statements only

No tier, no solve rate, no discrimination score, no `shard` field: nothing
measured and nothing derived. `Entry` (`src/hardy/corpus/problems.py`) has no
field for any of them, and `Entry.shard` is a property computed from the primary
MSC code rather than a stored value. A tier is a fact about one tactic ladder
against one Mathlib revision on one machine. It is not a property of a theorem,
and storing it in the corpus would make the dataset carry one machine's history
wherever it went.

The measurements therefore live outside the statements. The automation sweep
writes `evals/baseline.json` and every model run writes its own scoreboard under
`evals/scoreboards/`, both described in
[the artifacts reference](../reference/artifacts.md). A corpus-side
`measurements/` tree, holding the corpus-wide Lean measurements a third party
could recompute against their own ladder, is not built; today those figures live
only in Hardy's own `evals/` artifacts ([roadmap](../roadmap.md)).

The corpus is a directory rather than a file: `problems/<NN>.json` sharded by the
primary code's 2-digit MSC class, plus `sources.json`, `tombstones.json`,
`taxonomy/` and `CHANGELOG.md`. Sharding is a filing decision, made early because
re-sharding an existing corpus is a migration and because per-field authoring
wants independent working files. `load_corpus` (`src/hardy/corpus/catalog.py`)
concatenates the shards and validates uniqueness and the twin relation across all
of them, so nothing downstream sees the split.

## One entry, many occurrences, and the primary governs

A result is one entry citing several texts, not one entry per book. The
Nullstellensatz appears in most algebraic geometry texts; a copy per book would
duplicate the statement, split its measurements across ids, and make one theorem
look like several problems in every aggregate. So `occurrences` is a tuple of
`(source_id, locator)` pairs and the entry is the theorem.

**The first occurrence is primary and governs.** Everything that needs a single
answer reads it: the field the entry is filed under, the source level a report
stratifies on, and the text an antecedent must come from. The rest are
citations. The alternative is worse than it looks. If those questions were
answered existentially over all occurrences, an author could reach for whichever
text orders the material most favourably, and adding a book could move a headline
number. The primary occurrence pins it.

`locator` is a non-empty tuple of non-negative integers, `(chapter, section,
item)`, compared lexicographically, and `Occurrence` enforces both constraints.
That is not tidiness: an empty tuple sorts before every non-empty one and
`(-1,)` sorts before any real chapter, so an unconstrained tuple would let
malformed provenance satisfy a "strictly earlier" comparison without naming any
earlier result.

Occurrences are outside every digest, because adding a citation does not change
what a theorem says and must not invalidate a measurement. `source_issues`
(`catalog.py`) still checks that every occurrence names a text `sources.json`
carries, since a citation pointing at no text decides the field, the level and
the antecedent rule from nothing.

## MSC is canonical, the arXiv class is derived

MSC2020 is hierarchical, so a roll-up is a prefix operation and finer
granularity costs nothing later. A flat arXiv label can never be refined without
re-tagging every entry, so the arXiv class is computed from the MSC code rather
than stored beside it.

`Entry` requires a non-empty `msc` whose codes are published MSC2020 codes and
whose form names mathematics: `SUBJECT_CODE` accepts a section (`12Fxx`) or a
subsection (`12F10`) and refuses `12-XX`, the bare class, which is what a tagger
writes when they did not look, and `12-01`, which classifies a publication type.
A bare class would still work everywhere downstream: the field resolves, the
shard is found, a prefix selection matches. Only the precision is gone, and
recovering it means re-tagging by hand.

`src/hardy/corpus/taxonomy.py` resolves the arXiv class, the field label and the
reporting group **most specific first**: whole code, then section, then class.
The MSC name comes from the whole code alone, because a reviewer cannot check
`13A15` and can check "Ideals and multiplicative ideal theory", which is why the
vendored table stores code and name rather than a bare code list. MSC classes
are not homogeneous under an arXiv reading, and MSC 12 is the worst case,
spanning math.NT, math.AC, math.RA and math.LO, so a class-only table would file
a third of a class under the wrong archive. Where even that is wrong for one
entry, `arxiv_override` carries the exception, and it is validated against the
mapping's codomain: an unconstrained string would reintroduce exactly the
free-form classification this taxonomy exists to remove. A roll-up verifies the
whole code before taking a prefix, so `13ZZZ` does not come back as valid
commutative algebra to a caller that is not an `Entry`.

Two further decisions live in that module. The reporting group is a versioned
many-to-one map over classes, because the fields the corpus targets are not
2-digit classes: real analysis and measure is MSC 26 and MSC 28, and reporting
them apart would split one sample into two undersized ones. And the tables are
read from the corpus being loaded, through `taxonomy.using(root)`, not from
Hardy's own checkout: a released corpus binds its own taxonomy in its manifest,
so validating its entries against a different map would reject codes it carries
and accept codes it removed.

## Discrimination diagnoses; it does not filter

An earlier form of this design dropped zero-discrimination items from reporting,
on the reasoning that a problem every model solves and one no model solves both
carry no signal. That reasoning is right about signal and wrong about method.
Dropping items scored by the same models the report then compares selects on the
dependent variable, which distorts the very solve rates and comparisons it feeds.
It also destroys the corpus's most valuable property: a theorem retired because
the first panel all failed can never reveal that a later model uniquely solves
it, which is the discovery the instrument exists to make.

So low-discrimination items stay in, as declared difficulty strata. Only an
entry a human audit finds broken is retired, with a reason, and a retired entry
keeps its id and its place in its shard. The same reversal governs a pending
spot-audit: `Audit` (`problems.py`) is bound to the measurement panel that
raised it, and the intended response to a pending verdict is to withhold that
field's ranking claim rather than to drop the flagged entry, because the entry
was flagged precisely for discriminating and removing it can reverse which model
is favoured before anyone has found anything wrong with it. Nothing computes
discrimination today, so no queue is written and no audit record has been raised
([roadmap](../roadmap.md)).

## What reaches the model, and what does not

**`title` never reaches a model.** It is the result's common name, and it earns
its place twice over: it removes ambiguity for a reviewer, and two entries
sharing a title is a merge signal for deduplication a human would otherwise do
unaided. But "prove Hilbert's Nullstellensatz" is a retrieval cue. It converts
the task from doing mathematics into recalling a named theorem, and would
inflate scores for exactly the memorisation twins exist to detect. So `title` is
in no digest, in no prompt, and absent from `Entry.declaration()`, which a test
in `tests/unit/test_evals_problems.py` pins.

Note the asymmetry with `name`. A Lean identifier is a label the declaration
cannot omit, so the model does see it; a prose title is a hint the problem never
needed. The two are different fields with different lifetimes, and only `name`
is inside `statement_digest`.

**Rendered mathematics lives in `input`.** A reviewer needs to read the
statement as mathematics rather than as a Lean expression or as ASCII prose, so
`input` carries LaTeX inline and the viewer renders it. The alternative, a
separate `latex` field beside `input`, would create a second stored
representation of one theorem, free to drift from both the prose and the Lean.
Drift between representations is what the faithfulness read exists to catch, so
the design does not manufacture a third place for it. Models read LaTeX at least
as well as ASCII mathematics, so nothing is lost on the prompt side.

**Twins are perturbed statements, and the corpus keeps them honest.** An entry
with `expected: "false"` names, in `twin_of`, the true entry it perturbs, and
`ProblemSet._consistent` requires that target to exist, to be true, and to share
the twin's primary MSC code: a twin is by construction in the same field as the
statement it perturbs, and letting the two drift would silently move a result
between fields. A twin always runs batch, whatever mode its condition names
(`src/hardy/evals/runner.py`), because the staged loop grades every unverified
run partial and for a twin not verifying is the correct outcome; its budget is
recorded separately for the same reason. The sweep records a negation sweep only
for a twin, so `_truth_label_issues` (`src/hardy/evals/sweep.py`) refuses a
baseline whose recorded shape does not match the entry's current label:
relabelling a true entry as a twin keeps its statement digest, and without that
check the model would be asked to refute a claim the kernel can prove. A twin
the ladder closes outright is reported as a corpus finding, not as a model
result.

## Witnesses, and why the negation sweep does not see vacuity

The negation sweep looks as though it should catch a vacuous statement, and it
cannot. If `P` is vacuously true because its hypotheses are impossible or
overstrong, then `¬P` is false and the ladder finds no closer: the sweep comes
back clean on exactly the broken entry one would expect it to catch. The
negation sweep's real job is sign errors and refutably false statements.

Vacuity needs a stored artifact, so an entry carries a `witness`: a Lean term
instantiating its hypotheses. `witness_verdict` (`sweep.py`) checks it as
`theorem <Name>Witness : ∃ <binders>, True := <witness>` against the entry's own
imports, with `#print axioms` appended, and records `witnessed`, `broken` or
`unwitnessed`. Elaboration alone is not the check: `sorry` is a warning, so a
hole wearing a term's clothes elaborates, and it is the axiom report that
separates a witness from a hole. A broken witness is a baseline problem that
makes `hardy evals baseline` exit non-zero, and the finding is re-derived from
the baseline at every run rather than trusted from the sweep that wrote it.

Two cases are reported rather than passed. An entry with no binders at all is
`unwitnessed`, not trivially witnessed: a premise may live inside `conclusion`,
as in `∀ n < 10, ...`, and nothing in this module parses Lean to lift it out.
Binders an `∃` cannot bind, an implicit `{α : Type*}` or an instance `[Group G]`,
have no witness in this form, so the entry records `witness: null` with a
required `witness_note` saying why. An unwitnessed entry is one where nothing
but the human read stands between a vacuous statement and a field headline, so
the count travels with the number: `aggregate` (`src/hardy/evals/scoreboard.py`)
publishes `floor["active_unwitnessed"]` beside the headline.

## Candidate, active, retired

`status` is the lifecycle, and only an `active` entry reaches a headline number.

Promotion requires a human to read the canonical Lean statement against `input`
and against the entry's stated origin, and to record that they did. The
mechanical gate establishes that a statement elaborates and how automation
behaves; it establishes nothing about whether the Lean proposition faithfully
represents the source problem. A mistranslated or silently weakened theorem
passes every mechanical check, discriminates between models, and lands in a
field headline, and auditing only the highest-discrimination items does not
catch it, because a faithfully wrong statement need not discriminate unusually.

This is a read, not a proof. **No reference proofs are stored**, and nobody has
to be able to prove an entry for it to enter: broken problems are expected, and
finding them is part of what the corpus is for.

Prose describing a review is not a gate, so the record is bound to what was
read. `_an_active_entry_carries_a_current_faithful_review` (`problems.py`)
requires an `active` entry to carry a `faithful` review whose statement digest,
prompt digest, `msc` and reporting group all match the entry as it now stands,
so an edit or a re-tag drops the entry back to `candidate` instead of leaving a
stale approval standing. The classification is in there because a
wrong-but-syntactically-valid MSC code passes every taxonomy validator, and
field attribution is the headline claim. `expected` and `twin_of` are in the
prompt digest for a sharper version of the same problem: flipping an entry
between a true theorem and a false twin changes what a correct model response is.

A `Review` must also name a reviewer that is not whitespace and an ISO 8601
date, because the aggregation path trusts the status that record grants. The
review is recorded through `hardy evals corpus serve`
(`src/hardy/app/corpus_viewer.py`), which computes the digests server-side from
the entry it loaded rather than accepting them from the page, so a verdict sent
from a stale tab is refused rather than approving a statement that has since
changed.

Ids are permanent. A retired entry stays in its shard with `status: "retired"`
rather than being deleted, and `tombstones.json` records every id ever issued.
Validating uniqueness across the present shards cannot enforce that: delete a
retired row and the check sees only the new claimant. So `registry_issues`
(`catalog.py`) compares the registry against the merge base and rejects any id
that vanished or whose issue date moved, which is what keeps an external
citation valid.

## Antecedents, and why one never goes in `binders`

An antecedent is a prior result from the same text, preferring the same chapter
and reaching earlier only when needed, that Mathlib does not have. The rule is
fixed once and applies everywhere, because what counts as a reasonable
antecedent sets the scale of the whole uplift measurement, and a rule that
varied by field would put that inconsistency into a headline rather than into a
caveat. It is objective: anyone with the book can check whether it was applied.
It mirrors how the problem was meant to be solved. And intersecting it with what
Mathlib lacks keeps the set small and makes the result legible to a maintainer.

The tempting mechanism is to put the missing lemma in `binders` as a hypothesis:
sound by construction, no new machinery. It destroys the measurement silently.
`binders` are part of the canonical declaration and are injected into every run,
the bare condition included, so a hypothesis-encoded antecedent is present in
exactly the condition it was meant to be absent from: the difference between the
fixtured and bare conditions goes to zero for the gaps the mechanism was meant
to cover, and the entry states a stronger theorem than its source did. The
soundness argument is correct and irrelevant; the harness, not the logic, is what
decides this. `binders` therefore holds what the source theorem itself stated
and nothing else, and `_binders_never_carry_an_antecedent` (`problems.py`)
refuses an entry whose binders mention one of its own fixture ids. An authored
entry, one with no occurrences, has no primary text for an antecedent to be
prior in, so it may not carry fixtures at all.

Fixtures themselves are reserved rather than built: `Entry.fixtures` and its own
digest component exist, and no fixture store, injection path or locator-ordering
check exists to fill them ([roadmap](../roadmap.md)). The field is in the schema
from the start deliberately, since folding it into a digest later would
re-invalidate every measurement in the project at once.

## The axiom gate is a subset, not an equality

An accepted proof's `#print axioms` report must name nothing outside the three
standard axioms, `propext`, `Classical.choice` and `Quot.sound`, plus whatever a
human has explicitly approved. `classify` (`src/hardy/formal/audit.py`) checks a
subset rather than an equality, because `#print axioms` reports only what a proof
transitively uses: an equality gate would reject ordinary valid proofs that
happen not to need choice, and a fully constructive proof reports none of the
three. Order matters too. A forbidden axiom is fatal before approval is
consulted, so no approved-list entry can launder a hole, and no report at all is
a rejection rather than a clean sweep, since a caller that audited nothing has
established nothing. On the evals path nothing is approved at all: the batch
runner classifies against an empty approved list
(`src/hardy/workflows/batch.py`), so a graded run cannot widen its own trust
base. What Hardy does and does not control around that check is
[the trust boundary](trust-boundary.md); what a grade may claim on the strength
of it is [the output contract](output-contract.md).

A fixture set would be a per-entry, corpus-declared widening of that allowlist,
which is why it is the one part of the fixture design that is dangerous rather
than merely absent. Nothing widens the allowlist today.

## Canonicity: the numerator and the denominator range over the same population

A theorem in five of six standard texts is core curriculum; one in a single text
is specialised. That signal is the count of **distinct sources**, not
`len(occurrences)`: a result stated in one chapter and reused in three later ones
yields four occurrences in one book, which would read as core on a single source.

A bare count is meaningless without knowing how many books were looked at, so it
is reported against a surveyed-source denominator, "4 of 6 texts surveyed for
MSC 13". The rule that makes the ratio mean anything is that numerator and
denominator range over the same population: only occurrences in source and field
pairs marked fully surveyed count toward the numerator. Counting every citation
against a surveyed-only denominator mixes populations, can put the numerator
above the denominator, and misclassifies entries as core, which shifts the split
that a core-and-peripheral report is built on.

Nothing computes canonicity yet: `sources.json` carries no survey record, and
`hardy evals corpus report` counts occurrences per source rather than distinct
surveyed sources per entry ([roadmap](../roadmap.md)).

## Digests decide what is stale

Version numbers cannot express measurement validity and are not asked to. A
correction to one statement invalidates the measurements for that one id and
leaves the rest perfectly good, so staleness is a per-entry, per-component
comparison rather than a per-file one. Each measurement records the components it
actually depends on.

| Component | Covers | Where |
| --- | --- | --- |
| `statement_digest` | `name`, `binders`, `conclusion`, `imports`, `witness`, `witness_note` | `corpus/identity.py` |
| `fixture_set_digest` | the resolved contents of an entry's fixtures, transitively | `corpus/identity.py` |
| `prompt_digest` | the statement digest plus `input`, `expected`, `twin_of` | `corpus/identity.py` |
| `environment_digest` | the Lean identity and the host together | `evals/sweep.py` |
| `procedure_digest` | Hardy's deciding source, the tactic ladder and the sweep budgets | `evals/sweep.py` |

Each boundary is drawn for a reason.

The witness is inside `statement_digest`, so editing a valid witness into one
the kernel rejects cannot leave a cached non-vacuity pass looking current.

Fixtures are deliberately outside it. Folding them in would reach the
conditions that never load a fixture, and through `prompt_digest` it would stale
every bare model condition too. `fixture_set_digest` follows the pointer rather
than the id, because an edit to a referenced fixture's statement under a stable
id changes the assumptions of every dependent problem.

`input` is inside `prompt_digest` because the runner hands it to the model as
the informal claim, so rewording it can change solve behaviour with the Lean
untouched. `expected` and `twin_of` are in there because they shape the run
rather than describe it.

`environment_digest` exists because recording provenance is not the same as
governing reuse. A Mathlib upgrade changes elaboration, automation tiers and
witness acceptance, and a baseline that stored the Lean version without letting
it decide staleness would serve results measured against a library that no
longer exists. The host is in the digest as well: machine speed turns the
sweep's process backstop into timed-out attempts, so a tactic that times out on
a slow machine and closes on a fast one gives one statement two different tiers.
The refusal names both machines, because "environment digest" alone reads as a
mystery to whoever hits it.

`procedure_digest` exists because the corpus and the library are not the only
things that can change. `__version__` is fixed at `0.1.0` across every checkout,
so hashing it alone would accept measurements produced by different sweep logic,
a different axiom parser or a different notion of a successful elaboration.
`DECIDING_SOURCES` (`sweep.py`) therefore hashes the bytes of the modules that
decide a sweep's outcome: the sweep itself, the axiom audit, the Lean driver,
the Lean syntax reader, and the two corpus modules that assemble a declaration
and compute its identity. It is **deliberately conservative**: editing a comment
in one of them stales the baseline, and that is the cheaper of the two errors,
because the alternative is a measurement silently attributed to code that did
not produce it. The sweep budgets are in there beside the ladder for the same
reason, since the wall backstop moves attempts between timed out and closed,
which moves tiers. Model runs have their own counterpart digest over Hardy's
deciding source, recorded on a scoreboard's condition, described in
[the artifacts reference](../reference/artifacts.md), and reasoned about on
[the evaluation page](evaluation.md).

Source bytes are hashed with line endings normalised
(`source_digest`, `src/hardy/evals/digests.py`). `.gitattributes` pins
`corpus/**` and `evals/**` as `-text`, because their bytes are hashed and a
converted checkout would make a published version verify clean nowhere; `src/**`
is not pinned, so a Windows checkout of the same commit can hold CRLF, and
hashing raw bytes there would give identical logic two different digests and
refuse a measurement everywhere else for no real reason.

### The manifest binds the changelog head

Content versions answer a different question from measurement staleness:
`schema_version` is the format, `corpus_version` is the content, three-level,
where a patch corrects, a minor adds and a major breaks.

Asserting only that `corpus_version` equals the changelog head cannot detect an
unversioned edit: a shard changes while both strings stay put and the check
still passes, which makes a published version non-reproducible. So the head
binds a **manifest digest** over every content file, and the heading reads
`## <version> - <date> - manifest <digest>`. `manifest_digest` (`catalog.py`)
hashes the shards, the taxonomy tables, `sources.json`, `tombstones.json` and
the fixture and analysis-plan files when they exist, each under its posix-shaped
relative path, because `str(Path)` yields backslashes on Windows and an
unchanged corpus would otherwise hash differently per platform. A corpus-side
`measurements/` tree, were one built, would sit outside the manifest:
re-sweeping a baseline against a new Mathlib revision changes no content and
must not manufacture a release. `CHANGELOG.md` is outside it because the head is
where the digest is written, and hashing the file the digest lives in could
never settle; `SCHEMA.md` is outside it so that an edit to a paragraph of prose
is not a content release that invalidates every scoreboard bound to the
manifest.

Two gates need a historical anchor rather than the working tree, because a tree
that has been edited in both places is self-consistent. `release_issues` compares
the manifest against the previous release's changelog head, which already carries
both the version it names and the manifest it bound, and `registry_issues`
compares the id registry against the merge base. A corpus version must also
increase, since rejecting only an unchanged version would let a release move the
chronology backward. CI supplies both anchors with a `git show` of the merge
base, and `hardy evals corpus check --since` and `--since-registry` run the same
two comparisons locally.

The analysis plan the design puts inside the manifest, fixing the hypothesis
family and the multiplicity adjustment, is not written: `analysis-plan.json` is
covered by the manifest if it appears and no corpus carries one
([roadmap](../roadmap.md)).

## Incremental sweeping follows; incremental model reuse does not

Per-component staleness buys an incremental sweep, and at corpus scale that is
the difference between tractable and not: a full ladder over thousands of entries
is hundreds of hours serially, and a one-line correction should cost one entry.
`sweep()` (`evals/sweep.py`) takes the prior baseline and carries forward each
entry whose statement digest matches, but only when the prior shares the
environment and the procedure identity, so a Mathlib upgrade or a change to the
sweep code invalidates every row at once rather than letting rows cross that
boundary. The carried row must
also have the shape the entry now needs, which is what re-sweeps a relabelled
twin.

The model side has no equivalent. A scoreboard is an immutable per-run artifact
carrying one condition, so a corrected entry cannot be re-run and spliced back:
the fresh row and the untouched rows would belong to different runs, and no
single condition would truthfully describe the result. Digests can still say
which model rows went stale, which is worth having, but acting on it means
re-running the whole condition. A condition-preserving cache that re-verified
every reused row's code, environment and prompt identity would change this, and
nothing here assumes one.

## Contamination is about prior formalisation

The artifact under evaluation is a Lean proof. A memorised informal proof
supplies the mathematical idea and almost nothing else: not the Mathlib lemma
names, the tactic sequence, the coercions or the `simp` normal forms, and
formalisation is most of the work even when the proof is known. A model that
knows a field's standard techniques because it read the field is a model that
will help a mathematician working in that field, which is the signal rather than
noise in it.

The risk that remains is an exact Lean artifact in training data, which is why
textbook exercises that were never formalised are a better source than benchmark
solutions published as Lean. It is also why a twin's reading is soundness first
and contamination only second: proving a false twin is unsound whatever the model
read, but declining to prove one is only evidence of contamination if the model
said the statement was false rather than merely failing to land a proof.
Distinguishing those needs a semantic refusal signal the harness does not emit,
so the terminal classes are reported side by side and the contamination number is
unavailable rather than approximated.

Textbook exercises may be harvested because the mathematics is not
copyrightable. `input` is always our own restatement rather than the book's
prose, no solution-manual text is reproduced, and `occurrences` records a
citation, which is what any paper does.

## Limits

Two gaps are open by decision rather than oversight, and both are recorded here
so neither is rediscovered as a bug ([roadmap](../roadmap.md)).

**A review does not bind the origin it was read against.** `occurrences` and
`rationale` are in no component digest, so changing an entry's primary citation
leaves an approval current although the reviewer no longer attests the stated
origin. Adding an origin digest changes what a review record is, not just how
one is checked.

**Scoreboard rows carry no prompt digest.** Nothing reuses a model run, so there
is no reuse decision for a per-row prompt digest to govern; it belongs with the
reporting work that would need it.

Three further limits shape what can be asked of the corpus today. Selection by
MSC code, arXiv class, difficulty, source or level is not implemented: a run
selects by id, tier, twin and status. Per-field aggregation is not implemented,
so the headline is restricted to `active` entries at tier 2 or above and reports
no field breakdown; `hardy evals compare` pairs slots between two boards
descriptively and is not the per-field paired report with a multiplicity
adjustment the design describes. And the evals runner drives one backend, so a
comparison across providers cannot be run at all, however the corpus is
classified ([roadmap](../roadmap.md)).

## Where the statements live

Statements are committed on the `corpus/curation` branch and code on `main`.
Harvesting statements, recording faithfulness reads through the viewer and
cutting a corpus release all happen on the corpus branch, which rebases on
`main`; a change that is genuinely both, such as a harvest that also improves the
ingestion skill, is split so that no corpus commit carries a source or test edit
along with it. The reason is diff size: one harvest was 39,000 lines of JSON, and
a harness change buried in that is not reviewable. The split also means a code
change is testable against the base corpus on `main` before the statements that
exercise it exist. `AGENTS.md` states the rule for anyone working in the
repository.
