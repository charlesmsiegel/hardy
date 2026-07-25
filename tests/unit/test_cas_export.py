"""Export honesty: the artifacts have to be checked, not just written."""

from __future__ import annotations

import hashlib
import json

import pytest

from hardy.cas import CasError
from hardy.cas_export import export_session


def test_export_writes_a_script_a_notebook_and_a_manifest(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        session.execute("b")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    notebook = json.loads((tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))

    assert "was run in a sandbox" in script  # the no-isolation warning travels with the artifact
    assert "a" in script and "b" in script
    assert notebook["nbformat"] == 4
    assert [cell["cell_type"] for cell in notebook["cells"]] == ["code", "code"]
    assert report.verified == 2
    assert report.reproduces

    # The manifest is written last and names both files by digest, so a crash
    # between the two writes leaves a detectably incomplete pair.
    for name, digest in manifest["files"].items():
        actual = hashlib.sha256((tmp_path / "cas" / name).read_bytes()).hexdigest()
        assert actual == digest


def test_a_cell_that_will_not_reproduce_is_marked_not_hidden(tmp_path, cas_session) -> None:
    """`drift` answers differently in every process. The export must say so."""
    session = cas_session()
    try:
        session.execute("a")
        session.execute("drift")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert report.diverged == 1
    assert report.verified == 1
    assert not report.reproduces
    assert [v.verdict for v in report.verdicts] == ["verified", "diverged"]

    # Written and marked, rather than withheld: a notebook labelled diverged is
    # more useful than no notebook.
    assert (tmp_path / "cas" / "session.ipynb").exists()
    notebook = json.loads((tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8"))
    verdicts = [cell["metadata"]["hardy"]["verification"] for cell in notebook["cells"]]
    assert verdicts == ["verified", "diverged"]
    assert "diverged" in (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")


def test_a_session_that_survived_a_restart_still_exports_as_verified(
    tmp_path, cas_session
) -> None:
    """The export's whole claim is that the artifacts reproduce the session.

    A cell run after a kernel death is an ordinary cell: it ran, the kernel
    recorded what it produced, and a fresh kernel replaying the accepted cells
    in order produces the same thing. Hardy's own note that the kernel had been
    rebuilt used to be recorded as part of that cell's output, so the replay --
    which has no restart to report -- necessarily disagreed with it, and every
    post-restart cell was published as `diverged`.
    """
    session = cas_session(cas_cell_seconds=1)
    try:
        session.execute("a")
        session.execute("hang")  # kills the kernel; never accepted
        restarted = session.execute("b")
        assert restarted.restart_note
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert [verdict.verdict for verdict in report.verdicts] == ["verified", "verified"]
    assert report.reproduces
    assert report.diverged == 0

    # And the note is nowhere in the published artifacts either: the notebook
    # is a record of what the kernel printed.
    notebook = json.loads((tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8"))
    assert "kernel restarted" not in json.dumps(notebook)


def test_failed_cells_are_distinguished_from_diverged_ones(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("a")
        session.execute("die")  # not accepted, so it cannot reach the export
        assert len(session.accepted()) == 1
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()
    assert report.verified == 1 and report.failed == 0


def test_export_refuses_a_session_with_nothing_in_it(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        with pytest.raises(CasError, match="no accepted cells"):
            export_session(session, tmp_path / "cas")
    finally:
        session.close()


def test_only_accepted_cells_reach_the_script(tmp_path, cas_session) -> None:
    session = cas_session()
    try:
        session.execute("keep")
        session.execute("boom")
        export_session(session, tmp_path / "cas")
    finally:
        session.close()
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    assert "keep" in script
    assert "boom" not in script
