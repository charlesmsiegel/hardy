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
    assert json.loads(cited.output)["cite_key"] == "perelman2002entropy"
    generated = session.workspace / "tex" / "references.tex"
    assert "\\bibitem{perelman2002entropy}" in generated.read_text(encoding="utf-8")


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
