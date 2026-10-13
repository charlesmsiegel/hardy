"""Regressions for exported artifacts preserving accepted source semantics."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from hardy.algebra.backends import SympyBackend, backend_for
from hardy.algebra.cas import CasSession
from hardy.algebra.export import export_session
from hardy.foundation.truncation import truncate
from hardy.workflows.contracts import RunLimits


def test_notebook_cell_ids_are_valid_unique_and_stable(tmp_path, cas_session):
    session = cas_session()
    session.execute("a")
    session.execute("b")
    export_session(session, tmp_path / "cas")
    book = json.loads((tmp_path / "cas/session.ipynb").read_text())
    nbformat.validate(book)
    ids = [cell["id"] for cell in book["cells"]]
    assert len(set(ids)) == len(ids)
    assert all(1 <= len(value) <= 64 and value.replace("-", "").isalnum() for value in ids)
    export_session(session, tmp_path / "cas")
    assert ids == [cell["id"] for cell in json.loads((tmp_path / "cas/session.ipynb").read_text())["cells"]]


@pytest.mark.parametrize("source", ["x = 1\n\f\nx", 's = "a\u2028b"\ns', 's = "a\u2029b"\rs', "x = 1\r\nx"])
def test_parser_line_offsets_preserve_source(source):
    backend = SympyBackend()
    script = backend.preamble + "\n" + backend.render_cell(source)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"1", repr("a\u2028b"), repr("a\u2029b")}


def test_unicode_separators_do_not_advance_file_line_numbers():
    text = "first\f\u2028still first\r\nsecond\nthird"
    result = truncate(text, start_line=2, line_limit=1)
    assert result.total_lines == 3
    assert result.text == "second\n"
    assert result.next_line == 3


@pytest.mark.parametrize("banner", ["helper.m2:3:1:(3):[1]: error: x", "/tmp/with spaces/helper.m2:3:1:(3): error: x"])
def test_loaded_macaulay2_banners_are_errors(banner):
    assert backend_for("macaulay2").classify("", banner) == "error"


def test_legacy_path_is_inert_in_script(tmp_path, cas_session):
    from hardy.algebra.export import CellVerdict, render_script
    session = cas_session()
    record = session.execute("1")
    record = record.model_copy(update={"path": "a\nraise RuntimeError('path executed') #.py", "source": "1"})
    script = render_script(session, (record,), [CellVerdict(seq=record.seq, verdict="verified")])
    compile(script, "<export>", "exec")
    assert "\nraise RuntimeError" not in script


def test_real_sympy_global_underscore_exports_faithfully(tmp_path):
    session = CasSession(backend=SympyBackend(), command=None, log_path=tmp_path / "cells.jsonl", limits=RunLimits(cas_cell_seconds=120), cwd=tmp_path)
    try:
        for source in ("for _ in range(2):\n    pass", "3 + 3", "None", "_"):
            assert session.execute(source).accepted
        report = export_session(session, tmp_path / "cas")
        assert report.reproduces, report.model_dump_json()
    finally:
        session.close()


@pytest.mark.real_toolchain
def test_real_macaulay2_loaded_file_error_is_not_accepted(tmp_path):
    command = shutil.which("M2")
    if not command:
        pytest.skip("Macaulay2 is not installed")
    (tmp_path / "bad.m2").write_text("1/0\n")
    session = CasSession(backend=backend_for("macaulay2"), command=Path(command), log_path=tmp_path / "cells.jsonl", limits=RunLimits(cas_cell_seconds=120), cwd=tmp_path)
    try:
        record = session.execute('load "bad.m2"')
        assert record.status == "error", record
        assert not record.accepted
    finally:
        session.close()


def test_macaulay2_failed_cell_note_is_stripped_before_error_search(tmp_path, cas_session):
    from hardy.algebra.export import CellVerdict, render_script
    session = cas_session()
    record = session.execute("a")
    backend = backend_for("macaulay2")
    session.backend = backend
    script = render_script(session, (record,), [CellVerdict(seq=record.seq, verdict="failed", detail="helper.m2:3:1:(3):[1]: error: x")])
    # Macaulay2 echoes source with an input prompt; the echoed comment must
    # not make a clean script's transcript look like an interpreter failure.
    note = next(line for line in script.splitlines() if line.startswith("-- --- cell"))
    echoed = "i1 : " + note
    assert backend.classify(backend.sanitize(echoed, script)) == "ok"


@pytest.mark.parametrize("source", ["__import__ = None; 6", "sys = None; 6", "print = None; repr = None; globals = None; 6"])
def test_display_helper_survives_import_name_rebinding(tmp_path, source):
    session = CasSession(backend=SympyBackend(), command=None, log_path=tmp_path / "cells.jsonl", limits=RunLimits(cas_cell_seconds=120), cwd=tmp_path)
    try:
        record = session.execute(source)
        assert record.accepted and record.value_repr == "6"
        backend = SympyBackend()
        result = subprocess.run([sys.executable, "-c", backend.preamble + "\n" + backend.render_cell(record.source)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "6\n"
    finally:
        session.close()


def test_display_helper_uses_current_stdout():
    backend = SympyBackend()
    source = 'original = sys.stdout; sink = __import__("io").StringIO(); sys.stdout = sink; 6'
    script = backend.preamble + "\n" + backend.render_cell(source)
    script += '\nsys.stdout = original; print(sink.getvalue(), end="")'
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "6\n"
