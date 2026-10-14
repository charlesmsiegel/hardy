"""Typed human project edits and exact read models over the existing owners.

The session supplies its live owners and holds its normal conversation/edit
gates. This adapter proposes records; mathematical acceptance remains in policy.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, StrictBool, StrictInt, model_validator

from hardy.foundation.journal import record_digest
from hardy.foundation.values import FrozenModel
from hardy.workflows.context import BindingSpec, DeclarationSpec
from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.judgments import SemanticJudgment
from hardy.workflows.interactive.scoped_admission import (
    AdmissionGateRecord,
    HumanScopeDecision,
    ScopedAdmissionRequest,
)
from hardy.workflows.interactive.semantic_operations import SemanticOperations
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    ItemRequest,
    JudgmentRequest,
    SourceRegion,
)
from hardy.workflows.ledger.contracts import (
    Digest,
    EvidenceRef,
    MathematicalContext,
    Obligation,
    ObligationKind,
    ProjectItem,
    ProjectItemKind,
    Relation,
    Scope,
    ScopedBinding,
    StableId,
    Text,
    VersionRef,
)


class ContextRequest(FrozenModel):
    id: StableId
    label: Text
    parent: VersionRef | None = None
    declarations: tuple[DeclarationSpec, ...] = ()
    bindings: tuple[BindingSpec, ...] = ()
    supersedes: VersionRef | None = None


class SourceRequest(FrozenModel):
    id: StableId
    text: Text


class ScopeRequest(FrozenModel):
    id: StableId
    must_prove: tuple[VersionRef, ...]


class RevisionRequest(FrozenModel):
    subject: VersionRef
    statement: Text
    context: VersionRef | None = None


class ObligationRequest(FrozenModel):
    subject: VersionRef
    scope: VersionRef
    kind: ObligationKind
    reason: Text


class SelectedReportRequest(FrozenModel):
    target: VersionRef
    original: VersionRef | None = None
    repaired: VersionRef | None = None
    scope: VersionRef | None = None


class FormalizationRequest(FrozenModel):
    subject: VersionRef
    id: StableId
    formal: Text
    scope: VersionRef


class AdmissionRequest(FrozenModel):
    subject: VersionRef
    scope: VersionRef
    interface: StrictBool = False
    sources: tuple[SourceRegion, ...] = ()
    formalization: VersionRef | None = None


class AdmissionGatesRequest(FrozenModel):
    request: Text
    request_digest: Digest
    faithfulness: EvidenceRef


class AdmissionDecisionRequest(FrozenModel):
    request: Text
    request_digest: Digest
    gate: Text | None = None
    gate_digest: Digest | None = None
    approved: StrictBool
    reviewer: Text
    conditions: Text
    reason: Text

    @model_validator(mode="after")
    def exact_gate(self):
        if (self.gate is None) != (self.gate_digest is None) or (self.approved and self.gate is None):
            raise ValueError("Approval requires the exact reviewed admission gate ID and digest.")
        return self


class SourceRegionRequest(FrozenModel):
    source: VersionRef
    start: StrictInt
    end: StrictInt


SEMANTIC_REQUESTS: dict[str, type[BaseModel]] = {
    "context": ContextRequest, "source": SourceRequest, "scope": ScopeRequest,
    "item": ItemRequest, "argument": ArgumentRequest, "step": ItemRequest,
    "critique": ItemRequest, "revise": RevisionRequest, "obligation": ObligationRequest,
    "judge": JudgmentRequest, "assess": AssessmentRequest, "report": SelectedReportRequest,
    "formalization": FormalizationRequest, "admission-request": AdmissionRequest,
    "admission-gates": AdmissionGatesRequest, "admission-decide": AdmissionDecisionRequest,
    "source-region": SourceRegionRequest,
}


def _dump(record: Any) -> dict[str, Any]:
    data = record.model_dump(mode="json")
    if hasattr(record, "ref"):
        data["ref"] = record.ref.model_dump(mode="json")
        data["digest"] = record.digest
    else:
        data["digest"] = record_digest(record)
    return data


class ProjectApplication:
    def __init__(self, problem, *, owners: ProjectOwners | None = None):
        self.operations = SemanticOperations(problem, owners=owners)

    def execute(self, operation: str, request: BaseModel | dict[str, Any]):
        model = SEMANTIC_REQUESTS.get(operation)
        if model is None:
            raise ValueError("Unknown semantic project operation.")
        request = model.model_validate(request.model_dump() if isinstance(request, BaseModel) else request)
        values = {name: getattr(request, name) for name in type(request).model_fields}
        operations = self.operations
        if operation in {"item", "argument", "step", "critique", "judge", "assess"}:
            return getattr(operations, operation)(request)
        if operation.startswith("admission-"):
            admissions = operations.admissions
            if operation == "admission-request":
                return admissions.request(**values)
            pending = admissions._request(request.request)
            if record_digest(pending) != request.request_digest:
                raise ValueError("Admission request changed; reload its exact version before reviewing.")
            if operation == "admission-gates":
                return admissions.run_gates(request.request, faithfulness=request.faithfulness)
            # The owner validates the exact reviewed gate, freshness and latest
            # gate under its own decision snapshot; the UI cannot bypass it.
            return admissions.decide(request.request, approved=request.approved,
                reviewer=request.reviewer, conditions=request.conditions, reason=request.reason,
                expected_gate_id=request.gate, expected_gate_digest=request.gate_digest)
        if operation == "report":
            return operations.report(**values)
        if operation == "source-region":
            region = SourceRegion.model_validate(values)
            return {"region": region.model_dump(mode="json"), "text": operations.region_text(region)}
        return getattr(operations, operation)(**values)

    def catalog(self) -> dict[str, Any]:
        """Exact choices, histories and gate reasons; never frontend authority."""
        operations = self.operations
        snapshot = operations.store.read()
        items = list(snapshot.current(ProjectItem))
        journal = operations.owners.journal.read()
        admissions = []
        for request in journal.of(ScopedAdmissionRequest):
            gates = [value for value in journal.of(AdmissionGateRecord) if value.request == request.id]
            gate = gates[-1] if gates else None
            decisions = [value for value in journal.of(HumanScopeDecision) if value.request == request.id]
            current = False
            reason = "Run all required admission gates before accepting this premise."
            if gate:
                try:
                    current = operations.admissions._valid_gate(gate, snapshot)
                    reason = gate.reason if current else "Admission gates are missing or stale; run them again."
                except (ValueError, OSError) as error:
                    reason = str(error)
            admissions.append({**_dump(request), "gate": _dump(gate) if gate else None,
                "can_approve": current, "reason": reason, "decisions": [_dump(value) for value in decisions]})
        source_history = []
        for item in snapshot.records:
            if not isinstance(item, ProjectItem):
                continue
            if item.kind != ProjectItemKind.SOURCE:
                continue
            row = _dump(item)
            try:
                row["text"] = operations.source_text(item.ref)
            except (ValueError, OSError) as error:
                row["error"] = str(error)
            source_history.append(row)
        sources = [row for row in source_history if snapshot.head(row["id"]).digest == row["digest"]]
        evidence = [reference for item in items for reference in item.evidence]
        for record in journal.records:
            if isinstance(record, SemanticJudgment):
                evidence.append(operations.judgments.reference(record))
            elif type(record).__name__ == "CheckedBinding":
                evidence.append(operations.owners.binding_reference(record))
            elif type(record).__name__ == "TranslationReview":
                evidence.append(operations.owners.review_reference(record))
            elif type(record).__name__ == "FrozenFormalEvidence":
                evidence.append(operations.owners.live_evidence.reference(record))
        evidence_rows = []
        seen = set()
        for reference in evidence:
            identity = reference.model_dump_json()
            if identity in seen:
                continue
            seen.add(identity)
            try:
                authenticated = operations.owners.read_evidence_at(snapshot, reference) is not None
                reason = None if authenticated else "Evidence is historical, missing a gate, or stale."
            except (ValueError, OSError) as error:
                authenticated, reason = False, str(error)
            evidence_rows.append({"reference": reference.model_dump(mode="json"),
                "label": f"{reference.subject.id} · {reference.kind.value} · {reference.artifact.digest[:8]}",
                "authenticated": authenticated, "reason": reason})
        return {
            "project": str(operations.problem), "revision": snapshot.revision,
            "contexts": [_dump(value) for value in snapshot.current(MathematicalContext)],
            "bindings": [_dump(value) for value in snapshot.current(ScopedBinding)],
            "scopes": [_dump(value) for value in snapshot.current(Scope)],
            "items": [_dump(value) for value in items], "sources": sources, "source_history": source_history,
            "obligations": [_dump(value) for value in snapshot.current(Obligation)],
            "relations": [_dump(value) for value in snapshot.current(Relation)],
            "admissions": admissions,
            "evidence_refs": evidence_rows,
            "evidence": [_dump(value) for value in journal.records
                         if type(value).__name__ in {"SemanticJudgment", "MathematicalAssessment", "TranslationReview"}],
        }
