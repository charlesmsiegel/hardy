"""Strict durable values and transport-neutral tool results.

Capability-specific claims and run grades belong to their owning contracts;
these primitives impose no dependency on a capability or workflow.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """A strict immutable value that is safe to hash or persist."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def json_digest(value: Any) -> str:
    """SHA-256 of sorted, compact UTF-8 JSON, preserving non-ASCII text.

    Evidence producers and readers must hash the same bytes. Callers own the
    payload and any domain tag; this function owns only its serialization.
    """
    canonical = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def schema_text(model: type[BaseModel]) -> str:
    """The JSON Schema text a structured stage sends, rendered one way only.

    Defined here rather than at each call site because two renderings of the
    same schema are two different requests: the staged runtime appends this
    string to the prompt, and the faithfulness gate persists it as the
    response contract the reader answered. Rendered apart -- one compact and
    one spaced, one escaping non-ASCII and one not -- the recorded identity
    would describe a serialization nobody was sent, which is exactly what
    hashing it was for.
    """
    return json.dumps(model.model_json_schema(), sort_keys=True)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
