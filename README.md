# Hardy

Hardy is an experimental, model-agnostic harness for theorem proving in Lean 4.
It puts a language model in a tight loop with the Lean kernel, giving the model
useful proof tools while keeping verification and honest reporting under the
harness's control.

The name recalls G. H. Hardy's response to Ramanujan: recognize the insight, then
demand the proof. Hardy aims to turn a model's mathematical ideas into artifacts
that people and machines can inspect.

## Status

Hardy is in active development and is not finished. The proof loop, the Lean
integration, the axiom audit, the writeup binding, and the report gate are all
built, and the acceptance suite is written to exercise them end to end.

What does not exist yet is evidence about how well it does the thing it is for.
There are no benchmark results here, and no measured rate at which an approved
formalization turns out to mean something other than the informal claim it came
from. That measurement is the point of the next phase, and until it exists every
claim in this document is about mechanism rather than performance.

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
hash, a proof is sought against that frozen statement, and an independent
verifier rebuilds and rechecks the result before anything is graded. `hardy
accept` runs the checked-in acceptance problems and cross-checks the artifacts
they produce; with `--force-budget-exhaustion-test` it exercises the whole
pipeline with no model, no network, and no toolchain. The earlier one-shot proof
experiment remains available as `hardy batch`, but is secondary.

While it proves, the model can ask `rank_premises` which declarations are worth
looking at for a goal, fusing Lean's own `#find` with Loogle. Retrieval spends a
metered budget, and a ranking names every source it asked, what that source
searched, and whether the order can be replayed at all — Lean's search runs in
the environment the run is frozen under, while the public Loogle tracks a Mathlib
it does not name. A ranking is a heuristic, never evidence: only the kernel
verifies anything.

All three surfaces read `#print axioms` through the same parser, so a proof
standing on `sorryAx` or on an axiom nobody approved is reported as such rather
than as a theorem — including one reached through an import, which nothing in the
source itself declares. Elaborating is not the same as being verified, and a
report Hardy cannot read is a refusal rather than a pass. `prove` and `batch` run
unattended and so refuse anything beyond Lean's own three axioms; a saved
interactive artifact can rest on an assumption a human approved, and says which.

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
the loop, and Hardy cannot make those decisions while it does not run one.

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

**Every `theorem` owes a writeup, and the writeup owes its reader the Lean.** A
saved theorem is carried by the document only when three things hold at once:
`record_name` maps it to a LaTeX name, the compiler really created that `\label`,
and the document quotes the theorem's **exact Lean statement** — the declaration
head from `theorem` through to the `:=` — verbatim, where TeX cannot mangle it.
Whitespace and Lean comments may differ; the proposition may not, and a quotation
that runs on into a *longer* statement does not count. Only the document the root
actually `\input`s counts, since a fragment nothing pulls in is in front of
nobody. Until every saved theorem is carried, saving a file that introduces a new
one is refused. `lemma`, `def`, and `instance` are exempt, so scaffolding stays
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
human-readable half out.

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
computer algebra cells whenever one is.

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
kernel a model tool call may already be using, `/status` shows the
workspace/model/paths and the full spend breakdown — turns, cost, and input,
output, cache-write and cache-read tokens — `/doctor` checks Lean, LaTeX,
computer algebra, and the model, `/clear` clears the screen (nothing on disk is
touched), and `/exit` (or `/quit`, or Ctrl+D) leaves.

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

Use `hardy chat --root path --project slug` to open a particular problem; either
flag alone still narrows the choice, and both default to the current directory
and to whichever problem is already there. A staged run is `hardy prove "every
prime above two is odd"`, and its artifacts — request, frozen claim, trajectory,
Lean source, verification, paper, and manifest — are written under `runs_root`.
The retained batch check is `hardy batch examples/true.json --output
hardy-output`. `hardy setup` finds and records the pinned toolchain, verifying
the Tectonic download against its recorded digest before installing it. Global
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
- [AGENTS.md](AGENTS.md) gives Codex and other coding agents the repository's
  startup context.

Keep these descriptions consistent. When the direction changes, update all
documents whose claims are affected in the same change.

## License

Apache-2.0

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
