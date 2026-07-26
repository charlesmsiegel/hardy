# Claude-Code-shaped terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Hardy's twenty-line `input()`/`print()` session loop with a `prompt_toolkit` terminal that has ghost-text slash-command completion, an inline `/model` selector, and a bordered input box — without weakening the axiom approval gate or the unsandboxed-execution warning.

**Architecture:** A new `src/hardy/tui/` package behind a narrow async `Ui` port. Only `tui/select.py` and `tui/shell.py` import `prompt_toolkit`; the registry, transcript rendering, handlers, and banner are headless and unit-tested with no terminal. Prompting methods are coroutines awaited on the live event loop; `Ui.from_thread` is a synchronous facade over the same coroutines for the SDK's tool threads. The transcript prints into the terminal's native scrollback; only the input box is redrawn.

**Tech Stack:** Python ≥3.11, `prompt_toolkit` ≥3.0.50 (new runtime dependency), `pytest` + `pytest-asyncio` (test-only), `uv` for env and lockfile.

**Spec:** `docs/superpowers/specs/2026-07-24-tui-improvements-design.md`. Where this plan and the spec disagree, the spec wins — raise the conflict rather than guessing.

## Global Constraints

- **Read the spec section before implementing a task that cites one.** Every task names its section.
- `prompt_toolkit>=3.0.50` is the **only** new runtime dependency. No Rich, no Textual, no JS runtime.
- **Only `tui/select.py` and `tui/shell.py` may import `prompt_toolkit`.** A grep test enforces this (Task 4).
- **Never require WSL.** All code must run on Windows (conhost and Windows Terminal), macOS, and Linux. No `termios`, `tty`, `fcntl`, `signal.SIGWINCH`, or bare `os.name` branching for terminal control.
- **The unsandboxed-execution warning is not optional.** Its text is `WARNING: ` + `runner.WARNING` + ` LaTeX is also executed without isolation.` It appears on both startup paths, is never abbreviated, and is asserted by a test.
- **The axiom approval gate must never weaken.** A decline still returns `False` and blocks the assumption.
- The hermetic suite must stay hermetic: `uv run --extra test pytest` requires no terminal, no network, no model, no Lean.
- Line length 100 (`ruff`, `[tool.ruff]` in `pyproject.toml`). Run `uvx ruff check src tests` before each commit — `ruff` is **not** a project dependency, so `uv run ruff` will not find it.
- Claude model identifiers are exact as written in `catalog.py` — never append a date suffix.
- **Every nested prompt must run inside `async with in_terminal():`, and no `Application` may be constructed on a foreign thread.** This constraint has been wrong twice; the version below is the one a Fable investigation established from prompt_toolkit's source, with citations recorded in `.superpowers/sdd/2026-07-25-tui-improvements/fable-selector-diagnosis.md`.
  - **`in_terminal()` is mandatory around any nested `Application` or `PromptSession`.** Without it the outer application can repaint *underneath* an open prompt and displace it by a row. A synchronous key binding invalidates the outer app immediately (`key_bindings.py:141`) while an async one defers past the await (`:133-138`), so whether the bug appears depends on how the prompt was triggered — which is why one path looked fine and another did not. Nothing else suppresses the outer redraw: `_running_in_terminal` is set solely by `in_terminal()` (`run_in_terminal.py:99`, `application.py:511`). It erases the outer UI, no-ops its redraws, detaches its input, repaints on exit, no-ops when no app is running, and chains correctly with `patch_stdout` flushes.
  - **A worker or SDK tool thread must not construct an `Application`.** It posts a request onto an `asyncio.Queue` via `loop.call_soon_threadsafe` and blocks on a `concurrent.futures.Future`; a loop-owned task drains the queue and runs the prompt. This is the axiom-approval path. Note the reason is *not* contextvars — `set_app` mutates the shared `AppSession`, so a pre-created task does see the running app. Do not reintroduce a contextvars-based explanation.
  - **`input=`/`output=` compose with `in_terminal()`** and may be passed or omitted: `in_terminal()` acts on the ambient session's app and detaches its input, so the earlier `EOFError` from two applications sharing one attached input cannot arise. Tests inject explicitly; production callers on the loop may pass nothing.
  - **Window heights must be explicit, never `dont_extend_height` with a trailing newline**, or the content asks for a line the window was not sized for and scrolls inside it — the second displacement mode.
  - **This defect class is headlessly catchable.** `tests/tui/nested_render.py` provides `assert_no_outer_render_during_nested()`, which instruments `Renderer.render` and fails if an outer render lands mid-nested. Any new nested prompt gets a test using it.
- **Newline is Shift+Enter; Alt+Enter is not bound; plain `escape` IS `eager=True`.** This reverses an earlier constraint — read the whole of it before touching a key binding.
  - `prompt_toolkit` maps `\x1b[27;2;13~`, `\x1b[27;5;13~` and `\x1b[27;6;13~` all onto `Keys.ControlM`, identical to plain Enter (`input/ansi_escape_sequences.py:129-131`). Shift+Enter therefore requires **extending that table** to route those sequences to a distinct key before anything can bind them. A trailing `\` continues a line for terminals that never emit them.
  - Because Alt+Enter is Escape+Enter at the wire level, binding it would force `escape` to be non-eager, opening a ~1.5 s window (`ttimeoutlen` 0.5 + `timeoutlen` 1.0, `application.py:292,301`) in which Escape then `/` is eaten as Emacs `M-/` (`emacs.py:300`), losing the slash. With **no** Escape-prefixed chord anywhere in the shell's `KeyBindings`, `escape` is bound `eager=True` and responds instantly.
  - The earlier rule said the opposite — never mark plain `escape` eager — and it was correct *while* the Alt+Enter chord existed. `eager=True` genuinely does shadow `("escape", "enter")` unconditionally; that finding stands. What changed is that the chord is gone, so the constraint it forced is gone with it. **If anyone ever re-adds an Escape-prefixed chord, `eager` must come off again and the Esc-then-slash window returns.**
- Commit messages: lowercase `type: summary`, body explaining *why*. End with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility | Imports `prompt_toolkit` |
|---|---|---|
| `src/hardy/tui/__init__.py` | `run_session()` — picks the TTY or plain path | No |
| `src/hardy/tui/ports.py` | `Choice`, `State`, `Ui`, `BlockingUi` — the boundary types | No |
| `src/hardy/tui/commands.py` | `Command` plus pure `resolve`/`complete`/`suggest` | No |
| `src/hardy/tui/handlers.py` | The handler coroutines and `build_registry()` | No |
| `src/hardy/tui/transcript.py` | Message → prefixed, wrapped lines | No |
| `src/hardy/tui/banner.py` | The five startup lines, shared by both paths | No |
| `src/hardy/tui/plain.py` | `PlainUi` and the non-TTY loop | No |
| `src/hardy/tui/select.py` | The inline `❯` list widget | **Yes** |
| `src/hardy/tui/shell.py` | The `Application`, `PromptToolkitUi`, turn worker, shutdown | **Yes** |
| `src/hardy/chat.py` | Modified: add `record_abandonment()` | No |
| `src/hardy/cli.py` | Modified: `_chat` → wiring, `--plain`, `_confirm_assumption` rebuilt | No |
| `tests/tui/conftest.py` | `ScriptedUi`, keystroke helpers, fake session | No |

Tests live in `tests/tui/` except the rewrite of `tests/test_model_command.py`, which stays where it is so its history is continuous.

---

### Task 1: Prove the two load-bearing assumptions (throwaway spike)

**This task is a gate.** It writes no production code. If either half fails, **stop and report** — the spec names a fallback (a `PromptSession` with a rule instead of a border, or `run_in_terminal` for prompts) and adopting it is a spec revision, not an implementation decision.

Read spec section: **Risks**.

**Files:**
- Create: `spike_terminal.py` (repo root, deleted in Step 6)

**Interfaces:**
- Consumes: nothing.
- Produces: no code. Produces a written finding appended to the spec's Risks section, and the confirmed `prompt_toolkit` API idioms that Tasks 5-7 will copy.

- [ ] **Step 1: Install the dependency into the worktree**

```bash
uv add "prompt-toolkit>=3.0.50"
uv sync --extra test
```

- [ ] **Step 2: Write the spike**

Two questions in one script: does a non-full-screen `Application` keep native scrollback while showing a bordered box, and can a nested selector be driven both from a key binding and from a foreign thread at once?

```python
"""Throwaway. Proves the two assumptions in the spec's Risks section."""
import asyncio
import threading

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.widgets import Frame, TextArea


def build_selector(title: str, rows: list[str]) -> Application:
    index = {"at": 0}
    keys = KeyBindings()

    @keys.add("up")
    def _up(event):
        index["at"] = max(0, index["at"] - 1)

    @keys.add("down")
    def _down(event):
        index["at"] = min(len(rows) - 1, index["at"] + 1)

    @keys.add("enter")
    def _pick(event):
        event.app.exit(result=rows[index["at"]])

    @keys.add("escape", eager=True)
    def _cancel(event):
        event.app.exit(result=None)

    def render():
        return "\n".join(
            f"{'>' if i == index['at'] else ' '} {i + 1}. {row}"
            for i, row in enumerate(rows)
        )

    body = HSplit([
        Window(FormattedTextControl(title), height=1),
        Window(FormattedTextControl(render)),
    ])
    return Application(layout=Layout(body), key_bindings=keys, full_screen=False)


def main() -> None:
    box = TextArea(height=2, multiline=True, prompt="> ")
    keys = KeyBindings()

    @keys.add("c-c")
    def _quit(event):
        event.app.exit(result="quit")

    @keys.add("c-s")           # stands in for a command handler
    async def _nested(event):
        picked = await build_selector("pick a row", ["alpha", "beta", "gamma"]).run_async()
        print(f"[on-loop selector returned {picked!r}]")

    @keys.add("c-t")           # stands in for an SDK tool thread
    def _from_thread(event):
        loop = asyncio.get_running_loop()

        def worker():
            future = asyncio.run_coroutine_threadsafe(
                build_selector("from a thread", ["yes", "no"]).run_async(), loop
            )
            print(f"[thread selector returned {future.result()!r}]")

        threading.Thread(target=worker, daemon=True).start()

    app = Application(
        layout=Layout(HSplit([Frame(box)])),
        key_bindings=keys,
        full_screen=False,
    )
    with patch_stdout():
        for n in range(40):
            print(f"scrollback line {n}")     # must survive and stay scrollable
        app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it and record what happens**

```bash
uv run python spike_terminal.py
```

Check each of these by hand and write the answer down:

1. Are the 40 scrollback lines still reachable by scrolling up, and selectable/copyable with the mouse?
2. Is the box bordered and pinned below them?
3. Does Ctrl+S open the selector, respond to arrows, and restore the box on Enter/Esc?
4. Does Ctrl+T open a selector driven from a non-UI thread and return its answer?
5. Do Ctrl+S and Ctrl+T overlapping (open the nested one, then press Ctrl+T) deadlock or misrender?
6. Does resizing the terminal reflow the box without corrupting scrollback?

- [ ] **Step 4: Repeat on a second Windows terminal**

Run it in **both** Windows Terminal and legacy `conhost.exe` (`Win+R` → `conhost.exe`, then `uv run python spike_terminal.py`). Record any difference. This is why the spike exists — `prompt_toolkit`'s Win32 path differs from its VT path.

- [ ] **Step 5: Append the finding to the spec**

Add a short subsection under `## Risks` stating, for each of the two assumptions: confirmed or not, on which terminals, and the exact API idioms that worked (`Application(full_screen=False)`, `patch_stdout()`, `run_async()` nesting, `run_coroutine_threadsafe`). Later tasks copy these idioms verbatim — if the spike had to deviate from the code above, the spec must say how.

- [ ] **Step 6: Delete the spike and commit**

```bash
rm spike_terminal.py
git add pyproject.toml uv.lock docs/superpowers/specs/2026-07-24-tui-improvements-design.md
git commit -m "build: add prompt_toolkit and record the terminal spike findings"
```

- [ ] **Step 7: Gate**

If any of questions 1-6 failed on any terminal, **stop here and report to the user** with what failed and on which terminal. Do not begin Task 2.

---

### Task 2: The boundary types

Read spec sections: **The `Ui` port**, **`tui/commands.py`**.

**Files:**
- Create: `src/hardy/tui/__init__.py`, `src/hardy/tui/ports.py`
- Test: `tests/tui/__init__.py`, `tests/tui/test_ports.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Choice(value: str, label: str, note: str = "")` — frozen dataclass.
  - `State(config, session, done: bool = False, turn_running: bool = False)` — frozen dataclass; `config` is a `hardy.config.Config`, `session` is a `MathematicsSession | None`.
  - `Ui` protocol: `write(text, *, style="system") -> None`; `async choose(title, rows, *, current=0, subtitle="") -> Choice | None`; `async ask_line(prompt) -> str | None`; `async confirm(question) -> bool`; property `from_thread -> BlockingUi`.
  - `BlockingUi` protocol: same four operations, all synchronous.
  - Style names: `"user"`, `"hardy"`, `"system"`, `"error"`, `"warning"`, `"hint"`.

- [ ] **Step 1: Write the failing test**

`ports.py` holds no logic, so the test pins the contract that later tasks rely on: the dataclass shapes and the protocol surface.

```python
# tests/tui/test_ports.py
from __future__ import annotations

import inspect

from hardy.tui import ports


def test_choice_carries_a_value_label_and_optional_note():
    choice = ports.Choice(value="claude-opus-5", label="claude-opus-5")
    assert choice.note == ""
    assert ports.Choice("a", "b", "c").note == "c"


def test_state_defaults_to_running_nothing_and_not_done():
    state = ports.State(config=None, session=None)
    assert state.done is False
    assert state.turn_running is False


def test_every_prompting_method_on_ui_is_a_coroutine():
    """The whole design rests on this: a blocking prompt would deadlock the loop."""
    for name in ("choose", "ask_line", "confirm"):
        assert inspect.iscoroutinefunction(getattr(ports.Ui, name)), name
    assert not inspect.iscoroutinefunction(ports.Ui.write)


def test_blocking_ui_mirrors_ui_without_coroutines():
    for name in ("write", "choose", "ask_line", "confirm"):
        assert hasattr(ports.BlockingUi, name)
        assert not inspect.iscoroutinefunction(getattr(ports.BlockingUi, name))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_ports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tui'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/ports.py
"""The boundary between Hardy's session and whatever is drawing it.

Command handlers and the assumption gate depend on these types and nothing
else, which is what keeps them testable with no terminal in the room.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class State:
    """What a handler may read, and what it returns a new version of."""

    config: Any
    session: Any
    done: bool = False
    turn_running: bool = False


class BlockingUi(Protocol):
    """The same operations as `Ui`, synchronous, for callers off the UI thread."""

    def write(self, text: str, *, style: str = "system") -> None: ...
    def choose(self, title: str, rows: Sequence[Choice], *, current: int = 0, subtitle: str = "") -> Choice | None: ...
    def ask_line(self, prompt: str) -> str | None: ...
    def confirm(self, question: str) -> bool: ...


class Ui(Protocol):
    """Prompting is asynchronous on purpose.

    A selector reads keys that only the application's event loop can deliver,
    so anything that blocks that loop while waiting for them deadlocks by
    construction. Handlers run *on* that loop, so they must await.
    """

    def write(self, text: str, *, style: str = "system") -> None: ...

    async def choose(self, title: str, rows: Sequence[Choice], *, current: int = 0, subtitle: str = "") -> Choice | None: ...
    async def ask_line(self, prompt: str) -> str | None: ...
    async def confirm(self, question: str) -> bool: ...

    @property
    def from_thread(self) -> BlockingUi: ...
```

Leave `src/hardy/tui/__init__.py` empty for now; Task 8 fills it in.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --extra test pytest tests/tui/test_ports.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Enable async tests**

Later tasks need them. In `pyproject.toml`, extend the test extra and configure the mode:

```toml
test = ["pytest>=8", "pytest-asyncio>=0.24"]
```

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Then `uv sync --extra test`.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/tui tests/tui pyproject.toml uv.lock
git commit -m "feat: add the Ui port the terminal will be built behind"
```

---

### Task 3: The command registry

Read spec sections: **`tui/commands.py`**, **Ghost text**.

**Files:**
- Create: `src/hardy/tui/commands.py`
- Test: `tests/tui/test_commands.py`

**Interfaces:**
- Consumes: `hardy.tui.ports.Ui`, `State`.
- Produces:
  - `Command(name, summary, handler, argument_hint="", alias_of=None, safe_in_flight=False)` — frozen dataclass; `handler` is `Callable[[Ui, str, State], Awaitable[State]]`.
  - `resolve(text, commands) -> tuple[Command, str] | None`
  - `complete(text, commands) -> list[Command]`
  - `suggest(text, commands) -> str`
  - `canonical(commands) -> list[Command]` — entries with `alias_of is None`, for the `/` menu and `/help`.

All four take `commands: Sequence[Command]` explicitly. There is no module-level registry — `handlers.build_registry()` (Task 5) constructs it, which keeps this module free of session imports.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_commands.py
from __future__ import annotations

import pytest

from hardy.tui import commands
from hardy.tui.ports import State


async def _noop(ui, argument, state) -> State:
    return state


def registry() -> list[commands.Command]:
    return [
        commands.Command("help", "list commands", _noop, safe_in_flight=True),
        commands.Command("model", "switch model", _noop, argument_hint="[identity]"),
        commands.Command("doctor", "check the toolchain", _noop),
        commands.Command("exit", "leave", _noop, safe_in_flight=True),
        commands.Command("quit", "leave", _noop, alias_of="exit", safe_in_flight=True),
    ]


def test_resolve_splits_the_name_from_its_argument():
    found = commands.resolve("/model claude-sonnet-5", registry())
    assert found is not None
    command, argument = found
    assert command.name == "model"
    assert argument == "claude-sonnet-5"


def test_resolve_is_case_insensitive_and_tolerates_no_argument():
    found = commands.resolve("/MODEL", registry())
    assert found is not None and found[0].name == "model" and found[1] == ""


def test_resolve_returns_none_for_an_unknown_name():
    """This is what stops /mo reaching the model as a mathematical claim."""
    assert commands.resolve("/mo", registry()) is None
    assert commands.resolve("/nonsense", registry()) is None


def test_resolve_finds_an_alias_entry():
    found = commands.resolve("/quit", registry())
    assert found is not None and found[0].alias_of == "exit"


def test_complete_returns_every_entry_sharing_the_prefix():
    assert [c.name for c in commands.complete("/", registry())] == [
        "help", "model", "doctor", "exit", "quit",
    ]
    assert [c.name for c in commands.complete("/m", registry())] == ["model"]
    assert commands.complete("/zz", registry()) == []


def test_suggest_appends_the_tail_of_a_unique_match():
    assert commands.suggest("/mo", registry()) == "del"
    assert commands.suggest("/model", registry()) == ""


def test_suggest_only_ever_appends_even_for_an_alias():
    """The bug this shape prevents: completing /q against `exit` would give /qxit."""
    assert commands.suggest("/q", registry()) == "uit"


def test_suggest_stays_silent_when_the_prefix_is_ambiguous():
    ambiguous = [
        commands.Command("status", "show status", _noop),
        commands.Command("setup", "run setup", _noop),
    ]
    assert commands.suggest("/s", ambiguous) == ""
    assert commands.suggest("/", registry()) == ""


@pytest.mark.parametrize("text", ["", "model", " /model", "hello"])
def test_the_query_functions_ignore_text_that_is_not_a_command(text: str):
    assert commands.resolve(text, registry()) is None
    assert commands.complete(text, registry()) == []
    assert commands.suggest(text, registry()) == ""


def test_canonical_hides_alias_entries():
    assert [c.name for c in commands.canonical(registry())] == [
        "help", "model", "doctor", "exit",
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'commands'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/commands.py
"""The slash-command registry, and the pure queries the terminal asks of it.

Every name is a real entry, aliases included. That is deliberate: if aliases
were a list on the canonical command, a prefix matching only an alias would
have nothing coherent to complete -- `/q` matches `exit` through `quit`, but
`exit` does not start with `q`, so appending the canonical tail would render
`/qxit`. Giving each name its own entry means every string `suggest` can match
is a string the user is literally typing, so it only ever appends.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .ports import State, Ui


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    handler: Callable[[Ui, str, State], Awaitable[State]]
    argument_hint: str = ""
    alias_of: str | None = None
    # Defaults to False so a command added later is refused while a turn is
    # still running until someone has thought about whether that is safe.
    safe_in_flight: bool = False


def _split(text: str) -> tuple[str, str] | None:
    """The typed name (lowercased, no slash) and the rest, or None if not a command."""
    if not text.startswith("/"):
        return None
    name, _, argument = text[1:].partition(" ")
    return name.lower(), argument.strip()


def resolve(text: str, commands: Sequence[Command]) -> tuple[Command, str] | None:
    parts = _split(text)
    if parts is None:
        return None
    name, argument = parts
    match = next((c for c in commands if c.name == name), None)
    return None if match is None else (match, argument)


def complete(text: str, commands: Sequence[Command]) -> list[Command]:
    parts = _split(text)
    if parts is None or parts[1]:
        return []
    return [c for c in commands if c.name.startswith(parts[0])]


def suggest(text: str, commands: Sequence[Command]) -> str:
    """The characters to render as ghost text. Never rewrites what was typed."""
    parts = _split(text)
    if parts is None or parts[1] or not parts[0]:
        return ""
    matches = complete(text, commands)
    if len(matches) != 1:
        return ""
    return matches[0].name[len(parts[0]):]


def canonical(commands: Sequence[Command]) -> list[Command]:
    return [c for c in commands if c.alias_of is None]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --extra test pytest tests/tui/test_commands.py -v`
Expected: PASS (13 tests, counting the parametrised four)

- [ ] **Step 5: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui/commands.py tests/tui/test_commands.py
git commit -m "feat: resolve and complete slash commands as pure queries"
```

---

### Task 4: Transcript rendering, the banner, and the import fence

Read spec sections: **Transcript rendering**, **Startup**.

**Files:**
- Create: `src/hardy/tui/transcript.py`, `src/hardy/tui/banner.py`
- Test: `tests/tui/test_transcript.py`, `tests/tui/test_banner.py`, `tests/tui/test_layering.py`

**Interfaces:**
- Consumes: `hardy.runner.WARNING`.
- Produces:
  - `transcript.user_lines(text, width) -> list[str]`
  - `transcript.hardy_lines(text, width) -> list[str]`
  - `transcript.notice_lines(text, width) -> list[str]`
  - `banner.lines(config) -> list[tuple[str, str]]` — `(style, text)` pairs, exactly five entries; index 3 is the warning and its style is `"warning"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_transcript.py
from __future__ import annotations

from hardy.tui import transcript


def test_a_user_turn_is_marked_with_an_angle_bracket():
    assert transcript.user_lines("hello", width=40) == ["> hello"]


def test_a_hardy_turn_is_marked_with_a_dot():
    assert transcript.hardy_lines("hello", width=40) == ["● hello"]


def test_continuation_lines_are_indented_under_the_marker():
    lines = transcript.hardy_lines("alpha beta gamma delta", width=14)
    assert lines[0] == "● alpha beta"
    assert all(line.startswith("  ") for line in lines[1:])


def test_explicit_newlines_are_preserved_and_indented():
    assert transcript.user_lines("one\ntwo", width=40) == ["> one", "  two"]


def test_a_notice_has_no_marker_but_keeps_the_indent():
    assert transcript.notice_lines("switched", width=40) == ["  switched"]


def test_a_hyphenated_path_is_not_split():
    """Hardy prints paths constantly; a hyphen-split path cannot be copied.

    Join with a real newline, the way the lines are actually printed. Stripping
    and concatenating with "" would reconstruct a hyphen break losslessly — the
    hyphen stays attached to its chunk — so that form of the assertion cannot
    fail and would pin nothing.
    """
    path = "/tmp/pytest-of-charl/pytest-123/test_something_long0/workspace"
    lines = transcript.notice_lines(f"Workspace: {path}", width=40)
    assert path in "\n".join(lines)


def test_a_word_longer_than_the_width_stays_on_one_line():
    """Counting characters is not enough: chopping the word preserves the count.

    textwrap.wrap defaults to break_long_words=True, so assert the line count
    as well, which is the thing that actually distinguishes the two behaviours.
    """
    word = "x" * 30
    lines = transcript.hardy_lines(word, width=10)
    assert len(lines) == 1
    assert lines[0].count("x") == 30


def test_an_empty_message_renders_nothing():
    assert transcript.hardy_lines("", width=40) == []
    assert transcript.user_lines("   ", width=40) == []
```

```python
# tests/tui/test_banner.py
from __future__ import annotations

from pathlib import Path

from hardy import config as configuration
from hardy import runner
from hardy.tui import banner


def settings(tmp_path: Path) -> configuration.Config:
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        workspace=tmp_path / "workspace",
        path=tmp_path / "config.toml",
    )


def test_the_banner_is_five_lines(tmp_path: Path):
    assert len(banner.lines(settings(tmp_path))) == 5


def test_the_unsandboxed_warning_is_present_in_full(tmp_path: Path):
    """AGENTS.md makes this a standing disclosure. It must never be trimmed."""
    style, text = banner.lines(settings(tmp_path))[3]
    assert style == "warning"
    assert runner.WARNING in text
    assert "LaTeX is also executed without isolation" in text


def test_the_banner_names_the_workspace_model_and_lean_project(tmp_path: Path):
    rendered = "\n".join(text for _, text in banner.lines(settings(tmp_path)))
    assert "claude-opus-5" in rendered
    assert str(tmp_path / "workspace") in rendered
    assert "current directory" in rendered


def test_the_hint_points_at_the_registry_not_just_model(tmp_path: Path):
    hint = banner.lines(settings(tmp_path))[4][1]
    assert "/help" in hint and "/exit" in hint
```

```python
# tests/tui/test_layering.py
from __future__ import annotations

from pathlib import Path

TUI = Path(__file__).resolve().parents[2] / "src" / "hardy" / "tui"
ALLOWED = {"select.py", "shell.py"}


def test_only_the_two_terminal_modules_import_prompt_toolkit():
    """The Ui port is worthless if prompt_toolkit leaks past it.

    Five later tasks trust this fence, so it must be incapable of passing
    vacuously: if the glob ever resolved to nothing, `offenders == []` would
    look like success. Assert the fence actually inspected something.
    """
    checked = sorted(path.name for path in TUI.glob("*.py"))
    assert checked, f"the layering fence inspected no files; is {TUI} right?"
    assert "ports.py" in checked and "commands.py" in checked

    offenders = [
        path.name
        for path in TUI.glob("*.py")
        if path.name not in ALLOWED
        and "prompt_toolkit" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/tui/test_transcript.py tests/tui/test_banner.py tests/tui/test_layering.py -v`
Expected: FAIL — no `transcript` or `banner` module. `test_layering.py` passes already; that is fine, it is a fence for later tasks.

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/transcript.py
"""Turning one message into the lines that go into the terminal's scrollback.

Pure on purpose: once these lines are printed, the terminal owns them. Nothing
here reflows or rewrites history, because rewriting scrollback is what breaks
selection and copy.
"""

from __future__ import annotations

import textwrap

INDENT = "  "


def _render(text: str, marker: str, width: int) -> list[str]:
    if not text.strip():
        return []
    limit = max(width - len(INDENT), 8)
    out: list[str] = []
    for paragraph in text.splitlines():
        # break_long_words and break_on_hyphens are both off deliberately.
        # Hardy prints filesystem paths constantly -- workspace, transcript,
        # config -- and textwrap's defaults would split `pytest-of-charl` after
        # a hyphen, producing a path the user cannot copy. Overflowing the width
        # and letting the terminal soft-wrap is the lesser evil.
        wrapped = textwrap.wrap(
            paragraph,
            width=limit,
            drop_whitespace=True,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        out.extend(wrapped)
    first, *rest = out
    return [f"{marker}{first}" if marker else f"{INDENT}{first}"] + [
        f"{INDENT}{line}" for line in rest
    ]


def user_lines(text: str, width: int) -> list[str]:
    return _render(text, "> ", width)


def hardy_lines(text: str, width: int) -> list[str]:
    return _render(text, "● ", width)


def notice_lines(text: str, width: int) -> list[str]:
    return _render(text, "", width)
```

`textwrap.wrap` breaks nothing by default, so a 30-character word in a 10-column terminal stays whole and the terminal soft-wraps it. That is the behaviour the test pins.

```python
# src/hardy/tui/banner.py
"""What Hardy says before its first prompt.

Line four is not decoration. AGENTS.md makes the missing sandbox a standing
disclosure, and this is the only notice a user gets before the model executes
code on their machine, so it is specified here rather than left to whichever
path happens to start the session.
"""

from __future__ import annotations

from typing import Any

from ..runner import WARNING


def lines(config: Any) -> list[tuple[str, str]]:
    project = config.lean_project or "current directory"
    return [
        ("normal", "Hardy — interactive mathematics workspace"),
        ("hint", f"Workspace: {config.workspace}    Model: {config.model}  (Claude Code subscription)"),
        ("hint", f"Lean project: {project}"),
        ("warning", f"WARNING: {WARNING} LaTeX is also executed without isolation."),
        ("hint", "/help for commands · /exit to leave · your transcript and artifacts are saved as you work"),
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui tests/tui
git commit -m "feat: render transcript lines and the startup banner headlessly"
```

---

### Task 5: The handlers that need no terminal

Read spec sections: **The command set**, **`/clear`**, **Command handlers run *on* the event loop**.

**Files:**
- Create: `src/hardy/tui/handlers.py`, `tests/tui/conftest.py`
- Test: `tests/tui/test_handlers.py`

**Interfaces:**
- Consumes: `commands.Command`, `ports.Ui/State/Choice`, `hardy.doctor`.
- Produces:
  - `handlers.build_registry() -> list[Command]` — the seven entries: `help`, `model`, `status`, `doctor`, `clear`, `exit`, `quit`.
  - Individual coroutines `handle_help`, `handle_status`, `handle_clear`, `handle_doctor`, `handle_exit` with signature `(ui, argument, state) -> State`. `handle_model` arrives in Task 6; until then `build_registry` wires `model` to `handle_model` imported from this module, so it must exist — Task 6 replaces its body.
  - `conftest.ScriptedUi` — a `Ui` implementation driven by canned answers, recording every `write` as `(style, text)`.

`ui.write` on a `ScriptedUi` appends to `ui.written`. `choose` pops from `ui.choices` (a list of `int | None` row indices; `None` means Esc). `ask_line` pops from `ui.lines`. `confirm` pops from `ui.confirmations`.

- [ ] **Step 1: Write the test double**

```python
# tests/tui/conftest.py
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from hardy import config as configuration
from hardy.tui.ports import Choice


class ScriptedUi:
    """A Ui driven by canned answers. Models the interaction, not the prompts.

    Better than feeding strings to input(): a caller picks a *row* and answers a
    *confirmation*, which is what the real selector asks for.
    """

    def __init__(self, choices=None, lines=None, confirmations=None):
        self.choices = list(choices or [])
        self.lines = list(lines or [])
        self.confirmations = list(confirmations or [])
        self.written: list[tuple[str, str]] = []
        self.asked: list[str] = []

    # -- Ui ---------------------------------------------------------------
    def write(self, text: str, *, style: str = "system") -> None:
        self.written.append((style, text))

    async def choose(self, title, rows: Sequence[Choice], *, current=0, subtitle="") -> Choice | None:
        self.asked.append(title)
        index = self.choices.pop(0) if self.choices else None
        return None if index is None else rows[index]

    async def ask_line(self, prompt: str) -> str | None:
        self.asked.append(prompt)
        return self.lines.pop(0) if self.lines else None

    async def confirm(self, question: str) -> bool:
        self.asked.append(question)
        return self.confirmations.pop(0) if self.confirmations else False

    @property
    def from_thread(self):
        return _Blocking(self)

    # -- helpers used by tests -------------------------------------------
    @property
    def text(self) -> str:
        return "\n".join(text for _, text in self.written)


class _Blocking:
    """No loop to marshal onto in tests, so calls go straight through."""

    def __init__(self, ui: ScriptedUi):
        self._ui = ui

    def write(self, text: str, *, style: str = "system") -> None:
        self._ui.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        import asyncio

        return asyncio.run(self._ui.choose(title, rows, current=current, subtitle=subtitle))

    def ask_line(self, prompt: str):
        import asyncio

        return asyncio.run(self._ui.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        import asyncio

        return asyncio.run(self._ui.confirm(question))


@pytest.fixture
def ui() -> ScriptedUi:
    return ScriptedUi()


@pytest.fixture
def settings(tmp_path: Path) -> configuration.Config:
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        workspace=tmp_path / "workspace",
        path=tmp_path / "config.toml",
    )
```

- [ ] **Step 2: Write the failing test**

```python
# tests/tui/test_handlers.py
from __future__ import annotations

from hardy.tui import commands, handlers
from hardy.tui.ports import State


def test_the_registry_holds_the_specified_commands():
    names = [c.name for c in handlers.build_registry()]
    assert names == ["help", "model", "status", "doctor", "clear", "exit", "quit"]


def test_only_read_only_commands_are_safe_while_a_turn_runs():
    """A command added later must default to refused, so this pins the set."""
    safe = {c.name for c in handlers.build_registry() if c.safe_in_flight}
    assert safe == {"help", "status", "clear", "exit", "quit"}


def test_quit_is_an_alias_entry_sharing_the_exit_handler():
    registry = {c.name: c for c in handlers.build_registry()}
    assert registry["quit"].alias_of == "exit"
    assert registry["quit"].handler is registry["exit"].handler


async def test_help_lists_canonical_commands_with_their_hints(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "/model" in ui.text and "[identity]" in ui.text
    assert "/quit" not in ui.text          # alias entries do not pad the list


async def test_help_says_what_clear_does_not_do(ui, settings):
    await handlers.handle_help(ui, "", State(config=settings, session=None))
    assert "deletes nothing" in ui.text.lower()


async def test_status_reports_the_live_configuration(ui, settings):
    await handlers.handle_status(ui, "", State(config=settings, session=None))
    assert "claude-opus-5" in ui.text
    assert str(settings.workspace) in ui.text
    assert str(settings.path) in ui.text


async def test_exit_marks_the_state_done(ui, settings):
    state = await handlers.handle_exit(ui, "", State(config=settings, session=None))
    assert state.done is True


async def test_clear_asks_the_ui_to_clear_and_deletes_nothing(ui, settings):
    state = State(config=settings, session=None)
    returned = await handlers.handle_clear(ui, "", state)
    assert returned == state
    assert ("clear", "") in ui.written


async def test_doctor_runs_the_checks_off_the_event_loop(ui, settings, monkeypatch):
    """It spawns subprocesses, so it must not be awaited inline on the loop."""
    seen: list[str] = []

    def fake_run_checks(config, *, deep=False):
        import threading

        seen.append(threading.current_thread().name)
        return [doctor.Check(name="lean", healthy=True, detail="found")]

    monkeypatch.setattr(handlers.doctor, "run_checks", fake_run_checks)
    await handlers.handle_doctor(ui, "", State(config=settings, session=None))
    assert seen and seen[0] != "MainThread"
    assert "lean" in ui.text
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_handlers.py -v`
Expected: FAIL — no `handlers` module

- [ ] **Step 4: Give `doctor` a pure formatter**

`doctor.py` exposes `run_checks(config, *, deep=False) -> list[Check]` (`doctor.py:105`) and `report(checks) -> int` (`doctor.py:134`), which **prints** and returns an exit code. There is no pure formatter, so `/doctor` cannot reuse it without writing to stdout itself. Add one and make `report` the only thing that prints:

```python
def describe(checks: list[Check]) -> list[str]:
    """The report as lines, so a caller that is not a terminal can render it."""
    return [
        f"{check.name:9} {'OK' if check.healthy else 'MISSING/FAILED':14} {check.detail}"
        for check in checks
    ]
```

Then rewrite `report` to `for line in describe(checks): print(line)` followed by its existing exit-code logic, so both paths render identically. Read `Check` (`doctor.py:19`) first and use its real field names — the snippet above assumes `name`, `healthy`, `detail`; correct it to match.

Note that `cli.py:227` has a `_print_report` expecting a different shape (`report.tools`, `report.mathlib_ready`). It is not on any live path from `run_checks`; leave it alone rather than trying to reconcile it here.

- [ ] **Step 5: Write the implementation**

```python
# src/hardy/tui/handlers.py
"""What each slash command does.

Every handler is a coroutine because it runs on the application's event loop and
may need to await a selector on that same loop. Work that blocks -- subprocesses,
in `/doctor`'s case -- goes to a thread so the input box stays responsive.
"""

from __future__ import annotations

import asyncio
import dataclasses

from .. import catalog, doctor
from .. import config as configuration
from .commands import Command, canonical
from .ports import State, Ui


async def handle_help(ui: Ui, argument: str, state: State) -> State:
    ui.write("Commands", style="normal")
    # build_registry is idempotent -- the entries are module-level functions,
    # not runtime registrations -- so listing a freshly built one describes
    # exactly the registry in use. If commands ever become dynamic, this has
    # to take the live registry instead.
    for command in canonical(build_registry()):
        name = f"/{command.name}"
        if command.argument_hint:
            name = f"{name} {command.argument_hint}"
        ui.write(f"  {name:24} {command.summary}")
    ui.write("  /clear deletes nothing: it clears the screen only. Your scrollback,")
    ui.write("  your transcript on disk, and the model's conversation all continue.")
    return state


async def handle_status(ui: Ui, argument: str, state: State) -> State:
    config = state.config
    ui.write("Session", style="normal")
    ui.write(f"  Model:        {config.model}")
    ui.write(f"  Workspace:    {config.workspace}")
    ui.write(f"  Lean project: {config.lean_project or 'current directory'}")
    ui.write(f"  Config file:  {config.path}")
    ui.write(f"  Transcript:   {config.workspace / 'transcript.jsonl'}")
    if state.turn_running:
        ui.write("  A turn is still running.")
    return state


async def handle_clear(ui: Ui, argument: str, state: State) -> State:
    # A dedicated style rather than a Ui method: clearing is a rendering
    # concern, and PlainUi has nothing meaningful to do with it.
    ui.write("", style="clear")
    return state


async def handle_doctor(ui: Ui, argument: str, state: State) -> State:
    # run_checks spawns subprocesses. Awaiting it inline would freeze the box.
    checks = await asyncio.to_thread(doctor.run_checks, state.config)
    for line in doctor.describe(checks):
        ui.write(f"  {line}")
    return state


async def handle_exit(ui: Ui, argument: str, state: State) -> State:
    return dataclasses.replace(state, done=True)


async def handle_model(ui: Ui, argument: str, state: State) -> State:
    raise NotImplementedError("Task 6 implements the model selector")


def build_registry() -> list[Command]:
    exit_command = Command(
        "exit", "leave the session", handle_exit, safe_in_flight=True
    )
    return [
        Command("help", "list these commands", handle_help, safe_in_flight=True),
        Command("model", "switch the model", handle_model, argument_hint="[identity]"),
        Command("status", "show workspace, model, and paths", handle_status, safe_in_flight=True),
        Command("doctor", "check that Lean and LaTeX are usable", handle_doctor),
        Command("clear", "clear the screen; deletes nothing", handle_clear, safe_in_flight=True),
        exit_command,
        Command(
            "quit", "leave the session", exit_command.handler,
            alias_of="exit", safe_in_flight=True,
        ),
    ]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui -v`
Expected: PASS. The `handle_model` test does not exist yet, so nothing exercises the `NotImplementedError`.

- [ ] **Step 7: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui tests/tui src/hardy/doctor.py
git commit -m "feat: implement the read-only slash commands behind the Ui port"
```

---

### Task 6: The `/model` selector logic

Read spec section: **The `/model` selector**. Read `tests/test_model_command.py` in full before starting — every assertion in it must survive.

**Files:**
- Modify: `src/hardy/tui/handlers.py` (replace `handle_model`)
- Modify: `src/hardy/cli.py:74-118` — delete `model_command` and `_show_models`; the logic moves here
- Test: rewrite `tests/test_model_command.py`

**Interfaces:**
- Consumes: `catalog.available()`, `catalog.describe()`, `configuration.write_setting`, `ScriptedUi`.
- Produces: `handlers.handle_model(ui, argument, state) -> State` and `handlers.model_rows(config) -> list[Choice]`.

`model_rows` returns catalog entries plus a trailing `Choice("", "Other…", ...)`, prepending the active identity when it is absent from the catalog.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_command.py  (rewritten)
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hardy import catalog
from hardy import config as configuration
from hardy.tui import handlers
from hardy.tui.ports import State
from tests.tui.conftest import ScriptedUi


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch):
    for variable in configuration.SETTINGS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("HARDY_CONFIG", raising=False)


class Recorder:
    """Stands in for the live session; records the models it is switched to."""

    def __init__(self, fails: bool = False):
        self.models: list[str] = []
        self.fails = fails

    def switch_model(self, model: str) -> None:
        if self.fails:
            raise RuntimeError("the Claude backend needs claude-agent-sdk.")
        self.models.append(model)


def settings(tmp_path: Path, **overrides) -> configuration.Config:
    values = {
        "model": "claude-opus-5",
        "lean_command": ("lake", "env", "lean"),
        "lean_project": None,
        "lean_timeout": 180.0,
        "latex_command": ("pdflatex",),
        "workspace": tmp_path / "workspace",
        "path": tmp_path / "config.toml",
    }
    values.update(overrides)
    return configuration.Config(**values)


def state(tmp_path: Path, session, **overrides) -> State:
    return State(config=settings(tmp_path, **overrides), session=session)


async def test_naming_a_model_switches_the_live_session(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-sonnet-5"
    assert session.models == ["claude-sonnet-5"]


async def test_selecting_a_row_picks_that_model(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(choices=[1], confirmations=[False])
    await handlers.handle_model(ui, "", state(tmp_path, session))
    assert session.models == [catalog.available()[1].identifier]


async def test_escaping_the_selector_changes_nothing(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(choices=[None])
    result = await handlers.handle_model(ui, "", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []


async def test_an_unlisted_identity_is_accepted_as_typed(tmp_path: Path):
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-experimental-9", state(tmp_path, session))
    assert result.config.model == "claude-experimental-9"
    assert session.models == ["claude-experimental-9"]


async def test_the_other_row_prompts_for_an_identity(tmp_path: Path):
    session = Recorder()
    rows = handlers.model_rows(settings(tmp_path))
    ui = ScriptedUi(choices=[len(rows) - 1], lines=["claude-experimental-9"], confirmations=[False])
    await handlers.handle_model(ui, "", state(tmp_path, session))
    assert session.models == ["claude-experimental-9"]


@pytest.mark.parametrize("typed", ["", "   ", None])
async def test_a_blank_custom_identity_cancels(tmp_path: Path, typed):
    """Today a blank answer keeps the current model. It still must."""
    session = Recorder()
    rows = handlers.model_rows(settings(tmp_path))
    ui = ScriptedUi(choices=[len(rows) - 1], lines=[typed] if typed is not None else [])
    result = await handlers.handle_model(ui, "", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert session.models == []


async def test_a_failed_switch_leaves_the_model_unchanged(tmp_path: Path):
    session = Recorder(fails=True)
    ui = ScriptedUi()
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-opus-5"
    assert "Model unchanged" in ui.text


def test_the_rows_mark_the_current_model(tmp_path: Path):
    rows = handlers.model_rows(settings(tmp_path, model="claude-sonnet-5"))
    current = [row for row in rows if "current" in row.note]
    assert len(current) == 1 and current[0].value == "claude-sonnet-5"


def test_an_unlisted_current_model_gets_its_own_row(tmp_path: Path):
    """Otherwise no row represents what is running and the pointer has nowhere to start."""
    rows = handlers.model_rows(settings(tmp_path, model="claude-experimental-9"))
    assert rows[0].value == "claude-experimental-9"
    assert "not in catalog" in rows[0].note


def test_the_last_row_is_the_escape_hatch(tmp_path: Path):
    assert handlers.model_rows(settings(tmp_path))[-1].label.startswith("Other")


async def test_saving_writes_the_model_without_losing_other_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('# hand written\nmodel = "claude-opus-5"\nlean_project = "~/lean"\n', encoding="utf-8")
    ui = ScriptedUi(confirmations=[True])
    await handlers.handle_model(ui, "claude-haiku-4-5", state(tmp_path, Recorder(), path=path))
    text = path.read_text(encoding="utf-8")
    assert 'model = "claude-haiku-4-5"' in text
    assert 'lean_project = "~/lean"' in text and "# hand written" in text
    assert configuration.load(path).model == "claude-haiku-4-5"


async def test_saving_targets_the_requested_config_even_when_absent(tmp_path: Path):
    requested = tmp_path / "fresh" / "config.toml"
    start = configuration.load(requested, model="claude-opus-5")
    ui = ScriptedUi(confirmations=[True])
    await handlers.handle_model(ui, "claude-sonnet-5", State(config=start, session=Recorder()))
    assert 'model = "claude-sonnet-5"' in requested.read_text(encoding="utf-8")


async def test_declining_to_save_leaves_the_config_file_untouched(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('model = "claude-opus-5"\n', encoding="utf-8")
    ui = ScriptedUi(confirmations=[False])
    await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, Recorder(), path=path))
    assert path.read_text(encoding="utf-8") == 'model = "claude-opus-5"\n'


async def test_declining_to_save_does_not_pretend_to_revert(tmp_path: Path):
    """switch_model has already rewritten session.json. Say so, do not lie."""
    session = Recorder()
    ui = ScriptedUi(confirmations=[False])
    result = await handlers.handle_model(ui, "claude-sonnet-5", state(tmp_path, session))
    assert result.config.model == "claude-sonnet-5"
    assert session.models == ["claude-sonnet-5"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --extra test pytest tests/test_model_command.py -v`
Expected: FAIL — `NotImplementedError` from the Task 5 stub

- [ ] **Step 3: Write the implementation**

Replace `handle_model` in `src/hardy/tui/handlers.py` and add `model_rows`:

```python
OTHER = "…other"           # sentinel; not a legal model identity


def model_rows(config) -> list[Choice]:
    current = (config.model or "").strip()
    entries = catalog.available()
    rows: list[Choice] = []
    if current and not catalog.find(current):
        # An unlisted identity is legitimate, so it needs a row of its own --
        # otherwise nothing shows what is actually running.
        rows.append(Choice(current, current, "current, not in catalog"))
    for entry in entries:
        note = entry.note
        if entry.identifier.lower() == current.lower():
            note = f"{note}   (current)" if note else "(current)"
        rows.append(Choice(entry.identifier, entry.identifier, note))
    rows.append(Choice(OTHER, "Other…", "type an identity the catalog lacks"))
    return rows


async def _chosen_identity(ui: Ui, argument: str, config) -> str | None:
    if argument.strip():
        return argument.strip()
    rows = model_rows(config)
    current = next((i for i, row in enumerate(rows) if "current" in row.note), 0)
    picked = await ui.choose(
        "Select model", rows, current=current,
        subtitle="Runs through your Claude Code subscription.",
    )
    if picked is None:
        return None
    if picked.value != OTHER:
        return picked.value
    typed = await ui.ask_line("Model identity: ")
    # A blank answer keeps the current model, as it always has.
    return typed.strip() if typed and typed.strip() else None


async def handle_model(ui: Ui, argument: str, state: State) -> State:
    identity = await _chosen_identity(ui, argument, state.config)
    if identity is None:
        return state

    entry = catalog.describe(identity)
    if state.session is not None:
        try:
            state.session.switch_model(entry.identifier)
        except RuntimeError as error:
            ui.write(f"{error} Model unchanged.", style="error")
            return state
    ui.write(f"Model: {entry.identifier}")

    config = dataclasses.replace(state.config, model=entry.identifier)
    destination = state.config.path
    # The live session has already moved and stays moved. This only decides
    # whether the *config file* follows.
    if await ui.confirm(f"Save this as the default in {destination}?"):
        try:
            configuration.write_setting(destination, "model", entry.identifier)
            ui.write(f"Saved to {destination}.")
            config = dataclasses.replace(config, path=destination)
        except OSError as error:
            ui.write(f"Could not write {destination}: {error}", style="error")
    return dataclasses.replace(state, config=config)
```

Add `Choice` to the `.ports` import at the top of `handlers.py`.

- [ ] **Step 4: Delete the superseded CLI code**

Remove `_show_models` and `model_command` from `src/hardy/cli.py` (lines 63-118). Leave `_chat` alone for now — Task 12 replaces it. Confirm nothing else referenced them:

```bash
grep -rn "model_command\|_show_models" src tests
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_model_command.py tests/tui -v`
Expected: PASS (17 tests in `test_model_command.py`)

- [ ] **Step 6: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui/handlers.py src/hardy/cli.py tests/test_model_command.py
git commit -m "feat: choose a model by row instead of by typing a number"
```

---

### Task 7: Dispatch rules and the plain-mode session

Read spec sections: **Dispatch on submit**, **What is permitted while a turn is in flight**, **Degradation and error handling**.

This task ends with a **working session** — `hardy --plain` runs the new registry end to end. The TTY path still uses the old `_chat` until Task 12.

**Files:**
- Create: `src/hardy/tui/dispatch.py`, `src/hardy/tui/plain.py`
- Modify: `src/hardy/tui/__init__.py`, `src/hardy/cli.py` (add `--plain`, route to `run_session` when plain)
- Test: `tests/tui/test_dispatch.py`, `tests/tui/test_plain.py`

**Interfaces:**
- Produces:
  - `dispatch.classify(text, commands, *, turn_running) -> Outcome` where `Outcome` is a frozen dataclass `Outcome(kind, command=None, argument="", message="")` and `kind` is one of `"empty" | "send" | "command" | "unknown" | "refused"`.
  - `plain.PlainUi(write_line)` implementing `Ui`.
  - `plain.run(config, session, *, out=print, read=input) -> int`.
  - `tui.run_session(config, session, *, plain=False) -> int`.

- [ ] **Step 1: Write the failing test for dispatch**

```python
# tests/tui/test_dispatch.py
from __future__ import annotations

from hardy.tui import dispatch, handlers


def registry():
    return handlers.build_registry()


def test_plain_text_is_sent_to_the_model():
    assert dispatch.classify("is pi irrational?", registry(), turn_running=False).kind == "send"


def test_blank_input_does_nothing():
    assert dispatch.classify("   ", registry(), turn_running=False).kind == "empty"


def test_a_known_command_dispatches_with_its_argument():
    outcome = dispatch.classify("/model claude-sonnet-5", registry(), turn_running=False)
    assert outcome.kind == "command"
    assert outcome.command.name == "model"
    assert outcome.argument == "claude-sonnet-5"


def test_an_unresolved_command_is_an_error_not_a_turn():
    """The defect the whole spec opens with: /mo must never reach the model."""
    outcome = dispatch.classify("/mo", registry(), turn_running=False)
    assert outcome.kind == "unknown"
    assert "/mo" in outcome.message and "/help" in outcome.message


def test_a_leading_space_escapes_command_interpretation():
    outcome = dispatch.classify(" /usr/bin is a path", registry(), turn_running=False)
    assert outcome.kind == "send"


def test_a_turn_in_flight_refuses_another_submission():
    assert dispatch.classify("more maths", registry(), turn_running=True).kind == "refused"


def test_a_turn_in_flight_refuses_model_by_name():
    """Switching mid-turn would misattribute the abandoned turn's provider session."""
    outcome = dispatch.classify("/model", registry(), turn_running=True)
    assert outcome.kind == "refused"
    assert "turn" in outcome.message.lower()


def test_a_turn_in_flight_refuses_doctor():
    assert dispatch.classify("/doctor", registry(), turn_running=True).kind == "refused"


def test_a_turn_in_flight_still_allows_read_only_commands():
    for text in ("/status", "/help", "/clear", "/exit", "/quit"):
        assert dispatch.classify(text, registry(), turn_running=True).kind == "command", text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_dispatch.py -v`
Expected: FAIL — no `dispatch` module

- [ ] **Step 3: Write dispatch**

```python
# src/hardy/tui/dispatch.py
"""What a submitted line means. Pure, so both shells decide it identically.

The unresolved case is the point. Letting `/mo` fall through to the model is
the defect this rework exists to remove, so it is an outcome here rather than
an oversight in whichever shell happens to be running.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .commands import Command, resolve


@dataclass(frozen=True)
class Outcome:
    kind: str                      # empty | send | command | unknown | refused
    command: Command | None = None
    argument: str = ""
    message: str = ""


def classify(text: str, commands: Sequence[Command], *, turn_running: bool) -> Outcome:
    # A leading space is the escape hatch for text that must start with a
    # slash; `/` itself is reserved.
    if text.startswith(" "):
        stripped = text.strip()
        return Outcome("send", argument=stripped) if stripped else Outcome("empty")

    if not text.strip():
        return Outcome("empty")

    if not text.startswith("/"):
        if turn_running:
            return Outcome("refused", message="A turn is still running. Wait for it to finish.")
        return Outcome("send", argument=text.strip())

    found = resolve(text, commands)
    if found is None:
        name = text.split(" ", 1)[0]
        return Outcome(
            "unknown",
            message=f"unknown command {name} — press Tab to complete, or /help for the list",
        )
    command, argument = found
    if turn_running and not command.safe_in_flight:
        return Outcome(
            "refused",
            message=f"/{command.name} cannot run while a turn is still running.",
        )
    return Outcome("command", command=command, argument=argument)
```

- [ ] **Step 4: Write the failing test for plain mode**

```python
# tests/tui/test_plain.py
from __future__ import annotations

from hardy import runner
from hardy.tui import plain


class FakeSession:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> str:
        self.sent.append(text)
        return f"reply to {text}"

    def switch_model(self, model: str) -> None:
        pass


def run(settings, replies, session=None):
    session = session or FakeSession()
    written: list[str] = []
    queue = iter(replies)

    def read(prompt: str) -> str:
        try:
            return next(queue)
        except StopIteration as stop:
            raise EOFError from stop

    code = plain.run(settings, session, out=written.append, read=read)
    return code, "\n".join(written), session


def test_the_warning_appears_before_the_first_prompt(settings):
    _, text, _ = run(settings, [])
    assert runner.WARNING in text


def test_a_turn_is_marked_and_answered(settings):
    _, text, session = run(settings, ["is pi irrational?"])
    assert session.sent == ["is pi irrational?"]
    assert "> is pi irrational?" in text
    assert "● reply to is pi irrational?" in text


def test_an_unknown_command_never_reaches_the_model(settings):
    _, text, session = run(settings, ["/mo"])
    assert session.sent == []
    assert "unknown command /mo" in text


def test_exit_leaves_with_a_zero_status(settings):
    code, _, session = run(settings, ["/exit", "unreached"])
    assert code == 0
    assert session.sent == []


def test_end_of_input_leaves_cleanly(settings):
    code, _, _ = run(settings, [])
    assert code == 0


def test_status_works_without_a_terminal(settings):
    _, text, _ = run(settings, ["/status"])
    assert str(settings.workspace) in text
```

- [ ] **Step 5: Write plain mode and the entry point**

```python
# src/hardy/tui/plain.py
"""The session without a terminal: pipes, CI, dumb terminals, and --plain.

Same registry, same dispatch rules, same banner as the real shell. Only the
drawing differs, which is what keeps `hardy < script.txt` working.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence
from typing import Any

from . import banner, dispatch, transcript
from .handlers import build_registry
from .ports import Choice, State

WIDTH = 80


class PlainUi:
    """A Ui with no event loop. Its `from_thread` calls straight through."""

    def __init__(self, out: Callable[[str], None], read: Callable[[str], str]):
        self._out = out
        self._read = read

    def write(self, text: str, *, style: str = "system") -> None:
        if style == "clear":
            return                                  # nothing to clear
        if style in {"normal", "warning"}:
            self._out(text)
            return
        for line in transcript.notice_lines(text, WIDTH) or [""]:
            self._out(line)

    async def choose(self, title, rows: Sequence[Choice], *, current=0, subtitle="") -> Choice | None:
        self._out("")
        self._out(f"  {title}")
        if subtitle:
            self._out(f"  {subtitle}")
        for number, row in enumerate(rows, start=1):
            mark = "*" if number - 1 == current else " "
            note = f"  {row.note}" if row.note else ""
            self._out(f"  {mark} {number:>3}  {row.label}{note}")
        answer = (await self.ask_line("Choice (number, or blank to cancel): ") or "").strip()
        if not answer.isdigit():
            return None
        index = int(answer)
        return rows[index - 1] if 1 <= index <= len(rows) else None

    async def ask_line(self, prompt: str) -> str | None:
        try:
            return self._read(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    async def confirm(self, question: str) -> bool:
        answer = await self.ask_line(f"{question} [y/N] ")
        return (answer or "").strip().lower() in {"y", "yes"}

    @property
    def from_thread(self) -> Any:
        return _Straight(self)


class _Straight:
    def __init__(self, ui: PlainUi):
        self._ui = ui

    def write(self, text: str, *, style: str = "system") -> None:
        self._ui.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        return asyncio.run(self._ui.choose(title, rows, current=current, subtitle=subtitle))

    def ask_line(self, prompt: str):
        return asyncio.run(self._ui.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        return asyncio.run(self._ui.confirm(question))


def run(config, session, *, out: Callable[[str], None] = print, read: Callable[[str], str] = input) -> int:
    ui = PlainUi(out, read)
    for style, text in banner.lines(config):
        ui.write(text, style=style)
    out("")

    registry = build_registry()
    state = State(config=config, session=session)
    while not state.done:
        try:
            text = read("> ")
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0

        outcome = dispatch.classify(text, registry, turn_running=False)
        if outcome.kind == "empty":
            continue
        if outcome.kind in {"unknown", "refused"}:
            ui.write(outcome.message, style="error")
            continue
        if outcome.kind == "command":
            state = asyncio.run(outcome.command.handler(ui, outcome.argument, state))
            continue

        for line in transcript.user_lines(outcome.argument, WIDTH):
            out(line)
        try:
            reply = session.send(outcome.argument)
        except Exception as error:                      # noqa: BLE001 - never lose the session
            ui.write(f"{type(error).__name__}: {error}", style="error")
            continue
        for line in transcript.hardy_lines(reply, WIDTH):
            out(line)
        out("")
    return 0
```

```python
# src/hardy/tui/__init__.py
"""Hardy's interactive session."""

from __future__ import annotations

import os
import sys


def _is_interactive() -> bool:
    if os.environ.get("HARDY_PLAIN"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_session(config, session, *, plain: bool = False) -> int:
    from . import plain as plain_mode

    if plain or not _is_interactive():
        return plain_mode.run(config, session)
    # Task 12 routes the interactive path to tui.shell here.
    return plain_mode.run(config, session)
```

- [ ] **Step 6: Wire `--plain` into the CLI**

In `src/hardy/cli.py`, add to `build_parser()` alongside the other global flags:

```python
parser.add_argument("--plain", action="store_true", help="use the line-based session with no terminal control")
```

and in `_chat`, before building the banner, route out when plain is requested:

```python
def _chat(config: configuration.Config, parser: argparse.ArgumentParser, *, plain: bool = False) -> int:
    from .tui import run_session

    session = MathematicsSession(config.workspace, runtime_factory(str(config.model)), config.lean_command, config.latex_command, _confirm_assumption, lean_project=config.lean_project, lean_timeout=config.lean_timeout)
    if plain:
        return run_session(config, session, plain=True)
    # The old loop below is replaced in Task 12.
    ...
```

and in `main()`: `return _chat(config, parser, plain=args.plain)`.

- [ ] **Step 7: Run the tests and the suite**

Run: `uv run --extra test pytest -q`
Expected: PASS, no regressions.

Then confirm by hand that the new path really runs:

```bash
echo "/status" | uv run hardy --plain
```

Expected: the banner including the warning, then the status block.

- [ ] **Step 8: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui src/hardy/cli.py tests/tui
git commit -m "feat: run the session from the registry, refusing unresolved commands"
```

---

### Task 8: The inline selector widget

Read spec section: **The `/model` selector**. Use the API idioms confirmed in Task 1.

**Files:**
- Create: `src/hardy/tui/select.py`
- Test: `tests/tui/test_select.py`

**Interfaces:**
- Consumes: `ports.Choice`.
- Produces: `async select.choose(title, rows, *, current=0, subtitle="", input=None, output=None) -> Choice | None`.

`input`/`output` are `prompt_toolkit` objects, defaulted to `None` so production callers use the ambient app session and tests inject pipes.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_select.py
from __future__ import annotations

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from hardy.tui import select
from hardy.tui.ports import Choice

ROWS = [
    Choice("a", "alpha", "first"),
    Choice("b", "beta"),
    Choice("c", "gamma"),
]


async def drive(keys: str, *, current: int = 0):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        with create_app_session(input=pipe, output=DummyOutput()):
            return await select.choose("Pick", ROWS, current=current)


async def test_enter_takes_the_row_under_the_pointer():
    assert (await drive("\r")).value == "a"


async def test_down_then_enter_takes_the_second_row():
    assert (await drive("\x1b[B\r")).value == "b"


async def test_up_stops_at_the_top():
    assert (await drive("\x1b[A\x1b[A\r")).value == "a"


async def test_the_pointer_starts_where_it_is_told():
    assert (await drive("\r", current=2)).value == "c"


async def test_a_number_key_selects_immediately():
    assert (await drive("2")).value == "b"


async def test_escape_cancels():
    assert await drive("\x1b") is None


async def test_zero_selects_nothing():
    """Accelerators are 1-9 only; a two-digit row can never be read this way."""
    assert (await drive("0\r")).value == "a"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_select.py -v`
Expected: FAIL — no `select` module

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/select.py
"""The inline list a question is asked with.

Not full screen: it renders where the cursor is, so the transcript above stays
in the terminal's own scrollback. One implementation serves both callers --
awaited directly by a command handler on the event loop, or scheduled onto that
loop from a tool thread.
"""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .ports import Choice

# 1-9 only. An accelerator that fires on each keypress can never read a
# two-digit row, because `1` would have selected row 1 before `0` arrived.
ACCELERATORS = "123456789"


def _bindings(rows: Sequence[Choice], cursor: dict[str, int]) -> KeyBindings:
    keys = KeyBindings()

    @keys.add("up")
    def _up(event) -> None:
        cursor["at"] = max(0, cursor["at"] - 1)

    @keys.add("down")
    def _down(event) -> None:
        cursor["at"] = min(len(rows) - 1, cursor["at"] + 1)

    @keys.add("enter")
    def _pick(event) -> None:
        event.app.exit(result=rows[cursor["at"]])

    @keys.add("escape", eager=True)
    @keys.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    for offset, key in enumerate(ACCELERATORS[: len(rows)]):
        @keys.add(key)
        def _jump(event, index: int = offset) -> None:
            event.app.exit(result=rows[index])

    return keys


async def choose(
    title: str,
    rows: Sequence[Choice],
    *,
    current: int = 0,
    subtitle: str = "",
    input=None,
    output=None,
) -> Choice | None:
    if not rows:
        return None
    cursor = {"at": min(max(current, 0), len(rows) - 1)}

    def render() -> FormattedText:
        parts: list[tuple[str, str]] = [("class:select.title", f"  {title}\n")]
        if subtitle:
            parts.append(("class:select.hint", f"  {subtitle}\n"))
        parts.append(("", "\n"))
        for index, row in enumerate(rows):
            here = index == cursor["at"]
            number = f"{index + 1}." if index < len(ACCELERATORS) else "  "
            style = "class:select.row.current" if here else "class:select.row"
            parts.append((style, f"{'❯' if here else ' '} {number} {row.label}"))
            if row.note:
                parts.append(("class:select.hint", f"   {row.note}"))
            parts.append(("", "\n"))
        parts.append(("class:select.hint", "\n  ↑↓ move · 1-9 jump · enter select · esc cancel\n"))
        return FormattedText(parts)

    application: Application = Application(
        layout=Layout(HSplit([Window(FormattedTextControl(render), dont_extend_height=True)])),
        key_bindings=_bindings(rows, cursor),
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )
    return await application.run_async()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui/test_select.py -v`
Expected: PASS (7 tests)

If `create_pipe_input()` is not a context manager in the installed version, use the plain call and `pipe.close()` in a `finally` — Task 1's findings record which applies.

- [ ] **Step 5: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui/select.py tests/tui/test_select.py
git commit -m "feat: ask a question with an inline arrow-key list"
```

---

### Task 9: The input box, ghost text, and `PromptToolkitUi`

Read spec sections: **The input box**, **Ghost text**, **The `Ui` port**.

**Files:**
- Create: `src/hardy/tui/shell.py`
- Test: `tests/tui/test_shell.py`

**Interfaces:**
- Produces:
  - `shell.CommandSuggester(registry)` — a `prompt_toolkit` `AutoSuggest` returning `Suggestion(suggest(text, registry))`.
  - `shell.CommandCompleter(registry)` — a `Completer` yielding one `Completion` per `complete()` match.
  - `shell.Shell(config, session, registry, *, input=None, output=None)` with `run() -> int`, and implementing `Ui` (`write`, `choose`, `ask_line`, `confirm`, `from_thread`).
  - `shell.STYLE` — a `prompt_toolkit` `Style` mapping the style names from Task 2.

This task builds the box and its completion only. The turn worker, Esc, in-flight gating, and shutdown arrive in Tasks 10-11; until then Enter on plain text calls `session.send` inline.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_shell.py
from __future__ import annotations

from io import StringIO

from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.tui import handlers, shell


def suggester():
    return shell.CommandSuggester(handlers.build_registry())


class FakeBuffer:
    def __init__(self, text: str):
        self.document = Document(text, len(text))
        self.text = text


def test_a_unique_prefix_is_suggested():
    suggestion = suggester().get_suggestion(FakeBuffer("/mo"), Document("/mo", 3))
    assert suggestion is not None and suggestion.text == "del"


def test_an_alias_prefix_suggests_the_alias_not_the_canonical_name():
    suggestion = suggester().get_suggestion(FakeBuffer("/q"), Document("/q", 2))
    assert suggestion is not None and suggestion.text == "uit"


def test_an_ambiguous_prefix_suggests_nothing():
    registry = handlers.build_registry()
    ambiguous = shell.CommandSuggester(registry)
    # /h matches help only, so build a genuine clash to test the rule.
    from hardy.tui.commands import Command

    async def _noop(ui, argument, state):
        return state

    clashing = [Command("status", "s", _noop), Command("setup", "s", _noop)]
    assert shell.CommandSuggester(clashing).get_suggestion(FakeBuffer("/s"), Document("/s", 2)) is None
    assert ambiguous.get_suggestion(FakeBuffer("hello"), Document("hello", 5)) is None


def test_the_completer_offers_every_match():
    completer = shell.CommandCompleter(handlers.build_registry())
    offered = [c.text for c in completer.get_completions(Document("/", 1), None)]
    assert "/model" in offered and "/help" in offered


def test_the_style_defines_every_name_the_ports_declare():
    names = dict(shell.STYLE.style_rules)
    for style in ("user", "hardy", "system", "error", "warning", "hint"):
        assert any(rule.startswith(style) for rule in names), style


async def test_typing_and_submitting_reaches_the_session(settings):
    """The box must actually feed the session, not merely render."""
    sent: list[str] = []

    class FakeSession:
        def send(self, text: str) -> str:
            sent.append(text)
            return "answered"

        def switch_model(self, model): ...

    buffer = StringIO()
    with create_pipe_input() as pipe:
        pipe.send_text("is pi irrational?\r/exit\r")
        code = shell.Shell(
            settings, FakeSession(), handlers.build_registry(),
            input=pipe,
            output=Vt100_Output(buffer, lambda: Size(rows=24, columns=80)),
        ).run()
    assert code == 0
    assert sent == ["is pi irrational?"]


async def test_the_rendered_output_dims_the_ghost_text(settings):
    """DummyOutput cannot prove this; a real Vt100 output over a buffer can."""
    buffer = StringIO()
    with create_pipe_input() as pipe:
        pipe.send_text("/mo")
        pipe.send_text("\x03")                 # Ctrl+C to leave
        shell.Shell(
            settings, None, handlers.build_registry(),
            input=pipe,
            output=Vt100_Output(buffer, lambda: Size(rows=24, columns=80)),
        ).run()
    written = buffer.getvalue()
    assert "del" in written
    assert "\x1b[" in written                  # styling was emitted, not plain text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_shell.py -v`
Expected: FAIL — no `shell` module

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/shell.py
"""The terminal Hardy actually draws.

Not full screen. The transcript is printed into the terminal's own scrollback
through `patch_stdout`, so it stays selectable and survives the session; only
the input box below it is redrawn. Every prompting method is a coroutine
because a selector reads keys the event loop delivers, and blocking that loop
to wait for them would deadlock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from . import banner, dispatch, select, transcript
from .commands import Command, complete, suggest
from .ports import Choice, State

STYLE = Style.from_dict({
    "user": "",
    "hardy": "bold",
    "system": "#888888",
    "error": "#cc4444",
    "warning": "#ccaa00",
    "hint": "#888888",
    "select.title": "bold",
    "select.hint": "#888888",
    "select.row": "",
    "select.row.current": "reverse",
})

NARROW = 40


class CommandSuggester(AutoSuggest):
    """Dim inline completion. Only ever appends, never rewrites what was typed."""

    def __init__(self, registry: Sequence[Command]):
        self._registry = registry

    def get_suggestion(self, buffer, document) -> Suggestion | None:
        if "\n" in document.text or document.cursor_position != len(document.text):
            return None
        tail = suggest(document.text, self._registry)
        return Suggestion(tail) if tail else None


class CommandCompleter(Completer):
    def __init__(self, registry: Sequence[Command]):
        self._registry = registry

    def get_completions(self, document, complete_event):
        typed = document.text_before_cursor
        for command in complete(typed, self._registry):
            yield Completion(
                f"/{command.name}",
                start_position=-len(typed),
                display_meta=command.summary,
            )


class Shell:
    def __init__(self, config, session, registry: Sequence[Command], *, input=None, output=None):
        self._state = State(config=config, session=session)
        self._registry = registry
        self._input, self._output = input, output
        self._width = 80
        self._status = ""
        try:
            history: Any = FileHistory(str(config.workspace / "input-history"))
        except OSError:
            history = InMemoryHistory()
        # Grows from one line to a cap, then scrolls with a scrollbar. Without
        # a cap, pasting a long Lean snippet pushes the transcript off screen.
        # The cap is derived from terminal height, so it is a callable and gets
        # recomputed on resize rather than captured once.
        self._box = TextArea(
            multiline=True,
            wrap_lines=True,
            scrollbar=True,
            # A callable, not a Dimension. Two reasons: `self._app` does not
            # exist yet at this point, and prompt_toolkit re-evaluates a callable
            # height every render, which is what makes the cap follow a resize.
            height=lambda: Dimension(min=1, max=self._max_box_height()),
            prompt="> ",
            history=history,
            auto_suggest=CommandSuggester(registry),
            completer=CommandCompleter(registry),
            complete_while_typing=False,
        )
        self._app = Application(
            layout=Layout(HSplit([Frame(self._box), Window(FormattedTextControl(self._hint), height=1)])),
            key_bindings=self._bindings(),
            style=STYLE,
            full_screen=False,
            input=input,
            output=output,
        )

    # -- rendering --------------------------------------------------------
    def _max_box_height(self) -> int:
        """About a third of the screen, capped at 12, floored at 3.

        A fraction rather than a constant because 80x24 and a tall terminal want
        different answers. The ceiling stops the box swallowing a large screen;
        the floor keeps it usable on a small one. Recomputed, never cached --
        it changes when the terminal is resized.
        """
        rows = self._output.get_size().rows if self._output is not None else 24
        return min(12, max(3, rows // 3))

    def _hint(self):
        left = self._status or "/ for commands · alt+enter for newline"
        return [("class:hint", f"  {left}"), ("class:hint", f"    {self._state.config.model}")]

    def write(self, text: str, *, style: str = "system") -> None:
        if style == "clear":
            print("\x1b[2J\x1b[H", end="")        # viewport only; scrollback untouched
            return
        lines = [text] if style in {"normal", "warning"} else transcript.notice_lines(text, self._width)
        for line in lines:
            print(line)

    def _echo(self, lines: list[str]) -> None:
        for line in lines:
            print(line)

    # -- Ui ---------------------------------------------------------------
    async def choose(self, title, rows: Sequence[Choice], *, current: int = 0, subtitle: str = "") -> Choice | None:
        # select.choose wraps itself in in_terminal(); do not add a second one.
        return await select.choose(title, rows, current=current, subtitle=subtitle)

    async def ask_line(self, prompt: str) -> str | None:
        from prompt_toolkit.application.run_in_terminal import in_terminal
        from prompt_toolkit.shortcuts import PromptSession

        # in_terminal() is mandatory: without it this application's own redraw
        # can land underneath the prompt and displace it by a row. See the
        # Global Constraints -- this is the bug that took five spike rounds.
        try:
            async with in_terminal():
                return await PromptSession(message=prompt).prompt_async()
        except (EOFError, KeyboardInterrupt):
            return None

    async def confirm(self, question: str) -> bool:
        picked = await self.choose(question, [Choice("no", "No"), Choice("yes", "Yes")], current=0)
        return picked is not None and picked.value == "yes"

    @property
    def from_thread(self):
        return _FromThread(self, self._app)

    # -- keys -------------------------------------------------------------
    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("escape", "enter")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("enter")
        async def _submit(event) -> None:
            text = self._box.text
            if text.endswith("\\"):
                event.current_buffer.delete_before_cursor()
                event.current_buffer.insert_text("\n")
                return
            self._box.text = ""
            await self._submit(text)

        @keys.add("c-c")
        @keys.add("c-d")
        def _leave(event) -> None:
            event.app.exit(result=0)

        return keys

    async def _submit(self, text: str) -> None:
        outcome = dispatch.classify(text, self._registry, turn_running=self._state.turn_running)
        if outcome.kind == "empty":
            return
        if outcome.kind in {"unknown", "refused"}:
            self.write(outcome.message, style="error")
            return
        if outcome.kind == "command":
            try:
                self._state = await outcome.command.handler(self, outcome.argument, self._state)
            except Exception as error:                  # noqa: BLE001 - a bad command must not end the session
                self.write(f"{type(error).__name__}: {error}", style="error")
            if self._state.done:
                self._app.exit(result=0)
            return
        self._echo(transcript.user_lines(outcome.argument, self._width))
        # Task 10 moves this onto a worker thread with a spinner.
        try:
            reply = self._state.session.send(outcome.argument)
        except Exception as error:                      # noqa: BLE001
            self.write(f"{type(error).__name__}: {error}", style="error")
            return
        self._echo(transcript.hardy_lines(reply, self._width))
        print()

    # -- entry ------------------------------------------------------------
    def run(self) -> int:
        with patch_stdout(raw=True):
            for style, text in banner.lines(self._state.config):
                self.write(text, style=style)
            print()
            return self._app.run() or 0


class _FromThread:
    """Task 11 fills this in; it exists here so `Shell` satisfies `Ui`."""

    def __init__(self, shell: Shell, app: Application):
        self._shell, self._app = shell, app

    def write(self, text: str, *, style: str = "system") -> None:
        self._shell.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        raise NotImplementedError("Task 11")

    def ask_line(self, prompt: str):
        raise NotImplementedError("Task 11")

    def confirm(self, question: str) -> bool:
        raise NotImplementedError("Task 11")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui/test_shell.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui/shell.py tests/tui/test_shell.py
git commit -m "feat: draw a bordered input box that ghosts slash commands"
```

---

### Task 10: The turn worker, Esc, and the durable abandonment record

Read spec sections: **Turn lifecycle**, **What is permitted while a turn is in flight**.

**Files:**
- Modify: `src/hardy/chat.py` — add `record_abandonment`
- Modify: `src/hardy/tui/shell.py` — worker thread, spinner, Esc
- Test: `tests/tui/test_turns.py`, `tests/test_chat.py` (append)

**Interfaces:**
- Produces:
  - `MathematicsSession.record_abandonment(reason: str) -> None` — appends `{"type": "turn", "status": "abandoned", "reason": reason}` through `_record`.
  - `Shell._start_turn(text)` / `Shell._finish_turn(reply)` — internal, but `State.turn_running` becomes observable behaviour.

- [ ] **Step 1: Write the failing test for the record**

```python
# append to tests/test_chat.py
def test_an_abandoned_turn_is_written_to_the_transcript(tmp_path: Path):
    """A dim notice dies with the session; replay reads transcript.jsonl."""
    import json

    # `session` is this module's existing helper (tests/test_chat.py:60) and
    # takes a FakeChatRuntime. Match how the tests around it build one.
    conversation = session(tmp_path, FakeChatRuntime([]))
    conversation.record_abandonment("user_pressed_escape")
    events = [
        json.loads(line)
        for line in conversation.transcript_path.read_text(encoding="utf-8").splitlines()
    ]
    abandoned = [event for event in events if event.get("status") == "abandoned"]
    assert abandoned and abandoned[-1]["reason"] == "user_pressed_escape"
    assert abandoned[-1]["type"] == "turn"
```

The helper is `session(tmp_path, runtime, approvals=())` at `tests/test_chat.py:60`, not a `build_session`. Read how the neighbouring tests construct `FakeChatRuntime` (`tests/test_chat.py:12`) and mirror it — its constructor argument is a script of canned responses, and an empty script is fine here because this test never sends a turn.

- [ ] **Step 2: Write the failing test for the shell behaviour**

```python
# tests/tui/test_turns.py
from __future__ import annotations

import threading
from io import StringIO

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.tui import handlers, shell


class SlowSession:
    """Blocks until released, so a turn is genuinely in flight."""

    def __init__(self):
        self.release = threading.Event()
        self.abandoned: list[str] = []
        self.switched: list[str] = []

    def send(self, text: str) -> str:
        self.release.wait(timeout=5)
        return "late reply"

    def switch_model(self, model: str) -> None:
        self.switched.append(model)

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


def drive(settings, session, keys: str):
    buffer = StringIO()
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        code = shell.Shell(
            settings, session, handlers.build_registry(),
            input=pipe,
            output=Vt100_Output(buffer, lambda: Size(rows=24, columns=80)),
        ).run()
    return code, buffer.getvalue()


async def test_escape_records_the_abandonment(settings):
    session = SlowSession()
    _, _ = drive(settings, session, "prove something\r\x1b\x03")
    session.release.set()
    assert session.abandoned == ["user_pressed_escape"]


async def test_escape_does_not_claim_to_have_cancelled(settings):
    session = SlowSession()
    _, written = drive(settings, session, "prove something\r\x1b\x03")
    session.release.set()
    assert "still running" in written
    assert "cancel" not in written.lower()


async def test_model_is_refused_while_a_turn_is_in_flight(settings):
    """Switching would stamp the new model's provider session on the old turn."""
    session = SlowSession()
    _, written = drive(settings, session, "prove something\r\x1b/model\r\x03")
    session.release.set()
    assert session.switched == []
    assert "cannot run while a turn is still running" in written


async def test_status_is_allowed_while_a_turn_is_in_flight(settings):
    session = SlowSession()
    _, written = drive(settings, session, "prove something\r\x1b/status\r\x03")
    session.release.set()
    assert str(settings.workspace) in written
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run --extra test pytest tests/tui/test_turns.py tests/test_chat.py -v`
Expected: FAIL — `record_abandonment` missing, Esc unhandled

- [ ] **Step 4: Add the record to `chat.py`**

```python
    def record_abandonment(self, reason: str) -> None:
        """Write down that a turn was walked away from.

        The terminal shows a notice, but a notice dies with the session and
        `transcript.jsonl` is what replay and evaluation read. Without this, a
        turn the user abandoned is indistinguishable from one they waited for.
        """
        self._record({"type": "turn", "status": "abandoned", "reason": reason})
```

- [ ] **Step 5: Rework the turn path in `shell.py`**

Replace the inline `session.send` in `Shell._submit` with a worker, and add Esc:

```python
    async def _run_turn(self, text: str) -> None:
        import dataclasses

        self._echo(transcript.user_lines(text, self._width))
        self._state = dataclasses.replace(self._state, turn_running=True)
        self._abandoned = False
        started = asyncio.get_running_loop().time()

        async def spinner() -> None:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            tick = 0
            while self._state.turn_running:
                elapsed = int(asyncio.get_running_loop().time() - started)
                self._status = f"{frames[tick % len(frames)]} working · {elapsed}s · esc to stop waiting"
                tick += 1
                self._app.invalidate()
                await asyncio.sleep(0.1)

        watch = asyncio.create_task(spinner())
        try:
            reply = await asyncio.to_thread(self._state.session.send, text)
        except Exception as error:                      # noqa: BLE001
            reply = None
            self.write(f"{type(error).__name__}: {error}", style="error")
        finally:
            self._state = dataclasses.replace(self._state, turn_running=False)
            self._status = ""
            watch.cancel()
            self._app.invalidate()

        if reply is None:
            return
        if self._abandoned:
            self.write("the abandoned turn has replied:")
        self._echo(transcript.hardy_lines(reply, self._width))
        print()
```

In `_submit`, replace the inline send with `asyncio.create_task(self._run_turn(outcome.argument))` so the box returns immediately. Add the Esc binding:

```python
        # No `eager=True`. Task 1 proved it unconditionally shadows the
        # ("escape", "enter") chord bound in Task 9 -- which is how terminals
        # encode Alt+Enter, the documented way to insert a newline. The cost is
        # that a lone Escape waits out the ambiguous-key timeout before firing.
        # That latency is acceptable here: Esc only stops *waiting* on a turn,
        # so a few tens of milliseconds change nothing, whereas losing Alt+Enter
        # would break multi-line input outright.
        @keys.add("escape")
        def _abandon(event) -> None:
            if not self._state.turn_running:
                return
            self._abandoned = True
            # Not a cancellation: the call cannot be stopped, and its tool
            # calls may already have written Lean or LaTeX. Say only what is
            # true -- we stopped waiting.
            session = self._state.session
            if session is not None and hasattr(session, "record_abandonment"):
                session.record_abandonment("user_pressed_escape")
            self.write("stopped waiting; the call is still running and its reply will appear when it lands")
```

Initialise `self._abandoned = False` in `__init__`.

**Task 1 settled the `escape` prefix conflict, and not the way this plan first guessed.** `eager=True` on plain `escape` shadows `("escape", "enter")` unconditionally — every time, not as a timing race — so the abandon binding must have **no** `eager` flag. Add a test in this task proving both survive together:

```python
async def test_alt_enter_inserts_a_newline_and_lone_escape_still_abandons(settings):
    """eager=True here would silently kill multi-line input. Pin both."""
    session = SlowSession()
    _, written = drive(settings, session, "one\x1b\rtwo\r\x1b\x03")
    session.release.set()
    assert session.sent_text == ["one\ntwo"] if hasattr(session, "sent_text") else True
    assert session.abandoned == ["user_pressed_escape"]
```

Give `SlowSession` a `sent_text` list recording what `send` received, so the newline is actually asserted rather than assumed.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui tests/test_chat.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
uvx ruff check src tests
git add src/hardy/chat.py src/hardy/tui/shell.py tests/tui/test_turns.py tests/test_chat.py
git commit -m "feat: run a turn on a worker and record it when abandoned"
```

---

### Task 11: Approval from a tool thread, and the shutdown policy

Read spec sections: **Axiom approval arrives from a foreign thread**, **Shutdown with a turn in flight**.

**Files:**
- Modify: `src/hardy/tui/shell.py` — finish `_FromThread`, add the double-tap Ctrl+C
- Modify: `src/hardy/cli.py:25-37` — rebuild `_confirm_assumption` as a `BlockingUi` consumer
- Test: `tests/tui/test_marshalling.py`, `tests/tui/test_shutdown.py`

**Interfaces:**
- Produces:
  - `_FromThread.choose/ask_line/confirm` — schedule the coroutine with `asyncio.run_coroutine_threadsafe(..., app.loop)` and block on `future.result()`.
  - `cli.confirm_assumption(ui) -> Callable[[dict[str, str]], bool]` — a factory returning the callback `MathematicsSession` expects.

- [ ] **Step 1: Write the failing test for marshalling**

```python
# tests/tui/test_marshalling.py
from __future__ import annotations

import threading
from io import StringIO

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.tui import handlers, shell

PROPOSAL = {
    "formal_name": "riemann",
    "lean_statement": "True",
    "latex_name": "RH",
    "informal_statement": "the hypothesis",
    "source": "paper",
    "reason": "needed",
}


def test_a_tool_thread_can_ask_and_get_an_answer(settings):
    """The axiom gate is called from an SDK tool thread. It must not deadlock."""
    from hardy import cli

    answers: list[bool] = []
    buffer = StringIO()

    class Session:
        def send(self, text: str) -> str:
            confirm = cli.confirm_assumption(the_shell)
            answers.append(confirm(PROPOSAL))
            return "done"

        def switch_model(self, model): ...
        def record_abandonment(self, reason): ...

    with create_pipe_input() as pipe:
        pipe.send_text("prove it\r")
        pipe.send_text("2\r")          # answer the approval selector: Yes
        pipe.send_text("\x03")
        the_shell = shell.Shell(
            settings, Session(), handlers.build_registry(),
            input=pipe,
            output=Vt100_Output(buffer, lambda: Size(rows=24, columns=80)),
        )
        the_shell.run()

    assert answers == [True]
    assert "riemann" in buffer.getvalue()


def test_from_thread_refuses_to_be_used_on_the_ui_thread(settings):
    """Using it there would deadlock, so it must fail loudly instead."""
    import pytest

    with create_pipe_input() as pipe:
        the_shell = shell.Shell(settings, None, handlers.build_registry(), input=pipe)
        with pytest.raises(RuntimeError, match="UI thread"):
            the_shell.from_thread.confirm("really?")


def test_the_approval_declines_by_default(settings):
    from hardy import cli

    class Ui:
        def __init__(self):
            self.written: list[str] = []

        def write(self, text, *, style="system"):
            self.written.append(text)

        def choose(self, title, rows, *, current=0, subtitle=""):
            return None                     # Esc

        def ask_line(self, prompt): return None
        def confirm(self, question): return False

    class Holder:
        from_thread = Ui()

    assert cli.confirm_assumption(Holder())(PROPOSAL) is False
```

- [ ] **Step 2: Write the failing test for shutdown**

```python
# tests/tui/test_shutdown.py
from __future__ import annotations

import threading

from prompt_toolkit.input import create_pipe_input

from hardy.tui import handlers, shell


class Stalled:
    def __init__(self):
        self.abandoned: list[str] = []

    def send(self, text: str) -> str:
        threading.Event().wait(timeout=5)
        return "never"

    def switch_model(self, model): ...

    def record_abandonment(self, reason: str) -> None:
        self.abandoned.append(reason)


def test_the_first_ctrl_c_refuses_and_names_the_cost(settings, monkeypatch):
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    session = Stalled()
    written: list[str] = []
    with create_pipe_input() as pipe:
        pipe.send_text("prove it\r\x03")
        the_shell = shell.Shell(settings, session, handlers.build_registry(), input=pipe)
        monkeypatch.setattr(the_shell, "write", lambda text, style="system": written.append(text))
        the_shell.run()
    assert exits == []
    assert any("orphaned" in text for text in written)


def test_the_second_ctrl_c_records_then_hard_exits(settings, monkeypatch):
    """A non-daemon worker is joined at shutdown, so only os._exit is immediate."""
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    session = Stalled()
    with create_pipe_input() as pipe:
        pipe.send_text("prove it\r\x03\x03")
        shell.Shell(settings, session, handlers.build_registry(), input=pipe).run()
    assert session.abandoned == ["forced_exit"]
    assert exits == [130]


def test_ctrl_c_with_no_turn_leaves_at_once(settings, monkeypatch):
    exits: list[int] = []
    monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))
    with create_pipe_input() as pipe:
        pipe.send_text("\x03")
        code = shell.Shell(settings, None, handlers.build_registry(), input=pipe).run()
    assert code == 0 and exits == []
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run --extra test pytest tests/tui/test_marshalling.py tests/tui/test_shutdown.py -v`
Expected: FAIL — `NotImplementedError("Task 11")`, no `confirm_assumption`

- [ ] **Step 4: Finish `_FromThread` — NOTE: the design below is superseded**

> **Read this before writing Step 4's code.** The `run_coroutine_threadsafe(coroutine, loop)` shape shown below does not work on a real terminal. A worker thread cannot construct an `Application` at all: `contextvars` do not cross a thread, so it has no correct ambient session to inherit, and passing the outer app's `input` explicitly makes two applications share one attached `Input` and the nested one reads EOF. Neither failure reproduces under a pipe input, so the test suite cannot catch either.
>
> The corrected design, per the Global Constraints:
> 1. `Shell` owns an `asyncio.Queue` of prompt requests and a loop-owned task draining it. Each request carries the prompt arguments and a `concurrent.futures.Future`.
> 2. The loop-owned task builds and runs the nested selector **in the loop's own context** — inheriting the ambient session, passing no `input`/`output` — and sets the future's result.
> 3. `_FromThread`'s methods post a request with `loop.call_soon_threadsafe(queue.put_nowait, request)` and block on `future.result()`. They construct nothing from prompt_toolkit.
>
> The `from_thread`-raises-on-the-UI-thread rule still stands: posting a request and blocking the UI thread on it would deadlock, since the loop-owned task can never run.

The code below shows the superseded shape for reference only. Implement the queue design instead.

```python
class _FromThread:
    """The sync facade over the async Ui, for callers that cannot await.

    The SDK calls its tool functions on their own threads, and the axiom gate
    is reached from there. Scheduling onto the loop and blocking *this* thread
    is correct: `self._gate` already serialises tool calls, and a pending
    axiom question should stop the turn.
    """

    def __init__(self, shell: Shell, app: Application):
        self._shell, self._app = shell, app

    def _run(self, coroutine):
        loop = self._app.loop
        if loop is None:
            raise RuntimeError("the application is not running")
        if threading.current_thread() is threading.main_thread() and get_app_or_none() is not None:
            coroutine.close()
            raise RuntimeError("from_thread must not be used on the UI thread; await the Ui directly")
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    def write(self, text: str, *, style: str = "system") -> None:
        self._shell.write(text, style=style)

    def choose(self, title, rows, *, current=0, subtitle=""):
        return self._run(self._shell.choose(title, rows, current=current, subtitle=subtitle))

    def ask_line(self, prompt: str):
        return self._run(self._shell.ask_line(prompt))

    def confirm(self, question: str) -> bool:
        return self._run(self._shell.confirm(question))
```

Add `import os` and `import threading` at the top of `shell.py`.

- [ ] **Step 5: Add the shutdown policy**

Replace the `c-c` binding in `Shell._bindings`:

```python
        @keys.add("c-c")
        def _interrupt(event) -> None:
            if not self._state.turn_running:
                event.app.exit(result=0)
                return
            if not self._forcing:
                self._forcing = True
                self.write(
                    "a turn is still running — Ctrl+C again to leave anyway; Lean or "
                    "LaTeX processes it started may be left orphaned and its artifacts incomplete",
                    style="warning",
                )
                return
            session = self._state.session
            if session is not None and hasattr(session, "record_abandonment"):
                session.record_abandonment("forced_exit")
            # The worker is non-daemon so an ordinary exit waits for a safe
            # boundary. That is exactly why a *forced* exit cannot go through
            # interpreter shutdown: it would be joined. os._exit skips it, at
            # the stated cost of orphaned children and no atexit or flush.
            os._exit(130)
```

Initialise `self._forcing = False` in `__init__`.

- [ ] **Step 6: Rebuild the approval callback in `cli.py`**

Replace `_confirm_assumption` (lines 25-37):

```python
def confirm_assumption(ui: Any) -> Callable[[dict[str, str]], bool]:
    """The axiom gate, reached from an SDK tool thread.

    `MathematicsSession` calls this synchronously from inside a tool call, so
    it must not touch the application directly -- it goes through
    `ui.from_thread`, which marshals onto the event loop and blocks here for
    the answer. A decline still hard-gates the assumption.
    """

    def confirm(proposal: dict[str, str]) -> bool:
        blocking = ui.from_thread
        blocking.write("Hardy wants to introduce an assumption:", style="warning")
        blocking.write(f"  Informal: {proposal['informal_statement']}")
        blocking.write(f"  Lean: axiom {proposal['formal_name']} : {proposal['lean_statement']}")
        blocking.write(f"  Source: {proposal['source']}")
        blocking.write(f"  Reason: {proposal['reason']}")
        picked = blocking.choose(
            f"Approve the assumption {proposal['formal_name']}?",
            [Choice("no", "No, decline it"), Choice("yes", "Yes, approve it")],
            current=0,
        )
        return picked is not None and picked.value == "yes"

    return confirm
```

Import `Choice` from `.tui.ports`. `MathematicsSession` is constructed before the shell exists, so pass a one-element holder the shell fills in, or construct the shell first and the session second — Task 12 settles the order; for now export the factory and keep the old `_confirm_assumption` for the still-live `_chat` path.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/tui -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui/shell.py src/hardy/cli.py tests/tui
git commit -m "feat: ask for axiom approval from a tool thread without deadlocking"
```

---

### Task 12: Retire the old loop

Read spec sections: **Startup**, **Degradation and error handling**, **Companion changes**.

**Files:**
- Modify: `src/hardy/tui/__init__.py` — route the interactive path to `Shell`
- Modify: `src/hardy/cli.py` — delete `_chat`'s loop and the old `_confirm_assumption`
- Test: `tests/tui/test_run_session.py`

**Interfaces:**
- Produces: `run_session(config, session_factory, *, plain=False) -> int`. Note the change: it takes a **factory** `Callable[[Callable], MathematicsSession]` so the shell can exist before the session and supply the approval callback.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_run_session.py
from __future__ import annotations

import io

import pytest

from hardy import runner
from hardy.tui import run_session


class FakeSession:
    def send(self, text: str) -> str:
        return "answered"

    def switch_model(self, model): ...
    def record_abandonment(self, reason): ...


def test_a_non_tty_falls_back_to_plain_mode(settings, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    code = run_session(settings, lambda confirm: FakeSession(), plain=False)
    assert code == 0
    assert runner.WARNING in capsys.readouterr().out


def test_hardy_plain_forces_plain_mode(settings, monkeypatch, capsys):
    monkeypatch.setenv("HARDY_PLAIN", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    assert run_session(settings, lambda confirm: FakeSession()) == 0
    assert runner.WARNING in capsys.readouterr().out


def test_a_dumb_terminal_falls_back(settings, monkeypatch, capsys):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    assert run_session(settings, lambda confirm: FakeSession()) == 0


def test_a_shell_that_will_not_start_falls_back_rather_than_failing(settings, monkeypatch, capsys):
    """Never end a session over rendering."""
    monkeypatch.setattr("hardy.tui._is_interactive", lambda: True)

    def explode(*args, **kwargs):
        raise RuntimeError("no console")

    monkeypatch.setattr("hardy.tui.shell.Shell", explode)
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    assert run_session(settings, lambda confirm: FakeSession()) == 0
    captured = capsys.readouterr()
    assert runner.WARNING in captured.out
    assert "no console" in captured.err
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --extra test pytest tests/tui/test_run_session.py -v`
Expected: FAIL — `run_session` takes a session, not a factory

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tui/__init__.py
"""Hardy's interactive session: a real terminal when there is one, lines when not."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any


def _is_interactive() -> bool:
    if os.environ.get("HARDY_PLAIN"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def run_session(config, session_factory: Callable[[Any], Any], *, plain: bool = False) -> int:
    """Run the session. `session_factory` receives the approval callback.

    The shell has to exist before the session does, because the session needs
    a way to ask for axiom approval and that way runs through the shell.
    """
    from . import plain as plain_mode

    if plain or not _is_interactive():
        return _run_plain(config, session_factory, plain_mode)

    from .. import cli
    from .handlers import build_registry
    from .shell import Shell

    try:
        held: dict[str, Any] = {}
        shell = Shell(config, None, build_registry())
        held["shell"] = shell
        session = session_factory(cli.confirm_assumption(shell))
        shell.attach(session)
        return shell.run()
    except Exception as error:                          # noqa: BLE001
        print(f"Falling back to the plain session: {error}", file=sys.stderr)
        return _run_plain(config, session_factory, plain_mode)


def _run_plain(config, session_factory: Callable[[Any], Any], plain_mode) -> int:
    ui_holder: dict[str, Any] = {}

    def confirm(proposal: dict[str, str]) -> bool:
        from .. import cli

        return cli.confirm_assumption(ui_holder["ui"])(proposal)

    session = session_factory(confirm)
    return plain_mode.run(config, session, ui_holder=ui_holder)
```

Add `Shell.attach(session)` setting `self._state = dataclasses.replace(self._state, session=session)`, and give `plain.run` an optional `ui_holder` parameter it populates with its `PlainUi` before the loop starts.

- [ ] **Step 4: Replace `_chat` in `cli.py`**

```python
def _chat(config: configuration.Config, parser: argparse.ArgumentParser, *, plain: bool = False) -> int:
    from .tui import run_session

    def build(confirm: Any) -> MathematicsSession:
        return MathematicsSession(
            config.workspace,
            runtime_factory(str(config.model)),
            config.lean_command,
            config.latex_command,
            confirm,
            lean_project=config.lean_project,
            lean_timeout=config.lean_timeout,
        )

    return run_session(config, build, plain=plain)
```

Delete the old `while True` loop, the five `print` calls, and the old `_confirm_assumption`. Confirm nothing else uses it:

```bash
grep -rn "_confirm_assumption" src tests
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS, no regressions. Then run it for real in a terminal:

```bash
uv run hardy
```

Check: the banner including the warning; `/` opens the menu; `/mo` shows a dim `del`; Tab completes it; Enter opens the selector; arrows and numbers work; Esc cancels; `/status` prints; `/exit` leaves. Repeat in `conhost.exe`.

- [ ] **Step 6: Commit**

```bash
uvx ruff check src tests
git add src/hardy/tui src/hardy/cli.py tests/tui
git commit -m "feat: make the new terminal the interactive session"
```

---

### Task 13: Documentation

Read the repository rule in `AGENTS.md`: `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` must stay consistent.

**Files:**
- Modify: `README.md`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html`, `docs/INSTALL.md`

**Interfaces:**
- Consumes: the shipped behaviour. Produces: no code.

- [ ] **Step 1: Find every claim the rework invalidates**

```bash
grep -rn "you>\|hardy>\|/model to change\|Type /model" README.md DESIGN.md FEATURES.md ARCHITECTURE.html docs/
```

- [ ] **Step 2: Update the prose**

- `README.md` — the session's key bindings and the command list. State that `--plain` and `HARDY_PLAIN` exist.
- `FEATURES.md` — under *Interactive exploration*, add the delivered items: ghost-text command completion, the `/model` selector, the bordered input box. Note that streaming is issue #32 and that Esc stops waiting rather than cancelling.
- `DESIGN.md` — record the one new runtime dependency and why: a real terminal input layer, with only two modules importing it.
- `ARCHITECTURE.html` — add the `tui` package to the overview.
- `docs/INSTALL.md` — mention nothing new unless `uv sync` behaviour changed.

- [ ] **Step 3: Verify the claims are true**

For each sentence added, run the thing it describes. Do not describe the spike's fallback if the spike succeeded, and do not describe Esc as cancelling.

- [ ] **Step 4: Commit**

```bash
git add README.md DESIGN.md FEATURES.md ARCHITECTURE.html docs/
git commit -m "docs: describe the interactive session as it now behaves"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: *Ui port* → 2; *commands.py* → 3; *command set* → 5, 6; *Transcript rendering* + *Startup* → 4; *Ghost text* + *input box* → 9; */model selector* → 6 (logic) and 8 (widget); *Dispatch on submit* → 7; *Turn lifecycle* → 10; *in-flight gating* → 7 (rules) and 10 (behaviour); *Concurrency* → 11; *Shutdown* → 11; *Degradation* → 7, 12; *Testing* → distributed, with the `DummyOutput`/`Vt100_Output` split honoured in 9; *Risks* → 1; *Companion changes* → 2 (dependency), 12, 13.

**Verified against the code before publishing.** Two assumptions in the first draft were wrong and are corrected in place: `doctor` has no pure formatter (`run_checks` returns `list[Check]` and `report` prints), so Task 5 Step 4 now adds `describe`; and `tests/test_chat.py`'s helper is `session(tmp_path, runtime, approvals=())` at line 60, not a `build_session`. The `Check` field names in Task 5's `describe` snippet are still unconfirmed — Step 4 says to read `doctor.py:19` and correct them.

**One gap deliberately left open:** Task 11 Step 6 leaves the shell/session construction order unsettled and Task 12 Step 3 settles it with `Shell.attach` plus a `ui_holder` in `plain.run`. If that indirection reads badly once written, invert it — build the `Ui` first and pass it to the factory. Either is fine; `test_run_session.py` covers both.

**Where this plan is most likely to be wrong.** Tasks 8-11 quote `prompt_toolkit` APIs from memory: `create_pipe_input()` as a context manager, `Vt100_Output(stream, get_size)`, `Application.loop`, `erase_when_done`, async key-binding handlers, and the `escape` / `escape enter` binding conflict. Task 1 exists to confirm these, and its Step 5 records the idioms that actually worked. **Where Task 1's findings differ from the code in later tasks, the findings win** — adjust the test and implementation rather than forcing the quoted form.

**Type consistency.** `Choice`, `State`, `Ui`, `BlockingUi`, `Command`, `Outcome` are defined once and used with the same field names throughout. `suggest`/`complete`/`resolve`/`canonical` keep the same signatures from Task 3 onwards. `record_abandonment(reason)` is called with `"user_pressed_escape"` (Task 10) and `"forced_exit"` (Task 11), matching the test assertions.

**Ordering risk worth naming.** Task 1 is a gate, and Tasks 2-7 do not depend on its outcome — they are headless. Tasks 8-12 do. If the spike fails, Tasks 2-7 remain valid work and only the shell tasks need respecifying.
