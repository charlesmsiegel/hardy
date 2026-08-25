"""The terminal Hardy actually draws.

Not full screen. The transcript is printed into the terminal's own scrollback
through `patch_stdout`, so it stays selectable and survives the session; only
the prompt area below it is redrawn. Every prompting method is a coroutine
because a selector reads keys the event loop delivers, and blocking that loop
to wait for them would deadlock.

There is no bordered box, at any width. The spec's fallback is taken
deliberately: resizing a non-full-screen prompt_toolkit application corrupts
the screen whenever previously drawn rows are wider than the new terminal,
because the terminal rewraps those rows while the renderer's cursor
bookkeeping (`renderer.py` `Renderer.erase`, `_output_screen_diff`) still
speaks the old geometry -- and a `Frame` guarantees every row spans the full
width. No application-level code can fix that; the terminal does not report
how it rewrapped. What an application *can* do is never draw a row the
terminal would need to rewrap: all chrome here (rule, prompt, hint) stays
narrower than the narrow-terminal threshold, so any resize at or above it
leaves the chrome untouched and reflow stays clean. The full diagnosis, with
citations, is in the task 9 report.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import os
import threading
from collections.abc import Sequence
from typing import Any

from prompt_toolkit.application import Application, in_terminal
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.input.vt100_parser import _IS_PREFIX_OF_LONGER_MATCH_CACHE
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ConditionalMargin, ScrollbarMargin
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from . import banner, dispatch, select, stream, transcript
from .commands import Command, canonical, complete, resolve, suggest
from .ports import Choice, State

# Posted to a turn's queue when nothing further is coming. An object of its
# own rather than None, so it can never be confused with an event.
_TURN_OVER = object()

# -- Shift+Enter --------------------------------------------------------------
#
# prompt_toolkit maps every vt100/xterm "modifyOtherKeys" encoding of
# Shift+Enter, Ctrl+Enter and Ctrl+Shift+Enter onto `Keys.ControlM` -- the same
# key as plain Enter (`input/ansi_escape_sequences.py:129-131`) -- so the
# distinction is discarded before any `KeyBindings` ever sees it, and no
# public API restores it. `ANSI_SEQUENCES` is a plain module-level dict, so
# this is the *one place* Hardy mutates prompt_toolkit's own process-global
# state to make Shift+Enter distinguishable at all. It is idempotent
# (assigning the same value to the same key twice is a no-op) and applies
# once per process regardless of how many `Shell`s get built or how many
# times this module is imported (Python only executes a module body once),
# so it cannot compound and there is nothing to leak between tests.
#
# `_SHIFT_ENTER` is a Private Use Area code point (U+E000): guaranteed never
# to be produced by a keyboard, so it can safely stand in as a synthetic
# single-character "key" for that. It is *not* guaranteed absent from pasted
# or otherwise injected text -- PUA codepoints do turn up in ligature- and
# font-extracted content -- so a paste containing this exact byte would also
# trigger the newline binding; low-likelihood and accepted, not something to
# design around. `KeyPress` only accepts a `Keys` member or exactly one
# character (`key_processor.py:42-43`); a synthetic multi-character name like
# "shift-enter" is rejected unless `Keys` itself gains a new member, and
# `Keys` is a real `enum.Enum` -- extending it at runtime needs a third-party
# dependency (`aenum`) this project does not otherwise carry.
_SHIFT_ENTER = ""
ANSI_SEQUENCES["\x1b[27;2;13~"] = _SHIFT_ENTER  # vt100/xterm modifyOtherKeys
ANSI_SEQUENCES["\x1b[13;2u"] = _SHIFT_ENTER  # kitty keyboard protocol
# The parser's own prefix-ambiguity cache is a lazily-filled, process-global
# dict keyed by partial byte sequences. Clearing it forces every prefix to be
# recomputed against the table above, rather than risking a stale answer some
# earlier, unrelated parsing (in this process, or an earlier test) cached
# before this module -- and the patch above -- had run.
_IS_PREFIX_OF_LONGER_MATCH_CACHE.clear()

STYLE = Style.from_dict(
    {
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
    }
)

#: Below this many columns the layout is "degraded": chrome rows may be
#: rewrapped by further narrowing. At or above it, resize never touches them.
NARROW = 40

#: The widest any chrome row may render. Two columns under NARROW so that a
#: resize down to exactly NARROW still cannot rewrap a chrome row.
CHROME = NARROW - 2

#: Rule left over after the spend meter, below which the meter is dropped. A
#: rule that ends flush with a number reads as a truncation whether or not it
#: is one, which is the doubt the meter exists to remove.
_RULE_TAIL = 2


class CommandSuggester(AutoSuggest):
    """Dim inline completion. Only ever appends, never rewrites what was typed."""

    def __init__(self, registry: Sequence[Command]):
        self._registry = registry

    def get_suggestion(self, buffer: Any, document: Any) -> Suggestion | None:
        if "\n" in document.text or document.cursor_position != len(document.text):
            return None
        tail = suggest(document.text, self._registry)
        return Suggestion(tail) if tail else None


class CommandCompleter(Completer):
    """Tab completion over the registry.

    `/` alone lists canonical entries only -- alias entries would double the
    menu while teaching nothing. A longer prefix completes over everything it
    matches, aliases included, because an alias is what the user is typing.
    """

    def __init__(self, registry: Sequence[Command]):
        self._registry = registry

    def get_completions(self, document: Any, complete_event: Any):
        typed = document.text_before_cursor
        matches = complete(typed, self._registry)
        if typed.strip() == "/":
            matches = [c for c in matches if c in canonical(self._registry)]
        for command in matches:
            yield Completion(
                f"/{command.name}",
                start_position=-len(typed),
                display_meta=command.summary,
            )


class Shell:
    """The interactive session: prompt area at the bottom, scrollback above."""

    def __init__(
        self, config: Any, session: Any, registry: Sequence[Command], *, input=None, output=None
    ):
        self._state = State(config=config, session=session)
        self._registry = registry
        self._input, self._output = input, output
        # Whether the turn currently running (if any) has been walked away
        # from with Esc. Read by `_run_turn` when its reply lands, so the
        # reply can be tagged as belonging to a turn nobody is waiting on
        # anymore, and reset at the start of every new turn.
        self._abandoned = False
        # The spinner's current frame, drawn in the hint line while a turn is
        # running. Chrome (see the module docstring), so `_hint` slices it to
        # `_chrome_limit()` like everything else it draws.
        self._status = ""
        # The executor future for whatever turn is currently in flight, or
        # None. `run_async` checks this after the app exits so a turn cut
        # short by /exit or Ctrl+C -- not just Esc -- still gets an honest
        # abandonment record even if `_run_turn`'s own task never gets a
        # single turn on the loop before being cancelled (see fix-round 1 of
        # the task 10 report: a task cancelled before its first scheduled
        # step never runs any of its own body, not even a `try/finally`
        # wrapping the whole thing, so that path cannot be the only guard).
        self._pending_future: asyncio.Future | None = None
        # Whether the first Ctrl+C during this turn has already warned. Reset
        # whenever a new turn starts (alongside `_abandoned`); read only while
        # `state.turn_running`, so a turn finishing naturally makes it moot
        # without needing its own reset.
        self._forcing = False
        # How many commands are in flight. A count rather than a flag, because
        # the safe-in-flight ones (`/status`, `/help`) are deliberately allowed
        # to run *alongside* a long `/cas` cell -- and with a flag, the short
        # one finishing cleared the state belonging to the cell, after which Esc
        # found nothing to stop and the runaway was unreachable.
        self._commands_running = 0
        # `_command_stopping` is a command's `_abandoned`: the first press
        # interrupts, the second gives up and kills. Reset when the first
        # command starts, not when any does, for the same reason.
        self._command_stopping = False
        # Requests from `_FromThread`, each an (already-created, not-yet-
        # awaited) coroutine paired with the `concurrent.futures.Future` a
        # tool thread is blocked on. Never touched from that thread beyond
        # `put_nowait` via `call_soon_threadsafe`; only `_drain_prompt_requests`
        # (running on the loop) ever awaits the coroutine itself. See
        # `_FromThread` for why nothing prompt_toolkit-shaped may be built on
        # the posting thread.
        self._prompt_queue: asyncio.Queue = asyncio.Queue()
        # The running loop, captured once `run_async` starts. `_FromThread`
        # needs it to call `call_soon_threadsafe` from a tool thread, which
        # has no running loop of its own to look one up from.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set once, only from the loop, inside `_drain_prompt_requests`'s
        # `finally` -- true for the rest of this `Shell`'s life once set.
        # `_post_or_decline` checks it (also only ever on the loop) before
        # ever touching the queue, so a tool thread that posts after the
        # drainer is gone declines immediately instead of waiting on a queue
        # nobody will ever drain again.
        self._closed = False

        history: Any
        try:
            config.layout.problem.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(config.layout.problem / "input-history"))
        except OSError:
            history = InMemoryHistory()

        # Grows from one line to a cap, then scrolls internally. Without a
        # cap, pasting a long Lean snippet pushes the transcript off screen.
        # The height is a callable, not a Dimension: the box is built before
        # the output size is known, and prompt_toolkit re-evaluates a callable
        # every render, which is what makes the cap follow a resize.
        self._box = TextArea(
            multiline=True,
            wrap_lines=True,
            height=lambda: Dimension(min=1, max=self._max_box_height()),
            prompt="> ",
            history=history,
            auto_suggest=CommandSuggester(registry),
            completer=CommandCompleter(registry),
            complete_while_typing=False,
        )
        # The scrollbar only when the buffer actually overflows the cap. An
        # always-on scrollbar paints the window's last column on every row,
        # which is exactly the full-width chrome the reflow fallback exists to
        # avoid (see the module docstring).
        self._box.window.right_margins = [
            ConditionalMargin(
                ScrollbarMargin(display_arrows=False), filter=Condition(self._box_overflows)
            )
        ]

        self._app: Application = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(FormattedTextControl(self._rule), height=1),
                        self._box,
                        Window(FormattedTextControl(self._hint), height=1),
                    ]
                )
            ),
            key_bindings=self._bindings(),
            style=STYLE,
            full_screen=False,
            input=input,
            output=output,
        )
        self._guard_resize()

    def attach(self, session: Any) -> None:
        """Give the shell its session, once the session exists.

        Construction order runs the other way from the old REPL: the shell
        has to exist first so `cli.confirm_assumption(shell)` can be handed
        to the session's own constructor, so the session cannot be passed to
        `__init__` here -- it is built with `session=None` and wired in once
        the caller (`tui.run_session`) has one.
        """
        self._state = dataclasses.replace(self._state, session=session)

    # -- rendering --------------------------------------------------------

    def _size(self) -> Size:
        try:
            return (self._output or self._app.output).get_size()
        except (NotImplementedError, OSError):
            return Size(rows=24, columns=80)

    def _max_box_height(self) -> int:
        """About a third of the screen, capped at 12, floored at 3.

        A fraction rather than a constant because 80x24 and a tall terminal
        want different answers; the ceiling stops the box swallowing a large
        screen; the floor keeps it usable on a small one. Recomputed every
        render, never cached -- it changes when the terminal is resized.
        """
        return min(12, max(3, self._size().rows // 3))

    def _box_overflows(self) -> bool:
        info = self._box.window.render_info
        return info is not None and info.content_height > info.window_height

    def _chrome_limit(self) -> int:
        """Never draw chrome the terminal could rewrap on a narrowing resize."""
        return max(8, min(self._size().columns, NARROW) - 2)

    def _rule(self):
        """The line between transcript and prompt; it also carries the model
        and, when there is room, what the session has spent so far.

        The spec drew the model on the right end of the hint line, but
        anything right-aligned renders a full-width row, which the reflow
        fallback forbids. The rule has room; the model lives here instead.

        The meter is fitted whole or not at all. Everything else on this row is
        truncated to the limit, which is fine for a model name -- a reader can
        see it was cut -- and not fine for a number, because `$1.3` is a
        plausible reading of `$1.34` and there is nothing on screen to say it
        was ever longer. Widening the row instead is not available: it is the
        reflow contract, and a rule that survives a resize is worth more than a
        cost that is always visible. `/status` has the number unconditionally.
        """
        limit = self._chrome_limit()
        text = f"── {self._state.config.model} "
        meter = self._meter()
        if meter:
            wider = f"{text}── {meter} "
            if len(wider) + _RULE_TAIL <= limit:
                text = wider
        return [("class:hint", (text + "─" * limit)[:limit])]

    def _meter(self) -> str:
        """What the session has spent, abbreviated, or "" if it cannot say.

        `getattr` rather than an attribute: the shell is built before its
        session exists (`attach` wires it in afterwards, so the slot holds
        None until then), and it is read on every render regardless.
        """
        spent = getattr(self._state.session, "usage", None)
        return spent.brief() if spent is not None else ""

    def _hint(self):
        limit = self._chrome_limit()
        # While a turn is running the spinner owns the hint line -- it is
        # chrome exactly like the rest of this row, so it gets the same
        # `_chrome_limit()` slice rather than a width of its own.
        if self._state.turn_running and self._status:
            return [("class:hint", self._status[:limit])]
        text = self._box.text
        if text.startswith("/") and " " in text and "\n" not in text:
            found = resolve(text, self._registry)
            if found is not None and found[0].argument_hint:
                return [("class:hint", f"  /{found[0].name} {found[0].argument_hint}"[:limit])]
        # Exactly `_chrome_limit()` (38) at full width -- there is no room to
        # spare, so this is not the design doc's mock verbatim ("for newline").
        return [("class:hint", "  / for commands · shift+enter newline"[:limit])]

    def write(self, text: str, *, style: str = "system") -> None:
        if style == "clear":
            print("\x1b[2J\x1b[H", end="")  # viewport only; scrollback untouched
            return
        if style in {"normal", "warning"}:
            print(text)
            return
        for line in transcript.notice_lines(text, self._size().columns) or [""]:
            print(line)

    def _echo(self, lines: list[str]) -> None:
        for line in lines:
            print(line)

    # -- Ui ---------------------------------------------------------------

    async def choose(
        self, title: str, rows: Sequence[Choice], *, current: int = 0, subtitle: str = ""
    ) -> Choice | None:
        # select.choose suspends this application itself (in_terminal); a
        # second wrapper here would be redundant, not harmful.
        return await select.choose(title, rows, current=current, subtitle=subtitle)

    async def ask_line(self, prompt: str) -> str | None:
        # in_terminal() is mandatory. Without it this application's own
        # postponed redraw can land underneath the open prompt and drag the
        # real cursor a row out from under it -- the displacement defect
        # diagnosed in fable-selector-diagnosis.md. Nothing else suppresses
        # this app's renderer while the nested one runs.
        try:
            async with in_terminal():
                return await PromptSession(message=prompt).prompt_async()
        except (EOFError, KeyboardInterrupt):
            return None

    async def confirm(self, question: str) -> bool:
        picked = await self.choose(question, [Choice("no", "No"), Choice("yes", "Yes")], current=0)
        return picked is not None and picked.value == "yes"

    @property
    def from_thread(self) -> _FromThread:
        return _FromThread(self)

    async def _drain_prompt_requests(self) -> None:
        """Run prompts `_FromThread` posted, on the loop, in the loop's own
        context -- the ambient session a nested prompt needs to inherit.

        Nothing prompt_toolkit-shaped is ever built on the posting thread:
        only the coroutine crosses the queue, and creating one (calling
        `self.choose(...)` etc.) runs none of its body -- the `Application`
        or `PromptSession` inside it is only ever constructed here, the
        moment this actually awaits it. `choose` and `ask_line` each already
        wrap themselves in `in_terminal()`, so this does not wrap again.

        `app.exit()` cancels this and awaits it with `timeout=None`. If this
        happens to be idle at `self._prompt_queue.get()` when that lands, the
        `CancelledError` is raised *outside* the inner `try` below and would
        otherwise propagate straight out with nothing left to drain whatever
        a tool thread posts a moment later -- that thread would then block on
        its future forever, since nothing would ever set it. The outer
        `try`/`finally` closes that: on *any* exit from this coroutine,
        cancelled idle, cancelled mid-prompt, or (in principle) a normal
        return, it marks the shell closed and empties the queue, declining
        everything left in it, before letting the cancellation continue.
        """
        try:
            while True:
                coroutine, future = await self._prompt_queue.get()
                try:
                    result = await coroutine
                except asyncio.CancelledError:
                    # The app is exiting; a tool thread blocked on this must
                    # not hang forever just because nothing will ever answer
                    # it.
                    if not future.done():
                        future.set_exception(_PromptUnavailable())
                    raise
                except BaseException as error:  # noqa: BLE001 - the waiting thread must always be released
                    if not future.done():
                        future.set_exception(error)
                else:
                    if not future.done():
                        future.set_result(result)
        finally:
            # Set *before* draining what is left: `_post_or_decline` (what
            # every posting call actually goes through, always on the loop)
            # checks this flag, so once it is true nothing more can ever
            # reach the queue below. Both this and `_post_or_decline` only
            # ever run on the loop, so -- unlike a flag read on the posting
            # thread -- there is no window where the two disagree.
            self._closed = True
            while not self._prompt_queue.empty():
                coroutine, future = self._prompt_queue.get_nowait()
                coroutine.close()  # never awaited; avoid a "was never awaited" warning
                if not future.done():
                    future.set_exception(_PromptUnavailable())

    def _post_or_decline(self, coroutine, future: concurrent.futures.Future) -> None:
        """The only thing that ever puts a request onto `_prompt_queue`.

        Always scheduled onto the loop via `call_soon_threadsafe` from
        `_FromThread._run` -- never called directly from a tool thread -- so
        this check of `self._closed` races nothing: `_drain_prompt_requests`'s
        own shutdown also only ever touches `_closed` and the queue from the
        loop. Whichever of the two the loop happens to run first, the other
        sees its result correctly, closing the window a plain boolean read on
        the posting thread could only narrow, never close.
        """
        if self._closed:
            coroutine.close()  # never awaited; avoid a "was never awaited" warning
            if not future.done():
                future.set_exception(_PromptUnavailable())
            return
        self._prompt_queue.put_nowait((coroutine, future))

    # -- keys -------------------------------------------------------------

    def _bindings(self) -> KeyBindings:
        # Newline is Shift+Enter (`_SHIFT_ENTER`, module level), not
        # Alt+Enter. That is what lets plain `escape` below be `eager=True`:
        # Alt+Enter is Escape+Enter on the wire, so binding it would make
        # `escape` a prefix of a longer chord, and prompt_toolkit forbids
        # `eager=True` combined with that -- an eager escape unconditionally
        # shadows any longer sequence starting with it (verified in task 10).
        # With no Escape-prefixed chord left anywhere in this `KeyBindings`,
        # there is nothing left to shadow, and eager buys something real: a
        # lone Escape used to wait out an ambiguous-key timeout (~1.5s) before
        # firing, during which Escape followed by `/` was swallowed as Emacs
        # `M-/` (`key_binding/bindings/emacs.py:300`) -- pressing Esc then
        # typing `/status` lost the slash. Eager matching is decided before
        # any longer match is even considered, so that window is gone.
        keys = KeyBindings()

        @keys.add(_SHIFT_ENTER)
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("enter")
        def _submit_key(event) -> None:
            # Captured synchronously: keys queued behind this Enter are typed
            # into the box before any scheduled coroutine runs, so reading
            # `self._box.text` inside the task would read the *next* input.
            text = self._box.text
            if text.endswith("\\"):
                event.current_buffer.delete_before_cursor()
                event.current_buffer.insert_text("\n")
                return
            self._box.text = ""
            outcome = dispatch.classify(
                text,
                self._registry,
                turn_running=self._state.turn_running,
                command_running=self._commands_running > 0,
            )
            if outcome.kind == "send":
                # `turn_running` flips here, synchronously, not inside
                # `_run_turn` once it gets to run. A lone Escape typed right
                # behind this Enter can be resolved -- by both the vt100
                # parser and the key processor -- in the very same input
                # batch, with no event-loop turn in between; if the flip
                # waited for the scheduled task to actually start, `_abandon`
                # below would still see `turn_running=False` and do nothing.
                self._state = dataclasses.replace(self._state, turn_running=True)
                self._abandoned = False
                self._forcing = False
                # Submitted to the executor *here*, synchronously -- not
                # `asyncio.to_thread` inside `_run_turn`. `to_thread` is a
                # coroutine: it does not call `executor.submit()` until its
                # own `await` line runs, and that line only runs once the
                # scheduled `_run_turn` task gets its first turn on the loop.
                # A `/exit` or Ctrl+C in the *same input batch* schedules the
                # app's own exit before that first turn ever comes; prompt_
                # toolkit then cancels every background task, including one
                # that never ran a single line of its body -- so `session.send`
                # is never called at all, and the turn vanishes from
                # transcript.jsonl with no record whatsoever. Calling
                # `run_in_executor` here submits the work immediately, before
                # anything later in this same batch can prevent it.
                #
                # What is submitted is `_drain`, not `session.send`: the turn
                # arrives in pieces now, and they cross back to this loop
                # through `arrivals`. The queue is created here, on the loop,
                # for the same reason the work is submitted here -- `_run_turn`
                # may never get a turn to create one.
                #
                # `session.stream` is *called* here too, synchronously, and
                # only its iteration handed to the executor. Both it and the
                # runtime's `stream` are eager for this reason: starting the
                # turn is what clears the per-turn cancellation flags, and a
                # lone Escape behind this Enter is resolved in the very same
                # input batch, with no event-loop turn in between. Left to the
                # worker, those resets would land after `_abandon` had already
                # cancelled, and the turn would run on with the transcript
                # saying it had stopped.
                loop = asyncio.get_running_loop()
                arrivals: asyncio.Queue = asyncio.Queue()
                try:
                    events = self._state.session.stream(outcome.argument)
                except Exception as error:  # noqa: BLE001 - never lose the session
                    self._state = dataclasses.replace(self._state, turn_running=False)
                    self.write(f"{type(error).__name__}: {error}", style="error")
                    return
                future = loop.run_in_executor(None, self._drain, events, arrivals, loop)
                self._pending_future = future
                event.app.create_background_task(
                    self._run_turn(outcome.argument, future, arrivals)
                )
                return
            if outcome.kind == "command":
                # Counted here, synchronously, for exactly the reason
                # `turn_running` is flipped here: a lone Escape typed behind
                # this Enter is resolved in the very same input batch, with no
                # event-loop turn in between. Left to `_run_command`, which
                # does not run a line of its body until the loop gets to it,
                # the press would find no command running and do nothing.
                if self._commands_running == 0:
                    self._command_stopping = False
                self._commands_running += 1
                # Lifted here, synchronously, and not inside `_run_command`:
                # an Escape typed behind this Enter is resolved in the very
                # same input batch, before the scheduled task runs a line of
                # its body. Lifting it there would erase the stop that press
                # had already recorded -- against this very command -- and the
                # cell would start as though nothing had been pressed.
                #
                # Only a command that takes the session over. The safe-in-flight
                # ones are exactly those allowed to run *during* a cancelled
                # turn, so lifting the stop for `/status` would let a tool call
                # admitted before the press spawn its child after it.
                resume = getattr(self._state.session, "resume_work", None)
                if resume is not None and not outcome.command.safe_in_flight:
                    resume()
            event.app.create_background_task(self._run_command(outcome))

        @keys.add("escape", eager=True)
        def _abandon(event) -> None:
            if not self._state.turn_running:
                # No turn, but a command can still own a child: `/cas` runs its
                # cell on a worker precisely so this key can be read while it
                # does. Nothing else here is long enough to interrupt, and a
                # command that owns no child reports exactly that.
                if self._commands_running:
                    self._stop_command()
                return
            if self._abandoned:
                # The turn was already stopped, so this press is about the
                # child that has not taken the hint. Asking twice is how a user
                # says they would rather lose the kernel's state than keep
                # waiting for it.
                stopped = self._escalate_turn()
                # "any ... among them", not "a ... among them": the count says
                # how many children were killed, not which. Asserting that a
                # computer algebra session lost its state would be a lie
                # whenever the child that would not stop was Lean or LaTeX and
                # the kernel was sitting idle with every value still in it.
                self.write(
                    "stopped waiting; killed what had not stopped -- any computer "
                    "algebra kernel among them has lost its state"
                    if stopped
                    else "nothing left to stop; the turn is already cancelled",
                    style="warning",
                )
                return
            # A real cancellation now, and the wording says only what is still
            # true. The model stops, no further tool call runs, and the child
            # processes in flight are asked to stop -- a Lean elaboration, a
            # Tectonic compile, the cell a CAS kernel is grinding on. What is
            # still not promised is the workspace: a tool call that already
            # wrote a file has written it, and an interrupted child leaves
            # behind whatever it had got to.
            stopped = self._cancel_turn()
            self.write(
                "cancelled; interrupted the work in flight -- esc again to stop "
                "waiting and kill it"
                if stopped
                else "cancelled; work a tool had already started may still finish"
            )

        @keys.add("c-d")
        def _leave(event) -> None:
            event.app.exit(result=0)

        # Not Escape-prefixed, so `escape`'s `eager=True` above cannot shadow
        # it -- eager only preempts longer sequences that start with the key
        # it is bound to, and "c-c" shares no prefix with "escape" at all.
        @keys.add("c-c")
        def _interrupt(event) -> None:
            if not self._state.turn_running:
                event.app.exit(result=0)
                return
            if not self._forcing:
                self._forcing = True
                self.write(
                    "a turn is still running -- Ctrl+C again to leave anyway; "
                    "Lean, LaTeX, or computer algebra processes it started may "
                    "be left orphaned and its artifacts incomplete",
                    style="warning",
                )
                return
            # Through `_record_abandonment`, not a direct call: in a test,
            # `os._exit` below is replaced with something that returns
            # instead of ending the process, so `event.app.exit()` after it
            # runs for real -- which would otherwise let the ordinary
            # turn-cut-short backstop (`run_async`'s `finally`) record a
            # second, misleading "app_exited" behind this one. The idempotent
            # guard makes "forced_exit" -- the true, specific reason -- win.
            self._record_abandonment("forced_exit")
            # The worker is non-daemon (confirmed directly against
            # `ThreadPoolExecutor._adjust_thread_count`, which constructs its
            # threads with no `daemon=` argument at all -- so an ordinary exit
            # waits for `session.send` at a safe boundary rather than
            # truncating a write mid-flight. That is exactly why a *forced*
            # exit cannot go through interpreter shutdown: it would be joined,
            # and `session.send` cannot be told to stop. `os._exit` bypasses
            # that entirely, at the cost the first press already named:
            # orphaned children, no `atexit`, nothing flushed.
            os._exit(130)
            # Unreachable in production: `os._exit` never returns. Tests
            # replace it to observe the call without actually tearing down
            # the interpreter, and for that substitution to be able to prove
            # anything, this application still has to stop running -- so it
            # is told to, explicitly, rather than relying on a process exit
            # that, under the mock, never happens.
            event.app.exit(result=130)

        return keys

    def _record_abandonment(self, reason: str) -> None:
        """Idempotent: the first reason recorded for a turn wins.

        Still "abandoned" rather than "cancelled", and still distinct from
        `_cancel_turn`. These are the paths where the turn was *not* stopped --
        the app is going away with work still running -- and calling that a
        cancellation would claim something nothing did.
        """
        if self._abandoned:
            return
        self._abandoned = True
        session = self._state.session
        if session is not None and hasattr(session, "record_abandonment"):
            session.record_abandonment(reason)

    def _cancel_turn(self) -> int:
        """Stop the turn in flight. Idempotent, like the abandonment record.

        Returns how many child processes were asked to stop, so the notice can
        say what the press reached instead of describing work in general.
        """
        if self._abandoned:
            return 0
        self._abandoned = True
        session = self._state.session
        if session is not None and hasattr(session, "cancel"):
            # `cancel` gained a return value when Esc gained the power to
            # interrupt. A session predating that answers `None`, which is not
            # a count and must not be reported as one.
            stopped = session.cancel("user_pressed_escape")
            return stopped if isinstance(stopped, int) else 0
        if session is not None and hasattr(session, "record_abandonment"):
            # A session too old to be told to stop. Say so honestly rather
            # than claiming a cancellation that did not happen.
            session.record_abandonment("user_pressed_escape")
        return 0

    def _escalate_turn(self) -> int:
        """Kill what the interrupt did not stop. The second Esc."""
        session = self._state.session
        escalate = getattr(session, "escalate", None)
        if escalate is None:
            return 0
        stopped = escalate()
        return stopped if isinstance(stopped, int) else 0

    def _drain(
        self,
        events: Any,
        arrivals: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Wait on a turn already started by `_submit_key`; post what arrives.

        Everything this touches crosses a thread boundary exactly once, through
        `call_soon_threadsafe`. `_run_turn` does all the drawing, on the loop,
        because prompt_toolkit is not safe to touch from here.

        The sentinel is posted in a `finally`, so a turn that raises still ends
        the drawing loop rather than leaving it waiting on a queue that nothing
        will ever fill. The exception itself travels on the future.
        """
        try:
            for event in events:
                loop.call_soon_threadsafe(arrivals.put_nowait, event)
        finally:
            loop.call_soon_threadsafe(arrivals.put_nowait, _TURN_OVER)

    async def _run_command(self, outcome: dispatch.Outcome) -> None:
        # The count was raised by `_submit_key` before this task existed, and
        # stays up for the whole handler rather than only the part that runs a
        # child: `dispatch.classify` reads it to refuse a second command or a
        # model turn while one is in flight, and that has to hold while a
        # handler is waiting on a prompt of its own as much as while it is
        # computing.
        if outcome.kind == "empty":
            return
        if outcome.kind in {"unknown", "refused"}:
            self.write(outcome.message, style="error")
            return
        # The stop was lifted by `_submit_key`, before any Escape in the same
        # input batch could be resolved. Doing it here would undo that press.
        try:
            self._state = await outcome.command.handler(self, outcome.argument, self._state)
        except Exception as error:  # noqa: BLE001 - a bad command must not end the session
            self.write(f"{type(error).__name__}: {error}", style="error")
        finally:
            # Wherever the handler ended, including a raise: a count left up
            # would refuse every command and every turn after it.
            self._commands_running -= 1
        if self._state.done:
            self._app.exit(result=0)

    def _stop_command(self) -> None:
        """Esc against a command rather than a turn.

        There is no turn to unwind and nothing to record as cancelled -- a
        command is not a turn, and writing one into the transcript would claim
        the model had been stopped. All this does is reach the child, which for
        `/cas` is the cell in the kernel the human and the model share.
        """
        session = self._state.session
        if self._command_stopping:
            escalate = getattr(session, "escalate", None)
            stopped = escalate() if escalate is not None else 0
            self.write(
                "stopped waiting; killed what had not stopped -- any computer "
                "algebra kernel among them has lost its state"
                if isinstance(stopped, int) and stopped
                else "nothing left to stop",
                style="warning",
            )
            return
        self._command_stopping = True
        interrupt = getattr(session, "interrupt_work", None)
        stopped = interrupt() if interrupt is not None else 0
        self.write(
            "interrupted; esc again to stop waiting and kill it"
            if isinstance(stopped, int) and stopped
            else "nothing running to interrupt"
        )

    async def _run_turn(
        self, text: str, future: asyncio.Future, arrivals: asyncio.Queue
    ) -> None:
        """Draw a turn already submitted to the executor by `_submit_key`.

        `future` is already running on a worker thread by the time this is
        even scheduled -- see the comment there for why submission cannot
        wait until this coroutine gets to run. `turn_running` is already true
        too, for the same reason. The spinner text is chrome (module
        docstring) and drawn through `_hint`, which slices it to
        `_chrome_limit()`.

        The turn is drained here rather than awaited whole: `_drain` posts each
        event as it arrives, and the future is awaited afterwards only to
        collect whatever it raised.
        """
        self._echo(transcript.user_lines(text, self._size().columns))
        started = asyncio.get_running_loop().time()
        painter = stream.TurnPainter(self._size().columns)

        async def spinner() -> None:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            tick = 0
            while self._state.turn_running:
                elapsed = int(asyncio.get_running_loop().time() - started)
                frame = frames[tick % len(frames)]
                # Naming the tool is the difference between a spinner that
                # reassures and one that only proves the process is alive.
                doing = painter.running or "working"
                self._status = f"{frame} {doing} · {elapsed}s · esc to cancel"
                tick += 1
                self._app.invalidate()
                await asyncio.sleep(0.1)

        watch = asyncio.create_task(spinner())
        failure: BaseException | None = None
        try:
            while True:
                event = await arrivals.get()
                if event is _TURN_OVER:
                    break
                self._echo(painter.draw(event))
            # Only now: `_drain` posts the sentinel in a `finally`, so the
            # queue has already ended by the time the future can be inspected,
            # and this cannot wait on work that has stopped.
            await future
        except asyncio.CancelledError:
            # The app is exiting (Ctrl+C, /exit) with the turn still in flight.
            # This task *did* get a turn on the loop (it reached this line), so
            # it is the one place that can record the reason distinctly from
            # Esc; `run_async`'s own backstop only covers a task that never ran
            # at all. Re-raised so cancellation still propagates normally.
            self._record_abandonment("app_exited")
            # The tail below is never reached from here -- this re-raises so
            # cancellation still propagates -- and the painter is still holding
            # the line it was wrapping. Flushed before going, for the reason
            # `--plain` flushes on Ctrl+C: words the user has already watched
            # arrive must not vanish because the app is leaving.
            self._echo(painter.finish())
            raise
        except Exception as error:  # noqa: BLE001 - never lose the session to one bad turn
            failure = error
        finally:
            if self._pending_future is future:
                self._pending_future = None
            self._state = dataclasses.replace(self._state, turn_running=False)
            self._status = ""
            watch.cancel()
            self._app.invalidate()

        # Whatever was streamed before a failure was really said, so the tail
        # is flushed either way rather than discarded along with the turn.
        tail = painter.finish()
        if tail and self._abandoned and not painter.streamed:
            # A turn the user stopped is not dropped, it is labelled. Only when
            # nothing was streamed: if the reply was drawn as it arrived, the
            # user watched it happen and a notice here would land in the middle
            # of prose they have already read.
            self.write("this turn was stopped; it had already replied:")
        self._echo(tail)
        if failure is not None:
            self.write(f"{type(failure).__name__}: {failure}", style="error")
            return
        print()

    # -- resize -----------------------------------------------------------

    def _guard_resize(self) -> None:
        """Resize handling must not fire while a nested prompt owns the screen.

        `Application._on_resize` (application.py:590-600) erases with this
        app's renderer *unconditionally*; only the redraw half checks
        `_running_in_terminal` (application.py:511). On Windows resizes arrive
        via `_poll_output_size` (application.py:1211-1231), which keeps
        polling while a nested prompt is open, so a resize mid-prompt would
        erase from the nested app's cursor position and wipe part of it.
        Skipped while suspended; the resume path (`in_terminal.__aexit__`,
        run_in_terminal.py:108-112) re-reads the size and repaints anyway.
        """
        original = self._app._on_resize

        def on_resize() -> None:
            if not self._app._running_in_terminal:
                original()

        self._app._on_resize = on_resize  # type: ignore[method-assign]

    # -- entry ------------------------------------------------------------

    async def run_async(self) -> int:
        # No `create_app_session` here, deliberately. The shell runs in the
        # caller's ambient session so that `set_app` during `run_async` is
        # visible to every task the caller owns -- a private session would
        # leave `in_terminal()` in prompts called from those tasks looking at
        # a session whose app is never set, and nothing would be suspended.
        # Callers that inject `input=`/`output=` (tests) wrap themselves in
        # `create_app_session(input=..., output=...)`; production passes
        # neither and the default session's devices already match.
        self._loop = asyncio.get_running_loop()
        self._app.create_background_task(self._drain_prompt_requests())
        with patch_stdout(raw=True):
            session = self._state.session
            for style, text in banner.lines(
                self._state.config,
                cas=getattr(session, "cas", None),
                cas_detail=getattr(session, "cas_detail", ""),
            ):
                self.write(text, style=style)
            print()
            try:
                return await self._app.run_async() or 0
            finally:
                # Backstop for a turn cut short by /exit or Ctrl+C rather
                # than Esc: `_run_turn`'s own task may never run a single
                # line of its body if it is cancelled before its first turn
                # on the loop (verified directly -- a task cancelled before
                # it starts skips its body entirely, `try`/`finally` included),
                # so that task cannot be the only place this gets recorded.
                # This coroutine is not itself one of the background tasks
                # prompt_toolkit cancels on exit, so it is guaranteed to run.
                future = self._pending_future
                if future is not None and not future.done():
                    self._record_abandonment("app_exited")

    def run(self) -> int:
        return asyncio.run(self.run_async())


class _PromptUnavailable(Exception):
    """Raised on the `concurrent.futures.Future` `_FromThread._run` blocks on
    when a prompt request could not be delivered at all -- the drainer is
    gone, or is going, and nothing will ever await the coroutine. Never seen
    outside `_FromThread`: each public method there catches it and returns
    its own type-appropriate decline (`None` for `choose`/`ask_line`,
    `False` for `confirm`), the same shape a refusal already takes.
    """


class _FromThread:
    """The sync facade over the async Ui, for callers that cannot await.

    The SDK calls its tool functions on their own threads, and the axiom gate
    (`cli.confirm_assumption`) is reached from there. A worker thread must not
    construct anything prompt_toolkit-shaped itself: handing a nested
    `Application` the outer app's own attached `Input` makes two applications
    share one attached input, and the nested one reads `EOFError`; giving it
    no input at all leaves it with no ambient session to inherit, because
    `contextvars` do not cross a thread boundary. So nothing is built here.
    Each method creates the coroutine (inert until awaited -- calling an
    `async def` runs none of its body) and posts *that* onto `Shell`'s own
    queue with `loop.call_soon_threadsafe`, then blocks this thread on a
    `concurrent.futures.Future`. `Shell._drain_prompt_requests`, running on
    the loop, is the only thing that ever awaits it -- which is where the
    nested `Application` actually gets built, in the loop's own context.

    Blocking the *posting* thread is correct and intended: `MathematicsSession
    ._gate` already serialises tool calls, and a pending axiom question
    should stop the turn. What must never happen is the reverse -- the UI
    thread blocking on anything -- which is what the check below exists to
    refuse outright rather than deadlock on.
    """

    def __init__(self, shell: Shell):
        self._shell = shell

    def _run(self, coroutine):
        loop = self._shell._loop
        if loop is None:
            # `run_async` has not started (or has fully finished and torn
            # down): there is no loop to compare this thread against yet.
            # The best available signal is still "is this the thread that
            # would drive it" -- true today only because `Shell.run()`
            # always starts the loop from the main thread.
            if threading.current_thread() is threading.main_thread():
                coroutine.close()  # never awaited; avoid a "was never awaited" warning
                raise RuntimeError(
                    "from_thread must not be used on the UI thread; await the Ui directly"
                )
            coroutine.close()
            raise RuntimeError("the application is not running")

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            # What actually matters is whether *this* call is already
            # running on the loop's own thread -- checking the thread
            # identity instead only happened to agree with that, because
            # nothing today drives the loop anywhere but the main thread.
            coroutine.close()
            raise RuntimeError(
                "from_thread must not be used on the UI thread; await the Ui directly"
            )

        future: concurrent.futures.Future = concurrent.futures.Future()
        loop.call_soon_threadsafe(self._shell._post_or_decline, coroutine, future)
        return future.result()

    def write(self, text: str, *, style: str = "system") -> None:
        self._shell.write(text, style=style)

    def choose(
        self, title: str, rows: Sequence[Choice], *, current: int = 0, subtitle: str = ""
    ) -> Choice | None:
        try:
            return self._run(self._shell.choose(title, rows, current=current, subtitle=subtitle))
        except _PromptUnavailable:
            return None

    def ask_line(self, prompt: str) -> str | None:
        try:
            return self._run(self._shell.ask_line(prompt))
        except _PromptUnavailable:
            return None

    def confirm(self, question: str) -> bool:
        try:
            return self._run(self._shell.confirm(question))
        except _PromptUnavailable:
            return False
