"""Application acceptance: real persisted turn and CAS child, no paid provider.

WM_CLOSE covers Windows Console Host, not Windows Terminal tab closure.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest


def _alive(pid):
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            return kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("ui", ["web", "tui"])
@pytest.mark.parametrize("kernel_mode", ["idle", "active"])
def test_console_close_abandons_turn_and_reaps_kernel(tmp_path, ui, kernel_mode):
    if os.name == "nt" and os.environ.get("HARDY_NATIVE_CONSOLE_CLOSE") != "1":
        pytest.skip("set HARDY_NATIVE_CONSOLE_CLOSE=1 for real Windows desktop WM_CLOSE")
    if os.name != "nt" and not hasattr(signal, "SIGHUP"):
        pytest.skip("requires native Windows console or POSIX SIGHUP")
    helper = Path(__file__).parents[1] / "fixtures" / "console" / "shutdown_session.py"
    command = [sys.executable, str(helper), str(tmp_path), ui, kernel_mode]
    if os.name == "nt":
        # A Windows venv python.exe redirects to another process. Launch the
        # real interpreter so Popen's handle/PID owns the UI being closed.
        # Add this venv's packages explicitly, including for the real CAS
        # interpreter it launches; .pth processing retains editable installs.
        bootstrap = (
            "import runpy,site,sys; from pathlib import Path; "
            "packages=sys.argv.pop(1); site.addsitedir(packages); "
            "sys._hardy_console_test_packages=packages; "
            "sys.argv=sys.argv[1:]; sys.path.insert(0,str(Path(sys.argv[0]).parent)); "
            "runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        command = [sys._base_executable, "-c", bootstrap, sysconfig.get_paths()["purelib"],
                   str(helper), str(tmp_path), ui, kernel_mode]
    kernel_pid = None
    with (tmp_path / "child-output.txt").open("w", encoding="utf-8") as output:
        child = subprocess.Popen(
            command,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            ready = tmp_path / "ready.json"
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline:
                assert child.poll() is None, (tmp_path / "child-output.txt").read_text(encoding="utf-8")
                time.sleep(0.05)
            assert ready.exists(), (tmp_path / "child-output.txt").read_text(encoding="utf-8")
            identity = json.loads(ready.read_text(encoding="utf-8"))
            print(f"application console identity: {identity}", flush=True)
            assert identity["pid"] == child.pid
            assert identity["kernel_mode"] == kernel_mode
            if kernel_mode == "active":
                assert (tmp_path / "active-cell.txt").read_text() == "running"
            kernel_pid = identity["kernel_pid"]
            assert _alive(kernel_pid), "fixture must own a real live CAS process"
            transcript = Path(identity["transcript"])
            before = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
            assert any(event["type"] == "user" for event in before)
            assert not any(event.get("status") == "abandoned" for event in before)
            started = time.monotonic()
            if os.name == "nt":
                assert identity["hwnd"]
                assert identity["window_class"] == "ConsoleWindowClass", identity
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                               ctypes.c_size_t, ctypes.c_ssize_t]
                user32.PostMessageW.restype = ctypes.c_bool
                assert user32.PostMessageW(identity["hwnd"], 0x0010, 0, 0), ctypes.get_last_error()
            else:
                os.kill(child.pid, signal.SIGHUP)
            child.wait(timeout=8)
            elapsed = time.monotonic() - started
            events = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
            abandoned = [event for event in events if event.get("status") == "abandoned"]
            print(f"application close evidence: exit={child.returncode}, seconds={elapsed}, "
                  f"abandoned={abandoned}, kernel_alive={_alive(kernel_pid)}", flush=True)
            assert len(abandoned) == 1
            assert abandoned[0]["reason"] == ("console_closed" if os.name == "nt" else "terminal_closed")
            assert not _alive(kernel_pid), "CAS process survived its console owner"
            if os.name != "nt":
                assert child.returncode == 129
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            if kernel_pid and _alive(kernel_pid):
                os.kill(kernel_pid, signal.SIGTERM)
