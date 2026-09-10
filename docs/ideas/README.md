# Research strategy and supporting checks

Start with the [canonical roadmap](../roadmap.md) and
[research architecture](../research-architecture.md). They control delivery
order, task IDs, ownership, and issue policy. The older root `ROADMAP.md` is
superseded and is not carried into this branch.

This collection was retained from `docs/research-improvement-ideas` at
`c6b987d`. The [September 8 strategy synthesis](HARDY_STRATEGY_SYNTHESIS_2026-09-08.md)
supplies historical product rationale and the suite taxonomy, not an alternative
implementation plan. Its sequencing, issue dispositions, and architectural
recommendations yield to the current roadmap and research architecture.

These are proposals, not implemented features or a delivery commitment. The
[README](../../README.md), [design](../../DESIGN.md),
[feature inventory](../../FEATURES.md), [architecture overview](../../ARCHITECTURE.html),
and [installation guide](../INSTALL.md) describe the existing implementation
and its limits. Their older future sequencing yields to the roadmap; the
first-experiment acceptance contract remains the regression floor.

## Historical context

The previous research roadmap and seven topic proposals (trust, claims,
library gaps, supplements, literature, proof strategies/CAS, and readiness),
plus the original research gap analysis, have been removed. Their strategic
subjects are covered by the synthesis. Its proposal to replace transcript-tree
branching with mathematical attempts is historical: the current architecture
distinguishes conversation history, mathematical contexts, project relations,
and proof-search frontiers. The earlier texts remain in Git history at commit
`c2bf4c1`.

The documents below remain because they supply measurement controls, concrete
failure cases, and review procedures beyond the current roadmap. Apply them to
its evaluation lane V and the acceptance checks of each implementation slice;
suite letters below are measurement categories, not roadmap task IDs. References
inside retained protocols to the synthesis provide historical context and do not
override the current architecture.

## Supporting evaluation protocols

Suite letters follow section 7 of the synthesis. Gap classification (E) and
definition acquisition (F) are scored separately; literature is G, repair H,
CAS I, memory J, trust K, reuse L, and scripted collaboration M. Suites N and O
(multi-artifact and publication assembly) are specified in the synthesis.

| Document | Detail retained |
| --- | --- |
| [Shared protocol](research/08-evaluation-protocol.md) | Identities, controls, budgets, family splits, uncertainty, and reporting |
| [Foundation evaluation](research/09-evaluation-foundations.md) | Suites A–D: proving, translation, false targets, retrieval |
| [Library evaluation](research/10-evaluation-library.md) | Suites E, F, G, L: gaps, definitions, literature, downstream reuse |
| [Workflow evaluation](research/11-evaluation-workflows.md) | Suites H, I, J, K, M: repair, CAS, memory, trust, collaboration |

## Supporting audit procedures

| Document | Detail retained |
| --- | --- |
| [Semantic checks](audit/01-semantic-checks.md) | Binder/notation comparisons, discriminating cases, legitimate controls |
| [Mechanical preflight](audit/02-mechanical-preflight.md) | Ordered checks, inconclusive outcomes, vacuity and false-twin controls |
| [Verifier audit](audit/03-verifier-audit.md) | Exact identity, parsing, recomputation, confinement versus audit independence |
| [Ingestion and human review](audit/04-ingestion-and-human-review.md) | Independent readings and digest-bound human review packets |
| [Regression campaign](audit/05-regression-campaign.md) | Concrete fixtures, expected outcomes, coverage and failure reporting |

The original [audit checklist](sources/hardy_agent_audit_checklist.md) remains
as the source for these detailed procedures. Its prioritization and escalation
suggestions are historical: the roadmap controls delivery, and the required
human corpus-faithfulness review is not waived by automated preflight.

## Scope and interpretation

The imported synthesis is preserved byte-for-byte. Its issue dispositions,
external comparisons, and corpus-size claims are dated source assertions, not
newly verified findings about this checkout or the live tracker. In particular,
its reference to approximately 1,000+ pairs is not this branch's shipped count.
No remote issue has been changed by importing these recommendations.

A failed search does not establish library absence. Filesystem confinement and
a fresh process do not alone establish an unspoofable audit. Computational
support remains distinct from kernel proof. Generated Lean, TeX, downloaded
material, CAS cells, and helper processes remain unsafe under the current
execution model.

Harness code, tooling, and these proposals belong on main-based branches.
Corpus statements, source registry, releases, and corpus measurements belong on
the corpus curation branch. Local `evals/` artifacts are ignored, not committed
evidence. No implementation, corpus edits, live experiments, or issue updates
are part of this documentation change.
