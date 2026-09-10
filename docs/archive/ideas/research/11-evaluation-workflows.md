# Suites H, I, J, K, and M: repair, CAS, memory, trust, and collaboration

[Collection index](../README.md) · Read the
[shared protocol](08-evaluation-protocol.md) first.
Suite labels follow the [strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md),
section 7. These protocols add controls and scoring detail. Suites N and O
in that document cover multi-artifact and publication assembly.

## H. Critique and repair

Create proof/artifact families with seeded defects: a Lean hole, unjustified
informal step, misused citation, omitted boundary case, claim drift, false
intermediate lemma, and a patch that breaks a downstream proof. Include clean
controls. Annotate defect locations, category, evidence, affected dependency
closure, and acceptable fixes before testing.

Run critique without disclosing the seeds. Match findings to gold defects using
a documented rule; duplicate paraphrases count once. Adjudicate valid unexpected
findings blind to condition. Report defect recall and precision, false alarms
on clean inputs, duplicate reports, evidence sufficiency, and human effort.

For repair, freeze the original target and input digests. Give the repairer
either gold holes or detected holes as separate conditions: the latter measures
the whole pipeline, the former isolates repair ability. Recheck target and
affected dependents after each patch.

Report verified closures / eligible holes, completed corrected artifacts /
assigned tasks, claim-drift count, downstream regression rate, overlapping-hole
reopen correctness, budget, and cost. A false target may require an honest
refutation rather than repair. Editing its signature never counts as closure.

## I. CAS-assisted mathematics

Use tasks with independently known opportunities for examples, counterexamples,
formula discovery, low-degree exploration, or ideal/syzygy computation. Freeze
backend versions, seeds where meaningful, initial files/state, and verification
targets. A gold opportunity does not prescribe the exact computation.

Compare no CAS, fresh CAS per cell, and persistent CAS under matched total
budgets. Separate protocol robustness tests from mathematical benefit: use
mutate-then-error, restart, truncation, and concurrent-export fixtures to check
state handling independently of whether a model solves a theorem.

Measure replay/export success on named session prefixes, canonicalized output
agreement where ordering is immaterial, useful discovery assessed against gold
mathematics, downstream checked proofs/refutations, model and backend cost, and
human correction time. Record backend-unavailable outcomes and unsupported
replay features explicitly.

Any CAS-only observation reported as kernel verification is a trust failure.
Numerical agreement is not a proof, and failure to reproduce may indicate
external-state dependence rather than mathematical falsity.

## M. Interactive mathematician–Hardy episodes

Use known mathematics with an adjudicated dependency structure. One episode
should exercise: target proposal, human reduction, rejected overstrong
translation, suggested construction, intermediate lemma, dead-end abandonment,
paper introduction, special-case computation, and return to the target.
Shorter episodes may isolate one interaction first.

Specify interventions as a versioned state machine with observable triggers,
permitted responses, timeout branches, and completion rules. For example:
after a proposed signature, reject a specified added hypothesis; after the
corrected signature, approve it; if it remains wrong after a fixed number of
turns, end with unresolved formalization. Do not use an unconstrained model
“human” that quietly provides stronger help to weaker conditions.

Run ordinary chat, Lean-feedback-only, Hardy, and selected Hardy ablations.
Provide equivalent intervention information and record any unavoidable tool
differences. Use the same external artifact grading and distinguish a scripted
stand-in from a real mathematician. A later human study should record domain
experience, time, task order, and learning effects.

Primary results are checked dependency closure and preservation of the user's
claim. Secondary measures: time to approved formalization, correction turns,
faithfully incorporated suggestions / applicable suggestions, false-lemma
detection, justified objections, repeated dead ends, human interventions,
checked reusable lemmas, context use, and cost. Count unresolved assumptions
and holes beside progress. A collection of disconnected lemmas does not
establish the target.

Report progress per intervention together with absolute progress and total
interventions; ratios alone can reward doing almost nothing. Keep the episode
family as the unit when calculating uncertainty.

## J. Long-term memory and branching

Construct multi-session families with a reusable checked lemma, a failed
approach whose scope matters, an exact-repeat task, a related transfer task,
two mathematical attempts attached to the same claim revision, and one
dependency/toolchain change. Branching means selecting and abandoning attempts;
it does not require rewinding provider transcripts or all workspace artifacts.
Specify the memory snapshot visible at every episode boundary.

Compare memory off, full retained history, compact lessons, and structured
claim/attempt records when feasible. Match budgets and report the prompt space
consumed by each. Freeze memory before held-out transfer; do not let one
condition learn from another's scored outcomes.

Measure valid reuse, repeated failed strategies, summary token savings,
performance change, exact-repeat hits, held-out success, contamination findings,
attempt/frontier recovery, and source/assumption identity mismatches.
Count stale-memory suggestions separately from stale evidence actually credited.

Inject restart and interrupted-save points. Recovered status, active revisions,
Lean/TeX artifacts, and assumption sets must agree. A lesson that “strategy S
failed under budget B” must not become “the lemma is false” after compaction.
Abandoning an attempt should preserve valid lemmas without making its unapproved
assumptions current in another attempt. Distinguish established proof dependencies
from explanatory or proposed edges: equivalence links may form cycles, but
circular proof justifications must never count as a verified dependency closure.

## K. Verifier and trust-boundary adversarial tests

This is a correctness/security campaign, not a theorem-capability score.
Maintain a matrix of surface, exact fixture, expected verdict, current coverage,
execution environment, and evidence. The
[audit regression campaign](../audit/05-regression-campaign.md) defines the
starting cases.

Include holes, custom/imported axioms, same-name type drift, missing/duplicate/
truncated/spoofed audit output, punctuation/Unicode/quoted names, ambiguous
names, macros/elaborators, generated declarations, altered toolchain/imports,
stale supplements/paper modules, forged artifacts, filesystem effects, and
TeX/CAS misuse. Pair hostile cases with valid unusual syntax so blanket
rejection cannot masquerade as correct parsing.

Start with hermetic parser/state fixtures. Real hostile execution requires a
disposable, explicitly bounded environment; the current absence of isolation
must remain visible. Mark fixtures not run or not supported as coverage gaps.

Report false certification count, attacks attempted by family, false rejection
on clean controls, stale-evidence leaks, missing checks, and replayable failure
artifacts. One false certification stops promotion of the affected trust claim
and prompts investigation; do not average it away among thousands of easy
passes. Zero observed false accepts covers only the exercised cases.

## Cross-suite first campaign

Start with one complete episode joining an approved claim, a corrected
translation, a library decision, a checked lemma, a restart, and a report.
Add deterministic failures at the state and grading boundaries. Then expand
task diversity and run matched ablations using the shared protocol.

Publish separate tables for semantic outcomes, operational reliability, trust
failures, and resource use. These suites together support a research-readiness
argument; none individually proves it.
