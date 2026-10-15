"""Review findings remain exact, actionable inputs to the supported Repair gateway."""
from __future__ import annotations

import pytest
from test_live_manuscript import referee_fixture
from test_live_project_workflows import live, target

from hardy.workflows import critique
from hardy.workflows.interactive.semantic_records import ArgumentRequest, ItemRequest
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation, VersionRef
from hardy.workflows.research import ResearchRequest


@pytest.mark.parametrize("selected", ["dependency", "foreign", "stale", "stale-argument"])
def test_critique_exact_finding_subject_requires_current_argument_membership(tmp_path, monkeypatch, selected):
    chat, _, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        original = ops.argument(ArgumentRequest(id="original", target=claim.ref, text="Supplied proof"))
        step = ops.item(ItemRequest(id="dependent-step", kind="proof_step", name="Dependency step", statement="A step"))
        snapshot = ops.store.read()
        ops.store.append((Relation(id="dependency", kind="depends_on", source=original.ref, target=step.ref),),
            expected_revision=snapshot.revision, validate=ops.owners.policy.validate)
        if selected == "foreign":
            step = ops.item(ItemRequest(id="foreign", kind="proof_step", name="Other proof", statement="Unrelated step"))
        elif selected == "stale":
            ops.revise(step.ref, statement="A changed step")
        elif selected == "stale-argument":
            snapshot = ops.store.read()
            ops.store.append((original.model_copy(update={"statement": "A revised argument"}),),
                expected_revision=snapshot.revision, validate=ops.owners.policy.validate)
        finding = critique.CritiqueFinding(kind="check_informal_step", reason="Invalid step", subject=step.ref)
        flow = critique.CritiqueWorkflow(ops.store, operations=critique.CritiqueOperations(adversarial=lambda review:
            critique.ReviewPass(subject=review.subject.ref, scope=review.scope.ref,
                revision=review.snapshot.revision, findings=(finding,))))
        request = critique.CritiqueRequest(subject=claim.ref, scope=scope.ref)
        if selected == "dependency":
            result = flow.run(request)
            assert result.obligations[0].item == step.ref
        else:
            before = ops.store.read()
            with pytest.raises(ValueError, match="subject"):
                flow.run(request)
            assert ops.store.read() == before
    finally:
        chat.close()


def test_explicit_critique_root_subject_reuses_legacy_observation(tmp_path, monkeypatch):
    chat, _, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        findings = [critique.CritiqueFinding(kind="check_informal_step", reason="Invalid inference")]
        flow = critique.CritiqueWorkflow(ops.store, operations=critique.CritiqueOperations(adversarial=lambda review:
            critique.ReviewPass(subject=review.subject.ref, scope=review.scope.ref,
                revision=review.snapshot.revision, findings=tuple(findings))))
        request = critique.CritiqueRequest(subject=claim.ref, scope=scope.ref)
        old = flow.run(request)
        before = ops.store.read()
        findings[:] = [findings[0].model_copy(update={"subject": claim.ref})]
        current = flow.run(request)
        assert current.obligations == old.obligations
        assert ops.store.read() == before
    finally:
        chat.close()


@pytest.mark.parametrize("subject_key", ["original", "step"])
def test_referee_discovered_argument_defect_enters_repair(tmp_path, monkeypatch, subject_key):
    flow, request, intake, _, accepted = referee_fixture(tmp_path)
    assert flow.run(request)["receipt"]["status"] == "completed"
    subject = VersionRef.model_validate(accepted["bindings"][subject_key])
    discovered = [work for work in intake.store.read().current(Obligation)
        if work.id.startswith("critique:") and "Cancelling zero" in work.reason and work.item == subject]
    assert len(discovered) == 1, "Referee must attach actionable defects to their exact argument or step"
    defect, = discovered
    original = VersionRef.model_validate(accepted["bindings"]["original"])
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        result = chat.project_repair({"id": "repair-discovered", "obligation": defect.ref.model_dump(),
            "original_argument": original.model_dump()}, max_attempts=1)
        assert result["status"] in {"completed", "pending"}, result
        assert any(stage == "repair.propose" for stage, _, _ in models.calls)
        assert result["original_defect"] == defect.ref.model_dump()
        assert ops.store.read().head(original.id).ref == original
        assert ops.store.read().head(defect.id) == defect
        replacement = VersionRef.model_validate(result["repaired_argument"])
        assert any(edge.kind == "refines" and edge.source == replacement and edge.target == original
            for edge in ops.store.read().current(Relation))
    finally:
        chat.close()


def repair_request(ops, claim, scope):
    original = ops.argument(ArgumentRequest(id="original", target=claim.ref, text="An invalid inference"))
    defect = ops.obligation(original.ref, kind="check_informal_step", scope=scope.ref, reason="Invalid inference")
    return {"id": "repair", "obligation": defect.ref.model_dump(), "original_argument": original.ref.model_dump()}


def test_repair_reuses_existing_formal_proposal_on_first_attempt_and_retry(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
        request = repair_request(ops, claim, scope)
        models.bad_proof = True
        first = chat.project_repair(request, max_attempts=1)
        assert first["status"] == "pending", first
        assert "proof.propose" in [stage for stage, _, _ in models.calls]
        models.bad_proof = False
        retried = chat.project_repair(request, max_attempts=1)
        assert retried["status"] == "completed", retried
        proposals = [item for item in ops.store.read().current(ProjectItem)
            if ("role", "proposed_formalization") in item.semantics]
        assert proposals == [formal]
        assert [attempt["status"] for attempt in chat.project_workflow_status("repair")["attempts"]] == ["pending", "completed"]
    finally:
        chat.close()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_repair_blocked_preparation_is_pending_on_first_attempt_and_retry(tmp_path, monkeypatch, ambiguous):
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        ops.formalization(claim.ref, id="translation", formal="lemma different_fact : False", scope=scope.ref)
        if ambiguous:
            ops.formalization(claim.ref, id="another", formal="lemma target_fact : True", scope=scope.ref)
        request = repair_request(ops, claim, scope)
        for _ in range(2):
            result = chat.project_repair(request, max_attempts=1)
            assert result["status"] == "pending", result
            assert ("Multiple exact formal proposals" if ambiguous else "explicit revision required") in result["detail"]
        assert not any(stage == "proof.propose" for stage, _, _ in models.calls)
        assert not list((tmp_path / "lean").rglob("*.lean"))
        assert any(work.kind == "repair" and work.status == "open" for work in ops.store.read().current(Obligation))
    finally:
        chat.close()


@pytest.mark.parametrize("existing_candidate", [False, True])
@pytest.mark.parametrize("verdict", ["disputed", "unresolved"])
def test_disputed_correspondence_blocks_repair_before_proof_or_save(tmp_path, monkeypatch, existing_candidate, verdict):
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        if existing_candidate:
            models.bad_proof = True
            assert chat.project_research(ResearchRequest(id="prepare", target=claim.ref, scope=scope.ref), max_attempts=4)["status"] == "pending"
            formal, = [item for item in ops.store.read().current(ProjectItem)
                if ("role", "proposed_formalization") in item.semantics]
        else:
            formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
        dispute = chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict=verdict,
            reviewer="human", conditions="Read the exact pair", reason="Wrong translation")
        models.bad_proof = False
        request = repair_request(ops, claim, scope)
        models.calls.clear()
        before = {path: path.read_bytes() for path in (tmp_path / "lean").rglob("*.lean")}
        for _ in range(2):
            result = chat.project_repair(request, max_attempts=1)
            assert result["status"] == "pending", result
            assert not any(stage == "proof.propose" for stage, _, _ in models.calls)
            assert {path: path.read_bytes() for path in (tmp_path / "lean").rglob("*.lean")} == before
            assert "correspondence review" in result["detail"]
        assert chat.owners.read_evidence(chat.owners.review_reference(dispute)) is None
        assert any(work.kind == "repair" and work.status == "open" for work in ops.store.read().current(Obligation))
    finally:
        chat.close()


def test_correspondence_withdrawal_during_repair_proof_blocks_save(tmp_path, monkeypatch):
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
        request = repair_request(ops, claim, scope)
        prove = chat.live.capabilities.proof
        def withdraw_after_verification(task, submission):
            result = prove(task, submission)
            chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="disputed",
                reviewer="human", conditions="Review during proof", reason="Translation concern")
            return result
        monkeypatch.setattr(chat.live.capabilities, "proof", withdraw_after_verification)
        result = chat.project_repair(request, max_attempts=1)
        assert result["status"] == "pending", result
        assert any(stage == "proof.propose" for stage, _, _ in models.calls)
        assert not list((tmp_path / "lean").rglob("*.lean"))
        assert "correspondence review" in result["report"]["detail"]
    finally:
        chat.close()
