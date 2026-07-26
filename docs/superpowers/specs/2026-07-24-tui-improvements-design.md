# A Claude-Code-shaped terminal for Hardy

## Problem

Hardy's interactive session is the primary experience — `main()` falls through to
it when no subcommand is given (`cli.py:561`) — but it is twenty lines of
`input()` and `print()`.

- `cli.py:128-142` is the whole loop. Two commands exist: `/exit`/`/quit` and
  `/model`. Adding a third means adding another `startswith` branch.
- There is no completion of any kind. A user who types `/mo` gets their text sent
  to the model as a mathematical claim.
- `/model` prints a numbered list and blocks on `input()` (`cli.py:63-85`). It
  reads as a shell script, not as a selector.
- Every turn is labelled `you> ` and `hardy> ` (`cli.py:130`, `cli.py:142`). The
  input line is indistinguishable from the transcript, so there is no sense of an
  input area at all.
- There are no terminal dependencies, so none of the above has any machinery to
  build on.

Claude Code's coding harness is the stated northstar for how this should feel.

## Goals

Bring the interactive session to that standard on three axes:

1. **Ghost-text completion of slash commands**, so a growing command namespace is
   discoverable by typing rather than by memory.
2. **An interactive `/model` selector** with a pointer and arrow keys, near
   enough to Claude Code's that the muscle memory transfers.
3. **A cordoned input area** — a bordered box that owns the bottom of the screen
   — with past turns marked `>` for the user and `●` for Hardy, retiring the
   `you>`/`hardy>` labels.

## Non-goals

| Excluded | Why |
|---|---|
| Token streaming | `ChatRuntime.ask(text) -> str` (`chat.py:39`) is blocking. Making it stream reaches into `claude_runtime` and `codex_runtime`, well outside the terminal layer. Tracked as its own issue. |
| True interruption of a running turn | Depends on a cancellable runtime, which arrives with streaming. See *Turn lifecycle* for what Esc honestly does instead. |
| Migrating `ConsoleTerminal` (`cli.py:153`) | The staged `prove` workflow keeps its blocking prompts. It implements a different protocol; moving it onto `Ui` is a clean follow-up. |
| `/prove` inside the session | Would drag `ConsoleTerminal` into this pass. |
| Rich, Textual, or a JS/bun frontend | `prompt_toolkit` styles its own output; one dependency is enough. Textual owns the whole screen, which would cost native scrollback. A bun frontend means two runtimes and IPC on a Windows-first, no-WSL project. |
| A full-screen application | The transcript stays in the terminal's native scrollback — selectable, copyable, surviving exit. Only the input box is redrawn in place. |

## Decisions

| Question | Decision |
|---|---|
| Terminal library | `prompt_toolkit` >= 3.0.50, one new pure-Python runtime dependency |
| Screen model | Non-full-screen `Application` at the bottom; transcript printed above into native scrollback via `patch_stdout()` |
| Where the shell lives | A new `src/hardy/tui/` package; `cli.py::_chat` shrinks to wiring |
| How commands reach the terminal | A narrow `Ui` protocol. Only `tui/shell.py` and `tui/select.py` import `prompt_toolkit` |
| Ghost text trigger | Buffer starts with `/`, is a single line, cursor at end |
| Accepting a suggestion | Tab, Right, or End completes the text. It does **not** run. Enter runs |
| Ambiguous prefix | No ghost text; Tab opens a completion menu over the matches |
| `/model` row selection | Arrow keys move a `❯` pointer; number keys **1-9 only** select immediately; Enter selects; Esc cancels |
| Unlisted model identities | An `Other…` row prompts for a literal identity, preserving today's escape hatch (`catalog.py:38`). An unlisted *current* model also gets its own row |
| Newline in the input box | **Shift+Enter**, plus a trailing `\`. No Alt+Enter — see below |
| Not a TTY | Fall back to today's `input()`/`print()` loop, same command registry |
| Asking the human anything | `Ui`'s prompting methods are coroutines; command handlers are `async` and await them on the live event loop |
| Prompts raised from tool threads | `Ui.from_thread` is a sync facade using `run_coroutine_threadsafe`; the calling tool thread blocks for the answer |
| Command aliases | No alias field. `/quit` is its own registry entry with `alias_of="exit"`, so completion never has to bridge a name mismatch |
| The unsandboxed-execution warning | Printed unconditionally at startup on both paths, asserted by a test |
| A `/command` that does not resolve | An inline error. Never sent to the model; a leading space escapes command interpretation |
| Commands during an in-flight turn | Each `Command` declares whether it is safe in flight; undeclared defaults to refused. `/model` and `/doctor` are refused |
| Forcing an exit past a stalled turn | `record_abandonment` then `os._exit(130)`, with the orphaned-subprocess cost stated to the user first |
| Ctrl+C during a turn | First press refuses and explains; second press force-exits with a warning that artifacts may be incomplete |
| Test seam | A `ScriptedUi` double replaces `model_command`'s `ask=`/`out=` parameters |

## Architecture

### The `Ui` port

The load-bearing boundary. Command handlers and `model_command` depend on this
and nothing else, so they stay headless and testable.

```python
@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    note: str = ""

class Ui(Protocol):
    """Every prompting method is a coroutine. See Concurrency for why."""

    def write(self, text: str, *, style: str = "system") -> None: ...

    async def choose(self, title: str, rows: Sequence[Choice], *, current: int = 0,
                     subtitle: str = "") -> Choice | None: ...   # None = cancelled
    async def ask_line(self, prompt: str) -> str | None: ...     # None = cancelled
    async def confirm(self, question: str) -> bool: ...

    @property
    def from_thread(self) -> BlockingUi: ...   # sync facade for foreign threads

class BlockingUi(Protocol):
    """The same operations, synchronous, callable only off the UI thread."""

    def write(self, text: str, *, style: str = "system") -> None: ...
    def choose(self, title: str, rows: Sequence[Choice], *, current: int = 0,
               subtitle: str = "") -> Choice | None: ...
    def ask_line(self, prompt: str) -> str | None: ...
    def confirm(self, question: str) -> bool: ...
```

Three implementations: `PromptToolkitUi` (in `tui/shell.py`), `PlainUi` (in
`tui/plain.py`), and `ScriptedUi` (in `tests/`).

**The prompting methods are coroutines, and that is a load-bearing choice rather
than a stylistic one.** A selector has to read arrow keys, which arrive on the
application's event loop; anything that blocks that loop while waiting for them
deadlocks by construction. Awaiting a nested `Application.run_async()` on the
same loop is the only shape that works for a caller already running there — which
every command handler is. *Concurrency* works through the failure this avoids.

`from_thread` is the **sync facade over that async core**, for callers on a
foreign thread that cannot await: it schedules the coroutine with
`asyncio.run_coroutine_threadsafe` and blocks the calling thread on the returned
future. The SDK's tool threads need exactly this. It raises if called from the UI
thread, where `await` is both available and required; `PlainUi` and `ScriptedUi`,
having no event loop, return a facade that simply calls through.

`write` stays synchronous in both surfaces. It never waits on a human, so it has
nothing to await.

### Module split

| Module | Responsibility | Imports `prompt_toolkit` |
|---|---|---|
| `tui/commands.py` | The registry: `Command`, `resolve()`, `complete()`, `suggest()`. Decides *what* to suggest | No |
| `tui/transcript.py` | Message → styled lines. The `>` and `●` prefixes, continuation indent, width-aware wrapping | No |
| `tui/select.py` | The inline list widget: `❯` pointer, arrows, number accelerators, Esc | Yes |
| `tui/shell.py` | The `Application`: bordered growing box, hint line, key bindings, `patch_stdout` coordination, `PromptToolkitUi` | Yes |
| `tui/plain.py` | Non-TTY fallback loop and `PlainUi` | No |
| `tui/__init__.py` | Exports `run_session(config, session, *, plain=False) -> int` | No |

Separating `commands.py` from `shell.py` is what makes the completion *logic*
(`/m` → `/model`, ambiguity, unknown commands, case-insensitivity) testable as
pure functions, independently of whether dim text renders.

### `tui/commands.py`

```python
@dataclass(frozen=True)
class Command:
    name: str                        # without the slash
    summary: str                     # shown in the menu and by /help
    handler: Callable[[Ui, str, State], Awaitable[State]]
    argument_hint: str = ""          # e.g. "[identity]"
    alias_of: str | None = None      # e.g. "exit" on the quit entry
    safe_in_flight: bool = False     # may run while a turn is still running
```

`safe_in_flight` defaults to `False` so that a command added later is refused
during an in-flight turn until someone has thought about it. See *What is
permitted while a turn is in flight* for why the default matters.

**Handlers are coroutines**, because an interactive handler must await
`ui.choose()` on the event loop it was called from. See *Concurrency*.

**Every name is a real registry entry — there is no alias-matching.** `/quit` is
its own `Command` carrying `alias_of="exit"` and sharing `/exit`'s handler.
`/help` lists canonical entries and mentions their aliases alongside, rather than
listing both as peers.

That is deliberate, and it exists to kill a bug rather than to save a field. If
aliases were a `tuple[str, ...]` on the canonical command, a prefix matching only
an alias would have nothing coherent to complete: `/q` matches `exit` through
`quit`, but `exit` does not start with `q`, so appending the canonical tail
renders `/qxit` and returning nothing contradicts aliases being completable.
Making each name its own entry means every string `suggest()` can match is a
string the user is literally typing, so the whole mismatch cannot arise.

`State` carries the mutable session context a handler may replace — the
`Config`, the `MathematicsSession`, and a `done: bool` for `/exit`. Handlers
return a new `State`, so `/model` updating the config stays a value-returning
operation as it is today (`cli.py:74`).

Pure query functions over the registry. All three take the raw buffer text
**including the leading slash**, so the caller never has to strip it:

- `resolve(text) -> tuple[Command, str] | None` — splits `/name rest`, matches
  case-insensitively, returns the command and the remaining argument text; `None`
  for an unknown name.
- `complete(text) -> list[Command]` — every entry whose name starts with the
  typed prefix, in registry order.
- `suggest(text) -> str` — the characters to render as ghost text: the tail of
  the single matching name, otherwise `""`. `suggest("/mo")` is `"del"`;
  `suggest("/q")` is `"uit"`; `suggest("/")` and `suggest("/x")` are both `""`.
  It only ever *appends*, never rewrites what was typed.

### The command set

Every one is backed by code that already exists; this is wiring, not new
features.

| Command | Behaviour |
|---|---|
| `/help` | The registry as a table: name, argument hint, summary |
| `/model [identity]` | Bare, opens the selector. With an argument, switches directly, as today |
| `/status` | Workspace, model, Lean project, config path, transcript location |
| `/doctor` | Runs `doctor.run_checks(config)` and writes the report inline |
| `/clear` | Scrolls the conversation out of view. Deletes nothing — see below |
| `/exit` | Leaves the session. `/quit` is a separate entry with `alias_of="exit"` |

`/clear` deserves care, because the native-scrollback decision means Hardy does
not own the lines it has printed. It cannot delete only the conversation:
`ESC[2J` clears the viewport but leaves the transcript reachable by scrolling,
and `ESC[3J` erases the scrollback buffer including whatever unrelated shell
output preceded Hardy. Destroying a user's terminal history is not ours to do.
So `/clear` is defined as **viewport-only**: it clears the visible screen and
reprints the input box. Nothing is removed from scrollback, nothing is removed
from `transcript.jsonl`, and the model's conversation is untouched. `/help` and
the command's own summary must say so, because a `/clear` that silently implied
a reset of any of those three would be the dishonest kind of convenience.

## Behaviour

### Startup

`_chat` prints five lines before its loop today (`cli.py:123-127`), and one of
them is not decoration:

```
WARNING: Generated Lean is not sandboxed. Run Hardy only with trusted output in
a disposable development environment. LaTeX is also executed without isolation.
```

`AGENTS.md:21-23` makes the missing sandbox a standing disclosure — generated
Lean, TeX, and helper processes must never be described as safe — and this is the
only in-session notice a user gets before the model executes code on their
machine. A rework that describes `_chat` as "becoming wiring" could silently drop
it, which is why the banner is specified here rather than left to the
implementer.

`run_session` emits, in both the TTY and plain paths, before the first prompt:

| Line | Content | Style |
|---|---|---|
| 1 | `Hardy — interactive mathematics workspace` | Normal |
| 2 | Workspace, active model, and that it runs on the Claude Code subscription | Dim |
| 3 | Lean project, or `current directory` | Dim |
| 4 | **The unsandboxed-execution warning, in full** | Warning style, never dim |
| 5 | `/help` for commands · `/exit` to leave · transcript and artifacts are saved as you work | Dim |

Line 4 is not conditional on anything: not on `--plain`, not on terminal width,
not on a quiet flag, and it is never abbreviated to fit. If the terminal is too
narrow it wraps. A test asserts its presence on **both** startup paths, so
deleting it fails the suite rather than merely reading badly. Line 5 replaces
today's `/model`-centric hint now that a registry exists to point at.

### The input box

```
> is the golden ratio irrational?

● Yes — φ = (1+√5)/2 is irrational. The Lean statement
  I checked elaborates:

    theorem golden_ratio_irrational :
      Irrational goldenRatio

╭─────────────────────────────────────────────────────╮
│ > show that for all n : ℕ,                          │
│   ∑ i in range n, i = n * (n - 1) / 2█              │
╰─────────────────────────────────────────────────────╯
  / for commands · shift+enter for newline
```

- **The box grows with the input up to a maximum, then scrolls with a
  scrollbar.** It starts one line tall and grows as lines are added, so a short
  claim gets a short box. Past the maximum it stops growing and scrolls
  internally, keeping the cursor visible, with a scrollbar showing where in the
  buffer you are. Without a cap, pasting a long Lean snippet would push the
  transcript off the screen entirely.
- **The maximum is `min(12, max(3, rows // 3))`** where `rows` is the terminal
  height: about a third of the screen, never more than twelve lines, never
  fewer than three. A fraction rather than a constant because an 80×24 terminal
  and a tall one want different answers; the ceiling stops the box swallowing a
  large screen; the floor keeps it usable on a small one. Because it is derived
  from terminal height it must be **recomputed on resize**, not captured once.
- **Newline is Shift+Enter, and making that work takes one deliberate step.**
  `prompt_toolkit`'s sequence table maps `\x1b[27;2;13~` (Shift+Enter),
  `\x1b[27;5;13~` and `\x1b[27;6;13~` all onto `Keys.ControlM` — the same key as
  plain Enter (`input/ansi_escape_sequences.py:129-131`) — so out of the box the
  library discards the distinction before any binding sees it. The shell extends
  that table to route the Shift+Enter sequences to a key of their own and binds
  newline to it. A trailing `\` also continues a line, and is the fallback in
  terminals that never emit those sequences.
- **Alt+Enter is deliberately not bound**, and dropping it buys something. At the
  wire level Alt+Enter *is* Escape+Enter, so binding it forces the plain
  `escape` binding to be non-eager, which opens a ~1.5 s ambiguous-key window
  (`ttimeoutlen` 0.5 + `timeoutlen` 1.0). In that window Escape followed by `/`
  is swallowed as Emacs `M-/` (`emacs.py:300`) — so pressing Esc and then quickly
  typing `/status` loses the slash and submits `status`. With no Escape-prefixed
  chord, `escape` is bound `eager=True`: Esc responds instantly and that
  collision cannot occur.
- The hint line is dim: keys on the left, active model on the right.
- Below 40 columns the border is dropped for a bare `> ` prompt rather than
  wrapping a broken box.
- **Resize must reflow the box without corrupting the transcript above it.**
  Round one of the visual spike found this badly broken with a bordered
  non-full-screen layout, and it is unfixed. Task 9 owns it, and it is bound up
  with the maximum height above since that value changes on resize. If reflow
  cannot be made correct with the border, the border is what goes — a bare `> `
  prompt with a rule above it, as the sub-40-column case already does.
- History is navigated with Up/Down **only when the buffer is a single line**;
  otherwise those keys move the cursor between lines. History persists to
  `<workspace>/input-history` through `FileHistory`.

### Ghost text

Typing `/mo` renders `/mo` followed by a dim `del`, completing to `/model`. Tab,
Right, or End accepts it, leaving `/model` in the buffer for Enter to run.

`/` alone opens the full command menu, which lists canonical entries only —
alias entries would double its length while teaching nothing. A prefix matching
several commands shows no ghost text — guessing would be worse than silence — and
Tab opens a menu over the matches. Once a command name is complete and followed
by a space, the hint line shows its `argument_hint`.

Alias entries do ghost-complete, because they are ordinary registry entries:
`/q` renders a dim `uit`, giving `/quit`. The suggestion always continues what
was typed rather than replacing it, so no keystroke ever appears to be
retroactively rewritten.

### The `/model` selector

```
  Select model
  Runs through your Claude Code subscription.

❯ 1. claude-opus-5      strongest reasoning; 1M context   (current)
  2. claude-opus-4-8    previous Opus; 1M context
  3. claude-sonnet-5    near-Opus quality at lower cost
  4. claude-haiku-4-5   fastest and cheapest; 200K context
  5. Other…             type an identity the catalog lacks

  ↑↓ move · 1-5 jump · enter select · esc cancel
```

Rows come from `catalog.available()` and their notes come from the catalog
entries.

**An unlisted current model gets a row of its own.** Unlisted identities are
explicitly supported (`catalog.py:38-40` invents an entry for them), so
`config.model` may name something absent from `catalog.available()` — in which
case no catalog row represents the active model, the pointer has nowhere correct
to start, and the user cannot see what is running. The selector therefore
prepends the active identity as its own row, marked `(current, not in catalog)`,
whenever it does not appear in the catalog. Only then does the pointer start on
the current model.

Number keys **1-9** select immediately. Row 10 and beyond are reachable by
arrows only: an accelerator that fires on each keypress can never read a
two-digit row, because `1` would have already selected row 1 before `0` arrived.
The catalog holds four entries today, so this is a trap being removed rather
than a bug being fixed.

`Other…` calls `ui.ask_line()`. **Whitespace-only input cancels** rather than
switching — today a blank answer keeps the current model (`cli.py:89-90`), and
passing `""` through `catalog.describe()` into `session.switch_model()` would
build a runtime around an empty identity.

The sequence after a selection is unchanged from today, only re-rendered:
`session.switch_model()` first, and on `RuntimeError` the config is returned
untouched with the error written out — a failed switch must never leave the
session announcing a model it cannot use (`cli.py:99-104`). Then
`ui.confirm("Save as the default in <path>?")`, which is now a two-row Yes/No
selector defaulting to No rather than a `[y/N]` line.

**What Esc does depends on when it is pressed, and the spec will not overstate
it.** Esc *before* a row is chosen cancels outright and changes nothing. Esc at
the save-default confirmation does **not** roll back: by then
`session.switch_model()` has already rebuilt the runtime, written the new
provenance into `session.json`, and recorded a `model`/`switched` event in the
transcript (`chat.py:100-103`). Declining at that point declines writing the
*config file* — the live session has moved and stays moved. No tri-state result
and no rollback: the honest fix here is a narrower promise, not an undo
mechanism, and `/status` will show which model is live.

### Transcript rendering

| Kind | Rendering |
|---|---|
| User turn | `> ` then the text; continuation lines indented two spaces |
| Hardy turn | `● ` then the text; continuation lines indented two spaces |
| System notice | Dim, indented two spaces, no glyph |
| Error notice | Dim red, indented two spaces, no glyph |

A blank line separates turns. Wrapping respects the terminal width and the
two-space continuation indent. Lines already committed to scrollback are never
reflowed on resize — that is the terminal's business, and rewriting scrollback is
what would break copy-paste.

### Dispatch on submit

What Enter does depends on the first character, and the unresolved case is the
one that matters — it is the defect this document opens with.

| Submitted text | Result |
|---|---|
| Does not begin with `/` | Sent to the model as a turn |
| Begins with `/` and resolves | The command's handler is awaited |
| **Begins with `/` and does not resolve** | **An error notice. Never sent to the model** |

`resolve()` returning `None` is an error, not a fall-through. Typing `/mo` and
pressing Enter without accepting the suggestion writes `unknown command /mo —
press Tab to complete, or /help for the list` and leaves the text in the buffer to
be corrected. Sending it onward is the exact behaviour the Problem section
identifies: an incomplete command silently reinterpreted as a mathematical claim.
The registry test for unknown names is not enough to guarantee this, since it only
exercises the pure resolver — so an end-to-end test asserts that slash-prefixed
input which does not resolve never reaches `session.send`.

A leading `/` is therefore reserved. Ordinary mathematical text has no reason to
start with one — LaTeX uses a backslash — but text that genuinely must can be
submitted with a leading space, which suppresses command interpretation.

### Turn lifecycle

1. Enter submits. The user's text is echoed above the box through
   `transcript.user_lines()` and the box is cleared and disabled.
2. The hint line becomes a spinner with elapsed seconds and `esc to stop
   waiting`.
3. `session.send(text)` runs on a worker thread so the `Application` stays
   responsive.
4. The reply is printed above the box and the box is re-enabled.

**Esc does not cancel.** `session.send` is not cancellable, and its tool calls
may already have written Lean or LaTeX into the workspace, so claiming a
cancellation would be a lie. Esc returns the prompt with a dim notice — `stopped
waiting; the call is still running and its reply will appear when it lands` —
and the reply, when it arrives, is printed above the box tagged as belonging to
the abandoned turn. Nothing is silently dropped. Real cancellation arrives with
streaming.

**An abandoned turn is recorded, not just annotated.** A dim notice on screen
dies with the session; `transcript.jsonl` is what replay and evaluation read, and
`MathematicsSession.send` records only the ordinary user and provider events
(`chat.py:236`, `chat.py:256`). Without an explicit event, a turn the user walked
away from replays as one they waited for — exactly the kind of quiet
prettification this project forbids. `MathematicsSession` therefore grows one
operation, `record_abandonment(reason)`, appending a `{"type": "turn",
"status": "abandoned"}` event through the existing `_record` (`chat.py:162`), and
the shell calls it when Esc is pressed. This is the one place the rework reaches
past the terminal layer into `chat.py`, and it earns that reach: the TUI is what
introduces the abandonable turn, so the TUI owns keeping the record true.

### What is permitted while a turn is in flight

Abandoning a turn hands the prompt back while the worker is still running, so the
shell is briefly interactive over a session that is being mutated by another
thread. Refusing further *mathematical* submissions is not sufficient — the
dangerous input is a command.

`/model` is the case that must be refused. `switch_model` replaces
`self.runtime` (`chat.py:100`), but the abandoned `send()` still holds a `finally`
that calls `_remember_thread()` (`chat.py:239-242`), which reads
`self.runtime.session_id` and writes it into `state["provider_session"]`
(`chat.py:259-263`). Switch mid-turn and that `finally` stamps the **new** model's
provider session onto the turn the **old** model answered, while the transcript
already carries the switch event. Replay would then attribute one model's work to
another — precisely the identity confusion `AGENTS.md:29-31` exists to prevent.
There is a second hazard in the same window: `switch_model` mutates `self.state`
and rewrites `session.json` without taking `self._gate`, which serialises tool
calls against each other but not against a model switch, so it can interleave
with a tool call doing the same.

So an in-flight turn narrows the shell to what cannot touch session state:

| Input | While a turn is in flight |
|---|---|
| A mathematical submission | Refused with a dim notice; never queued |
| `/status`, `/help`, `/clear` | Allowed — read-only or display-only |
| `/model` | **Refused** with a notice naming the turn as the reason |
| `/doctor` | Refused — it spawns subprocesses and competes for the toolchain |
| `/exit` | Allowed; waits for the boundary and reports that it is waiting |

Two obligations follow, and the implementation plan must carry both. Every
`Command` declares whether it is safe in flight, so the gate is a property of the
registry rather than a list maintained somewhere else — a command added later
without that declaration defaults to **refused**, failing safe. And a test
asserts that a turn in flight refuses `/model`, because this failure is silent:
nothing crashes, the transcript simply becomes a false account.

Ctrl+C during an in-flight turn is covered under *Concurrency*.

## Concurrency

A human can be asked a question from two very different places — a command
handler and an SDK tool thread — and the two need opposite mechanisms. Getting
this wrong deadlocks the terminal, so it is specified before the behaviour that
depends on it.

| Context | Runs | Reaches the human by |
|---|---|---|
| Main / UI | The `prompt_toolkit` event loop, command handlers, every `Ui` coroutine | `await ui.choose(...)` |
| Turn worker | One `session.send(text)` per turn | Does not prompt |
| SDK tool threads | `_dispatch` → `_tool`, serialised by `self._gate` (`chat.py:72`) | `ui.from_thread.choose(...)` |

Only the UI thread may touch the terminal. Everything else marshals.

### Command handlers run *on* the event loop, so they must await

The registry's handlers are reached from a key binding — that is, from the event
loop itself. A synchronous handler that called a blocking `ui.choose()` would be
waiting, on the event loop, for arrow keys that only that same loop can deliver.
It would hang on the first keystroke. `from_thread` is no escape either: it
raises on the UI thread precisely because using it there would deadlock instead.

This is why `Ui`'s prompting methods are coroutines and
`Command.handler` returns an `Awaitable[State]`. A handler awaits
`ui.choose(...)`, which runs a nested `Application.run_async()` on the live loop;
the loop keeps delivering keys, the nested application consumes them, and the
outer input box resumes when it returns. No thread is involved, and none should
be: dispatching an interactive handler to a worker only to marshal each of its
prompts back would be strictly more machinery for the same result.

Two consequences worth stating, since they are easy to get wrong:

- **`/model` is the case that proves it.** Bare `/model` awaits `ui.choose()` for
  the row, then awaits `ui.ask_line()` if `Other…` was picked, then awaits
  `ui.confirm()` for the save. Three nested prompts from one handler, all on one
  loop.
- **A handler must not block the loop for non-UI work either.** `/doctor` calls
  `doctor.run_checks(config)`, which runs subprocesses. That goes to a thread via
  `asyncio.to_thread` so the box stays responsive, and the handler awaits it.

### Axiom approval arrives from a foreign thread

The dangerous case is not `session.send` — it is the approval gate inside it.
`chat.py:216-219` calls `self.confirm(proposal)` synchronously from within
`_tool`, and the comment at `chat.py:69-72` states the design outright: the SDK
may run several tools at once, each on its own thread, and those tools "stop to
ask a human for approval." Today that callback is `cli.py:25-37`, a bare
`input()` loop.

Left alone, this breaks in the new shell. The callback would run on an SDK tool
thread while the `Application` owns stdin on the main thread — two readers, one
terminal — and a naive rewrite calling `PromptToolkitUi.confirm()` directly would
mutate the application off its event loop and can hang the turn at precisely the
moment explicit axiom approval is required. That is the worst possible place for
this class of bug: `AGENTS.md` makes auditing assumptions a first-order guarantee,
not a convenience.

So `_confirm_assumption` is rebuilt as a `BlockingUi` consumer:

1. `_tool` calls `confirm(proposal)` on a tool thread, as it does today.
2. The callback calls `ui.from_thread.choose(...)`, which schedules the selector
   coroutine on the UI event loop with `asyncio.run_coroutine_threadsafe` and
   blocks the tool thread on the returned future.
3. The human answers; the result crosses back; the tool call resumes and returns
   its `ToolResult`.

The tool thread blocking is correct and intended — `self._gate` already
serialises tool calls, and a pending axiom question *should* stop the turn. What
must never happen is the reverse: the UI thread blocking on anything, which is
what both `from_thread` raising on the UI thread and the async handler contract
above exist to prevent.

Note that this is the same underlying selector the command handlers await. One
implementation, reached two ways: awaited directly on the loop, or scheduled onto
it and waited on from outside. `PlainUi` needs neither, having no loop; its
`from_thread` calls straight through.

### Shutdown with a turn in flight

`session.send` cannot be cancelled, so a worker cannot be told to stop. The two
naive options are both wrong: a non-daemon worker makes the process appear hung
after the user asks to leave, because the interpreter waits for it at exit; a
daemon worker can be killed mid Lean or LaTeX subprocess, or between a check
succeeding and its artifact being written — which would break the promise made
above that nothing is dropped.

The policy is therefore explicit, and modelled on the double-tap users already
know:

- **First Ctrl+C during a turn** does not exit. It writes `a turn is still
  running — Ctrl+C again to leave anyway; Lean or LaTeX processes it started may
  be left orphaned and its artifacts incomplete` and keeps the UI alive.
- **Second Ctrl+C** records the abandonment and then hard-exits — see below.
- **Ctrl+C with no turn in flight** leaves the session at once, as today.
- The worker is **non-daemon**, so an ordinary `/exit` waits for a safe boundary
  rather than truncating a write. `/exit` during a turn reports that it is
  waiting and for how long.

**The forced exit needs a named mechanism, because the obvious ones do not
work.** A non-daemon worker is joined by the interpreter at shutdown, so neither
`SystemExit` nor returning from the application can leave while `session.send` is
stalled — which is the only situation the second Ctrl+C exists for. Making the
worker a daemon instead would trade this away for the truncation hazard the
non-daemon choice was made to avoid.

The forced path is therefore:

1. `record_abandonment("forced_exit")` — a synchronous append through `_record`,
   so it completes before anything else happens.
2. `os._exit(130)` — bypassing interpreter shutdown entirely.

`os._exit` is chosen deliberately and its costs are part of the specification,
not an implementation detail to discover later: the worker is **not** joined,
`atexit` handlers do **not** run, buffered writes elsewhere are **not** flushed,
and any Lean or Tectonic child process the SDK started may be left **orphaned**.
This is why the first Ctrl+C states that cost before the user commits to it — a
forced exit is a deliberate choice to accept a mess, and Hardy should say so
rather than imply a clean stop. `130` is the conventional SIGINT status.

The two properties are then consistent: non-daemon keeps the *ordinary* exit
honest, and `os._exit` makes the *forced* exit actually immediate.

## Degradation and error handling

Plain mode — `tui/plain.py`, today's loop with the same command registry and a
numbered-list `/model` — is selected when any of these hold:

- `stdin` or `stdout` is not a TTY (piping, CI, `hardy < script.txt`)
- `TERM` is `dumb`
- `HARDY_PLAIN` is set, or `--plain` is passed

Beyond that:

| Failure | Response |
|---|---|
| `prompt_toolkit` cannot initialise | Warn on stderr, fall back to plain mode. Never fail a session over rendering |
| A command handler raises | Error notice with the message; the session continues |
| `session.send` raises | Error notice; the session continues |
| Terminal resized | `prompt_toolkit` redraws; the box reflows; scrollback is left alone |

## Testing

No test may require a real terminal — `uv run --extra test pytest` stays
hermetic.

| Target | Method |
|---|---|
| `commands.py` | Pure unit tests: unique match, ambiguous prefix, unknown command, case-insensitivity, argument splitting, and that `suggest()` only ever appends — `suggest("/q")` is `"uit"`, never a rewrite to `/exit` |
| `transcript.py` | Pure unit tests: both prefixes, continuation indent, wrapping at a given width |
| `model_command` and each handler | `ScriptedUi` — canned choices and confirmations, recorded writes. Handlers are coroutines, so these are `async` tests |
| `select.py`, `shell.py` — buffer state and actions | `create_pipe_input()` + `DummyOutput()` + `AppSession` |
| `select.py`, `shell.py` — **rendered appearance** | `create_pipe_input()` + `Vt100_Output` over a `StringIO` with a fixed `Size`, then assert on the escape sequences written |
| **Nested prompting on the loop** | Drive bare `/model` end to end through piped keystrokes — row, then `Other…` line, then save confirmation — and assert all three prompts complete. This is the test that would have caught a synchronous handler |
| Approval marshalling | Call the rebuilt `_confirm_assumption` from a worker thread against a running `AppSession`; assert the answer crosses back and neither thread deadlocks |
| **The startup warning** | Assert the unsandboxed-execution text appears on **both** the TTY and plain startup paths |
| **No outer render under a nested prompt** | `tests/tui/nested_render.py`'s `assert_no_outer_render_during_nested()`. Required coverage for every nested prompt — it is the only headless defence against a class that reached production through an implementer, a reviewer, and the controller |
| **The box's growth and cap** | Pure test of the maximum at several terminal heights: 24 rows → 8, 9 rows → 3 (floor), 60 rows → 12 (ceiling). Then keystrokes asserting the box grows line by line and stops at the cap rather than growing without bound |
| **Reflow on resize** | Change the reported terminal size between renders and assert the box's height follows the new cap, and that a resize while a prompt is open does not corrupt it |
| **Unresolved commands never reach the model** | End-to-end: submit `/mo`, assert an error notice and that `session.send` was not called. A leading space must still send literal text |
| **`/model` refused in flight** | With a turn in flight, submit `/model` and assert the runtime is unchanged. Silent failure otherwise — nothing crashes, the transcript just becomes false |
| Plain mode | Piped stdin; assert the old behaviour and that `/model` still switches |

The split in that table matters. `DummyOutput` discards every write, so it can
prove that Tab left `/model` in the buffer but **cannot** prove that `del`
rendered dim — the headline feature. Appearance assertions need an output that
keeps what it was given, which is why the rendering row uses a `Vt100_Output`
over an inspectable stream and asserts on the emitted escape sequences.

Between them the keystroke tests cover the headline features rather than mocking
them: typing `/mo` must emit a dim `del`; Tab must leave `/model` in the buffer
without running it; Down then Enter in the selector must choose row 2; Esc before
a selection must leave the config untouched; `0` must not select a row.

`tests/test_model_command.py` is rewritten against `ScriptedUi`, keeping every
existing assertion. `ScriptedUi` is a better seam than `ask=answers("2", "n")`
because it models the real interaction — pick a row, then confirm — instead of
faking string prompts.

## Risks

Two assumptions carry the design, and the implementation plan opens with a spike
for each before anything is built on top.

**1. The screen model.** That a **non-full-screen** `prompt_toolkit`
`Application` can draw a bordered box at the bottom while leaving native
scrollback intact, on both Windows Terminal and legacy conhost. If it fails, the
fallback within `prompt_toolkit` is a `PromptSession` with a `bottom_toolbar` and
a rule instead of a border — the `Ui` port and every headless module survive that
change unaltered.

**2. Prompting while an `Application` is already running**, from both directions:

- *On the loop* — that a nested `Application.run_async()` can be awaited from a
  key binding of the outer application, consume keys, and return cleanly with the
  input box restored. This is every interactive command, `/model` included.
- *From off the loop* — that `ui.from_thread` can schedule that same selector
  from an SDK tool thread and return the answer without deadlocking either side.
  This is the axiom approval path.

Both are the same primitive reached two ways, so one spike covers them, and it
must exercise them together: an approval arriving from a tool thread *while* a
nested selector is open is the case most likely to break. If nesting proves
unreliable, the fallback is to suspend the outer application for the duration of
the prompt (`run_in_terminal`) and prompt against the raw terminal — uglier, and
it interrupts the box, but single-reader and safe. What is *not* acceptable is
either a synchronous handler blocking the loop or `_confirm_assumption` left as a
bare `input()` competing with the application for stdin.

### Task 1 spike findings (2026-07-25)

The spike ran in two halves. `spike_terminal.py` (repo root) is the brief's
original script — it needs a human at a real terminal and has **not** been
run; see the instructions in `task-1-report.md` for how to run it on Windows
Terminal and legacy `conhost.exe`. `spike_headless.py` (repo root) is the
headless half, executed here, and is the one this subsection reports on.

**Assumption 1 (screen model — bordered box over native scrollback):
partly disproved. Round one of the visual half has been run.** Two failures
are real, and two observations turned out to be defects in the throwaway spike
script rather than in the design.

Real:

- **Reflow on resize is badly broken.** Resizing the window while the
  non-full-screen `Application` runs corrupts the rendering. This is the
  finding that most threatens the design as specified.
- **Opening one nested selector inside another did nothing.** Note this test
  was partly contaminated — one of the two paths exercised was the buggy one
  below — so treat it as unproven rather than cleanly disproved.

Not the design's fault, and already corrected in shipped code:

- **The thread-driven selector's pointer rendered off by one** while the
  on-loop selector was correct. `spike_terminal.py` round one built its
  thread selector with no explicit `input=`/`output=` — exactly the pattern
  the headless half had already proven breaks across a thread boundary. It
  predicted a silent misrender rather than an exception on a real terminal,
  and that is what happened. This is a **confirmation** of the correction now
  carried as a global constraint, and `select.py:89-90` already passes both
  through.
- **Enter inserted a newline instead of submitting.** Round one bound no
  `enter` handler and used `TextArea(multiline=True)`, so a newline was
  correct for that script.

Still open, because round one never tested it: whether output printed *while*
the application runs lands above the box, leaving the box at the bottom of the
content. A non-full-screen application draws at the cursor, so the box
scrolling when the user scrolls the terminal is expected — Claude Code behaves
the same way. What matters is where new output goes. Round two of the spike
instruments this with a ticker.

**Assumption 2 (prompting while an `Application` is already running):
confirmed on `prompt_toolkit==3.0.52`, headlessly, via
`create_pipe_input()` + `Vt100_Output(StringIO(), lambda: Size(...))`, on
Windows (Git Bash over MSYS2). Both directions hold, and so does the
overlap case:

- *On the loop* — a nested `Application.run_async()` awaited from inside an
  `async def` key binding of a running outer `Application` returns its
  result, and the outer application keeps taking keys afterward. Confirmed
  exactly as the brief's idiom describes.
- *From off the loop* — `asyncio.run_coroutine_threadsafe(coro, app.loop)`
  from a plain `threading.Thread` reaches the nested application and returns
  its result via `future.result()`, without deadlocking. **With one load-bearing
  correction, below.**
- *Overlapping* — opening a nested selector from a key binding and then, while
  it is still open, having a thread schedule a second nested selector on the
  same input/output did **not** deadlock and did **not** corrupt either
  result: each selector consumed its own `Enter` and returned the row it was
  sent, in the order the keys arrived. `prompt_toolkit` appears to serialize
  the two rather than let them race — evidence, not a documented guarantee,
  so later tasks should still avoid relying on true concurrency between two
  nested applications and treat this as "safe, not simultaneous."
- `patch_stdout()` stayed active for the whole run; a plain `print()` called
  from inside a synchronous key binding did not raise and its text reached
  the captured output stream, with the application resuming normally
  afterward.
- An `async def` handler bound with `@keys.add("enter")` is awaited by
  `prompt_toolkit` to actual completion — a two-step coroutine (`sleep` then
  exit) ran both steps in order before `run_async()` returned. It is not
  fired-and-forgotten.

**The one correction to the brief's idioms:** the brief's code relies on the
*ambient* application session — a nested `Application()` built without
`input=`/`output=` inherits them from whatever `create_app_session()` (or the
process default) is current. That inheritance reads `contextvars`, and
`contextvars.Context` **does not propagate across `threading.Thread`
boundaries** — and, more subtly, `asyncio.run_coroutine_threadsafe` /
`loop.call_soon_threadsafe` captures `contextvars.copy_context()` from the
**calling** (worker) thread at the moment it is invoked, not the target
loop's ambient context, even though the coroutine body itself later executes
on the loop's thread. The spike reproduced this exactly: a nested
`Application()` built inside a coroutine scheduled from a worker thread,
relying on ambient session lookup, raised
`prompt_toolkit.output.win32.NoConsoleScreenBufferError` trying to build a
*real* console output instead of picking up the test's `Vt100_Output`. On a
real terminal this would not raise — it would silently attach to the process's
actual stdio instead of the intended input/output, which is worse. **Fix,
confirmed to work:** pass `input=` and `output=` explicitly to every
`Application(...)` that might be constructed from a foreign-thread-scheduled
coroutine, rather than relying on `create_app_session()` ambient inheritance.
`PromptToolkitUi` must therefore hold explicit references to its `input`/
`output` objects and pass them to every nested `Application` it builds,
whether from a key binding or from `from_thread`.

**Confirmed API idioms (all on `prompt_toolkit==3.0.52`):**

| Idiom | Status | Notes |
|---|---|---|
| `Application(layout=..., key_bindings=..., full_screen=False)` | Confirmed | As in the brief |
| `Application(..., erase_when_done=True, input=, output=)` | Confirmed | All four accepted together |
| `await nested_app.run_async()` inside an `async def` key binding | Confirmed | Outer app resumes afterward |
| `asyncio.run_coroutine_threadsafe(coro, app.loop)` from a worker thread | Confirmed, **with the input=/output= correction above** | Do not rely on ambient `create_app_session()` for the nested `Application`; pass `input=`/`output=` explicitly |
| `Application.loop` | Confirmed to exist, but **only while running** | Set to `None` at construction, assigned the running loop for the duration of `run_async()`, reset to `None` on exit. Reading it before the app starts or after it returns is `None`. Capture it from inside the app (e.g. at the top of the run, or from a key binding) rather than from the constructor |
| `create_pipe_input()` | Confirmed, **context manager only** | `with create_pipe_input() as p:` — as of 3.0.28 it no longer returns a bare `PipeInput` |
| `Vt100_Output(stream, get_size)` | Confirmed | Signature is `Vt100_Output(stdout, get_size, term=None, default_color_depth=None, enable_bell=True, enable_cpr=True)`; `get_size` is a zero-arg callable returning `data_structures.Size` |
| `prompt_toolkit.application.create_app_session(input=, output=)` | Confirmed to exist and work | But see the threading correction — it does not help a coroutine scheduled from another thread |
| `patch_stdout()` active during `app.run()`/`run_async()`, `print()` inside a key binding | Confirmed | No corruption observed; text reaches the output |
| `@keys.add("enter")` on an `async def` | Confirmed | Awaited to completion by `prompt_toolkit`, not fire-and-forget |
| `Frame(body)`, `TextArea(multiline=, wrap_lines=, prompt=, history=, auto_suggest=, completer=, complete_while_typing=)` | Confirmed | All listed kwargs accepted |
| `AutoSuggest.get_suggestion(buffer, document) -> Suggestion(text) \| None` | Confirmed | Shape matches exactly |
| `Completer.get_completions(document, complete_event) -> Iterable[Completion]`, `Completion(text, start_position=, display_meta=)` | Confirmed | Shape matches; `Completion` additionally accepts `display=`, `style=`, `selected_style=` |
| `Style.from_dict({...})`, `.style_rules` | Confirmed | `style_rules` is a `list[tuple[str, str]]` of the rules as given |
| `class:select.row.current` dotted style classes | Confirmed | Resolves via exact match on the full dotted class; a rule for `select.row` alone does not also apply to `select.row.current` — each dotted class is its own key, not a prefix match |
| Binding both `@keys.add("escape", eager=True)` and `@keys.add("escape", "enter")` in one `KeyBindings` | **Behaves differently from a naive reading — matters for Esc/save-confirm flows** | `eager=True` on the one-key binding makes it fire **immediately** on Escape and **shadows** the two-key sequence outright: sending `Escape` then `Enter` back-to-back still only ever fires `escape-alone` twice, never `escape-enter`. Removing `eager=True` from the one-key binding restores correct disambiguation: a lone `Escape` (followed by a pause past `prompt_toolkit`'s ambiguous-key timeout) fires `escape-alone`, while `Escape` immediately followed by `Enter` fires `escape-enter`. **Any handler that wants a plain `Escape` and a chorded `Escape`-prefixed sequence to coexist must not mark the plain `Escape` binding eager.** |

The escape/escape-enter finding matters beyond a style note: the design's
`select.py` cancels on Esc, and if a later task ever wants an
`Escape`-prefixed chord (e.g. `Escape, Enter` as a distinct action) alongside
a plain-Esc cancel, marking plain Esc `eager=True` — which the brief's own
`spike_terminal.py` example does, for the unrelated reason of cancelling
without waiting on the ambiguous-key timeout — would silently make the chord
unreachable. If a future task needs both, it must accept the timeout latency
on plain Esc instead of using `eager=True`, or bind the chord under a
different prefix key.

Full evidence, including the executed output of every assertion above and the
raw traceback that reproduced the threading trap, is in
`.superpowers/sdd/2026-07-25-tui-improvements/task-1-report.md`.

## Companion changes

- `pyproject.toml`: add `prompt-toolkit>=3.0.50` to `dependencies`; refresh
  `uv.lock`.
- `cli.py`: `_chat` becomes wiring; add the global `--plain` flag. The five
  startup lines it prints today (`cli.py:123-127`) move into `run_session` — see
  *Startup*, and note that the unsandboxed-execution warning among them is a
  standing disclosure, not boilerplate to be tidied away.
- `cli.py:25-37`: `_confirm_assumption` is rebuilt as a `Ui` consumer reached
  through `ui.from_thread`, and shows the proposal as a two-row selector instead
  of a `[y/N]` loop. Its refusal semantics are unchanged — a decline still
  returns `False` and hard-gates the assumption.
- `chat.py`: add `record_abandonment(reason)`. This is the only change to the
  session core, and it exists so an abandoned or force-exited turn is visible in
  `transcript.jsonl` rather than only on a screen that is about to disappear.
- Per the repository rule, keep `README.md`, `DESIGN.md`, `FEATURES.md`, and
  `ARCHITECTURE.html` consistent with the new interactive behaviour.

## Follow-ups

1. Token streaming: turn `ChatRuntime.ask` into a streaming protocol, which also
   unlocks real interruption. Filed as its own issue.
2. Move `ConsoleTerminal` onto `Ui` so the staged `prove` workflow gets the same
   selectors.
3. `/prove` inside the session, once (2) lands.
4. Lean syntax highlighting in the transcript, if it justifies a second
   dependency.
