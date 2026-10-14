"""Durable attributed semantic readings, restricted to the purpose actually reviewed.

These authenticate local owner records and exact bytes, not reviewer identity or
mathematical truth. Neither a source read nor a human approval is a kernel proof.
"""
from __future__ import annotations

from uuid import uuid4

from hardy.foundation.journal import record_digest
from hardy.foundation.values import FrozenModel
from hardy.workflows.interactive.semantic_owner import SemanticOwner
from hardy.workflows.interactive.semantic_records import PURPOSE_KINDS, JudgmentRequest
from hardy.workflows.interactive.semantic_versions import read_region, relevant_versions
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceKind,
    EvidenceRef,
    ProjectItem,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence
from hardy.workflows.ledger.state import LedgerSnapshot

PRODUCER = "hardy.semantic-judgments"
URI = "hardy-semantic-judgment:"


class SemanticJudgment(JudgmentRequest):
    id: Text
    context: VersionRef | None
    inputs: tuple[VersionRef, ...]
    recorded_at: Text


class CitationEvidenceBinding(FrozenModel):
    id: Text
    citation: VersionRef
    evidence: tuple[EvidenceRef, ...]
    inputs: tuple[VersionRef, ...]
    recorded_at: Text


class SemanticJudgments:
    def __init__(self, owners: SemanticOwner):
        self.owners = owners

    def record(self, request: JudgmentRequest) -> SemanticJudgment:
        from hardy.workflows.interactive.semantic_records import stamp
        from hardy.workflows.ledger.store import LedgerStore

        request = JudgmentRequest.model_validate(request.model_dump())
        snapshot = LedgerStore(self.owners.problem).read()
        subject, scope = snapshot.get(request.subject), snapshot.get(request.scope)
        if not isinstance(subject, ProjectItem) or not isinstance(scope, Scope):
            raise ValueError("semantic review requires exact item and scope")
        extra = (*request.relevant, request.scope, *((request.citation,) if request.citation else ()))
        if request.purpose in {"source_read", "citation"} and not request.sources:
            raise ValueError("source and citation judgments require an actual exact source read")
        for region in request.sources:
            read_region(self.owners.problem, snapshot, region)
        inputs = relevant_versions(self.owners.problem, snapshot, request.subject, extra, sources=request.sources)
        record = SemanticJudgment(**request.model_dump(), id=f"semantic-judgment:{uuid4().hex}",
                                  context=subject.context, inputs=inputs, recorded_at=stamp())
        self.owners._append(record)
        return record

    @staticmethod
    def reference(record: SemanticJudgment) -> EvidenceRef:
        return EvidenceRef(kind=EvidenceKind.LITERATURE if record.purpose == "source_read" else EvidenceKind.FAITHFULNESS,
            subject=record.subject, producer=PRODUCER,
            artifact=ArtifactRef(uri=URI + record.id, digest=record_digest(record)))

    def read(self, snapshot: LedgerSnapshot, reference: EvidenceRef) -> AuthenticatedEvidence | None:
        if reference.producer != PRODUCER:
            return None
        record = self.owners._find(SemanticJudgment, URI, reference.artifact)
        if record is None or reference != self.reference(record) or record.verdict != "approved":
            return None
        try:
            matching = [value for value in self.owners.journal.read().of(SemanticJudgment)
                        if (value.subject, value.scope, value.purpose, value.citation)
                        == (record.subject, record.scope, record.purpose, record.citation)]
            if not matching or matching[-1] != record:
                return None
            inputs = relevant_versions(self.owners.problem, snapshot, record.subject,
                (*record.relevant, record.scope, *((record.citation,) if record.citation else ())), sources=record.sources)
            if inputs != record.inputs:
                return None
        except (ValueError, OSError):
            return None
        citation = record.citation
        if record.purpose in {"source_read", "citation"}:
            bindings = [value for value in self.owners.journal.read().of(CitationEvidenceBinding)
                        if reference in value.evidence]
            if bindings:
                binding = bindings[-1]
                try:
                    if relevant_versions(self.owners.problem, snapshot, binding.citation,
                            tuple(ref for ref in binding.inputs if ref != binding.citation)) != binding.inputs:
                        return None
                    citation = binding.citation
                except (ValueError, OSError):
                    return None
        return AuthenticatedEvidence(reference, record.scope, record.context,
            "source_read" if record.purpose == "source_read" else "faithful",
            citation=citation, obligation_kinds=PURPOSE_KINDS[record.purpose])

    def bind_citation(self, citation: VersionRef, *, evidence: tuple[EvidenceRef, ...]) -> CitationEvidenceBinding:
        """Bind preexisting source/applicability receipts after the exact contract exists.

        The source_statement artifact names the source-read receipt, whose
        reader rechecks retained source bytes and exact regions. Separating this
        binding avoids a citation -> evidence -> citation content-hash cycle.
        """
        from hardy.workflows.interactive.semantic_records import stamp
        from hardy.workflows.ledger.store import LedgerStore
        snapshot = LedgerStore(self.owners.problem).read()
        contract = snapshot.get(citation)
        if not isinstance(contract, CitationContract) or tuple(contract.evidence) != tuple(evidence):
            raise ValueError("citation binding requires its exact recorded evidence")
        checked = []
        source_regions = []
        for reference in evidence:
            value = self.read(snapshot, reference)
            record = self.owners._find(SemanticJudgment, URI, reference.artifact)
            if (value is None or record is None or reference.subject != contract.required_claim
                    or record.purpose not in {"source_read", "citation"}):
                raise ValueError("citation evidence must be authenticated source/applicability readings")
            checked.append(value)
            source_regions.extend(region.source for region in record.sources)
        if (not any(v.outcome == "source_read" and v.reference.artifact == contract.source_statement for v in checked)
                or not any(v.outcome == "faithful" for v in checked)
                or len({(v.scope, v.context) for v in checked}) != 1):
            raise ValueError("citation requires matching source and applicability judgments")
        inputs = relevant_versions(self.owners.problem, snapshot, citation)
        # The contract pins receipts, which in turn pin and reauthenticate these
        # source versions; include them directly in the binding for change audits.
        inputs = relevant_versions(self.owners.problem, snapshot, citation, tuple(source_regions))
        binding = CitationEvidenceBinding(id=f"citation-binding:{uuid4().hex}", citation=citation,
            evidence=evidence, inputs=inputs, recorded_at=stamp())
        self.owners._append(binding)
        return binding
