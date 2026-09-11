"""Managed artifacts: content identity, atomic admission, provenance, and honest absence."""

from __future__ import annotations

import hashlib
import os
import threading

import pytest

from hardy.literature.sources.artifacts import (
    ArtifactError,
    ArtifactStore,
    ImportRefused,
    ImportRequest,
    detect_format,
)
from hardy.literature.sources.contracts import AccessPolicy, SourceFormat

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts", clock=lambda: 1_700_000_000.0)


def test_import_copies_bytes_and_reads_do_not_touch_original_path(tmp_path):
    original = tmp_path / "downloads" / "Hartshorne.pdf"
    original.parent.mkdir()
    original.write_bytes(PDF)
    artifacts = store(tmp_path)
    outcome = artifacts.import_bytes(ImportRequest(path=original))
    assert outcome.reused is False
    assert outcome.artifact.sha256 == hashlib.sha256(PDF).hexdigest()
    assert outcome.artifact.format is SourceFormat.PDF
    assert outcome.provenance.original_path == str(original)
    original.unlink()
    assert artifacts.read(outcome.artifact.sha256) == PDF
    assert artifacts.availability(outcome.artifact.sha256).status == "available"


def test_same_bytes_from_two_paths_is_one_artifact_with_two_provenance_records(tmp_path):
    artifacts = store(tmp_path)
    first = artifacts.import_bytes(ImportRequest(data=PDF, original_name="a.pdf"))
    second = artifacts.import_bytes(ImportRequest(data=PDF, original_name="b.pdf", source_url="https://x/b.pdf"))
    assert second.reused is True
    assert first.artifact == second.artifact
    assert artifacts.stored() == (first.artifact.sha256,)
    names = sorted(p.original_name for p in artifacts.provenance(first.artifact.sha256))
    assert names == ["a.pdf", "b.pdf"]
    assert first.provenance.id != second.provenance.id


def test_same_metadata_different_bytes_are_separate_artifacts(tmp_path):
    artifacts = store(tmp_path)
    metadata = (("title", "Algebraic Geometry"), ("isbn", "9780387902449"))
    first = artifacts.import_bytes(ImportRequest(data=PDF, user_metadata=metadata))
    second = artifacts.import_bytes(ImportRequest(data=PDF + b"\n% corrected printing\n", user_metadata=metadata))
    assert first.artifact.sha256 != second.artifact.sha256
    assert len(artifacts.stored()) == 2


def test_changing_the_original_file_after_import_leaves_the_artifact_unchanged(tmp_path):
    original = tmp_path / "book.pdf"
    original.write_bytes(PDF)
    artifacts = store(tmp_path)
    outcome = artifacts.import_bytes(ImportRequest(path=original))
    original.write_bytes(PDF + b"tampered")
    assert artifacts.read(outcome.artifact.sha256) == PDF
    again = artifacts.import_bytes(ImportRequest(path=original))
    assert again.artifact.sha256 != outcome.artifact.sha256


def test_interrupted_import_leaves_no_readable_artifact(tmp_path, monkeypatch):
    artifacts = store(tmp_path)
    sha = hashlib.sha256(PDF).hexdigest()
    real_replace = os.replace

    def crash(src, dst):
        raise OSError("power failure before rename")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        artifacts.import_bytes(ImportRequest(data=PDF))
    monkeypatch.setattr(os, "replace", real_replace)
    assert artifacts.holds(sha) is False
    assert artifacts.availability(sha).status == "unavailable"
    assert not list((tmp_path / "artifacts").glob(".staging-*"))
    with pytest.raises(ArtifactError):
        artifacts.read(sha)


def test_missing_artifact_reports_unavailable_not_absent_identity(tmp_path):
    artifacts = store(tmp_path)
    sha = "0" * 64
    availability = artifacts.availability(sha)
    assert availability.status == "unavailable"
    assert availability.sha256 == sha
    with pytest.raises(ArtifactError, match="unavailable"):
        artifacts.read(sha)


def test_corrupted_content_is_reported_corrupt_and_refused(tmp_path):
    artifacts = store(tmp_path)
    outcome = artifacts.import_bytes(ImportRequest(data=PDF))
    (tmp_path / "artifacts" / outcome.artifact.sha256 / "content").write_bytes(b"rotten")
    assert artifacts.availability(outcome.artifact.sha256).status == "corrupt"
    with pytest.raises(ArtifactError, match="corrupt"):
        artifacts.read(outcome.artifact.sha256)


def test_concurrent_identical_imports_coalesce(tmp_path):
    artifacts = store(tmp_path)
    barrier = threading.Barrier(4)
    outcomes = []

    def worker(index):
        barrier.wait()
        outcomes.append(artifacts.import_bytes(ImportRequest(data=PDF, original_name=f"{index}.pdf")))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 4
    assert {o.artifact.sha256 for o in outcomes} == {hashlib.sha256(PDF).hexdigest()}
    assert sum(1 for o in outcomes if not o.reused) == 1
    assert len(artifacts.provenance(outcomes[0].artifact.sha256)) == 4


def test_oversized_input_is_refused_before_staging(tmp_path):
    artifacts = store(tmp_path)
    with pytest.raises(ImportRefused):
        artifacts.import_bytes(ImportRequest(data=b"x" * 100, max_bytes=50))
    original = tmp_path / "big.bin"
    original.write_bytes(b"y" * 100)
    with pytest.raises(ImportRefused):
        artifacts.import_bytes(ImportRequest(path=original, max_bytes=50))
    assert artifacts.stored() == ()
    assert not (tmp_path / "artifacts").exists() or not list((tmp_path / "artifacts").iterdir())


def test_request_needs_exactly_one_of_data_or_path(tmp_path):
    with pytest.raises(ImportRefused):
        store(tmp_path).import_bytes(ImportRequest())
    with pytest.raises(ImportRefused):
        store(tmp_path).import_bytes(ImportRequest(data=b"x", path=tmp_path / "x"))


def test_access_policy_and_metadata_are_recorded(tmp_path):
    artifacts = store(tmp_path)
    outcome = artifacts.import_bytes(ImportRequest(
        data=b"Hello, world.\n", original_name="notes.txt", access=AccessPolicy.REDISTRIBUTABLE,
        user_metadata=(("title", "Notes"),),
    ))
    assert outcome.artifact.access is AccessPolicy.REDISTRIBUTABLE
    assert outcome.artifact.format is SourceFormat.TEXT
    assert outcome.provenance.user_metadata == (("title", "Notes"),)
    assert artifacts.record(outcome.artifact.sha256) == outcome.artifact


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (PDF, None, (SourceFormat.PDF, "application/pdf")),
        (b"PK\x03\x04" + b"\x00" * 26 + b"mimetypeapplication/epub+zip", "x.epub", (SourceFormat.EPUB, "application/epub+zip")),
        (b"<!DOCTYPE html><html><body>hi</body></html>", None, (SourceFormat.HTML, "text/html")),
        (b"\x1f\x8b\x08\x00", "source.tar.gz", (SourceFormat.TEX_TREE, "application/gzip")),
        (b"plain text\n", "a.md", (SourceFormat.TEXT, "text/plain")),
        (b"\x00\x01\x02\xff", None, (SourceFormat.UNKNOWN, "application/octet-stream")),
    ],
)
def test_detect_format(data, name, expected):
    assert detect_format(data, name=name) == expected
