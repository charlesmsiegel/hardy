"""Durable attributed semantic readings, restricted to the purpose actually reviewed.

These authenticate local owner records and exact bytes, not reviewer identity or
mathematical truth. Neither a source read nor a human approval is a kernel proof.
"""
from __future__ import annotations

from typing import Any, Literal
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
    Relation,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.validation import references

PRODUCER = "hardy.semantic-judgments"
URI = "hardy-semantic-judgment:"


class TranslationReview(FrozenModel):
    """An attributed correspondence judgment; approval is never a kernel proof."""

    id: str
    subject: VersionRef
    translation: VersionRef
    relation: VersionRef
    scope: VersionRef
    context: VersionRef | None
    verdict: Literal["approved", "disputed", "unresolved"]
    reviewer: str
    conditions: str
    reason: str
    recorded_at: str
    inputs: tuple[VersionRef, ...] = ()

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(**kwargs)
        if not self.inputs:
            data.pop("inputs", None)
        return data


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

    @staticmethod
    def _extra(request: JudgmentRequest) -> tuple[VersionRef, ...]:
        return (*request.relevant, request.scope,
                *((request.citation,) if request.citation else ()),
                *tuple(references(request.citation_use)))

    def _source_matches(self, contract: CitationContract, request: JudgmentRequest) -> bool:
        reading = self.owners._find(SemanticJudgment, URI, contract.source_statement)
        return bool(reading is not None and reading.purpose == "source_read"
                    and reading.subject == request.subject and reading.scope == request.scope
                    and reading.sources and reading.sources == request.sources)

    def _citation_identity(self, snapshot: LedgerSnapshot, record: SemanticJudgment):
        """Resolve a prospective review to its exact bound use, preserving revisions."""
        if record.citation is not None:
            return record.citation
        reference = self.reference(record)
        bound = {value.citation for value in self.owners.journal.read().of(CitationEvidenceBinding)
                 if reference in value.evidence}
        if len(bound) == 1:
            return next(iter(bound))
        if bound:
            raise ValueError("citation review has conflicting exact bindings")
        if record.citation_use is None:
            return None
        contracts = [value for value in snapshot.current(CitationContract)
                     if record.citation_use.matches(value) and self._source_matches(value, record)]
        # An unbound review cannot select between distinct exact contracts.
        return contracts[0].ref if len(contracts) == 1 else record.citation_use

    def _history_key(self, snapshot: LedgerSnapshot, record: SemanticJudgment):
        citation = (self._citation_identity(snapshot, record) if record.purpose in {"citation", "source_read"}
                    else (record.citation, record.citation_use))
        return (record.subject, record.scope, record.context, record.purpose, citation,
                record.sources, self._formalizations(snapshot, record))

    @staticmethod
    def _formalizations(snapshot: LedgerSnapshot, record: SemanticJudgment) -> set[VersionRef]:
        return {value.source for value in snapshot.current(Relation)
                if record.purpose == "correspondence" and value.kind == "formalizes"
                and value.target == record.subject and value.source in record.relevant}

    def correspondence_review(self, subject: VersionRef, formalization: VersionRef, scope: VersionRef,
                              *, human_only: bool = True):
        """One journal-ordered exact-pair history for native and semantic reviews.

        Generic correspondence readings (for example, admission of an external
        premise) carry no formalization and retain their separate authority.
        """
        reviews = [value for value in self.owners.journal.read().records if (
            isinstance(value, TranslationReview) and value.subject == subject
            and value.translation == formalization and value.scope == scope
        ) or (
            isinstance(value, SemanticJudgment) and value.purpose == "correspondence"
            and value.subject == subject and value.scope == scope
            and formalization in value.relevant
        )]
        human = [value for value in reviews if getattr(value, "actor", None) != "model"]
        selected = human if human_only or human else reviews
        latest = selected[-1] if selected else None
        return latest, isinstance(latest, TranslationReview)

    def _current(self, snapshot: LedgerSnapshot, record: SemanticJudgment) -> bool:
        if record.purpose == "correspondence":
            formalizations = self._formalizations(snapshot, record)
            if formalizations:
                return all(self.correspondence_review(record.subject, formalization, record.scope,
                    human_only=False)[0] == record for formalization in formalizations)
        key = self._history_key(snapshot, record)
        matching = [value for value in self.owners.journal.read().of(SemanticJudgment)
                    if (value.subject, value.scope, value.context, value.purpose,
                        value.sources) == (record.subject, record.scope,
                            record.context, record.purpose, record.sources)
                    and (self._history_key(snapshot, value) == key or (
                        # Withdrawing the original physical source reading
                        # also withdraws uses subsequently bound to that read.
                        # An explicitly selected other use remains separate.
                        record.purpose == "source_read" and value.verdict != "approved"
                        and value.citation is None and value.citation_use is None))]
        human = [value for value in matching if value.actor != "model"]
        selected = human or matching
        return bool(selected and selected[-1] == record)

    def record(self, request: JudgmentRequest) -> SemanticJudgment:
        from hardy.workflows.interactive.semantic_records import stamp
        from hardy.workflows.ledger.store import LedgerStore

        request = JudgmentRequest.model_validate(request.model_dump())
        snapshot = LedgerStore(self.owners.problem).read()
        subject, scope = snapshot.get(request.subject), snapshot.get(request.scope)
        if not isinstance(subject, ProjectItem) or not isinstance(scope, Scope):
            raise ValueError("semantic review requires exact item and scope")
        extra = self._extra(request)
        if request.purpose == "citation" and request.citation is None and request.citation_use is None:
            raise ValueError("applicability review requires an exact citation or frozen citation_use proposal")
        if request.citation_use is not None and request.citation_use.required_claim != request.subject:
            raise ValueError("citation proposal must name the exact reviewed subject")
        if request.citation is not None:
            contract = snapshot.get(request.citation)
            if (not isinstance(contract, CitationContract) or contract.required_claim != request.subject
                    or request.citation_use is not None and not request.citation_use.matches(contract)):
                raise ValueError("judgment must bind the exact citation subject and use")
            if request.purpose in {"citation", "source_read"} and not self._source_matches(contract, request):
                raise ValueError("citation review sources do not match its exact source receipt")
        if request.purpose in {"source_read", "citation"} and not request.sources:
            raise ValueError("source and citation judgments require an actual exact source read")
        for region in request.sources:
            text = read_region(self.owners.problem, snapshot, region)
            if request.purpose in {"citation", "source_read"} and request.verdict == "approved" and not text.strip():
                raise ValueError("an approved source reading requires nonempty source material")
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
            if not self._current(snapshot, record):
                return None
            inputs = relevant_versions(self.owners.problem, snapshot, record.subject,
                self._extra(record), sources=record.sources)
            if inputs != record.inputs:
                return None
            if record.purpose in {"citation", "source_read"}:
                if not record.sources or any(not read_region(self.owners.problem, snapshot, region).strip()
                                             for region in record.sources):
                    return None
                if record.citation is not None:
                    contract = snapshot.get(record.citation)
                    if (not isinstance(contract, CitationContract) or not self._source_matches(contract, record)
                            or contract.required_claim != record.subject
                            or record.citation_use is not None and not record.citation_use.matches(contract)):
                        return None
        except (ValueError, OSError):
            return None
        citation = record.citation
        if record.purpose == "citation" and citation is None and record.citation_use is None:
            return None
        if record.purpose in {"source_read", "citation"}:
            bindings = [value for value in self.owners.journal.read().of(CitationEvidenceBinding)
                        if reference in value.evidence]
            if bindings:
                if len({binding.citation for binding in bindings}) != 1:
                    return None
                binding = bindings[-1]
                if record.citation is not None and record.citation != binding.citation:
                    return None
                try:
                    contract = snapshot.get(binding.citation)
                    if (not isinstance(contract, CitationContract) or contract.required_claim != record.subject
                            or contract.evidence != binding.evidence
                            or not self._source_matches(contract, record)
                            or record.citation_use is not None and not record.citation_use.matches(contract)
                            or record.purpose == "source_read" and contract.source_statement != reference.artifact):
                        return None
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
            record = self.owners._find(SemanticJudgment, URI, reference.artifact)
            prior = [binding for binding in self.owners.journal.read().of(CitationEvidenceBinding)
                     if reference in binding.evidence]
            if record is not None and (
                record.citation is not None and record.citation != citation
                or any(binding.citation != citation for binding in prior)
                or record.citation_use is not None and not record.citation_use.matches(contract)
            ):
                raise ValueError("receipt reviewed or was already bound to a different citation")
            if (record is not None and record.purpose == "citation"
                    and record.citation is None and record.citation_use is None):
                raise ValueError("legacy applicability receipt did not freeze this citation use")
            if record is not None and not self._source_matches(contract, record):
                raise ValueError("citation sources do not match the source/applicability review")
            value = self.read(snapshot, reference)
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
