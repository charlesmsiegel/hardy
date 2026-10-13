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

    from hardy.app.config import read_file

    source = Path(__file__).resolve().parents[1] / 'scripts/lib/common.sh'
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    python = venv / 'bin' / 'python'
    installed = tmp_path / 'prefix/tools/tectonic/0.16.9/tectonic'
    installed.parent.mkdir(parents=True)
    installed.write_text('downloaded compiler')
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
    environment.update(HOME=str(tmp_path), HARDY_HOME=str(tmp_path / 'prefix'), HARDY_CONFIG=str(config), PYTHONPATH=str(source.parents[2] / 'src'))
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
        assert read_file(config)['tectonic'] == str(installed)
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


def _run_shell_tectonic(tmp_path, contents, env_setting=None, healthy=False):
    import subprocess
    import sys

    source = Path(__file__).resolve().parents[1] / 'scripts/lib/common.sh'
    config = tmp_path / 'config.toml'
    compiler = tmp_path / 'custom-compiler'
    if healthy:
        compiler.write_text('#!/bin/sh\necho tectonic-test\n')
        compiler.chmod(0o755)
    if contents is not None:
        config.write_text(contents.replace('CUSTOM', str(compiler)), encoding='utf-8')
    venv = tmp_path / 'venv'
    (venv / 'bin').mkdir(parents=True)
    worker = venv / 'bin/python'
    installed = tmp_path / 'prefix/tools/tectonic/0.16.9/tectonic'
    installed.parent.mkdir(parents=True)
    installed.write_text('#!/bin/sh\necho downloaded\n')
    installed.chmod(0o755)
    marker = tmp_path / 'downloaded'
    worker.write_text('#!' + sys.executable + '\n'
                      'import sys\nfrom types import SimpleNamespace\nfrom pathlib import Path\n'
                      'from hardy.app import installers\n'
                      f'def fake_install(**kwargs):\n Path({str(marker)!r}).touch(); return SimpleNamespace(installed_path=Path({str(installed)!r}))\n'
                      'installers.install_tectonic=fake_install\n'
                      'sys.argv=sys.argv[1:]; exec(sys.stdin.read())\n')
    worker.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if key != 'HARDY_TECTONIC'}
    environment.update(HOME=str(tmp_path), HARDY_HOME=str(tmp_path / 'prefix'), HARDY_CONFIG=str(config),
                       PYTHONPATH=str(source.parents[2] / 'src'))
    if env_setting is not None:
        environment['HARDY_TECTONIC'] = env_setting.replace('CUSTOM', str(compiler))
    result = subprocess.run(['bash', '-c', '. "$1"; VENV="$2"; ensure_tectonic; result=$?; '
                             'printf "selected=%s\\n" "${HARDY_TECTONIC:-}"; exit "$result"',
                             'test', str(source), str(venv)], env=environment, capture_output=True, text=True, timeout=15)
    return result, config, marker, installed


@pytest.mark.parametrize('contents,environment', [('tectonic = "CUSTOM"\n', None), (None, 'CUSTOM')])
def test_healthy_effective_compiler_skips_recipe_even_during_outage(tmp_path, contents, environment):
    result, config, marker, _ = _run_shell_tectonic(tmp_path, contents, environment, healthy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), 'an existing healthy compiler must not download'
    if contents:
        assert config.read_text() == contents.replace('CUSTOM', str(tmp_path / 'custom-compiler'))
    else:
        assert not config.exists()


@pytest.mark.parametrize('contents', ['\ufefftectonic = "CUSTOM"\n', '"tectonic" = "CUSTOM"\n'])
def test_parser_supported_custom_compiler_setting_is_not_duplicated(tmp_path, contents):
    from hardy.app.config import read_file

    result, config, _, _ = _run_shell_tectonic(tmp_path, contents)
    assert result.returncode == 0, result.stdout + result.stderr
    assert config.read_text() == contents.replace('CUSTOM', str(tmp_path / 'custom-compiler'))
    assert read_file(config)['tectonic'] == str(tmp_path / 'custom-compiler')


@pytest.mark.parametrize('contents', ['tectonic = ""\n', '\ufeff"tectonic" = ""\n', '"tec\\u0074onic" = ""\n'])
def test_semantically_empty_compiler_is_upserted_through_config_writer(tmp_path, contents):
    from hardy.app.config import read_file

    result, config, _, installed = _run_shell_tectonic(tmp_path, contents, env_setting='')
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_file(config)['tectonic'] == str(installed)
    assert f'selected={installed}' in result.stdout
