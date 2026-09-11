"""Physical worker slots and cooperative cancellation, independent of what a worker does."""
import threading
import time

import pytest

from hardy.agents.executor import CancelToken, LocalExecutor, WorkerCancelled, WorkerJob


def _gate():
    return threading.Event(), threading.Event()


def test_jobs_beyond_the_slot_count_wait_for_a_slot():
    executor = LocalExecutor(1)
    started, release = _gate()
    order = []

    def first(token):
        order.append("first-start")
        started.set()
        assert release.wait(5)
        order.append("first-end")
        return 1

    def second(token):
        order.append("second-start")
        return 2

    a = executor.submit(WorkerJob("a", first))
    b = executor.submit(WorkerJob("b", second))
    assert started.wait(5)
    time.sleep(0.05)
    assert order == ["first-start"]
    assert executor.active() == 1 and not b.done()
    release.set()
    assert a.result(5) == 1 and b.result(5) == 2
    assert order == ["first-start", "first-end", "second-start"]
    executor.shutdown(wait=True)


def test_cancelling_a_queued_job_means_its_body_never_runs():
    executor = LocalExecutor(1)
    started, release = _gate()
    ran = []

    def hold(token):
        started.set()
        assert release.wait(5)

    executor.submit(WorkerJob("hold", hold))
    queued = executor.submit(WorkerJob("queued", lambda token: ran.append("ran")))
    assert started.wait(5)
    queued.cancel()
    release.set()
    with pytest.raises(WorkerCancelled):
        queued.result(5)
    assert ran == [] and queued.token.cancelled
    executor.shutdown(wait=True)


def test_cancelling_a_running_job_fires_callbacks_and_the_body_can_stop():
    executor = LocalExecutor(2)
    started, _ = _gate()
    observed = []

    def run(token):
        token.on_cancel(lambda: observed.append("callback"))
        started.set()
        while True:
            token.check()
            time.sleep(0.01)

    handle = executor.submit(WorkerJob("loop", run))
    assert started.wait(5)
    handle.cancel()
    with pytest.raises(WorkerCancelled):
        handle.result(5)
    assert observed == ["callback"]
    handle.cancel()                       # a second cancel neither raises nor refires
    assert observed == ["callback"]
    executor.shutdown(wait=True)


def test_done_callbacks_run_once_and_after_the_result_is_available():
    executor = LocalExecutor(1)
    seen = []
    handle = executor.submit(WorkerJob("quick", lambda token: "value"))
    handle.result(5)
    handle.add_done_callback(lambda h: seen.append(h.result(0)))
    executor.shutdown(wait=True)
    assert seen == ["value"]


def test_on_cancel_registered_after_cancellation_runs_immediately():
    token = CancelToken()
    token.cancel()
    calls = []
    token.on_cancel(lambda: calls.append(1))
    assert calls == [1]
    with pytest.raises(WorkerCancelled):
        token.check()


@pytest.mark.parametrize("slots", [1, 3, 40])
def test_slot_count_is_whatever_was_asked_for(slots):
    executor = LocalExecutor(slots)
    assert executor.slots == slots
    executor.shutdown(wait=True)


def test_a_failing_job_surfaces_its_error():
    executor = LocalExecutor(1)

    def boom(token):
        raise RuntimeError("boom")

    handle = executor.submit(WorkerJob("boom", boom))
    with pytest.raises(RuntimeError, match="boom"):
        handle.result(5)
    assert handle.done()
    executor.shutdown(wait=True)
