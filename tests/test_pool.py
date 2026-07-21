import asyncio
import sys
from pathlib import Path

import pytest

from hardy.lean.pool import ReplPool, WorkerSpec
from hardy.lean.repl import LeanReplError

FAKE = [sys.executable, str(Path(__file__).parent / "fake_repl.py")]


def make_pool(**kwargs) -> ReplPool:
    kwargs.setdefault("size", 1)
    kwargs.setdefault("argv", FAKE)
    return ReplPool(**kwargs)


def test_nonpositive_size_rejected():
    # size=0 would spawn no workers and hang every check on an empty queue.
    for bad in (0, -1):
        with pytest.raises(ValueError):
            ReplPool(size=bad, argv=FAKE)


async def test_cancelled_check_recycles_worker_and_pool_recovers():
    pool = make_pool(command_timeout=30)
    await pool.start()
    task = asyncio.create_task(pool.check_proof("HANG", timeout=30))
    await asyncio.sleep(0.2)  # let the check check the worker out and block
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # size-1 pool must not deadlock: the worker was retired and refilled.
    v = await asyncio.wait_for(
        pool.check_proof("theorem t : True := trivial"), timeout=10
    )
    assert v.complete
    await pool.close()


async def test_close_terminates_in_flight_worker():
    pool = make_pool(command_timeout=30)
    await pool.start()
    task = asyncio.create_task(pool.check_proof("HANG", timeout=30))
    await asyncio.sleep(0.2)  # worker is checked out and hanging
    assert pool._live  # a live worker exists
    await pool.close()  # must terminate it, not just drain the idle queue
    v = await asyncio.wait_for(task, timeout=10)  # in-flight check must unblock
    assert not v.complete  # its worker was killed under it
    assert pool._live == set()  # nothing leaked


async def test_close_wakes_queued_waiters():
    pool = make_pool(command_timeout=30)  # size 1
    await pool.start()
    inflight = asyncio.create_task(pool.check_proof("HANG", timeout=30))
    await asyncio.sleep(0.1)  # this check owns the only worker
    queued = asyncio.create_task(pool.check_proof("theorem t : True := trivial"))
    await asyncio.sleep(0.1)  # this one is blocked in _idle.get()
    await pool.close()
    # Neither may hang: the in-flight check crashes out, the queued one errors.
    v = await asyncio.wait_for(inflight, timeout=10)
    assert not v.complete
    with pytest.raises(LeanReplError):
        await asyncio.wait_for(queued, timeout=10)


async def test_start_cancellation_retires_spawned_workers(monkeypatch):
    # Cancelling start() mid-spawn must not strand a worker that already
    # finished importing (in _live but not yet idle).
    pool = make_pool(size=2)
    real_spawn = pool._spawn
    done_one = []

    async def one_fast_one_slow():
        if not done_one:
            done_one.append(1)
            return await real_spawn()
        await asyncio.sleep(3600)  # second worker never finishes

    monkeypatch.setattr(pool, "_spawn", one_fast_one_slow)
    task = asyncio.create_task(pool.start())
    await asyncio.sleep(0.3)  # first worker registered in _live; second spawning
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool._live == set()  # the completed worker was retired, not leaked


async def test_close_during_start_retires_late_workers(monkeypatch):
    # If close() finishes while start()'s spawns are still in flight, the late
    # workers must be retired, not queued into a closed pool.
    pool = make_pool(size=1)
    real_spawn = pool._spawn

    async def slow_spawn():
        await asyncio.sleep(0.4)  # completes after close() has run
        return await real_spawn()

    monkeypatch.setattr(pool, "_spawn", slow_spawn)
    start_task = asyncio.create_task(pool.start())
    await asyncio.sleep(0.1)  # start() is mid-spawn
    await pool.close()  # shutdown wins the race
    await start_task  # start() completes and must retire, not enqueue
    assert pool._live == set()  # no worker leaked
    with pytest.raises(LeanReplError):
        await pool.check_proof("theorem t : True := trivial")


async def test_replacement_spawned_after_close_is_retired(monkeypatch):
    # If close() wins the race while _replace() is spawning, the fresh worker
    # must be retired, not queued into a dead pool.
    pool = make_pool(max_commands=1)
    await pool.start()
    real_spawn = pool._spawn
    spawned = []

    async def spawn_then_close():
        worker = await real_spawn()
        spawned.append(worker)
        pool._closed = True  # simulate close() landing during the spawn await
        return worker

    monkeypatch.setattr(pool, "_spawn", spawn_then_close)
    v = await pool.check_proof("theorem t : True := trivial")  # dirty -> _replace
    assert v.complete
    assert spawned and not spawned[0].repl.alive  # replacement retired
    assert pool._live == set()  # nothing leaked into the closed pool
    await pool.close()


async def test_fatal_response_recycles_worker(monkeypatch):
    # A worker that answers with a fatal repl-level message has lost the base
    # environment; it must be replaced, not requeued to fail every next check.
    pool = make_pool()
    await pool.start()
    spawns = 0
    real_spawn = pool._spawn

    async def counting_spawn():
        nonlocal spawns
        spawns += 1
        return await real_spawn()

    monkeypatch.setattr(pool, "_spawn", counting_spawn)
    v = await pool.check_proof("FATAL")
    assert not v.complete
    assert spawns == 1  # worker replaced after losing the base env
    v2 = await pool.check_proof("SHOW_ENV")
    assert v2.warnings[0].data == "env=0"  # replacement re-established base_env
    await pool.close()


async def test_cancel_during_replacement_poisons_pool(monkeypatch):
    # Cancelling while _replace() is spawning the new worker must not silently
    # drop the slot; later checks fail fast instead of deadlocking.
    pool = make_pool(max_commands=1)
    await pool.start()
    real_spawn = pool._spawn

    async def slow_spawn():
        await asyncio.sleep(2)  # wide window to cancel inside _replace
        return await real_spawn()

    # Patch before the check so its dirty-path replacement uses the slow spawn.
    monkeypatch.setattr(pool, "_spawn", slow_spawn)
    task = asyncio.create_task(pool.check_proof("theorem t : True := trivial"))
    await asyncio.sleep(0.3)  # check done + dirty (max_commands=1); now replacing
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(LeanReplError):
        await asyncio.wait_for(
            pool.check_proof("theorem u : True := trivial"), timeout=10
        )
    await pool.close()


async def test_cancel_during_scratch_reset_recycles_worker():
    # Cancellation can land while the reset command runs (not just the check);
    # the worker must still be retired so a size-1 pool doesn't deadlock.
    spec = WorkerSpec(
        argv=FAKE, reset_argv=[sys.executable, "-c", "import time; time.sleep(3)"]
    )
    pool = ReplPool(size=1, spec_factory=lambda: spec)
    await pool.start()
    task = asyncio.create_task(pool.check_proof("theorem t : True := trivial"))
    await asyncio.sleep(0.3)  # check answered fast; now blocked in the reset
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    v = await asyncio.wait_for(
        pool.check_proof("theorem u : True := trivial"), timeout=10
    )
    assert v.complete  # replacement worker serves the next check
    await pool.close()


async def test_check_proof_returns_verdict():
    pool = make_pool()
    await pool.start()
    v = await pool.check_proof("theorem t : True := trivial")
    assert v.complete
    await pool.close()


async def test_checks_always_fork_from_base_env():
    # The fake's first command (the imports) returns env 0, so base_env == 0.
    # Every subsequent check must send env=0 — never build on a prior check.
    pool = make_pool()
    await pool.start()
    for _ in range(3):
        v = await pool.check_proof("SHOW_ENV")
        assert v.warnings[0].data == "env=0"
    await pool.close()


async def test_timeout_yields_failure_and_pool_recovers():
    pool = make_pool(command_timeout=0.5)
    await pool.start()
    v = await pool.check_proof("HANG")
    assert not v.complete and v.failure == "timeout"
    v2 = await pool.check_proof("theorem t : True := trivial")
    assert v2.complete  # replacement worker took over
    await pool.close()


async def test_crash_yields_failure_and_pool_recovers():
    pool = make_pool()
    await pool.start()
    v = await pool.check_proof("DIE")
    assert not v.complete and v.failure == "crash"
    v2 = await pool.check_proof("theorem t : True := trivial")
    assert v2.complete
    await pool.close()


async def test_worker_recycled_after_max_commands(monkeypatch):
    pool = make_pool(max_commands=1)
    await pool.start()
    spawns = 0
    real_spawn = pool._spawn

    async def counting_spawn():
        nonlocal spawns
        spawns += 1
        return await real_spawn()

    monkeypatch.setattr(pool, "_spawn", counting_spawn)
    await pool.check_proof("theorem a : True := trivial")
    await pool.check_proof("theorem b : True := trivial")
    assert spawns >= 1  # first worker retired after hitting the command cap
    await pool.close()


async def test_failed_base_import_rejected():
    # The fake answers ERROR with an env id *plus* an error message — the
    # pool must refuse to admit that as a pristine base environment.
    pool = make_pool(imports="ERROR")
    with pytest.raises(LeanReplError):
        await pool.start()


async def test_partial_start_closes_spawned_workers(monkeypatch):
    pool = make_pool(size=2)
    real_spawn = pool._spawn
    spawned = []
    calls = 0

    async def flaky_spawn():
        nonlocal calls
        calls += 1
        if calls == 1:
            worker = await real_spawn()
            spawned.append(worker)
            return worker
        raise RuntimeError("boom")

    monkeypatch.setattr(pool, "_spawn", flaky_spawn)
    with pytest.raises(RuntimeError):
        await pool.start()
    assert spawned and not spawned[0].repl.alive  # no stranded worker


async def test_failed_scratch_reset_recycles_worker(monkeypatch):
    spec = WorkerSpec(
        argv=FAKE, reset_argv=[sys.executable, "-c", "raise SystemExit(1)"]
    )
    pool = ReplPool(size=1, spec_factory=lambda: spec)
    await pool.start()
    spawns = 0
    real_spawn = pool._spawn

    async def counting_spawn():
        nonlocal spawns
        spawns += 1
        return await real_spawn()

    monkeypatch.setattr(pool, "_spawn", counting_spawn)
    v = await pool.check_proof("theorem t : True := trivial")
    assert v.complete
    assert spawns == 1  # unwipeable scratch → worker replaced
    await pool.close()


async def test_unlaunchable_reset_recycles_worker():
    # A reset command that cannot even be spawned counts as a failed reset —
    # the worker is replaced, the verdict still comes back, nothing leaks.
    spec = WorkerSpec(argv=FAKE, reset_argv=["/nonexistent/reset-binary"])
    pool = ReplPool(size=1, spec_factory=lambda: spec)
    await pool.start()
    v = await pool.check_proof("theorem t : True := trivial")
    assert v.complete
    v2 = await pool.check_proof("theorem u : True := trivial")
    assert v2.complete  # replacement worker serves the next check
    await pool.close()


async def test_replacement_failure_poisons_pool_instead_of_deadlocking(monkeypatch):
    pool = make_pool(spawn_retries=1, spawn_retry_delay=0)
    await pool.start()

    async def failing_spawn():
        raise RuntimeError("docker under pressure")

    monkeypatch.setattr(pool, "_spawn", failing_spawn)
    with pytest.raises(LeanReplError):
        await pool.check_proof("DIE")  # crash → replacement fails after retries
    # The slot is gone; further checks must fail promptly, never hang.
    with pytest.raises(LeanReplError):
        await pool.check_proof("theorem t : True := trivial")
    await pool.close()
