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

#: Tries per provider call: the first, and three retries.
ATTEMPTS = 4
#: Statuses worth another attempt after backing off: rate limited, overloaded,
#: and the server errors a provider answers while briefly unavailable.
RETRIED_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
#: Statuses the provider answers before it does any work: rate limited (429)
#: and overloaded (529). Neither bills anything.
REJECTED_BEFORE_WORK = frozenset({429, 529})


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
        """Whether `error` is the transport saying the request ran out of time.

        By class name along the MRO rather than by importing the SDK: each SDK
        loads lazily, and a caller may have injected a client with none
        installed behind it. A timeout is still a timeout there.
        """
        return isinstance(error, TimeoutError) or any(cls.__name__ == "APITimeoutError" for cls in type(error).__mro__)

    @staticmethod
    def _rejected(error: BaseException) -> bool:
        """Whether a failed call was an explicit pre-execution rejection.

        By shape rather than by class, since the SDK is loaded lazily: an SDK
        status error carries the HTTP status and the response it came in.
        Every other failure -- a 5xx, a dropped connection, Hardy's own
        deadline -- may have happened after the provider started work, so it
        is not one of these (owner decision 4).
        """
        status = getattr(error, "status_code", None)
        return (isinstance(status, int) and not isinstance(status, bool) and status in REJECTED_BEFORE_WORK
                and getattr(error, "response", None) is not None)

    def complete(self, *, system: str, messages: Sequence[Message], specs: Sequence[dict[str, Any]],
                 timeout: float | None = None) -> ProviderTurn:
        """One model call within `timeout` seconds, retried while a retry can finish.

        Each attempt is admitted by the spend budget before it is sent and
        settled by what came back. The first attempt is quoted with the whole
        timeout and later ones with what is left of it; the request then goes
        out with what admission left. A transient failure is retried after
        backing off, unless the back-off alone would outlast the deadline.
        """
        started, provider_called = self._clock(), False
        request = self._request(system, messages, specs)
        for attempt in range(ATTEMPTS):
            quoted = timeout if attempt == 0 else self._remaining(timeout, started)
            if quoted is not None:
                request["timeout"] = quoted
            reservation = self._admit(request, provider_called=provider_called)
            remaining = self._remaining(timeout, started)
            if remaining is not None:
                if remaining <= 0:
                    self._settle(reservation, dict.fromkeys(COUNTERS, 0))
                    raise ProviderRequestTimeout(f"the provider request exceeded its {timeout:g}s budget before sending",
                        provider_called=provider_called)
                request["timeout"] = remaining
            provider_called = True
            try:
                reply, reported = self._attempt(request, remaining, reservation)
            except Exception as error:
                if self._timed_out(error):
                    bound = f"its {timeout:g}s budget" if timeout is not None else "the transport's own timeout"
                    raise TimeoutError(f"the provider request exceeded {bound}") from error
                delay = self._retry_delay(error, attempt, timeout, started)
                if delay is None:
                    raise
                self.observe({"type": "provider_retry", "status": getattr(error, "status_code", None),
                              "attempt": attempt + 1, "sleep": delay})
                self._sleep(delay)
                if timeout is not None and self._clock() - started >= timeout:
                    raise
                continue
            self._settle(reservation, reported)
            return self._turn(reply, reported)
        raise RuntimeError("provider retry budget exhausted")  # pragma: no cover

    def _remaining(self, timeout: float | None, started: float) -> float | None:
        return None if timeout is None else max(0.0, timeout - (self._clock() - started))

    def _admit(self, request: dict[str, Any], *, provider_called: bool) -> str | None:
        """Reserve one attempt's expected spend: its reservation, or None unbudgeted.

        A refusal says whether an earlier attempt of this call already sent.
        """
        if self.spend_budget is None:
            return None
        # The budget schema names its output cap max_tokens, including when
        # the wire protocol calls the identical cap max_completion_tokens.
        quote = request if "max_tokens" in request else {**request, "max_tokens": self.max_tokens}
        try:
            return self.spend_budget.reserve(quote)
        except SpendLimitReached as error:
            error.provider_called = provider_called
            raise

    def _settle(self, reservation: str | None, usage: dict[str, Any] | None) -> None:
        if reservation is not None:
            self.spend_budget.settle(reservation, usage)

    def _attempt(self, request: dict[str, Any], remaining: float | None,
                 reservation: str | None) -> tuple[Any, dict[str, Any] | None]:
        """Send one request and read its usage report.

        A failure settles its reservation before it propagates: at zero for an
        explicit pre-execution rejection, unknown for anything else, which may
        have happened after the provider started work.
        """
        try:
            reply = self._within(request, remaining)
            return reply, self._usage(reply)
        except BaseException as error:
            self._settle(reservation, dict.fromkeys(COUNTERS, 0) if self._rejected(error) else None)
            raise

    def _retry_delay(self, error: Exception, attempt: int, timeout: float | None, started: float) -> float | None:
        """How long to back off before the next attempt, or None when this failure is final.

        Final when the attempts are spent, when the failure is not a transient
        status or a dropped connection, or when the back-off -- the provider's
        `retry-after` where it is longer -- would reach the deadline.
        """
        status = getattr(error, "status_code", None)
        connection = any(cls.__name__ == "APIConnectionError" for cls in type(error).__mro__)
        if attempt == ATTEMPTS - 1 or not (status in RETRIED_STATUSES or connection):
            return None
        delay = float(2 ** attempt)
        headers = getattr(getattr(error, "response", None), "headers", {})
        with contextlib.suppress(ValueError, TypeError):
            delay = max(delay, float(headers.get("retry-after", 0)))
        remaining = self._remaining(timeout, started)
        return None if remaining is not None and delay >= remaining else delay
