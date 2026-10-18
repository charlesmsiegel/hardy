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
    DerivedRepresentation,
    NativeSourceSpan,
    NodeKind,
    ObservationKind,
    QualityProfile,
    RepresentationKind,
    SourceEdgeKind,
    SourceFormat,
)
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.locators import project, resolve_span
from hardy.literature.sources.tex import observe_tex, pack_directory
from hardy.literature.statements import inventory

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


@pytest.mark.parametrize(
    ("environment", "inventory_kind", "structural_kind"),
    (
        ("fact", "fact", None),
        ("assumption", "assumption", None),
        ("hypothesis", "hypothesis", None),
        ("remark", None, "remark"),
        ("exercise", None, "exercise"),
        ("construction", None, "construction"),
        ("thm", "theorem", "theorem"),
        ("definition*", "definition", "definition"),
    ),
)
def test_inventory_and_navigation_keep_distinct_statement_policies(environment, inventory_kind, structural_kind):
    text = (
        r"\documentclass{article}\begin{document}"
        + f"\\begin{{{environment}}}A source claim.\\end{{{environment}}}"
        + r"\end{document}"
    )
    representation = DerivedRepresentation(
        id="rep-text", artifact_sha256="a" * 64, kind=RepresentationKind.NORMALIZED_TEXT,
        extractor="test", extractor_version="1", output_sha256="b" * 64,
        derived_at="2026-09-11T00:00:00+00:00", quality=QualityProfile(status="ok"),
        access="private_local", payload_files=("text.txt",),
    )

    inventoried = inventory({"main.tex": text})
    observed = observe_tex(representation, text, representation.artifact_sha256)

    assert [statement.kind for statement in inventoried] == ([] if inventory_kind is None else [inventory_kind])
    assert [observation.value("kind") for observation in observed if observation.kind is ObservationKind.STATEMENT_START] == (
        [] if structural_kind is None else [structural_kind]
    )


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
    assert not refs
    assert any(e.kind is SourceEdgeKind.SOURCE_REFERS_TO and e.target == lemma.id for e in tree.edges)


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


def test_tex_labels_belong_to_their_own_environment(tmp_path):
    source = r'''\documentclass{article}
\begin{document}
\begin{remark} short \end{remark}
\begin{theorem}[Own]\label{thm:x} body \end{theorem}
\begin{lemma}\begin{equation}\label{eq:nested}x=y\end{equation}\end{lemma}
\end{document}'''
    _, report = imported(tmp_path, source.encode(), name="main.tex")
    statements = [o for o in report.extraction.observations if o.kind is ObservationKind.STATEMENT_START]
    assert [o.value("label") for o in statements] == ["", "thm:x", ""]


def test_literal_form_feed_is_not_a_file_boundary(tmp_path):
    lib, report = imported(tmp_path, MAIN.replace(r"\input{lemmas}", "").replace("Introduction", "Intro\fduction").encode(), name="main.tex")
    sha = report.outcome.artifact.sha256
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    assert "Intro\fduction" in lib.representations.text(sha, assembled.id)
    assert not [o for o in report.extraction.observations if o.kind is ObservationKind.PAGE_BREAK]


def test_coverage_counts_reached_files_not_root_segments(tmp_path):
    lib, report = imported(tmp_path, bundle(
        ("main.tex", MAIN.encode()), ("lemmas.tex", LEMMAS.encode()),
        ("unread.tex", b"Unreached text."),
    ))
    sha = report.outcome.artifact.sha256
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    assert assembled.quality.coverage == pytest.approx(2 / 3)


def test_inserted_separators_are_explicitly_unmapped(tmp_path):
    lib, report = imported(tmp_path, bundle(("main.tex", MAIN.encode()), ("lemmas.tex", LEMMAS.encode())))
    sha = report.outcome.artifact.sha256
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    native = lib.representations.list(sha, RepresentationKind.NATIVE_SOURCE)[0]
    mapping, = lib.representations.mappings(sha, left=native.id)
    text = lib.representations.text(sha, assembled.id)
    assert mapping.partial
    unmapped = len(text) - sum(right.end - right.start for _, right in mapping.pairs)
    assert unmapped == 2
    diagnostic = next(d for d in report.extraction.diagnostics if d.code == "unmapped_synthetic_text")
    assert diagnostic.detail.startswith("2 assembled character(s)")


def test_tex_label_references_and_citations(tmp_path):
    source = MAIN.replace(r"See Lemma \ref{lem:key}.", r"See Lemma \ref{lem:key}, \eqref{eq:one}, \cite{paper}, \ref{missing}.")
    lib, report = imported(tmp_path, bundle(("main.tex", source.encode()), ("lemmas.tex", LEMMAS.encode())))
    tree = lib.build_tree(report.outcome.artifact.sha256)
    refs = [e for e in tree.edges if e.kind is SourceEdgeKind.SOURCE_REFERS_TO]
    assert {tree.node(e.target).label for e in refs} == {"lem:key", "eq:one"}
    assert [d.detail for d in tree.diagnostics if d.code == "unresolved_reference"] == ["label missing is referenced but no such unit was recovered"]


def test_native_mapping_uses_raw_offsets_after_comments_and_inclusions(tmp_path):
    main = "\\documentclass{article}\n% Long comment before the first segment\n\\begin{document}\nIntro.\n\\input{sec}\nAfter input.\n\\end{document}\n"
    child = "% Child comment\n\\begin{theorem}\\label{thm:x} body \\end{theorem}\n"
    lib, report = imported(tmp_path, bundle(("main.tex", main.encode()), ("sec.tex", child.encode())))
    sha = report.outcome.artifact.sha256
    native = lib.representations.list(sha, RepresentationKind.NATIVE_SOURCE)[0]
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    sources = json.loads(lib.representations.payload(sha, native.id, "sources.json"))
    text = lib.representations.text(sha, assembled.id)
    mapping, = lib.representations.mappings(sha, left=native.id)
    for left, right in mapping.pairs:
        assert sources[left.path][left.start:left.end] == text[right.start:right.end]
    after = text.index("After input.")
    from hardy.literature.sources.contracts import RepresentationSpan
    mapped = project(mapping, RepresentationSpan(representation=assembled.id, start=after, end=after + 12))
    assert mapped[0].start == main.index("After input.")


def test_directory_tar_orders_portable_posix_member_names(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "z.tex").write_text("child")
    (tmp_path / "a.tex").write_text("root")
    with tarfile.open(fileobj=io.BytesIO(pack_directory(tmp_path))) as tar:
        assert tar.getnames() == ["a.tex", "a/z.tex"]


def test_tex_boundary_observation_scan_does_not_rescan_text(tmp_path):
    from hardy.literature.sources.tex import observe_tex

    class CountedText(str):
        scanned = 0

        def find(self, *args):
            self.scanned += len(self)
            return super().find(*args)

        def split(self, *args):
            self.scanned += len(self)
            return super().split(*args)

    lib, report = imported(tmp_path, MAIN.encode(), name="main.tex")
    sha = report.outcome.artifact.sha256
    rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    text = CountedText("\f".join(["x" * 100] * 3000))
    separators = tuple(100 + index * 101 for index in range(2999))
    observations = observe_tex(rep, text, sha, separators=separators)
    breaks = [o for o in observations if o.kind is ObservationKind.PAGE_BREAK]
    assert [o.anchor.locator.start for o in breaks] == list(separators)
    assert text.scanned <= len(text)


def test_mapping_keeps_live_offsets_after_masked_tex_and_crlf(tmp_path):
    source = "\\documentclass{article}\r\n\\begin{document}\r\n% hidden label \\label{comment}\r\n\\newcommand{\\hidden}{\\begin{theorem}\\label{macro}secret\\end{theorem}}\r\n\\begin{verbatim}\\label{quoted}\\end{verbatim} Live.\r\n\\iffalse\\label{false}\\fi\r\n\\begin{theorem}\\label{live} True.\\end{theorem}\r\n\\end{document}"
    lib, report = imported(tmp_path, source.encode(), name="main.tex")
    sha = report.outcome.artifact.sha256
    native = lib.representations.list(sha, RepresentationKind.NATIVE_SOURCE)[0]
    assembled = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    text = lib.representations.text(sha, assembled.id)
    mapping, = lib.representations.mappings(sha, left=native.id)
    for left, right in mapping.pairs:
        assert source[left.start:left.end] == text[right.start:right.end]
    from hardy.literature.sources.contracts import RepresentationSpan
    start = text.index(r"\label{live}")
    mapped = project(mapping, RepresentationSpan(representation=assembled.id, start=start, end=start + 12))
    assert mapped[0].start <= source.index(r"\label{live}") < mapped[0].end
    left, right = next((left, right) for left, right in mapping.pairs if right.start <= start and start + 12 <= right.end)
    assert left.start + start - right.start == source.index(r"\label{live}")
    labels = [o.value("label") for o in report.extraction.observations if o.kind is ObservationKind.LABEL]
    assert labels == ["live"]
    assert mapping.partial
    assert any(d.code == "unmapped_synthetic_text" for d in report.extraction.diagnostics)


def test_environment_owns_label_after_nested_environment_closes(tmp_path):
    source = r'''\documentclass{article}
\begin{document}
\begin{theorem}\begin{equation}\label{eq:x}x\end{equation}\label{thm:own}Own\end{theorem}
\begin{lemma}By \ref{thm:own} and \eqref{eq:x}.\end{lemma}
\end{document}'''
    lib, report = imported(tmp_path, source.encode(), name="main.tex")
    statements = [o for o in report.extraction.observations if o.kind is ObservationKind.STATEMENT_START]
    assert [o.value("label") for o in statements] == ["thm:own", ""]
    tree = lib.build_tree(report.outcome.artifact.sha256)
    refs = [e for e in tree.edges if e.kind is SourceEdgeKind.SOURCE_REFERS_TO]
    assert {tree.node(e.target).label for e in refs} == {"thm:own", "eq:x"}
    assert not [d for d in tree.diagnostics if d.code == "unresolved_reference"]


@pytest.mark.parametrize("labels, missing", [("a,b", []), ("a,missing,b", ["missing"])])
def test_grouped_cref_resolves_each_recovered_label(tmp_path, labels, missing):
    source = (r"\documentclass{article}\begin{document}"
              r"\begin{lemma}\label{a}A\end{lemma}"
              r"\begin{lemma}\label{b}B\end{lemma}"
              r"\begin{theorem}By \cref{" + labels + r"}.\end{theorem}\end{document}")
    lib, report = imported(tmp_path, source.encode(), name="main.tex")
    tree = lib.build_tree(report.outcome.artifact.sha256)
    refs = [edge for edge in tree.edges if edge.kind is SourceEdgeKind.SOURCE_REFERS_TO]
    assert [tree.node(edge.target).label for edge in refs] == ["a", "b"]
    assert [diagnostic.detail for diagnostic in tree.diagnostics if diagnostic.code == "unresolved_reference"] == [
        f"label {label} is referenced but no such unit was recovered" for label in missing
    ]
    assert refs[0].anchor == refs[1].anchor


@pytest.mark.parametrize("explicit_preference", [False, True])
def test_historical_tree_replay_reuses_revision_without_changing_preference(tmp_path, explicit_preference):
    from hardy.literature.sources.representations import payload_digest

    lib, report = imported(tmp_path, MAIN.encode(), name="main.tex")
    sha = report.outcome.artifact.sha256
    first_rep = lib.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[0]
    first = lib.build_tree(sha, representation=first_rep.id)
    payloads = {"text.txt": (lib.representations.text(sha, first_rep.id) + "\nExtra prose.").encode()}
    second_rep = first_rep.model_copy(update={"id": "normalized_text-other", "output_sha256": payload_digest(payloads)})
    lib.representations.admit(second_rep, payloads)
    second = lib.build_tree(sha, representation=second_rep.id)
    if explicit_preference:
        lib.trees.prefer(sha, second.id, reason="user chose this revision", expected_revision=lib.trees.revision())
    journal_revision = lib.trees.revision()
    replay = lib.build_tree(sha, representation=first_rep.id)
    assert replay == first
    assert lib.trees.get(sha, first.id) == first and lib.trees.get(sha, second.id) == second
    assert lib.trees.preferred(sha) == second
    assert lib.trees.revision() == journal_revision


def test_plain_ref_can_target_a_label_containing_a_comma(tmp_path):
    source = (r"\documentclass{article}\begin{document}"
              r"\begin{lemma}\label{a,b}A\end{lemma}"
              r"\begin{theorem}By \ref{a,b}.\end{theorem}\end{document}")
    lib, report = imported(tmp_path, source.encode(), name="main.tex")
    tree = lib.build_tree(report.outcome.artifact.sha256)
    refs = [edge for edge in tree.edges if edge.kind is SourceEdgeKind.SOURCE_REFERS_TO]
    assert [tree.node(edge.target).label for edge in refs] == ["a,b"]
    assert not [diagnostic for diagnostic in tree.diagnostics if diagnostic.code == "unresolved_reference"]


def test_native_inclusions_use_conditionals_declared_in_the_root(tmp_path):
    main = r"\documentclass{article}\newif\ifdraft\begin{document}\input{part}\end{document}"
    part = r"\iffalse\ifdraft X\fi\input{ghost}\fi\input{live}"
    ghost = r"\begin{lemma}\label{ghost}Hidden\end{lemma}"
    live = r"\begin{lemma}\label{live}Shown\end{lemma}"
    lib, report = imported(tmp_path, bundle(("main.tex", main.encode()), ("part.tex", part.encode()),
                                         ("ghost.tex", ghost.encode()), ("live.tex", live.encode())))
    labels = [o.value("label") for o in report.extraction.observations if o.kind is ObservationKind.LABEL]
    assert labels == ["live"]
    tree = lib.build_tree(report.outcome.artifact.sha256)
    assert {node.label for node in tree.nodes if node.kind is NodeKind.LEMMA} == {"live"}
