# llm-math: An Agentic Harness for Theorem Proving in Lean 4

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

The LaTeX document records formalization status (verified / partially formalized with
`sorry`s remaining / not yet formalized) and, when a Lean proof exists, the two are
cross-linked so the informal writeup and formal proof state the same theorem. This
mirrors how the strongest draft-sketch-prove systems work (informal reasoning first,
formal second) and means the project always yields a usable artifact even when full
formalization fails — a LaTeX proof marked "not yet formalized" is a result; a failed
Lean attempt alone is not.

## Architecture Overview

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
| `note` | Informal scratchpad — write natural-language reasoning/proof plans that persist in context across attempts |
| `arxiv_search` | Query the arXiv API (title/abstract/author/category); returns metadata + abstracts |
| `fetch_paper` | Download a paper (PDF, and LaTeX source when available) into the project paper store; auto-adds a BibTeX entry |
| `read_paper` | Extract text/sections from a stored paper for the agent to read |
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

## Component 4: Orchestration & Search Strategies

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

## Component 6: Lean Environment Management

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

## Component 7: Evaluation Harness

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
  - `#print axioms` shows only expected axioms (no `sorryAx`, no smuggled axioms);
  - flag suspicious closers (`native_decide`, `decide` on huge goals) for review.
- **Output-contract check**: outside pure benchmark mode, a run isn't complete
  without its compile-checked LaTeX writeup; the Lean artifact is graded as
  verified / partial / absent.
- **Regression tracking**: every change to prompts/tools/strategy runs against a fixed
  eval set; results logged with config hashes so runs are comparable.

## Component 8: Telemetry & Trajectories

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
5. **M4 — Runtime abstraction proven**: Strands adapter + built-in minimal loop
   (Ollama / OpenAI-compatible endpoints) with prompted tool-calling fallback.
   Exit criterion: the same eval runs across all three runtimes from config alone.
6. **M5 — Search strategies**: sketch-and-discharge, parallel attempts, cheap-closer
   pre-pass; strategy comparison on the eval set.
7. **M6 — Retrieval & memory**: semantic premise search, cross-theorem memory,
   context summarization improvements.

## Open Questions

- REPL choice: `leanprover-community/repl` vs. Pantograph vs. LeanDojo — prototype
  against `repl` first, but keep the interaction layer abstract enough to swap.
- Statement source of truth: for benchmarks, statements are given; for general use,
  who formalizes? (Deferred with autoformalization.)
- How much Lean-specific prompting is too much? A harness goal is that *tool design*
  carries the Lean expertise, so weaker/general models still function.
