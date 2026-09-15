"""Files the browser drops onto a project: staged under `.local/uploads/`,
never committed, and promoted from there by two different gates.

Lean and TeX are staged here and left for the terminal's own `/import`
command to admit into `lean/` or `tex/`; this module never writes to either
tree. PDFs and other documents go the other way, through `library_import`,
which is `hardy library import` and `hardy library seed` run back to back --
the same admission the CLI performs, called directly rather than shelled out
to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hardy.foundation.files import WriteGuard
from hardy.workflows.layout import LOCAL_DIR, RESERVED_CHARACTERS, RESERVED_NAMES

#: A generous ceiling on one staged file, well under what a browser upload
#: or the loopback server would hold in memory at once.
MAX_UPLOAD = 32 << 20
#: Comfortably past any real file name; a browser drop that names a file
#: longer than this is not a name Hardy needs to accommodate.
MAX_NAME_LENGTH = 255
UPLOADS_DIR = "uploads"
SOURCE_SUFFIXES = {".pdf", ".epub", ".djvu", ".txt", ".md", ".html", ".htm", ".xml"}


def safe_name(name: str) -> str:
    """`name`, proven to be one plain file name, or a refusal.

    Interior dots are kept -- `Sylow.v2.lean` is an ordinary name -- but a
    leading or trailing dot, a trailing space, a path separator, a control
    character, a Windows-forbidden character, or a reserved device name is
    refused outright, the same shape of check
    `hardy.workflows.layout.validate_slug` applies to a project slug. The
    reserved-name check matches everything before the *first* dot, the way
    Windows reserves it and the way `validate_slug` checks it
    (`text.partition(".")[0]`) -- `Path.stem` strips only the last suffix, so
    it would wave `con.v2.lean` and `nul.tar.gz` through.
    """
    if not name or name in {".", ".."} or name.startswith(".") or name != name.rstrip(" ."):
        raise ValueError(f"not a usable file name: {name!r}")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"file name is longer than {MAX_NAME_LENGTH} characters: {name!r}")
    if "/" in name or "\\" in name or any(ord(c) < 32 or c == "\x7f" for c in name) or set(name) & RESERVED_CHARACTERS:
        raise ValueError(f"not a usable file name: {name!r}")
    if name.partition(".")[0].lower() in RESERVED_NAMES:
        raise ValueError(f"not a usable file name: {name!r}")
    return name


def kind_of(name: str) -> str:
    """What `stage` and the terminal's gates treat `name` as, by suffix alone."""
    suffix = Path(name).suffix.lower()
    if suffix == ".lean":
        return "lean"
    if suffix == ".tex":
        return "tex"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    return "other"


def _dir(problem: Path) -> Path:
    return problem / LOCAL_DIR / UPLOADS_DIR


def _describe(path: Path) -> dict[str, Any]:
    return {"name": path.name, "path": str(path.resolve()), "size": path.stat().st_size, "kind": kind_of(path.name)}


def stage(problem: Path, name: str, data: bytes) -> dict[str, Any]:
    """Write `data` under `problem`'s upload area as `name`, suffixing a collision.

    `A.lean` staged twice becomes `A.lean` and `A-2.lean`, never a silent
    overwrite -- the browser client drops files one at a time and the user
    sees both.
    """
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"upload of {len(data)} bytes exceeds the {MAX_UPLOAD} byte limit")
    name = safe_name(name)
    guard = WriteGuard(_dir(problem), create=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, count = name, 1
    while guard.path(candidate).exists():
        count += 1
        candidate = f"{stem}-{count}{suffix}"
    guard.write_bytes(candidate, data)
    return _describe(guard.path(candidate))


def staged(problem: Path) -> list[dict[str, Any]]:
    """Every file waiting in `problem`'s upload area, sorted by name."""
    root = _dir(problem)
    if not root.is_dir():
        return []
    return [_describe(path) for path in sorted(root.iterdir()) if path.is_file() and not path.is_symlink()]


def discard(problem: Path, name: str) -> None:
    """Remove a staged file the user decided not to keep, or refuse if it is not there."""
    name = safe_name(name)
    guard = WriteGuard(_dir(problem), create=True)
    if not guard.path(name).is_file():
        raise ValueError(f"no staged file {name!r}")
    guard.unlink(name)


def library_import(problem: Path, name: str, *, title: str = "", author: str = "", intent: str = "",
                    library_root: Path | None = None) -> dict[str, Any]:
    """Admit a staged document into the personal library and seed `problem` with it.

    What `hardy library import` followed by `hardy library seed` do, called
    directly: the staged bytes at `name` are imported as a content-addressed
    artifact, extracted, and then seeded into the problem so the session may
    read it. Imports local to keep this module's own import cheap -- the
    literature package pulls in the adapter registry, which most callers of
    `stage` and `staged` never touch.
    """
    from hardy.literature.sources.artifacts import ImportRequest
    from hardy.literature.sources.contracts import AccessPolicy
    from hardy.literature.sources.library import ManagedLibrary
    from hardy.literature.sources.reading import SourceUnavailable
    from hardy.literature.sources.seeds import SeedStore, new_seed
    from hardy.literature.sources.tools import library_root as default_root

    name = safe_name(name)
    path = _dir(problem) / name
    if not path.is_file():
        raise ValueError(f"no staged file {name!r}")
    held = ManagedLibrary(library_root or default_root())
    metadata = tuple((k, v) for k, v in (("title", title), ("author", author)) if v)
    request = ImportRequest(path=path, original_name=name, access=AccessPolicy.PRIVATE_LOCAL, user_metadata=metadata)
    report = held.import_source(request, extract=True)
    artifact = report.outcome.artifact
    edition = held.catalog.edition_of(artifact.sha256)
    try:
        tree = held.trees.preferred(artifact.sha256)
    except SourceUnavailable:
        tree = None
    store = SeedStore(problem)
    seed = new_seed(artifact.sha256, edition=edition.id if edition else None, tree=tree.id if tree else None,
                     priority=0, intent=intent or None)
    store.add(seed, expected_revision=store.revision())
    return {
        "artifact": artifact.sha256,
        "format": artifact.format.value,
        "reused": bool(report.outcome.reused),
        "extraction": report.extraction.status if report.extraction is not None else None,
        "seed": seed.id,
    }
