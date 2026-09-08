"""Recorded outcomes of a bounded batch proving run."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from hardy.agents.contracts import TurnEvent as TurnEvent
from hardy.agents.parsing import _balanced_object as _balanced_object
from hardy.agents.parsing import json_object as json_object
from hardy.formal.contracts import DECLARATION_KEYWORD as DECLARATION_KEYWORD
from hardy.formal.contracts import Request as Request
from hardy.foundation.values import ToolResult as ToolResult


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
