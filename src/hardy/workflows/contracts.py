"""Value-only contracts shared by staged providers and evidence readers."""
from __future__ import annotations

from pydantic import field_validator

from ..domain import FrozenModel

class ProofSubmission(FrozenModel):
    proof_body: str
    informal_proof: str

    @field_validator("proof_body")
    @classmethod
    def require_only_the_proof_term(cls, value: str) -> str:
        # The theorem is Hardy's to state. A submission that redeclares it is
        # rejected here rather than discovered later by the verifier.
        stripped = value.strip()
        if not stripped:
            raise ValueError("proof_body must not be empty")
        first = stripped.split(maxsplit=1)[0]
        if first in {"theorem", "lemma"}:
            raise ValueError("proof_body must not contain a theorem declaration")
        return value

