"""Read models for exact semantic proposals; every assessment names its evidence and limits."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hardy.foundation.files import WriteGuard
from hardy.foundation.locking import FileLock
from hardy.workflows.interactive.project import _selected
from hardy.workflows.layout import validate_slug
from hardy.workflows.ledger.contracts import Obligation, ObligationKind, ProjectItem
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews

if TYPE_CHECKING:
    from hardy.workflows.interactive.evidence import ProjectOwners


def target_report(problem: Path, selector: str, *, owners: ProjectOwners | None = None) -> dict[str, Any]:
    # Imported here because the evidence owner owns the journal's schema.
    from hardy.workflows.interactive.evidence import (
        CheckedBinding,
        ProjectOwners,
        TranslationReview,
    )

    snapshot = LedgerStore(problem).read()
    target = _selected(snapshot, selector, ProjectItem)
    if ("role", "semantic_target") not in target.semantics:
        raise ValueError("Inspection requires a semantic target.")
    owner = owners or ProjectOwners.reading(problem)
    from hardy.workflows.ledger.contracts import Relation, RelationKind

    links = [r for r in snapshot.current(Relation) if r.kind == RelationKind.FORMALIZES and r.target == target.ref]
    if len(links) != 1:
        raise ValueError("The target has no unique exact proposed formalization.")
    formal = snapshot.get(links[0].source)
    if not isinstance(formal, ProjectItem):
        raise ValueError("The proposed formalization is not an item.")
    _, _, link, scope = owner._proposal_pair(snapshot, target.ref, formal.ref)
    journal = owner.journal.read()
    reviews = [r for r in journal.of(TranslationReview) if r.subject == target.ref and r.translation == formal.ref]
    bindings = [b for b in journal.of(CheckedBinding) if b.target == target.ref and b.translation == formal.ref]
    latest = reviews[-1] if reviews else None
    faithful = latest is not None and owner.read_evidence_at(snapshot, owner.review_reference(latest)) is not None
    work = {o.kind: o for o in snapshot.current(Obligation) if o.item in {target.ref, formal.ref}}
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
                     if proof and proof.resolution else "No checked save is bound to this exact formal statement.")
    context = LedgerViews(snapshot).context(target.context) if target.context else None
    candidates = []
    for item in snapshot.current(ProjectItem):
        if item.statement != formal.statement or item.context != formal.context or not item.id.startswith("lean:"):
            continue
        if any(o.item == item.ref and o.kind == ObligationKind.PROVE and o.resolution is not None
               and owner.policy.is_accepted(snapshot, o.resolution) for o in snapshot.current(Obligation)):
            candidates.append({**item.ref.model_dump(), "name": item.name})
    return {
        "project": str(problem),
        "saved_candidates": candidates,
        "context_hypotheses": [h.model_dump(mode="json") for h in context.local_hypotheses] if context else [],
        "schema": "hardy.semantic-target-report/v1", "revision": snapshot.revision,
        "target": {**target.ref.model_dump(), "statement": target.statement},
        "formal": {**formal.ref.model_dump(), "statement": formal.statement},
        "context": target.context.model_dump() if target.context else None,
        "scope": scope.model_dump(mode="json"), "relation": link.ref.model_dump(),
        "translation": {"status": translation_status, "formalization_accepted": bool(translated),
                        "reason": latest.reason if latest else "No correspondence review is recorded.",
                        "reviews": [r.model_dump(mode="json") for r in reviews]},
        "formal_proof": {"status": "kernel_verified" if verified else "stale" if proof and proof.resolution else "unresolved",
                         "reason": formal_reason, "axioms": axioms, "evidence": evidence,
                         "environment_revalidated": bool(verified),
                         "environment_revalidation_available": owner._build_snapshot is not None},
        "statement": {"status": "supported_by_reviewed_formal_proof" if statement_ready else "unresolved",
                      "reason": None if statement_ready else "Requires both current correspondence and a bound checked proof."},
        "original_proof": {"status": "unassessed", "reason": "No supplied original argument has been assessed."},
        "repaired_proof": {"status": "unassessed", "reason": "No repaired argument has been assessed."},
        "intermediate_claims": [], "assumptions": [],
        "coverage_gaps": ["Supplied arguments, intermediate claims and repairs have not been assessed."],
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
