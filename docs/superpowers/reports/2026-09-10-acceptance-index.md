# V0 acceptance fixture index

This index connects implemented roadmap primitives to deterministic acceptance
fixtures. It records behavior exercised by the suite, not model performance or
mathematical correctness of arbitrary generated artifacts. Test doubles supply
external judgments where named; ledger, artifact, identity and orchestration
owners remain real. The full landing reports record aggregate test coverage.

| Contract / roadmap work | Fixtures | Acceptance boundary |
| --- | --- | --- |
| A0/B0 ledger and policy | [contracts](../../../tests/unit/test_ledger_contracts.py), [store](../../../tests/unit/test_ledger_store.py), [policy](../../../tests/unit/test_ledger_policy.py) | Durable identities, revisions, typed relations and current evidence |
| A1 package direction | [boundaries](../../../tests/unit/test_module_boundaries.py) | Static and runtime import direction, including read-only evaluation owners |
| A2/A3 statement and trust preparation | [formalization](../../../tests/unit/test_formalization.py), [admission](../../../tests/unit/test_admission_policy.py) | Exact frozen mathematics and explicit scoped assumptions |
| A4 strategy contract | [contracts](../../../tests/unit/test_strategy_contracts.py) | Shared strategy inputs, results and budget ownership |
| A5 manuscript inventory | [manuscript](../../../tests/unit/test_manuscript.py) | Exact source spans and bounded inventory; no semantic correctness inference |
| B1-B3 project semantics and acceptance | [Core B](../../../tests/unit/test_core_b_acceptance.py), [graph](../../../tests/unit/test_ledger_graph.py), [views](../../../tests/unit/test_ledger_views.py) | Exact dependencies, authenticated establishment and historical/current status |
| B4 concept/representation choice | [representation](../../../tests/unit/test_representation.py) | Explicit alternatives, selected capabilities and refinements |
| B5 declarations/context/notation | [declarations](../../../tests/unit/test_declarations.py), [context](../../../tests/unit/test_mathematical_context.py) | Scoped referents and hypotheses; arbitrary and chosen objects remain distinct |
| C0-C4 acquisition | [Core C](../../../tests/unit/test_core_c_acceptance.py), [resolver](../../../tests/unit/test_acquisition_resolver.py), [literature](../../../tests/unit/test_acquisition_literature.py), [interfaces](../../../tests/unit/test_acquisition_interfaces.py) | Typed routes, exact sources, child evidence, bounded recursion and restart refusal |
| C5/C6 proof strategies | [iterative](../../../tests/unit/test_iterative_strategy.py), [sketch](../../../tests/unit/test_sketch_strategy.py) | Shared budgets, explicit holes and final original-claim verification |
| D0/D1 literature-aware Research | [Core D](../../../tests/unit/test_core_d_acceptance.py), [Research](../../../tests/unit/test_research.py) | Four acquisition routes, self-assumption refusal, exact trust and restart status |
| D2-D4 Critique/Repair/Referee | [Critique](../../../tests/unit/test_critique.py), [Repair](../../../tests/unit/test_repair.py), [Referee](../../../tests/unit/test_referee.py) | Layer coverage, counterexamples, guarded repair and unresolved citation uses |
| D5/D6 publication | [planner](../../../tests/unit/test_publication.py), [assembly](../../../tests/unit/test_publish.py) | Minimal contexts, visibility, exact plan assembly and retained human prose |
| D7-D9 Explore | [representations](../../../tests/unit/test_explore_representation.py), [contexts](../../../tests/unit/test_explore_context.py), [research](../../../tests/unit/test_explore_research.py) | Concepts before theorem targets, context branches, transport and persistent approaches |
| E1 synthetic referee manuscript | [manuscript](../../../tests/integration/test_referee_manuscript.py) | Ten planted scenarios, clean controls, exact source coverage and restart; scripted semantic readings and synthetic authority do not measure live referee performance |
| E3 terminal publication | [session](../../../tests/test_project_publication.py), [terminal](../../../tests/tui/test_project_publication.py) | Exact selection, presentation history, deduplicated attachments, evidence refusal, fresh draft bundles and cancellation teardown; scripted compiler |
| E4 full status and context | [project summary](../../../tests/test_project_summary.py) | One persisted snapshot, scoped research and trust, stale citations, pending obligations and readiness; terminal evidence authentication remains unavailable |
| F0-F3 search and lessons | [best first](../../../tests/unit/test_best_first_strategy.py), [race](../../../tests/unit/test_race_strategy.py), [escalation](../../../tests/unit/test_escalating_strategy.py), [lessons](../../../tests/unit/test_strategy_lessons.py) | Exact claim, shared ceilings, cancellation and attributed failure lessons |
| G0-G3 manuscripts and prose | [recursive Referee](../../../tests/unit/test_referee_recursive.py), [versions](../../../tests/unit/test_version_audit.py), [structure](../../../tests/unit/test_publication_structure.py), [refresh](../../../tests/unit/test_exposition_refresh.py) | Coverage limits, invalidation, chapter membership and explicit prose revisions |
| H0-H2 reuse and exposure | [retrieval](../../../tests/unit/test_project_retrieval.py), [reuse](../../../tests/unit/test_project_reuse.py), [exposure](../../../tests/unit/test_evals_exposure.py) | Current evidence and exact-repeat/transfer/unseen declarations; fixtures do not establish gains |
| I0/X2 conversation and checkpoints | [history](../../../tests/unit/test_conversation_history.py), [terminal history](../../../tests/tui/test_conversation_history.py), [streaming](../../../tests/test_claude_runtime_stream.py) | Selected branch replay, durable partial observations and stale-worker refusal; mathematical state and all-branch spend remain current |
| I1/I2 prompt and catalog conveniences | [templates](../../../tests/unit/test_prompt_templates.py), [commands](../../../tests/tui/test_prompt_commands.py), [catalog](../../../tests/test_catalog.py) | Recorded request expansion and configured model identities; no proof evidence or invented availability |
| X0/X5 save gates and spend | [save sequence](../../../tests/unit/test_save_gate_sequence.py), [spend](../../../tests/unit/test_spend_budget.py) | Ordered refusals and durable reserve/settle; no hard billing guarantee |
| X6 durable attempts and adjudication | [batch journals](../../../tests/unit/test_batch_recording.py), [reviews](../../../tests/unit/test_evals_adjudication.py) | Crash/incomplete identity, exact completion receipts and attributed history; legacy and remote runtime identity gaps remain explicit |
| X4 ordered CAS diagnostics | [sentinel](../../../tests/unit/test_cas_sentinel.py), [echo](../../../tests/unit/test_cas_sentinel_echo.py), [export](../../../tests/unit/test_cas_export.py), [real backends](../../../tests/integration/test_cas_real.py) | Single ordered capture, split echoes, retained terminal diagnostics and legacy capture refusal; real backend tests require installed Singular/Macaulay2 |
| S3 process floor | [process](../../../tests/unit/test_process.py) | Finite requests, bounded capture, overflow, cancellation and teardown |
| V1-V3 measurements and imports | [history](../../../tests/unit/test_evals_history.py), [certification](../../../tests/unit/test_evals_certification.py), [benchmarks](../../../tests/unit/test_evals_benchmarks.py) | Comparable controls, prospective first-k receipts, exact upstream bytes and explicit unknowns |

Core I/X2 conversation fixtures and X6 journal/adjudication fixtures above are
present in the integrated tree; their item reports give focused verification
counts. All runnable primitives continue to require their own regressions as
behavior changes. V0 remains an ongoing obligation, rather than a claim that
future primitives or the deferred Core E human trials are accepted.

S1/S2 hostile-input acceptance has not passed: process bounds do not establish
filesystem/network confinement or an independent axiom-audit environment.
The pinned upstream V3 import experiments establish lexical coverage only.
Recorded Lean acceptance still has the trust limitations described in README.

The [S1/S2 capability recheck](2026-09-10-isolation-recheck.md) confirms the host
still lacks demonstrated aggregate scratch quotas and the required confinement
boundary. Those items remain unaccepted. V0 also covers transcript replacement
refusal through the portable history regression and the original native-link
case in [chat tests](../../../tests/test_chat.py); Windows skips the latter when
its token cannot create symlinks, so Linux CI supplies that platform check.
