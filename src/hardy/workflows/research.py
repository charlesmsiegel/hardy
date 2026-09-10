"""Research schedules exact project work through shared acquisition and policy.

The plan is mathematical work, not acceptance. A2 prepares the original statement;
C4 checkpoints prerequisites and authenticates all completion through B2. Named
capabilities retain model/runtime/tool lifetimes. Inspection of a refutation or
computation never establishes the target theorem. Replaying a request reuses its
exact plan and reauthenticates evidence, without a second workflow database.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hardy.foundation.values import FrozenModel
from hardy.workflows.acquisition.contracts import ClassifiedGap, GapKind, ResolverResult
from hardy.workflows.acquisition.resolver import RecursiveResolver, Resolver
from hardy.workflows.context import ContextManager
from hardy.workflows.formalization import (
    FormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    StandaloneFormalizationInput,
    resolve_input,
)
from hardy.workflows.ledger.contracts import (
    LedgerRecord,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
    StableId,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore


class ResearchRequest(FrozenModel):
    id: StableId
    target: VersionRef
    scope: VersionRef
    context: VersionRef | None = None
    operation: Literal["prove", "refute", "compute"] = "prove"


@dataclass(frozen=True)
class ResearchPlan:
    records: tuple[LedgerRecord, ...] = ()
    prerequisites: tuple[Obligation, ...] = ()


@dataclass(frozen=True)
class ResearchReport:
    request: ResearchRequest
    operation: str
    completed: bool
    established: bool
    used_assumptions: tuple[VersionRef, ...]
    outstanding: tuple[VersionRef, ...]
    reasons: tuple[str, ...]
    attempts: int
    revision: int


class ResearchWorkflow:
    def __init__(self, store: LedgerStore, *, resolver: RecursiveResolver,
                 identify: Callable[[LedgerSnapshot, ResearchRequest], ResearchPlan],
                 prepare: Callable[[FormalizationInput], PreparedCandidate | SemanticBlockers],
                 record_formalization: Callable[[LedgerSnapshot, Obligation, PreparedCandidate], ResolverResult],
                 prove: Resolver, refute: Resolver | None = None, compute: Resolver | None = None):
        if resolver.store.project != store.project:
            raise ValueError("research and acquisition must share the same ledger")
        self.store, self.resolver = store, resolver
        self.identify, self.prepare = identify, prepare
        self.record_formalization = record_formalization
        self.prove, self.refute, self.compute = prove, refute, compute

    @staticmethod
    def _subject(snapshot: LedgerSnapshot, request: ResearchRequest) -> tuple[ProjectItem, Scope]:
        target, scope = snapshot.get(request.target), snapshot.get(request.scope)
        if not isinstance(target, ProjectItem) or not target.statement or not isinstance(scope, Scope):
            raise ValueError("research requires a statement and exact scope")
        if target.context != request.context:
            raise ValueError("research context differs from exact target context")
        if snapshot.head(target.id) != target or snapshot.head(scope.id) != scope:
            raise ValueError("stale research target or scope")
        return target, scope

    def _register(self, request: ResearchRequest) -> Obligation:
        snapshot = self.store.read()
        target, scope = self._subject(snapshot, request)
        note = ProjectItem(id=request.id, kind="research_note", name="Research request",
            origin="generated_local", context=request.context,
            semantics=(("research-request", request.model_dump_json()),))
        work = Obligation(id=f"{request.id}:target", item=target.ref, scope=scope, context=target.context,
            kind="prove" if request.operation == "prove" else "check_informal_step")
        formalize = Obligation(id=f"{request.id}:formalize", item=target.ref, kind="formalize",
                               scope=scope, context=target.context)
        if any(record.id == request.id for record in snapshot.records):
            if snapshot.head(request.id) != note:
                raise ValueError("research request identity already names another request")
            # A serialized note is not permission to skip the original A2 gate.
            # Link endpoints may pin prior obligation revisions after acceptance,
            # but both their exact semantics and their current heads must match.
            snapshot.get(work.ref)
            snapshot.get(formalize.ref)
            link = snapshot.head(f"{request.id}:formalization")
            if not isinstance(link, Relation) or link.kind != "blocked_by":
                raise ValueError("research formalization dependency is missing or changed")
            for expected, references in ((work, (snapshot.head(work.id).ref, link.source)),
                                         (formalize, (snapshot.head(formalize.id).ref, link.target))):
                for reference in references:
                    value = snapshot.get(reference)
                    if not isinstance(value, Obligation) or (
                        value.id, value.item, value.kind, value.scope, value.context
                    ) != (expected.id, expected.item, expected.kind, expected.scope, expected.context):
                        raise ValueError("research formalization work changed identity")
            return snapshot.head(work.id)
        plan = self.identify(snapshot, request)
        if not isinstance(plan, ResearchPlan):
            raise ValueError("research identification must return a ResearchPlan")
        known = {record.id for record in snapshot.records}
        for record in (*plan.records, *plan.prerequisites):
            if record.id in known or not isinstance(record, (ProjectItem, Relation, Obligation)):
                raise ValueError("research plan may only propose new items, relations and open work")
            if isinstance(record, Obligation) and (record.status != "open" or record.resolution is not None
                                                  or record.scope != scope):
                raise ValueError("research plan cannot assign acceptance or change scope")
        links = [Relation(id=f"{request.id}:formalization", kind="blocked_by",
                          source=work.ref, target=formalize.ref)]
        for index, prerequisite in enumerate(plan.prerequisites):
            links.append(Relation(id=f"{request.id}:prerequisite:{index}", kind="blocked_by",
                                  source=formalize.ref, target=prerequisite.ref))
        self.store.append((*plan.records, *plan.prerequisites, note, work, formalize, *links),
                          expected_revision=snapshot.revision, validate=self.resolver.policy.validate)
        return work

    def _formalize(self, snapshot: LedgerSnapshot, work: Obligation, gap: ClassifiedGap) -> ResolverResult:
        item = snapshot.get(work.item)
        request = (ContextManager(self.store, policy=self.resolver.policy).formalization_input(
            work.item, work.scope.ref) if work.context is not None
            else StandaloneFormalizationInput(text=item.statement))
        resolved = resolve_input(request)
        candidate = resolved if isinstance(resolved, SemanticBlockers) else self.prepare(request)
        if isinstance(candidate, SemanticBlockers):
            return ResolverResult(children=candidate.obligations, detail="Formalization has semantic prerequisites")
        if not isinstance(candidate, PreparedCandidate) or candidate.claim.original_text != item.statement:
            raise ValueError("formalization changed the original research statement")
        semantic = candidate.claim.semantic_context
        expected = resolved
        if semantic is not None and expected is not None:
            expected = expected.model_copy(update={"generated_binders": semantic.generated_binders})
        if semantic != expected:
            raise ValueError("formalization changed the exact research context or scope")
        if not candidate.elaboration.success:
            return ResolverResult(detail="Research statement candidate failed elaboration")
        result = self.record_formalization(snapshot, work, candidate)
        note = ProjectItem(id=f"{work.id}:candidate:{candidate.claim.content_hash}", kind="research_note",
            name="Research statement candidate", origin="generated_local", context=work.context,
            semantics=(("frozen-claim", candidate.claim.model_dump_json()),))
        link = Relation(id=f"{note.id}:formalizes", kind="formalizes", source=note.ref, target=work.item)
        return ResolverResult(records=(*result.records, note, link), children=result.children,
                              evidence=result.evidence, detail=result.detail)

    def run(self, request: ResearchRequest, *, max_attempts: int = 64) -> ResearchReport:
        request = ResearchRequest.model_validate(request.model_dump())
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a nonnegative integer")
        self.resolver.check_cancelled()
        work = self._register(request)
        registry = dict(self.resolver.resolvers)
        operation = getattr(self, request.operation)

        def dispatch(snapshot: LedgerSnapshot, child: Obligation, gap: ClassifiedGap) -> ResolverResult:
            if child.id == work.id:
                return (operation(snapshot, child, gap) if operation is not None else
                        ResolverResult(detail=f"No {request.operation} capability configured"))
            if child.id == f"{request.id}:formalize":
                return self._formalize(snapshot, child, gap)
            previous = self.resolver.resolvers.get((gap.kind, child.kind), self.resolver.resolvers.get(gap.kind))
            return previous(snapshot, child, gap) if previous else ResolverResult(detail="No capability configured")

        for kind in GapKind:
            registry[(kind, work.kind)] = dispatch
            registry[(kind, "formalize")] = dispatch
        runner = RecursiveResolver(self.store, classifier=self.resolver.classifier, policy=self.resolver.policy,
            resolvers=registry, decide=self.resolver.decide, check_cancelled=self.resolver.check_cancelled)
        result = runner.resolve(work.ref, max_attempts=max_attempts)
        snapshot = self.store.read()
        target, scope = self._subject(snapshot, request)
        established = self.resolver.policy.premise_allowed(snapshot, target.ref, scope=scope, context=target.context)
        used = self.resolver.policy.trust_boundary(snapshot, target.ref, scope=scope,
                                                  context=target.context) if established else ()
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale research report: project changed during authority authentication")
        return ResearchReport(request, request.operation, result.resolved, established, used,
                              result.outstanding, result.reasons, result.attempts, snapshot.revision)
