"""`hardy web` opens a registered project or nothing; the current directory is never a root."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_problem

from hardy.app import cli, project_registry
from hardy.app.project_registry import ProjectRegistry
from hardy.foundation import paths


class FakeCas:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.session = self

    def close(self) -> None:
        pass


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A user directory of this test's own, and no toolchain behind the launch."""
    home = tmp_path / "home"
    (home / ".hardy").mkdir(parents=True)
    monkeypatch.setattr(paths, "global_dir", lambda: home / ".hardy")
    monkeypatch.setenv("HARDY_CONFIG", str(home / ".hardy" / "config.toml"))
    monkeypatch.delenv("HARDY_ROOT", raising=False)
    monkeypatch.delenv("HARDY_PROJECT", raising=False)
    monkeypatch.setattr(cli.search_tools, "build_runtime", lambda config: (None, "no search in this test"))
    monkeypatch.setattr(cli.cas_tools, "build_runtime", lambda **kwargs: (FakeCas(kwargs["cwd"]), "fake"))
    monkeypatch.setattr(cli, "MathematicsSession", lambda problem, *a, **k: FakeSession(problem, k.get("chat", "main")))
    monkeypatch.setattr(cli, "runtime_factory", lambda *a, **k: object())
    return home


@pytest.fixture
def served(monkeypatch):
    """`serve` replaced by a capture: the host is inspected, never bound to a port."""
    seen: dict = {}

    def fake_serve(host, *, port, open_browser, **_):
        seen["host"] = host
        seen["state"] = host.state()
        seen["port"] = port

    monkeypatch.setattr("hardy.app.web.server.serve", fake_serve)
    return seen


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["hardy", *argv])
    return cli.main()


def test_web_ignores_the_current_directory_and_scaffolds_nothing(home, served, tmp_path, monkeypatch) -> None:
    cwd = tmp_path / "checkout"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert _run(monkeypatch, "web", "--port", "0") == 0
    assert served["state"]["open"] is False and served["state"]["slug"] is None
    assert sorted(child.name for child in cwd.iterdir()) == []
    # The registry every default `ProjectRegistry()` in this process reaches
    # (the suite's autouse fixture points it at tmp_path), not a path the
    # test guessed: an assertion on the wrong file passes for the wrong reason.
    assert not project_registry.default_registry_path().exists()
    assert not project_registry.default_projects_root().exists()


def test_web_opens_the_last_opened_project_by_default(home, served, tmp_path, monkeypatch) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    ProjectRegistry().touch(problem)
    monkeypatch.chdir(tmp_path)
    assert _run(monkeypatch, "web", "--port", "0") == 0
    state = served["state"]
    assert state["open"] is True and state["slug"] == "sylow"
    assert state["path"] == str(problem.resolve()) and state["root"] == str(problem.resolve().parent)
    assert not (tmp_path / "main").exists()


def test_web_project_flag_names_a_registered_project_or_exits_2(home, served, tmp_path, monkeypatch, capsys) -> None:
    a = make_problem(tmp_path / "one", "sylow")
    b = make_problem(tmp_path / "two", "burnside")
    registry = ProjectRegistry()
    registry.add(a)
    registry.add(b)
    assert _run(monkeypatch, "web", "--project", "burnside", "--port", "0") == 0
    assert served["state"]["slug"] == "burnside"
    assert registry.last_opened().path == b.resolve()
    with pytest.raises(SystemExit) as refused:
        _run(monkeypatch, "web", "--project", "nope", "--port", "0")
    assert refused.value.code == 2
    err = capsys.readouterr().err
    assert "no registered project named 'nope'" in err and "burnside" in err and "sylow" in err


def test_web_has_no_root_flag_and_chat_needs_a_project(home, served, tmp_path, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as refused:
        _run(monkeypatch, "web", "--root", str(tmp_path))
    assert refused.value.code == 2
    with pytest.raises(SystemExit) as refused:
        _run(monkeypatch, "web", "--chat", "lean-proof", "--port", "0")
    assert refused.value.code == 2 and "--chat" in capsys.readouterr().err
