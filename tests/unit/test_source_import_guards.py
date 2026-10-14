"""Portable imports reject filesystem links before reading or writing bytes."""
import hashlib
import json
from pathlib import Path

import pytest

from hardy.foundation.files import LayoutError
from hardy.foundation.values import json_digest
from hardy.literature.sources.export import ExportClass, ExportManifest, import_export
from hardy.literature.sources.library import ManagedLibrary


def bundle_with_file(root: Path, relative: str, data: bytes = b"portable content") -> Path:
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    manifest = ExportManifest(classes=(ExportClass.PRIVATE_SOURCE_CACHE,), files=((relative, hashlib.sha256(data).hexdigest()),),
                              artifacts=(), created_at="2026-01-01T00:00:00+00:00")
    (root / "export.json").write_text(manifest.model_dump_json())
    return root


@pytest.mark.parametrize("lean", [False, True])
@pytest.mark.parametrize("linked_root", [False, True])
def test_import_refuses_destination_symlink_ancestors(tmp_path, lean, linked_root):
    relative = "lean/HardyShared/New.lean" if lean else "artifacts/new/content"
    bundle = bundle_with_file(tmp_path / "bundle", relative)
    library = ManagedLibrary(tmp_path / "library")
    destination = tmp_path / "shared-lean" if lean else library.root
    outside = tmp_path / "outside"
    outside.mkdir()
    link = destination if linked_root else destination / ("HardyShared" if lean else "artifacts")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    summary = import_export(library, bundle, shared_lean=destination if lean else None)
    assert not summary.copied
    assert summary.skipped
    assert list(outside.iterdir()) == []


def test_import_refuses_source_symlink_ancestor(tmp_path):
    relative = "artifacts/new/content"
    bundle = bundle_with_file(tmp_path / "bundle", relative)
    outside = tmp_path / "outside"
    (bundle / "artifacts").rename(outside)
    (bundle / "artifacts").symlink_to(outside, target_is_directory=True)
    library = ManagedLibrary(tmp_path / "library")
    summary = import_export(library, bundle)
    assert not summary.copied
    assert summary.skipped
    assert not (library.root / relative).exists()


def test_import_refuses_unknown_library_location(tmp_path):
    bundle = bundle_with_file(tmp_path / "bundle", "configuration.json")
    library = ManagedLibrary(tmp_path / "library")
    summary = import_export(library, bundle)
    assert not summary.copied
    assert summary.skipped
    assert not (library.root / "configuration.json").exists()


def test_import_copies_guarded_bytes_without_overwriting_existing_file(tmp_path):
    relative = "artifacts/new/content"
    bundle = bundle_with_file(tmp_path / "bundle", relative)
    library = ManagedLibrary(tmp_path / "library")
    assert import_export(library, bundle).copied == (relative,)
    target = library.root / relative
    assert target.read_bytes() == b"portable content"
    target.write_bytes(b"local content")
    summary = import_export(library, bundle)
    assert not summary.copied
    assert "local file differs" in summary.skipped[0]
    assert target.read_bytes() == b"local content"


@pytest.mark.parametrize("side", ["source", "destination"])
@pytest.mark.parametrize("owner", ["catalog", "trees/journal"])
def test_import_refuses_journal_symlink_ancestors(tmp_path, side, owner):
    event = {"schema": "hardy.journal/v1", "sequence": 1, "previous": None, "records": []}
    data = json.dumps(event | {"digest": json_digest(event)}).encode()
    relative = f"{owner}/00000000000000000001.json"
    bundle = bundle_with_file(tmp_path / "bundle", relative, data)
    library = ManagedLibrary(tmp_path / "library")
    outside = tmp_path / "outside"
    if side == "source":
        (bundle / owner).rename(outside)
        (bundle / owner).symlink_to(outside, target_is_directory=True)
    else:
        outside.mkdir()
        link = library.root / owner
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
    summary = import_export(library, bundle)
    assert not summary.copied
    assert summary.skipped
    assert not summary.journals_seeded
    if side == "destination":
        assert list(outside.iterdir()) == []


@pytest.mark.parametrize("side", ["source", "destination"])
def test_import_refuses_symlink_file_leaves(tmp_path, side):
    relative = "artifacts/new/content"
    bundle = bundle_with_file(tmp_path / "bundle", relative)
    library = ManagedLibrary(tmp_path / "library")
    outside = tmp_path / "outside"
    outside.write_bytes(b"portable content")
    target = (bundle if side == "source" else library.root) / relative
    if side == "source":
        target.unlink()
    else:
        target.parent.mkdir(parents=True)
    target.symlink_to(outside)
    summary = import_export(library, bundle)
    assert not summary.copied
    assert summary.skipped
    assert outside.read_bytes() == b"portable content"


@pytest.mark.parametrize("root_link", [False, True])
def test_import_refuses_symlink_bundle_manifest_or_root(tmp_path, root_link):
    bundle = bundle_with_file(tmp_path / "bundle", "artifacts/new/content")
    if root_link:
        outside = tmp_path / "outside"
        bundle.rename(outside)
        bundle.symlink_to(outside, target_is_directory=True)
    else:
        outside = tmp_path / "outside.json"
        (bundle / "export.json").rename(outside)
        (bundle / "export.json").symlink_to(outside)
    with pytest.raises(LayoutError):
        import_export(ManagedLibrary(tmp_path / "library"), bundle)


def test_import_reports_journal_destination_file_without_changing_it(tmp_path):
    event = {"schema": "hardy.journal/v1", "sequence": 1, "previous": None, "records": []}
    data = json.dumps(event | {"digest": json_digest(event)}).encode()
    bundle = bundle_with_file(tmp_path / "bundle", "catalog/00000000000000000001.json", data)
    library = ManagedLibrary(tmp_path / "library")
    library.root.mkdir()
    target = library.root / "catalog"
    target.write_bytes(b"local file")
    summary = import_export(library, bundle)
    assert not summary.copied
    assert summary.skipped
    assert target.read_bytes() == b"local file"


def test_normalized_journal_path_still_requires_valid_chain(tmp_path):
    bundle = bundle_with_file(tmp_path / "bundle", "./catalog/00000000000000000001.json", b"not a journal")
    library = ManagedLibrary(tmp_path / "library")
    summary = import_export(library, bundle)
    assert not summary.copied
    assert any("journal chain invalid" in entry for entry in summary.skipped)
    assert not (library.root / "catalog/00000000000000000001.json").exists()
