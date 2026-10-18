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


@pytest.mark.parametrize("operation", ["append", "write_spend"])
def test_a_closed_journal_cannot_write_after_a_successor_owns_it(journal, operation) -> None:
    journal.append(_record(0, 0))
    journal.write_spend(1.5)
    journal.close()
    assert journal.closed and not journal.owned
    successor = CellJournal(journal.path)
    assert successor.own() == 1.5
    try:
        successor.append(_record(1, 0))
        before = journal.path.read_bytes()
        spend_path = journal.path.with_name(journal.spend_name)
        spend_before = spend_path.read_bytes()
        with pytest.raises(CasError, match="not owned"):
            if operation == "append":
                journal.append(_record(2, 0))
            else:
                journal.write_spend(99)
        assert journal.path.read_bytes() == before
        assert spend_path.read_bytes() == spend_before
        assert [record.seq for record in journal.records] == [0]
        assert [record.seq for record in successor.records] == [0, 1]
    finally:
        successor.close()


def test_a_closed_journal_cannot_reacquire_its_lease(journal) -> None:
    journal.close()
    with pytest.raises(CasError, match="closed"):
        journal.own()
    assert not journal.owned


def test_owning_an_already_owned_journal_keeps_its_lease(journal) -> None:
    journal.append(_record(0, 0).model_copy(update={"spent_ms": 1_000}))
    journal.write_spend(2.5)
    lease = journal._lease
    assert journal.own() == 2.5
    assert journal._lease is lease and lease.held
    journal.append(_record(1, 0))
    assert [record.seq for record in journal.records] == [0, 1]
    with pytest.raises(CasError, match="cannot be owned"):
        CellJournal(journal.path).own()


@pytest.mark.parametrize("operation", ["append", "write_spend"])
def test_a_journal_with_a_replaced_lease_cannot_write(journal, operation) -> None:
    journal.append(_record(0, 0))
    journal.write_spend(1.5)
    lease_path = journal.path.with_name(journal.path.name + ".lock")
    journal._lease.__exit__(None, None, None)
    lease_path.rename(lease_path.with_suffix(".replaced"))
    successor = CellJournal(journal.path)
    successor.own()
    try:
        before = journal.path.read_bytes()
        spend_path = journal.path.with_name(journal.spend_name)
        spend_before = spend_path.read_bytes()
        with pytest.raises(CasError, match="lease was replaced"):
            if operation == "append":
                journal.append(_record(1, 0))
            else:
                journal.write_spend(99)
        assert journal.path.read_bytes() == before
        assert spend_path.read_bytes() == spend_before
        assert [record.seq for record in journal.records] == [0]
        assert [record.seq for record in successor.records] == [0]
    finally:
        successor.close()


@pytest.mark.parametrize("operation", ["append", "write_spend"])
def test_a_journal_with_a_deleted_lease_cannot_write(journal, operation) -> None:
    journal.append(_record(0, 0))
    journal.write_spend(1.5)
    lease_path = journal.path.with_name(journal.path.name + ".lock")
    journal._lease.__exit__(None, None, None)
    lease_path.unlink()
    before = journal.path.read_bytes()
    spend_path = journal.path.with_name(journal.spend_name)
    spend_before = spend_path.read_bytes()
    with pytest.raises(FileNotFoundError):
        if operation == "append":
            journal.append(_record(1, 0))
        else:
            journal.write_spend(99)
    assert journal.path.read_bytes() == before
    assert spend_path.read_bytes() == spend_before
    assert [record.seq for record in journal.records] == [0]


def test_a_closed_scratch_journal_can_still_be_removed(journal) -> None:
    journal.append(_record(0, 0))
    journal.close()
    journal.remove()
    assert not journal.path.exists()


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
