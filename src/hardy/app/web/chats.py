"""A problem's chats: `main` is the legacy transcript, the rest live under `chats/<id>/`."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hardy.foundation.files import WriteGuard
from hardy.workflows.layout import CHAT_META, CHATS_DIR, DEFAULT_CHAT, validate_chat

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


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def list_chats(problem: Path) -> list[Chat]:
    found = [Chat(DEFAULT_CHAT, DEFAULT_CHAT, 0.0)]
    root = problem / CHATS_DIR
    if root.is_dir():
        others = []
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            meta = _read(child / CHAT_META)
            if meta is None:
                continue
            others.append(Chat(child.name, str(meta.get("title") or child.name), float(meta.get("created") or 0.0)))
        found.extend(sorted(others, key=lambda chat: (chat.created, chat.id)))
    return found


def _write(problem: Path, chat: Chat) -> None:
    WriteGuard(problem / CHATS_DIR / chat.id, create=True).write_json(
        CHAT_META, {"schema": SCHEMA, "title": chat.title, "created": chat.created}
    )


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
