"""The two Macaulay2-shaped behaviours the non-echoing fake cannot exercise.

`fake_sentinel_cas.py` (behind `sentinel_session`) is modelled on Singular in
`-q` mode: it never echoes stdin and never writes to stderr, so nothing in
the hermetic suite could have caught a regression in `_find_marker`'s
tail-aware skip of a marker's own echoed occurrence, or in
`classify(stdout, stderr)` reading an error off stderr alone -- both were
previously verified only by the real-backend CI job against actual
Macaulay2. `fake_sentinel_cas_echo.py` (behind `echoing_sentinel_session`)
does both, so these run hermetically.
"""

from __future__ import annotations


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
    `classify` must catch this on the stderr argument.
    """
    session = echoing_sentinel_session()
    record = session.execute("error;")
    assert record.status == "error"
    assert record.accepted is False
    assert "error:" in record.stderr


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
