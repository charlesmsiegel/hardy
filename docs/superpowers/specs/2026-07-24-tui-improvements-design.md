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
| `/model` row selection | Arrow keys move a `❯` pointer; number keys 1-N select immediately; Enter selects; Esc cancels |
| Unlisted model identities | An `Other…` row prompts for a literal identity, preserving today's escape hatch (`catalog.py:38`) |
| Newline in the input box | Alt+Enter always; Shift+Enter where the terminal reports it; a trailing `\` also continues |
| Not a TTY | Fall back to today's `input()`/`print()` loop, same command registry |
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
```

Three implementations: `PromptToolkitUi` (in `tui/shell.py`), `PlainUi` (in
`tui/plain.py`), and `ScriptedUi` (in `tests/`).

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
| `/clear` | Clears the visible transcript. Does **not** reset session history — the wording must not imply it does |
| `/exit`, `/quit` | Leaves the session |

## Behaviour

### The input box

```
> is the golden ratio irrational?

● No — φ = (1+√5)/2 is irrational. The Lean statement
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

Rows come from `catalog.available()`; notes come from the catalog entries; the
pointer starts on the current model. `Other…` calls `ui.ask_line()` and accepts
whatever is typed, matching `catalog.describe()`'s tolerance for unlisted
identities.

The sequence after a selection is unchanged from today, only re-rendered:
`session.switch_model()` first, and on `RuntimeError` the config is returned
untouched with the error written out — a failed switch must never leave the
session announcing a model it cannot use (`cli.py:99-104`). Then
`ui.confirm("Save as the default in <path>?")`, which is now a two-row Yes/No
selector defaulting to No rather than a `[y/N]` line.

Esc at any point returns the config unchanged.

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

Ctrl+C leaves the session, as today. Only one turn is in flight at a time;
submitting during a turn is refused with a dim notice rather than queued.

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
| `select.py`, `shell.py` | `create_pipe_input()` + `DummyOutput()` + `AppSession`: real keystrokes in, rendered output and resulting actions asserted |
| Plain mode | Piped stdin; assert the old behaviour and that `/model` still switches |

The keystroke tests are what make the headline features genuinely covered rather
than merely mocked: typing `/mo` must render a dim `del`; Tab must leave
`/model` in the buffer without running it; Down then Enter in the selector must
choose row 2; Esc must leave the config untouched.

`tests/test_model_command.py` is rewritten against `ScriptedUi`, keeping every
existing assertion. `ScriptedUi` is a better seam than `ask=answers("2", "n")`
because it models the real interaction — pick a row, then confirm — instead of
faking string prompts.

## Risk

The one load-bearing technical assumption is that a **non-full-screen**
`prompt_toolkit` `Application` can draw a bordered box at the bottom while
leaving native scrollback intact, on both Windows Terminal and legacy conhost.
The implementation plan must open with a throwaway spike proving this before
anything is built on it. If it fails, the fallback within `prompt_toolkit` is a
`PromptSession` with a `bottom_toolbar` and a rule instead of a border — the
`Ui` port and every headless module survive that change unaltered.

## Companion changes

- `pyproject.toml`: add `prompt-toolkit>=3.0.50` to `dependencies`; refresh
  `uv.lock`.
- `cli.py`: `_chat` becomes wiring; add the global `--plain` flag.
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
