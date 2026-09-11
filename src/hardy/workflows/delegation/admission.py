"""Routing findings into ledger records through Hardy's existing owners; no second ontology.

A Finding stays execution provenance. Selected for structural persistence,
it becomes an AdmissionCandidate: ordinary ledger records built by a
mechanical routing table (a candidate lemma is a LEMMA with open PROVE and
FORMALIZE work; a counterexample is an EXAMPLE with COUNTEREXAMPLE_TO; a
failed approach is a blocked APPROACH; a literature lead is a research note).
Admission allocates identities, reuses an exact structural duplicate, clusters
a near-duplicate without identifying it, and records every finding that maps
to one object. Local admission writes a subtree overlay; authoritative
admission is a separate act with current-head verification.
"""
from __future__ import annotations

import re
from typing import Literal

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.delegation.findings import Finding
from hardy.workflows.delegation.overlay import SubtreeProjectOverlay
from hardy.workflows.ledger.contracts import (
    LedgerRecord,
    Obligation,
    ObligationKind,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot

Route = Literal[
    "candidate_lemma", "verified_proof", "new_verified_lemma", "approach", "failed_approach", "counterexample",
    "computation", "literature_result", "literature_lead", "representation", "question", "note",
]

_ROUTES: dict[str, Route] = {
    "candidate_lemma": "candidate_lemma", "reduction": "candidate_lemma", "construction": "candidate_lemma",
    "verified_lemma": "verified_proof", "proof_submission": "verified_proof",
    "strategy": "approach", "failed_approach": "failed_approach", "obstruction": "failed_approach",
    "counterexample": "counterexample", "computation": "computation",
    "literature_result": "literature_result", "literature_lead": "literature_lead",
    "question": "question", "note": "note", "formalization": "note",
}

_CLAIM_KINDS = frozenset({ProjectItemKind.LEMMA, ProjectItemKind.THEOREM, ProjectItemKind.PROPOSITION,
                          ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE})
_TOKEN = re.compile(r"[a-z0-9]+")


class AdmissionCandidate(FrozenModel):
    id: str
    finding_ids: tuple[str, ...]
    route: Route
    records: tuple[LedgerRecord, ...]
    target: Literal["local", "authoritative"]
    base_revision: int
    change_set: str | None = None
    subject: VersionRef | None = None


class AdmissionOutcome(FrozenModel):
    candidate_id: str
    proposal_refs: tuple[str, ...]
    action: Literal["created", "reused_existing", "revised", "linked", "resolved_obligation", "kept_local",
                    "rejected", "conflicted"]
    authoritative_refs: tuple[VersionRef, ...] = ()
    identity_map: tuple[tuple[str, str], ...] = ()
    near_duplicates: tuple[VersionRef, ...] = ()
    reasons: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


# -- routing ---------------------------------------------------------------------------

def _normalize(text: str | None) -> str:
    return " ".join((text or "").split())


def _target(snapshot: LedgerSnapshot, finding: Finding) -> ProjectItem | None:
    for ref in finding.related_refs:
        try:
            record = snapshot.get(ref)
        except ValueError:
            continue
        if isinstance(record, ProjectItem):
            return record
    for identity in finding.related_ids:
        try:
            record = snapshot.head(identity)
        except ValueError:
            continue
        if isinstance(record, ProjectItem):
            return record
    return None


def route_finding(finding: Finding, snapshot: LedgerSnapshot, *, scope: VersionRef, delegation_id: str,
                  change_set: str | None = None) -> AdmissionCandidate:
    """The mechanical routing table of spec section 15.2; identities are allocated per delegation."""
    route = _ROUTES.get(finding.kind, "note")
    policy = snapshot.get(scope)
    if not isinstance(policy, Scope):
        raise ValueError("admission requires a stored trust scope")
    target = _target(snapshot, finding)
    context = target.context if target is not None else snapshot.active_context
    base = f"{delegation_id}:{finding.sequence}"
    name = finding.summary[:80]
    records: list[LedgerRecord] = []
    subject: VersionRef | None = None
    if route == "verified_proof":
        # Evidence must bind an authoritative exact subject; nothing is minted locally.
        return AdmissionCandidate(id=f"{base}:candidate", finding_ids=(finding.id,), route=route, records=(),
                                  target="authoritative", base_revision=snapshot.revision, change_set=change_set,
                                  subject=target.ref if target is not None else None)
    if route == "candidate_lemma":
        item = ProjectItem(id=f"{base}:lemma", kind=ProjectItemKind.LEMMA, name=name, statement=finding.payload or name,
                           origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        records.append(Obligation(id=f"{base}:prove", item=item.ref, kind=ObligationKind.PROVE, scope=policy,
                                  context=context))
        records.append(Obligation(id=f"{base}:formalize", item=item.ref, kind=ObligationKind.FORMALIZE, scope=policy,
                                  context=context))
        if target is not None:
            records.append(Relation(id=f"{base}:supports", kind=RelationKind.SUPPORTS, source=item.ref, target=target.ref))
        subject = item.ref
    elif route == "counterexample":
        item = ProjectItem(id=f"{base}:example", kind=ProjectItemKind.EXAMPLE, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        if target is not None:
            records.append(Relation(id=f"{base}:counterexample", kind=RelationKind.COUNTEREXAMPLE_TO,
                                    source=item.ref, target=target.ref))
        subject = item.ref
    elif route in {"approach", "failed_approach"}:
        blocked = route == "failed_approach"
        item = ProjectItem(id=f"{base}:approach", kind=ProjectItemKind.APPROACH, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="blocked" if blocked else "proposed",
                                                  reason=(finding.payload or name) if blocked else None,
                                                  author=finding.source_delegation))
        records.append(item)
        if target is not None:
            kind = RelationKind.PURSUES if target.kind is ProjectItemKind.GOAL else RelationKind.TARGETS
            records.append(Relation(id=f"{base}:pursues", kind=kind, source=item.ref, target=target.ref))
        subject = item.ref
    elif route == "computation":
        item = ProjectItem(id=f"{base}:computation", kind=ProjectItemKind.COMPUTATION, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        if target is not None:
            records.append(Relation(id=f"{base}:illustrates", kind=RelationKind.ILLUSTRATES, source=item.ref,
                                    target=target.ref))
        subject = item.ref
    elif route == "question":
        item = ProjectItem(id=f"{base}:question", kind=ProjectItemKind.QUESTION, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status="proposed", author=finding.source_delegation))
        records.append(item)
        subject = item.ref
    else:
        status = "lead" if route in {"literature_lead", "literature_result"} else "proposed"
        item = ProjectItem(id=f"{base}:note", kind=ProjectItemKind.RESEARCH_NOTE, name=name,
                           statement=finding.payload or name, origin=ProjectOrigin.GENERATED_LOCAL, context=context,
                           research=ResearchState(status=status, author=finding.source_delegation),
                           semantics=(("finding", finding.id), ("finding_kind", finding.kind)))
        records.append(item)
        subject = item.ref
    return AdmissionCandidate(id=f"{base}:candidate", finding_ids=(finding.id,), route=route, records=tuple(records),
                              target="local", base_revision=snapshot.revision, change_set=change_set, subject=subject)


# -- identity ----------------------------------------------------------------------------

def structural_fingerprint(record: ProjectItem, snapshot: LedgerSnapshot) -> str:
    """Exact-duplicate identity: kind, normalized statement and mathematical context.

    Dependencies are relations about a claim rather than the claim, and a worker
    proposing a statement does not know the graph that will surround it; two
    records stating the same thing in the same context are one claim.
    """
    return json_digest({"kind": record.kind.value, "statement": _normalize(record.statement).casefold(),
                        "context": record.context.id if record.context else None})


def _tokens(text: str | None) -> frozenset[str]:
    return frozenset(_TOKEN.findall(_normalize(text).casefold()))


def find_duplicates(candidate: ProjectItem, snapshot: LedgerSnapshot, *, threshold: float = 0.6,
                    ) -> tuple[tuple[VersionRef, ...], tuple[VersionRef, ...]]:
    """(exact structural duplicates, semantic near-duplicates) among current heads of the same kind."""
    exact, near = [], []
    mine = structural_fingerprint(candidate, snapshot)
    tokens = _tokens(candidate.statement)
    for item in snapshot.current(ProjectItem):
        if item.id == candidate.id or item.kind is not candidate.kind:
            continue
        if structural_fingerprint(item, snapshot) == mine:
            exact.append(item.ref)
            continue
        if item.kind in _CLAIM_KINDS and tokens:
            theirs = _tokens(item.statement)
            overlap = len(tokens & theirs) / len(tokens | theirs) if tokens | theirs else 0.0
            if overlap >= threshold:
                near.append(item.ref)
    return tuple(exact), tuple(near)


# -- local admission ----------------------------------------------------------------------

class LocalAdmission:
    """Admit candidates into a subtree overlay; provenance from many findings to one record is kept."""

    def __init__(self, overlay: SubtreeProjectOverlay) -> None:
        self.overlay = overlay
        self._provenance: dict[VersionRef, list[str]] = {}

    def provenance(self, ref: VersionRef) -> tuple[str, ...]:
        return tuple(self._provenance.get(ref, ()))

    def admit(self, candidate: AdmissionCandidate) -> AdmissionOutcome:
        if candidate.target != "local":
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=("this candidate needs authoritative admission",))
        snapshot = self.overlay.effective()
        primary = next((r for r in candidate.records if isinstance(r, ProjectItem)), None)
        if primary is None:
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=("no project item to admit",))
        exact, near = find_duplicates(primary, snapshot)
        if exact:
            existing = exact[0]
            self._provenance.setdefault(existing, []).extend(candidate.finding_ids)
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids,
                                    action="reused_existing", authoritative_refs=(existing,),
                                    identity_map=((primary.id, existing.id),), near_duplicates=near)
        known = {record.id for record in snapshot.records}
        records = tuple(r for r in candidate.records if r.id not in known)
        try:
            self.overlay.admit_local(records, expected_local_revision=snapshot.revision)
        except ValueError as error:
            return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="rejected",
                                    reasons=(str(error),), near_duplicates=near)
        self._provenance.setdefault(primary.ref, []).extend(candidate.finding_ids)
        return AdmissionOutcome(candidate_id=candidate.id, proposal_refs=candidate.finding_ids, action="created",
                                authoritative_refs=(primary.ref,), near_duplicates=near)


__all__ = [
    "AdmissionCandidate", "AdmissionOutcome", "LocalAdmission", "Route", "find_duplicates", "route_finding",
    "structural_fingerprint",
]
