# Evaluation

This page explains how Hardy measures a model against the corpus, and how it
measures itself, for a reader about to run a sweep, read a scoreboard, or judge
what one of those numbers is worth. What the corpus holds and why is
[the corpus page](corpus.md); the shape of every file these commands write is in
[the artifacts reference](../reference/artifacts.md) and their options are in
[the CLI reference](../reference/cli.md). This page is the reasoning behind
them: what each measurement decides, and what it cannot say.

## A solve rate says nothing until the list says what a solve is worth

Competition sets are heavily one-tactic or heavily trained on. A model that
closes forty of them with `exact?` has demonstrated that Mathlib contains forty
theorems. Hardy's ground truth cannot be gamed, because the kernel and the axiom
audit decide what was proved ([trust boundary](trust-boundary.md)), but the
*difficulty* of what was proved is a matter of opinion unless something measures
it.

So the corpus carries a measured floor beside it. `hardy evals baseline` runs a
fixed ladder of sixteen single tactics and eight chains (`SINGLES` and `CHAINS`,
`src/hardy/evals/sweep.py`) against every canonical statement, and each entry
lands in a tier derived from which tactics closed it. The list is a decision
rather than a discovery: changing it re-tiers the whole corpus, which is why it
is hashed into the baseline's procedure digest. `tier_of` computes the
tier from `closed_by` and nothing sets it independently:

| Tier | What closed it | What it is good for |
| --- | --- | --- |
| 0 | a single tactic other than `exact?`, `apply?` or `hint` | a sanity check; excluded from the headline |
| 1 | one of those three searchers, and no other single | the statement is essentially in Mathlib: useful for testing search, not proving |
| 2 | no single tactic, but a chain | tactic-level planning |
| 3 | nothing on the ladder | an intermediate statement is required |

`hint` sits with the searchers rather than with the other singles because it
runs `exact?` internally, so a goal it closes is a library-search hit and not an
automation close.

A tier is a fact about one ladder against one Mathlib revision on one machine,
never a property of a theorem, which is why it lives in `evals/baseline.json`
and not in the corpus. The reasoning for that split is on
[the corpus page](corpus.md).

## Three kinds of measurement, and what each cannot say

Grouping them by what is being measured keeps three different epistemic
positions apart.

**Lean and Mathlib measurements, with no model involved.** The elaboration
check, the automation ladder above, the same ladder run against each statement's
negation, and the kernel-checked witness that a statement's hypotheses are
satisfiable at all. These are the only measurements here reproducible by a third
party with no provider account, and the ladder is the headline dataset artifact:
a count of undergraduate problems Lean's automation cannot touch. The baseline
records the ladder, the Mathlib revision and the host it ran on so that someone
can recompute it against their own ladder and disagree, though no corpus-side
measurement file carrying those figures is published
([the corpus page](corpus.md)). What they cannot say is that a statement means
what it should. The negation sweep catches sign errors and refutably false
statements and is blind to vacuity, because a vacuously true statement has a
false negation the ladder also fails to close. That is what the witness is for, and why an entry
that carries none is reported unwitnessed rather than quietly counted
([the corpus page](corpus.md)).

**Model conditions, one run each.** The bare prove condition against stock
Mathlib is the primary measurement. The twin condition hands the model a false
neighbour of a true statement, where the correct behaviour is failing to prove
it. Repeats measure variance across attempts at the same entry. Fixtured
proving, the same theorem with the source's own prior results in scope, is not
built ([roadmap](../roadmap.md)). What none of these can say on its own is why
a model failed; a condition is a treatment, and the record of it is a label,
not an explanation.

**Derived reports over runs, with no Lean.** Solve rates with intervals, tier
profiles, totals, pooled views across boards, and the descriptive slot pairing
`hardy evals compare` performs. These are arithmetic over rows that were
decided elsewhere, so their honesty is entirely a question of what the rows
already established. They cannot manufacture an outcome, and they cannot repair
one: a report over a board whose rows are unauditable is a report about an
unauditable board.

## Heartbeats first, wall clock second

"Quickly" measured in seconds flaps across containers. `maxHeartbeats` is close
to deterministic for one toolchain, so every attempt runs under a fixed
`HEARTBEAT_BUDGET` of 200000, records the heartbeats it used, and the tiers are
decided by heartbeats. Wall seconds are recorded beside them and decide nothing.

The mechanism has a trap in it. Mathlib's `#count_heartbeats in` wraps a command
in `set_option maxHeartbeats 0`, so it cannot bound anything on its own: used
alone it counts an attempt that never stops. An inner `set_option maxHeartbeats
B in` overrides it and the count is still taken, so `_block` (`sweep.py`) emits
the count wrapper first and the budget second, in that order. The file header
also sets `set_option Elab.async false`, so a count is attributable to its own
declaration rather than to whatever the elaborator happened to be doing.

The kernel does not count heartbeats at all. `decide` can spend minutes there
under any budget, so every process carries a wall backstop as well:
`max(config.lean_timeout, WALL_BACKSTOP_FLOOR)`, which is 600 seconds at the
default `lean_timeout` of 180 (`src/hardy/app/evals.py`). Both budgets are
inputs to `procedure_digest_of`, because the backstop moves an attempt between
`timed_out` and `closed`, and that moves tiers.

## Two stages, so a closer cannot borrow a neighbour's proof

`exact?`, `apply?` and `hint` cite whatever is in the environment. A named
theorem closed by `simp` two lines up is a valid citation for `exact?` on the
same statement, which would credit the searcher with a proof it did not find.
The sweep therefore runs in two stages (`sweep_proposition`, `sweep.py`).

Stage A puts every single and chain into one process as an anonymous `example`
carrying the entry's own binders, so nothing enters the environment. The binder
shape is load-bearing: `stage_a_source` presents hypotheses as local context,
the way the real declaration does, rather than as a leading universally
quantified goal, because a tactic that expects hypotheses in context can fail
the second and close the first. An attempt is a *candidate closer* when its
lines carry no error, no `unsolved goals`, and no `declaration uses 'sorry'`
warning. If the whole process hits the wall backstop, the sweep falls back to
one process per tactic for that entry, so one runaway tactic cannot mark the
rest unknown.

Stage B takes each candidate alone, as a named `theorem`, followed by
`#print axioms`. It is closed when elaboration succeeds and the printed axioms
are within `audit.STANDARD`. A candidate that fails confirmation is recorded
`unconfirmed`. That should not happen, and the command says so on stderr, but it
is a record rather than an exit code: the artifact keeps what happened.

## The baseline refuses an incomplete record

`Baseline`'s own validator will not load a truncated tactic record. Whenever an
entry's `elaborates` is true, its attempts, and its negation's attempts when a
negation was swept, must name exactly this baseline's singles and chains, no
fewer and no more; an entry with `elaborates: false` must carry no attempts at
all.

Without that rule, `attempts: {}` beside `closed_by: []` and `elaborates: true`
would still satisfy the check that a tier follows its closers, because the two
empty sets agree and `tier_of(())` is 3. A statement nobody measured would pass
as one every configured tactic was tried against and failed, which is exactly
the entry a headline count of tier-3 problems is made of.

## The environment gate is not advisory

Before `hardy evals run` spends anything, `staleness` (`sweep.py`) checks the
baseline it was handed against this checkout: the corpus digest it was measured
over, the singles and chains against the code's own constants, the heartbeat
budget, the procedure digest, all four fields of the Lean environment identity,
the host, and that the baseline records no problems with the list. Any of them
is a refusal, not a warning.

That is deliberately unlike `hardy doctor`'s toolchain pin check, which reports
and lets the caller proceed. The whole point of the tier file is that a Mathlib
upgrade can turn a tier-3 problem into an `exact?` one-liner overnight, so a run
under a different Mathlib is not a run on this list, and a scoreboard that said
otherwise would be worse than no scoreboard.

One more refusal happens before anything runs: `hardy evals run --backend codex`
is rejected outright, because the batch runner, the canonical reader and staged
tool-event counting are all Claude-shaped (`src/hardy/app/evals.py`). `--model`
varies freely within the backend, so a comparison between models is reachable
today and a comparison across providers is not, however the corpus is
classified ([roadmap](../roadmap.md)).

## One row, one outcome, read off the run directory

A row's outcome is mechanical: `batch_row` and `staged_row` (`scoreboard.py`)
derive it from the run directory alone, and the validator re-derives it later
from the same files.

| `expected` | `outcome` | When |
| --- | --- | --- |
| true | `solved` | batch: the run terminated `verified` with a clean axiom audit. staged: the run reached `completed` with a `kernel_verified` formal grade **and** an `agreed` canonical comparison. |
| true | `solved_other` | staged only: `kernel_verified`, but the canonical comparison came back `disputed` or `unavailable`. The model proved something and the record cannot say it was this statement. |
| true | `unsolved` | anything else, with `terminal_reason` beside it. |
| false | `refused` | the twin criterion: terminal in `no_proof_submitted` or `axioms_rejected`, no accepted `submit_proof`, and every Lean-accepted `check_proof` carried a hole. |
| false | `exhausted` | a turn or wall-clock limit. Hardy stopped waiting, which is not a refusal. |
| false | `graded` | anything else, including a twin reported `verified`. A harness bug, reported in red rather than hidden. |
| either | `invalid` | the recorded-run audit could not make sense of the artifacts. |

`solved_other` exists because collapsing it into either neighbour is a lie in
one direction or the other. Calling it `solved` credits the model with proving
this statement when the only independent reader of the two Lean statements
either disagreed or never answered; calling it `unsolved` discards a
kernel-verified proof. The reader's own verdict is stored beside the row, so the
distinction is auditable rather than asserted.

**Twins never run staged.** The runner sets `mode = "batch" if entry.expected ==
"false" else condition.mode` (`runner.py`), whatever `--mode` says. The staged
workflow grades every unverified run `partial`, which is the correct grade for
an unfinished proof and useless as a reading of a refusal, and the refusal
criterion above is defined over a batch run's own submissions. So `--mode
staged` with twins included runs the true entries staged and the twins batch,
and each row says which. The validator computes the mode it expects from the
condition and the entry rather than believing the row's own claim.

**`invalid` rows are missing measurements, not failures.** A row the audit
cannot read says nothing about the model: the artifact is unreadable, which is a
fact about the harness. The tier aggregate reports the invalid count in its own
field beside the rate, so a reader can see how much of `n` is unmeasured. It does
not yet remove those rows from the denominator: `_tier_aggregate` puts every true
row into `n` and only `solved` rows into the numerator, so an invalid row
currently depresses a solve rate exactly like a failed proof, and in a
comparison could manufacture discordance that reads as a model difference.
Excluding them from ranking-capable reports, and withholding a claim entirely
where the invalid rate is high, belongs with the reporting layer that is not
built ([roadmap](../roadmap.md)).

Coverage is reported the same way. `floor` is computed over the baseline's
entries, so a partial baseline makes every floor a floor over the swept subset;
the aggregate carries `baselined` and `active_baselined` denominators so those
numbers can be read against what was actually swept, and `active_unwitnessed`
counts only entries that have a baseline row at all. Undercounting a caveat is
worse than undercounting a score.

## Nothing hashes the scoreboard, and everything in it re-derives

A staged run's manifest hashes its own directory
([artifacts](../reference/artifacts.md)). A scoreboard is covered by no hash,
the same way a manifest is not covered by one, and `hardy evals check` is what
makes that safe: it re-derives each row field by field from the run directory it
names, recomputes the aggregates from the rows, and checks the rows against the
selection the condition describes. The validator's job is that every figure a
scoreboard states is re-derivable from artifacts that *are* hashed.

The selection check has a sharp edge worth stating. Every entry in the selection
must have its repeats present, in the same order the runner would produce them,
with no rows outside the selection. When a board is marked `interrupted`, its
rows must be an exact *prefix* of that order rather than merely a subset,
because a board that could delete only its failed rows and call itself
interrupted would pass with an inflated solve rate. A board with every expected
row present must carry a `finished_at`, and an interrupted one must not carry
one at all, since the runner writes `finished_at` only on its closing
non-interrupted write; a board claiming both was edited after the fact.

Concurrency does not get to weaken that. The runner allocates one slot per
`(entry, repeat)` in selection order and persists only the completed contiguous
prefix after each completion, so an interrupted board is a genuine prefix and a
finished board is in exact run order. Appending rows as workers finish would
satisfy neither check. The sweep needs no equivalent care, because it builds a
dict keyed by entry id and its elaborations are independent processes over a
read-only project.

## Pooling: what makes two boards the same experiment

A scoreboard is one condition on one day, and accumulating across days is a view
over several boards rather than a mutated artifact. The bar `hardy evals pool`
has to clear is one sentence: **the pooled result must be indistinguishable from
one long sequential run made at a single moment, had every sample been ready and
the compute been available.**

The pooling key is `(run_procedure_digest, environment_digest)`.

**The source set is a denylist, not an allowlist.** `run_source_paths`
(`identity.py`) digests every `src/hardy/**/*.py` except an explicit exclusion
list, with line endings normalised so a CRLF checkout of one commit does not
disagree with an LF one. Two alternatives were tried and rejected on evidence.
A hand-written allowlist drawn from the obvious imports omitted `closers`, which
decides whether a proof closes, and `usage`, which computes the very token
counts a pool aggregates, and ten more besides. A derived import closure reached
79 of the package's 81 modules, because the runner imports its runtime factory
from a hub module. Inclusion by default inverts the failure mode: omitting a
deciding module becomes impossible, and excluding one is a deliberate line a
reader can challenge.

Being wrong in the other direction is quieter and cost more. `summary.py` only
reads finished boards, so it can change nothing about a run, and including it
moved the key and orphaned every scoreboard on disk: boards stopped pooling and
`hardy evals todo` reported `boards_counted: 0` for models that plainly had
boards. The rule that keeps the exclusions honest is testable rather than
editorial: nothing the digest covers may import a module the digest excludes.

The prompt templates stay separate keys rather than folding into the source
digest, so a changed digest says which input moved. The `prompts/` Python that
renders and interpolates those templates is in neither prompt hash and is picked
up by the source denylist, which is the reason the source set cannot be replaced
by a list of prompt files.

Three fields are kept out of the key, each for a stated reason, and `pool()`
correspondingly declines to ask for the three checks built on them:

- `problems_sha256` must drift, because entries are added by design.
- `baseline_sha256` drifts as the baseline grows. What matters is the
  baseline's own environment and procedure digests, which is the property that
  says a carried-forward tier was measured under a matching environment and
  procedure. A file hash would reject compatible baselines without
  establishing compatibility for the ones it accepted.
- `source_revision` is provenance, not identity, which is what lets a
  corpus-harvest commit leave the pool intact.

Two further refusals are structural. No `(id, repeat)` pair may be claimed by
more than one board, since the same entry run twice under one condition is a
fact to report and not a tie to break silently. And a board recording no
`run_procedure_digest` is refused as carrying nothing rather than treated as
agreeing with its neighbours; a blank is not a match.

**Budgets are frozen, and workers are the only knob.** `lean_timeout` stays at
180 and the wall backstop at 600, chosen once, because raising either would
invalidate the baseline carry-forward and un-pool every prior run. Worker counts
are the one thing free to vary, and both the sweep and a run take `--workers`
(defaulting to 1, since the sweep is CPU-bound on Lean elaboration while a run
is mostly waiting on a provider, and the right number differs per machine).
Every row records the `workers` value it ran under, so its `wall_seconds` is
self-describing rather than a bare number a later reader mistakes for serial
time.

**Which numbers survive concurrency.** Outcomes, token counts and cost are
unaffected by worker count, because the frozen backstop sits far enough above an
ordinary elaboration that contention would have to stretch one by roughly two
orders of magnitude to move an attempt between `closed` and `timed_out`. Those
are the headline figures. Summed `wall_seconds` is not: under N workers it
overstates serial wall clock by a contention-dependent amount, so the pool
attaches a note saying how many workers the figure was measured under, or that
the concurrency is unrecorded, and never presents it as a serial time.

## `hardy evals compare` describes; it does not attribute

Comparison reads two boards, authenticates both, pairs their exact
`(id, repeat)` slots, and reports the pairs. It computes no mean, names no
winner, runs no significance test and attributes nothing causally
(`compare.py`).

A board that fails its own audit is not dropped: its findings are carried into
the result and its rows are retained, but no unauthenticated row is credited
with an outcome or a usage figure. Reporting a broken board as absent would hide
the breakage; crediting it would launder it.

The control fields each come back `equal`, `different` or `unknown`, and
`unknown` never collapses into equality. A field is unknown when the evidence
for it is missing or disagrees across a board's own rows, which is a different
fact from two boards agreeing. The caller names each field it *intended* to vary
with `--vary`, so an unintended difference is reported as one rather than left
for a reader to notice.

**The reader model joins the condition.** The canonical reader is chosen outside
the recorded condition, from configuration (`staged.py`), while its verdict is
what separates `solved` from `solved_other`. Two boards with identical
conditions can therefore have been graded by different readers, and the
disagreement lands in the very outcome the comparison is about. So `_controls`
reads the reviewer's model, backend, template digest, response-schema digest and
per-slot prompt digest off each row's own `canonical.json`, not off the
condition label, and the same is done for the strategy, history mode, context
policy and shared tool budget: a condition label alone cannot establish what
actually ran.

**Recorded provenance is not an independent attestation.** Source, host,
concurrency and treatment identities describe an experiment; they do not certify
it. No old board is restamped to become comparable with a new one.

**Spend is a lower bound.** Every usage figure carries its own coverage,
`complete`, `partial` or `missing`, and complete means the provider reported as
many exchanges as the run recorded. Canonical review usage is counted off the
reader's own trajectory, from completed provider reports, because a requested
exchange that died before returning a result leaves no record; those figures are
therefore always partial and the reader's exchange count is reported as unknown
rather than as a total. All recorded attempts contribute usage, unsolved ones
included, and an absent report is not zero.

## The certified statistic, and what it is not

`certify` (`certification.py`) reads a board and reports, per k, the observed
success within each problem's first k declared attempts, under a per-attempt cap
on independent verifier calls against a frozen claim. Attempts are declared
prospectively, before the run, and each declared slot survives interruption.

It is not an IID pass@k estimator, not a confidence interval, not a cap on every
Lean process the run starts, and not an independent kernel replay: an eligible
staged attempt rests on the same recorded Lean and canonical trust boundary
every other row does. Three figures are reported rather than one, because
missing evidence must not quietly shrink the denominator: an observed lower
bound over what actually solved, an observed upper bound that keeps
unauthenticated and unrun slots open, and a certified value that appears only
when every declared slot is present and eligible. A completed unsolved attempt
contributes zero rather than disappearing.

The limits are reported in the certificate itself rather than left to a reader.
Batch receipts cannot establish the verifier-call cap and stay provisional. Lean
CPU seconds are unknown. Scheduler makespan covers producer start through board
seal, and worker occupancy is executor time rather than CPU utilization. Hard
provider token and invoice limits are unestablished, so requesting a certificate
about them yields explicit provisional reasons instead.

## Exposure cohorts, and the question they do not answer

An exposure plan freezes a source, index, query and split before a run and binds
them to the run's identity; the receipts bind owner-written declarations to what
was actually forwarded to the provider (`exposure.py`). Rows land in four
cohorts, kept separate and never summed: exact repeat, related transfer,
declared local held-out, and unknown.

An attributed split names prior relationships. Neither text similarity nor the
absence of a memory establishes independence, so the cohorts are declarations
that the receipts authenticate, not inferred semantic equivalence. Missing or
truncated coverage cannot establish held-out performance, and a relabelled run
identity or a stripped exposure field is rejected rather than accepted at face
value.

The boundary is worth stating plainly: **local completeness says nothing about
provider pretraining.** Everything here concerns the declared local source
universe. What the model saw before it arrived is unknown, and no arrangement of
these cohorts makes it known.

## What the reuse fixture decided

`tests/unit/test_project_reuse.py` builds a project, reopens its ledger store,
rebuilds and serializes its retrieval index, and then compares retrieval off
against retrieval on through the same deterministic consumer. Six categories are
expected to come back: an exact checked lemma with its source artifact and
current policy acceptance, a stable concept through an active scoped alias, an
exact representation with its semantic description, an exact context with its
parent and scoped members, an open research goal carrying no proof claim, and a
goal-linked current approach beside an explicitly historical blocked one. Each
scores 0 with retrieval off and 1 with it on. Goal and approach share a query,
so these are six category expectations rather than six independent theorem
trials, and the fixture makes zero provider and Lean calls: it establishes
discovery and delivery after a restart, not a cost or theorem-success gain. The
restart is a ledger and index restart, not a whole-process or provider one.

The decision it produced is that the existing ledger, derived index and
authenticated retrieval already cover durable reuse, so no separate proof-memory
database is built. The condition for revisiting it is equally explicit: a
residual category that genuinely needs its own lifetime and API, portable
tactic or strategy lessons being the candidate, or broader model trials showing
that scripted discovery cases were not the thing that mattered.

## Power, clustering, and the reports that are not written

Two facts about the data bound every ranking claim someone might want from it,
and both are reasons the reporting layer is smaller than the data.

**Repeats are not sample size.** Rows are stored one per repeat, so feeding rows
straight into an interval or a paired test would treat correlated attempts at
the same theorem as independent samples: 125 problems at 10 repeats would present
as 1,250 observations and narrow every interval by roughly a factor of three.
Any such statistic has to collapse each `(model, entry)` pair to one declared
item-level outcome first, under a rule fixed in advance and recorded, and where
repeat-level variation is itself the question it belongs in a separate per-item
statistic rather than in the sample size.

**Items are not independent either.** A field's entries are drawn from as few as
two texts, so exercises sharing a chapter, a prerequisite chain or an author's
habits succeed and fail together. Collapsing repeats removes one source of
dependence and leaves this one untouched, so intervals and tests belong
clustered on the source text, with each field's effective sample size reported
beside its item count. A field whose entries come from a single text carries no
ranking claim at all: one cluster is one observation, however many exercises it
contains.

The reporting those two facts imply is not built ([roadmap](../roadmap.md)).
`scoreboard.py` aggregates by tier and has no field counterpart, so the headline
is restricted to active entries at tier 2 or above and carries no field
breakdown. `hardy evals compare` pairs exact slots descriptively and is not the
paired per-field report the design describes: there is no McNemar test, no
clustered interval, no refusal to emit a ranking when a confidence interval
crosses zero, and no power curve under a stated effect size in place of a bare
"needs N more pairs". A curve is the honest output there rather than a number,
because a paired test turns on the imbalance between the two one-sided
discordances and not on any fixed count of them: a handful of one-sided
discordances can be significant while many evenly split ones never are. To
illustrate the scale, 125 entries in a field yield on the order of 37 discordant
pairs for a two-model comparison, short of the roughly 70 wanted *under the
assumption* that two thirds of them fall one way. That figure is an illustration
under a stated effect size, not a threshold that holds generally. Nor is the
multiplicity adjustment written. That one
cannot be a per-invocation calculation, since a caller passing one pair at a
time would make every family size one and license exactly the claims the gate
exists to refuse; it needs a versioned analysis plan committed beside the
corpus, naming the models and fields in advance and binding mode, limits,
repeats, fixture condition, selection and the repeat-to-item rule. No corpus
carries one.

The retrieval side has the same shape of gap. Counting every query shape that
surfaced the expected lemma cannot say whether a shape earned its complexity,
because a constants query returns a superset of a conclusion query often enough
that both counters rise together, and the table would then credit the shape that
changed nothing. What answers it is an ablation: rank each case with the
conclusion shape alone, then with each shape added, and report per shape the
cases it *rescued* and *promoted*, with raw co-occurrence beside them and
labelled as the different thing it is. Running that hermetically needs cassettes
that record the identity of whatever answered each query, the endpoint for a
remote engine and the toolchain pin plus `lake-manifest.json` digest for
`#find`, a runner that refuses to replay a set whose recorded identities
disagree with each other, and seconds replayed from the recording rather than
measured off the replay, since a cassette read takes microseconds where the live
call took twenty seconds. No cassettes exist and no such harness is built
([roadmap](../roadmap.md)).

One further number is unavailable rather than approximated. Reading a twin's
non-proof as contamination requires the model asserting the statement is false,
which the harness does not emit as a signal, so the terminal classes are
reported side by side and no contamination rate is computed
([the corpus page](corpus.md)).

## Measuring the harness itself

The other half of evaluation is deterministic acceptance fixtures over Hardy's
own behaviour. They record what the suite exercises, never model performance or
the mathematical correctness of an arbitrary generated artifact: test doubles
supply external judgments where a fixture names them, while the ledger,
artifact, identity and orchestration owners are real. The third column is the
boundary each fixture leaves standing; what it does cover is stated in the
test's own docstring.

| Feature | Fixtures | What it does not establish |
| --- | --- | --- |
| ledger and policy | [contracts](../../tests/unit/test_ledger_contracts.py), [store](../../tests/unit/test_ledger_store.py), [policy](../../tests/unit/test_ledger_policy.py) | That any verification claim behind a record is real: capability stand-ins supply it. Schema consistency is not evidence policy, and exact history is not authority. |
| package direction | [boundaries](../../tests/unit/test_module_boundaries.py) | Anything about behaviour inside a package; what is checked is import direction, statically and at runtime. |
| statement and trust preparation | [formalization](../../tests/unit/test_formalization.py), [admission](../../tests/unit/test_admission_policy.py) | That an admitted assumption is true, or that Lean accepted anything: these decisions run with no session, provider or real Lean. |
| strategy contract | [contracts](../../tests/unit/test_strategy_contracts.py) | That an attempt was any good. The seam records bounded attempts without grading them. |
| manuscript inventory | [manuscript](../../tests/unit/test_manuscript.py) | Anything semantic. Exact spans and bounded counts locate text; they do not read it. |
| project semantics and acceptance | [acceptance](../../tests/unit/test_core_b_acceptance.py), [graph](../../tests/unit/test_ledger_graph.py), [views](../../tests/unit/test_ledger_views.py) | That an authenticated establishment rests on real Lean evidence: the project is synthetic and its receipts are stand-ins. |
| concepts, representations and context | [representation](../../tests/unit/test_representation.py), [declarations](../../tests/unit/test_declarations.py), [context](../../tests/unit/test_mathematical_context.py) | Any mathematical fact. A representation choice, a scoped alias and a name-index hit are inspectable state, never a proof. |
| acquisition | [acquisition](../../tests/unit/test_core_c_acceptance.py), [resolver](../../tests/unit/test_acquisition_resolver.py), [literature](../../tests/unit/test_acquisition_literature.py), [interfaces](../../tests/unit/test_acquisition_interfaces.py) | That a proposal succeeds. The source bytes are real; the model and Lean operations are scripted. |
| proof strategies | [iterative](../../tests/unit/test_iterative_strategy.py), [sketch](../../tests/unit/test_sketch_strategy.py), [best first](../../tests/unit/test_best_first_strategy.py), [race](../../tests/unit/test_race_strategy.py), [escalation](../../tests/unit/test_escalating_strategy.py), [lessons](../../tests/unit/test_strategy_lessons.py) | Any benchmark improvement, and no strategy's own report of success: only a fresh independent check of the assembled original claim establishes a result. Compact replay quotes exact failed attempts and cannot license a general impossibility. |
| research, critique, repair, referee | [research composition](../../tests/unit/test_core_d_acceptance.py), [Research](../../tests/unit/test_research.py), [Critique](../../tests/unit/test_critique.py), [Repair](../../tests/unit/test_repair.py), [Referee](../../tests/unit/test_referee.py) | Any external judgment, all of which are scripted here. Referee audits recorded readings and does not hold the authority behind them. |
| publication and prose | [planner](../../tests/unit/test_publication.py), [assembly](../../tests/unit/test_publish.py), [structure](../../tests/unit/test_publication_structure.py), [versions](../../tests/unit/test_version_audit.py), [refresh](../../tests/unit/test_exposition_refresh.py) | That a version comparison verifies mathematics: a reading schedules a recheck rather than performing one, and document structure is not mathematical use. |
| explore | [representations](../../tests/unit/test_explore_representation.py), [contexts](../../tests/unit/test_explore_context.py), [research](../../tests/unit/test_explore_research.py) | A proof of anything. Interpretation, context branches and persistent approaches exist before a theorem target does. |
| synthetic referee | [manuscript](../../tests/integration/test_referee_manuscript.py) | Live referee performance: the semantic readings and the acceptance authority are both scripted, kernel and formalization review are skipped, and citation depth stays zero. |
| terminal publication and status | [session](../../tests/test_project_publication.py), [terminal](../../tests/tui/test_project_publication.py), [summary](../../tests/test_project_summary.py) | Authenticated terminal evidence, which remains unavailable, and document compilation, which is scripted. |
| reuse and exposure | [retrieval](../../tests/unit/test_project_retrieval.py), [reuse](../../tests/unit/test_project_reuse.py), [exposure](../../tests/unit/test_evals_exposure.py) | A gain of any kind, or model performance: the restart is a ledger and index restart and the fixtures make no provider or Lean calls. |
| conversation and checkpoints | [history](../../tests/unit/test_conversation_history.py), [terminal history](../../tests/tui/test_conversation_history.py), [streaming](../../tests/test_claude_runtime_stream.py) | That selecting a branch rewinds mathematical state, which it never does, and that a durable partial observation is a completed turn. |
| durable attempts and adjudication | [batch journals](../../tests/unit/test_batch_recording.py), [reviews](../../tests/unit/test_evals_adjudication.py) | That a declared actor or label is an authenticated identity: they are caller declarations, a review never rewrites the attempt it reviews, and legacy runs' identity gaps stay unknown. |
| save gates and spend | [save sequence](../../tests/unit/test_save_gate_sequence.py), [spend](../../tests/unit/test_spend_budget.py) | Any hard billing guarantee. What is ordered is the refusal sequence, and what is durable is the reserve and settle record. |
| measurements and imports | [history](../../tests/unit/test_evals_history.py), [certification](../../tests/unit/test_evals_certification.py), [benchmarks](../../tests/unit/test_evals_benchmarks.py) | Any performance or benchmark result: certification mechanics run on scripted receipts, and an imported archive is preserved bytes rather than a runnable environment. |
| process floor | [process](../../tests/unit/test_process.py) | Filesystem or network confinement, or an independent axiom-audit environment; bounds on requests, capture, overflow and teardown are bounds on one process ([trust boundary](trust-boundary.md)). |

Every runnable component still owes its own regressions as its behaviour
changes; this table is an obligation, not a certificate that anything added
later is covered.

### The synthetic referee, and the invariant that keeps it honest

The referee fixture is the largest of these and the easiest to misread, so it
states its own boundary. Ten scenarios are planted in authored TeX that the test
never compiles: a correct theorem and a correct citation as clean controls, a
missing citation hypothesis, a circular pair of proofs, an unsupported prose
argument, an irrelevant side theorem, a silent local hypothesis drift, a
notation shadowing, an unjustified transport, and a weak shorthand later used at
full strength. Each has one expected workflow result, and the inventory, ledger
persistence, graph checks and policy all run for real.

The invariant is about what the fixture cannot manufacture. Before synthetic
acceptance, the report carries eleven selected claims, zero verified, zero
formalization probes and eleven unresolved claims; kernel and formalization
review are skipped. The adversarial reading that produces the unsupported-prose
finding is **explicitly injected**, and without that injection all three review
layers stay skipped, structural findings stay structural, and no
unsupported-prose finding appears at all. A semantic finding cannot arrive
except from a reading somebody scripted, which is exactly why the fixture
measures recorded structure rather than referee performance. A lexical inventory
and a complete recorded graph cannot establish that a real manuscript has no
further semantic defects or hidden dependencies.
