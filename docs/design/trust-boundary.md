# Trust boundary

This page states what Hardy controls, what it does not, and why, for a reader
deciding how much of a Hardy run's report to believe. The shape of the system
is [the architecture overview](overview.md); which package owns which decision
is [module boundaries](module-boundaries.md).

Hardy controls two things: what the model may call, and what the record may
claim. It does not control what the code it executes may do. Almost every
misreading of Hardy's claims comes from mistaking the first two for the third,
so the sections below keep them apart deliberately.

## What Hardy controls and what it does not

Controlled:

- **Hardy runs its own tools.** Every Lean check, LaTeX compile, computer
  algebra cell and file write is performed by Hardy's code. The provider SDK
  decides *when* a tool runs; it never runs one. On the default Claude backend
  the tools are registered in process (`agents/claude.py`); a staged
  `--backend codex` run serves the same tools from a Hardy-owned MCP subprocess
  (`agents/codex.py`), which is a process seam on the same unconfined host and
  not a boundary.
- **On the Claude backend, anything that is not a Hardy tool is refused.** The
  permission callback in `agents/claude.py` allows a call only when its name is
  one of the tools Hardy registered, and denies everything else whatever it is
  called. Claude Code's `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`,
  `WebFetch`, `WebSearch` and the rest are additionally disallowed outright,
  but the default-deny gate is what carries the guarantee: a denylist has to
  anticipate every built-in the CLI grows next, and this one does not. The
  scoping to that backend is load-bearing. A staged `--backend codex` run has
  no such gate: its working thread is started with `sandbox=workspace_write`
  and
  `approval_mode=auto_review` (`agents/codex.py`), so that SDK's agent keeps
  its own file and shell tools, auto-approved, over the run directory. Hardy's
  tools are still the only way to reach Lean, TeX and the record, but on that
  backend they are not the only tools in the conversation.
- **No inherited configuration.** Claude Code settings and `CLAUDE.md` files
  are not read. An interactive session reads exactly one project file,
  `AGENTS.md` at the project root or `HARDY.md` in its place, never an
  ancestor, and records the exact text shown to the model in
  `transcript.jsonl` with the whole file's SHA-256 in `session.json`. Graded
  runs read none of it. The flags and settings that govern the read are in
  [the CLI reference](../reference/cli.md) and
  [the configuration reference](../reference/configuration.md).
- **No extension surface.** Nothing can register a tool, sit on a tool result,
  or supply a summary of a session.
- **A faithfulness reader with no tools**, described below.
- **Bounded archive unpacking.** `literature/archives.py` normalises every
  member path to a relative POSIX path, refuses `..`, a leading separator, a
  drive letter, a backslash and a NUL byte, bounds path depth, refuses
  symlinks, hardlinks, devices and FIFOs rather than skipping them, and
  enforces file-count, per-file and total quotas on the *decompressed* stream
  rather than on what the headers claim. The extraction is staged beside its
  target and moved in with one rename, so a refusal leaves the staging
  directory empty rather than half populated. That is a bound on what an
  archive can do to the filesystem. It is not a sandbox: nothing there is
  executed or compiled, and the code that unpacks a hostile archive carefully
  does not make the archive's contents trustworthy.
- **Bounded child processes.** `foundation/process.py` validates a request
  before launching anything, holds a wall deadline, bounds captured stdout and
  stderr, and classifies termination so that an output overflow stays distinct
  from a timeout and never becomes a success. On Windows a tracked child is
  put in a Job Object and `TerminateJobObject` reaches its descendants, which
  is the Windows spelling of killing a process group. The residual gap is
  narrow and stated in the code: the child has been running since `Popen`
  returned, so a grandchild spawned in the microseconds before the job
  assignment escapes the job.

Not controlled:

- **Generated Lean.** Elaboration executes arbitrary code. A `macro`, an
  `elab` or an `#eval` in a submitted source runs during the check, before any
  verdict exists.
- **LaTeX.** The default compiler is `pdflatex -interaction=nonstopmode
  -halt-on-error` (`app/config.py`). Hardy does not pass `-no-shell-escape`
  and does not inspect how the local distribution configures restricted
  `\write18`, so whatever shell access a document has on this machine, a
  generated document has.
- **Computer algebra cells.** A cell is a full interpreter with the
  filesystem and the network: `os.system` in Python, `run` in Macaulay2,
  `system("sh", ...)` in Singular. Scanning cell source for those would be
  trivially bypassable while implying a safety Hardy does not provide.
- **Anything fetched.** `rank_premises` sends goal text to a Loogle endpoint
  and reads what comes back, and the literature workflow downloads
  third-party archives. Bounded unpacking limits what such an archive does to
  the filesystem; it says nothing about whether its contents are honest.
- **Helper processes.** Lean, `lake`, TeX and the computer algebra kernel are
  ordinary child processes of the account that ran `hardy`, with everything
  that account can touch. Nothing in Hardy confines them.

All of the first list is an **honesty boundary, not a security boundary**. It
governs what the model can reach through the SDK and what a run's record can
claim, so that a run is the run its record says it is. None of it confines a
process. Real confinement is a boundary the operating system enforces around
the whole of Hardy, model loop and executors together; the policy it would
have to meet, the controls it would have to enforce and the attacks each one
has to survive are in
[the confinement policy](../isolation.md).
Until that exists, run only trusted output, in an environment you are willing
to lose. [The roadmap](../roadmap.md) tracks the work.

## The kernel is the only authority

The Lean kernel is the authority for formal proof, subject to an audited axiom
set. Nothing the model says about its own work is evidence of anything, and no
other component may supply a verdict in the kernel's place. The computer
algebra session has no authority at all: `formal/verifier.py` never consults
its output, and a CAS result is a reason to try a proof rather than a reason to
believe one.

`formal/audit.py` reads `#print axioms` and grades what it finds. It is pure
functions over strings, with no subprocess, no filesystem and no model, and it
fails closed everywhere: a report that is missing, duplicated or unreadable is
a rejection, because the next thing the caller does is grade an artifact, and
silence must never read as "depends on nothing". `sorryAx` is in `FORBIDDEN`
and no human may approve it. Three axioms are standard for ordinary Mathlib
proofs (`propext`, `Classical.choice`, `Quot.sound`) and their presence is not
news; anything else is either an approved assumption or an unapproved one that
refuses the save.

The audited scope is every non-private theorem and lemma in the rebuilt
modules, not only the names the model registered, because a scope the model
chooses is a gate it can switch off: a session registering nothing would have
nothing to audit. Private declarations are skipped, since the probe elaborates
a file that imports the module and cannot name a private declaration from
there; a module with nothing auditable records "not established" rather than
refusing. An empty scope therefore means there is nothing to audit, and a save
with nothing to audit is refused rather than waved through. Treating it as a
pass would make the gate optional: a model that simply declares nothing
auditable would save `sorryAx`-dependent work after an exit-code check alone.
For the same reason the grade is derived from the audit verdict rather than
from Lean's exit code, and a kernel-verified grade with no faithfulness
verdict behind it is refused on read-back rather than believed. What each
grade means and where it is written is in
[the artifacts reference](../reference/artifacts.md).

Who may widen the trust base, and when, differs by path. In an interactive
session a human approves each axiom at the moment it is requested, with the
statement and the goal on screen. A staged `hardy prove` run has nobody to ask
during the run, so it widens the trust base only from a human-authored
`--assume` file, a declaration made before the run starts; a proof the kernel
reports as using one of those axioms is graded `verified_modulo`, and the
manifest names exactly the ones it used rather than everything the file
offered (`app/cli.py`, `workflows/prove.py`). A `hardy batch` run cannot widen
it at all: there is no declaration file and nobody to approve one, so anything
beyond the standard axioms refuses the proof rather than being recorded and
shipped (`workflows/batch.py`).

## The audit runs inside the environment it audits

The strongest claim Hardy makes has a known limit, and stating it plainly is
part of the claim being worth anything.

`#print axioms` is elaborated by a Lean environment the submitted source has
already had the chance to extend. A source that registers a command elaborator
or a macro rule for that syntax can answer the audit itself, printing one
clean-looking report instead of invoking Lean's built-in handler. The
duplicate-report check does not catch this, because in that scenario only the
replacement handler runs and only one report appears. Moving the audit to a
second invocation does not close it either: the audited module still has to be
imported, and its elaborator extensions come with it.

So the audit establishes that an artifact is not *accidentally* unsound: that
a proof reached by ordinary means does not rest on `sorryAx` or on an axiom
nobody approved. It is not a defence against a source written to subvert
elaboration, and it cannot be one while Lean runs unconfined with the
submitted source deciding what elaboration means. Closing it needs an
independent, sandboxed re-check of the exact compiled declaration and its
imported artifact closure, by something that is not the environment under
audit. Hardy does not do that. [The roadmap](../roadmap.md) is where that work
is tracked.

## What the SDK may not do

The SDK decides when to call Hardy's tools. It never runs them, it is never
handed the filesystem, and it inherits nothing from the machine it runs on.
That is the default-deny gate above, seen from the model's side.

There is no extension surface either, and this is a standing constraint rather
than an open question. The comparison is with coding agents whose headline
feature is self-extensibility: modules that run with the user's full
permissions, register tools and commands, intercept tool calls, render
interface, persist session entries and supply their own compaction summary.
For a coding agent that is a fair trade, because the worst case is bad code
and the user reads the diff. Hardy's worst case is different in kind. An
extension that can register a tool, sit on a tool result, or write a summary
can manufacture a proof: it can make the record say that `#print axioms` was
read through the one parser when it was not, that the artifact was rebuilt and
rechecked when it was not, that a theorem resting on `sorryAx` rests on
nothing.

**Extensions may observe, propose, and render. They may never verify, audit,
or write the record.**

Closed to any extension mechanism Hardy ever grows: the axiom audit path and
the `#print axioms` probe that feeds it, the final verifier and the interactive
save gate, `transcript.jsonl`, `session.json` and the run manifest, the
faithfulness reader's isolation, and every decision about whether a save or a
report is refused. The test for a proposed hook is whether its worst case is a
refusal or a fabrication. A hook shaped like "may this tool call proceed, and
if not, here is why" is admissible, because refusing is always safe: the
artifacts stay as they were and the record says what was refused. A hook
shaped like "here is the summary of what happened" is not, because fabricating
history is never safe, and a summary is history.

The corollary is for text rather than code. `AGENTS.md`, `HARDY.md`, a project
command, a prompt template, any statement of intent a later feature reads: all
of it is **input**, and it is recorded as input, rendered to the model as the
user's own words under a stated precedence that Hardy's constraints outrank.
None of it is ever evidence. No file licenses a hole, an unapproved axiom, a
statement quietly weakened to pass, or a claim of verification the kernel did
not make. The prompt-set hash a graded run records does not move when such a
file changes, because that hash names the instructions Hardy gave and a user's
file is not one of them.

## The faithfulness reader

Between an informal claim and its Lean statement there is a translation, and
the kernel has nothing to say about it. The faithfulness gate
(`workflows/faithfulness.py`) is what stands there.

The reader is independent of *context*, not merely of weights. It starts on its
own thread and receives the user's words and the frozen Lean signature alone,
without the conversation that produced the formalization or that
conversation's own account of what it did. Withholding tools is what makes
that true rather than aspirational: the reader is offered none, because the
computer algebra tools run on one shared kernel and reach the filesystem, so a
reader holding them could read the run's own artifacts.

It is asked for entailment in both directions rather than for confidence,
because a wrong translation is typically rendered at high confidence, fluent
Lean stating a slightly different claim, so a confidence threshold would miss
exactly the mismatches worth halting on. A model asked to confirm its own
translation is predisposed to find it defensible, which is what makes most
self-checks theatrical rather than load-bearing.

The gate is deliberately asymmetric. A pass can be wrong; a halt is never
expensive, because surfacing a mismatch costs one question and proving the
wrong theorem costs the whole run. So a disputed translation stops the run,
and so does a reader that could not be reached: neither is an agreement, and
there is no third outcome that proceeds quietly. The read carries its own
deadline, so "the reader never answered" becomes an answer rather than a hang.

Under `--backend codex` that isolation **cannot** be enforced, and the gate
says so rather than claiming otherwise. The Codex reader is given an empty
working directory of its own, outside the run tree, and the narrowest sandbox
that SDK offers. But that SDK's read-only sandbox permits reads anywhere and
carries no readable-root control to narrow, so a Codex reader that goes
looking can still read an absolute path into the run directory and find the
formalization and the trajectory. The empty working directory removes the
obvious route and nothing more. The runtime therefore reports what its
isolation is actually worth and the verdict records it, in
`reviewer_isolation` beside the prompt and response-schema digests. A reader of
the artifacts can see which guarantee a given verdict was produced under.

What the check cannot establish is that the reader was right. It establishes
that the translation was read by something that had no stake in it, and it
records that beside the frozen claim, so a later reader follows claim to
formalization to faithfulness verdict to proof without rerunning any of it.

## Assumptions

An assumption is an axiom a human approves, and approving one widens the trust
base of everything that imports it. The gates around
`request_assumption` exist so that the widening is visible, deliberate, and
narrower than what was asked for.

- **Search before request.** A session tracks every name passed to a completed
  declaration search and whether it resolved. A request made with no search
  since the last one is refused, when the session has a search runtime at all,
  with the instruction to look for the result before assuming it. The proposal
  shown to the human carries the names that were tried, each marked resolved
  or not.
- **The axiom is elaborated, verbatim.** The exact declaration line
  `save_lean` will later be handed is compiled against `import Mathlib`, not a
  reformatting of it and not the bare statement. If Lean does not elaborate
  it, the request is refused with Lean's own message. Full Mathlib rather than
  the workspace's imports, because a narrower import set turns "you used a
  name that does not exist" into "you used a name I did not import", which is
  a different and misleading sentence.
- **Fail-closed probes, and which path runs which.** Three exist, all in
  `workflows/admission.py`. A shape gate reads the request as Lean would. A
  provability probe runs a triviality ladder to ask whether standard automation
  already closes the statement. A vacuity elaboration asks whether the
  statement is satisfied by a trivial witness after its propositional
  hypotheses are stripped. A refutation probe asks Lean whether the *negation*
  is provable (`refutation_probe`, over `formal/refute.py`). An ordinary
  assumption request runs shape, provability and vacuity
  (`AdmissionPolicy.check_global`); a paper-sourced one runs shape,
  provability and refutation instead (`check_paper`), and an opaque constant
  is not elaborated as a proposition at all, so nothing is proved or refuted
  about it and the prompt says so. A staged run runs the same refutation over
  every axiom its `--assume` file declares, writing each verdict to the
  trajectory whether or not it refuted anything, because "we looked and found
  nothing" is the fact the grade rests on (`workflows/prove.py`). The
  provability probe has a stated limit: it is a **filter over what standard
  automation closes, not a decision procedure**, and a true statement no
  tactic in the ladder finds a witness for passes it. Its other job is to say
  *this is not an assumption, it is a lemma you have not looked up*.
- **The axiom is declared after the probes.** With it in scope above them,
  `exact?` closes every statement by citing the axiom under test, and each
  honest request is refused as "proved from itself". Lean resolves names in
  order, so the ordering is what makes the question real.
- **Probe failure is not refusal.** If the compile times out or the toolchain
  is unavailable, the request proceeds to the human and the approval prompt
  says the statement could not be checked. A machine that cannot run Lean must
  not be one on which every assumption is approved silently, nor one on which
  none can be.
- **The goal is on screen at approval.** The prompt prints the run's goal
  beside the statement, and prints its absence rather than hiding it, because
  a human approving an axiom with the assignment off-screen is not making the
  decision the record says they made. Hardy makes no judgment about the
  relationship between the two; the claim is only that the human can see both.
- **Decline is the default.** Every non-approval path in
  `app/terminal.py` returns false, including an unexpected exception from the
  prompting path itself and a prompt that could not be shown at all. A bug in
  the presentation must not be able to fail this gate open.

Two limits are worth naming. An axiom that arrives through an **imported file**
Hardy did not write cannot be approved: approval matches on a normalised Lean
statement Hardy reconstructed from its own source, and there is nothing to
reconstruct for a foreign file. Such an axiom simply blocks the save, which is
the correct failure, and a `sorry` in a shared file makes every dependent
report `sorryAx`, which no human may approve either. And **who approved an
assumption is not recorded**: the durable record carries a status and no
identity, so a versioned record cannot attribute a trust decision to a person.
Capturing it means deciding what Hardy knows about its user and changing the
record's schema. [The roadmap](../roadmap.md) carries it.

## Recorded acceptance is not proof

The project ledger stores what the mathematical work amounts to: items, scopes,
versions, obligations, citations, acceptances. It points at formal, literature
and run evidence. It does not copy that evidence and it cannot manufacture it.

So a stored status never authenticates itself. Acceptance is denied unless an
injected capability reader authenticates the exact evidence and the exact
decision, on every use, including after a restart. The terminal has no
configured reader today and says so in its own summary rather than implying
otherwise: the project section prints that evidence authentication is
unavailable and that recorded acceptance is not proof. Rendering cannot grant
authority; a view that reads the ledger is a view.

Three consequences follow, and each has bitten:

- **Rechecks of reverse dependencies are observations.** A repaired obligation
  is accepted only on independently authenticated evidence, not because a
  dependent rebuilt.
- **An external receipt can disappear without a ledger revision.** Direct and
  recursive receipts are authenticated at final reporting rather than trusted
  from an earlier read, because the file behind one can be gone.
- **Adapters never turn a recorded acceptance field into proof.** A structural
  adapter that ran no adversarial provider gets no adversarial-review credit;
  an earlier citation check does not lend its acceptance to a new manuscript
  use; a compiled draft does not certify its mathematics.

The layout of what is stored where is in
[the on-disk layout reference](../reference/on-disk-layout.md).

## Identity and spend

A run records its identity so that two runs can be compared honestly. What it
records is narrower than it looks, and the gap is stated in the record rather
than papered over.

Recorded: the model alias, the backend and endpoint with credentials stripped,
the toolchain, the corpus and source digests, and local worker metadata
including the interpreter version, the platform and the SHA-256 of the
launching executable. Not established: the remote model revision, the
installed provider SDK identity, and the full local runtime closure. Those
fields say `not established` rather than being omitted
(`workflows/batch_recording.py`). On Windows the digested `sys.executable` may
name a venv launcher rather than the Python DLL and standard library, so even
that byte digest is weaker than it reads. A model alias and a launcher digest
do not close any of it.

Spend is an estimate. The `provider_budget` setting names a spend-policy file
([configuration](../reference/configuration.md)), and the budget owner reserves
expected spend before a provider call and admits or denies the call against the
reservation, with exact reported token counts settling it afterwards; missing
usage reports and
interrupted calls retain their liability rather than being refunded, and an
actual overrun denies later calls (`agents/spend_budget.py`). A quote estimates
input tokens and reserves the actual output cap, so it **cannot** guarantee a
hard total-token ceiling. An optional tariff derives a cost from the counts; it
does not establish the provider's invoice. The budget path is supported on the
backend where Hardy owns the loop, and refused where it does not.

Actor labels are declarations. The adjudication journal appends attributed
review records beside frozen batch artifacts, under an expected-prior-head
check so that two concurrent writers cannot both append against one head, and
a changed artifact marks earlier entries stale while retaining history. The
actor, problem and repeat labels in those records are supplied by the caller
and are not authenticated identities (`evals/adjudication.py`). Decisions there
never replace canonical verdicts or kernel evidence.

## Errors that must stay distinct

A refusal that is reported as the wrong kind of failure is a lie about the
system's state, and each of these collapses has happened.

- **A filesystem refusal is not a malformed record.** When a running session's
  transcript is replaced by a symlink, the write guard refuses the path and
  raises `LayoutError` (`foundation/files.py`). History refresh once caught
  that as a generic `ValueError` and reclassified it as `SchemaError`
  (`workflows/interactive/record.py`), which says the record is corrupt when
  the record is fine and the path is hostile. Refusal and corruption are
  separate public errors and are kept separate.
- **A missing store is not an empty store.** A source absent from a frozen
  index is recorded with that status rather than as a source that holds
  nothing (`workflows/retrieval.py`), and a missing discovery stays a receipt.
  "Nothing was found" and "nowhere was looked" are different answers.
- **An output overflow is not a timeout, and neither is a success.** The
  shared process owner bounds captured output, detects a one-byte overflow
  without waiting for the deadline, and keeps overflow distinct from timeout;
  a child that exits zero after overflowing does not become a success
  (`foundation/process.py`). Probes refuse truncated answers rather than
  reading them, and TeX diagnostics disclose the overflow.
- **A refused audit is not a clean one.** Unparseable output, a missing report
  for an audited declaration and an audit that could not be established all
  reject, rather than defaulting to clean.

## Compaction

When a long session outgrows the context window, something decides what
survives about which lemmas were proved, which axioms are standing, and which
attempts failed and why. That is a record-integrity question, not a
convenience one.

On a backend whose SDK owns the turn loop, the provider decides it, invisibly,
and `transcript.jsonl` does not record **what was dropped**. Hardy neither
chooses the compaction nor writes it down, which is the strongest reason to
want the loop back.

On the backend where Hardy owns the loop, the summary is Hardy's and almost
none of it is narration. The goal, the standing assumptions and the naming
registry come from `session.json`; what is proved and what is still open come
from the stored audit verdicts; the modules come from the Lean tree; and even
"what was tried and why it failed" comes from the tool results the transcript
already holds, in Lean's own words. A summary derived from the files it
describes is **checkable** against them, which is what no coding agent's
summary can offer. `/status --full` prints literally the same summary, through
the same assembler, so what the model was told and what a human can look at are
one document rather than two ([session commands](../reference/session-commands.md)).

Two of its rules are load-bearing rather than tidy. The cut is walked backwards
until it lands on a message a conversation may legally resume from, because a
tool result separated from the call it answers is not a smaller context but an
invalid one. And the compaction is written into `transcript.jsonl`, saying what
was summarised, where the kept messages start, what the summary said, and the
window it was planned against, because a compaction that leaves no trace is
exactly the invisible loss this exists to prevent.

Two headings exist for a specific reason, and it is the mechanism by which a
long session degrades: standing assumptions and the naming registry are
established early, so they are the oldest thing in the window and the first a
naive compaction drops, and they are exactly what a later turn must not
contradict. Deriving them beats remembering them.

The summary is backend-independent; only the moment to insert it is not. On the
SDK backends there is no such moment, so no compactor is offered there rather
than one they would silently drop. The SDK's `PreCompact` hook exposes the
trigger and the provider transcript path, which may be enough to persist the
summary and mark the boundary. What that hook **cannot** do is decide what the
surviving context contains, so it would record the loss rather than end it.
[The roadmap](../roadmap.md) tracks the rest.
