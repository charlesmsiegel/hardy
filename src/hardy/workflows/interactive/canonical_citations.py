"""Authenticate canonical C2 source, exact theorem and applicability receipts."""
from __future__ import annotations

from hashlib import sha256

from hardy.formal.contracts import FrozenClaim
from hardy.foundation.files import read_bytes
from hardy.literature.metadata import ArxivError, parse_id
from hardy.literature.statements import survey
from hardy.workflows.acquisition.literature import LiteratureComparison
from hardy.workflows.contracts import FaithfulnessVerdict
from hardy.workflows.interactive.manuscript_records import CanonicalCitationReading
from hardy.workflows.interactive.semantic_versions import relevant_versions
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    ProjectItem,
    Scope,
)
from hardy.workflows.ledger.policy import AuthenticatedEvidence


class CanonicalCitationOwner:
    def __init__(self, owners, *, library=None):
        self.owners, self.library = owners, library

    def _read_file(self, source):
        artifact = source.artifact
        if not artifact.uri.startswith("workflow-artifact:"):
            raise ValueError("canonical source is not a retained capability artifact")
        data = read_bytes(self.owners.problem, artifact.uri.removeprefix("workflow-artifact:"))
        if sha256(data).hexdigest() != artifact.digest or artifact.digest != source.digest:
            raise ValueError("retained canonical source bytes changed")
        return data.decode("utf-8")

    def _validate(self, snapshot, record):
        contract = snapshot.get(record.contract)
        source = snapshot.get(record.source_item)
        scope = snapshot.get(record.scope)
        if (not isinstance(contract, CitationContract) or snapshot.head(contract.id).ref != contract.ref
                or not isinstance(source, ProjectItem) or source.kind != "external_result"
                or not isinstance(scope, Scope) or contract.required_claim != record.subject
                or contract.source_statement not in source.artifacts):
            raise ValueError("canonical citation binding refers to stale or foreign source/use")
        self.owners.policy._current_scope(snapshot, scope)
        inputs = relevant_versions(self.owners.problem, snapshot, record.subject,
            (record.scope, record.contract, record.source_item))
        if inputs != record.inputs:
            raise ValueError("canonical citation source/context/contract versions changed")
        subject = snapshot.get(record.subject)
        if subject.context != record.context:
            raise ValueError("canonical citation context changed")
        prefix = f"arxiv:{contract.paper_id}{contract.paper_version}/source/"
        pinned = {artifact.uri.removeprefix(prefix): artifact for artifact in source.artifacts if artifact.uri.startswith(prefix)}
        retained = {value.path: self._read_file(value) for value in record.files}
        if (set(retained) != set(pinned) or any(sha256(text.encode()).hexdigest() != pinned[path].digest
                for path, text in retained.items())):
            raise ValueError("retained canonical source differs from its actual C2 source files")
        source_reading = retained
        if self.library is not None:
            identifier = parse_id(contract.paper_id + contract.paper_version)
            paper = self.library.read(identifier)
            actual = self.library.source_texts(identifier)
            source_reading = actual
            if any(actual.get(path) != text for path, text in retained.items()):
                raise ValueError("canonical library source changed or is unavailable")
            if not any(artifact.uri == f"arxiv:{paper.arxiv_id}/metadata" and artifact.digest == paper.content_sha256
                       for artifact in source.artifacts):
                raise ValueError("canonical paper metadata differs from the acquired source")
        excerpt = contract.source_statement
        if sha256((source.statement or "").encode()).hexdigest() != excerpt.digest:
            raise ValueError("citation source statement digest changed")
        filename, separator, label = (excerpt.locator or "").partition("#")
        recovered = [value for value in survey(source_reading).statements if value.file == filename and value.ref == label]
        if not separator or len(recovered) != 1 or recovered[0].text != source.statement:
            raise ValueError("actual retained source does not reproduce the exact cited theorem")
        frozen = FrozenClaim.model_validate(record.frozen_claim)
        verdict = FaithfulnessVerdict.model_validate(record.faithfulness)
        comparison = LiteratureComparison.model_validate(record.comparison)
        faithful_artifact = ArtifactRef.model_validate(record.faithfulness_artifact)
        if (frozen.original_text != source.statement or verdict.claim_sha256 != frozen.content_hash or not verdict.agreed
                or not comparison.conclusion_matches or comparison.required_statement != subject.statement
                or comparison.conclusion != contract.conclusion
                or tuple(value.hypothesis for value in comparison.hypotheses) != contract.source_hypotheses
                or any(value.availability == "unavailable" for value in comparison.hypotheses)
                or not any(value.artifact == faithful_artifact for value in contract.evidence)):
            raise ValueError("citation comparison or exact-source translation does not support this contract")
        if not faithful_artifact.uri.startswith("workflow-artifact:"):
            raise ValueError("citation translation verdict is not a retained capability artifact")
        faithful_bytes = read_bytes(self.owners.problem, faithful_artifact.uri.removeprefix("workflow-artifact:"))
        if (sha256(faithful_bytes).hexdigest() != faithful_artifact.digest
                or FaithfulnessVerdict.model_validate_json(faithful_bytes) != verdict):
            raise ValueError("citation faithfulness artifact differs from the actual verdict")
        from hardy.workflows.interactive.judgments import SemanticJudgment
        human = [value for value in self.owners.journal.read().of(SemanticJudgment)
            if value.subject == record.subject and value.scope == record.scope and value.actor != "model"
            and value.purpose == "citation" and (value.citation in {None, contract.ref})
            and (value.citation_use is None or value.citation_use.matches(contract))]
        if human and human[-1].verdict != "approved":
            raise ValueError("explicit human citation decision does not approve this use")
        return contract

    def read(self, snapshot, reference: EvidenceRef):
        if reference.producer not in {"hardy.literature.library", "hardy.workflows.faithfulness"}:
            return None
        candidates = []
        for record in self.owners.journal.read().of(CanonicalCitationReading):
            try:
                contract = snapshot.get(record.contract)
                if reference in contract.evidence and reference.subject == record.subject:
                    self._validate(snapshot, record)
                    candidates.append(record)
            except (ValueError, OSError, ArxivError):
                continue
        if not candidates or len({value.contract for value in candidates}) != 1:
            return None
        record = candidates[-1]
        try:
            contract = self._validate(snapshot, record)
            kind = "source_read" if reference.producer == "hardy.literature.library" else "faithful"
            if ((kind == "source_read" and (reference.kind != "literature" or reference.artifact != contract.source_statement))
                    or (kind == "faithful" and reference.kind != "faithfulness")):
                return None
        except (ValueError, OSError, ArxivError):
            return None
        return AuthenticatedEvidence(reference, record.scope, record.context, kind, citation=record.contract,
            obligation_kinds=("check_citation", "acquire_prerequisite"))
