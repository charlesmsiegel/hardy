"""A mechanical source inventory keeps its locations in the original text."""

from __future__ import annotations

import hashlib

import pytest

from hardy.literature.manuscript import inventory

SOURCE = r"""%
é\section*{Intro}
\begin{theorem}
  \label
    {thm:one}
  \citep[see]{alpha, beta}
  \begin{definition}\label{def:one}A thing.\end{definition}
  \begin{proof}\cite{alpha}Done.\end{proof}
\end{theorem}
"""


def _slice(source: str, span) -> str:
    assert span.valid_for(span.path, source)
    return source[span.start : span.end]


def test_inventory_records_exact_original_slices_in_source_order() -> None:
    found = inventory({"paper.tex": SOURCE})

    assert [(source.path, source.digest) for source in found.sources] == [
        ("paper.tex", hashlib.sha256(SOURCE.encode("utf-8")).hexdigest())
    ]
    assert [(section.level, section.starred) for section in found.sections] == [("section", True)]
    assert _slice(SOURCE, found.sections[0].command) == r"\section*{Intro}"
    assert _slice(SOURCE, found.sections[0].title) == "Intro"
    assert [(item.kind, item.name) for item in found.environments] == [
        ("theorem", "theorem"),
        ("definition", "definition"),
        ("proof", "proof"),
    ]
    assert _slice(SOURCE, found.environments[0].opening) == r"\begin{theorem}"
    assert _slice(SOURCE, found.environments[0].closing) == r"\end{theorem}"
    assert [(item.value, _slice(SOURCE, item.argument)) for item in found.labels] == [
        ("thm:one", "thm:one"),
        ("def:one", "def:one"),
    ]
    assert [(item.command_name, item.key, _slice(SOURCE, item.key_span)) for item in found.citations] == [
        ("citep", "alpha", "alpha"),
        ("citep", "beta", "beta"),
        ("cite", "alpha", "alpha"),
    ]
    assert not found.unsupported


def test_comments_verbatim_and_macro_bodies_do_not_become_inventory_records() -> None:
    source = r"""% \begin{theorem}\label{comment}\cite{comment}\end{theorem}
\% \label{escaped-percent}
\\% \label{commented-after-two-slashes}
\verb|\begin{theorem}\label{inline}\cite{x}|
\begin{verbatim}\begin{theorem}\label{verbatim}\cite{x}\end{theorem}\end{verbatim}
\begin{lstlisting}\begin{theorem}\label{listing}\cite{x}\end{theorem}\end{lstlisting}
\begin{minted}{tex}\begin{theorem}\label{minted}\cite{x}\end{theorem}\end{minted}
\newcommand{\hidden}{\begin{theorem}\label{macro}\cite{x}\end{theorem}}
\def\alsohidden{\begin{theorem}\label{defmacro}\cite{x}\end{theorem}}
\begin{theorem}\label{real}\cite{real}\end{theorem}
"""

    found = inventory({"source.tex": source})

    assert [(item.name, item.kind) for item in found.environments] == [("theorem", "theorem")]
    # `\%` is literal content, while the percent after `\\` starts a comment.
    assert [item.value for item in found.labels] == ["escaped-percent", "real"]
    assert [item.key for item in found.citations] == ["real"]
    assert {item.kind for item in found.unsupported} >= {"macro_definition"}


@pytest.mark.parametrize("command", ("newcommand", "renewcommand", "providecommand"))
def test_macro_optional_default_body_is_suppressed(command: str) -> None:
    source = (
        f"\\{command}{{\\hidden}}[1][default]"
        "{\\begin{theorem}\\label{wrong}\\cite{wrong}\\end{theorem}}\n"
        "\\begin{theorem}\\label{real}\\cite{real}\\end{theorem}"
    )

    found = inventory({"source.tex": source})

    assert [(item.name, item.kind) for item in found.environments] == [("theorem", "theorem")]
    assert [item.value for item in found.labels] == ["real"]
    assert [item.key for item in found.citations] == ["real"]
    assert {item.kind for item in found.unsupported} >= {"macro_definition"}


def test_repeated_occurrences_stay_distinct_and_source_identity_is_not_a_path_read() -> None:
    first = "é\\label{same}\\cite{key}\\label{same}\\cite{key}"
    second = "é\\section{Other}"
    found = inventory({"z.tex": first, "a.tex": second})

    assert [item.path for item in found.sources] == ["a.tex", "z.tex"]
    assert [item.value for item in found.labels] == ["same", "same"]
    assert [item.key for item in found.citations] == ["key", "key"]
    assert found.labels[0] != found.labels[1]
    assert _slice(first, found.labels[0].argument) == "same"


def test_malformed_and_unclosed_structures_are_partial_and_deterministic() -> None:
    source = r"""\begin{theorem}\label{open}
\end{proof}
\label{missing
\verb|unterminated
"""

    sources = {"bad.tex": source, "verbatim.tex": r"\begin{verbatim} hidden"}
    first = inventory(sources)
    second = inventory(sources)

    assert first == second
    assert first.environments[0].closing is None
    assert {item.kind for item in first.unsupported} >= {
        "mismatched_environment",
        "unbalanced_argument",
        "unterminated_verb",
        "unclosed_verbatim",
        "unclosed_environment",
    }


def test_comment_splicing_does_not_invent_a_control_word_and_spans_reject_changed_text() -> None:
    source = "\\sec% split control word\ntion{not a section}\n\\label % comment\n {real}"
    found = inventory({"paper.tex": source})

    assert not found.sections
    assert [item.value for item in found.labels] == ["real"]
    assert _slice(source, found.labels[0].command) == "\\label % comment\n {real}"
    assert "split_control_word" in {item.kind for item in found.unsupported}
    assert not found.labels[0].argument.valid_for("paper.tex", source.replace("real", "next"))


def test_unpaired_surrogates_are_refused_before_digesting() -> None:
    with pytest.raises(UnicodeEncodeError):
        inventory({"broken.tex": "\ud800"})


@pytest.mark.parametrize("command", ("newcommand", "renewcommand", "providecommand"))
@pytest.mark.parametrize("options", ("", "[1][default]"))
def test_unbraced_macro_name_suppresses_stored_decoys(command, options):
    definition = (
        f"\\{command}\\hidden{options}"
        r"{\begin{theorem}\label{decoy}\cite{decoy}\end{theorem}}"
    )
    source = definition + r"\label{real}\cite{real}"
    found = inventory({"paper.tex": source})
    assert not found.environments
    assert [item.value for item in found.labels] == ["real"]
    assert [item.key for item in found.citations] == ["real"]
    assert _slice(source, found.unsupported[0].span) == definition


@pytest.mark.parametrize("definition", [
    r"\newcommand hidden{\begin{theorem}\label{decoy}\cite{decoy}\end{theorem}}",
    r"\newcommand{\hidden}{\begin{theorem}\label{decoy}\cite{decoy}\end{theorem}",
])
def test_unsupported_macro_definition_does_not_leak_body_records(definition):
    found = inventory({"paper.tex": definition})
    assert not found.environments and not found.labels and not found.citations
    assert _slice(definition, found.unsupported[0].span) == definition


def test_citation_comment_does_not_manufacture_keys_or_change_original_spans():
    command = "\\cite{one,% ignored, decoy } {\ntwo}"
    source = command + r"\cite{two}"
    found = inventory({"paper.tex": source})
    assert [item.key for item in found.citations] == ["one", "two", "two"]
    assert [_slice(source, item.key_span) for item in found.citations] == ["one", "two", "two"]
    assert [_slice(source, item.command) for item in found.citations] == [command, command, r"\cite{two}"]
    assert found.citations[1].key_span != found.citations[2].key_span
    assert not found.unsupported


def test_comment_spliced_citation_key_is_bounded_unsupported():
    source = "\\cite{pa% ignored, }\nper, real}"
    found = inventory({"paper.tex": source})
    assert [item.key for item in found.citations] == ["real"]
    assert len(found.unsupported) == 1
    assert _slice(source, found.unsupported[0].span) == "pa% ignored, }\nper"


def test_title_nested_occurrences_are_reported_as_bounded_unsupported():
    title = r"Related work \cite{paper}\label{sec:related}"
    source = "\\section{" + title + r"}\cite{after}\label{after}"
    found = inventory({"paper.tex": source})
    assert _slice(source, found.sections[0].title) == title
    assert [item.key for item in found.citations] == ["after"]
    assert [item.value for item in found.labels] == ["after"]
    assert len(found.unsupported) == 1
    assert _slice(source, found.unsupported[0].span) == title


# --- Only real TeX conditionals open a conditional (#189) ------------------------------


def _labels(found) -> list[str]:
    return [label.value for label in found.labels]


def _conditionals(found) -> list[str]:
    return [finding.detail for finding in found.findings if finding.kind == "conditional"]


def test_iff_is_an_ordinary_control_word() -> None:
    """`\\iff` has no `\\fi`, and reading it as a conditional swallowed the
    rest of the file: every section, label and citation after the first
    "if and only if" was missing from the inventory."""
    source = (
        "\\section{Intro}\n"
        "We have $a \\iff b$.\n"
        "\\section[Short]{Main results}\\label{sec:main}\n"
        "\\begin{theorem}\\label{thm:a} x \\end{theorem}\n"
        "See \\cite{foo,bar}.\n"
    )
    found = inventory({"a.tex": source})

    assert len(found.sections) == 2
    assert _labels(found) == ["sec:main", "thm:a"]
    assert [citation.key for citation in found.citations] == ["foo", "bar"]
    assert "theorem" in [environment.name for environment in found.environments]
    assert _conditionals(found) == []


def test_iff_inside_a_real_conditional_does_not_deepen_it() -> None:
    found = inventory({"a.tex": "\\ifx\\a\\b $p\\iff q$ \\fi \\label{after}"})

    assert _conditionals(found) == ["literal conditional source is not interpreted"]
    assert _labels(found) == ["after"]


def test_a_newif_conditional_is_a_conditional() -> None:
    found = inventory({"a.tex": "\\newif\\ifdraft \\ifdraft x\\fi \\label{z}"})

    assert _conditionals(found) == ["literal conditional source is not interpreted"]
    assert _labels(found) == ["z"]
    assert not [item for item in found.findings if item.kind == "stray_conditional_end"]


def test_a_newif_declared_in_another_file_is_still_a_conditional() -> None:
    found = inventory({
        "macros.tex": "\\newif\\ifdraft\n",
        "paper.tex": "\\ifdraft \\label{hidden}\\fi \\label{z}",
    })

    assert _conditionals(found) == ["literal conditional source is not interpreted"]
    assert _labels(found) == ["z"]


def test_a_commented_newif_declares_nothing() -> None:
    found = inventory({"a.tex": "% \\newif\\ifdraft\n\\ifdraft x\\fi \\label{z}"})

    assert _conditionals(found) == []
    assert _labels(found) == ["z"]


def test_ifthenelse_takes_arguments_and_opens_nothing() -> None:
    found = inventory({"a.tex": "\\ifthenelse{\\equal{a}{b}}{x}{y}\\label{w}"})

    assert _conditionals(found) == []
    assert _labels(found) == ["w"]


def test_iffalse_still_hides_what_it_wraps() -> None:
    found = inventory({"a.tex": "\\iffalse \\label{hidden}\\fi \\label{shown}"})

    assert _conditionals(found) == ["literal conditional source is not interpreted"]
    assert _labels(found) == ["shown"]


def test_the_inventory_and_the_writeup_scan_share_one_predicate() -> None:
    """Two readers of the same TeX must agree on what opens a conditional."""
    from hardy.documents import syntax
    from hardy.literature import manuscript

    assert manuscript.opens_conditional is syntax.opens_conditional


def test_an_iftex_conditional_inside_iffalse_carries_its_own_fi() -> None:
    """`\\ifpdftex` is the `iftex` package's conditional. Not counted, its `\\fi`
    closed the `\\iffalse` early and recorded the label the branch hides."""
    found = inventory({"a.tex": "\\iffalse \\ifpdftex a\\fi \\label{hidden}\\fi \\label{shown}"})

    assert _labels(found) == ["shown"]
    assert not [item for item in found.findings if item.kind == "stray_conditional_end"]


@pytest.mark.parametrize(
    "name",
    [
        # The `iftex` package's engine tests.
        "ifpdftex", "ifPDFTeX", "ifXeTeX", "ifLuaTeX", "ifetex", "ifeTeX",
        "ifptex", "ifuptex", "ifvtex", "ifluahbtex",
        # Engine primitives beyond TeX and e-TeX.
        "ifpdfabsnum", "ifpdfabsdim", "ifabsnum", "ifabsdim", "ifprimitive", "ifcondition",
    ],
)
def test_engine_and_iftex_conditionals_are_conditionals(name) -> None:
    from hardy.documents.syntax import opens_conditional

    assert opens_conditional(name)


# --- Codex on #393: a conditional counts from where TeX declares it ------------


CODEX = "\\def\\ifdraft{} \\iffalse \\ifdraft \\fi \\label{live}\\cite{live} \\newif\\ifdraft"


def _ambiguous(found) -> list[str]:
    return [finding.detail for finding in found.findings if finding.kind == "ambiguous_conditional"]


def test_a_conditional_declared_after_a_redefinition_is_a_finding() -> None:
    """TeX meets `\\ifdraft` as the macro `\\def` made it, so the first `\\fi`
    closes the false branch and the label and citation after it are live. The
    global `\\newif` pre-scan nested it and dropped them without a word."""
    found = inventory({"a.tex": CODEX})

    assert _ambiguous(found), found.findings
    assert "\\ifdraft" in _ambiguous(found)[0]


@pytest.mark.parametrize(
    "source",
    [
        "\\iffalse \\ifdraft \\fi \\label{live} \\newif\\ifdraft",
        "\\let\\ifdraft\\iftrue \\iffalse \\ifdraft \\fi \\label{live}",
        "\\newcommand{\\setup}{\\newif\\ifdraft} \\iffalse \\ifdraft \\fi \\label{live}",
        "\\iffalse\\newif\\ifdraft\\fi \\iffalse \\ifdraft \\fi \\label{live}",
    ],
    ids=["later", "let", "macro", "skipped"],
)
def test_every_uncertain_conditional_is_a_finding(source: str) -> None:
    assert _ambiguous(inventory({"a.tex": source}))


def test_a_newif_declared_first_is_still_a_conditional_without_a_finding() -> None:
    found = inventory({"a.tex": "\\newif\\ifdraft \\iffalse \\ifdraft x\\fi \\label{hidden}\\fi \\label{z}"})

    assert _labels(found) == ["z"]
    assert not _ambiguous(found)


def test_a_newif_in_the_file_that_inputs_the_user_is_certain() -> None:
    """With one root (the file holding `\\begin{document}`), the order across
    files is the order the root inputs them in."""
    found = inventory({
        "main.tex": "\\newif\\ifdraft\n\\begin{document}\n\\input{paper}\n\\end{document}\n",
        "paper.tex": "\\iffalse \\ifdraft \\label{hidden}\\fi \\label{also}\\fi \\label{z}",
    })

    assert _labels(found) == ["z"]
    assert not _ambiguous(found)
