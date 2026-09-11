"""A rebuildable index over every admitted tree's statement units.

The index accelerates discovery across artifacts and nothing else: every hit
names the artifact, tree and node it came from, and a caller reads the
authoritative record before using it. Deleting `index/` loses nothing; a
rebuild from the stores reproduces it byte for byte, and its digest says
which library state it was built from.
"""
from __future__ import annotations

import json
import re

from hardy.foundation.files import WriteGuard, read_text
from hardy.foundation.values import FrozenModel, json_digest

from .contracts import STATEMENT_KINDS
from .library import ManagedLibrary
from .locators import SpanError, resolve_span

FILE = "statements.json"
WORD = re.compile(r"[A-Za-z0-9]+")


class IndexEntry(FrozenModel):
    artifact_sha256: str
    tree: str
    node: str
    kind: str
    number: str | None = None
    title: str | None = None
    content_sha256: str
    words: tuple[str, ...]


class IndexHit(FrozenModel):
    entry: IndexEntry
    rank: int
    match: str


class SourceIndex:
    def __init__(self, library: ManagedLibrary) -> None:
        self.library = library
        self.directory = library.root / "index"

    def rebuild(self) -> str:
        entries: list[IndexEntry] = []
        for sha in self.library.artifacts.stored():
            tree = self.library.trees.preferred(sha)
            if tree is None:
                continue
            texts = self.library.representations.texts(sha)
            for node in tree.nodes:
                if node.kind not in STATEMENT_KINDS:
                    continue
                span = node.statement_span or node.span
                try:
                    text = resolve_span(span, texts)
                except SpanError:
                    text = ""
                words = tuple(sorted({w.lower() for w in WORD.findall(f"{node.title or ''} {text}")}))
                entries.append(IndexEntry(artifact_sha256=sha, tree=tree.id, node=node.id, kind=node.kind.value, number=node.number,
                                          title=node.title, content_sha256=span.content_sha256, words=words))
        payload = {"schema": "hardy.source-index/v1", "entries": [e.model_dump(mode="json") for e in entries]}
        digest = json_digest(payload)
        payload["digest"] = digest
        WriteGuard(self.directory, create=True).write_bytes(FILE, (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        return digest

    def digest(self) -> str | None:
        try:
            return json.loads(read_text(self.library.root, f"index/{FILE}")).get("digest")
        except (FileNotFoundError, ValueError):
            return None

    def entries(self) -> tuple[IndexEntry, ...]:
        try:
            payload = json.loads(read_text(self.library.root, f"index/{FILE}"))
        except FileNotFoundError:
            return ()
        return tuple(IndexEntry.model_validate(e) for e in payload.get("entries", []))

    def search(self, query: str, *, limit: int = 20) -> tuple[IndexHit, ...]:
        words = {w.lower() for w in WORD.findall(query)}
        number = query.strip()
        hits = []
        for entry in self.entries():
            if entry.node == number:
                hits.append(IndexHit(entry=entry, rank=0, match="exact node id"))
            elif entry.number == number:
                hits.append(IndexHit(entry=entry, rank=1, match="printed number"))
            elif words and words <= set(entry.words):
                hits.append(IndexHit(entry=entry, rank=3, match="all words (fuzzy)"))
        hits.sort(key=lambda h: (h.rank, h.entry.artifact_sha256, h.entry.node))
        return tuple(hits[:limit])

    def clear(self) -> None:
        path = self.directory / FILE
        if path.is_file():
            path.unlink()
