"""Seeds are refs a problem commits; they carry no bytes."""

from __future__ import annotations

import json

import pytest

from hardy.literature.sources.seeds import SeedStore, new_seed

SHA = "9" * 64


def test_seed_store_holds_refs_only_no_bytes(tmp_path):
    problem = tmp_path / "problem"
    store = SeedStore(problem)
    seed = new_seed(SHA, edition="edition-1", tree="tree-1", priority=2, intent="Chapter II")
    store.add(seed, expected_revision=0)
    assert store.seeds() == (seed,)
    files = list((problem / "sources").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    dumped = json.dumps(payload)
    assert SHA in dumped and "content" not in payload["records"][0]["value"]
    assert set(payload["records"][0]["value"]) == {"id", "artifact_sha256", "edition", "tree", "subtree", "priority", "intent", "seeded_at"}


def test_seeds_order_by_priority_and_removal_is_a_record(tmp_path):
    store = SeedStore(tmp_path / "problem")
    low = new_seed("1" * 64, priority=0)
    high = new_seed("2" * 64, priority=5)
    store.add(low, expected_revision=0)
    store.add(high, expected_revision=1)
    assert [s.id for s in store.seeds()] == [high.id, low.id]
    store.remove(low.id, expected_revision=2, reason="done")
    assert [s.id for s in store.seeds()] == [high.id]
    assert store.revision() == 3
    with pytest.raises(ValueError, match="unknown seed"):
        store.remove("seed-nope", expected_revision=3)


def test_prefix_lookup(tmp_path):
    store = SeedStore(tmp_path / "problem")
    seed = new_seed(SHA)
    store.add(seed, expected_revision=0)
    assert store.by_prefix("99999999") == (seed,)
    assert store.by_prefix(seed.id) == (seed,)
    assert store.by_prefix("abc") == ()
    assert SeedStore(tmp_path / "other").seeds() == ()
