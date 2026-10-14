"""Current source-mapping interpretation, separate from ledger/evidence authority.

Only exact source-owned relations are selected here. All retained records and
policy inputs remain in the authoritative snapshot; independent relations are
unaffected. Latest mapping decisions never fall back to older approvals.
"""
from __future__ import annotations

from hardy.foundation.journal import record_digest
from hardy.workflows.interactive.manuscript_records import IntakeProposal, IntakeReview
from hardy.workflows.ledger.contracts import Relation, VersionRef
from hardy.workflows.ledger.graph import LedgerGraph


def claim_use_key(key, mappings):
    visited = set()
    while key not in visited:
        visited.add(key)
        value = mappings.get(key)
        if value is None:
            break
        if value.kind == "claim":
            return key
        key = value.target if value.kind == "argument" else value.argument if value.kind == "step" else None
    raise ValueError("citation use must name a claim or an exact argument/step supporting a claim")


def _mapping_edges(proposal, bindings):
    """Reproduce only relations created by this exact extraction's materializer."""
    mappings = {value.key: value for value in proposal.mappings}
    edges = {}
    for key, value in mappings.items():
        required = (key, *(ref for ref in (value.target, value.source, value.argument, value.context) if ref))
        if any(ref not in bindings for ref in required):
            continue
        identity, subject = proposal.id + ":" + key, bindings[key]
        records = []
        if value.kind == "argument":
            records.append(Relation(id=identity + ":conclusion", kind="supports", source=subject, target=bindings[value.target]))
        elif value.kind in {"claim", "step"}:
            if value.target:
                records.append(Relation(id=identity + ":supports", kind="supports", source=subject, target=bindings[value.target]))
            if value.argument:
                records.append(Relation(id=identity + ":contained", kind="contains", source=bindings[value.argument], target=subject))
        elif value.kind == "dependency":
            records.extend((Relation(id=identity, kind="depends_on", source=bindings[value.source], target=bindings[value.target]),
                Relation(id=identity + ":intermediate", kind="supports", source=bindings[value.target], target=bindings[value.source])))
        elif value.kind == "citation":
            claim = claim_use_key(value.source, mappings)
            if claim in bindings:
                records.append(Relation(id=identity + ":use", kind="depends_on", source=bindings[claim], target=subject,
                    mappings=(("exact-source-use", bindings[value.source].model_dump_json()),)))
        edges.update((record.id, record.ref) for record in records)
    return edges


def _mapping_edge_ids(proposal):
    """Withhold orphan source interpretations until a review is committed."""
    for value in proposal.mappings:
        identity = proposal.id + ":" + value.key
        if value.kind == "argument":
            yield identity + ":conclusion"
        elif value.kind in {"claim", "step"}:
            if value.target:
                yield identity + ":supports"
            if value.argument:
                yield identity + ":contained"
        elif value.kind == "dependency":
            yield identity
            yield identity + ":intermediate"
        elif value.kind == "citation":
            yield identity + ":use"


def current_mapping_relations(snapshot, journal):
    """Current source interpretation, without changing policy's ledger snapshot.

    Only exact deterministic edges authenticated by historical mapping reviews
    are gated. Independent authored relationships retain their ordinary meaning.
    A stale latest decision never falls back to an older source interpretation.
    """
    proposals = {VersionRef(id=value.id, digest=record_digest(value)): value for value in journal.of(IntakeProposal)}
    owned = {identity for proposal in proposals.values() for identity in _mapping_edge_ids(proposal)}
    selected, latest = {}, {}
    for review in journal.of(IntakeReview):
        proposal = proposals.get(review.proposal)
        if proposal is not None:
            latest[review.proposal] = review
    superseded = {edge.target for edge in snapshot.current(Relation) if edge.kind == "supersedes"}
    for ref, review in latest.items():
        if all(snapshot.head(value.id).ref == value and value not in superseded for value in review.inputs):
            selected.update(_mapping_edges(proposals[ref], review.bindings))
    return tuple(edge for edge in LedgerGraph(snapshot).relations
        if edge.id not in owned or selected.get(edge.id) == edge.ref)
