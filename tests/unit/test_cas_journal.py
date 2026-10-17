"""The cell log and the kernel exchange, each on its own.

`CasSession` is the policy over these two; most of what they do is pinned
through it. What is pinned here is what each one promises by itself.
"""

from __future__ import annotations

import sys

import pytest

from hardy.algebra.backends import SympyBackend
from hardy.algebra.contracts import CasError, CellRecord
from hardy.algebra.exchange import CellExchange
from hardy.algebra.journal import CellJournal
from hardy.algebra.kernel import Kernel


def _record(seq: int, segment: int, *, source: str = "x", accepted: bool = True,
            backend: str = "sympy") -> CellRecord:
    return CellRecord(seq=seq, segment=segment, author="model", source=source,
                      status="ok", accepted=accepted, backend=backend)


@pytest.fixture
def journal(tmp_path):
    owned = CellJournal(tmp_path / "cells.jsonl")
    owned.own()
    yield owned
    owned.close()


def test_only_the_live_segments_accepted_cells_are_what_a_rebuild_replays(journal) -> None:
    journal.append(_record(0, 0))
    journal.append(_record(1, 0, accepted=False))
    journal.append(_record(2, 1, source=""))  # the reset boundary
    journal.append(_record(3, 1, accepted=False))
    journal.append(_record(4, 1))
    assert journal.segment == 1
    assert [record.seq for record in journal.cells()] == [3, 4]
    assert [record.seq for record in journal.accepted()] == [4]


def test_a_segment_another_backend_wrote_is_named(journal) -> None:
    journal.append(_record(0, 0, backend=""))  # written before the field existed
    assert journal.foreign_backend("sympy") is None
    journal.append(_record(1, 0, backend="singular"))
    assert journal.foreign_backend("sympy") == "singular"
    journal.append(_record(2, 1, source="", backend="sympy"))
    assert journal.foreign_backend("sympy") is None


def test_the_spend_sidecar_outlives_the_last_append(tmp_path) -> None:
    first = CellJournal(tmp_path / "cells.jsonl")
    assert first.own() == 0.0
    first.append(_record(0, 0).model_copy(update={"spent_ms": 1_000}))
    first.write_spend(2.5)
    first.close()
    assert CellJournal(tmp_path / "cells.jsonl").own() == 2.5


def test_a_closed_journal_cannot_be_written_through_again(journal) -> None:
    journal.close()
    assert journal.closed and not journal.owned
    with pytest.raises(CasError):
        journal.check_lease()


def test_a_second_owner_is_refused_while_the_first_holds_the_lease(journal, tmp_path) -> None:
    with pytest.raises(CasError, match="cannot be owned"):
        CellJournal(tmp_path / "cells.jsonl").own()


def test_a_stop_with_no_cell_in_flight_reaches_nothing() -> None:
    exchange = CellExchange(SympyBackend(), cell_seconds=10)
    assert exchange.interrupt() is False
    assert exchange.escalate() is False
    assert exchange.last_cell_killed() is False


def test_after_shutdown_a_new_kernel_is_refused_and_stopped(tmp_path) -> None:
    exchange = CellExchange(SympyBackend(), cell_seconds=10)
    exchange.shut_down()
    kernel = Kernel([sys.executable, "-c", "import sys; sys.stdin.read()"], tmp_path, 4096)
    try:
        assert exchange.adopt(kernel) is False
        assert exchange.kernel is None
        assert kernel.process.wait(timeout=10) is not None
    finally:
        kernel.kill(immediate=True)
