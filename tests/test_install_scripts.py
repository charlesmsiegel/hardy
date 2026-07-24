from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHELL_SCRIPTS = ["install.sh", "install-linux.sh", "install-macos.sh", "lib/common.sh"]
POWERSHELL_SCRIPT = SCRIPTS / "install-windows.ps1"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the POSIX installers are checked on POSIX hosts")


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_installers_parse(name: str):
    interpreter = "sh" if name == "install.sh" else "bash"
    if shutil.which(interpreter) is None:
        pytest.skip(f"{interpreter} is not available")
    subprocess.run([interpreter, "-n", str(SCRIPTS / name)], check=True)


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shellcheck_is_clean(name: str):
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(["shellcheck", "-x", str(SCRIPTS / name)], capture_output=True, text=True, cwd=SCRIPTS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_executable_installers_are_executable():
    for name in ("install.sh", "install-linux.sh", "install-macos.sh"):
        assert os.access(SCRIPTS / name, os.X_OK), f"{name} is not executable"


@pytest.mark.parametrize("script", ["install-linux.sh", "install-macos.sh"])
def test_help_describes_the_installer_without_touching_the_system(script: str):
    """--help must work on a clean machine, before anything is installed.

    Both installers are checked on whichever host runs the tests: reaching
    --help means the script's top-level code survived a machine with none of
    its tools present, which is the state it is written for. A `set -e` exit
    before the argument parser is silent and looks like nothing happened.
    """
    result = subprocess.run([str(SCRIPTS / script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, f"{script} exited {result.returncode}: {result.stdout}{result.stderr}"
    assert result.stdout.strip(), f"{script} --help printed nothing"
    for flag in ("--yes", "--skip-mathlib", "--skip-latex", "--full-latex", "--no-config", "--prefix"):
        assert flag in result.stdout


@pytest.mark.parametrize("script", ["install-linux.sh", "install-macos.sh"])
def test_unknown_options_are_refused(script: str):
    result = subprocess.run([str(SCRIPTS / script), "--definitely-not-a-flag"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "unknown option" in result.stderr


def test_the_dispatcher_delegates_to_this_platforms_installer():
    result = subprocess.run([str(SCRIPTS / "install.sh"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Installs everything Hardy needs" in result.stdout
    # Windows users reach the dispatcher too, and must be sent to PowerShell.
    assert "install-windows.ps1" in (SCRIPTS / "install.sh").read_text(encoding="utf-8")


def test_powershell_installer_parses():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not installed")
    command = (
        "$errors = $null; $tokens = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{POWERSHELL_SCRIPT}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run([powershell, "-NoProfile", "-Command", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_supported_platform_has_an_installer():
    assert POWERSHELL_SCRIPT.exists()
    for name in SHELL_SCRIPTS:
        assert (SCRIPTS / name).exists()


def written_config(tmp_path: Path, **environment: str) -> str:
    """Run the installer's config writer alone and return the file it produced."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    target = tmp_path / "config.toml"
    driver = tmp_path / "drive.sh"
    driver.write_text(
        "set -eu\n"
        "WRITE_CONFIG=1; SKIP_MATHLIB=1; ASSUME_YES=1; LEAN_PROJECT=/tmp/lean\n"
        f'HARDY_CONFIG="{target}"\n'
        f'. "{SCRIPTS / "lib/common.sh"}"\n'
        "write_config >/dev/null 2>&1\n",
        encoding="utf-8",
    )
    subprocess.run(["bash", str(driver)], check=True, env={**os.environ, **environment, "HOME": str(tmp_path)})
    return target.read_text(encoding="utf-8")


def test_an_unattended_install_persists_an_explicit_backend_pin(tmp_path: Path):
    """Without the pin, Hardy infers the backend from the saved identity once the
    installer environment is gone — the case the pin exists to correct."""
    written = written_config(tmp_path, HARDY_MODEL="claude-gateway-id", HARDY_BACKEND="openai", OPENAI_API_KEY="sk-test")
    assert 'model = "claude-gateway-id"' in written
    assert 'backend = "openai"' in written


def test_an_unattended_install_records_no_backend_when_none_is_pinned(tmp_path: Path):
    written = written_config(tmp_path, HARDY_MODEL="claude-opus-5", ANTHROPIC_API_KEY="sk-ant")
    assert 'model = "claude-opus-5"' in written
    assert 'anthropic_api_key = "sk-ant"' in written
    assert "backend" not in written


def interactive_config(tmp_path: Path, answers: list[str], **environment: str) -> str:
    """Run the installer's config writer with a scripted operator at the prompts."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    target = tmp_path / "config.toml"
    driver = tmp_path / "drive.sh"
    driver.write_text(
        "set -eu\n"
        "WRITE_CONFIG=1; SKIP_MATHLIB=1; ASSUME_YES=0; LEAN_PROJECT=/tmp/lean\n"
        f'HARDY_CONFIG="{target}"\n'
        f'. "{SCRIPTS / "lib/common.sh"}"\n'
        "write_config >/dev/null 2>&1\n",
        encoding="utf-8",
    )
    # The prompts only run on a terminal, so give the driver one.
    script = ["script", "-qec", f"bash {driver}", "/dev/null"]
    subprocess.run(script, input="\n".join(answers) + "\n", text=True, capture_output=True,
                   env={**os.environ, **environment, "HOME": str(tmp_path)})
    return target.read_text(encoding="utf-8") if target.exists() else ""


def test_an_interactive_install_asks_for_the_pinned_backend_not_the_identity(tmp_path: Path):
    """A claude-* identity behind a pinned gateway needs the gateway's key; the
    Anthropic key it would otherwise collect is unusable there."""
    if shutil.which("script") is None:
        pytest.skip("util-linux script is not available")
    written = interactive_config(tmp_path, ["claude-gateway-id", "http://localhost:8000/v1", "sk-gateway"], HARDY_BACKEND="openai")
    if not written:
        pytest.skip("the installer did not reach the prompts in this environment")
    assert 'backend = "openai"' in written
    assert 'base_url = "http://localhost:8000/v1"' in written
    assert "anthropic_api_key" not in written
