"""Representations are write-once, plural, and served only as recorded."""

from __future__ import annotations

import json

import pytest

from hardy.literature.sources.contracts import (
    AccessPolicy,
    DerivedRepresentation,
    PageRegion,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
)
from hardy.literature.sources.locators import resolve_span, span_for
from hardy.literature.sources.representations import (
    RepresentationError,
    RepresentationStore,
    payload_digest,
)

SHA = "d" * 64


def record(id, payloads, *, kind=RepresentationKind.NORMALIZED_TEXT, access=AccessPolicy.PRIVATE_LOCAL, derived_at="2026-09-11T00:00:00+00:00"):
    return DerivedRepresentation(
        id=id, artifact_sha256=SHA, kind=kind, extractor="test", extractor_version="1",
        output_sha256=payload_digest(payloads), derived_at=derived_at,
        quality=QualityProfile(status="ok"), access=access, payload_files=tuple(payloads),
    )


def test_old_representation_stays_readable_after_a_new_one_is_admitted(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    old_text = b"Theorem 1. Old reading."
    new_text = b"Theorem 1. Improved reading."
    old = store.admit(record("rep-native-v1", {"text.txt": old_text}), {"text.txt": old_text})
    span = span_for(old, old_text.decode(), 0, 10, id="span-1", mapping_provenance="test")
    store.admit(record("rep-native-v2", {"text.txt": new_text}, derived_at="2026-09-12T00:00:00+00:00"), {"text.txt": new_text})
    assert [r.id for r in store.list(SHA)] == ["rep-native-v1", "rep-native-v2"]
    assert resolve_span(span, store.texts(SHA)) == "Theorem 1."
    assert store.text(SHA, "rep-native-v1") == old_text.decode()


def test_two_representations_coexist_without_a_canonical_merge(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    store.admit(record("rep-native", {"text.txt": b"native"}, kind=RepresentationKind.NATIVE_TEXT), {"text.txt": b"native"})
    store.admit(record("rep-ocr", {"text.txt": b"ocr"}, kind=RepresentationKind.OCR_TEXT), {"text.txt": b"ocr"})
    assert {r.kind for r in store.list(SHA)} == {RepresentationKind.NATIVE_TEXT, RepresentationKind.OCR_TEXT}
    assert store.list(SHA, RepresentationKind.OCR_TEXT)[0].id == "rep-ocr"
    assert store.texts(SHA) == {"rep-native": "native", "rep-ocr": "ocr"}


def test_page_index_and_printed_label_are_distinct_queries(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    pages = [{"index": 0, "label": "i", "width": 612, "height": 792}, {"index": 1, "label": None, "width": 612, "height": 792},
             {"index": 2, "label": "128", "width": 612, "height": 792}]
    payload = {"pages.json": json.dumps(pages).encode()}
    store.admit(record("rep-pages", payload, kind=RepresentationKind.PAGE_MANIFEST), payload)
    assert store.page_count(SHA) == 3
    assert store.page_labels(SHA) == ((0, "i"), (2, "128"))
    assert store.page_labels("e" * 64) == ()
    assert store.page_count("e" * 64) is None


def test_admit_same_id_with_different_output_is_refused(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    store.admit(record("rep-1", {"text.txt": b"one"}), {"text.txt": b"one"})
    again = store.admit(record("rep-1", {"text.txt": b"one"}), {"text.txt": b"one"})
    assert again.id == "rep-1"
    with pytest.raises(RepresentationError, match="different output"):
        store.admit(record("rep-1", {"text.txt": b"two"}), {"text.txt": b"two"})


def test_admit_checks_payload_digest_and_names(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    bad = record("rep-1", {"text.txt": b"one"}).model_copy(update={"output_sha256": "1" * 64})
    with pytest.raises(RepresentationError, match="digest"):
        store.admit(bad, {"text.txt": b"one"})
    with pytest.raises(RepresentationError, match="payload_files"):
        store.admit(record("rep-2", {"text.txt": b"one"}), {"other.txt": b"one"})
    with pytest.raises(RepresentationError, match="plain file"):
        store.admit(record("rep-3", {"../x": b"one"}), {"../x": b"one"})


def test_tampered_payload_is_refused_on_read(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    store.admit(record("rep-1", {"text.txt": b"one"}), {"text.txt": b"one"})
    (tmp_path / "reps" / SHA / "rep-1" / "text.txt").write_bytes(b"two")
    with pytest.raises(RepresentationError, match="no longer"):
        store.text(SHA, "rep-1")


def test_mappings_are_stored_beside_their_left_representation_and_queryable(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    store.admit(record("rep-text", {"text.txt": b"Theorem 1."}), {"text.txt": b"Theorem 1."})
    mapping = RepresentationMapping(
        id="map-text-pages", artifact_sha256=SHA, left="rep-text", right="rep-pages", partial=True, producer="test",
        pairs=((RepresentationSpan(representation="rep-text", start=0, end=10), PageRegion(page_index=0, precision="page")),),
    )
    store.admit(record("rep-text", {"text.txt": b"Theorem 1."}), {"text.txt": b"Theorem 1."}, mappings=(mapping,))
    assert store.mappings(SHA) == (mapping,)
    assert store.mappings(SHA, left="rep-text", right="rep-pages") == (mapping,)
    assert store.mappings(SHA, right="rep-other") == ()
    with pytest.raises(RepresentationError, match="unknown representation"):
        store.admit(record("rep-2", {"text.txt": b"x"}), {"text.txt": b"x"}, mappings=(mapping.model_copy(update={"id": "m2", "left": "rep-missing"}),))


def test_derived_representation_inherits_private_access(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    held = store.admit(record("rep-ocr", {"text.txt": b"ocr"}, kind=RepresentationKind.OCR_TEXT, access=AccessPolicy.PRIVATE_LOCAL), {"text.txt": b"ocr"})
    assert held.access is AccessPolicy.PRIVATE_LOCAL


def test_missing_representation_is_reported_as_not_held(tmp_path):
    store = RepresentationStore(tmp_path / "reps")
    with pytest.raises(RepresentationError, match="not held"):
        store.get(SHA, "rep-missing")
    assert store.list(SHA) == ()


def test_raced_admission_with_different_output_is_refused(tmp_path, monkeypatch):
    import os

    store = RepresentationStore(tmp_path / "reps")
    winner_store = RepresentationStore(tmp_path / "winner")
    winner = winner_store.admit(record("rep-1", {"text.txt": b"winner"}), {"text.txt": b"winner"})
    real_replace = os.replace
    target = tmp_path / "reps" / SHA / "rep-1"

    def race(src, dst):
        if os.fspath(dst) == os.fspath(target) and not target.exists():
            import shutil

            shutil.copytree(tmp_path / "winner" / SHA / "rep-1", target)
            raise OSError("the winner got there first")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", race)
    with pytest.raises(RepresentationError, match="concurrently with different output"):
        store.admit(record("rep-1", {"text.txt": b"loser"}), {"text.txt": b"loser"})
    assert store.get(SHA, "rep-1") == winner and store.text(SHA, "rep-1") == "winner"
    same = store.admit(record("rep-1", {"text.txt": b"winner"}), {"text.txt": b"winner"})
    assert same == winner
