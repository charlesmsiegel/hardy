"""The interactive session's axiom audit.

`_final_gates` already refuses an `axiom` written into the source being saved.
It cannot see one reached through an import, which is the case these cover:
Lean is asked what the saved declarations actually depend on, over the staged
tree, before anything is written.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call, session
from workspace_helpers import results

CLEAN = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro\n"
ASSUMED = CLEAN.rstrip() + " -- axioms: Papers.Smith.main\n"
HOLED = CLEAN.rstrip() + " -- axioms: sorryAx\n"
APPROVAL = {
    "formal_name": "Papers.Smith.main",
    "lean_statement": "True",
    "latex_name": "asm:smith",
    "informal_statement": "Smith's main theorem.",
    "source": "arXiv:2001.00001v2",
    "reason": "not in Mathlib",
}


def saved(tmp_path: Path, name: str = "Main.lean") -> Path:
    return tmp_path / "lean" / name


def state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "session.json").read_text())


def test_a_proof_resting_on_sorry_ax_is_refused(tmp_path: Path):
    """`sorryAx` clears the word-boundary hole regex; the audit catches it."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "sorryAx" in refusal["output"]
    assert not saved(tmp_path).exists()


def test_a_hole_is_never_offered_for_approval(tmp_path: Path):
    """A human cannot approve a hole, so nothing may ask them to."""
    asked = []
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.confirm = lambda proposal: asked.append(proposal) or True
    chat.send("Save it.")
    assert asked == []
    assert not saved(tmp_path).exists()


def test_an_axiom_reached_through_an_import_is_refused(tmp_path: Path):
    """Nothing in this source declares an axiom, so the text gate sees nothing."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": ASSUMED}, "lean")]))
    chat.send("Save it.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]
    assert "request_assumption" in refusal["output"]
    assert not saved(tmp_path).exists()


def test_an_approved_assumption_saves_and_is_graded_modulo(tmp_path: Path):
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", dict(APPROVAL), "ask"),
            call("save_lean", {"source": ASSUMED}, "lean"),
        ]),
        approvals=[True],
    )
    chat.send("Assume it, then save.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"]
    assert "approved assumptions ['Papers.Smith.main']" in result["output"]
    assert saved(tmp_path).exists()
    assert state(tmp_path)["audit"]["Main"]["status"] == "modulo"


def test_a_declined_assumption_still_blocks_the_save(tmp_path: Path):
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", dict(APPROVAL), "ask"),
            call("save_lean", {"source": ASSUMED}, "lean"),
        ]),
        approvals=[False],
    )
    chat.send("Assume it, then save.")
    assert not results(tmp_path, "save_lean")[-1]["ok"]
    assert not saved(tmp_path).exists()


def test_a_clean_audit_saves_and_says_so(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"]
    assert "standard axioms only" in result["output"]
    recorded = state(tmp_path)["audit"]["Main"]
    assert recorded["status"] == "clean"
    assert recorded["declarations"] == [{"name": "HardyTarget", "axioms": []}]


def test_the_audit_covers_a_module_the_save_did_not_touch(tmp_path: Path):
    """Rebuilding a dependent is not enough: what it now rests on is new too."""
    # A lemma, so the writeup ratchet does not block the second save. It is
    # audited all the same: an unsound helper reaches every theorem above it.
    helper = "import Mathlib\n\nlemma Helper.base : True := by exact True.intro\n"
    consumer = "import Helper\n\ntheorem HardyTarget : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": helper, "path": "Helper.lean"}, "one"),
            call("save_lean", {"source": consumer}, "two"),
        ]),
    )
    chat.send("Save both.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    # Now poison the helper. The consumer is untouched, and inherits the axiom.
    chat.runtime.script = [
        call("save_lean", {"source": helper.rstrip() + " -- axioms: Papers.Smith.main\n",
                           "path": "Helper.lean"}, "three"),
    ]
    chat.send("Change the helper.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "HardyTarget" in refusal["output"], "the dependent's own audit must be reported"
    assert "-- axioms" not in saved(tmp_path, "Helper.lean").read_text()


def test_a_file_declaring_no_theorem_has_nothing_to_audit(tmp_path: Path):
    """Scaffolding claims nothing, so it is not gated on claiming nothing."""
    scaffold = "import Mathlib\n\ndef helper : Nat := 0 -- exact True.intro\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": scaffold}, "lean")]))
    chat.send("Save it.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"]
    assert saved(tmp_path).exists()
    assert state(tmp_path)["audit"]["Main"]["status"] == "not established"
    assert "no theorem" in state(tmp_path)["audit"]["Main"]["reason"]


def test_an_unreadable_audit_refuses_rather_than_saving(tmp_path: Path):
    """A report Hardy cannot read is not a report that found nothing."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    # The audit line is what Hardy appends; without it Lean reports nothing.
    chat.lean.with_audit = staticmethod(lambda source, targets: source)
    chat.send("Save it.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "could not be established" in refusal["output"]
    assert not saved(tmp_path).exists()


def test_the_workspace_listing_carries_the_last_audit(tmp_path: Path):
    """A model that cannot see what its tree rests on cannot report it."""
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": CLEAN}, "lean"),
            call("read_workspace", {}, "list"),
        ]),
    )
    chat.send("Save it and show me the workspace.")
    listing = json.loads(results(tmp_path, "read_workspace")[-1]["output"])
    assert listing["audit"]["Main"]["status"] == "clean"


def test_an_unrelated_save_does_not_overwrite_an_earlier_verdict(tmp_path: Path):
    """Each module's record is its own, or a scaffolding save would erase the
    only evidence of what the theorem beside it rests on."""
    scaffold = "import Mathlib\n\ndef helper : Nat := 0 -- exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": CLEAN}, "one"),
            call("save_lean", {"source": scaffold, "path": "Scratch.lean"}, "two"),
        ]),
    )
    chat.send("Save both.")
    recorded = state(tmp_path)["audit"]
    assert recorded["Main"]["status"] == "clean"
    assert recorded["Scratch"]["status"] == "not established"


def test_deleting_a_module_drops_its_audit_record(tmp_path: Path):
    """A record left behind would describe declarations that are gone."""
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": CLEAN, "path": "Scratch.lean"}, "save"),
            call("delete_file", {"path": "Scratch.lean"}, "drop"),
        ]),
    )
    chat.send("Save it, then remove it.")
    assert results(tmp_path, "delete_file")[-1]["ok"]
    assert state(tmp_path)["audit"] == {}


def test_check_lean_is_not_gated_by_the_audit(tmp_path: Path):
    """Scratch work is where a model finds out what an axiom costs it."""
    chat = session(tmp_path, FakeChatRuntime([call("check_lean", {"source": HOLED}, "check")]))
    chat.send("Check it.")
    assert results(tmp_path, "check_lean")[-1]["ok"]
    assert not saved(tmp_path).exists()
