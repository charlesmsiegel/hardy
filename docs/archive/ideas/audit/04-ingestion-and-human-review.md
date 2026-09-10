# Ingestion and human-review packets

[Collection index](../README.md) · Source: audit checklist §§4–5.
Historical rationale: [strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md),
section 4, Phase 1 (claim revisions) and Phase 2 (definition/convention mappings).

## Proposed flow

Source intake → translation with rationale → exact frozen candidate →
mechanical preflight → independent semantic comparison → human review →
digest-bound decision.

This flow prepares a reviewable candidate. The corpus's existing required
human faithfulness review still applies before activation. The source
checklist's “only escalate” rule is interpreted as removing obvious mechanical
defects from the human queue, not allowing an agent to waive that gate.

## Translation record

Require an explanation of each binder, non-obvious typeclass, hypothesis,
conclusion, convention, and source locator. Extra assumptions need a stated
reason and must remain visible. Keep the rationale separate from the signature
and from any independent reader's initial interpretation.

Freeze the candidate before proof search. If it changes, create a new revision
and expire affected evidence. A proof that happens to work must not persuade
the reviewer to accept a mismatched translation.

## Independent second pass

Give the reviewer the source and required definition context plus the frozen
Lean candidate, without the generating conversation or proof result. Ask for
discriminating cases and entailment in both directions. Record the actual
backend isolation and whether artifacts were accessible; do not promise
independence solely because the reviewer used a separate session.

After its initial reading, the reviewer may challenge the translator's rationale.
Preserve disagreements and operationally unavailable reviews separately.
A model-generated comparison is heuristic evidence, even if no discrepancy is
found.

## Human packet

Present source bytes/version and exact locator; original prose; frozen Lean and
imports; the two structured readings; a binder/convention map; concrete suspected
mismatches; elaboration/witness/triviality/contradiction/retrieval/axiom results;
and the exact semantic question that remains.

Put serious findings first. Keep full diagnostics as linked artifacts rather
than forcing a human to read a log. Show unknown and unsupported checks openly.
A mechanically broken candidate goes back for repair; subtle encodings,
unresolved equivalence, and advanced domain questions go to the reviewer.

Record actor, decision, rationale, time, and all digests the decision covers.
Human acceptance of one revision cannot migrate to another silently. The
reviewer may ask for a new formalization; that restarts the affected checks.

## Acceptance and measurements

Test a candidate repaired after preflight: its earlier report and approval must
not cover the changed signature. Test a reader outage: no agreement is invented.
Test an apparently faithful candidate: it still reaches the required human
gate before active-corpus promotion.

Measure packet completeness, mechanical defects found before review, reviewer
time, decision reversals, unresolved cases, and downstream false accepts.
Reducing human questions is useful only if retained decisions remain faithful.
No corpus edits, automated approvals, or activation are part of these notes.
