"""Fresh structured model calls through the session's budget-bound runtime factory.

Only named operations cross this boundary. Each call records the exact prompts,
schema, backend conditions and actual usage; a new context is not a claim about
independent weights or the correctness of a model judgment.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar
from uuid import uuid4

from hardy.agents.contracts import ChatRuntime, provenance
from hardy.agents.parsing import json_object
from hardy.agents.usage import Usage
from hardy.foundation.values import FrozenModel, ToolResult, schema_text
from hardy.workflows.interactive.live_records import ModelCallReceipt
from hardy.workflows.interactive.semantic_records import stamp

T = TypeVar("T", bound=FrozenModel)


@dataclass(frozen=True)
class ModelCallPorts:
    make_runtime: Callable[..., ChatRuntime]
    record: Callable[[dict[str, Any]], int]
    usage: Callable[[dict[str, Any], int], None]
    journal: Callable[[FrozenModel], None]
    dispatch: Callable[[str, dict[str, Any]], ToolResult]
    check_cancelled: Callable[[], None]
    model: Callable[[], str]
    configuration: Callable[[], dict[str, Any]]
    cwd: Path
    specs: list[dict[str, Any]]


@dataclass(frozen=True)
class StructuredResponse(Generic[T]):
    value: T
    receipt: ModelCallReceipt


class StructuredModelCalls:
    def __init__(self, ports: ModelCallPorts):
        self.ports = ports
        self.operation = "unselected"
        self._active: ChatRuntime | None = None
        self._calling = False
        self._pending_context: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _settled(runtime, timeout=0.0):
        settle = getattr(runtime, "settle", None)
        if settle is not None:
            return bool(settle(timeout))
        worker = getattr(runtime, "worker", None)
        return worker is None or not worker.is_alive()

    @property
    def busy(self) -> bool:
        with self._lock:
            if self._calling:
                return True
            if self._active is None:
                return False
            if not self._settled(self._active):
                return True
            context = self._pending_context
            self._active = None
            self._pending_context = None
        self.ports.record({"type": "workflow_model_settled", "context_id": context})
        return False

    def cancel(self) -> None:
        with self._lock:
            runtime = self._active
        if runtime is not None:
            runtime.cancel()

    def structured(self, stage: str, payload: dict[str, Any], schema: type[T], *,
                   system: str = "Return the requested JSON. Proposals grant no mathematical acceptance.",
                   tools: bool = False, model: str | None = None) -> StructuredResponse[T]:
        self.ports.check_cancelled()
        if self.busy:
            raise ValueError("A workflow provider worker is still running or tearing down")
        with self._lock:
            if self._calling or self._active is not None:
                raise ValueError("A workflow provider worker is still running or tearing down")
            self._calling = True
        identity = uuid4().hex
        context_id = f"workflow-context:{identity}"
        schema_source = schema_text(schema)
        prompt = json.dumps({"stage": stage, "input": payload, "response_schema": json.loads(schema_source)},
                            ensure_ascii=False, sort_keys=True)
        specs = self.ports.specs if tools else []
        started = time.monotonic()
        usage = Usage()
        reported = False
        accounting = threading.Lock()
        response = ""
        provider_error = ""
        accepting = threading.Event()
        accepting.set()
        runtime = None
        details: dict[str, Any] = {"model": model or self.ports.model(), "backend": None, "endpoint": None}
        self.ports.record({"type": "workflow_model_request", "id": identity, "operation": self.operation,
                           "stage": stage, "context_id": context_id, "system_prompt": system,
                           "user_prompt": prompt, "schema": schema_source})

        def observe(event: dict[str, Any]) -> None:
            nonlocal usage, reported, provider_error
            # Every runtime here starts without a resumed context. Unknown native
            # thread IDs therefore cannot borrow another call's usage baseline.
            event = {**event, "workflow_call": identity, "workflow_context": context_id}
            if event.get("type") == "result":
                with accounting:
                    if event.get("provider_unasked") or event.get("is_error"):
                        provider_error = str(event.get("error") or event.get("result") or "Provider unavailable")
                    event = {**event, "cumulative": False}
                    usage = usage.record(event)
                    reported = True
                    offset = self.ports.record(event)
                    self.ports.usage(event, offset)
            else:
                self.ports.record({"type": "workflow_runtime", "workflow_call": identity, "event": event})

        status = "failed"
        detail = ""
        def dispatch(name, arguments):
            if not accepting.is_set():
                return ToolResult(False, "This workflow call has ended; stale tool request refused")
            self.ports.check_cancelled()
            return self.ports.dispatch(name, arguments)
        try:
            runtime = self.ports.make_runtime(model=model or self.ports.model(), system_prompt=system,
                specs=specs, dispatch=dispatch if tools else lambda *_: ToolResult(False, "No Hardy tools offered"),
                cwd=self.ports.cwd, session_id=None, observe=observe)
            details = provenance(runtime)
            with self._lock:
                self._active = runtime
            self.ports.check_cancelled()
            response = str(runtime.ask(prompt))
            if provider_error:
                raise ConnectionError(provider_error)
            self.ports.check_cancelled()
            if not self._settled(runtime, 0.05):
                raise RuntimeError("Workflow provider worker teardown is pending; new work remains excluded")
            found = json_object(response)
            value = schema.model_validate_json(found or response)
            status = "completed"
        except BaseException as error:
            detail = f"{type(error).__name__}: {error}"
            status = ("cancelled" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else
                      "unavailable" if isinstance(error, (PermissionError, ConnectionError, OSError)) else "failed")
            raise
        finally:
            accepting.clear()
            if runtime is not None and status != "completed":
                runtime.cancel()
            pending = runtime is not None and not self._settled(runtime)
            with self._lock:
                self._calling = False
                self._active = runtime if pending else None
                self._pending_context = context_id if pending else None
            with accounting:
                if runtime is not None and not reported:
                    # Serialize the marker with a late result so history never
                    # records result-before-marker for the same fresh request.
                    event = {"type": "exchange_unreported", "workflow_call": identity}
                    offset = self.ports.record(event)
                    self.ports.usage(event, offset)
                    usage = usage.record(event)
                receipt_usage = usage.summary()
            receipt = ModelCallReceipt(id=identity, operation=self.operation, stage=stage, context_id=context_id,
                model=str(details["model"]), backend=details.get("backend"), endpoint=details.get("endpoint"),
                configuration=details, system_prompt=system, user_prompt=prompt, response_schema=schema_source,
                response=response, isolation=getattr(runtime, "isolation_guarantee", None),
                tools=tuple(spec["function"]["name"] for spec in specs), status=status, detail=detail,
                elapsed=time.monotonic() - started, usage=receipt_usage, recorded_at=stamp(), teardown_pending=pending)
            self.ports.journal(receipt)
        return StructuredResponse(value, receipt)
