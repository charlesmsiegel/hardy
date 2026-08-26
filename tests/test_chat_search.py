"""The interactive session can search, and says so when it cannot.

`CHAT_TOOLS` offered nine tools and none of them searched, while `chat.md.j2`
told the model to "search and check first". A model in that position guesses
names into `check_lean`, which is the slowest possible way to discover that a
lemma is called something else -- and, when the guess is a module path rather
than a lemma name, Lean's answer names a missing file and reads as a broken
installation. One session read it that way and stopped writing Lean.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from test_chat import FakeChatRuntime, factory

from hardy.chat import MathematicsSession
from hardy.models import ToolResult


class FakeSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def rank_premises(self, goal: str, limit: int = 10) -> ToolResult:
        self.calls.append(("rank_premises", {"goal": goal, "limit": limit}))
        return ToolResult(True, json.dumps({"premises": [{"name": "Nat.add_comm"}]}))

    def search_declarations(self, query: str, limit: int = 10) -> ToolResult:
        self.calls.append(("search_declarations", {"query": query, "limit": limit}))
        return ToolResult(True, json.dumps({"results": []}))

    def inspect_declarations(self, names: list[str]) -> ToolResult:
        self.calls.append(("inspect_declarations", {"names": names}))
        return ToolResult(True, json.dumps({"found": []}))

    def search_modules(self, query: str, limit: int = 20) -> ToolResult:
        self.calls.append(("search_modules", {"query": query, "limit": limit}))
        return ToolResult(True, json.dumps({"modules": ["Mathlib.GroupTheory.Sylow"]}))


@pytest.fixture
def session_factory(tmp_path: Path):
    def build(**overrides):
        runtime = FakeChatRuntime([])
        workspace = overrides.pop("workspace", tmp_path)
        answers = iter(overrides.pop("approvals", ()))
        return MathematicsSession(
            workspace,
            factory(type(runtime), runtime.script),
            (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
            (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
            overrides.pop("confirm", lambda proposal: next(answers)),
            **overrides,
        )

    return build


def test_the_session_advertises_the_search_tools() -> None:
    chat = importlib.import_module("hardy.chat")

    offered = {spec["function"]["name"] for spec in chat.CHAT_TOOLS}

    assert {"rank_premises", "search_declarations", "inspect_declarations"} <= offered


def test_the_session_advertises_a_module_search() -> None:
    """The other three answer about declarations. The failure that motivated
    this one was a module path, which `#find` cannot speak to at all."""
    chat = importlib.import_module("hardy.chat")

    offered = {spec["function"]["name"] for spec in chat.CHAT_TOOLS}

    assert "search_modules" in offered


def test_a_ranking_asked_for_reaches_the_search_runtime(session_factory) -> None:
    search = FakeSearch()
    session = session_factory(search=search, search_detail="Mathlib abcdef in /lean")

    result = session._tool("rank_premises", {"goal": "⊢ _ + _ = _ + _", "limit": 5})

    assert result.ok
    assert search.calls == [("rank_premises", {"goal": "⊢ _ + _ = _ + _", "limit": 5})]


def test_a_module_search_reaches_the_runtime(session_factory) -> None:
    search = FakeSearch()
    session = session_factory(search=search, search_detail="Mathlib abcdef in /lean")

    result = session._tool("search_modules", {"query": "Sylow", "limit": 5})

    assert result.ok
    assert search.calls == [("search_modules", {"query": "Sylow", "limit": 5})]


def test_without_a_lake_project_the_tool_refuses_with_the_reason(session_factory) -> None:
    """Advertised and refusing, not absent.

    Deliberately unlike the `cas_*` tools, which are withheld when no backend
    was found. A CAS backend is optional; a Lean project is the thing Hardy is
    for, and a model handed no search tool concludes Hardy cannot search rather
    than that this machine is not set up.
    """
    session = session_factory(search=None, search_detail="lean_project is not set")

    result = session._tool("rank_premises", {"goal": "⊢ True"})

    assert not result.ok
    assert "lean_project is not set" in result.output


def test_the_refusal_is_recorded_in_the_transcript_like_any_other_answer(
    session_factory,
) -> None:
    session = session_factory(search=None, search_detail="lean_project is not set")

    session._dispatch("rank_premises", {"goal": "⊢ True"})

    recorded = [
        entry
        for entry in session.transcript_path.read_text(encoding="utf-8").splitlines()
        if json.loads(entry).get("name") == "rank_premises"
    ]
    assert len(recorded) == 1
