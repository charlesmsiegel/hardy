"""Durable provider-call admission, separate from measured Usage and Lean checks.

One owner reserves expected spend for all conversations in a run. Reservations
are estimates, never provider billing guarantees. Exact reported token fields
settle them; missing reports retain liability, and overruns forbid later calls.
The journal survives restarts without turning an unfinished call into a refund.
An explicit immutable tariff derives cost; it does not manufacture an invoice.
"""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Self

from pydantic import Field, model_validator

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.foundation.values import FrozenModel, json_digest

COUNTERS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class Tariff(FrozenModel):
    id: str = Field(min_length=1)
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    cache_read_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    cache_write_per_million: Decimal = Field(ge=0, allow_inf_nan=False)

    def cost(self, counts: Mapping[str, int]) -> Decimal:
        return (counts["input_tokens"] * self.input_per_million
            + counts["output_tokens"] * self.output_per_million
            + counts["cache_creation_input_tokens"] * self.cache_write_per_million
            + counts["cache_read_input_tokens"] * self.cache_read_per_million) / 1_000_000


class SpendPolicy(FrozenModel):
    id: str = Field(min_length=1)
    models: tuple[str, ...] = Field(min_length=1)
    token_limit: int | None = Field(default=None, ge=0, strict=True)
    cost_limit_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_characters_per_token: Decimal = Field(default=Decimal("4"), gt=0, allow_inf_nan=False)
    input_overhead_tokens: int = Field(default=512, ge=0, strict=True)
    tariff: Tariff | None = None

    @model_validator(mode="after")
    def configured_limits(self) -> Self:
        if self.token_limit is None and self.cost_limit_usd is None:
            raise ValueError("provider budget requires a token or cost limit")
        if self.cost_limit_usd is not None and self.tariff is None:
            raise ValueError("cost limit requires an explicit tariff")
        if any(not name.strip() for name in self.models):
            raise ValueError("budget models must be explicit nonempty identities")
        return self

    @property
    def digest(self) -> str:
        return json_digest(self.model_dump(mode="json"))


class SpendLimitReached(RuntimeError):
    def __init__(self, limit: str):
        self.limit = limit
        super().__init__(f"provider budget refused the call: {limit}")


class SpendBudget:
    def __init__(self, path: Path, policy: SpendPolicy, *, read_only: bool = False):
        self.path = Path(path)
        self.policy = SpendPolicy.model_validate(policy.model_dump())
        self.guard = WriteGuard(self.path.parent, create=not read_only)
        self._read_only = read_only
        self._lock = threading.Lock()
        self._active: set[str] = set()
        with self._lock, self._file_lock():
            if not self.path.exists() and not read_only:
                self._append([], "start", {"schema": "hardy.provider-budget/v1", "policy": self.policy.model_dump(mode="json")})
            self._state(self._read())

    def _file_lock(self):
        return nullcontext() if self._read_only else FileLock(self.path.with_suffix(self.path.suffix + ".lock"))

    def _quote(self, characters: int, output: int) -> tuple[int, Decimal]:
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (characters, output)) or output == 0:
            raise ValueError("provider reservation requires character counts and a positive output cap")
        estimated_input = math.ceil(Decimal(characters) / self.policy.input_characters_per_token) + self.policy.input_overhead_tokens
        tariff = self.policy.tariff
        cost = ((estimated_input * max(tariff.input_per_million, tariff.cache_read_per_million, tariff.cache_write_per_million)
                 + output * tariff.output_per_million) / 1_000_000) if tariff else Decimal(0)
        return estimated_input + output, cost

    def _read(self) -> list[dict[str, Any]]:
        data = self.guard.path(self.path.name).read_bytes()
        if not data.endswith(b"\n"):
            raise ValueError("provider budget journal has an incomplete tail")
        events = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        previous = None
        for number, event in enumerate(events):
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                raise ValueError("provider budget journal requires object events and payloads")
            value = {key: val for key, val in event.items() if key != "digest"}
            if (set(event) != {"sequence", "previous", "kind", "payload", "digest"}
                    or event["sequence"] != number or event["previous"] != previous
                    or event["digest"] != json_digest(value)):
                raise ValueError("provider budget journal sequence/content mismatch")
            previous = event["digest"]
        if not events or events[0]["kind"] != "start" or events[0]["payload"] != {
                "schema": "hardy.provider-budget/v1", "policy": self.policy.model_dump(mode="json")}:
            raise ValueError("provider budget policy differs from its immutable journal")
        return events

    def _append(self, events: list[dict[str, Any]], kind: str, payload: dict[str, Any]) -> None:
        if self._read_only:
            raise ValueError("provider budget reader cannot append")
        event = {"sequence": len(events), "previous": events[-1]["digest"] if events else None,
                 "kind": kind, "payload": payload}
        event["digest"] = json_digest(event)
        with self.guard.open(self.path.name, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, allow_nan=False, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _state(self, events: list[dict[str, Any]]) -> tuple[dict, dict, str | None]:
        reservations, settled, ending = {}, {}, None
        for event in events[1:]:
            payload = event["payload"]
            if event["kind"] == "reserve":
                if payload["id"] in reservations:
                    raise ValueError("duplicate provider reservation")
                tokens, cost = self._quote(payload["input_characters"], payload["max_tokens"])
                if (payload["model"] not in self.policy.models or payload["tokens"] != tokens or payload["cost_usd"] != str(cost)
                        or not isinstance(payload["request_sha256"], str) or len(payload["request_sha256"]) != 64):
                    raise ValueError("provider reservation differs from its frozen quote policy")
                reservations[payload["id"]] = payload
            elif event["kind"] == "settle":
                if payload["id"] not in reservations or payload["id"] in settled:
                    raise ValueError("provider settlement lacks one preceding reservation")
                if payload != self._settlement(payload["id"], payload["reported"]):
                    raise ValueError("provider settlement differs from reported usage")
                settled[payload["id"]] = payload
            elif event["kind"] == "deny":
                ending = payload["limit"]
            else:
                raise ValueError("unknown provider budget journal event")
        return reservations, settled, ending

    def _settlement(self, identifier: str, usage: Mapping | None) -> dict[str, Any]:
        if usage is not None and not isinstance(usage, Mapping):
            raise ValueError("provider settlement requires a usage object")
        reported = {key: value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
                    for key in COUNTERS for value in [(usage or {}).get(key)]}
        complete = all(value is not None for value in reported.values())
        cost = self.policy.tariff.cost(reported) if complete and self.policy.tariff else None
        return {"id": identifier, "reported": reported, "tokens": sum(reported.values()) if complete else None,
                "cost_usd": str(cost) if cost is not None else None}

    def reserve(self, request: Mapping[str, Any]) -> str:
        if self._read_only:
            raise ValueError("provider budget reader cannot reserve")
        if request.get("model") not in self.policy.models:
            with self._lock, self._file_lock():
                self._append(self._read(), "deny", {"limit": "model_not_in_policy"})
            raise SpendLimitReached("model_not_in_policy")
        output = request.get("max_tokens")
        if isinstance(output, bool) or not isinstance(output, int) or output < 1:
            raise ValueError("provider budget requires an actual positive output cap")
        serialized = json.dumps(dict(request), ensure_ascii=False, sort_keys=True, allow_nan=False)
        tokens, cost = self._quote(len(serialized), output)
        tariff = self.policy.tariff
        with self._lock, self._file_lock():
            events = self._read()
            reservations, settled, _ = self._state(events)
            pending = set(reservations) - set(settled)
            actual_tokens = sum(value["tokens"] or 0 for value in settled.values())
            actual_cost = sum((Decimal(value["cost_usd"] or "0") for value in settled.values()), Decimal(0))
            limit = None
            if (pending - self._active or any(value["tokens"] is None for value in settled.values())):
                limit = "unknown_usage"
            elif self.policy.token_limit is not None and actual_tokens + sum(reservations[k]["tokens"] for k in pending) + tokens > self.policy.token_limit:
                limit = "token_limit"
            elif self.policy.cost_limit_usd is not None and actual_cost + sum(Decimal(reservations[k]["cost_usd"]) for k in pending) + cost > self.policy.cost_limit_usd:
                limit = "cost_limit"
            elif any(value["tokens"] > reservations[k]["tokens"] or
                     tariff and Decimal(value["cost_usd"]) > Decimal(reservations[k]["cost_usd"]) for k, value in settled.items()):
                limit = "reservation_overrun"
            if limit:
                self._append(events, "deny", {"limit": limit})
                raise SpendLimitReached(limit)
            identifier = str(uuid.uuid4())
            self._append(events, "reserve", {"id": identifier, "model": request["model"], "request_sha256": sha256(serialized.encode()).hexdigest(),
                "input_characters": len(serialized), "max_tokens": output, "tokens": tokens, "cost_usd": str(cost)})
            self._active.add(identifier)
            return identifier

    def settle(self, identifier: str, usage: Mapping | None) -> None:
        if self._read_only:
            raise ValueError("provider budget reader cannot settle")
        with self._lock:
            # Even a failed write ends our knowledge of this call. Its durable
            # pending reservation must become unknown liability, not live credit.
            self._active.discard(identifier)
            with self._file_lock():
                value = self._settlement(identifier, usage)
                events = self._read()
                reservations, settled, _ = self._state(events)
                if identifier not in reservations:
                    raise ValueError("unknown provider reservation")
                if identifier in settled:
                    if settled[identifier] != value:
                        raise ValueError("provider reservation already has a different settlement")
                    return
                self._append(events, "settle", value)

    def summary(self) -> dict[str, Any]:
        with self._lock, self._file_lock():
            reservations, settled, ending = self._state(self._read())
            pending = len(reservations) - len(settled)
            unknown = pending or any(value["tokens"] is None for value in settled.values())
            tokens = sum(value["tokens"] or 0 for value in settled.values())
            cost = sum((Decimal(value["cost_usd"] or "0") for value in settled.values()), Decimal(0))
            return {"policy": self.policy.model_dump(mode="json"), "policy_sha256": self.policy.digest,
                "journal_sha256": sha256(self.guard.path(self.path.name).read_bytes()).hexdigest(),
                "reservations": len(reservations), "pending": pending,
                "reported_tokens": sum(n or 0 for value in settled.values() for n in value["reported"].values()),
                "actual_tokens": None if unknown else tokens,
                "reservation_overruns": sum(value["tokens"] is not None and (
                    value["tokens"] > reservations[k]["tokens"] or self.policy.tariff is not None
                    and Decimal(value["cost_usd"]) > Decimal(reservations[k]["cost_usd"])) for k, value in settled.items()),
                "derived_cost_usd": str(cost) if self.policy.tariff and not unknown else None,
                "provider_cost_usd": None, "ending_limit": ending,
                "enforcement": "expected-spend admission; actual overrun remains possible"}


def budget_record_issues(directory: Path, recorded: Any) -> tuple[str, ...]:
    """Read-only cross-check against the exact journal, including missing identity."""
    path = directory / "provider-budget.jsonl"
    if recorded is None and not path.exists():
        return ()
    try:
        policy = SpendPolicy.model_validate(recorded["policy"])
        if SpendBudget(path, policy, read_only=True).summary() != recorded:
            return ("provider budget summary or journal digest differs",)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return (f"provider budget: {error}",)
    return ()


def bind_spend_budget(factory: Callable, path: Path) -> Callable:
    """Bind every runtime from one factory to the same durable run/session owner."""
    policy = getattr(factory, "spend_policy", None)
    if policy is None and not path.exists():
        return factory
    if getattr(factory, "budget_backend", None) != "api":
        raise ValueError("provider budgets require the harness-owned API backend")
    if policy is None:
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        policy = SpendPolicy.model_validate(first["payload"]["policy"])
    owner = SpendBudget(path, policy)
    def bound(*args, **kwargs):
        return factory(*args, **kwargs, spend_budget=owner)
    bound.spend_budget = owner
    return bound
