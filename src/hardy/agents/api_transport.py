"""Shared deadline, retry and spend admission for Hardy-owned API calls."""
from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from hardy.agents.contracts import ProviderRequestTimeout
from hardy.agents.loop import Message, ProviderTurn
from hardy.agents.spend_budget import COUNTERS, SpendBudget, SpendLimitReached


class ApiProvider:
    """Translate in subclasses; keep call admission and whole-request bounds here.

    A deadline abandons the waiting request, not its socket worker. The provider
    can still finish and bill it; an unsettled budget reservation is retained.
    """

    prompt_caching = False

    def __init__(self, model: str, *, client: Any | None = None, max_tokens: int = 8192,
                 spend_budget: SpendBudget | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 observe: Callable[[dict[str, Any]], None] | None = None):
        self.model, self.max_tokens, self._client = model, max_tokens, client
        self.spend_budget = spend_budget
        self._sleep, self._clock = sleep, clock
        self.observe = observe or (lambda event: None)

    def _within(self, request: dict[str, Any], timeout: float | None) -> Any:
        if timeout is None:
            return self._send(request)
        outcome: dict[str, Any] = {}
        def call():
            try:
                outcome["reply"] = self._send(request)
            except BaseException as error:  # noqa: BLE001 - re-raised on the caller's thread
                outcome["error"] = error
        worker = threading.Thread(target=call, name="hardy-provider", daemon=True)
        worker.start()
        worker.join(max(timeout, 0.0))
        if worker.is_alive():
            raise TimeoutError(f"the provider request exceeded its {timeout:g}s budget")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    @staticmethod
    def _timed_out(error: BaseException) -> bool:
        return isinstance(error, TimeoutError) or any(cls.__name__ == "APITimeoutError" for cls in type(error).__mro__)

    @staticmethod
    def _rejected(error: BaseException) -> bool:
        status = getattr(error, "status_code", None)
        return (isinstance(status, int) and not isinstance(status, bool) and status in {429, 529}
                and getattr(error, "response", None) is not None)

    def complete(self, *, system: str, messages: Sequence[Message], specs: Sequence[dict[str, Any]],
                 timeout: float | None = None) -> ProviderTurn:
        started, provider_called = self._clock(), False
        request = self._request(system, messages, specs)
        for attempt in range(4):
            remaining = timeout if attempt == 0 else (None if timeout is None else max(0.0, timeout - (self._clock() - started)))
            if remaining is not None:
                request["timeout"] = remaining
            # The budget schema names its output cap max_tokens, including when
            # the wire protocol calls the identical cap max_completion_tokens.
            quote = request if "max_tokens" in request else {**request, "max_tokens": self.max_tokens}
            try:
                reservation = self.spend_budget.reserve(quote) if self.spend_budget is not None else None
            except SpendLimitReached as error:
                error.provider_called = provider_called
                raise
            remaining = None if timeout is None else max(0.0, timeout - (self._clock() - started))
            if remaining is not None:
                if remaining <= 0:
                    if reservation is not None:
                        self.spend_budget.settle(reservation, dict.fromkeys(COUNTERS, 0))
                    raise ProviderRequestTimeout(f"the provider request exceeded its {timeout:g}s budget before sending",
                        provider_called=provider_called)
                request["timeout"] = remaining
            try:
                provider_called = True
                reply = self._within(request, remaining)
                reported = self._usage(reply)
            except BaseException as error:
                if reservation is not None:
                    self.spend_budget.settle(reservation, dict.fromkeys(COUNTERS, 0) if self._rejected(error) else None)
                if self._timed_out(error):
                    bound = f"its {timeout:g}s budget" if timeout is not None else "the transport's own timeout"
                    raise TimeoutError(f"the provider request exceeded {bound}") from error
                status = getattr(error, "status_code", None)
                connection = any(cls.__name__ == "APIConnectionError" for cls in type(error).__mro__)
                if attempt == 3 or not (status in {429, 500, 502, 503, 504, 529} or connection):
                    raise
                delay = float(2 ** attempt)
                headers = getattr(getattr(error, "response", None), "headers", {})
                with contextlib.suppress(ValueError, TypeError):
                    delay = max(delay, float(headers.get("retry-after", 0)))
                remaining = None if timeout is None else timeout - (self._clock() - started)
                if remaining is not None and delay >= remaining:
                    raise
                self.observe({"type": "provider_retry", "status": status, "attempt": attempt + 1, "sleep": delay})
                self._sleep(delay)
                if timeout is not None and self._clock() - started >= timeout:
                    raise
                continue
            if reservation is not None:
                self.spend_budget.settle(reservation, reported)
            return self._turn(reply, reported)
        raise RuntimeError("provider retry budget exhausted")  # pragma: no cover
