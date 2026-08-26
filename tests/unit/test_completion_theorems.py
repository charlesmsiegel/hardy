r"""An asserted theorem must leave the reader something to check it against.

The graded writeup carried four `\begin{theorem}` environments, one `\label`,
and nothing behind any of them -- while `report_result` correctly refused the
session twice. The report gate guards the claim; this one guards the document,
which is the artifact a human actually submits.
"""

from __future__ import annotations

import importlib

completion = importlib.import_module("hardy.completion")

THEOREM_STYLE = (
    "\\newtheorem{theorem}{Theorem}\n"
    "\\newtheorem{lemma}[theorem]{Lemma}\n"
    "\\begin{document}\n"
)


def _tex(body: str) -> dict[str, str]:
    return {"writeup.tex": THEOREM_STYLE + body + "\n\\end{document}\n"}


def _owed(tex, **overrides):
    arguments = {
        "theorems": {},
        "registry": [],
        "labels": set(),
        "assumptions": [],
        "used": set(),
        "tex": tex,
    }
    arguments.update(overrides)
    return [item for item in completion.outstanding(**arguments) if item.kind == "theorem"]


def test_a_theorem_environment_backed_by_nothing_is_owed() -> None:
    """Four of these, one label, zero backed, is what got graded C-."""
    owed = _owed(_tex("\\begin{theorem}\nGroups of prime order are abelian.\n\\end{theorem}"))

    assert len(owed) == 1
    assert "backed by nothing" in owed[0].detail


def test_a_lemma_environment_is_exempt() -> None:
    """Hardy already treats a saved lemma as scaffolding that owes no writeup.
    The document side of the rule says the same thing."""
    assert not _owed(_tex("\\begin{lemma}\nA step.\n\\end{lemma}"))


def test_a_theorem_labelled_for_a_saved_theorem_is_backed() -> None:
    assert not _owed(
        _tex("\\begin{theorem}\\label{PrimeAbelian}\nAbelian.\n\\end{theorem}"),
        theorems={"prime_abelian": "theorem prime_abelian : True :="},
        registry=[
            {"formal_name": "prime_abelian", "latex_name": "PrimeAbelian", "description": "x"}
        ],
        labels={"PrimeAbelian"},
    )


def test_a_theorem_labelled_for_an_approved_assumption_is_backed() -> None:
    """An appendix stating an approved axiom inside a theorem environment is
    honest: the appendix is where an assumption is supposed to be displayed."""
    assert not _owed(
        _tex("\\begin{theorem}\\label{Sylow}\nSylow.\n\\end{theorem}"),
        registry=[{"formal_name": "sylow", "latex_name": "Sylow", "description": "x"}],
        labels={"Sylow"},
        assumptions=[
            {
                "formal_name": "sylow",
                "lean_statement": "True",
                "latex_name": "Sylow",
                "informal_statement": "x",
                "source": "y",
                "reason": "z",
            }
        ],
    )


def test_a_label_nothing_backs_is_still_owed() -> None:
    owed = _owed(
        _tex("\\begin{theorem}\\label{Invented}\nAnything.\n\\end{theorem}"),
        labels={"Invented"},
    )

    assert len(owed) == 1
    assert "Invented" in owed[0].detail


def test_a_theorem_environment_in_a_fragment_counts() -> None:
    assert (
        len(
            _owed(
                {
                    "writeup.tex": THEOREM_STYLE
                    + "\\input{tex/appendix.tex}\n\\end{document}\n",
                    "tex/appendix.tex": "\\begin{theorem}\nUnbacked.\n\\end{theorem}\n",
                }
            )
        )
        == 1
    )


def test_a_theorem_inside_an_unexpanded_macro_is_not_an_assertion() -> None:
    r"""`executed` keeps a `\newcommand` body. `without_definitions` is what
    knows the difference between defining a block and typesetting one, and
    without it this gate's first false positive is a document that was honest."""
    assert not _owed(
        _tex("\\newcommand{\\exampleblock}{\\begin{theorem}Not asserted.\\end{theorem}}")
    )


def test_a_theorem_shown_inside_a_listing_is_not_an_assertion() -> None:
    assert not _owed(
        _tex("\\begin{verbatim}\n\\begin{theorem}\nshown\n\\end{theorem}\n\\end{verbatim}")
    )


def test_an_environment_titled_something_else_is_not_a_theorem() -> None:
    """The printed word decides, not the environment's name."""
    assert not _owed(
        {
            "writeup.tex": "\\newtheorem{theorem}{Remark}\n\\begin{document}\n"
            "\\begin{theorem}\nA remark.\n\\end{theorem}\n\\end{document}\n"
        }
    )


def test_an_environment_named_thm_and_titled_theorem_is_one() -> None:
    owed = _owed(
        {
            "writeup.tex": "\\newtheorem{thm}{Theorem}\n\\begin{document}\n"
            "\\begin{thm}\nUnbacked.\n\\end{thm}\n\\end{document}\n"
        }
    )

    assert len(owed) == 1


def test_a_theorem_obligation_blocks_a_report_by_having_no_subject() -> None:
    """`_report_result` blocks on `not item.subject`, which is what an
    obligation about the document itself looks like."""
    owed = _owed(_tex("\\begin{theorem}\nUnbacked.\n\\end{theorem}"))

    assert owed[0].subject == ""
