# The interactive session

This page explains how `hardy chat` keeps a workspace that survives the process
and stays honest about what happened in it, for a reader who has used the
session and wants the reasoning behind its shape. The shape of the whole system
is [the architecture overview](overview.md); who is trusted with what,
assumptions included, is [the trust boundary](trust-boundary.md); what each `/`
command takes and does is
[session commands](../reference/session-commands.md), and this page says why
those commands are shaped that way. The files named here are laid out in
[the on-disk layout](../reference/on-disk-layout.md) and their fields in
[artifacts](../reference/artifacts.md); the settings are in
[configuration](../reference/configuration.md).

## The durable workspace

A session is a view onto files, not a conversation that happens to write some.
Three of them carry it:

- `session.json`, the record: the naming registry, every approved assumption
  and why, the stored audit verdict for each closed theorem.
- `transcript.jsonl`, the append-only trace: whole assistant blocks, every tool
  call and every result, the project context that was read, the steering blocks,
  the model switches, the branch transitions.
- `.local/state.json`, machine-local: the provider's own conversation id, the
  running spend ledger and its cursor, and the transcript identity the resumable
  conversation is bound to.

The split is the design. The first two are evidence and are committed; the third
means nothing on another machine or another account, so it is never committed
and nothing depends on it being there. Everything the terminal shows about the
state of the work, `/status` and `/status --full` above all, is read back out of
the first two rather than out of anything the model said, which is what lets the
screen contradict the conversation in front of the user.

`record.py` owns the writing. Appends are serialized, because three threads
reach it: the provider runtime's worker, the tool threads the SDK starts, and
the shell. Append mode alone does not keep their lines whole; under load,
threads appending together lose lines to interleaving, and a reader drops a torn
line silently. So the lock is held for exactly one line's write, and the
transcript additionally takes an OS-level lock so two processes over one
workspace cannot interleave either. `session.json` is replaced whole or not at
all through a rename, and is deliberately not flushed to the platter per write:
it is derived from the transcript beside it, and the rename is atomic whether or
not the bytes have landed.

One file of project instructions is read: `HARDY.md` if it is there, `AGENTS.md`
otherwise, at the project root and nowhere else. Ancestors are never walked, and
the whole text, not a digest of it, is appended to the transcript on first use
and on every change. The objection to inheriting the user's ambient agent
configuration was never that Hardy read the user's context; it was that nothing
recorded it, and recorded context satisfies "a run is the run its record
claims". It is rendered as context and not as authority, because an `AGENTS.md`
in a Lean repository plausibly says "get it compiling" and no project file may
license a hole, a weakened statement, or an unapproved axiom
([the trust
boundary](trust-boundary.md#what-hardy-controls-and-what-it-does-not)).
Graded runs read no such file at all, since a run whose instructions came partly
from a project-local file cannot be compared with one that did not.
`workflows/interactive/context.py` holds the reading and the bounds; a
pathological file is capped by lines and by bytes together, head first, and the
model is told when it is looking at a fragment.

Reopening a workspace resumes the provider conversation it left off in. Two
switches sit around that, and each names the one thing it governs.
`--no-project-context` governs what this run's system prompt carries, and it is
one of three ways to say the same thing: the flag, `HARDY_PROJECT_CONTEXT`, or
the `project_context` key in a config file, since whether a session reads the
project's own instructions is a coherent standing preference
([configuration](../reference/configuration.md#settings)). `--fresh-thread`
starts the session on a new provider conversation while keeping the workspace,
the artifacts, the transcript and the spend ledger exactly as they are: only the
machine-local conversation id is discarded, and the discard is an event in the
transcript, because a turn produced from an empty conversation is not comparable
to one produced from a thousand-turn one. That one is deliberately a flag and
nothing else, with no config key and no environment variable behind it, because
"always start fresh" persisted would silently discard the conversation on every
launch, which is not a coherent standing preference. Together the two are the
fully clean interactive condition.

### What a project switch rebuilds and what it keeps

`/project switch` opens another problem in the process already running, and the
whole design is the line between rebuilt and kept (`app/projects.py`).

Rebuilt, because they are the problem's own: the record, the transcript, the
approved assumptions, the Lean namespace, and the computer algebra kernel, which
logs into that problem's own `cas/` because sharing one would put two problems'
cells in one log and one export. That is what keeps two problems in one root
from sharing an axiom approval or a trajectory.

Kept, because they belong to the root and cost tens of seconds to establish: the
pinned Lake project and the Mathlib environment behind the search tools. Without
that distinction a switch would be `exit` with extra steps, which is the
workaround it exists to replace.

Kept per problem rather than per switch: the retrieval meter. Renewing it on
every open gave a problem a full allowance each time it was reopened, so a cycle
of two problems refilled both, and repeating the cycle made a cumulative budget
effectively unlimited while the next ranking reported no prior spend. Returning
to a problem resumes its record and its conversation; its meter belongs with
them.

`--fresh-thread` is deliberately not carried into a switch: it is one act on the
session the launch opened, and carrying it would silently discard the
conversation of every problem visited afterwards, which is the standing
preference the flag refuses to be. The switch itself is refused while a turn is
running, because a running turn is appending to the record and the transcript of
the problem it started in. It runs on a worker so the terminal stays live, and
it is cancellable: the old kernel is not closed and the active project is not
rewritten until one commit point, so a cancelled switch closes only the kernel
it started and leaves the session exactly where it was. One thing before that
point is bounded rather than closed: preparing the target problem's layout is
not atomic, so a cancel arriving while it runs leaves whatever it had made by
then. That is cheaply survivable, because Hardy recognises its own bare
scaffold, so the name can be created again rather than being burned by the
attempt.

## Streaming and cancellation

The terminal draws deltas as they arrive; the record holds whole blocks. That
rule and its consequences for the transcript are
[the output contract](output-contract.md#streaming-does-not-change-the-record).
What belongs here is what streaming does to cancellation, because a turn that
can be stopped mid-flight is a turn whose record can disagree with what
happened.

Esc sets a flag. `MathematicsSession.cancel` records the cancellation, asks the
runtime to interrupt the model, and signals the children the turn started;
`turns._dispatch` then refuses *new* tool calls while letting in-flight ones
finish. The flag is read **twice**: once before the tool gate and again after
acquiring it. The provider may launch several calls at once, and one can pass
the first check, block behind a Lean run that takes minutes, and arrive long
after the turn was cancelled; without the second look it would run, which is
exactly what cancelling promises will not happen.

Starting a turn is what clears the flag, so where "starting" happens decides
whether a cancellation survives. Neither `turns.stream` nor the Claude runtime's
`stream` is a generator: a generator body runs on whoever iterates it, which is
a worker thread, and Esc resolves in the same input batch as the Enter that
began the turn. A lazy reset would land after the cancellation and undo it,
leaving the model running while the transcript said it had stopped. So both are
eager, and the shell hands only the iteration to the executor.

Eagerness closes the window before the turn's own thread starts. It does not
close the one inside it: connecting the SDK client happens on that thread, so a
cancellation landing while the client is still unpublished finds nothing to
interrupt and can only set the flag. The exchange therefore reads the flag after
publishing the client and before asking anything (`agents/claude.py`). Without
that, the turn goes on to consume a whole reply while the terminal and the
transcript both call it stopped.

**Interrupting does not kill a subprocess, and cannot unwrite a file.** A Lean,
LaTeX or computer algebra process already running is asked to stop; a second Esc
stops waiting and kills what did not take the hint, at the cost of that child's
state. A file a tool call has already written stays written. A reply that lands
anyway is still printed, labelled as belonging to the turn that was stopped,
because a record that denied text the user watched arrive would be worse
evidence, not better.

Teardown has an ordering requirement. A consumer that unwinds, Ctrl+C under
`--plain` most of all, closes the generator, and with `yield from` that teardown
reaches the runtime first: it interrupts the model and then waits on its worker,
all while the session's tool gate is still open and the provider can dispatch
one more call. `turns._stream` therefore yields explicitly rather than
delegating, so the gate shuts before any of that, and the plain loop holds its
own named reference to the generator so the close happens after `cancel()`
rather than the instant the loop is left. The transcript gains a `turn` event
with status `cancelled`, kept distinct from `abandoned`, which is for the
`/exit`, forced-exit and app-exited paths where nothing was actually stopped.

Whatever the terminal has drawn is flushed on every path out of a turn: the
wrapper emits a line only once no further delta can change it, so a turn cut off
mid-sentence always has words still held in its tail. That covers the failure
path, the plain path's Ctrl+C, and the case where the application leaves with a
turn in flight.

Starting a turn can now fail before a single event exists, since it writes the
transcript's `user` event and starts a thread. Both terminals catch that
synchronously and keep the session rather than losing it to one bad turn.

One consequence reaches the record. The provider's conversation id is written
down by the **observer path**, on the `result` event, rather than only by the
outer generator's teardown. A turn nobody drains still happens, because the
runtime's worker is eager; left to the generator alone, reopening the workspace
would start from nothing while the artifacts on disk implied a conversation that
had already taken place. A report the consumer already gave up waiting for is
kept in the transcript, since it happened, but is not folded into the spend
ledger: every figure in it is session-to-date, a later turn has since reported a
larger one, and differencing against it would read as a counter restart and
count the whole thing twice. The cursor still advances past it, because skipping
is a decision and a replay after a crash must make the same one.

## The transcript identity binds the provider conversation

`.local/` is gitignored, which makes a `git checkout` the interesting case. A
checkout that rewinds the *versioned* transcript leaves the stored conversation
id pointing at the newer tip. Resuming it would continue a provider
conversation containing turns that are absent from the transcript on disk, so
the session's answers would depend on context the record does not contain, which
is the one property the record exists to guarantee.

So the conversation is bound to the transcript it was recorded against, and the
binding is checked on every open rather than trusted. A conversation whose
transcript has been shortened or replaced is dropped. Losing a resumable
conversation is the cheap half of that trade; a record that cannot account for
its own answers is the expensive half.

**The cheap check is not sufficient, and reusing it would be a bug.** Comparing
the ledger cursor against the file's size catches a *shortened* transcript and
nothing else: checking out a divergent branch whose transcript is the same
length or longer leaves the cursor arithmetically valid against a history that
never produced it, and the conversation resumes from the other branch's context.
So a transcript **identity** is stored, a length together with a digest of the
transcript's first that-many bytes, recomputed and compared on open
(`record._transcript_identity`, `record._carried_thread`). The size test
stays as the cheap first check, not as the whole test.

The identity is bound to the conversation id, not to the ledger cursor, and the
two are written together and never apart. The observer appends the `result`
event and remembers the conversation *before* the spend fold advances the
cursor, so digesting the prefix the ledger accounts for would leave a resumable
conversation whose last turn sits beyond the digested span; a later checkout
replacing only that tail would compare equal and resume with hidden branch
context. An identity that did not travel with the conversation id would describe
some other moment, and a conversation id with no identity cannot be checked at
all. The branch epoch is checked alongside it, so a fork invalidates the old
binding even if the process dies before anything else is written. A backend that
has no conversation to remember clears the binding and records a `thread` event,
rather than leaving a stale one that a later switch back would resume with no
memory of the turns in between and nothing in the record marking the join.

## The steering block

The end-of-turn notice tells the *user* that nothing is saved. A failing session
was told eight times; the model saw none of them, and wrote itself a status
report saying the work was done. The steering block is the same arithmetic, put
where the model reads.

Every line is computed from disk or from this session's own counters, and none
of it is text the model wrote:

- saved theorems, machine-checked and open, from the same counting the writeup's
  banner uses, so one arithmetic answers both
  ([the output contract](output-contract.md#what-the-document-must-carry));
- how many assumptions are approved, from the record;
- this session's `save_lean` and `check_lean` call counts and how many of each
  succeeded, kept per session rather than per turn;
- any saved statement a single automation call closes outright, naming the
  tactic, which is the same disclosure the document carries and is put here
  because the model is the one that can still strengthen the statement;
- any `.tex` file under `tex/` not yet reached from `writeup.tex` by following
  `\input` and `\include` transitively.

Three details are load-bearing. The block is **prepended to the person's
message** as one string rather than sent as its own turn, because the runtime's
history is a sequence of turns and a block arriving as its own turn would read
back as something one of the parties said instead of as the workspace's
arithmetic addressed to whoever reads next. It is **recorded as a separate
event** immediately before the `user` event, so a reader of the trajectory can
tell Hardy's words from the person's, and computed before that event is written
so the transcript's order matches the model's context. And it is **omitted
entirely** when the workspace holds no Lean and no LaTeX file and the session
has made no tool call, which is the first turn of a fresh workspace, where every
line would be zero. Existence of the `tex/` directory does not count, since a
session creates it at startup.

The whole computation is wrapped in one guard: reading the Lean tree, the
obligations and the TeX paths can each fail for reasons that have nothing to do
with whether the turn should proceed, and a status line must never be the thing
that aborts the turn it is reporting on. The tally is snapshotted before it is
read, because a tool thread may add a key to it while a new turn is computing
the block, and iterating a dict that resizes underneath raises rather than
returning stale but safe data.

## The save streak brake

Three consecutive refused saves of one path, and the fourth is refused before
any gate or Lean process runs, with a message saying to check a smaller piece
and then save that checked source (`workflows/interactive/formal.py`,
`SAVE_STREAK_LIMIT = 3`). The counters live in memory only, in neither the
record nor `.local/state.json`, because they describe this session's behaviour
rather than the workspace, and they are cleared at the start of every turn: a
new turn is a new chance.

Four details make the brake behave rather than merely fire. The counter is keyed
on the path's safe-relative form, so `Main.lean` and `./Main.lean` share one
streak instead of two half-sized ones nothing ever brakes. A refusal does not
increment the counter further. The brake is lifted only by a green `check_lean`
on **the exact source intended for that path**, digested over the bytes a save
would actually write, so a check of something else, which has not been shown to
fix anything, does not admit the save; and one green check admits **one** save
rather than every save of that source for the rest of the turn, since a single
check on a byte string that then failed the save's stricter gates would
otherwise buy an unbounded run of refused saves the brake never fired on again.
`check_lean` itself is never throttled.

## Probes on an assumption request

An assumption is elaborated before anyone is asked to approve it, and refused if
Lean can prove it outright, because a statement the kernel closes is a theorem
nobody saved yet. That gate and the approval around it are
[the trust boundary](trust-boundary.md#assumptions). Two additions belong here
because they are about what the request has to show before a human sees it.

**The search gate.** A request is refused when no `inspect_declarations` has run
since the last one, telling the model to pass several candidate spellings and
let Lean say which exist. A session built with no search runtime cannot be asked
to search, so the gate does not apply there. An inspection that was *attempted*
but did not finish passes the gate and is described as such, because a machine
whose search will not answer must not be one where nothing can be assumed. The
proposal shown to the human carries the names inspected since the last request,
each marked resolved or not, so the human sees what was tried rather than being
told that something was.

**The vacuity probe.** The first elaboration asks two questions at once: does
the proposed axiom elaborate at all, and does any of a fixed automation ladder
close it. A second elaboration runs only when the first returns no refusal, and
asks whether the statement is vacuous: every named binder whose type is visibly
a `Prop` hypothesis is removed, and the ladder is tried against what is left.
Instance, universe and data binders are kept. When the conclusion is a bare
existential, witness attempts are added as well, and a witness the type does not
admit simply fails to elaborate on its own line, which counts as not closing;
unique existence is skipped, since a bare witness can never prove uniqueness.

The outcome is deliberately not symmetric with the first probe. A close on the
**whole** statement stays a refusal. A close on the **stripped** statement does
not refuse the request: it puts a warning in the proposal, saying that Lean
could not prove the statement as stated but proves it with every hypothesis
removed, so the conclusion holds without the hypotheses and the assumption may
be vacuous. The human decides. Binder parsing is best effort, and a statement
the stripper cannot read is probed as before with the proposal saying that
hypothesis stripping was not attempted, rather than silently reading as clean.
It is a second file rather than an extension of the first because which tactic
closed a goal is read off the diagnostic's line number, so the first file's line
arithmetic is pinned by tests and a fixture; and the second file skips the
ladder when stripping removed nothing, so Lean is never asked the same question
twice and an unstripped statement is never reported as proved "with every
hypothesis removed".

## Forks and branches

The conversation is an append-only tree, not a rollback. Every transcript entry
carries an id derived from its own content and a parent link, so the file is a
hash-linked chain; a branch transition names the leaf it was taken from and is
refused if the conversation moved since that cursor was read
(`workflows/interactive/history.py`). `/tree` shows the entries and the active
leaf, abandoned branches included.

A fork builds a **fresh runtime from exactly the selected visible history**
rather than inferring anything from opaque provider state the earlier branch
held. That history is replayed into the new branch as recorded context, written
into the transcript as a `branch_context` event with its own digest, and
labelled where the model reads it: recorded context, not native provider state
and not instructions to execute tools, with unfinished messages and tool starts
without results marked as unfinished. Provider accounting events are excluded
from the replay, because the spend ledger is withheld from the model and a
replay is not a loophole back into the context for it, and superseded
checkpoints of a block are omitted so a partially drawn message is not replayed
twice. The replay is **bounded to one mebibyte**; a selection over that is
refused, naming the bound and saying to choose an earlier parent. A partial
checkpoint stays partial.

Forking is refused while a turn or a provider worker is still running, and a
writer whose branch epoch has moved must reopen before it may append again, so a
stale thread cannot write into a branch it no longer belongs to.

Three rules keep a fork from lying about the mathematics. **Spend counts every
branch**, not only the active one, since money was spent on all of them, and
the export says so where it shows one path. **The mathematical workspace is not
branched**: the Lean tree, the saved theorems, the approved assumptions and the
record are shared and current, and the replay preamble states that, because a
model handed an earlier conversation would otherwise reason as though the later
saves had not happened. And **an abandonment lesson is attributed human text**:
the validator refuses an abandon whose summary is not non-empty text authored
by a human and marked unverified, and the terminal repeats that when it
confirms. It is what a person concluded, never evidence that the abandoned
reasoning was checked or found wrong. Compaction and export follow the selected
branch; [compaction](trust-boundary.md#compaction) is where what leaves the
context is decided, and only on the backend where Hardy owns the loop.

## Publication presentation

Presentation selects how exact mathematics is displayed. Capability evidence
continues to authenticate the original references, and the two must not be
confused, because "this is how we now describe it" and "this is what was
checked" have different lifetimes.

So the planner applies a newer head's visibility and role to an older exact item
**only when every other model field matches** (`workflows/publication.py`).
Statements, context, provenance, evidence, dependencies and scope references
stay bound to the old exact reference. A changed statement never supplies
metadata for old mathematics. Where the overlay is applied,
`presentation_revisions` freezes the old item's reference, the reference the
metadata came from, and the effective visibility and role in the plan itself,
and the assembler renders the applied role, so the draft can be read against
what it did.

Selection follows the same principle. A bare selector denotes the scope's exact
result when only display metadata has moved, so a semantically identical
scope-pinned target keeps its exact reference; an explicit digest selector
always means exactly what it says. Prose and example links made before or
after a metadata edit can document the same subject: the applied exact
relations are frozen in the plan's attachments, and prose keeps both its
documented target and the selected one. A newly attached example contributes
its own exact prerequisites to the shared evidence audit and to container
placement rather than inheriting anything.

Marking refuses one case outright: the presentation of an item already admitted
into a scope's background or interfaces cannot be revised, because that scope's
exact permission would go stale. Publication does not migrate trust.

There is deliberately **no second metadata store**. The equality rule reuses the
immutable ledger records that already exist, and a future flag to choose between
exact and current presentation belongs in the existing publication request
rather than in a parallel store that could disagree with the ledger. The planner
assumes recorded semantic links are meaningful and infers nothing that is not
recorded: not missing prose, not missing dependencies, and not mathematical
faithfulness from text.

Publication compiles, which makes it the one command that owns a child process
for minutes. Cancelling the await cannot stop the compiler or release the
session gates, so the handler signals the tracked compiler processes and keeps
the command and its Esc control alive until the worker actually finishes;
repeated presses escalate. The next prompt must never block on a leftover
publication holding the conversation gate, so no publication worker outlives the
handler's return. The shell owns resuming child signals at command admission,
before later keys in the same input batch are read, so admitting a publication
cannot erase an Esc, or downgrade a second-Esc kill, already entered for it. The
plain terminal has no batch key dispatcher, so it resumes at its own direct
command entry instead.

## Prompt templates and reserved names

A project keeps its repeatable asks beside itself, in `.hardy/prompts/`, and
four shortcuts ship with every session. A project file of the same name as a
bundled shortcut **replaces** that shortcut for that project; a file named for
an operational command is refused rather than allowed to shadow it, so a file
in a checkout can never change what `/exit` or `/status` does. The reserved
set is derived from the command registry itself, rather than listed twice.

A template is not a command Hardy runs. Dispatch expands it and the line becomes
an ordinary message, which is what puts the **expansion**, never the `/name`,
into the transcript: a shared transcript that said `/audit` would refer to a
file its reader may not have. A placeholder with nothing to fill it is a refusal
rather than an empty string, because a prompt that quietly lost half its
sentence still looks entirely ordinary. And a template is input rather than
instruction: it is deliberately not folded into the prompt-set hash a staged run
records, so a project file can never move the identity of Hardy's own prompts.
The reading rules, the size bound and the refusal of anything reached through a
link are in
[session commands](../reference/session-commands.md#your-own-commands).

## The model menu

`/model` with no argument opens a menu whose rows are read **through the
backend**, because an unfiltered list offered a session four identities its
transport could not serve. Every row is labelled with where it came from and
with `availability unverified`, and the menu's subtitle says the suggestions are
Hardy's bundled catalog and that provider availability was not queried. The
catalog performs no live discovery, asserts no capabilities, and adds no
identities of its own; it is a list of suggestions with provenance
(`app/catalog.py`).

A configured identity the catalog does not list, or lists under an incompatible
family, is still shown, first, marked as the current configured identity and
carrying its reason. Retaining it matters: the session is running on it. But
visibility is not permission, so the row says `capabilities unknown` and
`availability unverified` too, and a catalogued identity the backend cannot
serve is refused at selection with its reason rather than at the next provider
request. An identity the catalog has never heard of is the escape hatch and is
left to the transport to judge, since the provider is the authority on a release
this file has not caught up with. A bare number is refused: row numbers are not
stable identities, because the menu prepends an unlisted current row and always
appends the escape hatch, so a digit would name a different model than the
number a user remembers.

The switch itself is recorded in the transcript, because which model produced
which turn is part of the experiment's identity rather than a display detail.
The provider conversation carries over, and a backend that holds the
conversation itself is handed it explicitly, so that switching model means the
same thing on every transport rather than silently discarding the session's
history on some of them.

## Why there is no warm Lean pool

Every Lean call starts a process and elaborates `import Mathlib`. Making that
persistent is the obvious optimization, and the design defers it until measured
latency warrants it. `hardy latency` is that measurement rather than a guess
(`formal/latency.py`, and
[the command reference](../reference/cli.md#hardy-latency)).

A call spends its time in two halves a pool treats differently. The **prelude**,
process start plus the imports, is fixed and is exactly what a warm process pays
once instead of every time; the remainder elaborates the proof body, and a warm
process still pays it in full. So the question is not the wall time of a call,
which conflates the two, but the share of a run the prelude takes:

```
recoverable = prelude * (calls - workers)
```

The prelude is isolated the only way that works, by elaborating a source that
carries the imports and nothing else, repeated so the median steps over the
first sample, which also warms the operating system's page cache and so
measures a colder machine than a run ever sees. Below three successful probes no
verdict is issued at all: the one-time page-cache cost would be inside the
number, and that number is then multiplied across every call, so a report that
disclaims its own prelude and then rules from it is arguing with itself.

The `- workers` term is the load-bearing part. Every warm process pays its own
first import, so ten calls against a single persistent process recover nine
preludes and against a pool of four recover six. Crediting a pool with imports
nobody avoids is what makes an unwarranted pool look warranted, and the count of
workers is clamped to the count of calls so a four-worker pool over one call
cannot be credited with saving anything. The default is one worker, the single
persistent process, which is the cheapest thing a pool could mean. The default
threshold is a quarter of the run: below that, a pool recovers less than the
process-death recovery, pristine reset and snapshot machinery costs to carry.

Two limits are stated rather than papered over. The estimate is **sequential**:
it says how much prelude time disappears, not what a concurrent pool does to a
critical path this command does not observe. And a prelude measured on one
machine is a property of that toolchain and that machine, so the measurement
records the environment it was taken in rather than being quoted as a constant.

## Terminal notes

Everything above is the design. This section is the terminal, where several
decisions look arbitrary until the trap behind them is named. The real terminal
layer is confined to two modules, `app/tui/shell.py` and `app/tui/select.py`;
everything else speaks a plain `Ui` port, which is why the line-based fallback
needs none of it and behaves identically where it can.

**Every command name is a real registry entry, aliases included.** `/quit` is
its own entry pointing at `/exit`'s handler rather than an alias field on it. If
aliases were a list on the canonical command, a prefix matching only an alias
would have nothing coherent to complete: `/q` matches `exit` through `quit`, but
`exit` does not start with `q`, so appending the canonical tail renders `/qxit`,
while returning nothing contradicts aliases being completable. Giving each name
its own entry means every string the suggester can match is a string the user is
literally typing, so it only ever appends. `/help` lists the canonical
entries only, so an alias completes at the prompt without doubling the list a
reader has to scan.

**Alt+Enter is deliberately not bound, and dropping it buys something.** A
newline is Shift+Enter, which takes one deliberate step: the library maps the
Shift+Enter, Ctrl+Enter and Ctrl+Shift+Enter sequences onto the same key as
plain Enter, so the shell extends that table to route Shift+Enter to a key of
its own; a trailing backslash also continues a line, for terminals that never
emit those sequences. At the wire level Alt+Enter *is* Escape then Enter, so
binding it would make `escape` the prefix of a longer chord, and a plain
`escape` binding could then no longer be eager. A non-eager Escape waits out an
ambiguous-key window of about a second and a half, and in that window Escape
followed by `/` is swallowed as an Emacs binding, so pressing Esc and then
typing `/status` loses the slash and submits `status`. With no Escape-prefixed
chord anywhere in the bindings, Escape is eager, responds instantly, and that
collision cannot occur.

**Eager Escape and a chorded Escape cannot coexist.** This is the same fact from
the library's side, and it is worth stating because it is not what a naive
reading predicts: an `eager=True` binding on the one-key Escape fires
immediately and **shadows** any two-key sequence starting with Escape outright,
so Escape then Enter fires the one-key binding twice and never the chord.
Removing `eager=True` restores correct disambiguation and reintroduces the
window above. Any handler that wants a plain Escape and an Escape-prefixed chord
to coexist must not mark the plain binding eager; Hardy chooses the eager
Escape and binds no chord.

**`/clear` is viewport-only.** Because scrollback is the terminal's, Hardy does
not own the lines it has printed. Clearing the viewport and homing the cursor is
all it does; erasing the scrollback buffer would also destroy whatever unrelated
shell output preceded Hardy, which is not ours to do. Nothing is removed from
scrollback, nothing from `transcript.jsonl`, and the model's conversation is
untouched, and both `/help` and the command's own summary say so, because a
`/clear` that implied a reset of any of those three would be the dishonest kind
of convenience.

**`/model` is refused while a turn is running.** Switching replaces the live
runtime, but the abandoned turn still holds a teardown that remembers the
provider conversation, reading it off whatever runtime is current: switch
mid-turn and that teardown stamps the *new* model's conversation onto the turn
the *old* model answered, while the transcript already carries the switch.
Replay would then attribute one model's work to another, which is the identity
confusion the record exists to prevent. There is a second hazard in the same
window: the switch rewrites `session.json` without taking the tool gate, so it
can interleave with a tool call doing the same.

**Command handlers are coroutines, because they run on the event loop.** They
are reached from a key binding, which is the loop itself. A synchronous handler
calling a blocking selector would be waiting, on the event loop, for arrow keys
only that loop can deliver, and would hang on the first keystroke; marshalling
to a thread is no escape, since that raises on the UI thread precisely because
using it there would deadlock. So the prompting methods are coroutines: a
handler awaits the selector, which runs a nested application on the live loop,
the loop keeps delivering keys, and the outer input box resumes when it returns.
No thread is involved, and none should be.

**A forced exit is `os._exit(130)`, and its costs are part of the design.** The
first Ctrl+C during a turn only warns, and names the cost before the user
commits to it. The second records the abandonment synchronously, so the trace
lands first, and then bypasses interpreter shutdown entirely. That is
deliberate rather than expedient: the turn's worker is non-daemon, so an
ordinary exit joins it at a safe boundary rather than truncating a write
mid-flight. That is exactly why a *forced* exit cannot go through shutdown at
all: it would be joined, and the turn cannot be told to stop. The price is that
the worker is not joined, `atexit` handlers do not run, buffered writes
elsewhere are not flushed, and a Lean or LaTeX child may be orphaned. `130` is
the conventional interrupt status. A forced exit is a deliberate choice to
accept a mess, and the warning says so rather than implying a clean stop.

**Nothing terminal-shaped is built on a foreign thread, because ambient device
lookup reads contextvars.** A nested application built without explicit input
and output devices inherits them from whatever application session is current,
and that inheritance reads a `contextvars.Context`, which does not propagate
across thread boundaries; worse, scheduling a coroutine onto a loop from
another thread captures the *calling* thread's context at the moment it is
invoked, not the loop's, even though the coroutine body later runs on the
loop's thread. A nested application relying on ambient lookup then tries to
build a real console output: on one platform that raises, and elsewhere it
silently attaches to the process's actual stdio instead of the intended
devices, which is worse. So a tool thread posts only the *coroutine* across a
queue, and the drainer awaits it on the loop, in the loop's own context, where
the application is constructed for the first time. The shell and the selector
both accept explicit input and output devices as well, which is how a test
injects an inspectable stream; the shell passes its own to the application it
owns, while a nested one takes the ambient session that the drainer has just
guaranteed is the right one. A discarded output cannot prove that a suggestion
rendered dim, so appearance is asserted against a real virtual-terminal output
over an inspectable stream and read back as escape sequences; behaviour alone
can use a discarding one.
