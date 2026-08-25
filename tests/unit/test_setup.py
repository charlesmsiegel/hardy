import importlib
from pathlib import Path
from types import SimpleNamespace


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fixture')
    return path


def test_executable_discovery_prefers_explicit_then_path_then_common(tmp_path) -> None:
    setup = importlib.import_module('hardy.setup')
    explicit = _touch(tmp_path / 'explicit' / 'tool.exe')
    on_path = _touch(tmp_path / 'path' / 'tool.exe')
    common = _touch(tmp_path / 'common' / 'tool.exe')

    assert setup.resolve_executable(explicit, lambda _: str(on_path), (common,)) == explicit
    assert setup.resolve_executable(None, lambda _: str(on_path), (common,)) == on_path
    assert setup.resolve_executable(None, lambda _: None, (common,)) == common


def test_environment_report_requires_successful_smoke_tests(tmp_path) -> None:
    config_module = importlib.import_module('hardy.config')
    process = importlib.import_module('hardy.process')
    setup = importlib.import_module('hardy.setup')
    elan = _touch(tmp_path / 'bin' / 'elan.exe')
    lake = _touch(tmp_path / 'bin' / 'lake.exe')
    tectonic = _touch(tmp_path / 'bin' / 'tectonic.exe')
    lean_project = tmp_path / 'lean_project'
    lean_project.mkdir()
    calls = []

    def runner(spec):
        calls.append(spec.argv)
        if spec.argv[-1] == '--version' and spec.argv[0] == str(elan):
            output = 'elan 4.2.1\n'
        elif spec.argv[-1] == '--version' and spec.argv[0] == str(tectonic):
            output = 'tectonic 0.16.9\n'
        elif spec.argv[-1] == '--version':
            output = 'Lean (version 4.32.0, commit 8c9756b)\n'
        else:
            output = ''
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=0,
            stdout=output,
            stderr='',
            timed_out=False,
            output_overflow=False,
            duration_ms=1,
        )

    report = setup.discover_environment(
        config_module.Config(
            model='test-model',
            lean_command=('lake', 'env', 'lean'),
            lean_project=lean_project,
            lean_timeout=30.0,
            latex_command=('tectonic',),
            root=lean_project,
            project='workspace',
            elan=elan,
            lake=lake,
            tectonic=tectonic,
        ),
        runner=runner,
        backend_probe=lambda: (True, 'openai-codex 0.144.4'),
        which=lambda _: None,
        common_locations={},
    )

    statuses = {status.name: status for status in report.tools}
    assert report.healthy
    assert report.authenticated
    assert report.mathlib_ready
    assert statuses['lean'].version.startswith('Lean (version 4.32.0')
    assert any(call[1:4] == ('env', 'lean', '--version') for call in calls)


def test_codex_probe_retains_only_auth_state_and_sdk_version() -> None:
    setup = importlib.import_module('hardy.setup')
    closed = []

    class FakeCodex:
        def account(self, *, refresh_token):
            assert not refresh_token
            return SimpleNamespace(
                account=SimpleNamespace(email='must-not-be-returned@example.test')
            )

        def close(self):
            closed.append(True)

    authenticated, version = setup.probe_codex(
        client_factory=FakeCodex,
        sdk_version='0.144.4',
    )

    assert authenticated
    assert version == 'openai-codex 0.144.4'
    assert closed == [True]


def test_codex_login_does_not_wait_before_user_confirmation() -> None:
    setup = importlib.import_module('hardy.setup')
    waited = []

    class Login:
        auth_url = 'https://example.test/device'

        def wait(self):
            waited.append(True)
            return SimpleNamespace(success=True)

    class FakeCodex:
        def login_chatgpt(self):
            return Login()

        def close(self):
            pass

    success = setup.ensure_codex_login(
        confirmer=lambda prompt: 'https://example.test/device' in prompt and False,
        client_factory=FakeCodex,
    )

    assert not success
    assert waited == []


def test_confirmed_codex_login_waits_for_sdk_success() -> None:
    setup = importlib.import_module('hardy.setup')
    waited = []

    class Login:
        auth_url = 'https://example.test/device'

        def wait(self):
            waited.append(True)
            return SimpleNamespace(success=True)

    class FakeCodex:
        def login_chatgpt(self):
            return Login()

        def close(self):
            pass

    success = setup.ensure_codex_login(
        confirmer=lambda _: True,
        client_factory=FakeCodex,
    )

    assert success
    assert waited == [True]
