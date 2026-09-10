"""Explicit prospective eval identity carried into a named execution.

The caller sets this on the thread that invokes the workflow. A custom executor
which starts another thread must propagate its context explicitly; missing
propagation leaves the attempt uncertifiable, never inferred from its path.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import Field

from hardy.foundation.values import FrozenModel


class EvaluationAttempt(FrozenModel):
    declaration_sha256: str = Field(min_length=64, max_length=64)
    slot: int = Field(ge=0, strict=True)
    nonce: str = Field(min_length=1)
    problem_id: str = Field(min_length=1)
    repeat: int = Field(ge=0, strict=True)
    statement_sha256: str = Field(min_length=64, max_length=64)
    prompt_sha256: str = Field(min_length=64, max_length=64)


_CURRENT: ContextVar[EvaluationAttempt | None] = ContextVar("hardy_evaluation_attempt", default=None)


def current_attempt() -> dict | None:
    current = _CURRENT.get()
    return current.model_dump(mode="json") if current is not None else None


@contextmanager
def evaluation_attempt(value: EvaluationAttempt | None) -> Iterator[None]:
    token = _CURRENT.set(value)
    try:
        yield
    finally:
        _CURRENT.reset(token)
