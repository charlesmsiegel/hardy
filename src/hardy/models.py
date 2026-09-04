from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# What a request's declaration may open with. Attributes and modifiers come
# before the keyword in ordinary Lean, and this is the earliest of the three
# places that had to be taught so -- the head grammar in `hardy.lean` never saw
# a decorated declaration, because this refused it first.
DECLARATION_KEYWORD = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|nonrec|unsafe|partial|scoped|local)\s+)*"
    r"(?:theorem|lemma|example)(?:\s|$)"
)


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
        # Read through what may precede the keyword rather than demanding it
        # come first. `@[simp] theorem T` and `protected theorem T` are ordinary
        # Lean, and refusing them here made the head grammar's tolerance of both
        # unreachable -- the request never got that far.
        if not DECLARATION_KEYWORD.match(declaration):
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

    # `notice` is Hardy's own, not the model's: what the workspace still
    # owes, drawn after the reply and read off the artifacts rather than off
    # anything that was said.
    kind: str                    # text | thinking | tool_use | tool_result | reply | notice
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
    # The provider's own turn count, or None when it never reported one -- which
    # is what a run cancelled by the wall clock looks like. Not an int with a
    # zero default: 0 claims a run that took no turns.
    turns: int | None
    # What the run cost and how many tokens it moved, shaped by
    # `usage.Usage.summary()`: a figure the provider never stated is `None`
    # rather than 0, and `reported` says how many exchanges each figure covers.
    # Required rather than defaulted, because a spend field a caller can quietly
    # omit is a spend field that reads as free -- the one thing the ledger
    # exists to stop a record saying.
    usage: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    # The Lean and Mathlib the verdict was computed against, shaped like
    # `domain.EnvironmentIdentity`, or `{"unrecorded": <why>}` when the run
    # could not identify them. Never absent and never a literal: a `verified`
    # beside no toolchain is a claim about a Lean nobody can name.
    toolchain: dict[str, Any] | None = None
    # The last skeleton Lean accepted with holes still in it, as
    # `{"proof": ..., "holes": [...]}`, or None when the run reached none.
    # Never a grade and never a proof: a sketch is an intermediate state, and
    # `formalization` stays "not formalized" whatever is recorded here. It is
    # written down so a run that got the structure right and ran out of turns
    # leaves the partial development behind instead of only the transcript of
    # having attempted one.
    sketch: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def json_object(text: str) -> str | None:
    """Find the one JSON object in a model's reply, fence or no fence.

    Here rather than in one caller because two of them need it and neither
    owns it: the staged runtime reads structured proof submissions, and the
    interactive session reads an independent reader's verdict. A model that
    wraps its answer in a fence or a sentence has still answered; one that
    returns no object at all has not, and both callers treat that as a
    failure rather than as a default.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        body = stripped.split("```")
        for part in body:
            candidate = part[4:] if part.startswith("json") else part
            found = _balanced_object(candidate)
            if found is not None:
                return found
    return _balanced_object(stripped)


def _balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
