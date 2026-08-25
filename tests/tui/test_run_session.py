"""`run_session` routes to the real `Shell` on a TTY, `PlainUi` otherwise, and
must never end a session just because the terminal could not be drawn.

`session_factory` -- not a session -- is the seam: the shell has to exist
before the session does, since the session needs the approval callback and
that callback runs through whichever `Ui` ends up live.
"""

from __future__ import annotations

import io
from io import StringIO

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from hardy import chat, runner
from hardy.layout import LayoutError
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


def test_a_schema_error_is_not_treated_as_a_rendering_problem(settings, monkeypatch):
    """The interactive fallback exists for rendering failures, not this.

    Reproduced: catching `SchemaError` under the same broad `except
    Exception` as a real rendering failure printed a misleading "Falling
    back to the plain session: ..." line and retried in `_run_plain`, where
    the identical refusal then escaped uncaught as a raw traceback right
    after it -- a user with an old workspace got a wrong diagnosis followed
    by a stack trace. It must propagate untouched instead, for `_chat` to
    report once, cleanly.
    """
    monkeypatch.setattr("hardy.tui._is_interactive", lambda: True)

    def explode(confirm):
        raise chat.SchemaError("session.json is schema version 1; this Hardy reads version 2 only")

    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output):
            with pytest.raises(chat.SchemaError):
                run_session(settings, explode)

    assert "Falling back" not in buffer.getvalue()


def test_a_write_guard_refusal_is_not_treated_as_a_rendering_problem_either(settings, monkeypatch, capsys):
    """A refusal is as deliberate as the schema one, and as unrecoverable.

    A `transcript.jsonl` that is a symlink out of the project is still one in
    the plain session, so "Falling back to the plain session" would be both a
    wrong diagnosis and a second attempt at the same refused write.
    """
    monkeypatch.setattr("hardy.tui._is_interactive", lambda: True)

    def explode(confirm):
        raise LayoutError("transcript.jsonl is a symlink to ../../victim.sh")

    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output), pytest.raises(LayoutError):
            run_session(settings, explode)

    # On stderr, where the fallback line is actually printed -- checking the
    # rendered screen would pass whether or not the fallback ran, and the
    # fallback is the whole of what this test is about.
    assert "Falling back" not in capsys.readouterr().err


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
