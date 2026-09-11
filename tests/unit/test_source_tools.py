"""The source tools read seeded sources only, in bounded, provenance-carrying answers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

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


def test_tools_read_the_tree_the_seed_names_not_the_preferred_one(tmp_path):
    from hardy.literature.sources.contracts import NodeKind
    from hardy.literature.sources.repair import (
        ProposedUnit,
        RepairProposal,
        apply_repair,
        weak_regions,
    )

    pages = [Page((("THEOREM 2.3 Every compact set is closed.", 72, 720), ("PROOF Standard. Q.E.D.", 72, 690),
                   ("Lemma 2.4. A clean lemma.", 72, 660), ("Proof. Clear. Q.E.D.", 72, 630)))]
    runtime, sha = seeded(tmp_path, pages=pages)
    lib = runtime.library
    first = lib.trees.preferred(sha)
    texts = lib.representations.texts(sha)
    (window,) = weak_regions(first, texts)
    proof_at = window.text.index("PROOF")
    proposal = RepairProposal(window=window, proposer="m", proposer_version="0", units=(
        ProposedUnit(kind=NodeKind.THEOREM, start=window.start, end=window.start + proof_at - 1, number="2.3", boundary_status="high",
                     statement_end=window.start + proof_at - 1),
        ProposedUnit(kind=NodeKind.PROOF, start=window.start + proof_at, end=window.end, boundary_status="high", proof_of="2.3")))
    second = lib.trees.admit(apply_repair(first, proposal, texts), {r.id: r for r in lib.representations.list(sha)}, texts)
    assert lib.trees.preferred(sha).id == second.id and second.id != first.id
    listed = json.loads(runtime.call("list_sources", {}).output)["sources"][0]
    assert listed["tree"] == first.id
    found = json.loads(runtime.call("find_source_statements", {"source": sha[:12], "number": "2.3"}).output)
    assert found["tree"] == first.id and found["statements"] == []  # 2.3 only exists in the repaired tree
    hits = json.loads(runtime.call("search_source", {"source": sha[:12], "query": "2.4"}).output)
    read = json.loads(runtime.call("read_source", {"source": sha[:12], "node": hits["hits"][0]["node"]}).output)
    assert read["tree"] == first.id
    region = json.loads(runtime.call("show_source_region", {"source": sha[:12], "node": hits["hits"][0]["node"]}).output)
    assert region["tree"] == first.id
    source_map = json.loads(runtime.call("source_map", {"source": sha[:12]}).output)
    assert source_map["tree"] == first.id
    later = datetime(2030, 1, 1, tzinfo=UTC)
    runtime.seeds.add(new_seed(sha, tree=second.id, priority=0, now=later), expected_revision=runtime.seeds.revision())
    refused = runtime.call("source_map", {"source": sha[:12]})
    assert refused.ok is False and "several trees" in refused.output


def test_delivery_refuses_when_no_cut_fits_the_budget(tmp_path):
    runtime, sha = seeded(tmp_path)
    found = json.loads(runtime.call("find_source_statements", {"source": sha[:12], "number": "1.2"}).output)
    (node,) = found["statements"]
    full = json.loads(runtime.call("read_source", {"source": sha[:12], "node": node["node"]}).output)
    assert full["span"]["anchors"]
    elided = {**full, "text": "", "truncated": True, "span": {**full["span"], "anchors": [], "anchors_elided": True}}
    size = len(json.dumps(elided, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    tight = SourceToolRuntime(runtime.seeds, runtime.library, observation_bytes=size + 8)
    kept = tight.call("read_source", {"source": sha[:12], "node": node["node"]})
    assert kept.ok, kept.output
    payload = json.loads(kept.output)
    assert payload["span"]["anchors_elided"] is True and payload["span"]["content_sha256"] == full["span"]["content_sha256"]
    assert len(kept.output.encode("utf-8")) <= size + 8
    hopeless = SourceToolRuntime(runtime.seeds, runtime.library, observation_bytes=size - 8)
    refused = hopeless.call("read_source", {"source": sha[:12], "node": node["node"]})
    assert refused.ok is False and "does not fit the observation budget" in refused.output
    assert len(refused.output.encode("utf-8")) <= size - 8
