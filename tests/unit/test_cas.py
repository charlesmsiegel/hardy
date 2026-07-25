"""Session semantics: state, acceptance, resets, deaths, and honest recovery."""

from __future__ import annotations

import pytest

from hardy.cas import CasError, CellOutcome, backend_for, reproduces


def test_state_carries_between_cells(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        assert session.execute("a").value_repr == "1"
        assert session.execute("b").value_repr == "2"
        assert session.execute("c").value_repr == "3"
    finally:
        session.close()


def test_a_failed_cell_is_recorded_but_not_accepted(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        failed = session.execute("boom")
        assert failed.status == "error"
        assert failed.accepted is False
        assert [record.source for record in session.accepted()] == ["a"]
        # The kernel survives a failed cell, as an interpreter would.
        assert session.execute("c").status == "ok"
    finally:
        session.close()


def test_reset_starts_a_new_segment_that_survives_a_reload(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        session.execute("b")
        assert len(session.accepted()) == 2
        session.reset()
    finally:
        session.close()

    # The reset is durable because the boundary was written as a record, not
    # inferred. A reload that replayed the pre-reset cells would be a bug.
    reloaded = cas_session()
    try:
        assert reloaded.segment == 1
        assert reloaded.accepted() == ()
    finally:
        reloaded.close()


def test_a_timeout_kills_the_kernel_and_the_next_cell_rebuilds(tmp_path, cas_session) -> None:
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("b")
        timed_out = session.execute("hang")
        assert timed_out.status == "timeout"
        assert timed_out.accepted is False
        assert session.state == "dead"

        # Only the two accepted cells are replayed, so the counter resumes at 3.
        recovered = session.execute("d")
        assert recovered.status == "ok"
        assert recovered.value_repr == "3"
        assert "replayed 2 cell(s)" in recovered.stdout
    finally:
        session.close()


def test_a_rebuild_that_reconstructs_different_values_poisons_the_session(tmp_path, cas_session) -> None:
    """Running clean is not recovering.

    `drift` answers differently in every process, so replaying it rebuilds a
    namespace that no longer matches the record. Everything executed afterwards
    would be standing on it, so the session refuses to continue.
    """
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("drift")
        session.execute("hang")
        assert session.state == "dead"

        with pytest.raises(CasError, match="different output on replay"):
            session.execute("later")
        assert session.state == "poisoned"

        with pytest.raises(CasError, match="poisoned"):
            session.execute("anything")
    finally:
        session.close()


def test_a_poisoned_session_is_usable_again_after_a_reset(tmp_path, cas_session) -> None:
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("drift")
        session.execute("hang")
        with pytest.raises(CasError):
            session.execute("later")
        assert session.state == "poisoned"

        session.reset()
        assert session.execute("fresh").value_repr == "1"
    finally:
        session.close()


def test_capture_overflow_is_flagged_rather_than_hidden(tmp_path, cas_session) -> None:
    session = cas_session(cas_output_bytes=4_096)
    try:
        record = session.execute("flood")
        assert record.capture_truncated is True
        assert len(record.stdout) < 400_000
    finally:
        session.close()


def test_the_session_budget_is_not_retryable(tmp_path, cas_session) -> None:
    session = cas_session(cas_session_seconds=0)
    try:
        with pytest.raises(CasError, match="budget exhausted"):
            session.execute("a")
    finally:
        session.close()


def test_an_oversized_cell_is_refused(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        with pytest.raises(CasError, match="64 KiB"):
            session.execute("x" * (64 * 1024 + 1))
    finally:
        session.close()


def test_a_kernel_that_exits_is_reported_as_death_not_as_an_answer(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        record = session.execute("die")
        assert record.status == "kernel_died"
        assert record.accepted is False
    finally:
        session.close()


def test_comparison_covers_stderr(tmp_path, cas_session) -> None:
    """A cell whose warnings did not reproduce has not reproduced."""
    session = cas_session()
    try:
        record = session.execute("noisy")
        assert record.stderr.strip() == "warning: noisy"
        quiet = record.model_copy(update={"stderr": ""})
        outcome = CellOutcome(status="ok", stdout="out", stderr="warning: noisy", value_repr="1")
        assert reproduces(record, outcome)
        assert not reproduces(quiet, outcome)
    finally:
        session.close()


def test_unknown_backends_are_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="unknown cas_backend"):
        backend_for("mathematica")
