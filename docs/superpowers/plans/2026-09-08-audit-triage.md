# Audit triage implementation plan

**Goal:** Check the health report's dead-code and duplicate findings against current source, remove confirmed redundancy, and explain false positives with evidence.

**Architecture:** Preserve the current application/workflow/capability boundaries and observable output. Retain explicit compatibility exports and callback signatures. Share only identical operations with a clear owner.

**Constraints:** Work on main; leave corpus content and recorded identities intact. No evaluation or sweep may be running while sources change. Use the existing `.venv-refactor` environment because `.venv` points to a missing interpreter.

- [x] Read report metadata, raw analyzer evidence, startup documents, and detector implementations; reproduce the findings against current source.
- [x] Record dispositions for all 32 duplicate groups, unused imports, parameters, and function/class candidates.
- [x] Run existing tests and add characterization cases for exact digest bytes and bounded splitting before refactoring.
- [x] Remove unused `has_appendix`, `scoreboard_corpus_issues`, and `read_stage_b`'s unused tactic argument, updating its callers.
- [x] Consolidate identical digest serialization, command quote stripping, bounded splitting, string scanning, and capture-marker emission under their existing owners. Preserve error wording and control-flow boundaries.
- [x] Run focused tests, the hermetic suite with coverage, module boundary checks, and lint on the diff. Record platform limitations separately from regressions.
- [x] Add a linked triage report to the health pages, retaining their original scores as historical, untriaged detector output.

Theory: Structural similarity is a lead; deletion requires caller evidence, and extraction requires the same operation and contract. Distinct digest tags, parser states, protocol signatures, and refusal explanations are meaningful behavior. Shared serialization must preserve the exact existing bytes.

Review: an independent reviewer found no concrete src/tests regression and agreed with all 22 retained duplicate groups. The ledger covers 691 records; qualified references were resolved for every retained function/class candidate. Full verification: 3,455 passed, 79 pre-existing/reproduced-original failures, 135 skipped, 36 deselected; coverage 89.72%. No new regression identified. See docs/overview-evidence/dead-code-verification.json.
