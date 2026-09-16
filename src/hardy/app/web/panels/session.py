"""Read-only views of the live session: summary, transcript, branch tree, and jobs."""

from __future__ import annotations

import json
from typing import Any

from hardy.documents.export import _superseded


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
            # A line Hardy started a turn with is Hardy's, and drawn as such.
            role = "hardy" if event.get("author") == "hardy" else "user"
            out.append({"role": role, "text": str(event.get("message", {}).get("content", "")), "entry_id": entry.entry_id})
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
