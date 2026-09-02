"""Rows read off run directories, aggregates that are only counts and medians, and the validator."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path, PurePosixPath
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
        wall_seconds=(active / 1000.0) if (active := manifest.timings_ms.get("active")) is not None else None,
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
            if (entry := baseline.entries.get(r.id)) is not None
            and entry.negation is not None and entry.negation.closed_by
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


def validate_scoreboard(scoreboard_dir: Path, *, problems_path: Path, baseline_path: Path) -> tuple[str, ...]:
    """Every figure in a committed scoreboard, re-derived from artifacts the audit accepts (spec §5)."""
    from .problems import load_problems, sha256_of
    from .runner import RefusedRun, Scoreboard, select

    board_path = scoreboard_dir / "scoreboard.json"
    if not board_path.exists():
        return (f"{scoreboard_dir} has no scoreboard.json",)
    try:
        board = Scoreboard.model_validate_json(board_path.read_text(encoding="utf-8"))
    except Exception as error:  # pydantic.ValidationError, JSON errors
        return (f"scoreboard.json does not validate: {type(error).__name__}",)
    issues: list[str] = []
    # 1. bound to the committed list and tier file
    if board.problems_sha256 != sha256_of(problems_path):
        issues.append("problems_sha256 does not match evals/problems.json")
    if board.baseline_sha256 != sha256_of(baseline_path):
        issues.append("baseline_sha256 does not match evals/baseline.json")
    problems = load_problems(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    if baseline.environment != board.environment:
        issues.append("the scoreboard's environment is not the baseline's")
    # 2-5. every row re-derived
    for row in board.rows:
        where = row.run_dir
        # A row's `run_dir` is untrusted committed text, not a path this
        # function chose: reject anything that could name a file outside
        # the scoreboard directory as a finding, never let it become a read.
        candidate = PurePosixPath(row.run_dir)
        escapes = not row.run_dir or candidate.is_absolute() or Path(row.run_dir).is_absolute() or ".." in candidate.parts
        run_dir = scoreboard_dir / Path(*candidate.parts) if not escapes else scoreboard_dir
        if not escapes:
            try:
                run_dir.resolve().relative_to(scoreboard_dir.resolve())
            except ValueError:
                escapes = True
        if escapes:
            issues.append(f"{where}: run_dir points outside the scoreboard directory")
            continue
        if not run_dir.exists():
            issues.append(f"{where}: missing")
            continue
        try:
            entry = problems.by_id(row.id)
        except KeyError:
            issues.append(f"{where}: row id {row.id!r} is not in the problem list")
            continue
        if row.id not in baseline.entries:
            issues.append(f"{where}: row id {row.id!r} is not in the baseline")
            continue
        tier = baseline.entries[row.id].tier
        derived = batch_row(entry, tier, run_dir, scoreboard_dir, repeat=row.repeat) if row.mode == "batch" else staged_row(entry, tier, run_dir, scoreboard_dir, repeat=row.repeat)
        if derived.outcome == "invalid":
            issues.append(f"{where}: the recorded-run audit reports findings: " + "; ".join(acceptance.validate_recorded_run(run_dir if row.mode == "batch" else (_nested_run(run_dir) or run_dir))[:3]))
        issues.extend(_entry_issues(entry, row, run_dir))
        for field in Row.model_fields:
            if getattr(derived, field) != getattr(row, field):
                issues.append(f"{where}: {field} is {getattr(row, field)!r} but the run says {getattr(derived, field)!r}")
        if row.mode == "staged":
            issues.extend(_canonical_issues(run_dir, where))
    # 6. aggregates
    if aggregate(list(board.rows), baseline) != board.aggregates:
        # Not "...from the rows": that phrase's own plural would satisfy
        # check 7's `not any("row" in i for i in ...)` for the wrong reason.
        issues.append("the scoreboard's aggregates do not recompute")
    # 7. selection complete unless interrupted
    sel = board.condition.selection
    try:
        expected = {(e.id, k) for e in select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True)) for k in range(board.condition.repeats)}
    except RefusedRun as refused:
        issues.append(f"selection names entries not in the list: {refused}")
        expected = set()
    have = {(r.id, r.repeat) for r in board.rows}
    for extra in sorted(have - expected):
        issues.append(f"row {extra[0]} repeat {extra[1]} is outside the selection")
    if not board.interrupted:
        for missing in sorted(expected - have):
            issues.append(f"row {missing[0]} repeat {missing[1]} is missing and the scoreboard is not marked interrupted")
    return tuple(issues)


def _entry_issues(entry: Entry, row: Row, run_dir: Path) -> list[str]:
    issues = []
    if row.mode == "batch":
        trajectory = _read(run_dir / "trajectory.json")
        request = trajectory.get("request") or {}
        if request.get("declaration") != entry.declaration():
            issues.append(f"{row.run_dir}: the run's declaration is not the entry's canonical declaration")
        if request.get("informal_claim") != entry.input:
            issues.append(f"{row.run_dir}: the run's informal claim is not the entry's input")
    else:
        nested = _nested_run(run_dir)
        if nested is not None and (nested / "request.md").exists() and (nested / "request.md").read_text(encoding="utf-8").strip() != entry.input.strip():
            issues.append(f"{row.run_dir}: request.md is not the entry's input")
    return issues


def _canonical_issues(row_dir: Path, where: str) -> list[str]:
    import hashlib

    from .staged import CanonicalVerdict

    path = row_dir / "canonical.json"
    if not path.exists():
        return [f"{where}: no canonical.json"]
    try:
        verdict = CanonicalVerdict.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as error:
        return [f"{where}: canonical.json does not validate: {type(error).__name__}"]
    issues = []
    for name, expected in (("canonical-prompt.md", verdict.prompt_sha256), ("canonical-schema.json", verdict.response_schema_sha256)):
        if expected is None:
            continue
        file = row_dir / name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            issues.append(f"{where}: {name} does not hash to the verdict's record of it")
    nested = _nested_run(row_dir)
    if nested is not None and verdict.claim_sha256 is not None:
        manifest = RunManifest.model_validate_json((nested / "manifest.json").read_text(encoding="utf-8"))
        if manifest.claim_sha256 != verdict.claim_sha256:
            issues.append(f"{where}: canonical.json names a claim hash the manifest does not")
    return issues


def check_command(args: Any) -> int:
    issues = validate_scoreboard(args.scoreboard, problems_path=args.problems, baseline_path=args.baseline)
    print(f"Scoreboard: {args.scoreboard}")
    for issue in issues:
        print("CONSISTENCY ERROR: " + issue)
    if not issues:
        board = json.loads((args.scoreboard / "scoreboard.json").read_text(encoding="utf-8"))
        agg = board["aggregates"]
        h = agg["headline"]
        print(f"headline (tiers 2-3): {h['solved']}/{h['n']} solved, 95% {h['interval'][0]:.2f}-{h['interval'][1]:.2f}; floor: {agg['floor']}")
        for t in ("0", "1", "2", "3"):
            a = agg["tiers"][t]
            print(f"tier {t}: n={a['n']} solved={a['solved']} refused={a['refused']} exhausted={a['exhausted']} graded={a['graded']} medians={a['medians']}")
    return 0 if not issues else 1
