"""A conversation is an append-only tree, not a mathematical rollback.

The journal owns the cursor. A branch transition names both the old cursor and
an earlier parent; replay follows parents, never chronological siblings. Content
hashes identify new entries, while legacy records receive stable virtual IDs.
Visible replay omits superseded checkpoints on that path and opaque provider
state. Human lessons remain attributed, unverified text, never proof evidence.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from hardy.documents.export import _superseded

REPLAY_LIMIT = 1 << 20


def encoded(event: Any) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identify(event: dict[str, Any]) -> str:
    return "entry:" + hashlib.sha256(encoded(event).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HistoryEntry:
    entry_id: str
    parent_id: str | None
    payload: str

    def event(self) -> dict[str, Any]:
        return json.loads(self.payload)


@dataclass(frozen=True)
class HistorySnapshot:
    entries: tuple[HistoryEntry, ...]
    active_leaf: str | None
    epoch: str | None

    def path(self) -> tuple[HistoryEntry, ...]:
        by_id = {entry.entry_id: entry for entry in self.entries}
        found = []
        cursor = self.active_leaf
        while cursor is not None:
            entry = by_id[cursor]
            found.append(entry)
            cursor = entry.parent_id
        return tuple(reversed(found))

    def replay(self) -> str:
        # Result events are provider accounting, deliberately withheld from
        # model context. Prior replay envelopes are excluded to avoid nesting.
        events = [event for entry in self.path()
                  if (event := entry.event()).get("type") not in {"branch_context", "result"}]
        superseded = _superseded(events)
        text = encoded([event for index, event in enumerate(events) if index not in superseded])
        if len(text.encode("utf-8")) > REPLAY_LIMIT:
            raise ValueError("Selected conversation exceeds the visible replay limit; choose an earlier parent.")
        return text


class History:
    """Incremental validated journal reader; snapshots expose no mutable state."""

    def __init__(self, events: Iterable[dict[str, Any]] = ()):
        self.entries: dict[str, HistoryEntry] = {}
        self.active_leaf: str | None = None
        self.epoch: str | None = None
        for event in events:
            self.append(event)

    def snapshot(self) -> HistorySnapshot:
        return HistorySnapshot(tuple(self.entries.values()), self.active_leaf, self.epoch)

    def validate(self, event: dict[str, Any]) -> HistoryEntry:
        body = dict(event)
        if "entry_id" not in body and "parent_id" not in body:
            if body.get("type") == "conversation_branch":
                raise ValueError("Branch transition is missing its identity.")
            identifier = "legacy:" + hashlib.sha256(encoded([len(self.entries), body]).encode("utf-8")).hexdigest()
            parent = self.active_leaf
        else:
            identifier = body.pop("entry_id", None)
            if identifier != identify(body) or "parent_id" not in body:
                raise ValueError("Transcript entry identity does not match its content.")
            parent = body["parent_id"]
            if parent is not None and (not isinstance(parent, str) or parent not in self.entries):
                raise ValueError(f"Unknown conversation parent: {parent!r}")
            if body.get("type") == "conversation_branch":
                if "from_leaf" not in body or body["from_leaf"] != self.active_leaf:
                    raise ValueError("Conversation changed since the selected cursor was read.")
                if body.get("action") not in ("fork", "abandon"):
                    raise ValueError("Unknown conversation branch action.")
                summary = body.get("summary")
                if body["action"] == "abandon" and (
                    not isinstance(summary, dict) or not isinstance(summary.get("text"), str)
                    or not summary["text"].strip() or summary.get("author") != "human"
                    or summary.get("status") != "unverified" or summary.get("from_leaf") != self.active_leaf
                ):
                    raise ValueError("Abandoning a branch requires an attributed, unverified human lesson.")
            elif parent != self.active_leaf:
                raise ValueError("Transcript event does not continue the active conversation.")
        if identifier in self.entries:
            raise ValueError("Duplicate transcript entry identity.")
        return HistoryEntry(identifier, parent, encoded({**event, "entry_id": identifier, "parent_id": parent}))

    def append(self, event: dict[str, Any]) -> None:
        entry = self.validate(event)
        self.entries[entry.entry_id] = entry
        self.active_leaf = entry.entry_id
        if event.get("type") == "conversation_branch":
            self.epoch = entry.entry_id
