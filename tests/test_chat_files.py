from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
MAIN = "import Basic\nlemma hardyMain : True := by exact True.intro\n"


def test_read_workspace_lists_the_tree_rather_than_two_files(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("read_workspace", {}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("What is here?")
    payload = json.loads(results(tmp_path)[-1]["output"])
    assert [entry["path"] for entry in payload["lean"]] == ["Basic.lean", "Main.lean"]
    assert payload["lean"][0]["module"] == "Basic"
    assert payload["lean"][0]["lemmas"] == ["hardyBasic"]
    assert payload["lean"][1]["imports"] == ["Basic"]
    assert payload["undocumented_theorems"] == []


def test_read_file_returns_contents(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("read_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Show me the file.")
    assert "hardyBasic" in results(tmp_path)[-1]["output"]


def test_read_file_refuses_a_path_outside_the_workspace(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("read_file", {"path": "../../secrets.lean"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Read something else.")
    assert results(tmp_path)[-1]["ok"] is False


def test_delete_file_is_refused_when_something_imports_it(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("delete_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete the base.")
    assert results(tmp_path)[-1]["ok"] is False
    assert "Main" in results(tmp_path)[-1]["output"]
    assert (tmp_path / "lean" / "Basic.lean").exists()


def test_delete_file_removes_an_unimported_file(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Scratch.lean", "source": BASIC}),
        call("delete_file", {"path": "Scratch.lean"}),
        {"role": "assistant", "content": "Deleted."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Drop the scratch.")
    assert results(tmp_path)[-1]["ok"] is True
    assert not (tmp_path / "lean" / "Scratch.lean").exists()


def test_deleting_a_missing_file_is_an_answer_not_a_crash(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("delete_file", {"path": "Nowhere.lean"}),
        {"role": "assistant", "content": "It was not there."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete nothing.")
    assert results(tmp_path)[-1]["ok"] is False


def test_a_latex_fragment_can_be_saved_and_read_back(tmp_path: Path):
    root = "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n"
    runtime = FakeChatRuntime([
        call("save_latex", {"source": root}),
        call("save_latex", {"path": "sections/one.tex", "source": "Section one.\n"}),
        call("read_file", {"path": "sections/one.tex"}),
        {"role": "assistant", "content": "Written."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Split the writeup.")
    assert (tmp_path / "tex" / "sections" / "one.tex").exists()
    assert "Section one." in results(tmp_path)[-1]["output"]
