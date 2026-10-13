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


@pytest.mark.parametrize("name", ["install.sh", "install-linux.sh", "install-macos.sh", "lib/common.sh"])
def test_parent_traversal_below_root_symlink_is_refused(name, tmp_path):
    alias = tmp_path / "root-alias"
    alias.symlink_to("/", target_is_directory=True)
    if name == "lib/common.sh":
        program = '. "$1"; require_absolute_dir_option --prefix "$2"'
    else:
        source = (SCRIPTS / name).read_text()
        program = source[source.index('bootstrap_require_absolute_dir_option()'):source.index('# A standalone entrypoint')]
        program += '\nbootstrap_require_absolute_dir_option --prefix "$2"'
    result = subprocess.run(["bash", "-c", program, "test", str(SCRIPTS / name), str(alias / "hardy-nonexistent-test-dir" / "..")],
                            env=dict(os.environ, HOME=str(tmp_path)), capture_output=True, text=True)
    assert result.returncode == 1
    assert "--prefix" in result.stderr


@pytest.mark.parametrize("checks,skip_latex,skip_mathlib,model,expected", [
    ([('claude login', False, True)], 0, 0, 'configured', 0),
    ([('claude cli', False, True)], 0, 0, 'configured', 1),
    ([('tectonic', False, True)], 0, 0, 'configured', 1),
    ([('latex', False, True), ('tectonic', False, True)], 1, 0, 'configured', 0),
    ([('lean', False, True), ('mathlib', False, True)], 0, 1, 'configured', 0),
    ([('model', False, True)], 0, 0, '', 0),
    ([('claude cli', False, True)], 1, 1, '', 1),
    ([('cas', False, False)], 0, 0, 'configured', 0),
])
def test_install_verdict_classifies_doctor_checks_and_always_prints_summary(
    checks, skip_latex, skip_mathlib, model, expected, tmp_path,
):
    import json
    import sys

    venv = tmp_path / "prefix" / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text('#!' + sys.executable + '\n'
                      'import os, sys, json\n'
                      'from hardy.app import doctor, config\n'
                      'doctor.run_checks = lambda _: [doctor.Check(n, ok, n + " detail", required) '
                      'for n, ok, required in json.loads(os.environ["FAKE_DOCTOR_CHECKS"])]\n'
                      'config.load = lambda: None\n'
                      'program = sys.argv[2]; sys.argv = sys.argv[2:]; exec(program)\n')
    python.chmod(0o755)
    hardy = venv / "bin" / "hardy"
    hardy.write_text('#!/bin/sh\n[ "$1" = --help ] && exit 0\nexit 1\n')
    hardy.chmod(0o755)
    noops = ('resolve_install_source', 'check_disk_space', 'ensure_python', 'ensure_tools', 'create_environment',
             'link_command', 'install_launcher', 'ensure_elan', 'ensure_lean_project', 'ensure_latex',
             'ensure_tectonic', 'ensure_claude_cli', 'write_config')
    program = '. "$1";\n' + '\n'.join(name + '() { :; }' for name in noops)
    program += '\nos_label() { echo test; }; INSTALL_FROM=source; REPO_ROOT="$PWD";'
    program += f' SKIP_LATEX={skip_latex}; SKIP_MATHLIB={skip_mathlib}; hardy_install_main --yes'
    result = subprocess.run(["bash", "-c", program, "test", str(SCRIPTS / 'lib/common.sh')],
                            env=dict(os.environ, HOME=str(tmp_path), HARDY_HOME=str(tmp_path / 'prefix'),
                                     HARDY_CONFIG=str(tmp_path / 'config.toml'), HARDY_MODEL=model,
                                     PYTHONPATH=str(SCRIPTS.parent / 'src'), FAKE_DOCTOR_CHECKS=json.dumps(checks)),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stdout + result.stderr
    assert 'Hardy is installed.' in result.stdout
    if checks[0][0] == 'claude login':
        assert 'claude login' in result.stdout


@pytest.mark.parametrize('pull_status', [0, 7])
def test_update_redacts_credentialed_git_origin_on_success_and_failure(pull_status, tmp_path):
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    (source / 'pyproject.toml').write_text('[project]\nname = "test"\n')
    tools = tmp_path / 'tools'
    tools.mkdir()
    git = tools / 'git'
    git.write_text('#!/bin/sh\ncase "$3" in\n'
                   'rev-parse) echo current ;;\n'
                   'diff) exit 0 ;;\n'
                   'pull) echo "https://u:SECRET@example.test/fork"; '
                   'echo "fatal: https://u:SECRET@example.test/fork https://u:SECRET@example.test/again" >&2; '
                   f'exit {pull_status} ;;\nesac\n')
    git.chmod(0o755)
    uv = tools / 'uv'
    uv.write_text('#!/bin/sh\nexit 0\n')
    uv.chmod(0o755)
    result = run_script('update.sh', tmp_path, ['--yes', '--source', str(source)],
                        {'PATH': str(tools) + os.pathsep + os.environ['PATH']})
    assert result.returncode == (1 if pull_status else 0), result.stdout + result.stderr
    assert 'SECRET' not in result.stdout + result.stderr
    assert 'https://***@example.test/fork' in result.stdout + result.stderr
    if pull_status:
        assert 'git pull failed' in result.stderr
