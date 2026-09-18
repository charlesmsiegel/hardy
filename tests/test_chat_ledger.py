"""An interactively saved theorem is recorded in the project ledger (#171).

The kernel's verdict lives in `session.json`; the ledger carries the record's
own claim about the same declaration. These tests drive a real session through
the fake Lean and read both back, so the two lanes are shown to be written by
one save and to stay independent: an item with an open obligation beside a
verified verdict is a disagreement a reader can see, not an inference.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_chat import FakeChatRuntime, call
from test_chat_audit import APPROVAL, ASSUMED, CLEAN, HOLED, session, state
from workspace_helpers import results

from hardy.app.web.panels import record as panels
from hardy.workflows.interactive.evidence import EVIDENCE_DIR, PRODUCER, ProjectOwners
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore

RESTATED = CLEAN.replace(": True :=", ": True ∧ True :=").replace("exact True.intro", "constructor <;> exact True.intro")


def _prove(snapshot, item: c.ProjectItem) -> c.Obligation:
    found = [o for o in snapshot.current(c.Obligation) if o.item == item.ref and o.kind is c.ObligationKind.PROVE]
    assert len(found) == 1, [o.id for o in found]
    return found[0]


def test_a_clean_saved_theorem_is_recorded_and_its_proof_obligation_resolved(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    outcome = results(tmp_path, "save_lean")[-1]
    assert outcome["ok"] and "project ledger: HardyTarget" in outcome["output"], outcome["output"]
    assert state(tmp_path)["audit"]["Main"]["status"] == "clean"
    snapshot = LedgerStore(tmp_path).read()
    item = snapshot.head("lean:HardyTarget")
    assert isinstance(item, c.ProjectItem) and item.kind is c.ProjectItemKind.THEOREM
    assert item.name == "HardyTarget" and item.statement == "theorem HardyTarget : True"
    assert item.origin is c.ProjectOrigin.GENERATED_LOCAL
    prove = _prove(snapshot, item)
    assert prove.status is c.ObligationStatus.RESOLVED and prove.resolution is not None
    # Accepted through the session's own readers, which re-read the durable
    # evidence and decision records rather than trusting the stored field.
    assert chat.owners.policy.is_accepted(snapshot, prove.resolution)
    reference = prove.resolution.evidence[0]
    assert reference.kind is c.EvidenceKind.FORMAL and reference.subject == item.ref
    assert reference.producer == PRODUCER and reference.artifact.locator == "HardyTarget"
    evidence = chat.owners.read_evidence(reference)
    assert evidence is not None and evidence.outcome == "kernel_proof" and evidence.used_assumptions == ()
    # The durable record is a journal beside the ledger, committed with it.
    assert (tmp_path / EVIDENCE_DIR / "00000000000000000001.json").is_file()


def test_the_results_page_shows_a_populated_record_lane_beside_the_kernel_lane(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    row = next(row for row in panels.results(tmp_path)["theorems"] if row["name"] == "HardyTarget")
    assert row["kernel"]["verdict"] == "verified"
    record = row["record"]
    assert record is not None and record["id"] == "lean:HardyTarget" and record["kind"] == "theorem"
    assert record["statement"] == "theorem HardyTarget : True"
    assert [(o["kind"], o["status"]) for o in record["obligations"]] == [("prove", "resolved")]
    assert record["obligations"][0]["evidence"][0]["producer"] == PRODUCER
    # The lane reports the item's own evidence field verbatim: nothing there,
    # because the evidence is on the resolution, and the lane says so rather
    # than borrowing it.
    assert record["evidence"] == []
    assert chat.owners.policy is not None


def test_a_proof_resting_on_a_hole_is_recorded_with_its_obligation_open(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    snapshot = LedgerStore(tmp_path).read()
    item = snapshot.head("lean:HardyTarget")
    prove = _prove(snapshot, item)
    assert prove.status is c.ObligationStatus.OPEN and prove.resolution is None
    assert prove.reason and "hole" in prove.reason
    assert not (tmp_path / EVIDENCE_DIR).exists()


def test_a_theorem_resting_on_an_approved_assumption_is_recorded_but_not_resolved(tmp_path: Path):
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("request_assumption", dict(APPROVAL), "ask"),
            call("save_lean", {"source": ASSUMED}, "lean"),
        ]),
        approvals=[True],
    )
    chat.send("Save it.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
    snapshot = LedgerStore(tmp_path).read()
    prove = _prove(snapshot, snapshot.head("lean:HardyTarget"))
    # The ledger scope admits no assumption, so a proof resting on one is
    # not a kernel proof the ledger can accept; the reason names it.
    assert prove.status is c.ObligationStatus.OPEN and "Papers.Smith.main" in (prove.reason or "")


def test_saving_the_same_theorem_again_moves_nothing(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([
        call("save_lean", {"source": CLEAN}, "lean"),
        call("save_lean", {"source": CLEAN}, "lean"),
    ]))
    chat.send("Save it twice.")
    first, second = results(tmp_path, "save_lean")[-2:]
    assert "resolved" in first["output"] and "project ledger: unchanged" in second["output"]
    snapshot = LedgerStore(tmp_path).read()
    assert snapshot.revision == 2                                             # item and open obligation; then closed
    assert len(snapshot.current(c.Obligation)) == 1


def test_a_restated_theorem_is_a_new_revision_with_its_own_obligation(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([
        call("save_lean", {"source": CLEAN}, "lean"),
        call("save_lean", {"source": RESTATED}, "lean"),
    ]))
    chat.send("Save, then restate.")
    snapshot = LedgerStore(tmp_path).read()
    head = snapshot.head("lean:HardyTarget")
    assert head.statement == "theorem HardyTarget : True ∧ True"
    versions = [r for r in snapshot.records if isinstance(r, c.ProjectItem) and r.id == head.id]
    assert [v.statement for v in versions] == ["theorem HardyTarget : True", "theorem HardyTarget : True ∧ True"]
    # The first statement's obligation stays resolved against that exact
    # revision; the restated head gets a fresh one, resolved on its own evidence.
    obligations = {o.id: o for o in snapshot.current(c.Obligation) if o.kind is c.ObligationKind.PROVE}
    assert len(obligations) == 2 and all(o.status is c.ObligationStatus.RESOLVED for o in obligations.values())
    assert {o.item for o in obligations.values()} == {versions[0].ref, versions[1].ref}
    assert chat.owners.policy.is_accepted(snapshot, _prove(snapshot, head).resolution)


def test_a_hole_introduced_after_a_proof_was_accepted_reopens_the_obligation(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([
        call("save_lean", {"source": CLEAN}, "lean"),
        call("save_lean", {"source": HOLED}, "lean"),
    ]))
    chat.send("Prove it, then break it.")
    snapshot = LedgerStore(tmp_path).read()
    prove = _prove(snapshot, snapshot.head("lean:HardyTarget"))
    assert prove.status is c.ObligationStatus.OPEN and prove.previous is not None
    earlier = snapshot.get(prove.previous)
    assert earlier.status is c.ObligationStatus.RESOLVED
    assert "reopened" in results(tmp_path, "save_lean")[-1]["output"]


def test_a_lemma_is_recorded_under_its_qualified_name(tmp_path: Path):
    source = "import Mathlib\n\nnamespace Foo\nlemma «first result» : True := by exact True.intro\nend Foo\n"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": source}, "lean")]))
    chat.send("Save it.")
    snapshot = LedgerStore(tmp_path).read()
    items = snapshot.current(c.ProjectItem)
    assert len(items) == 1 and items[0].kind is c.ProjectItemKind.LEMMA and items[0].name == "Foo.«first result»"
    assert items[0].id.startswith("lean:Foo.")                                # a valid stable id, readable
    assert _prove(snapshot, items[0]).status is c.ObligationStatus.RESOLVED


def test_acceptance_survives_reopening_and_fails_closed_on_a_tampered_record(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))
    chat.send("Save it.")
    snapshot = LedgerStore(tmp_path).read()
    resolution = _prove(snapshot, snapshot.head("lean:HardyTarget")).resolution
    # A fresh owner over the same bytes authenticates the same acceptance.
    reopened = ProjectOwners(tmp_path, audit=lambda space, modules: (({}), "unused"))
    assert reopened.policy.is_accepted(snapshot, resolution)
    # A record that no longer says what the reference pinned is refused, not repaired.
    path = tmp_path / EVIDENCE_DIR / "00000000000000000001.json"
    event = json.loads(path.read_text(encoding="utf-8"))
    event["records"][0]["value"]["axioms"] = ["sorryAx"]
    path.write_text(json.dumps(event), encoding="utf-8")
    assert not ProjectOwners(tmp_path, audit=reopened._audit).policy.is_accepted(snapshot, resolution)
    assert not chat.owners.policy.is_accepted(snapshot, resolution)


def test_a_ledger_that_refuses_the_write_does_not_refuse_the_save(tmp_path: Path, monkeypatch):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": CLEAN}, "lean")]))

    def refuse(*args, **kwargs):
        raise ValueError("ledger is read-only in this test")

    monkeypatch.setattr(LedgerStore, "append", refuse)
    chat.send("Save it.")
    outcome = results(tmp_path, "save_lean")[-1]
    assert outcome["ok"] and (tmp_path / "lean" / "Main.lean").exists()
    assert "project ledger: not recorded" in outcome["output"] and "read-only" in outcome["output"]
    assert LedgerStore(tmp_path).read().records == ()
