"""Model-assessed representation choices with exact persistent use links.

Search proposes realizations; a structured model decision names the weakest
adequate interpretation and explains assumptions. Neither choice nor an optional
materialization artifact is semantic or kernel authority. New structure gets a
new representation identity, preserving every earlier use. The ledger revision
is checked after external operations so a stale decision cannot partly commit.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from hardy.foundation.values import FrozenModel
from hardy.workflows.formalization import SemanticRequirement
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    LedgerRecord,
    MathematicalContext,
    Obligation,
    ObligationKind,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Scope,
    StableId,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


class RepresentationRequest(FrozenModel):
    id: StableId
    concept: VersionRef
    intended_use: Text
    scope: VersionRef
    use_site: VersionRef | None = None
    context: VersionRef | None = None


class RepresentationModel(FrozenModel):
    """Caller-recorded provider/model/configuration that produced the decision."""
    provider: Text
    model: Text
    configuration: tuple[tuple[Text, Text], ...]


class SearchHit(FrozenModel):
    name: Text
    description: Text
    artifact: ArtifactRef | None = None


class RepresentationPlan(FrozenModel):
    id: StableId
    name: Text
    description: Text


class RepresentationDecision(FrozenModel):
    action: Literal["reuse", "plan", "refine"]
    reason: Text
    selected: VersionRef | None = None
    plan: RepresentationPlan | None = None
    assumptions: tuple[Text, ...] = ()
    interpretations: tuple[Text, ...] = ()
    requirements: tuple[SemanticRequirement, ...] = ()

    @model_validator(mode="after")
    def check_choice(self) -> Self:
        if (self.selected is not None) != (self.action in {"reuse", "refine"}):
            raise ValueError("reuse/refine must select an exact known representation")
        if (self.plan is not None) != (self.action in {"plan", "refine"}):
            raise ValueError("plan/refine must provide a new representation plan")
        return self


class RepresentationMaterialization(FrozenModel):
    """Recorded candidate artifacts/prerequisites, with no acceptance field."""
    artifacts: tuple[ArtifactRef, ...] = ()
    requirements: tuple[SemanticRequirement, ...] = ()


@dataclass(frozen=True)
class RepresentationQuery:
    concept: ProjectItem
    intended_use: str
    use_site: ProjectItem | None
    context: MathematicalContext | None
    snapshot: LedgerSnapshot


@dataclass(frozen=True)
class RepresentationOptions:
    query: RepresentationQuery
    known: tuple[ProjectItem, ...]
    local: tuple[SearchHit, ...]
    mathlib: tuple[SearchHit, ...]


@dataclass(frozen=True)
class RepresentationSelection:
    query: RepresentationQuery
    representation: ProjectItem
    decision: RepresentationDecision


@dataclass(frozen=True)
class RepresentationResult:
    representation: ProjectItem
    assessment: ProjectItem
    outstanding: tuple[Obligation, ...]
    materialization: RepresentationMaterialization | None = None


class RepresentationResolver:
    def __init__(self, store: LedgerStore, *, model: RepresentationModel,
                 search_local: Callable[[RepresentationQuery], tuple[SearchHit, ...]],
                 search_mathlib: Callable[[RepresentationQuery], tuple[SearchHit, ...]],
                 decide: Callable[[RepresentationOptions], RepresentationDecision],
                 materialize: Callable[[RepresentationSelection], RepresentationMaterialization] | None = None):
        self.store = store
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.search_local = search_local
        self.search_mathlib = search_mathlib
        self.decide = decide
        self.materialize = materialize

    def resolve(self, request: RepresentationRequest) -> RepresentationResult:
        request = RepresentationRequest.model_validate(request.model_dump())
        snapshot = self.store.read()
        concept = snapshot.get(request.concept)
        scope = snapshot.get(request.scope)
        use_site = snapshot.get(request.use_site) if request.use_site else None
        if not isinstance(concept, ProjectItem) or concept.kind != ProjectItemKind.CONCEPT:
            raise ValueError("representation request must name a concept")
        if not isinstance(scope, Scope):
            raise ValueError("representation request must name a Scope")
        if use_site is not None and not isinstance(use_site, ProjectItem):
            raise ValueError("use site must be a project item")
        for record in (concept, scope, use_site):
            if record is not None and snapshot.head(record.id).ref != record.ref:
                raise ValueError("stale representation request")
        context_ref = request.context or (use_site.context if use_site else None)
        if use_site is not None and request.context is not None and use_site.context != request.context:
            raise ValueError("use site context differs from requested context")
        context = snapshot.get(context_ref) if context_ref else None
        if context is not None and not isinstance(context, MathematicalContext):
            raise ValueError("representation context must be a mathematical context")
        allowed_contexts = {c.ref for c in LedgerGraph(snapshot).context_chain(context_ref)} if context_ref else set()
        relations = snapshot.current(Relation)
        known_refs = {r.source for r in relations if r.kind == RelationKind.INTERPRETS and r.target == concept.ref}
        known = tuple(item for item in snapshot.current(ProjectItem)
                      if item.kind == ProjectItemKind.REPRESENTATION and item.ref in known_refs
                      and (item.context is None or item.context in allowed_contexts))
        query = RepresentationQuery(concept, request.intended_use, use_site, context, snapshot)
        local = tuple(SearchHit.model_validate(hit.model_dump()) for hit in self.search_local(query))
        mathlib = tuple(SearchHit.model_validate(hit.model_dump()) for hit in self.search_mathlib(query))
        decision = self.decide(RepresentationOptions(query, known, local, mathlib))
        decision = RepresentationDecision.model_validate(decision.model_dump())
        if decision.selected is not None and decision.selected not in {item.ref for item in known}:
            raise ValueError("selected representation is not a known exact interpretation")
        records: list[LedgerRecord] = []
        if decision.plan is not None:
            if any(record.id == decision.plan.id for record in snapshot.records):
                raise ValueError("new representation plan requires a new stable identity")
            representation = ProjectItem(id=decision.plan.id, kind=ProjectItemKind.REPRESENTATION,
                name=decision.plan.name, statement=decision.plan.description, origin=ProjectOrigin.GENERATED_LOCAL,
                context=context_ref, research=ResearchState(status="planned", reason=decision.reason),
                semantics=tuple(("assumption", value) for value in decision.assumptions))
            records.extend((representation, Relation(id=f"{request.id}:interprets", kind=RelationKind.INTERPRETS,
                                                     source=representation.ref, target=concept.ref)))
            if decision.selected is not None:
                records.append(Relation(id=f"{request.id}:refines", kind=RelationKind.REFINES,
                                        source=representation.ref, target=decision.selected))
        else:
            representation = next(item for item in known if item.ref == decision.selected)
            if decision.assumptions:
                raise ValueError("additional assumptions require an explicit refinement plan")
        # An existing exact use cannot acquire another interpretation silently.
        # The caller revises the use item (or forks it) and submits that new ref.
        historical = tuple(record for record in snapshot.records if isinstance(record, Relation))
        interpreted_refs = {r.source for r in historical if r.kind == RelationKind.INTERPRETS
                            and r.target.id == concept.id}
        if use_site is not None and any(r.kind == RelationKind.USES and r.source == use_site.ref
                                       and r.target in interpreted_refs and r.target != representation.ref for r in historical):
            raise ValueError("revise the use site before changing its representation interpretation")
        materialization = None
        if self.materialize is not None:
            materialization = self.materialize(RepresentationSelection(query, representation, decision))
            materialization = RepresentationMaterialization.model_validate(materialization.model_dump())
        assessment = ProjectItem(id=f"{request.id}:assessment", kind=ProjectItemKind.RESEARCH_NOTE,
            name=f"Representation choice for {concept.name}", statement=request.intended_use,
            origin=ProjectOrigin.GENERATED_LOCAL, context=context_ref,
            research=ResearchState(status="proposed", reason=decision.reason,
                                   author=f"{self.model.provider}:{self.model.model}"),
            artifacts=materialization.artifacts if materialization else (),
            semantics=(("decision", decision.model_dump_json()),
                       ("model", self.model.model_dump_json()),
                       ("request", request.model_dump_json()),
                       *( ("local-search", hit.model_dump_json()) for hit in local),
                       *( ("mathlib-search", hit.model_dump_json()) for hit in mathlib)))
        records.extend((assessment,
            Relation(id=f"{request.id}:assessment-uses", kind=RelationKind.USES,
                     source=assessment.ref, target=representation.ref)))
        if use_site is not None:
            records.append(Relation(id=f"{request.id}:uses", kind=RelationKind.USES,
                                    source=use_site.ref, target=representation.ref))
        # Carrier/interpretation prerequisites belong to this use. Attaching a
        # local requirement to a global representation would mismatch its scope.
        prerequisite_subject = use_site or assessment
        outstanding = [o for o in snapshot.current(Obligation)
                       if o.item in {representation.ref, prerequisite_subject.ref}]
        pending: list[tuple[ProjectItem, str, str]] = [
            (prerequisite_subject, req.kind, req.reason) for req in decision.requirements]
        if decision.plan is not None:
            pending.insert(0, (representation, "construct_interface", "Materialize the proposed representation interface"))
        if materialization:
            pending.extend((prerequisite_subject, req.kind, req.reason) for req in materialization.requirements)
        for index, (subject, kind, reason) in enumerate(pending):
            obligation = Obligation(id=f"{request.id}:prerequisite:{index}", item=subject.ref,
                                    kind=ObligationKind(kind), scope=scope, context=subject.context, reason=reason)
            outstanding.append(obligation)
            records.append(obligation)
        self.store.append(tuple(records), expected_revision=snapshot.revision)
        return RepresentationResult(representation, assessment, tuple(outstanding), materialization)
