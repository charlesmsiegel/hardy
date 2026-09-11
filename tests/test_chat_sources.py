"""The interactive session offers the source tools and reads only seeded sources."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pdf_helpers import book_pages, build_pdf
from test_chat import FakeChatRuntime, factory

from hardy.literature.sources import tools as source_tools
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.seeds import SeedStore, new_seed
from hardy.workflows.interactive.session import CHAT_TOOLS, MathematicsSession

sys.path.insert(0, str(Path(__file__).with_name("unit")))


@pytest.fixture
def session(tmp_path: Path) -> MathematicsSession:
    workspace = tmp_path / "problem"
    workspace.mkdir()
    runtime = FakeChatRuntime([])
    return MathematicsSession(
        workspace,
        factory(type(runtime), runtime.script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: False,
    )


def test_the_session_advertises_the_source_tools() -> None:
    offered = {spec["function"]["name"] for spec in CHAT_TOOLS}
    assert {"list_sources", "source_map", "find_source_statements", "search_source", "read_source", "show_source_region"} <= offered


def test_the_session_reads_the_machine_library_not_the_problem(session, tmp_path) -> None:
    assert session.sources.library.root == source_tools.library_root()
    assert session.sources.seeds.problem == session.workspace


def test_a_seeded_source_is_readable_and_an_unseeded_one_is_not(session, tmp_path) -> None:
    library = ManagedLibrary(source_tools.library_root())
    sha = library.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    library.build_tree(sha)
    refused = session._tool("source_map", {"source": sha[:12]})
    assert refused.ok is False and "seeded" in refused.output
    store = SeedStore(session.workspace)
    store.add(new_seed(sha), expected_revision=0)
    listed = session._tool("list_sources", {})
    assert listed.ok, listed.output
    assert json.loads(listed.output)["sources"][0]["artifact"] == sha
    found = json.loads(session._tool("find_source_statements", {"source": sha[:12], "number": "2.1"}).output)
    read = session._tool("read_source", {"source": sha[:12], "node": found["statements"][0]["node"]})
    assert read.ok and json.loads(read.output)["text"].startswith("Lemma 2.1.")
    assert not any(p.suffix == ".pdf" for p in session.workspace.rglob("*"))
