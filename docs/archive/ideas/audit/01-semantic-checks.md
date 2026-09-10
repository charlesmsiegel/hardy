# Semantic checks before faithfulness review

[Collection index](../README.md) · Source: audit checklist §1 A–J and §4 A–C.
Primary research use: [Suite B](../research/09-evaluation-foundations.md).

## Produce two independent readings

Normalize source prose and the elaborated Lean statement separately into
objects, hypotheses, quantifier dependencies, and conclusion. Preserve the
original text and locators beside each interpretation. Do not give the Lean
reader the translating model's rationale until its first reading is recorded;
otherwise both readers may repeat the same misconception.

Every finding should name the exact prose span, Lean binder or subexpression,
suspected direction of change, concrete discriminating case when available,
and evidence strength. Use “suspected mismatch” for a heuristic finding.
Neither fluency nor kernel acceptance establishes semantic faithfulness.

## Checklist and discriminating cases

| Check | Concrete comparison | Required inspection |
| --- | --- | --- |
| Hypothesis drift | Arbitrary ring becomes field or commutative ring | List the additional laws and whether the source supplies them |
| Conclusion drift | Bijection becomes injection; equivalence becomes implication | Identify the missing direction or property |
| Quantifiers | Every input has an output becomes one output for all inputs | Record which witnesses may depend on which variables |
| Vacuity | Assumptions imply False | Link a witness or contradiction result; retain unknowns |
| Degeneracy | Nontriviality excludes zero rings or singleton groups | Compare allowed boundary cases in both statements |
| Object choice | Finite carrier versus finitely generated module | Inspect types and meaning, not shared words |
| Overloaded notation | Order means subobject inclusion | Resolve the elaborated operand and result types |
| Coercion | Subobject becomes its carrier set or carrier type | Identify what information or quantification changed |
| Typeclass assumptions | Extra order, finiteness, or algebraic structure | Map each non-obvious instance to source meaning |
| Redundancy | Unused parameter or stronger-than-needed structure | Distinguish harmless redundancy from an unintended specialization |

Additional controls should include existence versus uniqueness, equality versus
inclusion, exact value versus bound, subring versus ideal, homomorphism versus
embedding/equivalence, and the action selected by scalar multiplication.
An added decidability instance may be computational scaffolding under the
chosen logic rather than a mathematical restriction; do not declare every
extra instance a defect.

## Meaningful negative controls

Include faithfully equivalent alternative formulations. For example, a bundled
structure may package hypotheses stated separately in prose. A redundant
hypothesis is not automatically a mistranslation when the source includes it.
An unused proof assumption does not license silently strengthening the theorem
by removing it from the approved signature.

The detector must explain why a discrepancy matters or preserve it as unresolved.
Only an independent semantic decision can settle difficult representational
equivalence. Any corrected statement is a new revision with fresh review.

## Deliverable and acceptance

Deliver one concise mismatch table with independently produced summaries,
mechanical evidence links, and unresolved questions. No automatic corpus
rewrite or active-status promotion follows from this report.

Seed a quantifier swap, a lost implication, and a finite-generation mismatch
beside faithful controls. The pipeline should flag the three cases with the
right local evidence and avoid treating the controls as proven defects.
Suite B measures false accepts, false alarms, abstentions, and family-level
coverage; a checklist run is not itself a faithfulness verdict.
