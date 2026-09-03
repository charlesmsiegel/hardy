"""The corpus directory: sharded loading, the id registry, the manifest, checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hardy.evals.corpus import (
    CorpusError,
    check_issues,
    load_corpus,
    load_tombstones,
    manifest_digest,
    report,
    tombstone_issues,
    version_issues,
)

ROOT = Path(__file__).resolve().parents[2]


def _entry(**overrides) -> dict:
    base = {
        "id": "odd-squares", "input": "If $a$ and $b$ are odd, $a^2+b^2$ is not a square.",
        "name": "OddSquares", "binders": "(a b : ℤ)", "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)",
        "expected": "true", "source": "classical", "msc": ["11A"],
        "difficulty": "substantial", "status": "candidate",
        "witness": None, "witness_note": "n/a", "rationale": "smoke", "occurrences": [],
    }
    base.update(overrides)
    return base


def _write(root: Path, shard: str, entries: list[dict], version: str = "0.1.0", schema: int = 2) -> None:
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "problems" / f"{shard}.json").write_text(
        json.dumps({"schema_version": schema, "corpus_version": version, "entries": entries}),
        encoding="utf-8",
    )


def _sources(root: Path, ids: list[str]) -> None:
    (root / "sources.json").write_text(
        json.dumps({"schema_version": 1, "sources": {id: {"title": id} for id in ids}}),
        encoding="utf-8")


def _registry(root: Path, issued: dict[str, str]) -> None:
    (root / "tombstones.json").write_text(
        json.dumps({"schema_version": 1, "issued": issued}), encoding="utf-8")


def _changelog(root: Path, version: str = "0.1.0", digest: str | None = None) -> None:
    """The head binds the manifest digest, so it is written after the content."""
    digest = manifest_digest(root) if digest is None else digest
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} - 2026-09-03 - manifest {digest}\n\n- initial\n",
        encoding="utf-8")


# --- Sharded loading ---


def test_shards_are_concatenated_into_one_set(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _write(tmp_path, "20", [_entry(id="b", name="B", msc=["20D"])])
    assert {e.id for e in load_corpus(tmp_path).entries} == {"a", "b"}


def test_an_id_duplicated_across_two_shards_is_rejected(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _write(tmp_path, "20", [_entry(id="a", name="B", msc=["20D"])])
    with pytest.raises(CorpusError, match="duplicate id"):
        load_corpus(tmp_path)


def test_an_entry_filed_in_the_wrong_shard_is_rejected(tmp_path):
    """Otherwise the derivation of the shard from `msc[0][:2]` is a fiction."""
    _write(tmp_path, "20", [_entry(id="a", name="A", msc=["13A15"])])
    with pytest.raises(CorpusError, match="belongs in shard 13"):
        load_corpus(tmp_path)


def test_an_empty_corpus_is_refused_rather_than_returning_nothing(tmp_path):
    (tmp_path / "problems").mkdir()
    with pytest.raises(CorpusError, match="no problem shards"):
        load_corpus(tmp_path)


def test_a_shard_declaring_an_unreadable_schema_is_refused(tmp_path):
    """Otherwise a future format loads as this one and the version says nothing."""
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])], schema=99)
    with pytest.raises(CorpusError, match="schema_version"):
        load_corpus(tmp_path)


def test_a_malformed_shard_is_a_corpus_error_not_a_raw_parse_failure(tmp_path):
    """`corpus check` gathers issues; a bare KeyError or ValidationError
    escaping the loader would crash it instead of being reported."""
    (tmp_path / "problems").mkdir(parents=True)
    (tmp_path / "problems" / "13.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CorpusError, match="13.json"):
        load_corpus(tmp_path)

    (tmp_path / "problems" / "13.json").write_text(
        json.dumps({"schema_version": 2, "corpus_version": "0.1.0"}), encoding="utf-8")
    with pytest.raises(CorpusError, match="13.json"):
        load_corpus(tmp_path)


def test_a_shard_version_that_is_not_three_level_is_refused(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])], version="0.1")
    with pytest.raises(CorpusError, match="13.json"):
        load_corpus(tmp_path)


# --- The id registry ---


def test_a_live_id_must_be_registered(tmp_path):
    _write(tmp_path, "13", [_entry(id="fresh", name="Fresh", msc=["13A15"])])
    _registry(tmp_path, {})
    issues = tombstone_issues(load_corpus(tmp_path), load_tombstones(tmp_path))
    assert any("fresh" in i and "not registered" in i for i in issues)


def test_an_issued_id_that_vanished_is_reported(tmp_path):
    """Deleting a retired row would otherwise free its id for reuse."""
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _registry(tmp_path, {"a": "2026-09-03", "gone": "2026-08-01"})
    issues = tombstone_issues(load_corpus(tmp_path), load_tombstones(tmp_path))
    assert any("gone" in i and "retired" in i for i in issues)


def test_a_registry_matching_the_corpus_is_clean(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _registry(tmp_path, {"a": "2026-09-03"})
    assert tombstone_issues(load_corpus(tmp_path), load_tombstones(tmp_path)) == []


# --- Manifest and version ---


def test_the_manifest_covers_content_and_ignores_measurements(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _registry(tmp_path, {"a": "2026-09-03"})
    before = manifest_digest(tmp_path)

    (tmp_path / "measurements").mkdir()
    (tmp_path / "measurements" / "baseline-abc-host.json").write_text("{}", encoding="utf-8")
    assert manifest_digest(tmp_path) == before, "a baseline re-sweep must not demand a version bump"

    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"], conclusion="True")])
    assert manifest_digest(tmp_path) != before


def test_the_manifest_ignores_the_readers_guide(tmp_path):
    """`SCHEMA.md` is documentation, not data (spec section 3 lists what the
    manifest covers, and prose is not on it). Hashing it would make an edited
    paragraph a content release and invalidate every scoreboard bound to the
    manifest -- the same objection that keeps `measurements/` out.
    """
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    before = manifest_digest(tmp_path)
    (tmp_path / "SCHEMA.md").write_text("# rewritten\n", encoding="utf-8")
    assert manifest_digest(tmp_path) == before


def test_the_changelog_head_must_match_the_corpus_version(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])], version="0.1.0")
    _registry(tmp_path, {"a": "2026-09-03"})
    _changelog(tmp_path, "0.2.0")
    assert any("0.1.0" in i and "0.2.0" in i for i in version_issues(tmp_path))

    _changelog(tmp_path, "0.1.0")
    assert version_issues(tmp_path) == []


def test_the_changelog_head_binds_the_manifest_digest(tmp_path):
    """Comparing version strings alone cannot see an *unversioned* edit: a
    shard changes while both strings stay put and the gate still passes,
    which makes a published version non-reproducible (spec section 3).
    """
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _registry(tmp_path, {"a": "2026-09-03"})
    _changelog(tmp_path)
    assert version_issues(tmp_path) == []

    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"], conclusion="True")])
    issues = version_issues(tmp_path)
    assert any("manifest digest" in i for i in issues), issues


def test_a_changelog_head_with_no_manifest_digest_is_refused(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.1.0 - 2026-09-03\n", encoding="utf-8")
    assert any("manifest" in i for i in version_issues(tmp_path))


# --- Provenance ---


def test_an_occurrence_citing_an_unregistered_source_is_reported(tmp_path):
    """Primary provenance decides the field, the level and the antecedent
    rule; a citation pointing at no text decides them from nothing.
    """
    entry = _entry(id="a", name="A", msc=["13A15"],
                   occurrences=[{"source_id": "atiyah-macdonald", "locator": [3, 2, 12]}])
    _write(tmp_path, "13", [entry])
    _registry(tmp_path, {"a": "2026-09-03"})
    _sources(tmp_path, [])
    _changelog(tmp_path)
    assert any("atiyah-macdonald" in i for i in check_issues(tmp_path))

    _sources(tmp_path, ["atiyah-macdonald"])
    _changelog(tmp_path)
    assert check_issues(tmp_path) == []


# --- check and report ---


def test_check_gathers_every_class_of_issue(tmp_path):
    _write(tmp_path, "13", [_entry(id="a", name="A", msc=["13A15"])])
    _registry(tmp_path, {})
    _changelog(tmp_path, "9.9.9")
    issues = check_issues(tmp_path)
    assert any("not registered" in i for i in issues)
    assert any("changelog head" in i for i in issues)


def test_the_shipped_corpus_is_clean_and_internally_consistent():
    problems = load_corpus(ROOT / "corpus")
    assert len(problems.entries) == 20
    assert sum(1 for e in problems.entries if e.expected == "false") == 5
    assert check_issues(ROOT / "corpus") == []


def test_every_shipped_entry_is_classified_finer_than_its_shard():
    for entry in load_corpus(ROOT / "corpus").entries:
        assert entry.input.strip(), entry.id
        assert len(entry.msc[0]) > 2, entry.id
        assert entry.shard == entry.msc[0][:2]


def test_report_counts_by_group_and_status():
    lines = report(ROOT / "corpus")
    assert any("candidate" in line for line in lines)
    assert any("number-theory" in line for line in lines)
