from __future__ import annotations

from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

FIRST = "import Mathlib\ntheorem hardyOne : True := by exact True.intro\n"
SECOND = "import Mathlib\ntheorem hardyTwo : True := by exact True.intro\n"
LEMMAS = "import Mathlib\nlemma hardyHelper : True := by exact True.intro\n"
TEX = "\\documentclass{article}\n\\begin{document}One.\\label{thm:one}\\end{document}\n"


def test_a_second_theorem_is_refused_while_the_first_is_undocumented(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove two things.")
    saved = results(tmp_path)
    assert saved[0]["ok"] is True
    assert saved[1]["ok"] is False
    assert "hardyOne" in saved[1]["output"]
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_documenting_the_first_releases_the_ratchet(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": TEX}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Both saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove, write up, prove again.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)
    assert (tmp_path / "lean" / "Two.lean").exists()


def test_a_registered_name_without_a_label_does_not_count_as_documented(tmp_path: Path):
    """record_name alone is a promise; the label is the writeup keeping it."""
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Still blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Register without writing up.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_lemmas_never_trip_the_ratchet(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "A.lean", "source": LEMMAS}),
        call("save_lean", {"path": "B.lean", "source": LEMMAS.replace("hardyHelper", "hardyOther")}),
        {"role": "assistant", "content": "Both saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save scaffolding.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)


def test_repairing_an_undocumented_theorem_is_allowed(tmp_path: Path):
    """Condition 2 of the ratchet: no new theorem name, so no refusal.

    Without it a model would be trapped -- unable to fix, restate, or remove
    the very theorem blocking every save.
    """
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("save_lean", {"path": "One.lean", "source": FIRST.replace("by exact True.intro", "by exact True.intro -- repaired")}),
        {"role": "assistant", "content": "Repaired."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Fix the proof.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)
    assert "repaired" in (tmp_path / "lean" / "One.lean").read_text()


def test_deleting_the_undocumented_theorem_clears_the_ratchet(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("delete_file", {"path": "One.lean"}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Cleared and saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Abandon the first and prove another.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)
    assert (tmp_path / "lean" / "Two.lean").exists()


def test_a_partial_writeup_saves_with_an_advisory(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("record_name", {"formal_name": "hardyTwo", "latex_name": "thm:two", "description": "Two."}),
        call("save_latex", {"source": TEX}),
        {"role": "assistant", "content": "Partial writeup saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Write up what exists.")
    last = results(tmp_path)[-1]
    assert last["ok"] is True
    assert "thm:two" in last["output"]
    assert (tmp_path / "tex" / "writeup.tex").exists()


def test_a_namespaced_theorem_is_documented_by_either_name(tmp_path: Path):
    source = "import Mathlib\nnamespace Hardy\ntheorem one : True := by exact True.intro\nend Hardy\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": source}),
        call("record_name", {"formal_name": "Hardy.one", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": TEX}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Use a namespace.")
    assert results(tmp_path)[-1]["ok"] is True, results(tmp_path)
