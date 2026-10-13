"""Pinned POSIX Tectonic recipe, without downloads or running toolchains."""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from hardy.app import installers

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX Tectonic recipe and executable modes')

TARGET = 'x86_64-unknown-linux-musl'


def archive_bytes():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in ['tectonic', '../unrelated']:
            payload = b'#!/bin/sh\nexit 0\n'
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_posix_tectonic_verifies_archive_and_extracts_only_executable(tmp_path, monkeypatch):
    content = archive_bytes()
    monkeypatch.setitem(installers.TECTONIC_POSIX_SHA256, TARGET, hashlib.sha256(content).hexdigest())
    urls = []

    def download(url, destination):
        urls.append(url)
        destination.write_bytes(content)

    result = installers.install_tectonic(destination_root=tmp_path / 'tools', target=TARGET,
                                        confirmer=lambda _: True, downloader=download)
    assert result.installed_path is not None
    assert result.installed_path.name == 'tectonic'
    assert result.installed_path.read_bytes() == b'#!/bin/sh\nexit 0\n'
    assert result.installed_path.stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / 'unrelated').exists()
    assert urls[0].endswith(f'tectonic-{installers.TECTONIC_VERSION}-{TARGET}.tar.gz')


def test_tampered_posix_tectonic_preserves_previous_install(tmp_path):
    previous = tmp_path / 'tools' / 'tectonic' / installers.TECTONIC_VERSION / 'tectonic'
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b'previous verified executable')
    result = installers.install_tectonic(destination_root=tmp_path / 'tools', target=TARGET,
                                        confirmer=lambda _: True, downloader=lambda _, path: path.write_bytes(b'tampered'))
    assert result.installed_path is None
    assert 'checksum' in result.manual_instructions
    assert previous.read_bytes() == b'previous verified executable'


def test_unsupported_posix_target_does_not_download(tmp_path):
    def download(*_):
        pytest.fail('an unsupported platform must not download anything')

    result = installers.install_tectonic(destination_root=tmp_path, target='unsupported',
                                        confirmer=lambda _: True, downloader=download)
    assert result.installed_path is None
    assert 'manually' in result.manual_instructions


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell integration')
@pytest.mark.parametrize('existing,skip_config', [(False, False), (True, False), (False, True)])
def test_shell_tectonic_records_path_and_preserves_existing_config(existing, skip_config, tmp_path):
    import subprocess
    import sys

    source = Path(__file__).resolve().parents[1] / 'scripts/lib/common.sh'
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    python = venv / 'bin' / 'python'
    installed = tmp_path / 'tools/tectonic'
    python.write_text('#!' + sys.executable + '\n'
                      'import sys\nfrom types import SimpleNamespace\nfrom hardy.app import installers\n'
                      f'installers.install_tectonic=lambda **kwargs: SimpleNamespace(installed_path={str(installed)!r})\n'
                      'sys.argv=sys.argv[1:]; exec(sys.stdin.read())\n')
    python.chmod(0o755)
    config = tmp_path / 'config.toml'
    if existing:
        config.write_text('tectonic = "/user/compiler"\n')
    program = '. "$1"; VENV="$2"; WRITE_CONFIG="$3"; ensure_tectonic; printf "selected=%s\\n" "${HARDY_TECTONIC:-}"'
    environment = {key: value for key, value in os.environ.items() if key != 'HARDY_TECTONIC'}
    environment.update(HOME=str(tmp_path), HARDY_CONFIG=str(config), PYTHONPATH=str(source.parents[2] / 'src'))
    result = subprocess.run(['bash', '-c', program, 'test', str(source), str(venv), '0' if skip_config else '1'],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    if existing:
        assert config.read_text() == 'tectonic = "/user/compiler"\n'
        assert 'selected=\n' in result.stdout
    elif skip_config:
        assert not config.exists()
        assert f'selected={installed}' in result.stdout
    else:
        assert config.read_text() == f'tectonic = "{installed}"\n'
        assert f'selected={installed}' in result.stdout


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell integration')
@pytest.mark.parametrize('skip_latex', [0, 1])
def test_shell_tectonic_skip_and_failure_preserve_config(skip_latex, tmp_path):
    import subprocess

    source = Path(__file__).resolve().parents[1] / 'scripts/lib/common.sh'
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    python = venv / 'bin' / 'python'
    marker = tmp_path / 'called'
    python.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    python.chmod(0o755)
    config = tmp_path / 'config.toml'
    config.write_text('model = "configured"\n')
    result = subprocess.run(['bash', '-c', '. "$1"; VENV="$2"; SKIP_LATEX="$3"; ensure_tectonic',
                             'test', str(source), str(venv), str(skip_latex)],
                            env=dict(os.environ, HOME=str(tmp_path), HARDY_CONFIG=str(config)),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == (0 if skip_latex else 1)
    assert marker.exists() == (not skip_latex)
    assert config.read_text() == 'model = "configured"\n'
