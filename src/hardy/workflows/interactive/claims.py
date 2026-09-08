"""Durable research claims and exact-revision dependency semantics.

Claims summarize evidence produced elsewhere; this module never verifies Lean.
It accepts only the already-validated staged-run manifest as proof evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from hardy.formal.contracts import FormalStatus
from hardy.foundation.values import FrozenModel
from hardy.workflows.contracts import RunManifest, RunPhase


class ClaimRole(str, Enum):
    TARGET = "target"
    LEMMA = "lemma"
    REDUCTION = "reduction"


class ClaimStatus(str, Enum):
    OPEN = "open"
    KERNEL_VERIFIED = "kernel_verified"
    VERIFIED_MODULO = "verified_modulo"
    REFUTED = "refuted"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"


class ClaimRef(FrozenModel):
    claim_id: str
    revision: int = Field(ge=1)

    @field_validator("claim_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _claim_id(value)

    def __str__(self) -> str:
        return f"{self.claim_id}@r{self.revision}"


class ClaimEvidence(FrozenModel):
    run_id: str
    frozen_claim_sha256: str
    verification_sha256: str
    assumed: tuple[str, ...] = ()
    recorded_at: datetime


class ClaimFormalization(FrozenModel):
    state: str = "absent"
    lean_declaration: str | None = None
    lean_name: str | None = None
    frozen_claim_sha256: str | None = None
    faithfulness_reviewed: bool = False


class ClaimRevision(FrozenModel):
    claim_id: str
    revision: int = Field(ge=1)
    title: str
    role: ClaimRole
    informal_statement: str
    status: ClaimStatus = ClaimStatus.OPEN
    dependencies: tuple[ClaimRef, ...] = ()
    assumption_dependencies: tuple[str, ...] = ()
    formalization: ClaimFormalization = Field(default_factory=ClaimFormalization)
    evidence: ClaimEvidence | None = None
    created_at: datetime
    provenance: str = "human"

    @field_validator("claim_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _claim_id(value)

    @field_validator("title", "informal_statement")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim title and statement must be nonempty")
        return value.strip()

    @model_validator(mode="after")
    def evidence_matches_status(self) -> ClaimRevision:
        verified = self.status in {ClaimStatus.KERNEL_VERIFIED, ClaimStatus.VERIFIED_MODULO}
        if verified != (self.evidence is not None):
            raise ValueError("a verified claim status requires proof evidence, and only one")
        if (
            self.evidence
            and self.evidence.frozen_claim_sha256 != self.formalization.frozen_claim_sha256
        ):
            raise ValueError("proof evidence refers to a different frozen statement")
        return self


class Claim(FrozenModel):
    claim_id: str
    current_revision: int = Field(ge=1)
    revisions: tuple[ClaimRevision, ...]

    @model_validator(mode="after")
    def coherent_history(self) -> Claim:
        expected = list(range(1, len(self.revisions) + 1))
        if not self.revisions or [r.revision for r in self.revisions] != expected:
            raise ValueError("claim revisions must be contiguous and start at r1")
        if any(r.claim_id != self.claim_id for r in self.revisions):
            raise ValueError("revision belongs to a different claim")
        if self.current_revision != len(self.revisions):
            raise ValueError("current_revision must identify the last immutable revision")
        return self


class ClaimLedger(FrozenModel):
    schema_version: Literal[1] = 1
    next_id: int = Field(default=1, ge=1)
    claims: tuple[Claim, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> ClaimLedger:
        ids = [c.claim_id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate claim identity")
        highest = max((int(value[1:]) for value in ids), default=0)
        if self.next_id <= highest:
            raise ValueError("next_id must be greater than every allocated claim ID")
        known = {(r.claim_id, r.revision) for c in self.claims for r in c.revisions}
        for claim in self.claims:
            for revision in claim.revisions:
                for dep in revision.dependencies:
                    if (dep.claim_id, dep.revision) not in known:
                        raise ValueError(f"dependency {dep} does not exist")
                    if dep.claim_id == revision.claim_id:
                        raise ValueError("a claim revision cannot depend on its own claim")
        graph = {
            (r.claim_id, r.revision): {(d.claim_id, d.revision) for d in r.dependencies}
            for c in self.claims
            for r in c.revisions
        }

        def visit(node, active: set, done: set) -> None:
            if node in active:
                raise ValueError("claim dependencies must be acyclic")
            if node in done:
                return
            active.add(node)
            for target in graph[node]:
                visit(target, active, done)
            active.remove(node)
            done.add(node)

        done: set = set()
        for node in graph:
            visit(node, set(), done)
        return self


def now() -> datetime:
    return datetime.now(UTC)


def _claim_id(value: str) -> str:
    if not (value.startswith("C") and value[1:].isdigit() and int(value[1:]) > 0):
        raise ValueError("claim_id must be C followed by a positive integer")
    return value


class ClaimService:
    """Validated operations over the claim ledger embedded in ``session.json``."""

    def __init__(self, state: dict[str, Any], persist, record):
        self._state, self._persist, self._record = state, persist, record

    def ledger(self) -> ClaimLedger:
        return ClaimLedger.model_validate(self._state.get("research_claims", {}))

    def _put(self, ledger: ClaimLedger, event: dict[str, Any]) -> None:
        self._state["research_claims"] = ledger.model_dump(mode="json")
        self._persist()
        self._record(event)

    def create(
        self,
        statement: str,
        *,
        title: str | None = None,
        role: ClaimRole = ClaimRole.TARGET,
        provenance: str = "human",
    ) -> ClaimRevision:
        ledger = self.ledger()
        claim_id = f"C{ledger.next_id}"
        clean = statement.strip()
        revision = ClaimRevision(
            claim_id=claim_id,
            revision=1,
            title=(title or clean[:60]),
            role=role,
            informal_statement=clean,
            created_at=now(),
            provenance=provenance,
        )
        claim = Claim(claim_id=claim_id, current_revision=1, revisions=(revision,))
        self._put(
            ledger.model_copy(
                update={"next_id": ledger.next_id + 1, "claims": (*ledger.claims, claim)}
            ),
            {"type": "claim_created", "claim": str(ClaimRef(claim_id=claim_id, revision=1))},
        )
        return revision

    def get(self, claim_id: str, revision: int | None = None) -> ClaimRevision:
        claim = next((c for c in self.ledger().claims if c.claim_id == claim_id.upper()), None)
        if claim is None:
            raise ValueError(f"unknown claim {claim_id}")
        number = revision or claim.current_revision
        return next((r for r in claim.revisions if r.revision == number), None) or (
            _raise(f"unknown revision {claim.claim_id}@r{number}")
        )

    def revise(
        self,
        claim_id: str,
        statement: str,
        *,
        title: str | None = None,
        role: ClaimRole | None = None,
        provenance: str = "human",
    ) -> ClaimRevision:
        ledger = self.ledger()
        claim = next((c for c in ledger.claims if c.claim_id == claim_id.upper()), None)
        if claim is None:
            raise ValueError(f"unknown claim {claim_id}")
        old = claim.revisions[-1]
        revision = ClaimRevision(
            claim_id=claim.claim_id,
            revision=claim.current_revision + 1,
            title=title or old.title,
            role=role or old.role,
            informal_statement=statement,
            created_at=now(),
            provenance=provenance,
        )
        replacement = claim.model_copy(
            update={
                "current_revision": revision.revision,
                "revisions": (*claim.revisions, revision),
            }
        )
        claims = tuple(replacement if c.claim_id == claim.claim_id else c for c in ledger.claims)
        self._put(
            ledger.model_copy(update={"claims": claims}),
            {
                "type": "claim_revised",
                "claim": str(ClaimRef(claim_id=claim.claim_id, revision=revision.revision)),
            },
        )
        return revision

    def add_dependency(self, owner: ClaimRef, dependency: ClaimRef) -> ClaimRevision:
        ledger = self.ledger()
        current = self.get(owner.claim_id, owner.revision)
        self.get(dependency.claim_id, dependency.revision)
        if dependency in current.dependencies:
            return current
        updated = current.model_copy(update={"dependencies": (*current.dependencies, dependency)})
        return self._replace(
            ledger,
            updated,
            {"type": "claim_dependency", "claim": str(owner), "dependency": str(dependency)},
        )

    def attach_run(self, ref: ClaimRef, manifest: RunManifest) -> ClaimRevision:
        revision = self.get(ref.claim_id, ref.revision)
        grade = manifest.grades.formal
        if manifest.phase is not RunPhase.COMPLETED or grade not in {
            FormalStatus.KERNEL_VERIFIED,
            FormalStatus.VERIFIED_MODULO,
        }:
            return revision
        evidence = manifest.grades.verification_evidence
        if (
            evidence is None
            or not manifest.claim_sha256
            or evidence.claim_sha256 != manifest.claim_sha256
        ):
            raise ValueError("the completed run does not carry matching verification evidence")
        status = (
            ClaimStatus.KERNEL_VERIFIED
            if grade is FormalStatus.KERNEL_VERIFIED
            else ClaimStatus.VERIFIED_MODULO
        )
        formalization = ClaimFormalization(
            state="approved",
            frozen_claim_sha256=manifest.claim_sha256,
            faithfulness_reviewed=bool(
                manifest.grades.faithfulness_review and manifest.grades.faithfulness_review.agreed
            ),
        )
        proof = ClaimEvidence(
            run_id=str(manifest.run_id),
            frozen_claim_sha256=manifest.claim_sha256,
            verification_sha256=evidence.digest,
            assumed=manifest.grades.assumed,
            recorded_at=now(),
        )
        updated = revision.model_copy(
            update={
                "status": status,
                "formalization": formalization,
                "evidence": proof,
                "assumption_dependencies": manifest.grades.assumed,
            }
        )
        return self._replace(
            self.ledger(),
            updated,
            {"type": "claim_evidence", "claim": str(ref), "status": status.value},
        )

    def _replace(
        self, ledger: ClaimLedger, revision: ClaimRevision, event: dict[str, Any]
    ) -> ClaimRevision:
        claims = []
        for claim in ledger.claims:
            if claim.claim_id != revision.claim_id:
                claims.append(claim)
                continue
            revisions = tuple(
                revision if r.revision == revision.revision else r for r in claim.revisions
            )
            claims.append(claim.model_copy(update={"revisions": revisions}))
        checked = ClaimLedger.model_validate(
            ledger.model_copy(update={"claims": tuple(claims)}).model_dump()
        )
        self._put(checked, event)
        return revision

    def render_list(self) -> str:
        rows = [
            f"{c.claim_id}@r{c.current_revision}  {c.revisions[-1].title}  {_label(c.revisions[-1].status)}"
            for c in self.ledger().claims
        ]
        return "Claims\n" + ("\n".join(rows) if rows else "  No claims recorded.")

    def render_claim(self, claim_id: str) -> str:
        r = self.get(claim_id)
        ledger = self.ledger()
        used = [
            str(ClaimRef(claim_id=x.claim_id, revision=y.revision))
            for x in ledger.claims
            for y in x.revisions
            if ClaimRef(claim_id=r.claim_id, revision=r.revision) in y.dependencies
        ]
        deps = [f"  {d}  {_label(self.get(d.claim_id, d.revision).status)}" for d in r.dependencies]
        deps += [f"  {a}  APPROVED" for a in r.assumption_dependencies]
        history = [
            f"  r{x.revision}{' current' if x.revision == r.revision else ''}  {_label(x.status)}"
            for x in next(c for c in ledger.claims if c.claim_id == r.claim_id).revisions
        ]
        return "\n".join(
            [
                f"{r.claim_id} — {r.title}",
                f"current revision: r{r.revision}",
                f"role: {r.role.value}",
                f"status: {r.status.value}",
                "",
                "informal statement:",
                r.informal_statement,
                "",
                "formalization:",
                f"  {r.formalization.state}",
                "",
                "depends on:",
                *(deps or ["  none"]),
                "",
                "used by:",
                *([f"  {x}" for x in used] or ["  none"]),
                "",
                "revision history:",
                *history,
            ]
        )

    def render_frontier(self) -> str:
        lines = ["Research frontier"]
        unresolved = []
        for claim in self.ledger().claims:
            r = claim.revisions[-1]
            lines.append(f"{r.claim_id}@r{r.revision}  {r.title}  {_label(r.status)}")
            for dep in r.dependencies:
                target = self.get(dep.claim_id, dep.revision)
                lines.append(f"  ├── {dep}  {target.title}  {_label(target.status)}")
                if target.status is ClaimStatus.OPEN:
                    unresolved.append(str(dep))
            for assumption in r.assumption_dependencies:
                lines.append(f"  └── {assumption}  APPROVED ASSUMPTION")
        lines += (
            ["", "Current unresolved dependencies:", *(f"  {x}" for x in sorted(set(unresolved)))]
            if unresolved
            else ["", "No unresolved claim dependencies."]
        )
        return "\n".join(lines)


def _raise(message: str):
    raise ValueError(message)


def _label(status: ClaimStatus) -> str:
    return {
        ClaimStatus.KERNEL_VERIFIED: "VERIFIED",
        ClaimStatus.VERIFIED_MODULO: "VERIFIED-MODULO",
    }.get(status, status.value.upper())
