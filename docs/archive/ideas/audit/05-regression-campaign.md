# Regression campaign and source coverage

[Collection index](../README.md) · Source: audit checklist §§6–7.

## Small, explicit fixtures

Each fixture needs a family ID, immutable source/input, declared environment,
expected check outcome, evidence, and a matching clean control where possible.
Assert a behavior that can fail rather than testing that a checklist keyword
appears in generated prose.

| Family | Concrete fixture | Expected outcome |
| --- | --- | --- |
| Quantifier drift | Over naturals, “for each x choose y > x” becomes “one y > every x” | Semantic mismatch with dependency explanation |
| Structure drift | A ring theorem is translated with a field hypothesis | Flag extra structure; require semantic adjudication |
| Lost direction | A nontrivial equivalence becomes one implication | Identify the omitted direction |
| Uniqueness | Unique existence becomes existence | Identify the missing uniqueness requirement |
| Vacuity | Natural n with both n = 0 and n = 1 | Checked contradiction; no non-vacuity claim |
| Degeneracy | Zero ring admitted or excluded contrary to the prose | Flag changed domain and show boundary case |
| Finiteness | A finitely generated module is encoded as finite carrier | Distinguish notions; use an infinite cyclic module as a discriminating example |
| Object substitution | A submodule is replaced by an arbitrary set | Identify missing closure structure |
| Order | Subobject inclusion is read as numeric order | Resolve operand types and flag interpretation |
| Action | A different scalar action elaborates under the same notation | Inspect selected action and source meaning |
| Holes/axioms | sorry, custom axiom, imported custom axiom | No unauthorized verified grade |
| Audit framing | Missing, extra clean, duplicate, or truncated report | Fail closed, even beside a valid-looking line |
| Names | Unicode, namespaces, quoted and punctuation-ending identifiers | Exact theorem audited; clean controls accepted |
| Similar declarations | Frozen target and another theorem differ only by suffix | Never credit the other theorem's audit |
| Extensions | Macro/elaborator or environment-modifying command before target | Expose unsupported boundary or reject forged evidence |
| Approval drift | Approved name retains spelling but changes type | Approval and dependent current grades stale |
| Artifact substitution | Swap proof, report, or toolchain identity after checking | Recomputed binding rejects mismatch |

The semantic cases are detection/adjudication tests, not a promise that a
deterministic parser can settle arbitrary mathematical equivalence. Live Lean
fixtures should use the pinned project's real syntax and validated expected
outcomes before they become regression ground truth.

## Campaign order

First establish fail-closed grading and identity fixtures. Next add witness/
vacuity diagnostics and structural semantic cases. Then add theorem-reuse and
false-twin controls, generated-command tests, and the full human-review packet.
This preserves the source checklist's priority while serving the research
collection's trust, claim, and library experiments.

Separate hermetic unit/protocol tests, pinned-toolchain integration tests, model
semantic evaluations, and confined adversarial execution. A skipped integration
case is a coverage gap, not a successful test. Keep runtime reasonable by
running the small deterministic set per change and the appropriate larger
campaign when its input/procedure identities change.

## Reporting and acceptance

For each family publish attempted/passed/failed/unsupported counts and artifact
links. Record false certification separately from semantic false acceptance,
false rejection, and operational error. Any false certification stops promotion
of the affected capability. Clean controls must also pass.

Require at least one meaningful case for every claimed supported family before
calling the initial campaign complete. Expanding family diversity and held-out
semantic evaluation is subsequent work; one seed per family demonstrates
coverage intent, not a statistical guarantee.

## Coverage and strategy alignment

The [canonical roadmap](../../../roadmap.md) and
[research architecture](../../../research-architecture.md) control current work.
The retained protocols add concrete measurement and review detail; the
[strategy synthesis](../HARDY_STRATEGY_SYNTHESIS_2026-09-08.md) supplies historical
rationale and suite definitions:

| Material | Current home |
| --- | --- |
| Product direction, roadmap, issue triage, readiness | Canonical roadmap and research architecture |
| Shared experimental controls and reporting | Evaluation protocol |
| Synthesis Suites A–D | Foundation evaluation |
| Synthesis Suites E, F, G, L | Library evaluation |
| Synthesis Suites H, I, J, K, M | Workflow evaluation |
| Synthesis Suites N, O | Strategy synthesis, section 7 |
| Audit §1; §4 A–C | Semantic checks |
| Audit §2 | Mechanical preflight |
| Audit §3 | Verifier audit |
| Audit §4 D–E; §5 | Ingestion and human review |
| Audit §§6–7 | This regression campaign |

The [index](../README.md) links the synthesis, all retained protocols, and the
original audit checklist. The checklist supplies detailed review cases; its
historical priorities do not override the current roadmap or required human review.
