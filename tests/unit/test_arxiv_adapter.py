"""A held arXiv paper registers as work, exact version, artifact and tree; PaperLibrary is untouched."""

from __future__ import annotations

import gzip
import io
import tarfile

from hardy.literature import arxiv
from hardy.literature.metadata import parse_id
from hardy.literature.sources.arxiv_adapter import register_paper
from hardy.literature.sources.contracts import AccessPolicy, NodeKind, SourceFormat
from hardy.literature.sources.library import ManagedLibrary

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{identifier}</id>
    <published>2002-11-11T18:00:00Z</published>
    <updated>2002-11-11T18:00:00Z</updated>
    <title>The entropy formula for the Ricci flow</title>
    <summary>A monotonic expression for the Ricci flow.</summary>
    <author><name>Grigori Perelman</name></author>
    <arxiv:primary_category term="math.DG"/>
    <category term="math.DG"/>
  </entry>
</feed>
"""
MAIN = b"\\documentclass{article}\n\\begin{document}\n\\section{Entropy}\n\\begin{theorem}\\label{thm:mono}\nThe entropy is monotone.\n\\end{theorem}\n\\begin{proof}\nCompute.\n\\end{proof}\n\\end{document}\n"


def bundle(*members):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def held(tmp_path, identifier="math.DG/0211159v1", with_source=True):
    papers = arxiv.PaperLibrary(tmp_path / "papers")
    answers = {"query": FEED.format(identifier=identifier).encode("utf-8"), "e-print": bundle(("main.tex", MAIN))}

    def transport(url, timeout, limit=None):
        return answers["e-print"] if "e-print" in url else answers["query"]

    client = arxiv.ArxivClient(papers, transport=transport, clock=lambda: 1_000_000.0, sleep=lambda s: None)
    record, _ = client.fetch(identifier)
    if with_source:
        client.fetch_source(identifier)
    return papers, record


def test_held_paper_registers_as_edition_and_tree_artifact(tmp_path):
    papers, record = held(tmp_path)
    library = ManagedLibrary(tmp_path / "library")
    identifier = parse_id("math.DG/0211159v1")
    report = register_paper(library, papers, identifier)
    sha = report.outcome.artifact.sha256
    artifact = report.outcome.artifact
    assert artifact.format is SourceFormat.TEX_TREE and artifact.access is AccessPolicy.PUBLIC_PROVIDER_RETRIEVABLE
    assert report.extraction.status == "ok"
    edition = library.catalog.edition_of(sha)
    assert edition is not None and edition.id == "edition-arxiv-math.DG-0211159v1" and ("arxiv", "math.DG/0211159v1") in edition.identifiers
    work = library.catalog.snapshot().work(edition.work)
    assert work.title == "The entropy formula for the Ricci flow" and work.authors == ("Grigori Perelman",)
    tree = library.build_tree(sha)
    assert any(n.kind is NodeKind.THEOREM and n.label == "thm:mono" for n in tree.nodes)
    provenance = library.artifacts.provenance(sha)[0]
    assert provenance.provider == "arxiv" and provenance.source_url.endswith("math.DG/0211159v1")
    assert papers.holds(identifier) and papers.holds_source(identifier)  # untouched
    again = register_paper(library, papers, identifier)
    assert again.outcome.reused and len(library.artifacts.provenance(sha)) == 2 and len(library.catalog.snapshot().editions) == 1


def test_versions_are_distinct_editions_of_one_work(tmp_path):
    papers_v1, _ = held(tmp_path / "one", "math.DG/0211159v1")
    papers_v2, _ = held(tmp_path / "two", "math.DG/0211159v2")
    library = ManagedLibrary(tmp_path / "library")
    first = register_paper(library, papers_v1, parse_id("math.DG/0211159v1"))
    second = register_paper(library, papers_v2, parse_id("math.DG/0211159v2"))
    editions = {e.id: e for e in library.catalog.snapshot().editions}
    assert set(editions) == {"edition-arxiv-math.DG-0211159v1", "edition-arxiv-math.DG-0211159v2"}
    assert len({e.work for e in editions.values()}) == 1
    # identical e-print bytes are one artifact; the second registration proposes a second edition but cannot make it authoritative
    assert first.outcome.artifact.sha256 == second.outcome.artifact.sha256
    assert library.catalog.edition_of(first.outcome.artifact.sha256).id == "edition-arxiv-math.DG-0211159v1"
    assert [p.edition for p in library.catalog.candidates_for(first.outcome.artifact.sha256)] == ["edition-arxiv-math.DG-0211159v2"]


def test_paper_without_source_registers_its_metadata_text(tmp_path):
    papers, record = held(tmp_path, with_source=False)
    library = ManagedLibrary(tmp_path / "library")
    report = register_paper(library, papers, parse_id("math.DG/0211159v1"))
    assert report.outcome.artifact.format is SourceFormat.TEXT
    assert library.catalog.edition_of(report.outcome.artifact.sha256) is not None
