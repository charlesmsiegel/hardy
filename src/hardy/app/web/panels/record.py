"""Read-only views of the project ledger."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from hardy.app.web.panels import vocabulary
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation
from hardy.workflows.ledger.store import LedgerStore

#: How much of a ledger item's statement the graph panel carries per node. The
#: graph is a map of the project, not a reader for full statements -- `tex/`
#: and the lean tree already serve those in full.
STATEMENT_LIMIT = 400


def graph(problem: Path) -> dict[str, Any]:
    """The project ledger's current heads and relations, with staleness against them.

    An edge is stale when either endpoint's pinned digest no longer matches
    that item's current head -- the relation was recorded against a version of
    the item that has since been revised, and nothing has re-checked it.
    """
    snapshot = LedgerStore(problem).read()
    heads = {item.id: item for item in snapshot.current(ProjectItem)}
    counts: dict[str, Counter[str]] = {}
    for obligation in snapshot.current(Obligation):
        # The exact status, not a bucket. Six values go in and six come out:
        # `investigating` and `blocked` are not `other`, and a reader deciding
        # whether to trust an item needs to know which one it is.
        counts.setdefault(obligation.item.id, Counter())[obligation.status.value] += 1
    nodes = []
    for item in heads.values():
        statement = item.statement or ""
        nodes.append({
            "id": item.id, "digest": item.digest, "kind": item.kind.value, "name": item.name,
            "statement": statement[:STATEMENT_LIMIT], "origin": item.origin.value,
            "evidence": sorted({evidence.kind.value for evidence in item.evidence}),
            "artifacts": [artifact.uri for artifact in item.artifacts],
            "research": item.research.status if item.research else None,
            "family": vocabulary.family(item.kind),
            "obligations": dict(counts.get(item.id, Counter())),
        })
    edges = []
    for relation in snapshot.current(Relation):
        def stale(ref) -> bool:
            head = heads.get(ref.id)
            return head is None or head.digest != ref.digest

        edges.append({
            "id": relation.id, "kind": relation.kind.value, "source": relation.source.id,
            "target": relation.target.id, "evidence": sorted({evidence.kind.value for evidence in relation.evidence}),
            "stale": stale(relation.source) or stale(relation.target),
            "style": vocabulary.edge_style(relation.kind),
        })
    return {"nodes": nodes, "edges": edges, "revision": snapshot.revision}
