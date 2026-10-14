"""Deadlines and stops remain effective after a process leader exits."""
import contextlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from hardy.algebra.kernel import _Kernel
from hardy.foundation import process


def test_guarded_child_cannot_consume_the_hosts_stdin(tmp_path):
    source = (
        'import sys\n'
        'from hardy.foundation.process import run_guarded\n'
        'r = run_guarded([sys.executable, "-c", '
        '"import sys; print(repr(sys.stdin.read()))"], timeout=5)\n'
        'print(r.stdout.strip())\n'
    )
    result = subprocess.run(
        [sys.executable, '-c', source], input='Hardy input\n',
        text=True, capture_output=True, timeout=10, check=True,
    )
    assert result.stdout.strip() == "''"


@pytest.mark.parametrize('stop', ['deadline', 'interrupt', 'escalate'])
def test_a_leader_exit_does_not_end_the_descendants_budget(tmp_path, monkeypatch, stop):
    ready = tmp_path / 'ready'
    descendant = (
        'import os,signal,time,sys\n'
        'signal.signal(signal.SIGINT, signal.SIG_IGN)\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        f'open({str(ready)!r}, "w").write(str(os.getpid()))\n'
        'print("observed output", flush=True)\n'
        'time.sleep(30)\n'
    )
    contained = tmp_path / 'contained'
    leader = (
        'import subprocess,sys,time,pathlib\n'
        f'while not pathlib.Path({str(contained)!r}).exists(): time.sleep(.005)\n'
        f'subprocess.Popen([sys.executable,"-c",{descendant!r}])'
    )
    original_tracked = process.tracked
    @contextlib.contextmanager
    def tracked(child):
        with original_tracked(child) as entry:
            contained.write_text('ready')
            yield entry
    monkeypatch.setattr(process, 'tracked', tracked)
    presses = []
    def press():
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            time.sleep(.01)
        # Observe the reaped leader rather than using a guessed delay.
        while time.monotonic() < until:
            with process._RUNNING_LOCK:
                entries = list(process._RUNNING)
            if entries and entries[0].child.poll() is not None:
                presses.append(process.interrupt_children())
                if stop == 'escalate':
                    process.stop_children()
                return
            time.sleep(.01)
    worker = threading.Thread(target=press) if stop != 'deadline' else None
    if worker:
        worker.start()
    try:
        result = process.run_process(process.ProcessSpec(
            argv=(sys.executable, '-c', leader), cwd=tmp_path,
            timeout_seconds=1.0 if stop == 'deadline' else 10.0,
            max_output_bytes=4096,
        ))
        if worker:
            worker.join(timeout=6)
        assert result.returncode is None
        assert result.timed_out == (stop == 'deadline')
        assert result.interrupted == (stop != 'deadline')
        assert 'observed output' in result.stdout
        if worker:
            assert presses == [1]
        _assert_child_stopped(int(ready.read_text()))
    finally:
        if ready.exists():
            with contextlib.suppress(OSError):
                os.kill(int(ready.read_text()), 9)
        if worker:
            worker.join(timeout=6)


@pytest.mark.skipif(os.name != 'posix', reason='setsid escape requires POSIX')
def test_kernel_kill_does_not_wait_for_an_escaped_pipe_holder(tmp_path):
    ready = tmp_path / 'helper'
    helper = f'import os,time; open({str(ready)!r},"w").write(str(os.getpid())); time.sleep(30)'
    source = (
        'import subprocess,sys\n'
        f'subprocess.Popen([sys.executable,"-c",{helper!r}], start_new_session=True)\n'
        'print("ready", flush=True)\n'
        'sys.stdin.readline()\n'
    )
    kernel = _Kernel([sys.executable, '-c', source], tmp_path, 4096, merge_stderr=True)
    until = time.monotonic() + 5
    while (not ready.exists() or 'ready' not in kernel.stdout_text()) and time.monotonic() < until:
        time.sleep(.01)
    done = threading.Event()
    worker = threading.Thread(target=lambda: (kernel.kill(), done.set()), daemon=True)
    try:
        assert ready.exists()
        worker.start()
        assert done.wait(1), 'kill waited on a pipe whose reader still owns it'
        assert kernel.process.poll() is not None
        os.kill(int(ready.read_text()), 0)  # The escaped holder is still alive.
    finally:
        if ready.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(ready.read_text()), 9)
        worker.join(timeout=3)
        kernel.kill(immediate=True)


def _assert_child_stopped(pid):
    if os.name == 'nt':
        import ctypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        api.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            try:
                assert api.WaitForSingleObject(handle, 1000) == 0
            finally:
                api.CloseHandle(handle)
    else:
        status = Path(f'/proc/{pid}/status')
        until = time.monotonic() + 1
        while status.exists() and time.monotonic() < until:
            try:
                if 'State:\tZ' in status.read_text(encoding='utf-8'):
                    return
            except FileNotFoundError:
                return
            time.sleep(.01)
        assert not status.exists(), 'the descendant survived the group kill'


def test_kernel_kill_stops_group_members_even_when_the_leader_obeys(tmp_path):
    ready = tmp_path / 'member'
    helper = (
        'import os,signal,time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        f'open({str(ready)!r}, "w").write(str(os.getpid()))\n'
        'time.sleep(30)\n'
    )
    contained = tmp_path / 'contained'
    source = (
        'import subprocess,sys,time,pathlib\n'
        f'print("waiting for containment", {str(contained)!r}, flush=True)\n'
        f'while not pathlib.Path({str(contained)!r}).exists(): time.sleep(.005)\n'
        'print("containment ready", flush=True)\n'
        # The helper never reads input. Give it its own stream while the parent
        # waits on the kernel's input, rather than sharing that Windows handle.
        f'helper = subprocess.Popen([sys.executable,"-c",{helper!r}], stdin=subprocess.DEVNULL)\n'
        'print("helper spawned", helper.pid, flush=True)\n'
        'sys.stdin.readline()\n'
    )
    kernel = _Kernel([sys.executable, '-c', source], tmp_path, 4096, merge_stderr=True)
    contained.write_text('ready')
    until = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < until:
        time.sleep(.01)
    try:
        assert ready.exists(), (kernel.process.poll(), kernel.stdout_text(), kernel.stderr_text())
        kernel.kill()
        assert kernel.process.poll() is not None
        _assert_child_stopped(int(ready.read_text()))
    finally:
        if ready.exists():
            with contextlib.suppress(OSError):
                os.kill(int(ready.read_text()), 9)
        kernel.kill(immediate=True)


@pytest.mark.skipif(os.name != 'posix', reason='setsid escape requires POSIX')
@pytest.mark.parametrize('operation', ['reset', 'escalate'])
def test_sentinel_stop_is_bounded_with_an_escaped_pipe_holder(tmp_path, sentinel_session, monkeypatch, operation):
    ready = tmp_path / 'escaped'
    helper = f'import os,time; open({str(ready)!r},"w").write(str(os.getpid())); time.sleep(30)'
    source = (
        'import subprocess,sys,signal,time\n'
        'signal.signal(signal.SIGINT, signal.SIG_IGN)\n'
        'for line in sys.stdin:\n'
        '    if "hardy-" in line:\n'
        '        print(line.split(chr(34))[1], flush=True)\n'
        '    elif line.strip() == "work;":\n'
        f'        subprocess.Popen([sys.executable,"-c",{helper!r}], start_new_session=True)\n'
        + ('        time.sleep(30)\n' if operation == 'escalate' else '')
    )
    session = sentinel_session(cas_cell_seconds=30)
    monkeypatch.setattr(session.backend, 'argv', lambda *_args: (sys.executable, '-u', '-c', source))
    records = []
    running = threading.Thread(target=lambda: records.append(session.execute('work;')), daemon=True)
    running.start()
    until = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < until:
        time.sleep(.01)
    done = threading.Event()
    operation_thread = None
    try:
        assert ready.exists()
        if operation == 'reset':
            running.join(timeout=3)
            assert records and records[0].accepted
            action = session.reset
        else:
            assert session.interrupt()
            action = session.escalate
        operation_thread = threading.Thread(target=lambda: (action(), done.set()), daemon=True)
        operation_thread.start()
        assert done.wait(1), f'{operation} waited on the escaped holders pipe'
        running.join(timeout=3)
        if operation == 'escalate':
            assert records[0].status == 'interrupted'
            assert records[0].kernel_lost is True
    finally:
        if ready.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(ready.read_text()), 9)
        if operation_thread:
            operation_thread.join(timeout=3)
        session.close()


@pytest.mark.skipif(os.name != 'posix', reason='setsid escape requires POSIX')
def test_kernel_kill_does_not_close_stdin_owned_by_a_blocked_writer(tmp_path):
    ready = tmp_path / 'input-holder'
    helper = f'import os,time; open({str(ready)!r},"w").write(str(os.getpid())); time.sleep(30)'
    source = (
        'import subprocess,sys,time\n'
        f'subprocess.Popen([sys.executable,"-c",{helper!r}], stdin=sys.stdin, start_new_session=True)\n'
        'time.sleep(30)\n'
    )
    kernel = _Kernel([sys.executable, '-c', source], tmp_path, 4096, merge_stderr=True)
    until = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < until:
        time.sleep(.01)
    entered = threading.Event()
    real_stdin = kernel.process.stdin
    class Input:
        def write(self, payload):
            entered.set()
            return real_stdin.write(payload)
        def flush(self):
            return real_stdin.flush()
        def close(self):
            return real_stdin.close()
    kernel.process.stdin = Input()
    writer = threading.Thread(target=lambda: kernel.send(b'x' * 1_000_000), daemon=True)
    done = threading.Event()
    killer = threading.Thread(target=lambda: (kernel.kill(immediate=True), done.set()), daemon=True)
    try:
        assert ready.exists()
        writer.start()
        assert entered.wait(1)
        killer.start()
        assert done.wait(1), 'kill waited for stdin owned by the active sender'
        assert kernel.process.poll() is not None
        writer.join(timeout=1)
        assert not writer.is_alive(), 'a killed kernels sender still holds the session caller'
    finally:
        if ready.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(ready.read_text()), 9)
        writer.join(timeout=3)
        killer.join(timeout=3)
        kernel.kill(immediate=True)


def test_input_delivery_does_not_assume_a_leader_exit_ended_its_descendant(tmp_path):
    helper = 'import sys,time; time.sleep(.3); sys.stdin.buffer.read(1000000)'
    source = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{helper!r}], stdin=sys.stdin)'
    kernel = _Kernel([sys.executable, '-c', source], tmp_path, 4096, merge_stderr=True)
    try:
        assert kernel.send(b'x' * 1_000_000)
    finally:
        kernel.kill(immediate=True)


def test_windows_close_waits_for_job_settlement(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hardy.algebra import kernel as kernel_module

    kernel = _Kernel([sys.executable, '-c', 'import sys; sys.stdin.readline()'],
                     tmp_path, 4096, merge_stderr=True)
    settled = threading.Event()
    queried = threading.Event()
    done = threading.Event()

    def members(child):
        assert child is kernel.process
        queried.set()
        return not settled.is_set()

    monkeypatch.setattr(kernel_module, 'os', SimpleNamespace(name='nt'), raising=False)
    monkeypatch.setattr(kernel_module, 'tree_has_members', members, raising=False)
    closer = threading.Thread(target=lambda: (kernel.kill(), done.set()), daemon=True)
    try:
        closer.start()
        assert queried.wait(1), 'close did not check asynchronous job settlement'
        assert not done.is_set(), 'close returned while the job still had a member'
        settled.set()
        assert done.wait(2)
        assert all(not reader.is_alive() and pipe.closed for pipe, reader in kernel._readers)
    finally:
        settled.set()
        closer.join(timeout=3)
        kernel.kill(immediate=True)
