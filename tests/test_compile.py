import sys
import time
from pathlib import Path

from hardy.latex.compile import compile_tex

FAKE_ENGINE = [sys.executable, str(Path(__file__).parent / "fake_tectonic.py")]

SOURCE = r"\documentclass{article}\begin{document}hi\end{document}"


def test_success_produces_pdf(tmp_path):
    result = compile_tex(SOURCE, tmp_path / "staging", engine=FAKE_ENGINE)
    assert result.success
    assert result.errors == []
    assert result.pdf_path is not None and result.pdf_path.exists()
    assert (tmp_path / "staging" / "main.tex").read_text() == SOURCE


def test_failure_parses_structured_errors(tmp_path):
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "fail"},
    )
    assert not result.success
    assert result.pdf_path is None
    stderr_err = next(e for e in result.errors if e.line is not None)
    assert stderr_err.file == "main.tex"
    assert stderr_err.line == 3
    assert "Undefined control sequence" in stderr_err.message
    assert any(e.line is None and "Undefined control sequence" in e.message
               for e in result.errors)


def test_timeout_reported_not_raised(tmp_path):
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "hang"},
        timeout=0.5,
    )
    assert not result.success
    assert any("timed out" in e.message for e in result.errors)


def test_timeout_enforced_after_output_closes(tmp_path):
    # Compiler closes stdout/stderr (EOF) but keeps running — the timeout must
    # still fire instead of blocking forever on wait().
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "closehang"},
        timeout=0.5,
    )
    assert not result.success
    assert any("timed out" in e.message for e in result.errors)


def test_abort_kills_engine_child_process_group(tmp_path):
    # On a timeout abort, helper processes the engine spawned must die too, or
    # a delayed child could write into staging after compile_tex() returns.
    marker = tmp_path / "leaked"
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "grouphang", "HARDY_CHILD_MARKER": str(marker)},
        timeout=0.5,
    )
    assert not result.success
    time.sleep(3)  # the child would write the marker at 2s if it had survived
    assert not marker.exists()


def test_missing_engine_returns_structured_failure(tmp_path):
    # A missing/typo'd engine must yield a CompileResult, not an OSError.
    result = compile_tex(
        SOURCE, tmp_path / "staging", engine=["/nonexistent/tex-binary"]
    )
    assert not result.success
    assert result.errors and "could not launch compiler" in result.errors[0].message


def test_sandbox_timeout_arg_preserves_fractions():
    from hardy.latex.compile import _sandbox_timeout_arg

    assert _sandbox_timeout_arg(1.9) == "1.9"
    assert _sandbox_timeout_arg(0.5) == "0.5"
    assert float(_sandbox_timeout_arg(120.0)) == 120.0
    assert float(_sandbox_timeout_arg(0.0)) > 0  # never a zero/negative duration


def test_docker_client_env_preserves_connection(monkeypatch):
    from hardy.latex.compile import _docker_client_env

    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "prod")
    monkeypatch.setenv("HARDY_SECRET_TOKEN", "hunter2")
    env = _docker_client_env()
    assert env["DOCKER_HOST"] == "tcp://remote:2376"
    assert env["DOCKER_CONTEXT"] == "prod"
    assert "PATH" in env
    assert "HARDY_SECRET_TOKEN" not in env  # still scrubbed to the allowlist


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARDY_SECRET_TOKEN", "hunter2")
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "dump-env"},
    )
    seen = result.log_tail.split()
    assert "HARDY_SECRET_TOKEN" not in seen
    assert "PATH" in seen


def test_stale_artifacts_never_grade_current_run(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "main.pdf").write_bytes(b"stale pdf")
    (staging / "main.log").write_text("! Stale error from a previous run.\n")
    result = compile_tex(
        SOURCE, staging, engine=FAKE_ENGINE, extra_env={"FAKE_TEX_MODE": "fail"}
    )
    assert not result.success
    assert result.pdf_path is None  # the stale pdf must not pass the check
    assert all("Stale" not in e.message for e in result.errors)


def test_runaway_output_killed_at_cap(tmp_path):
    result = compile_tex(
        SOURCE,
        tmp_path / "staging",
        engine=FAKE_ENGINE,
        extra_env={"FAKE_TEX_MODE": "spew"},
        timeout=30,
    )
    assert not result.success
    assert any("output exceeded" in e.message for e in result.errors)


def test_extract_artifacts_takes_only_expected_members(tmp_path):
    import io
    import tarfile

    from hardy.latex.compile import _extract_artifacts

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for member_name, payload in [
            ("main.pdf", b"%PDF-1.4 ok"),
            ("main.log", b"log"),
            ("../evil.sh", b"nope"),
            ("other.txt", b"nope"),
        ]:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    _extract_artifacts(buf.getvalue(), tmp_path)
    assert (tmp_path / "main.pdf").read_bytes() == b"%PDF-1.4 ok"
    assert (tmp_path / "main.log").read_bytes() == b"log"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["main.log", "main.pdf"]
    # A garbled stream must not raise — the compile is graded by exit code.
    _extract_artifacts(b"this is not a tar stream", tmp_path)


import shutil

import pytest

from hardy.latex.template import render_writeup


@pytest.mark.tex
def test_rendered_template_compiles_with_real_texlive(tmp_path):
    if shutil.which("lualatex") is None:  # DEFAULT_ENGINE is lualatex
        pytest.skip("lualatex (TeX Live) not installed")
    source = render_writeup(
        title="A Test Theorem",
        statement=r"For every natural number $n$, $n + 0 = n$.",
        informal_proof=r"Immediate from the definition of addition.",
        formalization_status="not formalized",
    )
    result = compile_tex(source, tmp_path / "staging")
    assert result.success, result.errors
