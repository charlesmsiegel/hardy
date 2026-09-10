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
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.formalization import (
    ContextualFormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    resolve_input,
)
from hardy.workflows.ledger.contracts import (
    MathematicalContext,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    ResearchState,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.graph import DEPENDENCIES, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
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
        remain readable. B2 reauthentication denies stale subjects without stamping
        unrelated project items with an invented invalidation status.
        """
        snapshot = self.store.read()
        original = snapshot.get(subject)
        if not isinstance(original, ProjectItem) or snapshot.head(subject.id).ref != subject:
            raise ValueError("revise_use requires a current exact project item")
        if original.kind in {ProjectItemKind.DECLARATION, ProjectItemKind.REPRESENTATION}:
            raise ValueError("declarations and representations require their shared owners")
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


