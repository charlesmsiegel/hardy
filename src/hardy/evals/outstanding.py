"""What is left to do, under the pooling key this checkout would produce.

Read-only and free: a control agent asks this before deciding what to spend on,
and `evals run`/`evals baseline` ask it to fill in a selection nobody named.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hardy.evals import sweep
from hardy.formal.contracts import EnvironmentIdentity


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

    The cheap half of what `evals pool` asks of a board. `poolable_boards`
    adds the other half, the board's own audit, before any row is claimed as
    evidence or any board is counted.

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
            from hardy.evals.exposure import board_exposure_issues
            if board_exposure_issues(board_path.parent, board):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        matched.append(board_path.parent.name)
    return sorted(matched)


def poolable_boards(scoreboards_root: Path, *, key: tuple[str | None, str], problems_path: Path,
                    baseline_path: Path) -> tuple[list[str], dict[str, list[str]]]:
    """The boards `evals pool` would admit under `key`, and those it would refuse.

    Returns the admitted labels, sorted, and each refused label with the
    findings its own audit (`scoreboard_self_issues`, the check `pool.pool`
    applies) reported. A board that fails that audit can never be pooled, so
    none of its rows is evidence that an entry was run, and `evals todo`
    must not count it: that would drop the entry from every default run
    while nothing poolable exists for it.

    The audit re-derives every row from its run directory and reads the
    corpus and tier file, so it costs more than the key match; it runs only
    on the boards that already match.
    """
    from hardy.evals.scoreboard import scoreboard_self_issues

    admitted: list[str] = []
    refused: dict[str, list[str]] = {}
    for label in matching_boards(scoreboards_root, key=key):
        try:
            issues = list(scoreboard_self_issues(scoreboards_root / label, problems_path=problems_path,
                                                 baseline_path=baseline_path))
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues = [f"the board's own audit could not run: {type(error).__name__}: {error}"]
        if issues:
            refused[label] = issues
        else:
            admitted.append(label)
    return admitted, refused


def evaluated_ids(scoreboards_root: Path, *, boards: list[str]) -> tuple[set[str], set[str]]:
    """The entry ids `boards` fully cover, and those they cover only in part.

    `boards` are labels `poolable_boards` admitted. An id is complete when
    its rows across them, leaving out `invalid` ones, fill every repeat slot
    `range(condition.repeats)`; `repeats` is in the pooling key, so every
    admitted board shares it. An id with some slots but not all is partial:
    an interrupted board holds `(X, 0)` of three, say. Counting it done would
    pool one sample beside other entries' three, the unbalanced design the
    key keeps `repeats` in to prevent.
    """
    slots: dict[str, set[int]] = {}
    repeats = 1
    for label in boards:
        try:
            board = json.loads((scoreboards_root / label / "scoreboard.json").read_text(encoding="utf-8"))
            repeats = max(repeats, int((board.get("condition") or {}).get("repeats") or 1))
            for row in board.get("rows") or []:
                if row.get("outcome") == "invalid":
                    continue   # recorded, but not a sample of anything
                slots.setdefault(str(row.get("id")), set()).add(int(row.get("repeat") or 0))
        except (OSError, ValueError, TypeError, AttributeError):
            continue      # an unreadable board is not evidence of anything
    complete = {id_ for id_, have in slots.items() if set(range(repeats)) <= have}
    return complete, set(slots) - complete


def unbaselined_active(problems: Any, baseline: Any | None) -> list[str]:
    """Active entries with no usable baseline row -- what `evals baseline`'s default resweeps.

    Usable is `sweep.row_carries`: a row measured against another statement
    (or recording no statement digest), or a twin's row with no negation
    sweep, is one `staleness` refuses and the sweep would not reuse, so it
    counts as unbaselined here too. Otherwise the repair `staleness` names --
    re-running `hardy evals baseline` -- would find nothing to do.

    Needs no run digest: a baseline sweep is Lean-only, gated by the corpus
    and the toolchain, not by which model, mode or limits a run would use.
    `baseline=None` (no tier file written yet) means every active entry is
    unbaselined.
    """
    return [
        e.id for e in problems.entries
        if e.status == "active" and (baseline is None or not sweep.row_carries(baseline, e, e.statement_digest()))
    ]


def outstanding(problems: Any, baseline: Any, scoreboards_root: Path, *, key: tuple[str | None, str],
                problems_path: Path, baseline_path: Path) -> dict[str, Any]:
    """What is left under `key`, judged by the boards `evals pool` would admit.

    - `boards_counted`: the boards admitted as evidence; `boards_refused`:
      those matching `key` that fail their own audit, with its findings.
    - `unbaselined_active`: active entries with no usable baseline row.
    - `unevaluated_active`: active entries with no valid sample on an
      admitted board -- what the default `evals run` selects.
    - `partially_evaluated_active`: active entries holding some of their
      repeats but not all. The default run leaves them out, because a fresh
      board repeating their slots would not pool with the one holding them.

    Only `active` entries: a `candidate` has not been checked by a human yet,
    and spending model time on one would benchmark a draft.
    """
    admitted, refused = poolable_boards(scoreboards_root, key=key, problems_path=problems_path,
                                        baseline_path=baseline_path)
    complete, partial = evaluated_ids(scoreboards_root, boards=admitted)
    active = [e.id for e in problems.entries if e.status == "active"]
    return {
        "boards_counted": admitted,
        "boards_refused": refused,
        "unbaselined_active": unbaselined_active(problems, baseline),
        "unevaluated_active": [id_ for id_ in active if id_ not in complete and id_ not in partial],
        "partially_evaluated_active": [id_ for id_ in active if id_ in partial],
    }
