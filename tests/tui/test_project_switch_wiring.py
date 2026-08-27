"""The reopener's route from `cli` to a handler, and what a switch retargets.

`handle_project` is tested against a stand-in reopener; this is about the real
one existing, reaching the handler by both shell paths, and the one piece of
terminal state that is per-problem and outlives a swapped `State`.
"""

from __future__ import annotations

import dataclasses
import io
from io import StringIO

from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy import config as configuration
from hardy import layout
from hardy.tui import dispatch, run_session
from hardy.tui.handlers import build_registry
from hardy.tui.plain import run as run_plain
from hardy.tui.ports import State
from hardy.tui.shell import Shell

from .conftest import Streams


class FakeSession(Streams):
    def send(self, text: str) -> str:
        return "answered"

    def switch_model(self, model): ...
    def record_abandonment(self, reason): ...


def _scripted(lines: list[str]):
    remaining = iter(lines)

    def read(prompt: str) -> str:
        return next(remaining)

    return read


def test_plain_mode_hands_the_reopener_to_its_handlers(settings):
    """`--plain` is a session too, and `/project switch` has to work in it."""
    (settings.root / "burnside").mkdir(parents=True, exist_ok=True)
    (settings.root / "burnside" / layout.RECORD).write_text("{}", encoding="utf-8")
    opened: list[str] = []
    printed: list[str] = []
    reopened = FakeSession()

    def reopen(slug, confirm, current):
        opened.append(slug)
        return settings, reopened

    code = run_plain(
        settings,
        FakeSession(),
        out=printed.append,
        read=_scripted(["/project switch burnside", "/exit"]),
        reopen=reopen,
    )
    assert code == 0
    assert opened == ["burnside"]


def test_run_session_threads_the_reopener_into_the_plain_state(settings, monkeypatch, capsys):
    (settings.root / "burnside").mkdir()
    (settings.root / "burnside" / layout.RECORD).write_text("{}", encoding="utf-8")
    opened: list[str] = []

    def reopen(slug, confirm, current):
        opened.append(slug)
        return settings, FakeSession()

    monkeypatch.setattr("sys.stdin", io.StringIO("/project switch burnside\n/exit\n"))
    assert run_session(settings, lambda confirm: FakeSession(), reopen=reopen) == 0
    assert opened == ["burnside"]


def test_the_shell_carries_the_reopener_in_its_state(settings):
    def reopen(slug, confirm, current): ...

    shell = Shell(settings, None, build_registry(), reopen=reopen)
    assert shell.state.reopen is reopen


def test_a_shell_built_without_one_still_works(settings):
    assert Shell(settings, None, build_registry()).state.reopen is None


def test_input_history_follows_the_project_a_switch_opened(settings, tmp_path):
    """Every line typed at the prompt is machine-local state of one problem.

    Left pointing at the launch project, a switch would go on writing what is
    typed about the new problem into the old problem's `.local/`, which is the
    cross-contamination this whole layout exists to end.
    """
    shell = Shell(settings, None, build_registry())
    started = tmp_path / "started"
    started.mkdir(parents=True, exist_ok=True)
    other = configuration.Config(
        model=settings.model,
        lean_command=settings.lean_command,
        lean_project=None,
        lean_timeout=settings.lean_timeout,
        latex_command=settings.latex_command,
        root=settings.root,
        project="burnside",
        path=settings.path,
    )
    shell.retarget(other)
    history = shell.history_path
    assert history is not None
    assert history.parent == other.layout.local


async def test_a_switch_dispatched_by_the_shell_moves_the_session_and_its_history(settings):
    """Through the shell's own command path, not a bare handler call.

    The retarget hangs off `_run_command`, so nothing below the shell can
    prove it happens for a line someone actually submitted.
    """
    (settings.root / "burnside").mkdir(parents=True, exist_ok=True)
    (settings.root / "burnside" / layout.RECORD).write_text("{}", encoding="utf-8")
    opened: list[str] = []

    def reopen(slug, confirm, current):
        opened.append(slug)
        return dataclasses.replace(settings, project=slug), FakeSession()

    registry = build_registry()
    holder = {"rows": 24, "cols": 80}
    with create_pipe_input() as pipe:
        output = Vt100_Output(
            StringIO(), lambda: Size(rows=holder["rows"], columns=holder["cols"]), term="xterm"
        )
        with create_app_session(input=pipe, output=output):
            terminal = Shell(
                settings, FakeSession(), registry, input=pipe, output=output, reopen=reopen
            )
            outcome = dispatch.classify("/project switch burnside", registry, turn_running=False)
            # Raised by `_submit_key` before the task exists; `_run_command`
            # lowers it in its `finally`.
            terminal._commands_running = 1
            await terminal._run_command(outcome)

    assert opened == ["burnside"]
    assert terminal.state.config.project == "burnside"
    assert terminal.history_path == settings.root / "burnside" / ".local" / "input-history"


async def test_a_command_that_moves_no_files_leaves_the_history_alone(settings):
    """`/model` returns a replaced config too, and must not retarget anything.

    Retargeting resets the buffer, which drops the history already loaded --
    a real cost to pay for a change that moved no files at all.
    """
    registry = build_registry()

    async def rename_the_model(ui, argument, state):
        return dataclasses.replace(state, config=dataclasses.replace(state.config, model="other"))

    registry = [
        dataclasses.replace(command, handler=rename_the_model)
        if command.name == "model"
        else command
        for command in registry
    ]
    holder = {"rows": 24, "cols": 80}
    with create_pipe_input() as pipe:
        output = Vt100_Output(
            StringIO(), lambda: Size(rows=holder["rows"], columns=holder["cols"]), term="xterm"
        )
        with create_app_session(input=pipe, output=output):
            terminal = Shell(settings, FakeSession(), registry, input=pipe, output=output)
            history = terminal._box.buffer.history
            terminal._commands_running = 1
            await terminal._run_command(
                dispatch.classify("/model other", registry, turn_running=False)
            )

    assert terminal.state.config.model == "other"
    assert terminal._box.buffer.history is history
    assert terminal.history_path == settings.layout.local / "input-history"


class Recording(Streams):
    """A session that remembers the questions it was actually asked."""

    def __init__(self, name: str):
        self.name = name
        self.asked: list[str] = []

    def send(self, text: str) -> str:
        self.asked.append(text)
        return f"{self.name} answered"

    def switch_model(self, model): ...
    def record_abandonment(self, reason): ...


def test_a_turn_after_a_switch_goes_to_the_problem_now_open(settings, tmp_path):
    """`--plain` drove the session it was handed, not the one in its state.

    After a switch that session belongs to the problem the user has left: its
    provider thread, its transcript, and a computer algebra kernel the switch
    has already closed. Every question after a switch went to it, and the
    problem actually open received nothing.
    """
    (settings.root / "burnside").mkdir(parents=True, exist_ok=True)
    (settings.root / "burnside" / layout.RECORD).write_text("{}", encoding="utf-8")
    left, opened = Recording("sylow"), Recording("burnside")

    def reopen(slug, confirm, current):
        return dataclasses.replace(current, project=slug), opened

    run_plain(
        settings,
        left,
        out=lambda text: None,
        read=_scripted(["/project switch burnside", "a question about burnside", "/exit"]),
        reopen=reopen,
    )

    assert opened.asked == ["a question about burnside"]
    assert left.asked == []


def test_the_plain_fallback_keeps_the_problem_the_shell_had_switched_to(settings, monkeypatch):
    """A rendering failure must not silently return to the abandoned problem.

    `session_factory` reopens what `run_session` was called with and closes
    over the kernel built for it -- the one the switch shut. Falling back on it
    lands the user in the problem they left, with a dead kernel.
    """
    switched = dataclasses.replace(settings, project="burnside")
    reopened: list[str] = []

    def reopen(slug, confirm, current):
        reopened.append(slug)
        return switched, Recording(slug)

    class Switched:
        """A shell that has already switched, and then fails to draw."""

        def __init__(self, config, session, registry, *, reopen=None, **kwargs):
            self.state = State(config=switched, session=None, reopen=reopen)

        def attach(self, session): ...
        def run(self):
            raise RuntimeError("the terminal could not be drawn")

    import hardy.tui.shell as shell_module

    monkeypatch.setattr(shell_module, "Shell", Switched)
    plain_calls: list = []
    monkeypatch.setattr(
        "hardy.tui.plain.run",
        lambda config, session, **kwargs: plain_calls.append((config, session)) or 0,
    )
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("sys.stdout", _Tty())

    assert run_session(settings, lambda confirm: Recording("sylow"), reopen=reopen) == 0
    assert reopened == ["burnside"]
    assert plain_calls[0][0].project == "burnside"
    assert plain_calls[0][1].name == "burnside"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True
