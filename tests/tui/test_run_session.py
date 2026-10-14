"""`run_session` routes to the real `Shell` on a TTY, `PlainUi` otherwise, and
must never end a session just because the terminal could not be drawn.

`session_factory` -- not a session -- is the seam: the shell has to exist
before the session does, since the session needs the approval callback and
that callback runs through whichever `Ui` ends up live.
"""

from __future__ import annotations

import asyncio
import io
from io import StringIO
from types import SimpleNamespace

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import StdoutProxy

from hardy.app.tui import run_session
from hardy.foundation.files import LayoutError
from hardy.workflows import batch as runner
from hardy.workflows.interactive import session as chat

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
    monkeypatch.setattr("hardy.app.tui._is_interactive", lambda: True)

    def explode(*args, **kwargs):
        raise RuntimeError("no console")

    monkeypatch.setattr("hardy.app.tui.shell.Shell", explode)
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
    monkeypatch.setattr("hardy.app.tui._is_interactive", lambda: True)

    def explode(confirm):
        raise chat.SchemaError("session.json is schema version 1; this Hardy reads version 2 only")

    buffer = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(buffer, lambda: Size(rows=24, columns=80))
        with create_app_session(input=pipe, output=output), pytest.raises(chat.SchemaError):
            run_session(settings, explode)

    assert "Falling back" not in buffer.getvalue()


def test_a_write_guard_refusal_is_not_treated_as_a_rendering_problem_either(settings, monkeypatch, capsys):
    """A refusal is as deliberate as the schema one, and as unrecoverable.

    A `transcript.jsonl` that is a symlink out of the project is still one in
    the plain session, so "Falling back to the plain session" would be both a
    wrong diagnosis and a second attempt at the same refused write.
    """
    monkeypatch.setattr("hardy.app.tui._is_interactive", lambda: True)

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


@pytest.mark.parametrize("delayed_stdout", [False, True])
def test_the_banner_appears_on_the_interactive_shell_path(settings, monkeypatch, delayed_stdout):
    """The banner carries the unsandboxed-execution warning (a standing
    disclosure, `AGENTS.md`) -- it must not get lost when the interactive
    path is wired to the real `Shell` instead of the old REPL loop.
    """
    monkeypatch.setattr("hardy.app.tui._is_interactive", lambda: True)
    if delayed_stdout:
        # Deterministically run the proxy worker only during close(), after
        # /exit has finished the app. Its loop callbacks then arrive too late
        # to deliver prints made through patch_stdout before the first prompt.
        monkeypatch.setattr(StdoutProxy, "_start_write_thread", lambda proxy: SimpleNamespace(join=proxy._write_thread))
        monkeypatch.setattr(StdoutProxy, "_get_app_loop", lambda proxy: asyncio.get_running_loop())
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


def test_render_failure_closes_live_session_before_plain_factory(settings, monkeypatch):
    from types import SimpleNamespace

    import hardy.app.tui as tui

    class Session(FakeSession):
        closed = False
        def close(self):
            self.closed = True
    from hardy.workflows.delegation.store import OwnerToken
    token = OwnerToken.hold(settings.layout.problem)
    first = Session()
    replacement = Session()
    replacement.close = lambda: (setattr(replacement, "closed", True), token.release())
    assert OwnerToken.alive(settings.layout.problem, token.id)
    class BrokenShell:
        def __init__(self, config, *args, **kwargs):
            self.state = SimpleNamespace(config=config, session=None)
        def attach(self, session):
            self.state.session = replacement
        def run(self):
            raise RuntimeError('render failed')
    monkeypatch.setattr(tui, '_is_interactive', lambda: True)
    monkeypatch.setattr('hardy.app.tui.shell.Shell', BrokenShell)
    def fallback(config, factory, **kwargs):
        assert replacement.closed
        assert not OwnerToken.alive(settings.layout.problem, token.id)
        assert not first.closed
        return 0
    monkeypatch.setattr(tui, '_run_plain', fallback)
    assert tui.run_session(settings, lambda confirm: first) == 0


@pytest.mark.parametrize('changed_problem', [False, True])
def test_fallback_reopens_live_model_chat_and_kernel(settings, monkeypatch, changed_problem):
    import dataclasses
    from types import SimpleNamespace

    import hardy.app.tui as tui

    live = dataclasses.replace(settings, model='changed-model', chat='aside', project='other' if changed_problem else settings.project)
    returned = dataclasses.replace(live, context_window=12345)
    stale_kernel = SimpleNamespace(closed=True)
    fresh_kernel = SimpleNamespace(closed=False)
    class Session:
        def __init__(self, kernel):
            self.cas = kernel
        def close(self):
            pass
    old = Session(fresh_kernel)
    class BrokenShell:
        def __init__(self, *args, **kwargs):
            self.state = SimpleNamespace(config=live, session=old)
        def attach(self, session):
            pass
        def run(self):
            raise RuntimeError('render failure')
    built = []
    def launch_factory(confirm):
        built.append(True)
        return Session(stale_kernel)
    reopened = []
    def reopen(slug, confirm, current, *, chat):
        assert current is live and chat == 'aside'
        reopened.append(slug)
        return returned, Session(fresh_kernel)
    def plain(config, session, **kwargs):
        assert config is returned
        assert not session.cas.closed
        return 0
    monkeypatch.setattr(tui, '_is_interactive', lambda: True)
    monkeypatch.setattr('hardy.app.tui.shell.Shell', BrokenShell)
    monkeypatch.setattr('hardy.app.tui.plain.run', plain)
    assert tui.run_session(settings, launch_factory, reopen=reopen) == 0
    assert built == [True] and reopened == [live.project]
