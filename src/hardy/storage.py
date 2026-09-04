"""Crash-resistant run artifacts and trajectories.

Every artifact is written whole or not at all, and every trajectory event is
flushed before the next one is accepted, so a run interrupted mid-proof still
reads back as a consistent prefix rather than a truncated record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

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


class LockTimeout(RuntimeError):
    """A lock somebody else was holding for longer than the caller would wait."""


class FileLock:
    """One exclusively-created file, standing in for a cross-process mutex.

    Two Hardy processes on one machine share a paper library and a problem
    directory, and both of the promises made about those -- "one request every
    three seconds from this machine", "citing two papers at once loses
    neither" -- are read-modify-write sequences. An atomic write makes each
    step whole; it does nothing about two processes interleaving their steps,
    and the second promise is precisely about that.

    `O_CREAT | O_EXCL` is the whole mechanism, and it is chosen over `fcntl` or
    `msvcrt` because it is the same code on every platform Hardy installs on.
    Its one weakness is a holder that died: the file outlives the process. So
    a lock older than `stale_after` is taken from it rather than waited on
    forever, and `stale_after` must be comfortably longer than any hold.

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
        stale_after: float = 300.0,
        required: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.required = required
        self._sleep = sleep
        self.held = False
        #: What this lock wrote into the file it created. An inode number is
        #: not an identity -- a filesystem hands the number of a file just
        #: unlinked straight to the next one created -- so what it removes is
        #: recognised by content it alone could have written.
        self._token: bytes | None = None

    def __enter__(self) -> FileLock:
        deadline = time.monotonic() + self.timeout
        while True:
            if self._claim():
                self.held = True
                return self
            if time.monotonic() >= deadline:
                if self.required:
                    raise LockTimeout(
                        f"{self.path} was held by another process for more than "
                        f"{self.timeout:g}s; refusing to write without it"
                    )
                return self
            self._sleep(0.05)

    def __exit__(self, *_: object) -> None:
        """Release, and only what this lock actually took.

        Not an unconditional unlink. A holder slower than `stale_after` has
        its lock broken and a new holder claims a fresh one at the same path;
        the slow holder then finishes and, removing the path by name, deletes
        the *new* holder's lock -- and a third process claims immediately,
        putting two writers in the critical section. Deleting by identity
        instead makes a broken lock the breaker's problem alone.
        """
        if self.held:
            self._unlink_instance(self.path, self._token)
            self.held = False
            self._token = None

    def _claim(self) -> bool:
        """Take the lock, or say why not. A stale one is taken from its holder."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            # `O_EXCL` refuses an existing path of any kind, a symlink
            # included, so a lock file shipped in a repository as a link
            # cannot be written through -- it is simply never claimed, and the
            # staleness check below removes it.
            handle = os.open(self.path, flags, 0o600)
        except FileExistsError:
            self._break_if_stale()
            return False
        except OSError:
            return False
        token = f"{os.getpid()} {uuid4().hex}".encode()
        try:
            os.write(handle, token)
        except OSError:
            # The exclusive create succeeded and writing into it did not -- a
            # quota or a full disk, reached while allocating the lock's first
            # block. Leaving the file behind would block every caller for the
            # whole staleness window over a lock nobody holds.
            os.close(handle)
            self.path.unlink(missing_ok=True)
            return False
        os.close(handle)
        self._token = token
        return True

    @staticmethod
    def _instance(path: Path) -> tuple[bytes, float] | None:
        """What the file at `path` says it is, and how old it is -- or None.

        Both out of one descriptor, so the age judged and the file removed are
        the same file rather than the same name read twice.
        """
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            handle = os.open(path, flags)
        except OSError:
            return None
        try:
            return os.read(handle, 256), os.fstat(handle).st_mtime
        except OSError:
            return None
        finally:
            os.close(handle)

    @classmethod
    def _unlink_instance(cls, path: Path, token: bytes | None) -> None:
        """Remove `path`, but only while it still holds `token`.

        A lock file is a name, and every process racing for one races for the
        same name; what a holder or a breaker may remove is the particular
        lock it saw, and only a token written into the file can say whether
        that is what is there now. Reading and unlinking are two calls and no
        POSIX primitive unlinks by content, so a lock replaced between them is
        still removed; what this closes is the far wider window in which a
        decision taken earlier is carried out later against whatever has since
        answered to the name.
        """
        if token is None:
            return
        found = cls._instance(path)
        if found is None or found[0] != token:
            return
        path.unlink(missing_ok=True)

    def _break_if_stale(self) -> None:
        """Take a lock its holder cannot still be using, if anyone may.

        Breaking is itself serialised, by a second `O_EXCL` file, and that is
        not fussiness. Two processes meeting the same abandoned lock both see
        it as stale; the first unlinks it and claims a fresh one, and the
        second's unlink -- decided on the old file and executed a moment later
        -- then removes the NEW holder's lock. Both are inside the critical
        section, which is exactly the concurrency this class exists to
        prevent, arrived at through the recovery path.

        With the mutex, only one process may break a given lock at a time, and
        it re-checks staleness while holding it. A process that loses the race
        simply returns and retries the lock itself on the next poll.

        The mutex is held for one `stat` and one `unlink`, so a `.break` file
        that is itself old belongs to a process that died mid-recovery: it is
        removed and the next poll tries again. That is one level of recovery,
        not a recursion.
        """
        if not self._stale(self.path):
            return
        breaking = self.path.with_name(self.path.name + ".break")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            os.close(os.open(breaking, flags, 0o600))
        except FileExistsError:
            if self._stale(breaking):
                breaking.unlink(missing_ok=True)
            return
        except OSError:
            return
        try:
            # Re-checked under the mutex: the holder may have finished and a
            # new one taken the lock while this process was getting here, and
            # that lock is not stale and not this one's to remove. The file
            # judged stale here is also the file removed -- one stat decides
            # both, so a lock replaced between them keeps its own age rather
            # than inheriting the verdict passed on its predecessor.
            found = self._instance(self.path)
            if found is not None and time.time() - found[1] > self.stale_after:
                self._unlink_instance(self.path, found[0])
        finally:
            breaking.unlink(missing_ok=True)

    def _stale(self, path: Path) -> bool:
        try:
            return time.time() - path.stat().st_mtime > self.stale_after
        except OSError:
            return False


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
