# Hardy workbench, second pass: computations as files, a model picker, and conversation during computation

This file is the durable record of three related changes to the interactive
session and the browser client: the reasoning behind each and the criteria an
implementation must meet. Implementation status lives in
[the roadmap](../../roadmap.md).

It builds on [the delegation and research-swarm design](2026-09-10-delegation-swarm-design.md):
where that spec introduced one hierarchy for background *model* work, this one
puts background *computation* under the same hierarchy rather than beside it.

## 1. Goal and scope

Three asks, taken together because the third depends on the first two being
honest about what they record:

1. **Every computation is done in a CAS file.** A computer algebra cell is the
   execution of a file under the problem's `cas/` tree, authored by the model
   or typed by the user, so that what was computed is a file a reader can open,
   diff and rerun, not a string that lived only in a tool call.
2. **A model picker in the browser.** `/model` already switches the live
   model; the page gets a control that does the same thing without typing.
3. **Conversation continues while Lean or the CAS computes.** A long check,
   save or cell no longer holds the turn hostage. It is detached into a
   background job under the delegation hierarchy, the turn ends, the person
   can keep talking, and the result reaches the model at its next safe
   boundary. A message typed while a turn is running is queued and sent when
   the turn ends rather than refused.

Out of scope: concurrent chats over one record, a warm Lean pool, browser-side
file editing, and any change to what counts as evidence.

## 2. Invariants carried forward

1. **No computation is evidence.** A CAS file is a record of what was
   computed and nothing more; nothing here reaches `formal/`.
2. **Nothing enters the model's context mid-request.** A background result is
   queued durably and delivered ahead of the next provider request, never
   smuggled into an unrelated tool result.
3. **Every tool call goes through the turn coordinator's gate.** Detaching a
   computation moves *who releases* the gate, never whether it is held.
4. **The record stays true.** A detached call is recorded as detached; its
   result is recorded when it exists, as Hardy's own event; and a turn Hardy
   starts on the model's behalf is recorded as Hardy's, not the person's.
5. **Delegation is execution state.** A computation job is a delegation leaf
   with no provider context, no ledger refs and no findings. It has an id, a
   journal, a place in `/jobs`, and a cancellation, and it does not become a
   second job system.

## 3. Computations as files

### 3.1 The tool

`cas_run` takes `path` (required) and `source` (optional).

- `path` is relative to the problem's `cas/` directory, proven safe by
  `safe_relative`, and must carry the backend's script suffix (`.py` for
  SymPy, `.sing` for Singular, `.m2` for Macaulay2). Hardy's own names are
  refused: `cells.jsonl` and its siblings, `session.*`, `export.json`, and
  anything under the scratch trees `replay/` and `script-run/`.
- With `source`, the file is written first, normalised to `source.rstrip() +
  "\n"` exactly as `save_lean` normalises, through the `cas/` write guard, and
  then executed in the persistent kernel as one cell. Without `source`, the
  file already there is read and executed; a missing file is a refusal.
- The cell record carries `path`. `source` stays on the record too, because
  the file may be rewritten later and the record has to say what ran.

State still carries over between cells: a file that defines a ring and a file
that uses it are two files run in order, as two Lean modules are saved in
dependency order. The exported script remains the whole reproduction; each
cell's header line in it, and each notebook cell's metadata, now name the file
the cell came from.

The same runtime method serves every binding (`algebra/tools.py`,
`app/mcp.py`, `agents/staged.py`, `workflows/delegation/worker.py`), each
writing under its own `cas/` directory, so a worker's private kernel files its
cells beside its private journal.

### 3.2 The workspace

`.py`/`.sing`/`.m2` paths resolve to the `cas/` tree in `_resolve`, so
`read_file`, `delete_file` and `read_workspace` see CAS files as they see Lean
and TeX. Deleting one deletes the file and nothing else: the cells it ran stay
in the journal, because a record that forgot a computation when its source was
removed would be a record edited after the fact.

### 3.3 The human's cells

`/cas run <path>` runs a file. `/cas file <path>` reads a block and saves it
to `<path>` before running it. A bare `/cas <expr>` or `/cas` block is filed
as `cas/typed/NNNN.<suffix>` with the next free number, so a typed cell is a
file too. `state`, `reset` and `export` are unchanged.

### 3.4 Prompt

The CAS section of the chat prompt says that a cell is a file, that the path
names what the file computes, and that a file is rerun by naming it without
source.

## 4. The model picker

`GET /api/models` returns the rows `/model` would offer: the same
`model_rows` the terminal menu draws, with the current identity marked, the
backend and how it authenticates, and without the terminal's "Other…"
sentinel. The page draws a native `<select>` in the header showing the current
model, with the rows as options and an "Other…" option that swaps in a text
field. Choosing submits `/model <identity>` through the ordinary input path,
so the switch is recorded, refused while a turn runs, and followed by the same
"save as default?" card the terminal asks, and the header updates from the
`state` event the switch produces. The control is disabled while a turn or a
command runs, with the dispatcher's own sentence as its title.

No new switching path: the picker is sugar over the command, and a second path
would drift.

## 5. Background computation

### 5.1 What is detached and when

`check_lean`, `save_lean`, `check_latex`, `save_latex` and `cas_run` are the
detachable tools. A call runs on a computation thread from the start; the
dispatching turn waits up to `compute_detach_seconds` (a setting, default 10;
`0` disables detaching) for it. A call that finishes inside the grace is
answered inline exactly as before and leaves no delegation behind. One that
does not is **detached**: the tool call returns at once with the job's id and
the sentence that the result arrives at the model's next turn, the job is
journaled as a delegation, and the human is told through the notice channel.

Promotion after the grace, rather than a delegation for every call, keeps
`/jobs` about work that actually took time.

### 5.2 The gate hand-off

The turn coordinator's gate is a plain lock, and a plain lock may be released
by a thread other than the one that took it. The dispatcher takes the gate,
starts the computation, and waits. If the call is answered in time the
dispatcher releases the gate as it always did. If the call is detached, the
dispatcher returns *without releasing*, and the computation thread releases
the gate when the work and its bookkeeping are done. Every other tool call
still waits its turn behind the gate; a detached save cannot interleave with
anything.

The hand-off is decided under a lock the job and the dispatcher share, so a
computation finishing in the same instant the grace expires is delivered
exactly once, inline or detached, never both and never neither.

### 5.3 What the job is

A detached computation is a delegation leaf under the session root with
`task_mode="compute"`, `created_by="model"`, no project refs, no scope, an
empty lease and no worker slots. It is journaled `created`, `budget.reserved`
and `started` with this process's owner token, and ends `completed` (the tool
produced an answer, green or red), `cancelled` or `failed` (the tool raised).
The `WorkerResult.synthesis` is the tool's output bounded to an excerpt;
`result.json` under the delegation's artifacts holds the whole output. Usage
reports `active_seconds` and marks cost, tokens and provider calls unknown,
because none were spent.

`DelegationSpec.scope` becomes optional to admit this. Every existing path
still records one.

`/jobs` lists it, `/jobs <id>` inspects it, `/cancel <id>` cancels it: the
token's callback interrupts the children the job's own thread started and, for
a cell, the kernel. Children started by a detached job are marked *detached*
in the process register, and Esc's interrupt and stop sweeps skip them: a
person stopping the model's reply is not stopping a check they were told is
running in the background.

### 5.4 Delivery to the model

When a detached job ends, Hardy appends a `job` event to the transcript: the
job id, the tool name and arguments, the original call id, the status and the
whole result. A job event is owed to the model until a `job_delivered` event
names it, and the owed events are rendered as a block ahead of the next
provider request, beside the workspace steering block and the delegation
attention block:

```
[Hardy background results — written by Hardy, not the user]
job d-…: check_lean Main.lean — finished, 41s — ok
<the tool output, bounded>
```

Each result is bounded to the observation budget with a note naming the
transcript event when cut. The `job_delivered` event is written once the
runtime has accepted the request, as attention receipts are, so a request
that never reached the provider leaves the results owed.

The delegation's own attention item still routes: the human gets the notice,
and the model's attention block names the completion. The results block is
what carries the output the model has to act on.

### 5.5 Continuation

Background completion does not start unsolicited main-agent turns by default
(delegation spec 16.8). A computation the model itself started is the named
exception: its next action explicitly depends on the result. So when a job
ends and the session is idle -- no turn running, no command running, no
message queued -- the host starts a turn whose text is Hardy's:

> Hardy: background work you started has finished; its results are above.
> Continue from them.

The turn's `user` event carries `author: "hardy"`, the terminal and the page
draw it as Hardy's line rather than the person's, and it does not count as a
human turn when a delegation continuation asks whether the conversation has
moved on. If the session is not idle the results simply ride along on the next
turn, whatever starts it.

## 6. Messages during a turn

`dispatch.classify` answers `queued` for a plain message while a turn or a
command runs, instead of `refused`. Both hosts keep the queued lines in order
and, when the turn or command ends, start one turn from all of them joined by
blank lines: several lines typed while the model worked are one message, in
the order typed. A slash command that is not safe in flight is still refused,
because a command takes the session over and cannot wait in a queue without
changing what it means.

The page echoes a queued line at once, as it echoes any accepted line, and the
composer says how many are waiting. The terminal prints that the line is
queued. Nothing is written to the transcript until the queued turn starts, so
the record's order is the order the model saw.

Precedence at the end of a turn: queued human messages first, then a job
continuation if results are owed and nothing is queued.

## 7. Settings and control surface

- `compute_detach_seconds` (`HARDY_COMPUTE_DETACH_SECONDS`), default `10`.
- `GET /api/models`; the `state` snapshot gains `queued` (how many lines wait).
- `/cas run <path>`, `/cas file <path>`.
- No new slash commands: `/jobs` and `/cancel` already cover a computation.

## 8. Acceptance criteria

1. `cas_run` without `path` is refused; with `path` and `source` it writes the
   file and runs it; with `path` alone it runs the file on disk; a reserved or
   unsafe path is refused before anything runs.
2. The cell record and the export name the file each cell came from.
3. `read_workspace` lists CAS files; `read_file` and `delete_file` accept them.
4. A typed `/cas` cell is filed under `cas/typed/`.
5. `GET /api/models` returns the terminal menu's rows without the sentinel and
   marks the current identity; choosing one produces a recorded model switch.
6. A detachable call answered within the grace leaves no delegation and
   returns its result inline.
7. A detachable call past the grace returns the job id, the delegation is
   journaled `started`, `/jobs` lists it, and a second tool call waits behind
   the gate until the job releases it.
8. A job's result lands as a `job` event, is rendered ahead of the next
   provider request, and is marked delivered only once the request was
   accepted.
9. When a job ends with the session idle, the host starts a Hardy-authored
   turn; when a turn is running or a line is queued, no turn is started and
   the result rides along.
10. `/cancel <job>` interrupts the job's child and journals `cancelled`;
    Esc does not interrupt a detached job's child.
11. A message typed during a turn is queued and becomes the next turn's text,
    several joined in order; a non-safe command during a turn is still
    refused.
12. `compute_detach_seconds = 0` restores blocking behaviour throughout.

## 9. Deferred

- Detaching the faithfulness read and the staged `prove` stages.
- A results panel in the browser beyond the jobs panel.
- Editing CAS files in the browser.
- Cancelling a queued message before it is sent.
