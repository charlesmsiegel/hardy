"""Export classes and portable import for the personal library.

Three logically separate classes: metadata and semantics (works, editions,
artifact records and provenance, trees, the shared ledger, links,
realizations, promotions: every identity and digest, no source bytes and no
derived text of a private source), the user's own formal library (the shared
Lean sources), and the private source cache (artifact bytes and every
derived payload). Missing artifact bytes are named in missing_bytes; absent
derived payloads remain named in withheld_payloads, including private-cache
exports. Failed exports leave their fresh destination unchanged. The default
portable state is the first class alone. A
bundle imported on another machine restores refs and records; source reads
there report unavailable until the exact digest is imported, at which point
the historical identities resolve again without being reminted. Merging two
diverged journals is synchronization, which is deferred: an import only
seeds a journal that is empty here and reports what it left alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath

from hardy.foundation.files import LayoutError, WriteGuard, files_under, guard_for, read_bytes
from hardy.foundation.journal import JournalError, verify_chain
from hardy.foundation.locking import FileLock, LockTimeout
from hardy.foundation.values import FrozenModel, json_digest

from .artifacts import CONTENT, PROVENANCE_DIR, RECORD
from .contracts import AccessPolicy, RepresentationKind, most_restrictive
from .library import ARTIFACTS, REPRESENTATIONS, TREES, ManagedLibrary
from .representations import MAPPINGS
from .representations import RECORD as REPRESENTATION_RECORD

MANIFEST = "export.json"
SCHEMA = "hardy.library-export/v1"
JOURNALS = ("catalog", "ledger", "links", "realizations", "promotions")
PRIVATE = frozenset({AccessPolicy.PRIVATE_LOCAL, AccessPolicy.UNKNOWN_RESTRICTED})
PORTABLE_KINDS = frozenset({RepresentationKind.PAGE_MANIFEST})


class ExportError(ValueError):
    """An export or import Hardy will not perform."""


def safe_relative(relative: str) -> PurePosixPath:
    """A manifest path as a normalized child path, or a refusal; never absolute, never climbing."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ExportError(f"bundle path {relative!r} is not a plain relative path")
    path = PurePosixPath(relative)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", relative) or any(part in {"", ".", ".."} for part in path.parts):
        raise ExportError(f"bundle path {relative!r} would leave its destination; refusing it")
    return path


class ExportClass(str, Enum):
    METADATA_SEMANTICS = "metadata_semantics"
    USER_FORMAL_LIBRARY = "user_formal_library"
    PRIVATE_SOURCE_CACHE = "private_source_cache"


class ExportManifest(FrozenModel):
    format: str = SCHEMA
    classes: tuple[ExportClass, ...]
    files: tuple[tuple[str, str], ...]
    artifacts: tuple[str, ...]
    withheld_payloads: tuple[str, ...] = ()
    missing_bytes: tuple[str, ...] = ()
    created_at: str


class ImportSummary(FrozenModel):
    copied: tuple[str, ...]
    skipped: tuple[str, ...]
    journals_seeded: tuple[str, ...]
    journals_kept: tuple[str, ...]


@dataclass(frozen=True)
class _JournalDestination:
    """Whether a local journal already has history, or its location was refused."""

    has_history: bool
    fault: str | None = None


def _copy(source: Path, into: Path, relative: str, files: list[tuple[str, str]]) -> None:
    target = into / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    files.append((relative, hashlib.sha256(target.read_bytes()).hexdigest()))


def _copy_tree(root: Path, into: Path, prefix: str, files: list[tuple[str, str]], *, suffixes: tuple[str, ...] = (".json",)) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_file() and not path.is_symlink() and path.suffix in suffixes and not path.name.endswith(".lock"):
            _copy(path, into, f"{prefix}/{path.relative_to(root).as_posix()}", files)


def export_library(
    library: ManagedLibrary, *, classes: frozenset[ExportClass], into: Path, shared_lean: Path | None = None,
) -> ExportManifest:
    """Publish only the selected portable classes into one fresh, atomic bundle."""
    into = Path(into).absolute()
    parent = WriteGuard(into.parent, create=True)
    try:
        with FileLock(parent.reserve(f".{into.name}.export.lock")):
            parent.reserve(into.name)
            if into.exists() and (not into.is_dir() or any(into.iterdir())):
                raise ExportError(f"{into} is not an empty directory; an export needs a fresh destination so a bundle holds only what its manifest claims")
            staging = Path(tempfile.mkdtemp(prefix=f".{into.name}.staging-", dir=parent.directory))
            removed_empty = False
            try:
                manifest = _export_library(library, classes=classes, into=staging, shared_lean=shared_lean)
                parent.reserve(into.name)
                if into.exists():
                    into.rmdir()  # Refuses newly populated destinations; never recursively remove one.
                    removed_empty = True
                os.replace(staging, into)
                return manifest
            except BaseException:
                if removed_empty and not into.exists():
                    into.mkdir()
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    except LockTimeout as error:
        raise ExportError(f"export destination {into} lock could not be taken: {error}") from error


def _export_library(library: ManagedLibrary, *, classes: frozenset[ExportClass], into: Path, shared_lean: Path | None) -> ExportManifest:
    files: list[tuple[str, str]] = []
    withheld: list[str] = []
    missing: list[str] = []
    digests = library.artifacts.stored()
    root = library.root
    if ExportClass.METADATA_SEMANTICS in classes:
        for sha in digests:
            withheld.extend(_export_artifact_metadata(library, sha, into, files))
        _copy_tree(root / TREES / "journal", into, f"{TREES}/journal", files)
        for journal in JOURNALS:
            _copy_tree(root / journal, into, journal, files)
    if ExportClass.USER_FORMAL_LIBRARY in classes and shared_lean is not None and shared_lean.is_dir():
        for relative in files_under(shared_lean, ".lean"):
            _copy(shared_lean / relative, into, f"lean/{relative.as_posix()}", files)
    if ExportClass.PRIVATE_SOURCE_CACHE in classes:
        for sha in digests:
            absent, uncopied = _export_artifact_cache(library, sha, into, files)
            if absent:
                missing.append(sha)
            withheld.extend(uncopied)
        copied_payloads = {name.removeprefix(f"{REPRESENTATIONS}/") for name, _ in files if name.startswith(f"{REPRESENTATIONS}/")}
        withheld = [name for name in withheld if name not in copied_payloads]
    manifest = ExportManifest(classes=tuple(sorted(classes, key=lambda c: c.value)), files=tuple(files), artifacts=digests,
                              withheld_payloads=tuple(dict.fromkeys(withheld)), missing_bytes=tuple(missing), created_at=datetime.now(UTC).isoformat(timespec="seconds"))
    (into / MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def _export_artifact_metadata(library: ManagedLibrary, sha: str, into: Path, files: list[tuple[str, str]]) -> list[str]:
    """Copy attribution and structure, applying both artifact and representation access."""
    withheld = []
    directory = library.root / ARTIFACTS / sha
    artifact_access = library.artifacts.record(sha).access
    _copy(directory / RECORD, into, f"{ARTIFACTS}/{sha}/{RECORD}", files)
    _copy_tree(directory / PROVENANCE_DIR, into, f"{ARTIFACTS}/{sha}/{PROVENANCE_DIR}", files)
    for record in library.representations.list(sha):
        rep_dir = library.root / REPRESENTATIONS / sha / record.id
        _copy(rep_dir / REPRESENTATION_RECORD, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{REPRESENTATION_RECORD}", files)
        _copy_tree(rep_dir / MAPPINGS, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{MAPPINGS}", files)
        # A policy tightened after extraction still governs its derived text.
        portable = most_restrictive(artifact_access, record.access) not in PRIVATE or record.kind in PORTABLE_KINDS
        for name in record.payload_files:
            if portable and (rep_dir / name).is_file():
                _copy(rep_dir / name, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{name}", files)
            else:
                withheld.append(f"{sha}/{record.id}/{name}")
    _copy_tree(library.root / TREES / sha, into, f"{TREES}/{sha}", files)
    return withheld


def _export_artifact_cache(library: ManagedLibrary, sha: str, into: Path, files: list[tuple[str, str]]) -> tuple[bool, list[str]]:
    """Copy available private bytes without recopying payloads already made portable."""
    content = library.root / ARTIFACTS / sha / CONTENT
    absent = not content.is_file()
    if not absent:
        _copy(content, into, f"{ARTIFACTS}/{sha}/{CONTENT}", files)
    withheld = []
    for record in library.representations.list(sha):
        for name in record.payload_files:
            relative = f"{REPRESENTATIONS}/{sha}/{record.id}/{name}"
            if relative not in {f for f, _ in files}:
                payload = library.root / REPRESENTATIONS / sha / record.id / name
                if payload.is_file():
                    _copy(payload, into, relative, files)
                else:
                    withheld.append(f"{sha}/{record.id}/{name}")
    return absent, withheld


def import_export(library: ManagedLibrary, bundle: Path, *, shared_lean: Path | None = None) -> ImportSummary:
    """Restore matching bytes and empty journals, preserving every local disagreement."""
    bundle = Path(bundle)
    manifest = ExportManifest.model_validate_json(read_bytes(bundle, MANIFEST))
    copied: list[str] = []
    skipped: list[str] = []
    journal_dirs = (*JOURNALS, f"{TREES}/journal")
    destinations = _journal_destinations(library.root, journal_dirs)
    kept = [owner for owner, target in destinations.items() if target.has_history]
    seeded = [owner for owner, target in destinations.items() if not target.has_history]
    grouped: dict[str, list[tuple[str, PurePosixPath, str]]] = {journal: [] for journal in journal_dirs}
    for relative, digest in manifest.files:
        try:
            safe = safe_relative(relative)
        except ExportError as error:
            skipped.append(f"{relative}: {error}")
            continue
        relative = safe.as_posix()
        if len(safe.parts) < 2 or safe.parts[0] not in {ARTIFACTS, REPRESENTATIONS, TREES, *JOURNALS, "lean"}:
            skipped.append(f"{relative}: not an exported library location")
            continue
        owner = next((j for j in journal_dirs if relative.startswith(j + "/")), None)
        if owner is not None:
            grouped[owner].append((relative, safe, digest))
            continue
        fault = _import_payload(library.root, bundle, safe, digest, shared_lean)
        if fault is None:
            copied.append(relative)
        else:
            skipped.append(f"{relative}: {fault}")
    journal_copies, journal_skips = _import_journals(library.root, bundle, grouped, destinations)
    copied.extend(journal_copies)
    skipped.extend(journal_skips)
    return ImportSummary(copied=tuple(copied), skipped=tuple(skipped), journals_seeded=tuple(j for j in seeded if any(c.startswith(j + "/") for c in copied)),
                         journals_kept=tuple(kept))


def _journal_destinations(root: Path, owners: tuple[str, ...]) -> dict[str, _JournalDestination]:
    destinations = {}
    for owner in owners:
        try:
            guard, _ = guard_for(root, f"{owner}/.import-location")
            existing = guard.directory
            destinations[owner] = _JournalDestination(existing.is_dir() and any(existing.glob("*.json")))
        except (OSError, LayoutError) as error:
            destinations[owner] = _JournalDestination(False, str(error))
    return destinations


def _import_journals(
    root: Path, bundle: Path, grouped: dict[str, list[tuple[str, PurePosixPath, str]]], destinations: dict[str, _JournalDestination],
) -> tuple[list[str], list[str]]:
    """Verify empty-destination journals as whole chains; synchronization stays deferred."""
    copied = []
    skipped = []
    for owner, entries in grouped.items():
        if not entries:
            continue
        target = destinations[owner]
        if target.fault is not None:
            skipped.append(f"{owner}: {target.fault}; nothing of this journal was imported")
            continue
        if target.has_history:
            skipped.append(f"{owner}: local journal already has history; merging is synchronization, which is deferred ({len(entries)} file(s) not imported)")
            continue
        fault = _import_journal(root, bundle, owner, entries)
        if fault is None:
            copied.extend(relative for relative, _, _ in entries)
        else:
            skipped.append(f"{owner}: {fault}; nothing of this journal was imported")
    return copied, skipped


def _import_payload(root: Path, bundle: Path, safe: PurePosixPath, digest: str, shared_lean: Path | None) -> str | None:
    """Verify one ordinary file, choose its root, and refuse to overwrite local bytes."""
    try:
        data = read_bytes(bundle, safe)
    except (OSError, LayoutError) as error:
        return f"cannot read bundle file ({error})"
    if hashlib.sha256(data).hexdigest() != digest:
        return "digest mismatch"
    if safe.parts[0] == "lean":
        if shared_lean is None:
            return "no shared Lean root given"
        destination = shared_lean
        destination_relative = PurePosixPath(*safe.parts[1:])
        if destination_relative.suffix != ".lean":
            return "not an exported Lean source"
    else:
        destination = root
        destination_relative = safe
    try:
        guard, name = guard_for(destination, destination_relative, create=True)
        target = guard.path(name)
        if target.exists():
            with guard.open(name, "rb") as handle:
                same = hashlib.sha256(handle.read()).hexdigest() == digest
            return "already present" if same else "local file differs; not overwritten"
        guard.write_bytes(name, data)
    except (OSError, LayoutError) as error:
        return f"cannot write destination ({error})"
    return None


def _import_journal(root: Path, bundle: Path, owner: str, entries: list[tuple[str, PurePosixPath, str]]) -> str | None:
    """Stage every file of one journal, verify the chain, then move it in as a unit; the fault otherwise."""
    try:
        parent_guard, name = guard_for(root, owner, create=True)
        target = parent_guard.reserve(name)
        destination_guard = WriteGuard(target)
        if target.exists() and not target.is_dir():
            return "journal destination is not a directory"
    except (OSError, LayoutError) as error:
        return f"cannot write journal destination ({error})"
    staging = Path(tempfile.mkdtemp(prefix=".import-journal-", dir=parent_guard.directory))
    try:
        depth = len(PurePosixPath(owner).parts) + 1
        for relative, safe, digest in entries:
            if len(safe.parts) != depth:
                return f"{relative} is not a journal file"
            try:
                data = read_bytes(bundle, safe)
            except (OSError, LayoutError) as error:
                return f"{relative} cannot read bundle file ({error})"
            if hashlib.sha256(data).hexdigest() != digest:
                return f"{relative} digest mismatch"
            (staging / safe.name).write_bytes(data)
        try:
            verify_chain(staging)
        except JournalError as error:
            return f"journal chain invalid ({error})"
        if not target.exists():
            os.replace(staging, parent_guard.reserve(name))
            return None
        for path in sorted(staging.iterdir(), key=lambda p: p.relative_to(staging).as_posix()):
            os.replace(path, destination_guard.reserve(path.name))
        return None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def bundle_digest(bundle: Path) -> str:
    """Identity of the manifest's ordered file claims, independently of creation time."""
    manifest = json.loads((Path(bundle) / MANIFEST).read_text(encoding="utf-8"))
    return json_digest(manifest.get("files", []))
