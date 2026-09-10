"""One loaded CAS history has one writer, including across real processes."""
import subprocess
import sys

import pytest

from hardy.algebra.backends import backend_for
from hardy.algebra.contracts import CasError
from hardy.algebra.session import CasSession
from hardy.algebra.tools import build_runtime
from hardy.workflows.contracts import RunLimits


def session(log):
    return CasSession(backend=backend_for("macaulay2"), command=None,
                      log_path=log, limits=RunLimits())


def test_second_loaded_owner_cannot_reset_or_lose_spend(tmp_path):
    log = tmp_path / "cells.jsonl"
    first = session(log)
    try:
        first.charge(1)
        first.reset()
        with pytest.raises(CasError, match="another.*session|another.*process"):
            session(log)
    finally:
        first.close()
    second = session(log)
    try:
        second.charge(2)
        second.reset()
        assert [(r.seq, r.segment) for r in second.records()] == [(0, 1), (1, 2)]
        assert second.total_spent_seconds == 3
    finally:
        second.close()


def test_competing_constructor_cannot_repair_an_owners_partial_append(tmp_path):
    log = tmp_path / "cells.jsonl"
    first = session(log)
    try:
        log.write_bytes(b'{"seq":')
        with pytest.raises(CasError, match="another.*session|another.*process"):
            session(log)
        assert log.read_bytes() == b'{"seq":'
    finally:
        first.close()
    repaired = session(log)
    try:
        assert repaired.records() == () and log.read_bytes() == b""
    finally:
        repaired.close()


WORKER = """from pathlib import Path
import sys
from hardy.algebra.backends import backend_for
from hardy.algebra.contracts import CasError
from hardy.algebra.session import CasSession
from hardy.workflows.contracts import RunLimits
try:
    s=CasSession(backend=backend_for('macaulay2'), command=None,
                 log_path=Path(sys.argv[1]), limits=RunLimits())
except CasError as error:
    print('blocked: '+str(error), flush=True)
else:
    print('owned', flush=True)
    if len(sys.argv)>2:
        sys.stdin.readline()
    s.close()
"""


def test_real_process_cannot_join_a_live_owner(tmp_path):
    log = tmp_path / "cells.jsonl"
    first = session(log)
    try:
        result = subprocess.run([sys.executable, "-c", WORKER, str(log)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("blocked:"), result.stdout
    finally:
        first.close()


def test_process_death_releases_lease_without_deleting_rendezvous_file(tmp_path):
    log = tmp_path / "cells.jsonl"
    child = subprocess.Popen([sys.executable, "-c", WORKER, str(log), "hold"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(CasError, match="another.*session|another.*process"):
            session(log)
    finally:
        child.kill()
        child.communicate(timeout=10)
    reopened = session(log)
    try:
        reopened.reset()
        assert reopened.records()[0].seq == 0
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ["execute", "reset", "charge", "probe", "hold"])
def test_closed_owner_cannot_run_or_write_after_lease_passes_to_successor(tmp_path, operation, monkeypatch):
    log = tmp_path / "cells.jsonl"
    closed = session(log)
    closed.close()
    closed.close()
    successor = session(log)
    try:
        successor.reset()
        before = log.read_bytes()
        monkeypatch.setattr(closed, "_start", lambda: pytest.fail("closed session started a kernel"))
        with pytest.raises(CasError, match="closed"):
            if operation == "execute":
                closed.execute("1+1")
            elif operation == "reset":
                closed.reset()
            elif operation == "charge":
                closed.charge(1)
            elif operation == "probe":
                closed.probe_version()
            else:
                with closed.hold():
                    pytest.fail("closed session entered export hold")
        assert log.read_bytes() == before
        assert closed.records() == ()
    finally:
        successor.close()


def test_failed_load_releases_lease_for_a_repaired_reopen(tmp_path):
    log = tmp_path / "cells.jsonl"
    spend = log.with_name(log.name + ".spend.json")
    spend.write_text("invalid")
    with pytest.raises(CasError, match="spend could not be read"):
        session(log)
    spend.write_text("0")
    reopened = session(log)
    reopened.close()


def test_deferred_owner_loads_current_history_when_parent_becomes_usable(tmp_path):
    parent = tmp_path / "blocked"
    parent.write_text("not a directory")
    log = parent / "cells.jsonl"
    deferred = session(log)
    try:
        assert deferred.records() == ()
        parent.unlink()
        parent.mkdir()
        first = session(log)
        try:
            first.charge(2)
            first.reset()
            with pytest.raises(CasError, match="another.*process"):
                deferred.reset()
        finally:
            first.close()
        # Reset can recover a poisoned session, but only after obtaining the
        # lease and loading the history written while its parent was absent.
        deferred.reset()
        assert [(r.seq, r.segment) for r in deferred.records()] == [(0, 1), (1, 2)]
        assert deferred.total_spent_seconds == 2
    finally:
        deferred.close()


def test_runtime_reports_competing_owner_without_starting_discovery(tmp_path):
    log = tmp_path / "cells.jsonl"
    first = session(log)
    try:
        runtime, detail = build_runtime(backend_name="macaulay2", command=None,
                                       limits=RunLimits(), log_path=log,
                                       on_session=lambda _: pytest.fail("discovery was reached"))
        assert runtime is None and ("another" in detail)
    finally:
        first.close()


def test_runtime_callback_failure_releases_its_newly_acquired_lease(tmp_path):
    log = tmp_path / "cells.jsonl"

    def failed_handoff(_):
        raise RuntimeError("handoff failed")

    with pytest.raises(RuntimeError, match="handoff failed"):
        build_runtime(backend_name="macaulay2", command=None, limits=RunLimits(),
                      log_path=log, on_session=failed_handoff)
    reopened = session(log)
    reopened.close()


def test_distinct_logs_have_independent_leases(tmp_path):
    first, second = session(tmp_path / "a.jsonl"), session(tmp_path / "b.jsonl")
    try:
        first.reset()
        second.reset()
        assert len(first.records()) == len(second.records()) == 1
    finally:
        first.close()
        second.close()
