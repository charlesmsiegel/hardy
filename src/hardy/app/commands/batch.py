"""`hardy batch`: the earlier one-shot proof experiment, and its closer ladder."""
from __future__ import annotations

import argparse
import json
import math
import threading

from hardy.app import config as configuration
from hardy.app.wiring import session_runtime
from hardy.formal.closers import CLOSERS
from hardy.formal.contracts import Request
from hardy.formal.lean import LeanTools
from hardy.workflows.batch import run


def _batch(args: argparse.Namespace, config: configuration.Config, parser: argparse.ArgumentParser) -> int:
    request = Request.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    lean = LeanTools(request, config.lean_command, timeout=config.lean_timeout, project=config.lean_project)
    # Refused here rather than at the first submission. An anonymous `example`
    # has no name for `#print axioms`, so the audit can never establish anything
    # about it and the run can never verify -- and finding that out at the end
    # costs a whole billable model run to reach a conclusion available now.
    if lean.target_name is None:
        parser.error(f"batch needs a named theorem or lemma to audit, not: {request.declaration!r}")
    # Finite, because the bound is enforced by waiting for it. `argparse`
    # accepts `inf` and `nan` as floats, and an infinite join raises
    # `OverflowError` while the daemon request carries on in the background --
    # the run written as a `runtime_error` immediately, for a request that may
    # yet finish and be billed for. A bound nothing can wait for is not a
    # bound, and the place to say so is where the flag is read.
    if not math.isfinite(args.wall_seconds):
        parser.error(f"--wall-seconds must be a finite number of seconds, not {args.wall_seconds}")
    # And small enough to wait for. `threading.Thread.join` raises
    # `OverflowError` above `threading.TIMEOUT_MAX` exactly as it does on an
    # infinity, so `--wall-seconds 1e20` walked past the finite check into the
    # same failure: the run written as a `runtime_error` at once, for a daemon
    # request that carries on and may yet be billed for. The same rule, stated
    # against the same limit, in the same place.
    if args.wall_seconds > threading.TIMEOUT_MAX:
        parser.error(
            f"--wall-seconds must be at most {threading.TIMEOUT_MAX:g} seconds, "
            f"which is the longest this platform can wait for, not {args.wall_seconds:g}"
        )
    closers = _closer_ladder(args.closers)
    result = run(request, session_runtime(config), lean, args.output, max_turns=args.max_turns, wall_seconds=args.wall_seconds, closers=closers, context_window=config.context_window)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.terminal_reason == "verified" else 1


#: What a bare `--closers` stands for. A sentinel rather than the tactic list
#: itself, so `--closers` and `--closers rfl` can be told apart by a value
#: nobody would type as a tactic.
DEFAULT_LADDER = "\x00default"


def _closer_ladder(requested: list[str | None] | None) -> tuple[str, ...] | None:
    """The tactics `--closers` asked for, in order, without splitting any of them.

    One tactic per flag. Commas used to separate them, which is wrong for the
    language: `simp [Nat.add_comm, Nat.add_left_comm]` is a single tactic, and
    splitting on the comma inside its bracket submitted two invalid ones --
    recording spurious failures and then spending the model turn the ladder was
    there to save. Nothing here parses Lean, so nothing here decides which
    commas are separators.

    A bare `--closers` expands to the standard ladder wherever it appears, so
    `--closers --closers omega` is the standard ladder followed by `omega`.
    """
    if not requested:
        return None
    tactics: list[str] = []
    for item in requested:
        if item is None or item == DEFAULT_LADDER:
            tactics.extend(CLOSERS)
            continue
        tactic = item.strip()
        if tactic:
            tactics.append(tactic)
    return tuple(tactics) or None
