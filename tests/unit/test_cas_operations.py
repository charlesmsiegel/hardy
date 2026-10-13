"""Cancellation belongs to the active export child, not the live kernel."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from hardy.algebra.cas import CasError, backend_for
from hardy.algebra.contracts import CellRecord
from hardy.algebra.operations import OperationControl
from hardy.algebra.replay import replay_in_fresh_kernel
from hardy.algebra.scripts import run_exported_script
from hardy.workflows.contracts import RunLimits


def test_a_stop_before_binding_is_delivered_and_escalation_targets_the_active_child():
    control = OperationControl()
    reached = []
    assert control.stop()
    with control.bind(lambda: reached.append("interrupt") or True,
                      lambda: reached.append("kill") or True):
        assert reached == ["interrupt"]
        assert control.stop()
        assert reached == ["interrupt", "kill"]
    with pytest.raises(CasError, match="cancelled"):
        control.check()
    assert control.stop()
    assert reached == ["interrupt", "kill"]


@pytest.mark.parametrize("operation", ["replay", "script", "script_without_console", "script_ignores_interrupt"])
def test_stopping_an_export_interrupts_its_actual_child(tmp_path, monkeypatch, operation):
    if operation == "script_without_console":
        monkeypatch.setattr("hardy.algebra.scripts.signal_interrupt", lambda process: False)
    backend = backend_for("sympy")
    control = OperationControl()
    ready = tmp_path / "ready"
    source = f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(60)"
    if operation == "script_ignores_interrupt":
        source = "import signal; signal.signal(signal.SIGINT, signal.SIG_IGN); " + source

    def run():
        if operation == "replay":
            return replay_in_fresh_kernel(
                backend=backend, command=None,
                cells=[CellRecord(seq=0, segment=0, author="human", source=source,
                                  status="ok", accepted=True)],
                limits=RunLimits(cas_cell_seconds=60), cwd=tmp_path, control=control,
            )
        script = tmp_path / "session.py"
        script.write_text(source, encoding="utf-8")
        return run_exported_script(backend=backend, command=None, script=script,
                                   cwd=tmp_path, timeout=60, max_output_bytes=1024,
                                   control=control)

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(run)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and not worker.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), worker.exception() if worker.done() else "child never started"
            assert control.stop()
            with pytest.raises(CasError, match="cancelled"):
                worker.result(timeout=5)
        finally:
            control.stop()


@pytest.mark.parametrize("operation", ["replay", "script"])
def test_a_pending_export_stop_prevents_child_creation(tmp_path, monkeypatch, operation):
    from hardy.algebra import replay, scripts

    control = OperationControl()
    control.stop()

    def spawn(*args, **kwargs):
        pytest.fail("a stopped export spawned a child")

    monkeypatch.setattr(replay, "CasSession", spawn)
    monkeypatch.setattr(scripts.subprocess, "Popen", spawn)
    with pytest.raises(CasError, match="cancelled"):
        if operation == "replay":
            replay_in_fresh_kernel(backend=backend_for("sympy"), command=None, cells=[],
                                   limits=RunLimits(), cwd=tmp_path, control=control)
        else:
            run_exported_script(backend=backend_for("sympy"), command=None,
                                script=tmp_path / "missing.py", cwd=tmp_path,
                                timeout=60, max_output_bytes=1024, control=control)


def test_cancelled_export_preserves_the_live_kernel_and_publishes_no_success(tmp_path):
    from hardy.algebra.cas import CasSession
    from hardy.algebra.export import export_session

    original = CasSession(backend=backend_for("sympy"), command=None,
                          log_path=tmp_path / "cells.jsonl",
                          limits=RunLimits(cas_cell_seconds=60), cwd=tmp_path)
    flag, ready = tmp_path / "recorded", tmp_path / "replaying"
    source = (
        f"import pathlib, time\nflag = pathlib.Path({str(flag)!r})\n"
        f"if flag.exists():\n    pathlib.Path({str(ready)!r}).touch()\n    time.sleep(60)\n"
        "else:\n    flag.touch()\nx = 41"
    )
    control = OperationControl()
    try:
        assert original.execute(source).accepted
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(export_session, original, tmp_path, control=control)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and not worker.done() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready.exists()
                control.stop()
                with pytest.raises(CasError, match="cancelled"):
                    worker.result(timeout=10)
            finally:
                control.stop()
        assert not (tmp_path / "export.json").exists()
        assert original.execute("x + 1").value_repr == "42"
    finally:
        original.close()


def test_script_stop_kills_pipe_holding_descendants_before_settling_readers(tmp_path, monkeypatch):
    import os
    import threading

    from hardy.algebra import scripts

    if os.name == "nt":
        pytest.skip("uses POSIX signal handlers to make the descendant ignore SIGINT")
    child_ready = tmp_path / "child-ready"
    child_source = (
        "import signal, pathlib, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        f"pathlib.Path({str(child_ready)!r}).touch(); time.sleep(60)"
    )
    source = (
        "import subprocess, sys, time\n"
        f"child = {child_source!r}\n"
        "subprocess.Popen([sys.executable, '-c', child])\n"
        "time.sleep(60)\n"
    )
    script = tmp_path / "session.py"
    script.write_text(source, encoding="utf-8")
    settling, release = threading.Event(), threading.Event()
    drain = scripts._drain_capped

    def paused_drain(*args):
        drain(*args)
        settling.set()
        release.wait(15)

    monkeypatch.setattr(scripts, "_drain_capped", paused_drain)
    kills = []
    kill_group = scripts.kill_group

    def observed_kill(process):
        kills.append(process.pid)
        return kill_group(process)

    monkeypatch.setattr(scripts, "kill_group", observed_kill)
    control = OperationControl()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(run_exported_script, backend=backend_for("sympy"), command=None,
                             script=script, cwd=tmp_path, timeout=60, max_output_bytes=1024,
                             control=control)
        try:
            deadline = time.monotonic() + 10
            while not child_ready.exists() and not worker.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert child_ready.exists()
            control.stop()
            # EOF means the descendant holding these pipes really died;
            # it must happen before the reader settlement joins expire.
            assert settling.wait(3)
            assert not worker.done()
            # During settlement a second press still addresses this script.
            before = len(kills)
            assert control.stop()
            assert len(kills) == before + 1
            release.set()
            with pytest.raises(CasError, match="cancelled"):
                worker.result(timeout=3)
        finally:
            control.stop()
            release.set()
