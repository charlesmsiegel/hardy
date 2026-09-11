"""Write-once storage for derived representations and their mappings.

A representation is admitted with its payload files and never rewritten; an
improved extraction is a new representation with a new id, and the old one
stays readable for every span that named it. Payload reads re-hash the bytes
against the record's output digest, so a representation is served only as
the exact reading it was recorded as. Page labels come from a page manifest
representation and are answered separately from page indices.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from hardy.foundation.files import LayoutError, WriteGuard, read_bytes, read_text
from hardy.foundation.values import json_digest

from .contracts import DerivedRepresentation, RepresentationKind, RepresentationMapping

RECORD = "representation.json"
MAPPINGS = "mappings"
TEXT_PAYLOAD = "text.txt"
PAGES_PAYLOAD = "pages.json"


class RepresentationError(ValueError):
    """A representation could not be admitted or read as recorded."""


def payload_digest(payloads: Mapping[str, bytes]) -> str:
    """The output digest of a representation: every payload's bytes, by name."""
    return json_digest({name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()})


class RepresentationStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, artifact_sha256: str, id: str) -> Path:
        return self.root / artifact_sha256 / id

    def admit(
        self, record: DerivedRepresentation, payloads: Mapping[str, bytes],
        mappings: tuple[RepresentationMapping, ...] = (),
    ) -> DerivedRepresentation:
        if set(record.payload_files) != set(payloads):
            raise RepresentationError("the record's payload_files must name exactly the payloads supplied")
        if payload_digest(payloads) != record.output_sha256:
            raise RepresentationError("payloads do not digest to the record's output_sha256")
        for name in payloads:
            if "/" in name or "\\" in name or name in {RECORD, MAPPINGS} or name.startswith("."):
                raise RepresentationError(f"payload name {name!r} is not a plain file name")
        existing = self._dir(record.artifact_sha256, record.id)
        if (existing / RECORD).is_file():
            held = self.get(record.artifact_sha256, record.id)
            if held.output_sha256 != record.output_sha256:
                raise RepresentationError(f"representation {record.id} already exists with different output")
            self.add_mappings(record.artifact_sha256, mappings)
            return held
        parent = WriteGuard(self.root / record.artifact_sha256, create=True)
        target = parent.reserve(record.id)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent.directory))
        try:
            for name, data in payloads.items():
                (staging / name).write_bytes(data)
            (staging / RECORD).write_bytes((record.model_dump_json(indent=2) + "\n").encode("utf-8"))
            (staging / MAPPINGS).mkdir()
            try:
                os.replace(staging, target)
            except OSError:
                if not (target / RECORD).is_file():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self.add_mappings(record.artifact_sha256, mappings)
        return self.get(record.artifact_sha256, record.id)

    def add_mappings(self, artifact_sha256: str, mappings: tuple[RepresentationMapping, ...]) -> None:
        """Record mappings beside their left representation; a repeated identical mapping is a no-op."""
        for mapping in mappings:
            if mapping.artifact_sha256 != artifact_sha256:
                raise RepresentationError("a mapping must belong to the artifact of the representations it aligns")
            owner = self._dir(artifact_sha256, mapping.left)
            if not (owner / RECORD).is_file():
                raise RepresentationError(f"mapping {mapping.id} names unknown representation {mapping.left}")
            guard = WriteGuard(owner / MAPPINGS, create=True)
            name = f"{mapping.id}.json"
            if guard.path(name).is_file():
                held = RepresentationMapping.model_validate_json(read_text(self.root, f"{artifact_sha256}/{mapping.left}/{MAPPINGS}/{name}"))
                if held != mapping:
                    raise RepresentationError(f"mapping {mapping.id} already exists with different content")
                continue
            guard.write_json(name, mapping.model_dump(mode="json"))

    def get(self, artifact_sha256: str, id: str) -> DerivedRepresentation:
        try:
            record = DerivedRepresentation.model_validate_json(read_text(self.root, f"{artifact_sha256}/{id}/{RECORD}"))
        except FileNotFoundError:
            raise RepresentationError(f"representation {id} of artifact {artifact_sha256} is not held") from None
        except (OSError, ValueError, LayoutError) as error:
            raise RepresentationError(f"representation {id} record is corrupt: {error}") from error
        if record.id != id or record.artifact_sha256 != artifact_sha256:
            raise RepresentationError(f"representation {id} record names a different identity")
        return record

    def list(self, artifact_sha256: str, kind: RepresentationKind | None = None) -> tuple[DerivedRepresentation, ...]:
        directory = self.root / artifact_sha256
        if not directory.is_dir() or directory.is_symlink():
            return ()
        records = []
        for child in sorted(directory.iterdir()):
            if child.is_symlink() or not (child / RECORD).is_file():
                continue
            record = self.get(artifact_sha256, child.name)
            if kind is None or record.kind is kind:
                records.append(record)
        return tuple(sorted(records, key=lambda r: (r.derived_at, r.id)))

    def payload(self, artifact_sha256: str, id: str, name: str) -> bytes:
        record = self.get(artifact_sha256, id)
        if name not in record.payload_files:
            raise RepresentationError(f"representation {id} has no payload {name!r}")
        data = read_bytes(self.root, f"{artifact_sha256}/{id}/{name}")
        held = {n: read_bytes(self.root, f"{artifact_sha256}/{id}/{n}") for n in record.payload_files}
        if payload_digest(held) != record.output_sha256:
            raise RepresentationError(f"representation {id} payloads no longer digest to their record")
        return data

    def text(self, artifact_sha256: str, id: str) -> str:
        return self.payload(artifact_sha256, id, TEXT_PAYLOAD).decode("utf-8")

    def texts(self, artifact_sha256: str) -> dict[str, str]:
        """Every text-bearing representation of an artifact, by id, for span resolution."""
        found: dict[str, str] = {}
        for record in self.list(artifact_sha256):
            if TEXT_PAYLOAD in record.payload_files:
                found[record.id] = self.text(artifact_sha256, record.id)
        return found

    def mappings(
        self, artifact_sha256: str, left: str | None = None, right: str | None = None,
    ) -> tuple[RepresentationMapping, ...]:
        found: list[RepresentationMapping] = []
        for record in self.list(artifact_sha256):
            directory = self._dir(artifact_sha256, record.id) / MAPPINGS
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if child.suffix != ".json" or child.is_symlink():
                    continue
                mapping = RepresentationMapping.model_validate_json(read_text(self.root, f"{artifact_sha256}/{record.id}/{MAPPINGS}/{child.name}"))
                if (left is None or mapping.left == left) and (right is None or mapping.right == right):
                    found.append(mapping)
        return tuple(found)

    def page_manifest(self, artifact_sha256: str) -> tuple[DerivedRepresentation, list[dict]] | None:
        manifests = self.list(artifact_sha256, RepresentationKind.PAGE_MANIFEST)
        if not manifests:
            return None
        record = manifests[-1]
        pages = json.loads(self.payload(artifact_sha256, record.id, PAGES_PAYLOAD).decode("utf-8"))
        if not isinstance(pages, list):
            raise RepresentationError("page manifest payload must be a list of pages")
        return record, pages

    def page_labels(self, artifact_sha256: str) -> tuple[tuple[int, str], ...]:
        manifest = self.page_manifest(artifact_sha256)
        if manifest is None:
            return ()
        _, pages = manifest
        return tuple((int(p["index"]), str(p["label"])) for p in pages if p.get("label") is not None)

    def page_count(self, artifact_sha256: str) -> int | None:
        manifest = self.page_manifest(artifact_sha256)
        return None if manifest is None else len(manifest[1])
