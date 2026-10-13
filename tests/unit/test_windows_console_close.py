"""Native transport investigation for #310: WM_CLOSE is not CTRL_BREAK_EVENT."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires a native Windows console")


def test_real_console_close_event(tmp_path):
    helper = Path(__file__).parents[1] / "fixtures" / "console" / "close_probe.py"
    child = subprocess.Popen(
        [sys.executable, str(helper), str(tmp_path)],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        ready = tmp_path / "ready.json"
        while not ready.exists() and time.monotonic() < deadline:
            assert child.poll() is None, "console probe exited before installing the native handler"
            time.sleep(0.05)
        assert ready.exists(), "native console probe did not become ready"
        hwnd = json.loads(ready.read_text(encoding="utf-8"))["hwnd"]
        assert hwnd, "Windows runner provides no console window; real close is unverified"
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW.restype = ctypes.c_bool
        assert user32.PostMessageW(hwnd, 0x0010, 0, 0), ctypes.get_last_error()  # WM_CLOSE
        timed_out = False
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            timed_out = True
        evidence = {
            "hwnd": hwnd,
            "visible": bool(user32.IsWindowVisible(ctypes.c_void_p(hwnd))),
            "exit": child.poll(),
            "timed_out": timed_out,
            "files": {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")},
        }
        print(f"native console transport diagnostics: {evidence}", flush=True)
        assert not timed_out, "WM_CLOSE accepted but the console process did not terminate; inspect native event evidence"
        observed = tmp_path / "native-event.json"
        assert observed.exists(), "WM_CLOSE did not deliver a console event on this Windows runner"
        event = json.loads(observed.read_text(encoding="utf-8"))
        print(f"native console close evidence: {event}; "
              f"SIGBREAK={(tmp_path / 'python-sigbreak.json').exists()}; "
              f"finally={(tmp_path / 'finally.json').exists()}; exit={child.returncode}")
        assert event["event"] == 2, "must observe CTRL_CLOSE_EVENT, never substitute CTRL_BREAK_EVENT"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
