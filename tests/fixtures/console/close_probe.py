"""Record the native console-close event, without treating SIGBREAK as equivalent."""

import ctypes
import json
import signal
import sys
import time
from pathlib import Path


def write(root, name, value):
    target = root / name
    pending = target.with_name(target.name + ".tmp")
    pending.write_text(json.dumps(value), encoding="utf-8")
    pending.replace(target)


def main():
    root = Path(sys.argv[1])
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def control(event):
        write(root, "native-event.json", {"event": event})
        # Observe the event, then allow Windows' normal termination. Keeping a
        # callback alive does not imply Python's main-thread finally can run.
        return False

    callback = callback_type(control)
    kernel32.SetConsoleCtrlHandler.argtypes = [callback_type, ctypes.c_bool]
    if not kernel32.SetConsoleCtrlHandler(callback, True):
        raise ctypes.WinError(ctypes.get_last_error())
    signal.signal(signal.SIGBREAK, lambda *_: write(root, "python-sigbreak.json", True))
    hwnd = kernel32.GetConsoleWindow()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    window_class = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, window_class, len(window_class))
    session_id = ctypes.c_uint()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session_id))
    write(
        root,
        "ready.json",
        {"hwnd": hwnd, "window_class": window_class.value, "session_id": session_id.value},
    )
    try:
        while True:
            time.sleep(0.05)
    finally:
        write(root, "finally.json", True)


if __name__ == "__main__":
    main()
