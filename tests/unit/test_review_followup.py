"""Mixed proof histories and independent-session withdrawals retain exact authority."""
from __future__ import annotations

import pytest
from test_live_project_workflows import live, target
from test_native_establishment import publication
from test_review_repair import repair_request

from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.live_records import FrozenFormalEvidence
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import JudgmentRequest
from hardy.workflows.ledger.contracts import Obligation
from hardy.workflows.research import ResearchRequest


def test_current_native_proof_remains_usable_after_research_proof_becomes_stale(tmp_path, monkeypatch):
    chat, _, ops = live(tmp_path, monkeypatch)
    try:
        claim = chat.project_target("Target", "True", formal="lemma target_fact : True")
        scope = ops.store.read().head("Target:scope")
        formal = ops.store.read().head("Target:formal")
        result = chat.project_research(ResearchRequest(id="research", target=claim.ref, scope=scope.ref), max_attempts=6)
        assert result["status"] == "completed", result
        saved = ops.store.read().head("lean:target_fact")
        chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="approved",
            reviewer="operator", conditions="Exact pair", reason="Same statement")
        chat.owners.bind_saved(claim.ref, formal.ref, saved.ref, scope=scope.ref)
        assert publication(chat, claim).ready
        stale = ops.store.read().head("research:target")
        source, = (tmp_path / "lean" / "Live").rglob("*.lean")
        assert chat.save_authored(str(source.relative_to(tmp_path)), source.read_text() + "\n-- checked comment revision\n").ok
        binding = chat.owners.bind_saved(claim.ref, formal.ref, saved.ref, scope=scope.ref)
        reference = chat.owners.establishment_reference(binding)

        def check_current_and_historical(session, store):
            snapshot = store.read()
            assert snapshot.head(stale.id) == stale
            assert not session.owners.policy.is_accepted(snapshot, stale.resolution)
            assert session.owners.read_evidence(reference) is not None
            assert session.owners.policy.premise_allowed(snapshot, claim.ref, scope=scope, context=claim.context)
            assert session.owners.policy.trust_boundary(snapshot, claim.ref, scope=scope, context=claim.context) == ()
            plan = publication(session, claim)
            assert claim.ref not in plan.unestablished
            assert any(a.item == claim.ref and a.authenticated and not a.used for a in plan.assumption_audits)
            # A separate stale Research receipt remains actionable history.
            assert stale.ref in {work.ref for work in plan.obligations}
            assert session.project_inspect(claim.id)["statement"]["status"] == "supported_by_reviewed_formal_proof"

        check_current_and_historical(chat, ops.store)
        chat.close()
        chat, _, ops = live(tmp_path, monkeypatch)
        check_current_and_historical(chat, ops.store)
        # Reuse never rehabilitates either stale source evidence or a withdrawn current proof.
        chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="disputed",
            reviewer="operator", conditions="Second reading", reason="Withdraw correspondence")
        assert not chat.owners.policy.premise_allowed(ops.store.read(), claim.ref, scope=scope, context=claim.context)
    finally:
        chat.close()


@pytest.mark.parametrize("workflow", ["research", "repair"])
@pytest.mark.parametrize("review_surface", ["native", "semantic"])
def test_second_owner_withdrawal_during_final_audit_prevents_save(tmp_path, monkeypatch, workflow, review_surface):
    chat, models, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
        request = repair_request(ops, claim, scope) if workflow == "repair" else ResearchRequest(
            id="research", target=claim.ref, scope=scope.ref)
        other = ProjectOwners.reading(tmp_path)
        original_audit = chat.formal._audit_tree
        withdrawals = []

        def during_audit(*args, **kwargs):
            result = original_audit(*args, **kwargs)
            if review_surface == "native":
                review = other.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="disputed",
                    reviewer="operator", conditions="Other session during final audit", reason="Withdraw exact pair")
            else:
                review = SemanticOperations(tmp_path, owners=other).judge(JudgmentRequest(
                    subject=claim.ref, scope=scope.ref, purpose="correspondence", relevant=(formal.ref,),
                    verdict="disputed", actor="human", reviewer="operator",
                    conditions="Other session during final audit", reason="Withdraw exact pair"))
            withdrawals.append(review)
            return result

        monkeypatch.setattr(chat.formal, "_audit_tree", during_audit)
        before_sources = chat.lean_workspace.sources()
        before_signatures = chat.lean_workspace.current_signatures()
        result = (chat.project_repair(request, max_attempts=1) if workflow == "repair"
            else chat.project_research(request, max_attempts=6))
        assert withdrawals, result
        assert any(stage == "proof.propose" for stage, _, _ in models.calls)
        assert result["status"] == "pending", result
        assert chat.lean_workspace.sources() == before_sources
        assert chat.lean_workspace.current_signatures() == before_signatures
        assert not list((tmp_path / "lean").rglob("*.lean"))
        assert ops.store.read().head_or_none("lean:target_fact") is None
        assert not any(value.outcome == "kernel_proof" for value in chat.owners.journal.read().of(FrozenFormalEvidence))
        latest, _ = chat.owners.judgments.correspondence_review(claim.ref, formal.ref, scope.ref)
        assert latest == withdrawals[-1]
        assert any(work.item == claim.ref and work.kind in {"prove", "repair"}
            and work.status == "open" for work in ops.store.read().current(Obligation))
        report = result["report"]
        assert "correspondence review" in (report.get("detail", "") + " ".join(report.get("reasons", ())))
        chat.close()
        chat, _, ops = live(tmp_path, monkeypatch)
        latest, _ = chat.owners.judgments.correspondence_review(claim.ref, formal.ref, scope.ref)
        assert latest == withdrawals[-1]
        chat.owners.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="approved",
            reviewer="operator", conditions="New exact reading", reason="Resolve translation concern")
        retried = (chat.project_repair(request, max_attempts=1) if workflow == "repair"
            else chat.project_research(request, max_attempts=6))
        assert retried["status"] == "completed", retried
        assert list((tmp_path / "lean").rglob("*.lean"))
    finally:
        chat.close()


@pytest.mark.parametrize("change", ["unrelated-dispute", "exact-approval"])
def test_final_audit_review_changes_preserve_current_exact_correspondence(tmp_path, monkeypatch, change):
    chat, _, ops = live(tmp_path, monkeypatch)
    try:
        claim, scope = target(ops)
        formal = ops.formalization(claim.ref, id="translation", formal="lemma target_fact : True", scope=scope.ref)
        request = repair_request(ops, claim, scope)
        other = ProjectOwners.reading(tmp_path)
        original_audit = chat.formal._audit_tree

        def during_audit(*args, **kwargs):
            result = original_audit(*args, **kwargs)
            if change == "unrelated-dispute":
                unrelated = SemanticOperations(tmp_path, owners=other)
                # A generic semantic reading is separate from this exact pair.
                unrelated.judge(JudgmentRequest(subject=claim.ref, scope=scope.ref, purpose="correspondence",
                    verdict="disputed", actor="human", reviewer="operator",
                    conditions="Different question", reason="No exact formalization selected"))
            else:
                other.review_translation(claim.ref, formal.ref, scope=scope.ref, verdict="approved",
                    reviewer="operator", conditions="Other session exact reading", reason="Confirm exact pair")
            return result

        monkeypatch.setattr(chat.formal, "_audit_tree", during_audit)
        result = chat.project_repair(request, max_attempts=1)
        assert result["status"] == "completed", result
        assert list((tmp_path / "lean").rglob("*.lean"))
    finally:
        chat.close()
