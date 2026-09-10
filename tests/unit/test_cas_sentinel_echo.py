"""Echoed marker boundaries and stderr attribution through an ordered pipe.

The real helper process echoes source and writes diagnostics on stderr like
Macaulay2. Hardy captures both descriptors in stdout with explicit provenance.
"""

from __future__ import annotations

import json
import threading

import pytest

from hardy.algebra.contracts import SENTINEL_BEGIN, SENTINEL_END, CasError
from hardy.algebra.kernel import _Kernel


def test_a_delayed_stderr_reader_cannot_accept_an_error_or_blame_the_next_cell(
    echoing_sentinel_session, monkeypatch,
):
    """The child writes the error before its marker; only parent delivery lags."""
    release = threading.Event()
    original = _Kernel._drain

    def delayed_stderr(self, pipe, destination):
        if pipe is self.process.stderr:
            release.wait(3)
        original(self, pipe, destination)

    monkeypatch.setattr(_Kernel, "_drain", delayed_stderr)
    session = echoing_sentinel_session()
    try:
        broken = session.execute("error;")
        assert broken.status == "error"
        assert not broken.accepted
        assert "division by zero" in broken.stdout + broken.stderr
        release.set()
        clean = session.execute("hello;")
        assert clean.status == "ok"
        assert "division by zero" not in clean.stdout + clean.stderr
    finally:
        release.set()


def test_sentinel_records_disclose_ordered_combined_capture(echoing_sentinel_session):
    session = echoing_sentinel_session()
    record = session.execute("hello;")
    assert record.model_dump()["capture_mode"] == "merged"


def test_a_merged_kernel_death_is_not_mistaken_for_a_timeout(echoing_sentinel_session):
    session = echoing_sentinel_session(cas_cell_seconds=2)
    record = session.execute("die;")
    assert record.status == "kernel_died"
    assert record.kernel_lost


def test_a_fatal_sentinel_error_retains_its_diagnostics(echoing_sentinel_session):
    session = echoing_sentinel_session()
    record = session.execute("error;\ndie;")
    assert record.status == "kernel_died"
    assert "division by zero" in record.stdout
    assert record.capture_mode == "merged"


def test_a_timed_out_sentinel_cell_retains_partial_diagnostics(echoing_sentinel_session):
    session = echoing_sentinel_session(cas_cell_seconds=1)
    record = session.execute("error;\nhang;")
    assert record.status == "timeout"
    assert "division by zero" in record.stdout
    assert record.capture_mode == "merged"
    assert "limit" in record.stderr


def test_a_fatal_stderr_flood_retains_bounded_diagnostics(echoing_sentinel_session):
    session = echoing_sentinel_session(cas_output_bytes=4096)
    record = session.execute("flooddie;")
    assert record.status == "kernel_died"
    assert record.capture_truncated
    assert record.capture_mode == "merged"
    assert "diagnostic-prefix" in record.stdout
    assert len(record.stdout.encode()) <= 4096


def test_reopening_a_legacy_sentinel_journal_requires_reset(echoing_sentinel_session):
    session = echoing_sentinel_session()
    session.execute("hello;")
    session.close()
    legacy = json.loads(session.log_path.read_text(encoding="utf-8"))
    del legacy["capture_mode"]
    session.log_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    reopened = echoing_sentinel_session()
    with pytest.raises(CasError, match="did not reproduce"):
        reopened.execute("second;")
    reopened.reset()
    assert reopened.execute("second;").accepted


def test_legacy_separate_capture_is_not_certified_by_merged_replay(echoing_sentinel_session):
    from hardy.algebra.contracts import CellOutcome, same_output

    session = echoing_sentinel_session()
    record = session.execute("hello;")
    legacy = type(record).model_validate({
        key: value for key, value in record.model_dump().items() if key != "capture_mode"
    })
    replay = CellOutcome.model_validate({
        "status": "ok", "stdout": record.stdout, "capture_mode": "merged",
    })
    assert not same_output(legacy, replay)


@pytest.mark.parametrize("suffix_bytes", [0, 1])
def test_a_split_echoed_end_marker_waits_for_its_suffix(
    echoing_sentinel_session, suffix_bytes,
) -> None:
    """A pipe read may end inside the echo template, before its closing quote."""
    session = echoing_sentinel_session()
    begin = SENTINEL_BEGIN.format(nonce="split")
    end = SENTINEL_END.format(nonce="split")
    extract = session._extractor("split")
    prefix = f'{begin}\nhello\ni3 : ECHO "{end}'.encode()
    partial = prefix + b'";'[:suffix_bytes]
    assert extract(partial) is None
    complete = prefix + f'";\ndeferred-output\n{end}\n'.encode()
    outcome, _ = extract(complete)
    assert "deferred-output" in outcome.stdout


def test_a_split_echoed_begin_marker_is_not_part_of_the_cell(echoing_sentinel_session):
    session = echoing_sentinel_session()
    begin = SENTINEL_BEGIN.format(nonce="split")
    end = SENTINEL_END.format(nonce="split")
    extract = session._extractor("split")
    echoed = f'i1 : ECHO "{begin}'.encode()
    assert extract(echoed) is None
    outcome, _ = extract(echoed + f'";\n{begin}\nhello\n{end}\n'.encode())
    assert outcome.stdout.strip() == "hello"


def test_content_is_extracted_despite_the_interpreters_own_echo(
    echoing_sentinel_session,
) -> None:
    """The fake echoes every line it is fed, including the begin marker
    statement's own source -- which contains the begin marker text a second
    time, ahead of the bare copy the interpreter actually answers with. A
    marker search that matched that first, embedded occurrence would set the
    body's start position too early, leaking the begin marker's own nonce
    text into `record.stdout`; `_find_marker`'s tail-aware skip must resolve
    to the bare, real occurrence instead.

    This backend has no `sanitize` override (that is exercised directly, on
    the real Macaulay2 shapes, in test_cas_sanitize.py), so the `iN :`
    prompt noise this fake also produces -- including a rendering of the
    *upcoming* end-marker statement's own source, which legitimately
    contains the substring "hardy-end" as literal echoed text, the same way
    real Macaulay2 does before `sanitize` strips it -- is expected to still
    be present here. What is not expected, and is what a regression in the
    begin-side skip would produce, is the begin marker's own nonce leaking
    in.
    """
    session = echoing_sentinel_session()
    record = session.execute("hello;")
    assert record.status == "ok"
    assert "hello;" in record.stdout
    assert "hardy-begin" not in record.stdout


def test_an_error_written_only_to_stderr_is_classified_as_an_error(
    echoing_sentinel_session,
) -> None:
    """The fake writes nothing error-shaped to stdout for a failing
    statement -- only to stderr, exactly like a real Macaulay2 error.
    Ordered capture must retain and classify it in the combined transcript.
    """
    session = echoing_sentinel_session()
    record = session.execute("error;")
    assert record.status == "error"
    assert record.accepted is False
    assert "error:" in record.stdout
    assert record.stderr == ""


def test_a_cell_is_not_cut_short_by_its_own_echoed_end_marker(
    echoing_sentinel_session,
) -> None:
    """The end marker's echo is not the end marker.

    An interpreter that echoes stdin writes the end-marker statement's text
    when it *reads* the line, which is a statement early: output the cell was
    still producing arrives after that echo and before the marker the
    interpreter actually prints. The kernel's rolling scanner cannot tell the
    two apart -- it is a substring test -- so a reader that ends the cell as
    soon as the scanner has seen "a" marker ends it at the echo and loses the
    trailing output. `defer;` in the fake produces exactly that shape.

    The scan-based fallback exists only for the case where the real marker's
    bytes were dropped at the retention cap, so it must be reached only when
    retention actually overflowed, which here it does not.
    """
    session = echoing_sentinel_session()
    record = session.execute("defer;")
    assert record.status == "ok"
    assert "deferred-output" in record.stdout
    assert record.capture_truncated is False


def test_state_still_persists_across_cells_despite_the_echo(
    echoing_sentinel_session,
) -> None:
    """The echo noise must not desynchronise the marker protocol itself --
    a second cell has to be answered as cleanly as the first, with its own
    begin marker resolved to the real occurrence, not the one embedded in
    its echoed source."""
    session = echoing_sentinel_session()
    first = session.execute("first;")
    second = session.execute("second;")
    assert first.status == "ok"
    assert second.status == "ok"
    assert "second;" in second.stdout
    assert "hardy-begin" not in second.stdout



def test_an_error_emitted_before_the_end_marker_still_classifies_the_cell(
    echoing_sentinel_session,
) -> None:
    """The interpreter finishes its stderr writes before executing the marker."""
    session = echoing_sentinel_session()
    record = session.execute("laterror;")
    assert record.status == "error"
    assert record.accepted is False
    assert "error:" in record.stdout
    assert record.stderr == ""
