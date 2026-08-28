"""The six findings PR #35 shipped with, each pinned by the case that showed it.

Every test here is a recovery, a bound, a verification, or a protocol frame
reporting something stronger than its evidence supports. They are grouped the
way the issue grouped them rather than by the module they land in, because
what they have in common is the failure mode and not the file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hardy import cas
from hardy.cas import CasError, CasSession, backend_for
from hardy.cas_driver import HEADER_BYTES, _Stream, bounded_repr, state_digest
from hardy.cas_export import TRANSCRIPT_BEGIN, export_session
from hardy.domain import RunLimits


@pytest.fixture
def sympy_session(tmp_path):
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=tmp_path / "cells.jsonl",
        limits=RunLimits(cas_cell_seconds=120),
        cwd=tmp_path,
    )
    yield session
    session.close()


# --------------------------------------------------- recovery that overclaims


def test_a_rebuild_that_reconstructs_a_different_value_is_refused(sympy_session) -> None:
    """An accepted cell that changes state without printing anything.

    `_restore` compared stdout, stderr and the value repr. A cell that binds a
    fresh random value records three empty fields, replays to three empty
    fields with a *different* value, and used to be called faithful -- and
    every cell after it was then computing from state nobody had compared.
    """
    sympy_session.execute("import random")
    # No output of any kind: this is the whole difficulty. What it changed is
    # invisible to a comparison of what it showed.
    quiet = sympy_session.execute("x = random.random()")
    assert quiet.accepted is True
    assert (quiet.stdout, quiet.stderr, quiet.value_repr) == ("", "", "")
    assert quiet.state_digest, "the driver has to describe the namespace it left"

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")
    assert sympy_session.state == "poisoned"


def test_a_rebuild_missing_a_failed_cells_mutation_is_refused(sympy_session) -> None:
    """`x = 41; 1 / 0` leaves `x` bound and the cell unaccepted.

    A rebuild replays the accepted cells only, so `x` is not in the
    reconstructed namespace at all. The accepted `pass` after it reproduces
    its own empty output either way, which is what used to make the rebuild
    look faithful while the next cell found no `x`.
    """
    broken = sympy_session.execute("x = 41; 1 / 0")
    assert broken.status == "error"
    assert broken.accepted is False
    # `x` is never read by an accepted cell, and that is the difficulty. A cell
    # that read it would fail on replay with a `NameError`, which the rebuild
    # already refused; the loss only went unnoticed while nothing looked.
    quiet = sympy_session.execute("pass")
    assert quiet.accepted is True

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


def test_an_honest_rebuild_still_succeeds(sympy_session) -> None:
    """The refusals above must not have cost every recovery.

    A session whose accepted cells really do describe its state rebuilds, and
    says so without a caveat: the digest agrees as well as the output.
    """
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("y = x**2 + 1")
    sympy_session._drop_kernel()

    rebuilt = sympy_session.execute("y")
    assert rebuilt.status == "ok"
    assert rebuilt.value_repr == "x**2 + 1"
    assert "kernel restarted" in rebuilt.restart_note
    assert "not verified" not in rebuilt.restart_note


def test_a_backend_with_no_digest_says_what_it_could_not_check(cas_session) -> None:
    """The fake kernel speaks the protocol without describing its namespace.

    Neither Singular nor Macaulay2 can be asked for one either. A replay there
    is still worth running -- it catches everything observable -- but a cell
    that printed nothing agrees with its record whatever it rebuilt, and the
    session has to say so rather than report a rebuild as if it had been
    checked.
    """
    session = cas_session()
    # `noisy` prints on both streams and reports a value. Reproducing all of
    # that says nothing about the namespace it left: output is what a cell
    # showed, and a cell is free to print a stable banner over a value that
    # differs. A missing digest is the whole answer, whatever was printed.
    loud = session.execute("noisy")
    assert loud.accepted is True
    assert loud.stdout and loud.stderr and loud.value_repr
    assert loud.state_digest == ""

    session._drop_kernel()
    rebuilt = session.execute("after")
    assert rebuilt.status == "ok"
    assert "reconstructed, not verified" in rebuilt.restart_note
    assert "no cell's replay could be fully compared" in rebuilt.restart_note
    assert "carry no state digest" in rebuilt.restart_note


# ------------------------------------------------------ bounds that do not bind


def test_a_cell_printing_far_past_the_cap_does_not_hold_it_all(tmp_path) -> None:
    """The cap has to bound what is *held*, not only what is reported.

    Capture used to accumulate in a `StringIO` and be clipped afterwards, so a
    cell printing in a loop grew a buffer with no limit at all: an advertised
    256 KiB cap could still cost gigabytes of resident memory and take the
    kernel -- and the session's whole state -- with it.

    The kernel is given an address space small enough that holding the output
    would fail, and asked to print far more than that. Surviving is the
    assertion; the reply's contents show the cap is still honoured.
    """
    limiter = tmp_path / "limited_driver.py"
    limiter.write_text(
        "import resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)\n"
        "sys.argv = [sys.argv[0], sys.argv[1]]\n"
        "import hardy.cas_driver as driver\n"
        "driver.main()\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, "-u", str(limiter), "4096"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
    )
    try:
        # Two thousand writes of a megabyte each: nothing near this fits in the
        # address space above, and every byte of it goes through the capture.
        source = "for _ in range(2_000):\n    print('x' * 1_000_000)\n"
        payload = json.dumps({"source": source, "stopping": False}).encode("utf-8")
        child.stdin.write(f"{len(payload):0{HEADER_BYTES}d}".encode("ascii") + payload)
        child.stdin.flush()
        header = child.stdout.read(HEADER_BYTES)
        assert header, "the kernel died rather than bounding its capture"
        reply = json.loads(child.stdout.read(int(header)).decode("utf-8"))
    finally:
        child.stdin.close()
        child.kill()
        child.wait(timeout=30)

    assert reply["status"] == "ok", reply
    assert reply["capture_truncated"] is True
    assert len(reply["stdout"].encode("utf-8")) <= 4096


@pytest.mark.parametrize(
    ("value", "limit"),
    [("'x' * 10**7", 4096), ("list(range(10**6))", 4096), ("{i: i for i in range(10**6)}", 4096)],
)
def test_an_over_large_value_is_not_built_in_full_to_be_thrown_away(
    value, limit, sympy_session
) -> None:
    """`repr(value)` was materialised whole and then clipped.

    A value whose own length already exceeds the byte budget cannot have a
    repr that would have survived the cap, so rendering only its head changes
    no answer that was ever going to be reported -- and it says in the text
    that it is a head rather than the value.
    """
    rendered, truncated = bounded_repr(eval(value), limit)  # noqa: S307 -- test input
    assert truncated is True
    assert len(rendered) < limit * 2
    assert "…" in rendered


def test_a_value_that_fits_is_reported_exactly(sympy_session) -> None:
    """The bound must not change what an ordinary cell answers."""
    assert bounded_repr([1, 2, 3], 4096) == ("[1, 2, 3]", False)
    assert bounded_repr("hello", 4096) == ("'hello'", False)
    assert sympy_session.execute("factor(symbols('x')**2 - 1)").value_repr == "(x - 1)*(x + 1)"


def test_a_broken_repr_is_reported_rather_than_fatal() -> None:
    """A cell writes its own `__repr__`, and it is allowed to be broken."""

    class Hostile:
        def __repr__(self):
            raise RuntimeError("no")

    rendered, truncated = bounded_repr(Hostile(), 4096)
    assert truncated is True
    assert "RuntimeError" in rendered


def test_a_late_stderr_overflow_is_not_lost_before_it_is_read(sentinel_session) -> None:
    """The flag was snapshotted before stderr had settled, then cleared.

    A sentinel backend has no status of its own: Hardy classifies the cell by
    looking for an error banner in what it captured. When the capture was cut
    it must not then assert success -- and here the cut lands on stderr, after
    the stdout end marker has already been found, so `consume()` reset the flag
    between the snapshot and the wait that finally read the bytes. An error
    banner in the discarded tail was then classified from a clean prefix and
    accepted into the state recovery replays and export publishes.
    """
    session = sentinel_session(cas_output_bytes=4_096)
    record = session.execute("latestderr;")
    assert record.capture_truncated is True, record.model_dump_json(indent=2)
    assert record.accepted is False, record.model_dump_json(indent=2)
    assert "cas_output_bytes" in record.restart_note


# ------------------------------------------- verification that accepts too much


def test_output_the_session_never_saw_is_not_reproduction(sympy_session, tmp_path) -> None:
    """The transcript check was a subsequence match with chrome tolerated.

    `__name__` is `__hardy_cas__` in the driver and `__main__` in the
    published file, so this cell is silent in the session and prints from the
    script. The extra line landed after the recorded transcript, where the
    old comparison allowed an interpreter's trailing prompt -- and the export
    called the pair verified.
    """
    silent = sympy_session.execute('if __name__ == "__main__":\n    print("only in the script")')
    assert silent.status == "ok"
    assert silent.stdout == ""

    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "diverged", report.model_dump_json(indent=2)
    assert report.reproduces is False
    assert "only in the script" in report.script_detail


def test_an_empty_recorded_transcript_does_not_accept_any_output(
    sympy_session, tmp_path
) -> None:
    """A session that printed nothing used to accept a script that printed anything."""
    sympy_session.execute('if __name__ == "__main__":\n    print("surprise")')
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "diverged", report.model_dump_json(indent=2)


def test_the_published_script_says_where_its_own_transcript_starts(
    sympy_session, tmp_path
) -> None:
    """What makes the comparison exact rather than a guess about chrome.

    Hardy no longer has to decide which lines an interpreter added around the
    file's output: the file brackets its own.
    """
    sympy_session.execute("2 + 2")
    export_session(sympy_session, tmp_path / "cas")
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    assert TRANSCRIPT_BEGIN in script


# ------------------------------------------------------------------- protocol


@pytest.mark.parametrize(
    "source",
    [
        "import os; os.write(1, b'straight to the descriptor\\n')",
        "import subprocess, sys; subprocess.run([sys.executable, '-c', \"print('helper')\"])",
    ],
)
def test_helper_output_on_the_raw_descriptor_does_not_break_the_frame(
    source, sympy_session
) -> None:
    """`redirect_stdout` rebinds `sys.stdout` and nothing else.

    `os.system`, a subprocess, or a native library writes to file descriptor
    1, which is the descriptor the length-prefixed reply travels on. Those
    bytes landed in front of the header, Hardy read a non-numeric length,
    declared the kernel desynchronised, and discarded a session's whole state
    over a helper's chatter. Capture is taken at the descriptor now, so the
    bytes are the cell's output instead.
    """
    record = sympy_session.execute(source)
    assert record.status == "ok", record.model_dump_json(indent=2)
    assert record.stdout.strip().endswith(("descriptor", "helper"))
    # And the session is still there to answer for it.
    assert sympy_session.execute("2 + 2").value_repr == "4"


def test_a_helpers_output_is_captured_in_the_order_it_was_written(sympy_session) -> None:
    """One descriptor, one pipe, one order. Two captures would interleave by luck."""
    record = sympy_session.execute(
        "import os, sys\n"
        "print('before')\n"
        "sys.stdout.flush()\n"
        "os.write(1, b'during\\n')\n"
        "print('after')\n"
    )
    assert record.stdout.splitlines() == ["before", "during", "after"]


def test_the_digest_ignores_an_address_but_not_a_value() -> None:
    """A rebuilt namespace holds equal objects at different addresses.

    Calling that a divergence would poison every session that ever bound a
    lambda or a plain instance, so an address is normalised out. What the
    object *is* still counts.
    """

    class Thing:
        pass

    first, second = {"a": Thing()}, {"a": Thing()}
    assert state_digest(first, {}, 4096) == state_digest(second, {}, 4096)
    assert state_digest({"a": 1}, {}, 4096) != state_digest({"a": 2}, {}, 4096)
    # A name still bound to what the preamble bound is not the session's state.
    shared = object()
    assert state_digest({"a": shared}, {"a": shared}, 4096) == state_digest({}, {}, 4096)


# ---------------------------------- the review of the fixes above, in its turn


def test_a_digest_normalises_an_address_without_normalising_a_value() -> None:
    """The first pass rewrote every `0x...` it found, content included.

    `"0x1234"` and `"0xabcd"` are different values that fingerprinted alike, so
    a rebuild holding the wrong one was called faithful — the exact failure the
    digest exists to prevent, reintroduced by the rule that makes it tolerant
    of addresses. The pattern is anchored to the shape a default repr uses.
    """
    assert state_digest({"a": "0x1234"}, {}, 4096) != state_digest({"a": "0xabcd"}, {}, 4096)
    assert state_digest({"a": ["0xfeed"]}, {}, 4096) != state_digest({"a": ["0xbeef"]}, {}, 4096)


def test_a_namespace_seen_only_in_prefix_is_not_fingerprinted() -> None:
    """A prefix is not a fingerprint.

    `bounded_repr` truncates, so two values agreeing for the first `limit`
    bytes would hash alike. No digest at all is the honest answer; the parent
    reads an empty one as "not compared" and says so.
    """
    first = {"a": "z" * 5_000 + "end"}
    second = {"a": "z" * 5_000 + "different"}
    assert state_digest(first, {}, 4096) == ""
    assert state_digest(second, {}, 4096) == ""

    # A *container* that long is a different case, and the refusal is not
    # wanted there: the walk describes it member by member, so something whose
    # repr would not have fitted is still fingerprinted exactly. The refusal
    # is for a leaf — something Hardy can only look at through `repr`.
    held = {"a": ["z"] * 5_000 + ["end"]}
    apart = {"a": ["z"] * 5_000 + ["different"]}
    assert state_digest(held, {}, 4096) != ""
    assert state_digest(held, {}, 4096) != state_digest(apart, {}, 4096)


def test_a_namespace_that_cannot_be_sorted_still_describes_itself() -> None:
    """`globals()` accepts a non-string key, and `sorted` over mixed types raises.

    That used to lose the whole digest — and a lost digest was reported as an
    ordinary successful rebuild.
    """
    namespace = {"a": 1}
    namespace[7] = "seven"
    assert state_digest(namespace, {}, 4096) != ""
    assert state_digest(namespace, {}, 4096) != state_digest({"a": 1}, {}, 4096)


def test_a_container_of_few_but_enormous_members_is_not_built_in_full() -> None:
    """An item count is not a size.

    A one-element list holding a multi-gigabyte string has one item, so
    deciding by `len(value)` sent it down the plain `repr` path and built the
    whole thing — with `MemoryError` caught only after the allocation had been
    attempted, by which point the OS may have taken the kernel instead.
    """
    for value in (["x" * 10**7], {"k": "y" * 10**7}, ("z" * 10**7,)):
        rendered, truncated = bounded_repr(value, 4096)
        assert truncated is True, value[:1]
        assert len(rendered) < 4096 * 2


def test_a_rebuild_is_unverified_whenever_a_cell_printed_but_carries_no_digest(
    cas_session,
) -> None:
    """Reproduced output is not a second opinion on a missing digest.

    The first rule also required the cell to have printed nothing, so a cell
    printing a stable banner over an unobservably different value was left off
    the list and the rebuild was reported as an ordinary success.
    """
    session = cas_session()
    loud = session.execute("noisy")
    assert (loud.stdout, loud.state_digest) == ("out", "")

    session._drop_kernel()
    assert "not verified" in session.execute("after").restart_note


def test_a_cell_that_rebinds_print_does_not_break_the_transcript_markers(
    sympy_session, tmp_path
) -> None:
    """The closing marker runs after every cell, so it sees their globals.

    `print = lambda *_: None` is a real thing for a cell to do, and it used to
    swallow the marker — reporting `diverged` for a session whose cells
    reproduced exactly. The builtin is reached through its module, which a
    cell's own global cannot shadow.
    """
    sympy_session.execute("print('before')")
    sympy_session.execute("print = lambda *_: None")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)


def test_the_script_records_the_environment_it_was_checked_under(
    sympy_session, tmp_path
) -> None:
    """A verdict describes running these bytes *this way*.

    Hardy pins `PYTHONHASHSEED` for the kernel and for the check, so a printed
    set does not reorder itself between them. A reader who runs the file
    without it can see a different order from the one Hardy compared, so the
    file and the manifest both say what it was checked under.
    """
    sympy_session.execute("2 + 2")
    export_session(sympy_session, tmp_path / "cas")
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))
    assert "PYTHONHASHSEED=0" in script
    assert manifest["environment"] == {"PYTHONHASHSEED": "0"}


def test_output_a_helper_writes_after_its_cell_is_not_silently_dropped(
    sympy_session,
) -> None:
    """A pipe orders writes already made, not writes still to come.

    A subprocess that prints after its cell returned writes behind the marker
    that closed the cell. Those bytes belong to a record already written, so
    they are discarded rather than pinned on whoever runs next — and a discard
    is what `capture_truncated` exists to admit to, so the next cell says so.
    """
    spawned = sympy_session.execute(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        "\"import time; time.sleep(0.2); print('late')\"])\n"
    )
    assert spawned.status == "ok"
    # Outside any cell, which is the window this is about: the helper writes
    # while nothing is listening, so its bytes belong to a record already on
    # disk and there is nowhere honest to put them.
    time.sleep(1.0)

    noticed = sympy_session.execute("2 + 2")
    assert noticed.value_repr == "4"
    assert noticed.stdout == "", "the helper's output must not become another cell's"
    assert noticed.capture_truncated is True, (
        "the helper's late output was discarded and nothing admitted it"
    )


# ------------------------------------------ the second review, in its turn too


def test_a_default_repr_is_refused_rather_than_normalised(sympy_session) -> None:
    """`<Box object at 0x…>` says the type and the address and nothing else.

    Normalising the address away made two instances holding different values
    agree, so a rebuild that reconstructed the wrong one was reported faithful
    — the failure the digest exists to catch, reintroduced by the tolerance
    that keeps it from firing on every lambda.
    """

    class Box:
        pass

    first, second = Box(), Box()
    first.payload, second.payload = 1, 2
    assert state_digest({"box": first}, {}, 4096) == ""
    assert state_digest({"box": second}, {}, 4096) == ""

    # And end to end: the cell is silent, so nothing but the digest could have
    # caught it, and the session must not claim it did.
    sympy_session.execute("import random")
    sympy_session.execute("class Box: pass")
    quiet = sympy_session.execute("box = Box(); box.payload = random.random()")
    assert (quiet.stdout, quiet.value_repr, quiet.state_digest) == ("", "", "")
    sympy_session._drop_kernel()
    assert "not verified" in sympy_session.execute("1 + 1").restart_note


def test_a_user_bound_dunder_is_state_like_any_other(sympy_session) -> None:
    """Skipping every `__`-prefixed name skipped the user's as well as the
    interpreter's, so a silent `__cache = random.random()` was invisible to
    every digest and its rebuild reported an ordinary success."""
    sympy_session.execute("import random")
    quiet = sympy_session.execute("__cache = random.random()")
    assert (quiet.stdout, quiet.value_repr) == ("", "")
    assert quiet.state_digest, "a user's dunder is state"

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


def test_a_deeply_nested_large_member_is_still_bounded() -> None:
    """A depth cutoff that returned zero read "too deep to measure" as "small",
    so seven nested singleton lists around a huge string took the plain `repr`
    path and allocated the whole thing. Past the cutoff the answer is now "too
    large", which is the only safe direction for a bound."""
    value: object = "x" * 10**7
    for _ in range(30):
        value = [value]
    rendered, truncated = bounded_repr(value, 4096)
    assert truncated is True
    assert len(rendered) < 4096 * 2


def test_undecodable_bytes_stay_distinct(sympy_session) -> None:
    """`errors="replace"` collapses every invalid byte to the same U+FFFD, so
    a cell writing `b"\\xff"` and a replay writing `b"\\xfe"` compared equal and
    the export reported verification of output that had changed."""
    first = sympy_session.execute(r"import os; os.write(1, b'\xff')")
    second = sympy_session.execute(r"import os; os.write(1, b'\xfe')")
    assert first.stdout != second.stdout
    assert first.stdout and second.stdout


def test_a_cell_that_breaks_import_does_not_break_the_closing_marker(
    sympy_session, tmp_path
) -> None:
    """Every marker that resolves a global *after* the cells is one more name
    to shadow: `print`, then `__import__`, then whatever came next. The closing
    marker resolves nothing at the end — `atexit` captures the bound `print`
    before cell one and the interpreter emits it at shutdown."""
    sympy_session.execute("2 + 2")
    sympy_session.execute("__import__ = None")
    sympy_session.execute("print = lambda *_: None")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)


def test_a_script_that_prints_after_its_marker_is_not_verified(
    sympy_session, tmp_path
) -> None:
    """A child the last cell spawned keeps the inherited descriptor open, so it
    can write after the file has finished. Slicing to the markers put those
    bytes outside the comparison, where the export still called itself
    verified — and with no later live cell, nothing else would have noticed."""
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import subprocess, sys\n"
        "    subprocess.Popen([sys.executable, '-c', "
        "\"import time; time.sleep(0.5); print('after the marker')\"])\n"
    )
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "diverged", report.model_dump_json(indent=2)
    assert report.reproduces is False


# ----------------------------------------------------- and the third review's


def test_bytes_behind_the_marker_in_one_read_are_admitted_to(sympy_session) -> None:
    """One `os.read` can carry the marker and a helper's next write together.

    Clearing the buffer dropped that tail with nothing recorded, so whether
    anything admitted to the discard came down to how the pipe happened to
    chunk. It is the same discard as the between-cells one.
    """
    sympy_session.execute(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        "\"import time; time.sleep(0.2); print('late')\"])\n"
    )
    time.sleep(1.0)
    noticed = sympy_session.execute("2 + 2")
    assert noticed.value_repr == "4"
    assert noticed.stdout == ""
    assert noticed.capture_truncated is True


def test_a_capture_that_cannot_be_written_down_exactly_says_so(sympy_session) -> None:
    """`backslashreplace` keeps `b"\\xff"` and `b"\\xfe"` apart, and still is not
    a faithful encoding: `b"\\xff"` and the ASCII bytes `b"\\\\xff"` render the
    same. A record is a JSON string and cannot hold arbitrary bytes at all, so
    the honest report is the admission rather than a cleverer escape."""
    raw = sympy_session.execute(r"import os; os.write(1, b'\xff')")
    assert raw.capture_truncated is True, raw.model_dump_json(indent=2)

    # And an ordinary capture is still reported as exact.
    clean = sympy_session.execute("print('plain')")
    assert clean.stdout == "plain\n"
    assert clean.capture_truncated is False


def test_a_cell_that_replaces_stdout_does_not_redirect_the_closing_marker(
    sympy_session, tmp_path
) -> None:
    """`print` with no `file` looks `sys.stdout` up when it runs, so capturing
    the function was not enough — a cell reassigning the stream still carried
    the closing marker off with it. The destination is bound at registration
    too, before any cell."""
    sympy_session.execute("2 + 2")
    sympy_session.execute("import io, sys; sys.stdout = io.StringIO()")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)


def test_a_script_whose_output_is_still_arriving_is_not_verified(
    sympy_session, tmp_path
) -> None:
    """A descendant holding the pipe past the drain join leaves the reader
    alive. Snapshotting the buffer there and calling the capture complete let
    an export compare against a transcript that had not finished arriving."""
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import subprocess, sys\n"
        "    subprocess.Popen([sys.executable, '-c', "
        "\"import time; time.sleep(30); print('much later')\"])\n"
    )
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict in {"unverified", "diverged"}, report.model_dump_json(indent=2)
    assert report.reproduces is False


# ---------------------------------------------------- and the fourth review's


def test_a_cell_that_assigns_underscore_itself_is_state(sympy_session) -> None:
    """`_` is normally the driver's last-value binding, already compared as
    `value_repr`. A cell is free to assign it directly, and then it is state
    like any other: `_ = random.random()` has no `value_repr` at all, so
    skipping the name unconditionally let two namespaces holding different
    `_` fingerprint alike."""
    assert state_digest({"_": 1}, {}, 4096) != state_digest({"_": 2}, {}, 4096)

    sympy_session.execute("import random")
    quiet = sympy_session.execute("_ = random.random()")
    assert quiet.value_repr == ""
    assert quiet.state_digest, "an assigned `_` has to reach the digest"

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


def test_the_drivers_own_last_value_is_not_counted_twice(sympy_session) -> None:
    """The skip still has to work for the binding it was written for, or every
    session with a trailing expression would be reported unverified."""
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("x**2 + 1")
    sympy_session._drop_kernel()

    rebuilt = sympy_session.execute("x")
    assert rebuilt.status == "ok"
    assert "not verified" not in rebuilt.restart_note


def test_a_capture_keeps_nothing_written_before_its_fence() -> None:
    """Arming a capture says "keep things from now"; it says nothing about what
    is already sitting unread in the pipe. A helper that wrote a moment before
    the next cell began had its bytes kept as that cell's output, with nothing
    marked incomplete. A begin marker written into the pipe is ordered behind
    exactly those bytes, so finding it proves what is in front is somebody
    else's.

    Driven at the stream rather than through a session, because the window is
    a race by construction: whether unread bytes are still in the pipe when a
    cell arms depends on the drain thread's timing, and a test that had to win
    that race to mean anything would pass for the wrong reason when it lost.
    """
    stream = _Stream(read_fd=-1, limit=4096)
    stream.arm(b"<begin>", b"<end>")
    stream.feed(b"from the last cell\n<begin>mine\n<end>")

    text, exact = stream.text()
    assert text == "mine\n"
    assert exact is True
    assert stream.done is True
    assert stream.stray is True, "what was in front of the fence has to be admitted to"


def test_a_capture_whose_fence_never_arrived_does_not_wait_it_out() -> None:
    """A begin marker whose write failed must not cost the cell its whole
    settle grace and then report nothing. The cell is taken unfenced and said
    to be incomplete."""
    stream = _Stream(read_fd=-1, limit=4096)
    stream.arm(b"<begin>", b"<end>")
    stream.feed(b"unfenced\n<end>")

    assert stream.done is True
    assert stream.text()[0] == "unfenced\n"
    assert stream.stray is True


def test_the_fence_does_not_cost_an_ordinary_cell_its_output(sympy_session) -> None:
    """Both markers are Hardy's own and neither belongs in the record."""
    record = sympy_session.execute("print('kept'); 1 + 1")
    assert record.stdout == "kept\n"
    assert record.value_repr == "2"
    assert record.capture_truncated is False
    assert "hardy-cell-begin" not in record.stdout


# ----------------------------------------------------- and the fifth review's


def test_a_mutation_of_the_displayed_value_is_state(sympy_session) -> None:
    """Remembering the displayed object across cells hid mutations to it.

    `[]` as a trailing expression binds `_`; a later `_.append(...)` leaves the
    same object at the same identity holding something new, so an
    identity-keyed skip kept excluding it. The skip now lasts exactly as long
    as the claim that justifies it — the cell whose `value_repr` carries the
    value.
    """
    sympy_session.execute("import random")
    sympy_session.execute("[]")
    mutated = sympy_session.execute("_.append(random.random())")
    assert mutated.value_repr == ""
    assert mutated.state_digest, "a mutated `_` has to reach the digest"

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


def test_a_cell_that_closes_stderr_does_not_take_the_kernel_with_it(
    sympy_session,
) -> None:
    """`traceback.print_exc()` writes to `sys.stderr`, which is backed by
    descriptor 2 — and a cell is free to close it. The print then raised a
    second exception from inside the handler, which escaped `run_cell` and
    killed the driver with no reply frame: an ordinary failing cell became a
    lost kernel and a forced rebuild."""
    record = sympy_session.execute("import os; os.close(2); 1 / 0")
    assert record.status == "error", record.model_dump_json(indent=2)
    assert "ZeroDivisionError" in record.stderr
    # And the kernel is still there to answer for it.
    assert sympy_session.execute("2 + 2").value_repr == "4"


def test_an_ordinary_traceback_still_follows_what_the_cell_printed(
    sympy_session,
) -> None:
    """Formatting the traceback rather than printing it must not reorder it."""
    record = sympy_session.execute(
        "import sys\nprint('first', file=sys.stderr)\nraise ValueError('second')\n"
    )
    assert record.status == "error"
    assert record.stderr.index("first") < record.stderr.index("ValueError")


# ----------------------------------------------------- and the sixth review's


def test_an_export_does_not_call_an_uncompared_namespace_verified(
    sympy_session, tmp_path
) -> None:
    """`_restore` refuses to call a digestless replay a checked rebuild. An
    export publishing the same replay as `verified` is the same overclaim on
    the other path — and the export is the artifact a reader keeps."""
    sympy_session.execute("import random")
    sympy_session.execute("class Box: pass")
    quiet = sympy_session.execute("box = Box(); box.payload = random.random()")
    assert quiet.state_digest == "", "a default repr is not a fingerprint"

    report = export_session(sympy_session, tmp_path / "cas")
    assert report.unverified >= 1, report.model_dump_json(indent=2)
    assert report.reproduces is False
    assert any("state not compared" in v.detail for v in report.verdicts)


def test_an_export_of_a_fully_fingerprinted_session_still_verifies(
    sympy_session, tmp_path
) -> None:
    """The refusal above must not have cost every export."""
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("factor(x**2 - 1)")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.reproduces is True, report.model_dump_json(indent=2)


def test_a_deleted_preamble_name_is_a_change_to_the_namespace(sympy_session) -> None:
    """A name the preamble bound and a cell removed contributes nothing to a
    walk of what is left, so a namespace missing it fingerprinted exactly like
    one still holding it. `del symbols; 1 / 0` followed by an accepted `pass`
    rebuilt with `symbols` quietly back, and was called faithful."""
    broken = sympy_session.execute("del symbols; 1 / 0")
    assert broken.status == "error"
    assert broken.accepted is False

    quiet = sympy_session.execute("pass")
    assert quiet.accepted is True

    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


def test_a_displayed_value_seen_only_in_prefix_is_not_skipped(sympy_session) -> None:
    """The skip is justified by `value_repr` carrying the value, so it lasts
    only while `value_repr` is the whole of it. A trailing value reported from
    its prefix, skipped from the digest as well, would leave the differing
    tail in neither — and a replay could rebuild a different one and match
    both."""
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=sympy_session.log_path.parent / "clipped.jsonl",
        limits=RunLimits(cas_cell_seconds=120, cas_output_bytes=4_096),
        cwd=sympy_session.cwd,
    )
    try:
        session.execute("import random")
        clipped = session.execute("[0] * 5000 + [random.random()]")
        assert clipped.capture_truncated is True
        assert clipped.accepted is True
        # `_` goes through the digest, so the tail that `value_repr` lost is
        # fingerprinted after all — walked member by member rather than
        # rendered, so being too long to print is no longer too long to
        # check. A replay that rebuilds a different last element is refused.
        assert clipped.state_digest != ""
        session._drop_kernel()
        with pytest.raises(CasError, match="did not reproduce"):
            session.execute("1 + 1")
    finally:
        session.close()


# --------------------------------------------------- and the seventh review's


def test_the_digest_tells_a_shared_object_from_an_equal_one() -> None:
    """A repr describes a value and says nothing about the object graph.

    `a = []; b = a` and `a = []; b = []` render identically, so a replay that
    rebuilt the wrong one fingerprinted the same — and a later `a.append(1); b`
    then sees different state. Sharing is part of the fingerprint now.
    """
    shared: list = []
    aliased = {"a": shared, "b": shared}
    apart = {"a": [], "b": []}
    assert state_digest(aliased, {}, 4096) != state_digest(apart, {}, 4096)

    # Two namespaces with the same shape still agree, or every rebuild would
    # diverge on its own aliasing.
    other: list = []
    assert state_digest({"a": other, "b": other}, {}, 4096) == state_digest(aliased, {}, 4096)
    # And values are still compared, not replaced by their identity.
    assert state_digest({"a": [1]}, {}, 4096) != state_digest({"a": [2]}, {}, 4096)


def test_a_rebuild_that_loses_the_sharing_is_refused(sympy_session, tmp_path) -> None:
    """End to end, with the aliasing decided by something outside the cell.

    The flag exists on the first run and not on the replay, so the accepted
    cell binds `b` to `a` live and to a fresh list on rebuild. Nothing it
    prints differs.
    """
    flag = tmp_path / "aliased"
    flag.write_text("yes", encoding="utf-8")
    sympy_session.execute(f"import os\npath = {str(flag)!r}")
    quiet = sympy_session.execute("a = []\nb = a if os.path.exists(path) else []")
    assert (quiet.stdout, quiet.value_repr) == ("", "")
    assert quiet.state_digest

    flag.unlink()
    sympy_session._drop_kernel()
    with pytest.raises(CasError, match="did not reproduce"):
        sympy_session.execute("1 + 1")


# --------------------------------------------------- and the eighth review's


def test_a_state_only_divergence_says_so(sympy_session, tmp_path) -> None:
    """A silent `x = random.random()` reproduces every printed field and fails
    only on the digest. Reporting that as "different output" told `export.json`
    and the notebook the opposite of what happened, hiding exactly the state
    discrepancy the digest was added to expose."""
    sympy_session.execute("import random")
    sympy_session.execute("x = random.random()")
    report = export_session(sympy_session, tmp_path / "cas")

    diverged = [v for v in report.verdicts if v.verdict == "diverged"]
    assert diverged, report.model_dump_json(indent=2)
    assert any("rebuilt a different namespace" in v.detail for v in diverged)
    assert not any("different output" in v.detail for v in diverged)


def test_a_name_the_digest_skips_is_still_a_position_in_the_graph() -> None:
    """A name whose value is not hashed is still a position in the object graph.

    A name still bound to the very object the preamble bound is skipped, to
    keep the digest to what the session did. Leaving it out of the *numbering*
    as well let the graph be rebuilt differently for free: two equal preamble
    objects took the same ordinals either way, so a session name aliasing one
    or the other fingerprinted alike — while a later `z.append(1)` is visible
    through one of them and not the other.
    """
    first: list = []
    second: list = []
    baseline = {"b1": first, "b2": second}
    live = {"b1": first, "b2": second}
    aliasing_first = state_digest({**live, "z": first}, baseline, 4096)
    aliasing_second = state_digest({**live, "z": second}, baseline, 4096)
    assert aliasing_first != aliasing_second


def test_a_rebuild_over_a_truncated_capture_is_unverified(sympy_session) -> None:
    """The retained prefixes matched and the discarded tails were never
    compared, so a cell printing a deterministic prefix over a random tail
    replays cleanly with nothing having checked the part that differs."""
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=sympy_session.log_path.parent / "clipped-rebuild.jsonl",
        limits=RunLimits(cas_cell_seconds=120, cas_output_bytes=4_096),
        cwd=sympy_session.cwd,
    )
    try:
        session.execute("import random")
        clipped = session.execute("print('x' * 5000 + str(random.random()))")
        assert clipped.capture_truncated is True
        assert clipped.accepted is True, "the driver protocol still accepts it"

        session._drop_kernel()
        rebuilt = session.execute("1 + 1")
        assert rebuilt.status == "ok"
        assert "not verified" in rebuilt.restart_note, rebuilt.restart_note
    finally:
        session.close()


# ---------------------------------------------------- and the ninth review's


def test_the_digest_sees_sharing_below_the_top_level() -> None:
    """Registering only the names saw sharing at the top level and nowhere else.

    `a = [[]]` with `b` bound either to `a[0]` or to a fresh `[]` renders
    `[[]]` and `[]` both ways — and after a rebuild `b.append(1)` either does
    or does not change `a`.
    """
    nested: list = []
    assert state_digest({"a": [nested], "b": nested}, {}, 4096) != state_digest(
        {"a": [[]], "b": []}, {}, 4096
    )


def test_a_deletion_cannot_be_forged_by_a_value(sympy_session) -> None:
    """The marker shared its encoding with an ordinary rendered value.

    `Symbol("deleted")` renders as `deleted`, so a namespace missing a name
    hashed exactly like one holding that symbol under it. Every entry carries
    its kind now, and no kind's bytes can be produced by another.
    """
    absent = state_digest({}, {"symbols": 1}, 4096)
    forged = state_digest({"symbols": __import__("sympy").Symbol("deleted")}, {"symbols": 1}, 4096)
    assert absent != forged


def test_output_written_before_a_cell_closes_its_descriptor_is_not_lost(
    sympy_session,
) -> None:
    """A closed descriptor ends the wait without the end marker arriving, and
    whatever was already in the scan window went unretained and unreported:
    the cell recorded empty stdout and called the capture exact."""
    record = sympy_session.execute("import os; os.write(1, b'VISIBLE'); os.close(1)")
    assert "VISIBLE" in record.stdout, record.model_dump_json(indent=2)
    assert record.capture_truncated is True, record.model_dump_json(indent=2)


def test_the_script_check_cannot_change_the_file_it_publishes(
    sympy_session, tmp_path
) -> None:
    """The check executes the published bytes, and a cell is free to rewrite
    the path it was run from — Python has already loaded the module, so the
    run finishes and matches the transcript. The verdict would then be
    `verified` for an artifact that no longer existed.

    The file runs where it was published, so the write lands on it; what must
    not survive is the *artifact* being left as whatever the run made of it.
    """
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import sys, pathlib\n"
        "    pathlib.Path(sys.argv[0]).write_text('# gone\\n', encoding='utf-8')\n"
    )
    directory = tmp_path / "cas"
    report = export_session(sympy_session, directory)

    published = (directory / "session.py").read_text(encoding="utf-8")
    # `# gone` is in the file legitimately -- it is the cell's own source. What
    # must not have happened is the file being *replaced* by it.
    assert published.strip() != "# gone"
    assert "from sympy import *" in published
    assert TRANSCRIPT_BEGIN in published
    # The bytes on disk are the ones the manifest describes, and the verdict
    # says what happened rather than passing over it.
    assert report.script_verdict == "failed", report.model_dump_json(indent=2)
    assert "rewrote itself" in report.script_detail
    assert "put back" in report.script_detail


def test_a_restore_the_guard_refuses_is_reported_rather_than_swallowed(
    sympy_session, tmp_path
) -> None:
    """Putting the bytes back goes through the guard, and the guard refuses a
    symlink — which is one of the things a run is free to leave behind. A
    restore that could not happen leaves an artifact the manifest's hash does
    not describe, and saying so is the whole of what is left to do about it."""
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import os, pathlib, sys\n"
        "    here = pathlib.Path(sys.argv[0])\n"
        "    elsewhere = here.parent / 'elsewhere.py'\n"
        "    elsewhere.write_text('# elsewhere\\n', encoding='utf-8')\n"
        "    here.unlink()\n"
        "    os.symlink(elsewhere, here)\n"
    )
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "failed", report.model_dump_json(indent=2)
    assert "could not be put back" in report.script_detail
    assert report.reproduces is False


# ---------------------------------------------------- and the tenth review's


def test_the_digest_tells_sharing_inside_a_container() -> None:
    """Numbering the names caught sharing between them and nothing below.

    `[x, x]` and `[[], []]` render identically whatever is done to the
    rendering, and no amount of numbering the *names* separates them — the
    two members are one object in the first and two in the second, and
    `a[0].append(1); a[1]` tells them apart. So the structure is walked
    rather than printed, and a reference is part of the fingerprint wherever
    it occurs.
    """
    inner: list = []
    assert state_digest({"a": [inner, inner]}, {}, 4096) != state_digest(
        {"a": [[], []]}, {}, 4096
    )

    # At any depth, and through a name that is bound below another rather
    # than beside it.
    box: list = [[]]
    assert state_digest({"a": box, "b": box[0]}, {}, 4096) != state_digest(
        {"a": [[]], "b": []}, {}, 4096
    )

    # Two namespaces built the same way still agree, and the values inside
    # are still compared rather than replaced by their positions.
    other: list = []
    assert state_digest({"a": [other, other]}, {}, 4096) == state_digest(
        {"a": [inner, inner]}, {}, 4096
    )
    assert state_digest({"a": [[1]]}, {}, 4096) != state_digest({"a": [[2]]}, {}, 4096)


def test_a_graph_too_large_to_walk_is_refused() -> None:
    """A container can hold itself, and the walk is bounded so that it ends.

    Exceeding the bound refuses rather than truncating: a fingerprint of part
    of a graph, presented as one of the whole, is the same lie the truncated
    repr was.
    """
    loop: list = []
    loop.append(loop)
    # Cyclic is fine — the second sight of an object is a reference, not a
    # descent.
    assert state_digest({"a": loop}, {}, 4096)

    wide = {"a": [[] for _ in range(4096)]}
    assert state_digest(wide, {}, 16) == "", "no digest at all is the honest answer"


# -------------------------------------------------- and the eleventh review's


def test_the_script_is_checked_where_it_is_published(sympy_session, tmp_path) -> None:
    """Byte identity does not make a copy stand in for the artifact.

    Moving a file changes `__file__`, so a cell that branches on where the
    script sits takes one path under a check run from a scratch directory and
    the other when a reader runs the published file. The verdict would then
    describe a run nobody will ever perform.
    """
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import pathlib\n"
        '    if pathlib.Path(__file__).parent.name == "cas":\n'
        '        print("published only")\n'
    )
    directory = tmp_path / "cas"
    report = export_session(sympy_session, directory)
    assert report.script_verdict == "diverged", report.model_dump_json(indent=2)
    assert report.reproduces is False

    # And the artifact really does print it, which is what makes the verdict
    # above the honest one rather than a false alarm.
    run = subprocess.run(
        [sys.executable, str(directory / "session.py")],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    assert "published only" in run.stdout


def test_a_script_that_exits_early_has_not_reproduced_anything(
    sympy_session, tmp_path
) -> None:
    """The closing marker is an `atexit` callback, and `atexit` fires on
    `SystemExit(0)` as readily as on reaching the end of the file. A first cell
    guarded by `if __name__ == "__main__": raise SystemExit(0)` is silent under
    the driver and ends the script under `python session.py`, so both markers
    went out around an empty transcript that matched a record of silent cells,
    and the export called that reproduction."""
    sympy_session.execute('if __name__ == "__main__":\n    raise SystemExit(0)\n')
    sympy_session.execute("import random")
    sympy_session.execute("x = random.random()")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "failed", report.model_dump_json(indent=2)
    assert "stopped before it reached its last cell" in report.script_detail


def test_a_script_that_runs_to_the_end_still_verifies(sympy_session, tmp_path) -> None:
    """The refusal above must not have cost every export."""
    sympy_session.execute("x = symbols('x')")
    sympy_session.execute("factor(x**2 - 1)")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)


def test_the_displayed_value_is_fingerprinted_like_any_other_name() -> None:
    """`_` was skipped while it held the value `value_repr` had recorded.

    That justification cost three findings before it was abandoned. The last
    of them is this one: `(lambda x: [x, x])([])` and `[[], []]` have the same
    `value_repr` and different futures, so a trailing expression that was the
    only binding for an alias-sensitive container went into neither the repr
    nor the digest — and with `_` the only name in the namespace, the digest
    was SHA-256 of nothing at all.
    """
    shared: list = []
    together = {"_": [shared, shared]}
    apart = {"_": [[], []]}
    assert repr(together["_"]) == repr(apart["_"]), "the repr cannot tell them apart"
    assert state_digest(together, {}, 4096) != state_digest(apart, {}, 4096)
    assert state_digest(together, {}, 4096), "and it is a fingerprint, not an empty hash"

    # A cell that assigns `_` itself has no `value_repr` at all, which is the
    # first of the three findings and stays fixed.
    assert state_digest({"_": 1}, {}, 4096) != state_digest({"_": 2}, {}, 4096)


def test_the_fingerprint_payload_is_bounded_as_well_as_the_node_count(
    monkeypatch,
) -> None:
    """A node budget bounds how many objects the walk visits, not what they
    render to. One container of a few thousand distinct near-cap strings built
    the whole fingerprint in memory before hashing any of it — 1.5 GiB under a
    256 KiB cap, in a kernel a session's whole state depends on staying alive.
    """
    limit = 4096
    fed: list[int] = []
    real = hashlib.sha256

    class Counting:
        def __init__(self) -> None:
            self._inner = real()

        def update(self, payload: bytes) -> None:
            fed.append(len(payload))
            self._inner.update(payload)

        def hexdigest(self) -> str:
            return self._inner.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", Counting)
    held = [("x" * (limit - 64)) + str(index) for index in range(512)]
    digest = state_digest({"a": held}, {}, limit)

    assert digest == "", "past the payload bound there is no fingerprint to give"
    # Streamed rather than accumulated: no single write is anywhere near the
    # total, which is what keeps the peak to one leaf's repr.
    assert fed and max(fed) <= limit + 64, max(fed)


def test_an_ordinary_namespace_is_still_within_the_payload_bound() -> None:
    """The bound above must not have cost every digest."""
    assert state_digest({"a": list(range(1000)), "b": "x" * 100}, {}, 4096)


# --------------------------------------------------- and the twelfth review's


def test_a_descendant_cannot_rewrite_the_artifact_after_the_verdict(
    sympy_session, tmp_path
) -> None:
    """A descendant with its own stdout is invisible to every check the run
    makes: the drain workers finish, the capture looks complete, and it
    outlives the script. One that slept and then rewrote `sys.argv[0]` changed
    the published file after the manifest had recorded its hash — with the
    verdict already `verified`.
    """
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import subprocess, sys\n"
        "    subprocess.Popen(\n"
        "        [sys.executable, '-c',\n"
        "         'import time, sys, pathlib; time.sleep(3); "
        "pathlib.Path(sys.argv[1]).write_text(\"# late\\\\n\")', sys.argv[0]],\n"
        "        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
    )
    directory = tmp_path / "cas"
    report = export_session(sympy_session, directory)
    published = directory / "session.py"
    before = published.read_bytes()

    # The verdict says what it is: nothing here disagreed with the record, and
    # a run that outlives itself is not one the check saw the whole of.
    assert report.script_verdict == "unverified", report.model_dump_json(indent=2)
    assert "left a process running" in report.script_detail
    # And the manifest's hash still describes the file, which it did not when
    # the descendant was left to run.
    recorded = json.loads((directory / "export.json").read_text(encoding="utf-8"))
    time.sleep(4)
    assert published.read_bytes() == before, "the artifact was rewritten after the export"
    digest = hashlib.sha256(published.read_bytes()).hexdigest()
    assert digest in json.dumps(recorded), recorded


def test_output_from_a_descendant_is_still_compared_not_waved_through(
    sympy_session, tmp_path
) -> None:
    """Killing what the run left behind must not throw away the evidence. A
    child that prints arrives on the inherited descriptor and is drained before
    anything is killed, so a script whose child contradicts the record is
    `diverged` on the evidence rather than unverified for want of it."""
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        "    import subprocess, sys\n"
        "    subprocess.Popen([sys.executable, '-c', "
        "\"import time; time.sleep(0.5); print('after the marker')\"])\n"
    )
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "diverged", report.model_dump_json(indent=2)


def test_a_cell_cannot_claim_the_script_finished(sympy_session, tmp_path) -> None:
    """The completion evidence was a list in the script's own namespace, so a
    cell could `append` to it and buy itself the finished marker before exiting
    early. There is no name to reach now: the evidence is a string generated
    per export, and a cell that assigns the name something of its own gets
    exactly what it assigned."""
    sympy_session.execute(
        'if __name__ == "__main__":\n'
        '    _hardy_finished = "«hardy-transcript-finished-0000000000000000»"\n'
        "    raise SystemExit(0)\n"
    )
    sympy_session.execute("y = 1")
    sympy_session.execute("z = 2")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "failed", report.model_dump_json(indent=2)
    assert "stopped before it reached its last cell" in report.script_detail


def test_the_completion_evidence_is_fresh_for_each_export(
    sympy_session, tmp_path
) -> None:
    """A constant is one a cell could type on purpose."""
    sympy_session.execute("1 + 1")
    first = export_session(sympy_session, tmp_path / "one").script_path
    second = export_session(sympy_session, tmp_path / "two").script_path
    pattern = re.compile(r"«hardy-transcript-finished-[0-9a-f]+»")
    one = pattern.findall(Path(first).read_text(encoding="utf-8"))
    two = pattern.findall(Path(second).read_text(encoding="utf-8"))
    assert len(one) == len(two) == 1, (one, two)
    assert one != two


def test_a_cell_may_print_something_that_looks_like_hardys_own_sentinel(
    sympy_session, tmp_path
) -> None:
    """The first attempt at this used a fixed marker and looked for it anywhere
    in the output, so a cell whose legitimate output happened to equal Hardy's
    sentinel failed a faithful export on the strength of its own text."""
    sympy_session.execute("print('«hardy-transcript-finished-0123456789abcdef»')")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)
    assert report.reproduces


def test_a_cell_that_sabotages_the_interpreter_keeps_a_runnable_artifact(
    sympy_session, tmp_path
) -> None:
    """Whatever says the file finished runs at the end of the file, where a
    cell has had its turn with every name. An `__import__("builtins").print`
    there would raise under `__import__ = None` and leave a published script
    that dies when a reader runs it — Hardy's own statement breaking an
    otherwise working artifact. A bare assignment cannot."""
    sympy_session.execute("2 + 2")
    sympy_session.execute("__import__ = None")
    sympy_session.execute("print = lambda *_: None")
    directory = tmp_path / "cas"
    report = export_session(sympy_session, directory)
    assert report.script_verdict == "verified", report.model_dump_json(indent=2)

    run = subprocess.run(
        [sys.executable, str(directory / "session.py")],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    assert run.returncode == 0, run.stderr


def test_a_rebuild_says_which_gap_its_unverified_cells_have(sympy_session) -> None:
    """`unverified` carries cells that carry no digest and cells whose capture
    was clipped, and the note named only the first. Telling a reader whose
    state *was* compared that there is no state digest for it sends them to
    look in the wrong place."""
    session = CasSession(
        backend=backend_for("sympy"),
        command=None,
        log_path=sympy_session.log_path.parent / "which-gap.jsonl",
        limits=RunLimits(cas_cell_seconds=120, cas_output_bytes=4_096),
        cwd=sympy_session.cwd,
    )
    try:
        clipped = session.execute("print('x' * 5000)")
        assert clipped.capture_truncated is True
        assert clipped.state_digest, "its namespace was fingerprinted"

        session._drop_kernel()
        rebuilt = session.execute("1 + 1")
        assert "captured up to cas_output_bytes" in rebuilt.restart_note
        assert "no state digest" not in rebuilt.restart_note
    finally:
        session.close()


# ------------------------------------------------- and the thirteenth review's


def test_a_repr_that_changes_the_namespace_withholds_the_digest() -> None:
    """Fingerprinting runs `repr`, and a `__repr__` is a cell's own code.

    One that assigns `globals()["a"]` mutates a name already hashed, so the
    digest describes a namespace that no longer exists by the time it is
    finished — and if what it assigns differs run to run, the recorded digest
    and the replay's agree while the two namespaces do not. That is the exact
    failure the digest exists to catch, arriving through the digest itself.
    """
    import time as clock

    namespace: dict = {}

    class Restless:
        def __repr__(self) -> str:
            # `a` sorts first, so this lands after it has been hashed.
            namespace["a"] = clock.time_ns()
            return "<Restless>"

    namespace.update({"a": 0, "b": Restless()})
    assert state_digest(namespace, {}, 4096) == ""

    # A repr that merely drifts is refused too: the second pass hashes what
    # the first left, and any mutation a fingerprint could see moves it.
    drifting: dict = {}

    class Counting:
        def __repr__(self) -> str:
            drifting["a"] += 1
            return "<Counting>"

    drifting.update({"a": 0, "b": Counting()})
    assert state_digest(drifting, {}, 4096) == ""


def test_a_namespace_nothing_touches_is_still_fingerprinted() -> None:
    """The second pass must not have cost every digest."""
    assert state_digest({"a": 1, "b": [2, 3], "c": "four"}, {}, 4096)
    assert state_digest({"x": __import__("sympy").Symbol("x")}, {}, 4096)


def test_a_mutating_repr_is_caught_end_to_end(sympy_session, tmp_path) -> None:
    """And the session says the rebuild was not checked, rather than checking
    it against a fingerprint of a namespace that had already moved on."""
    sympy_session.execute("import time")
    sympy_session.execute(
        "class Restless:\n"
        "    def __repr__(self):\n"
        "        globals()['a'] = time.time_ns()\n"
        "        return '<Restless>'\n"
    )
    quiet = sympy_session.execute("a = 0\nb = Restless()")
    assert quiet.accepted is True
    assert quiet.state_digest == "", "a fingerprint that moves the namespace is not one"

    report = export_session(sympy_session, tmp_path / "cas")
    assert report.unverified >= 1, report.model_dump_json(indent=2)
    assert any("state not compared" in verdict.detail for verdict in report.verdicts)


def test_a_platform_that_cannot_sweep_descendants_does_not_claim_verified(
    sympy_session, tmp_path, monkeypatch
) -> None:
    """Windows has no process group to ask about or to signal, so a script's
    children can neither be accounted for nor stopped. Reporting "nothing was
    left behind" there said `verified` where the truth is that nobody looked —
    and a delayed child was still free to rewrite the published file after the
    readback and the manifest hash.
    """
    monkeypatch.setattr(cas, "can_sweep_descendants", lambda: False)
    sympy_session.execute("1 + 1")
    report = export_session(sympy_session, tmp_path / "cas")
    assert report.script_verdict == "unverified", report.model_dump_json(indent=2)
    assert "cannot account for what a script starts" in report.script_detail


def test_a_platform_that_can_sweep_still_verifies(sympy_session, tmp_path) -> None:
    """And where Hardy can look, it says what it found."""
    assert cas.can_sweep_descendants() is (os.name != "nt")
    sympy_session.execute("1 + 1")
    report = export_session(sympy_session, tmp_path / "cas")
    expected = "verified" if cas.can_sweep_descendants() else "unverified"
    assert report.script_verdict == expected, report.model_dump_json(indent=2)


# ----------------------------------------------- and the fourteenth review's


def test_a_replay_that_cannot_fingerprint_is_unchecked_not_divergent(
    sympy_session, tmp_path
) -> None:
    """The record carries a digest and the replay cannot produce one.

    A leaf whose `__repr__` succeeds live and mutates the namespace under a
    changed external condition leaves the same state and no fingerprint for
    it. Testing only the record for absence read that as a *different*
    namespace: the session was poisoned and the reader told the one thing that
    had not been established.
    """
    flag = tmp_path / "calm"
    flag.write_text("yes", encoding="utf-8")
    sympy_session.execute(f"import os, time\npath = {str(flag)!r}")
    sympy_session.execute(
        "class Weather:\n"
        "    def __repr__(self):\n"
        "        if not os.path.exists(path):\n"
        "            globals()['drift'] = time.time_ns()\n"
        "        return '<Weather>'\n"
    )
    quiet = sympy_session.execute("drift = 0\nw = Weather()")
    assert quiet.state_digest, "live, nothing moved and it was fingerprinted"

    flag.unlink()
    sympy_session._drop_kernel()
    after = sympy_session.execute("1 + 1")

    assert after.status == "ok", "the rebuild is not a divergence"
    assert "reconstructed, not verified" in after.restart_note
    # And it says which side was missing, rather than blaming the log for a
    # fingerprint it did carry.
    assert "the replay could not produce one" in after.restart_note
    assert "carry no state digest" not in after.restart_note


def test_an_export_says_which_side_could_not_be_fingerprinted(
    sympy_session, tmp_path
) -> None:
    """The same distinction on the export path."""
    flag = tmp_path / "calm"
    flag.write_text("yes", encoding="utf-8")
    sympy_session.execute(f"import os, time\npath = {str(flag)!r}")
    sympy_session.execute(
        "class Weather:\n"
        "    def __repr__(self):\n"
        "        if not os.path.exists(path):\n"
        "            globals()['drift'] = time.time_ns()\n"
        "        return '<Weather>'\n"
    )
    sympy_session.execute("drift = 0\nw = Weather()")

    flag.unlink()
    report = export_session(sympy_session, tmp_path / "cas")
    unverified = [v for v in report.verdicts if v.verdict == "unverified"]
    assert unverified, report.model_dump_json(indent=2)
    assert any("the replay could not fingerprint" in v.detail for v in unverified)
    assert not any(v.verdict == "diverged" for v in report.verdicts), (
        "not being able to compare is not a disagreement"
    )


def test_an_artifact_changed_while_the_export_is_written_is_not_described_as_sound(
    sympy_session, tmp_path, monkeypatch
) -> None:
    """`_verify_script` reads the script back the instant the run ends, and the
    notebook is rendered after that. Anything that changed a published file in
    between — a descendant that left its process group and outlived the sweep,
    most of all — would have been recorded under a hash of bytes nothing on
    disk had. The rewrite is simulated here, because a race cannot be timed
    from a test; the window is what is being pinned.
    """
    from hardy import cas_export

    real = cas_export.render_notebook

    def rewrite_then_render(session, cells, verdicts, script_verdict):
        (tmp_path / "cas" / "session.py").write_text("# clobbered\n", encoding="utf-8")
        return real(session, cells, verdicts, script_verdict)

    sympy_session.execute("1 + 1")
    monkeypatch.setattr(cas_export, "render_notebook", rewrite_then_render)
    report = export_session(sympy_session, tmp_path / "cas")

    assert report.script_verdict == "failed", report.model_dump_json(indent=2)
    assert "changed on disk while this export was still being written" in report.script_detail
    assert report.reproduces is False
    # The bytes are back, and the manifest describes what is there.
    published = (tmp_path / "cas" / "session.py").read_bytes()
    assert published.decode("utf-8").strip() != "# clobbered"
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))
    assert manifest["files"]["session.py"] == hashlib.sha256(published).hexdigest()
