"""Review exact project state through three independent, explicitly named layers.

Theory: a review observes defects; it neither repairs mathematics nor certifies it.
Reused: B0 guarded transactions, B5 context projection, A2 candidate preparation.
Named capability operations inspect snapshots; only ledger policy grants authority.
Findings retain their layer and exact subject, so another review kind extends the
same obligations rather than creating a second gap database. Callback execution
and mathematical completeness remain the caller's responsibility; skipped layers
stay visible. Work is linear in findings plus the existing graph/store traversals.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.context import ContextManager
from hardy.workflows.formalization import (
    FormalizationInput,
    PreparedCandidate,
    SemanticBlockers,
    SemanticRequirement,
    StandaloneFormalizationInput,
    resolve_input,
)
from hardy.workflows.ledger.contracts import (
    LedgerRecord,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    Relation,
    Scope,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

Layer = Literal["kernel", "formalization", "adversarial"]
LAYERS: tuple[Layer, ...] = ("kernel", "formalization", "adversarial")


class CritiqueFinding(FrozenModel):
    kind: Literal["prove", "formalize", "check_informal_step", "check_citation",
                  "resolve_representation", "refine_representation", "resolve_declaration",
                  "resolve_ambiguity", "justify_transport"]
    reason: Text
    counterexample: VersionRef | None = None


class CritiqueRequest(FrozenModel):
    subject: VersionRef
    scope: VersionRef
    requirements: tuple[SemanticRequirement, ...] = ()


@dataclass(frozen=True)
class ReviewInput:
    snapshot: LedgerSnapshot
    subject: ProjectItem
    scope: Scope


class ReviewPass(FrozenModel):
    """Attribution, not evidence authentication or a claim of mathematical truth."""

    subject: VersionRef
    scope: VersionRef
    revision: int
    findings: tuple[CritiqueFinding, ...] = ()


@dataclass(frozen=True)
class CritiqueOperations:
    kernel: Callable[[ReviewInput], ReviewPass] | None = None
    prepare: Callable[[FormalizationInput], PreparedCandidate | SemanticBlockers] | None = None
    adversarial: Callable[[ReviewInput], ReviewPass] | None = None


@dataclass(frozen=True)
class CritiqueResult:
    subject: VersionRef
    scope: VersionRef
    layers_run: tuple[Layer, ...]
    layers_skipped: tuple[Layer, ...]
    findings: tuple[tuple[Layer, CritiqueFinding], ...]
    obligations: tuple[Obligation, ...]
    relations: tuple[Relation, ...]

    @property
    def summary(self) -> str:
        if not self.layers_run:
            return "No critique layers ran. Skipped: " + ", ".join(self.layers_skipped) + "."
        result = (f"{len(self.findings)} findings from: " if self.findings else "No gaps detected by: ")
        result += ", ".join(self.layers_run) + "."
        if self.layers_skipped:
            result += " Skipped: " + ", ".join(self.layers_skipped) + "."
        return result


class CritiqueWorkflow:
    def __init__(self, store: LedgerStore, *, operations: CritiqueOperations,
                 policy: LedgerPolicy | None = None):
        self.store = store
        self.operations = operations
        self.policy = policy or LedgerPolicy()

    def run(self, request: CritiqueRequest) -> CritiqueResult:
        request = CritiqueRequest.model_validate(request.model_dump())
        snapshot = self.store.read()
        subject, scope = snapshot.get(request.subject), snapshot.get(request.scope)
        if not isinstance(subject, ProjectItem) or not isinstance(scope, Scope):
            raise ValueError("critique requires a project item and scope")
        if snapshot.head(subject.id) != subject or snapshot.head(scope.id) != scope:
            raise ValueError("critique requires current subject and scope")
        review_input = ReviewInput(snapshot, subject, scope)
        layers: list[Layer] = []
        findings: list[tuple[Layer, CritiqueFinding]] = []

        def review(layer: Layer, operation: Callable[[ReviewInput], ReviewPass] | None) -> None:
            if operation is None:
                return
            result = operation(review_input)
            if not isinstance(result, ReviewPass):
                raise ValueError("review operation must return a ReviewPass")
            result = ReviewPass.model_validate(result.model_dump())
            if (result.subject, result.scope, result.revision) != (
                subject.ref, scope.ref, snapshot.revision
            ):
                raise ValueError("review callback refers to different subject, scope or revision")
            layers.append(layer)
            findings.extend((layer, finding) for finding in result.findings)

        review("kernel", self.operations.kernel)
        if self.operations.prepare is not None:
            if subject.statement is None:
                raise ValueError("formalization probe requires the exact original statement")
            if subject.context is None:
                if request.requirements:
                    raise ValueError("semantic requirements require a mathematical context")
                formal_input = StandaloneFormalizationInput(text=subject.statement)
            else:
                formal_input = ContextManager(self.store, policy=self.policy).formalization_input(
                    subject.ref, scope.ref, requirements=request.requirements)
            semantic_context = resolve_input(formal_input)
            prepared = (semantic_context if isinstance(semantic_context, SemanticBlockers)
                        else self.operations.prepare(formal_input))
            layers.append("formalization")
            if isinstance(prepared, SemanticBlockers):
                for obligation in prepared.obligations:
                    if (obligation.item, obligation.scope, obligation.context) != (
                        subject.ref, scope, subject.context
                    ):
                        raise ValueError("formalization blockers refer to a different subject or context")
                    findings.append(("formalization", CritiqueFinding(
                        kind=obligation.kind.value, reason=obligation.reason or obligation.kind.value)))
            elif isinstance(prepared, PreparedCandidate):
                actual = prepared.claim.semantic_context
                expected = semantic_context
                if actual is not None and expected is not None:
                    expected = expected.model_copy(update={"generated_binders": actual.generated_binders})
                if prepared.claim.original_text != subject.statement or actual != expected:
                    raise ValueError("prepared probe changed the original statement or semantic context")
                if not prepared.elaboration.success:
                    findings.append(("formalization", CritiqueFinding(
                        kind="formalize", reason="Candidate failed Lean statement elaboration.")))
            else:
                raise ValueError("formalization operation returned an unsupported result")
        review("adversarial", self.operations.adversarial)
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale critique callback: ledger changed during review")

        # Every observation becomes ordinary open ledger work. Stable observation
        # IDs deduplicate repeat reads; a defect observed again reopens its history.
        findings = list(dict.fromkeys(findings))
        records: dict[str, LedgerRecord] = {}
        obligations = []
        relations = []
        heads = {record.id: record for record in snapshot.current(LedgerRecord)}
        for layer, finding in findings:
            if finding.counterexample is not None:
                example = snapshot.get(finding.counterexample)
                if (not isinstance(example, ProjectItem) or example.kind != ProjectItemKind.EXAMPLE
                        or snapshot.head(example.id) != example):
                    raise ValueError("counterexample must reference a current example item")
            key = "critique:" + json_digest({"subject": subject.ref.model_dump(),
                "scope": scope.ref.model_dump(), "layer": layer, "finding": finding.model_dump()})
            obligation = Obligation(id=key, item=subject.ref, scope=scope, context=subject.context,
                                    kind=finding.kind, reason=f"{layer}: {finding.reason}")
            old = heads.get(key)
            if old is not None:
                if not isinstance(old, Obligation) or (old.item, old.scope, old.kind) != (
                    obligation.item, obligation.scope, obligation.kind
                ):
                    raise ValueError("critique obligation identity collision")
                obligation = (old if old.status == "open" else obligation.model_copy(update={"previous": old.ref}))
            obligations.append(obligation)
            if old != obligation:
                records[key] = obligation
            if finding.counterexample is not None:
                relation = Relation(id=key + ":counterexample", kind="counterexample_to",
                                    source=finding.counterexample, target=subject.ref)
                relations.append(relation)
                if heads.get(relation.id) != relation:
                    records[relation.id] = relation
        if records:
            self.store.append(records.values(), expected_revision=snapshot.revision, validate=self.policy.validate)
        return CritiqueResult(subject.ref, scope.ref, tuple(layers),
                              tuple(layer for layer in LAYERS if layer not in layers),
                              tuple(findings), tuple(obligations), tuple(relations))
