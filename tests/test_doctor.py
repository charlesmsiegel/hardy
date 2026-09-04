from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hardy import doctor, process
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
        "root": tmp_path,
        "project": "workspace",
    }
    settings.update(overrides)
    return Config(**settings)


def named(checks: list[doctor.Check], name: str) -> doctor.Check:
    return next(check for check in checks if check.name == name)


@pytest.fixture(autouse=True)
def hermetic_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A signed-in Claude CLI that belongs to the test, not to the machine.

    Without this the suite passes on a developer's laptop and fails on a clean
    worker, which is the opposite of what a hermetic suite is for.
    """
    binaries = tmp_path / "subscription-bin"
    fake_tool(binaries, "claude", message='{"loggedIn": true, "authMethod": "oauth_token"}')
    monkeypatch.setenv("PATH", str(binaries), prepend=os.pathsep)


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
    # LeanTools invokes this as `lean_command --json <source>`, so the source
    # path arrives as the last argv entry, not the first.
    lean = (sys.executable, "-c", f"import pathlib, sys; pathlib.Path({str(recorder)!r}).write_text(pathlib.Path(sys.argv[-1]).read_text())")
    checks = doctor.run_checks(configuration(tmp_path, lean_command=lean), deep=True)
    assert named(checks, "mathlib").ok is True
    assert "import Mathlib" in recorder.read_text(encoding="utf-8")




def _answering(stdout: str):
    """Stand in for the guarded run the login check goes through.

    The seam is `run_guarded` rather than `subprocess.run`: every child Hardy
    starts goes through the group, the register, and the escalation ladder, and
    a probe patched at the old seam would quietly run the real `claude`.
    """
    def run(*_args, **_kwargs):
        return process.GuardedResult(returncode=0, stdout=stdout, stderr="")

    return run


def test_a_logged_out_cli_is_reported_as_a_failure(monkeypatch: pytest.MonkeyPatch):
    """`claude --version` would succeed while logged out, letting doctor call a
    machine ready when the first model call is going to fail authentication."""
    monkeypatch.setattr(doctor, "run_guarded", _answering('{"loggedIn": false}'))
    check = doctor._login_check("claude")
    assert check.ok is False and "claude login" in check.detail


def test_a_signed_in_cli_reports_how(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        doctor, "run_guarded", _answering('{"loggedIn": true, "authMethod": "oauth_token"}')
    )
    check = doctor._login_check("claude")
    assert check.ok and "oauth_token" in check.detail


def test_the_suite_does_not_depend_on_a_claude_cli_being_installed(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch):
    """A clean worker has no global `claude`, so the checks must fail rather than
    quietly pass on whatever the developer happens to have signed in."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    checks = doctor.run_checks(configuration(tmp_path))
    assert named(checks, "claude cli").ok is False
    assert "npm install" in named(checks, "claude cli").detail


def test_a_project_on_hardys_pins_is_reported_as_pinned(tmp_path: Path) -> None:
    from hardy import installers

    project = tmp_path / "lean"
    project.mkdir()
    (project / "lakefile.toml").write_text('name = "hardymath"\n', encoding="utf-8")
    (project / "lean-toolchain").write_text(installers.LEAN_TOOLCHAIN + "\n", encoding="utf-8")
    (project / "lake-manifest.json").write_text(
        '{"packages": [{"name": "mathlib", "rev": "0df444a3", "inputRev": "'
        + installers.MATHLIB_REVISION
        + '"}]}',
        encoding="utf-8",
    )

    check = named(doctor.run_checks(configuration(tmp_path)), "toolchain pin")

    assert check.ok
    assert not check.required
    assert installers.MATHLIB_REVISION in check.detail


def test_a_repinned_project_is_named_beside_what_hardy_pins(tmp_path: Path) -> None:
    """Advisory rather than fatal -- a deliberately repinned project is a
    supported way to run, and results record what actually ran -- but never
    silent: a project on another Lean must not pass as the pinned one."""
    project = tmp_path / "lean"
    project.mkdir()
    (project / "lakefile.toml").write_text('name = "hardymath"\n', encoding="utf-8")
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.20.0\n", encoding="utf-8")
    (project / "lake-manifest.json").write_text(
        '{"packages": [{"name": "mathlib", "rev": "abc", "inputRev": "master"}]}', encoding="utf-8"
    )

    check = named(doctor.run_checks(configuration(tmp_path)), "toolchain pin")

    assert not check.ok
    assert not check.required
    assert "v4.20.0" in check.detail and "master" in check.detail
    assert "results record what actually ran" in check.detail


def test_an_unreadable_pin_file_is_a_named_check_not_a_traceback(tmp_path: Path) -> None:
    """The check diagnoses a broken installation; it must not be what breaks on one."""
    if os.geteuid() == 0:
        pytest.skip("root reads unreadable files")
    project = tmp_path / "lean"
    project.mkdir()
    (project / "lakefile.toml").write_text('name = "hardymath"\n', encoding="utf-8")
    pin = project / "lean-toolchain"
    pin.write_text("leanprover/lean4:v4.33.1\n", encoding="utf-8")
    pin.chmod(0)
    try:
        check = named(doctor.run_checks(configuration(tmp_path)), "toolchain pin")
    finally:
        pin.chmod(0o644)

    assert not check.ok and not check.required
    assert "could not be read" in check.detail


def test_doctor_checks_the_api_backend_when_that_is_the_one_configured(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A correctly configured API-only machine must not be called broken.

    The quieter failure runs the other way and is worse: a machine with no key
    at all would be reported ready, because the checks it passed were for a
    backend it is not using.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    checks = doctor.run_checks(configuration(tmp_path, lean_project=project, backend="api"))

    names = [check.name for check in checks]
    assert "anthropic key" in names
    assert named(checks, "anthropic key").ok
    # And not the other backend's requirements, which this machine does not need.
    assert "claude cli" not in names
    assert "claude login" not in names
    assert named(checks, "backend").detail == "api"


def test_the_api_backend_without_a_key_is_a_named_failure(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    checks = doctor.run_checks(configuration(tmp_path, lean_project=project, backend="api"))

    key = named(checks, "anthropic key")
    assert not key.ok
    assert "ANTHROPIC_API_KEY" in key.detail


def test_the_key_itself_is_never_printed(tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A doctor report is pasted into issues. A key printed there is a key that
    # has been disclosed.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    checks = doctor.run_checks(configuration(tmp_path, lean_project=project, backend="api"))

    assert "sk-secret-value" not in "\n".join(doctor.describe(checks))


def test_the_subscription_backend_still_checks_the_cli(tmp_path: Path, project: Path) -> None:
    checks = doctor.run_checks(configuration(tmp_path, lean_project=project))

    names = [check.name for check in checks]
    assert "claude sdk" in names
    assert "anthropic key" not in names


def test_the_codex_backend_gets_its_own_checks(tmp_path: Path, project: Path) -> None:
    """A Codex-only machine was rejected for lacking Claude credentials it does
    not use -- and a Claude machine with no `openai-codex` passed and failed
    only when the runtime was built, which is the worse direction."""
    checks = doctor.run_checks(configuration(tmp_path, lean_project=project), backend="codex")

    names = [check.name for check in checks]
    assert "codex sdk" in names
    assert "claude cli" not in names and "anthropic key" not in names
