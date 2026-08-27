"""The six findings PR #35 shipped with, each pinned by the case that showed it.

Every test here is a recovery, a bound, a verification, or a protocol frame
reporting something stronger than its evidence supports. They are grouped the
way the issue grouped them rather than by the module they land in, because
what they have in common is the failure mode and not the file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from hardy.cas import CasError, CasSession, backend_for
from hardy.cas_driver import HEADER_BYTES, bounded_repr, state_digest
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
    assert "no cell's state could be compared" in rebuilt.restart_note


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
    first = {"a": ["z"] * 5_000 + ["end"]}
    second = {"a": ["z"] * 5_000 + ["different"]}
    assert state_digest(first, {}, 4096) == ""
    assert state_digest(second, {}, 4096) == ""


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
