"""Read-only views for the browser, each derived from the workspace's own artifacts.

Every function here is pure: given a session (for the conversational panels)
or a problem directory (for the artifact panels), it returns a JSON-
serializable value and nothing else -- no HTTP, no caching, no mutation. A
later task wires each one behind a GET endpoint; this module owns only the
shape of the answer.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from hardy.documents.export import _superseded
from hardy.foundation.files import resolve_named_child
from hardy.literature.bibliography import Bibliography, BibliographyError
from hardy.literature.sources.seeds import SeedStore
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation
from hardy.workflows.ledger.store import LedgerStore

#: A text panel this large would be unreadable in a browser tab regardless of
#: what it costs to serve; past this, `file_text` truncates and says so.
TEXT_LIMIT = 1 << 20
#: A compiled writeup this large is not the ordinary case; `pdf_bytes` refuses
#: rather than hand the browser something it will stall trying to render.
PDF_LIMIT = 32 << 20
#: How much of a ledger item's statement the graph panel carries per node. The
#: graph is a map of the project, not a reader for full statements -- `tex/`
#: and the lean tree already serve those in full.
STATEMENT_LIMIT = 400
#: The only trees served as plain text, because they are the only trees whose
#: content is meant to be read raw: generated build output, the session
#: record, and everything else stays off this path.
SERVED_TREES = ("lean", "tex")


def summary(session: Any) -> dict[str, Any]:
    """The session's own `Summary`, reshaped for JSON rather than a terminal."""
    found = session.summary()
    return {
        "goal": session.goal(),
        "sections": [{"title": section.title, "lines": list(section.shown)} for section in found.sections],
        "obligations": list(found.obligations),
        "has_theorems": found.has_theorems,
    }


def transcript(session: Any) -> list[dict[str, Any]]:
    """The active branch of the conversation, superseded partials dropped.

    Only the roles a browser panel renders are mapped: `user`, `assistant`,
    a finished `tool` call, a `turn` boundary, and a handful of notice-like
    bookkeeping events. Anything else -- `tool_started` with no matching
    `tool`, provider-internal records -- is skipped rather than guessed at.
    """
    entries = session.conversation_tree().path()
    events = [entry.event() for entry in entries]
    skip = _superseded(events)
    out: list[dict[str, Any]] = []
    for index, (entry, event) in enumerate(zip(entries, events, strict=True)):
        if index in skip:
            continue
        kind = event.get("type")
        if kind == "user":
            out.append({"role": "user", "text": str(event.get("message", {}).get("content", "")), "entry_id": entry.entry_id})
        elif kind == "assistant":
            out.append({
                "role": "assistant", "text": str(event.get("message", {}).get("content", "")),
                "entry_id": entry.entry_id, "partial": bool(event.get("partial")),
            })
        elif kind == "tool":
            result = event.get("result") or {}
            out.append({
                "role": "tool", "name": str(event.get("name", "")), "ok": result.get("ok"),
                "text": str(result.get("output", "")), "entry_id": entry.entry_id,
                "call_id": str(event.get("call_id", "")),
            })
        elif kind == "turn":
            out.append({
                "role": "turn", "text": f"{event.get('status', '')}: {event.get('reason', '')}",
                "entry_id": entry.entry_id,
            })
        elif kind in {"conversation_branch", "imported", "model", "obligations"}:
            payload = {k: v for k, v in event.items() if k not in {"entry_id", "parent_id", "timestamp"}}
            out.append({"role": "notice", "text": json.dumps(payload, ensure_ascii=False), "entry_id": entry.entry_id})
    return out


def tree(session: Any) -> dict[str, Any]:
    """Every branch of the conversation, not just the active one, for the picker panel."""
    history = session.conversation_tree()
    entries = []
    for entry in history.entries:
        event = entry.event()
        lesson = ""
        if event.get("type") == "conversation_branch" and isinstance(event.get("summary"), dict):
            lesson = str(event["summary"].get("text", ""))
        entries.append({
            "entry_id": entry.entry_id, "parent_id": entry.parent_id,
            "type": str(event.get("type", "")), "lesson": lesson,
        })
    return {"active_leaf": history.active_leaf, "entries": entries}


def jobs(session: Any) -> dict[str, Any]:
    """Delegation status and spend, read from the session's own delegation port."""
    delegations = session.delegations
    status = delegations.status()
    found = delegations.tree()
    rows = []
    for delegation in found.delegations.values():
        if delegation.id == "root":
            continue
        parent = next((pid for pid in found.delegations if delegation.id in found.children(pid)), "root")
        rows.append({
            "id": delegation.id, "state": delegation.state.value,
            "objective": delegation.spec.objective, "parent": parent,
        })
    pending = [
        {"id": item.id, "summary": item.summary, "actionable": bool(item.actionable)}
        for item in delegations.attention().pending("human")
    ]
    return {
        "counts": dict(status.get("counts", {})), "root": status.get("root") or {},
        "delegations": rows, "attention": pending, "usage": session.usage.summary(),
    }


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
    out: dict[str, list[str]] = {"lean": [], "tex": [], "pdf": []}
    for tree_name in SERVED_TREES:
        root = problem / tree_name
        if root.is_dir():
            out[tree_name] = sorted(
                path.relative_to(problem).as_posix()
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink() and ".build" not in path.parts
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
        raise ValueError("only lean/ and tex/ are served as text")
    path = confine(problem, relative)
    data = path.read_bytes()
    truncated = len(data) > TEXT_LIMIT
    return {"path": relative, "text": data[:TEXT_LIMIT].decode("utf-8", errors="replace"), "truncated": truncated}


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


def graph(problem: Path) -> dict[str, Any]:
    """The project ledger's current heads and relations, with staleness against them.

    An edge is stale when either endpoint's pinned digest no longer matches
    that item's current head -- the relation was recorded against a version of
    the item that has since been revised, and nothing has re-checked it.
    """
    snapshot = LedgerStore(problem).read()
    heads = {item.id: item for item in snapshot.current(ProjectItem)}
    counts: dict[str, Counter[str]] = {}
    for obligation in snapshot.current(Obligation):
        bucket = counts.setdefault(obligation.item.id, Counter())
        status = obligation.status.value
        bucket["open" if status == "open" else "resolved" if status == "resolved" else "other"] += 1
    nodes = []
    for item in heads.values():
        statement = item.statement or ""
        bucket = counts.get(item.id, Counter())
        nodes.append({
            "id": item.id, "digest": item.digest, "kind": item.kind.value, "name": item.name,
            "statement": statement[:STATEMENT_LIMIT], "origin": item.origin.value,
            "evidence": sorted({evidence.kind.value for evidence in item.evidence}),
            "artifacts": [artifact.uri for artifact in item.artifacts],
            "research": item.research.status if item.research else None,
            "obligations": {"open": bucket["open"], "resolved": bucket["resolved"], "other": bucket["other"]},
        })
    edges = []
    for relation in snapshot.current(Relation):
        def stale(ref) -> bool:
            head = heads.get(ref.id)
            return head is None or head.digest != ref.digest

        edges.append({
            "id": relation.id, "kind": relation.kind.value, "source": relation.source.id,
            "target": relation.target.id, "evidence": sorted({evidence.kind.value for evidence in relation.evidence}),
            "stale": stale(relation.source) or stale(relation.target),
        })
    return {"nodes": nodes, "edges": edges, "revision": snapshot.revision}
