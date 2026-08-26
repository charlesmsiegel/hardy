r"""What the PDF says about itself.

`report_result` refused the graded run twice and was right both times. It
changed nothing: `save_latex` never refuses, so a five-page PDF asserting four
theorems over zero machine-checked Lean was compiled, published, and handed to
a grader. The gate that was missing was on the artifact, not on the claim.

The banner goes into the scratch copy `check` already compiles from, so it is
never in the saved source and the model cannot remove it from the document a
reader opens.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hardy.latex import LatexTools, stamped

COMMAND = (sys.executable, str(Path(__file__).parents[1] / "fake_latex.py"))
DOCUMENT = "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"


def test_the_stamp_lands_after_begin_document() -> None:
    out = stamped(DOCUMENT, "0 theorems machine-checked.")

    assert out.index("0 theorems machine-checked") > out.index("\\begin{document}")
    assert out.index("0 theorems machine-checked") < out.index("Hello")


def test_a_document_without_begin_document_is_returned_unchanged() -> None:
    """A fragment being probed, or a file too broken to compile. Neither is
    worth failing a build over."""
    fragment = "\\section{A fragment}\n"

    assert stamped(fragment, "anything") == fragment


def test_no_stamp_is_a_document_unchanged() -> None:
    assert stamped(DOCUMENT, None) == DOCUMENT


def test_the_stamp_introduces_no_package_and_no_definition() -> None:
    """Plain LaTeX only. A banner that needs a package can break an author's
    preamble, and breaking the build to enforce it inverts the priority."""
    out = stamped(DOCUMENT, "text")

    assert "\\usepackage" not in out
    assert "\\newcommand" not in out


def test_the_saved_source_does_not_carry_the_stamp(tmp_path: Path) -> None:
    """The banner is on the compiled copy. The author's file stays the author's."""
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    saved = tree / "writeup.tex"

    def commit() -> None:
        saved.write_text(DOCUMENT, encoding="utf-8")

    result = LatexTools(COMMAND).check(
        DOCUMENT, tree=tree, commit=commit, stamp="PROVENANCE-MARKER"
    )

    assert result.ok
    assert "PROVENANCE-MARKER" not in saved.read_text(encoding="utf-8")


def test_saving_a_fragment_still_stamps_the_root_that_is_compiled(tmp_path: Path) -> None:
    r"""Stamping `source` put the banner into a fragment with no
    `\begin{document}`, where it vanished -- so saving a section published an
    unstamped PDF while saving the root published a stamped one.

    Read back through the `.aux`, because that is the compiler's own record of
    what it processed: a label that reaches it is a label the compiler really
    created, from the root it really compiled.
    """
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n",
        encoding="utf-8",
    )
    (tree / "sections" / "one.tex").write_text("Section one.\n", encoding="utf-8")
    published = tmp_path / "out"
    published.mkdir()

    result = LatexTools(COMMAND).check(
        "Section one, revised.\\label{sec:one}\n",
        path="sections/one.tex",
        tree=tree,
        output_dir=published,
        aux_dir=published,
        stamp="\\label{StampReachedTheCompiler}",
    )

    assert result.ok
    assert "StampReachedTheCompiler" in (published / "writeup.aux").read_text(encoding="utf-8")


def test_the_session_stamp_counts_only_audited_theorems(session) -> None:
    """`_saved_theorems` is a textual scan. A banner that overstates what Lean
    checked is worse than no banner."""
    stamp = session._stamp()

    assert "0 theorems machine-checked by Lean" in stamp
    assert "0 assumptions approved" in stamp


def test_the_stamp_carries_the_goal(session) -> None:
    session.set_goal("No simple nonabelian group of order < 60.")

    assert "No simple nonabelian group of order < 60." in session._stamp()


def test_reporting_a_result_does_not_stale_the_writeup(session) -> None:
    """Whether a report was made is the session's bookkeeping, not a property
    of the document. Counting it staled the PDF on every accepted report, so a
    second report sat behind a recompile that changed no source."""
    before = session._tex_signature()
    session.state.setdefault("reports", []).append({"theorems": ["t"], "summary": "s"})

    assert session._tex_signature() == before


def test_setting_a_goal_makes_the_writeup_stale(session) -> None:
    before = session._tex_signature()
    session.set_goal("A goal.")

    assert session._tex_signature() != before


def test_the_signature_does_not_recurse_through_the_stamp(session) -> None:
    """`_stamp` asks for the obligations, `_stale_writeup` is one of them, and
    it asks for this signature. Hashing the stamp's inputs rather than its text
    is what breaks that loop."""
    assert session._tex_signature() == session._tex_signature()


def test_approving_an_assumption_makes_the_writeup_stale(session) -> None:
    """The direction that matters. A PDF compiled before an assumption was
    approved goes on saying nothing was assumed while the work rests on
    something -- which is the failure this whole design exists to prevent."""
    before = session._tex_signature()
    session.state["assumptions"].append(
        {"formal_name": "sylow", "lean_statement": "True", "latex_name": "Sylow",
         "informal_statement": "x", "source": "y", "reason": "z"}
    )

    assert session._tex_signature() != before


def test_saving_another_theorem_does_not_stale_the_writeup(session) -> None:
    """The other direction. A banner naming fewer machine-checked theorems than
    exist understates, and the ratchet already forces the writeup to carry the
    new theorem before anything is reportable."""
    before = session._tex_signature()
    (session.lean_workspace.root / "Extra.lean").parent.mkdir(parents=True, exist_ok=True)
    (session.lean_workspace.root / "Extra.lean").write_text(
        "theorem extra : True := trivial\n", encoding="utf-8"
    )

    assert session._tex_signature() == before
