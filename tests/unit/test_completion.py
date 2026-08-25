r"""What a workspace owes, computed from the artifacts and nothing else.

The session gathers the two trees and decides what to do about the answer.
These are the rules themselves: which document counts, what counts as quoting
Lean, and what an appendix owes a reader who cannot check Lean for themselves.
"""

from __future__ import annotations

import importlib

completion = importlib.import_module("hardy.completion")

STATEMENT = "theorem hardyOne : (n : Nat) -> n = n"
REGISTRY = [{"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}]
SYLOW = {
    "formal_name": "Sylow.first",
    "lean_statement": "True",
    "latex_name": "asm:sylow",
    "informal_statement": "Sylow's first theorem.",
    "source": "Rotman 4.12",
    "reason": "not in Mathlib",
}


def document(body: str) -> dict[str, str]:
    return {"writeup.tex": "\\begin{document}\n" + body + "\n\\end{document}\n"}


def owed(tex: dict[str, str], **overrides):
    arguments = {
        "theorems": {"hardyOne": STATEMENT},
        "registry": REGISTRY,
        "labels": {"thm:one"},
        "assumptions": [],
        "used": set(),
        "tex": tex,
    }
    arguments.update(overrides)
    return completion.outstanding(**arguments)


def kinds(obligations) -> list[str]:
    return [item.kind for item in obligations]


def test_a_quoted_statement_settles_the_document() -> None:
    assert owed(document(f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}")) == ()


def test_line_breaks_in_the_paper_are_forgiven() -> None:
    """A statement wrapped to the column width is the same statement."""
    wrapped = STATEMENT.replace(" : ", " :\n    ")
    assert owed(document(f"\\begin{{verbatim}}\n{wrapped}\n\\end{{verbatim}}")) == ()


def test_a_changed_statement_is_not_forgiven() -> None:
    """The one thing this check is for: a paper that quotes something else."""
    other = STATEMENT.replace("n = n", "n = n + 0")
    assert kinds(owed(document(f"\\begin{{verbatim}}\n{other}\n\\end{{verbatim}}"))) == ["statement"]


def test_a_listing_environment_counts_too() -> None:
    body = f"\\begin{{lstlisting}}[language=Lean]\n{STATEMENT}\n\\end{{lstlisting}}"
    assert owed(document(body)) == ()


def test_an_inline_verb_counts_too() -> None:
    assert owed(document(f"\\verb|{STATEMENT}|")) == ()


def test_a_statement_in_the_prose_is_reported_as_such() -> None:
    """TeX would eat parts of it, so the reader compares against a copy that
    is not what Lean saw. A different mistake deserves a different answer."""
    found = owed(document(STATEMENT))
    assert kinds(found) == ["statement"]
    assert "not inside a verbatim block" in found[0].detail


def test_a_fragment_nothing_inputs_is_not_in_the_document() -> None:
    tex = {
        "writeup.tex": "\\begin{document}\nOne.\n\\end{document}\n",
        "sections/one.tex": f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}\n",
    }
    assert kinds(owed(tex)) == ["statement"]


def test_a_fragment_the_root_inputs_is() -> None:
    tex = {
        "writeup.tex": "\\begin{document}\n\\input{sections/one}\n\\end{document}\n",
        "sections/one.tex": f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}\n",
    }
    assert owed(tex) == ()


def test_a_theorem_nothing_records_owes_a_mapping() -> None:
    found = owed(document(f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}"), registry=[])
    assert kinds(found) == ["record"]


def test_a_label_the_compiler_never_made_is_owed() -> None:
    found = owed(document(f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}"), labels=set())
    assert kinds(found) == ["label"]


def test_a_bare_registry_name_covers_one_declaration_and_not_two() -> None:
    alone = owed(
        document("\\begin{verbatim}\ntheorem one : True\n\\end{verbatim}"),
        theorems={"A.one": "theorem one : True"},
        registry=[{"formal_name": "one", "latex_name": "thm:one", "description": "One."}],
    )
    assert alone == ()
    shared = owed(
        document("\\begin{verbatim}\ntheorem one : True\n\\end{verbatim}"),
        theorems={"A.one": "theorem one : True", "B.one": "theorem one : True"},
        registry=[{"formal_name": "one", "latex_name": "thm:one", "description": "One."}],
    )
    assert kinds(shared) == ["record", "record"]


def test_an_assumption_the_work_rests_on_owes_an_appendix() -> None:
    found = owed(
        document(f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}"),
        assumptions=[SYLOW],
        used={"Sylow.first"},
    )
    assert kinds(found) == ["appendix", "assumption", "assumption"]
    assert "Sylow.first" in found[0].detail


def test_an_appendix_carrying_both_languages_settles_it() -> None:
    body = (
        f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}\n"
        "\\appendix\n\\section{Assumptions}\n"
        "Sylow's first theorem.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert found == ()


def test_an_appendix_missing_the_lean_axiom_still_owes_it() -> None:
    """The prose and the Lean can differ, which is the reason both are asked
    for. An appendix that states only the mathematics hides exactly that."""
    body = (
        f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}\n"
        "\\appendix\nSylow's first theorem.\\label{asm:sylow}"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert kinds(found) == ["assumption"]
    assert "axiom Sylow.first : True" in found[0].detail


def test_an_approval_nobody_used_owes_nothing() -> None:
    found = owed(
        document(f"\\begin{{verbatim}}\n{STATEMENT}\n\\end{{verbatim}}"),
        assumptions=[SYLOW],
        used=set(),
    )
    assert found == ()


def test_lean_comments_do_not_have_to_match() -> None:
    """A paper may annotate the listing it shows; the proposition is what is
    being compared, and Lean reads the annotation as a comment either way."""
    body = f"\\begin{{verbatim}}\n{STATEMENT} -- reflexivity\n\\end{{verbatim}}"
    assert owed(document(body)) == ()


def test_an_empty_workspace_owes_nothing() -> None:
    assert completion.outstanding(
        theorems={}, registry=[], labels=set(), assumptions=[], used=set(), tex={}
    ) == ()


def test_a_settled_workspace_summarises_as_nothing_outstanding() -> None:
    assert completion.summary(()) == "nothing outstanding"
