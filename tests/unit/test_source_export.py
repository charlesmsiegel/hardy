"""Portable state carries identities and digests; private bytes travel only when asked."""

from __future__ import annotations

import shutil

from pdf_helpers import book_pages, build_pdf

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import AccessPolicy, RepresentationKind
from hardy.literature.sources.export import ExportClass, export_library, import_export
from hardy.literature.sources.index import SourceIndex
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.reading import SourceReader
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ProjectItem
from hardy.workflows.shared.claims import ClaimLinker, InterpretationProposal, LinkStore
from hardy.workflows.shared.ledger import SharedClaims, shared_store

PDF = build_pdf(book_pages(), title="Tiny Algebra")


def verdict():
    review = FaithfulnessReview(formalization_entails_claim=True, claim_entails_formalization=True)
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="r", reviewer_backend="t", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED, review=review)


def populated(tmp_path, name="library"):
    root = tmp_path / name
    lib = ManagedLibrary(root)
    sha = lib.import_source(ImportRequest(data=PDF, original_name="tiny.pdf")).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    claims = SharedClaims(shared_store(root))
    links = LinkStore(root / "links")
    linker = ClaimLinker(library=lib, claims=claims, links=links)
    theorem = next(n for n in tree.nodes if n.number == "1.2")
    link = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(
        new_claim=ProjectItem(id="cyclic-subgroups", kind="theorem", name="Subgroups of cyclic groups are cyclic", origin="background_paper",
                              statement="Every subgroup of a cyclic group is cyclic."), interpreter="m")).id, verdict=verdict())
    return root, lib, sha, tree, theorem, link


def test_metadata_export_omits_private_bytes_but_keeps_digests(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    bundle = tmp_path / "bundle"
    manifest = export_library(lib, classes=frozenset({ExportClass.METADATA_SEMANTICS}), into=bundle)
    names = [f for f, _ in manifest.files]
    assert f"artifacts/{sha}/artifact.json" in names and f"artifacts/{sha}/content" not in names
    assert manifest.artifacts == (sha,)
    assert not list(bundle.rglob("content")) and not any(p.suffix == ".pdf" for p in bundle.rglob("*"))
    payload_names = [n for n in names if "/text.txt" in n]
    assert payload_names == []  # the book's own text is private and stays home
    assert any(n.endswith("/pages.json") for n in names)  # page geometry and labels are metadata
    assert manifest.withheld_payloads and all(w.startswith(sha) for w in manifest.withheld_payloads)
    assert any(n.startswith(f"trees/{sha}/tree-") for n in names) and any(n.startswith("ledger/") for n in names) and any(n.startswith("links/") for n in names)
    assert PDF not in b"".join(p.read_bytes() for p in bundle.rglob("*") if p.is_file())


def test_private_ocr_text_is_excluded_from_metadata_export(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    from hardy.literature.sources.contracts import DerivedRepresentation, QualityProfile
    from hardy.literature.sources.representations import payload_digest

    ocr = {"text.txt": b"ocr of a private book"}
    lib.representations.admit(DerivedRepresentation(id="ocr-1", artifact_sha256=sha, kind=RepresentationKind.OCR_TEXT, extractor="ocr", extractor_version="1",
                                                    output_sha256=payload_digest(ocr), derived_at="2026-09-11T00:00:00+00:00", quality=QualityProfile(status="ok"),
                                                    access=AccessPolicy.PRIVATE_LOCAL, payload_files=("text.txt",)), ocr)
    redistributable = {"text.txt": b"public notes"}
    lib.representations.admit(DerivedRepresentation(id="notes-1", artifact_sha256=sha, kind=RepresentationKind.NORMALIZED_TEXT, extractor="t", extractor_version="1",
                                                    output_sha256=payload_digest(redistributable), derived_at="2026-09-11T00:00:00+00:00", quality=QualityProfile(status="ok"),
                                                    access=AccessPolicy.REDISTRIBUTABLE, payload_files=("text.txt",)), redistributable)
    manifest = export_library(lib, classes=frozenset({ExportClass.METADATA_SEMANTICS}), into=tmp_path / "bundle")
    names = [f for f, _ in manifest.files]
    assert f"representations/{sha}/ocr-1/representation.json" in names and f"representations/{sha}/ocr-1/text.txt" not in names
    assert f"representations/{sha}/notes-1/text.txt" in names
    everything = export_library(lib, classes=frozenset(ExportClass), into=tmp_path / "full")
    assert f"representations/{sha}/ocr-1/text.txt" in [f for f, _ in everything.files] and everything.withheld_payloads == ()


def test_missing_bytes_keep_refs_and_reimport_restores_reads_without_reminting(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    bundle = tmp_path / "bundle"
    export_library(lib, classes=frozenset({ExportClass.METADATA_SEMANTICS}), into=bundle)
    other = ManagedLibrary(tmp_path / "other-machine")
    summary = import_export(other, bundle)
    assert summary.copied and "ledger" in summary.journals_seeded and "links" in summary.journals_seeded
    assert other.artifacts.availability(sha).status == "unavailable"
    assert other.artifacts.record(sha) if other.artifacts.holds(sha) else True
    assert other.trees.get(sha, tree.id).node(theorem.id) == theorem
    assert LinkStore(tmp_path / "other-machine" / "links").get(link.id) == link
    assert SharedClaims(shared_store(tmp_path / "other-machine")).head("cyclic-subgroups").ref == link.claim
    reader = SourceReader(other)
    delivery = reader.read_statement(sha, theorem.id)
    assert delivery.unavailable and "unavailable" in delivery.unavailable and delivery.artifact_sha256 == sha
    other.import_source(ImportRequest(data=PDF, original_name="restored.pdf"))
    assert other.artifacts.availability(sha).status == "available"
    restored = reader.read_statement(sha, theorem.id)
    assert restored.unavailable is None and restored.text.startswith("Theorem 1.2.") and restored.node == theorem.id
    assert other.trees.get(sha, tree.id).id == tree.id and LinkStore(tmp_path / "other-machine" / "links").get(link.id).claim == link.claim


def test_import_never_merges_into_a_journal_with_history(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    bundle = tmp_path / "bundle"
    export_library(lib, classes=frozenset({ExportClass.METADATA_SEMANTICS}), into=bundle)
    other_root, other, _, _, _, _ = populated(tmp_path, "other")
    summary = import_export(other, bundle)
    assert "ledger" in summary.journals_kept and any("synchronization" in s for s in summary.skipped)
    assert len(SharedClaims(shared_store(other_root)).claims()) == 1


def test_restart_preserves_everything_and_index_rebuilds(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    index = SourceIndex(lib)
    first = index.rebuild()
    assert index.search("1.2")[0].entry.node == theorem.id
    reopened = ManagedLibrary(root)
    assert reopened.artifacts.record(sha).sha256 == sha and reopened.trees.preferred(sha) == tree
    assert LinkStore(root / "links").get(link.id) == link
    assert SharedClaims(shared_store(root)).head("cyclic-subgroups").id == "cyclic-subgroups"
    shutil.rmtree(root / "index")
    assert SourceIndex(reopened).digest() is None
    second = SourceIndex(reopened).rebuild()
    assert second == first and SourceIndex(reopened).search("cyclic")[0].entry.artifact_sha256 == sha
    assert SourceIndex(reopened).digest() == first


def test_formal_library_export_carries_shared_lean(tmp_path):
    root, lib, sha, tree, theorem, link = populated(tmp_path)
    lean = tmp_path / "lean"
    (lean / "HardyShared").mkdir(parents=True)
    (lean / "HardyShared" / "Cyclic.lean").write_text("theorem x : True := trivial\n", encoding="utf-8")
    manifest = export_library(lib, classes=frozenset({ExportClass.USER_FORMAL_LIBRARY}), into=tmp_path / "bundle", shared_lean=lean)
    assert [f for f, _ in manifest.files] == ["lean/HardyShared/Cyclic.lean"]
    target = tmp_path / "other-lean"
    summary = import_export(ManagedLibrary(tmp_path / "other"), tmp_path / "bundle", shared_lean=target)
    assert summary.copied == ("lean/HardyShared/Cyclic.lean",) and (target / "HardyShared" / "Cyclic.lean").is_file()


def test_export_needs_a_fresh_destination_and_import_confines_paths(tmp_path):
    import json

    import pytest

    from hardy.literature.sources.export import ExportError, safe_relative

    root, lib, sha, tree, theorem, link = populated(tmp_path)
    bundle = tmp_path / "bundle"
    export_library(lib, classes=frozenset({ExportClass.PRIVATE_SOURCE_CACHE, ExportClass.METADATA_SEMANTICS}), into=bundle)
    with pytest.raises(ExportError, match="fresh destination"):
        export_library(lib, classes=frozenset({ExportClass.METADATA_SEMANTICS}), into=bundle)
    assert (bundle / "artifacts" / sha / "content").is_file()  # the earlier bundle is untouched, not silently relabelled
    for bad in ("../escape.json", "/abs/x.json", "a/../../b", "C:/x", "a\\b", ""):
        with pytest.raises(ExportError):
            safe_relative(bad)
    assert safe_relative("ledger/00000000000000000001.json").as_posix() == "ledger/00000000000000000001.json"
    crafted = tmp_path / "crafted"
    crafted.mkdir()
    payload = b"{}"
    (crafted / "evil.json").write_bytes(payload)
    import hashlib

    manifest = {"format": "hardy.library-export/v1", "classes": ["metadata_semantics"], "artifacts": [], "withheld_payloads": [],
                "created_at": "now", "files": [["../outside.json", hashlib.sha256(payload).hexdigest()], ["evil.json", hashlib.sha256(payload).hexdigest()]]}
    (crafted / "export.json").write_text(json.dumps(manifest), encoding="utf-8")
    target = ManagedLibrary(tmp_path / "victim" / "library")
    summary = import_export(target, crafted)
    assert not (tmp_path / "victim" / "outside.json").exists()
    assert any("would leave" in s for s in summary.skipped) and summary.copied == ("evil.json",)
