"""Exact project records are claims; capability owners remain their authority.

Named readers authenticate evidence and decisions on every use, including after
restart. They must read their owner's durable records, not deserialize a caller's
claim as a successful check. Missing readers deny acceptance. This module never
runs Lean or treats semantic review, source reading, or compilation as proof.
New capability outcomes extend the explicit obligation table without changing
the append-only ledger schema. Context ancestry permits weakening assumptions
only through a distinct, proved claim; it never relabels an existing result.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hardy.foundation.values import json_digest
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceKind,
    EvidenceRef,
    MathematicalContext,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    Resolution,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import DEPENDENCIES, TRANSPORT, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot

EvidenceOutcome = Literal[
    "kernel_proof", "elaborated", "source_read", "faithful", "cas_checked",
    "document_compiled", "run_validated",
]


@dataclass(frozen=True)
class AuthenticatedEvidence:
    """Capability-reader output, bound to the exact project use it checked.

    source_read means the exact source artifact was actually read; faithful
    means an authenticated semantic review agreed, not that Lean proved it.
    hypothesis is required for a proof discharging a citation's named premise.
    """

    reference: EvidenceRef
    scope: VersionRef
    context: VersionRef | None
    outcome: EvidenceOutcome
    hypothesis: str | None = None
    citation: VersionRef | None = None
    used_assumptions: tuple[VersionRef, ...] = ()
    transport: tuple[VersionRef, ...] = ()


@dataclass(frozen=True)
class AcceptanceDecision:
    """Authenticated decision binds the unaccepted proposal's content digest."""

    proposal: VersionRef
    obligation: VersionRef
    item: VersionRef
    scope: VersionRef
    context: VersionRef | None
    policy_digest: str


@dataclass(frozen=True)
class ScopeChangeDecision:
    """Reader-confirmed user authorization and capability admission, not a flag.

    For added assumptions the reader must authenticate the admission owner's
    source/search/probe decisions as well as the exact user-approved scope.
    A UI approval alone does not satisfy this operation's contract.
    """

    before: VersionRef | None
    after: VersionRef
    policy_digest: str


# Every obligation has a deliberate minimum. Formal elaboration alone does not
# establish semantics; a source read alone does not establish applicability.
_REQUIRED = {
    ObligationKind.PROVE: {"kernel_proof"},
    ObligationKind.FORMALIZE: {"elaborated", "faithful"},
    ObligationKind.DEFINE: {"elaborated", "faithful"},
    ObligationKind.ACQUIRE_PREREQUISITE: {"source_read", "faithful"},
    ObligationKind.CHECK_CITATION: {"source_read", "faithful"},
    ObligationKind.DISCHARGE_CITATION_HYPOTHESES: {"kernel_proof"},
    ObligationKind.CONSTRUCT_INTERFACE: {"elaborated", "faithful"},
    ObligationKind.RESOLVE_REPRESENTATION: {"elaborated", "faithful"},
    ObligationKind.REFINE_REPRESENTATION: {"elaborated", "faithful"},
    ObligationKind.RESOLVE_DECLARATION: {"elaborated", "faithful"},
    ObligationKind.JUSTIFY_TRANSPORT: {"kernel_proof", "faithful"},
    ObligationKind.RESOLVE_GOAL: {"kernel_proof"},
    ObligationKind.CRITIQUE: {"faithful"},
    ObligationKind.REPAIR: {"kernel_proof", "faithful"},
    ObligationKind.REFRESH_STALE_ARTIFACT: {"elaborated", "faithful"},
    ObligationKind.CHECK_INFORMAL_STEP: {"faithful"},
    ObligationKind.RESOLVE_AMBIGUITY: {"faithful"},
}
_OUTCOMES = {
    EvidenceKind.FORMAL: {"kernel_proof", "elaborated"},
    EvidenceKind.LITERATURE: {"source_read"},
    EvidenceKind.FAITHFULNESS: {"faithful"},
    EvidenceKind.CAS: {"cas_checked"},
    EvidenceKind.DOCUMENT: {"document_compiled"},
    EvidenceKind.RUN: {"run_validated"},
}
_FACTS = {
    ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION,
    ProjectItemKind.COROLLARY, ProjectItemKind.CLAIM, ProjectItemKind.EXTERNAL_RESULT,
    ProjectItemKind.CONJECTURE,
}
_ESTABLISHES = {
    **{kind: {ObligationKind.PROVE, ObligationKind.RESOLVE_GOAL} for kind in _FACTS},
    ProjectItemKind.DEFINITION: {ObligationKind.DEFINE},
    ProjectItemKind.STANDARD_OBJECT: {ObligationKind.DEFINE, ObligationKind.RESOLVE_DECLARATION},
    ProjectItemKind.REPRESENTATION: {ObligationKind.CONSTRUCT_INTERFACE,
        ObligationKind.RESOLVE_REPRESENTATION, ObligationKind.REFINE_REPRESENTATION},
}


class LedgerPolicy:
    """Inject trusted named operations; all reader failures fail closed.

    No adapter is installed implicitly: existing capability records do not yet
    bind every ledger subject/scope/context. An application must supply readers
    which establish these bindings from independently authenticated provenance.
    """

    def __init__(
        self, *,
        read_evidence: Callable[[EvidenceRef], AuthenticatedEvidence | None] | None = None,
        read_decision: Callable[[ArtifactRef], AcceptanceDecision | None] | None = None,
        read_scope_change: Callable[[Scope | None, Scope], ScopeChangeDecision | None] | None = None,
    ) -> None:
        self._read_evidence = read_evidence
        self._read_decision = read_decision
        self._read_scope_change = read_scope_change
        # Normalize checkout line endings, but bind the actual policy code so a
        # changed rule cannot accidentally reuse an old serialized decision.
        self.digest = json_digest({"policy": "hardy.ledger.policy/v1",
                                   "source": Path(__file__).read_text(encoding="utf-8")})

    def validate(self, before: LedgerSnapshot, after: LedgerSnapshot) -> None:
        """Store validator: semantic changes and acceptance checked under lock."""
        if after.records[:len(before.records)] != before.records:
            raise ValueError("policy requires append-only history")
        added = after.records[len(before.records):]
        seen = {record.id: record for record in before.records}
        for record in added:
            previous = seen.get(record.id)
            seen[record.id] = record
            if isinstance(record, MathematicalContext) and previous is not None and record != previous:
                raise ValueError("mathematical context is immutable; extend or fork it")
            if isinstance(record, ProjectItem) and isinstance(previous, ProjectItem):
                if record.kind != previous.kind:
                    raise ValueError("item kind cannot silently change mathematical identity")
                if record.context != previous.context or record.declaration != previous.declaration:
                    raise ValueError("context/declaration change requires a distinct item and transport")
                if previous.kind == ProjectItemKind.CONJECTURE and record.statement != previous.statement:
                    raise ValueError("corrected conjecture requires a distinct superseding item")
            if isinstance(record, Scope):
                self._check_scope(after, record)
                self._authorize_scope(previous if isinstance(previous, Scope) else None, record)
            if isinstance(record, Obligation):
                self._check_scope(after, record.scope)
                if after.head(record.scope.id).ref != record.scope.ref:
                    raise ValueError("obligation carries stale scope")
                if isinstance(previous, Obligation) and (
                    previous.item != record.item or previous.kind != record.kind
                    or previous.context != record.context
                ):
                    raise ValueError("obligation subject, kind and context cannot be relabelled")
                if record.resolution and record.resolution.accepted_by:
                    self._check_resolution(after, record.resolution, frozenset())
            if isinstance(record, Resolution) and record.accepted_by:
                self._check_resolution(after, record, frozenset())
            if isinstance(record, Relation) and isinstance(previous, Relation):
                semantic = DEPENDENCIES | TRANSPORT | {RelationKind.INTERPRETS, RelationKind.REFINES}
                if ((record.kind in semantic or previous.kind in semantic)
                        and record.source.id != previous.source.id):
                    raise ValueError("semantic relation must preserve its stable source identity")
                if (record.source == previous.source and (record.kind in semantic or previous.kind in semantic)
                        and any(getattr(record, field) != getattr(previous, field)
                                for field in ("kind", "target", "justification", "mappings"))):
                    raise ValueError("semantic relation replacement requires an explicit source revision")
            if isinstance(record, Relation) and record.kind == RelationKind.INTERPRETS:
                source, target = after.get(record.source), after.get(record.target)
                if not (isinstance(source, ProjectItem) and source.kind == ProjectItemKind.REPRESENTATION
                        and isinstance(target, ProjectItem) and target.kind == ProjectItemKind.CONCEPT):
                    raise ValueError("interprets must link a representation to a distinct concept")

    def accept(self, snapshot: LedgerSnapshot, resolution: Resolution,
               decision: ArtifactRef) -> Resolution:
        """Return accepted record only after re-reading authenticated authority."""
        if resolution.accepted_by is not None:
            raise ValueError("accept expects an unaccepted proposal")
        accepted = Resolution.model_validate({**resolution.model_dump(),
                                             "accepted_by": decision, "policy_digest": self.digest})
        self._check_resolution(snapshot, accepted, frozenset())
        return accepted

    def is_accepted(self, snapshot: LedgerSnapshot, resolution: Resolution) -> bool:
        try:
            self._check_resolution(snapshot, resolution, frozenset())
        except (ValueError, OSError, TypeError):
            return False
        return True

    def premise_allowed(self, snapshot: LedgerSnapshot, item: VersionRef, *,
                        scope: Scope, context: VersionRef | None) -> bool:
        try:
            return self._premise(snapshot, item, scope, context, frozenset())
        except (ValueError, OSError, TypeError):
            return False

    def transport_accepted(self, snapshot: LedgerSnapshot, relation: Relation, *,
                           scope: Scope) -> bool:
        """Authenticate a particular mapping, not merely its named obligation.

        Formal and faithfulness readers bind relation.ref, whose digest covers
        both endpoints and every mapping. The relation pins an open obligation;
        later owner evidence can pin the relation without cyclic ledger hashes.
        """
        try:
            self._check_transport(snapshot, relation, scope, frozenset())
        except (ValueError, OSError, TypeError):
            return False
        return True

    def trust_boundary(self, snapshot: LedgerSnapshot, item: VersionRef, *,
                       scope: Scope, context: VersionRef | None) -> tuple[VersionRef, ...]:
        """Actual assumptions from authenticated proof audits; unavailable raises.

        Capability readers must report the complete nonstandard axiom set as
        used_assumptions. Merely allowing an assumption in Scope does not mean a
        proof used it. Semantic graph dependencies alone are not a kernel audit.
        """
        if not self.premise_allowed(snapshot, item, scope=scope, context=context):
            raise ValueError("authenticated premise evidence is unavailable")
        if item in scope.allowed_background + scope.allowed_interfaces:
            return (item,)
        subject = snapshot.get(item)
        if not isinstance(subject, ProjectItem):
            raise ValueError("trust boundary requires an item")
        used = set()
        for obligation in snapshot.current(Obligation):
            if (obligation.item == item and obligation.scope.ref == scope.ref
                    and obligation.kind in _ESTABLISHES.get(subject.kind, set())
                    and obligation.status == ObligationStatus.RESOLVED and obligation.resolution
                    and self.is_accepted(snapshot, obligation.resolution)):
                for reference in obligation.resolution.evidence:
                    used.update(self._evidence(reference, obligation).used_assumptions)
        return tuple(sorted(used, key=lambda ref: (ref.id, ref.digest)))

    def _authorize_scope(self, old: Scope | None, new: Scope) -> None:
        if old == new or (old is None and not new.allowed_background and not new.allowed_interfaces):
            return
        if self._read_scope_change is None:
            raise ValueError("scope change requires authenticated policy/user authorization")
        try:
            decision = self._read_scope_change(old, new)
        except Exception as error:
            raise ValueError("scope authorization reader failed") from error
        if decision != ScopeChangeDecision(old.ref if old else None, new.ref, self.digest):
            raise ValueError("scope authorization does not match exact change and policy")

    def _check_scope(self, snapshot: LedgerSnapshot, scope: Scope) -> None:
        protected = {ref.id for current in snapshot.current(Scope) for ref in current.must_prove}
        for ref in scope.allowed_background + scope.allowed_interfaces:
            value = snapshot.get(ref)
            if not isinstance(value, ProjectItem):
                raise ValueError("scope admission requires a project item")
            if ref.id in protected:
                raise ValueError("must_prove item cannot be admitted, including stale revisions")
            if value.origin == ProjectOrigin.TARGET_PAPER:
                raise ValueError("target-paper self-assumption is forbidden")
            if value.kind in {ProjectItemKind.DECLARATION, ProjectItemKind.QUESTION,
                              ProjectItemKind.CONJECTURE, ProjectItemKind.GOAL}:
                raise ValueError("local declarations and research proposals are not trusted assumptions")
            if value.kind not in _ESTABLISHES:
                raise ValueError("scope may admit results or interfaces, not concepts or research state")
            if value.context is not None:
                raise ValueError("contextual items cannot become global trusted assumptions")
            if snapshot.head(ref.id).ref != ref:
                raise ValueError("scope admission references a stale item")

    def _current_scope(self, snapshot: LedgerSnapshot, scope: Scope) -> None:
        if snapshot.get(scope.ref) != scope or snapshot.head(scope.id).ref != scope.ref:
            raise ValueError("stale or unrecorded scope")
        self._check_scope(snapshot, scope)
        history = [value for value in snapshot.records if isinstance(value, Scope) and value.id == scope.id]
        for index, value in enumerate(history):
            self._authorize_scope(history[index - 1] if index else None, value)

    def _evidence(self, reference: EvidenceRef, obligation: Obligation, *,
                  hypothesis: str | None = None) -> AuthenticatedEvidence:
        if self._read_evidence is None:
            raise ValueError("capability evidence authentication is unavailable")
        try:
            value = self._read_evidence(reference)
        except Exception as error:
            raise ValueError("capability evidence reader failed") from error
        if not isinstance(value, AuthenticatedEvidence) or (
            value.reference != reference or reference.subject != obligation.item
            or value.scope != obligation.scope.ref or value.context != obligation.context
            or value.outcome not in _OUTCOMES[reference.kind]
        ):
            raise ValueError("evidence does not authenticate exact subject, scope, context and outcome")
        if obligation.kind == ObligationKind.DISCHARGE_CITATION_HYPOTHESES and hypothesis is None:
            if not isinstance(value.hypothesis, str) or not value.hypothesis.strip():
                raise ValueError("citation discharge evidence must name its hypothesis")
        elif value.hypothesis != hypothesis:
            raise ValueError("evidence authenticates a different hypothesis")
        allowed = set(obligation.scope.allowed_background + obligation.scope.allowed_interfaces)
        if not set(value.used_assumptions).issubset(allowed):
            raise ValueError("formal evidence used assumptions outside admitted scope")
        return value

    def _check_resolution(self, snapshot: LedgerSnapshot, resolution: Resolution,
                          visiting: frozenset[VersionRef]) -> None:
        if resolution.ref in visiting:
            raise ValueError("circular acceptance dependencies")
        visiting = visiting | {resolution.ref}
        if resolution.accepted_by is None or resolution.policy_digest != self.digest:
            raise ValueError("resolution lacks current policy acceptance")
        obligation = snapshot.get(resolution.obligation)
        if not isinstance(obligation, Obligation) or resolution.item != obligation.item:
            raise ValueError("resolution does not match exact obligation subject")
        if resolution.outstanding:
            raise ValueError("resolution retains outstanding obligations")
        self._current_scope(snapshot, obligation.scope)
        subject = snapshot.get(obligation.item)
        if not isinstance(subject, ProjectItem) or subject.context != obligation.context:
            raise ValueError("obligation does not match subject context")
        proposal = Resolution.model_validate({**resolution.model_dump(),
                                             "accepted_by": None, "policy_digest": None})
        if self._read_decision is None:
            raise ValueError("acceptance decision authentication is unavailable")
        try:
            decision = self._read_decision(resolution.accepted_by)
        except Exception as error:
            raise ValueError("acceptance decision reader failed") from error
        expected = AcceptanceDecision(proposal.ref, obligation.ref, subject.ref,
                                      obligation.scope.ref, obligation.context, self.digest)
        if decision != expected:
            raise ValueError("acceptance decision does not match exact proposal and policy")
        outcomes = {self._evidence(ref, obligation).outcome for ref in resolution.evidence}
        if not _REQUIRED[obligation.kind].issubset(outcomes):
            raise ValueError("illegal evidence combination for obligation category")
        if obligation.kind in {ObligationKind.CHECK_CITATION, ObligationKind.ACQUIRE_PREREQUISITE}:
            self._citation(snapshot, obligation, visiting)
        self._transport(snapshot, subject, obligation, visiting)
        for relation in LedgerGraph(snapshot).relations:
            if relation.kind not in DEPENDENCIES or relation.source != subject.ref:
                continue
            if relation.kind == RelationKind.BLOCKED_BY and isinstance(snapshot.get(relation.target), Obligation):
                ready = self._completed(snapshot, relation.target, obligation.scope, obligation.context, visiting)
            else:
                ready = self._premise(snapshot, relation.target, obligation.scope, obligation.context, visiting)
            if not ready:
                raise ValueError("resolution depends on an unestablished premise")

    @staticmethod
    def _contexts(snapshot: LedgerSnapshot, context: VersionRef | None) -> tuple[MathematicalContext, ...]:
        values = []
        visited = set()
        while context is not None:
            if context in visited:
                raise ValueError("cyclic mathematical context")
            visited.add(context)
            value = snapshot.get(context)
            if not isinstance(value, MathematicalContext):
                raise ValueError("context reference has wrong kind")
            values.append(value)
            context = value.parent
        return tuple(values)

    def _premise(self, snapshot: LedgerSnapshot, ref: VersionRef, scope: Scope,
                 context: VersionRef | None, visiting: frozenset[VersionRef]) -> bool:
        if ref in visiting:
            return False
        visiting = visiting | {ref}
        value = snapshot.get(ref)
        if isinstance(value, ScopedBinding):
            self._current_scope(snapshot, scope)
            contexts = self._contexts(snapshot, context)
            if not any(ref in entry.bindings for entry in contexts):
                return False
            return value.target is None or self._premise(snapshot, value.target, scope, context, visiting)
        if isinstance(value, Obligation):
            subject = snapshot.get(value.item)
            if not isinstance(subject, ProjectItem) or value.kind not in _ESTABLISHES.get(subject.kind, set()):
                return False
            return self._completed(snapshot, ref, scope, context, visiting)
        if not isinstance(value, ProjectItem):
            return False
        self._current_scope(snapshot, scope)
        contexts = self._contexts(snapshot, context)
        if value.declaration is not None:
            # Local assumptions are legal only within their owning exact context.
            if not any(ref in current.declarations for current in contexts):
                return False
            if not all(self._premise(snapshot, dependency, scope, context, visiting)
                       for dependency in value.declaration.dependencies):
                return False
            if value.declaration.justification:
                return self._premise(snapshot, value.declaration.justification, scope, context, visiting)
            return True
        if value.kind not in _ESTABLISHES:
            return False
        if value.context is not None and value.context not in {entry.ref for entry in contexts}:
            return False
        if ref in scope.allowed_background + scope.allowed_interfaces:
            return True
        for obligation in snapshot.current(Obligation):
            if (obligation.item == ref and obligation.scope.ref == scope.ref
                    and obligation.kind in _ESTABLISHES[value.kind]
                    and obligation.status == ObligationStatus.RESOLVED and obligation.resolution):
                self._check_resolution(snapshot, obligation.resolution, visiting)
                return True
        return False

    def _completed(self, snapshot: LedgerSnapshot, ref: VersionRef, scope: Scope,
                   context: VersionRef | None, visiting: frozenset[VersionRef]) -> bool:
        """Completion is enough for BLOCKED_BY, but is not proof of its item."""
        value = snapshot.get(ref)
        if not isinstance(value, Obligation):
            return False
        contexts = {entry.ref for entry in self._contexts(snapshot, context)}
        if value.context is not None and value.context not in contexts:
            return False
        return self._resolved(snapshot, ref, scope, visiting)

    def _resolved(self, snapshot: LedgerSnapshot, ref: VersionRef, scope: Scope,
                  visiting: frozenset[VersionRef], *, kind: ObligationKind | None = None) -> bool:
        value = snapshot.get(ref)
        if not isinstance(value, Obligation) or (kind is not None and value.kind != kind):
            return False
        # An open exact obligation may acquire an authenticated successor. It may
        # not acquire a different subject/context/kind through that stable ID.
        current = snapshot.head(value.id)
        if not isinstance(current, Obligation) or (
            current.item != value.item or current.context != value.context
            or current.kind != value.kind or current.scope.ref != scope.ref
            or current.status != ObligationStatus.RESOLVED or current.resolution is None
        ):
            return False
        self._check_resolution(snapshot, current.resolution, visiting)
        return True

    def _transport(self, snapshot: LedgerSnapshot, subject: ProjectItem,
                   obligation: Obligation, visiting: frozenset[VersionRef]) -> None:
        if obligation.kind == ObligationKind.JUSTIFY_TRANSPORT:
            return
        contexts = self._contexts(snapshot, subject.context)
        relevant = {subject.ref} | {value.ref for value in contexts}
        relevant.update(ref for value in contexts for ref in value.declarations)
        for relation in LedgerGraph(snapshot).relations:
            if relation.kind in TRANSPORT and relation.source in relevant:
                self._check_transport(snapshot, relation, obligation.scope, visiting)

    def _check_transport(self, snapshot: LedgerSnapshot, relation: Relation, scope: Scope,
                         visiting: frozenset[VersionRef]) -> None:
        if (relation.kind not in TRANSPORT or snapshot.get(relation.ref) != relation
                or relation.justification is None or not self._resolved(
                    snapshot, relation.justification, scope, visiting,
                    kind=ObligationKind.JUSTIFY_TRANSPORT)):
            raise ValueError("transport requires resolved preservation/equivalence justification")
        justification = snapshot.head(relation.justification.id)
        if not isinstance(justification, Obligation) or justification.resolution is None:
            raise ValueError("transport justification has no authenticated resolution")
        source = snapshot.get(relation.source)
        target = snapshot.get(relation.target)
        subject = snapshot.get(justification.item)
        if isinstance(source, MathematicalContext):
            if (not isinstance(target, MathematicalContext) or not isinstance(subject, ProjectItem)
                    or subject.context != source.ref or justification.context != source.ref):
                raise ValueError("transport justification belongs to a different source context")
        elif not isinstance(source, ProjectItem) or justification.item != source.ref:
            raise ValueError("transport justification belongs to a different source subject")
        checked = [self._evidence(ref, justification) for ref in justification.resolution.evidence]
        outcomes = {value.outcome for value in checked if relation.ref in value.transport}
        if not _REQUIRED[ObligationKind.JUSTIFY_TRANSPORT].issubset(outcomes):
            raise ValueError("transport evidence does not authenticate exact endpoints and mappings")

    def _citation(self, snapshot: LedgerSnapshot, obligation: Obligation,
                  visiting: frozenset[VersionRef]) -> None:
        citations = [value for value in snapshot.current(CitationContract)
                     if value.required_claim == obligation.item]
        if not citations:
            raise ValueError("citation acceptance requires an exact citation contract")
        for citation in citations:
            checked = [self._evidence(ref, obligation) for ref in citation.evidence]
            if any(value.citation != citation.ref for value in checked):
                raise ValueError("citation evidence does not bind exact citation contract")
            if not any(value.outcome == "source_read" and value.reference.artifact == citation.source_statement
                       for value in checked) or not any(value.outcome == "faithful" for value in checked):
                raise ValueError("citation requires exact source reading and faithfulness evidence")
            mappings = {mapping.hypothesis: mapping for mapping in citation.hypothesis_mapping}
            if set(mappings) != set(citation.source_hypotheses):
                raise ValueError("citation hypotheses are incompletely mapped")
            for hypothesis, mapping in mappings.items():
                if mapping.obligation is not None:
                    if not self._resolved(snapshot, mapping.obligation, obligation.scope, visiting,
                                          kind=ObligationKind.DISCHARGE_CITATION_HYPOTHESES):
                        raise ValueError("citation hypothesis discharge is unresolved")
                    discharge = snapshot.head(mapping.obligation.id)
                    if not isinstance(discharge, Obligation) or discharge.resolution is None or (
                        discharge.item != obligation.item or discharge.context != obligation.context
                    ) or not any(
                        (checked := self._evidence(ref, discharge)).outcome == "kernel_proof"
                        and checked.hypothesis == hypothesis
                        for ref in discharge.resolution.evidence
                    ):
                        raise ValueError("citation discharge proves a different subject or hypothesis")
                elif not mapping.evidence or not any(
                    self._evidence(ref, obligation, hypothesis=hypothesis).outcome == "kernel_proof"
                    for ref in mapping.evidence
                ):
                    raise ValueError("citation hypothesis requires authenticated discharge")
