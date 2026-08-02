from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
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


def run_installer_functions(body: str, script: Path = POWERSHELL_SCRIPT) -> subprocess.CompletedProcess:
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
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$t, [ref]$e); "
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


# --- installing from a published release ------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *arguments):  # noqa: D102 - the default logs to stderr
        pass


@contextlib.contextmanager
def serving(directory: Path):
    """A real HTTP server on the loopback interface, for the length of a test.

    The installers reach their release over HTTP, and a stub that returns file
    contents would not exercise the thing that actually runs — curl, and
    Invoke-WebRequest, against a URL.
    """
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def publish_release(directory: Path, *, corrupt: bool = False) -> str:
    """Lay out a release the way the release workflow does, and return its wheel name.

    `corrupt` rewrites the wheel after the manifest was computed, which is what
    a truncated download or a substituted file looks like from the installer's
    side.
    """
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "hardy_prover-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"the contents the manifest was computed over")
    lines = [f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}"]
    # Two tarballs sit beside the wheel in a real release, and the sdist's name
    # ends the same way the installer bundle's does.
    for name in ("hardy_prover-9.9.9.tar.gz", "hardy-installers.tar.gz"):
        payload = directory / name
        payload.write_bytes(name.encode())
        lines.append(f"{hashlib.sha256(payload.read_bytes()).hexdigest()}  {name}")
    if corrupt:
        wheel.write_bytes(b"something else entirely")
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return wheel.name


def run_with_common(body: str, tmp_path: Path, **environment: str) -> subprocess.CompletedProcess:
    """Run `body` with lib/common.sh in scope, as every POSIX installer has it."""
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")
    driver = tmp_path / "drive-common.sh"
    driver.write_text(f'set -eu\n. "{SCRIPTS / "lib/common.sh"}"\n{body}\n', encoding="utf-8")
    return subprocess.run(
        ["bash", str(driver)], capture_output=True, text=True,
        env={**os.environ, **environment, "HOME": str(tmp_path)},
    )


@posix_only
def test_the_release_location_follows_what_was_asked_for(tmp_path: Path):
    """Latest by default, a named tag when one is given, and anywhere at all
    when HARDY_RELEASE_BASE_URL is set — which is how CI points the installer at
    a release it built moments earlier."""
    default = run_with_common("release_base_url", tmp_path)
    assert default.stdout == "https://github.com/charlesmsiegel/hardy/releases/latest/download"

    pinned = run_with_common("release_base_url", tmp_path, HARDY_VERSION="v0.2.0")
    assert pinned.stdout == "https://github.com/charlesmsiegel/hardy/releases/download/v0.2.0"

    overridden = run_with_common("release_base_url", tmp_path, HARDY_RELEASE_BASE_URL="http://127.0.0.1:8000/")
    assert overridden.stdout == "http://127.0.0.1:8000"


@posix_only
def test_the_wheel_is_picked_out_of_the_manifest_and_nothing_else_is(tmp_path: Path):
    """The manifest is how the installer learns the version, so the lookup has
    to survive a release that also carries two tarballs."""
    publish_release(tmp_path / "release")
    manifest = tmp_path / "release/SHA256SUMS"

    found = run_with_common(f'release_asset "{manifest}" .whl', tmp_path)
    assert found.returncode == 0, found.stderr
    digest, name = found.stdout.split()
    assert name == "hardy_prover-9.9.9-py3-none-any.whl"
    assert len(digest) == 64

    missing = run_with_common(f'release_asset "{manifest}" .exe || printf absent', tmp_path)
    assert missing.stdout == "absent"


@posix_only
def test_a_release_download_is_checked_against_the_manifest(tmp_path: Path):
    release = tmp_path / "release"
    wheel = publish_release(release)
    with serving(release) as base:
        result = run_with_common(
            f'download_release_asset .whl "{tmp_path}/download"', tmp_path,
            HARDY_RELEASE_BASE_URL=base,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"{tmp_path}/download/{wheel}"
    assert (tmp_path / "download" / wheel).exists()


@posix_only
def test_a_download_that_does_not_match_the_manifest_is_refused(tmp_path: Path):
    """The wheel is code that will run as this user. A file that is not the one
    the release vouched for has to stop the install, not merely warn."""
    release = tmp_path / "release"
    publish_release(release, corrupt=True)
    with serving(release) as base:
        result = run_with_common(
            f'download_release_asset .whl "{tmp_path}/download"', tmp_path,
            HARDY_RELEASE_BASE_URL=base,
        )
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr


@posix_only
def test_a_release_with_no_manifest_says_so_rather_than_installing_anything(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with serving(empty) as base:
        result = run_with_common(
            f'download_release_asset .whl "{tmp_path}/download"', tmp_path,
            HARDY_RELEASE_BASE_URL=base,
        )
    assert result.returncode != 0
    assert "SHA256SUMS" in result.stderr


@posix_only
@pytest.mark.parametrize(
    ("tree", "expected"),
    [(True, "source"), (False, "release")],
    ids=["a checkout installs itself", "anything else takes the release"],
)
def test_where_hardy_comes_from_is_decided_by_what_is_actually_here(tmp_path: Path, tree: bool, expected: str):
    """An unpacked installer bundle carries scripts/ and no pyproject.toml,
    precisely so that a machine with no clone lands on the release."""
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    if tree:
        (root / "pyproject.toml").write_text('[project]\nname = "hardy-prover"\n', encoding="utf-8")
    result = run_with_common(f'REPO_ROOT="{root}"; resolve_install_source; printf %s "$INSTALL_FROM"', tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@posix_only
def test_an_unreadable_install_source_is_refused_rather_than_guessed(tmp_path: Path):
    result = run_with_common("resolve_install_source", tmp_path, HARDY_INSTALL_FROM="wherever")
    assert result.returncode != 0
    assert "release" in result.stderr and "source" in result.stderr


def installer_bundle(path: Path, marker: str) -> None:
    """A `hardy-installers.tar.gz` whose install-linux.sh only announces itself.

    The bootstrap's job is to fetch the rest of the installer and hand over to
    it; what it hands over to is the part being stubbed here, so that the test
    stops at the handover instead of installing Hardy onto the test machine.
    """
    staging = path.parent / "bundle"
    scripts = staging / "scripts"
    (scripts / "lib").mkdir(parents=True, exist_ok=True)
    (scripts / "lib" / "common.sh").write_text("# stub\n", encoding="utf-8")
    (scripts / "install-linux.sh").write_text(f"#!/usr/bin/env bash\necho '{marker}'\n", encoding="utf-8")
    with tarfile.open(path, "w:gz") as bundle:
        bundle.add(scripts, arcname="scripts")


def run_standalone(script: str, tmp_path: Path, **environment: str) -> subprocess.CompletedProcess:
    """Run one installer script with nothing beside it, as a download would be."""
    lonely = tmp_path / "elsewhere/bin"
    lonely.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / script, lonely / script)
    # install.sh is the one piped into `sh`, so run it under a real /bin/sh:
    # bash would accept things a POSIX shell does not.
    interpreter = "sh" if script == "install.sh" else "bash"
    return subprocess.run(
        [interpreter, str(lonely / script)], capture_output=True, text=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "HARDY_HOME": str(tmp_path / "hardy"),
            # Nothing may reach the network, including the fallback.
            "HARDY_REPO_URL": "http://127.0.0.1:1/unreachable",
            **environment,
        },
    )


@posix_only
@pytest.mark.parametrize("script", ["install.sh", "install-linux.sh"])
def test_one_downloaded_script_fetches_the_rest_of_the_installer_from_the_release(
    tmp_path: Path, script: str
):
    """The whole point of the release: a machine with a single script on it and
    no clone anywhere reaches a working installer. The dispatcher and the Linux
    installer each carry their own copy of this, because each has to work as the
    only file present.
    """
    release = tmp_path / "release"
    release.mkdir()
    installer_bundle(release / "hardy-installers.tar.gz", "ran the release installers")
    with serving(release) as base:
        result = run_standalone(script, tmp_path, HARDY_RELEASE_BASE_URL=base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Fetching the Hardy installers" in result.stdout
    assert "ran the release installers" in result.stdout
    assert (tmp_path / "hardy/installers/scripts/lib/common.sh").exists()
    assert not (tmp_path / "hardy/src").exists(), "the repository was fetched despite a usable release"


@posix_only
@pytest.mark.parametrize("script", ["install.sh", "install-linux.sh"])
def test_with_neither_a_release_nor_a_repository_the_installer_says_both(tmp_path: Path, script: str):
    """Two failed fetches must not read as one: whoever is looking at this needs
    to know the release was tried as well as the repository."""
    result = run_standalone(
        script, tmp_path, HARDY_RELEASE_BASE_URL="http://127.0.0.1:1/unreachable"
    )
    assert result.returncode != 0
    assert "could not fetch the Hardy installers" in result.stderr
    assert "clone the repository yourself" in result.stderr


@posix_only
def test_a_named_ref_takes_the_repository_rather_than_a_release(tmp_path: Path):
    """HARDY_REPO_REF is how a fork or a branch is installed, and neither has a
    release to download. Reaching for one anyway would install the wrong Hardy.
    """
    release = tmp_path / "release"
    release.mkdir()
    installer_bundle(release / "hardy-installers.tar.gz", "ran the release installers")
    with serving(release) as base:
        result = run_standalone(
            "install-linux.sh", tmp_path, HARDY_RELEASE_BASE_URL=base, HARDY_REPO_REF="some-branch"
        )
    assert result.returncode != 0
    assert "ran the release installers" not in result.stdout
    assert "some-branch" in result.stderr
    assert not (tmp_path / "hardy/installers").exists()


def test_the_release_publishes_exactly_what_the_installers_ask_for():
    """The asset names are a contract between two files nothing else connects.

    Rename one side and installation breaks for everyone, on a path no test
    that runs before the release could otherwise reach.
    """
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "hardy-installers.tar.gz" in workflow
    assert "SHA256SUMS" in workflow
    for name in ("install.sh", "install-linux.sh", "install-macos.sh"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "hardy-installers.tar.gz" in source, f"{name} does not fetch the release's installer bundle"
    # The POSIX scripts share one downloader; the PowerShell ones cannot, and
    # each reads the manifest itself.
    for name in ("lib/common.sh", "install-windows.ps1", "update-windows.ps1"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "SHA256SUMS" in source, f"{name} does not read the release manifest"
    assert "download_release_asset" in (SCRIPTS / "update.sh").read_text(encoding="utf-8"), (
        "update.sh cannot move a release install to a newer release"
    )


@pytest.mark.parametrize(
    "script", [POWERSHELL_SCRIPT, SCRIPTS / "update-windows.ps1"], ids=lambda p: p.name
)
def test_both_windows_scripts_verify_what_they_download(script: Path):
    """install-windows.ps1 has to run as a single downloaded file, so it cannot
    dot-source a shared library and the download logic is duplicated. Duplicated
    is survivable; one copy quietly losing its verification is not."""
    source = script.read_text(encoding="utf-8")
    assert "Get-FileHash" in source
    assert "checksum mismatch" in source


def test_the_windows_installer_reads_the_manifest_the_release_writes():
    result = run_installer_functions(
        "$RepoUrl = 'https://example.invalid/hardy'; $ReleaseVersion = ''; "
        "$m = New-TemporaryFile; "
        "Set-Content -LiteralPath $m -Value @("
        "  'aaaa  hardy-installers.tar.gz',"
        "  'bbbb  hardy_prover-9.9.9.tar.gz',"
        "  'cccc  hardy_prover-9.9.9-py3-none-any.whl'); "
        "$asset = Find-ReleaseAsset $m '.whl'; "
        "Write-Output \"$($asset.Digest) $($asset.Name)\"; "
        "Write-Output (Get-ReleaseBaseUrl)"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.split()
    assert lines[0] == "cccc"
    assert lines[1] == "hardy_prover-9.9.9-py3-none-any.whl"
    assert lines[2] == "https://example.invalid/hardy/releases/latest/download"


def test_the_windows_installer_refuses_a_download_the_manifest_does_not_vouch_for(tmp_path: Path):
    release = tmp_path / "release"
    publish_release(release, corrupt=True)
    with serving(release) as base:
        result = run_installer_functions(
            f"$env:HARDY_RELEASE_BASE_URL = '{base}'; $RepoUrl = ''; $ReleaseVersion = ''; "
            f"Save-ReleaseAsset '.whl' '{tmp_path / 'download'}'"
        )
    assert result.returncode != 0
    assert "checksum mismatch" in result.stdout + result.stderr


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
