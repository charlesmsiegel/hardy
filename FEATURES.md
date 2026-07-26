# Hardy feature inventory

This is the consolidated backlog extracted from the former milestone specs and
implementation plans. It describes desired behavior and current sequencing. The
interactive CLI slice covers the items marked **Now (implemented)**; a real model,
Mathlib, and LaTeX acceptance run is still needed before it is validated.

## Interactive exploration

- **Now (implemented):** running `hardy` starts a persistent terminal conversation
  rather than requiring a prewritten theorem request.
- **Now (implemented):** the agent can check and save Lean, compile and save LaTeX,
  read its workspace, and resume from a durable manifest and transcript.
- **Now (implemented):** a naming registry links Lean declarations to LaTeX labels
  for later translation review; this link is not itself a faithfulness grade.
- **Now (implemented):** introducing an axiom pauses for human approval and records
  its exact formal/informal statements, reason, and source identity. Existing local
  Lean modules remain available through ordinary imports in the launch project.
- **Now (implemented):** on a real terminal, the session runs through a
  `prompt_toolkit`-backed shell rather than a plain `input()` loop: dim
  ghost-text completion of slash commands as you type, a `/model` selector
  (arrow keys or a row number), and Esc that really cancels an in-flight turn:
  the model stops and no further tool call runs, though work already begun is
  left to finish rather than killed halfway, and a reply that lands anyway is
  printed and labelled. Without a TTY, or with `--plain`/`HARDY_PLAIN`/
  `TERM=dumb`, or if the terminal session fails to start, the same commands and
  banner run through a line-based session instead.
- **Now:** model output is streamed as it is produced rather than printed only
  once the turn finishes, and both ends of every tool call are drawn, so a
  three-minute Lean check reports itself instead of looking like a hang
  (issue #32).

Priority labels are sequencing hints:

- **Now** — shortest vertical slice needed for useful experiments.
- **Next** — makes experiments honest, repeatable, or substantially more useful.
- **Later** — scale, optimization, breadth, or production hardening.

## End-to-end product behavior

- **Now (implemented) — Prove workflow:** accept an informal claim paired with an exact Lean theorem, obtain a candidate
  proof from a model, feed Lean errors back, and stop with a checked proof or an
  explicit partial/failure result.
- **Now (implemented) — Linked artifacts:** save the exact Lean statement and proof plus a
  human-readable writeup about the same claim.
- **Now (implemented) — Honest grades:** independently report formalization status and informal
  completeness; never infer mathematical validity from compiled prose.
- **Next — Statement faithfulness gate:** use an independent prompt or model to
  compare the user's claim with its Lean formalization before proof search.
- **Next — Critique workflow:** inspect user, literature, or generated proofs and
  produce a structured ledger of gaps.
- **Next — Repair workflow:** patch one gap locally, without changing the claim,
  then recheck the patch's blast radius.

## Lean interaction and proof tools

- **Now (implemented):** invoke a caller-supplied Lean 4 + Mathlib environment and return structured
  elaboration errors and goals. Lean is asked for `--json`, so severities,
  positions and unsolved goals are parsed values rather than matched text.
- **Now (implemented):** tools to check a complete proof, inspect a goal after a tactic prefix, and
  search available declarations.
- **Now (implemented):** preserve the original statement and reject completed artifacts that use
  `sorry` or `admit`.
- **Now (implemented):** audit `#print axioms`; distinguish standard axioms,
  forbidden `sorryAx`, and explicitly declared paper assumptions. A missing
  axiom report fails the run rather than reading as an absence of axioms.
- **Next:** incremental proving with `sorry`-backed sketches while ensuring only
  the final grade requires a hole-free proof.
- **Later:** persistent REPL sessions, warm worker pools, pristine reset per run,
  process-death recovery, timeouts, and proof-state snapshot/pickling.
- **Later:** hybrid cheap closers such as `simp`, `omega`, `aesop`, `exact?`, and
  `duper` before spending model tokens.

## Agent runtime and context

- **Now (implemented):** typed tool definitions, bounded tool output, configurable
  model identity, and a wall-clock limit Hardy keeps itself.
- **Now (implemented):** structured trajectories containing prompts, responses, tool calls,
  tool results, Lean feedback, timing, usage, and terminal reason.
- **Now (implemented):** a Claude backend carried by the Claude Code agent SDK,
  authenticated by subscription with no API key, exposing Hardy's Lean and LaTeX
  tools as in-process SDK tools so the harness still performs every check and
  write. Built-in CLI tools are refused by default rather than by enumeration.
- **Now (implemented):** `/model` lists the catalogued Claude models, switches
  mid-conversation without losing the provider thread, records the switch, and
  can save the choice.
- **Now (implemented):** model, backend, and endpoint recorded together in the
  session state, the switch event, and the `prove` trajectory; a workspace from
  before the SDK backend carries a bounded tail of its conversation forward.
- **Known gap:** the SDK owns the turn loop, so turn limits are enforced by the
  provider and only the wall clock is Hardy's. Tracked in issue #23.
- **Now (implemented):** a Codex backend for ChatGPT subscriptions, on the same
  shape, shipped as the optional `codex` extra.
- **Next:** token and cost budgets with reserve/settle accounting.
- **Next:** reclaim enough of the loop to enforce Hardy's own bounds and run
  cheap Lean closers before spending a model turn (issue #23).
- **Later:** adapters for other agent SDKs, and an API-key path for users who
  prefer one to a subscription.
- **Later:** summarize failed attempts into compact lessons rather than replaying
  entire transcripts; measure whether summarization loses needed context.

## Computer algebra

- **Now (implemented):** a persistent CAS kernel, shared by the interactive
  chat, staged runs, and the MCP server through one bounded runtime, so a cell
  costs the same budget whichever transport asked for it. SymPy by default;
  Singular and Macaulay2 when `cas_backend` names them.
- **Now (implemented):** state carries between cells. Replay is recovery and
  verification rather than the execution path, because recomputing a Gröbner
  basis every turn is not affordable.
- **Now (implemented):** a rebuild after a kernel death compares every replayed
  cell against its record and poisons the session on divergence. Running
  without error is not the same as recovering.
- **Now (implemented):** export writes a backend-native script and an `.ipynb`,
  replays the cells in a fresh kernel, compares stdout, stderr, and value repr,
  and records a per-cell verdict in both artifacts plus an `export.json` naming
  both files by digest. A diverged export is written and marked, not withheld.
- **Now (implemented):** export then runs the script it published and compares
  that transcript against the record, because replaying cells says nothing
  about whether the file works: the script renders a trailing expression so it
  prints the value the kernel reported, and a cell-boundary construct that
  breaks the file is caught here rather than by the reader. `script_verdict`
  travels in `export.json` and the notebook, and an export reproduces only when
  the cells and the script both do.
- **Now (implemented):** a capture that hit `cas_output_bytes` is never called
  verified — a sentinel backend's cell is not accepted at all, since its error
  banner may be in the discarded tail, and an export marks a truncated cell
  `unverified` rather than claiming matching prefixes are a reproduction. The
  script verdict goes `unverified` too, never `diverged`: "the script printed
  something else" is as much a claim about the unread tail as "it printed the
  same".
- **Known cost of that refusal:** a refused cell still changed the live
  namespace, and that change is now outside the accepted set. Every later cell
  that depends on it will diverge on export and fail to rebuild after a kernel
  restart, exactly as one depending on an errored cell does. The cell's record
  says so. The remedy is to rerun it printing less, or to raise
  `cas_output_bytes` and rerun it, before building on it.
- **Now (implemented):** the human drives the same kernel through `/cas`, and
  those cells enter the same log, replay, and export as the model's.
- **Now (implemented):** an absent backend registers no tools on any binding,
  rather than advertising calls that can only fail.
- **Known gap:** no interrupt. A runaway cell is stopped only by its timeout,
  which kills the kernel and costs the accumulated state. Tracked in issue #33.
- **Now (implemented):** within one Hardy process, `cas_session_seconds` bounds
  total CAS wall clock rather than only the cells a caller asked for. A rebuild
  after a kernel death and the fresh-kernel replay an export verifies itself
  with are both charged; a cell's deadline is the smaller of `cas_cell_seconds`
  and what is left of the session, so a session with one second remaining
  cannot run a sleeping cell for a minute; and `cas_reset` — a tool the model
  can call itself — clears the namespace and opens a new segment without
  refunding time already spent.
- **Known gap:** the CAS budget bounds a process, not a workspace. The spend is
  held in memory and is not written to the cell log, so reopening a saved
  session starts `cas_session_seconds` again even though the cells it replays
  to rebuild that session are charged. A long-running run is bounded; a
  workspace reopened all day is not.
- **Now (implemented):** every cell record carries the backend and probed
  version that produced it, so a saved-but-never-exported trajectory still
  names its toolchain. A log whose live segment was written by another backend
  is refused rather than replayed under the newly configured one; a reset opens
  a clean segment without deleting anything.
- **Now (implemented):** an append interrupted mid-write costs one cell, not
  the session. A malformed *final unterminated* record is treated as a torn
  append and removed; a damaged record with a terminator behind it is still
  refused, because it was durable when it was written.
- **Now (implemented):** Singular and Macaulay2 adapters, verified on Linux CI
  against the real binaries. They remain unavailable natively on Windows —
  Macaulay2 has no Windows build and Singular arrives through Cygwin — which is
  why SymPy is the default.
- **Later:** a bounded artifact reader, if binding the last value to `_` proves
  insufficient for reaching an over-large result.

## Search and orchestration

- **Now (implemented):** iterative repair—submit, observe Lean feedback, revise, repeat.
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

- **Now (implemented):** generate a plain human-readable writeup and label its verification
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

## Installation and environment

- **Now (implemented):** one installer per OS (`scripts/install-linux.sh`,
  `scripts/install-macos.sh`, `scripts/install-windows.ps1`, dispatched by
  `scripts/install.sh`) takes a machine with no prerequisites to a working
  `hardy` in a single run. WSL is never required.
- **Now (implemented):** the installers add what is missing and skip what is
  present: Python 3.11+, `lake` through elan, a shared Lake project with
  Mathlib's prebuilt cache, `pdflatex`, and Hardy in its own virtual environment.
- **Now (implemented):** settings resolve from a TOML config file, then `HARDY_*`
  environment variables, then flags; the installer writes the file with the model
  and key and never overwrites an existing one.
- **Now (implemented):** a configured `lean_project` lets `hardy` run from any
  directory, and `hardy doctor` checks Lean, Mathlib, LaTeX, and model
  configuration, and reports whether the Claude Code CLI is signed in rather than merely present.
- **Next:** pin the Lean toolchain, Mathlib revision, and TeX package set by
  identity so an installation is reproducible and can be recorded in results.
- **Later:** publish a released package so installation does not require a clone,
  and cover each installer on real Linux, macOS, and Windows runners in CI.

## Safety and operations

- **Now (implemented):** prominently warn that the experimental path executes only trusted model
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

The retained one-shot harness and its fake-process tests exercise this contract on
a trivial theorem. The primary interactive shell additionally has fake-process
coverage for linked Lean/LaTeX artifacts and assumption approval. What remains is
an actual, recorded model + pinned Mathlib/LaTeX run on a nontrivial exploration,
plus pinning toolchain identities rather than accepting caller-provided commands.

## Staged proving, verification, and acceptance

- **Now (implemented) — Frozen claims:** an approved formalization is hashed with
  its verifier environment, persisted, and read back before it is proved against.
- **Now (implemented) — Independent final verification:** the theorem is rebuilt
  from the frozen claim and rechecked by a fresh Lean; nothing the model reported
  is trusted. A changed signature or a forbidden token ends the run.
- **Now (implemented) — Controlled documents:** the model supplies prose and Hardy
  writes the LaTeX, escaping every field into a fixed template and compiling with
  a checksum-pinned Tectonic bundle. A failed compile is stored saying so.
- **Now (implemented) — Durable runs:** artifacts are written whole or not at all,
  the trajectory is sequenced and flushed, and the manifest records the hash of
  every artifact alongside the prompt-set hash and the budgets in force.
- **Now (implemented) — Bounded Lean tools over MCP:** the same tool runtime the
  agent uses in process is served over stdio, so the official proof-check budget
  costs the same whichever transport reached it.
- **Now (implemented) — Acceptance:** `hardy accept` cross-checks a run's manifest,
  trajectory, Lean source and document against each other, and its deterministic
  path needs no model, network, or toolchain.
