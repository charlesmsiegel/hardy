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


def test_a_commented_out_label_does_not_release_the_ratchet(tmp_path: Path):
    """`% \\label{thm:one}` is a placeholder, not a writeup: LaTeX never
    creates that label and the document describes nothing."""
    commented = "\\documentclass{article}\n\\begin{document}% \\label{thm:one}\n\\end{document}\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": commented}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Still blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Comment out the label.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_an_escaped_percent_does_not_hide_a_real_label(tmp_path: Path):
    escaped = "\\documentclass{article}\n\\begin{document}100\\% done \\label{thm:one}\n\\end{document}\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": escaped}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Released."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Use an escaped percent.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)


def test_a_bare_name_shared_by_two_theorems_documents_neither(tmp_path: Path):
    """`A.result` and `B.result` both answer to `result`. One label must not
    cover both, or a theorem is reported as written up while nothing in the
    document refers to it."""
    first = "import Mathlib\nnamespace A\ntheorem result : True := by exact True.intro\nend A\n"
    second = "import Mathlib\nnamespace B\ntheorem result : True := by exact True.intro\nend B\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "A.lean", "source": first}),
        call("record_name", {"formal_name": "result", "latex_name": "thm:one", "description": "A result."}),
        call("save_latex", {"source": TEX}),
        call("save_lean", {"path": "B.lean", "source": second}),
        call("save_lean", {"path": "C.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked once ambiguous."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Share a leaf name.")
    saved = results(tmp_path)
    # B.lean saves: at that moment `result` names only A.result, so it is
    # documented and the ratchet is open.
    assert saved[3]["ok"] is True, saved
    # Once both exist the bare name is ambiguous, so neither counts and the
    # next new theorem is refused.
    assert saved[4]["ok"] is False
    assert not (tmp_path / "lean" / "C.lean").exists()


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
