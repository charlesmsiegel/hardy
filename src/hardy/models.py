from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Request:
    declaration: str
    informal_claim: str
    imports: tuple[str, ...] = ("Mathlib",)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Request:
        declaration = str(value["declaration"]).strip()
        if ":=" in declaration:
            raise ValueError("declaration must contain the statement only, not ':='")
        if not declaration.startswith(("theorem ", "lemma ", "example ")):
            raise ValueError("declaration must begin with theorem, lemma, or example")
        imports = tuple(str(item).strip() for item in value.get("imports", ["Mathlib"]))
        if not imports or any(not item for item in imports):
            raise ValueError("imports must be a non-empty list")
        return cls(declaration, str(value["informal_claim"]).strip(), imports)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurnEvent:
    """One thing that happened while a turn was in flight.

    `text` events are *deltas*, for drawing only. The reply a caller keeps is
    the `reply` event's text, assembled from whole blocks -- see
    `claude_runtime._deltas` for why consuming both would double every answer.
    """

    kind: str                    # text | thinking | tool_use | tool_result | reply
    text: str = ""               # a delta for `text`; the whole reply for `reply`
    name: str = ""               # the tool, for tool_use and tool_result
    ok: bool | None = None       # how a tool call came out, for tool_result
    # Which invocation this is, for tool_use and tool_result. The SDK can run
    # several calls at once, including two of the same tool, so the name does
    # not identify one of them -- pairing a result with its start needs the id.
    call_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    terminal_reason: str
    formalization: str
    informal_completeness: str
    proof: str | None
    lean_output: str
    # The audit's own verdict, shaped like `audit.Verdict.as_dict()`. It was
    # the whole Lean stdout blob under this name, which read as an axiom record
    # while containing nothing anyone had audited.
    axioms: dict[str, Any]
    turns: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
