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
