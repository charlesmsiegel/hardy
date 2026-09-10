# Session commands

This page names every `/` command the prompt inside `hardy chat` accepts, checked against the command registry so it cannot drift; it is for anyone typing at that prompt who wants to know what a command takes and does before running it.

## Commands

`/help` lists these at the prompt itself, in this order: every built-in below, then any bundled prompt shortcuts still in effect, then any commands a project has added of its own.

| Command | Arguments | What it does |
| --- | --- | --- |
| `/help` | | Lists the commands. |
| `/model` | `[identity]` | Switches the live model, or opens a selector (arrow keys or a row number, Enter to choose, Esc to cancel). A typed identity is not checked against a row number from that list: pass the identity itself, or run `/model` with nothing to pick from the menu. |
| `/cas` | `[state\|reset\|export\|expr]` | Runs one cell, or a block, in the persistent computer algebra kernel the model's own tool calls use. See "/cas" below. |
| `/goal` | `[text]` | States what this session is for, or prints the current goal. Shown beside every request to approve an assumption, so nobody approves an axiom with the assignment off-screen. |
| `/assume` | `<paper-id> [ref ...]` | With no reference, lists a fetched paper's statements. Named references ask the model to assume exactly those statements, one call to `assume_statement` per statement, going through the same approval and independent read any lazily minted axiom does. The command itself mints nothing. |
| `/import` | `[<dir>\|lean\|reference\|tex]` | Triages an existing pile of files, or promotes one into the project. See "/import" below. |
| `/project` | `[list\|new\|switch\|publish\|link\|mark]` | Lists or opens the projects in this root, starts a new one, or assembles a local publication draft. See "/project" below. |
| `/status` | `[--full]` | Shows the project, model and paths, and the spend breakdown, all read from the workspace's own files. `--full` adds the workspace summary. See "/status --full" below. |
| `/prove` | `[claim]` | Stages one claim from statement to document, without leaving the session, exactly as [`hardy prove`](cli.md#hardy-prove) does on the command line: a formalization the user approves or revises, an independent faithfulness read of the frozen statement, a proof search, and an independent verifier before anything is graded. With no claim given, it asks for one. |
| `/export` | `[path]` | Writes one shareable HTML account of this session: results carry their own stored verdicts, not whatever the conversation around them claimed, and credentials matching known shapes are stripped from everything but the Lean, so it still hashes to what was checked. |
| `/doctor` | | Checks that Lean, LaTeX, the computer algebra backend and the model are usable, without leaving the session. |
| `/clear` | | Clears the screen. Deletes nothing: the scrollback, the transcript on disk and the model's conversation all continue. |
| `/tree` | | Shows the conversation's entries and which one is the active leaf, abandoned branches included. |
| `/fork` | `<entry-id\|root>` | Continues the conversation from a chosen entry, on a new branch. See "/fork" below. |
| `/abandon` | `<entry-id\|root> <lesson>` | Leaves a branch the way `/fork` does, and attaches the lesson given as a human note. See "/abandon" below. |
| `/exit` | | Leaves the session. |
| `/quit` | | Same as `/exit`. |

### /cas

`/cas <source>` runs one cell. A bare `/cas` opens a multi-line block, ended by a line reading `/end`. `/cas state` reports the backend, the kernel, the segment, how much time has been spent and how much of this process's budget is left, and the cells accepted so far. `/cas reset` starts a clean kernel. `/cas export` writes a script and a notebook holding the current segment's accepted cells (a cell that failed, one that was interrupted, and anything from before a reset are left out) and replays them to check they reproduce.

A cell typed at `/cas` goes into the same log, under the same lock, as a cell the model runs, and is replayed and exported exactly like one of those. Because it is the one locked kernel a model tool call may already be using, `/cas` is refused while a turn or another command is running.

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

Everything else is refused while a turn, or another command, is already running, because a running turn owns the record, the transcript, and the one locked computer algebra kernel. Only the commands that read or that leave are allowed to run alongside one:

- `/help`
- `/status` (`/status --full` included)
- `/clear`
- `/tree`
- `/exit`
- `/quit`

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
