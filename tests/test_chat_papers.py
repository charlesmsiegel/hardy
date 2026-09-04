"""The interactive session can read the literature, and cite only what it read.

The session builds its own paper runtime -- there is nothing to discover, so
nothing to wire in from `cli.py` -- and the point worth pinning down here is
that the four tools are actually offered and actually dispatched. A tool that
exists in `paper_tools.py` and is never reachable from a session is a tool the
model does not have.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, factory

from hardy import arxiv
from hardy.chat import CHAT_TOOLS, MathematicsSession

FEED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
    b"<entry><id>http://arxiv.org/abs/math.DG/0211159v1</id>"
    b"<published>2002-11-11T18:00:00Z</published><updated>2002-11-11T18:00:00Z</updated>"
    b"<title>The entropy formula for the Ricci flow</title>"
    b"<summary>A monotonic expression for the Ricci flow.</summary>"
    b"<author><name>Grigori Perelman</name></author>"
    b'<category term="math.DG"/></entry></feed>'
)


@pytest.fixture
def session(tmp_path: Path) -> MathematicsSession:
    workspace = tmp_path / "problem"
    workspace.mkdir()
    runtime = FakeChatRuntime([])
    built = MathematicsSession(
        workspace,
        factory(type(runtime), runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )
    # A scripted arXiv in place of the real one. Everything else about the
    # runtime -- the library, the bibliography, the paths -- is what the
    # session built for itself.
    built.papers.client = arxiv.ArxivClient(
        built.papers.library,
        transport=lambda url, timeout: FEED,
        clock=lambda: 1_000_000.0,
        sleep=lambda seconds: None,
    )
    return built


def test_the_session_advertises_the_paper_tools() -> None:
    offered = {spec["function"]["name"] for spec in CHAT_TOOLS}
    assert {"search_papers", "fetch_paper", "read_paper", "cite_paper"} <= offered


def test_the_library_is_shared_by_every_problem_in_the_root(session) -> None:
    """One machine, one download. A second problem must not refetch."""
    assert session.papers.library.root == session.root / ".hardy" / "papers"
    assert session.papers.bibliography.path == session.workspace / "bibliography.json"


def test_a_fetch_and_a_citation_reach_the_runtime(session) -> None:
    fetched = session._tool("fetch_paper", {"paper_id": "math.DG/0211159"})
    assert fetched.ok, fetched.output
    cited = session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"})
    assert cited.ok, cited.output
    key = json.loads(cited.output)["cite_key"]
    assert key.startswith("perelman2002entropy-")
    generated = session.workspace / "tex" / "references.tex"
    assert f"\\bibitem{{{key}}}" in generated.read_text(encoding="utf-8")


def test_a_citation_of_something_never_fetched_is_refused(session) -> None:
    """The one property the tool surface exists to guarantee."""
    result = session._tool("cite_paper", {"paper_id": "2401.99999v1"})
    assert not result.ok
    assert "fetch_paper" in result.output
    assert not (session.workspace / "bibliography.json").exists()


def test_the_generated_bibliography_makes_a_citation_compile(session) -> None:
    r"""End to end: cite, `\input{references}`, and the compile resolves it.

    Without the `\input` the same document is refused, which is the check
    `latex.py` gained for exactly this: a `\cite` that resolves to `[?]` is a
    broken document that used to compile silently.
    """
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    body = f"\\documentclass{{article}}\n\\begin{{document}}\nAs shown in \\cite{{{key}}}.\n"
    without = session._tool("check_latex", {"source": body + "\\end{document}\n"})
    assert not without.ok
    assert key in without.output
    with_input = session._tool(
        "check_latex", {"source": body + "\\input{references}\n\\end{document}\n"}
    )
    assert with_input.ok, with_input.output


def test_a_paper_tex_cannot_hold_verbatim_still_compiles(session) -> None:
    r"""The generated file is the one file the model may not repair.

    So it has to arrive compilable. A collaboration author list is one
    physical line longer than a TeX input buffer, and a Cyrillic name stops
    pdfLaTeX outright -- and either one would make every writeup in the
    workspace fail from the moment of a successful `cite_paper`, with no move
    left from inside the session.
    """
    authors = "".join(
        f"<author><name>Author {n}</name></author>" for n in range(2_000)
    )
    session.papers.client = arxiv.ArxivClient(
        session.papers.library,
        transport=lambda url, timeout: FEED.replace(
            b"<author><name>Grigori Perelman</name></author>",
            (authors + "<author><name>Григорий</name></author>").encode(),
        ),
        clock=lambda: 1_000_000.0,
        sleep=lambda seconds: None,
    )
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    generated = (session.workspace / "tex" / "references.tex").read_text(encoding="utf-8")
    assert generated.isascii()
    assert max(len(line) for line in generated.splitlines()) <= 96
    result = session._tool(
        "check_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\n"
            f"As shown in \\cite{{{key}}}.\n\\input{{references}}\n\\end{{document}}\n"
        },
    )
    assert result.ok, result.output


def test_a_hand_written_bibliography_is_refused_at_the_save(session) -> None:
    r"""The claim is about what a reader sees, not about `bibliography.json`.

    `cite_paper` cannot be talked into an invented reference -- it takes an
    identifier and nothing else -- but `save_latex` takes arbitrary LaTeX, and
    a `\bibitem{invented2020}` written straight into the writeup resolves,
    compiles, and would be published with nothing behind it.
    """
    source = (
        "\\documentclass{article}\n\\begin{document}\nAs shown in \\cite{invented2020}.\n"
        "\\begin{thebibliography}{9}\n\\bibitem{invented2020} Nobody. Never.\n"
        "\\end{thebibliography}\n\\end{document}\n"
    )
    saved = session._tool("save_latex", {"source": source})
    assert not saved.ok
    assert "cite_paper" in saved.output
    assert not (session.workspace / "tex" / "writeup.tex").exists()
    # And refused at the check too, so the model is not told the document is
    # sound and then refused the save.
    assert not session._tool("check_latex", {"source": source}).ok


def test_the_generated_bibliography_is_not_the_models_to_write(session) -> None:
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"})
    overwrite = session._tool(
        "save_latex",
        {"path": "references.tex", "source": "\\bibitem{invented2020} Nobody.\n"},
    )
    assert not overwrite.ok
    assert "bibliography.json" in overwrite.output
    generated = (session.workspace / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "invented2020" not in generated
    assert "perelman2002entropy-" in generated


def test_the_generated_bibliography_is_not_the_models_to_delete(session) -> None:
    """Deleting it leaves every `\\input{references}` unresolvable."""
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"})
    result = session._tool("delete_file", {"path": "references.tex"})
    assert not result.ok
    assert (session.workspace / "tex" / "references.tex").is_file()


def test_a_comment_mentioning_bibitem_is_not_a_bibliography(session) -> None:
    """TeX never reads it, so refusing the document over it refuses nothing."""
    source = (
        "\\documentclass{article}\n\\begin{document}\n"
        "% never write \\bibitem by hand; use cite_paper\nText.\n\\end{document}\n"
    )
    assert session._tool("check_latex", {"source": source}).ok


def test_a_bibliography_in_a_saved_fragment_refuses_the_root_too(session) -> None:
    r"""The compile reads the whole tree, so the rule must too.

    Checking only the candidate was half a rule: `LatexTools.check` compiles
    the candidate against every saved file, so a `\bibitem` in a fragment
    written before this gate existed, or edited outside Hardy, would be
    pulled into a clean root and published with it.
    """
    sections = session.workspace / "tex" / "sections"
    sections.mkdir(parents=True)
    (sections / "one.tex").write_text(
        "\\begin{thebibliography}{9}\n\\bibitem{invented2020} Nobody.\n"
        "\\end{thebibliography}\n",
        encoding="utf-8",
    )
    result = session._tool(
        "save_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\n"
            "\\input{sections/one}\n\\end{document}\n"
        },
    )
    assert not result.ok
    assert "sections/one.tex" in result.output
    assert "cite_paper" in result.output


def test_the_generated_file_does_not_refuse_the_document_it_is_part_of(session) -> None:
    """Hardy's own reference list is the one exemption from that sweep."""
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    result = session._tool(
        "save_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\n"
            f"As shown in \\cite{{{key}}}.\n\\input{{references}}\n\\end{{document}}\n"
        },
    )
    assert result.ok, result.output


def test_a_bibliography_built_by_expansion_is_still_refused(session) -> None:
    r"""TeX is a macro language, so a `\bibitem` need not look like one.

    `\csname bibitem\endcsname` runs a `\bibitem` that no reader of the
    source would recognise. Rather than chase spellings, the writeup may not
    build control sequences by name at all -- a mathematical document has no
    use for it, and the compiler's own `\bibcite`/`\citation` record backs
    the rule up for whatever this misses.
    """
    source = (
        "\\documentclass{article}\n\\begin{document}\n"
        "As shown in \\cite{invented2020}.\n"
        "\\csname begin\\endcsname{thebibliography}{9}\n"
        "\\csname bibitem\\endcsname{invented2020} Nobody.\n"
        "\\csname end\\endcsname{thebibliography}\n"
        "\\end{document}\n"
    )
    result = session._tool("save_latex", {"source": source})
    assert not result.ok
    assert "\\csname" in result.output
    assert not (session.workspace / "tex" / "writeup.tex").exists()


def test_the_reference_list_cite_paper_generated_is_vouched_for(session) -> None:
    """The same check must accept Hardy's own entries, however it reads them."""
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    result = session._tool(
        "save_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\n"
            f"As shown in \\cite{{{key}}}.\n\\input{{references}}\n\\end{{document}}\n"
        },
    )
    assert result.ok, result.output


def test_a_citation_no_paper_backs_is_refused_from_the_compilers_own_record(session) -> None:
    r"""The backstop, exercised where the lexical rules cannot reach.

    `_vouched_references` is handed every key the compile touched -- what the
    reference list defined and what the text cited, from every `.aux` the
    compilation wrote. A key no `cite_paper` recorded refuses the document
    however it got there.
    """
    assert session._vouched_references(()) == ""
    refusal = session._vouched_references(("invented2020",))
    assert "invented2020" in refusal
    assert "cite_paper" in refusal
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    assert session._vouched_references((key,)) == ""
    assert "invented2020" in session._vouched_references((key, "invented2020"))


def test_deleting_a_fragment_is_gated_like_a_save(session) -> None:
    r"""Deleting publishes `writeup.pdf` too, so it owes the same gate.

    Without it, removing an unrelated fragment republished whatever reference
    list the remaining files happened to build: the ordinary
    unresolved-reference check sees nothing wrong with an invented `\bibitem`
    that resolves.
    """
    tex = session.workspace / "tex"
    tex.mkdir(parents=True, exist_ok=True)
    (tex / "writeup.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nText.\n\\end{document}\n",
        encoding="utf-8",
    )
    (tex / "spare.tex").write_text("Nothing much.\n", encoding="utf-8")
    (tex / "refs.tex").write_text(
        "\\begin{thebibliography}{9}\n\\bibitem{invented2020} Nobody.\n"
        "\\end{thebibliography}\n",
        encoding="utf-8",
    )
    result = session._tool("delete_file", {"path": "spare.tex"})
    assert not result.ok
    assert "refs.tex" in result.output
    # Refused before anything was removed, so nothing has to be put back.
    assert (tex / "spare.tex").is_file()


def test_a_writeup_file_merely_named_references_is_the_workspaces_own(session) -> None:
    """Only the generated file at the tree root is Hardy's.

    Reserved on the basename, `sections/references.tex` was refused at the
    check, refused at the save, and undeletable -- a name the workspace is
    entitled to use, taken by accident.
    """
    root = session._tool(
        "save_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\nText.\n"
            "\\end{document}\n"
        },
    )
    assert root.ok, root.output
    body = "\\section{Further reading}\nNothing cited here yet.\n"
    saved = session._tool("save_latex", {"path": "sections/references.tex", "source": body})
    assert saved.ok, saved.output
    assert (session.workspace / "tex" / "sections" / "references.tex").is_file()
    removed = session._tool("delete_file", {"path": "sections/references.tex"})
    assert removed.ok, removed.output


def test_the_generated_file_is_still_reserved_at_the_tree_root(session) -> None:
    refused = session._tool("save_latex", {"path": "references.tex", "source": "x\n"})
    assert not refused.ok
    assert "written by Hardy" in refused.output


def test_a_deletion_that_cannot_be_judged_still_keeps_the_fragment(session) -> None:
    """The unlink happens first, so every way out has to put the file back.

    `bibliography.json` is read to vouch for the reference list the compile
    built, and an unreadable one raises rather than returning a refusal --
    which came out of the compile, went past the restoration, and left the
    fragment permanently deleted by an operation reported as having failed.
    """
    bare = session._tool(
        "save_latex",
        {"source": "\\documentclass{article}\n\\begin{document}\nText.\n\\end{document}\n"},
    )
    assert bare.ok, bare.output
    saved = session._tool("save_latex", {"path": "sections/one.tex", "source": "Text.\n"})
    assert saved.ok, saved.output
    joined = session._tool(
        "save_latex",
        {"source": "\\documentclass{article}\n\\begin{document}\n\\input{sections/one}\n\\end{document}\n"},
    )
    assert joined.ok, joined.output
    fragment = session.workspace / "tex" / "sections" / "one.tex"
    assert fragment.is_file()
    (session.workspace / "bibliography.json").write_text("{ not json", encoding="utf-8")
    result = session._tool("delete_file", {"path": "sections/one.tex"})
    assert not result.ok
    assert fragment.is_file(), "the fragment was lost by a deletion that reported failure"
    assert fragment.read_text(encoding="utf-8") == "Text.\n"


def test_a_forged_reference_list_is_rewritten_before_the_compile(session) -> None:
    """Vouching for the keys does not vouch for what is printed under them.

    A `references.tex` that arrived stale from a clone, or was edited past
    the save refusal, could keep a key `cite_paper` recorded and change the
    authors beneath it -- and every gate passed, because the key check reads
    keys and the source check exempts this file by name.
    """
    session._tool("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    key = json.loads(
        session._tool("cite_paper", {"paper_id": "math.DG/0211159v1"}).output
    )["cite_key"]
    generated = session.workspace / "tex" / "references.tex"
    generated.write_text(
        generated.read_text(encoding="utf-8").replace("Grigori Perelman", "Somebody Else"),
        encoding="utf-8",
    )
    result = session._tool(
        "check_latex",
        {
            "source": "\\documentclass{article}\n\\begin{document}\n"
            f"As shown in \\cite{{{key}}}.\n\\input{{references}}\n\\end{{document}}\n"
        },
    )
    assert result.ok, result.output
    restored = generated.read_text(encoding="utf-8")
    assert "Grigori Perelman" in restored
    assert "Somebody Else" not in restored
