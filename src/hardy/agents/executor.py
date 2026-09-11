"""Physical worker slots below delegation semantics.

A `WorkerExecutor` runs opaque jobs on a bounded number of slots and hands
each one a cooperative `CancelToken`. It knows nothing about mathematics,
budgets, or findings: the orchestration above decides what to run; this
module decides only which of those runs now and how one is asked to stop.

Local threads are orchestration, not process isolation, exactly as the race
strategy states. A cluster executor would implement the same protocol.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol


class WorkerCancelled(Exception):
    """This job was asked to stop; no other job is affected."""


class CancelToken:
    """A flag a job reads, plus callbacks that reach what the job is waiting on."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise WorkerCancelled

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Run `callback` when cancelled; immediately if that already happened."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()


@dataclass(frozen=True)
class WorkerJob:
    name: str
    run: Callable[[CancelToken], Any]


class WorkerHandle(Protocol):
    name: str
    token: CancelToken

    def done(self) -> bool: ...
    def result(self, timeout: float | None = None) -> Any: ...
    def cancel(self) -> None: ...
    def add_done_callback(self, fn: Callable[[WorkerHandle], None]) -> None: ...


class WorkerExecutor(Protocol):
    slots: int

    def submit(self, job: WorkerJob) -> WorkerHandle: ...
    def active(self) -> int: ...
    def shutdown(self, *, wait: bool) -> None: ...


class _LocalHandle:
    def __init__(self, name: str, token: CancelToken, future: Future) -> None:
        self.name = name
        self.token = token
        self._future = future

    def done(self) -> bool:
        return self._future.done()

    def result(self, timeout: float | None = None) -> Any:
        return self._future.result(timeout)

    def cancel(self) -> None:
        self.token.cancel()

    def add_done_callback(self, fn: Callable[[WorkerHandle], None]) -> None:
        self._future.add_done_callback(lambda _: fn(self))


class LocalExecutor:
    """Threads in one pool; jobs beyond `slots` wait their turn in the pool's queue."""

    def __init__(self, slots: int, *, thread_name_prefix: str = "hardy-delegation") -> None:
        if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
            raise ValueError("an executor needs at least one worker slot")
        self.slots = slots
        self._pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix=thread_name_prefix)
        self._running = 0
        self._lock = threading.Lock()

    def submit(self, job: WorkerJob) -> WorkerHandle:
        token = CancelToken()

        def run() -> Any:
            # A job cancelled while it waited for a slot never starts.
            token.check()
            with self._lock:
                self._running += 1
            try:
                return job.run(token)
            finally:
                with self._lock:
                    self._running -= 1

        return _LocalHandle(job.name, token, self._pool.submit(run))

    def active(self) -> int:
        with self._lock:
            return self._running

    def shutdown(self, *, wait: bool) -> None:
        self._pool.shutdown(wait=wait)
