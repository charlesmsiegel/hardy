"""Statement preparation without a session or a provider lifecycle.

Theory: a candidate translates original text plus caller-resolved semantic state;
only the provider generates binder syntax, and every fragment retains its origin.
An exact subject statement is the original text, including its whitespace.
This module validates supplied records and composes freeze/Lean/independent review.
Prove owns calls, approval, budgets, cancellation and persisted-readback ordering.
B0/B4/B5 must establish store reachability, minimal closure, representation adequacy
and transport validity; supplied requirements remain open obligations here.
No local hypothesis becomes a trusted axiom. Preparation is linear in supplied
content except for deterministic source ordering (O(n log n)); it reads no ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import model_validator

from hardy.formal.contracts import (
    ContextualFormalizationProposal,
    DeclaredAssumption,
    EnvironmentIdentity,
    FormalizationContext,
    FormalizationProposal,
    FrozenClaim,
    SemanticEntry,
    SemanticRef,
    freeze_claim,
)
from hardy.formal.lean import LeanCheckResult
from hardy.foundation.values import FrozenModel, json_digest
from hardy.prompts import FORMALIZATION_PROMPT
from hardy.workflows.faithfulness import review_translation  # shared independent-read operation
from hardy.workflows.ledger.contracts import (
    MathematicalContext,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    Scope,
    ScopedBinding,
    VersionRef,
)

__all__ = [
    "ContextualFormalizationInput", "StandaloneFormalizationInput", "SemanticSource",
    "SemanticRequirement", "SemanticBlockers", "SemanticBlocked", "PreparedCandidate",
    "formalization_prompt", "proposal_type", "resolve_input", "freeze_formalization",
    "prepare_candidate", "review_translation",
]


class SemanticSource(FrozenModel):
    """Content is derived from this exact source, never an arbitrary gloss."""

    ref: VersionRef
    record: ProjectItem | ScopedBinding

    @model_validator(mode="after")
    def exact_reference(self) -> Self:
        if self.ref != self.record.ref:
            raise ValueError("semantic source reference differs from actual record")
        return self


class SemanticRequirement(FrozenModel):
    kind: Literal["resolve_representation", "refine_representation", "resolve_declaration", "justify_transport"]
    reason: str


class StandaloneFormalizationInput(FrozenModel):
    text: str


class ContextualFormalizationInput(FrozenModel):
    """Caller/B5 selects needed sources; context membership alone selects nothing.

    Ancestors validate supplied membership. They do not require unrelated
    declarations to be rendered or given binders. required_sources pins needed
    entries that have no generated binder (for example conventions).
    """
    text: str
    subject: ProjectItem
    context: MathematicalContext
    scope: Scope
    sources: tuple[SemanticSource, ...]
    ancestors: tuple[MathematicalContext, ...] = ()
    required_binders: tuple[VersionRef, ...]
    required_sources: tuple[VersionRef, ...] = ()
    required_representations: tuple[VersionRef, ...] = ()
    required_transports: tuple[VersionRef, ...] = ()
    requirements: tuple[SemanticRequirement, ...] = ()

    @model_validator(mode="after")
    def exact_subject_statement(self) -> Self:
        if self.subject.statement is not None and self.text != self.subject.statement:
            raise ValueError("original text differs from exact subject statement")
        return self


FormalizationInput = StandaloneFormalizationInput | ContextualFormalizationInput
Proposal = FormalizationProposal | ContextualFormalizationProposal


class SemanticBlockers(FrozenModel):
    obligations: tuple[Obligation, ...]


class SemanticBlocked(ValueError):
    def __init__(self, blockers: SemanticBlockers):
        self.obligations = blockers.obligations
        super().__init__("unresolved semantic context: " + "; ".join(o.reason or o.kind.value for o in self.obligations))


def _ref(ref: VersionRef) -> SemanticRef:
    return SemanticRef(**ref.model_dump())


def _blockers(request: ContextualFormalizationInput, requirements: list[SemanticRequirement]) -> SemanticBlockers:
    # Stable values only. B0 allocates/persists project obligations later.
    return SemanticBlockers(obligations=tuple(
        Obligation(
            id="formalization:" + json_digest({"item": request.subject.ref.model_dump(),
                                               "context": request.context.ref.model_dump(),
                                               "scope": request.scope.ref.model_dump(),
                                               "requirement": requirement.model_dump()}),
            item=request.subject.ref, kind=requirement.kind, scope=request.scope,
            context=request.context.ref, reason=requirement.reason,
        ) for requirement in requirements
    ))


def resolve_input(request: FormalizationInput) -> FormalizationContext | SemanticBlockers | None:
    """Validate supplied context, not mathematical resolution or trust policy."""
    if isinstance(request, StandaloneFormalizationInput):
        return None
    # model_copy updates bypass construction validators; consumers recheck.
    request.exact_subject_statement()
    if request.subject.context != request.context.ref:
        raise ValueError("subject context reference differs from supplied context")
    sources = {source.ref.id: source for source in request.sources}
    if len(sources) != len(request.sources):
        raise ValueError("duplicate semantic source identity")
    contexts = {context.id: context for context in (request.context, *request.ancestors)}
    if len(contexts) != 1 + len(request.ancestors):
        raise ValueError("duplicate context identity")
    requirements = list(request.requirements)

    def missing(kind: str, reason: str) -> None:
        requirements.append(SemanticRequirement(kind=kind, reason=reason))

    entries = []
    members: dict[str, tuple[VersionRef, str, str]] = {}
    visited = set()
    context = request.context
    while True:
        if context.id in visited:
            raise ValueError("cyclic supplied context")
        visited.add(context.id)
        entries.append(SemanticEntry(ref=_ref(context.ref), role="context", text=context.model_dump_json()))
        for role, refs in (("declaration", context.declarations), ("binding", context.bindings)):
            for ref in refs:
                if ref.id in members:
                    raise ValueError("duplicate member identity in supplied context chain")
                members[ref.id] = (ref, role, context.id)
        if context.parent is None:
            break
        parent = contexts.get(context.parent.id)
        if parent is None:
            missing("resolve_declaration", f"Missing parent context {context.parent.id}@{context.parent.digest}")
            break
        if parent.ref != context.parent:
            raise ValueError("parent context reference differs from supplied record")
        context = parent
    if visited != set(contexts):
        raise ValueError("supplied ancestor is outside the context chain")

    def require(ref: VersionRef, kind: str = "resolve_declaration") -> SemanticSource | None:
        source = sources.get(ref.id)
        if source is None:
            missing(kind, f"Missing semantic source {ref.id}@{ref.digest}")
        elif source.ref != ref:
            missing(kind, f"Stale semantic source: expected {ref.id}@{ref.digest}; "
                    f"supplied {source.ref.id}@{source.ref.digest}")
            return None
        return source

    roles: dict[str, str] = {}
    for source in request.sources:
        record = source.record
        membership = members.get(source.ref.id)
        if membership is None:
            if isinstance(record, ScopedBinding) or record.kind == ProjectItemKind.DECLARATION:
                raise ValueError("selected declaration/binding is outside supplied context")
            roles[source.ref.id] = "source"
        else:
            ref, role, owner = membership
            if source.ref != ref:
                raise ValueError("context member reference differs from supplied source")
            if role == "declaration":
                if not isinstance(record, ProjectItem) or record.kind != ProjectItemKind.DECLARATION:
                    raise ValueError("context declaration has wrong source kind")
                actual_owner = record.declaration.context_id
            else:
                if not isinstance(record, ScopedBinding):
                    raise ValueError("context binding has wrong source kind")
                actual_owner = record.context_id
            if owner != actual_owner:
                raise ValueError("context member belongs to another context")
            roles[source.ref.id] = role
        # Validate explicit edges over supplied source records. B5 still decides
        # what prose means, which representation is adequate, and what is needed.
        if isinstance(record, ScopedBinding) and record.target is not None:
            require(record.target)
        if isinstance(record, ProjectItem) and record.declaration is not None:
            for dependency in record.declaration.dependencies:
                require(dependency)

    for ref in request.required_sources:
        require(ref)
    for role, refs in (("representation", request.required_representations), ("transport", request.required_transports)):
        for ref in refs:
            source = require(ref, "resolve_representation" if role == "representation" else "justify_transport")
            if source is None:
                continue
            if not isinstance(source.record, ProjectItem):
                raise ValueError("representation/transport requires a project item")
            if role == "representation" and source.record.kind != ProjectItemKind.REPRESENTATION:
                raise ValueError("representation has wrong source kind")
            if roles[ref.id] != "source":
                raise ValueError("source has conflicting semantic roles")
            roles[ref.id] = role
    for ref in request.required_binders:
        source = require(ref)
        if source is not None and roles.get(ref.id) != "declaration":
            raise ValueError("required binder origin is not an exact context declaration")
    if requirements:
        return _blockers(request, requirements)
    entries.extend(SemanticEntry(ref=_ref(s.ref), role=roles[s.ref.id], text=s.record.model_dump_json())
                   for s in request.sources)
    entries.extend((SemanticEntry(ref=_ref(request.subject.ref), role="subject", text=request.subject.model_dump_json()),
                    SemanticEntry(ref=_ref(request.scope.ref), role="scope", text=request.scope.model_dump_json())))
    return FormalizationContext(
        subject=_ref(request.subject.ref), context=_ref(request.context.ref), scope=_ref(request.scope.ref),
        entries=tuple(sorted(entries, key=lambda entry: (entry.role, entry.ref.id))),
        required_binders=tuple(_ref(ref) for ref in request.required_binders),
    )


def proposal_type(request: FormalizationInput) -> type[FormalizationProposal] | type[ContextualFormalizationProposal]:
    return FormalizationProposal if isinstance(request, StandaloneFormalizationInput) else ContextualFormalizationProposal


def formalization_prompt(request: FormalizationInput, revision: str = "") -> str:
    context = resolve_input(request)
    if isinstance(context, SemanticBlockers):
        raise SemanticBlocked(context)
    prompt = FORMALIZATION_PROMPT + "\n\nUser claim:\n" + request.text
    if context is not None:
        prompt += ("\n\nCaller-resolved semantic context (exact source records):\n" + context.model_dump_json(indent=2)
                   + "\nGenerate each Lean binder fragment as generated_binders with its exact declaration_ref. "
                   "Every required_binders origin must occur; one declaration may generate multiple fragments. "
                   "The signature is rendered solely from these fragments. Context hypotheses are local parameters, "
                   "not globally trusted axioms. Do not invent semantic assumptions or alter the original statement.")
    if revision:
        prompt += "\n\nUser revision request:\n" + revision
    return prompt


def freeze_formalization(request: FormalizationInput, proposal: Proposal, environment: EnvironmentIdentity,
                         approved_at: datetime) -> FrozenClaim | SemanticBlockers:
    context = resolve_input(request)
    if isinstance(context, SemanticBlockers):
        return context
    if context is not None:
        if not isinstance(proposal, ContextualFormalizationProposal):
            raise ValueError("contextual formalization requires generated binder origins")
        declarations = {entry.ref for entry in context.entries if entry.role == "declaration"}
        origins = {binder.declaration_ref for binder in proposal.generated_binders}
        if not origins <= declarations:
            raise ValueError("unknown or stale generated binder origin")
        missing = set(context.required_binders) - origins
        if missing:
            return _blockers(request, [SemanticRequirement(kind="resolve_declaration",
                reason=f"Missing generated binder origin {ref.id}@{ref.digest}")
                for ref in sorted(missing, key=lambda ref: (ref.id, ref.digest))])
        context = context.model_copy(update={"generated_binders": proposal.generated_binders})
        proposal = FormalizationProposal(**proposal.model_dump(exclude={"generated_binders"}),
                                         binders=" ".join(b.lean_syntax for b in proposal.generated_binders))
    elif not isinstance(proposal, FormalizationProposal):
        raise ValueError("standalone formalization requires the standalone proposal schema")
    return freeze_claim(request.text, proposal, environment, approved_at, semantic_context=context)


class StatementChecker(Protocol):
    def check_proof(self, claim: FrozenClaim, proof_body: str,
                    allowed: tuple[DeclaredAssumption, ...]) -> LeanCheckResult: ...


@dataclass(frozen=True)
class PreparedCandidate:
    claim: FrozenClaim
    elaboration: LeanCheckResult


def prepare_candidate(request: FormalizationInput, proposal: Proposal, environment: EnvironmentIdentity,
                      approved_at: datetime, *, lean: StatementChecker,
                      assumptions: tuple[DeclaredAssumption, ...] = ()) -> PreparedCandidate | SemanticBlockers:
    claim = freeze_formalization(request, proposal, environment, approved_at)
    if isinstance(claim, SemanticBlockers):
        return claim
    return PreparedCandidate(claim=claim, elaboration=lean.check_proof(claim, "by sorry", assumptions))
