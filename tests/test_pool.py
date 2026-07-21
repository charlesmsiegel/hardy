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
