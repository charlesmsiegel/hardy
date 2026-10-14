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
    if ("role", "semantic_target") not in target.semantics:
        scopes = [value for value in snapshot.current(Scope) if target.ref in value.must_prove]
        selected_scope = snapshot.get(scope) if scope else scopes[0] if len(scopes) == 1 else None
        if not isinstance(selected_scope, Scope) or snapshot.head(selected_scope.id).ref != selected_scope.ref:
            raise ValueError("Inspection requires one exact scope or an explicit scope selection.")
        statement = assessments.report(snapshot, target.ref, selected_scope.ref, "statement")
        if statement["status"] == "unassessed":
            statement["status"] = "unresolved"
        lanes = argument_assessments(snapshot, target.ref, selected_scope.ref, operations=assessments, repaired=repaired)
        return {"schema": "hardy.semantic-target-report/v1", "project": str(problem), "revision": snapshot.revision,
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
    from hardy.workflows.ledger.contracts import Relation, RelationKind

    links = [r for r in snapshot.current(Relation) if r.kind == RelationKind.FORMALIZES and r.target == target.ref]
    if len(links) != 1:
        raise ValueError("The target has no unique exact proposed formalization.")
    formal = snapshot.get(links[0].source)
    if not isinstance(formal, ProjectItem):
        raise ValueError("The proposed formalization is not an item.")
    _, _, link, proposal_scope = owner._proposal_pair(snapshot, target.ref, formal.ref)
    scope = snapshot.get(requested_scope) if requested_scope is not None else proposal_scope
    if not isinstance(scope, Scope) or snapshot.head(scope.id).ref != scope.ref:
        raise ValueError("Inspection requires the selected exact current scope.")
    journal = owner.journal.read()
    reviews = [r for r in journal.of(TranslationReview) if r.subject == target.ref and r.translation == formal.ref
               and r.scope == scope.ref and r.context == target.context]
    bindings = [b for b in journal.of(CheckedBinding) if b.target == target.ref and b.translation == formal.ref
                and b.scope == scope.ref and b.context == target.context]
    latest = reviews[-1] if reviews else None
    faithful = latest is not None and owner.read_evidence_at(snapshot, owner.review_reference(latest)) is not None
    work = {o.kind: o for o in snapshot.current(Obligation) if o.item in {target.ref, formal.ref}
            and o.scope.ref == scope.ref and o.context == target.context}
    proof = work.get(ObligationKind.PROVE)
    formalization = work.get(ObligationKind.FORMALIZE)
    verified = (proof is not None and proof.resolution is not None
                and owner.policy.is_accepted(snapshot, proof.resolution))
    translated = (formalization is not None and formalization.resolution is not None
                  and owner.policy.is_accepted(snapshot, formalization.resolution))
    evidence = []
    axioms: list[str] | None = None
    if bindings:
        held = owner.formal_record(bindings[-1].evidence)
        if held is not None:
            axioms = list(held.axioms)
        evidence = [owner.binding_reference(bindings[-1]).model_dump(mode="json")]
    statement_ready = bool(verified and faithful and translated)
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
                        for o in work.values()],
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
