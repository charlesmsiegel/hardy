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

import pytest

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
        # The real session derives this from `fresh_thread`; the closure under
        # test reads and rewrites it, so the fake has to carry it too.
        self.fresh_thread_detail = "started fresh (test detail)" if kwargs.get("fresh_thread") else ""
        FakeMathematicsSession.instances.append(self)

    def send(self, text: str) -> str:
        return "done"

    def switch_model(self, model) -> None: ...
    def record_abandonment(self, reason) -> None: ...


def test_opening_a_project_creates_its_layout(tmp_path):
    """Otherwise every ignore rule this plan writes is inert.

    `grep -rn "ensure()" src/` returned nothing before this test: the
    directories and the anchored ignore rules existed only in unit tests, so a
    real run left `.build/` and `.local/` as ordinary trackable files.

    Also pins `prepare_layout`'s second statement, `unignore_tooling`, not
    only its first: deleting the `unignore_tooling(...)` call left every
    other test in this file (including the wiring spy below, which only
    watches whether `prepare_layout` runs, not what it does) green -- 409
    passed, 4 skipped. A pre-seeded legacy root `.gitignore` and an
    assertion on its *effect* catches that a bare call-count spy cannot.
    """
    (tmp_path / ".gitignore").write_text("*.log\n.hardy/\n", encoding="utf-8")
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")
    cli.prepare_layout(settings)

    problem = tmp_path / "sylow"
    assert (problem / "lean").is_dir()
    assert "/.local/" in (problem / ".gitignore").read_text(encoding="utf-8")

    root_ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".hardy/" not in root_ignore, "unignore_tooling must have run"
    assert "*.log" in root_ignore, "the user's other rules are untouched"


def test_chat_calls_prepare_layout_before_building_the_cas_runtime(tmp_path, monkeypatch):
    """Pins the wiring itself, not just what `prepare_layout` does on its own.

    `test_opening_a_project_creates_its_layout` above calls `prepare_layout`
    directly and cannot fail if `_chat` stops calling it. Deleting the
    `prepare_layout(config)` line from `_chat` left every other test in this
    file green, because the fakes here never touch the filesystem to notice
    the directories are gone -- so this spies on the call itself, and on its
    order: `cas_tools.build_runtime` writes its log under `<slug>/cas/` and
    needs that directory to already exist.
    """
    (tmp_path / ".gitignore").write_text("*.log\n.hardy/\n", encoding="utf-8")

    order: list[str] = []
    real_prepare_layout = cli.prepare_layout

    def spy_prepare_layout(config):
        order.append("prepare_layout")
        return real_prepare_layout(config)

    def fake_build_runtime(**kwargs):
        order.append("build_runtime")
        return FakeCasRuntime(), "fakecas 1.0"

    monkeypatch.setattr(cli, "prepare_layout", spy_prepare_layout)
    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", FakeMathematicsSession)
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    FakeMathematicsSession.instances = []
    code = cli._chat(settings(tmp_path), plain=True)

    assert code == 0
    assert order == ["prepare_layout", "build_runtime"]
    # `prepare_layout`'s call count alone does not pin its second statement:
    # a spy on the function as a whole still fires once even if
    # `unignore_tooling(...)` is deleted from inside it. Assert the effect.
    root_ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".hardy/" not in root_ignore, "unignore_tooling must have run"


def test_chat_hands_the_fresh_thread_flag_to_the_session(tmp_path, monkeypatch):
    """`--fresh-thread` is a per-run act with no config key, so the flag's only
    road to `MathematicsSession` is `_chat` reading it off the parsed args --
    the parser test elsewhere cannot notice this wire being cut."""
    import io
    from types import SimpleNamespace

    monkeypatch.setattr(cli.cas_tools, "build_runtime", lambda **kwargs: (None, ""))
    monkeypatch.setattr(cli, "MathematicsSession", FakeMathematicsSession)
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))

    FakeMathematicsSession.instances = []
    code = cli._chat(settings(tmp_path), plain=True, args=SimpleNamespace(fresh_thread=True))

    assert code == 0
    assert FakeMathematicsSession.instances[0].kwargs["fresh_thread"] is True

    # And absent args -- a direct caller, or a path with no flag -- means no
    # discard: the default must be the resuming behaviour every session has.
    monkeypatch.setattr("sys.stdin", io.StringIO("/exit\n"))
    FakeMathematicsSession.instances = []
    cli._chat(settings(tmp_path), plain=True)
    assert FakeMathematicsSession.instances[0].kwargs["fresh_thread"] is False


def test_the_fallback_rebuild_does_not_discard_the_fresh_conversation_again(tmp_path, monkeypatch):
    """`run_session` calls the factory a second time when the interactive
    shell falls back to plain -- possibly after turns have already been taken
    on the NEW conversation. The flag is one act on the launch: a second
    build still carrying it would discard the very conversation the first
    build created and append a second `fresh` event. The fallback session
    still shows the launch's fresh-start detail, because the condition it is
    running under is the one the first build established."""
    from types import SimpleNamespace

    import hardy.tui

    def fallback_run_session(config, factory, *, plain=False, reopen=None):
        factory(lambda proposal: False)
        factory(lambda proposal: False)
        return 0

    monkeypatch.setattr(cli.cas_tools, "build_runtime", lambda **kwargs: (None, ""))
    monkeypatch.setattr(cli, "MathematicsSession", FakeMathematicsSession)
    monkeypatch.setattr(hardy.tui, "run_session", fallback_run_session)

    FakeMathematicsSession.instances = []
    code = cli._chat(settings(tmp_path), plain=True, args=SimpleNamespace(fresh_thread=True))

    assert code == 0
    first, second = FakeMathematicsSession.instances
    assert first.kwargs["fresh_thread"] is True
    assert second.kwargs["fresh_thread"] is False
    assert second.fresh_thread_detail == first.fresh_thread_detail


def test_a_build_that_raised_leaves_the_fresh_ask_pending(tmp_path, monkeypatch):
    """Consumed by the first session actually built, not by the first attempt:
    an ask no session served must still reach the fallback session it was
    for."""
    from types import SimpleNamespace

    import hardy.tui

    class ExplodingOnce(FakeMathematicsSession):
        exploded = False

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if not ExplodingOnce.exploded:
                ExplodingOnce.exploded = True
                raise RuntimeError("the shell's first build fails")

    def fallback_run_session(config, factory, *, plain=False, reopen=None):
        with contextlib.suppress(RuntimeError):
            factory(lambda proposal: False)
        factory(lambda proposal: False)
        return 0

    monkeypatch.setattr(cli.cas_tools, "build_runtime", lambda **kwargs: (None, ""))
    monkeypatch.setattr(cli, "MathematicsSession", ExplodingOnce)
    monkeypatch.setattr(hardy.tui, "run_session", fallback_run_session)

    FakeMathematicsSession.instances = []
    code = cli._chat(settings(tmp_path), plain=True, args=SimpleNamespace(fresh_thread=True))

    assert code == 0
    first, second = FakeMathematicsSession.instances
    assert first.kwargs["fresh_thread"] is True
    assert second.kwargs["fresh_thread"] is True


def test_chat_wraps_a_schema_error_through_the_given_parser(tmp_path, monkeypatch, capsys):
    """The schema-1 refusal is deliberate; how it reaches the user is not.

    Before this, `run_session`'s interactive path caught it as an ordinary
    exception, printed a misleading "Falling back to the plain session:
    ..." line, retried in `_run_plain`, and let the identical refusal
    escape uncaught as a raw traceback. `_chat` must render it through
    `parser.error` -- one clean line -- the same way `LayoutError` is.
    """

    def explode(*args, **kwargs):
        raise cli.SchemaError("session.json is schema version 1; this Hardy reads version 2 only")

    def fake_build_runtime(**kwargs):
        return FakeCasRuntime(), "fakecas 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", explode)
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as excinfo:
        cli._chat(settings(tmp_path), plain=True, parser=parser)

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "schema version 1" in err
    assert "Falling back" not in err


def test_chat_without_a_parser_lets_a_schema_error_propagate(tmp_path, monkeypatch):
    """A direct caller with no parser to hand gets the real exception."""

    def explode(*args, **kwargs):
        raise cli.SchemaError("boom")

    def fake_build_runtime(**kwargs):
        return FakeCasRuntime(), "fakecas 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", explode)

    with pytest.raises(cli.SchemaError):
        cli._chat(settings(tmp_path), plain=True)


def test_chat_wraps_a_layout_error_through_the_given_parser(tmp_path, monkeypatch, capsys):
    """Every other `LayoutError` a run can hit goes through `parser.error`,
    which prints a clean message and exits 2 instead of a raw traceback --
    this one, raised later once a session is actually opening, must too.
    """

    def explode(self):
        raise cli.layout.LayoutError("boom: outside the root")

    monkeypatch.setattr(cli.layout.Layout, "ensure", explode)
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as excinfo:
        cli._chat(settings(tmp_path), plain=True, parser=parser)

    assert excinfo.value.code == 2
    assert "boom: outside the root" in capsys.readouterr().err


def test_chat_wraps_a_write_guard_refusal_through_the_given_parser(tmp_path, monkeypatch, capsys):
    """A guard refusal arrives later than `ensure`'s, and must read the same.

    `prepare_layout` runs before the session exists; a `WriteGuard` refuses
    while one is opening -- a `transcript.jsonl` the clone shipped as a
    symlink -- or in the middle of a turn. Without this it left `run_session`
    as a raw traceback, or as a "Falling back to the plain session" line
    followed by one.
    """

    def explode(*args, **kwargs):
        raise cli.layout.LayoutError(
            "/root/sylow/transcript.jsonl is a symlink to /elsewhere/victim.sh; "
            "refusing to read or write through it"
        )

    def fake_build_runtime(**kwargs):
        return FakeCasRuntime(), "fakecas 1.0"

    monkeypatch.setattr(cli.cas_tools, "build_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "MathematicsSession", explode)
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as excinfo:
        cli._chat(settings(tmp_path), plain=True, parser=parser)

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "transcript.jsonl is a symlink" in err
    assert "Falling back" not in err


def test_chat_without_a_parser_lets_a_layout_error_propagate(tmp_path, monkeypatch):
    """A direct caller with no parser to hand -- a test, an embedding -- gets
    the real exception rather than a silently swallowed one."""

    def explode(self):
        raise cli.layout.LayoutError("boom")

    monkeypatch.setattr(cli.layout.Layout, "ensure", explode)

    with pytest.raises(cli.layout.LayoutError):
        cli._chat(settings(tmp_path), plain=True)


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
