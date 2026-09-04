# Hardy

Hardy is an experimental, model-agnostic harness for theorem proving in Lean 4.
It puts a language model in a tight loop with the Lean kernel, giving the model
useful proof tools while keeping verification and honest reporting under the
harness's control.

The name recalls G. H. Hardy's response to Ramanujan: recognize the insight, then
demand the proof. Hardy aims to turn a model's mathematical ideas into artifacts
that people and machines can inspect.

## Status

Hardy is in active development. The proof loop has been exercised against a real
Mathlib installation and proves results at roughly the level of graduate
coursework without difficulty.

What does not exist yet is a controlled measurement of what the harness itself
contributes. One is in progress: a fixed weak model, run with Hardy against the
same model run without it, on a result that is long and bookkeeping-heavy rather
than deep — that there is no nonabelian simple group of order less than 60. The
question is not only whether the harnessed run succeeds more often, but whether
the unharnessed one produces proofs that *appear* complete while resting on
`sorry`, on an axiom nobody approved, or on a statement that drifted from the
informal claim. Until that experiment reports, everything below describes
mechanism rather than performance, and there are no benchmark numbers here.

The issue tracker is a working backlog rather than a defect list. An open issue
here usually records a design decision made and not yet acted on, and issue
numbers are referenced from the code and the docs where that reasoning matters —
see #23 for an example.

A tool that refuses to let a model claim more than its artifacts support should
hold itself to the same standard.

## What this cannot establish

The audit is elaborated by an environment the audited source could have extended.
Hardy reports what Lean's kernel says a theorem depends on, but that report is
produced inside a system the theorem's own source could have modified. Closing
the gap needs an independent, sandboxed re-check that Hardy does not yet do.
[DESIGN.md](DESIGN.md) carries the full argument.

## Interactive mathematics workspace

This repository restarted from a documentation-only reset. It now contains the
first interactive experimental implementation, without restoring the previous
prototype's sandbox, framework layers, or worker pool. Running `hardy` starts a
durable terminal conversation in which an agent can explore with the user, check
formal work in Lean, and maintain a linked LaTeX writeup.

The first experiment should prove one small theorem end to end with:

1. a model-driven proof loop;
2. direct Lean feedback;
3. a kernel-checked `.lean` artifact; and
4. a human-readable writeup whose verification limits are explicit.

The primary slice gives one model bounded tools to check and save Lean, compile
and save LaTeX, inspect the workspace, maintain a formal-to-LaTeX naming
registry, and request explicit permission for assumptions with provenance. It
saves the conversation and artifacts after every change.

An assumption is elaborated before anyone is asked to approve it, and refused if
Lean can prove it outright — a statement the kernel closes is a theorem nobody
saved yet, not an assumption. `/goal` records what the session is for and prints
it beside every such request, so nobody approves an axiom with the assignment
off-screen. The same tactic ladder is run against every theorem a save
introduces — there, one tactic closing the statement is disclosed rather than
refused, because a lemma that falls to `simp` is still a lemma while a vacuous
statement under a grand name must not pass silently. And every compile stamps
page one of the writeup with how many theorems Lean checked, how many
assumptions were approved, how many of the document's own theorem environments
are backed by neither, and which saved statements a single automation call
closes outright.

A persistent computer algebra session sits alongside them, so the question of
what is worth proving can be answered by computing rather than by guessing.
State carries between cells, `/cas` lets you drive the same kernel yourself, and
`cas_export` writes a script and a notebook, replays every cell in a fresh kernel
to check the cells reproduce, and then runs the script it just wrote to check the
file itself does. SymPy is the default because it is a Python dependency and
works everywhere; Singular and Macaulay2 are far stronger for algebraic geometry
and are used when configured. No computation is evidence — only Lean's kernel
verifies anything, and the verifier never reads a CAS result.

Alongside it, `hardy prove` stages a single claim explicitly: Hardy proposes a
formalization, you approve or revise it, the approved statement is frozen under a
hash, an independent reader checks that the frozen Lean says what you said before
any proof search starts, a proof is sought against that frozen statement, and an
independent verifier rebuilds and rechecks the result before anything is graded.

The same workflow is reachable from inside a running session as `/prove
<claim>`, on that session's live model and through its own terminal — a selector
for the approval, Esc to walk away — rather than as a separate program with its
own blocking prompts. Exploration and staged proving are the same activity at
different levels of commitment, and leaving the session to change level dropped
the conversation that motivated the claim exactly when it became useful. Nothing
about the workflow is relaxed by being reached this way: it is the same code,
the same frozen claim, the same independent faithfulness read, and the same
typed acknowledgement that generated code runs without isolation. A staged run
writes its own run directory and leaves your workspace and your conversation
untouched.

Esc reaches the run itself, not only the subprocesses under it: the provider
call in flight is stopped, no further stage begins, and the run finalizes as a
cancellation rather than as a runtime failure — so an abandoned `/prove` is not
billed for stages nobody waited for, and its manifest says why it stopped. That
holds for a press during the slow toolchain identification before the run has
even started, as well as during it. A Lean or Tectonic process already running
is asked to stop rather than killed, and Hardy waits for the call to come back
before finalizing, so the manifest describes the directory it names; a second
Esc kills what did not take the hint. That includes the run's own computer
algebra kernel, which is persistent and so is not in the register the signal
walks -- it is asked, and escalated, through its own session.

That faithfulness check is the one gate a green kernel cannot stand in for:
Lean's acceptance says a statement was proved and nothing about whether it is
the claim you made. The reader is asked from a conversation of its own, with no
tools at all, and is given your words and the Lean signature alone — not the
exchange that wrote the formalization, and not its own account of what it chose,
because a model handed the reasoning behind a translation reads the translation
through it. Denying it tools is the part that makes that real: the computer
algebra kernel is shared and unsandboxed, so a reader holding those tools could
simply read the run's own files. It is asked whether each statement entails the
other rather than how confident it is, since a wrong formalization is usually
fluent and confident.

That no-tools guarantee holds on the default Claude backend, where the runtime
refuses `Read`, `Bash`, `Glob` and `Grep` outright and refuses anything else by
default. It does not hold under `--backend codex`: that SDK's read-only sandbox
permits file reads anywhere and offers no way to narrow them, so its reader
could reach the run's own artifacts by absolute path. Hardy does not claim
otherwise — each runtime reports what its isolation is worth and every verdict
records it, so a run whose independence was never established says so rather
than reading like one where it was. Closing that needs the process confinement
the design defers.

A disagreement stops the run and shows you the mismatch, and so does a reader
that cannot be reached: a halt costs one question and a proof of the wrong
theorem costs the whole run. The verdict is written to `faithfulness.json`,
recorded in the trajectory beside the frozen claim, and carried in the manifest,
so a later reader can see the translation was checked and by what.

Set `faithfulness_model`, or pass `--faithfulness-model` for one run, to have a
different model do the reading. The flag is what a mixed setup needs: the
setting is global and the backends do not share model names, so a configured
Claude reviewer cannot serve a `--backend codex` run.

`hardy accept` runs the checked-in acceptance problems and cross-checks the artifacts
they produce; with `--force-budget-exhaustion-test` it exercises the whole
pipeline with no model, no network, and no toolchain. The earlier one-shot proof
experiment remains available as `hardy batch`, but is secondary.

`hardy evals` runs a tiered, classified corpus (`corpus/`, twenty statements so
far, five of them false "twins") rather than the three-problem acceptance set.
The corpus is a standalone CC-BY-4.0 dataset: entries are sharded by MSC2020
2-digit class under `corpus/problems/<NN>.json`, hold statements only — nothing
measured, nothing derived — and a release is gated by a manifest digest the
changelog head binds. `hardy evals corpus check` reports every mechanical
objection to it, `hardy evals corpus report` gives coverage by field, and
`hardy evals corpus serve` opens a local page that renders each entry —
statement, Lean, classification — re-read from disk on every refresh. Then: `hardy evals baseline` sweeps a committed tactic set to measure, per
statement, how much a plain tactic already closes before any model is asked;
`hardy evals run --label L --acknowledge-unsafe-execution` scores a model
against that floor and writes a scoreboard under `evals/scoreboards/<label>/`;
`hardy evals check <scoreboard-dir>` re-derives every figure in a committed
scoreboard from its run directories, the way `hardy accept --recorded` does
for the acceptance runs. See "Evaluation set (evals/)" in `FEATURES.md` for
the tiering rule, the outcome table, and what the aggregates do and do not
report.

While it proves, the model can ask `rank_premises` which declarations are worth
looking at for a goal, fusing a declaration-name index read from the pinned
Mathlib sources with Loogle. Retrieval spends a metered budget, and a ranking
names every source it asked, what that source searched, and whether the order
can be replayed at all — the index reads the sources the run is frozen under,
while the public Loogle tracks a Mathlib it does not name. A ranking is a
heuristic, never evidence: only the kernel verifies anything.

Three searches answer without a ranking. `inspect_declarations` asks Lean
whether names exist and hands back their real signatures — a batch that
finishes settles those spellings, and one that resolves nothing says so rather
than implying the result is absent; `search_declarations` matches declaration
names in that same source index, instantly and offline, so `simple group`
finds `IsSimpleGroup` — a hit there is a lead to confirm, and a miss is about
the index, never Lean's word; `search_modules` says which module to `import`
for a name, read from the package index Lake already wrote, so it answers even
on a machine where Lean will not start. A search that does not finish is
refused rather than returned empty — an empty answer from a search that never
ran reads as "Mathlib does not have it", and a session that believed that went
on to assume four theorems Mathlib proves.

Lean's own `#find` used to back declaration search, and was dropped on a
measurement rather than a guess: on the toolchain pinned here it still had not
answered at 300 seconds — ten times the process budget — while `exact?`
finished in 22 in the same environment, so Lean was healthy and `#find`
specifically was never going to answer inside a fresh process. The measurement
is recorded in `hardy/declarations.py` next to the index that replaced it.

All three surfaces read `#print axioms` through the same parser, so a proof
standing on `sorryAx` or on an axiom nobody approved is reported as such rather
than as a theorem — including one reached through an import, which nothing in the
source itself declares. Elaborating is not the same as being verified, and a
report Hardy cannot read is a refusal rather than a pass. `prove` and `batch` run
unattended and so refuse anything beyond Lean's own three axioms; a saved
interactive artifact can rest on an assumption a human approved, and says which.

An interactive workspace may also hold an unfinished proof. A single theorem can
take thousands of lines and many turns, and a file with a `sorry` still in it can
be saved, imported, and built on — which is how the skeleton survives between
turns instead of living in a context window and being re-sent whole on every
check. The kernel is what keeps that honest: `#print axioms` reports `sorryAx`
through imports, so a theorem resting on a hole anywhere beneath it is named as
open after every save, in `/status`, on the user's screen at the end of each
turn, and in the document's own banner. A report that names one is graded
*partial*, and must still quote its statement where a reader can see which half
of the work was done.

For the same reason `theorem` is reserved. A save may not introduce one whose
name `record_name` has not already mapped to a place in the document, so
scaffolding is stated as a `lemma` — which owes no writeup and is free to save —
rather than as a claim the writeup ratchet will then demand a paragraph for.

## Models and authentication

Hardy talks to Claude through the Claude Code agent SDK, so it runs on your
**Claude Max subscription**. There is no API key to configure and no endpoint to
point at: the credentials belong to the signed-in CLI.

```sh
pip install claude-agent-sdk
npm install -g @anthropic-ai/claude-code
claude login
```

`/model` inside a session lists the catalogued Claude models, switches the live
session, records the change in the transcript, and can save the choice as your
default. The conversation carries across a switch through the provider's own
session thread, which also survives closing and reopening the workspace. A model
not in the catalog can be typed in directly — that is the escape hatch for a
release this list has not caught up with.

`hardy doctor` reports whether the SDK, the CLI, and the login are actually
usable, asking `claude auth status` rather than assuming an installed binary
means a signed-in one.

Hardy is described as model-agnostic in the sense that nothing in the harness's
design depends on a particular provider; in practice the only backend wired up
today is Claude through the agent SDK. Other model providers will be added once
the core loop has been validated.

### What the SDK does not get to do

The SDK decides *when* to call Hardy's tools. It never runs them. Every Lean
check, every LaTeX compile, and every file write happens inside this harness,
because Hardy's tools are registered as in-process SDK tools rather than handed
over to the CLI. Claude Code's own `Bash`, `Read`, `Write` and `Edit` tools are
refused — anything that is not one of Hardy's tools is denied by default, not by
a list that would have to anticipate every tool the CLI grows. Your own Claude
Code settings and `CLAUDE.md` files are not inherited either, so a run is the run
its record claims.

What replaces them is **one file, read and written down**. An interactive session
reads `AGENTS.md` from the project root — or `HARDY.md`, which replaces it
outright where a repository's `AGENTS.md` is aimed at a coding agent rather than
at mathematics. No ancestor of the root is consulted, so instructions cannot
arrive from three directories away. The full text goes into `transcript.jsonl`
the first time it is used and again whenever it changes, with its SHA-256 in
`session.json`; a digest of a file the reader does not have would prove nothing,
and the objection to inherited context was always that it was unrecorded rather
than that it was read. It reaches the model in a delimited block labelled as the
user's own project instructions, under a statement that Hardy's constraints
outrank it: no file can license a `sorry`, an unapproved axiom, a statement
quietly weakened to make it pass, or a claim of verification the kernel did not
make. What is read is bounded — 2,000 lines or 50 KB, whichever comes first,
whole lines only — and the model is told when it is looking at a fragment.
`hardy prove` and `hardy batch` never read it at all: a graded run whose
instructions came partly from a project-local file is not comparable to another
run. `--no-project-context`, `project_context = false`, or
`HARDY_PROJECT_CONTEXT=0` stop an interactive session reading it at all. That
governs what this run's system prompt carries, and not what the conversation
already remembers: reopening a workspace resumes the provider thread as it
always has, so turns produced while the file *was* being sent are still in the
conversation. The transcript marks the boundary — the full text at the turn it
was read, a `withheld` event at the turn it stopped — so the record accounts
for both sides of it. A workspace that never read one is clean outright.

What the conversation remembers has its own flag: `--fresh-thread` starts the
session on a new provider conversation while the workspace, its artifacts, the
transcript and the spend ledger continue exactly as they are — only the
resumable thread id in machine-local `.local/state.json` is discarded, and the
discard is written into `transcript.jsonl` as a change of experimental
condition, since a turn produced from an empty conversation is not comparable
to one produced from a thousand-turn one. It is a per-run act with no config
key or `HARDY_*` variable behind it — "always start fresh" would silently
discard the conversation on every launch — and it is orthogonal to
`--no-project-context`: each governs the one thing it names, and together they
are the fully clean interactive condition. On a workspace with no resumable
conversation the flag is a quiet no-op that records nothing.

### What it does get to do, and why that matters

**The SDK owns the turn loop.** Hardy no longer decides when a model call
happens, so `--max-turns` is passed to the SDK and enforced there. The wall clock
stays Hardy's, because nothing in the SDK bounds a stalled request. The
trajectory says which of the two applied rather than implying the harness did
both:

```json
"limits": {
  "max_turns": 8, "wall_seconds": 300,
  "turns_enforced_by": "provider sdk", "wall_clock_enforced_by": "hardy",
  "note": "the SDK owns the loop; see issue #23"
}
```

Issue #23 records why this is worth reversing: bounded experiments, trajectory
fidelity, cheap Lean closers before model tokens, and token budgets all live in
the loop, and Hardy cannot make those decisions while it does not run one. So
does compaction, and that reason is stronger than the four: Hardy has no
compaction of its own, so when a long session outgrows the context window the
provider decides, invisibly, what survives about which lemmas were proved,
which axioms are standing, and which attempts failed — and `transcript.jsonl`
does not record what was dropped. Most of a useful mathematical summary is
already in `session.json` and `read_workspace`, mechanical and therefore
checkable in a way no coding agent's summary can be; only "what was tried and
why it failed" needs the model. `DESIGN.md` records the full argument,
including how much of it the SDK's own `PreCompact` hook could recover
without reclaiming the loop.

## Install

One command takes a clean machine — no Python, no Lean, no LaTeX, and no clone —
to a working `hardy`. Each installer installs what is missing and skips what is
not: Python 3.11+, `lake` (via elan), a shared Mathlib project, `pdflatex`, and
Hardy itself.

```sh
curl -fsSL https://raw.githubusercontent.com/charlesmsiegel/hardy/main/scripts/install.sh | sh
hardy
```

That fetches the installers from Hardy's latest release, downloads the released
wheel, checks it against the release's own manifest, and installs it. From a
clone, `scripts/install.sh` instead installs the clone itself, editable, which is
what working on Hardy wants.

On Windows, run `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`,
with or without a clone. WSL is not required. Every installer is exercised on a
real runner of its own operating system in CI. Expect the Mathlib step to
download several gigabytes and take 10–30 minutes; `--skip-mathlib` omits it if
you have your own Lake project.

The installer asks for a model identity and stores it in `~/.hardy/config.toml`
on every platform. It also installs the Claude Code CLI when npm is available.
There is no key to supply; sign in once with `claude login`. Every setting can be
overridden by a `HARDY_*` environment variable or a flag, so an unattended
install is:

```sh
HARDY_MODEL=claude-opus-5 scripts/install.sh --yes
```

`hardy doctor` reports whether Lean, LaTeX, and the model are usable, and `hardy
doctor --deep` additionally compiles a Mathlib probe file.
[docs/INSTALL.md](docs/INSTALL.md) documents every option, path, and failure mode.

`hardy latency` answers a narrower question: how much of a Lean call is the fixed
`import Mathlib` prelude that a warm process pool would pay only once per worker
(`--workers`, one by default). Given the call count and wall time of a run it
reports the share such a pool would recover, which is the evidence the deferred
worker pool is waiting on rather than a reason to build one.

## Use

A **root** is a directory holding one or more problems. Each is its own
`<root>/<slug>/` tree — a Lean tree under `lean/`, a writeup tree under `tex/`
rooted at `writeup.tex`, the compiled `writeup.pdf`, computer algebra artifacts
under `cas/`, `session.json`, and an append-only `transcript.jsonl` — and all of
it is meant to be committed: the sources and the record of what was claimed
belong in git beside each other. Only `.build/` (recompiled oleans and LaTeX
output) and `.local/` (the spend ledger, the provider's own session id, and
terminal input history) are not; Hardy writes a `.gitignore` saying so the first
time it opens a problem. `--root` names the directory and `--project` names the
slug; with only one problem already there Hardy opens it without being asked, and
a root with none starts one called `main`.

Inside a session, `/project list` shows what the root holds and marks the active
one, `/project switch <name>` opens another, and `/project new <name>` starts
one. Two problems in one folder share no record, no transcript, no approved
assumption and no Lean namespace, and moving between them is not an exit in
disguise: a switch rebuilds what belongs to a problem — its record, its
transcript, its provider thread, its computer algebra kernel — and keeps what
belongs to the root, the pinned Lake project and the Mathlib environment behind
the search tools, so that import cost is paid once per process rather than once
per problem. Where the root is a Lake project, `/project new` offers to register
the new problem's `lean/` in `lakefile.toml`, exactly as launching with
`--project` does. The problem you switch to is recorded in
`<root>/.hardy/config.toml`, so the next launch opens it.

`<root>/.hardy/` sits beside the problems, not inside any of them: it is Hardy's
own tooling for that root, committed like the rest. A small config file there may
set which project is active — nothing else, since it travels with a clone and
Hardy runs the configured CAS command before the prompt ever appears — and it
reserves `lean/` for a Lean library shared across every problem in the root,
built to its own `.build/lean/`, which like every `.build/` here is never
committed.

Both trees inside a problem hold as many files as the work needs. A Lean file's
path is its module name — `lean/Group/Sylow.lean` is `import Group.Sylow` — so a
development can be split and its pieces can import each other. Hardy compiles
each file to an olean under the problem's own `.build/lean/` and puts that
directory on `LEAN_PATH` beside Mathlib's, which is what makes the imports
resolve; `lake env` augments that variable rather than replacing it, so no shared
`lakefile.toml` is touched. Saving a file rebuilds everything that imports it and
is refused whole if any of them breaks, so a save can never leave the problem
uncompilable. LaTeX fragments are `\input` from `writeup.tex` and are always
compiled through it — so a split writeup is built fragment first, and
`writeup.tex` is rewritten to include it afterwards, since referencing a file
that does not exist yet will not compile. Deleting a fragment `writeup.tex` still
includes is refused, as is deleting a Lean file another imports. Deleting a Lean
file that holds a registered declaration *is* allowed — an undocumented result
must always be abandonable — and the naming registry drops the mappings it
stranded, recording that in `transcript.jsonl`, since losing a formal-to-writeup
link is a change to the record of what was claimed.

A user with months of existing `.lean` and `.tex` files does not start from
nothing, and `/import` is how that pile is weeded into a project rather than
pasted into it. `/import <directory>` triages the pile without modifying it or
the project: every Lean file is elaborated — files that import each other are
built together — and sorted into compiles clean, compiles with holes, does not
compile, and not mathematics, each under a digest of the bytes read, with the
assumptions it declares that nobody approved named per file; the whole list goes
into the transcript. Promotion is one file at a time and human-directed — there
is deliberately no model tool, since pulling arbitrary host files into the
audited tree is the user's judgment call. `/import lean` routes a file through
the same save path an authored file takes — assumption approval, the shadow
build, the axiom audit — skipping only the authorship ratchet, so an imported
`theorem` lands and its writeup debt is charged through the obligations instead
of refused at the door. `/import reference` places assumed background in the
root's shared `.hardy/lean/`, compiled immediately, with the axioms and holes it
carries named out loud. `/import tex` saves through the LaTeX save path and says
plainly when nothing `\input`s the file yet. Every promotion is recorded as
having arrived from outside — kind, origin, and the sha256 of what arrived —
rather than under the authorship the record would otherwise imply, and importing
never overwrites an existing file.

**Every *closed* `theorem` owes a writeup, and the writeup owes its reader the
Lean.** A theorem still resting on a hole owes none of it yet: nothing asks for a
paragraph about a result nobody has proved, and skeletons accumulate one after
another with no LaTeX between them. What follows attaches the moment the hole
closes — or at a `report_result` naming the open theorem, which carries it on
exactly these terms so a reader can see which half of the work was done. A saved
theorem is carried by the document only when three things hold at once:
`record_name` maps it to a LaTeX name, the compiler really created that `\label`,
and the document quotes the theorem's **exact Lean statement** — the declaration
head from `theorem` through to the `:=` — verbatim, where TeX cannot mangle it.
Whitespace and Lean comments may differ; the proposition may not, and a quotation
that runs on into a *longer* statement does not count. Only the document the root
actually `\input`s counts, since a fragment nothing pulls in is in front of
nobody. Until every closed saved theorem is carried, saving a file that introduces a new
one is refused; open ones are not counted. `lemma`, `def`, and `instance` are exempt, so scaffolding stays
free — the rule is that anything reported as a result is stated as a `theorem`.
Repairing, restating, or deleting an undocumented theorem is always allowed; only
*adding* a new one is gated.

**What nobody proved goes in an appendix.** If the work rests on a user-approved
axiom — a result Mathlib does not have, stood in for by assumption — the writeup
must open an `\appendix` that states it in both languages: the mathematics under
a `\label` for its LaTeX name, and the exact `axiom Name : statement` line Lean
was given, quoted verbatim. All of it after the `\appendix`, in the document's
reading order with inclusions spliced where they occur, so a disclosure in the
body followed by an empty appendix does not count. An approval nobody used owes
nothing, and a report rests on what its own theorems were audited to depend on
rather than on everything in the workspace.

**Nothing is finished until the artifacts say so.** Claiming a result is itself a
tool call — `report_result`, naming the theorems it claims — and Hardy refuses it
unless every claimed theorem is carried by the document and every assumption is
stated in the appendix. Both halves must also be *current*: an axiom audit
expires with the build signature it was established under, and the writeup is
established only while the files on disk are the ones that compiled, so a Lean or
TeX file edited behind Hardy's back is outstanding work until it is saved again.
All of it is one list, so `/status`, the end-of-turn notice, and a refused report
never disagree about what this workspace owes. Saying it in prose instead does
not get around this: after every turn Hardy writes what the workspace still owes
on the screen under its own name, read off the two trees rather than off anything
the model said — including the case that has none to owe, where it says no
theorem is saved and what was said rests on the conversation alone. `/status`
answers the same question whenever the user asks. A model may decline to report;
it cannot report what the artifacts do not carry, and it cannot quietly leave the
human-readable half out. The document's own prose is the mirror-image limit,
recorded in `FEATURES.md`: the theorem gate reads what the writeup formally
asserts, so a claim placed in body prose or in a `lemma` environment owes it
nothing, by design — what covers those is the page-one banner, whose counts say
how much of the document Lean checked and how much was assumed, never which
claim is unbacked.

The manifest links Lean declaration names to LaTeX labels and records every
user-approved assumption, exact Lean statement, informal rendering, reason, and
source. Hardy must ask before adding an assumption; declining it does not widen
the formal trust base. It also records, per Lean module, what the audit found
that module's theorems and lemmas to actually rest on the last time a save
covered it — which `read_workspace` reports back, so the model can say what its
own tree stands on rather than remember. Imports resolve through the configured
`lean_project`, which the installer points at the shared Mathlib project; set it
to your own Lake project — in the config file or with `--lean-project` — to
import your own Lean modules. Without it, Lean runs in the current directory as
before.

A problem's computer algebra artifacts live under its own `cas/`: an append-only
`cells.jsonl` recording every cell and who ran it, and, once exported,
`session.py` (or `.sing`/`.m2`), `session.ipynb`, and an `export.json` naming
both files by digest, recording which cells reproduced, and carrying a
`script_verdict` for running the exported script as a whole. In a session,
`/cas <source>` runs one cell, a bare `/cas` opens a block ended by `/end`, and
`/cas state`, `/cas reset`, and `/cas export` do the obvious things. Select a
backend with `cas_backend` (`sympy`, `singular`, or `macaulay2`) and point
`cas_command` at the executable when it is not on `PATH`; `hardy doctor` starts
the kernel and reports its version, treating a named non-default backend as
required and the built-in SymPy as advisory. Cells are executed without
isolation, like Lean and LaTeX — the session's opening banner says which backend
is live (or why none is) and extends the unsandboxed-execution warning to name
computer algebra cells whenever one is. What to do about that warning —
containing the whole of Hardy in a container or VM until isolation is restored
— is [docs/security.md](docs/security.md).

### The interactive session

Running `hardy` with no subcommand (or `hardy chat`) opens a real terminal
session when stdin and stdout are both a TTY: a short rule naming the current
model and what the session has spent, a bare `> ` prompt, and a one-line hint
underneath — not a bordered input box. An earlier design called for one; resizing
a non-full-screen `prompt_toolkit` application above a bordered frame turned out
to corrupt the screen on a narrowing resize, so every row of chrome here stays at
or under 38 columns instead, which is what keeps a resize safe.

The spend meter on that rule (`── claude-opus-5 ── $1.34 · 82k ───`) is the
running cost and token count of the session so far, taken from the provider's own
report after each exchange and accumulated in `.local/state.json`, so reopening a
workspace continues the total rather than restarting it. Every figure is
differenced rather than summed: the CLI restores a resumed session's running
totals before each exchange — the cost and the per-model token counts alike — and
reports them afterwards, so adding those figures up would overcount
triangularly. A workspace that predates the ledger recovers what it can from the
`result` events already in its `transcript.jsonl` the first time it is opened,
rather than reporting a long session as having spent nothing.

It is never estimated, and unreported is never rendered as zero. A backend that
reports no usage shows no meter, and `/status` says `not reported by this
backend` rather than `$0.00`. The same holds per field: a backend that states its
input tokens has not thereby stated that its output was zero, so only the
counters it actually reported carry numbers. Where a total covers part of a
session — recovered history has costs but no token counts — `/status` says which
exchanges it covers. Because no chrome row may grow, a terminal too narrow to
hold the meter whole drops it rather than showing part of a number; `/status`
still has it.

Slash commands: `/help` lists them, `/model` opens a selector to switch the live
model (arrow keys or a row number, Enter to choose, Esc to cancel), `/cas`
reaches the same persistent kernel described above (`/cas <source>`, a bare
`/cas` for a multi-line block ended by `/end`, `/cas state`, `/cas reset`, `/cas
export`) but is refused while a turn is running, since it is the same locked
kernel a model tool call may already be using, `/project` lists the problems in
this root and moves between them (`/project list`, `/project switch <name>`,
`/project new <name>`) and is refused in flight for the same shape of reason — a
running turn is appending to the record and the transcript of the problem it
started in — `/status` shows the
workspace/model/paths and the full spend breakdown — turns, cost, and input,
output, cache-write and cache-read tokens — and `/status --full` adds the
workspace summary described below, `/prove [claim]` stages one claim from
statement to document without leaving the session, `/export [path]` writes one
shareable HTML account of the session, `/doctor` checks Lean, LaTeX,
computer algebra, and the model, `/clear` clears the screen (nothing on disk is
touched), and `/exit` (or `/quit`, or Ctrl+D) leaves. A project may add commands
of its own; see "Your own commands" below.

Typing `/` shows a dim inline suggestion for the rest of a likely command as you
type it; Tab accepts it. Enter submits; to write a second line without
submitting, press Shift+Enter — a terminal that does not send that sequence can
end a line with a trailing `\` instead. The reply is drawn as the model writes
it, and each tool call is announced when it starts rather than when it returns,
so a long Lean check reports what it is doing instead of going quiet.

Esc cancels an in-flight turn: the model stops, no further tool call runs, and
the Lean, LaTeX, or computer algebra process it started is interrupted rather
than left running to its timeout — with one exception: the fresh kernel and the
script an export runs to check itself belong to a session built for that export,
and are bounded only by their own limits. A computer algebra cell that answers
the interrupt costs only itself: the kernel survives, and with it everything the
earlier cells put in the namespace. What an interrupted cell had already changed
before it was stopped stays changed — nothing is rolled back — which is why such
a cell is never accepted. One that does not answer within a couple of seconds is
stopped the way the timeout stopped it, and the state goes with it; a second Esc
skips that wait and kills what had not stopped. On Windows that kill reaches the
process Hardy started and not the tree beneath it — stopping a whole tree there
needs a job object Hardy does not set up. A cell you started yourself with `/cas`
is interrupted by the same press, and while one is running the box refuses a
second cell or a new question rather than interleaving them in the one kernel.
Commands that only read or leave — `/status`, `/help`, `/clear`, `/exit` — still
work while a cell runs.

What it cannot undo is work already done: a file a tool call has already written
stays written, and an interrupted child leaves behind whatever it had got to. A
reply that lands anyway is still printed, labelled as belonging to the turn you
stopped. Ctrl+C once, while a turn is running, only warns; a second Ctrl+C leaves
at once, at the cost of whatever that turn was still doing.

Without a TTY on both ends — a pipe, `TERM=dumb`, `HARDY_PLAIN=1`, or `--plain` —
Hardy runs the same commands and the same banner through a line-based session
instead, with no ghost text or selectors. If the real terminal session cannot
start at all, Hardy falls back to that line-based session automatically rather
than ending the run.

### Your own commands

A project keeps its repeatable asks beside itself, in `.hardy/prompts/`. Each
`<name>.md` there becomes `/<name>`: optional frontmatter supplies `description`
(what `/help` shows) and `argument-hint`, and the body is the message that gets
sent. `$1`…`$n` are the words typed after the command, `$@` is all of them, and
`$$` is a literal dollar so a body can carry LaTeX. Quoting groups one argument;
backslashes survive, because a mathematician's argument is full of them.

Nothing is read through a link, and nothing that is not an ordinary file is
read at all. A template's body is *sent*, so a checkout shipping
`.hardy/prompts/notes.md -> ~/.ssh/id_rsa` would otherwise turn `/notes` into a
command that mails a host file to the provider; a link to a device would hang
startup instead. Both are refused with a line saying so, and a file too large
to be prose is refused as well.

Two rules make a shared record readable. A placeholder with nothing to fill it
is a refusal rather than an empty string — a prompt that quietly lost half its
sentence still looks entirely ordinary. And **the expansion is what is recorded**:
`transcript.jsonl` holds the text that was sent, never the `/name`, so a
transcript shared with someone who does not have your `.hardy/prompts/` still
says what was asked. A file whose name would shadow a built-in command is
refused rather than allowed to redefine `/exit` or `/status`, and a template is
input rather than instruction: it is deliberately not folded into
`PROMPT_SET_SHA256`, so it can never move the hash a staged run records.

### The workspace summary

`/status --full` adds an account of the session assembled from the artifacts
rather than from anything the model remembers: the goal, the approved
assumptions with their source, stated reason and approval date, every saved
theorem under the verdict its own stored audit record gives it, what is still
open, the tool calls that were refused and what Lean said, the naming registry,
and what is outstanding. A theorem whose audit never ran, has expired, or whose
name is not unique appears under `Not established` rather than under `Proved` —
two headings would make one of them a claim. It carries no spend — that is
`/status`'s own section, for the human, and is withheld from the model on
purpose.

This is the checkable half of context compaction (#100). Hardy cannot yet decide
what leaves a long session's context, because the SDK owns the turn loop (#23);
what it can do already is derive the summary such a compaction would need from
files rather than from a narration nobody can verify.

### Exporting a session

`/export [path]` writes one self-contained HTML file — no scripts, no fonts, no
images, nothing fetched when it is opened — holding the conversation, the Lean
sources, the writeup sources, the audit verdicts, the naming registry, the
approved assumptions with their provenance, the spend, the project instructions
the model was given, the points where its memory of the conversation was reset,
what arrived from outside by import rather than being written here, the shared
Lean from `.hardy/lean` that the saved theorems actually import, and the
model and toolchain identities alongside the settings that decided what the
session could find out. With no path it lands in the problem directory under the
project's name and the time.

The point of it is that it does **not** flatten Hardy's distinctions. A
kernel-verified theorem, a theorem the kernel checked given an axiom a human
approved, and a sentence somebody typed into the conversation are three
different things and are rendered as three different things, with the axiom, its
source, the stated reason and the approval date printed beside the theorem
resting on it. The conversation appears under a heading saying plainly that
nothing in it is evidence for anything above it.

Two finer distinctions the page keeps, because getting either wrong would
overstate the work. An axiom is attributed to the declarations that actually
use it, read from each one's own axiom report — a stored verdict grades a
*module*, and its approved-axiom list is the union over everything the module
declares, so attributing that union to each theorem would say one resting on
nothing but `propext` rests on an assumption because its neighbour does. And a
verdict expires: if the toolchain, the source, or a dependency has moved since
the audit, the theorem reads "audit no longer established" rather than
"kernel-verified", the same way `/status` already reports it. A proof that is
both unfinished *and* resting on an approved axiom names both.

A third: a name that two saved modules declare is not graded at all. The
workspace permits that (nothing makes those modules import each other) and
everything downstream addresses a theorem *by name*, so the statement shown
comes from whichever module was read last while the verdict over it is drawn
from both — which could print a stale module's statement under the other's
clean audit. Both surfaces refuse to grade it and say which modules collide;
`/status` already reports the collision as work to do.

The conversation keeps its own limitations too: a reply the model was cut off
mid-sentence is labelled as an interrupted fragment rather than shown as a
finished answer, and a turn that was cancelled, abandoned, or stopped by
Hardy's wall clock says so where it ended. A tool result too long to carry
whole keeps its **end** and says it was cut — Lean and Tectonic print their
setup first and the diagnostic that failed the call last, so a page that kept
the beginning would show the imports and not the error.

Credentials matching known token shapes, and values under credential-shaped key
names, are removed before the file is written. That is a filter and not a proof,
and the page says so where a reader will see it: an export is made to leave the
machine, so read it before sharing it. The destination may be anywhere — moving
the file off the machine is the point — but it may not be a symlink: a checkout
shipping `report.html -> ~/.bashrc` would otherwise have an `/export
report.html` that looks entirely local overwrite the host file.

Use `hardy chat --root path --project slug` to open a particular problem; either
flag alone still narrows the choice, and both default to the current directory
and to whichever problem is already there. A staged run is `hardy prove "every
prime above two is odd"`, or `/prove every prime above two is odd` from inside a
session, and its artifacts — request, frozen claim, trajectory,
Lean source, verification, paper, and manifest — are written under `runs_root`.
The retained batch check is `hardy batch examples/true.json --output
hardy-output`, and `examples/sqrt-two-plus-sqrt-three.json` is the nontrivial
problem the recorded acceptance runs used. `hardy accept --recorded
acceptance/recorded/*` rechecks those committed runs — manifest against
trajectory against Lean source against document, the axiom line Lean printed
against the graded verdict, the toolchain named by revision — with no model,
network, or toolchain present. `hardy setup` finds and records the pinned
toolchain, verifying the Tectonic download against its recorded digest before
installing it. Global
options such as `--model`, `--lean-command`, and `--latex-command` go before the
subcommand. Use `uv run --extra test pytest` for the hermetic suite, which
substitutes fake model, Lean, and LaTeX processes and does not establish a real
Mathlib installation. Adding `--cov` measures what that suite reaches, writes
`coverage.xml` and `htmlcov/index.html`, and fails below the floor recorded in
`pyproject.toml`; CI runs it on every pull request and keeps the report as an
artifact. One number it reports is a measurement limit rather than a gap:
`hardy/cas_driver.py` is the body of a helper process the suite starts with
`subprocess`, so nothing in the harness observes it running.

## Documentation

- [DESIGN.md](DESIGN.md) defines the architecture, trust boundary, and design
  principles.
- [FEATURES.md](FEATURES.md) is the consolidated feature inventory and rough
  sequencing guide extracted from the former specs and plans.
- [ARCHITECTURE.html](ARCHITECTURE.html) is a self-contained visual map of the
  design and planned feature areas.
- [docs/INSTALL.md](docs/INSTALL.md) covers the per-OS installers, configuration,
  and installation troubleshooting.
- [docs/security.md](docs/security.md) states the trust boundary as it stands —
  what is controlled, what is not — and how to contain Hardy in a container or
  VM until process isolation is restored.
- [AGENTS.md](AGENTS.md) gives Codex and other coding agents the repository's
  startup context.

Keep these descriptions consistent. When the direction changes, update all
documents whose claims are affected in the same change.

## License

[Apache-2.0](LICENSE).

## Related

These share a commitment: a system should not be able to assert more than its
artifacts support.

- **[ludex-rpg](https://github.com/charlesmsiegel/ludex-rpg)** — a quote and a
  paraphrase must never be confusable, anywhere in the app.
- **[coding-skills](https://github.com/charlesmsiegel/coding-skills)** — a
  finding asserts a defect and carries a fix; a candidate reports a lead and
  carries the benign explanations. Confusing them raises.
- **[grimoire](https://github.com/charlesmsiegel/grimoire)** — the prompt a reply
  came from stays readable after everything it drew on has moved.
- **[rpg-bookbinder](https://github.com/charlesmsiegel/rpg-bookbinder)** — state
  lives in files, not in a shared prompt.
