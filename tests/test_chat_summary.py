"""What a real session says about itself, from the artifacts (#100, #105)."""

from __future__ import annotations

from pathlib import Path

from test_chat import FakeChatRuntime, call, session

from hardy import export

BASIC = "import Mathlib\ntheorem hardyBasic : True := by exact True.intro\n"
BROKEN = "import Mathlib\ntheorem hardyBroken : True := by exact\n"


def built(tmp_path: Path) -> object:
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime, registered=("hardyBasic",))
    chat.set_goal("Establish the basics")
    chat.send("Save something.")
    return chat


def test_the_summary_names_the_goal_and_the_saved_theorem(tmp_path: Path):
    text = built(tmp_path).summary().text()
    assert "Establish the basics" in text
    assert "Modules" in text and "Basic" in text
    # Under `Proved`, with its own verdict -- not merely somewhere in the page.
    proved = text.split("Proved\n", 1)[1].split("\nOpen", 1)[0].split("\nFailed", 1)[0]
    assert "hardyBasic" in proved


def test_the_summary_reports_a_refused_save_from_the_transcript(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Broken.lean", "source": BROKEN}),
        {"role": "assistant", "content": "That did not work."},
    ])
    chat = session(tmp_path, runtime, registered=("hardyBroken",))
    chat.send("Save something broken.")
    text = chat.summary().text()
    assert "Failed attempts" in text
    assert "save_lean Broken.lean" in text


def test_an_untouched_workspace_summarises_as_having_nothing(tmp_path: Path):
    text = session(tmp_path, FakeChatRuntime([])).summary().text()
    assert "not set (/goal)" in text
    assert "nothing here is reportable" in text
    assert "no Lean module is saved." in text


def test_the_summary_carries_no_spend(tmp_path: Path):
    text = built(tmp_path).summary().text().lower()
    assert "usd" not in text and "spent" not in text


def test_the_export_material_holds_everything_the_page_needs(tmp_path: Path):
    material = built(tmp_path).export_material()
    assert material["goal"] == "Establish the basics"
    assert "hardyBasic" in material["theorems"]
    assert any("hardyBasic" in source for source in material["lean"].values())
    assert material["provenance"]["model"] == "chat-model@test"
    assert any(event.get("type") == "user" for event in material["transcript"])


def test_a_real_session_exports_a_page_that_states_its_own_verdicts(tmp_path: Path):
    page = export.build(export.prepare(built(tmp_path).export_material()))
    assert page.startswith("<!doctype html>")
    assert "hardyBasic" in page
    assert "Establish the basics" in page
    # And the conversation, under the heading that says it proves nothing.
    assert "Save something." in page
    assert "None of it is evidence" in page
