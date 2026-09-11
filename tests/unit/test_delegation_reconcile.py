"""Authoritative admission reconciles onto the current head, re-verifies there, and journals every phase.

Criteria 22, 28, 30: unrelated concurrent change sets transplant; overlapping
edits conflict with both proposals kept; a head that moves during admission
restarts the reconciliation instead of retrying a stale transaction;
verification happens fresh on the current head before any commit; a new
verified lemma gets its authoritative identity before evidence is minted;
files landed without a ledger commit is an incomplete admission, never a
success.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

import pytest
from delegation_helpers import ScriptedOwners, seed_project

from hardy.formal.contracts import Request
from hardy.formal.lean import LeanTools
from hardy.formal.workspace import LeanWorkspace
from hardy.workflows.delegation.admission import (
    AdmissionAttempt,
    AdmissionPhase,
    AuthoritativeAdmission,
    reconcile,
    route_finding,
)
from hardy.workflows.delegation.findings import Finding
from hardy.workflows.delegation.store import DelegationStore
from hardy.workflows.delegation.workspace import WorkspaceOverlay
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore

FAKE_LEAN = (sys.executable, str(Path(__file__).resolve().parents[1] / "fake_lean.py"))
MAIN = "import Mathlib\n\ntheorem base_fact : True := by exact True.intro\n"
A = "import Mathlib\n\ntheorem a_fact : True := by exact True.intro\n"
B = "import Mathlib\n\ntheorem b_fact : True := by exact True.intro\n"


def _base(tmp_path: Path) -> LeanWorkspace:
    lean = LeanTools(Request("example : True", "workspace", ("Mathlib",)), FAKE_LEAN)
    root, build = tmp_path / "lean", tmp_path / ".build" / "lean"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Main.lean").write_text(MAIN, encoding="utf-8")

    def compile(module, source_root, build_root, source_file):
        result = lean.compile_module(source_root, build_root, source_file, lean_path=str(build_root))
        return result.ok, result.output

    workspace = LeanWorkspace(root, build, compile, environment="test-env")
    assert workspace.build_modules(["Main"]) is None
    return workspace


def _overlay(base, tmp_path, name, revision):
    return WorkspaceOverlay.snapshot(base, delegation_id=name, base_revision=revision,
                                     root=tmp_path / "delegations" / name / "overlay")


def test_unrelated_change_sets_transplant_and_overlapping_edits_conflict(tmp_path):
    base = _base(tmp_path)
    a = _overlay(base, tmp_path, "a", 100)
    b = _overlay(base, tmp_path, "b", 100)
    assert a.save(PurePosixPath("A.lean"), A) is None
    assert b.save(PurePosixPath("B.lean"), B) is None
    # A lands first: the head moves on.
    (tmp_path / "lean" / "A.lean").write_text(A, encoding="utf-8")
    plan_b, conflicts = reconcile(b.change_set(), base, head_revision=101)
    assert conflicts == () and [f.path for f in plan_b.files] == ["B.lean"]
    assert plan_b.base_project_revision == 101                                  # transplanted onto the head
    # Same-file edits: disjoint hunks merge, overlapping ones conflict with both kept.
    disjoint = _overlay(base, tmp_path, "d", 101)
    assert disjoint.save(PurePosixPath("Main.lean"), MAIN + "\ntheorem extra_d : True := by exact True.intro\n") is None
    (tmp_path / "lean" / "Main.lean").write_text("import Mathlib\n-- head comment\n\ntheorem base_fact : True := by exact True.intro\n", encoding="utf-8")
    merged, conflicts = reconcile(disjoint.change_set(), base, head_revision=102)
    assert conflicts == () and "-- head comment" in merged.files[0].content and "extra_d" in merged.files[0].content
    clashing = _overlay(base, tmp_path, "c", 101)
    assert clashing.save(PurePosixPath("Main.lean"), MAIN.replace("base_fact", "renamed_fact")) is None
    (tmp_path / "lean" / "Main.lean").write_text(MAIN.replace("base_fact", "other_fact"), encoding="utf-8")
    _, conflicts = reconcile(clashing.change_set(), base, head_revision=103)
    assert [conflict.path for conflict in conflicts] == ["Main.lean"]
    assert conflicts[0].proposed and conflicts[0].head and conflicts[0].proposed != conflicts[0].head


def _admission(tmp_path, base, owners, **extra):
    return AuthoritativeAdmission(LedgerStore(tmp_path), base, DelegationStore(tmp_path), verify=owners.verify,
                                  policy=owners.policy, decide=owners.decide, **extra)


def _candidate(tmp_path, overlay, name, statement="A new lemma from the worker"):
    ledger = LedgerStore(tmp_path)
    snapshot = ledger.read()
    finding = Finding(id=f"{name}:finding:0", source_delegation=name, kind="candidate_lemma", summary="new lemma",
                      payload=statement, related_refs=(snapshot.head("L17").ref,), sequence=0)
    DelegationStore(tmp_path).append(name, "delegation.created", {
        "spec": {"objective": "prove", "project_refs": [snapshot.head("L17").ref.model_dump()],
                 "scope": snapshot.head("scope").ref.model_dump(), "lease": {"official_checks": 1},
                 "concurrency": {"slots": 1}, "created_by": "human"}, "parent_id": None, "created_at": "t"})
    return route_finding(finding, snapshot, scope=snapshot.head("scope").ref, delegation_id=name,
                         change_set=overlay.change_set().id).model_copy(update={"target": "authoritative"}), overlay.change_set()


def test_new_verified_lemma_is_admitted_with_fresh_verification_and_exact_subject_evidence(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "created", outcome.reasons
    snapshot = LedgerStore(tmp_path).read()
    lemma = snapshot.head(outcome.authoritative_refs[0].id)
    assert lemma.kind is c.ProjectItemKind.LEMMA and lemma.statement == "A new lemma from the worker"
    prove = next(o for o in snapshot.current(c.Obligation) if o.item == lemma.ref and o.kind is c.ObligationKind.PROVE)
    assert prove.status is c.ObligationStatus.RESOLVED and owners.policy.is_accepted(snapshot, prove.resolution)
    assert prove.resolution.evidence[0].subject == lemma.ref                    # identity before evidence
    assert owners.verified == [lemma.id]
    assert (tmp_path / "lean" / "A.lean").read_text(encoding="utf-8") == A     # files committed on the head
    phases = [AdmissionAttempt.model_validate(e.payload["attempt"]).phase for e in DelegationStore(tmp_path).events()
              if e.kind == "admission.attempt"]
    assert phases[-1] is AdmissionPhase.COMPLETED and AdmissionPhase.LEDGER_COMMITTED in phases
    assert phases.index(AdmissionPhase.VERIFICATION_COMPLETE) < phases.index(AdmissionPhase.FILES_COMMITTED)


def test_a_head_that_moves_mid_admission_is_reconciled_again_not_retried_stale(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    moved = {"done": False}
    original_verify = owners.verify

    def verify_and_move_the_head(workspace, cand, obligation):
        if not moved["done"]:
            moved["done"] = True
            ledger = LedgerStore(tmp_path)
            snapshot = ledger.read()
            ledger.append((c.ProjectItem(id="late", kind="research_note", name="late", origin="human_authored",
                                         statement="the main session moved on"),), expected_revision=snapshot.revision)
        return original_verify(workspace, cand, obligation)

    owners.verify = verify_and_move_the_head
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "created", outcome.reasons
    attempts = [AdmissionAttempt.model_validate(e.payload["attempt"]) for e in DelegationStore(tmp_path).events()
                if e.kind == "admission.attempt"]
    assert [a.phase for a in attempts].count(AdmissionPhase.RECONCILED) == 2       # re-prepared from the new head
    # The admission itself adds two transactions (records, then the accepted resolution).
    assert attempts[-1].head_revision == LedgerStore(tmp_path).read().revision - 2


def test_verification_failure_after_files_are_prepared_commits_nothing(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    owners.verify = lambda workspace, cand, obligation: (None, "kernel refused it on the current head")
    before = LedgerStore(tmp_path).read().revision
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "rejected" and "refused" in " ".join(outcome.reasons)
    assert not (tmp_path / "lean" / "A.lean").exists() and LedgerStore(tmp_path).read().revision == before
    phases = [AdmissionAttempt.model_validate(e.payload["attempt"]).phase for e in DelegationStore(tmp_path).events()
              if e.kind == "admission.attempt"]
    assert phases[-1] is AdmissionPhase.FAILED and AdmissionPhase.FILES_COMMITTED not in phases


def test_conflicting_change_set_is_reported_with_both_proposals_kept(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("Main.lean"), MAIN.replace("base_fact", "renamed_fact")) is None
    (tmp_path / "lean" / "Main.lean").write_text(MAIN.replace("base_fact", "other_fact"), encoding="utf-8")
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "conflicted" and any("Main.lean" in r for r in outcome.reasons)
    assert any(a.endswith("conflicts.json") for a in outcome.artifacts)
    conflicts = json.loads((tmp_path / "delegations" / "w" / "conflicts.json").read_text(encoding="utf-8"))
    assert conflicts[0]["proposed"] != conflicts[0]["head"]
    assert (tmp_path / "lean" / "Main.lean").read_text(encoding="utf-8") == MAIN.replace("base_fact", "other_fact")


def test_a_crash_after_files_committed_is_an_incomplete_admission_not_a_success(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    admission = _admission(tmp_path, base, owners, crash_after=AdmissionPhase.FILES_COMMITTED)
    with pytest.raises(RuntimeError, match="simulated crash"):
        admission.admit(candidate, change_set)
    assert (tmp_path / "lean" / "A.lean").exists()
    assert "w:0:lemma" not in {r.id for r in LedgerStore(tmp_path).read().records}
    incomplete = _admission(tmp_path, base, owners).recover()
    assert [a.phase for a in incomplete] == [AdmissionPhase.FILES_COMMITTED]
    assert incomplete[0].candidate_id == candidate.id
    events = DelegationStore(tmp_path).events()
    assert any(e.kind == "attention.derived" and e.payload["item"]["sticky"] for e in events)


def test_exact_duplicate_of_an_authoritative_item_is_reused_not_recreated(tmp_path):
    heads = seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    candidate, change_set = _candidate(tmp_path, overlay, "w", statement="The special fiber is reduced")
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "reused_existing" and outcome.authoritative_refs == (heads["L12"].ref,)
    assert outcome.identity_map == (("w:0:lemma", "L12"),)


def test_admitting_the_same_candidate_twice_reuses_its_identity_and_commits_nothing_new(tmp_path):
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    first = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert first.action == "created"
    revision = LedgerStore(tmp_path).read().revision
    second = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert second.action == "reused_existing" and second.authoritative_refs == first.authoritative_refs
    assert LedgerStore(tmp_path).read().revision == revision and owners.verified == [first.authoritative_refs[0].id]
    assert "admission.incomplete" not in [e.kind for e in DelegationStore(tmp_path).events()]


def test_a_crash_while_files_are_being_written_is_an_incomplete_admission(tmp_path):
    """The mutation is journaled before the first authoritative write, so a half-written tree is never silent."""
    seed_project(tmp_path)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", LedgerStore(tmp_path).read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    with pytest.raises(RuntimeError, match="simulated crash"):
        _admission(tmp_path, base, owners, crash_after=AdmissionPhase.FILES_COMMITTING).admit(candidate, change_set)
    phases = [AdmissionAttempt.model_validate(e.payload["attempt"]).phase for e in DelegationStore(tmp_path).events()
              if e.kind == "admission.attempt"]
    assert phases[-1] is AdmissionPhase.FILES_COMMITTING
    assert phases.index(AdmissionPhase.VERIFICATION_COMPLETE) < phases.index(AdmissionPhase.FILES_COMMITTING)
    incomplete = _admission(tmp_path, base, owners).recover()
    assert [a.phase for a in incomplete] == [AdmissionPhase.FILES_COMMITTING]
    from hardy.workflows.delegation.attention import AttentionInbox
    pending = AttentionInbox(DelegationStore(tmp_path)).pending("human")
    assert pending and pending[0].sticky and pending[0].category == "admission"


def test_a_proof_for_an_existing_unverified_statement_resolves_its_obligation_not_just_reuses_it(tmp_path):
    from hardy.workflows.explore import ExploreWorkflow

    seed_project(tmp_path)
    ledger = LedgerStore(tmp_path)
    existing = ExploreWorkflow(ledger).record_item(id="LX", kind=c.ProjectItemKind.LEMMA, name="Lemma X",
                                                   statement="A new lemma from the worker")
    snapshot = ledger.read()
    ledger.append((c.Obligation(id="prove-LX", item=existing.ref, kind=c.ObligationKind.PROVE,
                                scope=snapshot.head("scope"), context=existing.context),),
                  expected_revision=snapshot.revision)
    base = _base(tmp_path)
    owners = ScriptedOwners(tmp_path / "capabilities")
    overlay = _overlay(base, tmp_path, "w", ledger.read().revision)
    assert overlay.save(PurePosixPath("A.lean"), A) is None
    candidate, change_set = _candidate(tmp_path, overlay, "w")
    outcome = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert outcome.action == "resolved_obligation" and outcome.authoritative_refs == (existing.ref,), outcome.reasons
    after = ledger.read()
    assert not any(record.id.endswith(":lemma") for record in after.records)      # no second identity
    prove = after.head("prove-LX")
    assert prove.status is c.ObligationStatus.RESOLVED and owners.policy.is_accepted(after, prove.resolution)
    assert owners.verified == ["LX"] and (tmp_path / "lean" / "A.lean").read_text(encoding="utf-8") == A
    again = _admission(tmp_path, base, owners).admit(candidate, change_set)
    assert again.action == "reused_existing" and again.authoritative_refs == (existing.ref,)
