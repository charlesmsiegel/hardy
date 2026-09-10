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
