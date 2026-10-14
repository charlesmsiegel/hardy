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
    with pytest.raises(RepresentationError, match="unknown representation rep-pages on its right"):
        store.add_mappings(SHA, (mapping,))  # a mapping into a coordinate system nobody holds is not provenance
    store.admit(record("rep-pages", {"pages.json": b"[]"}, kind=RepresentationKind.PAGE_MANIFEST), {"pages.json": b"[]"})
    store.admit(record("rep-text", {"text.txt": b"Theorem 1."}), {"text.txt": b"Theorem 1."}, mappings=(mapping,))
    assert store.mappings(SHA) == (mapping,)
    assert store.mappings(SHA, left="rep-text", right="rep-pages") == (mapping,)
    assert store.mappings(SHA, right="rep-other") == ()
    with pytest.raises(RepresentationError, match="unknown representation"):
        store.admit(record("rep-2", {"text.txt": b"x"}), {"text.txt": b"x"}, mappings=(mapping.model_copy(update={"id": "m2", "left": "rep-missing"}),))
    crossed = mapping.model_copy(update={"id": "m3", "pairs": ((RepresentationSpan(representation="rep-2", start=0, end=1), PageRegion(page_index=0)),)})
    with pytest.raises(RepresentationError, match="names representation rep-2, not rep-text"):
        store.add_mappings(SHA, (crossed,))


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


def test_payload_verification_reuses_unchanged_files_and_rechecks_tamper(tmp_path, monkeypatch):
    from file_identity_helpers import windows_creation_clock

    windows_creation_clock(monkeypatch)
    import os

    from hardy.literature.sources import representations as module

    store = RepresentationStore(tmp_path / 'reps')
    payloads = {'text.txt': b'one', 'layout.json': b'{}'}
    store.admit(record('rep-cache', payloads), payloads)
    reads = []
    original = module.read_bytes

    def counted(root, relative):
        reads.append(relative)
        return original(root, relative)

    monkeypatch.setattr(module, 'read_bytes', counted)
    assert store.text(SHA, 'rep-cache') == 'one'
    assert store.text(SHA, 'rep-cache') == 'one'
    assert len(reads) == 2  # one read per payload, only on first verification
    path = store.root / SHA / 'rep-cache' / 'layout.json'
    old = path.stat()
    path.write_bytes(b'[]')
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    with pytest.raises(RepresentationError, match='no longer'):
        store.text(SHA, 'rep-cache')


def test_payload_cache_refuses_symlink_replacement(tmp_path):
    store = RepresentationStore(tmp_path / 'reps')
    store.admit(record('rep-cache', {'text.txt': b'one'}), {'text.txt': b'one'})
    assert store.text(SHA, 'rep-cache') == 'one'
    path = store.root / SHA / 'rep-cache' / 'text.txt'
    other = tmp_path / 'other'
    other.write_bytes(b'one')
    path.unlink()
    path.symlink_to(other)
    from hardy.foundation.files import LayoutError
    with pytest.raises(LayoutError):
        store.text(SHA, 'rep-cache')


def test_unchanged_verified_text_is_decoded_once(tmp_path, monkeypatch):
    from file_identity_helpers import windows_creation_clock

    windows_creation_clock(monkeypatch)
    from hardy.literature.sources import representations as module

    store = RepresentationStore(tmp_path / 'reps')
    store.admit(record('rep-text', {'text.txt': b'one'}), {'text.txt': b'one'})
    decoded = []
    original = module.read_bytes

    class CountDecode(bytes):
        def decode(self, *args, **kwargs):
            decoded.append(1)
            return super().decode(*args, **kwargs)

    monkeypatch.setattr(module, 'read_bytes', lambda root, relative: CountDecode(original(root, relative)))
    assert store.text(SHA, 'rep-text') == 'one'
    assert store.text(SHA, 'rep-text') == 'one'
    assert decoded == [1]


def test_windows_change_clock_invalidates_representation_cache(tmp_path, monkeypatch):
    import os

    from file_identity_helpers import windows_creation_clock

    windows_creation_clock(monkeypatch)
    store = RepresentationStore(tmp_path / 'reps')
    store.admit(record('rep-text', {'text.txt': b'one'}), {'text.txt': b'one'})
    assert store.text(SHA, 'rep-text') == 'one'
    path = store.root / SHA / 'rep-text' / 'text.txt'
    before = path.stat()
    path.write_bytes(b'two')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(RepresentationError, match='no longer'):
        store.text(SHA, 'rep-text')


def test_cached_reader_survives_concurrent_lru_eviction(tmp_path, monkeypatch):
    from file_identity_helpers import windows_creation_clock

    windows_creation_clock(monkeypatch)
    import inspect
    import sys
    import threading

    store = RepresentationStore(tmp_path / "reps")
    payloads = {"text.txt": b"valid"}
    for index in range(65):
        store.admit(record(f"rep-{index}", payloads), payloads)
    for index in range(64):
        assert store.text(SHA, f"rep-{index}") == "valid"
    lines, start = inspect.getsourcelines(RepresentationStore.payload)
    promotion = start + next(i for i, line in enumerate(lines) if "self._verified.move_to_end(key)" in line)
    paused, resume = threading.Event(), threading.Event()
    results, errors = [], []

    def trace(frame, event, _arg):
        if frame.f_code is RepresentationStore.payload.__code__ and event == "line" and frame.f_lineno == promotion:
            paused.set()
            assert resume.wait(5)
        return trace

    def reader():
        sys.settrace(trace)
        try:
            results.append(store.text(SHA, "rep-0"))
        except Exception as error:
            errors.append(error)
        finally:
            sys.settrace(None)

    worker = threading.Thread(target=reader)
    worker.start()
    try:
        assert paused.wait(5)
        assert store.text(SHA, "rep-64") == "valid"
    finally:
        resume.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert results == ["valid"]
