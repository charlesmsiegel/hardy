# Hardy feature inventory

This is the consolidated backlog extracted from the former milestone specs and
implementation plans. It describes desired behavior, not current implementation:
after the repository reset, **none of these features is implemented**.

Priority labels are sequencing hints:

- **Now** — shortest vertical slice needed for useful experiments.
- **Next** — makes experiments honest, repeatable, or substantially more useful.
- **Later** — scale, optimization, breadth, or production hardening.

## End-to-end product behavior

- **Now — Prove workflow:** accept an informal or Lean theorem, obtain a candidate
  proof from a model, feed Lean errors back, and stop with a checked proof or an
  explicit partial/failure result.
- **Now — Linked artifacts:** save the exact Lean statement and proof plus a
  human-readable writeup about the same claim.
- **Now — Honest grades:** independently report formalization status and informal
  completeness; never infer mathematical validity from compiled prose.
- **Next — Statement faithfulness gate:** use an independent prompt or model to
  compare the user's claim with its Lean formalization before proof search.
- **Next — Critique workflow:** inspect user, literature, or generated proofs and
  produce a structured ledger of gaps.
- **Next — Repair workflow:** patch one gap locally, without changing the claim,
  then recheck the patch's blast radius.

## Lean interaction and proof tools

- **Now:** invoke a pinned Lean 4 + Mathlib environment and return structured
  elaboration errors and goals.
- **Now:** tools to check a complete proof, try a tactic, inspect goal state, and
  search available declarations.
- **Now:** preserve the original statement and reject completed artifacts that use
  `sorry` or `admit`.
- **Next:** audit `#print axioms`; distinguish standard axioms, forbidden
  `sorryAx`, and explicitly declared paper assumptions.
- **Next:** incremental proving with `sorry`-backed sketches while ensuring only
  the final grade requires a hole-free proof.
- **Later:** persistent REPL sessions, warm worker pools, pristine reset per run,
  process-death recovery, timeouts, and proof-state snapshot/pickling.
- **Later:** hybrid cheap closers such as `simp`, `omega`, `aesop`, `exact?`, and
  `duper` before spending model tokens.

## Agent runtime and context

- **Now:** one minimal turn loop with typed tool definitions, bounded tool output,
  configurable model identity, turn limit, and wall-clock limit.
- **Now:** structured trajectories containing prompts, responses, tool calls,
  tool results, Lean feedback, timing, usage, and terminal reason.
- **Next:** token and cost budgets with reserve/settle accounting.
- **Next:** a runtime interface selected by configuration rather than workflow
  code; capability flags for optional provider behavior.
- **Later:** adapters for hosted agent SDKs, Strands, and bare
  Ollama/vLLM/OpenAI-compatible endpoints, including prompted JSON tool calling
  when native tools are unavailable.
- **Later:** summarize failed attempts into compact lessons rather than replaying
  entire transcripts; measure whether summarization loses needed context.

## Search and orchestration

- **Now:** iterative repair—submit, observe Lean feedback, revise, repeat.
- **Next:** a pluggable strategy seam with shared token, wall-clock, and Lean-CPU
  budgets.
- **Later — Sketch and discharge:** create an informal plan and Lean skeleton,
  then solve holes independently.
- **Later — Best-first search:** rank a frontier of proof states and request
  multiple tactic proposals per state.
- **Later — Diverse parallel attempts:** run independent approaches and accept
  the first verified result.
- **Later:** escalate strategy after repeated no-progress and degrade gracefully as
  budget runs low.

## Critique and repair details

- **Next:** persistent ledger entries with `open`, `patched`, `verified-closed`,
  `dismissed`, and `abandoned` states plus evidence and stable identity.
- **Next:** three critique layers: kernel checking, formalization probing, and
  adversarial skeptics checking edge cases, intermediate claims, and citations.
- **Next:** crash-safe patch history; overlapping changes reopen affected holes
  instead of creating misleading new identities.
- **Next:** resolved entries remain as history; budget expiry marks and reports all
  unresolved entries; critique-only requests never repair automatically.
- **Later:** reuse ledger holes as the work units for sketch-and-discharge.

## Writeups, papers, and bibliography

- **Now:** generate a plain human-readable writeup and label its verification
  status clearly.
- **Next:** compile-check LaTeX and fail on missing references.
- **Next:** fetch arXiv metadata and content politely with rate limiting and query
  caching; resolve and store immutable versioned records with content digests.
- **Next:** treat downloaded archives as hostile: normalized extraction, symlink
  defense, file/byte quotas, temporary staging, and atomic admission.
- **Next:** maintain one canonical bibliography, deduplicated by versioned arXiv ID
  or DOI, with stable collision-safe cite keys and one controlled write path.
- **Next:** tools to search, fetch, read, and cite papers; citations flow through
  the compiled writeup.

## Assumed-paper libraries

- **Later:** eagerly inventory a paper's statements but mint axioms lazily on use;
  a standalone Assume request can mint an explicitly selected set.
- **Later:** put version-specific axioms in `Papers.<CiteKey>` namespaces, with
  docstrings tied to paper numbering and bibliography keys.
- **Later:** independently review each formalized statement for faithfulness;
  quarantine failures rather than making them importable.
- **Later:** map definitions to Mathlib first, create real definitions when cheap,
  and otherwise record opaque constants and characterizing axioms as added trust.
- **Later:** perform cheap refutation checks and include an exact axiom manifest in
  every downstream artifact; grade such proofs “verified modulo” those assumptions.

## Evaluation and reproducibility

- **Next:** benchmark loaders that preserve statements and imports exactly; pure
  benchmark mode skips formalization and writeup generation.
- **Next:** fail-closed anti-cheat: reconstruct the statement, scan live code for
  holes, audit axioms, and flag suspicious computational closers in source and
  trajectories.
- **Next:** certified pass@1/pass@k at fixed budget, provisional results kept
  separate, cost and Lean CPU per solve, makespan, utilization, failure kinds, and
  per-domain breakdowns.
- **Next:** canonical configuration hashes plus immutable code, worker, model,
  toolchain, corpus, and annotation identities; crash-safe attempt journals and
  append-only adjudication.
- **Later:** miniF2F first, followed by PutnamBench, ProofNet, and held-out custom
  sets; regression tracking for prompts, tools, runtimes, and strategies.
- **Later:** compare variants contemporaneously under identical environments and
  budgets rather than against stale historical numbers.

## Retrieval and memory

- **Later:** rank premises for the current goal using built-in search and Loogle,
  then add a versioned embedding index and persistent retrieval service.
- **Later:** meter retrieval CPU and include model, tokenizer, pooling, corpus, and
  index identities in provenance.
- **Later:** store proved lemmas, successful tactic patterns, and domain lessons
  with provenance, deduplication, supersession, and portability checks.
- **Later:** contamination-aware recall; benchmark transfer only on held-out
  theorems and report exact-repeat cache savings separately.

## Safety and operations

- **Now:** prominently warn that the experimental path executes only trusted model
  output in a disposable local environment.
- **Next:** deterministic timeouts, bounded outputs, durable/atomic result writes,
  and redaction of secrets from provider configuration and trajectories.
- **Later, before untrusted or shared use:** restore isolation for Lean, TeX, paper
  extraction, and helper processes with no network by default, read-only inputs,
  quota-limited scratch space, resource limits, and hostile-input testing.

## First experiment acceptance test

Given a small theorem such as the irrationality of `√2`, a configured model can
use structured Lean feedback to produce a `sorry`-free source file accepted by the
kernel, save a complete trajectory, and generate a clearly graded writeup about the
same statement. A failed attempt still leaves an intelligible trajectory and an
honest partial result.
