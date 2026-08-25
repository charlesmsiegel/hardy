"""Saying the work is finished, and being refused until it is.

Every other gate in this session guards an artifact, and a model that writes no
artifact walks past all of them: it can prove a theorem in conversation, say so
warmly, and leave a workspace with nothing in it. So the claim itself is a tool
call, checked against the same two trees everything else is checked against --
and what the checks look for is what a reader would need to check the work by
hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import events, results

THEOREM = "import Mathlib\ntheorem hardyOne : True := by exact True.intro\n"
STATEMENT = "theorem hardyOne : True"
LEMMA = "import Mathlib\nlemma hardyHelper : True := by exact True.intro\n"
APPROVAL = {
    "formal_name": "Sylow.first",
    "lean_statement": "True",
    "latex_name": "asm:sylow",
    "informal_statement": "Sylow's first theorem.",
    "source": "Rotman, Theorem 4.12",
    "reason": "not found in Mathlib",
}
ASSUMED = (
    "import Mathlib\n"
    "axiom Sylow.first : True\n"
    "theorem hardyOne : True := by exact True.intro\n"
)


def paper(body: str) -> str:
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def quoted(*lines: str) -> str:
    return "\\begin{verbatim}\n" + "\n".join(lines) + "\n\\end{verbatim}\n"


CARRIED = paper("One.\\label{thm:one}\n" + quoted(STATEMENT))
RECORD = call(
    "record_name",
    {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."},
)
REPORT = call("report_result", {"theorems": ["hardyOne"], "summary": "True holds."})


def state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))


def test_a_report_with_no_lean_at_all_is_refused(tmp_path: Path):
    """The failure this whole gate exists for: prose, and nothing behind it."""
    runtime = FakeChatRuntime([
        call("save_latex", {"source": paper("A complete and beautiful proof.")}),
        REPORT,
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove it and tell me you are done.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "nothing here is reportable" in refusal["output"]
    assert "reports" not in state(tmp_path)


def test_a_report_of_lean_the_writeup_never_quotes_is_refused(tmp_path: Path):
    """A label says the document is about a theorem. It does not say which."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": paper("One.\\label{thm:one}")}),
        REPORT,
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove it, write it up loosely, and report.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "does not quote its Lean statement" in refusal["output"]
    assert STATEMENT in refusal["output"], "the refusal must say exactly what to quote"


def test_a_statement_outside_a_verbatim_block_is_a_different_refusal(tmp_path: Path):
    """TeX outside verbatim eats what Lean needs: a reader compared their
    statement against a mangled copy, which is not a comparison."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": paper(f"One.\\label{{thm:one}} {STATEMENT}")}),
        REPORT,
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Put the statement in the prose.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "not inside a verbatim block" in refusal["output"]


def test_both_halves_present_makes_the_work_reportable(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        REPORT,
        {"role": "assistant", "content": "Reported."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove it, write it up, and report.")
    assert all(item["ok"] for item in results(tmp_path)), results(tmp_path)
    reported = state(tmp_path)["reports"]
    assert reported[0]["theorems"] == ["hardyOne"]
    # The statement is written down with the report, so a later reader of the
    # transcript can see what was claimed without reconstructing the tree.
    assert reported[0]["statements"] == {"hardyOne": STATEMENT}
    assert [event for event in events(tmp_path) if event.get("type") == "report"]


def test_a_lemma_is_not_reportable(tmp_path: Path):
    """`lemma` is scaffolding by construction: it owes no writeup, so it may
    not be reported either. The two rules have to draw the same line."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": LEMMA}),
        call("report_result", {"theorems": ["hardyHelper"], "summary": "A helper."}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Report the helper.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "a lemma is scaffolding that is not reportable" in refusal["output"]


def test_changing_the_statement_takes_the_report_back(tmp_path: Path):
    """The check is derived, never stored. A theorem written up and then
    restated is a theorem whose document describes something else."""
    restated = THEOREM.replace("theorem hardyOne : True :=", "theorem hardyOne : True ∧ True :=")
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        REPORT,
        call("save_lean", {"source": restated}),
        REPORT,
        {"role": "assistant", "content": "Refused the second time."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Report, restate, report again.")
    reports = results(tmp_path, "report_result")
    assert reports[0]["ok"] is True
    assert reports[1]["ok"] is False
    assert "theorem hardyOne : True ∧ True" in reports[1]["output"]


def test_an_assumption_owes_an_appendix_in_both_languages(tmp_path: Path):
    """The Sylow case: a result nobody proved here, holding up one that was.

    A reader who cannot see what was assumed cannot tell this theorem from a
    hypothesis, so the appendix owes them the Lean Hardy was given as well as
    the mathematics a human approved.
    """
    runtime = FakeChatRuntime([
        call("request_assumption", APPROVAL),
        call("save_lean", {"source": ASSUMED}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        REPORT,
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime, approvals=[True])
    chat.send("Assume Sylow and report.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "\\appendix" in refusal["output"]
    assert "Sylow.first" in refusal["output"]


def test_an_appendix_stating_the_assumption_releases_the_report(tmp_path: Path):
    appendix = paper(
        "One.\\label{thm:one}\n"
        + quoted(STATEMENT)
        + "\\appendix\n\\section{Assumptions}\n"
        "Sylow's first theorem, assumed.\\label{asm:sylow}\n"
        + quoted("axiom Sylow.first : True")
    )
    runtime = FakeChatRuntime([
        call("request_assumption", APPROVAL),
        call("save_lean", {"source": ASSUMED}),
        RECORD,
        call("save_latex", {"source": appendix}),
        REPORT,
        {"role": "assistant", "content": "Reported, modulo Sylow."},
    ])
    chat = session(tmp_path, runtime, approvals=[True])
    chat.send("Assume Sylow, write the appendix, and report.")
    report = results(tmp_path, "report_result")[-1]
    assert report["ok"] is True, report
    assert "Sylow.first" in report["output"], "a report must name what it rests on"
    assert state(tmp_path)["reports"][0]["assumptions"] == ["Sylow.first"]


def test_an_unused_approval_owes_no_appendix(tmp_path: Path):
    """An approval nobody used is not an assumption the work rests on, and
    listing it would pad the appendix with disclaimers to rule out by hand."""
    runtime = FakeChatRuntime([
        call("request_assumption", APPROVAL),
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        REPORT,
        {"role": "assistant", "content": "Reported."},
    ])
    chat = session(tmp_path, runtime, approvals=[True])
    chat.send("Ask, then prove it without the axiom.")
    assert results(tmp_path, "report_result")[-1]["ok"] is True


def test_a_turn_ends_with_hardys_own_verdict(tmp_path: Path):
    """Said by Hardy, after the model has stopped talking, read off the tree.

    This is the half the model cannot route around: it may decline to call
    `report_result` and simply announce the result, and the user still sees
    what the workspace does not have.
    """
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        {"role": "assistant", "content": "Proved it. We are done here."},
    ])
    chat = session(tmp_path, runtime)
    notices = [event for event in chat.stream("Prove it.") if event.kind == "notice"]
    assert len(notices) == 1
    assert "not finished" in notices[0].text
    assert "hardyOne" in notices[0].text
    assert [event for event in events(tmp_path) if event.get("type") == "obligations"]


def test_a_finished_workspace_ends_a_turn_quietly(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        {"role": "assistant", "content": "Saved and written up."},
    ])
    chat = session(tmp_path, runtime)
    assert [event for event in chat.stream("Prove and write up.") if event.kind == "notice"] == []


def test_a_save_says_what_the_work_still_owes(tmp_path: Path):
    """Told while the model is saving, not one theorem later when it is
    refused: a model that hears the obligation now can settle it now."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove it.")
    saved = results(tmp_path, "save_lean")[-1]
    assert saved["ok"] is True
    assert "Not reportable yet" in saved["output"]
    assert "hardyOne" in saved["output"]


def test_a_claim_over_an_empty_workspace_is_contradicted(tmp_path: Path):
    """No tool call, no artifact, and a reply that says it is proved.

    There are no obligations to list, because there is nothing to owe them --
    which is exactly what has to be said, since silence would read as assent.
    """
    runtime = FakeChatRuntime([{"role": "assistant", "content": "Proved it. Sylow follows."}])
    chat = session(tmp_path, runtime)
    notices = [event for event in chat.stream("Prove Sylow.") if event.kind == "notice"]
    assert len(notices) == 1
    assert "no theorem is saved" in notices[0].text
    assert "rests on the conversation alone" in notices[0].text


def test_a_lemma_only_workspace_is_still_not_reportable(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": LEMMA}),
        {"role": "assistant", "content": "Done."},
    ])
    chat = session(tmp_path, runtime)
    notices = [event for event in chat.stream("Prove it.") if event.kind == "notice"]
    assert "no theorem is saved" in notices[0].text


def test_a_file_edited_behind_hardys_back_cannot_be_reported(tmp_path: Path):
    """Everything else here reads the source tree, which an edit on disk
    satisfies. The audit is a fact about the build the save established, and it
    expires when anything beneath the module moves -- so the strongest claim
    Hardy makes is the one place that may not be inferred from text."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove and write up.")
    saved = tmp_path / "lean" / "Main.lean"
    # The statement is untouched, so only the audit can object.
    saved.write_text(saved.read_text() + "-- edited on disk\n", encoding="utf-8")
    refusal = chat._tool("report_result", {"theorems": ["hardyOne"], "summary": "True holds."})
    assert refusal.ok is False
    assert "no longer established" in refusal.output


def test_a_workspace_from_before_the_audit_cannot_be_reported(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": THEOREM}),
        RECORD,
        call("save_latex", {"source": CARRIED}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove and write up.")
    chat.state.pop("audit")
    refusal = chat._tool("report_result", {"theorems": ["hardyOne"], "summary": "True holds."})
    assert refusal.ok is False
    assert "never been audited" in refusal.output


def test_two_modules_sharing_a_theorem_name_owe_a_namespace(tmp_path: Path):
    """One name cannot stand for both in the registry, the label, or the
    statement the writeup quotes -- and the second save slips past the ratchet
    precisely because the name is already there."""
    first = "import Mathlib\ntheorem result : True := by exact True.intro\n"
    second = "import Mathlib\ntheorem result : True ∧ True := by exact True.intro\n"
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "A.lean", "source": first}),
        call("record_name", {"formal_name": "result", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": paper("One.\\label{thm:one}\n" + quoted("theorem result : True"))}),
        call("save_lean", {"path": "B.lean", "source": second}),
        call("report_result", {"theorems": ["result"], "summary": "True holds."}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove the same name twice.")
    refusal = results(tmp_path, "report_result")[-1]
    assert refusal["ok"] is False
    assert "each declare a theorem called `result`" in refusal["output"]
    assert "namespace" in refusal["output"]
