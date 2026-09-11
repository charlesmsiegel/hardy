"""Model-facing source tools: bounded, provenance-carrying, and limited to seeded sources.

A session may read only the sources its problem was seeded with; seeding is
the explicit grant, made by the user with `hardy library seed`. Every answer
is JSON naming the artifact, representation, tree, node and span it came
from, with a truncation flag, and is itself cut to the observation budget so
that a 500-page book reaches the model as a map and exact excerpts, never as
its text. No tool here writes anything.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.foundation import truncation
from hardy.foundation.paths import global_library
from hardy.foundation.values import ToolResult

from .contracts import NodeKind
from .library import ManagedLibrary
from .reading import Delivery, SourceReader, SourceUnavailable
from .seeds import SeedStore

SOURCE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sources",
            "description": "List the literature sources this problem was seeded with: exact artifact digests, editions, and a compact top-level map of each. Nothing else is readable from here.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_map",
            "description": "The structural map of one seeded source: chapters, sections and statement-like units with their printed numbers, to a bounded depth. Read units with read_source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "artifact digest prefix or seed id"},
                    "depth": {"type": "integer", "description": "container depth to expand, default 2"},
                    "node": {"type": "string", "description": "list only the children of this node"},
                },
                "required": ["source"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_source_statements",
            "description": "Find theorem-like units in a seeded source by printed number, kind, or words in the statement. An empty result means nothing was recovered, not that the source has none.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "number": {"type": "string", "description": "printed number such as 4.2 or II.5.8"},
                    "kind": {"type": "string", "description": "theorem, lemma, proposition, corollary, definition, example, exercise, remark"},
                    "query": {"type": "string", "description": "words that must all appear in the statement"},
                    "limit": {"type": "integer"},
                },
                "required": ["source"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_source",
            "description": "Search a seeded source's recovered units. Exact ids and printed numbers outrank word matches; word matches are labelled fuzzy.",
            "parameters": {
                "type": "object",
                "properties": {"source": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["source", "query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source",
            "description": "Read one unit of a seeded source: the statement alone, the proof alone, the whole unit, or the unit with surrounding context. Every answer names the exact artifact, representation and span it quotes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "node": {"type": "string"},
                    "part": {"type": "string", "enum": ["statement", "proof", "node", "context"], "description": "default statement"},
                    "start": {"type": "integer", "description": "character offset to resume a truncated read"},
                },
                "required": ["source", "node"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_source_region",
            "description": "Where a unit sits in the original artifact: page indices, printed page labels when the file declares them, and fragment origins when the extraction recorded them.",
            "parameters": {
                "type": "object",
                "properties": {"source": {"type": "string"}, "node": {"type": "string"}},
                "required": ["source", "node"],
                "additionalProperties": False,
            },
        },
    },
]
SOURCE_TOOL_NAMES = tuple(spec["function"]["name"] for spec in SOURCE_TOOLS)
MAX_MAP_NODES = 200
MAX_LIST_ENTRIES = 12


def library_root() -> Path:
    """Where the machine's library lives; a seam tests redirect."""
    return global_library()


class SourceToolRuntime:
    def __init__(self, seeds: SeedStore, library: ManagedLibrary, *, observation_bytes: int = truncation.DEFAULT_BYTE_LIMIT) -> None:
        self.seeds = seeds
        self.library = library
        self.observation_bytes = observation_bytes
        self.reader = SourceReader(library, max_characters=max(256, observation_bytes // 4))

    # --- dispatch -----------------------------------------------------------

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            if name == "list_sources":
                return self.list_sources()
            if name == "source_map":
                return self.source_map(str(arguments["source"]), depth=int(arguments.get("depth", 2) or 2), node=arguments.get("node"))
            if name == "find_source_statements":
                return self.find_statements(str(arguments["source"]), number=arguments.get("number"), kind=arguments.get("kind"),
                                            query=arguments.get("query"), limit=int(arguments.get("limit", 20) or 20))
            if name == "search_source":
                return self.search(str(arguments["source"]), str(arguments["query"]), limit=int(arguments.get("limit", 10) or 10))
            if name == "read_source":
                return self.read(str(arguments["source"]), str(arguments["node"]), part=str(arguments.get("part") or "statement"),
                                 start=max(0, int(arguments.get("start", 0) or 0)))
            if name == "show_source_region":
                return self.region(str(arguments["source"]), str(arguments["node"]))
        except SourceUnavailable as error:
            return ToolResult(False, self._bounded(str(error)))
        except (KeyError, TypeError, ValueError) as error:
            return ToolResult(False, self._bounded(f"{type(error).__name__}: {error}"))
        return ToolResult(False, f"unknown tool: {name}")

    # --- resolution ---------------------------------------------------------

    def _resolve(self, source: str) -> tuple[str, str | None]:
        """The seeded artifact and the tree the seed pins, if it pins one.

        A seed that names a tree grants that tree, not whichever tree the
        library prefers today; a later rebuild must not silently change what
        the session reads.
        """
        seeds = self.seeds.by_prefix(source)
        if not seeds:
            known = ", ".join(f"{s.artifact_sha256[:12]} ({s.id})" for s in self.seeds.seeds()) or "none"
            raise SourceUnavailable(
                f"{source!r} is not a source this problem was seeded with; seeded sources: {known}. "
                "Ask the user to run `hardy library seed <digest>` to grant one."
            )
        digests = {s.artifact_sha256 for s in seeds}
        if len(digests) > 1:
            raise SourceUnavailable(f"{source!r} matches several seeded sources; give more of the digest")
        trees = {s.tree for s in seeds if s.tree}
        if len(trees) > 1:
            raise SourceUnavailable(f"{source!r} is seeded under several trees ({', '.join(sorted(trees))}); unseed one first")
        return digests.pop(), (trees.pop() if trees else None)

    # --- operations ---------------------------------------------------------

    def list_sources(self) -> ToolResult:
        entries = []
        for seed in self.seeds.seeds()[:MAX_LIST_ENTRIES]:
            source_map = self.reader.source_map(seed.artifact_sha256, depth=1, max_nodes=MAX_MAP_NODES, tree=seed.tree)
            entries.append({
                "seed": seed.id, "artifact": seed.artifact_sha256, "edition": seed.edition or source_map.edition, "title": source_map.title,
                "priority": seed.priority, "intent": seed.intent, "pages": source_map.page_count, "nodes": source_map.node_count,
                "statements": source_map.statement_count, "tree": source_map.tree, "unavailable": source_map.unavailable,
                "map": [_entry(e) for e in source_map.entries[:24]], "map_truncated": source_map.truncated or len(source_map.entries) > 24,
            })
        note = "Seeded sources are readable through source_map, find_source_statements, search_source and read_source. A source not listed here is not readable." if entries else "This problem has no seeded sources; ask the user to run `hardy library seed <digest>`."
        return self._json({"sources": entries, "note": note})

    def source_map(self, source: str, *, depth: int = 2, node: str | None = None) -> ToolResult:
        sha, tree = self._resolve(source)
        if node:
            children = self.reader.list_children(sha, node, tree=tree)
            return self._json({"artifact": sha, "tree": tree, "node": node, "children": [_node_summary(n) for n in children[:MAX_MAP_NODES]],
                               "truncated": len(children) > MAX_MAP_NODES})
        source_map = self.reader.source_map(sha, depth=max(1, min(depth, 4)), max_nodes=MAX_MAP_NODES, tree=tree)
        if source_map.unavailable:
            return ToolResult(False, self._bounded(source_map.unavailable))
        return self._json({
            "artifact": sha, "tree": source_map.tree, "edition": source_map.edition, "title": source_map.title, "pages": source_map.page_count,
            "nodes": source_map.node_count, "statements": source_map.statement_count, "entries": [_entry(e) for e in source_map.entries],
            "truncated": source_map.truncated,
        })

    def find_statements(self, source: str, *, number: str | None, kind: str | None, query: str | None, limit: int) -> ToolResult:
        sha, tree = self._resolve(source)
        kinds: tuple[NodeKind, ...] = ()
        if kind:
            try:
                kinds = (NodeKind(kind.lower()),)
            except ValueError:
                return ToolResult(False, f"unknown statement kind {kind!r}")
        found = self.reader.find_statements(sha, kinds=kinds, number=number, query=query, limit=max(1, min(limit, 100)), tree=tree)
        note = "" if found else "no matching unit was recovered from this source; the source may still contain one the extraction missed"
        return self._json({"artifact": sha, "tree": tree, "statements": [_node_summary(n) for n in found], "note": note})

    def search(self, source: str, query: str, *, limit: int) -> ToolResult:
        sha, tree = self._resolve(source)
        hits = self.reader.search_text(sha, query, limit=max(1, min(limit, 50)), tree=tree)
        return self._json({"artifact": sha, "tree": tree, "query": query, "hits": [h.model_dump(mode="json") for h in hits],
                           "note": "ranks 0-2 are exact (id, printed number, title); 3-4 are fuzzy word matches and are leads, not identity"})

    def read(self, source: str, node: str, *, part: str, start: int) -> ToolResult:
        sha, tree = self._resolve(source)
        if part == "statement":
            delivery = self.reader.read_statement(sha, node, tree=tree)
        elif part == "proof":
            delivery = self.reader.read_proof(sha, node, tree=tree)
        elif part == "context":
            delivery = self.reader.read_context(sha, node, tree=tree)
        elif part == "node":
            delivery = self.reader.read_node(sha, node, start=start, tree=tree)
        else:
            return ToolResult(False, f"unknown part {part!r}; use statement, proof, node or context")
        return self._delivery(delivery, part)

    def region(self, source: str, node: str) -> ToolResult:
        sha, tree = self._resolve(source)
        anchors = self.reader.original_region(sha, node, tree=tree)
        labels = dict(self.library.representations.page_labels(sha))
        pages = sorted({a.locator.page_index for a in anchors if hasattr(a.locator, "page_index")})
        return self._json({
            "artifact": sha, "tree": tree, "node": node, "page_indices": pages, "printed_labels": {str(p): labels[p] for p in pages if p in labels},
            "anchors": [a.model_dump(mode="json") for a in anchors[:64]], "truncated": len(anchors) > 64,
            "note": "page_indices count from 0 in this artifact; printed_labels are what the file declares, when it declares any",
        })

    # --- rendering ----------------------------------------------------------

    def _delivery(self, delivery: Delivery, part: str) -> ToolResult:
        if delivery.unavailable:
            return ToolResult(False, self._bounded(delivery.unavailable))
        payload = {
            "artifact": delivery.artifact_sha256, "representation": delivery.representation, "tree": delivery.tree, "node": delivery.node,
            "part": part, "span": delivery.span.model_dump(mode="json") if delivery.span else None,
            "quality": delivery.quality.status if delivery.quality else None, "truncated": delivery.truncated,
            "total_characters": delivery.total_characters, "text": delivery.text,
        }
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        budget = self.observation_bytes

        def over() -> bool:
            return len(rendered.encode("utf-8")) > budget

        while over() and payload["text"]:
            payload["text"] = payload["text"][: max(0, len(payload["text"]) // 2)]
            payload["truncated"] = True
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if over() and isinstance(payload["span"], dict) and payload["span"].get("anchors"):
            # The span's anchors are the bulk of a small delivery; the ranges
            # and content digest keep the provenance, the anchors are elided.
            payload["span"] = {**payload["span"], "anchors": [], "anchors_elided": True}
            rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if over():
            return ToolResult(False, self._bounded(
                f"the delivery for node {delivery.node} of {delivery.artifact_sha256[:12]} does not fit the observation budget of {budget} bytes "
                "even with its text removed; raise the budget or read a smaller part"))
        return ToolResult(True, rendered)

    def _json(self, payload: dict[str, Any]) -> ToolResult:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(rendered.encode("utf-8")) <= self.observation_bytes:
            return ToolResult(True, rendered)
        for key in ("map", "entries", "children", "statements", "hits", "anchors", "sources"):
            if key in payload and isinstance(payload[key], list):
                while payload[key] and len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")) > self.observation_bytes:
                    payload[key] = payload[key][: max(0, len(payload[key]) * 2 // 3)]
                    payload["truncated"] = True
            for entry in payload.get("sources", []) if key == "sources" else []:
                while entry.get("map") and len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")) > self.observation_bytes:
                    entry["map"] = entry["map"][: max(0, len(entry["map"]) * 2 // 3)]
                    entry["map_truncated"] = True
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(rendered.encode("utf-8")) > self.observation_bytes:
            return ToolResult(False, self._bounded("the answer does not fit the observation budget even with every list emptied"))
        return ToolResult(True, rendered)

    def _bounded(self, message: str) -> str:
        cut = truncation.truncate(message, keep="head", byte_limit=self.observation_bytes, line_limit=None)
        return cut.text if not cut.truncated else cut.text + "\n... [refusal shortened]"


def _entry(entry: Any) -> dict[str, Any]:
    return {"node": entry.node, "kind": entry.kind.value, "number": entry.number, "title": entry.title, "depth": entry.depth,
            "children": entry.children, "pages": list(entry.pages), "boundary": entry.boundary_status}


def _node_summary(node: Any) -> dict[str, Any]:
    return {"node": node.id, "kind": node.kind.value, "number": node.number, "title": node.title, "parent": node.parent,
            "boundary": node.boundary_status, "pages": sorted({a.locator.page_index for a in node.span.anchors if hasattr(a.locator, "page_index")})}


def build_runtime(problem: Path, *, library: ManagedLibrary | None = None, observation_bytes: int = truncation.DEFAULT_BYTE_LIMIT,
                  make_library: Callable[[], ManagedLibrary] | None = None) -> SourceToolRuntime:
    held = library if library is not None else (make_library() if make_library else ManagedLibrary(library_root()))
    return SourceToolRuntime(SeedStore(problem), held, observation_bytes=observation_bytes)
