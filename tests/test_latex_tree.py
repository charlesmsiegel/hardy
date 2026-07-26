from __future__ import annotations

import sys
from pathlib import Path

from hardy.latex import LatexTools

COMMAND = (sys.executable, str(Path(__file__).with_name("fake_latex.py")))
ROOT = "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n"
FINE = "\\documentclass{article}\n\\begin{document}Fine.\\end{document}\n"


def test_input_resolves_against_the_saved_tree(tmp_path: Path):
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    (tree / "sections" / "one.tex").write_text("Section one.\\label{sec:one}\n", encoding="utf-8")
    result = LatexTools(COMMAND).check(
        "Section one, revised.\\label{sec:one}\n", path="sections/one.tex", tree=tree
    )
    assert result.ok


def test_a_candidate_root_overrides_the_saved_one(tmp_path: Path):
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    (tree / "writeup.tex").write_text("broken", encoding="utf-8")
    result = LatexTools(COMMAND).check(FINE, tree=tree)
    assert result.ok


def test_check_still_works_with_no_tree(tmp_path: Path):
    assert LatexTools(COMMAND).check(FINE).ok


def test_a_fragment_is_actually_compiled_when_the_root_ignores_it(tmp_path: Path):
    """The fragment-first order the prompt prescribes.

    Compiling the unchanged root would check nothing about the candidate, so
    malformed source would be saved as though it had been checked.
    """
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    (tree / "writeup.tex").write_text(FINE, encoding="utf-8")
    broken = LatexTools(COMMAND).check(
        "\\input{nowhere}\n", path="sections/one.tex", tree=tree
    )
    assert not broken.ok, "a fragment the root does not include must still be compiled"
    good = LatexTools(COMMAND).check("Section one.\n", path="sections/one.tex", tree=tree)
    assert good.ok


def test_a_fragment_cannot_be_saved_before_a_root_exists(tmp_path: Path):
    """Otherwise a fragment that happens to be a whole document publishes a PDF
    the durable tree has no root for."""
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    result = LatexTools(COMMAND).check(FINE, path="sections/one.tex", tree=tree)
    assert not result.ok
    assert "writeup.tex" in result.output


def test_the_aux_records_the_labels_that_were_created(tmp_path: Path):
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    aux = tmp_path / "build"
    document = (
        "\\documentclass{article}\n\\begin{document}\n"
        "Real.\\label{thm:real}\n% \\label{thm:commented}\n\\end{document}\n"
    )
    assert LatexTools(COMMAND).check(document, tree=tree, output_dir=tmp_path, aux_dir=aux).ok
    written = (aux / "writeup.aux").read_text()
    assert "thm:real" in written
    assert "thm:commented" not in written


def test_a_fragment_is_judged_by_the_root_that_includes_it(tmp_path: Path):
    """A fragment has no preamble; compiling it alone would fail for a reason
    that says nothing about the mathematics."""
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    assert LatexTools(COMMAND).check("Just prose.\n", path="sections/one.tex", tree=tree).ok


def test_the_pdf_lands_in_the_output_directory(tmp_path: Path):
    output = tmp_path / "workspace"
    assert LatexTools(COMMAND).check(FINE, output_dir=output).ok
    assert (output / "writeup.pdf").read_bytes() == b"%PDF-fake"


def test_a_braced_mention_is_not_an_inclusion(tmp_path: Path):
    r"""`{sections/one}` in a comment, or as another command's argument, means
    nothing to TeX. Reading it as an inclusion would leave the fragment
    uncompiled while reporting it as checked."""
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    mentioned = (
        "\\documentclass{article}\n"
        "% see \\input{sections/one} for the argument\n"
        "\\begin{document}\\index{sections/one}Body.\\end{document}\n"
    )
    (tree / "writeup.tex").write_text(mentioned, encoding="utf-8")
    # The fragment is not really included, so it must be compiled on its own
    # through a probe root -- and a broken one must fail.
    broken = LatexTools(COMMAND).check("\\input{nowhere}\n", path="sections/one.tex", tree=tree)
    assert not broken.ok


def test_a_real_inclusion_is_recognised(tmp_path: Path):
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    assert LatexTools(COMMAND).check("Revised.\n", path="sections/one.tex", tree=tree).ok
