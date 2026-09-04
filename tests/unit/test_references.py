"""Reading a TeX log for references that did not resolve.

Text in, findings out: no compiler runs here. The end-to-end behaviour --
that a document with an undefined reference is refused, and that a sound one
is not refused merely because a first pass had no `.aux` to read -- is
`tests/test_latex_references.py`.
"""

from __future__ import annotations

from hardy import references

UNDEFINED_REFERENCE = (
    "LaTeX Warning: Reference `thm:main' on page 1 undefined on input line 12.\n"
)
UNDEFINED_CITATION = (
    "LaTeX Warning: Citation `perelman2002entropy' on page 2 undefined on input line 40.\n"
)


def test_an_undefined_reference_is_named():
    found = references.unresolved(UNDEFINED_REFERENCE)
    assert [(item.kind, item.name, item.line) for item in found] == [
        ("reference", "thm:main", 12)
    ]
    assert "thm:main" in found[0].sentence()


def test_an_undefined_citation_is_named():
    found = references.unresolved(UNDEFINED_CITATION)
    assert [(item.kind, item.name) for item in found] == [
        ("citation", "perelman2002entropy")
    ]


def test_a_multiply_defined_label_is_a_finding():
    """Two labels of one name send every `\\ref` to whichever came last."""
    found = references.unresolved("LaTeX Warning: Label `thm:main' multiply defined.\n")
    assert [(item.kind, item.name, item.line) for item in found] == [
        ("label", "thm:main", None)
    ]


def test_a_clean_log_has_no_findings():
    assert references.unresolved("This is pdfTeX\nOutput written on writeup.pdf (1 page).\n") == ()


def test_the_same_reference_warned_about_twice_is_reported_once():
    """A `\\ref` in a running header is warned about once per page."""
    log = UNDEFINED_REFERENCE + UNDEFINED_REFERENCE.replace("page 1", "page 2")
    assert len(references.unresolved(log)) == 1


def test_a_warning_wrapped_by_tex_is_still_read():
    """TeX wraps its terminal output at 79 columns, mid-name and all.

    Read line by line, a long label's warning names half a label or nothing
    at all -- and the half is the part that would be shown to a model as the
    reference it should go and define.
    """
    name = "thm:a-label-long-enough-to-be-wrapped-across-two-printed-lines"
    whole = f"LaTeX Warning: Reference `{name}' on page 1 undefined on input line 12."
    wrapped = "\n".join(whole[index : index + 79] for index in range(0, len(whole), 79))
    assert len(wrapped.splitlines()) > 1
    assert [item.name for item in references.unresolved(wrapped)] == [name]


def test_a_rerun_request_is_recognised():
    assert references.rerun_requested(
        "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.\n"
    )
    assert references.rerun_requested("LaTeX Warning: There were undefined references.\n")
    assert not references.rerun_requested("Output written on writeup.pdf (1 page).\n")


def test_labels_nothing_points_at_are_found():
    sources = {
        "writeup.tex": "\\label{thm:main}\\label{sec:one}\nSee \\ref{thm:main}.\n",
    }
    assert references.unreferenced_labels(sources) == ("sec:one",)


def test_every_referencing_command_counts_as_pointing_at_a_label():
    """cleveref and hyperref reach a label as surely as `\\ref` does."""
    sources = {
        "writeup.tex": (
            "\\label{a}\\label{b}\\label{c}\\label{d}\\label{e}\n"
            "\\cref{a} \\eqref{b} \\autoref{c} \\hyperref[d]{here} \\pageref{e}\n"
        )
    }
    assert references.unreferenced_labels(sources) == ()


def test_a_comma_separated_reference_points_at_each_label():
    sources = {"writeup.tex": "\\label{a}\\label{b}\n\\cref{a, b}\n"}
    assert references.unreferenced_labels(sources) == ()


def test_the_report_names_the_labels_the_document_does_create():
    """What a model that misspelled a `\\ref` needs in front of it."""
    text = references.report(references.unresolved(UNDEFINED_REFERENCE), ("thm:mian",))
    assert "thm:main" in text
    assert "thm:mian" in text


def test_a_citation_report_says_where_a_citation_comes_from():
    text = references.report(references.unresolved(UNDEFINED_CITATION))
    assert "cite_paper" in text


def test_there_is_no_report_when_nothing_is_unresolved():
    assert references.report((), ("sec:one",)) == ""


def test_unreferenced_labels_are_a_note_and_not_a_finding():
    """Hardy's own completion gate demands labels nothing need point at.

    `completion.py` refuses a report unless the writeup creates a `\\label`
    for every registered name; nothing requires those labels to be
    referenced. Failing the compile over one would leave no document that
    satisfies both gates, so this is a sentence rather than a refusal.
    """
    assert references.unresolved("") == ()
    assert "sec:one" in references.note(("sec:one",))
    assert references.note(()) == ""
