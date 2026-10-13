"""Public evidence survives failed publication and portable text inputs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hardy.app import evals as commands
from hardy.evals import runner, summary
from hardy.foundation import locking
from hardy.foundation.locking import FileInUse


def _board():
    return SimpleNamespace(model_dump=lambda **kw: {"label": "portable", "claim": "ℝ"})


def _denied():
    error = PermissionError(13, "locked")
    error.winerror = 32
    return error


def test_scoreboard_retries_transient_lock(monkeypatch, tmp_path):
    target = tmp_path / "scoreboard.json"
    monkeypatch.setattr(locking, "_SHARING_RETRY", True)
    monkeypatch.setattr(locking, "_sleep", lambda _: None)
    real_replace = os.replace
    attempts = []

    def replace(source, destination):
        attempts.append(destination)
        if len(attempts) < 3:
            raise _denied()
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    runner._write(target, _board())
    assert len(attempts) == 3
    assert json.loads(target.read_text(encoding="utf-8"))["claim"] == "ℝ"
    assert list(tmp_path.iterdir()) == [target]


def test_scoreboard_permanent_lock_preserves_old_bytes_and_cleans_temp(monkeypatch, tmp_path):
    target = tmp_path / "scoreboard.json"
    target.write_bytes(b"previous board")
    monkeypatch.setattr(locking, "_SHARING_RETRY", True)
    monkeypatch.setattr(locking, "_sleep", lambda _: None)

    def replace(*args):
        raise _denied()

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(FileInUse, match="scoreboard.json"):
        runner._write(target, _board())
    assert target.read_bytes() == b"previous board"
    assert list(tmp_path.iterdir()) == [target]


def _pool_args(tmp_path, labels):
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_bytes(b"{}")
    for label in set(labels):
        board = tmp_path / "boards" / label
        board.mkdir(parents=True, exist_ok=True)
        (board / "scoreboard.json").write_bytes(b"{}")
    return argparse.Namespace(corpus=corpus, baseline=baseline, scoreboards=tmp_path / "boards", labels=labels, out=None)


def _pool_result():
    return {"boards": ["a", "b"], "aggregates": {"headline": {"solve_rate": 0, "n": 2}}}


def test_pool_default_path_is_label_order_independent(monkeypatch, tmp_path):
    from hardy.evals import pool

    monkeypatch.setattr(pool, "pool", lambda *a, **kw: _pool_result())
    monkeypatch.setattr(commands, "DEFAULT_POOLS", tmp_path / "pools")
    assert commands.run_pool(_pool_args(tmp_path, ["b", "a"])) == 0
    assert (tmp_path / "pools" / "a" / "pool.json").is_file()
    assert not (tmp_path / "pools" / "b").exists()


def test_pool_duplicate_labels_refuse_before_publication(monkeypatch, tmp_path):
    from hardy.evals import pool

    monkeypatch.setattr(pool, "pool", lambda *a, **kw: _pool_result())
    monkeypatch.setattr(commands, "DEFAULT_POOLS", tmp_path / "pools")
    assert commands.run_pool(_pool_args(tmp_path, ["a", "a"])) == 2
    assert not (tmp_path / "pools").exists()


@pytest.mark.parametrize("kind", ["pool", "summary", "scoreboard"])
def test_failed_publication_preserves_old_file(monkeypatch, tmp_path, kind):
    target = tmp_path / "result.json"
    target.write_bytes(b"previous evidence")

    def interrupted(*args):
        raise OSError("interrupted temporary write")

    monkeypatch.setattr(locking.os, "fsync", interrupted)
    with pytest.raises(OSError, match="interrupted temporary write"):
        if kind == "scoreboard":
            runner._write(target, _board())
        elif kind == "pool":
            from hardy.evals import pool

            monkeypatch.setattr(pool, "pool", lambda *a, **kw: _pool_result())
            args = _pool_args(tmp_path, ["a"])
            args.out = target
            commands.run_pool(args)
        else:
            from hardy.corpus import catalog

            monkeypatch.setattr(summary, "build", lambda *a, **kw: {})
            monkeypatch.setattr(summary, "render", lambda *a, **kw: "new evidence\n")
            monkeypatch.setattr(catalog, "load_corpus", lambda *a: None)
            summary.write(tmp_path, problems_path=tmp_path, baseline_path=tmp_path, out_path=target)
    assert target.read_bytes() == b"previous evidence"
    assert not any(p.name.startswith("tmp") or p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_source_revision_handles_utf8_git_output_in_ascii_locale(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "core.quotepath", "false"], check=True)
    # Bytes name also permits constructing the fixture when this test itself
    # runs with an ASCII filesystem codec on POSIX. Git emits UTF-8 either way.
    with open(os.fsencode(repo) + b"/notes-\xe2\x84\x9d.md", "wb") as stream:
        stream.write(b"fixture")
    script = "from pathlib import Path; from hardy.evals import runner; runner._source_anchor=lambda: Path(" + ascii(str(repo)) + "); print(runner.source_revision())"
    result = subprocess.run([sys.executable, "-X", "utf8=0", "-c", script], env={**os.environ, "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}, capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("-dirty")


def test_caller_and_model_crlf_batch_artifacts_are_lf_and_consistent(tmp_path):
    from test_recorded_runs import FAKE_LEAN, IDENTITY, _Runtime

    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools
    from hardy.workflows import batch
    from hardy.workflows.acceptance import validate_recorded_run

    request = Request.from_dict({"declaration": "theorem HardyTarget :\r\n  True", "informal_claim": "True\r\nis true."})
    lean = LeanTools(request, (sys.executable, str(FAKE_LEAN)))
    script = [("submit_proof", {"proof": "by\r\n  exact True.intro"})]
    output = tmp_path / "batch"
    result = batch.run(request, lambda model=None, **context: _Runtime(script, **context), lean, output, max_turns=3, toolchain=IDENTITY)
    assert result.terminal_reason == "verified"
    assert b"\r" not in (output / "proof.lean").read_bytes()
    assert b"\r" not in (output / "writeup.md").read_bytes()
    assert validate_recorded_run(output) == ()


def test_evals_text_writers_explicitly_disable_platform_translation(monkeypatch, tmp_path):
    from test_evals_staged import ENTRY, _run_dir, _Runtime

    from hardy.evals import staged

    real_open = Path.open

    def windows_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and any(c in mode for c in "wax") and newline is None:
            newline = "\r\n"
        return real_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", windows_open)
    runtime = _Runtime({"equivalent": True, "canonical_entails_model": True, "model_entails_canonical": True})
    row = tmp_path / "row"
    def make_runtime(store):
        from hardy.workflows.contracts import RunPhase

        store.append("provider.fixture", {"text": "ℝ"}, phase=RunPhase.FORMALIZING)
        return runtime

    staged.compare_canonical(ENTRY, _run_dir(tmp_path), row, runtime_factory=make_runtime, model="reader@test", wall_seconds=60)
    assert b"\r" not in (row / "canonical.json").read_bytes()
    assert b"\r" not in (row / "canonical-trajectory.jsonl").read_bytes()
    empty = tmp_path / "empty"
    empty.mkdir()
    row = tmp_path / "missing"
    staged.compare_canonical(ENTRY, empty, row, runtime_factory=lambda store: runtime, model="reader@test", wall_seconds=60)
    assert b"\r" not in (row / "canonical.json").read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows sharing violations")
@pytest.mark.parametrize("permanent", [False, True])
def test_native_windows_scoreboard_publication_with_open_reader(tmp_path, monkeypatch, permanent):
    import threading

    target = tmp_path / "scoreboard.json"
    target.write_bytes(b"previous evidence")
    opened, release = threading.Event(), threading.Event()

    def read():
        with target.open("rb"):
            opened.set()
            release.wait(10)

    reader = threading.Thread(target=read)
    reader.start()
    assert opened.wait(5)
    sleeps = []

    def retry_wait(delay):
        sleeps.append(delay)
        if not permanent:
            release.set()
            reader.join(timeout=5)
            assert not reader.is_alive()

    monkeypatch.setattr(locking, "_sleep", retry_wait)
    try:
        if permanent:
            with pytest.raises(FileInUse, match="scoreboard.json"):
                runner._write(target, _board())
            assert target.read_bytes() == b"previous evidence"
        else:
            runner._write(target, _board())
            assert json.loads(target.read_text(encoding="utf-8"))["label"] == "portable"
        assert sleeps, "the real open handle must cause a Windows sharing violation"
        assert list(tmp_path.iterdir()) == [target]
    finally:
        release.set()
        reader.join(timeout=5)


@pytest.mark.parametrize('declaration,proof', [
    ('theorem HardyTarget :\r\n  True', 'by\n  sorry'),
    ('theorem HardyTarget : True', 'by\r\n  sorry'),
])
def test_crlf_partial_sketch_keeps_consistent_lf_evidence(tmp_path, declaration, proof):
    from test_recorded_runs import FAKE_LEAN, IDENTITY, _Runtime

    from hardy.formal.contracts import Request
    from hardy.formal.lean import LeanTools
    from hardy.workflows import batch
    from hardy.workflows.recorded import validate_recorded_run

    request = Request.from_dict({'declaration': declaration, 'informal_claim': 'True is true.'})
    lean = LeanTools(request, (sys.executable, str(FAKE_LEAN)))
    script = [('sketch_proof', {'proof': proof})]
    output = tmp_path / 'partial'
    result = batch.run(request, lambda model=None, **context: _Runtime(script, **context),
                       lean, output, max_turns=3, toolchain=IDENTITY)
    assert result.terminal_reason != 'verified'
    assert result.sketch is not None
    assert b'\r' not in (output / 'writeup.md').read_bytes()
    assert validate_recorded_run(output) == ()
