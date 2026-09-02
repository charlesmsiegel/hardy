"""`hardy evals`: the baseline sweep, the set runner, and the scoreboard check."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain import EnvironmentIdentity
from ..lean import Elaboration, elaborate, environment_identity
from . import sweep
from .problems import load_problems, sha256_of

DEFAULT_PROBLEMS = Path("evals") / "problems.json"
DEFAULT_BASELINE = Path("evals") / "baseline.json"
DEFAULT_SCOREBOARDS = Path("evals") / "scoreboards"


def add_parser(subparsers: Any) -> None:
    evals = subparsers.add_parser("evals", help="the fixed problem set: baseline sweep, set runs, scoreboard checks")
    verbs = evals.add_subparsers(dest="evals_command", required=True)
    baseline = verbs.add_parser("baseline", help="sweep the tactic set over every canonical statement and write the tier file")
    baseline.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    baseline.add_argument("--out", type=Path, default=DEFAULT_BASELINE)
    run = verbs.add_parser("run", help="run every entry through batch or staged and write a scoreboard")
    run.add_argument("--label", required=True)
    run.add_argument("--mode", choices=("batch", "staged"), default="batch")
    run.add_argument("--backend", choices=("claude", "codex"), default="claude")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--only", default=None, help="comma-separated entry ids")
    run.add_argument("--tiers", default=None, help="comma-separated tiers, e.g. 2,3")
    run.add_argument("--no-twins", action="store_true")
    run.add_argument("--max-turns", type=int, default=None, help="batch mode default: 60. Refused under --mode staged.")
    run.add_argument("--wall-seconds", type=float, default=None, help="batch mode default: 1800.0. Refused under --mode staged.")
    run.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    run.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run.add_argument("--scoreboards", type=Path, default=DEFAULT_SCOREBOARDS)
    run.add_argument("--acknowledge-unsafe-execution", action="store_true")
    check = verbs.add_parser("check", help="re-derive a committed scoreboard from its run directories")
    check.add_argument("scoreboard", type=Path)
    check.add_argument("--problems", type=Path, default=DEFAULT_PROBLEMS)
    check.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)


def make_elaborate(config: Any) -> Callable[[str], Elaboration]:
    argv = (str(config.lake), "env", "lean", "--json")
    timeout = max(float(config.lean_timeout), sweep.WALL_BACKSTOP_FLOOR)
    return lambda source: elaborate(source, argv=argv, cwd=config.lean_project, timeout_seconds=timeout)


def host_info() -> dict[str, Any]:
    return {"platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count()}


def _identity(config: Any) -> EnvironmentIdentity:
    return environment_identity(config.lean_project, lean_command=(str(config.lake), "env", "lean"), timeout_seconds=config.limits.lean_process_seconds)


def run_baseline(args: argparse.Namespace, config: Any, *, elaborate: Callable[[str], Elaboration] | None = None,
                 identity: EnvironmentIdentity | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    problems = load_problems(args.problems)
    if identity is None:
        try:
            identity = _identity(config)
        except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
            print(f"Refused: the Lean toolchain could not be identified: {error}", file=sys.stderr)
            return 2
    elaborate = elaborate or make_elaborate(config)
    import_seconds = None
    if config is not None:
        probe = elaborate(sweep.header(("Mathlib",)) + "\nexample : True := trivial\n")
        import_seconds = probe.process.duration_ms / 1000.0 if probe.success else None
    baseline = sweep.sweep(
        problems, problems_sha256=sha256_of(args.problems), environment=identity, elaborate=elaborate, now=now,
        host=host_info(), import_seconds=import_seconds,
        wall_backstop_seconds=max(float(config.lean_timeout), sweep.WALL_BACKSTOP_FLOOR) if config is not None else sweep.WALL_BACKSTOP_FLOOR,
        report=lambda line: print(line, file=sys.stderr),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(baseline.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for problem in baseline.problems:
        print("PROBLEM: " + problem, file=sys.stderr)
    tiers = {t: sum(1 for e in baseline.entries.values() if e.tier == t) for t in range(4)}
    print(f"Baseline written to {args.out}: tiers " + ", ".join(f"{t}: {n}" for t, n in tiers.items()))
    return 1 if baseline.problems else 0


def main(args: argparse.Namespace, config: Any) -> int:
    if args.evals_command == "baseline":
        return run_baseline(args, config)
    if args.evals_command == "run":
        from .runner import run_set_command
        return run_set_command(args, config)
    if args.evals_command == "check":
        from .scoreboard import check_command
        return check_command(args)
    raise AssertionError(args.evals_command)
