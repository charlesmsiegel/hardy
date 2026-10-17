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
from typing import Generic, Protocol, TypeVar

#: What a job returns: opaque here, and handed back unread by its handle.
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)


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
        """Set the flag and run every callback once, even if some of them fail.

        The first failure is raised after all have run, carrying the others
        as notes: a failed interrupt must not keep the rest from being asked.
        """
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        errors: list[Exception] = []
        for callback in callbacks:
            try:
                callback()
            except Exception as error:  # noqa: BLE001 - a failed interrupt must not skip other cancellation callbacks
                errors.append(error)
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f"Additional cancellation callback failure: {type(error).__name__}: {error}")
                for note in getattr(error, "__notes__", ()):
                    errors[0].add_note(note)
            raise errors[0]


@dataclass(frozen=True)
class WorkerJob(Generic[R]):
    """One opaque job: a name to report it by, and the work, which reads its token."""

    name: str
    run: Callable[[CancelToken], R]


class WorkerHandle(Protocol[R_co]):
    """A submitted job: its token, its outcome once done, and a way to ask it to stop."""

    name: str
    token: CancelToken

    def done(self) -> bool: ...
    def result(self, timeout: float | None = None) -> R_co: ...
    def cancel(self) -> None: ...
    def add_done_callback(self, fn: Callable[[WorkerHandle[R_co]], None]) -> None: ...


class WorkerExecutor(Protocol):
    """Bounded worker slots that run submitted jobs, in process or elsewhere."""

    slots: int

    def submit(self, job: WorkerJob[R]) -> WorkerHandle[R]: ...
    def active(self) -> int: ...
    def shutdown(self, *, wait: bool) -> None: ...


class JobHandle(Generic[R]):
    """A `WorkerHandle` over a future: the pool's own jobs use it, and so does a
    computation that runs on a thread of its own and resolves the future itself."""

    def __init__(self, name: str, token: CancelToken, future: Future[R]) -> None:
        self.name = name
        self.token = token
        self._future = future

    def done(self) -> bool:
        return self._future.done()

    def result(self, timeout: float | None = None) -> R:
        return self._future.result(timeout)

    def cancel(self) -> None:
        self.token.cancel()

    def add_done_callback(self, fn: Callable[[WorkerHandle[R]], None]) -> None:
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

    def submit(self, job: WorkerJob[R]) -> WorkerHandle[R]:
        """Queue `job` for the next free slot; it never starts if cancelled first."""
        token = CancelToken()

        def run() -> R:
            # A job cancelled while it waited for a slot never starts.
            token.check()
            with self._lock:
                self._running += 1
            try:
                return job.run(token)
            finally:
                with self._lock:
                    self._running -= 1

        return JobHandle(job.name, token, self._pool.submit(run))

    def active(self) -> int:
        with self._lock:
            return self._running

    def shutdown(self, *, wait: bool) -> None:
        self._pool.shutdown(wait=wait)
