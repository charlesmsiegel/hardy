import importlib
import sys
import threading
import time
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


def _interrupt_once_running(process, deadline: float = 5.0) -> None:
    """Ask, from another thread, as Esc does. Waits for a child to exist first.

    The register is what `interrupt_children` reads, so polling it rather than
    sleeping a fixed amount is what keeps this from racing the spawn on a slow
    machine -- and from passing vacuously by interrupting nothing at all.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if process.interrupt_children():
            return
        time.sleep(0.01)
    raise AssertionError('no child was ever registered to interrupt')


def test_an_interrupt_stops_a_child_long_before_its_timeout(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--sleep', '30'),
        cwd=tmp_path,
        timeout_seconds=30,
        max_output_bytes=4_096,
    )
    threading.Thread(target=_interrupt_once_running, args=(process,), daemon=True).start()

    result = process.run_process(spec)

    assert result.interrupted
    assert not result.timed_out
    # Stopped by Hardy, so no exit status is reported as if the child had
    # chosen it -- `-SIGINT` is not a verdict about the work.
    assert result.returncode is None
    assert result.duration_ms < 10_000


def test_a_child_that_ignores_the_interrupt_is_still_stopped(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    ready = tmp_path / 'deaf'
    spec = process.ProcessSpec(
        argv=(
            sys.executable,
            str(EMITTER),
            '--ignore-interrupt',
            '--ready',
            str(ready),
            '--sleep-after',
            '60',
        ),
        cwd=tmp_path,
        timeout_seconds=60,
        max_output_bytes=4_096,
    )

    def interrupt_once_it_is_deaf() -> None:
        # Waiting for the handler to be *installed* is the whole point. An
        # interrupt delivered during interpreter startup kills the child
        # outright, and the escalation this test exists for never runs.
        end = time.monotonic() + 10
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        _interrupt_once_running(process)

    threading.Thread(target=interrupt_once_it_is_deaf, daemon=True).start()

    result = process.run_process(spec)

    # The grace runs out and the child is terminated, rather than the interrupt
    # being a request Hardy has no answer to when it is declined.
    assert result.interrupted
    assert not result.timed_out
    assert result.returncode is None
    # It really waited out the grace before escalating, and really did not wait
    # out the 60s timeout.
    assert result.duration_ms >= 1_500
    assert result.duration_ms < 30_000


def test_output_survives_an_interrupt(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    ready = tmp_path / 'said-it'
    spec = process.ProcessSpec(
        argv=(
            sys.executable,
            str(EMITTER),
            '--stdout',
            'partial',
            '--ready',
            str(ready),
            '--sleep-after',
            '30',
        ),
        cwd=tmp_path,
        timeout_seconds=30,
        max_output_bytes=4_096,
    )

    def interrupt_after_it_speaks() -> None:
        end = time.monotonic() + 10
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        _interrupt_once_running(process)

    threading.Thread(target=interrupt_after_it_speaks, daemon=True).start()

    result = process.run_process(spec)

    assert result.interrupted
    # What the child managed to say before it was stopped is still evidence.
    assert result.stdout == 'partial\n'


def test_a_run_nobody_stopped_is_not_reported_as_interrupted(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--stdout', 'done'),
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=4_096,
    )

    result = process.run_process(spec)

    assert not result.interrupted
    assert result.returncode == 0


def test_the_register_empties_when_a_run_finishes(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--stdout', 'done'),
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=4_096,
    )

    process.run_process(spec)

    # A stale entry would mean the next Esc signalled a pid nobody owns any
    # more, which on a recycled pid is worse than doing nothing.
    assert process.interrupt_children() == 0


def test_stop_children_terminates_a_child_that_refused_the_interrupt(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    ready = tmp_path / 'deaf'
    spec = process.ProcessSpec(
        argv=(
            sys.executable,
            str(EMITTER),
            '--ignore-interrupt',
            '--ready',
            str(ready),
            '--sleep-after',
            '60',
        ),
        cwd=tmp_path,
        timeout_seconds=60,
        max_output_bytes=4_096,
    )

    def press_twice() -> None:
        end = time.monotonic() + 10
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        _interrupt_once_running(process)
        # The second press, without waiting out the grace the first started.
        assert process.stop_children() == 1

    threading.Thread(target=press_twice, daemon=True).start()

    result = process.run_process(spec)

    # Still reported as a run Hardy stopped, not as a child that exited by
    # itself -- escalating changes how long it waits, not what happened.
    assert result.interrupted
    assert not result.timed_out
    assert result.returncode is None
    assert result.duration_ms < 1_500
