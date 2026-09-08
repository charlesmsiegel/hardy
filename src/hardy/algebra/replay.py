"""Replay accepted cells in a fresh session under an explicit shared budget."""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hardy.algebra.contracts import CasError, CellOutcome, CellRecord
from hardy.algebra.session import CasSession
from hardy.workflows.contracts import RunLimits


def replay_in_fresh_kernel(
    *,
    backend: Any,
    command: Path | None,
    cells: Sequence[CellRecord],
    limits: RunLimits,
    cwd: Path,
    budget_seconds: float | None = None,
    charge: Callable[[float], None] | None = None,
) -> list[CellOutcome | None]:
    """Run cells in a throwaway kernel. `None` marks a cell never reached.

    `charge` reports each cell's wall clock back to whoever owns the budget
    this replay is spending. An export replays the whole accepted segment, and
    that is the session's own time however fresh the kernel running it is.
    """
    foreign = next(
        (record.backend for record in cells if record.backend and record.backend != backend.name),
        None,
    )
    if foreign is not None:
        raise CasError(
            f"these cells were recorded by the {foreign} backend and cannot be "
            f"replayed under {backend.name}"
        )
    session = CasSession(
        backend=backend,
        command=command,
        log_path=cwd / "replay-scratch.jsonl",
        limits=limits,
        cwd=cwd,
    )
    outcomes: list[CellOutcome | None] = []
    budget = budget_seconds if budget_seconds is not None else limits.cas_session_seconds
    try:
        session._start()
        spent = 0.0
        for record in cells:
            remaining = budget - spent
            if remaining <= 0:
                outcomes.append(None)
                continue
            started = time.monotonic()
            outcome = session._send(
                record.source, min(float(limits.cas_cell_seconds), remaining)
            )
            elapsed = time.monotonic() - started
            spent += elapsed
            if charge is not None:
                charge(elapsed)
            outcomes.append(outcome)
            # `kernel_lost` as well: whatever stopped this kernel, the cells
            # behind it have no kernel left to run in, and reporting them as
            # anything other than unreplayed would be a claim about a process
            # that no longer exists.
            if outcome.status in {"kernel_died", "timeout"} or outcome.kernel_lost:
                outcomes.extend([None] * (len(cells) - len(outcomes)))
                break
    except CasError:
        outcomes.extend([None] * (len(cells) - len(outcomes)))
    finally:
        session.close()
        # Through the session's own guard: the scratch log is removed by the
        # same proven directory it was written through, not by a path rebuilt
        # here that nothing re-checks.
        session._log.unlink(session.log_path.name, missing_ok=True)
    return outcomes
