# Hardy: design

## Vision

Hardy is a model-agnostic agentic harness for proving theorems in Lean 4. The
harness—not the model—owns verification, budgets, artifacts, and the distinction
between a promising argument and a checked proof. Lean supplies an unambiguous
kernel signal; Hardy turns that signal into a useful model feedback loop.

The project restarted from documents rather than carrying forward its original
prototype. Its first implementation is a deliberately thin interactive CLI: a
conversation with Claude carried by its agent SDK, direct Lean and LaTeX
subprocesses, a durable transcript, explicit assumption approval, and a manifest
linking formal names to LaTeX labels. Continue optimizing for
learning and add abstraction only after an experiment exposes a real seam.

## Output contract

A full Prove run aims to produce two linked artifacts:

1. a human-readable mathematical writeup; and
2. a Lean source file for the same claim.

Every result reports two independent grades:

- **Formalization:** kernel verified, verified modulo listed paper assumptions,
  partially formalized, or not formalized.
- **Informal completeness:** no gaps detected (with the checks that ran), known
  gaps listed, or not assessed.

Only Lean kernel acceptance justifies “verified.” TeX compilation checks document
construction, not mathematical truth. If Hardy cannot finish, it should still
return useful partial artifacts and state their limits rather than overclaim.

## Core workflows

- **Explore** is the primary interactive shell: the human and model develop ideas
  conversationally while Hardy maintains Lean, LaTeX, naming, assumptions, and a
  durable transcript.
- **Prove** formalizes a claim, checks that the formal statement is faithful,
  searches for a proof, and produces the artifact pair.
- **Critique** accepts a formal or informal proof and records suspected defects in
  a persistent hole ledger.
- **Repair** patches one hole without changing the claim, verifies the patch, and
  rechecks the affected region.

These compose as `Prove → Critique → Repair → Critique` until all holes are
resolved, progress stops honestly, or the budget expires. Changing a hypothesis or
conclusion creates a revised claim; it is never silently treated as a repair.

## Architecture

```text
request / proof
      │
      ▼
workflow + strategy ─────── budgets / trajectory / evaluation
      │
      ▼
model runtime ───────────── tools and bounded context
      │
      ├──────────► Lean interaction ──► kernel verdict
      ├──────────► literature / retrieval / memory
      └──────────► writeup builder ───► human artifact
```

### 1. Workflow and strategy

The orchestrator owns phases, retries, budgets, and stopping rules. Search should
begin with a simple iterative repair loop. Later strategies—cheap automation,
sketch-and-discharge, best-first search, and diverse parallel attempts—share a
small strategy interface and are compared at equal budgets.

### 2. Model runtime

Hardy authenticates through Claude Code's agent SDK, so it runs on a Claude Max
subscription rather than a metered API key. That is a deliberate trade, and the
part traded away is the agent loop: the SDK decides when a model call happens,
not Hardy.

What is *not* traded away is the trust boundary. Hardy's Lean and LaTeX tools are
registered as in-process SDK tools, so the harness still performs every proof
check and every file write, and the CLI's own Bash/Read/Write/Edit tools are
refused. Anything that is not a Hardy tool is denied by default rather than by an
enumerated list, because a list has to anticipate every tool the CLI grows.

Losing the loop costs real things, and they are recorded rather than glossed:
turn limits become the SDK's to enforce, cheap Lean closers cannot run before a
model turn is spent, and token budgets have no decision point. The wall clock is
kept by Hardy, because nothing in the SDK bounds a stalled request, and the
trajectory states which of the two enforced what. Issue #23 tracks reclaiming
the loop without giving up subscription authentication.

### 3. Tool layer

The model receives narrow theorem-proving verbs rather than unrestricted control:
check a candidate, run a tactic, inspect goals, search lemmas, manage holes, read
papers, cite sources, and write the result. Tool results should be structured,
bounded, and useful as the next model observation.

The current interactive tools check and save complete Lean and LaTeX sources, read
the managed workspace, record formal-to-document names, and pause for explicit
human approval before admitting an assumption. The naming manifest is bookkeeping
for a later translation audit, not evidence that the formalization is faithful.

### 4. Lean interaction

The initial path invokes Lean directly in a temporary directory. Persistent sessions, warm pools,
incremental state, and proof-state snapshots are optimizations to add when measured
latency warrants them. Each run begins from a known environment, preserves the
original statement, rejects `sorry` in completed proofs, and audits dependencies.

### 5. Literature and frontier mathematics

Hardy can fetch immutable, versioned papers; maintain canonical bibliography data;
and compile citations into writeups. For results beyond Mathlib, an explicit
Assume workflow may translate selected paper statements into independently
reviewed Lean axioms. Downstream artifacts list the exact assumptions used.

### 6. Critique, repair, retrieval, and memory

Critique combines kernel errors, attempts to formalize informal steps, and
adversarial review. Repair works from an event-sourced hole ledger. Retrieval ranks
relevant premises, while memory stores portable lessons and proved lemmas. Exact
replay is measured separately from transfer to held-out problems.

### 7. Evaluation and observability

Every attempt produces a structured trajectory with messages, tool calls, Lean
feedback, timings, token usage, identities, and terminal status. Evaluation uses
fixed benchmark statements, strict anti-cheat checks, immutable provenance, and
contemporaneous comparisons at equal budgets.

### 8. Installation and configuration

Hardy is only useful when Lean, LaTeX, and a model are all reachable, so getting
a machine into that state is part of the design rather than an afterthought. One
installer per operating system — Linux, macOS, and Windows, with WSL never
required — installs whatever is missing and skips whatever is present: Python,
`lake` through elan, a shared Lake project carrying Mathlib, a TeX distribution,
and Hardy itself. Platform-specific work is confined to package installation;
the environment, Lean project, config file, and verification step are shared.

Settings resolve from a TOML config file, then `HARDY_*` environment variables,
then command-line flags. `lean_project` is what makes `hardy` runnable from any
directory: Lean elaborates in that Lake project, so imports resolve the same way
wherever the conversation starts. `hardy doctor` reports each prerequisite
separately, distinguishing a missing tool from a broken one, and checks that the
Claude Code CLI is not merely installed but actually signed in.

## Trust boundary and safety

The Lean kernel is the authority for formal proof, subject to an audited axiom
set. Independent faithfulness checks protect the translation from an informal
claim to Lean. Assumed-paper axioms widen the trust base and must be visible.

The reset intentionally removes the old container sandbox. This is a sequencing
choice, not a claim that generated Lean or TeX is safe. During the experimental
phase, run only trusted output in disposable local environments. Before accepting
untrusted inputs, multi-user execution, or autonomous network access, restore
process isolation, filesystem and network confinement, resource quotas, timeouts,
and hostile-input tests.

## Build order

1. **Interactive vertical slice (implemented):** one conversational model, direct
   Lean and LaTeX invocation, linked names, explicitly approved assumptions,
   durable transcript, and saved artifacts. The older one-shot proof loop remains
   as a dependency smoke path. A real model/Mathlib acceptance run remains to be
   recorded.
2. **Honest experiment harness:** faithfulness and axiom checks, budgets, fixed
   evaluation inputs, reproducible identities, and useful failure reports.
3. **Broaden capability:** literature, critique/repair, alternative runtimes and
   strategies, retrieval, and memory—only as experiments require them.
4. **Harden:** restore isolation and quotas before the system handles untrusted
   content or is offered as a service.

The complete feature inventory is in [FEATURES.md](FEATURES.md), and the visual
map is [ARCHITECTURE.html](ARCHITECTURE.html).
