"""The sentinel framing path: everything Singular and Macaulay2 rely on."""

from __future__ import annotations

from hardy.cas_export import export_session


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

    The fake delays writing it (`PROMPT_DELAY` in fake_sentinel_cas.py) long
    enough that the next cell has already been armed and dispatched by the
    time it lands -- a prompt that is still in flight is exactly the case a
    byte-count boundary cannot exclude (whatever offset it captures, this
    prompt arrives after it) and only the stream's write order can: the next
    cell's begin marker cannot appear before this prompt does, however late
    the prompt is, so waiting for the begin marker before reading anything
    excludes it regardless of timing.
    """
    session = sentinel_session()
    session.execute("first;")
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


def test_a_truncated_capture_is_not_accepted_as_a_success(sentinel_session) -> None:
    """A banner in the discarded tail must not be read as a clean cell.

    A sentinel backend has no status of its own: Hardy decides whether a cell
    failed by looking for an error banner in what it printed. `errorflood;`
    prints far more than `cas_output_bytes` and *then* fails, so the banner is
    in the tail that retention threw away and the retained prefix is clean.
    Hardy knows the capture was cut, so it cannot also assert the cell
    succeeded -- and above all must not admit it to the accepted state that
    recovery replays and export publishes, where the wrong verdict would be
    repeated as fact for the rest of the session's life.
    """
    session = sentinel_session(cas_output_bytes=4_096, cas_cell_seconds=10)
    record = session.execute("errorflood;")
    assert record.capture_truncated is True
    assert "? this is an error" not in record.stdout  # the evidence really is gone
    assert record.accepted is False
    assert record.restart_note  # and the reason is on the record, not silent
    assert session.accepted() == ()


def test_a_silent_cell_still_completes(sentinel_session) -> None:
    session = sentinel_session()
    assert session.execute("silent;").status == "ok"


def test_an_exported_sentinel_script_is_run_and_compared(tmp_path, sentinel_session) -> None:
    """The published script is fed back to the interpreter, as a reader would.

    A line-oriented interpreter prints a statement's value itself, so these
    cells go into the script verbatim -- but that only makes the artifact
    *plausible*, and export's claim is stronger than plausible.
    """
    session = sentinel_session()
    session.execute("hello;")
    session.execute("second;")
    report = export_session(session, tmp_path / "cas")
    assert report.script_verdict == "verified"
    assert report.reproduces
