# Hardy: An Agentic Harness for Theorem Proving in Lean 4

## Vision

Claude Code and Codex demonstrate that a well-designed *harness* — the loop, tools,
feedback channels, and context management wrapped around a model — matters as much as
the model itself. This project applies that lesson to formal mathematics: build a
model-agnostic agentic harness that makes any capable LLM good at proving theorems in
Lean 4.

The core insight that makes theorem proving an ideal agentic domain: **verification is
free and perfect**. Unlike code (where tests are incomplete) or prose (where quality is
subjective), the Lean kernel gives an unambiguous ground-truth signal on every attempt.
The harness's job is to put the model in a tight loop with that signal, give it the
tools a human Lean user relies on, and orchestrate search when a single attempt isn't
enough.

**Output contract — LaTeX always, Lean where possible.** Every prove request yields a
*pair* of artifacts. Ask it to "prove that the square root of 2 is irrational" and you
get back:

1. `sqrt2_irrational.tex` — a human-readable writeup: formal statement, informal
   proof, citations into the project bibliography, compile-checked.
2. `Sqrt2Irrational.lean` — a kernel-checked Lean 4 proof of the corresponding
   formal statement, whenever formalization is within reach (Mathlib coverage,
   tractable statement).

The LaTeX document records formalization status (verified / verified modulo assumed
paper results (see Component 6) / partially formalized with `sorry`s remaining / not
yet formalized) and, when a Lean proof exists, the two are cross-linked so the
informal writeup and formal proof state the same theorem. This
mirrors how the strongest draft-sketch-prove systems work (informal reasoning first,
formal second) and means the project always yields a usable artifact even when full
formalization fails — a LaTeX proof marked "not yet formalized" is a result; a failed
Lean attempt alone is not.

## Architecture Overview

> An interactive, diagrammed version of this document — workflows, components,
> frontier math, trust model, and roadmap — lives at
> [docs/architecture.html](docs/architecture.html) (open it in any browser).

```
┌──────────────────────────────────────────────────────────────┐
│                        Orchestrator                          │
│      (agent loop, search strategy, budgets, retries)         │
├────────────────┬─────────────────────────┬───────────────────┤
│ Agent Runtime  │       Tool Layer        │  Telemetry        │
│ Layer (Claude  │  (Lean, LaTeX, arXiv/   │  (trajectories,   │
│ Agent SDK,     │   bibliography, search, │   metrics, logs)  │
│ Strands, …)    │   retrieval)            │                   │
├────────────────┴─────────────────────────┴───────────────────┤
│              Environment Layer                               │
│  (Lean toolchain, REPL sessions, Mathlib, LaTeX toolchain,   │
│   paper store + references.bib, sandboxing)                  │
└──────────────────────────────────────────────────────────────┘
                          │
                    Evaluation Harness
              (miniF2F, PutnamBench, ProofNet, …)
```

## Component 1: Lean Interaction Layer

The foundation everything else sits on. The model needs fast, structured feedback from
Lean — whole-file `lake build` cycles (30s–minutes with Mathlib) are far too slow for
an inner loop.

**Requirements:**
- **Persistent REPL sessions** with Mathlib pre-loaded (import once, reuse for many
  attempts). Candidates: the community [`repl`](https://github.com/leanprover-community/repl)
  (JSON-over-stdio, supports pickling/restoring proof states), Pantograph, or LeanDojo.
  Recommendation: start with `leanprover-community/repl` — simplest, actively
  maintained, and its `sorry` extraction + proof-state manipulation covers our needs.
- **Structured feedback**: goal states (hypotheses + targets), error messages with
  positions, warnings (e.g. unused variables), and elaboration info — parsed into
  typed objects, not raw text.
- **Two granularities of checking**:
  1. *Tactic-level*: apply one tactic to a proof state, get the resulting state.
     Enables tree search and incremental progress.
  2. *File/declaration-level*: check a complete proof. The final source of truth.
- **Incremental proving via `sorry`**: let the agent sketch a proof skeleton with
  `sorry` placeholders (`have` decomposition), then discharge each subgoal
  independently. This is how strong human provers and Draft-Sketch-Prove-style systems
  work, and it parallelizes naturally.
- **State pickling**: snapshot and restore proof states so search can branch without
  replaying tactic prefixes.
- **Timeouts and resource limits** per tactic and per proof (`maxHeartbeats`, wall
  clock, memory) — runaway `decide`/`simp` calls must not stall the loop.

## Component 2: Tool Layer (what the model can call)

The Claude Code analogy: `Read`/`Edit`/`Bash` made models effective at code. The
theorem-proving equivalents:

| Tool | Purpose |
|------|---------|
| `check_proof` | Submit a full proof for a theorem statement; returns success or structured errors |
| `run_tactic` | Apply a tactic to a named proof state; returns new goals or error |
| `get_goal_state` | Pretty-printed goals with hypotheses at any `sorry`/position |
| `search_lemmas` | Find relevant Mathlib lemmas: `exact?`/`apply?`/`rw?` (proof-state-driven), Loogle (type-pattern), LeanSearch/semantic search (natural language) |
| `lookup_definition` | Unfold/show the definition and signature of any constant |
| `list_premises` | Retrieval: given the current goal, rank candidate premises (start with built-in tactics + Loogle; add embedding retrieval later) |
| `sketch` | Register a proof outline with `sorry` subgoals; harness tracks which subgoals remain |
| `hole_ledger` | Record, update, and list holes found in a proof (id, location, description, status); the persistent state handed between the Prove / Critique / Repair workflows |
| `note` | Informal scratchpad — write natural-language reasoning/proof plans that persist in context across attempts |
| `arxiv_search` | Query the arXiv API (title/abstract/author/category); returns metadata + abstracts |
| `fetch_paper` | Download a paper (PDF, and LaTeX source when available) into the project paper store; auto-adds a BibTeX entry |
| `read_paper` | Extract text/sections from a stored paper for the agent to read |
| `assume_paper` | Turn a stored paper's results into an axiomatized Lean library (see Component 6) that later proofs can import |
| `list_assumptions` | Show the assumed-paper libraries in scope and, for any proved theorem, its axiom manifest (which paper results it actually used) |
| `cite` | Look up or add an entry in `references.bib`; returns the cite key to use in LaTeX output |
| `write_latex` | Write/update a LaTeX document (writeup of a result, proof notes); harness compile-checks it |

Design rules learned from coding agents:
- Tools return **compact, high-signal output** (truncate huge goal states sensibly,
  dedup repeated errors).
- Errors are **actionable**: include the failing position, the expected vs. actual
  type, and nearby context.
- The model should never need to guess hidden state — everything observable via tools.

## Component 3: Agent Runtime Layer

"Plug any model in" is realized one level up from raw completions: we abstract over
**agent runtimes** — systems that own the model loop and tool execution — and start
with the **Claude Agent SDK** as the first implementation.

- **Harness-owned, runtime-neutral tool definitions.** All tools in Component 2 are
  defined once (name, JSON schema, handler) in our code. Each runtime adapter is
  responsible only for exposing them to its loop. This is the seam that keeps us
  portable: the Lean/LaTeX/arXiv logic never depends on any SDK.
- **`AgentRuntime` interface**: roughly
  `run(task, tools, config) -> Trajectory` — start an agentic session with a system
  prompt, our tools, and budgets; stream back tool calls/results and final output in
  a normalized trajectory format.
- **Adapters, in order:**
  1. **Claude Agent SDK** (first): gets us a production-quality loop, native tool
     use, context management/compaction, and subagents for free. MVP builds here.
  2. **Strands Agents**: second adapter, and the proof that the abstraction holds —
     it also brings multi-provider model support (Bedrock, LiteLLM, etc.) through a
     single adapter.
  3. **Built-in minimal loop** for bare model servers (**Ollama**, vLLM,
     OpenAI-compatible endpoints): our own simple loop with native tool calling
     where the model supports it and a prompted/parsed JSON fallback where it
     doesn't. This is what "any model" ultimately means — including small local
     models with no agent framework at all.
- **Config, not code**: which runtime, which model, context window, cost caps,
  parallelism, reasoning-effort knobs — all per-run configuration.
- **Prompt templates versioned and swappable**, since prompt strategy is a
  first-class experimental variable.
- **Leaky-abstraction policy**: runtime-specific features (e.g. Claude SDK
  subagents/compaction) may be used behind capability flags, but every strategy must
  have a degraded-but-functional path on the minimal loop, so results remain
  comparable across runtimes.

## Component 4: Workflows, Orchestration & Search

### The core workflows

Every proof-oriented request is one of three composable workflows, or a chain of
them. (A fourth workflow, **Assume** — *"assume this paper"*, detailed in
Component 6 — prepares axiomatized paper libraries; it runs standalone or as a
preparatory step the other three call on.)

1. **Prove** — *"find a proof of X."* The flow described throughout: formalize the
   statement, search for a proof, produce the LaTeX + Lean artifact pair.
2. **Critique** — *"find holes in this proof."* Takes any proof — user-supplied, a
   proof from the literature, or Hardy's own draft — and produces a structured
   **hole ledger**: unjustified steps, missing cases, quantifier slips, circular
   arguments, misapplied citations. Three detection layers, strongest first:
   - *Kernel*: for Lean-backed proofs, holes are free and exact — `sorry`s, failed
     elaboration, axiom-manifest surprises.
   - *Formalization probing*: for informal proofs, attempt to formalize each step;
     a step that resists formalization is a suspected hole. This is the deep reason
     the dual-output contract pays off — formalization *is* hole detection.
   - *Adversarial skeptics*: agents prompted to break each step (seek
     counterexamples to intermediate claims, check edge cases, verify cited results
     actually say what the proof needs).
3. **Repair** — *"this proof has a hole; propose a fix."* Takes one ledger entry and
   patches it locally — a bridging lemma, an added case, a corrected calculation —
   without regenerating the whole proof. Each patch is verified (kernel where
   formal; re-critique where informal). **A repair may change the proof, never the
   claim**: the original theorem statement is immutable during repair. If the only
   viable fix strengthens a hypothesis or weakens the conclusion, that is a
   *revised claim* — a distinct outcome, reported as such and re-entering the loop
   as a new statement — never graded as a successful repair of the original.

### The critique–repair loop

The workflows hand off to each other iteratively:

```
Prove ──▶ draft ──▶ Critique ──▶ hole ledger
                        ▲              │ empty? ──▶ done (status per trust ledger)
                        │              ▼
                        └──────── Repair (one hole at a time)
```

Loop discipline, so it converges instead of thrashing:
- The **hole ledger is persistent state**, shared across handoffs: each hole carries
  an id, location, description, and status (`open` / `patched` /
  `verified-closed` / `abandoned`).
- After a repair, Critique re-runs over the patch's blast radius — a fix must not
  silently reopen a closed hole or introduce new ones.
- **No-progress detection**: a hole reopened N times triggers a strategy escalation
  (different decomposition, more search budget) or an honest stop.
- Exit is a fixed point: ledger empty (fully closed, graded by the trust ledger) or
  budget exhausted — in which case the artifact ships with its remaining holes
  *listed*, which is itself a useful result ("the proof is correct except for the
  interchange of limits in Step 4, which we could not justify").

Sketch-and-discharge (below) is the degenerate case where the holes are deliberate:
a proof skeleton's `sorry`s are planned holes, discharged by the same Repair
machinery.

### Search strategies

A single agent in a loop is the baseline; the interesting work is in strategies
layered on top. Make strategy a pluggable interface so we can benchmark them against
each other:

1. **Iterative repair (MVP)**: attempt whole proof → read errors → revise → repeat,
   up to a budget. This alone is surprisingly strong with good error feedback.
2. **Sketch-and-discharge**: agent writes an informal proof plan, formalizes a
   skeleton with `have`/`sorry`, then discharges subgoals independently — possibly
   with parallel sub-agents, possibly with a cheaper model on easy subgoals.
3. **Best-first tactic search**: maintain a frontier of proof states; the model
   proposes k tactics per state; rank by model-assigned or heuristic scores.
   (AlphaProof/ReProver-style, but with a general LLM as the policy.)
4. **Parallel attempts with diversity**: n independent attempts with different
   temperatures/prompts/strategies; first verified proof wins. Pass@k is the natural
   metric and verification makes majority-voting unnecessary.
5. **Hybrid automation**: always try cheap closers first (`simp`, `omega`, `aesop`,
   `exact?`, `duper`) before spending model tokens on a subgoal.

Cross-cutting orchestration concerns:
- **Budgets**: tokens, wall-clock, and Lean CPU per theorem; strategies must degrade
  gracefully when budget runs low.
- **Context management**: long proof attempts overflow context. Summarize failed
  attempts into distilled lessons ("`ring` fails here because the goal isn't in a
  commutative ring") rather than replaying full transcripts.
- **Memory across theorems** (later): a store of proved lemmas, successful tactic
  patterns, and per-domain tricks the agent can consult.

## Component 5: Literature & Writeup Layer

The research half of the harness: finding and reading prior work, and producing the
LaTeX side of the output contract.

**arXiv integration:**
- `arxiv_search` against the arXiv API with the usual filters (category — `math.NT`,
  `math.CO`, etc. — author, title/abstract text, date range).
- `fetch_paper` downloads the PDF *and the LaTeX source when available* (source is
  far more useful to a model than extracted PDF text) into a content-addressed
  paper store (`papers/<arxiv-id>/`), and registers the paper in the bibliography.
- `read_paper` serves stored papers back to the agent in digestible chunks
  (per-section, with math source intact when we have the LaTeX).
- Polite API usage: rate limiting, caching of queries and downloads — never
  re-fetch what's in the store.

**Bibliography management:**
- One canonical `references.bib` for the project, machine-maintained:
  auto-generated entries from arXiv metadata (with DOI/journal fields when arXiv
  provides them), deduplication by arXiv id/DOI, stable cite keys
  (`author2023short`), and a validation pass (bibtex parses, no duplicate keys) in CI.
- The `cite` tool is the only write path — the agent never edits the `.bib` by hand,
  so the file stays clean.

**LaTeX output pipeline:**
- A standard document template (theorem/proof environments, `\cite` into
  `references.bib`, a formalization-status block linking to the `.lean` file).
- Every generated document is **compile-checked** (`latexmk`/`tectonic`) the same way
  Lean output is kernel-checked — errors feed back to the agent as structured
  messages. Weaker than kernel verification, but the same loop shape.
- Writeups live alongside their Lean counterparts (e.g. `results/sqrt2_irrational/`
  containing `.tex`, `.lean`, and a small manifest recording status and provenance).

## Component 6: Assumed-Paper Libraries (frontier mathematics)

Mathlib covers a lot, but real research builds on results that will never be
formalized bottom-up in time to be useful. The escape hatch: **"assume this paper"**
— formalize a paper's *statements* (not its proofs) as Lean `axiom` declarations, and
prove new results in the context of those assumptions.

**The mechanism.** Lean's `axiom` is exactly the right primitive, because axiom
dependencies are tracked by the kernel and surfaced by `#print axioms`:

```lean
namespace Papers.Smith2023  -- arXiv 2301.12345

/-- Theorem 3.2 of [smith2023modular]: … -/
axiom modular_lifting (p : ℕ) (hp : p.Prime) (hp5 : 5 ≤ p) : …

end Papers.Smith2023
```

A downstream theorem proved using this shows `Papers.Smith2023.modular_lifting` in
its axiom manifest. The anti-cheat check (Component 8) stops being just a cheat
detector and becomes a **dependency ledger**: every result is reported either as
*fully verified* (standard axioms only) or *verified modulo* an explicit list of
assumed paper results — which is precisely how working mathematicians treat the
literature anyway.

**The `assume_paper` workflow:**
1. `fetch_paper` pulls the paper (LaTeX source preferred) into the store.
2. An extraction pass identifies the paper's definitions, theorems, lemmas, and
   propositions, with their statement text and numbering.
3. A formalization pass turns each *result* into an `axiom` in a per-paper namespace
   (`Papers.<CiteKey>`), with a docstring linking back to the paper's numbering and
   BibTeX key. Results the agent cannot faithfully formalize are skipped and listed
   in the library's manifest — an honest partial library beats a wrong complete one.
4. A **faithfulness review** pass (independent skeptic agent, different prompt or
   model) compares each axiom against the paper's stated theorem: quantifiers,
   hypotheses, edge conditions. Axioms it flags are quarantined pending human review.
5. The library lands under `papers_lean/` as a buildable Lean package the main
   project imports; a manifest records paper ↔ axiom ↔ status mappings.

**The hard part — definitions.** A theorem statement can be axiomatized, but it
usually refers to objects the paper defines, and *definitions cannot be assumed* the
same way. In order of preference:
1. Map onto existing Mathlib definitions when they exist (best, and makes the
   axioms interoperate with all of Mathlib).
2. Write a real Lean definition when it's cheap.
3. Declare an `opaque` constant plus characterizing axioms for its properties
   (viable, but each characterizing axiom widens the trust surface).

**Soundness risks, managed rather than ignored:**
- A mis-formalized axiom can be *inconsistent* (in the worst case `False` becomes
  derivable, and the agent can then "prove" anything). Mitigations: the
  faithfulness-review pass; keeping each library minimal (only assume what gets
  used); a lint that tries cheap refutations of each axiom (e.g. `decide`/`simp`
  finding a counterexample on small instances when the statement is decidable); and
  the axiom manifest on every result so a bad assumption's blast radius is knowable
  after the fact.
- Definitional drift: the Lean rendering of a paper's definition may subtly differ
  from the author's intent. Every writeup that uses assumed results must state its
  assumptions in prose ("assuming Theorems 3.2 and 4.1 of [smith2023modular]") so a
  human reader can audit the trust chain without reading Lean.

## Component 7: Lean Environment Management

- **Pinned toolchain**: `lean-toolchain` + `lakefile` with a pinned Mathlib revision;
  `lake exe cache get` for prebuilt oleans. Reproducibility is non-negotiable for
  benchmarking.
- **Session pool**: REPL workers are expensive to start (Mathlib import ~30–60s), so
  maintain a warm pool; recycle workers on memory bloat or crash.
- **Sandboxing**: model-generated Lean code can execute arbitrary IO at elaboration
  time (`#eval`, `native_decide`). Run workers in containers with no network and a
  read-only filesystem.
- **Docker image** with toolchain + Mathlib cache baked in, for CI and for anyone
  reproducing results.

## Component 8: Evaluation Harness

You can't improve what you don't measure. This is as important as the agent itself.

- **Benchmarks**: miniF2F (Lean 4 port) as the standard first target; then
  PutnamBench, ProofNet (formalization), and a held-out custom set to detect
  benchmark contamination/overfitting.
- **Metrics**: solve rate at fixed budget (pass@1, pass@k), cost per solved theorem
  (tokens + Lean CPU), wall-clock, and per-domain breakdowns (algebra vs. number
  theory vs. analysis).
- **Anti-cheating validation** on every "solved" theorem:
  - proof is `sorry`-free and compiles against the *original* statement (agent must
    not modify it);
  - `#print axioms` shows only expected axioms: the standard three (`propext`,
    `Classical.choice`, `Quot.sound`) plus explicitly declared assumed-paper axioms
    (Component 6) — no `sorryAx`, no smuggled axioms. Benchmark runs allow *no*
    paper axioms; frontier runs report the axiom manifest with the result;
  - flag suspicious closers (`native_decide`, `decide` on huge goals) for review —
    detected by scanning the submitted source and recorded tactic trajectory, since
    `#print axioms` alone cannot reveal which tactic produced a term.
- **Output-contract check**: outside pure benchmark mode, a run isn't complete
  without its compile-checked LaTeX writeup; the Lean artifact is graded as
  verified / partial / absent.
- **Regression tracking**: every change to prompts/tools/strategy runs against a fixed
  eval set; results logged with config hashes so runs are comparable.

## Component 9: Telemetry & Trajectories

- Log every run as a structured trajectory: messages, tool calls, Lean feedback,
  timings, token counts.
- Uses: debugging the harness, comparing strategies, mining failure modes, and — down
  the road — distillation/fine-tuning data for smaller models.

## Later Phases (explicitly out of MVP scope)

- **Autoformalization at scale**: basic natural-language → Lean *statement*
  formalization is part of the core workflow from M1 (the user says "prove √2 is
  irrational"; the agent writes the Lean statement itself, and a faithfulness check
  confirms the formal statement matches the informal claim before proving begins).
  What's deferred is the hard research version: bulk formalization of corpora and
  automated statement-equivalence checking.
- **Premise-retrieval model**: embedding-based Mathlib retrieval (ReProver-style)
  as a tool.
- **Lemma library growth**: agent proposes and proves reusable intermediate lemmas.
- **Multi-agent review**: a skeptic agent that inspects statements for vacuity or
  mis-formalization before effort is spent.

## Proposed Stack

- **Harness language**: Python (ecosystem for model APIs, async orchestration,
  data analysis). The Lean side stays in Lean (REPL) — communication over JSON/stdio.
- **Agent runtime**: `claude-agent-sdk` (first adapter); `strands-agents` (second);
  hand-rolled minimal loop for Ollama/OpenAI-compatible endpoints (third).
- **Lean**: `leanprover-community/repl`, Mathlib (pinned).
- **Literature/writeup**: `arxiv` (API client), `bibtexparser`, `tectonic` or
  `latexmk` for compile-checking.
- **Plumbing**: pydantic for typed tool/trajectory schemas.

## Milestones

1. **M0 — Plumbing**: pinned Lean 4 + Mathlib project; Python wrapper around the
   REPL with session pooling, timeouts, structured goal/error parsing; LaTeX
   template + compile-check pipeline. Exit criterion: check 100 proofs/minute
   against warm sessions; compile-check a sample writeup.
2. **M1 — Minimal agent (Claude Agent SDK)**: first `AgentRuntime` adapter on the
   Claude Agent SDK; core tools (`check_proof`, `get_goal_state`, `search_lemmas`,
   `write_latex`); iterative-repair loop; dual-output workflow. Exit criterion:
   "prove that the square root of 2 is irrational" produces a compile-checked
   `.tex` writeup *and* a kernel-checked `.lean` proof, end to end.
3. **M2 — Evaluation harness**: miniF2F runner, anti-cheat validation, metrics +
   regression tracking. Exit criterion: reproducible baseline number for M1 agent.
4. **M3 — Literature layer**: arXiv search/fetch/read tools, paper store,
   machine-maintained `references.bib`, citations wired into writeups. Exit
   criterion: a writeup that cites fetched papers with a valid bibliography.
5. **M4 — Assumed-paper libraries**: `assume_paper` pipeline (extract → formalize
   statements as axioms → faithfulness review → buildable `Papers.*` package), axiom
   manifests wired into results and writeups. Exit criterion: assume a real arXiv
   paper and prove a small corollary of its main theorem, with the writeup stating
   the assumptions.
6. **M5 — Runtime abstraction proven**: Strands adapter + built-in minimal loop
   (Ollama / OpenAI-compatible endpoints) with prompted tool-calling fallback.
   Exit criterion: the same eval runs across all three runtimes from config alone.
7. **M6 — Critique & repair**: the find-holes and fix-hole workflows, the hole
   ledger, and the full critique–repair loop — including on user-supplied informal
   proofs. Exit criterion: hand Hardy a proof with a known subtle gap; it finds the
   gap, patches it, and re-verifies to a clean ledger.
8. **M7 — Search strategies**: sketch-and-discharge, parallel attempts, cheap-closer
   pre-pass; strategy comparison on the eval set.
9. **M8 — Retrieval & memory**: semantic premise search, cross-theorem memory,
   context summarization improvements.

## Open Questions

- REPL choice: `leanprover-community/repl` vs. Pantograph vs. LeanDojo — prototype
  against `repl` first, but keep the interaction layer abstract enough to swap.
- Statement source of truth: for benchmarks, statements are given; for general use,
  who formalizes? (Deferred with autoformalization.)
- How much Lean-specific prompting is too much? A harness goal is that *tool design*
  carries the Lean expertise, so weaker/general models still function.
- Assumed-paper granularity: assume a whole paper eagerly, or lazily formalize only
  the results a proof attempt actually wants to invoke? Lazy keeps the trust surface
  minimal; eager gives the agent a browsable library. Likely answer: extract the
  full statement inventory eagerly, formalize axioms lazily on first use.
- Transitive assumptions: paper A's theorem depends on paper B's — do we chase the
  citation graph, or axiomatize A's results at face value? (Face value first; the
  manifest records exactly what was taken on faith either way.)
