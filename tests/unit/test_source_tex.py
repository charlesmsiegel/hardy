"""TeX trees keep every file's identity and inclusion structure, and nothing runs."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile

import pytest

from hardy.foundation import process
from hardy.literature.sources.adapters import ExtractionRefused
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import (
    NativeSourceSpan,
    NodeKind,
    ObservationKind,
    RepresentationKind,
    SourceEdgeKind,
    SourceFormat,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import project, resolve_span
from hardy.literature.sources.tex import pack_directory

MAIN = r"""\documentclass{article}
\title{Fibers of the Prym map}
\author{Ron Donagi}
\begin{document}
\section{Introduction}
\begin{theorem}[Main]\label{thm:main}
The Prym map is generically injective for $g \ge 7$.
\end{theorem}
\begin{proof}
See Lemma \ref{lem:key}.
\end{proof}
\input{lemmas}
\end{document}
"""
LEMMAS = r"""\section{Lemmas}
\begin{lemma}\label{lem:key}
A key lemma.
\end{lemma}
\begin{equation}\label{eq:one}
x = y
\end{equation}
"""


def bundle(*members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def imported(tmp_path, data, name="source.tar.gz"):
    lib = ManagedLibrary(tmp_path / "library")
    report = lib.import_source(ImportRequest(data=data, original_name=name))
    return lib, report


def test_tex_tree_import_preserves_file_digests_and_includes(tmp_path):
    lib, report = imported(tmp_path, bundle(("main.tex", MAIN.encode()), ("lemmas.tex", LEMMAS.encode()), ("figure.png", b"\x89PNG\x00binary")))
    sha = report.outcome.artifact.sha256
    assert report.outcome.artifact.format is SourceFormat.TEX_TREE and report.extraction.status == "ok"
    native = lib.representations.list(sha, RepresentationKind.NATIVE_SOURCE)[0]
    manifest = json.loads(lib.representations.payload(sha, native.id, "files.json"))
    by_path = {m["path"]: m for m in manifest}
    assert set(by_path) == {"main.tex", "lemmas.tex", "figure.png"} and by_path["figure.png"]["text"] is False
    assert all(len(m["sha256"]) == 64 for m in manifest)
    sources = json.loads(lib.representations.payload(sha, native.id, "sources.json"))
    assert sources["lemmas.tex"] == LEMMAS
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    text = lib.representations.text(sha, assembled.id)
    assert text.index("Introduction") < text.index("\f") < text.index("Lemmas") and dict(assembled.configuration)["root"] == "main.tex"
    (mapping,) = lib.representations.mappings(sha, left=native.id)
    lemma_at = text.index(r"\begin{lemma}")
    from hardy.literature.sources.contracts import RepresentationSpan

    counterparts = project(mapping, RepresentationSpan(representation=assembled.id, start=lemma_at, end=lemma_at + 13))
    assert counterparts and isinstance(counterparts[0], NativeSourceSpan) and counterparts[0].path == "lemmas.tex"
    assert dict(report.extraction.metadata) == {"title": "Fibers of the Prym map", "author": "Ron Donagi"}


def test_tex_ingest_never_executes_or_compiles(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("ingestion spawned a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(process, "run_process", refuse)
    monkeypatch.setattr(process, "run_guarded", refuse)
    hostile = MAIN.replace(r"\begin{document}", r"\immediate\write18{rm -rf /}" + "\n" + r"\begin{document}")
    lib, report = imported(tmp_path, bundle(("main.tex", hostile.encode()), ("lemmas.tex", LEMMAS.encode())))
    assert report.extraction.status == "ok"
    tree = lib.build_tree(report.outcome.artifact.sha256)
    assert any(n.kind is NodeKind.THEOREM for n in tree.nodes)


def test_tex_environments_build_tree_without_model(tmp_path):
    lib, report = imported(tmp_path, bundle(("main.tex", MAIN.encode()), ("lemmas.tex", LEMMAS.encode())))
    sha = report.outcome.artifact.sha256
    kinds = {o.kind for o in report.extraction.observations}
    assert {ObservationKind.HEADING, ObservationKind.STATEMENT_START, ObservationKind.PROOF_START, ObservationKind.PROOF_END, ObservationKind.LABEL,
            ObservationKind.REFERENCE, ObservationKind.ENVIRONMENT} <= kinds
    tree = lib.build_tree(sha)
    theorem = next(n for n in tree.nodes if n.kind is NodeKind.THEOREM)
    assert theorem.label == "thm:main" and theorem.title == "Main" and theorem.number is None and theorem.number_origin == "none"
    lemma = next(n for n in tree.nodes if n.kind is NodeKind.LEMMA)
    assert lemma.label == "lem:key"
    proof = next(n for n in tree.nodes if n.kind is NodeKind.PROOF)
    assert (proof.id, theorem.id) in {(e.source, e.target) for e in tree.edges if e.kind is SourceEdgeKind.PROOF_OF}
    assert proof.boundary_status == "high"
    equation = next(n for n in tree.nodes if n.kind is NodeKind.EQUATION)
    texts = lib.representations.texts(sha)
    assert resolve_span(equation.span, texts).startswith(r"\begin{equation}") and resolve_span(equation.span, texts).endswith(r"\end{equation}")
    sections = [n for n in tree.nodes if n.kind is NodeKind.SECTION]
    assert [s.title for s in sections] == ["Introduction", "Lemmas"] and lemma.parent == sections[1].id
    refs = [d for d in tree.diagnostics if d.code == "unresolved_reference"]
    assert refs  # \ref{lem:key} names a label, not a printed number; the builder says so rather than inventing a target


def test_single_tex_file_and_packed_directory_are_admissible(tmp_path):
    lib, report = imported(tmp_path, MAIN.encode(), name="main.tex")
    assert report.outcome.artifact.format is SourceFormat.TEX_TREE and report.extraction.status == "ok"
    directory = tmp_path / "paper"
    directory.mkdir()
    (directory / "main.tex").write_text(MAIN, encoding="utf-8")
    (directory / "lemmas.tex").write_text(LEMMAS, encoding="utf-8")
    (directory / ".git").mkdir()
    (directory / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    packed = pack_directory(directory)
    again = pack_directory(directory)
    assert packed == again
    lib2, report2 = imported(tmp_path / "second", packed)
    assert report2.extraction.status == "ok" and lib2.build_tree(report2.outcome.artifact.sha256).nodes


def test_directory_import_refuses_symlinks_and_traversal(tmp_path):
    directory = tmp_path / "paper"
    directory.mkdir()
    (directory / "main.tex").write_text(MAIN, encoding="utf-8")
    outside = tmp_path / "outside.tex"
    outside.write_text("secret", encoding="utf-8")
    try:
        (directory / "link.tex").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(ExtractionRefused, match="symlink"):
        pack_directory(directory)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("../escape.tex")
        info.size = len(MAIN)
        tar.addfile(info, io.BytesIO(MAIN.encode()))
    lib, report = imported(tmp_path / "lib", buffer.getvalue())
    assert report.extraction.status == "failed" and "refused" in report.extraction.diagnostics[0].detail
    assert lib.artifacts.holds(report.outcome.artifact.sha256)
