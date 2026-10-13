"""Cancellation of one CAS operation, through its currently active child."""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from hardy.algebra.contracts import CasError


class OperationControl:
    """Remember a stop between children; a second press escalates that child."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._level = 0
        self._active: tuple[Callable[[], bool], Callable[[], bool]] | None = None

    def check(self) -> None:
        with self._lock:
            stopped = self._level > 0
        if stopped:
            raise CasError("CAS operation cancelled")

    def stop(self) -> bool:
        with self._lock:
            self._level += 1
            active, level = self._active, self._level
        if active is not None:
            active[min(level, 2) - 1]()
        return True

    @contextmanager
    def bind(self, interrupt: Callable[[], bool], escalate: Callable[[], bool]) -> Iterator[None]:
        active = (interrupt, escalate)
        with self._lock:
            self._active = active
            level = self._level
        try:
            if level:
                active[min(level, 2) - 1]()
            yield
        finally:
            with self._lock:
                if self._active is active:
                    self._active = None
