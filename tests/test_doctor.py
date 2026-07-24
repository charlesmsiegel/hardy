from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hardy import doctor
from hardy.config import Config

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake tools are POSIX shell scripts")


def fake_tool(directory: Path, name: str, *, exit_code: int = 0, message: str = "fake 1.0") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\necho '{message}'\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def configuration(tmp_path: Path, **overrides) -> Config:
    settings = {
        "model": "claude-opus-5",
        "lean_command": ("lake", "env", "lean"),
        "lean_project": tmp_path / "lean",
        "lean_timeout": 180.0,
        "latex_command": ("pdflatex",),
        "workspace": tmp_path / "workspace",
    }
    settings.update(overrides)
    return Config(**settings)


def named(checks: list[doctor.Check], name: str) -> doctor.Check:
    return next(check for check in checks if check.name == name)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "lean"
    path.mkdir()
    (path / "lakefile.toml").write_text("name = \"hardymath\"\n", encoding="utf-8")
    return path


def test_a_complete_installation_passes_every_required_check(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch):
    binaries = tmp_path / "bin"
    fake_tool(binaries, "lake", message="Lake version 5.0.0")
    fake_tool(binaries, "pdflatex", message="pdfTeX 3.141592653")
    monkeypatch.setenv("PATH", str(binaries), prepend=os.pathsep)
    checks = doctor.run_checks(configuration(tmp_path))
    assert [check.name for check in checks if check.required and not check.ok] == []
    assert named(checks, "lean").detail == "Lake version 5.0.0"
    assert doctor.report(checks) == 0


def test_missing_lean_and_latex_are_reported_as_failures(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    checks = doctor.run_checks(configuration(tmp_path))
    assert "not found on PATH" in named(checks, "lean").detail
    assert "not found on PATH" in named(checks, "latex").detail
    assert doctor.report(checks) == 1


def test_a_missing_lean_project_fails_before_lean_is_run(tmp_path: Path):
    checks = doctor.run_checks(configuration(tmp_path, lean_project=tmp_path / "absent"))
    assert named(checks, "lean project").ok is False
    assert "missing directory" in named(checks, "lean project").detail


def test_a_directory_without_a_lakefile_is_not_a_lean_project(tmp_path: Path):
    (tmp_path / "plain").mkdir()
    checks = doctor.run_checks(configuration(tmp_path, lean_project=tmp_path / "plain"))
    assert named(checks, "lean project").ok is False
    assert "lakefile" in named(checks, "lean project").detail


def test_an_unconfigured_lean_project_warns_without_failing(tmp_path: Path):
    check = named(doctor.run_checks(configuration(tmp_path, lean_project=None)), "lean project")
    assert check.ok and not check.required




def test_the_deep_check_compiles_a_mathlib_probe(tmp_path: Path, project: Path):
    recorder = project / "seen.lean"
    lean = (sys.executable, "-c", f"import pathlib, sys; pathlib.Path({str(recorder)!r}).write_text(pathlib.Path(sys.argv[1]).read_text())")
    checks = doctor.run_checks(configuration(tmp_path, lean_command=lean), deep=True)
    assert named(checks, "mathlib").ok is True
    assert "import Mathlib" in recorder.read_text(encoding="utf-8")




def test_a_logged_out_cli_is_reported_as_a_failure(monkeypatch: pytest.MonkeyPatch):
    """`claude --version` would succeed while logged out, letting doctor call a
    machine ready when the first model call is going to fail authentication."""
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"loggedIn": false}', ""))
    check = doctor._login_check("claude")
    assert check.ok is False and "claude login" in check.detail


def test_a_signed_in_cli_reports_how(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '{"loggedIn": true, "authMethod": "oauth_token"}', ""))
    check = doctor._login_check("claude")
    assert check.ok and "oauth_token" in check.detail
