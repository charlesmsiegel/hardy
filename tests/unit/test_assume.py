"""Reading a paper's statements out of its source, and naming them in Lean.

The inventory is eager and the minting is lazy, so this module has to be able
to list far more than anyone will assume: everything a reader would call a
result, in the order the paper states it, with whatever the paper itself calls
it. What it must never do is invent a number the paper does not carry.
"""

from __future__ import annotations

import pytest

from hardy import assume

PAPER = r"""
\documentclass{article}
\newtheorem{theorem}{Theorem}
\newtheorem{lemma}[theorem]{Lemma}
\begin{document}
\section{Introduction}
We prove the following.

\begin{theorem}[Main estimate]\label{thm:main}
For every $\epsilon > 0$ there is a $\delta$ with $f(\delta) < \epsilon$.
\end{theorem}

\begin{lemma}\label{lem:aux}
The function $f$ is continuous.
\end{lemma}

\input{sections/second}
\end{document}
"""

SECOND = r"""
\begin{proposition}\label{prop:late}
Every widget is a gadget.
\end{proposition}

\begin{verbatim}
\begin{theorem}
This one is printed, not asserted.
\end{theorem}
\end{verbatim}
"""


@pytest.fixture
def files() -> dict[str, str]:
    return {"main.tex": PAPER, "sections/second.tex": SECOND}


# --- The inventory -------------------------------------------------------------


def test_every_statement_the_paper_makes_is_listed_in_reading_order(files) -> None:
    found = assume.inventory(files)

    assert [item.kind for item in found] == ["theorem", "lemma", "proposition"]
    assert [item.label for item in found] == ["thm:main", "lem:aux", "prop:late"]
    assert found[0].heading == "Main estimate"
    assert "every $\\epsilon > 0$" in found[0].text.lower()
    assert found[2].file == "sections/second.tex"


def test_a_statement_shown_in_a_listing_is_not_one(files) -> None:
    """The paper is *about* that block; it does not assert it."""
    found = assume.inventory(files)

    assert not any("printed, not asserted" in item.text for item in found)


def test_each_statement_has_a_reference_the_model_can_name(files) -> None:
    found = assume.inventory(files)

    assert [item.ref for item in found] == ["thm:main", "lem:aux", "prop:late"]
    assert assume.find(found, "thm:main") is found[0]
    assert assume.find(found, "nonexistent") is None


def test_an_unlabelled_statement_is_referenced_by_its_position(files) -> None:
    """Hardy's own ordinal, not the number the paper prints -- nothing here
    runs TeX, and a made-up "Theorem 2" in a docstring is a citation nobody
    can follow."""
    found = assume.inventory({"main.tex": PAPER.replace("\\label{thm:main}", "")})

    assert found[0].ref == "theorem-1"
    assert found[0].number == ""


def test_a_paper_with_no_root_document_is_refused() -> None:
    with pytest.raises(assume.AssumeError, match="root"):
        assume.inventory({"notes.tex": "Some loose text.\n"})


def test_a_paper_whose_root_is_not_called_main_is_still_read() -> None:
    found = assume.inventory({"paper.tex": PAPER.replace("\\input{sections/second}", "")})

    assert [item.kind for item in found] == ["theorem", "lemma"]


def test_the_inventory_is_bounded(files) -> None:
    """A pathological source must not produce an unbounded listing."""
    body = "\n".join(
        f"\\begin{{theorem}}\\label{{t{n}}}Statement {n}.\\end{{theorem}}" for n in range(5_000)
    )
    found = assume.inventory(
        {"main.tex": "\\documentclass{article}\\begin{document}\n" + body + "\n\\end{document}"}
    )

    assert len(found) == assume.MAX_STATEMENTS


def test_a_statement_body_is_bounded(files) -> None:
    long_body = "x " * 20_000
    found = assume.inventory(
        {
            "main.tex": "\\documentclass{article}\\begin{document}\n"
            + f"\\begin{{theorem}}\\label{{t}}{long_body}\\end{{theorem}}\n"
            + "\\end{document}"
        }
    )

    assert len(found[0].text) <= assume.MAX_STATEMENT_CHARACTERS


# --- Where a minted axiom lives -------------------------------------------------


def test_a_cite_key_becomes_a_lean_namespace() -> None:
    assert assume.namespace_for("perelman2002entropy-3f9a1c2b4d5") == (
        "Papers.perelman2002entropy_3f9a1c2b4d5"
    )


def test_a_namespace_component_never_starts_with_a_digit() -> None:
    """Lean will not parse `Papers.2401foo`, and a module Hardy cannot save
    is worse than an ugly name."""
    assert assume.namespace_for("2401bar-abc").startswith("Papers.P")


def test_a_namespace_refuses_a_key_with_nothing_usable_in_it() -> None:
    with pytest.raises(assume.AssumeError, match="cite key"):
        assume.namespace_for("---")


def test_the_module_path_follows_the_namespace() -> None:
    assert assume.module_path_for("perelman2002entropy-3f9a") == (
        "Papers/perelman2002entropy_3f9a.lean"
    )


# --- The module Hardy writes ----------------------------------------------------


def _minted(**overrides) -> assume.Minted:
    fields = {
        "formal_name": "main_estimate",
        "lean_statement": "∀ ε : ℝ, 0 < ε → True",
        "informal_statement": "For every positive epsilon something holds.",
        "kind": "statement",
        "ref": "thm:main",
        "heading": "Main estimate",
        "paper_text": "For every $\\epsilon > 0$ there is a $\\delta$.",
    }
    fields.update(overrides)
    return assume.Minted(**fields)


def test_the_module_states_each_axiom_under_the_paper_namespace() -> None:
    source = assume.render_module(
        cite_key="perelman2002entropy-3f9a",
        arxiv_id="math.DG/0211159v1",
        title="The entropy formula for the Ricci flow",
        statements=(_minted(),),
    )

    assert "namespace Papers.perelman2002entropy_3f9a" in source
    assert "axiom main_estimate : ∀ ε : ℝ, 0 < ε → True" in source
    assert source.rstrip().endswith("end Papers.perelman2002entropy_3f9a")


def test_every_axiom_says_which_paper_and_which_statement_it_is() -> None:
    source = assume.render_module(
        cite_key="perelman2002entropy-3f9a",
        arxiv_id="math.DG/0211159v1",
        title="The entropy formula for the Ricci flow",
        statements=(_minted(),),
    )

    assert "/--" in source
    assert "thm:main" in source
    assert "math.DG/0211159v1" in source
    assert "perelman2002entropy-3f9a" in source
    assert "Main estimate" in source
    assert "For every positive epsilon something holds." in source


def test_the_docstring_cannot_be_closed_from_the_paper_s_own_text() -> None:
    r"""A paper containing `-/` would otherwise end the docstring early and
    put the rest of an author's sentence in front of the Lean parser."""
    source = assume.render_module(
        cite_key="k-1",
        arxiv_id="2401.00001v1",
        title="A paper about /- comments -/ in Lean",
        statements=(_minted(paper_text="closes with -/ and then axiom evil : False"),),
    )

    docstring, _, rest = source.split("/--", 1)[1].partition("-/")
    assert "axiom evil" in docstring, "the paper's words belong in the docstring"
    assert "axiom evil" not in rest, "and never where Lean would read a declaration"
    assert "axiom main_estimate" in rest


def test_an_opaque_constant_says_it_widens_the_trust_base() -> None:
    source = assume.render_module(
        cite_key="k-1",
        arxiv_id="2401.00001v1",
        title="A paper",
        statements=(
            _minted(kind="constant", formal_name="Widget", lean_statement="Type"),
        ),
    )

    assert "opaque Widget : Type" in source
    assert "added trust" in source.lower()


def test_the_module_is_regenerated_whole_rather_than_appended_to() -> None:
    """Two statements, one file, in the order they were minted."""
    source = assume.render_module(
        cite_key="k-1",
        arxiv_id="2401.00001v1",
        title="A paper",
        statements=(_minted(formal_name="first"), _minted(formal_name="second", ref="lem:aux")),
    )

    assert source.index("axiom first") < source.index("axiom second")
    assert source.count("namespace Papers.k_1") == 1
