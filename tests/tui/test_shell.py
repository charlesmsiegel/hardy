"""The shell: ghost text, the capped box, and the reflow-safety contract.

Two rendering properties here are load-bearing and each has a test that fails
without the code providing it:

- the box height cap is recomputed from the live terminal size (a static
  Dimension fails `test_the_box_cap_follows_a_resize`);
- no chrome row is wide enough for a narrowing resize to rewrap it, which is
  the whole reflow strategy (a bordered Frame fails
  `test_no_chrome_row_reaches_the_reflow_hazard_width`).

The nested-prompt and resize-while-prompt-open tests run under
`assert_no_outer_render_during_nested`, which raises if this application ever
paints or erases underneath an open prompt and refuses to pass vacuously.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.dimension import to_dimension
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy.tui import handlers, shell
from hardy.tui.commands import Command
from hardy.tui.ports import Choice
from hardy.usage import Usage

from .conftest import Streams
from .nested_render import assert_no_outer_render_during_nested


def _vt100(buffer: StringIO, holder: dict[str, int]) -> Vt100_Output:
    return Vt100_Output(
        buffer, lambda: Size(rows=holder["rows"], columns=holder["cols"]), term="xterm"
    )


def suggester():
    return shell.CommandSuggester(handlers.build_registry())


class FakeBuffer:
    def __init__(self, text: str):
        self.document = Document(text, len(text))
        self.text = text


# -- ghost text and completion (pure) -------------------------------------


def test_a_unique_prefix_is_suggested():
    suggestion = suggester().get_suggestion(FakeBuffer("/mo"), Document("/mo", 3))
    assert suggestion is not None and suggestion.text == "del"


def test_an_alias_prefix_suggests_the_alias_not_the_canonical_name():
    suggestion = suggester().get_suggestion(FakeBuffer("/q"), Document("/q", 2))
    assert suggestion is not None and suggestion.text == "uit"


def test_an_ambiguous_prefix_suggests_nothing():
    async def _noop(ui, argument, state):
        return state

    clashing = [Command("status", "s", _noop), Command("setup", "s", _noop)]
    assert shell.CommandSuggester(clashing).get_suggestion(FakeBuffer("/s"), Document("/s", 2)) is None
    assert suggester().get_suggestion(FakeBuffer("hello"), Document("hello", 5)) is None


def test_a_multiline_buffer_gets_no_suggestion():
    text = "/mo\nremainder"
    assert suggester().get_suggestion(FakeBuffer(text), Document(text, len(text))) is None


def test_the_completer_offers_every_match():
    completer = shell.CommandCompleter(handlers.build_registry())
    offered = [c.text for c in completer.get_completions(Document("/", 1), None)]
    assert "/model" in offered and "/help" in offered


def test_the_bare_slash_menu_lists_canonical_entries_only():
    completer = shell.CommandCompleter(handlers.build_registry())
    offered = [c.text for c in completer.get_completions(Document("/", 1), None)]
    assert "/quit" not in offered  # alias of /exit
    assert "/exit" in offered
    # A prefix that names the alias still completes it: it is what was typed.
    offered = [c.text for c in completer.get_completions(Document("/q", 2), None)]
    assert offered == ["/quit"]


def test_the_style_defines_every_name_the_ports_declare():
    names = dict(shell.STYLE.style_rules)
    for style in ("user", "hardy", "system", "error", "warning", "hint"):
        assert any(rule.startswith(style) for rule in names), style


# -- input history -----------------------------------------------------


def test_input_history_lives_under_local_not_the_problem_directory(settings):
    """Every line typed here, sent or not, is machine-local.

    Previously written to `<problem>/input-history`, which the problem's own
    `.gitignore` (`/.build/`, `/.local/`) does not cover -- so text a user
    typed and never sent sat as an ordinary trackable file, exactly the
    provider-session-id and spend-ledger leak `.local/` exists to prevent for
    everything else.
    """
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), {"rows": 24, "cols": 80})
        )
        history_path = Path(built._box.buffer.history.filename)
        assert history_path == settings.layout.input_history
        assert history_path.parent == settings.layout.local
        assert not (settings.layout.problem / "input-history").exists()
        # Written through the guard, not merely aimed at a checked path:
        # prompt_toolkit opens this file itself, once per accepted line, for
        # the rest of the session.
        built._box.buffer.history.store_string("theorem hardyOne")
        assert "theorem hardyOne" in history_path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="symlink_to needs Developer Mode on Windows")
def test_a_symlinked_input_history_falls_back_to_memory(settings):
    """Every line typed here, sent or not, goes in this file.

    A link out of the project is therefore both an escape and a disclosure --
    and neither is worth ending a terminal over, so the refusal costs the
    history rather than the session.
    """
    settings.layout.local.mkdir(parents=True, exist_ok=True)
    victim = settings.root / "victim.sh"
    victim.write_text("#!/bin/sh\n", encoding="utf-8")
    settings.layout.input_history.symlink_to(victim)
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), {"rows": 24, "cols": 80})
        )
        built._box.buffer.history.store_string("theorem hardyOne")
    assert victim.read_text(encoding="utf-8") == "#!/bin/sh\n"


# -- the capped, resize-following box -------------------------------------


def test_the_box_cap_follows_a_resize(settings):
    """min(12, max(3, rows // 3)), re-read from the live size every render.

    Fails if the height were a Dimension captured at construction: the second
    and third assertions would still see the first cap.
    """
    holder = {"rows": 24, "cols": 80}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )

        def cap() -> int:
            return to_dimension(built._box.window.height).max

        assert cap() == 8  # 24 // 3
        holder["rows"] = 60
        assert cap() == 12  # ceiling
        holder["rows"] = 9
        assert cap() == 3  # floor


async def test_no_chrome_row_reaches_the_reflow_hazard_width(settings):
    """The reflow contract: nothing the shell draws can be rewrapped at >=40.

    A terminal rewraps a stored row only if it is wider than the new width,
    and prompt_toolkit's renderer cannot survive that (its erase coordinates
    predate the rewrap). So the one thing an application controls -- how wide
    its rows are -- is the invariant: at 120 columns, no chrome row may extend
    to column NARROW or beyond. A bordered Frame fails this immediately.

    Async because writing the layout touches the buffer's lazy history load,
    which schedules a background task on the running loop.
    """
    holder = {"rows": 24, "cols": 120}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )
        layout = built._app.layout
        height = layout.container.preferred_height(120, 24).preferred
        screen = Screen()
        with set_app(built._app):
            layout.container.write_to_screen(
                screen, MouseHandlers(), WritePosition(0, 0, 120, height), "", False, None
            )
        await asyncio.sleep(0.05)  # let the history-load task settle before teardown
    for y in range(screen.height):
        used = [x for x, cell in screen.data_buffer[y].items() if cell.char.strip()]
        assert not used or max(used) < shell.NARROW, (
            f"row {y} draws at column {max(used)}; a narrowing resize would rewrap it"
        )


# -- the spend meter in the rule ------------------------------------------


#: A ledger a fully reporting backend would have left after four exchanges.
SPENT = Usage(
    turns=4, input_tokens=82_431, cost_usd=1.34,
    reports=dict.fromkeys(("cost_usd", *Usage.COUNTERS), 4),
)


def _rule(settings, session, columns: int = 120) -> str:
    holder = {"rows": 24, "cols": columns}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, session, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )
        return built._rule()[0][1]


def test_the_rule_carries_what_the_session_has_spent(settings):
    """The acceptance criterion: the number is on screen without asking for it."""
    drawn = _rule(settings, SimpleNamespace(usage=SPENT))
    assert "claude-opus-5" in drawn
    assert "$1.34" in drawn
    assert "82k" in drawn


def test_a_session_that_has_spent_nothing_leaves_the_rule_as_it_was(settings):
    assert _rule(settings, SimpleNamespace(usage=Usage())) == _rule(settings, None)


def test_a_narrow_terminal_drops_the_meter_rather_than_shrinking_the_rule(settings):
    """Below the point where it fits, the meter goes -- whole. Half of `$1.34`
    is a number that is not true, and the reflow contract forbids the row from
    growing to make room."""
    drawn = _rule(settings, SimpleNamespace(usage=SPENT), columns=24)
    assert "$" not in drawn
    assert "claude-opus-5" in drawn


def test_a_long_model_name_costs_the_meter_and_not_its_last_digits(settings):
    """The rule is truncated to the chrome limit, so a meter that only half
    fits would be drawn as a plausible, wrong number."""
    settings = dataclasses.replace(settings, model="claude-sonnet-4-5-20250929")
    drawn = _rule(settings, SimpleNamespace(usage=SPENT))
    assert "$" not in drawn
    assert len(drawn) <= shell.CHROME


def test_the_meter_never_widens_a_chrome_row_past_the_reflow_hazard(settings):
    """Every width from useless to generous: the invariant is unconditional."""
    for columns in range(8, 200, 3):
        drawn = _rule(settings, SimpleNamespace(usage=SPENT), columns=columns)
        assert len(drawn) < shell.NARROW, (columns, drawn)
        # Whatever survived the fit, no digit of it may have been cut off.
        assert "$" not in drawn or "$1.34 · 82k" in drawn


def test_the_meter_is_re_read_every_render_rather_than_captured(settings):
    """A total sampled when the shell was built would be zero forever. The
    session is also wired in by `attach` *after* construction, so the rule has
    to reach through the live state on each pass, not a remembered session."""
    holder = {"rows": 24, "cols": 120}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )
        assert "$" not in built._rule()[0][1]
        session = SimpleNamespace(usage=Usage())
        built.attach(session)
        assert "$" not in built._rule()[0][1]
        session.usage = SPENT
        assert "$1.34" in built._rule()[0][1]


def test_a_session_with_no_ledger_at_all_still_draws_its_rule(settings):
    """`attach` runs after construction, and the plain session is not the only
    thing that may sit in that slot."""
    assert "claude-opus-5" in _rule(settings, object())


async def test_no_chrome_row_reaches_the_hazard_width_while_a_meter_is_drawn(settings):
    """`test_no_chrome_row_reaches_the_reflow_hazard_width` with the session
    that has something to report -- the meter is new chrome and inherits the
    contract rather than being exempt from it."""
    holder = {"rows": 24, "cols": 120}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, SimpleNamespace(usage=SPENT), handlers.build_registry(),
            input=pipe, output=_vt100(StringIO(), holder),
        )
        layout = built._app.layout
        height = layout.container.preferred_height(120, 24).preferred
        screen = Screen()
        with set_app(built._app):
            layout.container.write_to_screen(
                screen, MouseHandlers(), WritePosition(0, 0, 120, height), "", False, None
            )
        await asyncio.sleep(0.05)
    for y in range(screen.height):
        used = [x for x, cell in screen.data_buffer[y].items() if cell.char.strip()]
        assert not used or max(used) < shell.NARROW, (
            f"row {y} draws at column {max(used)}; a narrowing resize would rewrap it"
        )


async def test_the_scrollbar_appears_only_when_the_box_overflows(settings):
    """The scrollbar must be conditional, or the reflow fallback is undone.

    An unconditional ScrollbarMargin paints the window's last column on every
    row -- exactly the full-width chrome that a narrowing resize rewraps and
    that `test_no_chrome_row_reaches_the_reflow_hazard_width` exists to
    forbid. So: with a short buffer no cell anywhere may carry the scrollbar
    (nor any ink at or past NARROW); once the buffer overflows the cap the
    scrollbar must appear at the window's edge; and it must vanish again when
    the buffer shrinks. Scrollbar cells are styled spaces, so the check is on
    style, not characters.

    Each state is painted twice: margin widths are cached per render_counter
    and the overflow filter reads the previous render's `render_info`, so the
    margin is one render late by prompt_toolkit's design. Two paints make the
    assertion deterministic instead of racing that lag.
    """
    holder = {"rows": 24, "cols": 120}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )
        layout = built._app.layout

        def paint() -> Screen:
            built._app.render_counter += 1  # margin widths are cached per render
            height = layout.container.preferred_height(120, 24).preferred
            screen = Screen()
            with set_app(built._app):
                layout.container.write_to_screen(
                    screen, MouseHandlers(), WritePosition(0, 0, 120, height), "", False, None
                )
            return screen

        def scrollbar_columns(screen: Screen) -> set[int]:
            return {
                x
                for y in range(screen.height)
                for x, cell in screen.data_buffer[y].items()
                if "scrollbar" in cell.style
            }

        def inked_past_hazard(screen: Screen) -> bool:
            return any(
                x >= shell.NARROW
                for y in range(screen.height)
                for x, cell in screen.data_buffer[y].items()
                if cell.char.strip() or "scrollbar" in cell.style
            )

        # Short buffer: no scrollbar cell exists anywhere, and nothing at all
        # reaches the hazard width.
        paint()
        screen = paint()
        assert not scrollbar_columns(screen)
        assert not inked_past_hazard(screen)

        # Overflowing buffer (20 logical lines against a cap of 8): the
        # scrollbar appears, at the window's right edge.
        built._box.text = "\n".join(f"line {i}" for i in range(20))
        paint()
        screen = paint()
        assert scrollbar_columns(screen) == {119}

        # And it reverts when the buffer shrinks back under the cap.
        built._box.text = "x"
        paint()
        screen = paint()
        assert not scrollbar_columns(screen)
        assert not inked_past_hazard(screen)

        await asyncio.sleep(0.05)  # let the history-load task settle before teardown


# -- the session actually receives input ----------------------------------


class FakeSession(Streams):
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> str:
        self.sent.append(text)
        return "answered"

    def switch_model(self, model) -> None: ...


def test_typing_and_submitting_reaches_the_session(settings):
    """The box must actually feed the session, not merely render."""
    session = FakeSession()
    holder = {"rows": 24, "cols": 80}
    with create_pipe_input() as pipe:
        output = _vt100(StringIO(), holder)
        with create_app_session(input=pipe, output=output):
            pipe.send_text("is pi irrational?\r/exit\r")
            code = shell.Shell(
                settings, session, handlers.build_registry(), input=pipe, output=output
            ).run()
    assert code == 0
    assert session.sent == ["is pi irrational?"]


async def test_the_rendered_output_dims_the_ghost_text(settings):
    """DummyOutput cannot prove this; inspecting the rendered `Screen`'s
    styled cells can, the same technique `test_no_chrome_row_reaches_the_
    reflow_hazard_width` and the scrollbar test below already use.

    An earlier version of this test drove real key input through a pipe and
    pattern-matched the raw ANSI byte stream for `/mo` immediately followed
    by a styled `del`. That is fooled by a real, valid rendering behaviour:
    prompt_toolkit's renderer is diff-based, and the suggestion is computed
    by a background task (`Buffer._create_auto_suggest_coroutine`), not
    synchronously as part of typing. Under load that computation can finish
    *after* a first render has already drawn `/mo` alone; a second render
    then draws only the new `del` cells (the diff), without re-emitting
    `/mo`, so the two are no longer byte-adjacent even though the terminal's
    actual, single, current screen shows exactly what it always did. Caught
    by instrumenting a real failure under full-suite load and inspecting the
    captured bytes directly (not committed): `/mo` and the styled `del`
    each appeared, correctly, just in two separate write calls with cursor-
    control codes in between, which the regex could not see past. Reading
    the rendered screen's cells is immune to this, because it reflects the
    terminal's current state directly rather than the sequence of writes
    that produced it, and needs no pipe, no key timing, and no exit at all.
    """
    holder = {"rows": 24, "cols": 80}
    with create_pipe_input() as pipe:
        built = shell.Shell(
            settings, None, handlers.build_registry(), input=pipe, output=_vt100(StringIO(), holder)
        )
        # `insert_text`, not setting `.document`/`.text` directly: auto-suggest
        # is scheduled from inside `Buffer.insert_text` itself (`buffer.py`,
        # guarded by `fire_event`), not by the generic text-changed event, and
        # it calls `get_app()` to do the scheduling -- which needs `set_app`
        # active, or it resolves to no application in particular.
        with set_app(built._app):
            built._box.buffer.insert_text("/mo")
            # The suggestion is computed by that background task, not
            # synchronously as part of typing, so wait for the actual event
            # -- a fixed guess is exactly what made the old version of this
            # test flaky under load.
            for _ in range(100):
                if built._box.buffer.suggestion is not None:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("no suggestion was ever computed for '/mo'")

        layout = built._app.layout
        height = layout.container.preferred_height(80, 24).preferred
        screen = Screen()
        with set_app(built._app):
            layout.container.write_to_screen(
                screen, MouseHandlers(), WritePosition(0, 0, 80, height), "", False, None
            )
        await asyncio.sleep(0.05)  # let the history-load task settle before teardown

    ghost_is_styled_differently = False
    for y in range(screen.height):
        cells = screen.data_buffer[y]
        width = max(cells.keys(), default=-1) + 1
        row = [cells.get(x) for x in range(width)]
        text = "".join(cell.char if cell else " " for cell in row)
        start = text.find("/mo")
        if start == -1:
            continue
        tail_start = start + 3
        if text[tail_start : tail_start + 3] != "del":
            continue
        # A bare `"del" in written` would be vacuous: the banner's word
        # "Model:" contains "del" too. Requiring a *different* style from
        # the typed "/mo" right before it is what rules that out -- the
        # banner's text is never styled the way the dim suggestion is.
        ghost_is_styled_differently = row[tail_start].style != row[start].style
        break
    assert ghost_is_styled_differently, "no differently-styled ghost tail 'del' found after '/mo'"


# -- nested prompts under the interleaving assertion ----------------------


async def test_nested_prompts_never_let_the_shell_paint_underneath(settings):
    """choose, ask_line, and confirm, each with a pending shell invalidate.

    The invalidate before each prompt arms the postponed redraw that displaced
    the spike's selector; the helper raises if it ever lands mid-prompt, and
    raises too if no prompt actually rendered. Removing `in_terminal()` from
    `ask_line` (or routing `choose` around `select.choose`) fails this test.
    """
    buffer = StringIO()
    holder = {"rows": 24, "cols": 80}
    answers: dict[str, object] = {}
    with create_pipe_input() as pipe:
        output = _vt100(buffer, holder)
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            )

            async def ask(coroutine, reply: str):
                built._app.invalidate()  # arm the failing shape
                task = asyncio.ensure_future(coroutine)
                await asyncio.sleep(0.3)
                pipe.send_text(reply)
                return await task

            async def driver() -> None:
                await asyncio.sleep(0.15)
                rows = [Choice("a", "alpha"), Choice("b", "beta")]
                answers["choose"] = await ask(built.choose("Pick", rows), "\r")
                answers["ask_line"] = await ask(built.ask_line("Name: "), "ok\r")
                answers["confirm"] = await ask(built.confirm("Sure?"), "\r")
                await asyncio.sleep(0.1)
                pipe.send_text("\x03")

            with assert_no_outer_render_during_nested():
                driving = asyncio.ensure_future(driver())
                code = await built.run_async()
                await driving
    assert code == 0
    assert answers["choose"] is not None and answers["choose"].value == "a"
    assert answers["ask_line"] == "ok"
    assert answers["confirm"] is False  # first row is No, on purpose


async def test_a_resize_while_a_prompt_is_open_never_touches_the_outer_screen(settings):
    """The size poll must not erase under an open prompt.

    `Application._on_resize` erases with the outer renderer even while
    suspended (only its redraw half checks `_running_in_terminal`), and the
    Windows size poll keeps running while a nested prompt is open. The shell
    guards `_on_resize`; removing the guard fails this test through the
    helper's erase tracking.
    """
    buffer = StringIO()
    holder = {"rows": 24, "cols": 80}
    got: dict[str, object] = {}
    with create_pipe_input() as pipe:
        output = _vt100(buffer, holder)
        with create_app_session(input=pipe, output=output):
            built = shell.Shell(
                settings, None, handlers.build_registry(), input=pipe, output=output
            )
            built._app.terminal_size_polling_interval = 0.05

            async def driver() -> None:
                await asyncio.sleep(0.2)  # app up; the poll has its baseline
                task = asyncio.ensure_future(built.ask_line("Name: "))
                await asyncio.sleep(0.2)  # prompt open, shell suspended
                holder["rows"] = 34  # the resize
                await asyncio.sleep(0.3)  # several polls observe it
                pipe.send_text("ok\r")
                got["answer"] = await task
                await asyncio.sleep(0.1)
                pipe.send_text("\x03")

            with assert_no_outer_render_during_nested() as recorded:
                driving = asyncio.ensure_future(driver())
                code = await built.run_async()
                await driving
    assert code == 0
    assert got["answer"] == "ok"
    assert recorded.erases, "sanity: suspension itself must have recorded an erase"


# -- Esc against a project reopen -----------------------------------------


def test_escape_reaches_a_reopen_in_flight_not_the_old_session(settings):
    """`/project switch` runs on a worker precisely so this key stays live.

    The kernel it may be stuck probing belongs to neither session -- not the
    one being replaced, which is what `state.session` is, and not yet the one
    being built -- so reaching only the session reported that nothing was
    running while the switch went on waiting out the probe.
    """
    stopped = []

    class Reopening:
        def __call__(self, slug, confirm, current): ...

        def cancel(self) -> bool:
            stopped.append(True)
            return True

    session = SimpleNamespace(interrupt_work=lambda: pytest.fail("the old session was signalled"))
    built = shell.Shell(settings, session, handlers.build_registry(), reopen=Reopening())
    built._stop_command()

    assert stopped == [True]


def test_escape_still_reaches_a_running_cell_when_no_reopen_is_in_flight(settings):
    """An opener with nothing open answers False, so the cell keeps the press."""
    asked = []

    class Idle:
        def __call__(self, slug, confirm, current): ...

        def cancel(self) -> bool:
            return False

    session = SimpleNamespace(interrupt_work=lambda: asked.append(True) or 1)
    built = shell.Shell(settings, session, handlers.build_registry(), reopen=Idle())
    built._stop_command()

    assert asked == [True]


def test_escape_reaches_the_work_a_command_published(settings):
    """`/prove`'s staged run is not a tracked child, so `interrupt_work` cannot
    answer for it: the provider call and the stage loop would go on after the
    press. A command that owns work of its own publishes a stop, and Esc takes
    that before it reaches the session."""
    stopped = []
    session = SimpleNamespace(
        interrupt_work=lambda: pytest.fail("the session answered for the command's work")
    )
    built = shell.Shell(settings, session, handlers.build_registry())
    built.stopping(lambda: stopped.append(True) or True)
    built._stop_command()

    assert stopped == [True]


def test_escape_falls_through_to_the_session_when_no_command_published_one(settings):
    """Every command but `/prove` today: the press means the cell, as before."""
    asked = []
    session = SimpleNamespace(interrupt_work=lambda: asked.append(True) or 1)
    built = shell.Shell(settings, session, handlers.build_registry())
    built.stopping(None)
    built._stop_command()

    assert asked == [True]


def test_a_published_stop_that_finds_nothing_leaves_the_press_to_the_session(settings):
    """Same shape as the idle opener above: False means "not mine"."""
    asked = []
    session = SimpleNamespace(interrupt_work=lambda: asked.append(True) or 1)
    built = shell.Shell(settings, session, handlers.build_registry())
    built.stopping(lambda: False)
    built._stop_command()

    assert asked == [True]


def test_escape_before_a_command_publishes_its_stop_is_not_lost(settings):
    """`_submit_key` SCHEDULES a command and returns, so an Escape typed behind
    the same Enter is resolved before the handler has run a line. The press must
    survive until the handler gets there."""
    stopped = []
    session = SimpleNamespace(interrupt_work=lambda: 1)
    built = shell.Shell(settings, session, handlers.build_registry())
    built._commands_running = 1               # as `_submit_key` sets it
    built._stop_command()                     # the press, before publication
    assert stopped == []

    built.stopping(lambda: stopped.append(True) or True)
    assert stopped == [True], "the earlier press never reached the command"


def test_a_remembered_press_is_spent_once(settings):
    stopped = []
    session = SimpleNamespace(interrupt_work=lambda: 1)
    built = shell.Shell(settings, session, handlers.build_registry())
    built._commands_running = 1
    built._stop_command()
    built.stopping(lambda: stopped.append(True) or True)
    built.stopping(None)
    built.stopping(lambda: stopped.append(True) or True)
    assert stopped == [True], "a spent press fired against the next command"


async def test_a_command_that_finishes_after_a_switch_does_not_revert_it(settings):
    """`/status --full` is safe in flight, so it can begin during a
    `/project switch` and suspend while the switch completes. Assigning its
    answer back put the shell on the problem the user had just left, holding a
    computer algebra kernel the opener had already closed."""
    import dataclasses as dc

    from hardy.tui import dispatch
    from hardy.tui.commands import Command
    from hardy.tui.ports import State

    built = shell.Shell(settings, SimpleNamespace(), handlers.build_registry())
    switched = State(config=dc.replace(settings, project="burnside"), session="new")

    async def slow(ui, argument, state):
        # The switch lands while this command is awaiting.
        built._state = switched
        return state                          # its own, now-stale answer

    outcome = dispatch.Outcome(
        "command", command=Command("slow", "", slow, safe_in_flight=True), argument=""
    )
    built._commands_running = 1
    await built._run_command(outcome)

    assert built.state.config.project == "burnside"
    assert built.state.session == "new"


async def test_a_command_that_finishes_normally_still_installs_its_state(settings):
    from hardy.tui import dispatch
    from hardy.tui.commands import Command

    built = shell.Shell(settings, SimpleNamespace(), handlers.build_registry())

    async def changing(ui, argument, state):
        import dataclasses as dc

        return dc.replace(state, session="replaced")

    outcome = dispatch.Outcome(
        "command", command=Command("changing", "", changing, safe_in_flight=True), argument=""
    )
    built._commands_running = 1
    await built._run_command(outcome)
    assert built.state.session == "replaced"


def test_a_second_escape_reaches_the_commands_own_escalation(settings):
    """The stopper answered true on every press, so this returned with the same
    first-press wording every time and the documented second Esc was swallowed.
    It is not the session's escalation to do either: that would take the
    interactive CAS kernel with it, whose state is not what the user is waiting
    on.

    Asserted on what the user is TOLD, not on how many times the stopper was
    called: the old code called it on every press too, so a count could not
    tell the two behaviours apart.
    """
    said: list[str] = []
    session = SimpleNamespace(
        interrupt_work=lambda: pytest.fail("the session answered for the command"),
        escalate=lambda: pytest.fail("the session's kernel was killed for a staged run"),
    )
    built = shell.Shell(settings, session, handlers.build_registry())
    built.write = lambda text, style="normal": said.append(text)
    built.stopping(lambda: True)

    built._stop_command()
    built._stop_command()

    assert "esc again" in said[0]
    assert "killed what had not stopped" in said[1], "the second press said the same thing"


async def test_a_goal_that_cannot_be_saved_is_a_line_not_a_traceback(ui, settings):
    """`plain.run` has no catch around a command, so an unwritable workspace
    ended the whole session. And the in-memory goal is restored, so what the
    user is told is true of the session as well as of the file."""
    from types import SimpleNamespace

    from hardy.tui import handlers
    from hardy.tui.ports import State

    def refuse(_text):
        raise OSError("read-only file system")

    session = SimpleNamespace(goal=lambda: "the old goal", set_goal=refuse)
    state = State(config=settings, session=session)

    assert await handlers.handle_goal(ui, "a new goal", state) is state
    assert "Could not save the goal" in ui.text
    assert "read-only file system" in ui.text
