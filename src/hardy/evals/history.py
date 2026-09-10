"""Chronological views of exact-slot comparisons, without pooling or attribution.

The comparison reader owns artifact authentication, controls, exposure and usage
coverage. This reader orders recorded timestamps and counts matched observations;
missing controls never become equality, and false-statement outcomes are kept
separate from proof gains. Dates describe the record, not an independent clock.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from hardy.evals.compare import CONTROL_FIELDS, ComparisonRefused, compare

MAX_BOARDS = 64
MAX_BOARD_BYTES = 8 << 20
MAX_TOTAL_BYTES = 32 << 20


class HistoryRefused(ValueError):
    """A history request is ambiguous, excessive, or changed while being read."""


def _bounded(values: Iterable[Any], limit: int, name: str) -> list[Any]:
    found = list(islice(values, limit + 1))
    if len(found) > limit:
        raise HistoryRefused(f"history accepts at most {limit} {name}")
    return found


def _bytes(path: Path) -> bytes:
    with (path / "scoreboard.json").open("rb") as stream:
        data = stream.read(MAX_BOARD_BYTES + 1)
    if len(data) > MAX_BOARD_BYTES:
        raise HistoryRefused(f"{path}: scoreboard exceeds {MAX_BOARD_BYTES} bytes")
    return data


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _finite_number(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"nonfinite JSON number: {value}")
    return parsed


def _observed(comparison: dict[str, Any]) -> dict[str, Any]:
    proof = {"pairs": 0, "gains": 0, "losses": 0, "unchanged": 0, "delta": None}
    negative: Counter[str] = Counter()
    unmatched = unauthenticated = mismatched = 0
    for pair in comparison["pairs"]:
        left, right = pair["left"], pair["right"]
        if left is None or right is None:
            unmatched += 1
        elif not left["authenticated"] or not right["authenticated"]:
            unauthenticated += 1
        elif left["expected"] != right["expected"]:
            mismatched += 1
        elif left["expected"] == "false":
            negative[f"{left['outcome']} -> {right['outcome']}"] += 1
        else:
            proof["pairs"] += 1
            before, after = left["outcome"] == "solved", right["outcome"] == "solved"
            proof["gains" if after and not before else "losses" if before and not after else "unchanged"] += 1
    if proof["pairs"]:
        proof["delta"] = proof["gains"] - proof["losses"]
    return {"proof": proof, "negative_outcomes": dict(negative), "unmatched": unmatched,
            "unauthenticated": unauthenticated, "expected_mismatch": mismatched}


def timeline(paths: Iterable[Path], *, varying: Iterable[str] = (), problems_path: Path,
             baseline_path: Path, board_baselines: Iterable[tuple[Path, Path]] = ()) -> dict[str, Any]:
    """Audit up to 64 boards and compare adjacent dated points without merging them.

    Per-board baseline overrides are explicit pairs, so duplicate overrides and
    overrides for boards outside this request can be refused before any audit.
    Unreadable and undated inputs remain findings rather than silently vanishing.
    """
    selected = [Path(path).resolve() for path in _bounded(paths, MAX_BOARDS, "boards")]
    if not selected:
        raise HistoryRefused("history requires at least one scoreboard")
    if len(set(selected)) != len(selected):
        raise HistoryRefused("duplicate scoreboard paths are not independent observations")
    intended = set(_bounded(varying, len(CONTROL_FIELDS), "varying fields"))
    if unknown := intended - set(CONTROL_FIELDS):
        raise HistoryRefused("unknown varying fields: " + ", ".join(sorted(unknown)))
    baselines = {}
    for board, baseline in _bounded(board_baselines, MAX_BOARDS, "baseline overrides"):
        board = Path(board).resolve()
        if board not in selected or board in baselines:
            raise HistoryRefused(f"duplicate or unselected board baseline: {board}")
        baselines[board] = Path(baseline).resolve()

    dated, unplaced, identities = [], [], {}
    total_bytes = 0
    for index, path in enumerate(selected):
        baseline = baselines.get(path, Path(baseline_path).resolve())
        point: dict[str, Any] = {"path": str(path), "baseline": str(baseline), "issues": [],
                                 "chronology_issues": [], "summary": None, "rows": []}
        try:
            data = _bytes(path)
        except OSError as error:
            point["issues"].append(str(error))
            unplaced.append(point)
            continue
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise HistoryRefused(f"scoreboard inputs exceed {MAX_TOTAL_BYTES} total bytes")
        digest = hashlib.sha256(data).hexdigest()
        if digest in identities.values():
            raise HistoryRefused(f"duplicate scoreboard bytes: {path}")
        identities[path] = digest
        try:
            raw = json.loads(data, parse_constant=_finite_number, parse_float=_finite_number)
            if not isinstance(raw, dict):
                raise ValueError("scoreboard must be a JSON object")
        except (ValueError, UnicodeError) as error:
            point["issues"].append(str(error))
            unplaced.append(point)
            continue
        start, finish = _timestamp(raw.get("started_at")), _timestamp(raw.get("finished_at"))
        point["started_at"], point["finished_at"] = raw.get("started_at"), raw.get("finished_at")
        if start is None:
            point["chronology_issues"].append("started_at is missing, invalid, or lacks a timezone; chronology unknown")
        if finish is None:
            point["chronology_issues"].append("finished_at is missing, invalid, or lacks a timezone; interval end unknown")
        elif start is not None and finish < start:
            point["chronology_issues"].append("finished_at precedes started_at; interval unknown")
            finish = None
        try:
            audited = compare(path, path, varying=intended, problems_path=problems_path, baseline_path=baseline)
            point["summary"], point["rows"] = audited["sides"]["left"], audited["rows"]["left"]
            point["issues"].extend(point["summary"]["audit_issues"])
        except ComparisonRefused as error:
            point["issues"].append(str(error))
            point["recorded_rows"] = raw.get("rows")
        if start is None:
            unplaced.append(point)
        else:
            dated.append((start, index, finish, point))
    dated.sort(key=lambda item: (item[0], item[1]))
    transitions = []
    for left, right in zip(dated, dated[1:], strict=False):
        start, _, finish, before = left
        next_start, _, _, after = right
        timing = ("unknown" if finish is None else "overlap" if next_start < finish
                  else "simultaneous_start" if next_start == start else "nonoverlapping")
        change: dict[str, Any] = {"left": before["path"], "right": after["path"], "timing": timing,
                                  "comparability": "incomparable", "comparable_delta": None,
                                  "comparison": None, "observed": None, "issues": []}
        try:
            comparison = compare(Path(before["path"]), Path(after["path"]), varying=intended,
                                 problems_path=problems_path, baseline_path=Path(before["baseline"]),
                                 right_baseline_path=Path(after["baseline"]))
            controls = comparison["comparability"]
            status = ("incomparable" if controls["unintended_differences"]
                      or any(side["audit_issues"] for side in comparison["sides"].values())
                      else "comparable" if controls["recorded_controls_match"] else "unknown")
            observed = _observed(comparison)
            change.update(comparison=comparison, observed=observed, comparability=status,
                          comparable_delta=observed["proof"]["delta"] if status == "comparable" else None)
        except ComparisonRefused as error:
            change["issues"].append(str(error))
        transitions.append(change)
    for path, digest in identities.items():
        try:
            same = hashlib.sha256(_bytes(path)).hexdigest() == digest
        except OSError:
            same = False
        if not same:
            raise HistoryRefused(f"{path}: scoreboard changed while reading history")
    return {
        "schema_version": 1, "varying": sorted(intended),
        "boards": [point for _, _, _, point in dated], "unplaced": unplaced,
        "transitions": transitions,
        "notes": [
            "Recorded timestamps order this view; ties retain input order. They are metadata, not an independently authenticated clock.",
            "Matched-slot gains, losses and deltas are descriptive observations, not causal attribution, significance, or evidence of independent samples.",
            "Unknown or incompatible controls leave comparable_delta null. Explicit --vary fields do not supply a contemporaneous control or establish causality.",
            "Boards are never pooled. Negative/twin outcomes are separate from proof gains and losses; missing and invalid observations are not failures or zero spend.",
            "Overlapping and open run intervals cannot establish serial progression or serial runtime. Missing or undated boards remain unplaced findings.",
        ],
    }
