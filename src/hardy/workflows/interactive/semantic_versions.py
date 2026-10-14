"""Revalidate exact semantic dependencies and retained source bytes on every read."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from hardy.foundation.files import read_bytes
from hardy.workflows.interactive.semantic_records import SourceRegion
from hardy.workflows.ledger.contracts import (
    CitationContract,
    MathematicalContext,
    ProjectItem,
    ProjectItemKind,
    Relation,
    RelationKind,
    VersionRef,
)
from hardy.workflows.ledger.graph import DEPENDENCIES, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot


def source_text(problem: Path, snapshot: LedgerSnapshot, source: VersionRef) -> str:
    record = snapshot.get(source)
    if not isinstance(record, ProjectItem) or record.kind != ProjectItemKind.SOURCE or len(record.artifacts) != 1:
        raise ValueError("source reference must name a retained semantic source")
    artifact = record.artifacts[0]
    if not artifact.uri.startswith("semantic-sources/"):
        raise ValueError("source must belong to the retained semantic-source owner")
    data = read_bytes(problem, artifact.uri)
    if sha256(data).hexdigest() != artifact.digest:
        raise ValueError("source bytes no longer authenticate their exact version")
    return data.decode("utf-8")


def read_region(problem: Path, snapshot: LedgerSnapshot, region: SourceRegion) -> str:
    text = source_text(problem, snapshot, region.source)
    if region.end > len(text):
        raise ValueError("source region runs past its exact source version")
    return text[region.start:region.end]


def relevant_versions(problem: Path, snapshot: LedgerSnapshot, subject: VersionRef,
                      extra: tuple[VersionRef, ...] = (), *,
                      sources: tuple[SourceRegion, ...] = ()) -> tuple[VersionRef, ...]:
    """Current exact closure including context members, source mappings and citations.

    Supersession retains addressability but explicitly withdraws current judgment
    authority. Activating an unrelated context does not mutate an old context.
    """
    graph = LedgerGraph(snapshot)
    selected: set[VersionRef] = set()
    pending = [subject, *extra, *(region.source for region in sources)]
    superseded = {r.target for r in snapshot.current(Relation) if r.kind == RelationKind.SUPERSEDES}
    while pending:
        ref = pending.pop()
        if ref in selected:
            continue
        value = snapshot.get(ref)
        if snapshot.head(ref.id).ref != ref or ref in superseded:
            raise ValueError("semantic dependency is stale or superseded")
        selected.add(ref)
        pending.extend(graph.dependency_closure(ref))
        if isinstance(value, ProjectItem):
            if value.context:
                pending.append(value.context)
            if value.kind == ProjectItemKind.SOURCE:
                source_text(problem, snapshot, ref)
            for key, payload in value.semantics:
                if key == "source-region":
                    region = SourceRegion.model_validate_json(payload)
                    read_region(problem, snapshot, region)
                    pending.append(region.source)
            pending.extend(graph.dependency_closure(ref))
        elif isinstance(value, MathematicalContext):
            pending.extend((*value.declarations, *value.bindings))
            if value.parent:
                pending.append(value.parent)
        elif isinstance(value, CitationContract):
            pending.extend((value.use_site, value.required_claim))
            artifact = value.source_statement
            if (artifact.uri.startswith("semantic-sources/")
                    and sha256(read_bytes(problem, artifact.uri)).hexdigest() != artifact.digest):
                raise ValueError("citation source bytes changed")
        # Relationship edits and added dependencies are part of the judgment's
        # inputs. CONTAINS covers argument steps; SUPPORTS pins its exact conclusion.
        for edge in graph.relations:
            if edge.source == ref and edge.kind in DEPENDENCIES | {RelationKind.CONTAINS, RelationKind.SUPPORTS}:
                pending.extend((edge.ref, edge.target))
    for region in sources:
        read_region(problem, snapshot, region)
    return tuple(sorted(selected, key=lambda ref: (ref.id, ref.digest)))
