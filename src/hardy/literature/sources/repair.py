"""Bounded model-assisted structural repair.

A model may classify or re-bound material it was actually shown, and nothing
else. `weak_regions` names the windows the deterministic pass could not settle:
unknown text that looks structured, and units whose boundary is only
probable. A repairer returns a proposal whose every unit lies inside the
window it received; `apply_repair` refuses any unit that reaches outside, then
builds a new tree version that supersedes the old one, keeps every node the
window did not touch, and records the repair as a diagnostic so a later
reader can see which nodes a model proposed. The old tree is never modified.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from hardy.foundation.values import FrozenModel, json_digest

from .contracts import (
    CONTAINER_KINDS,
    STATEMENT_KINDS,
    Diagnostic,
    NodeKind,
    SourceEdge,
    SourceEdgeKind,
    SourceTree,
)
from .trees import Unit, _stamp, node_record

STRUCTURE_HINT = re.compile(r"\b(THEOREM|LEMMA|PROPOSITION|COROLLARY|DEFINITION|Theorem|Lemma|Proposition|Corollary|Definition|Proof|PROOF)\b")


class RepairWindow(FrozenModel):
    artifact_sha256: str
    tree: str
    node: str
    representation: str
    start: int
    end: int
    text: str
    reason: str


class ProposedUnit(FrozenModel):
    kind: NodeKind
    start: int
    end: int
    number: str | None = None
    title: str | None = None
    label: str | None = None
    boundary_status: str = "probable"
    statement_end: int | None = None
    proof_of: str | None = None   # number of the statement this proof proves, when the model can tell


class RepairProposal(FrozenModel):
    window: RepairWindow
    units: tuple[ProposedUnit, ...]
    proposer: str
    proposer_version: str
    uncertainty: str = ""


Repairer = Callable[[RepairWindow], RepairProposal]


def weak_regions(tree: SourceTree, texts: Mapping[str, str]) -> tuple[RepairWindow, ...]:
    windows: list[RepairWindow] = []
    for node in tree.nodes:
        part = node.span.ranges[0]
        text = texts.get(part.representation)
        if text is None:
            continue
        window_text = text[part.start:part.end]
        if node.kind is NodeKind.UNKNOWN and STRUCTURE_HINT.search(window_text):
            reason = "unclassified text mentions a structural keyword"
        elif node.boundary_status != "high" and node.kind not in CONTAINER_KINDS:
            reason = f"{node.kind.value} boundary is {node.boundary_status}"
        else:
            continue
        windows.append(RepairWindow(artifact_sha256=tree.artifact_sha256, tree=tree.id, node=node.id, representation=part.representation,
                                    start=part.start, end=part.end, text=window_text, reason=reason))
    return tuple(windows)


def check_proposal(proposal: RepairProposal) -> tuple[Diagnostic, ...]:
    """Every proposed unit must lie inside the window; anything else is refused."""
    problems: list[Diagnostic] = []
    window = proposal.window
    for index, unit in enumerate(proposal.units):
        if unit.start < window.start or unit.end > window.end or unit.start > unit.end:
            problems.append(Diagnostic(code="repair_outside_window", severity="error",
                                       detail=f"unit {index} spans {unit.start}:{unit.end} outside the window {window.start}:{window.end}"))
        if unit.statement_end is not None and not (unit.start <= unit.statement_end <= unit.end):
            problems.append(Diagnostic(code="repair_statement_end", severity="error", detail=f"unit {index} statement end lies outside the unit"))
        if unit.kind in CONTAINER_KINDS:
            problems.append(Diagnostic(code="repair_container", severity="error", detail=f"unit {index} proposes a {unit.kind.value}; repair reclassifies leaves only"))
        if unit.number and not re.fullmatch(r"[A-Z]?[0-9]+(?:\.[0-9]+)*", unit.number):
            problems.append(Diagnostic(code="repair_number", severity="error", detail=f"unit {index} number {unit.number!r} is not a printed-style number"))
        if unit.number and unit.number not in window.text:
            problems.append(Diagnostic(code="repair_number_absent", severity="error", detail=f"unit {index} number {unit.number!r} does not appear in the window"))
    return tuple(problems)


def apply_repair(tree: SourceTree, proposal: RepairProposal, texts: Mapping[str, str]) -> SourceTree | tuple[Diagnostic, ...]:
    problems = check_proposal(proposal)
    if problems:
        return problems
    window = proposal.window
    if window.tree != tree.id or window.artifact_sha256 != tree.artifact_sha256:
        return (Diagnostic(code="repair_wrong_tree", severity="error", detail="the proposal names a different tree"),)
    text = texts.get(window.representation)
    if text is None or text[window.start:window.end] != window.text:
        return (Diagnostic(code="repair_window_drift", severity="error", detail="the window text no longer matches the representation"),)
    try:
        target = tree.node(window.node)
    except KeyError:
        return (Diagnostic(code="repair_unknown_node", severity="error", detail=f"node {window.node} is not in tree {tree.id}"),)
    kept = [n for n in tree.nodes if n.id != target.id]
    taken = {n.id for n in kept}
    page_spans = None
    provenance = f"{proposal.proposer}/{proposal.proposer_version} repair of {tree.id}"
    new_nodes = []
    for unit in sorted(proposal.units, key=lambda u: u.start):
        record = node_record(tree.artifact_sha256, window.representation, text,
                             Unit(kind=unit.kind, start=unit.start, end=unit.end, title=unit.title, number=unit.number,
                                  number_origin="explicit" if unit.number else "none", label=unit.label, boundary=unit.boundary_status,
                                  statement_end=unit.statement_end if unit.kind in STATEMENT_KINDS else None),
                             parent=target.parent, start=unit.start, end=unit.end, order=0, page_spans=page_spans, taken=taken, provenance=provenance)
        taken.add(record.id)
        new_nodes.append((unit, record))
    nodes = sorted(kept + [r for _, r in new_nodes], key=lambda n: n.span.ranges[0].start)
    counters: dict[str | None, int] = {}
    renumbered = []
    for node in nodes:
        order = counters.get(node.parent, 0)
        counters[node.parent] = order + 1
        renumbered.append(node.model_copy(update={"order": order}) if node.order != order else node)
    edges = [e for e in tree.edges if e.source != target.id and e.target != target.id]
    edges += [SourceEdge(kind=SourceEdgeKind.CONTAINS, source=r.parent, target=r.id) for _, r in new_nodes if r.parent]
    by_number = {n.number: n for n in renumbered if n.kind in STATEMENT_KINDS and n.number}
    for unit, record in new_nodes:
        if unit.kind is NodeKind.PROOF and unit.proof_of and unit.proof_of in by_number:
            edges.append(SourceEdge(kind=SourceEdgeKind.PROOF_OF, source=record.id, target=by_number[unit.proof_of].id))
    diagnostics = tuple(d for d in tree.diagnostics) + (Diagnostic(
        code="model_repair", severity="info",
        detail=f"{proposal.proposer}/{proposal.proposer_version} reclassified window {window.start}:{window.end} into "
               f"{len(new_nodes)} unit(s); uncertainty: {proposal.uncertainty or 'not stated'}",
        representation=window.representation,
    ),)
    seed = json_digest([tree.artifact_sha256, tree.builder, tree.builder_version, [n.model_dump(mode="json") for n in renumbered], [e.model_dump(mode="json") for e in edges]])
    return SourceTree(
        id=f"tree-{seed[:16]}", artifact_sha256=tree.artifact_sha256, version=tree.version + 1, builder=tree.builder,
        builder_version=tree.builder_version, representations=tree.representations, nodes=tuple(renumbered), edges=tuple(edges),
        diagnostics=diagnostics, supersedes=tree.id, built_at=_stamp(),
    )
