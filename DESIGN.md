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

Both formalization grades follow an audit of the axioms Lean reports for each
graded declaration, not a process exit code. `sorryAx` is fatal and no approval
can make it an assumption. “Verified modulo listed paper assumptions” is
reachable only in the interactive path, where a human is present to approve one;
`prove` and `batch` run unattended and fail closed instead. A report that is
missing, duplicated, or unreadable is a refusal rather than a pass.

A kernel-verified grade carries the record its verification hash is taken over —
the frozen claim, the elaborated Lean source, the axioms that source reported,
and the toolchain that read it — so the hash is derived rather than declared. On
read-back the grade is recomputed from the run's own artifacts instead of
believed. What no amount of hashing establishes is that Lean ran at all: the
axiom list is the one component with no second witness in the run directory.

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

That condition is now measurable rather than rhetorical. `hardy latency` times the
**prelude** — process start plus `import Mathlib`, elaborated with no proof body, so
the fixed cost is isolated from the work a warm process would still pay. A pool
recovers the prelude on every call after the first, so the decision is the share
`prelude × (calls − 1)` takes of a run, not the wall time of any single call. The
`− 1` is the load-bearing part: a warm pool still pays one import, and crediting it
with all of them is what makes an unwarranted pool look warranted.

### 5. Computer algebra

Lean answers whether a proof is correct. It does not help decide what is worth
proving, and a mathematician settles that by computing. Hardy therefore carries
a persistent computer algebra kernel: SymPy by default, because it is a Python
dependency and runs everywhere, with Singular and Macaulay2 available when
configured — far better suited to algebraic geometry, and far worse suited to
Windows, where Macaulay2 has no native build at all.

The kernel is persistent because replaying an accumulated script on every call
would recompute a Gröbner basis every turn. Replay is kept for the two jobs it
is good at: rebuilding state after a kernel dies, and checking that an exported
script and notebook actually reproduce the session. Both compare recorded
output against replayed output, because a cell that errored may already have
mutated the namespace, so a live session and a clean script can disagree
without anything saying so. A rebuild that reconstructs different values
poisons the session rather than reporting success.

Persistence is also why the kernel is interrupted rather than timed out. A cell
sent under the wrong monomial ordering can run far longer than intended, and
killing the kernel to stop it discards every value the session accumulated —
paying for one mistaken cell with all of them. Esc signals the child instead,
and the driver answers the signal rather than dying, so the cell stops and the
namespace stands. What that cannot promise is obedience: a cell inside a C loop
that never returns to its interpreter will not see the signal, so an interrupt
that goes unanswered within a short grace escalates to exactly what the timeout
did. An interrupted cell is never accepted — it did not finish, and like an
errored one it may already have changed the namespace.

Replaying the cells is not the same claim as the script working, so export also
runs the file it just published, as a subprocess, and compares that transcript
against the record too. A kernel evaluates a trailing expression and reports its
value where a plain script discards it, and a construct that is legal at the
head of a cell can be illegal partway down a file. Both verdicts are published;
an export reproduces only when both hold.

The same bounded runtime serves the interactive chat, staged runs, and the MCP
server, so a cell costs the same budget whichever transport asked for it.
Nothing computed here is evidence. The verifier never reads a CAS result, and
no computation can move a formalization grade.

### 6. Literature and frontier mathematics

Hardy can fetch immutable, versioned papers; maintain canonical bibliography data;
and compile citations into writeups. For results beyond Mathlib, an explicit
Assume workflow may translate selected paper statements into independently
reviewed Lean axioms. Downstream artifacts list the exact assumptions used.

### 7. Critique, repair, retrieval, and memory

Critique combines kernel errors, attempts to formalize informal steps, and
adversarial review. Repair works from an event-sourced hole ledger. Retrieval ranks
relevant premises, while memory stores portable lessons and proved lemmas. Exact
replay is measured separately from transfer to held-out problems.

### 8. Evaluation and observability

Every attempt produces a structured trajectory with messages, tool calls, Lean
feedback, timings, token usage, identities, and terminal status. Evaluation uses
fixed benchmark statements, strict anti-cheat checks, immutable provenance, and
contemporaneous comparisons at equal budgets.

### 9. Installation and configuration

Hardy is only useful when Lean, LaTeX, and a model are all reachable, so getting
a machine into that state is part of the design rather than an afterthought. One
installer per operating system — Linux, macOS, and Windows, with WSL never
required — installs whatever is missing and skips whatever is present: Python,
`lake` through elan, a shared Lake project carrying Mathlib, a TeX distribution,
and Hardy itself. Platform-specific work is confined to package installation;
the environment, Lean project, config file, and verification step are shared.

Installing Hardy means putting a released wheel into a virtual environment, not
obtaining a copy of the repository. A tagged release publishes the wheel beside
a `SHA256SUMS` manifest and a bundle of the installer scripts themselves, so a
machine holding nothing but one downloaded script fetches the rest of the
installer, hands over to it, and it fetches the wheel — refusing anything whose
digest the release does not vouch for, installers included, since those are code
about to run as the user. The scripts and the wheel come from the same release,
which is why the bundle exists at all: installers taken from `main` against a
wheel from a release are two versions that need not agree, and the same reason
makes the updater replace the retained scripts alongside the wheel. Published
assets are never rewritten, so a version number is a complete answer to what is
installed. A clone is still the developer's path — run from one, the installers
install that tree editable — and remains the fallback for a fork or a branch,
which have no release to download. Naming a release and failing to get it is an
error rather than an invitation to install a branch instead.

Three installers tested only by hand are three installers that are usually
broken, and the Windows one shipped once without ever having been executed. Each
therefore runs end to end on a real runner of its own operating system on every
pull request, starting from a single downloaded script and a release built from
the commit under test. Mathlib and TeX are skipped there — gigabytes and tens of
minutes, more than a runner has — so what CI establishes is that the installer
reaches a `hardy` that runs, not that it reaches a `hardy` that can prove
anything.

Settings resolve from a TOML config file, then `HARDY_*` environment variables,
then command-line flags. `lean_project` is what makes `hardy` runnable from any
directory: Lean elaborates in that Lake project, so imports resolve the same way
wherever the conversation starts. `hardy doctor` reports each prerequisite
separately, distinguishing a missing tool from a broken one, and checks that the
Claude Code CLI is not merely installed but actually signed in.

### 9. Terminal interface

The interactive session's one new runtime dependency is `prompt_toolkit`: a
real terminal input layer, needed for ghost-text command completion, a
`/model` selector, and Esc-to-cancel without blocking the input box on
a synchronous `input()` call while a turn is in flight. Esc stops the model and
interrupts the children the turn started -- excepting the two an export runs
inside a session of its own, which keep only their own limits; a second press escalates from
interrupt to kill, because an interrupt is a request and a child that ignores
it would otherwise leave the user with nothing further to press. It is confined to two
modules (`hardy/tui/select.py` and `hardy/tui/shell.py`); everything else in
`hardy/tui` speaks only the plain `Ui` port, so the line-based fallback
(`--plain`, `HARDY_PLAIN`, a non-TTY, or a terminal session that fails to
start) needs none of it.

## Trust boundary and safety

The Lean kernel is the authority for formal proof, subject to an audited axiom
set. Independent faithfulness checks protect the translation from an informal
claim to Lean. Assumed-paper axioms widen the trust base and must be visible.

The audit runs inside the environment it audits: `#print axioms` is elaborated
by a Lean environment the submitted source has already had the chance to extend,
and a source that registers its own elaborator for that syntax can answer the
audit itself. Moving the audit to a second invocation does not close this — the
audited module would still have to be imported, and its elaborator extensions
come with it. The audit therefore establishes that an artifact is not
*accidentally* unsound; it is not a defence against a source written to subvert
elaboration, and cannot be one while Lean runs unconfined. Closing it belongs
with the process isolation deferred below.

The reset intentionally removes the old container sandbox. This is a sequencing
choice, not a claim that generated Lean, TeX, or computer algebra cells are
safe. Every CAS language can leave a sandbox that does not exist — `os.system`
in Python, `run` in Macaulay2, `system("sh", …)` in Singular — and scanning cell
source for those would be trivially bypassable while implying a safety Hardy
does not provide. The warning is stated on every surface that can execute a
cell instead: the chat banner, the staged run's typed acknowledgement, the MCP
tool descriptions, and the header of every exported script. During the
experimental phase, run only trusted output in disposable local environments.
Before accepting
untrusted inputs, multi-user execution, or autonomous network access, restore
process isolation, filesystem and network confinement, resource quotas, timeouts,
and hostile-input tests.

## Build order

1. **Interactive vertical slice (implemented):** one conversational model, direct
   Lean and LaTeX invocation, linked names, explicitly approved assumptions,
   durable transcript, and saved artifacts. Both artifacts are multi-file trees,
   with Lean modules importing each other and a save refused whole if it would
   break a dependent; a saved `theorem` owes a writeup before another may be
   added. The older one-shot proof loop remains as a dependency smoke path. A
   real model/Mathlib acceptance run remains to be recorded.
2. **Honest experiment harness:** faithfulness and axiom checks, budgets, fixed
   evaluation inputs, reproducible identities, and useful failure reports.
3. **Broaden capability:** literature, critique/repair, alternative runtimes and
   strategies, retrieval, and memory—only as experiments require them. Premise
   retrieval's first half arrived ahead of this step: ranking a goal across
   Lean's own search and Loogle is implemented, metered, and carries its
   provenance, while the versioned embedding index and the service that would
   serve it remain here.
4. **Harden:** restore isolation and quotas before the system handles untrusted
   content or is offered as a service.

The complete feature inventory is in [FEATURES.md](FEATURES.md), and the visual
map is [ARCHITECTURE.html](ARCHITECTURE.html).
