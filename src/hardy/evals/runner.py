"""The set runner: every entry through batch or staged, one row each, refusing before it spends (spec §3)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
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

# A single safe path component: no `/` or `\`, no leading `.` or `-`, nothing
# that could turn `scoreboards_root / label` into a path outside
# `evals/scoreboards` (item 4). `..` alone, `../x`, `a/b`, and every absolute
# path (POSIX or Windows) all fail this on the first character or the
# separator, so no further check is needed.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RefusedRun(RuntimeError):
    """A §3.1 gate: the run did not start, and this is why."""


class Condition(FrozenModel):
    model: str
    backend: str
    mode: Literal["batch", "staged"]
    # Both always recorded: a twin still runs batch under a staged condition
    # (#23), so a staged scoreboard's rows can be governed by either prompt
    # set, and each hash must cover only the templates that governed it.
    staged_prompt_set_sha256: str
    batch_prompt_set_sha256: str
    hardy_version: str
    # The Git revision the run was made from, `-dirty` suffixed when the
    # working tree carried uncommitted changes, `None` when it could not be
    # identified (no `git`, no `.git`). Evals are run from a source checkout
    # and no release bump occurs per commit, so `hardy_version` alone cannot
    # distinguish two runs made from different commits of the same release
    # (item 8). Defaulted so every existing `Condition(...)` call site --
    # test fixtures included -- need not name it.
    source_revision: str | None = None
    limits: dict[str, float | int]
    repeats: int
    selection: dict[str, Any]


def source_revision(root: Path) -> str | None:
    """The Git commit this process's source checkout is at, or `None`.

    Never raises: a source checkout with no `.git` (a stripped clone, a
    packaging step run outside the repository) or no `git` on `PATH` must not
    turn "record the revision" into a refusal to run a set. `-dirty` is
    appended when `git status --porcelain` reports uncommitted changes, so a
    scoreboard's `condition` cannot be mistaken for stating a commit's own
    committed state when the tree that actually ran had more than that.
    """
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0:
        return None
    revision = head.stdout.strip()
    if not revision:
        return None
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return revision
    if status.returncode == 0 and status.stdout.strip():
        revision += "-dirty"
    return revision


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
    # newline="\n": the same repository-evidence integrity concern as
    # commands.py's baseline write -- Path.write_text's default would
    # checkin a scoreboard as CRLF on Windows despite `.gitattributes`
    # marking evals/scoreboards/** -text so its bytes stay the ones any
    # digest is taken over.
    tmp.write_text(json.dumps(board.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def run_set(*, label: str, problems_path: Path, baseline_path: Path, scoreboards_root: Path, condition: Condition,
            environment: EnvironmentIdentity, batch_runner: BatchRunner, staged_runner: StagedRunner | None = None,
            now: Callable[[], datetime], report: Callable[[str], None]) -> Path:
    if not LABEL_RE.fullmatch(label):
        # Before anything is read or created: `scoreboards_root / label`
        # would otherwise resolve outside `evals/scoreboards` for a label
        # like `../../new-eval` or an absolute path, and the run tree would
        # be written there despite the documented output contract (item 4).
        raise RefusedRun(f"--label must be a single path component matching {LABEL_RE.pattern!r}, not {label!r}")
    problems = load_problems(problems_path)
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    issues = staleness(baseline, problems_sha256=sha256_of(problems_path), environment=environment,
                       problem_ids=[entry.id for entry in problems.entries])
    if issues:
        raise RefusedRun("; ".join(issues))
    out = scoreboards_root / label
    if out.exists():
        raise RefusedRun(f"{out} already exists; a label is one condition on one day")
    if condition.mode == "staged" and staged_runner is None:
        raise RefusedRun("staged mode needs a staged runner")
    sel = condition.selection
    entries = select(problems, baseline, only=sel.get("only"), tiers=sel.get("tiers"), twins=sel.get("twins", True))
    if not entries:
        # Before `out` is created: `--tiers 2` against a baseline with no
        # tier-2 entries, or `--only <twin> --no-twins`, would otherwise
        # write a finished, zero-row scoreboard that `hardy evals check`
        # accepts (the same empty selection derives the same empty expected
        # order), presenting a nominally completed experiment with no
        # samples (item 5).
        raise RefusedRun("the selection matches no entries (tiers/only/twins filters left nothing to run)")
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


def _batch_runner(config: Any, model: str) -> BatchRunner:
    from ..cli import runtime_factory
    from ..lean import LeanTools
    from ..models import Request
    from ..runner import run

    def run_one(entry: Entry, output: Path, max_turns: int, wall_seconds: float) -> None:
        request = Request.from_dict({"declaration": entry.declaration(), "informal_claim": entry.input, "imports": list(entry.imports)})
        # The same command `environment_identity` was asked about for this
        # run's `environment` field (run_set_command), not `config.lean_
        # command`: a global `--lean-command` that differs from `config.lake
        # env lean` would otherwise check every batch proof against a
        # different toolchain from the one the scoreboard's `environment`
        # and the baseline sweep both name, so a row could pass its own
        # checks under a Lean this experiment was never actually measured
        # against (item 2).
        lean = LeanTools(request, (str(config.lake), "env", "lean"), timeout=config.lean_timeout, project=config.lean_project)
        # `model`, not `config.model`: the condition already recorded
        # whichever model `--model` selected (run_set_command), and every row
        # must actually be produced by that model -- not silently by
        # whatever `config.model` happens to be, which under `--model
        # override@test` would be a different one (item 1).
        run(request, runtime_factory(model), lean, output, max_turns=max_turns, wall_seconds=wall_seconds)

    return run_one


BATCH_DEFAULT_MAX_TURNS = 60
BATCH_DEFAULT_WALL_SECONDS = 1800.0


def run_set_command(args: argparse.Namespace, config: Any) -> int:
    from ..lean import environment_identity
    from ..prompts import BATCH_PROMPT_SET_SHA256, PROMPT_SET_SHA256
    from ..runner import WARNING

    if args.backend != "claude":
        print(
            "Refused: the evals runner drives the Claude backend only: the batch runner, the "
            "canonical reader and staged tool-event counting are Claude-shaped; a Codex condition "
            "would attribute Claude runs to Codex",
            file=sys.stderr,
        )
        return 2
    from .commands import _refuse_missing

    refusal = _refuse_missing(args.problems, args.baseline)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
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
    try:
        environment = environment_identity(config.lean_project, lean_command=(str(config.lake), "env", "lean"), timeout_seconds=config.limits.lean_process_seconds)
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        print(f"Refused: the Lean toolchain could not be identified: {error}", file=sys.stderr)
        return 2
    if args.mode == "staged":
        # A staged run is bounded by the workflow's own budgets, not
        # --max-turns/--wall-seconds (refused above); a twin still runs
        # batch under staged mode (#23), so its budget -- and, since
        # `_batch_runner` hands a twin's `LeanTools` the same
        # `config.lean_timeout` a staged run's own checks use, its per-check
        # Lean timeout too -- is recorded here. `lean_process_seconds`
        # governs the toolchain-identity probe, not a proof check, but it is
        # still one of the run's own budgets and belongs beside the others.
        limits: dict[str, float | int] = {
            "active_seconds": config.limits.active_seconds, "proof_seconds": config.limits.proof_seconds,
            "official_checks": config.limits.official_checks, "lean_process_seconds": config.limits.lean_process_seconds,
            "twin_max_turns": BATCH_DEFAULT_MAX_TURNS, "twin_wall_seconds": BATCH_DEFAULT_WALL_SECONDS,
            "lean_timeout": float(config.lean_timeout),
        }
    else:
        # `lean_timeout` here is condition provenance only: it is what
        # `_batch_runner` hands `LeanTools` for every per-check Lean call
        # this run makes, but the batch trajectory itself records no
        # per-check timeout (item 3), so the validator has nothing to
        # cross-check it against.
        limits = {
            "max_turns": args.max_turns if args.max_turns is not None else BATCH_DEFAULT_MAX_TURNS,
            "wall_seconds": args.wall_seconds if args.wall_seconds is not None else BATCH_DEFAULT_WALL_SECONDS,
            "lean_timeout": float(config.lean_timeout),
        }
    # `args.problems` defaults to `evals/problems.json`, so its grandparent is
    # the repository root -- the directory containing `evals/`. Kept this
    # simple deliberately rather than searching upward for `.git`: a caller
    # who passes `--problems` somewhere else entirely is already outside the
    # documented "run from a source checkout's root" contract `_refuse_missing`
    # states above (item 8).
    source_root = args.problems.resolve().parent.parent
    condition = Condition(
        model=str(args.model or config.model), backend=args.backend, mode=args.mode,
        staged_prompt_set_sha256=PROMPT_SET_SHA256, batch_prompt_set_sha256=BATCH_PROMPT_SET_SHA256,
        hardy_version=__version__, source_revision=source_revision(source_root), limits=limits, repeats=args.repeats,
        selection={"only": args.only.split(",") if args.only else None, "tiers": [int(t) for t in args.tiers.split(",")] if args.tiers else None,
                   "twins": not args.no_twins},
    )
    staged = None
    if args.mode == "staged":
        from .staged import staged_runner
        staged = staged_runner(config, backend=args.backend)
    try:
        out = run_set(label=args.label, problems_path=args.problems, baseline_path=args.baseline, scoreboards_root=args.scoreboards,
                      condition=condition, environment=environment, batch_runner=_batch_runner(config, condition.model), staged_runner=staged,
                      now=lambda: datetime.now(UTC), report=lambda line: print(line, file=sys.stderr))
    except RefusedRun as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 2
    print(f"Scoreboard: {out / 'scoreboard.json'}")
    return 0
