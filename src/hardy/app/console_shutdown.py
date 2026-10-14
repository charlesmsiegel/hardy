"""Bounded emergency cleanup for a terminal that is being closed by its host.

Windows invokes the close handler on a native thread and terminates the
process when it returns. Python signal handlers and main-thread finally
blocks therefore cannot own this path. Ordinary Ctrl+C remains unchanged.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator


class ConsoleShutdown:
    """Run evidence, process termination, and cleanup within one close budget.

    Each phase gets a daemon thread: a stuck provider, filesystem, or owner
    must not prevent the next phase or consume Windows' whole close allowance.
    A timed-out evidence write is a failure, never proof of abandonment.
    """

    def __init__(self, abandon: Callable[[str], None], terminate: Callable[[], None],
                 close: Callable[[], None], *, timeout: float = 3.0):
        self.abandon, self.terminate, self.close = abandon, terminate, close
        self.timeout = timeout
        self.failures: list[str] = []
        self._lock = threading.Lock()
        self._started = False
        self._done = threading.Event()
        self._deadline = 0.0

    def _phase(self, name: str, operation: Callable[[], None], deadline: float) -> None:
        def run() -> None:
            try:
                operation()
            except Exception as error:  # noqa: BLE001 - later owners must still be reached
                self.failures.append(f"{name}: {type(error).__name__}: {error}")

        worker = threading.Thread(target=run, name=f"console-close-{name}", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            self.failures.append(f"{name}: close deadline exceeded")

    def _run(self, reason: str) -> None:
        try:
            started = self._deadline - self.timeout
            self._phase("abandonment", lambda: self.abandon(reason), started + self.timeout / 3)
            self._phase("termination", self.terminate, started + 2 * self.timeout / 3)
            self._phase("cleanup", self.close, self._deadline)
        finally:
            self._done.set()

    def notify(self, reason: str) -> None:
        """First close wins; repeated events share its deadline and cleanup."""
        with self._lock:
            if not self._started:
                self._started = True
                self._deadline = time.monotonic() + self.timeout
                threading.Thread(target=self._run, args=(reason,), daemon=True).start()
        self._done.wait(max(0.0, self._deadline - time.monotonic()))


@contextlib.contextmanager
def console_shutdown(abandon: Callable[[str], None], terminate: Callable[[], None],
                     close: Callable[[], None]) -> Iterator[ConsoleShutdown]:
    """Install only the close transport; restore registration on normal exit."""
    shutdown = ConsoleShutdown(abandon, terminate, close)
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def control(event: int) -> bool:
            if event == 2:  # CTRL_CLOSE_EVENT; never substitute CTRL_BREAK_EVENT
                shutdown.notify("console_closed")
            return False  # Windows completes normal close termination.

        callback = prototype(control)
        kernel.SetConsoleCtrlHandler.argtypes = [prototype, wintypes.BOOL]
        kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
        if not kernel.SetConsoleCtrlHandler(callback, True):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield shutdown
        finally:
            kernel.SetConsoleCtrlHandler(callback, False)
    elif hasattr(signal, "SIGHUP") and threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGHUP)

        def hangup(_number, _frame) -> None:
            shutdown.notify("terminal_closed")
            os._exit(129)  # A blocked non-daemon provider cannot keep a closed terminal alive.

        signal.signal(signal.SIGHUP, hangup)
        try:
            yield shutdown
        finally:
            signal.signal(signal.SIGHUP, previous)
    else:
        yield shutdown
