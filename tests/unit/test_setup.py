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


def test_the_setup_probe_follows_the_configured_backend(monkeypatch) -> None:
    """`hardy setup` graded every machine on the Claude CLI.

    Its exit status is what a script reads, and it was answering a question
    about a transport the user may not have selected: an API-only machine with
    a good key was reported broken for lacking `claude`, and a Claude-configured
    machine passed without the key the `api` runtime needs and failed at its
    first request instead.
    """
    setup = importlib.import_module('hardy.setup')

    assert setup.backend_probe('claude') is setup.probe_claude
    assert setup.backend_probe('api') is setup.probe_api
    # An identity the config loader would have refused. Falling back to the
    # subscription probe is the conservative half of the pair: it can report a
    # machine unready, never a machine ready on credentials nobody has.
    assert setup.backend_probe('something-else') is setup.probe_claude


def test_the_api_probe_asks_for_the_sdk_and_the_key_and_prints_neither(monkeypatch) -> None:
    """Both halves, and the key itself in neither answer.

    `anthropic` is an optional extra and is not installed in this environment,
    so the installed case is arranged rather than assumed -- which also keeps
    the test honest about what it is checking: the probe's own logic, not this
    machine's package list.
    """
    import sys
    from types import SimpleNamespace

    setup = importlib.import_module('hardy.setup')
    monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(__version__='0.40.0'))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-not-a-real-key')

    ok, detail = setup.probe_api()

    assert ok is True
    assert 'anthropic 0.40.0' in detail
    # A setup report is pasted into issues. The key is asked about, never shown.
    assert 'sk-ant-not-a-real-key' not in detail

    monkeypatch.delenv('ANTHROPIC_API_KEY')
    missing, why = setup.probe_api()
    assert missing is False
    assert 'ANTHROPIC_API_KEY' in why
    assert 'sk-ant' not in why


def test_the_api_probe_imports_the_sdk_rather_than_reading_its_metadata(monkeypatch) -> None:
    """Distribution metadata survives a partial installation and says nothing
    about whether the package will load: a missing runtime dependency leaves
    the version readable and the import broken. `hardy setup` reports its final
    health from this probe, so it would have called an unusable installation
    ready and left the failure for the first provider turn."""
    import builtins

    setup = importlib.import_module('hardy.setup')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-not-a-real-key')
    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == 'anthropic':
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', broken)

    ok, detail = setup.probe_api()

    assert ok is False
    assert 'not importable' in detail
    assert 'httpx' in detail


def test_the_api_probe_reports_an_absent_sdk_rather_than_raising() -> None:
    setup = importlib.import_module('hardy.setup')
    try:
        import anthropic  # noqa: F401
    except ImportError:
        pass
    else:  # pragma: no cover - only when the optional extra is installed here
        import pytest

        pytest.skip('anthropic is installed in this environment')

    ok, detail = setup.probe_api()

    assert ok is False
    assert 'not importable' in detail
