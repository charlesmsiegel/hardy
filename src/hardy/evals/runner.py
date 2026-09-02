"""The set runner: every entry through batch or staged, one row each, refusing before it spends (spec §3)."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .. import __version__
from ..domain import EnvironmentIdentity, FrozenModel
from .problems import Entry, ProblemSet, load_problems, sha256_of
from .scoreboard import Aggregates, Row, aggregate, batch_row, staged_row
from .sweep import Baseline, staleness

BatchRunner = Callable[[Entry, Path, int, float], None]
StagedRunner = Callable[[Entry, Path, str], None]   # (entry, row_dir, model): writes the nested run and canonical.json


class RefusedRun(RuntimeError):
    """A §3.1 gate: the run did not start, and this is why."""


class Condition(FrozenModel):
    model: str
    backend: str
    mode: Literal["batch", "staged"]
    prompt_set_sha256: str
    hardy_version: str
    limits: dict[str, float | int]
    repeats: int
    selection: dict[str, Any]


class Scoreboard(FrozenModel):
    schema_version: Literal[1] = 1
    label: str
    condition: Condition
    environment: EnvironmentIdentity
    baseline_sha256: str
    problems_sha256: str
    rows: tuple[Row, ...]
    aggregates: Aggregates
    started_at: datetime
    finished_at: datetime | None
    interrupted: bool


def select(problems: ProblemSet, baseline: Baseline, *, only: list[str] | None, tiers: list[int] | None, twins: bool) -> tuple[Entry, ...]:
    # `only`'s own order, not the set's: a caller who names entries explicitly
    # is choosing a run order, not just a subset (spec §3.2 "select"). Repeats
    # are folded to their first occurrence -- `--only t,t` must not run `t`
    # twice into the same row directory -- and a name the list does not carry
    # is a gate this refuses before anything runs, not a selection silently
    # narrowed by ignoring it.
    if only is None:
        ids = [entry.id for entry in problems.entries]
    else:
        seen: set[str] = set()
        ids = []
        for id_ in only:
            if id_ not in seen:
                seen.add(id_)
                ids.append(id_)
        known = {entry.id for entry in problems.entries}
        unknown = [id_ for id_ in ids if id_ not in known]
        if unknown:
            raise RefusedRun("--only names entries not in the list: " + ", ".join(unknown))
    chosen = []
    for id_ in ids:
        entry = problems.by_id(id_)
        if tiers is not None and baseline.entries[entry.id].tier not in tiers:
            continue
        if entry.expected == "false" and not twins:
            continue
        chosen.append(entry)
    return tuple(chosen)


def _write(path: Path, board: Scoreboard) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(board.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_set(*, label: str, problems_path: Path, baseline_path: Path, scoreboards_root: Path, condition: Condition,
            environment: EnvironmentIdentity, batch_runner: BatchRunner, staged_runner: StagedRunner | None = None,
            now: Callable[[], datetime], report: Callable[[str], None]) -> Path:
    problems = load_problems(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    issues = staleness(baseline, problems_sha256=sha256_of(problems_path), environment=environment)
    if issues:
        raise RefusedRun("; ".join(issues))
    out = scoreboards_root / label
    if out.exists():
        raise RefusedRun(f"{out} already exists; a label is one condition on one day")
    if condition.mode == "staged" and staged_runner is None:
        raise RefusedRun("staged mode needs a staged runner")
    sel = condition.selection
    entries = select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True))
    out.mkdir(parents=True)
    rows: list[Row] = []
    board = Scoreboard(label=label, condition=condition, environment=environment, baseline_sha256=sha256_of(baseline_path),
                       problems_sha256=sha256_of(problems_path), rows=(), aggregates=aggregate([], baseline),
                       started_at=now(), finished_at=None, interrupted=False)
    _write(out / "scoreboard.json", board)
    try:
        for entry in entries:
            for repeat in range(condition.repeats):
                tier = baseline.entries[entry.id].tier
                # Twins never run staged: the loop grades every unverified run partial (#23).
                mode = "batch" if entry.expected == "false" else condition.mode
                report(f"{entry.id} [{mode} {repeat}]")
                row_dir = out / "runs" / entry.id / f"{mode}-{repeat}"
                if mode == "batch":
                    # A batch-mode condition's own limits govern a batch row.
                    # A twin under a staged condition still runs batch (the
                    # loop grades every unverified staged run partial, #23),
                    # but staged limits carry active_seconds/proof_seconds/
                    # official_checks, not max_turns/wall_seconds -- so its
                    # budget is the separately-recorded twin_* pair instead.
                    if condition.mode == "batch":
                        max_turns, wall_seconds = int(condition.limits["max_turns"]), float(condition.limits["wall_seconds"])
                    else:
                        max_turns, wall_seconds = int(condition.limits["twin_max_turns"]), float(condition.limits["twin_wall_seconds"])
                    batch_runner(entry, row_dir, max_turns, wall_seconds)
                    row = batch_row(entry, tier, row_dir, out, repeat=repeat)
                else:
                    row_dir.mkdir(parents=True, exist_ok=True)
                    staged_runner(entry, row_dir, condition.model)  # type: ignore[misc]
                    row = staged_row(entry, tier, row_dir, out, repeat=repeat)
                rows.append(row)
                report(f"  -> {row.outcome} ({row.terminal_reason})")
                board = board.model_copy(update={"rows": tuple(rows), "aggregates": aggregate(rows, baseline)})
                _write(out / "scoreboard.json", board)
    except BaseException:
        _write(out / "scoreboard.json", board.model_copy(update={"interrupted": True}))
        raise
    _write(out / "scoreboard.json", board.model_copy(update={"finished_at": now()}))
    return out


def _batch_runner(config: Any) -> BatchRunner:
    from ..cli import runtime_factory
    from ..lean import LeanTools
    from ..models import Request
    from ..runner import run

    def run_one(entry: Entry, output: Path, max_turns: int, wall_seconds: float) -> None:
        request = Request.from_dict({"declaration": entry.declaration(), "informal_claim": entry.input, "imports": list(entry.imports)})
        lean = LeanTools(request, config.lean_command, timeout=config.lean_timeout, project=config.lean_project)
        run(request, runtime_factory(str(config.model)), lean, output, max_turns=max_turns, wall_seconds=wall_seconds)

    return run_one


BATCH_DEFAULT_MAX_TURNS = 60
BATCH_DEFAULT_WALL_SECONDS = 1800.0


def run_set_command(args: argparse.Namespace, config: Any) -> int:
    from ..lean import environment_identity
    from ..prompts import PROMPT_SET_SHA256
    from ..runner import WARNING

    if not args.acknowledge_unsafe_execution:
        print(WARNING, file=sys.stderr)
        print("Re-run with --acknowledge-unsafe-execution to accept this for every run in the set.", file=sys.stderr)
        return 2
    print(WARNING, file=sys.stderr)
    if args.mode == "staged" and (args.max_turns is not None or args.wall_seconds is not None):
        print(
            "Refused: --max-turns/--wall-seconds do not govern a staged run; its budgets are "
            "config.limits.active_seconds, proof_seconds and official_checks",
            file=sys.stderr,
        )
        return 2
    environment = environment_identity(config.lean_project, lean_command=(str(config.lake), "env", "lean"), timeout_seconds=config.limits.lean_process_seconds)
    if args.mode == "staged":
        # A staged run is bounded by the workflow's own budgets, not
        # --max-turns/--wall-seconds (refused above); a twin still runs
        # batch under staged mode (#23), so its budget is recorded too.
        limits: dict[str, float | int] = {
            "active_seconds": config.limits.active_seconds, "proof_seconds": config.limits.proof_seconds,
            "official_checks": config.limits.official_checks,
            "twin_max_turns": BATCH_DEFAULT_MAX_TURNS, "twin_wall_seconds": BATCH_DEFAULT_WALL_SECONDS,
        }
    else:
        limits = {
            "max_turns": args.max_turns if args.max_turns is not None else BATCH_DEFAULT_MAX_TURNS,
            "wall_seconds": args.wall_seconds if args.wall_seconds is not None else BATCH_DEFAULT_WALL_SECONDS,
        }
    condition = Condition(
        model=str(args.model or config.model), backend=args.backend, mode=args.mode, prompt_set_sha256=PROMPT_SET_SHA256,
        hardy_version=__version__, limits=limits, repeats=args.repeats,
        selection={"only": args.only.split(",") if args.only else None, "tiers": [int(t) for t in args.tiers.split(",")] if args.tiers else None,
                   "twins": not args.no_twins},
    )
    staged = None
    if args.mode == "staged":
        from .staged import staged_runner
        staged = staged_runner(config, backend=args.backend)
    try:
        out = run_set(label=args.label, problems_path=args.problems, baseline_path=args.baseline, scoreboards_root=args.scoreboards,
                      condition=condition, environment=environment, batch_runner=_batch_runner(config), staged_runner=staged,
                      now=lambda: datetime.now(UTC), report=lambda line: print(line, file=sys.stderr))
    except RefusedRun as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 2
    print(f"Scoreboard: {out / 'scoreboard.json'}")
    return 0
