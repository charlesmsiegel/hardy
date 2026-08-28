"""The interactive session's axiom audit.

`_final_gates` already refuses an `axiom` written into the source being saved.
It cannot see one reached through an import, which is the case these cover:
Lean is asked what the saved declarations actually depend on, over the staged
tree, before anything is written.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call
from test_chat import session as _session
from workspace_helpers import results

CLEAN = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro\n"
ASSUMED = CLEAN.rstrip() + " -- axioms: Papers.Smith.main\n"
HOLED = CLEAN.rstrip() + " -- axioms: sorryAx\n"
#: The results these tests state. `theorem` is reserved to names `record_name`
#: has mapped, and none of these tests is about that rule -- they are about
#: what a declaration rests on -- so the fixture registers them and each test
#: goes on saying only what it is for.
RESULTS = ("HardyTarget", "Foo.HardyTarget", "HardySecond", "A", "B", "Top", "«first result»")


def session(tmp_path: Path, runtime: FakeChatRuntime, **kwargs):
    kwargs.setdefault("registered", RESULTS)
    return _session(tmp_path, runtime, **kwargs)


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


def test_a_proof_resting_on_sorry_ax_is_saved_and_recorded_open(tmp_path: Path):
    """The hole is kept, and named. Refusing it left nowhere to build a proof."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    outcome = results(tmp_path, "save_lean")[-1]
    assert outcome["ok"]
    assert saved(tmp_path).exists()
    record = state(tmp_path)["audit"]["Main"]
    assert record["status"] == "open"
    assert "HardyTarget" in str(record["declarations"])


def test_a_hole_is_never_offered_for_approval(tmp_path: Path):
    """A human cannot approve a hole, so nothing may ask them to.

    Still true, and now the interesting case: the save succeeds, so a design
    that reached for approval on the way past would have had one to reach for.
    """
    asked = []
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.confirm = lambda proposal: asked.append(proposal) or True
    chat.send("Save it.")
    assert asked == []
    assert saved(tmp_path).exists()


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


def test_approving_an_imported_assumption_does_not_brick_the_workspace(tmp_path: Path):
    """The refusal tells the model to call `request_assumption`. That records a
    naming-registry entry, and the registry guard demands every registered name
    survive somewhere in the tree — which an axiom reached through an import
    never does. Every later save was then refused, with no tool to undo it.

    The name deliberately appears in no source here. The other tests in this
    file drive the fake Lean with an `-- axioms:` marker, which puts the axiom's
    name in the file and hides exactly this.
    """
    approval = dict(APPROVAL, formal_name="Papers.Smith.elsewhere")
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", approval, "ask"),
            call("save_lean", {"source": CLEAN}, "lean"),
        ]),
        approvals=[True],
    )
    chat.send("Approve it, then save.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
    assert saved(tmp_path).exists()


def test_a_registered_theorem_that_vanishes_is_still_caught(tmp_path: Path):
    """The exemption above is for approved assumptions only. A theorem the
    registry points at must still be required to survive.

    Judged against what the tree held before, not against the registry alone.
    A name is now registered *ahead* of the declaration it maps -- `theorem` is
    reserved to registered results, so the order is record and then save -- and
    a guard asked only of the staged tree refused every save in between,
    including the one that would have introduced the theorem. A name that never
    existed has not vanished; this one did.
    """
    gone = "import Mathlib\n\nlemma hardyElse : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": CLEAN}, "lean"),
            call("save_lean", {"source": gone}, "lean"),
        ]),
    )
    chat.send("Save the theorem, then overwrite it away.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "HardyTarget" in refusal["output"]


def test_a_name_registered_before_its_declaration_blocks_nothing(tmp_path: Path):
    """The order `theorem` now requires: record the result, then state it."""
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("record_name", {"formal_name": "HardyLater", "latex_name": "thm:later",
                                 "description": "coming in a moment"}, "name"),
            call("save_lean", {"path": "Helper.lean",
                               "source": "import Mathlib\n\nlemma hardyHelp : True := by exact True.intro\n"}, "lean"),
        ]),
    )
    chat.send("Register the result, then save a helper.")
    assert results(tmp_path, "save_lean")[-1]["ok"]


def test_a_verdict_from_another_toolchain_is_not_reported_as_current(tmp_path: Path):
    """The verdict says what Lean reported under one toolchain and project.
    Reopening against another rebuilds the oleans but left the old verdict in
    `session.json`, and `read_workspace` handed it to the model as the module's
    current audit until some later save happened to cover it again.
    """
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    assert state(tmp_path)["audit"]["Main"]["status"] == "clean"

    reopened = session(tmp_path, FakeChatRuntime([]), lean_command=("different-lean",))
    record = reopened._workspace_listing()["audit"]["Main"]
    assert record["status"] == "not established"
    assert record["stale"] is True
    # What it said is kept for reference; it is the status that must not pass.
    assert record["declarations"]

    same = session(tmp_path, FakeChatRuntime([]))
    assert same._workspace_listing()["audit"]["Main"]["status"] == "clean"


def test_a_verdict_written_before_verdicts_were_stamped_is_not_current(tmp_path: Path):
    """A workspace saved by an older Hardy has no signature on its records.
    Unknown is not the same as matching."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    stored = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    del stored["audit"]["Main"]["signature"]
    (tmp_path / "session.json").write_text(json.dumps(stored), encoding="utf-8")

    reopened = session(tmp_path, FakeChatRuntime([]))
    assert reopened._workspace_listing()["audit"]["Main"]["status"] == "not established"


def test_a_verdict_expires_when_the_source_changes_outside_a_save(tmp_path: Path):
    """The toolchain is only one of the things a verdict depends on. Editing the
    file on disk moves what the audit was about without any save noticing."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    assert chat._workspace_listing()["audit"]["Main"]["status"] == "clean"

    saved(tmp_path).write_text(CLEAN.replace("True.intro", "True.intro -- edited"), encoding="utf-8")
    assert chat._workspace_listing()["audit"]["Main"]["status"] == "not established"


def test_the_workspace_can_still_be_listed_when_the_tree_is_cyclic(tmp_path: Path):
    """Files edited on disk can form a cycle, and this listing is how the model
    finds out and repairs it — so it must not be the thing that fails. Signing
    verdicts made the signature computation unconditional, which broke exactly
    the case the signature exists to notice."""
    a = "import Mathlib\n\ntheorem A : True := by exact True.intro\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": a, "path": "A.lean"}, "a")]))
    chat.send("Save it.")
    saved(tmp_path, "A.lean").write_text("import B\n\ntheorem A : True := by exact True.intro\n")
    saved(tmp_path, "B.lean").write_text("import A\n\ntheorem B : True := by exact True.intro\n")

    listing = chat._workspace_listing()
    assert [entry["module"] for entry in listing["lean"]]
    assert listing["audit"]["A"]["status"] == "not established"


def test_a_verdict_expires_when_a_module_it_imports_changes(tmp_path: Path):
    """The signature is recursive, so a change beneath a module expires the
    verdict above it — which is the case a per-file hash would miss."""
    base = "import Mathlib\n\nlemma Base.root : True := by exact True.intro\n"
    top = "import Base\n\ntheorem Top : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": base, "path": "Base.lean"}, "base"),
            call("save_lean", {"source": top, "path": "Top.lean"}, "top"),
        ]),
    )
    chat.send("Save both.")
    assert chat._workspace_listing()["audit"]["Top"]["status"] == "clean"

    saved(tmp_path, "Base.lean").write_text(base.replace("True.intro", "True.intro -- edited"), encoding="utf-8")
    listing = chat._workspace_listing()["audit"]
    assert listing["Base"]["status"] == "not established"
    assert listing["Top"]["status"] == "not established"


def test_a_clean_verdict_covers_the_declarations_it_names_and_no_others(tmp_path: Path):
    """A known limit, pinned so it is examined rather than assumed.

    What the audit asks about comes from a textual scan, so a declaration a
    command macro generates is not asked about. A module with *no* literal
    declaration records "not established" — but one with a literal lemma beside
    a generated theorem records "clean", and that verdict covers only the
    literal one. The record names the declarations it covers, which is the only
    thing making the scope readable; closing the gap means enumerating a
    module's exports from the built environment.
    """
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    recorded = state(tmp_path)["audit"]["Main"]
    assert recorded["status"] == "clean"
    # Not a bare status: the names it was established over travel with it.
    assert [item["name"] for item in recorded["declarations"]] == ["HardyTarget"]


def test_an_assumption_inside_a_namespace_needs_only_one_approval(tmp_path: Path):
    """The textual gate read `namespace Foo; axiom bar` as `bar` while Lean
    reports `Foo.bar`. Approving `bar` cleared the gate and was refused by the
    audit; approving `Foo.bar` was refused by the gate. Neither approval could
    save the module, and there was no third option.
    """
    source = (
        "import Mathlib\n\nnamespace Foo\n\naxiom bar : True\n\n"
        "theorem HardyTarget : True := by exact True.intro -- axioms: Foo.bar\n\nend Foo\n"
    )
    approval = dict(APPROVAL, formal_name="Foo.bar", lean_statement="True")
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", approval, "ask"),
            call("save_lean", {"source": source}, "lean"),
        ]),
        approvals=[True],
    )
    chat.send("Approve it, then save.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
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


def test_two_siblings_may_share_a_declaration_name(tmp_path: Path):
    """`A` and `B` both import `Base` and both declare a root-level `step`.
    Neither breaks the other — they never import each other — but a single probe
    importing both puts `step` in scope twice, and Lean will not print an
    ambiguous name. Editing `Base` must not refuse a workspace Lean accepts.
    """
    base = "import Mathlib\n\nlemma Base.root : True := by exact True.intro\n"
    sibling = "import Base\n\nlemma step : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": base, "path": "Base.lean"}, "base"),
            call("save_lean", {"source": sibling, "path": "A.lean"}, "a"),
            call("save_lean", {"source": sibling, "path": "B.lean"}, "b"),
        ]),
    )
    chat.send("Save the base and both siblings.")
    assert results(tmp_path, "save_lean")[-1]["ok"], results(tmp_path, "save_lean")[-1]["output"]

    # Now edit the base. Both siblings rebuild, so both are audited.
    chat.runtime.script = [
        call("save_lean", {"source": base.replace("True.intro", "True.intro -- touched"),
                           "path": "Base.lean"}, "again"),
    ]
    chat.send("Edit the base.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
    assert state(tmp_path)["audit"]["A"]["status"] == "clean"
    assert state(tmp_path)["audit"]["B"]["status"] == "clean"


def test_two_siblings_may_collide_on_something_that_is_not_a_theorem(tmp_path: Path):
    """The audit targets are distinct here — `stepA` and `stepB` — but both
    modules define a root-level `helper`. A grouping check that looked only at
    the names being audited saw no collision, ran the combined probe, and hit
    the duplicate anyway. The clash can be in a def, a structure, an instance;
    the probe retries per module rather than trying to name the kinds.
    """
    base = "import Mathlib\n\nlemma Base.root : True := by exact True.intro\n"
    sibling = "import Base\n\ndef helper : Nat := 0\n\nlemma step{tag} : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": base, "path": "Base.lean"}, "base"),
            call("save_lean", {"source": sibling.format(tag="A"), "path": "A.lean"}, "a"),
            call("save_lean", {"source": sibling.format(tag="B"), "path": "B.lean"}, "b"),
        ]),
    )
    chat.send("Save the base and both siblings.")
    assert results(tmp_path, "save_lean")[-1]["ok"], results(tmp_path, "save_lean")[-1]["output"]

    chat.runtime.script = [
        call("save_lean", {"source": base.replace("True.intro", "True.intro -- touched"),
                           "path": "Base.lean"}, "again"),
    ]
    chat.send("Edit the base.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
    assert state(tmp_path)["audit"]["A"]["status"] == "clean"
    assert state(tmp_path)["audit"]["B"]["status"] == "clean"


def test_a_shared_name_is_still_audited_rather_than_skipped(tmp_path: Path):
    """Splitting the probe must not lose the finding it exists to make."""
    base = "import Mathlib\n\nlemma Base.root : True := by exact True.intro\n"
    clean_sibling = "import Base\n\nlemma step : True := by exact True.intro\n"
    poisoned = clean_sibling.rstrip() + " -- axioms: Papers.Smith.main\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": base, "path": "Base.lean"}, "base"),
            call("save_lean", {"source": clean_sibling, "path": "A.lean"}, "a"),
            call("save_lean", {"source": poisoned, "path": "B.lean"}, "b"),
        ]),
    )
    chat.send("Save the base and both siblings.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]
    assert not saved(tmp_path, "B.lean").exists()


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


def test_a_guillemet_quoted_declaration_is_auditable(tmp_path: Path):
    """`theorem «first result»` is ordinary Lean and `declarations` reports it,
    so the audit has to be able to ask about a name containing a space."""
    quoted = "import Mathlib\n\ntheorem «first result» : True := by exact True.intro\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": quoted}, "lean")]))
    chat.send("Save it.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
    assert state(tmp_path)["audit"]["Main"]["declarations"] == [
        {"name": "«first result»", "axioms": []}
    ]


def test_a_private_lemma_does_not_make_the_workspace_unsaveable(tmp_path: Path):
    """Lean mangles a private name so no importing file can reach it, and the
    audit asks its questions over an import. Asking about one errors, which
    would refuse every save of a file using the ordinary `private lemma` idiom.
    """
    scaffolded = (
        "import Mathlib\n\n"
        "private lemma step : True := by exact True.intro\n\n"
        "theorem HardyTarget : True := by exact True.intro\n"
    )
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": scaffolded}, "lean")]))
    chat.send("Save it.")
    result = results(tmp_path, "save_lean")[-1]
    assert result["ok"], result["output"]
    # The exported theorem is audited; the private helper is not asked about,
    # and does not need to be — anything exported that uses it inherits its axioms.
    assert state(tmp_path)["audit"]["Main"]["declarations"] == [
        {"name": "HardyTarget", "axioms": []}
    ]


def test_a_private_theorem_is_refused_rather_than_left_unaudited(tmp_path: Path):
    """A `theorem` is what the workspace reports as a result, and one nothing
    outside the module can name can be neither audited nor cited in a writeup.
    Skipping it silently would leave a documented result nothing checked."""
    hidden = "import Mathlib\n\nprivate theorem HardyTarget : True := by exact True.intro\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": hidden}, "lean")]))
    chat.send("Save it.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "HardyTarget" in refusal["output"]
    assert "private" in refusal["output"]
    assert not saved(tmp_path).exists()


def test_a_module_of_only_private_lemmas_is_not_a_clean_audit(tmp_path: Path):
    """Nothing exported means nothing established, not everything established."""
    private_only = "import Mathlib\n\nprivate lemma step : True := by exact True.intro\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": private_only}, "lean")]))
    chat.send("Save it.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    assert state(tmp_path)["audit"]["Main"]["status"] == "not established"


def test_check_lean_is_not_gated_by_the_audit(tmp_path: Path):
    """Scratch work is where a model finds out what an axiom costs it."""
    chat = session(tmp_path, FakeChatRuntime([call("check_lean", {"source": HOLED}, "check")]))
    chat.send("Check it.")
    assert results(tmp_path, "check_lean")[-1]["ok"]
    assert not saved(tmp_path).exists()


def test_an_open_theorem_is_named_in_what_the_workspace_owes(tmp_path: Path):
    """A hole nobody is told about is worse than one that is refused."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    owed = chat.obligations()
    assert owed[0].kind == "open"
    assert any(item.subject == "HardyTarget" and "hole" in item.detail for item in owed)


def test_an_open_theorem_owes_no_writeup_yet(tmp_path: Path):
    """Otherwise a skeleton owes a paragraph about a theorem nobody has proved."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    assert {item.kind for item in chat.obligations()} == {"open"}


def test_closing_the_hole_moves_the_obligation_to_the_writeup(tmp_path: Path):
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"source": CLEAN}, "lean"),
        ]),
    )
    chat.send("Save it, then close it.")
    kinds = {item.kind for item in chat.obligations()}
    assert "open" not in kinds
    # The fixture registered it, so what a closed theorem owes now is the
    # document's half: a label the compiler really made, and its statement
    # quoted where a reader can compare.
    assert "label" in kinds


def test_an_open_theorem_does_not_block_the_next_one(tmp_path: Path):
    """The catch-up ratchet is about writeups, and an open theorem owes none."""
    second = (
        "import Mathlib\n\ntheorem HardySecond : True := by exact True.intro"
        " -- axioms: sorryAx\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"path": "Second.lean", "source": second}, "lean"),
        ]),
    )
    chat.send("Save two skeletons.")
    assert results(tmp_path, "save_lean")[-1]["ok"]


def test_a_report_naming_an_open_theorem_is_graded_partial(tmp_path: Path):
    """It is a real result to have got this far, and it is not a proof."""
    carried = (
        "\\documentclass{article}\n\\begin{document}\n"
        "The target.\\label{thm:HardyTarget}\n"
        "\\begin{verbatim}\ntheorem HardyTarget : True\n\\end{verbatim}\n"
        "\\end{document}\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_latex", {"source": carried}, "tex"),
        ]),
    )
    chat.send("Save the skeleton and write it up.")
    reported = chat._tool(
        "report_result", {"theorems": ["HardyTarget"], "summary": "As far as I got."}
    )
    assert reported.ok, reported.output
    assert "partial" in reported.output
    assert "HardyTarget" in reported.output
    assert state(tmp_path)["reports"][-1]["status"] == "partial"


def test_a_report_of_an_open_theorem_the_writeup_never_quotes_is_refused(tmp_path: Path):
    """Partial is not a discount on the document. A reader who cannot see the
    statement cannot tell which half of the work was done."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save the skeleton.")
    reported = chat._tool(
        "report_result", {"theorems": ["HardyTarget"], "summary": "As far as I got."}
    )
    assert not reported.ok
    assert "HardyTarget" in reported.output


def test_an_open_theorem_is_not_counted_as_machine_checked(tmp_path: Path):
    """A banner that calls a holed proof checked is worse than no banner."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    assert "0 theorems machine-checked" in chat._stamp()
    assert "1 theorem here is still open" in chat._stamp()
    # Named, not merely counted. The PDF is the artifact a reader holds, and a
    # banner saying only how many claims are unproved leaves them unable to
    # tell which of the ones in front of them it means.
    assert "HardyTarget" in chat._stamp()


def test_an_open_theorems_name_is_escaped_for_the_banner(tmp_path: Path):
    """Lean names carry `_`, which is TeX's subscript and breaks a document."""
    holed = (
        "import Mathlib\n\ntheorem Hardy_under : True := by exact True.intro"
        " -- axioms: sorryAx\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([call("save_lean", {"source": holed}, "lean")]),
        registered=("Hardy_under",),
    )
    chat.send("Save it.")
    assert "Hardy\\_under" in chat._stamp()


CARRIED_TEX = (
    "\\documentclass{article}\n\\begin{document}\n"
    "The target.\\label{thm:HardyTarget}\n"
    "\\begin{verbatim}\ntheorem HardyTarget : True\n\\end{verbatim}\n"
    "\\end{document}\n"
)


def test_open_theorems_still_accumulate_once_a_writeup_has_been_compiled(tmp_path: Path):
    """The guarantee above, in the workspace where it was quietly false.

    Saving the first open theorem moves the banner's open set, which moves the
    tex signature, which makes `_stale_writeup` report the compiled document as
    out of date. That obligation is not an `open` one, so the ratchet counted
    it and refused the next skeleton -- forcing a LaTeX recompile between two
    saves that owe the document nothing.
    """
    second = (
        "import Mathlib\n\ntheorem HardySecond : True := by exact True.intro"
        " -- axioms: sorryAx\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_latex", {"source": CARRIED_TEX}, "tex"),
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"path": "Second.lean", "source": second}, "lean"),
        ]),
    )
    chat.send("Write up, then save two skeletons.")
    saves = results(tmp_path, "save_lean")
    assert saves[-1]["ok"], saves[-1]["output"]


def test_a_compiled_writeup_still_goes_stale_when_a_theorem_reopens(tmp_path: Path):
    """And the staleness itself must survive: the banner counts an open theorem
    out of `machine-checked`, so a PDF compiled before the hole appeared says
    something about this workspace that is no longer true."""
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": CLEAN}, "lean"),
            call("save_latex", {"source": CARRIED_TEX}, "tex"),
            call("save_lean", {"source": HOLED}, "lean"),
        ]),
    )
    chat.send("Prove it, write it up, then break it open again.")
    kinds = {(item.kind, item.subject) for item in chat.obligations()}
    assert ("open", "HardyTarget") in kinds
    assert ("label", "") in kinds, "the compiled banner now overstates and must say so"


def test_a_hole_no_audited_declaration_could_account_for_is_refused(tmp_path: Path):
    """The audit asks `#print axioms` about theorems and lemmas, and about
    nothing else. A file declaring neither has no way to report a hole as open,
    so a hole in one would sit in the workspace unnamed by `/status`, by the
    end-of-turn notice, and by the banner -- which is the one thing saving
    holes was not allowed to cost."""
    source = "import Mathlib\n\ndef hardyUnfinished : Nat := by sorry\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    chat.send("Save a definition with a hole.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "lemma" in refusal["output"]
    assert not saved(tmp_path).exists()


def test_an_all_open_workspace_is_not_told_it_may_report_nothing(tmp_path: Path):
    """`report_result` accepts an open theorem as a partial result, so a notice
    saying none of it is reportable contradicts a report Hardy would take."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    events = [item for item in chat.stream("Save it.") if item.kind == "notice"]
    assert events, "an open workspace still owes a notice"
    text = events[-1].text
    assert "partial" in text
    assert "None of this is reportable" not in text
    note = results(tmp_path, "save_lean")[-1]["output"]
    assert "Not reportable yet" not in note
    assert "partial" in note


def test_the_word_sorry_in_a_comment_is_not_a_hole(tmp_path: Path):
    """Real Lean does not read a `sorry` inside a comment, so neither may the
    stand-in: reading one there audits a finished proof as open, and lets a
    candidate that cannot elaborate look like one that elaborates with a hole."""
    source = (
        "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
        "\n-- no sorry is needed here\n"
    )
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    chat.send("Save it with a remark.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    assert state(tmp_path)["audit"]["Main"]["status"] == "clean"
    assert not [item for item in chat.obligations() if item.kind == "open"]


def test_a_report_resting_on_an_assumption_is_not_recorded_as_clean(tmp_path: Path):
    """The durable grade said `clean` while the sentence beside it said the
    work was modulo an approved axiom. A record that has to be read against
    its own message is not a record."""
    carried = (
        "\\documentclass{article}\n\\begin{document}\n"
        "The target.\\label{thm:HardyTarget}\n"
        "\\begin{verbatim}\ntheorem HardyTarget : True\n\\end{verbatim}\n"
        "\\appendix\nSmith's main theorem.\\label{asm:smith}\n"
        "\\begin{verbatim}\naxiom Papers.Smith.main : True\n\\end{verbatim}\n"
        "\\end{document}\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", dict(APPROVAL), "ask"),
            call("save_lean", {"source": ASSUMED}, "lean"),
            call("save_latex", {"source": carried}, "tex"),
        ]),
        approvals=[True],
    )
    chat.send("Assume it, prove it, write it up.")
    reported = chat._tool(
        "report_result", {"theorems": ["HardyTarget"], "summary": "True holds."}
    )
    assert reported.ok, reported.output
    assert state(tmp_path)["reports"][-1]["status"] == "modulo"
    assert "as modulo" in reported.output


def test_an_open_theorem_in_a_theorem_environment_still_backs_it(tmp_path: Path):
    """The prompt asks the model to put a result in a theorem environment, so
    the partial report has to survive one. It did not: an open theorem was
    left out of the set that backs a document assertion, so the environment
    read as backed by nothing and the report was refused."""
    carried = (
        "\\documentclass{article}\n"
        "\\newtheorem{theorem}{Theorem}\n\\begin{document}\n"
        "\\begin{theorem}\\label{thm:HardyTarget}\nThe target holds.\n\\end{theorem}\n"
        "\\begin{verbatim}\ntheorem HardyTarget : True\n\\end{verbatim}\n"
        "\\end{document}\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_latex", {"source": carried}, "tex"),
        ]),
    )
    chat.send("Save the skeleton and write it up properly.")
    assert not [item for item in chat.obligations() if item.kind == "theorem"]
    reported = chat._tool(
        "report_result", {"theorems": ["HardyTarget"], "summary": "As far as I got."}
    )
    assert reported.ok, reported.output
    assert "partial" in reported.output


def test_a_bare_name_two_open_theorems_share_documents_neither(tmp_path: Path):
    """`A.t` and `B.t` both answer to `t`. Passing only the claimed theorem to
    the completion check hid the other, so the leaf looked unique and one label
    was accepted as documentation for a theorem the document never named."""
    first = (
        "import Mathlib\nnamespace A\ntheorem t : True := by exact True.intro"
        " -- axioms: sorryAx\nend A\n"
    )
    second = (
        "import Mathlib\nnamespace B\ntheorem t : True := by exact True.intro"
        " -- axioms: sorryAx\nend B\n"
    )
    carried = (
        "\\documentclass{article}\n\\begin{document}\n"
        "One.\\label{thm:t}\n"
        "\\begin{verbatim}\ntheorem t : True\n\\end{verbatim}\n"
        "\\end{document}\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("record_name", {"formal_name": "t", "latex_name": "thm:t",
                                 "description": "A result."}, "name"),
            call("save_lean", {"path": "A.lean", "source": first}, "lean"),
            call("save_lean", {"path": "B.lean", "source": second}, "lean"),
            call("save_latex", {"source": carried}, "tex"),
        ]),
        registered=(),
    )
    chat.send("Share a leaf name across two skeletons.")
    reported = chat._tool("report_result", {"theorems": ["A.t"], "summary": "One of them."})
    assert not reported.ok, reported.output
    assert "A.t" in reported.output


def test_the_word_sorry_in_a_string_is_not_an_untrackable_hole(tmp_path: Path):
    """`LeanRunner.has_holes` read raw source, so the guard for a hole nothing
    can report refused valid Lean over a word in a string literal."""
    source = 'import Mathlib\n\ndef hardyNote : String := "sorry"\n'
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    assert not chat.lean.has_holes(source)
    assert not chat.lean.has_holes("theorem t : True := trivial -- without sorry\n")
    assert chat.lean.has_holes("theorem t : True := by sorry\n")
    chat.send("Save a definition mentioning the word.")
    outcome = results(tmp_path, "save_lean")[-1]
    # Whatever else this file does or does not do in Lean, the word in a string
    # must not be what stops it: that refusal names a hole nobody wrote.
    assert "declares no theorem or lemma" not in outcome["output"]


def test_closing_a_hole_and_adding_a_theorem_in_one_save_is_refused(tmp_path: Path):
    """The ratchet asks its question before Lean, so it cannot see a closure
    the same save performs.

    Opening `HardyTarget` costs nothing, so the gate lets the next save in --
    and that save closes it *and* introduces `HardySecond`, leaving two
    undocumented closed theorems where the ratchet permits one. Only the audit
    knows which holes this source closes, so the question is asked again once
    it has run, before anything is committed.
    """
    both = (
        "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro\n"
        "theorem HardySecond : True := by exact True.intro\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"source": both}, "lean"),
        ]),
    )
    chat.send("Open one, then close it and add another at once.")
    saves = results(tmp_path, "save_lean")
    assert saves[0]["ok"]
    assert not saves[1]["ok"], saves[1]["output"]
    assert "HardySecond" in saves[1]["output"]
    # And nothing was written: the tree still holds only the open theorem.
    assert "HardySecond" not in saved(tmp_path).read_text()
    assert [item.kind for item in chat.obligations()] == ["open"]


def test_closing_a_hole_on_its_own_is_always_allowed(tmp_path: Path):
    """The refusal above is about *adding* alongside a closure, not about
    closing. A model must never be unable to finish the proof it is holding."""
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"source": CLEAN}, "lean"),
        ]),
    )
    chat.send("Open it, then close it.")
    saves = results(tmp_path, "save_lean")
    assert all(item["ok"] for item in saves), saves


def test_a_bare_registry_name_backs_an_open_theorem_environment(tmp_path: Path):
    """The rest of `outstanding` lets a bare entry stand for its sole qualified
    declaration; the environment scan did not, so a correctly labelled theorem
    environment for `A.t` registered as `t` read as backed by nothing."""
    source = (
        "import Mathlib\nnamespace A\ntheorem t : True := by exact True.intro"
        " -- axioms: sorryAx\nend A\n"
    )
    carried = (
        "\\documentclass{article}\n"
        "\\newtheorem{theorem}{Theorem}\n\\begin{document}\n"
        "\\begin{theorem}\\label{thm:t}\nIt holds.\n\\end{theorem}\n"
        "\\begin{verbatim}\ntheorem t : True\n\\end{verbatim}\n"
        "\\end{document}\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("record_name", {"formal_name": "t", "latex_name": "thm:t",
                                 "description": "A result."}, "name"),
            call("save_lean", {"source": source}, "lean"),
            call("save_latex", {"source": carried}, "tex"),
        ]),
        registered=(),
    )
    chat.send("Register a bare name, state it in a namespace, write it up.")
    assert not [item for item in chat.obligations() if item.kind == "theorem"]
    reported = chat._tool("report_result", {"theorems": ["A.t"], "summary": "So far."})
    assert reported.ok, reported.output
    assert "partial" in reported.output


def test_a_registered_name_mentioned_only_in_a_comment_never_existed(tmp_path: Path):
    """`_resolves` falls back to a textual scan, which reads comments. A helper
    that merely names the coming result made the guard believe it existed, so
    editing that comment away read as the declaration vanishing."""
    mentions = (
        "import Mathlib\n\n-- HardyLater will go here\n"
        "lemma hardyHelp : True := by exact True.intro\n"
    )
    without = "import Mathlib\n\nlemma hardyHelp : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("record_name", {"formal_name": "HardyLater", "latex_name": "thm:later",
                                 "description": "coming"}, "name"),
            call("save_lean", {"path": "Helper.lean", "source": mentions}, "lean"),
            call("save_lean", {"path": "Helper.lean", "source": without}, "lean"),
        ]),
        registered=(),
    )
    chat.send("Mention the coming result, then tidy the comment away.")
    saves = results(tmp_path, "save_lean")
    assert all(item["ok"] for item in saves), saves


def test_a_hole_marks_only_the_declaration_that_carries_it(tmp_path: Path):
    """`#print axioms` answers about one declaration. A stand-in that attached
    the hole to the whole module marked a finished theorem open whenever an
    unfinished one sat beside it, so every test about closing one hole while
    another stays open exercised the opposite of production."""
    source = (
        "import Mathlib\n\ntheorem HardyTarget : True := by sorry\n"
        "theorem HardySecond : True := by exact True.intro\n"
    )
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    chat.send("Save one open and one closed.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    rested = {
        entry["name"]: entry["axioms"]
        for entry in state(tmp_path)["audit"]["Main"]["declarations"]
    }
    assert "sorryAx" in rested["HardyTarget"]
    assert "sorryAx" not in rested["HardySecond"]
    assert [item.subject for item in chat.obligations() if item.kind == "open"] == [
        "HardyTarget"
    ]


def test_only_the_hole_is_forgiven_not_the_rest_of_the_file(tmp_path: Path):
    """A holed file still has to elaborate. Accepting one because it had a hole
    would let a hermetic test assert that `save_lean` takes a file real Lean
    rejects, which is the promise this whole change rests on."""
    source = (
        "import Mathlib\n\ntheorem HardyTarget : True := by sorry\n"
        "theorem HardySecond : True := by nonsense_tactic\n"
    )
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    chat.send("Save a skeleton with a broken proof beside it.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert not saved(tmp_path).exists()


def test_a_theorem_importing_a_holed_module_is_still_open(tmp_path: Path):
    """The direction attribution must never close. Narrowing a hole to the
    declaration that writes it is only sound inside one file; across an import
    this stand-in has no dependency graph, so anything declared beside a holed
    import reports the hole rather than risking a false clean."""
    holed = "import Mathlib\n\nlemma hardyStep : True := by sorry\n"
    user = "import Step\n\ntheorem HardyTarget : True := by exact True.intro\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"path": "Step.lean", "source": holed}, "lean"),
            call("save_lean", {"source": user}, "lean"),
        ]),
    )
    chat.send("Save a holed helper, then a theorem importing it.")
    assert all(item["ok"] for item in results(tmp_path, "save_lean"))
    assert "HardyTarget" in [
        item.subject for item in chat.obligations() if item.kind == "open"
    ]


def test_two_declarations_sharing_a_leaf_are_attributed_apart(tmp_path: Path):
    """`A.t` holed and `B.t` finished are two declarations, and real
    `#print axioms B.t` answers about the finished one. Comparing by last
    component alone marked both open, so a test closing one of them would have
    been reading an audit state production never produces."""
    source = (
        "import Mathlib\n"
        "namespace A\ntheorem t : True := by sorry\nend A\n"
        "namespace B\ntheorem t : True := by exact True.intro\nend B\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("record_name", {"formal_name": "A.t", "latex_name": "thm:at",
                                 "description": "A's."}, "name"),
            call("record_name", {"formal_name": "B.t", "latex_name": "thm:bt",
                                 "description": "B's."}, "name"),
            call("save_lean", {"source": source}, "lean"),
        ]),
        registered=(),
    )
    chat.send("Two namespaces, one hole.")
    assert results(tmp_path, "save_lean")[-1]["ok"], results(tmp_path, "save_lean")[-1]["output"]
    rested = {
        entry["name"]: entry["axioms"]
        for entry in state(tmp_path)["audit"]["Main"]["declarations"]
    }
    assert "sorryAx" in rested["A.t"]
    assert "sorryAx" not in rested["B.t"]


def test_an_assumption_under_an_open_theorem_does_not_block_the_next_skeleton(tmp_path: Path):
    """Open skeletons accumulate without LaTeX between them -- and an approved
    axiom an *unfinished* proof leans on is not yet a claim owed to a reader.

    The appendix obligation it raises is not an `open` one, so the ratchet
    counted it and refused the next skeleton, putting a LaTeX round trip back
    in the middle of exactly the workflow this change exists to open up.
    """
    first = CLEAN.rstrip() + " -- axioms: sorryAx, Papers.Smith.main\n"
    second = (
        "import Mathlib\n\ntheorem HardySecond : True := by exact True.intro"
        " -- axioms: sorryAx\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", dict(APPROVAL), "ask"),
            call("save_lean", {"source": first}, "lean"),
            call("save_lean", {"path": "Second.lean", "source": second}, "lean"),
        ]),
        approvals=[True],
    )
    chat.send("Assume it, open one, then open another.")
    saves = results(tmp_path, "save_lean")
    assert all(item["ok"] for item in saves), saves[-1]["output"]
    # The disclosure is still owed -- it is simply not what stops the next
    # skeleton, and a report naming the theorem still has to settle it.
    assert [item.kind for item in chat.obligations() if item.kind != "open"]
    reported = chat._tool(
        "report_result", {"theorems": ["HardyTarget"], "summary": "As far as I got."}
    )
    assert not reported.ok
    assert "Papers.Smith.main" in reported.output


def test_an_approved_axiom_does_not_authorise_a_theorem_sharing_its_leaf(tmp_path: Path):
    """`request_assumption` records its own naming entry, so the registry holds
    `t` the moment an axiom called `t` is approved. The leaf fallback then let
    `theorem A.t` be stated with no result mapping of its own -- and because
    `completion` reads the registry by the same rule, the *axiom's* label could
    then satisfy the theorem's record and label obligations. A reader following
    that link lands on the appendix entry for an assumption, not on the theorem
    it is supposed to describe.
    """
    approval = dict(APPROVAL, formal_name="t", latex_name="asm:t")
    source = "import Mathlib\nnamespace A\ntheorem t : True := by exact True.intro\nend A\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", approval, "ask"),
            call("save_lean", {"source": source}, "lean"),
        ]),
        approvals=[True],
        registered=(),
    )
    chat.send("Approve an axiom, then state a theorem with the same last name.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"], refusal["output"]
    assert "lemma" in refusal["output"]


def test_the_banner_stays_ascii_for_a_unicode_theorem_name(tmp_path: Path):
    """Lean identifiers are Unicode and the interactive compiler is `pdflatex`
    by default, which fails on a character it has no mapping for.

    So naming an open theorem in the banner could break every later
    `save_latex` over a character the author never typed -- Hardy injecting the
    fault into the author's document. `escape_tex_text` handles TeX's own
    specials and leaves `α` alone, which is right for the Tectonic path and
    wrong here.
    """
    holed = (
        "import Mathlib\n\ntheorem α : True := by exact True.intro -- axioms: sorryAx\n"
    )
    chat = session(
        tmp_path,
        FakeChatRuntime([call("save_lean", {"source": holed}, "lean")]),
        registered=("α",),
    )
    chat.send("Save a skeleton with a Greek name.")
    banner = chat._stamp()
    assert banner.isascii(), banner
    # Still identified: a count alone would not say which claim is unproved.
    assert "03B1" in banner


def test_the_banner_stays_ascii_for_a_unicode_goal(tmp_path: Path):
    """The same fault, on the line that already printed free text from the user
    before this change: a goal saying `√2` broke the same compiler."""
    chat = session(tmp_path, FakeChatRuntime([]), registered=())
    chat.set_goal("√2 is irrational")
    banner = chat._stamp()
    assert banner.isascii(), banner
    assert "221A" in banner
