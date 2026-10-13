"""Hermetic regressions for installer provenance and filesystem boundaries."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX entrypoints")


def run_script(name, tmp_path, args=(), env=None, stdin=False):
    variables = dict(os.environ, HOME=str(tmp_path / "home"), HARDY_HOME=str(tmp_path / "prefix"),
                     HARDY_BIN_DIR=str(tmp_path / "bin"), HARDY_SKIP_PLATFORM_CHECK="1",
                     HARDY_RELEASE_BASE_URL="http://127.0.0.1:1")
    variables.update(env or {})
    interpreter = "sh" if name == "install.sh" else "bash"
    command = [interpreter, "-s", "--"] if stdin else [interpreter, str(SCRIPTS / name)]
    return subprocess.run(command + list(args), input=(SCRIPTS / name).read_text() if stdin else None,
                          cwd=tmp_path, env=variables, capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh", "update.sh", "uninstall.sh"])
@pytest.mark.parametrize("option", ["--prefix", "--bin-dir"])
@pytest.mark.parametrize("value", ["", ".", "/", "//", "/tmp/..", "/./"])
def test_invalid_directory_options_do_not_touch_files(name, option, value, tmp_path):
    sentinel = tmp_path / "src" / "keep"
    sentinel.parent.mkdir()
    sentinel.write_text("preserve")
    result = run_script(name, tmp_path, ["--yes", option, value])
    assert result.returncode == 1, result.stdout + result.stderr
    assert option in result.stderr
    assert sentinel.read_text() == "preserve"
    assert not (tmp_path / "prefix").exists()


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh", "update.sh", "uninstall.sh"])
@pytest.mark.parametrize("variable", ["HARDY_HOME", "HARDY_BIN_DIR"])
def test_explicit_empty_environment_directory_is_refused(name, variable, tmp_path):
    result = run_script(name, tmp_path, ["--yes"], {variable: ""})
    assert result.returncode == 1
    assert "absolute" in result.stderr
    assert not (tmp_path / "prefix").exists()


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh"])
def test_stdin_bootstrap_ignores_cwd_lookalikes(name, tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    decoy = cwd / "install-linux.sh"
    decoy.write_text('touch "' + str(tmp_path / "executed") + '"\n')
    (cwd / "lib").mkdir()
    (cwd / "lib" / "common.sh").write_text(decoy.read_text())
    # stdin identifies the shell, even when a same-named file happens to exist.
    (cwd / "sh").write_text("decoy")
    (cwd / "bash").write_text("decoy")
    # Per-OS entrypoints used to look in the cwd parent's scripts/lib instead.
    common = tmp_path / "scripts" / "lib" / "common.sh"
    common.parent.mkdir(parents=True)
    common.write_text(decoy.read_text())
    result = run_script(name, cwd, ["--yes"], stdin=True)
    assert result.returncode == 1
    assert "Fetching the Hardy installers" in result.stdout
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh"])
def test_clone_errors_do_not_disclose_credentials(name, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    git = tools / "git"
    git.write_text('#!/bin/sh\necho "fatal: https://u:SECRET@example.test/missing" >&2\nexit 1\n')
    git.chmod(0o755)
    # A standalone copy exercises the bootstrap rather than the checkout.
    result = run_script(name, tmp_path, ["--yes"],
                        {"PATH": str(tools) + os.pathsep + os.environ["PATH"],
                         "HARDY_REPO_URL": "https://u:SECRET@example.test/missing",
                         "HARDY_REPO_REF": "main"}, stdin=True)
    assert result.returncode == 1
    assert "SECRET" not in result.stdout + result.stderr
    assert "https://***@example.test/missing" in result.stderr


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh", "update.sh", "uninstall.sh"])
def test_symlink_to_root_is_refused(name, tmp_path):
    alias = tmp_path / "root-alias"
    alias.symlink_to("/", target_is_directory=True)
    result = run_script(name, tmp_path, ["--yes", "--prefix", str(alias)])
    assert result.returncode == 1
    assert "--prefix" in result.stderr
    assert not (tmp_path / "prefix").exists()


@pytest.mark.parametrize("tool", ["uv", "elan"])
def test_tampered_pinned_bootstrap_is_not_extracted_or_executed(tool, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text('#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\nprintf tampered > "$2"\n')
    curl.chmod(0o755)
    tar = tools / "tar"
    tar.write_text('#!/bin/sh\ntouch "' + str(tmp_path / "extracted") + '"\n')
    tar.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", '. "$1"; "install_pinned_$2"', "test", str(SCRIPTS / "lib/common.sh"), tool],
        env=dict(os.environ, HOME=str(tmp_path), HARDY_HOME=str(tmp_path / "prefix"),
                 PATH=str(tools) + os.pathsep + os.environ["PATH"]),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not (tmp_path / "extracted").exists()
    assert not (tmp_path / "prefix").exists()


def test_downloaded_bootstraps_have_recorded_pins():
    import re

    common = (SCRIPTS / "lib/common.sh").read_text()
    entries = re.findall(r"\t(?:uv|elan):[^)]+\) printf '%s\\n' ([0-9a-f]+) ;;", common)
    assert len(entries) == 10
    assert all(len(digest) == 64 for digest in entries)
    for path in [SCRIPTS / "install-macos.sh", SCRIPTS / "lib/common.sh"]:
        active = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"\|\s*(?:sh|bash)\b|bash -c.*curl", active)


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh"])
def test_real_script_with_bare_filename_uses_checkout(name, tmp_path):
    result = subprocess.run(["sh" if name == "install.sh" else "bash", name, "--help"],
                            cwd=SCRIPTS, env=dict(os.environ, HOME=str(tmp_path), HARDY_SKIP_PLATFORM_CHECK="1"),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Fetching the Hardy installers" not in result.stdout
    assert "--prefix" in result.stdout


def test_tampered_homebrew_script_is_not_executed(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text('#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\nprintf \'touch "%s"\\n\' "' + str(tmp_path / "executed") + '" > "$2"\n')
    curl.chmod(0o755)
    macos = (SCRIPTS / "install-macos.sh").read_text()
    homebrew = macos[macos.index('HOMEBREW_INSTALL_COMMIT='):macos.index('os_install_prerequisites()')]
    program = '. "$1"; use_homebrew_path() { :; }; have() { [ "$1" != brew ]; }; confirm() { return 0; };\n'
    program += homebrew + '\nensure_homebrew'
    result = subprocess.run(["bash", "-c", program, "test", str(SCRIPTS / "lib/common.sh")],
                            env=dict(os.environ, HOME=str(tmp_path), PATH=str(tools) + os.pathsep + os.environ["PATH"]),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not (tmp_path / "executed").exists()
