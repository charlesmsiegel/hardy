# Verifier audit plan

[Collection index](../README.md) · Source: audit checklist §3 A–G and §6.
Historical rationale: [strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md),
section 4, Phase 0 (trust and assumption identity).
Evaluation: [Suite K](../research/11-evaluation-workflows.md).

## Scope and evidence

The source checklist names the former root audit, Lean, runner, and verifier
modules. Their current review targets are `src/hardy/formal/audit.py`,
`src/hardy/formal/lean.py`, `src/hardy/workflows/batch.py`, and
`src/hardy/formal/verifier.py`, plus their callers/tests and artifact validation paths. These are review targets, not a claim that each contains a
newly demonstrated defect.

For every finding retain an exact input or state transition, expected behavior,
observed behavior, and replay command under a recorded environment. Distinguish
a parser defect, stale-identity defect, semantic drift, forged record, and
unconfined execution. Repairing one class does not establish the others.

## Audit matrix

| Boundary | Fixture | Required result |
| --- | --- | --- |
| Axiom parsing | Missing, malformed, truncated, duplicate, or conflicting report | Refuse certification with a named reason |
| Axiom policy | Hole, custom axiom, imported unapproved axiom | Hole never approved; undeclared trust refused |
| Name resolution | Namespaces, Unicode, punctuation, quoted identifiers, near names | Audit exactly the intended declaration |
| Recomputed verification | Edited claimed grade or source hash | Derive from checked inputs and expose the mismatch |
| Environment identity | Changed Lean, Mathlib, Lake imports, supplement | Refuse current evidence reuse |
| Approval identity | Same name, different type or defining dependency | Stale approval and affected grades |
| Artifact binding | Swapped proof/report/corpus entry or missing dependency | No ambiguous or mutable evidence credited |
| Generated commands | Elaborator, macro, command shadowing, environment extension | No forged clean result accepted |
| Side effects | Generated process changes audit evidence | Treat as trust-boundary failure, not clean output |

Include accepted controls for valid names ending in punctuation and ordinary
uses of syntax or instances. Rejecting every unusual declaration may avoid a
false accept while destroying supported functionality.

## Restrictions versus independent checking

Assumption identity must include the Lean-reported type, universe parameters,
declaration kind, defining module, source closure, and pinned environment.
Record the type-serialization version and printer options when text is hashed;
surface text alone cannot establish semantic identity across environments.
Exercise same-name type replacement, re-export from a different module, and a
changed imported definition whose assumption's printed type stays unchanged.
Recompute identity on rebuild and restart, preserve historical approvals, and
withhold stale dependent grades. An approved but unused axiom stays out of the
reported used-assumption set.

Confinement bounds filesystem, process, network, and resource effects. Audit
independence prevents generated commands from forging theorem and axiom evidence.
Test both boundaries separately, with verifier evidence outside the generated
process's writable scope. Neither a fresh process nor parser hardening alone
establishes independence.

The source suggests screening `axiom`, `opaque`, `macro`, `elab`,
`syntax`, command elaboration, `#eval`, options, attributes, and instances.
These require context: some are necessary mathematical infrastructure, some
extend elaboration, and some change trust. A blanket lexical blacklist is not
a semantic audit and can be bypassed by generated syntax.

If a constrained benchmark proof surface is adopted, define its grammar and
document the legitimate cases it excludes. Keep it separate from the broader
interactive development surface. Independently checked source closure and
tested evidence extraction remain necessary; a fresh process can still import
the problematic code.

## Campaign delivery

First run hermetic parser and manifest fixtures. Then design real Lean cases
and confinement probes in a disposable environment, naming unsupported
platforms and unexecuted cases. Do not execute hostile Lean, TeX, or CAS in the
ordinary checkout to test whether it is isolated.

Report false accepts, clean-control false rejections, fixture counts by boundary,
and evidence gaps. Any false accept blocks the corresponding trust claim.
A clean finite campaign does not prove a sandbox or verifier secure.
