"""Export classes and portable import for the personal library.

Three logically separate classes: metadata and semantics (works, editions,
artifact records and provenance, trees, the shared ledger, links,
realizations, promotions: every identity and digest, no source bytes and no
derived text of a private source), the user's own formal library (the shared
Lean sources), and the private source cache (artifact bytes and every
derived payload). The default portable state is the first class alone. A
bundle imported on another machine restores refs and records; source reads
there report unavailable until the exact digest is imported, at which point
the historical identities resolve again without being reminted. Merging two
diverged journals is synchronization, which is deferred: an import only
seeds a journal that is empty here and reports what it left alone.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from hardy.foundation.files import files_under
from hardy.foundation.values import FrozenModel, json_digest

from .artifacts import CONTENT, PROVENANCE_DIR, RECORD
from .contracts import AccessPolicy, RepresentationKind
from .library import ARTIFACTS, REPRESENTATIONS, TREES, ManagedLibrary
from .representations import MAPPINGS
from .representations import RECORD as REPRESENTATION_RECORD

MANIFEST = "export.json"
SCHEMA = "hardy.library-export/v1"
JOURNALS = ("catalog", "ledger", "links", "realizations", "promotions")
PRIVATE = frozenset({AccessPolicy.PRIVATE_LOCAL, AccessPolicy.UNKNOWN_RESTRICTED})
PORTABLE_KINDS = frozenset({RepresentationKind.PAGE_MANIFEST})


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
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix in suffixes and not path.name.endswith(".lock"):
            _copy(path, into, f"{prefix}/{path.relative_to(root).as_posix()}", files)


def export_library(
    library: ManagedLibrary, *, classes: frozenset[ExportClass], into: Path, shared_lean: Path | None = None,
) -> ExportManifest:
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, str]] = []
    withheld: list[str] = []
    digests = library.artifacts.stored()
    root = library.root
    if ExportClass.METADATA_SEMANTICS in classes:
        for sha in digests:
            directory = root / ARTIFACTS / sha
            _copy(directory / RECORD, into, f"{ARTIFACTS}/{sha}/{RECORD}", files)
            _copy_tree(directory / PROVENANCE_DIR, into, f"{ARTIFACTS}/{sha}/{PROVENANCE_DIR}", files)
            for record in library.representations.list(sha):
                rep_dir = root / REPRESENTATIONS / sha / record.id
                _copy(rep_dir / REPRESENTATION_RECORD, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{REPRESENTATION_RECORD}", files)
                _copy_tree(rep_dir / MAPPINGS, into, f"{REPRESENTATIONS}/{sha}/{record.id}/{MAPPINGS}", files)
                portable = record.access not in PRIVATE or record.kind in PORTABLE_KINDS
                for name in record.payload_files:
                    if portable:
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
            _copy(root / ARTIFACTS / sha / CONTENT, into, f"{ARTIFACTS}/{sha}/{CONTENT}", files)
            for record in library.representations.list(sha):
                for name in record.payload_files:
                    relative = f"{REPRESENTATIONS}/{sha}/{record.id}/{name}"
                    if relative not in {f for f, _ in files}:
                        _copy(root / REPRESENTATIONS / sha / record.id / name, into, relative, files)
        withheld = []
    manifest = ExportManifest(classes=tuple(sorted(classes, key=lambda c: c.value)), files=tuple(files), artifacts=digests,
                              withheld_payloads=tuple(withheld), created_at=datetime.now(UTC).isoformat(timespec="seconds"))
    (into / MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def import_export(library: ManagedLibrary, bundle: Path, *, shared_lean: Path | None = None) -> ImportSummary:
    bundle = Path(bundle)
    manifest = ExportManifest.model_validate_json((bundle / MANIFEST).read_text(encoding="utf-8"))
    copied: list[str] = []
    skipped: list[str] = []
    seeded: list[str] = []
    kept: list[str] = []
    journal_state: dict[str, bool] = {}
    for journal in (*JOURNALS, f"{TREES}/journal"):
        existing = library.root / journal
        journal_state[journal] = existing.is_dir() and any(existing.glob("*.json"))
        (kept if journal_state[journal] else seeded).append(journal)
    for relative, digest in manifest.files:
        source = bundle / relative
        if not source.is_file():
            skipped.append(f"{relative}: missing from bundle")
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            skipped.append(f"{relative}: digest mismatch")
            continue
        if relative.startswith("lean/"):
            if shared_lean is None:
                skipped.append(f"{relative}: no shared Lean root given")
                continue
            target = shared_lean / relative[len("lean/"):]
        else:
            target = library.root / relative
        owner = next((j for j in journal_state if relative.startswith(j + "/")), None)
        if owner is not None and journal_state[owner]:
            skipped.append(f"{relative}: local journal {owner} already has history; merging is synchronization, which is deferred")
            continue
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                skipped.append(f"{relative}: already present")
            else:
                skipped.append(f"{relative}: local file differs; not overwritten")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative)
    return ImportSummary(copied=tuple(copied), skipped=tuple(skipped), journals_seeded=tuple(j for j in seeded if any(c.startswith(j + "/") for c in copied)),
                         journals_kept=tuple(kept))


def bundle_digest(bundle: Path) -> str:
    manifest = json.loads((Path(bundle) / MANIFEST).read_text(encoding="utf-8"))
    return json_digest(manifest.get("files", []))
