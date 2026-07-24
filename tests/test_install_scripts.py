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


def test_help_describes_the_installer_without_touching_the_system():
    """--help must work on a clean machine, before anything is installed."""
    script = "install-macos.sh" if sys.platform == "darwin" else "install-linux.sh"
    result = subprocess.run([str(SCRIPTS / script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for flag in ("--yes", "--skip-mathlib", "--skip-latex", "--full-latex", "--no-config", "--prefix"):
        assert flag in result.stdout


def test_unknown_options_are_refused():
    script = "install-macos.sh" if sys.platform == "darwin" else "install-linux.sh"
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
