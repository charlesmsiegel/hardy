"""Crash-resistant run artifacts and trajectories.

Every artifact is written whole or not at all, and every trajectory event is
flushed before the next one is accepted, so a run interrupted mid-proof still
reads back as a consistent prefix rather than a truncated record.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from .domain import FrozenModel, RunManifest, RunPhase

SECRET_KEY = re.compile(
    r"^(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)$",
    re.IGNORECASE,
)


class ArtifactIdentity(FrozenModel):
    relative_path: str
    byte_count: int
    sha256: str


class TrajectoryEvent(FrozenModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    sequence: int
    timestamp: datetime
    phase: RunPhase
    kind: str
    payload: dict[str, Any]


def atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


#: What the platform says when the lock is simply somebody else's. Anything
#: else out of the locking call is a fault rather than a queue.
BUSY = frozenset(
    code
    for code in (
        getattr(errno, "EWOULDBLOCK", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EACCES", None),
        getattr(errno, "EDEADLK", None),
    )
    if code is not None
)


class LockTimeout(RuntimeError):
    """A lock somebody else was holding for longer than the caller would wait."""


class LockUnavailable(LockTimeout):
    """A lock that could not be taken for a reason that is not contention.

    A read-only checkout, a directory the user cannot write, a symlink where
    the lock file should be, no descriptors left, a filesystem that does not
    lock. A subclass so that a caller which only wants to know it did not get
    the lock still catches it, and one that reports *why* can tell this from
    "somebody else is writing" -- which is the difference between a fault a
    person can fix and a wait that will end on its own.
    """


class FileLock:
    """A cross-process mutex the operating system holds, not a heuristic.

    Two Hardy processes on one machine share a paper library and a problem
    directory, and both of the promises made about those -- "one request every
    three seconds from this machine", "citing two papers at once loses
    neither" -- are read-modify-write sequences. An atomic write makes each
    step whole; it does nothing about two processes interleaving their steps,
    and the second promise is precisely about that.

    The lock is `flock` on POSIX and a byte-range lock on Windows, taken on an
    open descriptor and released by the kernel when the descriptor closes --
    including when it closes because the process died. That last part is the
    reason for the mechanism.

    This started as `O_CREAT | O_EXCL` on a file, which is the same code
    everywhere and needs no platform branch, and the whole of what went wrong
    followed from one gap in it: a file outlives the process that made it, so
    an abandoned lock had to be recognised by its age and taken. Every attempt
    to make that sound failed on the same seam, because "remove the lock I
    judged stale" cannot be written atomically against a *pathname*:

    - two processes meeting one abandoned lock both broke it, the second
      removing the first's fresh replacement; serialising the break with a
      second lock file narrowed that and did not close it;
    - a holder slower than the staleness window had its lock broken, then
      released by name and deleted its successor's lock;
    - recognising an instance by device and inode does not work at all, since
      a filesystem hands the number of a file just unlinked straight to the
      next one created;
    - recognising it by a token written into the file leaves the read and the
      unlink as two calls, and POSIX has no unlink-by-content;
    - and the age itself was a wall-clock subtraction, so a clock stepped
      backwards or a VM restored with a lock dated in the future made an
      abandoned lock immortal and every citation fail until the clock caught
      up.

    None of those exist here. There is no staleness window, nothing is ever
    broken, no lock file is ever unlinked, and no clock is read. A dead
    holder's lock is free the moment it dies.

    The file is still created -- it is the rendezvous point the descriptor is
    opened on -- and it is deliberately left behind, since an empty file at a
    known path is not a claim on anything. `O_NOFOLLOW` still refuses a
    symlink at the final component, so a repository shipping one as a link
    cannot make a lock, or anything written under its cover, land elsewhere.

    `required` says what a caller loses if the lock cannot be had. The
    bibliography loses a citation, so it raises; the arXiv throttle loses only
    politeness, and refusing to fetch because another process is slow would
    trade a real failure for an imagined one.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 30.0,
        required: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.required = required
        self._sleep = sleep
        self.held = False
        self._handle: int | None = None

    def __enter__(self) -> FileLock:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                taken = self._claim()
            except OSError as error:
                # Not contention, so not waited out. A caller that can do
                # without the lock does; one that cannot is told what actually
                # went wrong, rather than that somebody else is writing.
                self._close()
                if self.required:
                    raise LockUnavailable(f"{self.path} could not be locked: {error}") from error
                return self
            if taken:
                self.held = True
                return self
            if time.monotonic() >= deadline:
                self._close()
                if self.required:
                    raise LockTimeout(
                        f"{self.path} was held by another process for more than "
                        f"{self.timeout:g}s; refusing to write without it"
                    )
                return self
            self._sleep(0.05)

    def __exit__(self, *_: object) -> None:
        """Release by closing the descriptor, which is all a release is now.

        Nothing is unlinked. A lock file left on disk is inert -- the next
        caller opens the same path and takes the lock on it -- so there is no
        successor's lock to delete by mistake, and no abandoned file for a
        fresh checkout to wait out.
        """
        if self.held and self._handle is not None:
            _unlock(self._handle)
            self.held = False
        self._close()

    def _close(self) -> None:
        if self._handle is not None:
            os.close(self._handle)
            self._handle = None

    def _claim(self) -> bool:
        """Whether the lock was taken. `False` means somebody else holds it.

        Only that. A lock file that cannot be opened at all -- a read-only
        checkout, a directory the user cannot write, no descriptors left, a
        filesystem with no locking -- is not contention, and reporting it as
        contention is how a citation came to wait out its whole timeout and
        then blame a session that does not exist. Those raise, and
        `__enter__` decides what the caller loses.
        """
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            # `O_NOFOLLOW` fails on a symlink rather than opening what it
            # points at, so a lock file shipped in a repository as a link is
            # never written through and never taken. That refusal reaches the
            # caller as itself now, rather than as a wait for a holder that
            # was never there.
            self._handle = os.open(self.path, flags, 0o600)
        try:
            _lock(self._handle)
        except OSError as error:
            if getattr(error, "errno", None) not in BUSY:
                raise
            return False
        # Whose it is, for a person looking at a stuck workspace. Written
        # under the lock, so it is never a partial read, and it is not what
        # the lock is enforced by -- nothing here is trusted to be accurate.
        try:
            os.ftruncate(self._handle, 0)
            os.write(self._handle, str(os.getpid()).encode())
        except OSError:
            pass
        return True


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _lock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)

    def _unlock(handle: int) -> None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: int) -> None:
        # `flock` and not `fcntl.lockf`: a POSIX record lock belongs to the
        # process, so two sessions inside one process -- which is what a test
        # is, and what a threaded caller would be -- would not exclude each
        # other at all.
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: int) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


class RunStore:
    def __init__(self, path: Path, run_id: UUID) -> None:
        self.path = path
        self.run_id = run_id
        self.trajectory_path = path / "trajectory.jsonl"
        self._next_sequence = 0
        self._trajectory_lock = threading.Lock()

    @classmethod
    def create(
        cls,
        root: Path,
        problem_slug: str,
        *,
        now: datetime,
        run_id: UUID,
    ) -> RunStore:
        timestamp = now.astimezone().strftime("%Y%m%dT%H%M%S%z")
        path = root / f"{timestamp}-{problem_slug}-{run_id.hex[:8]}"
        path.mkdir(parents=True, exist_ok=False)
        return cls(path, run_id)

    @classmethod
    def open(cls, path: Path, *, run_id: UUID) -> RunStore:
        store = cls(path, run_id)
        if not store.trajectory_path.exists():
            return store
        events = [
            TrajectoryEvent.model_validate_json(line)
            for line in store.trajectory_path.read_text(encoding="utf-8").splitlines()
        ]
        for expected, event in enumerate(events):
            if event.run_id != run_id or event.sequence != expected:
                raise ValueError("trajectory run identity or sequence is invalid")
        store._next_sequence = len(events)
        return store

    def write_text(self, relative_path: PurePosixPath, text: str) -> ArtifactIdentity:
        return self.write_bytes(relative_path, text.encode("utf-8"))

    def write_bytes(self, relative_path: PurePosixPath, content: bytes) -> ArtifactIdentity:
        target = self._artifact_target(relative_path)
        atomic_write_bytes(target, content)
        return ArtifactIdentity(
            relative_path=relative_path.as_posix(),
            byte_count=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def write_json(
        self,
        relative_path: PurePosixPath,
        value: BaseModel | dict[str, Any],
    ) -> ArtifactIdentity:
        serializable = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        text = json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return self.write_text(relative_path, text)

    def finalize(self, manifest: RunManifest) -> ArtifactIdentity:
        return self.write_json(PurePosixPath("manifest.json"), manifest)

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        phase: RunPhase,
    ) -> TrajectoryEvent:
        with self._trajectory_lock:
            event = TrajectoryEvent(
                run_id=self.run_id,
                sequence=self._next_sequence,
                timestamp=datetime.now(UTC),
                phase=phase,
                kind=kind,
                payload=_redact(payload),
            )
            serialized = event.model_dump_json() + "\n"
            with self.trajectory_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            self._next_sequence += 1
            return event

    def _artifact_target(self, relative_path: PurePosixPath) -> Path:
        parts = relative_path.parts
        invalid = (
            relative_path.is_absolute()
            or not parts
            or any(part in {".", ".."} or "\\" in part or ":" in part for part in parts)
        )
        if invalid:
            raise ValueError(f"invalid relative artifact path: {relative_path}")
        return self.path.joinpath(*parts)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY.match(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    json.dumps(value)
    return value
