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
