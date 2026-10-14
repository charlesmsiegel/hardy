"""Actual canonical-paper acquisition; source assumptions remain explicit pending work."""
from __future__ import annotations

from hashlib import sha256

from hardy.foundation.values import FrozenModel
from hardy.workflows.acquisition.literature import (
    LiteratureComparison,
    LiteratureReading,
    LiteratureResolver,
    LiteratureSelection,
)
from hardy.workflows.admission import CheckDecision
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict


class LiteratureSelections(FrozenModel):
    selections: tuple[LiteratureSelection, ...] = ()


def read_translation(capabilities, claim) -> LiteratureReading:
    response = capabilities.calls.structured("formalization.faithfulness", {"claim": claim.model_dump(mode="json")},
        FaithfulnessReview, system="Read the exact original source statement and frozen Lean in both directions. "
        "Report every changed hypothesis, interpretation and ambiguity. You receive no Hardy tools.")
    receipt, review = response.receipt, response.value
    verdict = FaithfulnessVerdict(claim_sha256=claim.content_hash, reviewer_model=receipt.model,
        reviewer_backend=receipt.backend or "unknown", reviewer_isolation=receipt.isolation,
        prompt_sha256=sha256(receipt.user_prompt.encode()).hexdigest(),
        response_schema_sha256=sha256(receipt.response_schema.encode()).hexdigest(),
        outcome=FaithfulnessOutcome.AGREED if review.agrees else FaithfulnessOutcome.DISPUTED, review=review)
    return LiteratureReading(verdict, capabilities.artifact("faithfulness.json", verdict.model_dump_json().encode()))


def build_literature_resolver(capabilities, model):
    def search(query):
        actual = capabilities.ports.papers.search(query.subject.statement or query.subject.name, limit=8)
        if not actual.ok:
            return ()
        selected = capabilities.calls.structured("literature.select", {"required": query.subject.model_dump(mode="json"),
            "actual_search": actual.output}, LiteratureSelections, tools=True).value
        result = []
        for selection in selected.selections[:8]:
            metadata = capabilities.ports.papers.fetch(selection.paper_id)
            source = capabilities.ports.papers.fetch_source(selection.paper_id) if metadata.ok else metadata
            if metadata.ok and source.ok:
                result.append(selection)
        return tuple(result)
    return LiteratureResolver(library=capabilities.library, model=model, search=search,
        compare=lambda query, source: capabilities.calls.structured("literature.compare", {
            "required": query.subject.model_dump(mode="json"), "source_statement": source.statement.text,
            "canonical_artifacts": [value.model_dump(mode="json") for value in source.artifacts]}, LiteratureComparison).value,
        prepare=capabilities.prepare, review=lambda claim: read_translation(capabilities, claim),
        request_admission=lambda _request, _source, _candidate: CheckDecision(
            refusal="Exact source formalization and explicit human scoped admission remain required; canonical source retained"),
        # C2's canonical arxiv provenance must not be replaced by a semantic
        # judgment receipt. Until that exact binding is installed, the existing
        # resolver retains typed citation/hypothesis/admission work and grants no
        # literature acceptance.
        read_evidence=None, policy=capabilities.owners.policy)
