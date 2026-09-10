"""Explore records semantic progress and delegates interpretation to shared owners.

Theory: a conversation produces exact ledger items and immutable context branches;
representation changes create new uses rather than rewriting what earlier claims meant.
Reused: B0 transactions, B1 dependency queries, B4 representation resolution, B5 contexts.
Model judgments remain proposals; this owner grants neither trust nor verification.
The next semantic input can add declarations or research relations to the same ledger.
Assumes: adapters supply semantic dependencies; prose interpretation is not certified.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hardy.foundation.values import FrozenModel
from hardy.workflows.context import BindingSpec, ContextManager, ContextTransport, DeclarationSpec
from hardy.workflows.formalization import (
    ContextualFormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    resolve_input,
)
from hardy.workflows.ledger.contracts import (
    EvidenceRef,
    LedgerRecord,
    MathematicalContext,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.graph import DEPENDENCIES, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews, ResearchView
from hardy.workflows.representation import (
    RepresentationModel,
    RepresentationRequest,
    RepresentationResolver,
    RepresentationResult,
)


@dataclass(frozen=True)
class RevisedUse:
    item: ProjectItem
    affected: tuple[VersionRef, ...]


@dataclass(frozen=True)
class Inquiry:
    question: ProjectItem
    conjecture: ProjectItem
    goal: ProjectItem


class ContextPlan(FrozenModel):
    reason: Text
    declarations: tuple[DeclarationSpec, ...] = ()
    bindings: tuple[BindingSpec, ...] = ()


@dataclass(frozen=True)
class ContextInterpretation:
    text: str
    parent: VersionRef
    snapshot: LedgerSnapshot


class ExploreWorkflow:
    def __init__(self, store: LedgerStore, *,
                 representations: RepresentationResolver | None = None,
                 contexts: ContextManager | None = None):
        self.store = store
        self.representations = representations
        self.contexts = contexts or ContextManager(store)
        if self.contexts.store.project != store.project or (
            representations is not None and representations.store.project != store.project
        ):
            raise ValueError("Explore collaborators must use the same project ledger")

    def record_item(self, *, id: str, kind: ProjectItemKind, name: str,
                    statement: str | None = None, dependencies: tuple[VersionRef, ...] = (),
                    origin: ProjectOrigin = ProjectOrigin.HUMAN_AUTHORED) -> ProjectItem:
        snapshot = self.store.read()
        if any(record.id == id for record in snapshot.records):
            raise ValueError("new Explore item requires a new identity")
        item = ProjectItem(id=id, kind=kind, name=name, statement=statement,
                           context=snapshot.active_context, origin=origin)
        relations = tuple(Relation(id=f"{id}:dependency:{index}", kind=RelationKind.DEPENDS_ON,
                                   source=item.ref, target=ref) for index, ref in enumerate(dependencies))
        self.store.append((item, *relations), expected_revision=snapshot.revision)
        return item

    def represent(self, request: RepresentationRequest) -> RepresentationResult:
        if self.representations is None:
            raise ValueError("Explore representation resolver is not configured")
        return self.representations.resolve(request)

    def revise_use(self, subject: VersionRef, *, statement: str, reason: str) -> RevisedUse:
        """Preserve history and dependencies; interpretation must be selected anew.

        The returned exact dependents need reconsideration; their historical records
        remain readable. B3 views report stale exact dependencies without stamping
        unrelated project items with an invented invalidation status.
        """
        snapshot = self.store.read()
        original = snapshot.get(subject)
        if not isinstance(original, ProjectItem) or snapshot.head(subject.id).ref != subject:
            raise ValueError("revise_use requires a current exact project item")
        if original.kind in {ProjectItemKind.DECLARATION, ProjectItemKind.REPRESENTATION}:
            raise ValueError("declarations and representations require their shared owners")
        if original.kind == ProjectItemKind.CONJECTURE:
            raise ValueError("corrected conjecture requires a distinct superseding item")
        if not reason.strip():
            raise ValueError("revision requires a reason")
        graph = LedgerGraph(snapshot)
        changed = ProjectItem.model_validate({**original.model_dump(), "statement": statement,
            "artifacts": (), "evidence": (),
            "semantics": (*original.semantics, ("revision_reason", reason))})
        retained = tuple(Relation.model_validate({**r.model_dump(), "source": changed.ref})
            for r in graph.relations if r.source == original.ref and r.kind in DEPENDENCIES
            and not (isinstance(target := snapshot.get(r.target), ProjectItem)
                     and target.kind == ProjectItemKind.REPRESENTATION))
        affected = graph.reverse_closure(original.ref)
        self.store.append((changed, *retained), expected_revision=snapshot.revision)
        return RevisedUse(changed, affected)

    def establish_context(self, *, id: str, text: str, model: RepresentationModel,
                          interpret: Callable[[ContextInterpretation], ContextPlan]) -> MathematicalContext:
        """Persist a model's semantic setup and provenance in one B5/B0 transaction."""
        snapshot = self.store.read()
        if snapshot.active_context is None:
            raise ValueError("semantic setup requires an active mathematical context")
        model = RepresentationModel.model_validate(model.model_dump())
        request = ContextInterpretation(text, snapshot.active_context, snapshot)
        plan = ContextPlan.model_validate(interpret(request).model_dump())
        context, records = self.contexts._extension(snapshot, snapshot.active_context,
            id=id, label=text, declarations=plan.declarations, bindings=plan.bindings,
            origin=ProjectOrigin.GENERATED_LOCAL)
        assessment = ProjectItem(id=f"{id}:interpretation", kind=ProjectItemKind.RESEARCH_NOTE,
            name="Semantic context interpretation", statement=text, context=context.ref,
            origin=ProjectOrigin.GENERATED_LOCAL,
            research=ResearchState(status="proposed", reason=plan.reason, author=f"{model.provider}:{model.model}"),
            semantics=(("model", model.model_dump_json()), ("plan", plan.model_dump_json())))
        self.store.append((*records, assessment), expected_revision=snapshot.revision, activate=context.ref)
        return context

    def activate_context(self, context: VersionRef) -> LedgerSnapshot:
        return self.contexts.activate(context)

    def materialize(self, subject: VersionRef, scope: VersionRef, *,
                    prepare: Callable[[ContextualFormalizationInput], PreparedCandidate | SemanticBlockers]
                    ) -> PreparedCandidate | SemanticBlockers:
        snapshot = self.store.read()
        def guarded_prepare(request: ContextualFormalizationInput) -> PreparedCandidate | SemanticBlockers:
            result = prepare(request)
            if self.store.read().revision != snapshot.revision:
                raise ValueError("stale ledger revision during contextual materialization")
            if isinstance(result, PreparedCandidate):
                actual = result.claim.semantic_context
                expected = resolve_input(request)
                if actual is not None and expected is not None:
                    expected = expected.model_copy(update={"generated_binders": actual.generated_binders})
                if result.claim.original_text != request.text or actual != expected:
                    raise ValueError("materialization changed the original statement or semantic context")
            elif isinstance(result, SemanticBlockers):
                if any((work.item, work.scope, work.context) != (subject, request.scope, request.context.ref)
                       or work.status != "open" or work.resolution is not None for work in result.obligations):
                    raise ValueError("formalization blockers must be open work for the exact subject/context/scope")
            else:
                raise ValueError("materialization returned an unsupported result")
            return result
        return self.contexts.materialize(subject, scope, prepare=guarded_prepare)



    def ask(self, *, id: str, question: str, conjecture: str, author: str,
            dependencies: tuple[VersionRef, ...] = ()) -> Inquiry:
        snapshot = self.store.read()
        items = tuple(ProjectItem(id=f"{id}:{kind}", kind=kind, name=text, statement=text,
            context=snapshot.active_context, origin=ProjectOrigin.HUMAN_AUTHORED,
            research=ResearchState(status="active" if kind == "goal" else "proposed", author=author))
            for kind, text in (("question", question), ("conjecture", conjecture), ("goal", conjecture)))
        q, c, g = items
        links = (Relation(id=f"{id}:poses", kind=RelationKind.POSES, source=q.ref, target=c.ref),
                 Relation(id=f"{id}:targets", kind=RelationKind.TARGETS, source=g.ref, target=c.ref))
        required = tuple(Relation(id=f"{item.id}:dependency:{index}", kind=RelationKind.DEPENDS_ON,
                                 source=item.ref, target=ref)
                         for item in (c, g) for index, ref in enumerate(dependencies))
        self._append_new(snapshot, (*items, *links, *required))
        return Inquiry(q, c, g)

    def _append_new(self, snapshot: LedgerSnapshot, records: tuple[LedgerRecord, ...]) -> None:
        known = {record.id for record in snapshot.records}
        if any(record.id in known for record in records):
            raise ValueError("new Explore records require new stable identities")
        self.store.append(records, expected_revision=snapshot.revision)

    @staticmethod
    def _current_item(snapshot: LedgerSnapshot, ref: VersionRef, kind: ProjectItemKind) -> ProjectItem:
        item = snapshot.get(ref)
        if not isinstance(item, ProjectItem) or item.kind != kind:
            raise ValueError(f"expected a {kind.value} item")
        if snapshot.head(ref.id).ref != ref:
            raise ValueError("stale Explore item revision")
        return item

    def start_approach(self, *, id: str, goal: VersionRef, description: str, author: str) -> ProjectItem:
        snapshot = self.store.read()
        target = self._current_item(snapshot, goal, ProjectItemKind.GOAL)
        approach = ProjectItem(id=id, kind=ProjectItemKind.APPROACH, name=description, statement=description,
            origin=ProjectOrigin.GENERATED_LOCAL, context=target.context,
            research=ResearchState(status="active", author=author))
        self._append_new(snapshot, (approach, Relation(id=f"{id}:pursues", kind=RelationKind.PURSUES,
                                                      source=approach.ref, target=goal)))
        return approach

    def block_approach(self, approach: VersionRef, *, reason: str, author: str,
                       evidence: tuple[EvidenceRef, ...]) -> ProjectItem:
        snapshot = self.store.read()
        original = self._current_item(snapshot, approach, ProjectItemKind.APPROACH)
        blocked = ProjectItem.model_validate({**original.model_dump(), "research": ResearchState(
            status="blocked", reason=reason, author=author,
            evidence=tuple(dict.fromkeys((*(original.research.evidence if original.research else ()), *evidence))))})
        self.store.append((blocked,), expected_revision=snapshot.revision)
        return blocked

    def record_product(self, approach: VersionRef, product: VersionRef) -> Relation:
        snapshot = self.store.read()
        self._current_item(snapshot, approach, ProjectItemKind.APPROACH)
        if not isinstance(snapshot.get(product), ProjectItem):
            raise ValueError("approach product must be a project item")
        relation = Relation(id=f"{approach.id}:produces:{product.id}", kind=RelationKind.PRODUCES,
                            source=approach, target=product)
        self._append_new(snapshot, (relation,))
        return relation

    def counterexample(self, *, id: str, conjecture: VersionRef, description: str,
                       reason: str, author: str, evidence: tuple[EvidenceRef, ...] = ()) -> ProjectItem:
        snapshot = self.store.read()
        original = self._current_item(snapshot, conjecture, ProjectItemKind.CONJECTURE)
        example = ProjectItem(id=id, kind=ProjectItemKind.EXAMPLE, name=description, statement=description,
            context=original.context, origin=ProjectOrigin.HUMAN_AUTHORED,
            research=ResearchState(status="proposed", reason=reason, author=author, evidence=evidence))
        refuted = ProjectItem.model_validate({**original.model_dump(), "research": ResearchState(
            status="refuted", reason=reason, author=author, evidence=evidence)})
        link = Relation(id=f"{id}:counterexample", kind=RelationKind.COUNTEREXAMPLE_TO,
                        source=example.ref, target=original.ref)
        if any(record.id in {id, link.id} for record in snapshot.records):
            raise ValueError("counterexample requires a new identity")
        retained = tuple(Relation.model_validate({**relation.model_dump(), "source": refuted.ref})
            for relation in LedgerGraph(snapshot).relations
            if relation.source == original.ref and relation.kind in DEPENDENCIES)
        self.store.append((example, refuted, link, *retained), expected_revision=snapshot.revision)
        return example

    def correct_conjecture(self, original: VersionRef, *, id: str, statement: str,
                           reason: str, author: str) -> ProjectItem:
        snapshot = self.store.read()
        prior = self._current_item(snapshot, original, ProjectItemKind.CONJECTURE)
        corrected = ProjectItem(id=id, kind=ProjectItemKind.CONJECTURE, name=prior.name, statement=statement,
            context=prior.context, origin=ProjectOrigin.HUMAN_AUTHORED,
            research=ResearchState(status="proposed", reason=reason, author=author))
        retained = tuple(Relation.model_validate({**relation.model_dump(),
            "id": f"{id}:dependency:{index}", "source": corrected.ref})
            for index, relation in enumerate(LedgerGraph(snapshot).relations)
            if relation.source == prior.ref and relation.kind in DEPENDENCIES)
        self._append_new(snapshot, (corrected, *retained,
            Relation(id=f"{id}:supersedes", kind=RelationKind.SUPERSEDES, source=corrected.ref, target=prior.ref)))
        return corrected

    def summary(self, scope: Scope | None = None) -> ResearchView:
        return LedgerViews(self.store.read(), self.contexts.policy).research(scope)

    def bind(self, *, id: str, label: str, bindings: tuple[BindingSpec, ...]) -> MathematicalContext:
        snapshot = self.store.read()
        if snapshot.active_context is None:
            raise ValueError("bindings require an active mathematical context")
        return self.contexts.extend(snapshot.active_context, id=id, label=label, bindings=bindings)

    def normalize_goal(self, goal: VersionRef, *, id: str, label: str, scope: VersionRef,
                       mappings: tuple[tuple[str, str], ...], declarations: tuple[DeclarationSpec, ...] = (),
                       bindings: tuple[BindingSpec, ...] = (), justification: VersionRef | None = None
                       ) -> ContextTransport:
        snapshot = self.store.read()
        original = self._current_item(snapshot, goal, ProjectItemKind.GOAL)
        if original.context is None:
            raise ValueError("normalization requires a goal in a mathematical context")
        return self.contexts.transport(original.context, id=id, label=label, subject=goal, scope=scope,
            mappings=mappings, declarations=declarations, bindings=bindings, justification=justification)

    def transport_ready(self, context: VersionRef, scope: VersionRef) -> bool:
        """Authenticate the recorded mappings; original goals still need their own proof."""
        snapshot = self.store.read()
        policy_scope = snapshot.get(scope)
        if not isinstance(policy_scope, Scope):
            raise ValueError("transport requires a policy scope")
        graph = LedgerGraph(snapshot)
        contexts = {item.ref for item in graph.context_chain(context)}
        subjects = {item.ref for item in snapshot.current(ProjectItem) if item.context in contexts}
        mappings = tuple(relation for relation in graph.relations
            if relation.kind == RelationKind.TRANSPORTED_FROM and relation.source in contexts | subjects)
        return bool(mappings) and all(self.contexts.policy.transport_accepted(snapshot, relation, scope=policy_scope)
                                      for relation in mappings)
