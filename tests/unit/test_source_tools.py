"""The source tools read seeded sources only, in bounded, provenance-carrying answers."""

from __future__ import annotations

import json

from pdf_helpers import Page, book_outline, book_pages, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.seeds import SeedStore, new_seed
from hardy.literature.sources.tools import (
    SOURCE_TOOL_NAMES,
    SOURCE_TOOLS,
    SourceToolRuntime,
    build_runtime,
)


def seeded(tmp_path, pages=None, observation_bytes=32 * 1024, **kwargs):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=build_pdf(pages or book_pages(), **kwargs), original_name="book.pdf")).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    problem = tmp_path / "problem"
    store = SeedStore(problem)
    store.add(new_seed(sha, tree=tree.id, priority=1, intent="algebra background"), expected_revision=0)
    return build_runtime(problem, library=lib, observation_bytes=observation_bytes), sha


def test_tool_specs_are_well_formed():
    assert set(SOURCE_TOOL_NAMES) == {"list_sources", "source_map", "find_source_statements", "search_source", "read_source", "show_source_region"}
    for spec in SOURCE_TOOLS:
        assert spec["function"]["parameters"]["additionalProperties"] is False


def test_tools_refuse_unseeded_unknown_source(tmp_path):
    runtime, sha = seeded(tmp_path)
    refused = runtime.call("source_map", {"source": "deadbeef"})
    assert refused.ok is False and "not a source this problem was seeded with" in refused.output
    lib = runtime.library
    other = lib.import_source(ImportRequest(data=build_pdf([Page((("Theorem 9.9. Unseeded.", 72, 720),))]))).outcome.artifact.sha256
    lib.build_tree(other)
    assert runtime.call("read_source", {"source": other[:12], "node": "x"}).ok is False
    empty = build_runtime(tmp_path / "other-problem", library=lib)
    assert "no seeded sources" in json.loads(empty.call("list_sources", {}).output)["note"]


def test_seeding_exposes_a_compact_map_not_the_text(tmp_path):
    pages = []
    for chapter in range(1, 41):
        fragments = [(f"Chapter {chapter}. Topic {chapter}", 72, 740)]
        for k in range(1, 13):
            fragments.append((f"Theorem {chapter}.{k}. Statement number {k} of chapter {chapter} with some words.", 72, 740 - 25 * k))
        pages.append(Page(tuple(fragments)))
    runtime, sha = seeded(tmp_path, pages, observation_bytes=8_000)
    listed = runtime.call("list_sources", {})
    assert listed.ok, listed.output
    payload = json.loads(listed.output)
    (entry,) = payload["sources"]
    assert entry["artifact"] == sha and entry["statements"] == 480 and entry["nodes"] > 500
    assert "Statement number" not in listed.output
    assert len(listed.output.encode("utf-8")) <= 8_000
    source_map = runtime.call("source_map", {"source": sha[:10], "depth": 1})
    assert source_map.ok and "Statement number" not in source_map.output
    assert len(source_map.output.encode("utf-8")) <= 8_000


def test_read_carries_provenance_and_is_bounded(tmp_path):
    runtime, sha = seeded(tmp_path, observation_bytes=1_200)
    found = json.loads(runtime.call("find_source_statements", {"source": sha[:12], "number": "1.2"}).output)
    (node,) = found["statements"]
    assert node["kind"] == "theorem"
    read = runtime.call("read_source", {"source": sha[:12], "node": node["node"]})
    assert read.ok, read.output
    payload = json.loads(read.output)
    assert payload["artifact"] == sha and payload["representation"] and payload["tree"] and payload["span"]["content_sha256"]
    assert payload["text"].startswith("Theorem 1.2.") and payload["quality"] == "ok" and payload["part"] == "statement"
    proof = json.loads(runtime.call("read_source", {"source": sha[:12], "node": node["node"], "part": "proof"}).output)
    assert proof["text"].startswith("Proof.") and proof["node"] != node["node"]
    assert len(read.output.encode("utf-8")) <= 1_200


def test_search_labels_fuzzy_hits_and_region_reports_pages(tmp_path):
    runtime, sha = seeded(tmp_path, labels=["i", "ii", "128"], outline=book_outline())
    hits = json.loads(runtime.call("search_source", {"source": sha[:12], "query": "Lemma 2.1"}).output)["hits"]
    assert hits[0]["number"] == "2.1" and hits[0]["rank"] == 1
    region = json.loads(runtime.call("show_source_region", {"source": sha[:12], "node": hits[0]["node"]}).output)
    assert region["page_indices"] == [2] and region["printed_labels"] == {"2": "128"}
    assert runtime.call("read_source", {"source": sha[:12], "node": "n-missing"}).ok is False
    assert runtime.call("nonsense", {}).ok is False


def test_runtime_is_a_thin_facade_over_the_reader(tmp_path):
    runtime, sha = seeded(tmp_path)
    assert isinstance(runtime, SourceToolRuntime)
    children = json.loads(runtime.call("source_map", {"source": sha[:12], "node": json.loads(runtime.call("source_map", {"source": sha[:12]}).output)["entries"][0]["node"]}).output)
    assert children["children"]
