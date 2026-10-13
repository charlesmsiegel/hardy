from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from hardy.foundation.values import ToolResult
from hardy.workflows.interactive.jobs import ComputationJobs, Detached, _Job


@pytest.mark.parametrize('fails', [False, True])
def test_attach_outcome_is_a_barrier_even_after_old_timeout(monkeypatch, fails):
    reached = threading.Event()
    finish_tool = threading.Event()
    attached = threading.Event()
    outcome = threading.Event()
    original_event = threading.Event

    class AttachBarrier:
        def set(self):
            attached.set()

        def wait(self, timeout=None):
            reached.set()
            # Deterministically expire the former five-second wait.
            if timeout is not None:
                self.timed_out = True
                outcome.set()
                return False
            outcome.set()
            return attached.wait(2)

    jobs_created = []
    original_init = _Job.__init__

    def initialize(job, *args, **kwargs):
        original_init(job, *args, **kwargs)
        job.attached = AttachBarrier()
        jobs_created.append(job)

    monkeypatch.setattr(_Job, '__init__', initialize)
    recorded = []
    finished = []
    gate = threading.Lock()
    gate.acquire()

    class Delegations:
        def attach_computation(self, **kwargs):
            finish_tool.set()
            assert reached.wait(2)
            assert outcome.wait(2)
            # A timed wait has escaped the barrier: wait for its old
            # bookkeeping to finish before resolving the attach outcome.
            if getattr(jobs_created[0].attached, 'timed_out', False):
                assert jobs_created[0].done.wait(2)
            if fails:
                raise RuntimeError('attach failed')
            return SimpleNamespace(id='d1')

        def finish_computation(self, job_id, **kwargs):
            finished.append(job_id)

    jobs = ComputationJobs(detach_after=.001, gate=gate, record=lambda event: recorded.append(event) or 0,
                           recorded=lambda: iter(recorded), delegations=Delegations(), notify=lambda _: None,
                           tally=lambda *args: None, cas_session=lambda: None)
    completed = original_event()
    jobs.on_finished = completed.set

    def tool(*args):
        assert finish_tool.wait(2)
        return ToolResult(True, 'ok')

    if fails:
        with pytest.raises(RuntimeError, match='attach failed'):
            jobs.call('cas_run', {}, tool, 'call')
        assert gate.locked(), 'orphan completion released the dispatcher-owned gate'
        gate.release()
        assert not recorded and not finished
    else:
        assert isinstance(jobs.call('cas_run', {}, tool, 'call'), Detached)
        assert completed.wait(2)
        assert recorded[0]['job_id'] == 'd1'
        assert finished == ['d1']
    assert jobs.running() == ()
    assert not jobs.holds_cas()
    assert gate.acquire(blocking=False)
    gate.release()


def test_notice_failure_after_attach_keeps_detached_gate_ownership():
    finish_tool = threading.Event()
    finished = threading.Event()
    gate = threading.Lock()
    gate.acquire()
    records = []
    delegations = SimpleNamespace(attach_computation=lambda **kwargs: SimpleNamespace(id='d1'),
                                  finish_computation=lambda *args, **kwargs: None)

    def notify(_):
        raise RuntimeError('notice sink unavailable')

    def tool(*args):
        assert finish_tool.wait(2)
        return ToolResult(True, 'ok')

    jobs = ComputationJobs(detach_after=.001, gate=gate, record=lambda event: records.append(event) or 0,
                           recorded=lambda: iter(records), delegations=delegations, notify=notify,
                           tally=lambda *args: None, cas_session=lambda: None)
    jobs.on_finished = finished.set
    try:
        assert isinstance(jobs.call('check_lean', {}, tool, 'call'), Detached)
        assert gate.locked()
    finally:
        finish_tool.set()
        assert finished.wait(2)
    assert not gate.locked()
    assert jobs.running() == ()
