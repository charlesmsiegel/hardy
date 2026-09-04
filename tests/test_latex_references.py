"""A compile is judged on its references, not only on its exit status.

LaTeX resolves a missing `\\ref` to `??` and a missing `\\cite` to `[?]` and
exits 0 for both, so a document full of `??` used to come back as a clean
check and be saved. The stand-in compiler in `fake_latex.py` models the part
that makes this checkable: cross-references resolve out of the `.aux` written
by the previous pass, so a sound document is undefined on its first pass and
resolved on its second.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hardy.latex import LatexTools

COMMAND = (sys.executable, str(Path(__file__).with_name("fake_latex.py")))
PREAMBLE = "\\documentclass{article}\n\\begin{document}\n"
END = "\\end{document}\n"


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    return tree


def test_a_resolved_reference_survives_the_second_pass(tmp_path: Path):
    """The first pass has no `.aux` yet, so one pass is not a verdict."""
    source = PREAMBLE + "Theorem \\label{thm:main}. See \\ref{thm:main}.\n" + END
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert result.ok, result.output


def test_an_undefined_reference_fails_the_compile(tmp_path: Path):
    source = PREAMBLE + "See \\ref{thm:missing}.\n" + END
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert not result.ok
    assert "thm:missing" in result.output
    assert "??" in result.output


def test_an_undefined_citation_fails_the_compile(tmp_path: Path):
    source = PREAMBLE + "As shown in \\cite{nobody2020}.\n" + END
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert not result.ok
    assert "nobody2020" in result.output


def test_a_citation_with_a_bibitem_resolves(tmp_path: Path):
    """The compiler wrapper judges resolution, not authorship.

    Who may write a `\\bibitem` is a workspace rule, enforced at the save --
    see `bibliography.hand_written_bibliography` and
    `tests/test_chat_papers.py`. Down here the only question is whether the
    citation resolves, and against a `thebibliography` it does.
    """
    source = (
        PREAMBLE
        + "As shown in \\cite{nobody2020}.\n"
        + "\\begin{thebibliography}{9}\n\\bibitem{nobody2020} Nobody.\n\\end{thebibliography}\n"
        + END
    )
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert result.ok, result.output


def test_a_broken_reference_is_never_saved_or_published(tmp_path: Path):
    """The refusal has to take the save and the PDF with it.

    A compile that exits 0 published `writeup.pdf` and ran the caller's
    `commit`, so a document whose every reference reads `??` was saved as
    checked work with a PDF to match.
    """
    committed = []
    output = tmp_path / "out"
    result = LatexTools(COMMAND).check(
        PREAMBLE + "See \\ref{thm:missing}.\n" + END,
        tree=_tree(tmp_path),
        output_dir=output,
        commit=lambda: committed.append(True),
    )
    assert not result.ok
    assert committed == []
    assert not (output / "writeup.pdf").exists()


def test_a_duplicate_label_fails_the_compile(tmp_path: Path):
    source = PREAMBLE + "\\label{thm:main}\\label{thm:main}\nSee \\ref{thm:main}.\n" + END
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert not result.ok
    assert "thm:main" in result.output


def test_a_label_nothing_points_at_is_a_note_rather_than_a_refusal(tmp_path: Path):
    """The case Hardy's own completion gate requires a writeup to contain."""
    result = LatexTools(COMMAND).check(
        PREAMBLE + "Theorem \\label{thm:main}.\n" + END, tree=_tree(tmp_path)
    )
    assert result.ok, result.output
    assert "thm:main" in result.output
    assert "nothing in the document points at" in result.output


def test_a_fragment_probe_is_not_judged_on_its_siblings_labels(tmp_path: Path):
    """The fragment-first order the prompt prescribes has to stay possible.

    A fragment the root does not include yet is compiled through a probe
    root, which cannot see the labels its siblings create -- so judging that
    compile on undefined references would refuse every section that refers to
    another one, forever.
    """
    tree = _tree(tmp_path)
    (tree / "writeup.tex").write_text(PREAMBLE + END, encoding="utf-8")
    result = LatexTools(COMMAND).check(
        "Section two, which cites \\ref{thm:main} from section one.\n",
        path="sections/two.tex",
        tree=tree,
    )
    assert result.ok, result.output


def test_a_reference_into_another_fragment_of_the_same_document_resolves(tmp_path: Path):
    tree = _tree(tmp_path)
    (tree / "sections").mkdir()
    (tree / "sections" / "one.tex").write_text("Theorem \\label{thm:main}.\n", encoding="utf-8")
    (tree / "writeup.tex").write_text(
        PREAMBLE + "\\input{sections/one}\n" + END, encoding="utf-8"
    )
    result = LatexTools(COMMAND).check(
        PREAMBLE + "\\input{sections/one}\nSee \\ref{thm:main}.\n" + END, tree=tree
    )
    assert result.ok, result.output


def test_a_document_whose_numbers_never_settle_is_refused(tmp_path: Path):
    """Every pass spent and the compiler still asking for another one.

    Accepting it because nothing was reported *undefined* publishes a PDF
    whose numbers the compiler has just said are not the document's.
    """
    source = PREAMBLE + "% unstable\nTheorem \\label{thm:main}. See \\ref{thm:main}.\n" + END
    result = LatexTools(COMMAND).check(source, tree=_tree(tmp_path))
    assert not result.ok
    assert "settled" in result.output


def test_an_unsettled_document_is_not_published(tmp_path: Path):
    output = tmp_path / "out"
    committed = []
    result = LatexTools(COMMAND).check(
        PREAMBLE + "% unstable\nSee \\label{a}\\ref{a}.\n" + END,
        tree=_tree(tmp_path),
        output_dir=output,
        commit=lambda: committed.append(True),
    )
    assert not result.ok
    assert committed == []
    assert not (output / "writeup.pdf").exists()


def test_keys_are_collected_from_every_auxiliary_file(tmp_path: Path):
    r"""`\include` gives a fragment its own `.aux`.

    A reference list executed inside one writes its `\bibcite` records where
    a reader of `writeup.aux` never sees them, and the citation resolves
    anyway -- so the whole auxiliary tree is read, not the root's alone.
    """
    tree = _tree(tmp_path)
    (tree / "part.tex").write_text(
        "\\begin{thebibliography}{9}\n\\bibitem{hidden2020} Nobody.\n"
        "\\end{thebibliography}\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, ...]] = []
    LatexTools(COMMAND).check(
        PREAMBLE + "\\include{part}\n" + END,
        tree=tree,
        vouched=lambda keys: seen.append(keys) or "",
    )
    assert seen and "hidden2020" in seen[0]


def test_a_refused_reference_list_takes_the_save_with_it(tmp_path: Path):
    committed = []
    result = LatexTools(COMMAND).check(
        PREAMBLE + "Text \\label{a}\\ref{a}.\n" + END,
        tree=_tree(tmp_path),
        commit=lambda: committed.append(True),
        vouched=lambda keys: "no.",
    )
    assert not result.ok
    assert "no." in result.output
    assert committed == []


def test_an_aux_file_committed_into_the_tree_is_not_read_as_this_compile_s(
    tmp_path: Path,
):
    r"""`_copy_tree` copies everything under `tex/`, build artifacts included.

    A checkout carrying `tex/old.aux` with a `\citation` in it would be read
    as a citation of the document being checked, and every clean writeup in
    that tree refused over a file the compiler never wrote.
    """
    tree = _tree(tmp_path)
    (tree / "old.aux").write_text("\\citation{obsolete}\n", encoding="utf-8")
    seen: list[tuple[str, ...]] = []
    result = LatexTools(COMMAND).check(
        PREAMBLE + "Text.\n" + END,
        tree=tree,
        vouched=lambda keys: seen.append(keys) or "",
    )
    assert result.ok, result.output
    assert seen and "obsolete" not in seen[0]


def test_a_fragment_reached_through_another_fragment_is_the_real_document(tmp_path: Path):
    r"""Inclusion is transitive, and the reference checks follow it.

    `writeup.tex` includes `a.tex`, `a.tex` includes `b.tex`. Asking only
    whether the root's own text names `b.tex` said no, so saving `b.tex` was
    compiled through a probe root -- which exempts it from the reference
    checks -- and an undefined `\ref` in a fragment genuinely in the document
    exited zero and was committed.
    """
    tree = _tree(tmp_path)
    (tree / "a.tex").write_text("\\input{b}\n", encoding="utf-8")
    (tree / "b.tex").write_text("Text.\n", encoding="utf-8")
    (tree / "writeup.tex").write_text(PREAMBLE + "\\input{a}\n" + END, encoding="utf-8")
    result = LatexTools(COMMAND).check(
        "See \\ref{thm:missing}.\n", path="b.tex", tree=tree
    )
    assert not result.ok, result.output
    assert "thm:missing" in result.output


def test_a_compile_that_writes_no_pdf_is_not_a_success(tmp_path: Path):
    r"""Exit zero and no document is not a document.

    A draft-mode compiler setting, or `\pdfdraftmode` in the source, runs
    everything and writes no file. Publication was simply skipped while the
    check still said yes, so the save recorded the tree as freshly compiled
    and whatever `writeup.pdf` was there before stayed, presented as current.
    """
    committed = []
    output = tmp_path / "out"
    output.mkdir()
    (output / "writeup.pdf").write_bytes(b"%PDF-older")
    result = LatexTools(COMMAND).check(
        PREAMBLE + "% draftmode\nText.\n" + END,
        tree=_tree(tmp_path),
        output_dir=output,
        commit=lambda: committed.append(True),
    )
    assert not result.ok
    assert "no writeup.pdf" in result.output
    assert committed == []
    assert output.joinpath("writeup.pdf").read_bytes() == b"%PDF-older"


def test_a_compile_that_writes_no_aux_does_not_leave_the_last_ones_labels(
    tmp_path: Path,
):
    r"""`\nofiles` suppresses the auxiliary file and still produces the PDF.

    The published `.aux` was left untouched, so the save stamped the new
    source as current while the completion gate went on crediting labels from
    a document that no longer exists.
    """
    tree = _tree(tmp_path)
    output = tmp_path / "out"
    aux_dir = tmp_path / "build"
    tools = LatexTools(COMMAND)
    first = tools.check(
        PREAMBLE + "Theorem \\label{thm:main}. See \\ref{thm:main}.\n" + END,
        tree=tree,
        output_dir=output,
        aux_dir=aux_dir,
    )
    assert first.ok, first.output
    assert "thm:main" in (aux_dir / "writeup.aux").read_text(encoding="utf-8")
    again = tools.check(
        PREAMBLE + "\\nofiles\nText with no labels at all.\n" + END,
        tree=tree,
        output_dir=output,
        aux_dir=aux_dir,
    )
    assert again.ok, again.output
    assert not (aux_dir / "writeup.aux").exists(), (
        "the previous document's labels survived a compile that recorded none"
    )
