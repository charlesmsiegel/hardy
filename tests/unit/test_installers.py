import hashlib
import importlib
import zipfile


def test_declined_install_never_downloads_or_runs_anything(tmp_path) -> None:
    installers = importlib.import_module('hardy.installers')
    actions = []

    outcome = installers.install_tectonic(
        destination_root=tmp_path,
        confirmer=lambda _: False,
        downloader=lambda *_: actions.append('download'),
    )

    assert outcome.status == 'declined'
    assert 'hardy setup' in outcome.manual_instructions
    assert actions == []


def test_checksum_mismatch_never_admits_the_download(tmp_path) -> None:
    installers = importlib.import_module('hardy.installers')

    def write_bad_archive(_url, target):
        target.write_bytes(b'not the pinned release')

    outcome = installers.install_tectonic(
        destination_root=tmp_path,
        confirmer=lambda _: True,
        downloader=write_bad_archive,
    )

    assert outcome.status == 'failed'
    assert 'checksum' in outcome.manual_instructions.lower()
    assert outcome.installed_path is None
    assert not (tmp_path / 'tectonic' / '0.16.9' / 'tectonic.exe').exists()
    assert list(tmp_path.rglob('*.zip')) == []


def test_verified_archive_extracts_only_the_expected_executable(
    tmp_path,
    monkeypatch,
) -> None:
    installers = importlib.import_module('hardy.installers')

    def write_archive(_url, target):
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('tectonic.exe', b'pinned executable')
            archive.writestr('unexpected.txt', b'do not extract')
        monkeypatch.setattr(
            installers,
            'TECTONIC_SHA256',
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )

    outcome = installers.install_tectonic(
        destination_root=tmp_path,
        confirmer=lambda _: True,
        downloader=write_archive,
    )

    assert outcome.status == 'installed'
    assert outcome.installed_path.read_bytes() == b'pinned executable'
    assert list(tmp_path.rglob('unexpected.txt')) == []


def test_elan_install_uses_a_fixed_user_scope_winget_command(tmp_path) -> None:
    installers = importlib.import_module('hardy.installers')
    process = importlib.import_module('hardy.process')
    winget = tmp_path / 'winget.exe'
    winget.write_bytes(b'fixture')
    calls = []

    def runner(spec):
        calls.append(spec)
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout='installed\n',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    outcome = installers.install_elan(
        winget=winget,
        cwd=tmp_path,
        confirmer=lambda _: True,
        runner=runner,
    )

    assert outcome.status == 'installed'
    assert calls[0].argv == (
        str(winget),
        'install',
        '--id',
        'Lean.Elan',
        '--version',
        '4.2.1',
        '--exact',
        '--scope',
        'user',
        '--disable-interactivity',
        '--accept-package-agreements',
        '--accept-source-agreements',
    )


def test_mathlib_setup_runs_only_checked_in_lake_commands(tmp_path) -> None:
    installers = importlib.import_module('hardy.installers')
    process = importlib.import_module('hardy.process')
    lake = tmp_path / 'lake.exe'
    lake.write_bytes(b'fixture')
    calls = []

    def runner(spec):
        calls.append(spec.argv)
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout='',
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    outcome = installers.prepare_mathlib(
        lake=lake,
        lean_project=tmp_path,
        confirmer=lambda _: True,
        runner=runner,
    )

    assert outcome.status == 'installed'
    assert calls == [
        (str(lake), 'update'),
        (str(lake), 'exe', 'cache', 'get'),
    ]


def test_downloader_streams_to_the_requested_staging_path(tmp_path) -> None:
    installers = importlib.import_module('hardy.installers')
    target = tmp_path / 'staged.bin'

    installers.download_file(
        'data:application/octet-stream;base64,cGlubmVk',
        target,
    )

    assert target.read_bytes() == b'pinned'
