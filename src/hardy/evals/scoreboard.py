"""Rows read off run directories, aggregates that are only counts and medians, and the validator."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Literal

from .. import acceptance
from ..domain import FormalStatus, FrozenModel, RunManifest, RunPhase
from .problems import Entry
from .sweep import Baseline

Outcome = Literal["solved", "solved_other", "unsolved", "refused", "exhausted", "graded", "invalid"]
EXHAUSTION = frozenset({"turn_limit", "wall_clock_limit"})
MEDIAN_FIELDS = ("exchanges", "turns", "cost_usd", "wall_seconds", "search_calls", "lean_checks")


class Row(FrozenModel):
    id: str
    tier: int
    twin_of: str | None
    expected: Literal["true", "false"]
    mode: Literal["batch", "staged"]
    repeat: int
    run_dir: str
    outcome: Outcome
    terminal_reason: str | None
    cost_usd: float | None
    exchanges: int | None
    turns: int | None
    wall_seconds: float | None
    lean_checks: int
    search_calls: int
    canonical: Literal["agreed", "disputed", "unavailable"] | None = None
    approval: Literal["automatic"] | None = None


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def batch_row(entry: Entry, tier: int, run_dir: Path, scoreboard_dir: Path, *, repeat: int) -> Row:
    base = dict(id=entry.id, tier=tier, twin_of=entry.twin_of, expected=entry.expected, mode="batch", repeat=repeat,
                run_dir=_relative(run_dir, scoreboard_dir))
    # Asked before anything here is read: a run the audit cannot make sense of
    # is a finding for it to report, not a missing file for this function to
    # raise over.
    if acceptance.validate_recorded_run(run_dir):
        return Row(outcome="invalid", terminal_reason=None, cost_usd=None, exchanges=None, turns=None,
                   wall_seconds=None, lean_checks=0, search_calls=0, **base)
    result = _read(run_dir / "result.json")
    trajectory = _read(run_dir / "trajectory.json")
    tools = [e for e in trajectory.get("events", []) if e.get("type") == "tool"]
    usage = result.get("usage") or {}
    common = dict(
        base, terminal_reason=result.get("terminal_reason"),
        cost_usd=usage.get("cost_usd"), exchanges=usage.get("exchanges"), turns=result.get("turns"),
        wall_seconds=(trajectory.get("limits") or {}).get("elapsed_seconds"),
        lean_checks=sum(1 for e in tools if e.get("name") in {"check_proof", "submit_proof"}),
        search_calls=sum(1 for e in tools if e.get("name") in acceptance.BATCH_SEARCH),
    )
    if entry.expected == "true":
        solved = result.get("terminal_reason") == "verified" and (result.get("axioms") or {}).get("status") == "clean"
        return Row(outcome="solved" if solved else "unsolved", **common)
    if not acceptance.refusal_issues(run_dir):
        return Row(outcome="refused", **common)
    if result.get("terminal_reason") in EXHAUSTION:
        return Row(outcome="exhausted", **common)
    return Row(outcome="graded", **common)


def _nested_run(row_dir: Path) -> Path | None:
    runs = sorted(p for p in row_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()) if row_dir.is_dir() else []
    return runs[0] if len(runs) == 1 else None


def staged_row(entry: Entry, tier: int, row_dir: Path, scoreboard_dir: Path, *, repeat: int) -> Row:
    run_dir = _nested_run(row_dir)
    base: dict[str, Any] = dict(id=entry.id, tier=tier, twin_of=entry.twin_of, expected=entry.expected, mode="staged", repeat=repeat,
                                run_dir=_relative(row_dir, scoreboard_dir), approval="automatic")
    # Asked before anything here is read: a run the audit cannot make sense of
    # (including one with no nested run to find) is a finding for it to
    # report, not a missing or malformed file for this function to raise over.
    if run_dir is None or acceptance.validate_recorded_run(run_dir):
        return Row(outcome="invalid", terminal_reason=None, cost_usd=None, exchanges=None, turns=None,
                   wall_seconds=None, lean_checks=0, search_calls=0, canonical=None, **base)
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    uses = [e for e in events if e.get("kind") == "claude.tool_use"]
    names = [str((e.get("payload") or {}).get("name", "")).removeprefix("mcp__hardy__") for e in uses]
    canonical_path = row_dir / "canonical.json"
    canonical = _read(canonical_path).get("outcome") if canonical_path.exists() else "unavailable"
    common = dict(
        base, terminal_reason=manifest.terminal_reason.value if manifest.terminal_reason else manifest.phase.value,
        cost_usd=manifest.usage.get("cost_usd"), exchanges=manifest.usage.get("exchanges"), turns=None,
        wall_seconds=manifest.timings_ms.get("active", 0) / 1000.0 if manifest.timings_ms else None,
        lean_checks=sum(1 for n in names if n == "lean_check_proof"),
        search_calls=sum(1 for n in names if n in acceptance.STAGED_SEARCH), canonical=canonical,
    )
    verified = manifest.phase is RunPhase.COMPLETED and manifest.grades.formal is FormalStatus.KERNEL_VERIFIED
    if not verified:
        return Row(outcome="unsolved", **common)
    return Row(outcome="solved" if canonical == "agreed" else "solved_other", **common)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


class TierAggregate(FrozenModel):
    n: int
    solved: int
    solved_other: int
    unsolved: int
    invalid: int
    solve_rate: float | None
    interval: tuple[float, float]
    refused: int
    exhausted: int
    graded: int
    mechanically_false: int
    refusal_rate: float | None
    medians: dict[str, float | None]
    unreported_costs: int


class Aggregates(FrozenModel):
    tiers: dict[str, TierAggregate]
    headline: TierAggregate
    floor: dict[str, int]


def _tier_aggregate(rows: list[Row], baseline: Baseline) -> TierAggregate:
    true_rows = [r for r in rows if r.expected == "true"]
    twins = [r for r in rows if r.expected == "false"]
    solved = [r for r in true_rows if r.outcome == "solved"]
    n = len(true_rows)
    medians: dict[str, float | None] = {}
    for field in MEDIAN_FIELDS:
        values = [getattr(r, field) for r in solved if getattr(r, field) is not None]
        medians[field] = float(statistics.median(values)) if values else None
    refused = sum(1 for r in twins if r.outcome == "refused")
    return TierAggregate(
        n=n, solved=len(solved), solved_other=sum(1 for r in true_rows if r.outcome == "solved_other"),
        unsolved=sum(1 for r in true_rows if r.outcome == "unsolved"), invalid=sum(1 for r in rows if r.outcome == "invalid"),
        solve_rate=len(solved) / n if n else None, interval=wilson(len(solved), n),
        refused=refused, exhausted=sum(1 for r in twins if r.outcome == "exhausted"), graded=sum(1 for r in twins if r.outcome == "graded"),
        mechanically_false=sum(
            1 for r in twins
            if (negation := baseline.entries[r.id].negation) is not None and negation.closed_by
        ),
        refusal_rate=refused / len(twins) if twins else None,
        medians=medians, unreported_costs=sum(1 for r in solved if r.cost_usd is None),
    )


def aggregate(rows: list[Row], baseline: Baseline) -> Aggregates:
    tiers = {str(t): _tier_aggregate([r for r in rows if r.tier == t], baseline) for t in range(4)}
    headline = _tier_aggregate([r for r in rows if r.tier in (2, 3)], baseline)
    floor = {"entries": len(baseline.entries)}
    for t in range(4):
        floor[f"tier_{t}"] = sum(1 for e in baseline.entries.values() if e.tier == t)
    floor["single_tactic_closes"] = sum(1 for e in baseline.entries.values() if e.tier in (0, 1))
    return Aggregates(tiers=tiers, headline=headline, floor=floor)
