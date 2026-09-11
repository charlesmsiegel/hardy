"""Findings are structured worker discoveries: execution provenance, never project truth.

A Finding carries a mathematical payload, exact related refs and an evidence
profile. Proposing one reaches the immediate parent and nobody else; sharing
sideways or downward is a deliberate, recorded promotion authorized by an
ancestor over both sides, and it can make a finding discoverable or push it
into a recipient's next context, which are different acts. Promotion never
changes an evidence grade, admits nothing to the ledger and resolves no
obligation. Duplicates cluster for presentation with every trajectory kept;
contradictions are preserved and answered with adjudication, never a vote.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.delegation.contracts import (
    ConcurrencyLease,
    CoordinationPolicy,
    DelegationSpec,
    ResourceLease,
)
from hardy.workflows.delegation.retrieval import VisibilityPolicy
from hardy.workflows.delegation.store import DelegationStore, DelegationTree
from hardy.workflows.ledger.contracts import Text, VersionRef


class FindingKind(str, Enum):
    CANDIDATE_LEMMA = "candidate_lemma"
    VERIFIED_LEMMA = "verified_lemma"
    REDUCTION = "reduction"
    CONSTRUCTION = "construction"
    COUNTEREXAMPLE = "counterexample"
    COMPUTATION = "computation"
    LITERATURE_LEAD = "literature_lead"
    LITERATURE_RESULT = "literature_result"
    OBSTRUCTION = "obstruction"
    FAILED_APPROACH = "failed_approach"
    STRATEGY = "strategy"
    QUESTION = "question"
    NOTE = "note"
    PROOF_SUBMISSION = "proof_submission"
    FORMALIZATION = "formalization"


class EvidenceProfile(str, Enum):
    SPECULATIVE = "speculative"
    KERNEL_PROOF = "kernel_proof"
    REPRODUCIBLE_COMPUTATION = "reproducible_computation"
    EXACT_SOURCE_SPAN = "exact_source_span"
    INDEPENDENT_REPRODUCTION = "independent_reproduction"
    HUMAN_ENDORSED = "human_endorsed"


#: Claims a counterexample on the same refs contradicts.
_CLAIMS = frozenset({FindingKind.CANDIDATE_LEMMA, FindingKind.VERIFIED_LEMMA, FindingKind.PROOF_SUBMISSION})


class Finding(FrozenModel):
    id: str
    source_delegation: str
    kind: str
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

    @property
    def subjects(self) -> frozenset[str]:
        return frozenset((*(ref.id for ref in self.related_refs), *self.related_ids))


class Visibility(str, Enum):
    PRIVATE = "worker-private"
    CELL = "cell"
    PARENT = "parent-visible"
    SELECTED = "selected"
    SWARM = "swarm"


PromotionMode = Literal["discoverable", "push", "upward"]
Selector = Literal["policy", "coordinator", "human"]


class PromotionRecord(FrozenModel):
    finding_id: str
    source: str
    recipient: str
    mode: PromotionMode
    selector: Selector
    authorized_by: str
    reason: Text
    sequence: int
    context_transition: str | None = None


class PromotionRefused(ValueError):
    """The promotion would break isolation or exceed the authorizer's reach."""


def _finding_like(identity: str) -> bool:
    return ":finding:" in identity


class FindingLedger:
    """Journal-backed: `finding.proposed`, `finding.promoted`, `context.pushes_consumed`."""

    def __init__(self, store: DelegationStore) -> None:
        self.store = store

    # -- state ------------------------------------------------------------------------

    def _state(self) -> tuple[DelegationTree, dict[str, Finding], list[PromotionRecord], dict[str, list[str]]]:
        tree = self.store.tree()
        findings: dict[str, Finding] = {}
        promotions: list[PromotionRecord] = []
        pending: dict[str, list[str]] = {}
        for event in tree.events:
            if event.kind == "finding.proposed":
                finding = Finding.model_validate(event.payload["finding"])
                findings.setdefault(finding.id, finding)
            elif event.kind == "finding.promoted":
                record = PromotionRecord.model_validate(event.payload["record"])
                promotions.append(record)
                if record.mode == "push":
                    pending.setdefault(record.recipient, []).append(record.finding_id)
            elif event.kind == "context.pushes_consumed":
                pending[event.delegation_id] = []
        return tree, findings, promotions, pending

    def get(self, finding_id: str) -> Finding:
        _, findings, _, _ = self._state()
        if finding_id not in findings:
            raise ValueError(f"unknown finding: {finding_id}")
        return findings[finding_id]

    def all(self) -> tuple[Finding, ...]:
        return tuple(self._state()[1].values())

    def promotions(self, finding_id: str) -> tuple[PromotionRecord, ...]:
        return tuple(r for r in self._state()[2] if r.finding_id == finding_id)

    # -- isolation ---------------------------------------------------------------------

    @staticmethod
    def _policy(tree: DelegationTree, id: str) -> VisibilityPolicy:
        policy = VisibilityPolicy()
        for node in (*reversed(tree.ancestors(id)), id):
            hidden = tree.get(node).spec.hidden_ids
            policy = policy.narrowed(VisibilityPolicy(
                hidden_ids=tuple(h for h in hidden if not _finding_like(h)),
                hidden_findings=tuple(h for h in hidden if _finding_like(h)),
            ))
        return policy

    def policy_for(self, id: str) -> VisibilityPolicy:
        return self._policy(self.store.tree(), id)

    # -- flow ----------------------------------------------------------------------------

    def propose(self, finding: Finding) -> Finding:
        """Upward is permissive: a proposal reaches the immediate parent and nobody else."""
        finding = Finding.model_validate(finding.model_dump())
        self.store.tree().get(finding.source_delegation)
        self.store.append(finding.source_delegation, "finding.proposed", {"finding": finding.model_dump(mode="json")})
        return finding

    def visible_to(self, id: str) -> tuple[Finding, ...]:
        tree, findings, promotions, _ = self._state()
        policy = self._policy(tree, id)
        children = set(tree.children(id))
        visible: dict[str, Finding] = {}
        for finding in findings.values():
            if finding.source_delegation == id or finding.source_delegation in children:
                visible[finding.id] = finding
        for record in promotions:
            if record.recipient == id and record.finding_id in findings:
                visible[record.finding_id] = findings[record.finding_id]
        return tuple(f for f in visible.values() if policy.permits_finding(f.id))

    def visibility(self, finding_id: str) -> Visibility:
        tree, findings, promotions, _ = self._state()
        if finding_id not in findings:
            raise ValueError(f"unknown finding: {finding_id}")
        scope = Visibility.PARENT
        for record in promotions:
            if record.finding_id != finding_id:
                continue
            if tree.get(record.recipient).parent_id is None:
                return Visibility.SWARM
            scope = Visibility.SELECTED
        return scope

    def promote(self, finding_id: str, *, recipient: str, mode: PromotionMode, selector: Selector,
                authorized_by: str, reason: str) -> PromotionRecord:
        tree, findings, promotions, _ = self._state()
        if finding_id not in findings:
            raise ValueError(f"unknown finding: {finding_id}")
        finding = findings[finding_id]
        tree.get(recipient)
        tree.get(authorized_by)
        source = finding.source_delegation

        def over(node: str, target: str) -> bool:
            return node == target or node in tree.ancestors(target)

        if mode == "upward":
            if recipient not in tree.ancestors(source):
                raise PromotionRefused("upward promotion must name an ancestor of the source")
            if not over(authorized_by, source):
                raise PromotionRefused("an upward promotion is authorized by an ancestor of the source")
        elif not (over(authorized_by, source) and over(authorized_by, recipient)):
            raise PromotionRefused("cross-branch sharing needs an ancestor authorized over both sides")
        if not self._policy(tree, recipient).permits_finding(finding_id):
            raise PromotionRefused(f"{finding_id} is hidden from {recipient}; inherited isolation dominates promotion")
        record = PromotionRecord(
            finding_id=finding_id, source=source, recipient=recipient, mode=mode, selector=selector,
            authorized_by=authorized_by, reason=reason, sequence=len(promotions),
            context_transition="next_safe_boundary" if mode == "push" else None,
        )
        self.store.append(recipient, "finding.promoted", {"record": record.model_dump(mode="json")})
        return record

    def pending_pushes(self, recipient: str) -> tuple[Finding, ...]:
        _, findings, _, pending = self._state()
        return tuple(findings[id] for id in pending.get(recipient, ()) if id in findings)

    def consume_pushes(self, recipient: str) -> tuple[Finding, ...]:
        """The pushed findings a recipient takes into its next context; recorded as consumed."""
        pushed = self.pending_pushes(recipient)
        if pushed:
            self.store.append(recipient, "context.pushes_consumed", {"findings": [f.id for f in pushed]})
        return pushed

    # -- structure ---------------------------------------------------------------------

    def clusters(self) -> tuple[tuple[Finding, ...], ...]:
        """Exact structural duplicates grouped for presentation; every finding stays its own record."""
        groups: dict[str, list[Finding]] = {}
        for finding in self.all():
            groups.setdefault(finding.structural_fingerprint, []).append(finding)
        return tuple(tuple(group) for group in groups.values())

    def contradictions(self) -> tuple[tuple[Finding, Finding], ...]:
        """(claim, counterexample) pairs over a shared subject. Preserved, never averaged away."""
        findings = self.all()
        pairs = []
        for claim in findings:
            if claim.kind not in {k.value for k in _CLAIMS}:
                continue
            for counter in findings:
                if counter.kind == FindingKind.COUNTEREXAMPLE.value and claim.subjects & counter.subjects:
                    pairs.append((claim, counter))
        return tuple(sorted(pairs, key=lambda pair: (pair[0].id, pair[1].id)))


def adjudication_spec(parent: DelegationSpec, claim: Finding, counter: Finding, *,
                      lease: ResourceLease) -> DelegationSpec:
    """A bounded adversarial delegation to settle a contradiction; never a vote."""
    refs = tuple(dict.fromkeys((*parent.project_refs, *claim.related_refs, *counter.related_refs)))
    return DelegationSpec(
        objective=(f"Adjudicate the contradiction between {claim.id} ({claim.summary}) and "
                   f"{counter.id} ({counter.summary}): establish which survives exact checking"),
        project_refs=refs, scope=parent.scope, context=parent.context, task_mode="adjudicate",
        lease=lease, concurrency=ConcurrencyLease(slots=1), coordination=CoordinationPolicy.ADVERSARIAL,
        model=parent.model, created_by="policy", notify_human=False, hidden_ids=parent.hidden_ids,
    )
