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


def test_spine_resolves_encoded_relative_urls_and_rejects_escape():
    from hardy.literature.sources.epub import EpubAdapter

    container = CONTAINER.replace('OEBPS/content.opf', 'OEBPS/pkg/content%20file.opf')
    package = OPF.replace('chapter1.xhtml', '../Text/ch%201.xhtml#start').replace('chapter2.xhtml', '../../../escape.xhtml')
    items, _, diagnostics = EpubAdapter()._spine(epub({'META-INF/container.xml': container,
        'OEBPS/pkg/content file.opf': package, 'OEBPS/Text/ch 1.xhtml': CH1}))
    assert items == [('OEBPS/Text/ch 1.xhtml', CH1)]
    assert len(diagnostics) == 1 and diagnostics[0].code == 'spine_item_unreadable'


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_xml_refuses_declarations_beyond_prefix(encoding):
    from hardy.literature.sources.adapters import ExtractionRefused
    from hardy.literature.sources.epub import _parse_xml

    source = "<?xml version='1.0'?>" + ' ' * 5000 + "<!DOCTYPE r [<!ENTITY a 'AAAA'>]><r>&a;</r>"
    with pytest.raises(ExtractionRefused, match='DOCTYPE'):
        _parse_xml(source.encode(encoding), 'padded')


@pytest.mark.parametrize('br', ['<br>', '<br/>'])
def test_void_elements_keep_sibling_paths_and_exact_ranges(br):
    from hardy.literature.sources.epub import scan_html

    text, nodes, _ = scan_html(f'<html><body><img id="fig"><p>a{br}b</p><hr><p>c</p></body></html>')
    assert text == 'a\nb\nc\n'
    assert [(n['path'], n['start'], n['end']) for n in nodes] == [
        ('html[1]/body[1]/img[1]', 0, 0), ('html[1]/body[1]/p[1]', 0, 3),
        ('html[1]/body[1]/p[2]', 4, 5)]


@pytest.mark.parametrize('formula,expected', [
    ('<mi>x</mi><mo>&gt;</mo><mn>0</mn>', 'x > 0'),
    ('<semantics><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation><annotation-xml><mi>duplicate</mi></annotation-xml></semantics>', 'x^2'),
])
def test_mathml_text_preserves_formula_and_exact_block_range(tmp_path, formula, expected):
    lib = ManagedLibrary(tmp_path / 'library')
    source = f'<html><body><p>Let <math>{formula}</math> be positive.</p></body></html>'
    report = lib.import_source(ImportRequest(data=source.encode(), original_name='math.html'))
    sha = report.outcome.artifact.sha256
    rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    text = lib.representations.text(sha, rep.id)
    assert text == f'Let {expected} be positive.\n'
    assert any(d.code == 'mathml_rendered_as_text' for d in rep.quality.diagnostics)
    from hardy.literature.sources.epub import scan_html
    _, nodes, _ = scan_html(source)
    assert text[nodes[0]['start']:nodes[0]['end']] == text.rstrip('\n')


@pytest.mark.parametrize('tag', ['math', 'svg'])
def test_recovery_preserves_later_paragraph_and_reports_quality(tmp_path, tag):
    lib = ManagedLibrary(tmp_path / 'library')
    source = f'<html><body><p>Let <{tag}><mi>x</mi> be.</p></stray><p>Second paragraph text.</p></body></html>'
    report = lib.import_source(ImportRequest(data=source.encode(), original_name='broken.html'))
    sha = report.outcome.artifact.sha256
    rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    assert 'Second paragraph text.' in lib.representations.text(sha, rep.id)
    assert any(d.code == 'recovered_markup' for d in rep.quality.diagnostics)
    from hardy.literature.sources.epub import scan_html
    _, nodes, _ = scan_html(source)
    assert nodes[-1]['path'] == 'html[1]/body[1]/p[2]'
    assert nodes[-1]['end'] > nodes[-1]['start']


def test_scanner_accumulation_work_is_linear():
    from hardy.literature.sources.epub import _Blocks

    class CountedChunks(list):
        visited = 0

        def __iter__(self):
            for chunk in super().__iter__():
                self.visited += len(chunk)
                yield chunk

    for count in (1000, 2000):
        parser = _Blocks()
        parser.text = CountedChunks()
        parser.feed('<html><body>' + '<p>paragraph</p>' * count + '</body></html>')
        parser.close()
        parser.finish()
        assert ''.join(parser.text) == 'paragraph\n' * count
        assert parser.text.visited <= parser.length * 2


def test_mathml_linearization_and_svg_omission_do_not_claim_complete_coverage(tmp_path):
    lib = ManagedLibrary(tmp_path / 'library')
    source = b'<html><body><p><math><mfrac><mi>x</mi><mi>y</mi></mfrac></math><svg><text>diagram</text></svg></p></body></html>'
    report = lib.import_source(ImportRequest(data=source, original_name='lossy.html'))
    sha = report.outcome.artifact.sha256
    rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    assert rep.quality.status == 'partial' and rep.quality.coverage is None
    assert {'mathml_rendered_as_text', 'svg_omitted'} <= {d.code for d in rep.quality.diagnostics}


def test_empty_whitespace_chunk_does_not_create_extra_block_separator():
    from hardy.literature.sources.epub import scan_html

    text, nodes, _ = scan_html('<p>a</p>  <span id="b"></span><p>c</p>')
    assert text == 'a\nc\n'
    assert nodes[1]['start'] == nodes[1]['end'] == 2


def test_encrypted_epub_reports_extraction_refused(tmp_path):
    raw = bytearray(epub())
    raw[6] |= 1
    central = raw.index(b'PK\x01\x02')
    raw[central + 8] |= 1
    library = ManagedLibrary(tmp_path / 'library')
    report = library.import_source(ImportRequest(data=bytes(raw), original_name='encrypted.epub'))
    assert report.extraction.status == 'failed'
    assert any(item.code == 'extraction_refused' for item in report.extraction.diagnostics)


def test_scanner_golden_nested_blocks_inline_ids_whitespace_and_links():
    from hardy.literature.sources.epub import scan_html

    text, nodes, links = scan_html('<div><p>a <span id="s">b</span> c</p><p> d </p></div>')
    assert text == 'a \nb c\nd \n'
    assert [(n['path'], n['start'], n['end']) for n in nodes] == [
        ('div[1]', 0, 10), ('div[1]/p[1]', 0, 6), ('div[1]/p[1]/span[1]', 3, 4), ('div[1]/p[2]', 7, 9)]
    assert links == []
    text, nodes, links = scan_html('<p> a <a href="#x">link</a> </p> \n<p id="x">b</p>')
    assert text == ' a link \nb\n'
    assert [(n['path'], n['start'], n['end']) for n in nodes] == [('p[1]', 0, 8), ('p[2]', 9, 10)]
    assert links == [('x', 3)]
