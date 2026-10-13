"""Record the native console-close event, without treating SIGBREAK as equivalent."""

import ctypes
import json
import signal
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)


def write(name, value):
    (root / name).write_text(json.dumps(value), encoding="utf-8")


def control(event):
    write("native-event.json", {"event": event})
    # Observe the event, then allow Windows' normal termination. Keeping a
    # callback alive does not imply Python's main-thread finally can run.
    return False


callback = callback_type(control)
kernel32.SetConsoleCtrlHandler.argtypes = [callback_type, ctypes.c_bool]
if not kernel32.SetConsoleCtrlHandler(callback, True):
    raise ctypes.WinError(ctypes.get_last_error())
signal.signal(signal.SIGBREAK, lambda *_: write("python-sigbreak.json", True))
write("ready.json", {"hwnd": kernel32.GetConsoleWindow()})
try:
    while True:
        time.sleep(0.05)
finally:
    write("finally.json", True)
