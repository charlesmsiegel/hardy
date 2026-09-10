"""Bounded source expansion for Referee; coverage never confers citation authority.

The named reader supplies semantic uses of an existing C2 source, authenticated
against its recorded bytes. Child checks reuse Referee/C2 obligations and B2.
Only CITES associations are added: recursive coverage is distinct from the
mathematical dependency graph and acceptance of a direct citation contract.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from typing import Literal

from hardy.foundation.values import json_digest
from hardy.literature.manuscript import SourceSpan, inventory
from hardy.workflows.ledger.contracts import (
    CitationContract,
    ProjectItem,
    Relation,
    Scope,
    VersionRef,
)
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel

MAX_SOURCE_FILES = 128
MAX_SOURCE_BYTES = 2_000_000


@dataclass(frozen=True)
class CitationUse:
    span: SourceSpan
    use_site: VersionRef
    required_claim: VersionRef


@dataclass(frozen=True)
class CitationAudit:
    use: CitationUse
    obligation: VersionRef
    contracts: tuple[CitationContract, ...]
    checked: bool
    outstanding: tuple[VersionRef, ...]


@dataclass(frozen=True)
class CitationExpansion:
    """An attributed reading of an existing exact C2 source, or its absence.

Sources must be the complete TeX source set pinned on the source item's C2
provenance. Records may propose new external requirements, never update them.
An empty source signals unavailable acquisition, not a citation-free paper.
Expansion accepts at most 128 source files and 2,000,000 UTF-8 source bytes.
"""

    contract: VersionRef
    scope: VersionRef
    revision: int
    source: VersionRef | None = None
    sources: Mapping[str, str] = field(default_factory=dict)
    uses: tuple[CitationUse, ...] = ()
    records: tuple[ProjectItem, ...] = ()
    model: RepresentationModel | None = None
    detail: str = ""


@dataclass(frozen=True)
class RecursiveCitationAudit:
    contract: VersionRef | None
    parent: VersionRef | None
    depth: int
    status: Literal["expanded", "depth_limit", "node_limit", "cycle", "missing_reader",
                    "unavailable", "no_contract"]
    children: tuple[CitationAudit, ...] = ()
    unmapped: tuple[SourceSpan, ...] = ()
    limitations: tuple[str, ...] = ()
    detail: str = ""
    model: RepresentationModel | None = None


def validate_expansion(snapshot: LedgerSnapshot, contract: CitationContract, scope: Scope,
                       result: CitationExpansion) -> tuple[ProjectItem, tuple[SourceSpan, ...], tuple[str, ...]]:
    if (result.contract, result.scope, result.revision) != (contract.ref, scope.ref, snapshot.revision):
        raise ValueError("stale or foreign citation expansion contract, scope or revision")
    source = snapshot.get(result.source)
    if (not isinstance(source, ProjectItem) or source.kind != "external_result"
            or snapshot.head(source.id) != source or contract.source_statement not in source.artifacts
            or sha256((source.statement or "").encode()).hexdigest() != contract.source_statement.digest):
        raise ValueError("citation expansion must bind the exact existing C2 source")
    if not any(isinstance(r, Relation) and r.kind == "cites" and r.source == contract.use_site
               and r.target == source.ref for r in snapshot.current(Relation)):
        raise ValueError("citation expansion source lacks its exact C2 use provenance")
    if result.model is None:
        raise ValueError("citation expansion requires attributed semantic reading")
    prefix = f"arxiv:{contract.paper_id}{contract.paper_version}/source/"
    artifacts = {a.uri.removeprefix(prefix): a for a in source.artifacts if a.uri.startswith(prefix)}
    if not result.sources or any(path not in artifacts or
            sha256(text.encode()).hexdigest() != artifacts[path].digest for path, text in result.sources.items()):
        raise ValueError("citation expansion source bytes differ from exact C2 provenance")
    # Coverage is limited to C2's pinned source files, which may be only the
    # theorem file. This does not establish whole-paper inventory completeness.
    if set(result.sources) != set(artifacts):
        raise ValueError("citation expansion omits pinned source files")
    scanned = inventory(result.sources)
    heads = {record.id: record for record in snapshot.records}
    required = {use.required_claim for use in result.uses}
    for record in result.records:
        if (not isinstance(record, ProjectItem) or record.kind != "external_result"
                or record.ref not in required or record.context != source.context
                or record.evidence or record.research is not None):
            raise ValueError("citation expansion cannot write authority or unrelated semantic work")
        if record.id in heads and heads[record.id] != record:
            raise ValueError("citation expansion cannot overwrite existing semantic work")
        heads[record.id] = record
    spans = {c.key_span for c in scanned.citations}
    if len({use.span for use in result.uses}) != len(result.uses):
        raise ValueError("duplicate citation expansion use")
    for use in result.uses:
        item = heads.get(use.required_claim.id)
        if (use.use_site != source.ref or use.span not in spans or not isinstance(item, ProjectItem)
                or item.ref != use.required_claim or item.kind != "external_result"
                or item.context != source.context or item.ref in scope.must_prove):
            raise ValueError("citation expansion use must bind exact source and external requirement")
    return source, tuple(c.key_span for c in scanned.citations if c.key_span not in {u.span for u in result.uses}), tuple(
        finding.detail for finding in scanned.unsupported)


def audit_recursive(*, store: LedgerStore, policy: LedgerPolicy, scope: Scope,
                    roots: tuple[CitationAudit, ...], depth: int, max_nodes: int,
                    expand: Callable[[LedgerSnapshot, CitationContract, Scope], CitationExpansion] | None,
                    check: Callable[[CitationUse, Scope], VersionRef],
                    audit: Callable[[LedgerSnapshot, CitationUse, VersionRef], CitationAudit]
                    ) -> tuple[RecursiveCitationAudit, ...]:
    if depth == 0:
        return ()
    nodes: list[RecursiveCitationAudit] = []
    reserved = 0

    def visit(contract: CitationContract, parent: VersionRef | None, level: int, ancestry: tuple, *, claimed=False) -> None:
        nonlocal reserved
        key = (contract.paper_id, contract.paper_version, contract.source_statement)
        common = dict(contract=contract.ref, parent=parent, depth=level)
        if key in ancestry:
            nodes.append(RecursiveCitationAudit(**common, status="cycle", detail="Exact source repeats on this citation path."))
            return
        if level >= depth:
            nodes.append(RecursiveCitationAudit(**common, status="depth_limit", detail="Requested citation depth reached."))
            return
        if not claimed and reserved >= max_nodes:
            nodes.append(RecursiveCitationAudit(**common, status="node_limit", detail="Citation expansion node budget reached."))
            return
        if not claimed:
            reserved += 1
        if expand is None:
            nodes.append(RecursiveCitationAudit(**common, status="missing_reader", detail="Citation expansion reader unavailable."))
            return
        snapshot = store.read()
        if snapshot.head(scope.id) != scope or snapshot.head(contract.id) != contract:
            raise ValueError("stale citation expansion subject or scope")
        result = expand(snapshot, contract, scope)
        if not isinstance(result, CitationExpansion):
            raise ValueError("citation expansion reader must return CitationExpansion")
        if store.read().revision != snapshot.revision:
            raise ValueError("stale citation expansion callback: ledger changed during reading")
        if (result.contract, result.scope, result.revision) != (contract.ref, scope.ref, snapshot.revision):
            raise ValueError("stale or foreign citation expansion contract, scope or revision")
        if result.source is None:
            if result.records or result.uses or result.sources:
                raise ValueError("unavailable citation expansion cannot supply semantic work")
            nodes.append(RecursiveCitationAudit(**common, status="unavailable", detail=result.detail or "Exact cited source unavailable."))
            return
        if (len(result.sources) > MAX_SOURCE_FILES
                or sum(len(text) for text in result.sources.values()) > MAX_SOURCE_BYTES
                or sum(len(text.encode()) for text in result.sources.values()) > MAX_SOURCE_BYTES):
            raise ValueError("citation expansion source input bound exceeded")
        result = replace(result, sources=dict(result.sources))
        source, unmapped, limitations = validate_expansion(snapshot, contract, scope, result)
        # A fan-out cannot cause unbounded C2 work. Each child consumes one
        # remaining node slot; omitted uses remain an explicit node cutoff.
        available = max_nodes - reserved
        selected = result.uses[:available]
        reserved += len(selected)
        selected_refs = {use.required_claim for use in selected}
        additions = [r for r in result.records if r.ref in selected_refs
                     and not any(old.id == r.id for old in snapshot.records)]
        note = ProjectItem(id="referee:expansion:" + json_digest((contract.ref.model_dump(), scope.ref.model_dump(),
            source.ref.model_dump(), result.model.model_dump(), result.detail, [(u.span.path, u.span.digest, u.span.start,
                u.span.end, u.required_claim.model_dump()) for u in selected])),
            kind="research_note", name="Source-bound citation expansion", origin="generated_local",
            artifacts=source.artifacts, semantics=(("model", result.model.model_dump_json()),
                ("detail", result.detail or "Explicit semantic citation uses"),
                ("contract", contract.ref.model_dump_json()), ("scope", scope.ref.model_dump_json()),
                ("source", source.ref.model_dump_json()),
                ("uses", json.dumps([{"span": asdict(u.span), "use_site": u.use_site.model_dump(),
                    "required_claim": u.required_claim.model_dump()} for u in selected])),))
        if not any(old.id == note.id for old in snapshot.records):
            additions.append(note)
            additions.append(Relation(id=note.id + ":reading", kind="cites", source=contract.ref, target=note.ref))
        if additions:
            store.append(additions, expected_revision=snapshot.revision, validate=policy.validate)
        children = []
        for use in selected:
            reference = check(use, scope)
            current = store.read()
            link = Relation(id="referee:recursive-link:" + json_digest((contract.ref.model_dump(), reference.model_dump())),
                            kind="cites", source=contract.ref, target=reference)
            if not any(old.id == link.id for old in current.records):
                store.append((link,), expected_revision=current.revision, validate=policy.validate)
            children.append(audit(store.read(), use, reference))
        nodes.append(RecursiveCitationAudit(**common, status="expanded", children=tuple(children),
            unmapped=unmapped, limitations=limitations, detail=result.detail, model=result.model))
        if len(selected) < len(result.uses):
            nodes.append(RecursiveCitationAudit(**common, status="node_limit", detail="Citation child uses omitted by node budget."))
        for child in children:
            if not child.contracts:
                nodes.append(RecursiveCitationAudit(contract=None, parent=contract.ref, depth=level + 1,
                    status="no_contract", children=(child,), detail="C2 could not produce an exact child citation contract."))
            for index, candidate in enumerate(child.contracts):
                visit(candidate, contract.ref, level + 1, (*ancestry, key), claimed=index == 0)

    for root in roots:
        if not root.contracts:
            nodes.append(RecursiveCitationAudit(contract=None, parent=None, depth=0,
                status="no_contract", children=(root,), detail="No exact direct citation contract is available to expand."))
        for contract in root.contracts:
            visit(contract, None, 0, ())
    return tuple(nodes)
