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
