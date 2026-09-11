"""EPUB and HTML keep their DOM and map normalized text back to it; zips obey the archive rules."""

from __future__ import annotations

import io
import zipfile

import pytest
from pdf_helpers import book_pages, build_pdf

from hardy.literature import archives
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import (
    BibliographicWork,
    DOMLocator,
    EditionOrVersion,
    GroupingDecision,
    GroupingProposal,
    IdentityEvidence,
    NodeKind,
    ObservationKind,
    RepresentationKind,
    RepresentationSpan,
    SourceFormat,
    WorkKind,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import project, resolve_span

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Tiny Algebra</dc:title><dc:creator>A. Author</dc:creator><dc:identifier id="uid">urn:isbn:9780387902449</dc:identifier>
    <dc:publisher>Springer</dc:publisher><dc:date>1977-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine><itemref idref="ch2"/><itemref idref="ch1"/></spine>
</package>"""
CH1 = """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter 1</title><style>p {color: red}</style></head>
<body><h1 id="c1">Chapter 1. Groups</h1><p id="def11">Definition 1.1. A subgroup is a subset closed under the operation.</p>
<p id="thm12">Theorem 1.2. Every subgroup of a cyclic group is cyclic.</p><p>Proof. Take the least positive element. Q.E.D.</p>
<p>See <a href="#thm12">Theorem 1.2</a> above.</p></body></html>"""
CH2 = """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="c2">Chapter 2. Rings</h1><p>Lemma 2.1. Every ideal of Z is principal.</p></body></html>"""


def epub(members=None, mimetype=True):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if mimetype:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, content in (members or {"META-INF/container.xml": CONTAINER, "OEBPS/content.opf": OPF, "OEBPS/chapter1.xhtml": CH1,
                                          "OEBPS/chapter2.xhtml": CH2, "OEBPS/style.css": "p {}"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_epub_preserves_spine_and_maps_text_to_dom(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    report = lib.import_source(ImportRequest(data=epub(), original_name="tiny.epub"))
    sha = report.outcome.artifact.sha256
    assert report.outcome.artifact.format is SourceFormat.EPUB and report.extraction.status == "ok", report.extraction.diagnostics
    dom = lib.representations.list(sha, RepresentationKind.DOM)[0]
    text_rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    text = lib.representations.text(sha, text_rep.id)
    assert text.index("Chapter 2. Rings") < text.index("Chapter 1. Groups")  # spine order, not manifest order
    assert "color: red" not in text
    (mapping,) = lib.representations.mappings(sha, left=dom.id)
    at = text.index("Theorem 1.2. Every subgroup")
    counterparts = project(mapping, RepresentationSpan(representation=text_rep.id, start=at, end=at + 10))
    assert counterparts and isinstance(counterparts[0], DOMLocator) and counterparts[0].spine_item == "OEBPS/chapter1.xhtml"
    assert "p[" in counterparts[0].node_path
    kinds = {o.kind for o in report.extraction.observations}
    assert {ObservationKind.HEADING, ObservationKind.LABEL, ObservationKind.REFERENCE, ObservationKind.PAGE_BREAK} <= kinds
    labels = {o.value("label") for o in report.extraction.observations if o.kind is ObservationKind.LABEL}
    assert {"c1", "def11", "thm12", "c2"} <= labels
    assert dict(report.extraction.metadata) == {"title": "Tiny Algebra", "author": "A. Author", "isbn": "9780387902449", "venue": "Springer", "year": "1977"}
    assert report.proposals and report.proposals[0].evidence[0].kind in {"publisher_metadata", "isbn"}


def test_epub_headings_build_tree_without_model(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    sha = lib.import_source(ImportRequest(data=epub())).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    chapters = [n for n in tree.nodes if n.kind is NodeKind.CHAPTER]
    assert [c.title for c in chapters] == ["Rings", "Groups"] and all(c.number_origin == "explicit" for c in chapters)
    theorem = next(n for n in tree.nodes if n.kind is NodeKind.THEOREM)
    assert theorem.parent == chapters[1].id and theorem.number == "1.2"
    texts = lib.representations.texts(sha)
    assert resolve_span(theorem.statement_span, texts).startswith("Theorem 1.2. Every subgroup of a cyclic group is cyclic.")


def test_epub_tree_is_separate_from_pdf_tree_of_same_edition(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    pdf = lib.import_source(ImportRequest(data=build_pdf(book_pages(), title="Tiny Algebra"))).outcome.artifact.sha256
    book = lib.import_source(ImportRequest(data=epub())).outcome.artifact.sha256
    work = BibliographicWork(id="work-tiny", kind=WorkKind.BOOK, title="Tiny Algebra", authors=("A. Author",))
    edition = EditionOrVersion(id="edition-tiny", work=work.id, label="First", identifiers=(("isbn", "9780387902449"),))
    catalog = lib.catalog
    catalog.add_work(work, expected_revision=catalog.snapshot().revision)
    catalog.add_edition(edition, expected_revision=catalog.snapshot().revision)
    for index, sha in enumerate((pdf, book), start=1):
        catalog.propose_grouping(GroupingProposal(id=f"prop-same-{index}", artifact_sha256=sha, edition=edition.id, proposer="test",
                                                  evidence=(IdentityEvidence(kind="isbn", value="9780387902449", provenance="test"),)), expected_revision=catalog.snapshot().revision)
        catalog.decide_grouping(GroupingDecision(id=f"dec-same-{index}", proposal=f"prop-same-{index}", status="authoritative", decided_by="user:c", reason="same book"),
                                expected_revision=catalog.snapshot().revision)
    assert catalog.artifacts_of(edition.id) == tuple(sorted((pdf, book)))
    pdf_tree, epub_tree = lib.build_tree(pdf), lib.build_tree(book)
    assert pdf_tree.artifact_sha256 == pdf and epub_tree.artifact_sha256 == book and pdf_tree.id != epub_tree.id
    pdf_theorem = next(n for n in pdf_tree.nodes if n.number == "1.2")
    epub_theorem = next(n for n in epub_tree.nodes if n.number == "1.2")
    assert pdf_theorem.id != epub_theorem.id and lib.trees.correspondences(pdf) == ()


def test_html_file_uses_the_same_pipeline(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    report = lib.import_source(ImportRequest(data=CH1.encode("utf-8"), original_name="chapter1.html"))
    assert report.outcome.artifact.format is SourceFormat.HTML and report.extraction.status == "ok"
    assert dict(report.extraction.metadata)["title"] == "Chapter 1"
    tree = lib.build_tree(report.outcome.artifact.sha256)
    assert any(n.kind is NodeKind.THEOREM and n.number == "1.2" for n in tree.nodes)


def test_broken_or_doctyped_epubs_are_refused_not_crashed(tmp_path):
    lib = ManagedLibrary(tmp_path / "library")
    no_container = lib.import_source(ImportRequest(data=epub({"OEBPS/content.opf": OPF}), original_name="x.epub"))
    assert no_container.extraction.status == "failed" and "container.xml" in no_container.extraction.diagnostics[0].detail
    bomb = OPF.replace("<?xml version=\"1.0\"?>", "<?xml version=\"1.0\"?><!DOCTYPE lol [<!ENTITY lol \"lol\">]>")
    doctyped = lib.import_source(ImportRequest(data=epub({"META-INF/container.xml": CONTAINER, "OEBPS/content.opf": bomb, "OEBPS/chapter1.xhtml": CH1, "OEBPS/chapter2.xhtml": CH2}), original_name="y.epub"))
    assert doctyped.extraction.status == "failed" and "DOCTYPE" in doctyped.extraction.diagnostics[0].detail


def test_zip_traversal_and_bomb_are_refused(tmp_path):
    into = tmp_path / "into"
    into.mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", "x")
    with pytest.raises(archives.ArchiveError):
        archives.extract(buffer.getvalue(), into)
    assert list(into.iterdir()) == []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.txt", b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(archives.ArchiveError, match="limit for one file|inflates past"):
        archives.extract(buffer.getvalue(), into, limits=archives.Limits(max_file_bytes=1024 * 1024, max_total_bytes=1024 * 1024))
    assert list(into.iterdir()) == []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(archives.ArchiveError, match="symlink"):
        archives.extract(buffer.getvalue(), into)
    good = epub()
    extraction = archives.extract(good, into)
    assert extraction.kind == "zip" and {f.path for f in extraction.files} >= {"mimetype", "META-INF/container.xml"}
