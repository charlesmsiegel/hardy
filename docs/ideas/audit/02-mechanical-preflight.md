# Mechanical corpus preflight

[Collection index](../README.md) · Source: audit checklist §2 A–H and §1 D–E.
Research links: [Suites A, C, D](../research/09-evaluation-foundations.md).

## Reuse existing checks

Begin with the existing corpus validator, witness mechanism, pinned tactic
baseline, retrieval tools, and axiom audit. Read their current contracts before
adding a parallel implementation. Store preflight measurements separately from
immutable statements; corpus content and measurements follow the branch rules.

Each check records input digest, imports, Lean/Mathlib/procedure identity,
budget, result, diagnostics, and evidence artifact. Suggested common outcomes
are established, finding, inconclusive, unsupported, and operational error.
A timeout or unavailable toolchain cannot become a pass.

## Run order

1. Validate schema and exact source assembly. Elaborate the exact declaration
   with only declared imports; extra ambient imports must not rescue it.
2. Exercise non-vacuity witnesses. Preserve witness success, failed witness
   candidate, unsupported binder shape, and no applicable witness mechanism.
   Failure of one witness does not prove impossible hypotheses.
3. Probe the hypotheses for contradiction where representable. A checked
   contradiction under the declared assumptions establishes vacuity for that
   context. Failure to find one says nothing about consistency.
4. Run the existing fixed tactic ladder and search baseline with budgets.
   Report trivial closure and direct theorem reuse as measurements, not defects.
5. Inspect known-present declaration matches with Lean-confirmed signatures.
   Record useful alternative matches and near-misses separately.
6. Validate false twins against their intended mutation and independent
   falsity evidence. A “twin” that is true or vacuously true needs correction.
7. Compare prose entities and formal binders, then route uncertain semantic
   findings to the semantic packet.
8. Inspect the actual model-visible prompt and tool output for hidden title,
   expected-verdict, canonical-proof, or metadata leakage.

## Important distinctions

A closed theorem may be easy because Mathlib already proves it, because its
hypotheses are contradictory, or because it was mistranslated. Tactic success
alone does not tell these apart. Preserve easy calibration entries unless a
specific corpus defect is established.

A successfully elaborated witness needs the same relevant assumption context;
a witness for a simplified or differently quantified proposition does not
establish non-vacuity of the entry. In a false twin, a counterexample must
satisfy all hypotheses and falsify the exact conclusion.

Source-title hiding and imported theorem availability are separate conditions.
Do not silently strip necessary imports to make the benchmark look harder;
that creates a different task requiring its own identity.

## Acceptance and cost

A malformed declaration fails before semantic review. An unexpressible witness
is reported as unsupported. A contradictory-assumption fixture is flagged.
A correct easy theorem remains valid and receives its automation-floor result.
A false-twin candidate without falsity evidence remains unresolved.

Track checks attempted, coverage by binder shape, findings per family,
inconclusive/error counts, Lean work, and review time saved. A high preflight
pass rate with most checks unsupported is not useful coverage.

This proposal does not authorize a live sweep. When implemented, batch
digest-sensitive changes and run measurements only after the procedure is
frozen and no earlier sweep/run is in flight.
