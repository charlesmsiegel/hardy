import importlib
import sys
from pathlib import Path

EMITTER = Path(__file__).parents[1] / 'fixtures' / 'process' / 'emit.py'


def test_process_captures_stdout_and_stderr_separately(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(
            sys.executable,
            str(EMITTER),
            '--stdout',
            'hello',
            '--stderr',
            'problem',
        ),
        cwd=tmp_path,
        timeout_seconds=2,
        max_output_bytes=4_096,
    )

    result = process.run_process(spec)

    assert result.returncode == 0
    assert result.stdout == 'hello\n'
    assert result.stderr == 'problem\n'
    assert not result.timed_out
    assert not result.output_overflow


def test_timeout_returns_an_explicit_result(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--sleep', '5'),
        cwd=tmp_path,
        timeout_seconds=0.1,
        max_output_bytes=4_096,
    )

    result = process.run_process(spec)

    assert result.timed_out
    assert result.returncode is None
    assert result.duration_ms < 2_000


def test_output_overflow_terminates_the_process_early(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(
            sys.executable,
            str(EMITTER),
            '--bytes',
            '100000',
            '--sleep-after',
            '5',
        ),
        cwd=tmp_path,
        timeout_seconds=3,
        max_output_bytes=1_024,
    )

    result = process.run_process(spec)

    assert result.output_overflow
    assert not result.timed_out
    assert result.returncode is None
    assert len(result.stdout.encode('utf-8')) <= 1_024
    assert result.duration_ms < 2_000


def test_provider_credentials_are_not_inherited(
    tmp_path,
    monkeypatch,
) -> None:
    process = importlib.import_module('hardy.process')
    monkeypatch.setenv('OPENAI_API_KEY', 'do-not-forward')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--env', 'OPENAI_API_KEY'),
        cwd=tmp_path,
        timeout_seconds=2,
        max_output_bytes=4_096,
    )

    result = process.run_process(spec)

    assert result.returncode == 0
    assert result.stdout == 'OPENAI_API_KEY=<missing>\n'


def test_explicit_child_environment_values_are_available(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--env', 'HARDY_VISIBLE'),
        cwd=tmp_path,
        timeout_seconds=2,
        max_output_bytes=4_096,
        env={'HARDY_VISIBLE': 'yes'},
    )

    result = process.run_process(spec)

    assert result.returncode == 0
    assert result.stdout == 'HARDY_VISIBLE=yes\n'
