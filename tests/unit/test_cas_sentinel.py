"""The sentinel framing path: everything Singular and Macaulay2 rely on."""

from __future__ import annotations


def test_a_cell_is_answered_and_the_marker_is_not_in_the_output(sentinel_session) -> None:
    session = sentinel_session()
    record = session.execute("hello;")
    assert record.status == "ok"
    assert "hello" in record.stdout
    assert "hardy-end" not in record.stdout


def test_an_error_banner_is_classified_as_an_error(sentinel_session) -> None:
    session = sentinel_session()
    record = session.execute("error;")
    assert record.status == "error"
    assert record.accepted is False


def test_state_is_not_polluted_by_the_previous_cells_prompt(sentinel_session) -> None:
    """A line-oriented interpreter prints a prompt after every cell.

    It arrives after the marker, so it belongs to no cell. If it leaks into the
    next cell's buffer, every recorded output is wrong by one prompt and the
    export cannot reproduce.
    """
    session = sentinel_session()
    session.execute("first;")
    # `consume()` trims exactly through the marker it just answered and no
    # further: the cell's own frame ("first;") is gone, but the trailing
    # prompt -- belonging to no cell -- is still sitting in the kernel's
    # buffer. If `consume()` were a no-op (or never called), "first;" would
    # still be there too; if it were a wholesale wipe, "fake>" would already
    # be gone. Without this assertion, the two checks below pass whether or
    # not the residue they depend on ever actually survived to be excluded.
    leftover = bytes(session._kernel.out)
    assert b"fake>" in leftover
    assert b"first;" not in leftover
    second = session.execute("second;")
    assert "fake>" not in second.stdout
    assert second.stdout.strip() == "second;"


def test_a_cell_that_swallows_the_marker_is_a_timeout_not_a_wrong_answer(
    sentinel_session,
) -> None:
    """An unterminated statement consumes the echo line as its own input."""
    session = sentinel_session(cas_cell_seconds=2)
    record = session.execute("unterminated")
    assert record.status == "timeout"
    assert record.accepted is False


def test_output_larger_than_the_cap_still_returns_an_answer(sentinel_session) -> None:
    """Scanning continues past the retention cap, or a big answer reads as death."""
    session = sentinel_session(cas_output_bytes=4_096, cas_cell_seconds=10)
    record = session.execute("flood;")
    assert record.status == "ok"
    assert record.capture_truncated is True
    assert len(record.stdout) <= 8_192


def test_a_silent_cell_still_completes(sentinel_session) -> None:
    session = sentinel_session()
    assert session.execute("silent;").status == "ok"
