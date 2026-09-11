"""Bounded reading and search over admitted sources, with provenance on every answer.

Every delivery names the exact artifact, representation, span and tree it
came from, says whether it was cut, and carries the representation's quality
so a caller can tell clean native text from a poor extraction. A source
whose bytes are missing on this machine is identifiable and reports
`unavailable`; nothing here pretends it never existed. Ranking prefers exact
ids and printed numbers over words, and words over nothing; a fuzzy hit is
labelled as one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from hardy.foundation.values import FrozenModel

from .contracts import (
    CONTAINER_KINDS,
    STATEMENT_KINDS,
    NodeKind,
    PageRegion,
    QualityProfile,
    RepresentationSpan,
    SourceAnchor,
    SourceEdgeKind,
    SourceNode,
    SourceSpan,
    SourceTree,
)
from .library import ManagedLibrary
from .locators import SpanError, covering, resolve_span

ALIAS = re.compile(
    r"^(?:(?P<kind>theorem|lemma|proposition|corollary|definition|claim|conjecture|example|exercise|remark|construction|solution|chapter|section)\s+)?"
    r"(?P<number>[A-Z]?[IVXLC0-9]+(?:\.[0-9]+)*)$",
    re.IGNORECASE,
)
WORD = re.compile(r"[A-Za-z0-9]+")


class SourceUnavailable(ValueError):
    """The artifact is identifiable but its bytes or tree are not readable here."""


class MapEntry(FrozenModel):
    node: str
    kind: NodeKind
    number: str | None = None
    title: str | None = None
    depth: int
    children: int
    pages: tuple[int, ...] = ()
    boundary_status: str


class SourceMap(FrozenModel):
    artifact_sha256: str
    tree: str | None
    edition: str | None = None
    title: str | None = None
    page_count: int | None = None
    node_count: int = 0
    statement_count: int = 0
    entries: tuple[MapEntry, ...] = ()
    truncated: bool = False
    unavailable: str | None = None


class Delivery(FrozenModel):
    text: str
    artifact_sha256: str
    representation: str | None = None
    span: SourceSpan | None = None
    tree: str | None = None
    node: str | None = None
    truncated: bool = False
    total_characters: int = 0
    quality: QualityProfile | None = None
    anchors: tuple[SourceAnchor, ...] = ()
    unavailable: str | None = None


class SearchHit(FrozenModel):
    node: str
    kind: NodeKind
    number: str | None = None
    title: str | None = None
    rank: int
    match: str
    snippet: str


@dataclass(frozen=True)
class _Loaded:
    tree: SourceTree
    texts: dict[str, str]
    representation: str


class SourceReader:
    def __init__(self, library: ManagedLibrary, *, max_characters: int = 8_192) -> None:
        self.library = library
        self.max_characters = max_characters

    # --- loading ------------------------------------------------------------

    def _load(self, sha256: str, tree_id: str | None = None) -> _Loaded:
        availability = self.library.availability(sha256)
        if availability.status != "available":
            raise SourceUnavailable(f"artifact {sha256} is {availability.status}: {availability.detail}")
        tree = self.library.trees.get(sha256, tree_id) if tree_id else self.library.trees.preferred(sha256)
        if tree is None:
            raise SourceUnavailable(f"artifact {sha256} has no admitted source tree yet")
        texts = self.library.representations.texts(sha256)
        return _Loaded(tree=tree, texts=texts, representation=tree.representations[0])

    def _unavailable(self, sha256: str, error: Exception) -> Delivery:
        return Delivery(text="", artifact_sha256=sha256, unavailable=str(error))

    # --- navigation ---------------------------------------------------------

    def source_map(self, sha256: str, *, depth: int = 2, max_nodes: int = 200, tree: str | None = None) -> SourceMap:
        try:
            loaded = self._load(sha256, tree)
        except SourceUnavailable as error:
            return SourceMap(artifact_sha256=sha256, tree=None, unavailable=str(error))
        edition = self.library.catalog.edition_of(sha256)
        title = None
        if edition is not None:
            work = self.library.catalog.snapshot().work(edition.work)
            title = work.title if work else None
        entries: list[MapEntry] = []
        truncated = False

        def walk(parent: str | None, level: int) -> None:
            nonlocal truncated
            for node in loaded.tree.children(parent):
                if level >= depth and node.kind not in CONTAINER_KINDS:
                    continue
                if len(entries) >= max_nodes:
                    truncated = True
                    return
                children = loaded.tree.children(node.id)
                entries.append(MapEntry(node=node.id, kind=node.kind, number=node.number, title=node.title, depth=level,
                                        children=len(children), pages=_pages(node), boundary_status=node.boundary_status))
                if level + 1 < depth or node.kind in CONTAINER_KINDS:
                    walk(node.id, level + 1)

        walk(None, 0)
        return SourceMap(
            artifact_sha256=sha256, tree=loaded.tree.id, edition=edition.id if edition else None, title=title,
            page_count=self.library.representations.page_count(sha256), node_count=len(loaded.tree.nodes),
            statement_count=sum(1 for n in loaded.tree.nodes if n.kind in STATEMENT_KINDS), entries=tuple(entries), truncated=truncated,
        )

    def list_children(self, sha256: str, node: str, *, tree: str | None = None) -> tuple[SourceNode, ...]:
        return self._load(sha256, tree).tree.children(node)

    def find_statements(
        self, sha256: str, *, kinds: tuple[NodeKind, ...] = (), number: str | None = None, query: str | None = None,
        limit: int = 50, tree: str | None = None,
    ) -> tuple[SourceNode, ...]:
        loaded = self._load(sha256, tree)
        words = {w.lower() for w in WORD.findall(query or "")}
        found = []
        for node in loaded.tree.nodes:
            if node.kind not in STATEMENT_KINDS:
                continue
            if kinds and node.kind not in kinds:
                continue
            if number and node.number != number:
                continue
            if words:
                text = _text_of(node, loaded).lower()
                if not all(w in text for w in words):
                    continue
            found.append(node)
        return tuple(found[:limit])

    def resolve_alias(self, sha256: str, alias: str, *, tree: str | None = None) -> tuple[SourceNode, ...]:
        loaded = self._load(sha256, tree)
        match = ALIAS.match(alias.strip())
        if match is None:
            return ()
        kind = (match.group("kind") or "").lower()
        number = match.group("number")
        found = [n for n in loaded.tree.nodes if n.number == number and (not kind or n.kind.value == kind)]
        return tuple(found)

    def search_text(self, sha256: str, query: str, *, limit: int = 20, tree: str | None = None) -> tuple[SearchHit, ...]:
        loaded = self._load(sha256, tree)
        hits: list[SearchHit] = []
        stripped = query.strip()
        words = [w.lower() for w in WORD.findall(stripped)]
        exact_alias = {n.id for n in self.resolve_alias(sha256, stripped, tree=tree)} if stripped else set()
        for node in loaded.tree.nodes:
            text = _text_of(node, loaded)
            lowered = text.lower()
            if node.id == stripped:
                rank, why = 0, "exact node id"
            elif node.id in exact_alias:
                rank, why = 1, "printed number"
            elif node.title and node.title.lower() == stripped.lower():
                rank, why = 2, "exact title"
            elif words and all(w in lowered for w in words):
                rank, why = 3, "all words present (fuzzy)"
            elif words and any(w in lowered for w in words):
                rank, why = 4, "some words present (fuzzy)"
            else:
                continue
            hits.append(SearchHit(node=node.id, kind=node.kind, number=node.number, title=node.title, rank=rank, match=why,
                                  snippet=text[:160]))
        hits.sort(key=lambda h: (h.rank, h.node))
        return tuple(hits[:limit])

    # --- reading ------------------------------------------------------------

    def read_node(self, sha256: str, node: str, *, start: int = 0, tree: str | None = None) -> Delivery:
        try:
            loaded = self._load(sha256, tree)
            record = loaded.tree.node(node)
        except (SourceUnavailable, KeyError) as error:
            return self._unavailable(sha256, error)
        return self._deliver(loaded, record.span, node=record.id, start=start)

    def read_statement(self, sha256: str, node: str, *, tree: str | None = None) -> Delivery:
        try:
            loaded = self._load(sha256, tree)
            record = loaded.tree.node(node)
        except (SourceUnavailable, KeyError) as error:
            return self._unavailable(sha256, error)
        return self._deliver(loaded, record.statement_span or record.span, node=record.id)

    def read_proof(self, sha256: str, node: str, *, tree: str | None = None) -> Delivery:
        try:
            loaded = self._load(sha256, tree)
            statement = loaded.tree.node(node)
        except (SourceUnavailable, KeyError) as error:
            return self._unavailable(sha256, error)
        if statement.kind is NodeKind.PROOF:
            return self._deliver(loaded, statement.span, node=statement.id)
        proofs = [e.source for e in loaded.tree.edges if e.kind is SourceEdgeKind.PROOF_OF and e.target == node]
        if not proofs:
            return Delivery(text="", artifact_sha256=sha256, tree=loaded.tree.id, node=node,
                            unavailable="no proof node is recorded for this statement; that is not evidence the source has none")
        proof = loaded.tree.node(proofs[0])
        return self._deliver(loaded, proof.span, node=proof.id)

    def read_context(self, sha256: str, node: str, *, before: int = 400, after: int = 400, tree: str | None = None) -> Delivery:
        try:
            loaded = self._load(sha256, tree)
            record = loaded.tree.node(node)
        except (SourceUnavailable, KeyError) as error:
            return self._unavailable(sha256, error)
        part = record.span.ranges[0]
        text = loaded.texts[part.representation]
        start, end = max(0, part.start - before), min(len(text), part.end + after)
        window = RepresentationSpan(representation=part.representation, start=start, end=end)
        return self._deliver(loaded, window, node=record.id)

    def read_span(self, span: SourceSpan, *, tree: str | None = None) -> Delivery:
        try:
            loaded = self._load(span.artifact_sha256, tree)
        except SourceUnavailable as error:
            return self._unavailable(span.artifact_sha256, error)
        return self._deliver(loaded, span, node=span.node)

    def original_region(self, sha256: str, node: str, *, tree: str | None = None) -> tuple[SourceAnchor, ...]:
        loaded = self._load(sha256, tree)
        record = loaded.tree.node(node)
        anchors = list(record.span.anchors)
        part = record.span.ranges[0]
        for mapping in self.library.representations.mappings(sha256, left=part.representation):
            for locator in covering(mapping, part):
                if isinstance(locator, PageRegion) and locator.precision != "page":
                    anchors.append(SourceAnchor(artifact_sha256=sha256, locator=locator, derivation=f"mapping {mapping.id}"))
        for mapping in self.library.representations.mappings(sha256, right=part.representation):
            for locator in covering(mapping, part):
                if isinstance(locator, RepresentationSpan):
                    for inner in self.library.representations.mappings(sha256, left=locator.representation):
                        for region in covering(inner, locator):
                            if isinstance(region, PageRegion) and region.precision != "page":
                                anchors.append(SourceAnchor(artifact_sha256=sha256, locator=region, derivation=f"mapping {mapping.id} then {inner.id}"))
        return tuple(anchors)

    def _deliver(self, loaded: _Loaded, span: SourceSpan | RepresentationSpan, *, node: str | None, start: int = 0) -> Delivery:
        sha256 = loaded.tree.artifact_sha256
        try:
            content = resolve_span(span, loaded.texts)
        except SpanError as error:
            return Delivery(text="", artifact_sha256=sha256, tree=loaded.tree.id, node=node, unavailable=str(error))
        representation = span.ranges[0].representation if isinstance(span, SourceSpan) else span.representation
        record = self.library.representations.get(sha256, representation)
        window = content[start:start + self.max_characters]
        truncated = start > 0 or len(content) > start + len(window)
        anchors = span.anchors if isinstance(span, SourceSpan) else ()
        return Delivery(text=window, artifact_sha256=sha256, representation=representation,
                        span=span if isinstance(span, SourceSpan) else None, tree=loaded.tree.id, node=node,
                        truncated=truncated, total_characters=len(content), quality=record.quality, anchors=anchors)


def _pages(node: SourceNode) -> tuple[int, ...]:
    return tuple(sorted({a.locator.page_index for a in node.span.anchors if isinstance(a.locator, PageRegion)}))


def _text_of(node: SourceNode, loaded: _Loaded) -> str:
    try:
        return resolve_span(node.span, loaded.texts)
    except SpanError:
        return ""
