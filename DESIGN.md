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

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Orchestrator                        │
│   (agent loop, search strategy, budgets, retries)       │
├──────────────┬──────────────────────┬───────────────────┤
│ Model Layer  │      Tool Layer      │  Telemetry        │
│ (any LLM via │  (Lean interaction,  │  (trajectories,   │
│  adapters)   │   search, retrieval) │   metrics, logs)  │
├──────────────┴──────────────────────┴───────────────────┤
│                 Lean Environment Layer                  │
│   (toolchain mgmt, REPL sessions, Mathlib, sandboxing)  │
└─────────────────────────────────────────────────────────┘
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

Design rules learned from coding agents:
- Tools return **compact, high-signal output** (truncate huge goal states sensibly,
  dedup repeated errors).
- Errors are **actionable**: include the failing position, the expected vs. actual
  type, and nearby context.
- The model should never need to guess hidden state — everything observable via tools.

## Component 3: Model Adapter Layer

"Plug any model in" means a thin, well-specified interface:

- **Common abstraction**: `complete(messages, tools) -> (text | tool_calls)`, with
  adapters for Anthropic, OpenAI, Google, and local models (vLLM/Ollama via
  OpenAI-compatible endpoints). Either a small hand-rolled layer or LiteLLM.
- **Tool-calling normalization**: native tool-use APIs where available; a
  prompted/parsed fallback (e.g. fenced JSON blocks) for models without them.
- **Per-model configuration**: context window, cost per token, max parallel requests,
  reasoning-effort knobs, prompt-format quirks — all in config, not code.
- **Prompt templates versioned and swappable**, since prompt strategy is a first-class
  experimental variable.

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

## Component 5: Lean Environment Management

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

## Component 6: Evaluation Harness

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
- **Regression tracking**: every change to prompts/tools/strategy runs against a fixed
  eval set; results logged with config hashes so runs are comparable.

## Component 7: Telemetry & Trajectories

- Log every run as a structured trajectory: messages, tool calls, Lean feedback,
  timings, token counts.
- Uses: debugging the harness, comparing strategies, mining failure modes, and — down
  the road — distillation/fine-tuning data for smaller models.

## Later Phases (explicitly out of MVP scope)

- **Autoformalization**: natural-language statement → Lean statement (needed for
  attacking problems that aren't pre-formalized). Requires its own eval (statement
  equivalence checking is hard).
- **Premise-retrieval model**: embedding-based Mathlib retrieval (ReProver-style)
  as a tool.
- **Lemma library growth**: agent proposes and proves reusable intermediate lemmas.
- **Multi-agent review**: a skeptic agent that inspects statements for vacuity or
  mis-formalization before effort is spent.

## Proposed Stack

- **Harness language**: Python (ecosystem for model APIs, async orchestration,
  data analysis). The Lean side stays in Lean (REPL) — communication over JSON/stdio.
- **Key dependencies**: `leanprover-community/repl`, Mathlib (pinned), an LLM adapter
  layer (hand-rolled or LiteLLM), pydantic for typed tool/trajectory schemas.

## Milestones

1. **M0 — Lean plumbing**: pinned Lean 4 + Mathlib project; Python wrapper around the
   REPL with session pooling, timeouts, structured goal/error parsing. Exit criterion:
   check 100 proofs/minute against warm sessions.
2. **M1 — Minimal agent**: one model adapter (Anthropic), tools `check_proof` +
   `get_goal_state` + `search_lemmas`, iterative-repair loop. Exit criterion:
   end-to-end solve of easy miniF2F problems.
3. **M2 — Evaluation harness**: miniF2F runner, anti-cheat validation, metrics +
   regression tracking. Exit criterion: reproducible baseline number for M1 agent.
4. **M3 — Model-agnostic layer**: second and third adapters (OpenAI, local vLLM);
   prompted tool-calling fallback. Exit criterion: same eval runs across 3 providers
   from config alone.
5. **M4 — Search strategies**: sketch-and-discharge, parallel attempts, cheap-closer
   pre-pass; strategy comparison on the eval set.
6. **M5 — Retrieval & memory**: semantic premise search, cross-theorem memory,
   context summarization improvements.

## Open Questions

- REPL choice: `leanprover-community/repl` vs. Pantograph vs. LeanDojo — prototype
  against `repl` first, but keep the interaction layer abstract enough to swap.
- Statement source of truth: for benchmarks, statements are given; for general use,
  who formalizes? (Deferred with autoformalization.)
- How much Lean-specific prompting is too much? A harness goal is that *tool design*
  carries the Lean expertise, so weaker/general models still function.
