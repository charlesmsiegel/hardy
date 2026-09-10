# Working in `hardy chat`

This guide walks through the things you do inside a running `hardy chat`
session: opening and switching problems, seeing what the model can and cannot
touch, saving Lean and LaTeX, approving assumptions, driving computer algebra
by hand, importing files you already have, citing papers, publishing a draft,
branching the conversation, and getting out. It is for someone already at the
prompt; for what each command takes and returns, see
[session commands](../reference/session-commands.md), for `hardy chat`'s own
flags see [the CLI reference](../reference/cli.md), and for the reasoning
behind any of this see
[the interactive session design](../design/interactive-session.md).

## Roots and problems

A **root** is a directory holding one or more problems, each its own `<slug>/`
tree with a Lean library, a writeup, computer algebra artifacts, and a record of
what happened. Open one with:

```sh
hardy chat --root ~/proofs --project sylow
```

Either flag alone still narrows the choice, and both default to the current
directory and to whichever problem is already active there. With only one
problem in the root, Hardy opens it without asking; a root with none starts one
called `main`. See [`hardy chat`](../reference/cli.md#hardy-chat) for the full
flag table and [on-disk layout](../reference/on-disk-layout.md) for what a
problem directory contains.

From inside a session:

```text
/project list
/project new <name>
/project switch <name>
```

`/project list` shows every problem in the root and marks the active one.
`/project new <name>` starts a fresh problem; if the root is a Lake project,
Hardy offers to register the new problem's `lean/` in `lakefile.toml`.
`/project switch <name>` opens a problem already there.

A switch is a reopen, not a restart. Two problems in one root share no record,
no transcript, no approved assumption and no Lean namespace, so switching
rebuilds everything that belongs to the problem you are leaving: its record, its
transcript, its provider conversation, and its computer algebra kernel, which
logs into that problem's own `cas/` rather than mixing two problems' cells in
one log. What survives the switch is what belongs to the root and is expensive
to rebuild: the pinned Lake project and the Mathlib environment behind the
search tools, so that cost is paid once per process rather than once per
problem. A switch is refused while a turn is running, because a running turn is
still appending to the record and transcript of the problem it started in.

The problem you switch to is written into `<root>/.hardy/config.toml`, so the
next launch of `hardy chat` in this root opens the same one without you naming
it again. `/project new <name>` refuses a name that already exists as a project
here, and separately refuses a directory that exists but is not a Hardy project
of its own, such as `src/` or a Lean library you keep beside your problems; it
will not scatter `lean/`, `tex/` and a record into a tree it did not make.

## The tools the model has, and the ones it is refused

The model's whole reach into your workspace is a fixed set of tool calls Hardy
itself runs and records. There is no general file or shell access behind any of
them:

- **Lean and LaTeX**: `check_lean`, `save_lean`, `check_latex`, `save_latex`.
- **Reading and housekeeping**: `read_workspace`, `read_file`, `delete_file`,
  `record_name`.
- **Assumptions**: `request_assumption`, `list_statements`, `assume_statement`.
- **Reporting**: `report_result`, the only way to call anything proved, done, or
  complete.
- **Search**: `inspect_declarations`, `search_declarations`, `search_modules`,
  and `rank_premises` for ranked suggestions.
- **Papers**: `search_papers`, `fetch_paper`, `fetch_source`, `read_paper`,
  `cite_paper`.
- **Computer algebra**: the `cas_*` tools, offered only when a backend actually
  started.

On the default Claude backend, anything that is not one of Hardy's own tools is
refused. Claude Code's own `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`,
`WebFetch` and `WebSearch` are disallowed outright, and a default-deny gate
refuses everything else regardless of name, so a built-in the CLI grows later is
refused too rather than slipping through. A staged run under `--backend codex`
does not carry this gate: that SDK keeps its own file and shell tools over the
run directory, auto-approved. Hardy's own tools are still the only way to reach
Lean, TeX and the record on that backend, but they are not the only tools in the
conversation. See [the trust boundary](../design/trust-boundary.md) for the full
account, including what none of this controls: generated Lean, LaTeX, and
computer algebra cells all run unsandboxed once a tool call reaches them.

You see each tool call as it starts, not only once it returns, so a Lean check
that takes a while reports what it is doing instead of going quiet for the
duration. Every call and its result is appended to `transcript.jsonl`,
refusals included, which is what `/status --full` and `/export` later read
back.

## Saving Lean and the writeup

`save_lean` checks a candidate file against the workspace, rebuilds everything
that imports it, and refuses the save whole if any of that breaks;
`check_lean` runs the same check without saving, a dry run. `save_latex` does
the equivalent for the writeup tree, where a fragment is `\input` from
`writeup.tex` and always compiled through it; `check_latex` is its dry run.
Deleting a Lean file is refused if another workspace file still imports it,
and deleting a fragment `writeup.tex` still includes is refused the same way;
a Lean file holding a registered declaration can still be deleted, since an
undocumented result must always be abandonable, and the naming registry
drops the mapping it stranded.

The writeup owes something back. Every *closed* `theorem` a save introduces has
to already be covered by the writeup, or the save is refused: the naming
registry has to map it to a LaTeX name, the compiled document has to actually
create that label, and the document has to quote the declaration's exact Lean
statement, verbatim, from `theorem` through the `:=`. The gate only blocks *new*
undocumented theorems; repairing, restating, or deleting an existing one is
always allowed, so the model is never trapped by debt it cannot pay off in the
same save. `lemma`, `def`, `instance`, `abbrev` and `example` are exempt from
all of this, which is why scaffolding is written as `lemma` rather than
`theorem`: a skeleton that is still being built up owes nothing until the model
is ready to call it a result. An open theorem, one still resting on a hole, owes
nothing yet either; the debt attaches the moment the hole closes.

The usual order is: call `record_name` to register the correspondence between a
Lean declaration and a LaTeX label before or alongside the save, so the ratchet
above has something to check the save against, then `save_lean`.
`read_workspace` reports what is currently registered and what each module's
last audit found it resting on, so the model can answer "what does my own tree
stand on" by asking rather than remembering.

Claiming a result finished is itself a tool call, `report_result`, naming the
theorems it claims, and it is refused unless every one of them is carried by the
document and every assumption the work rests on is stated in an appendix, in
both Lean and prose. A theorem still resting on a hole is graded partial rather
than refused outright, and the report has to name which theorem that is so a
reader can see which half of the work was actually done. Saying a result is
finished in ordinary prose, without calling `report_result`, does not get around
any of this: after every turn Hardy prints what the workspace still owes, read
off the two trees rather than off anything the model said.

See
[the writeup ratchet](../design/output-contract.md#the-writeup-ratchet) for
exactly what counts and why, and
[what the document must carry](../design/output-contract.md#what-the-document-must-carry)
for the page-one stamp: every compile reports how many theorems Lean checked,
how many assumptions were approved, how many of the document's own theorem
environments rest on neither, and which saved statements a single automation
call closes outright. `/status` and `/status --full` show the same figures
without opening the PDF; see "The workspace summary" below.

## Assumptions

`/goal [text]` states what the session is for, or prints the current goal with
no argument. It is shown beside every assumption request, so nobody approves an
axiom with the assignment off-screen.

```text
/assume <paper-id> [<ref> ...]
```

With no reference, `/assume` asks the model to fetch the paper's source if it
has not already, list its statements, and show them to you; it mints nothing.
Given one or more references, it asks the model to call `assume_statement` once
per named statement, through exactly the same gates a lazily minted axiom goes
through: elaboration, a counterexample search, and an independent reader
comparing the proposed Lean against the paper's own words, before you are asked
at all.

An axiom is refused outright if Lean can prove it: a statement the kernel closes
on its own is a theorem nobody saved yet, not something to assume. When you are
asked, the approval prompt shows, in order:

- the goal, as you stated it with `/goal` (or a note that none is set);
- the informal statement, in plain language;
- the exact Lean line being proposed, `axiom Name : statement`, or `opaque` for
  an assumed definition, which is more trust and is shown as such;
- the source it is attributed to;
- the stated reason for needing it;
- whether it was checked, and what;
- a previously requested and declined phrasing, if there is one, so a weakening
  between the two asks is visible rather than approved unseen;
- what has been searched for it, in Mathlib, since the last request.

You answer yes or no; declining, and pressing Esc at the prompt, are both
treated as No, and neither one widens what the session may assume. Nothing about
this is relaxed for `/prove` run from inside a session: it is the same frozen
claim, the same independent read, and the same approval prompt as `hardy prove`
on the command line.

A staged `hardy prove` run has nobody to ask mid-run, so it widens the trust
base only from a declaration made before the run starts:

```sh
hardy prove "..." --assume assumptions.json
```

where the file holds:

```json
{"assumptions": [{"name": "...", "statement": "...", "source": "..."}]}
```

Each entry is checked for an obvious counterexample before proving starts, and
a proof that actually used one is graded `verified_modulo` rather than
`kernel_verified`; the manifest names exactly the axioms `#print axioms` found
the proof resting on, not everything the file declared. See
[`hardy prove`](../reference/cli.md#hardy-prove) for the flag and
[assumptions in the trust boundary](../design/trust-boundary.md#the-kernel-is-the-only-authority)
for who may widen the trust base and when.

## Computer algebra

A persistent computer algebra kernel sits alongside the conversation, shared
between the model's own `cas_*` tool calls and your own hand:

```text
/cas <source>
/cas
/end
/cas state
/cas reset
/cas export
```

`/cas <source>` runs one cell. A bare `/cas` opens a multi-line block,
terminated by a line reading `/end`, which is the way to send something whose
indentation matters, since a one-line send would otherwise have to be stripped
and could silently change what you wrote. `/cas state` reports the backend and
version, the kernel, the segment, how much time has been spent, how much of this
process's budget is left, and the cells accepted so far; `/cas reset` starts a
clean kernel. Because it is the one locked kernel a model tool call may already
be using, `/cas` is refused while a turn or another command is running, and a
cell you send goes into the same append-only log as one the model runs, replayed
and exported the same way.

`/cas export` writes a script (`session.py`, or the `.sing`/`.m2` equivalent)
and a notebook covering the current segment's accepted cells, replays them in a
fresh kernel to check they reproduce, and then runs the script it just wrote to
check the file itself does too. The command reports how many cells verified,
diverged, failed, or came back unverified, and separately the verdict of running
the exported script as a whole. Nothing here is evidence of anything
mathematical: only Lean's kernel verifies a proof, and the verifier never reads
a computer algebra result. See [computer algebra](../design/computer-algebra.md)
for the export mechanics and why a rebuild is verified rather than assumed.

The backend is chosen once, at startup, by the `cas_backend` setting (`sympy`
by default, or `singular`, `macaulay2`); see
[configuration](../reference/configuration.md) to change it, and `/doctor` to
check it. If no backend is available, `/cas` says so.

## Importing existing files

```text
/import <directory>
/import lean <file> [dest]
/import reference <file> [dest]
/import tex <file> [dest]
```

`/import <directory>` is for a pile of `.lean` and `.tex` files you already have
before this workspace existed. It triages the pile without touching it or the
project: every Lean file is elaborated (files that import each other are built
together), and sorted into compiles clean, compiles with holes, does not
compile, and not mathematics, each with its digest and the assumptions it
declares that nobody has approved. The whole triage goes into the transcript, so
you can look it over before promoting anything.

Promotion is a separate, human-directed step, one file at a time; there is
deliberately no model tool for it, because pulling arbitrary host files into the
audited tree is your judgment call and nobody else's.

- `/import lean <file> [dest]` routes a file through the same save path an
  authored file takes, assumption approval, the shadow build, the axiom audit,
  skipping only the authorship ratchet, so an imported `theorem` lands and its
  writeup debt is charged the normal way instead of being refused at the door.
- `/import reference <file> [dest]` places assumed background into the root's
  shared `.hardy/lean/`, compiled immediately with its axioms and holes named
  out loud.
- `/import tex <file> [dest]` saves through the LaTeX path and says plainly if
  nothing `\input`s the file yet.

Every promotion is recorded as having arrived from outside, by kind, origin,
and the sha256 of what arrived, rather than under the authorship the record
would otherwise imply, and importing never overwrites a file already there.

## Papers and citations

The model can read the literature and can only cite what it read:

- `search_papers` searches arXiv and returns leads, titles, authors, abstracts;
  nothing is recorded from a search alone.
- `fetch_paper` stores one paper under the exact version arXiv reported, with a
  digest of what it holds; a paper already held is served from disk rather than
  fetched again.
- `fetch_source` downloads and unpacks that version's LaTeX bundle, treating it
  as hostile: paths normalised, symlinks and hardlinks refused, file and byte
  quotas enforced on the decompressed stream, and nothing in it executed or
  compiled.
- `read_paper` serves a bounded window of the metadata, abstract, or one source
  file at a time; a long read comes back truncated with the line to resume from.
  An abstract is a claim, not a proof, and the model is told so directly in the
  tool's own description.
- `cite_paper` is the only way anything lands in the bibliography: it takes an
  identifier and nothing else, no title, no author, no year, records the fetched
  paper in the problem's one canonical bibliography, and hands back a cite key
  to use in `\cite{...}`.

The writeup itself may not declare references; `tex/references.tex` is
regenerated whole from `bibliography.json` on every citation, so a hand edit to
it is undone by the next `cite_paper` call. Put `\input{references}` in the
writeup once, and cite by whichever key `cite_paper` returned.

Fetching is polite: one request to arXiv every three seconds, throttled
through a timestamp on disk so two Hardy processes on one machine share the
budget, with every query cached for a day. `bibliography.json` travels with
the problem and is committed; the fetched paper bytes themselves are a
machine-local cache under `.hardy/papers/`, shared by every problem in the
root and never committed, so a clone with an empty cache can still say which
bytes a citation was made against, by digest.

## Publication

```text
/project publish Main --scope scope --output first-draft
/project link Example illustrates Main
/project link Paragraph documents Main
/project mark Helper internal
```

`/project publish` prepares a local publication draft, nothing more: a fresh
bundle under `publications/<output>/` holding the frozen plan in
`publication.json`, the assembled `writeup.tex`, and `compile.log`, with the
readiness and any gaps printed to the terminal. Item and scope selectors are
stable ids or `ID@FULL_SHA256`, never a guessed name, and both `--scope` and
`--output` are required. An existing bundle at that output name is refused,
including after restart, so a later publication can never overwrite one already
made. `/project link SOURCE illustrates|documents TARGET` records that one item
bears on another (`documents` needs an exposition or document-fragment source,
`illustrates` needs an example); `/project mark ITEM internal|public|omitted`
sets an item's visibility, refused for an item already admitted as trusted
background, since that pins its exact digest and this command does not migrate
trust. The full selector syntax and what each verb refuses is in
[project publication commands](../reference/cli.md#project-publication-commands).

All three verbs are refused while a turn is running, for the same reason
`/project switch` is. `/publish <selection>` is a separate, older prompt
shortcut that only asks the model to draft a publication in conversation; it
does not write a bundle, unlike `/project publish` above.

## Branches

The conversation is an append-only tree, never a rollback.

```text
/tree
/fork <entry-id|root>
/abandon <entry-id|root> <lesson>
```

`/tree` lists every entry and marks the active leaf, abandoned branches
included. `/fork` opens a new branch from a chosen entry (or `root`, the empty
conversation), building a fresh runtime from exactly that entry's visible
history rather than inferring anything from provider state the earlier branch
held; the replay is bounded to one mebibyte, and a selection over that is
refused by name, telling you to choose an earlier parent. `/abandon` leaves a
branch the same way and attaches the lesson you give as an unverified human
note, never as evidence that the abandoned reasoning was checked or wrong.
Neither command touches the mathematical workspace: the Lean tree, the saved
theorems, the approved assumptions and the record are shared across every branch
and unaffected by which one is active. Spend accrues across all of them, not
only the active branch.

Both `/fork` and `/abandon` are refused while a turn or a provider worker is
running. Compaction and `/export` both follow whichever branch is currently
active.

## The workspace summary

```text
/status --full
```

Plain `/status` reads the model, the spend ledger, and the workspace's paths
from the artifacts themselves. `--full` adds a summary assembled the same way:
the goal, every approved assumption with its source, stated reason and approval
date, every saved theorem under the verdict its own stored audit gives it, what
is still open, what tool calls were refused and what Lean said, the naming
registry, and what remains before anything here may be reported as finished. A
theorem whose audit never ran, has expired, or whose name collides with another
module's appears under "Not established" rather than under "Proved", because
printing both headings would make one of them a claim on its own. It carries no
spend figures; that section is `/status`'s own, for you, and is withheld from
the model on purpose.

A theorem closed by a single automation call, one tactic that happened to
finish the whole statement, is named along with the tactic that closed it: it
is still a saved theorem, but it may assert far less than its name or the
prose around it suggests. And if nothing is saved at all, `--full` says
exactly that, "No theorem is saved: nothing here is reportable.", rather than
the separate "Nothing outstanding: every saved theorem is written up." it
prints when saved theorems exist and none owe anything.

## Exporting a session

```text
/export [path]
```

`/export` writes one self-contained HTML file, no scripts, no fonts, no images,
nothing fetched when it is opened, holding the conversation, the Lean and
writeup sources, the audit verdicts, the naming registry, the approved
assumptions with their provenance, the spend, the project instructions the model
was given, the points where its memory of the conversation was reset, anything
that arrived by import rather than being written in the session, the shared Lean
the saved theorems actually import, and the model and toolchain identities
alongside the settings that shaped what the session could find out. With no path
given, it lands in the problem directory under the project's name and the time.

The point of the file is that it keeps Hardy's distinctions rather than
flattening them: a kernel-verified theorem, a theorem the kernel checked given a
human-approved axiom, and a sentence someone typed into the conversation are
rendered as three different things, never blurred into one narrative.
Credentials matching known token shapes are removed before the file is written,
which is a filter and not a proof, so read an export before you share it; the
Lean is exempt from that filtering, since it still needs to hash to what was
actually checked, but the writeup `.tex` is not.

The destination can be anywhere off the machine, moving it there is the point,
but it cannot be a symlink: a checkout that shipped `report.html` as a link to a
file elsewhere on your machine would otherwise let `/export report.html`
overwrite that file while looking like an ordinary local write.

## Models

```text
/model [identity]
```

With no argument, `/model` opens a selector: arrow keys or a row number, Enter
to choose, Esc to cancel, and an "Other..." row at the bottom for typing an
identity the catalog does not carry at all. Rows are read through the current
backend, so a session cannot be offered an identity its own transport cannot
serve; each row is marked with where it came from and that availability was
not verified against the provider. A configured identity the catalog does not
recognize, or lists under an incompatible family, still appears, first,
marked as current. Typing an identity directly bypasses the catalog entirely
and is left to the transport to judge; a bare row number typed as an argument
to `/model` is refused, since row numbers are not stable across sessions.
After switching, you are asked whether to save the new identity as the
default in the config file; the running session has already moved either
way.

`hardy chat` itself runs on whichever backend the config file names (`claude` by
default, or `api`); the model switch above changes identity within that backend,
not the backend itself. `--fresh-thread` starts the session on a new provider
conversation while leaving the workspace, its record and the spend ledger
untouched; only the machine-local conversation id is discarded, and the discard
is itself an event in the transcript. It is a flag only, with no config key or
environment variable behind it, because "always start fresh" is not a coherent
standing preference the way `--no-project-context` is.

## Cancelling

Esc cancels a turn in flight: the model stops, no further tool call runs, and
the Lean, LaTeX or computer algebra process it started is interrupted rather
than left running to its own timeout. On the subscription backends this is
exact, the SDK genuinely stops the model; on `backend = "api"` the in-flight
request cannot be aborted, so it runs to its answer, which is discarded rather
than shown, and no tool call runs either way. A second Esc stops waiting and
kills whatever had not taken the first hint, at the cost of that child's state,
such as a computer algebra kernel's namespace; on Windows, that kill reaches the
process Hardy started and not the tree beneath it. Against a command that owns a
child of its own, a running `/cas` cell or a `/prove` run, Esc reaches that
instead, with the same two presses.

A computer algebra cell that answers the first Esc costs only itself: the kernel
survives, and everything earlier cells put in its namespace survives with it,
but the cell that was interrupted is never accepted, since nothing it changed
before it stopped is rolled back. A cell that does not answer within a couple of
seconds is stopped the way its own timeout would have stopped it, and its state
goes with it.

Ctrl+C, once, while a turn is running, only warns. A second Ctrl+C leaves the
session at once, at the cost of whatever that turn was still doing, and may
orphan a Lean, LaTeX, or computer algebra process it started. With no turn
running, Ctrl+C leaves immediately.

While a turn is running, most commands wait or refuse outright, because a
running turn owns the record, the transcript, and the one locked computer
algebra kernel. Only `/help`, `/status` (`--full` included), `/clear`, `/tree`,
`/exit` and `/quit` work alongside one; everything else, including `/model`,
`/cas`, `/project`, `/import` and `/prove`, has to wait for the turn to end or
be cancelled first.

## Project context files

Hardy reads one file of project instructions at the project root, and nothing
above it: `HARDY.md` if it exists, `AGENTS.md` otherwise. Ancestor directories
are never walked, and Claude Code's own settings and `CLAUDE.md` files are not
read at all. The whole text, not a digest, is appended to the transcript the
first time it is read and again on every change, so what the model was told is
part of the record rather than an assumption about ambient configuration. It is
treated as context, not authority: nothing in it can license a hole, a weakened
statement, or an unapproved axiom, so an `AGENTS.md` that says "get it
compiling" cannot turn into permission for either. A pathological file is capped
by both lines and bytes, head first, and the model is told outright when what it
is looking at is a truncated fragment rather than the whole file.

`--no-project-context` (or `HARDY_PROJECT_CONTEXT=0`, or
`project_context = false` in the config file) turns this off entirely;
`/status` shows whether the active session is carrying project instructions,
under "Instructions". Graded runs, `hardy prove`, `hardy accept`,
`hardy evals run`, read no such file at all, since a run whose instructions
came partly from a project-local file is not comparable to one that did not.

