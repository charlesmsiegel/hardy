"""Named journal/evidence port shared by semantic owners; no session dependency."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from hardy.foundation.journal import Journal
from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef
from hardy.workflows.ledger.policy import AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot


class SemanticOwner(Protocol):
    problem: Path
    journal: Journal
    policy: LedgerPolicy

    def _append(self, record: FrozenModel) -> None: ...
    def _find(self, record_type: type, prefix: str, artifact: ArtifactRef) -> Any: ...
    def read_evidence_at(self, snapshot: LedgerSnapshot, reference: EvidenceRef) -> AuthenticatedEvidence | None: ...
