from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHELL_SCRIPTS = ["install.sh", "install-linux.sh", "install-macos.sh", "uninstall.sh", "update.sh", "lib/common.sh"]
POWERSHELL_SCRIPT = SCRIPTS / "install-windows.ps1"
POWERSHELL_SCRIPTS = [POWERSHELL_SCRIPT, SCRIPTS / "uninstall-windows.ps1", SCRIPTS / "update-windows.ps1"]
# Every script a user can run directly, as opposed to lib/common.sh.
EXECUTABLE_SCRIPTS = ["install.sh", "install-linux.sh", "install-macos.sh", "uninstall.sh", "update.sh"]

# Only the POSIX installers need a POSIX host. The Windows installer is checked
# everywhere, and above all on Windows, which is the platform it is written for.
posix_only = pytest.mark.skipif(os.name == "nt", reason="the POSIX installers are checked on POSIX hosts")


@posix_only
@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_installers_parse(name: str):
    interpreter = "sh" if name == "install.sh" else "bash"
    if shutil.which(interpreter) is None:
        pytest.skip(f"{interpreter} is not available")
    subprocess.run([interpreter, "-n", str(SCRIPTS / name)], check=True)


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_scripts_keep_unix_line_endings(name: str):
    """A CRLF shell script fails at the shebang: `bad interpreter: bash^M`.

    Windows tooling introduces them silently, and nothing else in the suite
    would notice until the script reached a Linux machine. .gitattributes
    pins the checkout; this catches a file that arrived wrong anyway.
    """
    assert b"\r\n" not in (SCRIPTS / name).read_bytes(), f"{name} has CRLF line endings"


@posix_only
@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shellcheck_is_clean(name: str):
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(["shellcheck", "-x", str(SCRIPTS / name)], capture_output=True, text=True, cwd=SCRIPTS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_executable_installers_are_executable():
    """Checked against the git index, not the working copy.

    Windows has no executable bit and reports every file executable, so a
    script committed 100644 would look fine on the machine that added it and
    arrive unrunnable on the machines that need it.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts"],
        capture_output=True, text=True, cwd=SCRIPTS.parent,
    )
    if listing.returncode != 0:
        pytest.skip("not a git checkout")
    modes = {line.split()[3]: line.split()[0] for line in listing.stdout.splitlines() if line.strip()}
    for name in EXECUTABLE_SCRIPTS:
        assert modes.get(f"scripts/{name}") == "100755", f"{name} is committed without the executable bit"


@posix_only
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


@posix_only
@pytest.mark.parametrize("script", ["install-linux.sh", "install-macos.sh"])
def test_unknown_options_are_refused(script: str):
    result = subprocess.run([str(SCRIPTS / script), "--definitely-not-a-flag"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "unknown option" in result.stderr


@posix_only
def test_the_dispatcher_delegates_to_this_platforms_installer():
    result = subprocess.run([str(SCRIPTS / "install.sh"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Installs everything Hardy needs" in result.stdout
    # Windows users reach the dispatcher too, and must be sent to PowerShell.
    assert "install-windows.ps1" in (SCRIPTS / "install.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS, ids=lambda p: p.name)
def test_powershell_installer_parses(script: Path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not installed")
    command = (
        "$errors = $null; $tokens = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run([powershell, "-NoProfile", "-Command", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def fake_installation(root: Path) -> dict[str, Path]:
    """Everything an install leaves behind, so removal can be checked against it."""
    places = {
        "venv": root / "share/hardy/venv",
        "lean": root / "share/hardy/lean",
        "src": root / "share/hardy/src",
        "bin": root / "bin",
        "config": root / "config/hardy/config.toml",
        "elan": root / ".elan",
        "profile": root / ".profile",
    }
    for key in ("venv", "lean", "src", "bin", "elan"):
        places[key].mkdir(parents=True, exist_ok=True)
    (places["venv"] / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (places["lean"] / "lakefile.toml").write_text("name = \"hardymath\"\n", encoding="utf-8")
    (places["bin"] / "hardy").write_text("#!/bin/sh\n", encoding="utf-8")
    places["config"].parent.mkdir(parents=True, exist_ok=True)
    places["config"].write_text('model = "claude-opus-5"\n', encoding="utf-8")
    places["profile"].write_text(
        'export PATH="/other/bin:$PATH"\n'
        f'export PATH="{places["bin"]}:$PATH"  # added by the Hardy installer\n',
        encoding="utf-8",
    )
    return places


def run_uninstall(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    places = {
        "HOME": str(root),
        "HARDY_SKIP_PLATFORM_CHECK": "1",
        "HARDY_HOME": str(root / "share/hardy"),
        "HARDY_BIN_DIR": str(root / "bin"),
        "HARDY_CONFIG": str(root / "config/hardy/config.toml"),
    }
    return subprocess.run(
        ["bash", str(SCRIPTS / "uninstall.sh"), *arguments],
        capture_output=True, text=True, env={**os.environ, **places},
    )


@posix_only
def test_uninstall_takes_the_command_and_environment(tmp_path: Path):
    places = fake_installation(tmp_path)
    result = run_uninstall(tmp_path, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not places["venv"].exists()
    assert not places["src"].exists()
    assert not (places["bin"] / "hardy").exists()


@posix_only
def test_uninstall_leaves_the_costly_and_the_personal_alone_unless_asked(tmp_path: Path):
    """--yes must not mean yes to everything.

    Rebuilding the Lean project is a multi-gigabyte download, and the config is
    the user's own; an unattended uninstall that silently took both would be a
    far worse mistake than leaving them behind.
    """
    places = fake_installation(tmp_path)
    result = run_uninstall(tmp_path, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert places["lean"].exists(), "the Lean project was removed without being asked for"
    assert places["config"].exists(), "the config was removed without being asked for"
    assert places["elan"].exists(), "elan was removed without being asked for"


@posix_only
def test_uninstall_takes_the_rest_when_asked(tmp_path: Path):
    places = fake_installation(tmp_path)
    result = run_uninstall(tmp_path, "--yes", "--remove-lean-project", "--remove-config", "--remove-toolchain")
    assert result.returncode == 0, result.stdout + result.stderr
    for key in ("venv", "lean", "src", "config", "elan"):
        assert not places[key].exists(), f"{key} survived an uninstall that asked for it"


@posix_only
def test_uninstall_removes_only_the_path_line_the_installer_added(tmp_path: Path):
    places = fake_installation(tmp_path)
    run_uninstall(tmp_path, "--yes")
    remaining = places["profile"].read_text(encoding="utf-8")
    assert "added by the Hardy installer" not in remaining
    assert 'export PATH="/other/bin:$PATH"' in remaining, "an unrelated PATH line was removed"


@posix_only
def test_uninstall_of_nothing_is_not_an_error(tmp_path: Path):
    """Re-running it, or running it on a machine where the install half-failed,
    must report cleanly rather than fail on the first missing directory."""
    result = run_uninstall(tmp_path, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr


@posix_only
def test_update_explains_itself_and_refuses_nonsense():
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    environment = {**os.environ, "HARDY_SKIP_PLATFORM_CHECK": "1"}
    helped = subprocess.run(["bash", str(SCRIPTS / "update.sh"), "--help"], capture_output=True, text=True, env=environment)
    assert helped.returncode == 0, helped.stderr
    for flag in ("--mathlib", "--toolchain", "--source", "--yes"):
        assert flag in helped.stdout
    refused = subprocess.run(["bash", str(SCRIPTS / "update.sh"), "--nope"], capture_output=True, text=True, env=environment)
    assert refused.returncode != 0
    assert "unknown option" in refused.stderr


@posix_only
def test_update_says_what_is_wrong_when_hardy_is_not_installed(tmp_path: Path):
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    result = subprocess.run(
        ["bash", str(SCRIPTS / "update.sh"), "--yes"], capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path), "HARDY_HOME": str(tmp_path / "share/hardy"),
             "HARDY_SKIP_PLATFORM_CHECK": "1"},
    )
    assert result.returncode != 0
    assert "install" in (result.stdout + result.stderr).lower()


def test_every_supported_platform_has_an_installer():
    assert POWERSHELL_SCRIPT.exists()
    for name in SHELL_SCRIPTS:
        assert (SCRIPTS / name).exists()


def run_installer_functions(body: str) -> subprocess.CompletedProcess:
    """Run `body` with the installer's function definitions in scope.

    The script installs Hardy when it is dot-sourced, so its functions are
    lifted out of the parse tree instead: that tests the definitions actually
    shipped rather than a copy of them.
    """
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is not installed")
    preamble = (
        "$e = $null; $t = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{POWERSHELL_SCRIPT}', [ref]$t, [ref]$e); "
        "$ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | "
        "ForEach-Object { Invoke-Expression $_.Extent.Text }; "
    )
    return subprocess.run([powershell, "-NoProfile", "-Command", preamble + body], capture_output=True, text=True)


def test_the_windows_installer_writes_files_without_a_byte_order_mark(tmp_path: Path):
    """Windows PowerShell's `-Encoding UTF8` prepends a BOM.

    Lean rejects one before `import` ("expected token" at 1:0), so the
    installer's own Mathlib probe fails on a perfectly good project and the
    install stops with "'import Mathlib' still fails". tomllib rejects one
    before the first key, so a config written the same way breaks every later
    Hardy command.
    """
    probe = tmp_path / "probe.lean"
    config_file = tmp_path / "config.toml"
    result = run_installer_functions(
        f"Write-Utf8File '{probe}' \"import Mathlib`n\"; "
        f"Write-Utf8File '{config_file}' \"model = `\"m`\"`n\"; "
        f"Add-Utf8Line '{config_file}' 'lean_project = \"p\"'"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for path in (probe, config_file):
        assert path.exists(), f"{path.name} was not written: {result.stdout}{result.stderr}"
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), f"{path.name} starts with a UTF-8 BOM"
    # Appending must leave the file readable, and on its own line.
    assert config_file.read_text(encoding="utf-8").splitlines() == ['model = "m"', 'lean_project = "p"']


@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS, ids=lambda p: p.name)
def test_the_windows_scripts_generate_no_utf8_bom(script: Path):
    """The BOM-free helpers are only worth having if nothing bypasses them."""
    code = [line for line in script.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    offenders = [line.strip() for line in code if "-Encoding UTF8" in line]
    assert not offenders, f"-Encoding UTF8 writes a BOM on Windows PowerShell; use Write-Utf8File: {offenders}"


@pytest.mark.parametrize("name", ["uninstall.sh", "update.sh"])
def test_the_posix_scripts_send_windows_users_to_powershell(name: str):
    """Someone in Git Bash reaching for uninstall.sh needs the same signpost
    install.sh gives them, not a half-completed removal against POSIX paths."""
    source = (SCRIPTS / name).read_text(encoding="utf-8")
    assert "MINGW" in source and name.replace(".sh", "-windows.ps1") in source


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




@posix_only
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


@posix_only
def test_the_installer_installs_the_cli_the_runtime_needs(tmp_path: Path):
    """`hardy doctor` requires the Claude Code CLI, so a full install that never
    installs it would complete and then fail its own verification."""
    source = (SCRIPTS / "lib/common.sh").read_text(encoding="utf-8")
    assert "ensure_claude_cli" in source
    assert "@anthropic-ai/claude-code" in source
    assert source.index("ensure_claude_cli") < source.index("\n\tverify")
    windows = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    assert "Install-ClaudeCli" in windows and "@anthropic-ai/claude-code" in windows
