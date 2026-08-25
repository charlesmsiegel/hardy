from __future__ import annotations

from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

FIRST = "import Mathlib\ntheorem hardyOne : True := by exact True.intro\n"
SECOND = "import Mathlib\ntheorem hardyTwo : True := by exact True.intro\n"
LEMMAS = "import Mathlib\nlemma hardyHelper : True := by exact True.intro\n"


def writeup(*quoted: str, label: str = "thm:one") -> str:
    r"""A document that carries one theorem: a label, and the Lean it is about.

    Both halves, because either alone leaves the reader nothing to check. A
    `\label` says the document claims to describe a theorem; the verbatim Lean
    is what lets a human see that it describes *that* one.
    """
    body = "\n".join(quoted)
    return (
        "\\documentclass{article}\n\\begin{document}\n"
        f"One.\\label{{{label}}}\n"
        f"\\begin{{verbatim}}\n{body}\n\\end{{verbatim}}\n"
        "\\end{document}\n"
    )


TEX = writeup("theorem hardyOne : True")


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
    escaped = (
        "\\documentclass{article}\n\\begin{document}100\\% done \\label{thm:one}\n"
        "\\begin{verbatim}\ntheorem hardyOne : True\n\\end{verbatim}\n\\end{document}\n"
    )
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
        call("save_latex", {"source": writeup("theorem result : True")}),
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


def test_a_label_inside_verb_does_not_release_the_ratchet(tmp_path: Path):
    r"""`\verb|\label{thm:one}|` is a code sample. LaTeX never creates that
    label, so Hardy reads the compiler's .aux rather than the source text --
    the same reason it believes Lean's kernel and not its own reading."""
    verbatim = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\verb|\\label{thm:one}|\n\\end{document}\n"
    )
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": verbatim}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Still blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Show the label as a code sample.")
    assert results(tmp_path)[-1]["ok"] is False
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_a_unicode_theorem_name_still_owes_a_writeup(tmp_path: Path):
    """Lean identifiers are Unicode. A theorem an ASCII pattern could not see
    would never be recorded, and so would never owe anything."""
    greek = "import Mathlib\ntheorem α : True := by exact True.intro\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": greek}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove something with a Greek name.")
    saved = results(tmp_path)
    assert saved[0]["ok"] is True
    assert saved[1]["ok"] is False
    assert "α" in saved[1]["output"]


def test_a_theorem_named_on_the_next_line_still_owes_a_writeup(tmp_path: Path):
    """Lean allows a newline between the keyword and the name."""
    split = "import Mathlib\ntheorem\n  hardySplit : True := by exact True.intro\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": split}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Split the declaration over two lines.")
    saved = results(tmp_path)
    assert saved[1]["ok"] is False
    assert "hardySplit" in saved[1]["output"]


def test_deleting_the_fragment_that_held_a_label_closes_the_ratchet_again(tmp_path: Path):
    """The label index has to follow the deletion.

    Otherwise the .aux keeps a label no remaining file provides, and the next
    theorem is released on the strength of a writeup that is gone.
    """
    root = "\\documentclass{article}\n\\begin{document}Body.\\end{document}\n"
    fragment = (
        "Section one.\\label{thm:one}\n"
        "\\begin{verbatim}\ntheorem hardyOne : True\n\\end{verbatim}\n"
    )
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": root}),
        call("save_latex", {"path": "sections/one.tex", "source": fragment}),
        call("delete_file", {"path": "sections/one.tex"}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked again."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Write up, then take it away.")
    saved = results(tmp_path)
    assert saved[4]["ok"] is True, "an unreferenced fragment can be deleted"
    assert saved[5]["ok"] is False, "its label must no longer count"
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_a_namespaced_theorem_is_documented_by_either_name(tmp_path: Path):
    source = "import Mathlib\nnamespace Hardy\ntheorem one : True := by exact True.intro\nend Hardy\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": source}),
        call("record_name", {"formal_name": "Hardy.one", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": writeup("theorem one : True")}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Use a namespace.")
    assert results(tmp_path)[-1]["ok"] is True, results(tmp_path)
