"""Immutable records and requests shared by live application workflow adapters."""
from __future__ import annotations

from typing import Any, Literal

from hardy.formal.contracts import DeclaredAssumption, FrozenClaim
from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef, StableId, Text, VersionRef


class ExploreOperationRequest(FrozenModel):
    id: StableId
    action: Literal["context", "concept", "representation"]
    text: Text
    context: VersionRef | None = None
    scope: VersionRef | None = None
    concept: VersionRef | None = None
    use_site: VersionRef | None = None


class RepairOperationRequest(FrozenModel):
    id: StableId
    obligation: VersionRef
    original_argument: VersionRef


class ModelCallReceipt(FrozenModel):
    id: Text
    operation: Text
    stage: Text
    context_id: Text
    model: Text
    backend: str | None
    endpoint: str | None
    configuration: dict[str, Any]
    system_prompt: str
    user_prompt: str
    response_schema: str
    response: str
    isolation: str | None
    tools: tuple[str, ...]
    status: Literal["completed", "failed", "unavailable", "cancelled"]
    detail: str
    elapsed: float
    usage: dict[str, Any]
    recorded_at: Text
    teardown_pending: bool = False

    def model_dump(self, **kwargs):
        result = super().model_dump(**kwargs)
        if not self.teardown_pending:
            result.pop("teardown_pending", None)
        return result


class WorkflowAttempt(FrozenModel):
    id: Text
    attempt: Text
    request_id: StableId
    kind: Literal["research", "explore", "repair"]
    request_json: Text
    status: Literal["running", "completed", "pending", "failed", "blocked", "cancelled", "interrupted"]
    detail: str = ""
    result: dict[str, Any] = {}
    elapsed: float = 0.0
    recorded_at: Text


class LedgerEffects(FrozenModel):
    id: Text
    before_revision: int
    after_revision: int
    before: tuple[VersionRef, ...]
    added: tuple[VersionRef, ...]


class FrozenFormalEvidence(FrozenModel):
    id: Text
    subject: VersionRef
    scope: VersionRef
    context: VersionRef | None
    claim: FrozenClaim
    formalization: VersionRef
    relation: VersionRef
    inputs: tuple[VersionRef, ...]
    outcome: Literal["elaborated", "kernel_proof"]
    source: ArtifactRef
    result: ArtifactRef
    environment: str
    axioms: tuple[str, ...] = ()
    declared: tuple[DeclaredAssumption, ...] = ()
    assumption_refs: tuple[VersionRef, ...] = ()
    used_assumptions: tuple[VersionRef, ...] = ()
    saved: VersionRef | None = None
    saved_inputs: tuple[tuple[str, str], ...] = ()
    signature: str | None = None
    reading: EvidenceRef | None = None
    retained_inputs: tuple[tuple[str, ArtifactRef], ...] = ()
    environment_artifacts: tuple[ArtifactRef, ...] = ()
    configuration: dict[str, Any] = {}
    recorded_at: Text


LIVE_RECORDS = (ModelCallReceipt, WorkflowAttempt, LedgerEffects, FrozenFormalEvidence)
