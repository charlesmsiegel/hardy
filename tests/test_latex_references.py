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


def test_the_diagnostic_is_inside_the_output_limit_too(tmp_path: Path):
    r"""The compiler's output was capped and the verdict appended after it.

    A document with thousands of unresolved references makes an arbitrarily
    long report, so the answer sailed past the cap the limit exists to be.
    """
    missing = "".join(f"See \\ref{{thm:missing{n}}}.\n" for n in range(2_000))
    result = LatexTools(COMMAND, output_limit=4_000).check(
        PREAMBLE + missing + END, tree=_tree(tmp_path)
    )
    assert not result.ok
    assert len(result.output) <= 4_000 + len("exit=0 elapsed=0.000s\n") + 8
    # The exit status survives the cut, and what is kept is Hardy's verdict
    # rather than TeX's chatter.
    assert result.output.startswith("exit=")
    assert "did not resolve" in result.output or "thm:missing" in result.output


def test_a_macro_included_fragment_is_still_compiled_and_still_caught(tmp_path: Path):
    r"""Where `reached_fragments` stops, and what holds anyway.

    `\newcommand{\body}{\input{part}}` followed by `\body` includes
    `part.tex`, and the scan -- which reads commands rather than expanding
    macros -- calls it unreached. So saving `part.tex` alone is compiled
    through a probe and its references are not judged. What this pins is that
    the gap is bounded: the fragment is still compiled, so malformed TeX is
    still refused, and the next save of the root judges the whole tree.
    """
    tree = _tree(tmp_path)
    (tree / "part.tex").write_text("Text.\n", encoding="utf-8")
    root = (
        "\\documentclass{article}\n\\newcommand{\\body}{\\input{part}}\n"
        "\\begin{document}\n\\body\n\\end{document}\n"
    )
    (tree / "writeup.tex").write_text(root, encoding="utf-8")
    tools = LatexTools(COMMAND)
    # Not judged on its references, because the scan cannot see the macro.
    lenient = tools.check("See \\ref{thm:missing}.\n", path="part.tex", tree=tree)
    assert lenient.ok
    # But it WAS compiled -- the compiler's own warning is in the answer -- so
    # source that is not TeX at all is still refused here.
    assert "thm:missing" in lenient.output
    broken = tools.check("\\input{nothing-at-all}\n", path="part.tex", tree=tree)
    assert not broken.ok
    # And the root's own save judges the whole document, undefined ref and all.
    (tree / "part.tex").write_text("See \\ref{thm:missing}.\n", encoding="utf-8")
    whole = tools.check(root, tree=tree)
    assert not whole.ok
    assert "thm:missing" in whole.output


def test_a_pdf_left_in_the_tree_is_not_mistaken_for_this_compile_s(tmp_path: Path):
    r"""`_copy_tree` hands the compiler the tree, artifacts included.

    A checked-in or left-behind `tex/writeup.pdf` made `pdf.exists()` true for
    a compile that wrote no document at all, so the refusal added for exactly
    that case passed -- and the OLD file was published as the new source's,
    with the evidence supplied by the tree being checked.
    """
    tree = _tree(tmp_path)
    (tree / "writeup.pdf").write_bytes(b"%PDF-committed")
    output = tmp_path / "out"
    committed = []
    result = LatexTools(COMMAND).check(
        PREAMBLE + "% draftmode\nText.\n" + END,
        tree=tree,
        output_dir=output,
        commit=lambda: committed.append(True),
    )
    assert not result.ok
    assert "no writeup.pdf" in result.output
    assert committed == []
    assert not (output / "writeup.pdf").exists()


def test_a_stale_table_of_contents_is_not_read_as_this_compile_s(tmp_path: Path):
    r"""`.aux` was the first artifact found; it is not the only one.

    Under `\nofiles` LaTeX reads an existing `.toc`, does not rewrite it, and
    puts last time's section titles and page numbers into a PDF that exits
    zero -- which the save then publishes and stamps as current.
    """
    tree = _tree(tmp_path)
    for name in ("writeup.toc", "writeup.out", "writeup.lof", "writeup.lot"):
        (tree / name).write_text("stale\n", encoding="utf-8")
    (tree / "figure.pdf").write_bytes(b"%PDF-figure")
    result = LatexTools(COMMAND).check(
        PREAMBLE + "% list-inputs\nText.\n" + END, tree=tree
    )
    assert result.ok, result.output
    handed = next(
        line for line in result.output.splitlines() if line.startswith("inputs: ")
    )
    for artifact in ("writeup.toc", "writeup.out", "writeup.lof", "writeup.lot"):
        assert artifact not in handed, handed
    # And an ordinary input of the same shape is still handed over: a `.pdf`
    # figure is a document's own file, not the compiler's leavings.
    assert "figure.pdf" in handed


def test_the_output_cap_counts_bytes_not_characters(tmp_path: Path):
    """A budget in bytes was being enforced by slicing characters.

    A report naming many multibyte labels came back several times the limit
    it had just been cut to -- the same mismatch the record wrapping had.
    """
    missing = "".join(f"See \\ref{{数式{n}}}.\n" for n in range(2_000))
    result = LatexTools(COMMAND, output_limit=4_000).check(
        PREAMBLE + missing + END, tree=_tree(tmp_path)
    )
    assert not result.ok
    assert len(result.output.encode("utf-8")) <= 4_000 + 64
    assert result.output.startswith("exit=")
