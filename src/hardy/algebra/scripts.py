"""Bounded execution and descendant checks for exported CAS scripts."""
from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from hardy.algebra.contracts import CasError
from hardy.foundation.process import child_creation, child_environment, kill_group
from hardy.foundation.values import FrozenModel


class ScriptRun(FrozenModel):
    """What running an exported script produced. `returncode` is None on timeout."""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    # Retention stopped at `cas_output_bytes`, so what is above is a prefix.
    # Any verdict drawn from it can only be `unverified`: an unread tail is not
    # evidence of agreement and is not evidence of disagreement either.
    capture_truncated: bool = False
    # The script exited while something it started was still running. What such
    # a run *does* is not bounded by what was observed of it: a descendant with
    # its own stdout keeps every worker here happy, outlives the check, and was
    # free to rewrite the published file after the verdict had been drawn on
    # it. The group is killed before anything reads the artifact back, so the
    # race is closed; this says the run had one, which is not something a
    # verdict can be drawn over.
    left_processes: bool = False
    # Nobody looked, as opposed to nobody was there. On a platform with no
    # process groups Hardy can neither account for what a script started nor
    # stop it, so the artifact-rewriting race above is open for every run --
    # and a verdict of `verified` would be a claim about a file that something
    # the run started is still free to change.
    descendants_unknown: bool = False


def _drain_capped(pipe: Any, into: bytearray, cap: int, overflowed: list[bool]) -> None:
    """Read a pipe to its end while keeping at most `cap` bytes.

    Reading has to continue past the cap even though retaining does not: a
    child whose pipe stops being read blocks on its next write and never
    reaches the deadline, and the wait for it never returns.
    """
    try:
        while chunk := pipe.read1(65_536):
            room = max(0, cap - len(into))
            into.extend(chunk[:room])
            if len(chunk) > room:
                overflowed[0] = True
    except (OSError, ValueError):
        overflowed[0] = True


def _feed(process: subprocess.Popen, payload: bytes) -> None:
    """Write the script to the child's stdin, then close it.

    Closing is not optional and is not only for the payload's sake: an
    interpreter reading stdin runs until EOF, so a handle left open keeps a
    child alive that has already done everything asked of it.
    """
    stdin = process.stdin
    if stdin is None:
        return
    try:
        if payload:
            stdin.write(payload)
            stdin.flush()
    except (OSError, ValueError):
        # The child exited, or was killed at the deadline, with the payload
        # part-written. Its own status is the answer; this thread just stops.
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            stdin.close()


def run_exported_script(
    *,
    backend: Any,
    command: Path | None,
    script: Path,
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
) -> ScriptRun:
    """Execute a rendered script the way a reader would, and capture it.

    Not a kernel and not a replay: one process, the whole file, no framing.
    That is the point -- the artifact Hardy publishes is a file somebody runs,
    and until it has been run there is no evidence about what it does.

    Bounded like every other capture Hardy takes. `subprocess.run` with
    `capture_output` grows a buffer until the child stops writing, so a script
    of cells that print heavily would hold its whole transcript in Hardy's
    memory -- and a `MemoryError` raised there would take the export's
    artifacts with it, which is exactly what an export is supposed to survive.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    argv = backend.script_argv(command, script)
    # A line-oriented interpreter is fed the file on stdin, as the session
    # feeds it cells. Everything else gets an immediately closed stdin, so a
    # program that reads it sees EOF instead of blocking until the deadline.
    payload = script.read_bytes() if getattr(backend, "script_stdin", False) else b""
    cap = max(1, max_output_bytes)
    out, err = bytearray(), bytearray()
    overflowed = [False]
    left_behind = [False]
    environment = dict(getattr(backend, "environment", {}))
    # The capture is decoded as UTF-8 below, so a Python child is told to
    # write it. On Windows its stdout would otherwise be encoded with the
    # console codepage, and the markers Hardy prints around the transcript --
    # `«` and `»` -- came back one byte each and decoded to U+FFFD, so every
    # export there was reported `diverged` for not printing its own markers.
    # Not part of `backend.environment`, which the manifest records as the
    # conditions the file was checked under: this is how Hardy reads the
    # output, not a condition the program's behaviour depends on. An
    # interpreter that is not Python ignores it.
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=child_environment(environment),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **child_creation(),
        )
    except (OSError, ValueError) as error:
        raise CasError(
            f"could not run the exported script ({' '.join(argv)}): {error}"
        ) from None

    workers = [
        threading.Thread(target=_drain_capped, args=(pipe, buffer, cap, overflowed), daemon=True)
        for pipe, buffer in ((process.stdout, out), (process.stderr, err))
    ]
    stdout_worker, stderr_worker = workers
    # Feeding stdin is a third concurrent job, not a preamble to the other two.
    # `subprocess.run(input=...)` multiplexes all three inside `communicate()`;
    # writing the payload straight through on this thread instead re-created
    # the deadlock `communicate` exists to avoid. Once the payload passes the
    # OS pipe buffer and the child is not draining it fast enough, the write
    # blocks -- and it blocks *before* the deadline loop below is ever reached,
    # so no timeout applies and nothing can kill the child. That is not a
    # corner: `script_stdin` is how Singular and Macaulay2 are fed, and with a
    # 64 KiB per-cell source cap a handful of cells passes any pipe buffer.
    feeder = threading.Thread(target=_feed, args=(process, payload), daemon=True)
    workers.append(feeder)
    for worker in workers:
        worker.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Killed first, and only then joined. A writer still blocked on a full
        # pipe is released by the child's death -- the write fails and the
        # thread unwinds -- whereas closing the handle from here would queue
        # behind the very write that is stuck. Every worker is a daemon and
        # every join is bounded, so a wedged one costs a thread.
        if process.poll() is None:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
        # A reader still going is a reader with more to read. A descendant that
        # holds the pipe open and writes after the script itself has exited
        # keeps its worker alive past the join, and snapshotting the buffer
        # there and calling the capture complete let an export compare against
        # a transcript that was still arriving -- and report `verified` for it.
        if stdout_worker.is_alive() or stderr_worker.is_alive():
            overflowed[0] = True
        # Whatever the script started is stopped here, before the caller reads
        # the published file back. A descendant that redirected its own output
        # is invisible to every check above -- the workers finish, the capture
        # looks complete -- and it outlives the run: one that slept and then
        # rewrote `sys.argv[0]` changed the artifact after the manifest had
        # recorded its hash, with the verdict already `verified`. The group
        # exists to be addressed as one, and the run is over.
        #
        # Probed before it is killed, because a group with members after the
        # leader has been reaped is the evidence, and killing it destroys the
        # evidence. What this cannot see is a descendant that left the group
        # with `setsid`. Where the platform has no groups at all the sweep is
        # not attempted and `descendants_unknown` says so, rather than a
        # probe reporting "nothing left behind" because nobody looked.
        if can_sweep_descendants():
            left_behind[0] = _group_has_members(process)
            kill_group(process)
        # And each stream is closed only once its own worker has actually let
        # go of it. A drain thread blocked in `pipe.read1` -- because, say, a
        # grandchild the child spawned inherited the handle and is still
        # holding it open -- holds that `BufferedReader`'s lock for as long as
        # the read is stuck; `_feed` closes stdin itself and holds its
        # `BufferedWriter`'s lock the same way for as long as its write is
        # stuck. Closing any of those same handles from here would wait on
        # that same blocked call, with no timeout, and the bounded joins two
        # lines above buy nothing at all. Leaving a stream to its wedged
        # worker costs a file handle for as long as that thread lives; the
        # child is already dead, so its end of the pipe is gone regardless and
        # nothing is kept alive by the wait.
        closing = []
        if not stdout_worker.is_alive():
            closing.append(process.stdout)
        if not stderr_worker.is_alive():
            closing.append(process.stderr)
        if not feeder.is_alive():
            closing.append(process.stdin)
        for stream in closing:
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
    return ScriptRun(
        returncode=None if timed_out else process.returncode,
        stdout=_decode(bytes(out)),
        stderr=_decode(bytes(err)),
        timed_out=timed_out,
        capture_truncated=overflowed[0],
        left_processes=left_behind[0],
        descendants_unknown=not can_sweep_descendants(),
    )


def can_sweep_descendants() -> bool:
    """Whether this platform lets Hardy account for and stop a script's children.

    POSIX puts the tree in a process group, which can be asked about and
    signalled as one. Windows has neither here: killing a tree there needs a
    job object, which nothing sets up, so `kill_group` reaches the leader --
    which has already exited -- and there is nothing to ask about the rest.

    Not a detail of the sweep but of what a verdict may claim. Reporting
    `False` from the probe on Windows would have said "nothing was left
    behind" where the truth is "nobody looked", and the check would then call
    an artifact verified that a delayed child was still free to rewrite.
    """
    return os.name != "nt"


def _group_has_members(child: subprocess.Popen) -> bool:
    """Whether anything is still running in the child's process group.

    Signal 0 is the existence check: it delivers nothing and reports whether
    there was anyone to deliver to. The leader has been waited on by the time
    this is asked, so it is no longer a member of its own group and a positive
    answer means the script left something behind.
    """
    try:
        os.killpg(child.pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


