"""Independent versioned statement/argument assessments; review never becomes proof."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from hardy.foundation.journal import record_digest
from hardy.workflows.interactive.semantic_owner import SemanticOwner
from hardy.workflows.interactive.semantic_records import AssessmentRequest
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, Scope, Text, VersionRef
from hardy.workflows.ledger.state import LedgerSnapshot


class MathematicalAssessment(AssessmentRequest):
    id: Text
    context: VersionRef | None
    inputs: tuple[VersionRef, ...]
    recorded_at: Text


class AssessmentOperations:
    def __init__(self, owners: SemanticOwner):
        self.owners = owners

    def record(self, request: AssessmentRequest) -> MathematicalAssessment:
        from hardy.workflows.interactive.semantic_records import stamp
        from hardy.workflows.ledger.store import LedgerStore

        request = AssessmentRequest.model_validate(request.model_dump())
        snapshot = LedgerStore(self.owners.problem).read()
        subject, scope = snapshot.get(request.subject), snapshot.get(request.scope)
        if not isinstance(subject, ProjectItem) or not isinstance(scope, Scope):
            raise ValueError("assessment requires exact item and scope")
        self.owners.policy._current_scope(snapshot, scope)
        argument = subject.kind in {ProjectItemKind.ARGUMENT, ProjectItemKind.PROOF_STEP}
        if argument != (request.dimension == "argument"):
            raise ValueError("assessment dimension does not match subject")
        context_members: set[VersionRef] = set()
        if subject.context:
            from hardy.workflows.ledger.graph import LedgerGraph
            context_members = {ref for c in LedgerGraph(snapshot).context_chain(subject.context) for ref in c.declarations}
        if not set(request.assumptions).issubset(set(scope.allowed_background + scope.allowed_interfaces) | context_members):
            raise ValueError("assessment assumptions are outside exact permitted scope/context")
        for evidence in request.evidence:
            if self.owners.read_evidence_at(snapshot, evidence) is None:
                raise ValueError("assessment evidence cannot be authenticated")
        inputs = relevant_versions(self.owners.problem, snapshot, request.subject,
                                   (*request.relevant, request.scope, *request.assumptions))
        record = MathematicalAssessment(**request.model_dump(), id=f"assessment:{uuid4().hex}",
            context=subject.context, inputs=inputs, recorded_at=stamp())
        self.owners._append(record)
        return record

    def read(self, record: MathematicalAssessment, *, snapshot: LedgerSnapshot) -> MathematicalAssessment | None:
        try:
            scope = snapshot.get(record.scope)
            if not isinstance(scope, Scope):
                return None
            self.owners.policy._current_scope(snapshot, scope)
            values = self.owners.journal.read().of(MathematicalAssessment)
            exact = [value for value in values if value.id == record.id and record_digest(value) == record_digest(record)]
            matching = [value for value in values if (value.subject, value.scope, value.dimension)
                        == (record.subject, record.scope, record.dimension)]
            if not exact or not matching or matching[-1] != record:
                return None
            inputs = relevant_versions(self.owners.problem, snapshot, record.subject,
                                       (*record.relevant, record.scope, *record.assumptions))
            if inputs != record.inputs:
                return None
            if any(self.owners.read_evidence_at(snapshot, reference) is None for reference in record.evidence):
                return None
        except (ValueError, OSError):
            return None
        return record

    def report(self, snapshot: LedgerSnapshot, subject: VersionRef, scope: VersionRef,
               dimension: str) -> dict[str, Any]:
        historical = [r for r in self.owners.journal.read().of(MathematicalAssessment)
                      if (r.subject, r.scope, r.dimension) == (subject, scope, dimension)]
        latest = historical[-1] if historical else None
        current = self.read(latest, snapshot=snapshot) if latest else None
        status = ("supported_by_review" if current and current.verdict == "supported" else
                  "refuted_by_review" if current and current.verdict == "refuted" else
                  current.verdict if current else "stale" if latest else "unassessed")
        return {"subject": subject.model_dump(mode="json"), "status": status,
                "reason": current.reason if current else "Recorded assessment has stale inputs or unavailable evidence."
                          if latest else "No assessment is recorded for this exact subject and scope.",
                "evidence_level": "heuristic_or_human_review", "evidence": [e.model_dump(mode="json") for e in current.evidence] if current else [],
                "assumptions": [r.model_dump(mode="json") for r in current.assumptions] if current else [],
                "coverage": list(current.coverage) if current else [],
                "unresolved": list(current.unresolved) if current else ["Current assessment unavailable."],
                "assessments": [r.model_dump(mode="json") for r in historical]}


def argument_assessments(snapshot: LedgerSnapshot, target: VersionRef, scope: VersionRef, *,
                         operations: AssessmentOperations, repaired: VersionRef | None = None) -> dict[str, Any]:
    """Select argument lanes explicitly; no conclusion inherits an argument's defects."""
    from hardy.workflows.ledger.contracts import Relation, RelationKind
    related = [snapshot.get(edge.source) for edge in snapshot.current(Relation)
               if edge.target == target and edge.kind == RelationKind.SUPPORTS]
    originals = [item for item in related if isinstance(item, ProjectItem) and item.kind == ProjectItemKind.ARGUMENT
                 and ("argument-role", "original") in item.semantics]
    selected = None
    if repaired is not None:
        selected = snapshot.get(repaired)
        if (not isinstance(selected, ProjectItem) or selected not in related
                or selected.kind != ProjectItemKind.ARGUMENT or ("argument-role", "repaired") not in selected.semantics):
            raise ValueError("selected repair must be an exact repaired argument for this target")
    def empty(reason):
        return {"status": "unassessed", "reason": reason, "evidence": [],
                "assumptions": [], "coverage": [], "unresolved": [reason], "assessments": []}
    original = (operations.report(snapshot, originals[0].ref, scope, "argument") if len(originals) == 1 else
                empty("Multiple original arguments require an explicit audit selection." if originals else
                      "No supplied original argument has been assessed."))
    repair = (operations.report(snapshot, selected.ref, scope, "argument") if selected else
              empty("No repaired argument has been selected for assessment."))
    claim_kinds = {ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION,
                   ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE}
    intermediate = [operations.report(snapshot, item.ref, scope, "statement") for item in related
                    if isinstance(item, ProjectItem) and item.kind in claim_kinds]
    reports = [original, repair, *intermediate]
    assumptions = [ref for report in reports for ref in report["assumptions"]]
    gaps = [report["reason"] for report in reports if report["status"] in {"unassessed", "unresolved", "stale"}]
    gaps.extend(reason for report in reports for reason in report.get("unresolved", []) if reason not in gaps)
    return {"original_proof": original, "repaired_proof": repair, "intermediate_claims": intermediate,
            "argument_assumptions": assumptions, "argument_coverage_gaps": list(dict.fromkeys(gaps))}
