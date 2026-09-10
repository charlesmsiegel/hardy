# Suites A–D: proving, translation, false targets, and retrieval

[Collection index](../README.md) · Read the
[shared protocol](08-evaluation-protocol.md) first.
Suite labels follow the [strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md),
section 7, Suites A–D. These protocols add controls and scoring detail.

## A. Theorem-proving calibration

**Question.** Does a tool or strategy increase certified success on the same
already-formalized claims at the same budget?

Use exact Lean declarations with fixed imports, audited meaning, and pinned
environments. Preserve routine calibration examples and add substantial,
qualifying-level, and research-adjacent known results across fields. Difficulty
tiers measured by a tactic sweep remain procedure-dependent metadata; they are
not timeless properties of the theorem.

Gold is the exact claim plus accepted axiom policy. A successful artifact must
elaborate, prove that claim, and pass the common audit; a different true theorem
is not a solve. Show the automation floor and direct library reuse separately.
Run fixed baseline tactics before the model experiment under their recorded
procedure; reuse a baseline only when its required identities match.

Compare one ablation at a time: Lean feedback, declaration search, premise
ranking, cheap closers, memory, strategy. The proof-only input stays identical.
Report certified pass@1 and budget-defined pass@k, exact used axioms,
all-attempt and solved-only cost/time, tool/Lean calls, turns, and proof/import
size. Record failed states if instrumentation can capture them; do not infer
them from a model's prose.

**Failure criteria.** Any credited result for a changed statement, a hole, or an
unapproved axiom invalidates that certification and triggers investigation.
An easy theorem is not a defective item merely because it is easy.

## B. Formalization faithfulness

**Question.** Does the system preserve the mathematical claim, and does the
reviewer reject plausible wrong translations?

Create precise prose plus human-audited Lean, with accepted alternative
encodings and explicit convention notes. Include paired near-misses: stronger
hypotheses, weaker conclusions, quantifier swaps, equivalence versus implication,
existence versus uniqueness, finite carrier versus finite generation, wrong
object/coercion/operator, degeneracy, and subtly different topological notions.
Include legitimate representation differences as negative controls for the
heuristics. Experts must label the pairs before model evaluation.

Run two experiments separately. In translation, the model sees source material
and permitted definitions, but not the gold signature or proof. In reviewer
discrimination, supply a frozen candidate translation and the same relevant
source context to an independent reader with no generating conversation.
Capture actual isolation capabilities, not just a new thread ID.

Labels are faithful, materially unfaithful, unresolved, and operationally
unavailable; keep stronger/weaker/incomparable drift as reasons. Review quality
must not depend on whether proof search later succeeds.

Primary metrics:

- False-accept rate: accepted materially wrong candidates / all labeled wrong
  candidates, with unresolved outputs counted separately.
- Faithful-accept rate: accepted faithful candidates / all labeled faithful
  candidates.
- False discovery among accepts: accepted wrong candidates / all adjudicated
  accepted candidates, with the constructed class balance disclosed.
- End-to-end faithful translation rate, revision turns, escalation rate,
  reviewer disagreement, time, and tokens.

These denominators answer different questions. Report a confusion table rather
than a single accuracy score. Preserve abstentions so a reviewer that rejects
everything cannot look successful. Measure by failure family and field, with
family-based splits to keep near-duplicate pairs out of both train and test.

**Acceptance.** The pilot catches each seeded material drift while accepting
the legitimate controls. It is a fixture check, not evidence of a universal
semantic detector. A failed call is never treated as reviewer agreement.

## C. False statements and refutation

**Question.** Does Hardy refuse false certification, and can it produce useful
evidence explaining a false target?

Use synthetic twins, omitted-degeneracy assumptions, quantifier perturbations,
and source-backed statements known false at the recorded review date. Each
negative item needs independently adjudicated falsity evidence: preferably a
checked counterexample, otherwise a cited exact counterexample with explicit
limits. A different statement is not automatically a false twin.

Run negatives mixed with true controls without revealing expected verdict.
The target must elaborate; malformed input is a different robustness suite.
Separate kernel-checked refutation, reviewed informal counterexample,
unsupported assertion of falsity, refusal without refutation, exhaustion,
infrastructure failure, and false certification.

Report false certifications / evaluated false targets, useful refutations /
false targets with each evidence category separate, exhaustion and refusal
rates, and erroneous refutation claims on true controls. “Not proved” is never
“disproved.” Existing harness refusal criteria may be narrower than useful
mathematical refutation; preserve those existing outcome fields and add a
separate evidence assessment rather than silently relabeling them.

**Acceptance.** No false target is certified; every claimed refutation points
to evidence against the exact target. Show attack/task counts and uncertainty,
even when zero false certifications are observed.

## D. Mathlib retrieval

**Question.** Can the system find an appropriate declaration before assuming or
rebuilding it?

Use pinned declaration queries with validated gold sets, not necessarily one
gold spelling. Include naming mismatches, unexpected namespaces, generic
encodings, and decoys with wrong hypotheses. Record acceptable alternatives,
required instantiations, and cases needing a bridge. Add adjudicated negative
or unresolved cases so “always return something” cannot pass.

Compare the current index, index plus ranking, and any proposed retrieval
change with identical query and tool budgets. Confirm candidate signatures in
the pinned Lean environment. Distinguish retrieved name, correct module,
applicable signature, and successful use in the downstream task.

Report recall@1/@5/@k, signature-confirmed applicability, false-absence rate on
known-present tasks, precision of selected declarations, downstream solve
uplift, search calls, tokens, and elapsed time. Define recall as the fraction
of gold-relevant declarations returned when the gold set is exhaustive; when
the task only needs one suitable result, report hit@k explicitly instead.

A novel valid answer absent from the gold list needs blinded adjudication.
Version the corrected labels and recompute all conditions; do not penalize
one system for finding a previously unlisted solution.

**Acceptance.** The unfamiliar-name controls yield usable declarations and
wrong-hypothesis decoys are not reported as applicable. A timeout or incomplete
search produces “not found under this budget,” not a definitive absence claim.
