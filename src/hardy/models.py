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


@dataclass
class RunResult:
    terminal_reason: str
    formalization: str
    informal_completeness: str
    proof: str | None
    lean_output: str
    axioms: str
    turns: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
