"""Shared semantic project operations for workflows and application adapters.

All changes preserve exact versions. Proposals open work; judgments and checked
saves keep their separate owners. Context changes create distinct identities.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from hardy.foundation.files import WriteGuard
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.interactive.assessments import AssessmentOperations
from hardy.workflows.interactive.evidence import ProjectOwners
from hardy.workflows.interactive.project import ProjectOperations
from hardy.workflows.interactive.semantic_records import (
    ArgumentRequest,
    AssessmentRequest,
    ItemRequest,
    JudgmentRequest,
    SourceRegion,
)
from hardy.workflows.interactive.semantic_versions import (
    read_region,
    relevant_versions,
    source_text,
)
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
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
    VersionRef,
)
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import (
    RepresentationRequest,
    RepresentationResolver,
    RepresentationResult,
)


class SemanticOperations:
    def __init__(self, problem: Path, *, owners: ProjectOwners | None = None):
        self.problem = Path(problem)
        self.store = LedgerStore(problem)
        self.owners = owners or ProjectOwners.reading(problem)
        self.contexts = ContextManager(self.store, policy=self.owners.policy)
        self.judgments = self.owners.judgments
        self.assessments = AssessmentOperations(self.owners)
        self.admissions = self.owners.scoped_admissions

    def source(self, id: str, text: str) -> ProjectItem:
        """Retain bytes and advance the logical source; old versions remain readable."""
        data = text.encode("utf-8")
        digest = sha256(data).hexdigest()
        snapshot = self.store.read()
        old = snapshot.head_or_none(id)
        if old is not None and (not isinstance(old, ProjectItem) or old.kind != ProjectItemKind.SOURCE):
            raise ValueError("source identity belongs to another kind of record")
        artifact = ArtifactRef(uri=f"semantic-sources/{digest}.utf8", digest=digest)
        record = ProjectItem(id=id, kind=ProjectItemKind.SOURCE, name=id, origin=ProjectOrigin.HUMAN_AUTHORED,
                             artifacts=(artifact,))
        if old == record:
            source_text(self.problem, snapshot, old.ref)
            return old
        WriteGuard(self.problem / "semantic-sources", create=True).write_bytes(f"{digest}.utf8", data)
        self.store.append((record,), expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return record

    def source_text(self, source: VersionRef) -> str:
        return source_text(self.problem, self.store.read(), source)

    def region_text(self, region: SourceRegion) -> str:
        return read_region(self.problem, self.store.read(), region)

    def context(self, *, id: str, label: str, parent: VersionRef | None = None,
                declarations: tuple[DeclarationSpec, ...] = (), bindings: tuple[BindingSpec, ...] = (),
                supersedes: VersionRef | None = None) -> MathematicalContext:
        """Extend/fork exact contexts; revision never mutates a declaration or binding."""
        snapshot = self.store.read()
        if snapshot.head_or_none(id) is not None:
            raise ValueError("context requires a distinct identity")
        if supersedes is not None:
            old = snapshot.get(supersedes)
            if not isinstance(old, MathematicalContext) or snapshot.head(old.id).ref != supersedes:
                raise ValueError("context revision must name a current mathematical context")
        if parent is None:
            if declarations or bindings:
                raise ValueError("create a root, then extend it with local declarations/bindings")
            result = MathematicalContext(id=id, label=label, origin=ProjectOrigin.HUMAN_AUTHORED)
            records = (result,)
        else:
            result, records = self.contexts._extension(snapshot, parent, id=id, label=label,
                declarations=declarations, bindings=bindings, origin=ProjectOrigin.HUMAN_AUTHORED)
        if supersedes is not None:
            records = (*records, Relation(id=f"{id}:supersedes", kind=RelationKind.SUPERSEDES,
                                         source=result.ref, target=supersedes))
        self.store.append(records, expected_revision=snapshot.revision, activate=result.ref,
                          validate=self.owners.policy.validate)
        return result

    def target(self, id: str, statement: str, *, formal: str, context: VersionRef | None = None,
               supersedes: VersionRef | None = None) -> ProjectItem:
        return ProjectOperations(self.problem, publish=lambda *_args, **_kwargs: None).target(
            id, statement, formal=formal,
            context=f"{context.id}@{context.digest}" if context else None,
            supersedes=f"{supersedes.id}@{supersedes.digest}" if supersedes else None)

    def scope(self, id: str, *, must_prove: tuple[VersionRef, ...]) -> Scope:
        """Select required exact work; revisions can only add protected targets.

        This operation never introduces a premise. Existing admitted premises
        survive only under their original, reauthenticated admission history.
        """
        snapshot = self.store.read()
        old = snapshot.head_or_none(id)
        if old is not None and not isinstance(old, Scope):
            raise ValueError("scope identity belongs to another record kind")
        for ref in must_prove:
            value = snapshot.get(ref)
            if not isinstance(value, ProjectItem):
                raise ValueError("must_prove requires exact project items")
            relevant_versions(self.problem, snapshot, ref)
        if old is not None:
            if not set(old.must_prove).issubset(must_prove):
                raise ValueError("scope revision cannot drop or replace protected required targets")
            self.owners.policy._current_scope(snapshot, old)
        selected = Scope(id=id, must_prove=must_prove,
                         allowed_background=old.allowed_background if old else (),
                         allowed_interfaces=old.allowed_interfaces if old else ())
        if selected == old:
            return old
        self.store.append((selected,), expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return selected

    def item(self, request: ItemRequest) -> ProjectItem:
        request = ItemRequest.model_validate(request.model_dump())
        if request.kind in {ProjectItemKind.SOURCE, ProjectItemKind.ARGUMENT, ProjectItemKind.DECLARATION}:
            raise ValueError("sources, arguments and declarations use their focused operations")
        snapshot = self.store.read()
        if snapshot.head_or_none(request.id) is not None:
            raise ValueError("item creation requires a distinct identity")
        if request.context:
            relevant_versions(self.problem, snapshot, request.context)
        semantics = []
        if request.source:
            read_region(self.problem, snapshot, request.source)
            relevant_versions(self.problem, snapshot, request.source.source)
            semantics.append(("source-region", request.source.model_dump_json()))
        item = ProjectItem(id=request.id, kind=request.kind, name=request.name, statement=request.statement,
            context=request.context, origin=request.origin, semantics=tuple(semantics))
        records = [item]
        for index, ref in enumerate(request.dependencies):
            relevant_versions(self.problem, snapshot, ref)
            records.append(Relation(id=f"{request.id}:dependency:{index}", kind=RelationKind.DEPENDS_ON,
                                    source=item.ref, target=ref))
        if request.target:
            target = snapshot.get(request.target)
            if not isinstance(target, ProjectItem) or snapshot.head(target.id).ref != target.ref:
                raise ValueError("item target must be a current exact item")
            records.append(Relation(id=f"{request.id}:supports", kind=RelationKind.SUPPORTS,
                                    source=item.ref, target=request.target))
        if request.argument:
            argument = snapshot.get(request.argument)
            if (not isinstance(argument, ProjectItem) or argument.kind != ProjectItemKind.ARGUMENT
                    or snapshot.head(argument.id).ref != argument.ref or argument.context != item.context):
                raise ValueError("containing argument must be exact, current and share the context")
            records.append(Relation(id=f"{request.id}:contained", kind=RelationKind.CONTAINS,
                                    source=argument.ref, target=item.ref))
        if request.obligation:
            if request.scope is None:
                raise ValueError("an obligation requires its exact scope")
            scope = snapshot.get(request.scope)
            if not isinstance(scope, Scope) or snapshot.head(scope.id).ref != scope.ref:
                raise ValueError("item work requires a current scope")
            records.append(Obligation(id=f"{request.id}:work", item=item.ref, kind=request.obligation,
                                      scope=scope, context=item.context, reason="Semantic proposal remains unestablished."))
        self.store.append(records, expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return item

    def revise(self, subject: VersionRef, *, statement: str,
               context: VersionRef | None = None) -> ProjectItem:
        snapshot = self.store.read()
        item = snapshot.get(subject)
        if not isinstance(item, ProjectItem) or snapshot.head(item.id).ref != subject:
            raise ValueError("revision requires a current exact item")
        if (item.kind in {ProjectItemKind.ARGUMENT, ProjectItemKind.SOURCE, ProjectItemKind.DECLARATION,
                          ProjectItemKind.CONJECTURE} or ("role", "semantic_target") in item.semantics):
            raise ValueError("this kind requires a distinct explicit superseding version")
        if context is not None and context != item.context:
            raise ValueError("context revision requires a distinct item and explicit transport")
        revised = ProjectItem.model_validate({**item.model_dump(), "statement": statement})
        self.store.append((revised,), expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return revised

    def argument(self, request: ArgumentRequest) -> ProjectItem:
        request = ArgumentRequest.model_validate(request.model_dump())
        snapshot = self.store.read()
        target = snapshot.get(request.target)
        if not isinstance(target, ProjectItem) or snapshot.head(target.id).ref != target.ref:
            raise ValueError("argument must name a current exact conclusion")
        context = request.context if request.context is not None else target.context
        if context != target.context:
            raise ValueError("argument and conclusion require the same exact context")
        semantics = [("argument-role", "repaired" if request.original else "original")]
        if request.source:
            read_region(self.problem, snapshot, request.source)
            relevant_versions(self.problem, snapshot, request.source.source)
            semantics.append(("source-region", request.source.model_dump_json()))
        if snapshot.head_or_none(request.id) is not None:
            raise ValueError("argument version requires a distinct identity")
        argument = ProjectItem(id=request.id, kind=ProjectItemKind.ARGUMENT, name=request.id,
            statement=request.text, context=context, origin=ProjectOrigin.HUMAN_AUTHORED,
            semantics=tuple(semantics), research=ResearchState(status="proposed", reason=request.reason))
        records = [argument, Relation(id=f"{request.id}:conclusion", kind=RelationKind.SUPPORTS,
                                      source=argument.ref, target=target.ref)]
        if request.original:
            original = snapshot.get(request.original)
            conclusions = [r.target for r in snapshot.current(Relation)
                           if r.source == request.original and r.kind == RelationKind.SUPPORTS]
            if (not isinstance(original, ProjectItem) or original.kind != ProjectItemKind.ARGUMENT
                    or target.ref not in conclusions or original.context != context):
                raise ValueError("repaired argument must name an original argument for the same exact conclusion")
            # REFINES records lineage; SUPERSEDES would withdraw the original's
            # current assessment, which a repair must never erase.
            records.append(Relation(id=f"{request.id}:original", kind=RelationKind.REFINES,
                                    source=argument.ref, target=request.original))
        self.store.append(records, expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return argument

    def step(self, request: ItemRequest) -> ProjectItem:
        if request.kind != ProjectItemKind.PROOF_STEP or request.argument is None:
            raise ValueError("proof step requires proof_step kind and exact containing argument")
        return self.item(request)

    def critique(self, request: ItemRequest) -> ProjectItem:
        if request.kind != ProjectItemKind.CRITIQUE or request.target is None:
            raise ValueError("critique requires critique kind and exact target")
        return self.item(request)

    def obligation(self, subject: VersionRef, *, kind: ObligationKind, scope: VersionRef,
                   reason: str) -> Obligation:
        snapshot = self.store.read()
        item, selected_scope = snapshot.get(subject), snapshot.get(scope)
        if not isinstance(item, ProjectItem) or not isinstance(selected_scope, Scope):
            raise ValueError("obligation requires item and scope")
        relevant_versions(self.problem, snapshot, subject, (scope,))
        work = Obligation(id=f"{item.id}:work:{uuid4().hex}", item=subject, kind=kind,
                          scope=selected_scope, context=item.context, reason=reason)
        self.store.append((work,), expected_revision=snapshot.revision, validate=self.owners.policy.validate)
        return work

    def representation(self, request: RepresentationRequest, *, resolver: RepresentationResolver) -> RepresentationResult:
        if resolver.store.project.resolve() != self.problem.resolve():
            raise ValueError("representation resolver belongs to another project")
        return resolver.resolve(request)

    def judge(self, request: JudgmentRequest):
        return self.judgments.record(request)

    def assess(self, request: AssessmentRequest):
        return self.assessments.record(request)

    def report(self, target: VersionRef, *, repaired: VersionRef | None = None, scope: VersionRef | None = None):
        from hardy.workflows.interactive.semantic import target_report
        return target_report(self.problem, f"{target.id}@{target.digest}", owners=self.owners, repaired=repaired, scope=scope)
