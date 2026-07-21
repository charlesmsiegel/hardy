# Milestone Design Specs

One spec per remaining milestone of [DESIGN.md](../../../DESIGN.md)'s roadmap.
M0 (plumbing) is implemented — its plan lives at
[../plans/2026-07-21-m0-plumbing.md](../plans/2026-07-21-m0-plumbing.md) — so the
specs start at M1.

Each spec is the input for that milestone's implementation plan (written when the
milestone starts, same process that produced the M0 plan). Specs for later
milestones are expected to be **revised as earlier milestones land** — interfaces
named here for not-yet-built components (e.g. M6's ledger consumed by M7) are
design intent, and the spec of a milestone should be re-reviewed against reality
before its plan is written.

| Spec | Milestone | Exit criterion (short form) |
|------|-----------|------------------------------|
| [M1 — Minimal agent](2026-07-21-m1-minimal-agent-design.md) | Claude Agent SDK adapter, core tools, iterative repair, dual output, faithfulness skeptic | "prove √2 is irrational" → compile-checked `.tex` + kernel-checked `.lean`, end to end |
| [M2 — Evaluation harness](2026-07-21-m2-evaluation-harness-design.md) | miniF2F runner, anti-cheat, metrics, regression tracking | reproducible baseline number for the M1 agent |
| [M3 — Literature layer](2026-07-21-m3-literature-layer-design.md) | arXiv tools, paper store, machine-maintained `references.bib`, cited writeups | a writeup citing fetched papers with a valid bibliography |
| [M4 — Assumed-paper libraries](2026-07-21-m4-assumed-papers-design.md) | `assume_paper` pipeline, `Papers.*` package, axiom manifests | assume a real paper, prove a corollary, writeup states assumptions |
| [M5 — Runtime abstraction](2026-07-21-m5-runtime-abstraction-design.md) | Strands adapter + minimal loop with prompted fallback | same eval across three runtimes from config alone |
| [M6 — Critique & repair](2026-07-21-m6-critique-repair-design.md) | hole ledger, three-layer critique, local repair, the loop | find a known subtle gap, patch it, re-verify to a clean ledger |
| [M7 — Search strategies](2026-07-21-m7-search-strategies-design.md) | strategy interface, sketch/best-first/parallel/closers | a strategy beats contemporaneous iterative repair at equal budget |
| [M8 — Retrieval & memory](2026-07-21-m8-retrieval-memory-design.md) | semantic premises, cross-theorem memory, summarization | three toggled contemporaneous comparisons, all measured |

## Cross-milestone dependencies to watch

- **M1 defines the seams everything else uses:** `ToolRegistry`, `AgentRuntime`,
  `Trajectory`, `RunConfig`, `ProofSession`, workflow-phases-as-functions. M2, M5,
  M6, M7, M8 all consume them; changes there ripple furthest.
- **M2's tracking discipline** (config hash + git SHA, contemporaneous baselines)
  is reused verbatim by M7 (`compare_strategies.py`) and M8 (`compare_configs.py`).
- **M1's faithfulness-skeptic pattern** recurs in M4 (axiom review) and M6
  (formalization probing).
- **M4's `ensure_axiom` + axiom-manifest partition** feed M6's kernel critique
  layer and the writeup grading.
- **M6's hole ledger** is the substrate for M7's sketch-and-discharge.
- **M7's budget meter and comparison harness** are preconditions for M8's exit
  criteria.
- **Deferred debts noted where they land:** proof-state pickling (deferred in M1,
  paid in M7); Loogle/semantic `search_lemmas` (deferred in M1, paid in M8);
  citations (deferred in M1, paid in M3); real informal-completeness grades
  (hardcoded pre-M6, activated in M6).

Build order is numeric. The known safe deviation, should priorities shift: M5
depends only on M1+M2, and M3+M4 are independent of M5–M8's internals — but every
spec here assumes numeric order for what exists when it starts.
