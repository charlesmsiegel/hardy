"""Read-only views of files under the problem directory: sources, cells, PDFs, seeds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hardy.foundation.files import resolve_named_child
from hardy.literature.bibliography import Bibliography, BibliographyError
from hardy.literature.sources.seeds import SeedStore

#: A text panel this large would be unreadable in a browser tab regardless of
#: what it costs to serve; past this, `file_text` truncates and says so.
TEXT_LIMIT = 1 << 20
#: A compiled writeup this large is not the ordinary case; `pdf_bytes` refuses
#: rather than hand the browser something it will stall trying to render.
PDF_LIMIT = 32 << 20
#: The only trees served as plain text, because they are the only trees whose
#: content is meant to be read raw: generated build output, the session
#: record, and everything else stays off this path. `cas/` holds the cell
#: files a computation is filed as, the last export, and the journal.
SERVED_TREES = ("lean", "tex", "cas")
#: Under `cas/`, what is not a file to read: the scratch trees an export
#: empties, the writer lease, and the spend counter beside the journal.
CAS_SKIPPED = frozenset({"replay", "script-run", "cells.jsonl.lock", "cells.jsonl.spend.json"})
#: How many of the newest cells the journal view carries, and how much of one
#: cell's source or output. The journal itself is served whole as text.
CELLS_LIMIT = 500
CELL_TEXT_LIMIT = 16 * 1024


def confine(problem: Path, relative: str) -> Path:
    """`problem/relative`, each path component proven to be its parent's own child.

    Refuses an absolute path, `.`/`..` components, and -- through
    `resolve_named_child` -- a symlink anywhere along the way. This is the one
    gate every artifact panel reads or writes through.
    """
    candidate = Path(relative)
    parts = candidate.parts
    if not parts or candidate.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"not a path inside the problem: {relative!r}")
    current = problem.resolve()
    for part in parts:
        current = resolve_named_child(current / part, current)
    return current


def files(problem: Path) -> dict[str, list[str]]:
    """Every lean/tex source and every compiled PDF this problem currently has."""
    out: dict[str, list[str]] = {"lean": [], "tex": [], "cas": [], "pdf": []}
    for tree_name in SERVED_TREES:
        root = problem / tree_name
        if root.is_dir():
            out[tree_name] = sorted(
                path.relative_to(problem).as_posix()
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink() and ".build" not in path.parts
                and not (tree_name == "cas" and (path.relative_to(root).parts[0] in CAS_SKIPPED))
            )
    top = problem / "writeup.pdf"
    if top.is_file() and not top.is_symlink():
        out["pdf"].append("writeup.pdf")
    publications = problem / "publications"
    if publications.is_dir():
        out["pdf"].extend(
            sorted(
                path.relative_to(problem).as_posix()
                for path in publications.glob("*/writeup.pdf")
                if path.is_file() and not path.is_symlink()
            )
        )
    return out


def file_text(problem: Path, relative: str) -> dict[str, Any]:
    """A lean/ or tex/ source file's text, truncated and marked if it is too big.

    Nothing outside `lean/` and `tex/` is served as text: not `session.json`,
    not a stray file dropped anywhere else in the problem directory.
    """
    if Path(relative).parts[:1] not in {(name,) for name in SERVED_TREES}:
        raise ValueError("only lean/, tex/ and cas/ are served as text")
    if Path(relative).parts[:1] == ("cas",) and (len(Path(relative).parts) < 2 or Path(relative).parts[1] in CAS_SKIPPED):
        raise ValueError("not a computer algebra file of this problem")
    path = confine(problem, relative)
    data = path.read_bytes()
    # Imported here, not at module level, so a test's `monkeypatch.setattr(panels,
    # "TEXT_LIMIT", ...)` on the re-exporting package is what this reads -- the
    # same indirection the pre-split module gave for free by being one file.
    from hardy.app.web import panels

    truncated = len(data) > panels.TEXT_LIMIT
    return {"path": relative, "text": data[: panels.TEXT_LIMIT].decode("utf-8", errors="replace"), "truncated": truncated}


def cas_cells(problem: Path) -> dict[str, Any]:
    """The computer algebra journal as cells: what ran, from which file, and what it printed.

    Read straight off `cas/cells.jsonl` rather than through a `CasSession`,
    which would take the journal's writer lease from the live session. A
    line that will not parse is skipped, as the session itself skips a torn
    tail. Only the newest `CELLS_LIMIT` cells are carried, and each text
    field is cut to `CELL_TEXT_LIMIT` and says so; the whole journal is
    still served as text through `file_text`.
    """
    path = confine(problem, "cas/cells.jsonl")
    if not path.is_file():
        return {"cells": [], "segment": 0, "total": 0, "truncated": False}
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    segment = max((int(record.get("segment", 0) or 0) for record in records), default=0)

    def cut(text: Any) -> dict[str, Any]:
        shown = str(text or "")
        return {"text": shown[:CELL_TEXT_LIMIT], "truncated": len(shown) > CELL_TEXT_LIMIT}

    cells = [
        {
            "seq": record.get("seq"), "segment": record.get("segment", 0), "author": record.get("author", ""),
            "path": record.get("path", ""), "status": record.get("status", ""),
            "accepted": bool(record.get("accepted")), "live": int(record.get("segment", 0) or 0) == segment,
            "duration_ms": record.get("duration_ms", 0), "source": cut(record.get("source")),
            "stdout": cut(record.get("stdout")), "stderr": cut(record.get("stderr")),
            "value_repr": cut(record.get("value_repr")), "restart_note": str(record.get("restart_note", "") or ""),
        }
        for record in records[-CELLS_LIMIT:]
    ]
    return {"cells": cells, "segment": segment, "total": len(records), "truncated": len(records) > CELLS_LIMIT}


def pdf_bytes(problem: Path, relative: str) -> bytes:
    """One compiled PDF's bytes, refusing anything `files` did not list as one."""
    if relative not in files(problem)["pdf"]:
        raise ValueError("not a compiled PDF of this problem")
    path = confine(problem, relative)
    if path.stat().st_size > PDF_LIMIT:
        raise ValueError("PDF is larger than the browser limit")
    return path.read_bytes()


def sources(problem: Path) -> dict[str, Any]:
    """The problem's bibliography and library seeds, each already its own read model.

    A missing store is already an empty `Store`/`JournalSnapshot`, so only a
    corrupt `bibliography.json` needs handling here: `Bibliography.read`
    raises `BibliographyError` for that, and this degrades to an empty
    bibliography rather than failing the whole panel.
    """
    try:
        bibliography = [entry.model_dump(mode="json") for entry in Bibliography(problem).entries()]
    except BibliographyError:
        bibliography = []
    seeds = [
        {"id": seed.id, "artifact": seed.artifact_sha256, "priority": seed.priority, "intent": seed.intent or ""}
        for seed in SeedStore(problem).seeds()
    ]
    return {"bibliography": bibliography, "seeds": seeds}
