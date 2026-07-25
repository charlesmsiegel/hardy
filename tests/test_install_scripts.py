from __future__ import annotations

import os
import shutil
import subprocess
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




def test_an_unattended_install_writes_only_settings_the_parser_accepts(tmp_path: Path):
    """A generated config with an unknown key makes every later Hardy
    invocation fail with "unknown settings" — including the install-time doctor."""
    from hardy import config as configuration

    written = written_config(tmp_path, HARDY_MODEL="claude-opus-5", ANTHROPIC_API_KEY="sk-ant", OPENAI_API_KEY="sk-oai")
    assert 'model = "claude-opus-5"' in written
    assert "api_key" not in written and "base_url" not in written

    target = tmp_path / "written.toml"
    target.write_text(written, encoding="utf-8")
    assert configuration.load(target).model == "claude-opus-5"


def test_the_installer_installs_the_cli_the_runtime_needs(tmp_path: Path):
    """`hardy doctor` requires the Claude Code CLI, so a full install that never
    installs it would complete and then fail its own verification."""
    source = (SCRIPTS / "lib/common.sh").read_text(encoding="utf-8")
    assert "ensure_claude_cli" in source
    assert "@anthropic-ai/claude-code" in source
    assert source.index("ensure_claude_cli") < source.index("\n\tverify")
    windows = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    assert "Install-ClaudeCli" in windows and "@anthropic-ai/claude-code" in windows
