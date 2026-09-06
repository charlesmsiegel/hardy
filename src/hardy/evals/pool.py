"""The combined score, derived from immutable per-batch scoreboards.

A scoreboard is one condition on one day. Accumulating across days is a *view*
over several of them, never a mutated artifact: this reads, refuses what it
cannot honestly combine, and writes only its own output. Every figure it
states can be recomputed from the boards it names.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class PoolRefused(ValueError):
    """Boards this will not combine, and why."""


def pool(labels: list[Path], *, problems_path: Path, baseline_path: Path) -> dict[str, Any]:
    """Combine the scoreboards at `labels` into one derived score.

    Every board is re-validated with the existing `validate_scoreboard`
    first (spec's own audit, not a second implementation of it: a board that
    cannot pass its own audit is not evidence). Boards are then combined only
    when every one shares the same `(run_procedure_digest, environment_digest)`
    -- refusing by naming exactly which of the two differs, since a control
    agent reads this message and "incompatible" alone tells it nothing to
    act on -- and only when no `(id, repeat)` pair is claimed by more than one
    board, since the same entry run twice under one condition is a fact to
    report, not a tie to break silently by picking one.
    """
    from .corpus import load_corpus, manifest_digest
    from .outstanding import environment_digest_of_board
    from .problems import sha256_of
    from .runner import Scoreboard
    from .scoreboard import active_ids, aggregate, validate_scoreboard
    from .sweep import Baseline

    problems = load_corpus(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))

    boards: list[tuple[Path, Scoreboard]] = []
    for path in labels:
        # The existing audit, not a second implementation of it: a board
        # this cannot verify is not evidence, and re-deriving the check here
        # would be a second thing to keep correct.
        issues = validate_scoreboard(path, problems_path=problems_path, baseline_path=baseline_path)
        if issues:
            raise PoolRefused(f"{path.name} does not validate: " + "; ".join(issues))
        board = Scoreboard.model_validate_json((path / "scoreboard.json").read_text(encoding="utf-8"))
        boards.append((path, board))

    # Each board's own pooling key -- the same two fields `evals todo`
    # computes for a run that hasn't happened yet -- read off the board it
    # actually names, not recomputed from this checkout.
    keys: dict[str, tuple[str | None, str]] = {
        path.name: (
            board.condition.run_procedure_digest,
            environment_digest_of_board({"environment": board.environment.model_dump(mode="json"), "host": board.host}),
        )
        for path, board in boards
    }

    run_digests = {run for run, _ in keys.values()}
    if len(run_digests) > 1:
        detail = ", ".join(f"{name}={run!r}" for name, (run, _) in sorted(keys.items()))
        raise PoolRefused(f"these boards differ in run_procedure_digest and cannot be pooled: {detail}")

    environment_digests = {environment for _, environment in keys.values()}
    if len(environment_digests) > 1:
        detail = ", ".join(f"{name}={environment!r}" for name, (_, environment) in sorted(keys.items()))
        raise PoolRefused(f"these boards differ in environment_digest and cannot be pooled: {detail}")

    run_digest = next(iter(run_digests))
    if run_digest is None:
        # Not "differ": every board here agrees, on carrying nothing.
        # A board written before this gate existed establishes no code as
        # having produced it, and treating a blank as agreement would make
        # the gate decorative -- the same rule `staleness` applies to a
        # blank environment digest.
        raise PoolRefused(
            "a board records no run_procedure_digest; it was written before the gate existed "
            "and cannot be pooled"
        )
    environment_digest = next(iter(environment_digests))

    rows = []
    claimed_by: dict[tuple[str, int], str] = {}
    for path, board in boards:
        for row in board.rows:
            slot = (row.id, row.repeat)
            if slot in claimed_by:
                raise PoolRefused(
                    f"{row.id} repeat {row.repeat} appears in both {claimed_by[slot]} and {path.name}; "
                    "the same entry ran twice under one condition"
                )
            claimed_by[slot] = path.name
            rows.append(row)

    aggregates = aggregate(rows, baseline, active_ids=active_ids(problems))
    return {
        "boards": sorted(keys),
        "pooling_key": {"run_procedure_digest": run_digest, "environment_digest": environment_digest},
        "problems_sha256": manifest_digest(problems_path),
        "baseline_sha256": sha256_of(baseline_path),
        "rows": [row.model_dump(mode="json") for row in rows],
        "aggregates": aggregates.model_dump(mode="json"),
        # A contended per-row wall_seconds summed across rows overstates
        # serial wall clock (Row.workers' own docstring); labelling the
        # figure is what stops a reader from mistaking it for one.
        "wall_seconds_note": (
            f"summed under up to {aggregates.totals.workers} concurrent workers; "
            "not a serial wall-clock figure"
        ),
    }
