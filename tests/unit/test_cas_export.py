"""Export honesty: the artifacts have to be checked, not just written."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from hardy.cas import CasError, CellOutcome
from hardy.cas_export import export_session
from hardy.layout import LayoutError


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


def test_a_truncated_capture_is_not_reported_as_verified(tmp_path, cas_session) -> None:
    """Matching prefixes are not a reproduction of the whole output.

    `flood` writes far more than `cas_output_bytes`, so both the live capture
    and the replay stop at the cap. The retained prefixes match, and nothing at
    all is known about the tails -- yet this used to be published as `verified`,
    and `ExportReport.reproduces` then claimed a complete reproduction on
    evidence that ran out partway through. AGENTS.md asks a partial result to
    state its limits.
    """
    session = cas_session(cas_output_bytes=4_096, cas_cell_seconds=30)
    try:
        record = session.execute("flood")
        assert record.capture_truncated is True
        assert record.accepted is True  # the driver knows it did not fail
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert [verdict.verdict for verdict in report.verdicts] == ["unverified"]
    assert "truncated" in report.verdicts[0].detail
    assert report.verified == 0 and report.unverified == 1
    assert not report.reproduces

    # And the notebook says so on the cell itself, not only in the manifest.
    notebook = json.loads((tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8"))
    metadata = notebook["cells"][0]["metadata"]["hardy"]
    assert metadata["verification"] == "unverified"
    assert metadata["capture_truncated"] is True


def test_a_truncated_capture_leaves_the_script_unverified_not_diverged(
    tmp_path, cas_session
) -> None:
    """`diverged` is an affirmative claim, and there is nothing to base it on.

    When a cell's capture stopped at `cas_output_bytes`, the record is a prefix
    and no complete transcript exists to compare a script run against. Saying
    "the script printed something different" about a tail Hardy never read is
    the same rounding-up that a truncated cell verdict refuses, pointed the
    other way.
    """
    session = cas_session(cas_output_bytes=4_096, cas_cell_seconds=30)
    try:
        session.execute("flood")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert report.script_verdict == "unverified", report.script_detail
    assert "cas_output_bytes" in report.script_detail
    assert not report.reproduces
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))
    assert manifest["script_verdict"] == "unverified"


def test_a_truncated_record_is_unverifiable_even_when_the_script_runs_clean(
    tmp_path, cas_session
) -> None:
    """The other half of the same rule, where the script itself behaves.

    A short, clean script run says nothing when the thing it is being compared
    against is a prefix: the recorded transcript is incomplete, so agreement
    over what survives is not agreement.
    """
    session = cas_session()
    try:
        session.execute("a")
        # Exactly what a cell that overran the cap leaves behind, without
        # needing a cell large enough to also overrun the script run's capture.
        session._records[-1] = session._records[-1].model_copy(
            update={"capture_truncated": True}
        )
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert report.script_verdict == "unverified", report.script_detail
    assert "truncated capture" in report.script_detail
    assert [verdict.verdict for verdict in report.verdicts] == ["unverified"]
    assert not report.reproduces


def test_the_script_detail_is_small_enough_to_publish(tmp_path, cas_session) -> None:
    """`script_detail` is copied into export.json, the notebook, and tool results.

    A recorded line may be `cas_output_bytes` long, so quoting a handful of them
    verbatim can make the explanation larger than either artifact it explains.
    """
    session = cas_session(cas_cell_seconds=30)
    try:
        # A 5 KiB line that differs in every process: the divergence is real,
        # and quoting the lines behind it verbatim is what has to be avoided.
        session.execute("longdrift")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert report.script_verdict == "diverged", report.script_detail
    assert len(report.script_detail) <= 600
    notebook = (tmp_path / "cas" / "session.ipynb").read_text(encoding="utf-8")
    assert len(json.loads(notebook)["metadata"]["hardy"]["script_verification"]["detail"]) <= 600


def test_a_check_that_blows_up_costs_the_verdict_not_the_artifacts(
    tmp_path, cas_session, monkeypatch
) -> None:
    """DESIGN.md asks for a partial artifact over silence, and this is the last
    step that could take one away: the script is written before it is run, so an
    exception escaping the check left a script with no notebook and no manifest
    beside it -- the exact half-written pair `export.json` exists to make
    detectable. A `MemoryError` from an unbounded capture was the live route in.
    """

    def explode(**_kwargs):
        raise MemoryError("out of memory reading the script's output")

    monkeypatch.setattr("hardy.cas_export.run_exported_script", explode)
    session = cas_session()
    try:
        session.execute("a")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert report.script_verdict == "unverified"
    assert "MemoryError" in report.script_detail
    assert not report.reproduces
    # Both artifacts and the manifest exist, and the manifest's digests match.
    for name in ("session.py", "session.ipynb", "export.json"):
        assert (tmp_path / "cas" / name).exists()
    manifest = json.loads((tmp_path / "cas" / "export.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((tmp_path / "cas" / name).read_bytes()).hexdigest() == digest


def test_a_multi_line_verdict_detail_still_publishes_a_runnable_script(
    tmp_path, cas_session, monkeypatch
) -> None:
    """The verdict is a comment, and a comment ends at the first newline.

    `_verdicts` builds a `failed` detail out of the replay's stderr, which for
    the default backend is a traceback. Written straight into the one-line
    `# --- cell N (model)  [failed: ...]` header, every line of it after the
    first landed in the script as source, and the published file died with
    `SyntaxError: unexpected indent` before reaching the first cell. This is
    reachable through exactly the drift the module docstring describes -- an
    accepted cell that fails in a fresh kernel -- so Hardy's own verification
    corrupted the artifact it was verifying and then reported a syntax error
    that was its own.
    """
    traceback = (
        'Traceback (most recent call last):\n  File "<hardy-cell>", line 1\n'
        "    a\nNameError: name 'a' is not defined\n"
    )

    def failed_replay(**_kwargs):
        return [CellOutcome(status="error", stderr=traceback)]

    monkeypatch.setattr("hardy.cas_export.replay_in_fresh_kernel", failed_replay)
    session = cas_session()
    try:
        session.execute("a")
        report = export_session(session, tmp_path / "cas")
    finally:
        session.close()

    assert [verdict.verdict for verdict in report.verdicts] == ["failed"]
    script = (tmp_path / "cas" / "session.py").read_text(encoding="utf-8")
    # The published bytes are the ones that have to compile. The fake kernel's
    # cells are plain Python names, so anything that fails here came out of
    # Hardy's own header.
    compile(script, "session.py", "exec")
    # Marked, not withheld: the failure still has to be visible in the file.
    assert "[failed:" in script
    assert "NameError" in script


def test_the_replay_directory_does_not_survive_into_the_next_export(
    tmp_path, cas_session
) -> None:
    """`script-run` is cleared between exports and `replay` was not.

    The replay's whole claim is that a *fresh* kernel reproduces the session,
    and a kernel that starts in the previous export's working directory can
    find a file the cell under test was supposed to create.
    """
    session = cas_session()
    try:
        session.execute("a")
        export_session(session, tmp_path / "cas")
        stray = tmp_path / "cas" / "replay" / "left-behind.txt"
        stray.write_text("written by the previous export\n", encoding="utf-8")
        export_session(session, tmp_path / "cas")
    finally:
        session.close()
    assert not stray.exists()


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


needs_symlinks = pytest.mark.skipif(
    os.name == "nt", reason="symlink_to needs Developer Mode on Windows"
)


@needs_symlinks
def test_an_export_directory_that_leaves_the_problem_is_refused(tmp_path, cas_session) -> None:
    """`cas/` is versioned, so a clone can ship it as a link out of the tree."""
    problem = tmp_path / "sylow"
    problem.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (problem / "cas").symlink_to(elsewhere, target_is_directory=True)
    session = cas_session()
    try:
        session.execute("a")
        with pytest.raises(LayoutError):
            export_session(session, problem / "cas")
    finally:
        session.close()
    assert list(elsewhere.iterdir()) == []


@needs_symlinks
def test_a_symlinked_replay_directory_does_not_become_a_kernel_cwd(tmp_path, cas_session) -> None:
    """The replay kernel runs the user's own cells, which write files.

    `shutil.rmtree(..., ignore_errors=True)` says nothing when it declines to
    remove a link, so the scratch tree used to become whatever it pointed at
    and the cells ran there.
    """
    directory = tmp_path / "cas"
    directory.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (directory / "replay").symlink_to(elsewhere, target_is_directory=True)
    session = cas_session()
    try:
        session.execute("a")
        with pytest.raises(LayoutError):
            export_session(session, directory)
    finally:
        session.close()
    assert list(elsewhere.iterdir()) == []
