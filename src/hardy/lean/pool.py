"""Warm REPL worker pool with environment isolation and recycling.

Isolation (DESIGN.md Component 1): each worker runs the configured imports
once at spawn — validated clean (no errors, no sorries, no fatal message)
before the worker is admitted, so a broken import can never masquerade as
the pristine base — and records the returned environment id as base_env.
Every check_proof forks from base_env, so declarations, axioms, and
instances introduced by one run are invisible to every other run — warm
process, clean declarations. Workers are replaced (never reused) after a
timeout, crash, or protocol error, after max_commands checks, or when
resident memory exceeds max_rss_mb.

Sandboxed workers carry two trusted side-channels in their WorkerSpec:
reset_argv kills straggler processes and wipes the container's /scratch
between checks (resetting the Lean environment id resets neither
filesystem state nor forked children — without this, one untrusted proof
could leave files or background processes behind to interfere with the
next); cleanup_argv kills the container itself on close. A worker whose
reset fails is dirty and is replaced.

Replacement failures must not shrink the pool silently: _replace retries
spawning with backoff, and if the slot is unrecoverable the queue is
poisoned so every current and future caller fails promptly with
LeanReplError instead of deadlocking on a worker that will never arrive.

Memory caveat: the RSS check reads the spawned process via psutil, which is
meaningful for directly-spawned workers. For sandboxed workers the local
process is just the docker client — there the container's own --memory
limit (hardy.sandbox) is the real cap.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import psutil
from pydantic import BaseModel

from .feedback import ProofVerdict, failure_verdict, verdict
from .messages import CommandResponse
from .repl import LeanRepl, LeanReplError, ReplDied, ReplTimeout

# Queue sentinel marking a broken pool; it wakes waiters instead of a worker.
_POISON = object()


class WorkerSpec(BaseModel):
    argv: list[str]
    cwd: Path | None = None
    # Trusted command wiping the worker's scratch between checks (sandboxed).
    reset_argv: list[str] | None = None
    # Trusted command killing the worker's container on close (sandboxed).
    cleanup_argv: list[str] | None = None


async def _run_argv_ok(argv: list[str], timeout: float = 30.0) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        # Can't even launch the trusted command (fd/process pressure, missing
        # binary): report failure so the caller recycles — never propagate.
        return False
    try:
        return await asyncio.wait_for(proc.wait(), timeout) == 0
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    except asyncio.CancelledError:
        # Don't leave the reset subprocess running when the check is cancelled.
        proc.kill()
        raise


class PoolWorker:
    def __init__(self, repl: LeanRepl, base_env: int, spec: WorkerSpec):
        self.repl = repl
        self.base_env = base_env
        self.spec = spec
        self.commands_run = 0

    async def check(self, code: str, timeout: float | None) -> CommandResponse:
        self.commands_run += 1
        return await self.repl.run_command(code, env=self.base_env, timeout=timeout)


class ReplPool:
    def __init__(
        self,
        *,
        size: int,
        argv: list[str] | None = None,
        cwd: Path | None = None,
        spec_factory: Callable[[], WorkerSpec] | None = None,
        imports: str = "import Mathlib",
        import_timeout: float = 600.0,
        command_timeout: float = 60.0,
        max_commands: int = 500,
        max_rss_mb: int = 12_000,
        spawn_retries: int = 3,
        spawn_retry_delay: float = 1.0,
    ):
        if size < 1:
            # A zero/negative pool spawns no workers, so check_proof would wait
            # forever on an empty queue (e.g. an accidental --workers 0).
            raise ValueError(f"pool size must be >= 1, got {size}")
        if spec_factory is None:
            if argv is None:
                raise ValueError("pass argv (with optional cwd) or spec_factory")
            base = WorkerSpec(argv=argv, cwd=cwd)
            spec_factory = lambda: base  # noqa: E731
        self._size = size
        self._spec_factory = spec_factory
        self._imports = imports
        self._import_timeout = import_timeout
        self._command_timeout = command_timeout
        self._max_commands = max_commands
        self._max_rss_mb = max_rss_mb
        self._spawn_retries = spawn_retries
        self._spawn_retry_delay = spawn_retry_delay
        self._broken: Exception | None = None
        self._started = False
        self._ready = False
        self._closed = False
        # Every worker that currently owns a process — idle OR checked out — so
        # close() can terminate in-flight workers, not just the idle queue.
        self._live: set[PoolWorker] = set()
        self._idle: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        # Reject anything but a pristine, open pool: a second start() would
        # spawn another _size workers (blowing past the configured limits), and
        # a start() after close() would run the expensive imports only for every
        # check to reject the closed pool. Set synchronously (before the first
        # await) so a concurrent double-start can't slip through.
        if self._closed:
            raise LeanReplError("pool is closed")
        if self._started:
            raise LeanReplError("pool already started")
        self._started = True
        try:
            results = await asyncio.gather(
                *(self._spawn() for _ in range(self._size)), return_exceptions=True
            )
        except asyncio.CancelledError:
            # Cancelled mid-startup (e.g. an outer deadline): a worker that
            # already finished importing is in _live but not yet idle, and the
            # caller never reaches its own cleanup — retire everything spawned.
            for worker in list(self._live):
                await worker.repl.close()
            self._live.clear()
            raise
        workers = [r for r in results if isinstance(r, PoolWorker)]
        failures = [r for r in results if not isinstance(r, PoolWorker)]
        if failures:
            # A partial start must not strand the workers that did spawn.
            for worker in workers:
                await self._retire(worker)
            raise failures[0]
        if self._closed:
            # close() ran (and finished) while our spawns were still in flight;
            # queueing these now-orphaned workers into a closed pool would leak
            # them. Retire instead — no check will ever use them.
            for worker in workers:
                await self._retire(worker)
            return
        for worker in workers:
            self._idle.put_nowait(worker)
        # Only now is the pool usable; check_proof gates on this so a call
        # before startup finishes (or after it failed) raises instead of
        # blocking forever on a queue that will never receive a worker.
        self._ready = True

    async def _spawn(self) -> PoolWorker:
        spec = self._spec_factory()
        repl = LeanRepl(
            spec.argv,
            cwd=spec.cwd,
            default_timeout=self._command_timeout,
            cleanup_argv=spec.cleanup_argv,
        )
        await repl.start()
        resp = await repl.run_command(self._imports, timeout=self._import_timeout)
        # The REPL can return an env id *and* error messages; an import that
        # "succeeded" with errors is not a pristine base environment.
        if not verdict(resp).complete:
            await repl.close()
            raise LeanReplError(f"worker imports failed: {resp}")
        worker = PoolWorker(repl, base_env=resp.env, spec=spec)
        self._live.add(worker)
        return worker

    def _should_recycle(self, worker: PoolWorker) -> bool:
        if worker.commands_run >= self._max_commands:
            return True
        pid = worker.repl.pid
        if pid is None:
            return True
        try:
            rss = psutil.Process(pid).memory_info().rss
        except psutil.Error:
            return True
        return rss > self._max_rss_mb * 1024 * 1024

    async def check_proof(self, code: str, timeout: float | None = None) -> ProofVerdict:
        if self._broken is not None:
            raise LeanReplError(f"pool is broken: {self._broken}")
        if self._closed:
            raise LeanReplError("pool is closed")
        if not self._ready:
            raise LeanReplError("pool has not completed startup")
        worker = await self._idle.get()
        if worker is _POISON:
            self._idle.put_nowait(_POISON)  # chain: wake the next waiter too
            raise LeanReplError(
                f"pool is broken: {self._broken}" if self._broken else "pool is closed"
            )
        try:
            resp = await worker.check(code, timeout=timeout)
            # A fatal repl-level message (e.g. "unknown environment" for our
            # recorded base_env) means the worker can no longer service the
            # pristine base — replace it rather than requeue a slot that would
            # fail every future check the same way.
            dirty = self._should_recycle(worker) or resp.message is not None
            # The scratch reset is inside the try so a cancellation landing
            # during it (not just during the check) still retires the worker.
            if not dirty and worker.spec.reset_argv is not None:
                dirty = not await _run_argv_ok(worker.spec.reset_argv)
        except ReplTimeout:
            await self._replace(worker)
            return failure_verdict("timeout")
        except (ReplDied, LeanReplError):
            await self._replace(worker)
            return failure_verdict("crash")
        except asyncio.CancelledError:
            # The caller cancelled mid-check (e.g. an outer asyncio.wait_for).
            # The worker's REPL state is now unknowable and it is still checked
            # out, so a size-1 pool would deadlock forever if we just let the
            # cancellation propagate — retire and refill it before re-raising.
            try:
                await self._replace(worker)
            except LeanReplError:
                pass  # refill failed and poisoned the pool; still re-raise cancel
            raise
        if dirty:
            await self._replace(worker)
        elif self._closed:
            # close() ran while this check was in flight; do not return the
            # worker to a dead pool (it would leak the process/container).
            await self._retire(worker)
        else:
            self._idle.put_nowait(worker)
        return verdict(resp)

    async def _retire(self, worker: PoolWorker) -> None:
        """Permanently stop a worker: drop it from the live set and close it."""
        self._live.discard(worker)
        await worker.repl.close()

    async def _replace(self, worker: PoolWorker) -> None:
        try:
            await self._retire(worker)
            if self._closed:
                return  # a closing pool must not spawn fresh workers
            last_error: Exception | None = None
            for attempt in range(self._spawn_retries):
                try:
                    new = await self._spawn()
                except Exception as exc:  # transient docker/toolchain pressure
                    last_error = exc
                    await asyncio.sleep(self._spawn_retry_delay * (attempt + 1))
                    continue
                if self._closed:
                    # close() took its _live snapshot while we were spawning;
                    # this fresh worker would leak if queued into a dead pool.
                    await self._retire(new)
                    return
                self._idle.put_nowait(new)
                return
            # Unrecoverable: poison the queue so callers fail promptly rather
            # than deadlocking on a slot that will never be refilled.
            self._broken = last_error
            self._idle.put_nowait(_POISON)
            raise LeanReplError(f"could not replace worker: {last_error}")
        except asyncio.CancelledError:
            # Cancelled mid-replacement (retire, spawn, or backoff). The old
            # worker is already gone, so the slot would silently vanish and
            # deadlock later waiters — poison it so they fail fast instead,
            # then propagate the cancellation.
            if not self._closed:
                if self._broken is None:
                    self._broken = LeanReplError("worker replacement cancelled")
                self._idle.put_nowait(_POISON)
            raise

    async def close(self) -> None:
        # Mark closed first so any in-flight check retires its worker on return
        # instead of re-queueing it into a pool that is shutting down.
        self._closed = True
        while not self._idle.empty():
            worker = self._idle.get_nowait()
            if worker is not _POISON:
                await self._retire(worker)
        # Terminate every still-live worker — including ones checked out by an
        # in-flight check_proof — so no REPL process or sandbox container leaks.
        for worker in list(self._live):
            await worker.repl.close()
        self._live.clear()
        # Wake any callers already blocked in _idle.get(): no worker will ever
        # be returned now, so hand them a poison sentinel that chains to the
        # next waiter (each raises LeanReplError instead of hanging forever).
        self._idle.put_nowait(_POISON)
