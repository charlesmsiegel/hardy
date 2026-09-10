"""Compare exact problem/repeat slots without pooling experimental conditions.

Artifacts authenticate outcomes; recorded controls describe an experiment but
cannot establish causality. Every admitted usage value retains field-level
coverage, so failed attempts and missing reports cannot make a treatment cheap.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from hardy.agents.usage import Usage
from hardy.evals.contracts import Condition, Row, Scoreboard
from hardy.evals.outstanding import environment_digest_of_board
from hardy.evals.scoreboard import _nested_run, scoreboard_self_issues

USAGE_FIELDS = ("cost_usd", *Usage.COUNTERS)
# The compound run digest is retained as provenance, never treated as a
# control: changing the model changes that digest even with identical source.
CONTROL_FIELDS = tuple(name for name in Condition.model_fields if name != "run_procedure_digest") + ("environment", "workers", "strategy_source_sha256")


class ComparisonRefused(ValueError):
    """A comparison request or its input cannot be read."""


def _number(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def usage_measurements(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Interpret Usage.summary without upgrading unrecorded coverage to full."""
    exchanges = usage.get("exchanges")
    reported = usage.get("reported")
    reported = reported if isinstance(reported, dict) else {}
    measurements = {}
    for field in USAGE_FIELDS:
        value, count = usage.get(field), reported.get(field)
        if not _number(value):
            value, coverage = None, "missing"
        else:
            complete = (type(exchanges) is int and exchanges > 0
                        and type(count) is int and count == exchanges)
            coverage = "complete" if complete else "partial"
        measurements[field] = {"value": value, "coverage": coverage, "reported_exchanges": count}
    measurements["exchanges"] = exchanges if type(exchanges) is int and exchanges >= 0 else None
    return measurements


def _review_usage(row_dir: Path) -> dict[str, Any]:
    """Count recorded provider reports, not the editable verdict's usage claim.

    Reader trajectories do not reliably record requested exchanges that died
    before a result. Values are therefore lower bounds with partial coverage.
    """
    path = row_dir / "canonical-trajectory.jsonl"
    usage = Usage()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("kind") == "claude.result" and isinstance(event.get("payload"), dict):
                usage = usage.record(event["payload"])
    result = usage_measurements(usage.summary())
    for field in USAGE_FIELDS:
        if result[field]["value"] is not None:
            result[field]["coverage"] = "partial"
    # Results count completed reports, not all requested exchanges.
    result["exchanges"] = None
    return result


def _row(path: Path, row: Row, *, authenticated: bool) -> dict[str, Any]:
    data = row.model_dump(mode="json")
    data["recorded_outcome"] = row.outcome
    data["authenticated"] = authenticated and row.outcome != "invalid"
    proof_usage, canonical_usage = usage_measurements({}), None
    data["strategy_evidence"] = None
    if data["authenticated"]:
        row_dir = path / row.run_dir  # contained by the existing board audit
        if row.mode == "batch":
            payload = json.loads((row_dir / "result.json").read_text(encoding="utf-8"))
        else:
            nested = _nested_run(row_dir)
            if nested is None:
                raise ComparisonRefused(f"{row_dir}: staged run disappeared after audit")
            payload = json.loads((nested / "manifest.json").read_text(encoding="utf-8"))
            if "strategy.json" in payload.get("artifacts", {}):
                strategy = json.loads((nested / "strategy.json").read_text(encoding="utf-8"))
                events = [json.loads(line) for line in (nested / "trajectory.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                if isinstance(strategy, dict) and any(event.get("kind") == "workflow.strategy" and event.get("payload") == strategy for event in events):
                    data["strategy_evidence"] = strategy
            canonical_usage = _review_usage(row_dir)
        proof_usage = usage_measurements(payload.get("usage") or {})
    else:
        data["outcome"] = "invalid"
        for name in (*USAGE_FIELDS, "exchanges", "turns", "wall_seconds", "lean_checks", "search_calls"):
            data[name] = None
    data["proof_usage"] = proof_usage
    data["canonical_review_usage"] = canonical_usage
    return data


def _usage_totals(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    totals = {}
    for field in USAGE_FIELDS:
        metrics = [(row.get(key) or usage_measurements({}))[field] for row in rows]
        values = [metric["value"] for metric in metrics if metric["value"] is not None]
        totals[field] = {
            "value": sum(values) if values else None,
            **{f"{status}_rows": sum(metric["coverage"] == status for metric in metrics)
               for status in ("complete", "partial", "missing")},
        }
    return totals


def _side(path: Path, board: Scoreboard, issues: tuple[str, ...]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [_row(path, row, authenticated=not issues) for row in board.rows]
    summary = {
        "path": str(path.resolve()), "label": board.label,
        "started_at": board.started_at.isoformat(),
        "finished_at": board.finished_at.isoformat() if board.finished_at else None,
        "interrupted": board.interrupted, "audit_issues": list(issues),
        "condition": board.condition.model_dump(mode="json"),
        "environment": board.environment.model_dump(mode="json"), "host": board.host,
        "rows": len(rows),
        "proof_usage": _usage_totals(rows, "proof_usage"),
        "canonical_review_usage": _usage_totals([r for r in rows if r["mode"] == "staged"], "canonical_review_usage"),
    }
    return summary, rows


def _controls(board: Scoreboard, rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = board.condition.model_dump(mode="json")
    values.pop("run_procedure_digest")
    values["environment"] = (environment_digest_of_board(board.model_dump(mode="json")) if board.host else None)
    workers = {row.workers for row in board.rows}
    values["workers"] = sorted(workers) if workers and None not in workers else None
    # A condition label alone cannot establish which strategy ran. Require
    # exact manifest-bound evidence from every proof row; twins use batch.
    proof_rows = [row for row in rows if row["expected"] == "true"]
    for field in ("strategy", "history_mode", "strategy_source_sha256"):
        evidence_key = "source_sha256" if field == "strategy_source_sha256" else field
        evidence = [(row["strategy_evidence"] or {}).get(evidence_key) for row in proof_rows]
        actual = evidence[0] if evidence and all(value == evidence[0] for value in evidence) else None
        if field != "strategy_source_sha256" and values[field] is not None and values[field] != actual:
            actual = None
        values[field] = actual or None
    return values


def compare(left: Path, right: Path, *, varying: Iterable[str], problems_path: Path,
            baseline_path: Path, right_baseline_path: Path | None = None) -> dict[str, Any]:
    """Read two scoreboards; retain failed audits but never credit their rows.

    Different toolchain conditions may use separate authenticated baselines.
    Source/host/strategy metadata remain recorded provenance, not independent
    attestation. No mean, winner, significance or causal attribution is inferred.
    """
    intended = set(varying)
    if unknown := intended - set(CONTROL_FIELDS):
        raise ComparisonRefused("unknown varying fields: " + ", ".join(sorted(unknown)))
    boards, summaries, sides = [], {}, {}
    try:
        for name, path, baseline in (("left", left, baseline_path), ("right", right, right_baseline_path or baseline_path)):
            issues = scoreboard_self_issues(path, problems_path=problems_path, baseline_path=baseline)
            board = Scoreboard.model_validate_json((path / "scoreboard.json").read_text(encoding="utf-8"))
            summary, rows = _side(path, board, issues)
            boards.append(board)
            summaries[name], sides[name] = summary, rows
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ComparisonRefused(str(error)) from error
    controls = [_controls(board, sides[name]) for board, name in zip(boards, ("left", "right"), strict=True)]
    comparisons = {}
    for field in CONTROL_FIELDS:
        a, b = (values[field] for values in controls)
        status = "unknown" if a is None or b is None or a == "" or b == "" else "equal" if a == b else "different"
        comparisons[field] = {"left": a, "right": b, "status": status, "intended_to_vary": field in intended}
    differences = sorted(field for field, value in comparisons.items() if value["status"] == "different")
    unknown = sorted(field for field, value in comparisons.items() if value["status"] == "unknown")
    unintended = sorted(set(differences) - intended)
    indexes = {name: {(row["id"], row["repeat"]): row for row in rows} for name, rows in sides.items()}
    # Never silently overwrite duplicate samples in a corrupt board.
    duplicates = any(len(indexes[name]) != len(rows) for name, rows in sides.items())
    pairs = []
    if not duplicates:
        for id_, repeat in sorted(indexes["left"].keys() | indexes["right"].keys()):
            a, b = (indexes[name].get((id_, repeat)) for name in ("left", "right"))
            pairs.append({"id": id_, "repeat": repeat, "left": a, "right": b,
                          "status": "paired" if a is not None and b is not None else "left_only" if b is None else "right_only"})
    return {
        "schema_version": 1, "sides": summaries, "rows": sides, "pairs": pairs,
        "comparability": {"fields": comparisons, "differences": differences, "unknown": unknown,
                          "unintended_differences": unintended, "intended_varying": sorted(intended),
                          "recorded_controls_match": not unknown and not unintended and not duplicates
                          and not any(summary["audit_issues"] for summary in summaries.values())},
        "notes": [
            "Descriptive paired observations; no causal winner or significance claim.",
            "Source, host, concurrency and treatment labels are recorded provenance, not independent attestation.",
            "All recorded attempts contribute usage, including unsolved attempts; absent and partial reports are not zero.",
            "Per-row wall seconds may overlap under concurrency and are not serial runtime.",
            "Canonical review usage is separate; provider reports are lower bounds because requested exchanges may be unrecorded.",
            "Duplicate slots prevent pairing; original rows and audit findings are retained.",
        ],
    }
