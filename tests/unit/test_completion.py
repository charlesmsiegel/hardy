r"""What a workspace owes, computed from the artifacts and nothing else.

The session gathers the two trees and decides what to do about the answer.
These are the rules themselves: which document counts, what counts as quoting
Lean, and what an appendix owes a reader who cannot check Lean for themselves.
"""

from __future__ import annotations

import importlib

import pytest

completion = importlib.import_module("hardy.documents.completion")

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
    assert kinds(found) == ["appendix", "assumption", "assumption", "assumption"]
    assert "Sylow.first" in found[0].detail
    # Both languages and the link between them: the axiom Lean was given, the
    # label that names the statement, and the mathematics itself.
    details = " ".join(item.detail for item in found[1:])
    assert "axiom Sylow.first : True" in details
    assert "asm:sylow" in details
    assert "Sylow's first theorem" in details


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


def test_a_commented_out_listing_shows_the_reader_nothing() -> None:
    r"""`% \begin{verbatim} ... \end{verbatim}` on one line displays nothing.

    LaTeX discards the whole line, so a reader sees no Lean at all -- the same
    reason the label gate reads the compiler's `.aux` rather than the source.
    """
    tex = document("One.\n%\\begin{verbatim} " + STATEMENT + " \\end{verbatim}")
    assert kinds(owed(tex)) == ["statement"]


def test_a_percent_inside_a_listing_is_lean_and_not_a_comment() -> None:
    """`n % 2 = 0` is ordinary Lean, and stripping TeX comments inside a
    verbatim block would cut the statement in half."""
    statement = "theorem hardyOne : n % 2 = 0"
    tex = document("\\begin{verbatim}\n" + statement + "\n\\end{verbatim}")
    assert owed(tex, theorems={"hardyOne": statement}) == ()


def test_a_listing_after_a_commented_line_still_counts() -> None:
    body = "% a note\n\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}"
    assert owed(document(body)) == ()


def test_string_literals_are_compared_rather_than_blanked() -> None:
    """Blanked, `"a" = "a"` and `"b" = "b"` are the same run of spaces."""
    mine = 'theorem one : "a" = "a"'
    theirs = 'theorem one : "b" = "b"'
    tex = document("\\begin{verbatim}\n" + theirs + "\n\\end{verbatim}")
    assert kinds(owed(tex, theorems={"hardyOne": mine})) == ["statement"]


def test_an_appendix_with_a_bare_label_states_nothing() -> None:
    """A label says the document identifies an assumption. It does not say
    what was assumed, and the reader cannot look that up in session.json."""
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\n\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert kinds(found) == ["assumption"]
    assert "Sylow's first theorem" in found[0].detail


def test_the_approved_wording_may_sit_in_a_longer_sentence() -> None:
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\nWe assume Sylow's first theorem, unproved here.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    assert owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    ) == ()


def test_tex_escapes_do_not_hide_the_approved_wording() -> None:
    """A document that had to escape a character to compile still states it."""
    assumption = {**SYLOW, "informal_statement": "100% of finite groups"}
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\n100\\% of finite groups.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    assert owed(
        document(body),
        assumptions=[assumption],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    ) == ()


def test_an_input_shown_in_a_listing_pulls_in_nothing() -> None:
    r"""`\input{hidden}` displayed in a listing is an example of an inclusion.

    TeX never reads the file, so a statement quoted there is in front of
    nobody -- while the label the real document makes still counts, which is
    what made this a way to satisfy every obligation with an invisible file.
    """
    tex = {
        "writeup.tex": (
            "\\begin{document}\nOne.\n"
            "\\begin{verbatim}\n\\input{hidden}\n\\end{verbatim}\n"
            "\\end{document}\n"
        ),
        "hidden.tex": "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n",
    }
    assert kinds(owed(tex)) == ["statement"]


def test_an_appendix_shown_in_a_listing_opens_nothing() -> None:
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\appendix\n\\end{verbatim}\n"
        "Sylow's first theorem.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert "appendix" in kinds(found)


def test_a_lean_listing_is_not_the_informal_statement() -> None:
    """An approval whose wording happens to appear in the Lean quotation was
    answered by that quotation -- the listing standing in for the explanation
    it exists to be checked against."""
    assumption = {**SYLOW, "informal_statement": "True"}
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\n\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    found = owed(
        document(body),
        assumptions=[assumption],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert kinds(found) == ["assumption"]


def test_a_quotation_must_begin_at_a_token_boundary() -> None:
    """`FAKEtheorem t : True` ends where `theorem t : True` ends."""
    body = "\\begin{verbatim}\nFAKE" + STATEMENT + "\n\\end{verbatim}"
    assert kinds(owed(document(body))) == ["statement"]


def test_whitespace_inside_a_string_literal_is_not_collapsed() -> None:
    mine = 'theorem one : "a  b" = "a  b"'
    theirs = 'theorem one : "a b" = "a b"'
    tex = document("\\begin{verbatim}\n" + theirs + "\n\\end{verbatim}")
    assert kinds(owed(tex, theorems={"hardyOne": mine})) == ["statement"]


def test_a_statement_may_still_be_rewrapped_around_its_literals() -> None:
    mine = 'theorem one : "a  b" = "a  b"'
    theirs = 'theorem one :\n    "a  b" = "a  b"'
    tex = document("\\begin{verbatim}\n" + theirs + "\n\\end{verbatim}")
    assert owed(tex, theorems={"hardyOne": mine}) == ()


def test_alltt_does_not_count_as_a_verbatim_quotation() -> None:
    """`alltt` keeps TeX's grouping, so a listing of `{α : Type}` loses its
    braces and shows the reader a statement Lean never saw."""
    body = "\\begin{alltt}\n" + STATEMENT + "\n\\end{alltt}"
    assert kinds(owed(document(body))) == ["statement"]


def test_a_listing_in_a_false_branch_is_typeset_by_nobody() -> None:
    body = (
        "\\iffalse\n\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n\\fi\n"
    )
    assert kinds(owed(document(body))) == ["statement"]


def test_a_listing_after_a_false_branch_still_counts() -> None:
    body = (
        "\\iffalse\nnot typeset\n\\fi\n"
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}"
    )
    assert owed(document(body)) == ()


def test_a_false_branch_nests_the_conditionals_inside_it() -> None:
    """The `\\fi` of an `\\ifx` written inside `\\iffalse` closes the `\\ifx`,
    not the false branch. Stopping at it credited a listing TeX never typeset
    as a quotation shown to the reader."""
    source = "\\iffalse\n\\ifx a b \\fi\n\\begin{verbatim}\ntheorem t : 1 = 1\n\\end{verbatim}\n\\fi\n"
    assert completion.displayed(source).quoted == ()
    body = (
        "\\iffalse\n\\ifnum 1 = 1 \\fi\n\\begin{verbatim}\n" + STATEMENT
        + "\n\\end{verbatim}\n\\fi\n"
    )
    assert kinds(owed(document(body))) == ["statement"]


def test_a_false_branch_nests_only_real_conditionals() -> None:
    """`\\iff` is the logic symbol and has no `\\fi`: counting it would leave
    the branch open to the end of the file and hide the listing after it. A
    conditional the document declares with `\\newif` does nest, in whichever
    file declares it, and a `\\fi` in a comment closes nothing."""
    after = "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}"
    assert owed(document("\\iffalse\n$a \\iff b$\n\\fi\n" + after)) == ()
    hidden = "\\iffalse\n\\ifdraft x \\fi\n" + after + "\n\\fi\n"
    assert kinds(owed(document("\\newif\\ifdraft\n" + hidden))) == ["statement"]
    tex = {
        "writeup.tex": "\\newif\\ifdraft\n\\begin{document}\n\\input{body}\n\\end{document}\n",
        "body.tex": hidden,
    }
    assert kinds(owed(tex)) == ["statement"]
    assert kinds(owed(document("\\iffalse\n% \\fi\n" + after + "\n\\fi\n"))) == ["statement"]


def test_a_relative_inclusion_path_is_followed() -> None:
    tex = {
        "writeup.tex": "\\begin{document}\n\\input{./sections/one}\n\\end{document}\n",
        "sections/one.tex": "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n",
    }
    assert owed(tex) == ()


def test_a_disclosure_in_the_body_is_not_a_disclosure_in_the_appendix() -> None:
    """Label, prose and listing in the body, with an empty `\appendix` after
    them, cleared every check while the appendix stated nothing at all."""
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "Sylow's first theorem.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}\n"
        "\\appendix\n"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert kinds(found) == ["assumption", "assumption"]


def test_an_appendix_in_an_included_fragment_still_counts() -> None:
    """Reading order, not file order: the appendix may live in its own file."""
    tex = {
        "writeup.tex": (
            "\\begin{document}\n"
            "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
            "\\input{appendix}\n\\end{document}\n"
        ),
        "appendix.tex": (
            "\\appendix\nSylow's first theorem.\\label{asm:sylow}\n"
            "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}\n"
        ),
    }
    assert owed(
        tex,
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    ) == ()


def test_a_transforming_listing_is_not_a_quotation() -> None:
    """`literate={True}{{False}}4` renders `False` where the source says
    `True`: the reader is shown a proposition Lean never saw."""
    body = (
        "\\begin{lstlisting}[literate={True}{{False}}4]\n"
        + STATEMENT
        + "\n\\end{lstlisting}"
    )
    assert kinds(owed(document(body))) == ["statement"]


def test_an_ordinary_listing_configuration_still_quotes() -> None:
    body = "\\begin{lstlisting}[language=Lean]\n" + STATEMENT + "\n\\end{lstlisting}"
    assert owed(document(body)) == ()


def test_a_listing_after_a_transforming_one_still_counts() -> None:
    """The scan has to walk a rejected block to its end like any other."""
    body = (
        "\\begin{lstlisting}[literate={True}{{False}}4]\nnoise\n\\end{lstlisting}\n"
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}"
    )
    assert owed(document(body)) == ()


def test_prose_inside_an_unexpanded_definition_states_nothing() -> None:
    r"""An appendix whose disclosure lives in a `\newcommand` nobody uses has
    shown the reader nothing at all."""
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\n\\newcommand{\\hidden}{Sylow's first theorem}\n"
        "\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    found = owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    )
    assert kinds(found) == ["assumption"]
    assert "Sylow's first theorem" in found[0].detail


def test_prose_beside_a_definition_still_counts() -> None:
    body = (
        "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"
        "\\appendix\n\\newcommand{\\note}{unused}\n"
        "Sylow's first theorem.\\label{asm:sylow}\n"
        "\\begin{verbatim}\naxiom Sylow.first : True\n\\end{verbatim}"
    )
    assert owed(
        document(body),
        assumptions=[SYLOW],
        used={"Sylow.first"},
        labels={"thm:one", "asm:sylow"},
    ) == ()


def test_an_assumptions_entry_never_answers_for_a_theorem_of_the_same_name() -> None:
    """`request_assumption` records a naming entry of its own, and it describes
    the assumption. Resolving a theorem onto it points the durable mapping --
    and the label a reader follows -- at an appendix entry for an axiom rather
    than at the theorem it is supposed to describe.

    The exact lookup is the half that had to be closed too: excluding
    assumptions from the leaf fallback alone left this open the moment the
    *assumption* carried the qualified name and the result carried the bare one.
    """
    statement = "theorem A.t : True"
    axiom = dict(SYLOW, formal_name="A.t", latex_name="asm:at")
    registry = [
        {"formal_name": "t", "latex_name": "thm:t", "description": "The result."},
        {"formal_name": "A.t", "latex_name": "asm:at", "description": "The axiom."},
    ]
    obligations = owed(
        document(
            "Assumed.\\label{asm:at}\n"
            f"\\begin{{verbatim}}\n{statement}\n\\end{{verbatim}}"
        ),
        theorems={"A.t": statement},
        registry=registry,
        labels={"asm:at"},
        assumptions=[axiom],
    )
    # The assumption's label is created and its name matches exactly, so an
    # unguarded resolution reports nothing owed at all.
    assert "label" in kinds(obligations), obligations
    assert any("thm:t" in item.detail for item in obligations), obligations


# --- Codex on #393: a conditional counts from where TeX declares it ------------
#
# `\def\ifdraft{}` makes `\ifdraft` a macro. Inside `\iffalse` TeX meets it as
# one, so the first `\fi` closes the false branch and what follows is typeset.
# The `\newif\ifdraft` at the end had been collected before the scan began,
# so `\ifdraft` nested, and the live remainder -- an unbacked theorem here --
# was read as hidden and never judged.

THEOREM_STYLE = "\\newtheorem{theorem}{Theorem}\n"
UNBACKED = "\\begin{theorem}\nGroups of prime order are abelian.\n\\end{theorem}\n"


def _codex(before: str = "\\def\\ifdraft{}\n", after: str = "\\newif\\ifdraft\n") -> dict[str, str]:
    body = (
        before + "\\iffalse\n\\ifdraft\n\\fi\n" + UNBACKED
        + "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n" + after
    )
    return {"writeup.tex": THEOREM_STYLE + "\\begin{document}\n" + body + "\\end{document}\n"}


def test_a_conditional_declared_after_a_redefinition_is_not_guessed() -> None:
    """Neither reading may pass: nesting hid the live theorem, not nesting
    would credit a listing a different order hides. So it is a finding."""
    found = owed(_codex())
    assert "conditional" in kinds(found), found
    detail = next(item.detail for item in found if item.kind == "conditional")
    assert "\\ifdraft" in detail


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # Used before any declaration: TeX has no conditional there yet.
        ("", "\\newif\\ifdraft\n"),
        # Bound by `\let`, which may or may not make it a conditional.
        ("\\let\\ifdraft\\iftrue\n", ""),
        # Declared inside a macro body, which runs only when expanded.
        ("\\newcommand{\\setup}{\\newif\\ifdraft}\n", ""),
        # Declared inside a false branch, where TeX never runs it.
        ("\\iffalse\\newif\\ifdraft\\fi\n", ""),
    ],
    ids=["later", "let", "macro", "skipped"],
)
def test_every_uncertain_conditional_fails_the_check(before: str, after: str) -> None:
    assert "conditional" in kinds(owed(_codex(before, after)))


def test_a_conditional_declared_first_still_nests_without_a_finding() -> None:
    """The earlier cases still hold: declared before use, in the same file or
    in the preamble of the file that inputs the one using it."""
    hidden = "\\iffalse\n\\ifdraft x \\fi\n\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n\\fi\n"
    assert kinds(owed(document("\\newif\\ifdraft\n" + hidden))) == ["statement"]
    tex = {
        "writeup.tex": "\\newif\\ifdraft\n\\begin{document}\n\\input{body}\n\\end{document}\n",
        "body.tex": hidden,
    }
    assert kinds(owed(tex)) == ["statement"]


# --- Codex on #393, round 2: a file TeX never reads binds nothing --------------


def test_a_binding_in_an_orphan_file_does_not_make_a_conditional_ambiguous() -> None:
    """`\\def\\ifdraft{}` in a `.tex` nothing inputs is never read by TeX, so the
    root's own `\\newif\\ifdraft`, declared before use, stays certain and the
    report is not blocked."""
    hidden = "\\iffalse\n\\ifdraft x \\fi\n\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n\\fi\n"
    tex = {**document("\\newif\\ifdraft\n" + hidden), "old-notes.tex": "\\def\\ifdraft{}\n"}
    assert kinds(owed(tex)) == ["statement"]


def test_a_binding_in_a_file_some_reading_reaches_still_counts() -> None:
    """Input only inside a conditional, `maybe.tex` may be read, so its `\\let`
    still makes `\\ifdraft` one Hardy cannot place."""
    body = "\\newif\\ifdraft\n\\ifx a b \\input{maybe} \\fi\n\\iffalse\n\\ifdraft x \\fi\nlive\n\\fi\n"
    tex = {**document(body), "maybe.tex": "\\let\\ifdraft\\iftrue\n"}
    assert "conditional" in kinds(owed(tex))


# --- Review C1: other spellings of a conditional -------------------------------
#
# Each makes `\X` a conditional TeX counts while it skips, so the `\fi` after it
# is its own and the listing stays inside the false branch. Missed, the first
# `\fi` closed the branch and the listing was credited as shown.

LISTING = "\\begin{verbatim}\n" + STATEMENT + "\n\\end{verbatim}\n"


def _skipped(binding: str, use: str) -> dict[str, str]:
    return document(binding + "\\iffalse\n" + use + " x \\fi\n" + LISTING + "\\fi\n")


@pytest.mark.parametrize(
    ("binding", "use"),
    [
        ("\\let\\mycond\\iftrue\n", "\\mycond"),
        ("\\global\\let\\mycond=\\iffalse\n", "\\mycond"),
        ("\\expandafter\\newif\\csname ifdraft\\endcsname\n", "\\ifdraft"),
        ("\\expandafter\\let\\csname mycond\\endcsname\\iftrue\n", "\\mycond"),
    ],
    ids=["let", "global-let", "csname-newif", "csname-let"],
)
def test_a_conditional_bound_another_way_is_not_credited(binding: str, use: str) -> None:
    found = kinds(owed(_skipped(binding, use)))
    assert "conditional" in found and "statement" in found, found


@pytest.mark.parametrize("command", ["newboolean", "provideboolean"])
def test_a_boolean_declares_its_conditional(command: str) -> None:
    r"""`\newboolean{draft}` is `ifthen`'s `\newif\ifdraft`: declared first, it
    nests like one, and nothing is uncertain."""
    assert kinds(owed(_skipped(f"\\{command}{{draft}}\n", "\\ifdraft"))) == ["statement"]


def test_a_csname_name_hardy_cannot_read_makes_every_false_branch_a_finding() -> None:
    tex = document("\\expandafter\\newif\\csname if\\x\\endcsname\n\\iffalse\nnot typeset\n\\fi\n" + LISTING)
    assert "conditional" in kinds(owed(tex))


# --- Round 2 review, B1: a file TeX reads however it is loaded ------------------


@pytest.mark.parametrize(
    "load",
    [
        "\\input{defs}",
        "\\input defs ",
        "\\InputIfFileExists{defs}{}{}",
        "\\import{./}{defs}",
        "\\def\\d{defs}\\input{\\d}",
        "\\def\\a{de}\\def\\b{fs}\\input{\\a\\b}",
    ],
    ids=["braced", "plain", "if-exists", "import", "macro", "macro-built"],
)
def test_a_declaration_in_a_file_loaded_any_way_still_counts(load: str) -> None:
    r"""`defs.tex` declares `\ifdraft`, so the listing stays inside the false
    branch. Only `\input{...}` was followed, and loaded any other way the file
    dropped out with its `\newif`, and the hidden listing was credited."""
    body = load + "\n\\iffalse\n\\ifdraft x \\fi\n" + LISTING + "\\fi\n"
    tex = {**document(body), "defs.tex": "\\newif\\ifdraft\n"}
    assert "statement" in kinds(owed(tex)), kinds(owed(tex))


# --- Round 2 review, C2: any `\let`-like binding, whatever its target -----------


@pytest.mark.parametrize(
    ("binding", "use"),
    [
        ("\\let\\a\\iftrue\\let\\mycond\\a\n", "\\mycond"),
        ("\\csletcs{mycond}{iftrue}\n", "\\mycond"),
        ("\\cslet{mycond}\\iftrue\n", "\\mycond"),
        ("\\letcs\\mycond{iftrue}\n", "\\mycond"),
        ("\\futurelet\\mycond\\relax\\iftrue\n", "\\mycond"),
    ],
    ids=["chain", "csletcs", "cslet", "letcs", "futurelet"],
)
def test_a_let_like_binding_is_not_credited(binding: str, use: str) -> None:
    found = kinds(owed(_skipped(binding, use)))
    assert "conditional" in found and "statement" in found, found


# --- Round 2 review, C3: a literal name or a harmless target refuses nothing ----


@pytest.mark.parametrize(
    "binding",
    [
        "\\expandafter\\let\\csname ver@hyperref.sty\\endcsname\\relax\n",
        "\\let\\oldx\\relax\n",
        "\\let\\oldx=a\n",
    ],
    ids=["package-faking", "relax", "character"],
)
def test_a_let_to_a_non_conditional_target_is_no_finding(binding: str) -> None:
    body = binding + "\\iffalse\n\\oldx not typeset\n\\fi\n" + LISTING
    assert owed(document(body)) == ()


def test_a_let_to_a_rebound_harmless_target_still_counts() -> None:
    """`\\relax` is only harmless while nothing rebinds it."""
    binding = "\\let\\relax\\iftrue\n\\let\\oldx\\relax\n"
    assert "conditional" in kinds(owed(_skipped(binding, "\\oldx")))


def test_a_csname_name_holding_a_control_sequence_is_still_unreadable() -> None:
    tex = document("\\expandafter\\let\\csname my\\x\\endcsname\\iftrue\n\\iffalse\nnot typeset\n\\fi\n" + LISTING)
    assert "conditional" in kinds(owed(tex))


# --- Round 3 review leftovers ---------------------------------------------------


def test_a_load_spelled_with_caret_notation_counts_every_file() -> None:
    r"""`\input ^^64efs` reads `defs.tex`, whose name then appears nowhere."""
    body = "\\input ^^64efs\n\\iffalse\n\\ifdraft x \\fi\n" + LISTING + "\\fi\n"
    tex = {**document(body), "defs.tex": "\\newif\\ifdraft\n"}
    assert "statement" in kinds(owed(tex)), kinds(owed(tex))


@pytest.mark.parametrize(
    "binding",
    [
        "\\LetLtxMacro\\mycond\\iftrue\n",
        "\\LetLtxMacro{\\mycond}{\\iftrue}\n",
        "\\NewCommandCopy\\mycond\\iftrue\n",
        "\\RenewCommandCopy{\\mycond}{\\iftrue}\n",
        "\\DeclareCommandCopy\\mycond\\iftrue\n",
        "\\cs_set_eq:NN \\mycond \\iftrue\n",
        "\\cs_gset_eq:NN \\mycond \\iftrue\n",
        "\\cs_new_eq:NN \\mycond \\iftrue\n",
        "\\cs_set_eq:Nc \\mycond {iftrue}\n",
        "\\cs_set_eq:cN {mycond} \\iftrue\n",
        "\\cs_gset_eq:cc {mycond} {iftrue}\n",
    ],
)
def test_every_listed_let_like_command_binds_an_ambiguous_name(binding: str) -> None:
    found = kinds(owed(_skipped(binding, "\\mycond")))
    assert "conditional" in found and "statement" in found, found


@pytest.mark.parametrize(
    "binding",
    [
        "\\LetLtxMacro\\oldx\\relax\n",
        "\\NewCommandCopy{\\oldx}{\\relax}\n",
        "\\cs_set_eq:NN \\oldx \\relax\n",
        "\\cs_set_eq:Nc \\oldx {relax}\n",
        "\\cs_undefine:N \\oldx\n",
    ],
)
def test_a_let_like_copy_of_a_harmless_target_refuses_nothing(binding: str) -> None:
    body = binding + "\\iffalse\n\\oldx not typeset\n\\fi\n" + LISTING
    assert owed(document(body)) == ()


def test_a_harmless_target_rebound_by_a_copy_command_still_counts() -> None:
    binding = "\\NewCommandCopy\\relax\\iftrue\n\\cs_set_eq:NN \\oldx \\relax\n"
    assert "conditional" in kinds(owed(_skipped(binding, "\\oldx")))
