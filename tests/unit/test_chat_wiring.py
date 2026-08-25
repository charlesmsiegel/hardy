"""`cli._chat` wires the CAS runtime into the session and closes it exactly
once. Lost in the merge that brought the terminal rework's `run_session`
(a session *factory*, since the shell has to exist before the session can)
up to date with main's `_chat`, which built the runtime, passed it to
`MathematicsSession`, and closed it in a `finally` around the whole REPL.
Restored here, using fakes rather than a real kernel process: this is about
the wiring (built once, reaches the session, closed once), which
`tests/unit/test_cas_cli.py` and the real-subprocess-backed tests elsewhere
do not exercise at all, not the kernel mechanics those already cover.
"""

from __future__ import annotations

import contextlib
import io

from hardy import cli
from hardy import config as configuration


def settings(tmp_path):
    return configuration.Config(
        model="claude-opus-5",
        lean_command=("lake", "env", "lean"),
        lean_project=None,
        lean_timeout=180.0,
        latex_command=("pdflatex",),
        root=tmp_path,
        project="workspace",
        path=tmp_path / "config.toml",
    )


class FakeCasSession:
    def __init__(self):
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakeCasRuntime:
    def __init__(self):
        self.session = FakeCasSession()


class FakeMathematicsSession:
    """Captures every keyword argument `_chat`'s `build` closure passes,
    without touching a real runtime, Lean, or LaTeX."""

    instances: list[FakeMathematicsSession] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        FakeMathematicsSession.instances.append(self)

    def send(self, text: str) -> str:
        return "done"

    def switch_model(self, model) -> None: ...
    def record_abandonment(self, reason) -> None: ...


def test_chat_wires_cas_into_the_session_and_closes_it_once(tmp_path, monkeypatch):
    FakeMathematicsSession.instances = []
    build_calls: list[dict] = []
    fake_runtime = FakeCasRuntime()

    def fake_build_runtime(**kwargs):
        build_calls.append(kwargs)
        return fake_runtime, "fakecas 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", FakeMathematicsSession)
    # The plain path: no real terminal needed, and `--plain` is not the
    # question this test is about -- only whether `cas` reaches the session
    # and gets closed, which happens identically on both paths since `cas`
    # is built once in `_chat` itself, before `run_session` is ever called.
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    code = cli._chat(settings(tmp_path), plain=True)

    assert code == 0
    assert len(build_calls) == 1, "the runtime must be built once, not per session_factory call"
    assert len(FakeMathematicsSession.instances) == 1
    session = FakeMathematicsSession.instances[0]
    assert session.kwargs["cas"] is fake_runtime
    assert session.kwargs["cas_detail"] == "fakecas 1.0"
    assert fake_runtime.session.closed == 1


def test_chat_closes_cas_even_when_the_session_factory_raises(tmp_path, monkeypatch):
    """`finally` around `run_session(...)`, not code after it: an exception
    from anywhere inside must not leak the kernel process."""
    fake_runtime = FakeCasRuntime()

    def fake_build_runtime(**kwargs):
        return fake_runtime, "fakecas 1.0"

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", explode)
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    with contextlib.suppress(RuntimeError):
        cli._chat(settings(tmp_path), plain=True)
    assert fake_runtime.session.closed == 1


def test_chat_never_calls_close_when_no_backend_was_discovered(tmp_path, monkeypatch):
    def fake_build_runtime(**kwargs):
        return None, "sympy raised ImportError"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", FakeMathematicsSession)
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    FakeMathematicsSession.instances = []
    code = cli._chat(settings(tmp_path), plain=True)
    assert code == 0
    assert FakeMathematicsSession.instances[0].kwargs["cas"] is None
    assert FakeMathematicsSession.instances[0].kwargs["cas_detail"] == "sympy raised ImportError"
