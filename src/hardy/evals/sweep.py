"""The automation floor: a fixed tactic set against every canonical statement.

Tiers are decided by heartbeats, not seconds (spec §2.2), and the sweep runs
in two stages so `exact?` cannot be credited with a neighbour's proof (§2.3).
"""
from __future__ import annotations

import re
from typing import Literal

from .. import audit
from ..domain import FrozenModel
from ..lean import Elaboration

SINGLES: tuple[str, ...] = (
    "simp", "simp_all", "omega", "decide", "norm_num", "ring", "field_simp", "linarith",
    "nlinarith", "positivity", "tauto", "aesop", "grind", "hint", "exact?", "apply?",
)
# A decision, not a discovery: changing this list re-tiers the set (spec §2.1).
CHAINS: tuple[str, ...] = (
    "intros; simp_all", "constructor <;> simp_all", "simp_all; omega", "norm_num; ring",
    "norm_num; linarith", "field_simp; ring", "by_contra h; push_neg at h; nlinarith", "intros; aesop",
)
SEARCHERS: tuple[str, ...] = ("exact?", "apply?")
HEARTBEAT_BUDGET = 200000
WALL_BACKSTOP_FLOOR = 600.0

COUNT = re.compile(r"Used (\d+) heartbeats")
EXHAUSTED = "maximum number of heartbeats"
SORRY = "declaration uses 'sorry'"

Status = Literal["closed", "candidate", "failed", "heartbeats_exhausted", "timed_out", "unconfirmed", "not_run"]


class Attempt(FrozenModel):
    status: Status
    heartbeats: int | None = None
    seconds: float | None = None
    axioms: tuple[str, ...] | None = None
    message: str = ""


def header(imports: tuple[str, ...]) -> str:
    # `Elab.async false` so a count is attributable to its own declaration.
    return "".join(f"import {name}\n" for name in imports) + "set_option Elab.async false\n"


def _block(keyword: str, name: str, binders: str, conclusion: str, tactic: str) -> str:
    head = f"{keyword} {name}".rstrip() if name else keyword
    binders = f" {binders.strip()}" if binders.strip() else ""
    return (
        "#count_heartbeats in\n"
        f"set_option maxHeartbeats {HEARTBEAT_BUDGET} in\n"
        f"{head}{binders} : {conclusion.strip()} := by\n"
        f"  {tactic}\n"
    )


def stage_a_source(proposition: str, tactics: tuple[str, ...], imports: tuple[str, ...]) -> tuple[str, dict[str, tuple[int, int]]]:
    """Every tactic as an anonymous example, and the 1-based line range of each block."""
    text = header(imports) + "\n"
    spans: dict[str, tuple[int, int]] = {}
    for tactic in tactics:
        block = _block("example", "", "", proposition, tactic)
        start = text.count("\n") + 1
        text += block
        spans[tactic] = (start, text.count("\n"))  # last line of the block
        text += "\n"
    return text, spans


def stage_b_source(name: str, binders: str, conclusion: str, tactic: str, imports: tuple[str, ...]) -> str:
    return header(imports) + "\n" + _block("theorem", name, binders, conclusion, tactic) + f"\n#print axioms {name}\n"


def sorry_source(name: str, binders: str, conclusion: str, imports: tuple[str, ...]) -> str:
    binders = f" {binders.strip()}" if binders.strip() else ""
    return header(imports) + f"\ntheorem {name}{binders} : {conclusion.strip()} := by\n  sorry\n"


def _within(line: int | None, span: tuple[int, int]) -> bool:
    return line is not None and span[0] <= line <= span[1]


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0][:200] if message.strip() else ""


def read_stage_a(elaboration: Elaboration, spans: dict[str, tuple[int, int]]) -> dict[str, Attempt]:
    if elaboration.process.timed_out or elaboration.process.output_overflow:
        why = "process timed out" if elaboration.process.timed_out else "output overflow"
        return {tactic: Attempt(status="timed_out", message=why) for tactic in spans}
    errors = [d for d in elaboration.diagnostics if d.severity == "error"]
    stray = [d for d in errors if not any(_within(d.line, span) for span in spans.values())]
    if stray:
        return {tactic: Attempt(status="not_run", message=_first_line(stray[0].message)) for tactic in spans}
    out: dict[str, Attempt] = {}
    for tactic, span in spans.items():
        mine = [d for d in elaboration.diagnostics if _within(d.line, span)]
        count = next((int(m.group(1)) for d in mine for m in [COUNT.search(d.message)] if m), None)
        errs = [d for d in mine if d.severity == "error"]
        sorries = [d for d in mine if d.severity == "warning" and SORRY in d.message]
        if any(EXHAUSTED in d.message for d in errs):
            out[tactic] = Attempt(status="heartbeats_exhausted", heartbeats=count, message=_first_line(errs[0].message))
        elif errs:
            out[tactic] = Attempt(status="failed", heartbeats=count, message=_first_line(errs[0].message))
        elif sorries:
            out[tactic] = Attempt(status="failed", heartbeats=count, message=_first_line(sorries[0].message))
        else:
            out[tactic] = Attempt(status="candidate", heartbeats=count)
    return out


def read_stage_b(elaboration: Elaboration, name: str, tactic: str) -> Attempt:
    seconds = elaboration.process.duration_ms / 1000.0
    count = next((int(m.group(1)) for d in elaboration.diagnostics for m in [COUNT.search(d.message)] if m), None)
    if not elaboration.success:
        first = next((d.message for d in elaboration.diagnostics if d.severity == "error"), "did not elaborate")
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, message=_first_line(first))
    spoken = "\n".join(d.message for d in elaboration.diagnostics)
    reports = audit.parse(spoken, (name,))
    if reports is None:
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, message="no axiom report")
    axioms = tuple(reports[0].axioms)
    if not set(axioms) <= audit.STANDARD:
        return Attempt(status="unconfirmed", heartbeats=count, seconds=seconds, axioms=axioms, message="axioms beyond the standard three")
    return Attempt(status="closed", heartbeats=count, seconds=seconds, axioms=axioms)
