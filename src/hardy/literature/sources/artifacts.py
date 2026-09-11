"""The managed, immutable, content-addressed store of source bytes.

Hardy indexes only bytes it owns. An import reads a bounded input, digests it,
stages a copy beside the target, verifies the copy, and admits it with one
rename, so a crash leaves either a whole artifact or none. The artifact's name
is its SHA-256: identical bytes from two paths are one artifact with two
provenance records, and a file whose bytes changed is a second artifact. The
original path or URL is provenance, never a backing store, and nothing here
reads it again after admission.

A missing artifact is reported as `unavailable` with its identity intact, and
a stored file whose bytes no longer match its name is `corrupt`; neither is
"there was never such a source".
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hardy.foundation.files import LayoutError, WriteGuard, guard_for, read_bytes, read_text

from .contracts import (
    AccessPolicy,
    ArtifactAvailability,
    ImportProvenance,
    SourceArtifact,
    SourceFormat,
)

CONTENT = "content"
RECORD = "artifact.json"
PROVENANCE_DIR = "provenance"
ADAPTER = "hardy.literature.sources.artifacts"
ADAPTER_VERSION = "1"
READ_CHUNK = 1 << 20


class ArtifactError(ValueError):
    """An artifact could not be read as the exact bytes its identity names."""


class ImportRefused(ArtifactError):
    """An import request Hardy will not perform."""


@dataclass(frozen=True)
class ImportRequest:
    data: bytes | None = None
    path: Path | None = None
    original_name: str | None = None
    source_url: str | None = None
    provider: str | None = None
    access: AccessPolicy = AccessPolicy.PRIVATE_LOCAL
    user_metadata: tuple[tuple[str, str], ...] = ()
    max_bytes: int = 512 * 1024 * 1024


@dataclass(frozen=True)
class ImportOutcome:
    artifact: SourceArtifact
    provenance: ImportProvenance
    reused: bool


def detect_format(data: bytes, *, name: str | None = None) -> tuple[SourceFormat, str]:
    """Sniff the bytes first, then the name; an unknown format is still admissible."""
    head = data[:2048]
    lowered = (name or "").lower()
    if head.startswith(b"%PDF-"):
        return SourceFormat.PDF, "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        if b"application/epub+zip" in head or lowered.endswith(".epub"):
            return SourceFormat.EPUB, "application/epub+zip"
        return SourceFormat.UNKNOWN, "application/zip"
    if head.startswith(b"\x1f\x8b"):
        return SourceFormat.TEX_TREE, "application/gzip"
    if len(data) > 262 and data[257:262] == b"ustar":
        return SourceFormat.TEX_TREE, "application/x-tar"
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        return SourceFormat.HTML, "text/html"
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if lowered.endswith((".tex", ".ltx", ".sty")):
                return SourceFormat.TEX_TREE, "application/x-tex"
            if lowered.endswith((".html", ".htm")):
                return SourceFormat.HTML, "text/html"
            return SourceFormat.TEXT, "text/plain"
    return SourceFormat.UNKNOWN, "application/octet-stream"


def _stamp(when: float) -> str:
    return datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")


class ArtifactStore:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self._clock = clock

    # --- paths ------------------------------------------------------------

    def _dir(self, sha256: str) -> Path:
        return self.root / sha256

    def holds(self, sha256: str) -> bool:
        return (self._dir(sha256) / RECORD).is_file() and (self._dir(sha256) / CONTENT).is_file()

    def stored(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(
            child.name for child in self.root.iterdir()
            if not child.is_symlink() and len(child.name) == 64 and (child / RECORD).is_file()
        ))

    # --- reading ------------------------------------------------------------

    def record(self, sha256: str) -> SourceArtifact:
        if not self.holds(sha256):
            raise ArtifactError(f"artifact {sha256} is unavailable in this library")
        try:
            record = SourceArtifact.model_validate_json(read_text(self.root, f"{sha256}/{RECORD}"))
        except (OSError, ValueError, LayoutError) as error:
            raise ArtifactError(f"artifact {sha256} record is corrupt: {error}") from error
        if record.sha256 != sha256:
            raise ArtifactError(f"artifact {sha256} record names a different digest")
        return record

    def read(self, sha256: str) -> bytes:
        availability = self.availability(sha256)
        if availability.status != "available":
            raise ArtifactError(f"artifact {sha256} is {availability.status}: {availability.detail}")
        return read_bytes(self.root, f"{sha256}/{CONTENT}")

    def availability(self, sha256: str) -> ArtifactAvailability:
        if not self.holds(sha256):
            return ArtifactAvailability(sha256=sha256, status="unavailable", detail="no managed copy of these bytes on this machine")
        try:
            content = read_bytes(self.root, f"{sha256}/{CONTENT}")
        except (OSError, LayoutError) as error:
            return ArtifactAvailability(sha256=sha256, status="corrupt", detail=str(error))
        actual = hashlib.sha256(content).hexdigest()
        if actual != sha256:
            return ArtifactAvailability(sha256=sha256, status="corrupt", detail=f"stored bytes digest to {actual}")
        return ArtifactAvailability(sha256=sha256, status="available")

    def provenance(self, sha256: str) -> tuple[ImportProvenance, ...]:
        directory = self._dir(sha256) / PROVENANCE_DIR
        if not directory.is_dir() or directory.is_symlink():
            return ()
        records = []
        for child in sorted(directory.iterdir()):
            if child.suffix != ".json" or child.is_symlink():
                continue
            records.append(ImportProvenance.model_validate_json(read_text(self.root, f"{sha256}/{PROVENANCE_DIR}/{child.name}")))
        return tuple(records)

    # --- importing ----------------------------------------------------------

    def import_bytes(self, request: ImportRequest) -> ImportOutcome:
        data = self._bounded_input(request)
        sha256 = hashlib.sha256(data).hexdigest()
        format_, media_type = detect_format(data, name=request.original_name or (request.path.name if request.path else None))
        now = self._clock()
        reused = self.holds(sha256)
        if not reused:
            artifact = SourceArtifact(
                sha256=sha256, byte_size=len(data), format=format_, media_type=media_type,
                access=request.access, admitted_at=_stamp(now),
            )
            reused = not self._admit(artifact, data)
        artifact = self.record(sha256)
        provenance = ImportProvenance(
            id=f"import-{secrets.token_hex(8)}", artifact_sha256=sha256,
            original_name=request.original_name or (request.path.name if request.path else None),
            original_path=str(request.path) if request.path else None,
            source_url=request.source_url, provider=request.provider, imported_at=_stamp(now),
            adapter=ADAPTER, adapter_version=ADAPTER_VERSION, detected_media_type=media_type,
            user_metadata=request.user_metadata,
        )
        guard, name = guard_for(self.root, f"{sha256}/{PROVENANCE_DIR}/{provenance.id}.json", create=True)
        guard.write_json(name, provenance.model_dump(mode="json"))
        return ImportOutcome(artifact=artifact, provenance=provenance, reused=reused)

    def _bounded_input(self, request: ImportRequest) -> bytes:
        if (request.data is None) == (request.path is None):
            raise ImportRefused("an import names exactly one of bytes or a path")
        if request.data is not None:
            if len(request.data) > request.max_bytes:
                raise ImportRefused(f"input of {len(request.data)} bytes exceeds the {request.max_bytes} byte bound")
            if not request.data:
                raise ImportRefused("an empty input is not a source")
            return request.data
        path = Path(request.path)  # type: ignore[arg-type]
        try:
            size = path.stat().st_size
        except OSError as error:
            raise ImportRefused(f"{path} could not be read: {error}") from error
        if size > request.max_bytes:
            raise ImportRefused(f"{path} is {size} bytes, over the {request.max_bytes} byte bound")
        chunks: list[bytes] = []
        total = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > request.max_bytes:
                    raise ImportRefused(f"{path} grew past the {request.max_bytes} byte bound while being read")
                chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise ImportRefused(f"{path} is empty")
        return data

    def _admit(self, artifact: SourceArtifact, data: bytes) -> bool:
        """Stage beside the target and rename; True when this call admitted it."""
        root = WriteGuard(self.root, create=True)
        target = root.reserve(artifact.sha256)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root.directory))
        try:
            (staging / CONTENT).write_bytes(data)
            copied = hashlib.sha256((staging / CONTENT).read_bytes()).hexdigest()
            if copied != artifact.sha256:
                raise ArtifactError("staged copy does not digest to the artifact identity")
            (staging / RECORD).write_bytes((artifact.model_dump_json(indent=2) + "\n").encode("utf-8"))
            (staging / PROVENANCE_DIR).mkdir()
            try:
                os.replace(staging, target)
            except OSError:
                if self.holds(artifact.sha256):
                    return False
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return True
