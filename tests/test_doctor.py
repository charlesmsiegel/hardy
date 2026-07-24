from __future__ import annotations

import os
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
        "model": "provider/model-1",
        "base_url": "https://example.invalid/v1",
        "api_key": "secret",
        "api_key_env": "OPENAI_API_KEY",
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


def test_a_missing_model_and_key_fail_without_disclosing_the_key(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    checks = doctor.run_checks(configuration(tmp_path, model=None, api_key=""))
    assert named(checks, "model").ok is False
    assert named(checks, "api key").ok is False
    assert "secret" not in named(checks, "api key").detail


def test_the_api_key_check_never_prints_the_key(tmp_path: Path, project: Path):
    check = named(doctor.run_checks(configuration(tmp_path)), "api key")
    assert check.ok and check.detail == "present via config file" and "secret" not in check.detail


def test_the_deep_check_compiles_a_mathlib_probe(tmp_path: Path, project: Path):
    recorder = project / "seen.lean"
    lean = (sys.executable, "-c", f"import pathlib, sys; pathlib.Path({str(recorder)!r}).write_text(pathlib.Path(sys.argv[1]).read_text())")
    checks = doctor.run_checks(configuration(tmp_path, lean_command=lean), deep=True)
    assert named(checks, "mathlib").ok is True
    assert "import Mathlib" in recorder.read_text(encoding="utf-8")
