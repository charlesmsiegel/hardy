"""Materialize the required field closure of an exact representation choice.

Theory: a structure exposes operations/properties as fields, never global axioms.
Missing fields and interpretation prerequisites are typed work, not invented Lean.
Reused: B4 representation identities, C1 fragment/materialization values, B2
acceptance readers, and the formal owner's guarded named save operation.
Assumes: callers supply the downstream requirements and a checked writer that
refuses unowned overwrites; no execution isolation or live adapter is added here.
Watch: explicit dependency lists need semantic review; field closure is linear.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from hashlib import sha256
from typing import Self

from pydantic import model_validator

from hardy.formal.syntax import IDENTIFIER, safe_relative
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.acquisition.contracts import ResolverResult
from hardy.workflows.acquisition.definitions import DefinitionMaterialization, _fragment
from hardy.workflows.ledger.contracts import (
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    ResearchState,
    Text,
    VersionRef,
)
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.representation import RepresentationModel


class InterfaceRequirement(FrozenModel):
    kind: ObligationKind
    reason: Text


class InterfaceField(FrozenModel):
    name: Text
    lean_type: Text
    depends_on: tuple[Text, ...] = ()
    requirements: tuple[InterfaceRequirement, ...] = ()

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if not re.fullmatch(IDENTIFIER, self.name):
            raise ValueError("interface field must have a single Lean identifier")
        _fragment(self.lean_type)
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("field dependencies must be distinct")
        return self


class InterfacePlan(FrozenModel):
    """Implementation of B4's selected exact representation, not a new choice."""
    representation: VersionRef
    name: Text
    path: Text
    required: tuple[Text, ...]
    fields: tuple[InterfaceField, ...]
    requirements: tuple[InterfaceRequirement, ...] = ()

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if not re.fullmatch(IDENTIFIER, self.name):
            raise ValueError("interface name must be a single Lean identifier")
        safe_relative(self.path)
        if len({value.name for value in self.fields}) != len(self.fields):
            raise ValueError("interface fields must have distinct names")
        if len(set(self.required)) != len(self.required):
            raise ValueError("required interface fields must be distinct")
        return self


class InterfaceWrite(FrozenModel):
    """Exact checked-save request. The formal owner must guard paths/overwrites.

    A valid relative path is necessary but is not permission to replace a file.
    Implementations must use their formal workspace's checked atomic save gate.
    """
    plan: InterfacePlan
    obligation: Obligation
    source: Text

    @property
    def path(self) -> str:
        return str(safe_relative(self.plan.path))

    @property
    def source_digest(self) -> str:
        return sha256(self.source.encode("utf-8")).hexdigest()


class InterfaceResolver:
    def __init__(self, *, model: RepresentationModel,
                 select_plan: Callable[[LedgerSnapshot, Obligation], InterfacePlan],
                 write_interface: Callable[[InterfaceWrite], DefinitionMaterialization],
                 policy: LedgerPolicy | None = None):
        self.model = RepresentationModel.model_validate(model.model_dump())
        self.select_plan = select_plan
        self.write_interface = write_interface
        self.policy = policy or LedgerPolicy()

    def resolve(self, snapshot: LedgerSnapshot, obligation: Obligation, gap=None) -> ResolverResult:
        obligation = Obligation.model_validate(obligation.model_dump())
        representation = snapshot.get(obligation.item)
        if (not isinstance(representation, ProjectItem) or representation.kind != ProjectItemKind.REPRESENTATION
                or obligation.kind != ObligationKind.CONSTRUCT_INTERFACE
                or obligation.context != representation.context
                or snapshot.head(obligation.id) != obligation
                or snapshot.head(representation.id) != representation
                or snapshot.head(obligation.scope.id) != obligation.scope):
            raise ValueError("interface materialization requires a current representation obligation")
        plan = InterfacePlan.model_validate(self.select_plan(snapshot, obligation).model_dump())
        if plan.representation != representation.ref:
            raise ValueError("interface plan must preserve the exact selected representation")
        fields, missing = self._closure(plan)
        requirements = [*plan.requirements, *(req for field in fields for req in field.requirements)]
        requirements.extend(InterfaceRequirement(kind=ObligationKind.DEFINE,
            reason=f"Define required interface field {name}") for name in missing)
        children = self._children(snapshot, obligation, requirements)
        source = None
        materialized = DefinitionMaterialization()
        if not children and not missing:
            source = f"import Mathlib\n\nstructure {plan.name} where\n" + "".join(
                f"  {field.name} : {field.lean_type}\n" for field in fields)
            request = InterfaceWrite(plan=plan, obligation=obligation, source=source)
            materialized = DefinitionMaterialization.model_validate(self.write_interface(request).model_dump())
            if not materialized.artifacts or not any(
                artifact.digest == request.source_digest for artifact in materialized.artifacts
            ):
                raise ValueError("formal writer must return an artifact binding the exact interface source")
            if any(reference.subject != representation.ref for reference in materialized.evidence):
                raise ValueError("interface evidence belongs to a different exact representation")
        detail = "Required interface prerequisites remain open" if children or missing else "Minimal interface written; B2 acceptance remains required"
        assessment = ProjectItem(id=f"{obligation.id}:interface-assessment", kind=ProjectItemKind.RESEARCH_NOTE,
            name=f"Lean interface for {representation.name}", origin=ProjectOrigin.GENERATED_LOCAL,
            context=representation.context, artifacts=materialized.artifacts,
            research=ResearchState(status="proposed", reason=detail, author=f"{self.model.provider}:{self.model.model}"),
            semantics=(("plan", plan.model_dump_json()), ("model", self.model.model_dump_json()),
                *((("source", source),) if source is not None else ())))
        return ResolverResult(records=(assessment,), children=children,
                              evidence=materialized.evidence, detail=detail)

    @staticmethod
    def _closure(plan: InterfacePlan) -> tuple[tuple[InterfaceField, ...], tuple[str, ...]]:
        available = {field.name: field for field in plan.fields}
        visited: set[str] = set()
        visiting: set[str] = set()
        ordered = []
        missing = []

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError("interface field dependency cycle")
            if name in visited:
                return
            visiting.add(name)
            field = available.get(name)
            if field is None:
                missing.append(name)
            else:
                for dependency in field.depends_on:
                    visit(dependency)
                ordered.append(field)
            visiting.remove(name)
            visited.add(name)

        for name in plan.required:
            visit(name)
        return tuple(ordered), tuple(missing)

    def _children(self, snapshot: LedgerSnapshot, parent: Obligation,
                  requirements: list[InterfaceRequirement]) -> tuple[Obligation, ...]:
        pending = []
        seen = set()
        known_ids = {record.id for record in snapshot.records}
        for requirement in requirements:
            identity = json_digest(requirement.model_dump(mode="json"))
            if identity in seen:
                continue
            seen.add(identity)
            child = Obligation(id=f"{parent.id}:interface:{identity}", item=parent.item,
                kind=requirement.kind, scope=parent.scope, context=parent.context, reason=requirement.reason)
            if child.id in known_ids:
                current = snapshot.head(child.id)
                if not isinstance(current, Obligation) or any(
                    getattr(current, key) != getattr(child, key) for key in ("item", "kind", "scope", "context", "reason")
                ):
                    raise ValueError("interface prerequisite identity changed scope, context or subject")
                if (current.status == ObligationStatus.RESOLVED and current.resolution is not None
                        and self.policy.is_accepted(snapshot, current.resolution)):
                    continue
                child = current
            pending.append(child)
        return tuple(pending)
