"""Independent sketch lemmas share one budget and cannot certify their parent."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_iterative_strategy import _result
from test_strategy_contracts import _task

from hardy.formal.contracts import (
    DeclaredAssumption,
    FormalizationContext,
    SemanticEntry,
    SemanticRef,
    freeze_claim,
)
from hardy.formal.lean import LeanTools
from hardy.formal.verifier import FinalVerifier
from hardy.foundation.process import ProcessResult
from hardy.workflows.contracts import ProofSubmission, RunLimits
from hardy.workflows.ledger.contracts import Obligation, ObligationStatus, Scope
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.storage import RunStore
from hardy.workflows.strategies.contracts import run_strategy
from hardy.workflows.strategies.sketch import SketchHole, SketchPlan, SketchStrategy


def _plan():
    return SketchPlan(
        holes=(SketchHole(name="left", proposition="1 = 1"),
               SketchHole(name="right", proposition="2 = 2")),
        conclusion="exact right", informal_proof="Use two independent reflexive identities.",
    )


def _setup(tmp_path, *, accepted=lambda task, body: True, checks=5,
           closers=("rfl",), plan=None, elapsed=lambda: 0, cancelled=lambda: None):
    task = _task(limits=RunLimits(official_checks=checks))
    store = RunStore.create(tmp_path / "runs", "sketch", now=datetime.now(UTC), run_id=uuid4())
    ledger = LedgerStore(tmp_path / "project")
    scope = Scope(id="scope")
    ledger.append((scope,), expected_revision=0)
    verified, proposed = [], []

    def record_hole(item, obligation):
        ledger.append((item, obligation), expected_revision=ledger.read().revision)

    def verify(child, submission, check_store):
        verified.append((child, submission, check_store.path))
        return _result(child, submission.proof_body,
                       accepted=accepted(child, submission.proof_body))

    def propose_hole(child, prompt):
        proposed.append((child, prompt))
        return ProofSubmission(proof_body="by decide", informal_proof="Decidability.")

    strategy = SketchStrategy(
        propose_sketch=lambda _: plan or _plan(), propose_hole=propose_hole,
        verify=verify, store=store, scope=scope, record_hole=record_hole,
        active_elapsed=elapsed, check_cancelled=cancelled, monotonic=lambda: 0,
        closers=closers,
    )
    return task, strategy, store, ledger, verified, proposed


def test_closers_prove_current_holes_then_exact_parent_and_persist_open_obligations(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path)

    outcome = run_strategy(strategy, task)

    assert outcome.status == "submitted"
    assert outcome.evidence.claim_sha256 == task.claim.content_hash
    assert [t.claim.proposal.proposition for t, _, _ in verified] == ["1 = 1", "2 = 2", "2 = 2"]
    assert verified[-1][0] == task
    assert not proposed
    assert "have left : 1 = 1 :=\n    by rfl" in outcome.submission.proof_body
    assert "have right : 2 = 2 :=\n    by rfl" in outcome.submission.proof_body
    assert not LeanTools.has_holes(outcome.submission.proof_body)
    assert len({path for _, _, path in verified}) == 3
    restarted = LedgerStore(tmp_path / "project").read()
    obligations = restarted.current(Obligation)
    assert len(obligations) == 2
    assert all(o.status == ObligationStatus.OPEN and o.resolution is None for o in obligations)
    assert (store.path / "sketch" / "skeleton.lean").read_text().count("sorry") == 2
    assert json.loads((store.path / "sketch" / "outcome.json").read_text())["remaining_holes"] == []


def test_unsolved_closer_uses_iterative_search_with_an_independent_hole_task(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(
        tmp_path, checks=6, accepted=lambda t, body: body != "by rfl")

    outcome = run_strategy(strategy, task)

    assert outcome.status == "submitted"
    assert len(proposed) == 2
    assert proposed[0][0].claim != proposed[1][0].claim
    assert all(t.declared_assumptions == task.declared_assumptions for t, _ in proposed)
    assert "left" not in proposed[1][0].claim.proposal.binders
    assert [s.proof_body for _, s, _ in verified[:-1]] == ["by rfl", "by decide"] * 2


def test_one_shared_check_ceiling_preserves_second_hole_when_first_spends_budget(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(
        tmp_path, checks=3, accepted=lambda t, body: False)

    outcome = run_strategy(strategy, task)

    assert outcome.status == "exhausted"
    assert outcome.evidence is None
    assert len(verified) == 2  # one check reserved for final, never spent on a holed parent
    assert len(proposed) == 1
    assert all(t.claim != task.claim for t, _, _ in verified)
    saved = json.loads((store.path / "sketch" / "outcome.json").read_text())
    assert saved["remaining_holes"] == ["left", "right"]
    assert "left" in outcome.detail and "right" in outcome.detail


def test_parent_rejection_is_partial_even_when_every_hole_is_verified(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(
        tmp_path, accepted=lambda t, body: not body.startswith("by\n  have"))

    outcome = run_strategy(strategy, task)

    assert outcome.status == "partial"
    assert outcome.evidence is None
    assert "FinalVerifier" in outcome.detail
    assert verified[-1][0] == task


def test_hidden_conclusion_hole_is_never_submitted_to_verifier(tmp_path):
    plan = _plan().model_copy(update={"conclusion": "sorry"})
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path, plan=plan)

    outcome = run_strategy(strategy, task)

    assert outcome.status == "partial"
    assert outcome.evidence is None
    assert "conclusion" in outcome.detail
    assert all(t.claim != task.claim for t, _, _ in verified)


def test_active_ceiling_is_checked_after_sketch_model_returns(tmp_path):
    ticks = iter((0, 1800))
    task, strategy, store, ledger, verified, proposed = _setup(
        tmp_path, elapsed=lambda: next(ticks, 1800))

    outcome = run_strategy(strategy, task)

    assert outcome.status == "exhausted"
    assert not verified and not proposed
    assert "left" in outcome.detail and "right" in outcome.detail


def test_cancellation_during_hole_work_leaves_explicit_partial_artifact(tmp_path):
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        if calls >= 5:
            raise KeyboardInterrupt

    task, strategy, store, ledger, verified, proposed = _setup(tmp_path, cancelled=cancelled)

    outcome = run_strategy(strategy, task)

    assert outcome.status == "cancelled"
    assert outcome.evidence is None
    saved = json.loads((store.path / "sketch" / "outcome.json").read_text())
    assert saved["remaining_holes"]
    assert len(verified) <= 1


def test_duplicate_hole_names_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        SketchPlan(holes=(SketchHole(name="h", proposition="True"),) * 2,
                   conclusion="trivial")


def test_contextual_parent_is_preserved_without_impersonating_its_subject_in_hole_claims(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path)
    subject = SemanticRef(id="parent", digest="a" * 64)
    context = FormalizationContext(
        subject=subject, context=SemanticRef(id="context", digest="b" * 64),
        scope=SemanticRef(id="scope", digest="c" * 64), required_binders=(),
        entries=(SemanticEntry(ref=subject, role="subject",
                               text=json.dumps({"statement": task.claim.original_text})),),
    )
    claim = freeze_claim(task.claim.original_text, task.claim.proposal,
                         task.claim.environment, task.claim.approved_at, semantic_context=context)
    task = task.model_copy(update={"claim": claim})

    outcome = run_strategy(strategy, task)

    assert outcome.status == "submitted"
    assert verified[-1][0].claim == claim
    assert all(child.claim.semantic_context is None for child, _, _ in verified[:-1])
    assert all(claim.content_hash in child.claim.original_text for child, _, _ in verified[:-1])


def test_accepted_but_holed_child_remains_an_explicit_open_hole(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path, closers=("sorry",))

    outcome = run_strategy(strategy, task)

    assert outcome.status == "partial"
    assert outcome.evidence is None
    assert all(child.claim != task.claim for child, _, _ in verified)
    saved = json.loads((store.path / "sketch" / "outcome.json").read_text())
    assert saved["remaining_holes"] == ["left", "right"]


@pytest.mark.parametrize("parent_axiom", ["background", "sorryAx"])
def test_real_final_verifier_controls_parent_evidence_and_exact_assumption_scope(tmp_path, parent_axiom):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path)
    allowed = (DeclaredAssumption(name="background", statement="True",
                                  source="Fixture reference", justification="Explicitly permitted."),)
    task = task.model_copy(update={"declared_assumptions": allowed})
    sources = []

    def runner(spec):
        source = Path(spec.argv[-1]).read_text(encoding="utf-8")
        sources.append(source)
        name = source.split("#print axioms ")[-1].strip()
        axiom = parent_axiom if name == task.claim.proposal.theorem_name else "background"
        return ProcessResult(argv=spec.argv, cwd=spec.cwd, returncode=0, stderr="",
                             stdout=json.dumps({"severity": "information", "data":
                                                f"{name} depends on axioms: [{axiom}]"}),
                             timed_out=False, output_overflow=False, duration_ms=1)

    verifier = FinalVerifier(lake=tmp_path / "lake.exe", lean_project=tmp_path,
                             environment=task.claim.environment, limits=task.limits, runner=runner)
    strategy._verify = lambda child, submission, check_store: verifier.verify(
        child.claim, submission.proof_body, check_store, allowed=child.declared_assumptions)

    outcome = run_strategy(strategy, task)

    assert len(sources) == 3
    assert all(source.count("axiom background : True") == 1 for source in sources)
    assert all("axiom left" not in source and "axiom right" not in source for source in sources)
    if parent_axiom == "background":
        assert outcome.status == "submitted"
        assert outcome.evidence.axioms == ("background",)
        assert (store.path / "lean/Main.lean").read_text(encoding="utf-8") == sources[-1]
    else:
        assert outcome.status == "partial"
        assert outcome.evidence is None
        assert not (store.path / "lean/Main.lean").exists()


def test_proof_deadline_is_shared_across_holes_and_final_verification(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path)
    elapsed = 0
    verify = strategy._verify

    def spend_time(child, submission, check_store):
        nonlocal elapsed
        result = verify(child, submission, check_store)
        elapsed += 700
        return result

    strategy._monotonic = lambda: elapsed
    strategy._verify = spend_time

    outcome = run_strategy(strategy, task)

    assert outcome.status == "exhausted"
    assert outcome.evidence is None
    assert len(verified) == 2
    assert all(child.claim != task.claim for child, _, _ in verified)
    assert "final verification pending" in outcome.detail


def test_foreign_child_evidence_cannot_be_assembled_into_parent(tmp_path):
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path)
    strategy._verify = lambda child, submission, check_store: _result(
        task, submission.proof_body, accepted=True)

    with pytest.raises(ValueError, match="different task claim"):
        run_strategy(strategy, task)


def test_parent_and_hole_binders_are_preserved_in_each_independent_task(tmp_path):
    plan = SketchPlan(holes=(SketchHole(name="reflexivity", binders="(m : Nat)",
                                       proposition="m = m"),), conclusion="exact reflexivity n")
    task, strategy, store, ledger, verified, proposed = _setup(tmp_path, plan=plan)
    proposal = task.claim.proposal.model_copy(update={"binders": "(n : Nat)", "proposition": "n = n"})
    task = task.model_copy(update={"claim": freeze_claim("n equals n", proposal,
        task.claim.environment, task.claim.approved_at)})

    outcome = run_strategy(strategy, task)

    assert outcome.status == "submitted"
    assert verified[0][0].claim.proposal.binders == "(n : Nat) (m : Nat)"
    assert "have reflexivity (m : Nat) : m = m :=" in outcome.submission.proof_body
    assert verified[-1][0] == task
