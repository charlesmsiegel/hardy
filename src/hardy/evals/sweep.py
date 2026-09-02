"""The automation floor: a fixed tactic set against every canonical statement.

Tiers are decided by heartbeats, not seconds (spec §2.2), and the sweep runs
in two stages so `exact?` cannot be credited with a neighbour's proof (§2.3).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .. import audit
from ..domain import EnvironmentIdentity, FrozenModel
from ..lean import Elaboration
from .problems import Entry, ProblemSet

SINGLES: tuple[str, ...] = (
    "simp", "simp_all", "omega", "decide", "norm_num", "ring", "field_simp", "linarith",
    "nlinarith", "positivity", "tauto", "aesop", "grind", "hint", "exact?", "apply?",
)
# A decision, not a discovery: changing this list re-tiers the set (spec §2.1).
CHAINS: tuple[str, ...] = (
    "intros; simp_all", "constructor <;> simp_all", "simp_all; omega", "norm_num; ring",
    "norm_num; linarith", "field_simp; ring", "by_contra h; push_neg at h; nlinarith", "intros; aesop",
)
# `hint` runs `exact?` internally (Mathlib's `register_hint 600 exact?`), so a
# goal it closes is a library-search hit, not a tier-0 automation close.
SEARCHERS: tuple[str, ...] = ("exact?", "apply?", "hint")
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


Elaborate = Callable[[str], Elaboration]


class NegationBaseline(FrozenModel):
    attempts: dict[str, Attempt]
    closed_by: tuple[str, ...]


class EntryBaseline(FrozenModel):
    tier: int = Field(ge=0, le=3)
    elaborates: bool
    attempts: dict[str, Attempt]
    closed_by: tuple[str, ...]
    negation: NegationBaseline | None = None


class Baseline(FrozenModel):
    schema_version: Literal[1] = 1
    created_at: datetime
    problems_sha256: str
    environment: EnvironmentIdentity
    heartbeat_budget: int
    wall_backstop_seconds: float
    import_seconds: float | None = None
    singles: tuple[str, ...]
    chains: tuple[str, ...]
    host: dict[str, Any]
    problems: tuple[str, ...]
    entries: dict[str, EntryBaseline]


def tier_of(closed_by: tuple[str, ...]) -> int:
    closers = set(closed_by)
    if closers & (set(SINGLES) - set(SEARCHERS)):
        return 0
    if closers & set(SEARCHERS):
        return 1
    if closers & set(CHAINS):
        return 2
    return 3


def sweep_proposition(proposition: str, imports: tuple[str, ...], elaborate: Elaborate, *, confirm: Callable[[str], Attempt]) -> tuple[dict[str, Attempt], tuple[str, ...]]:
    """Stage A over every tactic, then stage B for each candidate. Returns attempts and the closers."""
    tactics = SINGLES + CHAINS
    source, spans = stage_a_source(proposition, tactics, imports)
    attempts = read_stage_a(elaborate(source), spans)
    if all(a.status == "timed_out" for a in attempts.values()):
        # One runaway tactic must not mark the rest unknown (spec §2.3).
        attempts = {}
        for tactic in tactics:
            single, span = stage_a_source(proposition, (tactic,), imports)
            attempts[tactic] = read_stage_a(elaborate(single), span)[tactic]
    closed: list[str] = []
    for tactic in tactics:
        if attempts[tactic].status != "candidate":
            continue
        confirmed = confirm(tactic)
        attempts[tactic] = confirmed.model_copy(update={"heartbeats": confirmed.heartbeats if confirmed.heartbeats is not None else attempts[tactic].heartbeats})
        if confirmed.status == "closed":
            closed.append(tactic)
    return attempts, tuple(closed)


def sweep_entry(entry: Entry, elaborate: Elaborate, *, confirm_name: str) -> EntryBaseline:
    if not elaborate(sorry_source(confirm_name, entry.binders, entry.conclusion, entry.imports)).success:
        return EntryBaseline(tier=3, elaborates=False, attempts={}, closed_by=())

    def confirm(tactic: str) -> Attempt:
        return read_stage_b(elaborate(stage_b_source(confirm_name, entry.binders, entry.conclusion, tactic, entry.imports)), confirm_name, tactic)

    attempts, closed = sweep_proposition(entry.proposition(), entry.imports, elaborate, confirm=confirm)
    negation = None
    if entry.expected == "false":
        neg_name = f"{confirm_name}Negation"

        def confirm_negation(tactic: str) -> Attempt:
            return read_stage_b(elaborate(stage_b_source(neg_name, "", entry.negation(), tactic, entry.imports)), neg_name, tactic)

        n_attempts, n_closed = sweep_proposition(entry.negation(), entry.imports, elaborate, confirm=confirm_negation)
        negation = NegationBaseline(attempts=n_attempts, closed_by=n_closed)
    return EntryBaseline(tier=tier_of(closed), elaborates=True, attempts=attempts, closed_by=closed, negation=negation)


def sweep(problems: ProblemSet, *, problems_sha256: str, environment: EnvironmentIdentity, elaborate: Elaborate,
          now: Callable[[], datetime], host: dict[str, Any], import_seconds: float | None = None,
          wall_backstop_seconds: float = WALL_BACKSTOP_FLOOR, report: Callable[[str], None] = lambda _: None) -> Baseline:
    entries: dict[str, EntryBaseline] = {}
    findings: list[str] = []
    for entry in problems.entries:
        report(f"sweeping {entry.id}")
        result = sweep_entry(entry, elaborate, confirm_name=entry.name)
        entries[entry.id] = result
        if not result.elaborates:
            findings.append(f"{entry.id}: the canonical statement does not elaborate")
        if entry.expected == "false" and result.closed_by:
            findings.append(f"{entry.id}: a twin closed by {', '.join(result.closed_by)}, so it is true")
        for tactic, attempt in result.attempts.items():
            if attempt.status == "unconfirmed":
                report(f"  {entry.id}: {tactic} was a candidate but did not confirm: {attempt.message}")
    return Baseline(
        created_at=now(), problems_sha256=problems_sha256, environment=environment,
        heartbeat_budget=HEARTBEAT_BUDGET, wall_backstop_seconds=wall_backstop_seconds, import_seconds=import_seconds,
        singles=SINGLES, chains=CHAINS, host=host, problems=tuple(findings), entries=entries,
    )


def staleness(baseline: Baseline, *, problems_sha256: str, environment: EnvironmentIdentity) -> tuple[str, ...]:
    """Why this baseline cannot tier a run today (spec §3.1). Empty means it can."""
    issues: list[str] = []
    if baseline.problems_sha256 != problems_sha256:
        issues.append("the baseline was swept over a different problems.json; re-run `hardy evals baseline`")
    for field in ("lean_version", "lean_commit", "mathlib_revision", "lake_manifest_sha256"):
        if getattr(baseline.environment, field) != getattr(environment, field):
            issues.append(f"the baseline's {field} is {getattr(baseline.environment, field)!r}, this project's is {getattr(environment, field)!r}")
    if baseline.singles != SINGLES or baseline.chains != CHAINS:
        issues.append("the baseline's singles/chains differ from the code's; re-run `hardy evals baseline`")
    if baseline.problems:
        issues.append("the baseline records problems with the list: " + "; ".join(baseline.problems))
    return tuple(issues)
