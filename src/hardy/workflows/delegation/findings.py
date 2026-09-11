"""Findings are structured worker discoveries: execution provenance, never project truth.

A Finding carries a mathematical payload, exact related refs and an evidence
profile. Proposing one changes no ledger record, resolves no obligation and
grades nothing; admission is a separate, later act through Hardy's owners.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import Text, VersionRef


class EvidenceProfile(str, Enum):
    SPECULATIVE = "speculative"
    KERNEL_PROOF = "kernel_proof"
    REPRODUCIBLE_COMPUTATION = "reproducible_computation"
    EXACT_SOURCE_SPAN = "exact_source_span"
    INDEPENDENT_REPRODUCTION = "independent_reproduction"
    HUMAN_ENDORSED = "human_endorsed"


class Finding(FrozenModel):
    id: str
    source_delegation: str
    kind: Text
    summary: Text
    payload: str = ""
    related_refs: tuple[VersionRef, ...] = ()
    related_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    evidence_profile: EvidenceProfile = EvidenceProfile.SPECULATIVE
    assumptions: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)
    sequence: int = Field(ge=0, strict=True)

    @property
    def structural_fingerprint(self) -> str:
        """Exact-duplicate identity: kind, normalized payload and exact related refs."""
        return json_digest({
            "kind": self.kind,
            "payload": " ".join(self.payload.split()),
            "related_refs": [ref.model_dump(mode="json") for ref in self.related_refs],
            "related_ids": list(self.related_ids),
        })
