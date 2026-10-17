"""Replay accepted cells in a fresh session under an explicit shared budget."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from hardy.algebra.backends import CasBackend
from hardy.algebra.contracts import CasError, CellOutcome, CellRecord
from hardy.algebra.operations import OperationControl
from hardy.algebra.session import CasSession
from hardy.workflows.contracts import RunLimits

#: The throwaway log a fresh replay's session keeps, removed when it is done.
SCRATCH_LOG = "replay-scratch.jsonl"


class _Spend:
    """The wall clock one replay spends, and the owner it is billed to."""

    def __init__(self, budget: float, charge: Callable[[float], None] | None) -> None:
        self.budget = budget
        self.spent = 0.0
        self._charge = charge

    @property
    def remaining(self) -> float:
        return self.budget - self.spent

    @contextmanager
    def timing(self) -> Iterator[None]:
        """Bill what the body takes. A body that raises bills nothing."""
        started = time.monotonic()
        yield
        elapsed = time.monotonic() - started
        self.spent += elapsed
        if self._charge is not None:
            self._charge(elapsed)


def _check(control: OperationControl | None) -> None:
    if control is not None:
        control.check()


@contextmanager
def _stoppable(session: CasSession, control: OperationControl | None) -> Iterator[None]:
    """Point `control` at this session's kernel, honouring a stop already asked for."""
    if control is None:
        yield
        return
    with control.bind(session.interrupt, session.escalate):
        control.check()
        yield


def _refuse_foreign(cells: Sequence[CellRecord], backend: CasBackend) -> None:
    foreign = next(
        (record.backend for record in cells if record.backend and record.backend != backend.name),
        None,
    )
    if foreign is not None:
        raise CasError(
            f"these cells were recorded by the {foreign} backend and cannot be "
            f"replayed under {backend.name}"
        )


def _ends_the_kernel(outcome: CellOutcome) -> bool:
    """Whether the cells behind this one have no kernel left to run in.

    `kernel_lost` as well as the statuses: whatever stopped this kernel,
    reporting the cells behind it as anything other than unreplayed would be a
    claim about a process that no longer exists.
    """
    return outcome.status in {"kernel_died", "timeout"} or outcome.kernel_lost


def replay_in_fresh_kernel(
    *,
    backend: CasBackend,
    command: Path | None,
    cells: Sequence[CellRecord],
    limits: RunLimits,
    cwd: Path,
    budget_seconds: float | None = None,
    charge: Callable[[float], None] | None = None,
    control: OperationControl | None = None,
) -> list[CellOutcome | None]:
    """Run cells in a throwaway kernel. `None` marks a cell never reached.

    `charge` reports each cell's wall clock back to whoever owns the budget
    this replay is spending. An export replays the whole accepted segment, and
    that is the session's own time however fresh the kernel running it is.
    """
    _refuse_foreign(cells, backend)
    _check(control)
    session = CasSession(
        backend=backend,
        command=command,
        log_path=cwd / SCRATCH_LOG,
        limits=limits,
        cwd=cwd,
    )
    spend = _Spend(
        budget_seconds if budget_seconds is not None else limits.cas_session_seconds, charge
    )
    outcomes: list[CellOutcome | None] = []
    try:
        # The start is spent from the same budget as the cells: a kernel the
        # export brings up is the session's time however briefly it lives.
        with spend.timing(), _stoppable(session, control):
            session.start_kernel()
        for record in cells:
            _check(control)
            if spend.remaining <= 0:
                outcomes.append(None)
                continue
            seconds = min(float(limits.cas_cell_seconds), spend.remaining)
            with spend.timing(), _stoppable(session, control):
                outcome = session.run_unrecorded(record.source, seconds)
            _check(control)
            outcomes.append(outcome)
            if _ends_the_kernel(outcome):
                break
    except CasError:
        _check(control)
    finally:
        session.close()
        session.remove_log()
    outcomes.extend([None] * (len(cells) - len(outcomes)))
    return outcomes
