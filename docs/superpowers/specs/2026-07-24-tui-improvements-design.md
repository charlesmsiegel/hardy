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
| Newline in the input box | Alt+Enter always; Shift+Enter where the terminal reports it; a trailing `\` also continues |
| Not a TTY | Fall back to today's `input()`/`print()` loop, same command registry |
| Prompts raised from tool threads | Marshalled onto the UI event loop by `Ui.from_thread`; the calling tool thread blocks for the answer |
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
    def write(self, text: str, *, style: str = "system") -> None: ...
    def choose(self, title: str, rows: Sequence[Choice], *, current: int = 0,
               subtitle: str = "") -> Choice | None: ...   # None = cancelled
    def ask_line(self, prompt: str) -> str | None: ...     # None = cancelled
    def confirm(self, question: str) -> bool: ...

    @property
    def from_thread(self) -> Ui: ...   # same surface, safe off the UI thread
```

Three implementations: `PromptToolkitUi` (in `tui/shell.py`), `PlainUi` (in
`tui/plain.py`), and `ScriptedUi` (in `tests/`).

`from_thread` returns an object with the same `Ui` surface whose calls are
marshalled onto the application's event loop (`loop.call_soon_threadsafe` plus a
`concurrent.futures.Future`), blocking the calling thread until the human
answers. Because the surface is identical, a caller that may run on either
thread — such as the axiom approval callback — is handed a `Ui` and never has to
know which. See *Concurrency*. Calling `from_thread` *from* the UI thread is a
programming error and raises rather than deadlocking; `PlainUi`'s and
`ScriptedUi`'s `from_thread` simply return `self`.

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
    name: str                       # canonical, without the slash
    summary: str                    # shown in the menu and by /help
    handler: Callable[[Ui, str, State], State]
    argument_hint: str = ""         # e.g. "[identity]"
    aliases: tuple[str, ...] = ()   # e.g. ("quit",) on the exit command
```

Aliases are resolvable and completable, but only the canonical name is ever
offered as ghost text — suggesting `/quit` for `/q` while `/exit` is the name
shown by `/help` would teach the wrong vocabulary.

`State` carries the mutable session context a handler may replace — the
`Config`, the `MathematicsSession`, and a `done: bool` for `/exit`. Handlers
return a new `State`, so `/model` updating the config stays a value-returning
operation as it is today (`cli.py:74`).

Pure query functions over the registry. All three take the raw buffer text
**including the leading slash**, so the caller never has to strip it:

- `resolve(text) -> tuple[Command, str] | None` — splits `/name rest`, matches
  name or alias case-insensitively, returns the command and the remaining
  argument text; `None` for an unknown name.
- `complete(text) -> list[Command]` — every command whose name or alias starts
  with the typed prefix, in registry order, deduplicated by command.
- `suggest(text) -> str` — the characters to render as ghost text: the tail of
  the single canonical name that matches, otherwise `""`. `suggest("/mo")` is
  `"del"`; `suggest("/")` and `suggest("/x")` are both `""`.

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
| `/exit`, `/quit` | Leaves the session |

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
  / for commands · alt+enter for newline   claude-opus-5
```

- The box grows with the input and reflows on resize.
- The hint line is dim: keys on the left, active model on the right.
- Below 40 columns the border is dropped for a bare `> ` prompt rather than
  wrapping a broken box.
- History is navigated with Up/Down **only when the buffer is a single line**;
  otherwise those keys move the cursor between lines. History persists to
  `<workspace>/input-history` through `FileHistory`.

### Ghost text

Typing `/mo` renders `/mo` followed by a dim `del`, completing to `/model`. Tab,
Right, or End accepts it, leaving `/model` in the buffer for Enter to run.

`/` alone opens the full command menu. A prefix matching several commands shows
no ghost text — guessing would be worse than silence — and Tab opens a menu over
the matches. Once a command name is complete and followed by a space, the hint
line shows its `argument_hint`.

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

Only one turn is in flight at a time; submitting during a turn is refused with a
dim notice rather than queued.

Ctrl+C during an in-flight turn is covered under *Concurrency*.

## Concurrency

Three kinds of thread are live at once, and only one of them may touch the
terminal.

| Thread | Runs | May touch the terminal |
|---|---|---|
| Main / UI | The `prompt_toolkit` event loop and every `Ui` method | Yes — exclusively |
| Turn worker | One `session.send(text)` per turn | No |
| SDK tool threads | `MathematicsSession._dispatch` → `_tool`, serialised by `self._gate` (`chat.py:72`) | No |

### Axiom approval crosses a thread boundary

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

So `_confirm_assumption` is rebuilt as a `Ui` consumer and wired with
`ui.from_thread`:

1. `_tool` calls `confirm(proposal)` on a tool thread, as it does today.
2. The callback calls `ui.from_thread.choose(...)`, which schedules the selector
   on the UI event loop and blocks the tool thread on a `Future`.
3. The human answers; the result crosses back; the tool call resumes and returns
   its `ToolResult`.

The tool thread blocking is correct and intended — `self._gate` already
serialises tool calls, and a pending axiom question *should* stop the turn. What
must not happen is the UI thread blocking on the tool thread, which is why
`from_thread` raises if called from the UI thread rather than deadlocking.

`PlainUi` needs none of this: it has no event loop, and `from_thread` returns
`self`.

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
  running — Ctrl+C again to leave anyway; its Lean or LaTeX artifacts may be left
  incomplete` and keeps the UI alive.
- **Second Ctrl+C** exits immediately, having said what the cost is.
- **Ctrl+C with no turn in flight** leaves the session at once, as today.
- The worker is **non-daemon**, so an ordinary `/exit` waits for a safe boundary
  rather than truncating a write. `/exit` during a turn reports that it is
  waiting and for how long.

A forced exit also calls `record_abandonment("forced_exit")` before leaving, so
the durable transcript says what happened even when the user did not wait.

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
| `commands.py` | Pure unit tests: unique match, ambiguous prefix, unknown command, case-insensitivity, argument splitting |
| `transcript.py` | Pure unit tests: both prefixes, continuation indent, wrapping at a given width |
| `model_command` and each handler | `ScriptedUi` — canned choices and confirmations, recorded writes |
| `select.py`, `shell.py` — buffer state and actions | `create_pipe_input()` + `DummyOutput()` + `AppSession` |
| `select.py`, `shell.py` — **rendered appearance** | `create_pipe_input()` + `Vt100_Output` over a `StringIO` with a fixed `Size`, then assert on the escape sequences written |
| Approval marshalling | Call the rebuilt `_confirm_assumption` from a worker thread against a running `AppSession`; assert the answer crosses back and neither thread deadlocks |
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

**2. Prompting from a tool thread.** That `ui.from_thread` can drive a selector
on a running `Application`'s event loop from an SDK tool thread and return the
answer without deadlocking either side. This is the axiom approval path, so it
has to be proven rather than assumed. If marshalling proves unreliable, the
fallback is to suspend the `Application` for the duration of the approval
(`run_in_terminal`) and prompt against the raw terminal — uglier, and it
interrupts the box, but it is single-reader and safe. What is *not* acceptable is
leaving `_confirm_assumption` as a bare `input()` competing with the application
for stdin.

## Companion changes

- `pyproject.toml`: add `prompt-toolkit>=3.0.50` to `dependencies`; refresh
  `uv.lock`.
- `cli.py`: `_chat` becomes wiring; add the global `--plain` flag.
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
