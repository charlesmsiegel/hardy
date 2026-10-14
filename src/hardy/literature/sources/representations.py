"""Write-once storage for derived representations and their mappings.

A representation is admitted with its payload files and never rewritten; an
improved extraction is a new representation with a new id, and the old one
stays readable for every span that named it. Payload reads verify the bytes
against the record's output digest and reuse verification while guarded file
identities stay unchanged, so only the recorded reading is served. Page labels come from a page manifest
representation and are answered separately from page indices.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from hardy.foundation.files import LayoutError, WriteGuard, guard_for, read_bytes, read_text
from hardy.foundation.values import json_digest
from hardy.literature.file_identity import change_token

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
        self._verified: OrderedDict[tuple[str, str], tuple[tuple, dict[str, bytes]]] = OrderedDict()
        self._verified_bytes = 0
        self._decoded: dict[tuple[str, str], str] = {}

    def _signature(self, relative: str) -> tuple[int | None, ...]:
        guard, name = guard_for(self.root, relative)
        path = guard.path(name)
        leaf = path.lstat()
        if not stat.S_ISREG(leaf.st_mode):
            raise LayoutError(f"{path} is not a regular file")
        parent = path.parent.stat()
        return (parent.st_dev, parent.st_ino, leaf.st_dev, leaf.st_ino,
                leaf.st_size, leaf.st_mtime_ns, change_token(path, leaf))

    def _payload_signature(self, artifact: str, id: str, record: DerivedRepresentation) -> tuple:
        return tuple(self._signature(f"{artifact}/{id}/{name}") for name in (RECORD, *record.payload_files))

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
            # A record whose payloads were withheld from a portable export and
            # have now been regenerated from the restored bytes: the same
            # output under the same identity, so the payloads are put back.
            guard = WriteGuard(existing)
            for name, data in payloads.items():
                if not (existing / name).is_file():
                    guard.write_bytes(name, data)
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
                # Another admitter won the rename. The winner is authoritative
                # only if it recorded the same output; a different reading
                # under this id is the conflict the sequential path refuses.
                winner = self.get(record.artifact_sha256, record.id)
                if winner.output_sha256 != record.output_sha256:
                    raise RepresentationError(f"representation {record.id} was admitted concurrently with different output") from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        self.add_mappings(record.artifact_sha256, mappings)
        return self.get(record.artifact_sha256, record.id)

    def add_mappings(self, artifact_sha256: str, mappings: tuple[RepresentationMapping, ...]) -> None:
        """Record mappings beside their left representation; a repeated identical mapping is a no-op."""
        for mapping in mappings:
            if mapping.artifact_sha256 != artifact_sha256:
                raise RepresentationError("a mapping must belong to the artifact of the representations it aligns")
            for side, rep in (("left", mapping.left), ("right", mapping.right)):
                if not (self._dir(artifact_sha256, rep) / RECORD).is_file():
                    raise RepresentationError(f"mapping {mapping.id} names unknown representation {rep} on its {side}")
            for left_locator, right_locator in mapping.pairs:
                for side, locator, expected in (("left", left_locator, mapping.left), ("right", right_locator, mapping.right)):
                    named = getattr(locator, "representation", None)
                    if named is not None and named != expected:
                        raise RepresentationError(f"mapping {mapping.id}: a {side} locator names representation {named}, not {expected}")
            owner = self._dir(artifact_sha256, mapping.left)
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
        for child in sorted(directory.iterdir(), key=lambda p: p.relative_to(directory).as_posix()):
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
        key = (artifact_sha256, id)
        signature = self._payload_signature(artifact_sha256, id, record)
        reusable = all(part[-1] is not None for part in signature)
        cached = self._verified.get(key)
        if reusable and cached is not None and cached[0] == signature:
            # Eviction after lookup does not invalidate the captured bytes.
            with suppress(KeyError):
                self._verified.move_to_end(key)
            return cached[1][name]
        held = {n: read_bytes(self.root, f"{artifact_sha256}/{id}/{n}") for n in record.payload_files}
        if payload_digest(held) != record.output_sha256:
            raise RepresentationError(f"representation {id} payloads no longer digest to their record")
        if self._payload_signature(artifact_sha256, id, record) != signature:
            raise RepresentationError(f"representation {id} changed during payload verification")
        self._decoded.pop(key, None)
        previous = self._verified.pop(key, None)
        if previous is not None:
            self._verified_bytes -= sum(map(len, previous[1].values()))
        size = sum(map(len, held.values()))
        # Keep a bounded process-local working set; larger readings remain usable.
        if reusable and size <= 64 * 1024 * 1024:
            self._verified[key] = (signature, held)
            self._verified_bytes += size
            while self._verified_bytes > 64 * 1024 * 1024 or len(self._verified) > 64:
                evicted_key, (_, evicted) = self._verified.popitem(last=False)
                self._decoded.pop(evicted_key, None)
                self._verified_bytes -= sum(map(len, evicted.values()))
        return held[name]

    def text(self, artifact_sha256: str, id: str) -> str:
        data = self.payload(artifact_sha256, id, TEXT_PAYLOAD)
        key = (artifact_sha256, id)
        text = self._decoded.get(key)
        if text is None:
            text = data.decode("utf-8")
            if key in self._verified:
                self._decoded[key] = text
        return text

    def texts(self, artifact_sha256: str) -> dict[str, str]:
        """Every text-bearing representation of an artifact, by id, for span resolution."""
        found: dict[str, str] = {}
        for record in self.list(artifact_sha256):
            if TEXT_PAYLOAD not in record.payload_files:
                continue
            try:
                found[record.id] = self.text(artifact_sha256, record.id)
            except (FileNotFoundError, LayoutError):
                # A record whose payload was withheld from a portable export:
                # the representation is identifiable here and its text is not.
                continue
        return found

    def mappings(
        self, artifact_sha256: str, left: str | None = None, right: str | None = None,
    ) -> tuple[RepresentationMapping, ...]:
        found: list[RepresentationMapping] = []
        for record in self.list(artifact_sha256):
            directory = self._dir(artifact_sha256, record.id) / MAPPINGS
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir(), key=lambda p: p.relative_to(directory).as_posix()):
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
