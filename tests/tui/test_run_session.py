"""`run_session` routes to the real `Shell` on a TTY, `PlainUi` otherwise, and
must never end a session just because the terminal could not be drawn.

`session_factory` -- not a session -- is the seam: the shell has to exist
before the session does, since the session needs the approval callback and
that callback runs through whichever `Ui` ends up live.
"""

from __future__ import annotations

import io
from io import StringIO

from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy import runner
from hardy.tui import run_session

from .conftest import Streams


class FakeSession(Streams):
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


def test_the_session_factory_is_called_exactly_once_on_the_plain_path(settings, monkeypatch):
    """Not two sessions for one run: the factory is the seam that lets the
    approval callback exist before the session does, not a hook to be
    invoked speculatively."""
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    calls: list[object] = []

    def factory(confirm):
        calls.append(confirm)
        return FakeSession()

    assert run_session(settings, factory, plain=True) == 0
    assert len(calls) == 1


def test_the_banner_appears_on_the_interactive_shell_path(settings, monkeypatch):
    """The banner carries the unsandboxed-execution warning (a standing
    disclosure, `AGENTS.md`) -- it must not get lost when the interactive
    path is wired to the real `Shell` instead of the old REPL loop.
    """
    monkeypatch.setattr("hardy.tui._is_interactive", lambda: True)
    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            # Queued before the loop exists at all: `Shell.run()` drives a
            # fresh `asyncio.run()`, whose app attaches its input reader
            # essentially immediately, so this is read back as the first
            # input event rather than racing startup.
            pipe.send_text("/exit\r")
            code = run_session(settings, lambda confirm: FakeSession())
    assert code == 0
    assert runner.WARNING in buffer.getvalue()


def test_the_banner_appears_on_the_plain_path(settings, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    assert run_session(settings, lambda confirm: FakeSession(), plain=True) == 0
    assert runner.WARNING in capsys.readouterr().out
