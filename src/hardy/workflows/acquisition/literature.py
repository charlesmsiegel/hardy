"""Goal-directed citations over the existing immutable paper and review owners.

Theory: retrieval proposes a source, comparison exposes its premises, and A2
translates the exact inventoried sentence. None of these establishes the goal.
The source is formalized independently of the goal's local context; applying it
there requires the citation's hypothesis discharges. A3 routes explicit admission
without granting trust. Capability readers authenticate exact use identities;
only the ledger policy may accept the returned candidates. Source reads use the
guarded library, and no downloaded code is executed here. Work is bounded by the
caller-supplied search limit and the existing statement inventory bounds.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from hardy.formal.contracts import FrozenClaim
from hardy.foundation.values import FrozenModel
from hardy.literature.library import PaperLibrary
from hardy.literature.metadata import PaperRecord, parse_id
from hardy.literature.statements import Statement, survey
from hardy.workflows.acquisition.contracts import ClassifiedGap, ResolverResult
from hardy.workflows.admission import (
    AdmissionPolicy,
    AdmissionRequest,
    CheckDecision,
    FaithfulnessDisposition,
    SourceEvidence,
    TrustRequestKind,
)
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessVerdict
from hardy.workflows.formalization import (
    PreparedCandidate,
    SemanticBlockers,
    StandaloneFormalizationInput,
)
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    HypothesisMapping,
    MathematicalContext,
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Text,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.representation import RepresentationModel


class LiteratureSelection(FrozenModel):
    paper_id: Text
    statement_ref: Text


class HypothesisComparison(FrozenModel):
    hypothesis: Text
    availability: Literal["established", "unresolved", "unavailable"]
    reason: Text
    evidence: tuple[EvidenceRef, ...] = ()


class LiteratureComparison(FrozenModel):
    required_statement: Text
    required_hypotheses: tuple[Text, ...]
    hypotheses: tuple[HypothesisComparison, ...]
    conclusion: Text
    conclusion_matches: bool
    conclusion_reason: Text

    @model_validator(mode="after")
    def distinct_hypotheses(self) -> Self:
        if len({h.hypothesis for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("source hypotheses must be distinct")
        return self


@dataclass(frozen=True)
class LiteratureQuery:
    snapshot: LedgerSnapshot
    obligation: Obligation
    subject: ProjectItem
    gap: ClassifiedGap


@dataclass(frozen=True)
class LiteratureSource:
    paper: PaperRecord
    statement: Statement
    evidence: SourceEvidence
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True)
class LiteratureReading:
    """A2's verdict plus its actual persisted artifact, authenticated by its owner."""
    verdict: FaithfulnessVerdict
    artifact: ArtifactRef


class LiteratureResolver:
    def __init__(self, *, library: PaperLibrary, model: RepresentationModel,
                 search: Callable[[LiteratureQuery], tuple[LiteratureSelection, ...]],
                 compare: Callable[[LiteratureQuery, LiteratureSource], LiteratureComparison],
                 prepare: Callable[[StandaloneFormalizationInput], PreparedCandidate | SemanticBlockers],
                 review: Callable[[FrozenClaim], LiteratureReading],
                 request_admission: Callable[[AdmissionRequest, SourceEvidence, PreparedCandidate], CheckDecision],
                 read_evidence: Callable[[EvidenceRef], AuthenticatedEvidence | None] | None = None,
                 max_candidates: int = 8):
        if type(max_candidates) is not int or max_candidates < 1:
            raise ValueError("max_candidates must be a positive integer")
        self.library = library
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.search = search
        self.compare = compare
        self.prepare = prepare
        self.review = review
        self.request_admission = request_admission
        self.read_evidence = read_evidence
        self.max_candidates = max_candidates
        self.admission = AdmissionPolicy()

    def resolve(self, snapshot: LedgerSnapshot, work: Obligation, gap: ClassifiedGap) -> ResolverResult:
        work = Obligation.model_validate(work.model_dump())
        gap = ClassifiedGap.model_validate(gap.model_dump())
        if gap.kind.value != "literature":
            raise ValueError("literature resolver requires a literature classification")
        subject = snapshot.get(work.item)
        if not isinstance(subject, ProjectItem) or not subject.statement:
            raise ValueError("literature acquisition requires an exact stated project item")
        for record in (work, subject, work.scope):
            if snapshot.get(record.ref) != record or snapshot.head(record.id).ref != record.ref:
                raise ValueError("stale literature obligation, subject or scope")
        if work.context != subject.context or gap.obligation != work:
            raise ValueError("literature request has a different context or classified obligation")
        if work.context is not None:
            context = snapshot.get(work.context)
            if not isinstance(context, MathematicalContext):
                raise ValueError("literature context is not a mathematical context")
        if work.kind.value not in {"acquire_prerequisite", "check_citation"} or work.status.value not in {"open", "investigating"}:
            raise ValueError("literature resolution requires an open acquisition/citation obligation")
        request = AdmissionRequest(TrustRequestKind.PAPER_STATEMENT_ASSUMPTION, work.item, work.scope)
        refusal = self.admission.request_refusal(request)
        if refusal:
            return ResolverResult(detail=refusal)
        query = LiteratureQuery(snapshot, work, subject, gap)
        prefix = f"{work.id}:literature:{work.digest[:16]}"
        notes: list[ProjectItem] = []
        selections = tuple(self.search(query))
        for index, selection in enumerate(selections[:self.max_candidates]):
            selection = LiteratureSelection.model_validate(selection.model_dump())
            source = self._read(selection, work)
            comparison = self.compare(query, source)
            comparison = LiteratureComparison.model_validate(comparison.model_dump())
            if comparison.required_statement != subject.statement:
                raise ValueError("literature comparison changed the exact required statement")
            usable = comparison.conclusion_matches and all(h.availability != "unavailable" for h in comparison.hypotheses)
            note = ProjectItem(id=f"{prefix}:comparison:{index}", kind="research_note",
                name=f"Citation comparison: {source.paper.arxiv_id} {source.statement.ref}",
                origin="generated_local", context=work.context, artifacts=source.artifacts,
                research=ResearchState(status="candidate" if usable else "unusable",
                                      reason=comparison.conclusion_reason,
                                      author=f"{self.model.provider}:{self.model.model}"),
                semantics=(("source-statement", source.statement.text),
                           ("comparison", comparison.model_dump_json()), ("model", self.model.model_dump_json()),
                           ("selection", selection.model_dump_json())))
            notes.append(note)
            if not usable:
                continue
            prepared = self.prepare(StandaloneFormalizationInput(text=source.statement.text))
            if isinstance(prepared, SemanticBlockers):
                prepared = SemanticBlockers.model_validate(prepared.model_dump())
                if any(child.item != work.item or child.context != work.context or child.scope != work.scope
                       or child.status.value != "open" for child in prepared.obligations):
                    raise ValueError("semantic prerequisite has a different subject, scope or context")
                return ResolverResult(records=tuple(notes), children=prepared.obligations,
                                      detail="Source formalization retains semantic prerequisites")
            if prepared.claim.original_text != source.statement.text:
                raise ValueError("prepared formalization changed the exact source statement")
            notes[-1] = notes[-1].model_copy(update={"semantics": (
                *notes[-1].semantics, ("frozen-claim", prepared.claim.model_dump_json()))})
            if not prepared.elaboration.success:
                return self._blocked(notes, work, prefix, "Source statement did not elaborate")
            reading = self.review(prepared.claim)
            verdict = FaithfulnessVerdict.model_validate(reading.verdict.model_dump())
            if verdict.claim_sha256 != prepared.claim.content_hash:
                raise ValueError("faithfulness review belongs to a different frozen source statement")
            notes[-1] = notes[-1].model_copy(update={
                "artifacts": (*notes[-1].artifacts, reading.artifact),
                "semantics": (*notes[-1].semantics, ("faithfulness", verdict.model_dump_json()))})
            disposition = self.admission.faithfulness(
                reached=verdict.outcome != FaithfulnessOutcome.UNAVAILABLE, agreed=verdict.agreed)
            if disposition != FaithfulnessDisposition.ACCEPT:
                return self._blocked(notes, work, prefix, f"Source faithfulness {disposition.value}")
            return self._candidate(query, prefix, notes, source, comparison, prepared, reading)
        detail = "No applicable source statement was found"
        if len(selections) > self.max_candidates:
            detail += "; candidate search limit reached"
        return ResolverResult(records=tuple(notes), detail=detail)

    def _read(self, selection: LiteratureSelection, work: Obligation) -> LiteratureSource:
        identifier = parse_id(selection.paper_id)
        if not identifier.versioned:
            raise ValueError("literature selection must name an exact paper version")
        paper = self.library.read(identifier)
        manifest = self.library.source_manifest(identifier)
        reading = survey(self.library.source_texts(identifier))
        statement, evidence, refusal = self.admission.paper_statement(paper, reading, selection.statement_ref)
        if refusal or statement is None or evidence is None:
            raise ValueError(refusal or "source statement unavailable")
        evidence = SourceEvidence(evidence.artifact, True, work.item)
        source_file = manifest.find(statement.file)
        if source_file is None:
            raise ValueError("inventoried statement file is absent from exact source manifest")
        artifacts = (evidence.artifact,
            ArtifactRef(uri=f"arxiv:{paper.arxiv_id}/source-archive", digest=manifest.archive_sha256),
            ArtifactRef(uri=f"arxiv:{paper.arxiv_id}/source/{statement.file}", digest=source_file.sha256),
            ArtifactRef(uri=f"arxiv:{paper.arxiv_id}/metadata", digest=paper.content_sha256))
        return LiteratureSource(paper, statement, evidence, artifacts)

    @staticmethod
    def _blocked(notes: list[ProjectItem], work: Obligation, prefix: str, detail: str) -> ResolverResult:
        child = Obligation(id=f"{prefix}:check", kind="check_citation", item=work.item,
                           context=work.context, scope=work.scope, reason=detail)
        return ResolverResult(records=tuple(notes), children=(child,), detail=detail)

    def _candidate(self, query: LiteratureQuery, prefix: str, notes: list[ProjectItem],
                   source: LiteratureSource, comparison: LiteratureComparison,
                   prepared: PreparedCandidate, reading: LiteratureReading) -> ResolverResult:
        work = query.obligation
        source_item = ProjectItem(
            id=f"literature:{source.paper.identifier.storage_name}:{source.evidence.artifact.digest[:16]}",
            kind="external_result", name=f"{source.paper.arxiv_id} {source.statement.ref}",
            statement=source.statement.text, origin="background_paper", artifacts=source.artifacts)
        children: list[Obligation] = []
        mappings = []
        for index, hypothesis in enumerate(comparison.hypotheses):
            if hypothesis.evidence:
                for evidence in hypothesis.evidence:
                    self._authenticate(evidence, work, outcome="kernel_proof", hypothesis=hypothesis.hypothesis)
                mappings.append(HypothesisMapping(hypothesis=hypothesis.hypothesis, evidence=hypothesis.evidence))
            else:
                child = Obligation(id=f"{prefix}:hypothesis:{index}", kind="discharge_citation_hypotheses",
                    item=work.item, context=work.context, scope=work.scope,
                    reason=f"Discharge source hypothesis: {hypothesis.hypothesis}. {hypothesis.reason}")
                children.append(child)
                mappings.append(HypothesisMapping(hypothesis=hypothesis.hypothesis, obligation=child.ref))
        source_ref = EvidenceRef(kind="literature", artifact=source.evidence.artifact,
                                 subject=work.item, producer="hardy.literature.library")
        faithful_ref = EvidenceRef(kind="faithfulness", artifact=reading.artifact,
                                   subject=work.item, producer="hardy.workflows.faithfulness")
        citation = CitationContract(id=f"{prefix}:citation", use_site=work.item, required_claim=work.item,
            paper_id=source.paper.identifier.stem, paper_version=f"v{source.paper.identifier.version}",
            source_statement=source.evidence.artifact,
            source_hypotheses=tuple(h.hypothesis for h in comparison.hypotheses),
            hypothesis_mapping=tuple(mappings), conclusion=comparison.conclusion,
            formal_declaration=prepared.claim.proposal.theorem_name, evidence=(source_ref, faithful_ref))
        if source_item.ref not in work.scope.allowed_background:
            request = AdmissionRequest(TrustRequestKind.PAPER_STATEMENT_ASSUMPTION, source_item.ref, work.scope)
            source_evidence = SourceEvidence(source.evidence.artifact, True, source_item.ref)
            decision = self.request_admission(request, source_evidence, prepared)
            notes.append(ProjectItem(id=f"{prefix}:admission-request", kind="research_note",
                name="Source assumption admission request", origin="generated_local", context=work.context,
                semantics=(("admission-request", json.dumps({"kind": request.kind.value,
                    "subject": source_item.ref.model_dump(), "scope": work.scope.model_dump(),
                    "source": source.evidence.artifact.model_dump(),
                    "refusal": decision.refusal, "checked": decision.checked})),)))
            children.append(Obligation(id=f"{prefix}:admission", kind="resolve_ambiguity", item=work.item,
                context=work.context, scope=work.scope,
                reason=f"Explicit source assumption admission required: {decision.refusal or decision.checked or 'pending'}"))
        evidence: tuple[EvidenceRef, ...] = ()
        if self.read_evidence is not None:
            for reference, outcome in ((source_ref, "source_read"), (faithful_ref, "faithful")):
                self._authenticate(reference, work, outcome=outcome, citation=citation)
            evidence = citation.evidence
        records = [*notes, citation, Relation(id=f"{prefix}:cites", kind="cites",
                    source=work.item, target=source_item.ref, artifacts=source.artifacts)]
        if any(record.id == source_item.id for record in query.snapshot.records):
            if query.snapshot.head(source_item.id) != source_item:
                raise ValueError("existing literature source identity has different content")
        else:
            records.append(source_item)
        return ResolverResult(records=tuple(records), children=tuple(children), evidence=evidence,
                              detail="Citation candidate retains explicit hypothesis and admission obligations")

    def _authenticate(self, reference: EvidenceRef, work: Obligation, *, outcome: str,
                      hypothesis: str | None = None, citation: CitationContract | None = None) -> None:
        if self.read_evidence is None:
            raise ValueError("capability evidence authentication is unavailable")
        value = self.read_evidence(reference)
        expected_kind = {"kernel_proof": "formal", "source_read": "literature", "faithful": "faithfulness"}[outcome]
        if not isinstance(value, AuthenticatedEvidence) or (
            value.reference != reference or reference.subject != work.item or reference.kind.value != expected_kind
            or value.scope != work.scope.ref or value.context != work.context or value.outcome != outcome
            or value.hypothesis != hypothesis or (citation is not None and value.citation != citation.ref)
            or not set(value.used_assumptions).issubset(work.scope.allowed_background + work.scope.allowed_interfaces)
        ):
            raise ValueError("evidence does not authenticate exact subject, scope, context, citation and hypothesis")
