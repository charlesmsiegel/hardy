# Synthetic referee manuscript implementation plan

**Goal:** Exercise roadmap E1's ten manuscript cases through the real Referee
workflow, with deterministic acceptance evidence under V0.

**Architecture:** Static TeX supplies exact manuscript/source bytes. Explicit
test semantic readings supply contexts, bindings, dependencies and a scripted
prose review. Real inventory, ledger, critique, graph and policy owners process
them. Capability receipts are synthetic and never claim a Lean/model run.

**Spec:** `docs/roadmap.md`, E1 and V0. **Stack:** Python, pytest, existing owners.

- [x] Add source fixtures and integration assertions for all ten cases, including
  weak shorthand followed by an unsupported stronger interpretation.
- [x] Observe the missing cyclic-dependency finding fail, then reuse the graph
  SCC query for the smallest structural Referee fix; cover self-dependency too.
- [x] Verify correct theorem/citation acceptance using explicit synthetic owner
  receipts, missing-hypothesis refusal, durable gaps and exact report coverage.
- [x] Run focused hermetic tests, record evidence and semantic/toolchain limits
  in the unique E1 report. Parent owns staging, commits and shared roadmap docs.
