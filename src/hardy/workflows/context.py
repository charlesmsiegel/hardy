"""Persistent local mathematics, independent of prose parsing and Lean syntax.

A context contains immutable local additions and pins its parent. Extending or
renaming creates new identities; activating an ancestor never erases a branch.
The model supplies semantic inputs. This owner validates scope and composes the
ledger graph with A2; it cannot certify a choice, transport, or trusted axiom.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from hardy.foundation.values import FrozenModel
from hardy.workflows.formalization import (
    ContextualFormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    SemanticRequirement,
    SemanticSource,
    resolve_input,
)
from hardy.workflows.ledger.contracts import (
    BindingKind,
    DeclarationDetails,
    DeclarationRole,
    LedgerRecord,
    MathematicalContext,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    Scope,
    ScopedBinding,
    StableId,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


class DeclarationSpec(FrozenModel):
    id: StableId
    symbol: Text
    semantic_type: Text
    role: DeclarationRole = DeclarationRole.ARBITRARY
    dependencies: tuple[VersionRef, ...] = ()
    justification: VersionRef | None = None


class BindingSpec(FrozenModel):
    id: StableId
    kind: BindingKind
    symbol: Text
    meaning: Text
    target: VersionRef | None = None


@dataclass(frozen=True)
class ContextTransport:
    context: MathematicalContext
    relation: Relation
    outstanding: tuple[Obligation, ...]
    transported_subject: ProjectItem


class ContextManager:
    def __init__(self, store: LedgerStore, *, policy: LedgerPolicy | None = None):
        self.store = store
        self.policy = policy or LedgerPolicy()

    def create_root(self, *, id: str, label: str,
                    origin: ProjectOrigin = ProjectOrigin.HUMAN_AUTHORED) -> MathematicalContext:
        snapshot = self.store.read()
        context = MathematicalContext(id=id, label=label, origin=origin)
        self.store.append((context,), expected_revision=snapshot.revision, activate=context.ref)
        return context

    @staticmethod
    def _extension(snapshot: LedgerSnapshot, parent: VersionRef, *, id: str, label: str,
                   declarations: tuple[DeclarationSpec, ...], bindings: tuple[BindingSpec, ...],
                   origin: ProjectOrigin) -> tuple[MathematicalContext, tuple[LedgerRecord, ...]]:
        chain = LedgerGraph(snapshot).context_chain(parent)
        members = {ref for context in chain for ref in (*context.declarations, *context.bindings)}
        local = tuple(ProjectItem(
            id=spec.id, kind=ProjectItemKind.DECLARATION, name=spec.symbol, origin=origin,
            declaration=DeclarationDetails(context_id=id, **spec.model_dump(exclude={"id"})),
        ) for spec in declarations)
        names = tuple(ScopedBinding(context_id=id, **spec.model_dump()) for spec in bindings)
        available = {record.ref: record for record in (*local, *names)}
        members.update(available)
        for record in (*local, *names):
            refs = record.declaration.dependencies if isinstance(record, ProjectItem) else (
                (record.target,) if record.target else ()
            )
            for ref in refs:
                dependency = available[ref] if ref in available else snapshot.get(ref)
                if (ref not in members and (isinstance(dependency, ScopedBinding) or
                    isinstance(dependency, ProjectItem) and dependency.declaration is not None)):
                    raise ValueError("declaration dependency is outside the parent/local context scope")
        context = MathematicalContext(id=id, parent=parent, label=label, origin=origin,
                                      declarations=tuple(item.ref for item in local),
                                      bindings=tuple(binding.ref for binding in names))
        return context, (*local, *names, context)

    def extend(self, parent: VersionRef, *, id: str, label: str,
               declarations: tuple[DeclarationSpec, ...] = (), bindings: tuple[BindingSpec, ...] = (),
               origin: ProjectOrigin = ProjectOrigin.HUMAN_AUTHORED,
               activate: bool = True) -> MathematicalContext:
        snapshot = self.store.read()
        context, records = self._extension(snapshot, parent, id=id, label=label,
                                           declarations=declarations, bindings=bindings, origin=origin)
        self.store.append(records, expected_revision=snapshot.revision,
                          activate=context.ref if activate else None)
        return context

    def activate(self, context: VersionRef) -> LedgerSnapshot:
        snapshot = self.store.read()
        LedgerGraph(snapshot).context_chain(context)
        return self.store.append((), expected_revision=snapshot.revision, activate=context)

    def transport(self, parent: VersionRef, *, id: str, label: str, subject: VersionRef,
                  scope: VersionRef, mappings: tuple[tuple[str, str], ...],
                  declarations: tuple[DeclarationSpec, ...] = (), bindings: tuple[BindingSpec, ...] = (),
                  justification: VersionRef | None = None) -> ContextTransport:
        """Record a proposed normalization; a reference does not authenticate it."""
        snapshot = self.store.read()
        subject_record, scope_record = snapshot.get(subject), snapshot.get(scope)
        if not isinstance(subject_record, ProjectItem) or subject_record.context != parent:
            raise ValueError("transport subject must belong to the exact parent context")
        if not isinstance(scope_record, Scope):
            raise ValueError("transport scope must be a Scope")
        context, records = self._extension(snapshot, parent, id=id, label=label,
                                           declarations=declarations, bindings=bindings,
                                           origin=ProjectOrigin.GENERATED_LOCAL)
        transported = ProjectItem(id=f"{id}:subject", kind=ProjectItemKind.GOAL,
                                  name=subject_record.name, statement=subject_record.statement,
                                  origin=ProjectOrigin.GENERATED_LOCAL, context=context.ref)
        outstanding = ()
        if justification is None:
            obligation = Obligation(id=f"{id}:transport", item=transported.ref, kind=ObligationKind.JUSTIFY_TRANSPORT,
                                    scope=scope_record, context=context.ref, reason=label)
            outstanding = (obligation,)
            justification = obligation.ref
        else:
            snapshot.get(justification)
        relation = Relation(id=f"{id}:transport-relation", kind=RelationKind.TRANSPORTED_FROM,
                            source=context.ref, target=parent, justification=justification, mappings=mappings)
        subject_relation = Relation(id=f"{id}:subject-transport", kind=RelationKind.TRANSPORTED_FROM,
                                    source=transported.ref, target=subject, justification=justification, mappings=mappings)
        # Mapping strings are a proposal, not authority to erase a source binder
        # or convention. Keep the original exact prerequisites in the child.
        retained = tuple(Relation(id=f"{id}:retained:{index}", kind=RelationKind.DEPENDS_ON,
                                  source=transported.ref, target=ref)
                         for index, ref in enumerate(LedgerGraph(snapshot).dependency_closure(subject)))
        self.store.append((*records, transported, *outstanding, relation, subject_relation, *retained), expected_revision=snapshot.revision,
                          activate=context.ref)
        return ContextTransport(context, relation, outstanding, transported)

    def _accepted(self, snapshot: LedgerSnapshot, obligation: Obligation, scope: Scope) -> bool:
        current = snapshot.head(obligation.id)
        return (isinstance(current, Obligation) and current.scope.ref == scope.ref
                and current.status == ObligationStatus.RESOLVED and current.resolution is not None
                and self.policy.is_accepted(snapshot, current.resolution))

    def render(self, context: VersionRef | None = None) -> str:
        return self.render_snapshot(self.store.read(), context)

    @staticmethod
    def render_snapshot(snapshot: LedgerSnapshot, context: VersionRef | None = None) -> str:
        """`render` over a snapshot the caller already holds, so one launch reads the ledger once."""
        ref = context or snapshot.active_context
        if ref is None:
            raise ValueError("no active mathematical context")
        graph = LedgerGraph(snapshot)
        active = graph.active_context(ref)
        return json.dumps({
            "context": ref.model_dump(mode="json"),
            "ancestors": [item.model_dump(mode="json") for item in graph.context_chain(ref)],
            "declarations": [item.model_dump(mode="json") for item in active.declarations],
            "bindings": [item.model_dump(mode="json") for item in active.bindings],
        }, ensure_ascii=False, indent=2)

    def formalization_input(self, subject: VersionRef, scope: VersionRef, *,
                            requirements: tuple[SemanticRequirement, ...] = ()) -> ContextualFormalizationInput:
        snapshot = self.store.read()
        item, policy_scope = snapshot.get(subject), snapshot.get(scope)
        if not isinstance(item, ProjectItem) or item.context is None or item.statement is None:
            raise ValueError("formalization requires a project statement with an exact context")
        if not isinstance(policy_scope, Scope):
            raise ValueError("formalization scope must be a Scope")
        graph = LedgerGraph(snapshot)
        chain = graph.context_chain(item.context)
        minimal = graph.minimal_context(subject)
        closure = set(graph.dependency_closure(subject))
        sources = tuple(SemanticSource(ref=ref, record=record) for ref in sorted(closure, key=lambda r: (r.id, r.digest))
                        if isinstance(record := snapshot.get(ref), (ProjectItem, ScopedBinding)))
        pending = list(requirements)
        # Status labels cannot authenticate discharge. Capability readers must
        # reauthenticate accepted resolutions on every projection after restart.
        context_refs = {context.ref for context in chain}
        relevant = closure | {subject, *context_refs}
        semantic_kinds = {"resolve_representation", "refine_representation", "resolve_declaration", "justify_transport"}
        for obligation in snapshot.current(Obligation):
            if obligation.item in relevant or obligation.context in context_refs and obligation.kind == ObligationKind.JUSTIFY_TRANSPORT:
                if self._accepted(snapshot, obligation, policy_scope):
                    continue
                kind = obligation.kind.value
                if kind == "construct_interface":
                    kind = "resolve_representation"
                if kind in semantic_kinds:
                    pending.append(SemanticRequirement(kind=kind, reason=obligation.reason or kind))
        for declaration in minimal.declarations:
            if (declaration.declaration.role in {DeclarationRole.CHOSEN, DeclarationRole.DERIVED}
                    and not self.policy.premise_allowed(snapshot, declaration.ref, scope=policy_scope, context=item.context)):
                pending.append(SemanticRequirement(kind="resolve_declaration",
                    reason=f"Authenticate chosen/derived declaration justification: {declaration.id}"))
        for relation in snapshot.current(Relation):
            if (relation.source in context_refs and relation.kind == RelationKind.TRANSPORTED_FROM
                    and not self.policy.transport_accepted(snapshot, relation, scope=policy_scope)):
                pending.append(SemanticRequirement(kind="justify_transport",
                    reason=f"Authenticate transport mapping {relation.id}@{relation.digest}"))
        return ContextualFormalizationInput(
            text=item.statement, subject=item, context=chain[-1], ancestors=chain[:-1], scope=policy_scope,
            sources=sources, required_binders=tuple(d.ref for d in minimal.declarations),
            required_sources=tuple(b.ref for b in minimal.bindings),
            required_representations=tuple(s.ref for s in sources if isinstance(s.record, ProjectItem)
                                           and s.record.kind == ProjectItemKind.REPRESENTATION),
            requirements=tuple(dict.fromkeys(pending)),
        )

    def materialize(self, subject: VersionRef, scope: VersionRef, *,
                    prepare: Callable[[ContextualFormalizationInput], PreparedCandidate | SemanticBlockers],
                    requirements: tuple[SemanticRequirement, ...] = ()) -> PreparedCandidate | SemanticBlockers:
        """Delegate syntax/Lean to A2, persisting returned unresolved prerequisites."""
        request = self.formalization_input(subject, scope, requirements=requirements)
        resolved = resolve_input(request)
        result = resolved if isinstance(resolved, SemanticBlockers) else prepare(request)
        if isinstance(result, SemanticBlockers):
            result = SemanticBlockers(obligations=tuple(dict.fromkeys(result.obligations)))
            snapshot = self.store.read()
            records = tuple(o for o in result.obligations if o.ref not in {r.ref for r in snapshot.records})
            if records:
                self.store.append(records, expected_revision=snapshot.revision)
        return result
