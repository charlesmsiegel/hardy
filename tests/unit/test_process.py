import importlib
import subprocess
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


def _await_file(path, deadline: float = 10.0) -> None:
    """Wait for the child to say it has got where the test needs it."""
    end = time.monotonic() + deadline
    while time.monotonic() < end and not path.exists():
        time.sleep(0.01)
    assert path.exists(), f'the child never wrote {path}'


def _press_escape(process) -> int:
    """Press Esc once, from another thread, as the shell does.

    Deliberately called once and never polled. `interrupt_children` leaves the
    stop in force, so a child that registers a moment later is stopped on
    arrival and there is nothing to poll *for* -- while polling would re-arm
    the stop over and over, including long after this test is done, and kill
    the children of whatever runs next.
    """
    return process.interrupt_children()


def test_an_interrupt_stops_a_child_long_before_its_timeout(tmp_path) -> None:
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--sleep', '30'),
        cwd=tmp_path,
        timeout_seconds=30,
        max_output_bytes=4_096,
    )
    threading.Thread(target=_press_escape, args=(process,), daemon=True).start()

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
        _await_file(ready)
        assert _press_escape(process) == 1, 'the child was not registered'

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
        _await_file(ready)
        assert _press_escape(process) == 1, 'the child was not registered'

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
        _await_file(ready)
        assert _press_escape(process) == 1, 'the child was not registered'
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


def test_a_child_that_starts_after_the_press_is_stopped_too(tmp_path) -> None:
    """The window between the cancellation gate and the spawn.

    A tool call admitted a moment before Esc has already passed the check that
    would have refused it, and its subprocess is not registered yet -- so a
    one-time sweep of the register finds nothing, and the child then runs to
    its full timeout with the press already spent. The stop stays in force so
    it is caught on arrival instead.
    """
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--sleep', '30'),
        cwd=tmp_path,
        timeout_seconds=30,
        max_output_bytes=4_096,
    )

    # Pressed before the child exists at all, and reaching nothing.
    assert process.interrupt_children() == 0

    result = process.run_process(spec)

    assert result.interrupted
    assert not result.timed_out
    assert result.duration_ms < 10_000


def test_the_next_turn_is_allowed_to_run(tmp_path) -> None:
    """The other half: a stop that outlived the turn it belonged to would kill
    the next turn's first child on sight."""
    process = importlib.import_module('hardy.process')
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--stdout', 'done'),
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=4_096,
    )
    process.interrupt_children()

    process.resume_children()
    result = process.run_process(spec)

    assert not result.interrupted
    assert result.returncode == 0
    assert result.stdout == 'done\n'


def test_a_press_that_reaches_nothing_does_not_discard_a_finished_run(tmp_path) -> None:
    """A press landing after the child exited but before its registration is
    dropped used to mark the run interrupted anyway -- throwing away a Lean
    check that had passed, and reporting that it never finished."""
    process = importlib.import_module('hardy.process')
    done = tmp_path / 'finished'
    spec = process.ProcessSpec(
        argv=(sys.executable, str(EMITTER), '--stdout', 'done', '--ready', str(done)),
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=4_096,
    )
    entries: list = []

    # Registers a child by hand, exactly as `run_process` does, and lets it
    # finish before pressing -- which is the window the report describes.
    child = subprocess.Popen(
        (sys.executable, str(EMITTER), '--stdout', 'done'),
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process.child_creation(),
    )
    with process.tracked(child) as entry:
        child.communicate()
        entries.append(entry)
        assert process.interrupt_children() == 1

    assert not entries[0].interrupted.is_set(), 'a finished run was recorded as stopped'

    # And the ordinary path is unaffected.
    process.resume_children()
    assert process.run_process(spec).returncode == 0


def test_a_late_arrival_that_already_finished_keeps_its_result(tmp_path) -> None:
    """The stop stays in force so a child registering after the press is caught
    on arrival -- but a fast one that finished before it registered produced a
    real result, and marking that would discard a Lean check that passed."""
    process = importlib.import_module('hardy.process')
    process.interrupt_children()  # a stop already in force

    child = subprocess.Popen(
        (sys.executable, '-c', 'pass'),
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process.child_creation(),
    )
    child.communicate()  # over before it is registered
    with process.tracked(child) as entry:
        pass

    assert not entry.interrupted.is_set()


def test_run_guarded_stops_a_child_that_ignores_the_interrupt(tmp_path) -> None:
    """The shared ladder, used by LaTeX, the Lake probe, and doctor alike."""
    process = importlib.import_module('hardy.process')
    ready = tmp_path / 'deaf'

    def press_escape() -> None:
        _await_file(ready)
        assert _press_escape(process) == 1

    threading.Thread(target=press_escape, daemon=True).start()

    started = time.monotonic()
    outcome = process.run_guarded(
        (
            sys.executable,
            str(EMITTER),
            '--ignore-interrupt',
            '--ready',
            str(ready),
            '--sleep-after',
            '60',
        ),
        cwd=tmp_path,
        timeout=60,
    )
    elapsed = time.monotonic() - started

    assert outcome.interrupted
    assert not outcome.timed_out
    assert outcome.returncode is None
    # Grace, then the group's SIGTERM -- and nowhere near the 60s timeout.
    assert 1.5 <= elapsed < 30
