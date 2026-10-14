"""Read models for exact semantic proposals; every assessment names its evidence and limits."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.workflows.interactive.project import _selected
from hardy.workflows.layout import validate_slug
from hardy.workflows.ledger.contracts import (
    Obligation,
    ObligationKind,
    ProjectItem,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews

if TYPE_CHECKING:
    from hardy.workflows.interactive.evidence import ProjectOwners


def target_report(problem: Path, selector: str, *, owners: ProjectOwners | None = None,
                  repaired: VersionRef | None = None, scope: VersionRef | None = None) -> dict[str, Any]:
    # Imported here because the evidence owner owns the journal's schema.
    from hardy.workflows.interactive.evidence import (
        CheckedBinding,
        ProjectOwners,
        TranslationReview,
    )

    requested_scope = scope
    snapshot = LedgerStore(problem).read()
    target = _selected(snapshot, selector, ProjectItem)
    owner = owners or ProjectOwners.reading(problem)
    from hardy.workflows.interactive.assessments import AssessmentOperations, argument_assessments
    assessments = AssessmentOperations(owner)
    from hardy.workflows.interactive.live_records import FrozenFormalEvidence
    live = owner.journal.read().of(FrozenFormalEvidence)
    native = [value for value in owner.journal.read().of(CheckedBinding) if value.target == target.ref
              and (scope is None or value.scope == scope)
              and owner.read_evidence_at(snapshot, owner.binding_reference(value)) is not None]
    if not native and (("role", "semantic_target") not in target.semantics
            or any(value.subject == target.ref and (scope is None or value.scope == scope) for value in live)):
        scopes = [value for value in snapshot.current(Scope) if target.ref in value.must_prove]
        owned_scope = next((value for value in scopes if value.id == f"{target.id}:scope"), None)
        selected_scope = snapshot.get(scope) if scope else owned_scope or (scopes[0] if len(scopes) == 1 else None)
        if not isinstance(selected_scope, Scope) or snapshot.head(selected_scope.id).ref != selected_scope.ref:
            raise ValueError("Inspection requires one exact scope or an explicit scope selection.")
        statement = assessments.report(snapshot, target.ref, selected_scope.ref, "statement")
        if statement["status"] == "unassessed":
            statement["status"] = "unresolved"
        lanes = argument_assessments(snapshot, target.ref, selected_scope.ref, operations=assessments, repaired=repaired)
        result = {"schema": "hardy.semantic-target-report/v1", "project": str(problem), "revision": snapshot.revision,
                "target": {**target.ref.model_dump(), "statement": target.statement},
                "scope": selected_scope.model_dump(mode="json"), "statement": statement,
                "formal_proof": {"status": "unresolved", "reason": "No checked formalization binding is recorded."},
                "translation": {"status": "unresolved", "reason": "No exact translation review is recorded."},
                "citation_review": {"status": "unassessed", "reason": "Citation evidence remains a separate capability."},
                "document_compilation": {"status": "unassessed", "reason": "Compilation does not assess mathematical arguments."},
                "original_proof": lanes["original_proof"], "repaired_proof": lanes["repaired_proof"],
                "intermediate_claims": lanes["intermediate_claims"],
                "assumptions": statement["assumptions"] + lanes["argument_assumptions"],
                "coverage_gaps": lanes["argument_coverage_gaps"]}
        candidates = [value for value in owner.journal.read().of(FrozenFormalEvidence)
                      if value.subject == target.ref and value.scope == selected_scope.ref]
        proposals = {value.formalization for value in candidates
                     if snapshot.head(value.formalization.id).ref == value.formalization}
        if len(proposals) == 1:
            formal_ref = next(iter(proposals))
            records = [value for value in candidates if value.formalization == formal_ref]
            formal = snapshot.get(formal_ref)
            proofs = [value for value in records if value.outcome == "kernel_proof"
                      and owner.read_evidence_at(snapshot, owner.live_evidence.reference(value)) is not None]
            readings = [value for value in records if value.outcome == "elaborated"
                        and owner.read_evidence_at(snapshot, owner.live_evidence.reference(value, faithful=True)) is not None]
            work = [value for value in snapshot.current(Obligation)
                    if value.item == target.ref and value.scope == selected_scope and value.resolution is not None
                    and owner.policy.is_accepted(snapshot, value.resolution)]
            verified = bool(proofs)
            mapping_accepted = bool(proofs and any(value.kind == ObligationKind.PROVE for value in work))
            translated = bool(readings and any(value.kind == ObligationKind.FORMALIZE for value in work))
            result["formal"] = {**formal.ref.model_dump(), "statement": formal.statement}
            result["formal_proof"] = {"status": ("verified_modulo" if proofs[-1].used_assumptions else "kernel_verified")
                if verified else "stale" if any(value.outcome == "kernel_proof" for value in records) else "unresolved",
                "evidence": [owner.live_evidence.reference(value).model_dump(mode="json") for value in proofs],
                "axioms": list(proofs[-1].axioms) if proofs else None,
                "used_assumptions": [ref.model_dump() for ref in proofs[-1].used_assumptions] if proofs else [],
                "mapping_accepted": mapping_accepted,
                "environment_revalidated": verified,
                "reason": None if verified else "No current accepted checked proof for this exact scope"}
            human_review, _legacy = owner.live_evidence.human_review(target.ref, formal.ref, selected_scope.ref)
            result["translation"] = {"status": "approved" if translated else human_review.verdict
                if human_review is not None and human_review.verdict != "approved" else "unresolved",
                "formalization_accepted": translated,
                "human_review": human_review.model_dump(mode="json") if human_review is not None else None,
                "evidence": [owner.live_evidence.reference(value, faithful=True).model_dump(mode="json") for value in readings]}
            if mapping_accepted and translated:
                assumptions = [ref.model_dump() for ref in owner.policy.trust_boundary(snapshot, target.ref,
                    scope=selected_scope, context=target.context)]
                result["statement"] = {**statement, "status": "supported_by_reviewed_formal_proof", "reason": None,
                    "evidence_level": "reviewed_kernel_proof", "evidence": [
                        owner.live_evidence.reference(proofs[-1]).model_dump(mode="json"),
                        owner.live_evidence.reference(readings[-1], faithful=True).model_dump(mode="json")],
                    "coverage": ["Exact frozen statement checked by Lean", "Exact correspondence read in a recorded fresh context"],
                    "assumptions": assumptions, "unresolved": []}
                result["assumptions"] = assumptions + lanes["argument_assumptions"]
        elif len(proposals) > 1:
            result["formal_proof"]["reason"] = "Multiple exact formalization proposals require explicit selection"
        return result
    from hardy.workflows.ledger.contracts import Relation, RelationKind

    links = [r for r in snapshot.current(Relation) if r.kind == RelationKind.FORMALIZES and r.target == target.ref
             and isinstance(snapshot.get(r.source), ProjectItem)
             and ("role", "proposed_formalization") in snapshot.get(r.source).semantics]
    if native:
        selected = native if requested_scope is not None else (
            [value for value in native if value.scope.id == f"{target.id}:scope"] or native)
        pairs = {(value.translation, value.scope) for value in selected}
        if len(pairs) != 1:
            raise ValueError("Inspection requires one exact bound formalization and scope selection.")
        formal_ref, bound_scope = next(iter(pairs))
        requested_scope = requested_scope or bound_scope
        links = [value for value in links if value.source == formal_ref]
    if len(links) != 1:
        raise ValueError("The target has no unique exact proposed formalization.")
    formal = snapshot.get(links[0].source)
    if not isinstance(formal, ProjectItem):
        raise ValueError("The proposed formalization is not an item.")
    _, _, link, proposal_scope = owner._proposal_pair(snapshot, target.ref, formal.ref, requested_scope)
    scope = snapshot.get(requested_scope) if requested_scope is not None else proposal_scope
    if not isinstance(scope, Scope) or snapshot.head(scope.id).ref != scope.ref:
        raise ValueError("Inspection requires the selected exact current scope.")
    journal = owner.journal.read()
    reviews = [r for r in journal.of(TranslationReview) if r.subject == target.ref and r.translation == formal.ref
               and r.scope == scope.ref and r.context == target.context]
    bindings = [b for b in journal.of(CheckedBinding) if b.target == target.ref and b.translation == formal.ref
                and b.scope == scope.ref and b.context == target.context]
    latest, legacy_review = owner.live_evidence.human_review(target.ref, formal.ref, scope.ref)
    faithful = latest is not None and owner.read_evidence_at(snapshot,
        owner.review_reference(latest) if legacy_review else owner.judgments.reference(latest)) is not None
    works = [o for o in snapshot.current(Obligation) if o.item in {target.ref, formal.ref}
             and o.scope.ref == scope.ref and o.context == target.context]
    accepted = [o for o in works if o.resolution is not None and owner.policy.is_accepted(snapshot, o.resolution)]
    proof = next((o for o in (*accepted, *works) if o.item == formal.ref and o.kind == ObligationKind.PROVE), None)
    formalization = next((o for o in (*accepted, *works)
                          if o.item == target.ref and o.kind == ObligationKind.FORMALIZE), None)
    verified = any(owner.read_evidence_at(snapshot, owner.binding_reference(value)) is not None for value in bindings)
    mapped = any(o.item == formal.ref and o.kind == ObligationKind.PROVE and o.resolution is not None
                 for o in accepted)
    translated = any(o.item == target.ref and o.kind == ObligationKind.FORMALIZE and o.resolution is not None
                     for o in accepted)
    evidence = []
    axioms: list[str] | None = None
    if bindings:
        held = owner.formal_record(bindings[-1].evidence)
        if held is not None:
            axioms = list(held.axioms)
        evidence = [owner.binding_reference(bindings[-1]).model_dump(mode="json")]
    statement_ready = bool(verified and mapped and faithful and translated)
    translation_status = ("approved" if faithful else "stale" if latest and latest.verdict == "approved"
                          else latest.verdict if latest else "unresolved")
    formal_reason = (None if verified else "Recorded proof cannot be authenticated against the current build."
                     if proof and proof.resolution else "No checked save is bound to this exact formal statement in the selected scope.")
    context = LedgerViews(snapshot).context(target.context) if target.context else None
    candidates = []
    for item in snapshot.current(ProjectItem):
        if item.statement != formal.statement or item.context != formal.context or not item.id.startswith("lean:"):
            continue
        if any(o.item == item.ref and o.kind == ObligationKind.PROVE and o.resolution is not None
               and owner.policy.is_accepted(snapshot, o.resolution) for o in snapshot.current(Obligation)):
            candidates.append({**item.ref.model_dump(), "name": item.name})
    lanes = argument_assessments(snapshot, target.ref, scope.ref, operations=assessments, repaired=repaired)
    statement_assessment = assessments.report(snapshot, target.ref, scope.ref, "statement")
    statement_report = (statement_assessment if not statement_ready and statement_assessment["status"] != "unassessed" else
                        {**statement_assessment, "status": "supported_by_reviewed_formal_proof" if statement_ready else "unresolved",
                         "reason": None if statement_ready else "Requires current correspondence and a bound checked proof, or an explicit statement assessment."})
    if statement_ready:
        actual = [reference.model_dump(mode="json") for work_item in (proof, formalization)
                  for reference in work_item.resolution.evidence]
        statement_report = {**statement_report, "evidence_level": "reviewed_kernel_proof", "evidence": actual,
            "coverage": ["Exact formal statement checked by Lean", "Exact informal/formal correspondence reviewed"],
            "assumptions": [ref.model_dump(mode="json") for ref in owner.policy.trust_boundary(
                snapshot, formal.ref, scope=scope, context=target.context)], "unresolved": []}
    return {
        "project": str(problem),
        "saved_candidates": candidates if scope.ref == proposal_scope.ref else [],
        "context_hypotheses": [h.model_dump(mode="json") for h in context.local_hypotheses] if context else [],
        "schema": "hardy.semantic-target-report/v1", "revision": snapshot.revision,
        "target": {**target.ref.model_dump(), "statement": target.statement},
        "formal": {**formal.ref.model_dump(), "statement": formal.statement},
        "context": target.context.model_dump() if target.context else None,
        "scope": scope.model_dump(mode="json"), "relation": link.ref.model_dump(),
        "translation": {"status": translation_status, "formalization_accepted": bool(translated),
                        "reason": latest.reason if latest else "No correspondence review is recorded for the selected exact scope.",
                        "reviews": [r.model_dump(mode="json") for r in reviews]},
        "formal_proof": {"status": "kernel_verified" if verified else "stale" if proof and proof.resolution else "unresolved",
                         "mapping_accepted": bool(mapped and faithful),
                         "reason": formal_reason, "axioms": axioms, "evidence": evidence,
                         "environment_revalidated": bool(verified),
                         "environment_revalidation_available": owner._build_snapshot is not None},
        "statement": statement_report,
        "original_proof": lanes["original_proof"], "repaired_proof": lanes["repaired_proof"],
        "intermediate_claims": lanes["intermediate_claims"],
        "assumptions": statement_report["assumptions"] + lanes["argument_assumptions"],
        "coverage_gaps": lanes["argument_coverage_gaps"],
        "citation_review": {"status": "unassessed", "reason": "Citation evidence remains a separate capability."},
        "document_compilation": {"status": "unassessed", "reason": "Compilation does not assess mathematical arguments."},
        "bindings": [b.model_dump(mode="json") for b in bindings],
        "obligations": [{**o.model_dump(mode="json"), "authenticated": bool(o.resolution and owner.policy.is_accepted(snapshot, o.resolution))}
                        for o in works],
    }


def export_target(problem: Path, selector: str, *, output: str, owners: ProjectOwners) -> Path:
    """Freeze the computed report; compilation or conversational claims never decide its assessments."""
    name = validate_slug(output)
    report = target_report(problem, selector, owners=owners)
    root = WriteGuard(problem)
    guard = WriteGuard(root.path("reports"), create=True)
    with FileLock(guard.reserve("writer.lock")):
        destination = guard.path(f"{name}.json")
        if destination.exists():
            raise ValueError("A report with this output name already exists.")
        guard.write_bytes(f"{name}.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return destination
