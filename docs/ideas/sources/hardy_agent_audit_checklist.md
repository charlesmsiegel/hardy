# Hardy Improvement and Pre-Human Audit Checklist

This document is for agents working on Hardy itself.

The goal is to catch as many corpus and verification defects as possible **before asking a human to review anything**.

Human review should be reserved for semantic questions that cannot be settled mechanically or by strong structural heuristics.

---

# 1. Corpus formalization checks to run before human review

For every theorem entry, compare the prose statement and Lean declaration structurally.

The agent should flag likely mismatches, but should not automatically rewrite corpus entries unless explicitly instructed.

## A. Hypothesis drift

Check whether the Lean statement assumes materially more or less than the prose.

Common suspicious strengthenings:

```text
Ring              -> CommRing
Ring              -> Domain
Domain            -> Field
Field             -> LinearOrderedField
arbitrary object  -> finite object
module            -> finite-dimensional module
group             -> abelian group
```

Common suspicious weakenings:

```text
Field             -> Ring
integral domain   -> CommRing
nonzero object    -> arbitrary object
finite            -> arbitrary
```

These are not always wrong, but they require justification.

## B. Conclusion drift

Look for Lean conclusions weaker or otherwise different from the prose.

Common patterns:

```text
x = y                 -> f x = f y
P ↔ Q                 -> P → Q
∃! x, P x             -> ∃ x, P x
equality              -> inclusion
isomorphism           -> injectivity
surjectivity          -> nonempty image
exact value           -> inequality bound
```

## C. Quantifier-order errors

Explicitly compare quantifier structure.

Flag:

```text
∀ x, ∃ y, ...
```

vs

```text
∃ y, ∀ x, ...
```

Also check for:

- variables accidentally fixed instead of quantified;
- variables quantified over the wrong type;
- conclusions moved into hypotheses;
- hypotheses embedded inside the conclusion where corpus tooling expects binders.

## D. Vacuity

Try to determine whether the hypotheses are jointly satisfiable.

Use Hardy's witness mechanism where possible.

Flag separately:

- witness succeeds;
- witness fails;
- witness is impossible to express mechanically because of implicit/typeclass binders;
- no witness mechanism applies.

Do not collapse these into a single pass/fail field.

A theorem with impossible hypotheses can be kernel-verified and still be a bad corpus entry.

## E. Degenerate structures

Check whether omitted or added assumptions materially change the theorem.

Examples:

```lean
[Nontrivial R]
```

may be necessary to exclude the zero ring.

Conversely, an unnecessary nontriviality assumption may strengthen the theorem.

Also inspect possible degeneracy from:

- empty types;
- singleton types;
- zero modules;
- trivial groups;
- vacuous finite sets.

## F. Wrong mathematical object

Check for common representation mistakes:

```text
Set vs Submodule
Set vs Ideal
Subgroup vs normal subgroup
Subring vs Ideal
Hom vs Embedding vs Equiv
Finite type vs finitely generated module
finite-dimensional vs finitely generated
element equality vs subobject equality
```

Example:

```lean
Finite M
```

is not equivalent to:

```lean
Module.Finite R M
```

## G. Overloaded notation

Resolve the types of overloaded operators before trusting the surface syntax.

Audit:

```lean
⊤
⊥
≤
<
*
•
^
/
∈
↑x
```

Check that they mean the intended mathematical operations.

Examples:

- `≤` may mean inclusion rather than numeric order;
- `•` may mean scalar multiplication, ideal action, or another action;
- `*` may be ring multiplication, pointwise set multiplication, or subobject multiplication;
- `⊤` and `⊥` depend entirely on the ambient ordered type.

## H. Coercion surprises

Inspect any statement relying on coercions between:

- subobjects and sets;
- naturals, integers, rationals, reals;
- bundled morphisms and functions;
- algebraic subobjects and their carrier types;
- elements and principal/singleton-generated subobjects.

If the mathematical meaning changes under coercion, flag it.

## I. Typeclass smuggling

Inspect every nontrivial instance argument.

Examples:

```lean
[NoZeroDivisors R]
[Nontrivial R]
[Finite R]
[Fintype R]
[DecidableEq R]
[LinearOrder R]
[NormedRing R]
```

Ask whether the source theorem genuinely assumes that structure or whether it was inserted only to make Lean elaborate or prove the statement.

## J. Redundant assumptions

Detect assumptions not used by the theorem statement or proof environment.

Redundancy does not automatically imply mistranslation, but it can indicate:

- an LLM added structure unnecessarily;
- the theorem was over-specialized;
- the benchmark difficulty was accidentally reduced.

---

# 2. Cheap automated checks that should run before any human review

These should be part of corpus ingestion or audit tooling where practical.

## A. Elaborate the exact declaration

Verify the assembled statement elaborates against exactly its declared imports.

Do not rely on a larger ambient import set during validation.

## B. Check witness / non-vacuity status

Run the witness test before semantic review.

Human reviewers should not be the first people to discover that the hypotheses are inconsistent.

## C. Check tactic triviality

Try a small fixed tactic ladder such as:

```text
rfl
simp
simpa
aesop
norm_num
omega
linarith
ring
```

depending on the imported environment and theorem shape.

A theorem closing immediately is not necessarily wrong, but should be surfaced because it may indicate:

- a vacuous theorem;
- a weakened conclusion;
- over-strong hypotheses;
- a theorem already available from imports;
- a near-duplicate of an existing Mathlib result.

## D. Check for direct theorem reuse from imports

Search whether the exact or near-exact theorem already exists in Mathlib.

If so, record that fact before benchmark scoring.

This is not a correctness failure, but it affects the meaning of the benchmark.

## E. Check for obvious contradiction in hypotheses

Try standard contradiction tools on the hypotheses alone where possible.

Examples:

- `norm_num`;
- `omega`;
- `linarith`;
- `nlinarith`;
- `simp_all`;
- finite-case normalization.

If the assumptions imply `False`, flag the entry as likely vacuous even if witness machinery cannot express the binders.

## F. Check false twins

For deliberate false twins:

- verify the mutation is actually false, not merely different;
- verify it remains close to the source theorem;
- verify it is not trivially false for an accidental syntactic reason;
- verify the twin is not made vacuously true by impossible hypotheses.

## G. Check binder coverage

Compare prose entities to Lean binders.

Flag source concepts that appear to be missing from Lean.

Also flag Lean binders with no apparent prose analogue.

## H. Check theorem-name leakage

If theorem titles or canonical names are hidden from the model, verify that:

- names do not leak through declaration identifiers;
- imports do not make the intended theorem trivially retrievable by name;
- metadata not intended for the model is excluded from prompts.

---

# 3. Trust-boundary checks for Hardy itself

The following code paths deserve adversarial review:

```text
src/hardy/audit.py
src/hardy/lean.py
src/hardy/runner.py
src/hardy/verifier.py
```

## A. `#print axioms` parsing must fail closed

Verify:

- malformed output is rejected;
- truncated output is rejected;
- missing theorem output is rejected;
- unexpected extra output cannot be mistaken for a clean report;
- `sorryAx` is always detected;
- unapproved axioms are always detected.

## B. The theorem being audited must be exactly the theorem proved

Audit identifier handling for:

- namespaces;
- Unicode;
- `!`;
- `?`;
- quoted identifiers;
- generated names;
- shadowing.

Make sure name parsing cannot cause Hardy to freeze one theorem and audit another.

## C. Independent verifier must recompute evidence

The verifier should independently recompute:

- source hash checks;
- theorem elaboration;
- kernel verification;
- axiom dependencies;
- grading.

It should not trust evidence files generated by the proving process except as inputs to be checked.

## D. Pinned toolchain integrity

Verify that the independent verifier uses the intended exact:

- Lean version;
- Mathlib revision;
- Lake environment;
- imports.

A result should not silently verify under a different dependency graph than the proving run.

## E. Environment contamination

Generated Lean can define or execute:

- macros;
- elaborators;
- commands;
- environment extensions;
- `#eval`.

Do not assume that an audit command elaborated in the same environment as untrusted generated code is independent evidence.

Where possible, move verification into a fresh process/environment containing only:

- the frozen declaration;
- the submitted proof;
- pinned imports;
- Hardy-controlled audit commands.

## F. Generated-source restrictions

Consider rejecting or specially handling generated proof files containing unexpected command forms such as:

```text
axiom
opaque
macro
elab
syntax
command_elab
#eval
set_option
attribute
instance
```

Not all are malicious, but they can alter the environment or trust assumptions.

A theorem-proving benchmark usually has no need for many of them.

## G. Artifact binding

Verify that all reported results are bound to hashes of:

- theorem statement;
- proof source;
- imports;
- toolchain/version metadata;
- corpus entry;
- assumptions;
- verification report.

No scoreboard or manifest should be able to refer ambiguously to mutable artifacts.

---

# 4. Pre-human semantic review pipeline

Before asking a human to inspect a corpus entry, an agent should produce a compact report containing:

## A. Prose normalization

Rewrite the source statement into a structured mathematical summary:

```text
Objects:
Hypotheses:
Quantifier order:
Conclusion:
```

## B. Lean normalization

Independently decode the Lean statement into the same structure.

Do not reuse the prose interpretation while doing this.

## C. Automatic comparison

Report suspected differences in:

- object types;
- hypotheses;
- conclusion strength;
- quantifier order;
- nontriviality assumptions;
- finiteness assumptions;
- commutativity assumptions;
- existence vs uniqueness;
- equality vs inclusion;
- overloaded operations;
- coercions;
- typeclass assumptions.

## D. Mechanical evidence

Include:

- elaboration result;
- witness status;
- trivial-tactic result;
- obvious contradiction check;
- direct/near Mathlib theorem matches;
- axiom audit result.

## E. Human escalation rule

Only escalate when:

1. the Lean and prose appear structurally different but the agent cannot determine whether the difference is mathematically harmless; or
2. the statement uses a subtle Mathlib encoding whose semantic equivalence cannot be established confidently; or
3. the theorem itself is sufficiently advanced that deciding equivalence requires domain expertise.

Do not ask a human to review entries with obvious mechanical defects.

---

# 5. Corpus ingestion guardrails

During LLM translation of new theorem statements:

## A. Require a translation rationale

Before accepting generated Lean, require the translating agent to explain:

- what every binder represents;
- what every non-obvious typeclass represents;
- how each hypothesis maps to the prose;
- how the conclusion maps to the prose.

This rationale is not proof of correctness, but it gives the audit agent a second artifact to challenge.

## B. Require minimal assumptions

Ask the translating agent to avoid adding stronger structure unless necessary.

If it adds assumptions not present in the source, require explicit justification.

## C. Separate translation from proof search

Freeze and audit the Lean statement before giving a proving agent access to it.

Proof success must never be allowed to influence whether a formalization is accepted as faithful.

## D. Adversarial second pass

Use a separate agent to search specifically for:

- vacuity;
- over-specialization;
- weakened conclusions;
- quantifier drift;
- wrong coercions;
- hidden typeclass assumptions.

The second agent should be prompted to find defects, not to defend the translation.

---

# 6. High-value regression tests to add

Add tests for known dangerous classes of failure.

## Corpus translation tests

Create synthetic entries where:

1. `∀ x, ∃ y` is mistranslated as `∃ y, ∀ x`;
2. `Ring` is strengthened to `Field`;
3. `P ↔ Q` becomes `P → Q`;
4. `∃!` becomes `∃`;
5. hypotheses are inconsistent;
6. zero-ring degeneracy changes the result;
7. `Finite M` is substituted for `Module.Finite R M`;
8. `Set` is substituted for `Submodule`;
9. `≤` means inclusion but is treated as numeric order;
10. scalar multiplication `•` resolves to the wrong action.

The audit layer should flag each one before human review.

## Verification tests

Create adversarial Lean files containing:

1. `sorry`;
2. a custom axiom;
3. an imported custom axiom;
4. misleading extra `#print axioms` output;
5. truncated audit output;
6. theorem names ending in `!` or `?`;
7. Unicode theorem names;
8. namespaced theorem names;
9. multiple declarations with similar names;
10. macros or elaborators before the theorem;
11. environment-modifying commands;
12. attempts to shadow or interfere with audit commands.

The verifier should fail closed.

---

# 7. Priority order

Recommended order for implementation work:

1. **Fail-closed verifier and axiom-audit hardening**
2. **Automatic vacuity / witness diagnostics**
3. **Structural prose-vs-Lean comparison**
4. **Strong-hypothesis / weak-conclusion heuristics**
5. **Direct-Mathlib theorem detection**
6. **False-twin validation**
7. **Generated-command restrictions**
8. **Adversarial regression corpus**
9. **Human-review escalation workflow**

The aim is to make human review the final semantic gate, not the first debugging tool.
