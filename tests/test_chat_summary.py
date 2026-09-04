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


def test_a_verdict_the_session_has_expired_is_handed_out_as_expired(tmp_path: Path):
    """`/status --full` and `/export` must not read `state["audit"]` raw: the
    session already expires a verdict whose toolchain, source or dependencies
    moved, and a page that ignored that would call a theorem kernel-verified
    beside its own "no longer established"."""
    chat = built(tmp_path)
    assert "hardyBasic: kernel-verified" in chat.summary().text()

    # What `_still_current` is for: the build signature no longer matches.
    for record in chat.state["audit"].values():
        record["signature"] = "moved"
    chat._save_state()

    text = chat.summary().text()
    assert "kernel-verified" not in text
    assert "hardyBasic: no longer established" in text
    assert all(
        record.get("stale") for record in chat.export_material()["audit"].values()
    )


def test_the_summary_and_the_export_gather_under_the_session_gate(tmp_path: Path):
    """`/status --full` is safe in flight, so a `save_lean` on another thread
    can land between two of the reads. The pair that must not straddle one is
    the audit and the sources: an old verdict beside a new statement says
    "kernel-verified" about content Lean never saw."""
    chat = built(tmp_path)
    held: list[str] = []

    class Watched:
        """Stands in for the gate, recording that it was held throughout."""

        def __init__(self):
            self.depth = 0

        def __enter__(self):
            self.depth += 1
            held.append("enter")
            return self

        def __exit__(self, *exception):
            held.append("exit")
            self.depth -= 1
            return False

    watched = Watched()
    chat._gate = watched
    # Read inside the gathering, so a witness can say the gate was open then.
    original = chat._theorem_statements

    def watching(sources=None):
        assert watched.depth == 1, "the sources were read outside the gate"
        return original(sources)

    chat._theorem_statements = watching
    chat.summary()
    chat.export_material()
    assert held == ["enter", "exit", "enter", "exit"]


def test_a_linked_writeup_is_not_reported_as_a_compiled_document(tmp_path: Path):
    """`is_file` and `stat` both follow a link, so a checked-out
    `writeup.pdf -> <any file>` had the page state that Hardy compiled a
    document and report that file's size. Hardy compiled nothing."""
    chat = built(tmp_path)
    elsewhere = tmp_path / "not-ours.pdf"
    elsewhere.write_bytes(b"%PDF-not-hardys")
    (Path(chat.workspace) / "writeup.pdf").symlink_to(elsewhere)

    assert "No compiled document" in chat.export_material()["document"]


def test_the_audit_and_the_statement_come_from_one_read_of_the_tree(tmp_path: Path):
    """The gate serializes Hardy's own tool calls and nothing else; editing a
    `.lean` file behind Hardy is supported. Two reads could straddle such an
    edit and pair a still-current verdict with a statement it was never about.

    The obligations are deliberately not part of this: they are the same
    independent computation `/status` and `report_result` use, and a later read
    can only make them ask for more.
    """
    chat = built(tmp_path)
    read = chat.lean_workspace.sources
    calls: list[int] = []
    edited = BASIC.replace("True := by exact True.intro", "1 = 1 := by rfl")

    def moving():
        calls.append(1)
        found = read()
        # An editor changes the file between one read of the tree and the next.
        return found if len(calls) == 1 else dict.fromkeys(found, edited)

    chat.lean_workspace.sources = moving
    material = chat.export_material()

    assert "1 = 1" not in material["theorems"]["hardyBasic"], (
        "a verdict was paired with a statement it never graded"
    )
    assert "1 = 1" not in "".join(material["lean"].values()), (
        "the page prints a source the verdict beside it is not about"
    )
