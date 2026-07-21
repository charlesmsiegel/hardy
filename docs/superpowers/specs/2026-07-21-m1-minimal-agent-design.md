# M1 — Minimal Agent (Claude Agent SDK) — Design Spec

**Milestone goal (DESIGN.md):** the first `AgentRuntime` adapter on the Claude Agent
SDK; core tools (`check_proof`, `run_tactic`, `get_goal_state`, `search_lemmas`,
`write_latex`); the iterative-repair loop; the dual-output workflow; a minimal
`#print axioms` audit; statement faithfulness gated by an independent skeptic.

**Exit criterion:** `"prove that the square root of 2 is irrational"` produces a
compile-checked `.tex` writeup *and* a kernel-checked `.lean` proof, end to end.

## Context: what M1 builds on

M0 delivered (all interfaces live in `src/hardy/`):

- `hardy.lean.pool.ReplPool` — warm sandboxed workers, base-env isolation,
  `check_proof(code) -> ProofVerdict`, recycling, poisoning on unrecoverable failure.
- `hardy.lean.repl.LeanRepl` — `run_command(code, env)`, `run_tactic(tactic,
  proof_state)`; timeout/death handling that leaves dirty workers dead.
- `hardy.lean.feedback.ProofVerdict` — structured pass/fail with errors, warnings,
  sorries, failure kind.
- `hardy.latex.template.render_writeup(...)` — two-grade status block
  (formalization status; informal completeness hardcoded *not assessed* pre-M6).
- `hardy.latex.compile.compile_tex_sandboxed(...)` — disclosure-safe sandboxed
  compile with structured `TexError`s.
- `hardy.lean.launch.sandboxed_worker_spec()` — pool-ready sandboxed worker spec.

M1 adds the first model-facing layer. **No agent code exists yet; everything below
is new.**

## Global constraints (inherited from DESIGN.md)

- Tools return compact, high-signal output; errors are actionable; no hidden state.
- The harness never trusts model output: Lean goes through the kernel (sandboxed
  pool), TeX through the sandboxed compiler.
- Informal completeness is reported as *not assessed* — never defaulted upward
  (pre-M6 rule; the M0 template already hardcodes this).
- **No citations in M1 writeups** — `cite`/`references.bib` is M3. The template has
  no `\cite`/`\bibliography`.
- Proving never starts on an unconfirmed statement: a rejected faithfulness review
  loops back to re-formalization (bounded) or stops with an explicit
  unconfirmed-statement outcome.
- Runtime-specific features sit behind capability flags; every strategy must have a
  degraded-but-functional path on the minimal loop (M5 proves this; M1 must not
  paint us into a corner).

## Architecture

```
hardy/agent/
  runtime.py      — AgentRuntime protocol + RunConfig + Trajectory (runtime-neutral)
  claude_sdk.py   — first adapter: Claude Agent SDK implementation
hardy/tools/
  registry.py     — ToolDef (name, description, JSON schema, async handler), ToolRegistry
  lean_tools.py   — propose_statement, check_proof, run_tactic, get_goal_state, search_lemmas
  latex_tools.py  — write_latex
hardy/workflows/
  prove.py        — the Prove workflow: formalize → faithfulness gate → search → audit → writeup
  faithfulness.py — independent skeptic review of the formalized statement
  audit.py        — minimal #print axioms audit
hardy/lean/session.py — ProofSession: a leased pool worker with proof-state access
hardy/prompts/
  __init__.py     — versioned prompt template loading
  prove_v1.py     — system + user prompt templates for the M1 loop
results/<slug>/   — output contract: <slug>.tex, <slug>.lean (when proved), manifest.json
```

### Component 1: harness-owned tool definitions (`hardy.tools`)

The portability seam from DESIGN.md Component 3: tools are defined once — name,
description, JSON schema (pydantic-generated), async handler — with **zero imports
from any agent SDK**. Each runtime adapter is responsible only for exposing them.

- `ToolDef(name: str, description: str, input_model: type[BaseModel],
  handler: Callable[[BaseModel], Awaitable[ToolResult]])`.
- `ToolResult(content: str, is_error: bool = False)` — content is always a compact
  string (the model-facing rendering); handlers do the truncation/dedup so every
  runtime shows the model identical text.
- `ToolRegistry` — the set of tools for a run; workflows assemble it, adapters
  consume it.

Output-shaping rules (Component 2 of DESIGN.md, enforced in handlers, unit-tested):

- Goal states over ~4 KB are truncated middle-out (head + tail, marker with elided
  line count) — never silently.
- Repeated identical errors are deduplicated with a count.
- Every Lean error rendering includes position, message, and the offending source
  line so the model never has to guess.

### Component 2: the M1 tool set

| Tool | Backing | Behavior |
|------|---------|----------|
| `propose_statement` | `ProofSession.check` | Formalize-phase only: submit a candidate `theorem` declaration; harness appends `:= by sorry`, elaborates, returns structured feedback. The statement freezes on first clean elaboration. |
| `check_proof` | `ProofSession.check` | Submit complete Lean source for the *fixed* statement; returns verdict rendering (success, or errors/sorries with positions). Also records the attempt in the trajectory. |
| `run_tactic` | `ProofSession.tactic` | Apply one tactic to a named proof state id; returns new goals or error. |
| `get_goal_state` | `ProofSession` state table | Pretty-printed goals + hypotheses for any proof state id the session has seen (from `sorries` or `run_tactic` results). |
| `search_lemmas` | `ProofSession.tactic` | M1 scope: proof-state-driven only — `exact?`/`apply?`/`rw?` are *tactics*, so they run through the REPL's tactic command against the named proof-state id (a file-level command could never address one); the handler renders the suggestion messages Lean prints. Loogle/LeanSearch are out of scope until M8 (retrieval). |
| `write_latex` | `render_writeup` + `compile_tex_sandboxed` | Takes title/informal-proof fields (not raw TeX preamble — the template owns the document shell), renders, compile-checks, returns structured errors or success. The theorem statement is **not** an input: the handler injects the harness-owned statement (frozen at formalization), so the agent cannot ship a polished writeup about a different claim. |

Statement immutability: `check_proof` takes only the *proof body*; the harness owns
the theorem statement (fixed at formalization time) and splices the body in. The
model cannot restate the theorem — the M2 anti-cheat assumption starts true by
construction in M1.

### Component 3: `ProofSession` (stateful Lean access)

`ReplPool.check_proof` is stateless by design; `run_tactic`/`get_goal_state` need
proof states, which are **per-worker-process** ids. New: `ProofSession`, a lease of
one pool worker for the duration of one agent task.

- `async ReplPool.lease() -> ProofSession` — checks a worker out of the idle
  queue. The lease is an **async context manager** (`async with pool.lease() as
  session:`); exit — including cancellation by the wall-clock deadline or any
  exception — always returns the worker (or retires it if dirty/over-budget), so
  cancelled agent tasks can never strand workers and exhaust the pool. A bare
  `release()` exists but the context-manager form is the contract workflows use.
- All session commands fork from `base_env` exactly like `check_proof`; the session
  additionally tracks `proof_state` ids returned in `sorries`/tactic responses, and
  the goal text for each, so `get_goal_state` answers from the session table.
- A timeout/crash inside a session invalidates **all** its proof states (they lived
  in the dead process). The session recovers *within* the task: it retires the dead
  worker and transparently acquires a replacement from the pool on the next tool
  call, so `check_proof` (which re-elaborates from source) works again immediately;
  only `run_tactic`/`get_goal_state` against pre-death proof-state ids return the
  actionable error ("state lost with a recycled worker — re-`check_proof` from
  source"). Without in-task replacement, one timed-out tactic would consume the
  rest of the proving run. Replacement failure (pool poisoned/broken) ends the
  task. Proof-state pickling (DESIGN Component 1) is deferred — M1 accepts state
  loss on worker death.
- Sessions count against the same recycling budgets (`max_commands`, RSS) as
  stateless checks.

### Component 4: `AgentRuntime` + trajectory (`hardy.agent.runtime`)

- `RunConfig(model: str, max_turns: int, max_tokens_total: int | None,
  wall_clock_s: float, prompt_version: str, runtime: str = "claude_sdk")` —
  pydantic; config, not code.
- `Trajectory` — the normalized record every adapter must emit: ordered
  `TrajectoryEvent`s (`assistant_text`, `tool_call`, `tool_result`, `usage`) with
  timestamps, plus totals (turns, tokens, wall clock) and the final text. This is
  the format M2 metrics and Component 9 telemetry consume; it is defined here, not
  in the adapter.
- `AgentRuntime` protocol: `async run(task: str, system_prompt: str,
  tools: ToolRegistry, config: RunConfig) -> Trajectory`. Budget enforcement is
  part of the protocol contract, owned by every adapter: `max_turns`, wall-clock,
  **and `max_tokens_total`** — enforced *before* each call, not just between
  calls: the adapter counts (or conservatively estimates) the pending request's
  tokens against the remaining allowance and clamps the response's
  max-tokens parameter to what remains; if the request plus a minimum useful
  response no longer fits, it stops without issuing the call. A check that
  runs only after the response would let one final call overshoot the cap by
  its own size, and those tokens are unrecoverable. Exhaustion is recorded in
  the trajectory with its kind. Token-limited runs exceeding their cap would
  invalidate every fixed-budget comparison M2/M7/M8 make (M7 later generalizes
  this same reserve-then-settle discipline into the shared strategy meter).
- `ClaudeSdkRuntime` — first implementation, on `claude-agent-sdk`: exposes the
  registry as in-process (SDK/MCP) tools, enforces all three budgets as above,
  records every event into the trajectory. SDK-specific niceties (subagents,
  compaction) are **not used in M1** — the M1 loop must be reproducible on the
  minimal runtime later.

### Component 5: the Prove workflow (`hardy.workflows.prove`)

Sequential phases, each a plain async function so M5 can rerun them on other
runtimes and M6 can splice Critique/Repair in between. **The budgets are
run-level, shared across phases**: the workflow owns one reserve-and-settle
meter for tokens/turns/wall-clock, and each phase's `AgentRuntime.run` receives
the meter's *remaining* allowance as its config — per-invocation caps that
reset each phase would let a nominal 10k-token run spend several multiples of
that (three rejected faithfulness rounds alone are seven agent runs before the
writeup). Phase budget splits are config, but the sum is the cap.

1. **Formalize.** An agent run turns the user's informal claim into a Lean
   `theorem` statement. `check_proof` cannot serve here — it takes a proof body
   for an already-frozen statement, and no statement exists yet — so the
   formalize phase gets its own tool, **`propose_statement`**: it accepts
   **exactly one bodyless `theorem <name> : <prop>` declaration** — the handler
   parses the submission and rejects multiple commands, auxiliary declarations,
   or a theorem that already carries a body *before* elaboration (otherwise a
   completed declaration smuggled alongside a bodyless one could elaborate
   cleanly while leaving ambiguous which header the harness froze, defeating
   the immutability guarantee) — then appends `:= by sorry`, elaborates it
   through the session, and returns structured elaboration feedback (errors
   with positions, or clean). The first clean elaboration fixes that round's
   **candidate**; `propose_statement` is only in the formalize phase's
   registry, and bounded retries apply. The freeze is **per-round**: a
   faithfulness rejection (step 2) explicitly discards the candidate and opens
   a fresh formalization round — without that transition, an implementation
   honoring the freeze would either keep proving the rejected statement or
   refuse the corrected one. The statement becomes immutable for the rest of
   the run only when a round's candidate is *accepted* by the skeptic.
2. **Faithfulness gate** (`hardy.workflows.faithfulness`). An *independent* skeptic
   — separate agent run with its own prompt (`faithfulness_v1`), no shared context
   with the formalizer — sees the informal claim and the formal statement, and
   answers a forced-choice verdict (`faithful` / `unfaithful(reason)`), checking
   quantifiers, hypotheses, edge conditions, direction of implication. Rejection
   loops back to step 1 with the reason attached, at most `max_formalize_rounds`
   (default 3); exhaustion is the explicit *unconfirmed-statement* outcome — the
   run stops, ships a failure-report writeup, and never enters proving.
3. **Prove (iterative repair).** The main agent run: system prompt + statement +
   tools (`check_proof`, `run_tactic`, `get_goal_state`, `search_lemmas`); loop
   until `check_proof` reports complete or budget (turns, wall-clock, or total
   tokens) expires.
   Strategy is hardcoded iterative-repair in M1; the strategy interface is M7.
4. **Audit** (`hardy.workflows.audit`). For a complete proof: run
   `#print axioms <name>` **in the environment id returned by the successful
   `check_proof`** — the theorem exists only there; `base_env` holds just the
   imports, so an audit forked from it could never find the declaration and
   every success would fail its own audit. The session retains the winning
   command's env id for exactly this purpose (ordinary checks keep forking from
   `base_env`). The audit **fails closed**: it passes only on a successful,
   error-free response whose payload parses as a recognized axiom list for the
   audited declaration — a timeout, crash, missing declaration, or unparsable
   output must demote the result as an audit *failure*, never fall through as
   an empty axiom set that vacuously satisfies the subset test. Parse the
   axiom list; pass iff it is a subset of `{propext, Classical.choice, Quot.sound}`
   and contains no `sorryAx`. Fail → the result is demoted to *partially
   formalized* and the discrepancy recorded in the manifest (the full anti-cheat
   suite — statement diffing, suspicious-closer scan — is M2).
5. **Write up.** One more agent run drafts title/informal proof for `write_latex`;
   the harness sets the grades itself (formalization status from steps 3–4;
   informal completeness *not assessed*). When a formal statement exists, the
   writeup's stated theorem is **rendered from the Lean statement** (single source
   of truth, DESIGN output contract) — concretely in M1: the template's theorem
   block typesets the Lean statement verbatim in a listing alongside an informal
   restatement the skeptic verified, and the manifest records the statement hash
   so any later drift between artifacts is detectable; a not-formalized run
   preserves the user's claim verbatim, flagged as informally stated. Bounded compile-and-repair
   retries; on exhaustion, ship the minimal compile-checked failure report
   (known-good template: status, errors, failing source attached) — LaTeX-always
   holds even when the generated document never builds, and the run is graded
   failed.
6. **Persist.** `results/<slug>/`: `<slug>.tex`, `<slug>.lean` (when a checked
   proof exists), `manifest.json` (statement, grades, axiom audit result,
   faithfulness verdict, budgets spent, trajectory reference), and the trajectory
   itself as JSONL. The `.tex` and `.lean` cross-link by relative path.

### Prompts (`hardy.prompts`)

Versioned, swappable (DESIGN Component 3): each template is a named constant with a
version suffix (`PROVE_V1`, `FAITHFULNESS_V1`, `WRITEUP_V1`); `RunConfig` selects by
name; the manifest records which version ran. No template logic beyond `.format()`
substitution — prompt strategy is an experimental variable, so it must be diffable.

## Key decisions and rationale

- **Session lease vs. stateless-only tools.** Considered shipping M1 with
  `check_proof` only (no lease machinery). Rejected: `run_tactic`/`get_goal_state`
  are named M1 deliverables in DESIGN.md, and iterative repair without goal
  inspection wastes most of the kernel's signal. The lease is a small extension of
  the pool's existing checkout discipline.
- **Harness-owned statement, body-only `check_proof`.** Alternative: let the model
  submit whole files and diff the statement afterward (M2-style). Rejected for M1:
  splicing makes statement tampering impossible rather than detectable, and is
  simpler than diffing.
- **Faithfulness skeptic as separate agent run, same model.** DESIGN allows
  "separate prompt or model"; M1 uses the same model with a fresh context and its
  own prompt (cheapest thing that is genuinely independent — no shared context with
  the formalizer). A different-model skeptic is config, not code, once M5 lands.
- **Workflow phases as plain functions, not SDK subagents.** Keeps the M5
  portability promise: the phases compose in Python, and each phase is one
  `AgentRuntime.run` call.

## Testing strategy

Same tiers as M0 (`pyproject.toml` markers):

- **Unit (default):** ToolRegistry/ToolDef schema generation; output-shaping rules
  (truncation, dedup) with synthetic verdicts; `ProofSession` state table and
  death-invalidates-states against `tests/fake_repl.py`; workflow phase logic with
  a `FakeRuntime` (scripted `Trajectory` responses — no model, no network);
  faithfulness gate loop-back and bounded exhaustion; audit parsing of
  `#print axioms` output fixtures; manifest writing.
- **`lean`:** `ProofSession` against the real REPL (lease, sorries → proof states,
  `run_tactic`, audit of a real proof).
- **`docker`:** one end-to-end dry-run with `FakeRuntime` inside the sandbox images
  (no model).
- **`model` (new marker):** anything that calls a real model, including the exit
  criterion script `scripts/prove_sqrt2.py`. Never runs in CI.

## Out of scope for M1

- Citations/bibliography (M3), assumed papers (M4), other runtimes (M5), hole
  ledger/critique/repair (M6), search strategies beyond iterative repair (M7),
  retrieval/memory, Loogle/LeanSearch (M8), proof-state pickling, benchmark
  running/metrics (M2), SDK subagents/compaction.
