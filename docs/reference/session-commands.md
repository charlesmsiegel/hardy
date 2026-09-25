# Session commands

This page names every `/` command the prompt inside `hardy chat` accepts, checked against the command registry so it cannot drift; it is for anyone typing at that prompt who wants to know what a command takes and does before running it.

## Commands

`/help` lists these at the prompt itself, in this order: every built-in below, then any bundled prompt shortcuts still in effect, then any commands a project has added of its own.

| Command | Arguments | What it does |
| --- | --- | --- |
| `/help` | | Lists the commands. |
| `/model` | `[identity]` | Switches the live model, or opens a selector (arrow keys or a row number, Enter to choose, Esc to cancel). A typed identity is not checked against a row number from that list: pass the identity itself, or run `/model` with nothing to pick from the menu. |
| `/cas` | `[state\|reset\|export\|run <path>\|file <path>\|expr]` | Runs one cell, or a block, in the persistent computer algebra kernel the model's own tool calls use. Every cell is a file under `cas/`. See "/cas" below. |
| `/goal` | `[text]` | States what this session is for, or prints the current goal. Shown beside every request to approve an assumption, so nobody approves an axiom with the assignment off-screen. |
| `/assume` | `<paper-id> [ref ...]` | With no reference, lists a fetched paper's statements. Named references ask the model to assume exactly those statements, one call to `assume_statement` per statement, going through the same approval and independent read any lazily minted axiom does. The command itself mints nothing. |
| `/import` | `[<dir>\|lean\|reference\|tex]` | Triages an existing pile of files, or promotes one into the project. See "/import" below. |
| `/project` | `[list\|new\|switch\|publish\|link\|mark]` | Lists or opens the projects in this root, starts a new one, or assembles a local publication draft. See "/project" below. |
| `/status` | `[--full]` | Shows the project, model and paths, and the spend breakdown, all read from the workspace's own files. `--full` adds the workspace summary. See "/status --full" below. |
| `/prove` | `[claim]` | Stages one claim from statement to document, without leaving the session, exactly as [`hardy prove`](cli.md#hardy-prove) does on the command line: a formalization the user approves or revises, an independent faithfulness read of the frozen statement, a proof search, and an independent verifier before anything is graded. With no claim given, it asks for one. |
| `/export` | `[path]` | Writes one shareable HTML account of this session: results carry their own stored verdicts, not whatever the conversation around them claimed, and credentials matching known shapes are stripped from everything but the Lean, so it still hashes to what was checked. |
| `/doctor` | | Checks that Lean, LaTeX, the computer algebra backend and the model are usable, without leaving the session. |
| `/checkpoint` | `[name\|list\|restore <id>]` | Saves the whole workspace as it stands, lists the checkpoints taken, or puts one back in the problem's place. See "/checkpoint" below. |
| `/clear` | | Clears the screen. Deletes nothing: the scrollback, the transcript on disk and the model's conversation all continue. |
| `/tree` | | Shows the conversation's entries and which one is the active leaf, abandoned branches included. |
| `/fork` | `<entry-id\|root>` | Continues the conversation from a chosen entry, on a new branch. See "/fork" below. |
| `/abandon` | `<entry-id\|root> <lesson>` | Leaves a branch the way `/fork` does, and attaches the lesson given as a human note. See "/abandon" below. |
| `/delegate` | `<item-id> [--checks N] [--seconds S] [--mode M] [--hide ids] [objective]` | Starts one background worker on a ledger item (a stable id, or `id@digest`) and returns at once. The worker gets its own provider context and none of this conversation. It reserves `--checks` official Lean checks (default one) and `--seconds` of active time (default an equal share of what the session can still promise across its free slots) from the session's ceiling. The ceiling belongs to the sessions open on the problem: work that earlier sessions ran and released before this budget began, work interrupted by a crash included, does not count against it, and a detached computation's time never does. A session that opens when no other session on the same problem is live starts a fresh delegation budget, so reopening a problem after every session on it has closed does. A session that opens while another is live joins that session's budget instead: a second terminal, a browser tab, and a browser chat switch, which always opens the new chat's session before closing the one it leaves, so it never starts a fresh budget. Concurrent sessions share one delegation budget, and it is fresh again only once all of them have closed. This is the delegation budget alone; the spend ledger, which totals what the workspace has cost and is what a new conversation does not reset, is a separate record and keeps counting across sessions. A worker whose provider is still running when its active time is spent is cancelled and ends `exhausted`. `--mode` is the task mode (`prove`, `explore`, `refute`, `critique`, `verify`; anything else is refused), and the default objective is `<mode> <item-id>`; `--hide` names ledger items, comma-separated, the worker must neither be shown nor be able to retrieve, for an independent attempt. Its result arrives as a notice and, at the next turn, as a compact block the model reads before your message. |
| `/jobs` | `[tree\|<id>\|pin\|unpin\|pause\|resume\|reinforce\|finish\|handle\|subscribe\|continue]` | With nothing: lists background delegations, detached computations among them, with their state, the root budget and what this session has used of it, what detached computations used (reported apart, never charged), and every item still awaiting attention. `tree` draws them beneath their parents; `<delegation-id>` inspects one (lease, usage, attention, where its artifacts are). The controls are journaled and take effect at the scheduler's next decision: `pin <id> <min_attention\|forbid_spend\|reinforce\|reserve_exploration> [value]` and `unpin <id> <kind>`; `pause <id>` and `resume <id>` for queued work; `reinforce <id> <checks>` grants a tranche of official checks; `finish <id> <synthesis>` closes an interior node and cancels what still runs beneath it; `handle <attention-id>` marks an item dealt with; `subscribe <id> <trigger[,trigger]> <queue\|notify\|interrupt>` overrides how events from that delegation reach you (triggers are `terminal`, `finding`, a finding kind such as `counterexample`, `priority`, `decision`, `progress`; `interrupt` ends the running turn at the next safe boundary and is reachable only this way). `continue` resumes the conversation from a continuation an interrupt recorded, only if no message has been sent since: the resumed text is submitted as though you had typed it, through the ordinary turn path, so it streams and can be cancelled; it is the one form that starts a turn, so it refuses while one runs. |
| `/cancel` | `<delegation-id>` | Requests cancellation of a delegation and everything under it. Queued work is cancelled outright; an active worker stops at its next step. Nothing it produced is merged. |
| `/exit` | | Leaves the session. |
| `/quit` | | Same as `/exit`. |

### /cas

Every cell is a file under the problem's `cas/` directory, with the backend's suffix (`.py` for SymPy, `.sing` for Singular, `.m2` for Macaulay2). `/cas run <path>` runs the file at `<path>` again. `/cas file <path>` opens a multi-line block, ended by a line reading `/end`, writes it to `<path>` and runs it. `/cas <source>` runs one typed cell, and a bare `/cas` opens a block for one; a typed cell is filed as `cas/typed/NNNN.<suffix>` with the next free number, so it is a file like any other. `/cas state` reports the backend, the kernel, the segment, how much time has been spent and how much of this process's budget is left, and the cells accepted so far, each with the file it ran from. `/cas reset` starts a clean kernel. `/cas export` writes a script and a notebook holding the current segment's accepted cells (a cell that failed, one that was interrupted, and anything from before a reset are left out), each naming its file, and replays them to check they reproduce.

A cell typed at `/cas` goes into the same log, under the same lock, as a cell the model runs, and is replayed and exported exactly like one of those. Because it is the one locked kernel a model tool call may already be using, `/cas` is refused while a turn or another command is running; a cell a detached background job is running holds the same lock, and a `/cas` sent meanwhile waits for it.

### /checkpoint

`/checkpoint [name]` copies the problem's whole directory as it stands, with an optional name, to `<root>/.hardy/checkpoints/<slug>/<id>/`: the record, every chat's transcript, `lean/`, `tex/`, `cas/` with its files and journal, the ledger, the delegation journal, the machine-local state that carries the provider thread, and the build cache. The scratch trees an export empties and the writer leases are left out; a symbolic link anywhere in the tree refuses the checkpoint rather than being followed or dropped. The session stays open. `/checkpoint list` names every checkpoint of this problem, oldest first. `/checkpoint restore <id>` asks, checkpoints what it is about to replace under a name saying so, closes the session, swaps the tree for the checkpoint's, and reopens the same problem the way `/project switch` reopens another: the computer algebra kernel's namespace is rebuilt from the journal's accepted cells rather than carried, and the first cell afterwards says so. A checkpoint is one machine's copy and not a commit; it is ignored by the tooling directory's own `.gitignore`.

### /import

`/import <directory>` triages a pile of files: it compiles every Lean file in it and writes nothing. `/import lean <file> [dest]`, `/import reference <file> [dest]` and `/import tex <file> [dest]` each promote one file into the project instead, through the same gates, audit and record an authored save gets, and record it as having arrived from outside, under its digest. There is deliberately no model tool for either triage or promotion: pulling an existing host file into the audited tree is the user's judgment call, not one the model makes for itself.

### /project

`/project`, or `/project list`, lists the problems in this root and marks the active one. `/project new <name>` starts a project; `/project switch <name>` opens one already there. Switching is a reopen, not a restart of the session: the process, the pinned Lake project and the Mathlib environment behind the search tools all survive it.

`/project publish`, `/project link` and `/project mark` assemble and record a local publication draft from items already in the workspace. Their selector syntax, required options and what each verb refuses are documented once, in [Project publication commands](cli.md#project-publication-commands), rather than twice.

### /status --full

Plain `/status` reads the model, the spend ledger and the workspace's paths from the artifacts themselves, not from anything the conversation claims. `--full` adds a summary of the workspace: the goal, the standing assumptions with their provenance, every saved theorem under the verdict its own stored audit record gives it, what failed, what is still open, the naming registry, and what is left before anything here may be reported as finished. Because it is drawn from the same files rather than from the model's account of them, it can disagree with the conversation, which is the reason it is worth printing.

### /fork

`/fork <entry-id|root>` opens a new branch from a chosen conversation entry (or from the empty root), building a fresh runtime out of exactly the visible history of the entry selected rather than inferring anything from provider state the earlier branch held. Replaying that history into the new branch is bounded to 1 MiB. Spend accrues across every branch a session has, not only the one currently active. The mathematical workspace, meaning the Lean tree, the saved theorems and the record, is shared by every branch and is unchanged by forking: only the conversation branches.

### /abandon

`/abandon <entry-id|root> <lesson>` leaves a branch the same way `/fork` does, and records the lesson given alongside it as attributed human text, not as verification evidence that the abandoned reasoning was checked or wrong. `/tree` marks an abandoned branch beside the active one, so what was tried is not lost from the record.

## Commands that work while a turn is running

A plain message typed while a turn or a command runs is not refused: it is queued, in the order typed, and sent as the next turn the moment what is running ends, several lines joined into one message. Nothing is written to the transcript until then, so the record's order is the order the model saw. A slash command is different, because a command takes the session over and cannot wait without changing what it means: every command below is refused while a turn, or another command, is already running, except the commands that read or that leave, which are allowed to run alongside one:

- `/help`
- `/status` (`/status --full` included)
- `/clear`
- `/tree`
- `/jobs`
- `/cancel`
- `/exit`
- `/quit`

A Lean check or save, a LaTeX check or save, or a computer algebra cell that runs longer than `compute_detach_seconds` ([configuration](configuration.md)) no longer holds the turn: it is detached into a background job listed by `/jobs` and cancellable with `/cancel`, the turn goes on without it, and its result reaches the model ahead of its next turn. While a job runs, the browser editor's save and check are refused, as they are during a turn, because the job is still writing the same Lean tree and build cache. When such a job ends with nothing else running, Hardy starts a turn of its own, drawn as Hardy's line, so the result is acted on; a line queued meanwhile goes first.

## Prompt shortcuts

Four prompt shortcuts ship with every session. Sending one expands it into an ordinary message: the expanded text, not the `/name`, is what reaches the model and what is written into the transcript, so a transcript shared with someone who lacks the shortcut still says what was actually asked. None of them is a workflow invocation or verification evidence by itself; each is convenience for a repeatable ask that is worth keeping next to the project rather than retyping.

| Shortcut | Arguments | Asks for |
| --- | --- | --- |
| `/audit` | `[selection]` | An audit of the workspace, or of the given selection: exact statements and scopes, proof status, remaining holes, assumptions, citation obligations and stale evidence, distinguishing kernel checks from heuristic review. |
| `/formalize` | `<claim>` | A faithful formal statement for the given claim, preserving its identity, scope, quantifiers and hypotheses, with interpretation choices and unresolved ambiguities listed. |
| `/publish` | `<selection>` | A local publication draft for the given selection, keeping verified and conjectural material visibly distinct and disclosing what the draft still needs. This is the prompt shortcut; the command that actually writes a draft bundle is `/project publish`, in [Project publication commands](cli.md#project-publication-commands). |
| `/restyle` | `<style instructions>` | A prose revision under the given style instructions, preserving the mathematics, the exact claims, hypotheses, scope, notation bindings, citations and proof-status disclosures. |

A project's own template of the same name replaces one of these four for that project; operational command names (`exit`, `status`, and the rest of the built-ins above) stay reserved and cannot be replaced this way. See "Your own commands" below.

## Your own commands

A project keeps its repeatable asks beside itself, in `.hardy/prompts/`. Each `<name>.md` there becomes `/<name>`: optional frontmatter supplies `description` (what `/help` shows) and `argument-hint`, and the body is the message that gets sent.

`$1`, `$2`, and so on are the words typed after the command, tokenized so that a mathematician's argument survives: backslashes are kept rather than read as escapes, so `\forall` or a Windows path comes through intact, and quoting still groups one argument. `$@` is everything typed, together. `$$` is a literal dollar, so a body can carry LaTeX such as `$x + y$` untouched. A placeholder with nothing to fill it is a refusal rather than an empty string: a template that quietly lost half its sentence would otherwise look entirely ordinary.

A template file is read only up to 64 KiB; past that it is not loaded, on the grounds that a prompt template is a paragraph or two and anything larger is not meant to be sent whole. A name must use lower-case letters, digits, hyphens and underscores, starting with a letter or digit. A file named for a built-in command such as `exit` or `status` is refused rather than allowed to shadow it, so a checked-in file can never change what an operational command does; a file named for one of the four bundled shortcuts above is not refused, and instead replaces that shortcut's default for this project.

The expansion is what gets recorded, never the `/name`: a shared transcript that said `/audit` instead would refer to a file its reader might not have. Nothing under `.hardy/prompts/` is read through a symbolic link, and nothing that is not an ordinary file is read at all, because a template's body is sent to the model: reading through a link could turn a command into one that sends a host credential file.

## Keys

| Key | What it does |
| --- | --- |
| Enter | Submits the line. |
| Shift+Enter | Starts a new line without submitting. A terminal that does not send that sequence can end a line with a trailing `\` instead. |
| Tab | Accepts the dim inline suggestion for the rest of a likely command name; it fills in the text and does not run the command. |
| Esc | Cancels a turn in flight: the model stops, no further tool call runs, and the Lean, LaTeX or computer algebra process it started is interrupted rather than left running to its own timeout. Pressed again, a second Esc stops waiting and kills whatever had not stopped, at the cost of that child's state, such as a computer algebra kernel's namespace. Against a command that owns a child of its own, such as a running `/cas` cell or a `/prove` run, Esc stops that instead, with the same two presses. |
| Ctrl+C | Once, while a turn is running, only warns. A second Ctrl+C leaves at once, at the cost of whatever that turn was still doing, and may orphan a Lean, LaTeX or computer algebra process it started. With no turn running, Ctrl+C leaves the session immediately. |
| Ctrl+D | Leaves the session, the same as `/exit` or `/quit`. |

## In the browser

`hardy web` runs this same registry: every command above, the prompt shortcuts and a project's own commands work at the browser's composer exactly as at the prompt, and are refused while a turn or a command runs by the same rule; a plain message sent meanwhile is queued, and the composer says how many wait. Typing `/mo` lists the commands it could be, and Tab or Enter completes the one chosen. The Help panel lists every command in this page's tables, the prompt shortcuts, the project's own commands and the page's keys, read from the same registry; clicking a command puts it in the composer rather than sending it. The header carries a picker over the rows `/model` offers; choosing one submits `/model <identity>` like a typed line, so the switch is recorded and refused mid-turn exactly as the command is. A selector, a line prompt, or a yes/no question, including the request to approve an assumption, appears as a card in the conversation; Esc dismisses it, which is a decline. Esc against a running turn stops it, and a second Esc kills what has not stopped, as at the terminal. `/exit` does not stop the server: Ctrl+C where `hardy web` runs does.
