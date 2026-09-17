"""A problem's chats: `main` is the legacy transcript, the rest live under `chats/<id>/`."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hardy.foundation.files import LayoutError, guard_for, read_text
from hardy.workflows.layout import CHAT_META, CHATS_DIR, DEFAULT_CHAT, TRANSCRIPT, validate_chat

SCHEMA = "hardy.chat/v1"


@dataclass(frozen=True)
class Chat:
    id: str
    title: str
    created: float

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "created": self.created}


def chat_id_for(title: str, taken: Iterable[str]) -> str:
    """A slug from the title, suffixed until it is free; never `main`."""
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-") or "chat"
    used = set(taken) | {DEFAULT_CHAT}
    candidate, count = base, 1
    while candidate in used:
        count += 1
        candidate = f"{base}-{count}"
    return validate_chat(candidate)


def _read(problem: Path, chat_id: str) -> dict | None:
    """`chat_id`'s `chat.json`, proven to be that file and not a symlink out.

    Goes through `hardy.foundation.files.read_text`, not `Path.read_text`, for
    the same reason every project read does: `chats/escaped/chat.json ->
    ~/notes/anything.json` in a cloned repository would otherwise have this
    module report on a file outside the problem entirely. `guard_for` proves
    every component on the way down -- `chats/`, `chats/<id>/`, and the leaf --
    so a symlinked `chats/` directory or a symlinked chat directory is refused
    here too, not only the leaf file.
    """
    try:
        data = json.loads(read_text(problem, f"{CHATS_DIR}/{chat_id}/{CHAT_META}"))
    except (LayoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def list_chats(problem: Path) -> list[Chat]:
    found = [Chat(DEFAULT_CHAT, DEFAULT_CHAT, 0.0)]
    root = problem / CHATS_DIR
    if root.is_dir() and not root.is_symlink():
        others = []
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            # A planted `chats/main/chat.json` would otherwise list `main`
            # twice, once as the legacy transcript above and once as itself --
            # two rows the browser draws as two chats, both opening the same
            # one, and the second carrying whatever title the file chose.
            if child.name == DEFAULT_CHAT:
                continue
            meta = _read(problem, child.name)
            if meta is None:
                continue
            try:
                created = float(meta.get("created") or 0.0)
            except (TypeError, ValueError):
                continue
            others.append(Chat(child.name, str(meta.get("title") or child.name), created))
        found.extend(sorted(others, key=lambda chat: (chat.created, chat.id)))
    return found


def _write(problem: Path, chat: Chat) -> None:
    """Write `chat`'s metadata, proving every component on the way down.

    One guard on `chats/<id>` was not enough and reading already knew it: that
    rule is "resolved, this is its own parent's immediate child", which
    `chats -> /elsewhere` satisfies, so a cloned repository carrying that link
    had `create_chat` write into a directory outside the problem -- one
    `list_chats` then refuses to read, so the chat existed and was invisible.
    `guard_for` proves `chats/`, `chats/<id>/` and the leaf in turn, which is
    exactly what `_read` does through `read_text`.
    """
    guard, name = guard_for(problem, f"{CHATS_DIR}/{chat.id}/{CHAT_META}", create=True)
    guard.write_json(name, {"schema": SCHEMA, "title": chat.title, "created": chat.created})


def create_chat(problem: Path, title: str) -> Chat:
    title = title.strip()
    if not title:
        raise ValueError("a chat needs a title")
    taken = {chat.id for chat in list_chats(problem)}
    chat = Chat(chat_id_for(title, taken), title, time.time())
    _write(problem, chat)
    return chat


def rename_chat(problem: Path, chat_id: str, title: str) -> Chat:
    chat_id = validate_chat(chat_id)
    title = title.strip()
    if not title:
        raise ValueError("a chat needs a title")
    if chat_id == DEFAULT_CHAT:
        raise ValueError("the main chat cannot be renamed")
    existing = {chat.id: chat for chat in list_chats(problem)}.get(chat_id)
    if existing is None:
        raise ValueError(f"no chat {chat_id!r}")
    renamed = Chat(chat_id, title, existing.created)
    _write(problem, renamed)
    return renamed


def overview(problem: Path) -> list[dict]:
    """Every chat with what Home's table shows: turns and when it last moved.

    `None` is not `0`. A chat whose history cannot be read has no turn count to
    report, and reporting `0` would claim it is empty -- a claim nothing has
    checked. The page prints *not reported* for `None` and `0` for zero.
    """
    rows = []
    for chat in list_chats(problem):
        turns, last = _activity(problem, chat.id)
        rows.append({"id": chat.id, "title": chat.title, "created": chat.created,
                     "turns": turns, "last_activity": last})
    return rows


def _activity(problem: Path, chat_id: str) -> tuple[int | None, float | None]:
    """`(turns, last timestamp)` from a chat's transcript, or `(None, None)`.

    Read as lines rather than replayed through `History`: this counts turns and
    nothing more, and replaying would validate a hash chain -- work the answer
    does not need and a failure mode the answer should not inherit.

    A transcript that cannot be opened at all reports `(None, None)`, which is
    not the same claim as `(0, None)`. A transcript that opens but has a line
    that will not parse must report the same `(None, None)`, not the count of
    whatever did parse (issue #169): a chat interrupted mid-write, or with one
    damaged line anywhere in it, would otherwise undercount by exactly the
    turns on and after the bad line, and that undercount is indistinguishable
    on the page from a genuine count -- the one thing this function exists to
    never hand back. Bailing out at the first bad line, rather than skipping it
    and continuing, also means a `timestamp` never gets to look complete when
    a later, larger one was on a line this function could not read.
    """
    relative = TRANSCRIPT if chat_id == DEFAULT_CHAT else f"{CHATS_DIR}/{chat_id}/{TRANSCRIPT}"
    try:
        text = read_text(problem, relative)
    except (LayoutError, OSError):
        return None, None
    turns, stamp = 0, None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return None, None
        if not isinstance(event, dict):
            return None, None
        if event.get("type") == "turn":
            turns += 1
        at = event.get("timestamp")
        if isinstance(at, (int, float)) and (stamp is None or at > stamp):
            stamp = float(at)
    return turns, stamp
