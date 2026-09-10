"""Ledger referential integrity, independent of mathematical evidence policy.

An atomic batch may contain mutually owned context/declaration records. Exact
references must resolve across the resulting snapshot; immutable local context
identities may only be extended through children. Evidence is authenticated by B2.
"""
from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from hardy.workflows.ledger.contracts import (
    CitationContract,
    LedgerRecord,
    MathematicalContext,
    Obligation,
    ProjectItem,
    ProjectItemKind,
    Relation,
    Resolution,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.state import LedgerSnapshot

RECORD_TYPES = {c.__name__: c for c in (
    ProjectItem, MathematicalContext, ScopedBinding, Scope, Obligation,
    Resolution, Relation, CitationContract,
)}


def references(value: object) -> Iterator[VersionRef]:
    if isinstance(value, VersionRef):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from references(getattr(value, name))
    elif isinstance(value, tuple | list):
        for child in value:
            yield from references(child)


def validate_structure(before: LedgerSnapshot, after: LedgerSnapshot) -> None:
    additions = after.records[len(before.records):]
    if len({r.id for r in additions}) != len(additions):
        raise ValueError("one transaction cannot write an identity twice")
    for record in additions:
        for ref in references(record):
            after.get(ref)
        old = next((r for r in before.current(LedgerRecord) if r.id == record.id), None)
        if old:
            if type(old) is not type(record):
                raise ValueError("record identity cannot change category")
            if old == record:
                raise ValueError("duplicate record revision")
            if isinstance(record, MathematicalContext | ScopedBinding) or (
                isinstance(record, ProjectItem) and record.kind == ProjectItemKind.DECLARATION
            ):
                raise ValueError("mathematical contexts, declarations and bindings are immutable")
            if isinstance(old, ProjectItem) and old.kind != record.kind:
                raise ValueError("project item identity cannot change kind")
            if isinstance(old, ProjectItem):
                if old.context != record.context:
                    raise ValueError("item context is immutable; create a distinct claim")
                if old.kind == ProjectItemKind.CONJECTURE and old.statement != record.statement:
                    raise ValueError("corrected conjecture must be a new superseding item")
        if isinstance(record, Obligation):
            if record.previous != (old.ref if old else None):
                raise ValueError("obligation previous must name its current predecessor")
            if not isinstance(after.get(record.item), ProjectItem):
                raise ValueError("obligation item reference must name a project item")
            if after.get(record.scope.ref) != record.scope:
                raise ValueError("obligation scope reference is not stored")
            if record.context is not None and not isinstance(after.get(record.context), MathematicalContext):
                raise ValueError("obligation context reference must name a mathematical context")
            if old and (old.item != record.item or old.kind != record.kind or old.context != record.context):
                raise ValueError("obligation revisions must preserve their subject, kind and context")
        if isinstance(record, Resolution):
            obligation = after.get(record.obligation)
            if not isinstance(obligation, Obligation) or obligation.item != record.item:
                raise ValueError("resolution must reference its exact obligation subject")
        if (isinstance(record, ProjectItem) and record.context is not None
                and not isinstance(after.get(record.context), MathematicalContext)):
            raise ValueError("item context reference must name a mathematical context")
        if isinstance(record, MathematicalContext):
            _context(after, record)
        if isinstance(record, ScopedBinding):
            owner = after.head(record.context_id)
            if not isinstance(owner, MathematicalContext) or record.ref not in owner.bindings:
                raise ValueError("binding must belong to its owning context")
        if isinstance(record, ProjectItem) and record.declaration:
            owner = after.head(record.declaration.context_id)
            if not isinstance(owner, MathematicalContext) or record.ref not in owner.declarations:
                raise ValueError("declaration must belong to its owning context")
    if after.active_context is not None and not isinstance(after.get(after.active_context), MathematicalContext):
        raise ValueError("active context reference must name a mathematical context")


def _context(snapshot: LedgerSnapshot, context: MathematicalContext) -> None:
    lineage = []
    cursor = context
    while True:
        if cursor.ref in {c.ref for c in lineage}:
            raise ValueError("mathematical context parent cycle")
        lineage.append(cursor)
        if cursor.parent is None:
            break
        parent = snapshot.get(cursor.parent)
        if not isinstance(parent, MathematicalContext):
            raise ValueError("context parent reference must name a context")
        cursor = parent
    visible = {ref for c in lineage for ref in (*c.declarations, *c.bindings)}
    symbols: set[str] = set()
    for ref in context.declarations:
        declaration = snapshot.get(ref)
        if not isinstance(declaration, ProjectItem) or declaration.declaration is None:
            raise ValueError("context declaration reference must name a declaration")
        details = declaration.declaration
        if details.context_id != context.id:
            raise ValueError("declaration owner differs from context")
        if details.symbol in symbols:
            raise ValueError("duplicate printed symbol in one context")
        symbols.add(details.symbol)
        for dependency in details.dependencies:
            target = snapshot.get(dependency)
            if (isinstance(target, ScopedBinding) or isinstance(target, ProjectItem) and target.declaration) and dependency not in visible:
                raise ValueError("declaration dependency is outside mathematical context")
    for ref in context.bindings:
        binding = snapshot.get(ref)
        if not isinstance(binding, ScopedBinding) or binding.context_id != context.id:
            raise ValueError("context binding reference has wrong owner")
        if binding.symbol in symbols:
            raise ValueError("duplicate printed symbol in one context")
        symbols.add(binding.symbol)
        if binding.target:
            target = snapshot.get(binding.target)
            if (isinstance(target, ScopedBinding) or isinstance(target, ProjectItem) and target.declaration) and binding.target not in visible:
                raise ValueError("binding target is outside mathematical context")

