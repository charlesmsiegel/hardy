"""Acquire a definition without turning model choices into mathematical authority.

Theory: search and synthesis propose a realization; ledger policy accepts exact owner
evidence. Prefer explicit mappings and bodies before exposing opaque assumptions.
Reused: admission filters, ledger records and the acquisition proposal seam.
Assumes: named search/save operations report their own work and execute trusted
Lean in disposable environments; no live adapter or isolation is installed here.
Watch: adequacy of a mapping is semantic review, never inferred from its name.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import model_validator

from hardy.formal.syntax import IDENTIFIER
from hardy.foundation.values import FrozenModel
from hardy.workflows.acquisition.contracts import (
    ClassifiedGap,
    ResolverResult,
    SearchMatch,
    SearchRecord,
)
from hardy.workflows.admission import (
    AdmissionPolicy,
    AdmissionRequest,
    CheckDecision,
    ProbeOperations,
    TrustRequestKind,
)
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    Obligation,
    ObligationKind,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    ResearchState,
    Text,
)
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.representation import RepresentationModel


def _fragment(value: str) -> None:
    # Small generated declaration fragments, not an arbitrary Lean module. This
    # is a shape check; the formal owner's elaboration/audit remains necessary.
    if len(value.splitlines()) != 1 or re.search(r"\b(?:axiom|constant|opaque|sorry|admit|unsafe|import|namespace|end)\b", value):
        raise ValueError("definition requires a single Lean fragment without commands or assumptions")


# A concrete single-declaration body, still requiring the formal owner's check.
class LocalDefinition(FrozenModel):
    name: Text
    lean_type: Text
    body: Text
    reason: Text

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        """Require one Lean identifier and command-free type/body fragments before materialization."""
        if not re.fullmatch(IDENTIFIER, self.name):
            raise ValueError("definition name must be a single Lean identifier")
        _fragment(self.lean_type)
        _fragment(self.body)
        return self

    @property
    def source(self) -> str:
        return f"import Mathlib\n\ndef {self.name} : {self.lean_type} := {self.body}\n"


# A proposed law for an opaque carrier; naming it grants no trust.
class CharacterizingAssumption(FrozenModel):
    name: Text
    statement: Text


# A carrier and its proposed laws, kept explicit until admission or proof.
class OpaqueDefinition(FrozenModel):
    name: Text
    lean_type: Text
    reason: Text
    assumptions: tuple[CharacterizingAssumption, ...] = ()

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        """Keep the carrier fragment explicit and all proposed assumption names distinct."""
        if not re.fullmatch(IDENTIFIER, self.name):
            raise ValueError("opaque name must be a single Lean identifier")
        _fragment(self.lean_type)
        names = [self.name, *(assumption.name for assumption in self.assumptions)]
        if len(set(names)) != len(names):
            raise ValueError("opaque assumptions must have distinct names")
        return self


# An exact search hit selected for formal materialization.
class DefinitionMapping(FrozenModel):
    source: Literal["mathlib", "local"]
    hit: SearchMatch
    reason: Text


# A formal owner's artifacts and references, reauthenticated by ledger policy.
class DefinitionMaterialization(FrozenModel):
    """A formal owner's artifacts and claimed references, reauthenticated by B2."""
    artifacts: tuple[ArtifactRef, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()


class DefinitionUnavailable(ValueError):
    """The formal operation could not materialize a concrete candidate."""


@dataclass(frozen=True)
class DefinitionQuery:
    """The exact ledger subject and obligation supplied to definition operations."""
    snapshot: LedgerSnapshot
    obligation: Obligation
    item: ProjectItem


@dataclass(frozen=True)
class DefinitionOptions:
    """Search receipts and concrete failures available to the next fallback choice."""
    query: DefinitionQuery
    local: SearchRecord
    mathlib: SearchRecord
    local_failure: str = ""
    mapping_failure: str = ""


class DefinitionResolver:
    """Prefer mappings, then concrete bodies, before proposing explicit assumptions."""
    def __init__(self, *,
                 model: RepresentationModel,
                 search_local: Callable[[DefinitionQuery], SearchRecord],
                 search_mathlib: Callable[[DefinitionQuery], SearchRecord],
                 select_mapping: Callable[[DefinitionOptions], DefinitionMapping | None],
                 create_local: Callable[[DefinitionOptions], LocalDefinition | None],
                 propose_opaque: Callable[[DefinitionOptions], OpaqueDefinition | None],
                 materialize: Callable[[DefinitionQuery, DefinitionMapping | LocalDefinition], DefinitionMaterialization],
                 probes: ProbeOperations | None = None):
        self.search_local = search_local
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.search_mathlib = search_mathlib
        self.select_mapping = select_mapping
        self.create_local = create_local
        self.propose_opaque = propose_opaque
        self.materialize = materialize
        self.probes = probes
        self.admission = AdmissionPolicy()

    def resolve(self, snapshot: LedgerSnapshot, obligation: Obligation,
                gap: ClassifiedGap | None = None) -> ResolverResult:
        """Try searched mappings and concrete bodies before returning any explicit opaque assumption work."""
        obligation = Obligation.model_validate(obligation.model_dump())
        item = snapshot.get(obligation.item)
        if (not isinstance(item, ProjectItem) or obligation.kind != ObligationKind.DEFINE
                or snapshot.head(obligation.id) != obligation or snapshot.head(item.id) != item
                or snapshot.head(obligation.scope.id) != obligation.scope):
            raise ValueError("definition acquisition requires a current definition obligation")
        query = DefinitionQuery(snapshot, obligation, item)
        local = SearchRecord.model_validate(self.search_local(query).model_dump())
        mathlib = SearchRecord.model_validate(self.search_mathlib(query).model_dump())
        if local.source != "local" or mathlib.source != "mathlib":
            raise ValueError("search receipts have the wrong source")
        options = DefinitionOptions(query, local, mathlib)
        result, options = self._mapped(options)
        if result is not None:
            return result
        result, options = self._defined(options)
        if result is not None:
            return result
        return self._opaque(options)

    def _mapped(self, options: DefinitionOptions) -> tuple[ResolverResult | None, DefinitionOptions]:
        """Materialize an exact searched mapping, retaining a failure for fallback."""
        query = options.query
        mapping = self.select_mapping(options)
        if mapping is not None:
            mapping = DefinitionMapping.model_validate(mapping.model_dump())
            searched = options.mathlib if mapping.source == "mathlib" else options.local
            if mapping.hit not in searched.hits:
                raise ValueError("mapping must select an exact searched candidate")
            if mapping.source == "local" and (
                mapping.hit.item is None or query.snapshot.head(mapping.hit.item.id).ref != mapping.hit.item
            ):
                raise ValueError("local mapping requires a current exact item")
            try:
                materialized = self.materialize(query, mapping)
            except DefinitionUnavailable as error:
                options = DefinitionOptions(query, options.local, options.mathlib, mapping_failure=str(error))
            else:
                return self._result(options, mapping.source, mapping.reason, materialized,
                                    candidate=mapping.model_dump_json()), options
        return None, options

    def _defined(self, options: DefinitionOptions) -> tuple[ResolverResult | None, DefinitionOptions]:
        """Try a concrete local body before making any opaque proposal."""
        definition = self.create_local(options)
        failure = "No real local definition was supplied"
        if definition is not None:
            definition = LocalDefinition.model_validate(definition.model_dump())
            try:
                materialized = self.materialize(options.query, definition)
            except DefinitionUnavailable as error:
                failure = str(error)
            else:
                return self._result(options, "local_definition", definition.reason, materialized,
                                    candidate=definition.model_dump_json()), options
        return None, DefinitionOptions(options.query, options.local, options.mathlib,
                                       failure, options.mapping_failure)

    def _opaque(self, options: DefinitionOptions) -> ResolverResult:
        """Probe a permitted assumption proposal without installing it as a premise."""
        item, obligation = options.query.item, options.query.obligation
        if not options.local.complete or not options.mathlib.complete:
            return self._result(options, "unresolved", "Complete local and Mathlib search is required before opacity")
        opaque = self.propose_opaque(options)
        if opaque is None:
            return self._result(options, "unresolved", options.local_failure)
        opaque = OpaqueDefinition.model_validate(opaque.model_dump())
        refusal = self.admission.request_refusal(AdmissionRequest(
            TrustRequestKind.GLOBAL_ASSUMPTION, item.ref, obligation.scope))
        if refusal:
            return self._result(options, "unresolved", refusal)
        if item.origin == ProjectOrigin.TARGET_PAPER or item.context is not None:
            return self._result(options, "unresolved", "Target-paper or contextual definitions cannot become global assumptions")
        if self.probes is None:
            return self._result(options, "unresolved", "Opaque assumptions require admission probe operations")
        contextual_probes = _opaque_probes(opaque, self.probes)
        checks = [self.admission.check_paper(opaque.name, opaque.name, opaque.lean_type, "constant", self.probes)]
        for assumption in opaque.assumptions:
            checks.append(self.admission.check_paper(assumption.name, assumption.name, assumption.statement,
                                                     "statement", contextual_probes))
        if refusal := next((check.refusal for check in checks if check.refusal), None):
            return self._result(options, "unresolved", refusal)
        return self._opaque_proposal(options, opaque, checks)

    def _opaque_proposal(self, options: DefinitionOptions, opaque: OpaqueDefinition,
                         checks: list[CheckDecision]) -> ResolverResult:
        obligation = options.query.obligation
        records = []
        children = []
        for index, (name, statement, kind) in enumerate([
            (opaque.name, opaque.lean_type, ObligationKind.DEFINE),
            *((value.name, value.statement, ObligationKind.PROVE) for value in opaque.assumptions),
        ]):
            assumption = ProjectItem(id=f"{obligation.id}:opaque:{index}",
                kind=ProjectItemKind.DEFINITION if index == 0 else ProjectItemKind.CLAIM,
                name=name, statement=statement, origin=ProjectOrigin.GENERATED_LOCAL,
                research=ResearchState(status="proposed", reason="Requires explicit authenticated admission or proof"),
                semantics=(("trust", "opaque characterizing assumption"),
                    ("admission-check", checks[index].checked + " "
                     + f"Property probes use {opaque.name} : {opaque.lean_type} as a local parameter; "
                     + "no characterizing laws are assumed. These checks neither construct a witness nor admit trust.")))
            records.append(assumption)
            children.append(Obligation(id=f"{obligation.id}:opaque-obligation:{index}",
                item=assumption.ref, kind=kind, scope=obligation.scope,
                reason="Establish this assumption or explicitly admit its exact scope change"))
        base = self._result(options, "opaque_proposal", opaque.reason, candidate=opaque.model_dump_json())
        return ResolverResult(records=(*base.records, *records), children=tuple(children),
                              detail="Opaque proposal requires explicit trust; no assumptions admitted")

    def _result(self, options: DefinitionOptions, method: str, detail: str,
                materialized: DefinitionMaterialization | None = None, *, candidate: str | None = None) -> ResolverResult:
        materialized = DefinitionMaterialization.model_validate(materialized.model_dump()) if materialized is not None else DefinitionMaterialization()
        query = options.query
        if any(reference.subject != query.item.ref for reference in materialized.evidence):
            raise ValueError("definition evidence names a different exact subject")
        assessment = ProjectItem(id=f"{query.obligation.id}:definition-assessment",
            kind=ProjectItemKind.RESEARCH_NOTE, name=f"Definition acquisition for {query.item.name}",
            origin=ProjectOrigin.GENERATED_LOCAL, context=query.item.context,
            research=ResearchState(status="proposed", reason=detail, author=f"{self.model.provider}:{self.model.model}"), artifacts=materialized.artifacts,
            semantics=(("method", method), ("model", self.model.model_dump_json()), ("local-search", options.local.model_dump_json()),
                ("mathlib-search", options.mathlib.model_dump_json()),
                *((("local-failure", options.local_failure),) if options.local_failure else ()),
                *((("mapping-failure", options.mapping_failure),) if options.mapping_failure else ()),
                *((("candidate", candidate),) if candidate else ())))
        return ResolverResult(records=(assessment,), evidence=materialized.evidence, detail=detail)


def _opaque_probes(candidate: OpaqueDefinition, operations: ProbeOperations) -> ProbeOperations:
    """Probe laws in their carrier context without assuming the candidate laws.

    The carrier is a local parameter, not a new axiom in the project. Replace
    only the prelude's blank second line so A3's diagnostic line arithmetic
    remains valid for elaboration and negation probes alike. Quantifying the
    entire statement instead would incorrectly refute ``Nonempty Carrier``
    by choosing Empty, rather than testing the proposed fixed carrier.
    """
    prelude = "import Mathlib\n\n"
    contextual = f"import Mathlib\nvariable ({candidate.name} : {candidate.lean_type})\n"

    def source(text: str) -> str:
        if not text.startswith(prelude):
            raise ValueError("opaque probe source changed its diagnostic prelude")
        return contextual + text[len(prelude):]

    return ProbeOperations(
        elaborate=lambda text: operations.elaborate(source(text)),
        refute=lambda text: operations.refute(source(text)),
    )
