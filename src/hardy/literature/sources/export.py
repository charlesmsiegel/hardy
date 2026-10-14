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
            directory = root / ARTIFACTS / sha
            artifact_access = library.artifacts.record(sha).access
            _copy(directory / RECORD, into, f"{ARTIFACTS}/{sha}/{RECORD}", files)
            _copy_tree(directory / PROVENANCE_DIR, into, f"{ARTIFACTS}/{sha}/{PROVENANCE_DIR}", files)
            for record in library.representations.list(sha):
                rep_dir = root / REPRESENTATIONS / sha / record.id
                _copy(rep_dir / REPRESENTATION_RECORD, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{REPRESENTATION_RECORD}", files)
                _copy_tree(rep_dir / MAPPINGS, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{MAPPINGS}", files)
                # A derived text is governed by the stricter of its own policy and
                # its artifact's: a policy tightened after extraction still holds.
                portable = most_restrictive(artifact_access, record.access) not in PRIVATE or record.kind in PORTABLE_KINDS
                for name in record.payload_files:
                    if portable and (rep_dir / name).is_file():
                        _copy(rep_dir / name, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{name}", files)
                    else:
                        withheld.append(f"{sha}/{record.id}/{name}")
            _copy_tree(root / TREES / sha, into, f"{TREES}/{sha}", files)
        _copy_tree(root / TREES / "journal", into, f"{TREES}/journal", files)
        for journal in JOURNALS:
            _copy_tree(root / journal, into, journal, files)
    if ExportClass.USER_FORMAL_LIBRARY in classes and shared_lean is not None and shared_lean.is_dir():
        for relative in files_under(shared_lean, ".lean"):
            _copy(shared_lean / relative, into, f"lean/{relative.as_posix()}", files)
    if ExportClass.PRIVATE_SOURCE_CACHE in classes:
        for sha in digests:
            content = root / ARTIFACTS / sha / CONTENT
            if content.is_file():
                _copy(content, into, f"{ARTIFACTS}/{sha}/{CONTENT}", files)
            else:
                missing.append(sha)
            for record in library.representations.list(sha):
                for name in record.payload_files:
                    relative = f"{REPRESENTATIONS}/{sha}/{record.id}/{name}"
                    if relative not in {f for f, _ in files}:
                        payload = root / REPRESENTATIONS / sha / record.id / name
                        if payload.is_file():
                            _copy(payload, into, relative, files)
                        else:
                            withheld.append(f"{sha}/{record.id}/{name}")
        copied_payloads = {name.removeprefix(f"{REPRESENTATIONS}/") for name, _ in files if name.startswith(f"{REPRESENTATIONS}/")}
        withheld = [name for name in withheld if name not in copied_payloads]
    manifest = ExportManifest(classes=tuple(sorted(classes, key=lambda c: c.value)), files=tuple(files), artifacts=digests,
                              withheld_payloads=tuple(dict.fromkeys(withheld)), missing_bytes=tuple(missing), created_at=datetime.now(UTC).isoformat(timespec="seconds"))
    (into / MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def import_export(library: ManagedLibrary, bundle: Path, *, shared_lean: Path | None = None) -> ImportSummary:
    bundle = Path(bundle)
    manifest = ExportManifest.model_validate_json(read_bytes(bundle, MANIFEST))
    copied: list[str] = []
    skipped: list[str] = []
    seeded: list[str] = []
    kept: list[str] = []
    journal_state: dict[str, bool] = {}
    journal_faults: dict[str, str] = {}
    journal_dirs = (*JOURNALS, f"{TREES}/journal")
    for journal in journal_dirs:
        try:
            guard, _ = guard_for(library.root, f"{journal}/.import-location")
            existing = guard.directory
            journal_state[journal] = existing.is_dir() and any(existing.glob("*.json"))
        except (OSError, LayoutError) as error:
            journal_faults[journal] = str(error)
            journal_state[journal] = False
        (kept if journal_state[journal] else seeded).append(journal)
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
        try:
            data = read_bytes(bundle, safe)
        except (OSError, LayoutError) as error:
            skipped.append(f"{relative}: cannot read bundle file ({error})")
            continue
        if hashlib.sha256(data).hexdigest() != digest:
            skipped.append(f"{relative}: digest mismatch")
            continue
        if relative.startswith("lean/"):
            if shared_lean is None:
                skipped.append(f"{relative}: no shared Lean root given")
                continue
            destination = shared_lean
            destination_relative = PurePosixPath(*safe.parts[1:])
            if destination_relative.suffix != ".lean":
                skipped.append(f"{relative}: not an exported Lean source")
                continue
        else:
            destination = library.root
            destination_relative = safe
        try:
            guard, name = guard_for(destination, destination_relative, create=True)
            target = guard.path(name)
            if target.exists():
                with guard.open(name, "rb") as handle:
                    same = hashlib.sha256(handle.read()).hexdigest() == digest
                skipped.append(f"{relative}: already present" if same else f"{relative}: local file differs; not overwritten")
                continue
            guard.write_bytes(name, data)
        except (OSError, LayoutError) as error:
            skipped.append(f"{relative}: cannot write destination ({error})")
            continue
        copied.append(relative)
    # A journal is a hash chain: it is imported whole after its staged copy
    # verifies, or not at all. A gap or a corrupt revision would otherwise
    # seed a journal every later replay refuses.
    for owner, entries in grouped.items():
        if not entries:
            continue
        if owner in journal_faults:
            skipped.append(f"{owner}: {journal_faults[owner]}; nothing of this journal was imported")
            continue
        if journal_state[owner]:
            skipped.append(f"{owner}: local journal already has history; merging is synchronization, which is deferred ({len(entries)} file(s) not imported)")
            continue
        fault = _import_journal(library.root, bundle, owner, entries)
        if fault is None:
            copied.extend(relative for relative, _, _ in entries)
        else:
            skipped.append(f"{owner}: {fault}; nothing of this journal was imported")
    return ImportSummary(copied=tuple(copied), skipped=tuple(skipped), journals_seeded=tuple(j for j in seeded if any(c.startswith(j + "/") for c in copied)),
                         journals_kept=tuple(kept))


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
    manifest = json.loads((Path(bundle) / MANIFEST).read_text(encoding="utf-8"))
    return json_digest(manifest.get("files", []))
