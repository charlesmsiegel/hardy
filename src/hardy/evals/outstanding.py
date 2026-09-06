"""What is left to do, under the pooling key this checkout would produce.

Read-only and free: a control agent asks this before deciding what to spend on,
and `evals run`/`evals baseline` ask it to fill in a selection nobody named.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain import EnvironmentIdentity
from . import sweep


def environment_digest_of_board(board: dict[str, Any]) -> str:
    """A board's own environment digest, computed the way a baseline's is.

    Over its recorded `environment` and `host` (`sweep.environment_digest_of`)
    -- a board stores those two components, not a precomputed digest -- so
    this is the single place that turns them into the same digest `evals run`
    would produce today. `pool.py` imports this rather than defining a second
    copy, so the two can never silently disagree on what "the same
    environment" means.
    """
    environment = EnvironmentIdentity.model_validate(board.get("environment") or {})
    host = board.get("host") or {}
    return sweep.environment_digest_of(environment, host)


def matching_boards(scoreboards_root: Path, *, key: tuple[str | None, str]) -> list[str]:
    """The label of every board under `scoreboards_root` whose condition and
    recorded environment together equal `key`, sorted.

    Shared by `evaluated_ids` (which rows may be claimed as evidence) and
    `evals todo` (which boards it is telling the truth about), so the two
    never drift on what counts as a match.

    A board carrying no `run_procedure_digest` matches nothing: it was
    written before this gate existed, so nothing establishes which code
    produced it, and treating a blank as agreement would make the gate
    decorative -- the same rule `staleness` already applies to a blank
    environment digest. An unreadable or malformed board is not evidence of
    anything either, and is skipped rather than raised.
    """
    if not scoreboards_root.exists():
        return []
    matched: list[str] = []
    for board_path in sorted(scoreboards_root.glob("*/scoreboard.json")):
        try:
            board = json.loads(board_path.read_text(encoding="utf-8"))
            condition = board.get("condition") or {}
            run_digest = condition.get("run_procedure_digest")
            if run_digest is None:
                continue
            if (run_digest, environment_digest_of_board(board)) != key:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        matched.append(board_path.parent.name)
    return sorted(matched)


def evaluated_ids(scoreboards_root: Path, *, key: tuple[str | None, str]) -> set[str]:
    """Every entry id already run under this exact pooling key."""
    found: set[str] = set()
    for label in matching_boards(scoreboards_root, key=key):
        try:
            board = json.loads((scoreboards_root / label / "scoreboard.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue      # an unreadable board is not evidence of anything
        found.update(str(row.get("id")) for row in board.get("rows") or [])
    return found


def unbaselined_active(problems: Any, baseline: Any | None) -> list[str]:
    """Active entries with no baseline row -- what `evals baseline`'s default resweeps.

    Needs no run digest: a baseline sweep is Lean-only, gated by the corpus
    and the toolchain, not by which model, mode or limits a run would use.
    `baseline=None` (no tier file written yet) means every active entry is
    unbaselined.
    """
    entries = baseline.entries if baseline is not None else {}
    return [e.id for e in problems.entries if e.status == "active" and e.id not in entries]


def outstanding(problems: Any, baseline: Any, scoreboards_root: Path, *, key: tuple[str | None, str]) -> dict[str, list[str]]:
    """The active entries with no baseline row, and those with no row under `key`.

    Only `active` entries: a `candidate` has not been checked by a human yet,
    and spending model time on one would benchmark a draft.
    """
    active = [e.id for e in problems.entries if e.status == "active"]
    done = evaluated_ids(scoreboards_root, key=key)
    return {
        "unbaselined_active": unbaselined_active(problems, baseline),
        "unevaluated_active": [id_ for id_ in active if id_ not in done],
    }
