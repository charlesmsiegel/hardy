"""Deterministic SourceTree reconstruction, mechanical validation and versioned storage.

A tree is built from observations over one normalized-text representation of
one artifact, plus native outline entries anchored to pages. Headings nest by
level; statements run to the next structural boundary; a proof runs to its
end marker when one is found and is otherwise marked probable; text no rule
claims becomes an `unknown` node rather than a guessed classification; a gap
in numbering is a diagnostic, never a node. In-text references become
`source_refers_to` edges, which say the document mentions a unit and nothing
about logical dependency.

Node identity derives from content (artifact, kind, explicit number, and the
digest of the statement text), so an improved extraction that leaves a
statement unchanged leaves its id and version unchanged, and the tree that
refines an older one names it in `supersedes` without touching it. Trees are
written once; which tree is preferred for new retrieval is a journaled
decision beside them, as are cross-artifact correspondences.
"""
from __future__ import annotations

import hashlib
import heapq
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hardy.foundation.files import LayoutError, WriteGuard, read_text
from hardy.foundation.journal import Journal, JournalSnapshot
from hardy.foundation.values import json_digest

from .contracts import (
    CONTAINER_KINDS,
    STATEMENT_KINDS,
    DerivedRepresentation,
    Diagnostic,
    NodeKind,
    ObservationKind,
    PageRegion,
    RepresentationMapping,
    RepresentationSpan,
    SourceAnchor,
    SourceArtifact,
    SourceCorrespondence,
    SourceEdge,
    SourceEdgeKind,
    SourceNode,
    SourceSpan,
    SourceTree,
    StructuralObservation,
    TreePreference,
)
from .locators import text_digest

BUILDER = "hardy.trees.deterministic"
BUILDER_VERSION = "2"
LEVELS: dict[str, int] = {"part": 0, "chapter": 1, "appendix": 1, "section": 2, "subsection": 3}
LEVEL_KINDS: dict[int, NodeKind] = {0: NodeKind.PART, 1: NodeKind.CHAPTER, 2: NodeKind.SECTION, 3: NodeKind.SUBSECTION}
KINDS_BY_KEYWORD: dict[str, NodeKind] = {k.value: k for k in STATEMENT_KINDS}
NUMBERED_TITLE = re.compile(r"^(?:(?:Chapter|Part|Appendix)\s+)?(?P<number>[0-9IVXLC]+(?:\.[0-9]+)*)\.?\s+(?P<title>.+)$")
JOURNAL_TYPES = {t.__name__: t for t in (TreePreference, SourceCorrespondence)}


class TreeError(ValueError):
    """A tree that failed mechanical validation, or a store operation refused."""


@dataclass(frozen=True)
class Unit:
    """A recovered structural boundary before artifact-bound node identity is assigned."""

    kind: NodeKind
    start: int
    end: int
    title: str | None = None
    number: str | None = None
    number_origin: str = "none"
    label: str | None = None
    level: int | None = None
    boundary: str = "high"
    statement_end: int | None = None
    observations: tuple[str, ...] = ()
    confidence: float | None = None
    proof_target_number: str | None = None


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _range(observation: StructuralObservation) -> RepresentationSpan | None:
    locator = observation.anchor.locator
    return locator if isinstance(locator, RepresentationSpan) else None


def _page(observation: StructuralObservation) -> int | None:
    locator = observation.anchor.locator
    return locator.page_index if isinstance(locator, PageRegion) else None


def _boundaries(text: str, observations: list[StructuralObservation]) -> list[int]:
    """Positions where any statement, proof or heading starts, plus the end of the text."""
    starts = sorted({
        r.start for o in observations
        if (r := _range(o)) is not None and o.kind in {ObservationKind.HEADING, ObservationKind.STATEMENT_START, ObservationKind.PROOF_START}
    })
    return [*starts, len(text)]


def _next_boundary(boundaries: list[int], after: int) -> int:
    return boundaries[min(bisect_right(boundaries, after), len(boundaries) - 1)]


def _trim(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in " \t\n\f":
        end -= 1
    return end


@dataclass(frozen=True)
class _ContainerLayout:
    """Heading ancestry and bounds, used together to locate a unit's owner."""

    units: list[Unit]
    ends: dict[int, int]
    parents: dict[int, Unit | None]
    starts: list[int]

    @classmethod
    def of(cls, text: str, units: list[Unit]) -> _ContainerLayout:
        containers = [unit for unit in units if unit.kind in CONTAINER_KINDS]
        ends: dict[int, int] = {}
        parents: dict[int, Unit | None] = {}
        stack: list[Unit] = []
        for unit in containers:
            while stack and (stack[-1].level or 0) >= (unit.level or 0):
                closed = stack.pop()
                ends[id(closed)] = _trim(text, closed.start, unit.start)
            parents[id(unit)] = stack[-1] if stack else None
            stack.append(unit)
        for unit in stack:
            ends[id(unit)] = _trim(text, unit.start, len(text))
        return cls(containers, ends, parents, [unit.start for unit in containers])

    def owner_at(self, position: int) -> Unit | None:
        index = bisect_right(self.starts, position) - 1
        unit = self.units[index] if index >= 0 else None
        while unit is not None:
            if position < max(self.ends[id(unit)], unit.start + 1):
                return unit
            unit = self.parents[id(unit)]
        return None


def _uncovered_ranges(text: str, nodes: list[SourceNode], headings: list[Unit]) -> list[tuple[int, int]]:
    leaves = [node for node in nodes if node.kind not in CONTAINER_KINDS]
    covered = sorted((node.span.ranges[0].start, node.span.ranges[0].end) for node in leaves)
    covered += [(node.span.ranges[0].start, node.span.ranges[0].end) for node in nodes
                if node.kind in CONTAINER_KINDS and node.span.ranges[0].end == node.span.ranges[0].start]
    occupied = sorted(covered + [(unit.start, unit.end) for unit in headings])
    position = 0
    gaps: list[tuple[int, int]] = []
    for start, end in occupied:
        if start > position and text[position:start].strip():
            gaps.append((position, start))
        position = max(position, end)
    if position < len(text) and text[position:].strip():
        gaps.append((position, len(text)))
    return gaps


def reading_order(nodes: list[SourceNode]) -> list[SourceNode]:
    """Sort nodes by text offset and assign sibling orders, preserving ties and ids."""
    nodes.sort(key=lambda node: node.span.ranges[0].start)
    ordered: list[SourceNode] = []
    counters: dict[str | None, int] = {}
    for node in nodes:
        order = counters.get(node.parent, 0)
        counters[node.parent] = order + 1
        ordered.append(node.model_copy(update={"order": order}) if node.order != order else node)
    return ordered


def _numbering_gaps(numbers: Iterable[str]) -> list[Diagnostic]:
    groups: dict[str, list[int]] = {}
    for number in numbers:
        head, _, tail = number.rpartition(".")
        if tail.isdigit():
            groups.setdefault(head, []).append(int(tail))
    diagnostics: list[Diagnostic] = []
    for head, values in groups.items():
        values.sort()
        for a, b in zip(values, values[1:], strict=False):
            if b > a + 1:
                prefix = f"{head}." if head else ""
                diagnostics.append(Diagnostic(code="numbering_gap", severity="info",
                                              detail=f"numbering jumps from {prefix}{a} to {prefix}{b}; units between were not recovered, which is not evidence they are absent"))
    return diagnostics


def node_record(
    artifact_sha256: str, rep: str, text: str, unit: Unit, *, parent: str | None, start: int, end: int, order: int,
    page_spans: Mapping[int, RepresentationSpan] | None, taken: set[str], provenance: str,
) -> SourceNode:
    """One node with a content-derived, artifact-bound identity and exact spans."""
    statement_text = text[unit.start:(unit.statement_end or unit.end)]
    seed = f"{artifact_sha256}|{unit.kind.value}|{unit.number or ''}|{unit.title or ''}|{text_digest(statement_text)}"
    node_id = "n-" + hashlib.sha256(seed.encode()).hexdigest()[:16]
    suffix = 2
    while node_id in taken:
        node_id = "n-" + hashlib.sha256(f"{seed}|{suffix}".encode()).hexdigest()[:16]
        suffix += 1
    ranges = (RepresentationSpan(representation=rep, start=start, end=end),)
    anchors = tuple(
        SourceAnchor(artifact_sha256=artifact_sha256, locator=PageRegion(page_index=page, precision="page"), derivation="page span overlap")
        for page, span in sorted((page_spans or {}).items()) if span.start < end and start < span.end
    )
    span = SourceSpan(id=f"span-{node_id}", artifact_sha256=artifact_sha256, ranges=ranges, anchors=anchors,
                      content_sha256=text_digest(text[start:end]), node=node_id, mapping_provenance=provenance)
    statement_span = None
    if unit.statement_end is not None and unit.kind in STATEMENT_KINDS:
        statement_span = SourceSpan(id=f"stmt-{node_id}", artifact_sha256=artifact_sha256,
                                    ranges=(RepresentationSpan(representation=rep, start=unit.start, end=unit.statement_end),), anchors=anchors,
                                    content_sha256=text_digest(statement_text), node=node_id, mapping_provenance=provenance)
    # Content-based, not offset-based: a re-extraction that leaves this unit's
    # text, kind, number and parent unchanged leaves its version unchanged.
    version = json_digest([unit.kind.value, parent, span.content_sha256,
                           statement_span.content_sha256 if statement_span else None, unit.number, unit.label, unit.title])
    return SourceNode(id=node_id, version=version, kind=unit.kind, parent=parent, order=order, title=unit.title, number=unit.number,
                      number_origin=unit.number_origin, label=unit.label, span=span, statement_span=statement_span,  # type: ignore[arg-type]
                      confidence=unit.confidence, boundary_status=unit.boundary, observations=unit.observations)  # type: ignore[arg-type]


def build_tree(
    artifact: SourceArtifact, representation: DerivedRepresentation, text: str,
    observations: tuple[StructuralObservation, ...], *, page_spans: Mapping[int, RepresentationSpan] | None = None,
    previous: SourceTree | None = None, builder_version: str = BUILDER_VERSION,
) -> SourceTree:
    """Reconstruct attributable units and mention edges without inferring logical authority."""
    if representation.artifact_sha256 != artifact.sha256:
        raise TreeError("a tree is built from representations of its own artifact")
    rep = representation.id
    diagnostics: list[Diagnostic] = []
    text_obs = [o for o in observations if o.artifact_sha256 == artifact.sha256 and (r := _range(o)) is not None and r.representation == rep]
    page_obs = [o for o in observations if o.artifact_sha256 == artifact.sha256 and _page(o) is not None]
    boundaries = _boundaries(text, text_obs)
    units = _heading_units(text_obs)
    heading_starts = {u.start for u in units}
    units.extend(_outline_units(text, page_obs, page_spans, heading_starts, diagnostics))
    if any(u.start in heading_starts for u in units):
        boundaries = sorted(set(boundaries) | heading_starts)
    units.extend(_leaf_units(text, text_obs, boundaries, diagnostics))
    units.sort(key=lambda u: (u.start, 0 if u.kind in CONTAINER_KINDS else 1))
    nodes, unit_by_node = _tree_nodes(artifact.sha256, rep, text, units, page_spans=page_spans, builder_version=builder_version)
    lookup = _NodeIndex.of(nodes)
    edges = [SourceEdge(kind=SourceEdgeKind.CONTAINS, source=n.parent, target=n.id) for n in nodes if n.parent]
    edges.extend(_proof_edges(nodes, unit_by_node, lookup, diagnostics))
    edges.extend(_reference_edges(nodes, text_obs, text, lookup, diagnostics))
    diagnostics.extend(_numbering_gaps(lookup.numbers))

    seed = [artifact.sha256, BUILDER, builder_version, [n.model_dump(mode="json") for n in nodes], [e.model_dump(mode="json") for e in edges]]
    # Preserve the historical v1 identity protocol. Current trees include
    # diagnostics so reuse cannot discard newly discovered extraction gaps.
    if builder_version != "1":
        seed.append([d.model_dump(mode="json") for d in diagnostics])
    tree_seed = json_digest(seed)
    return SourceTree(
        id=f"tree-{tree_seed[:16]}", artifact_sha256=artifact.sha256, version=(previous.version + 1) if previous else 1,
        builder=BUILDER, builder_version=builder_version, representations=(rep,), nodes=tuple(nodes), edges=tuple(edges),
        diagnostics=tuple(diagnostics), supersedes=previous.id if previous else None, built_at=_stamp(),
    )


def _heading_units(text_obs: list[StructuralObservation]) -> list[Unit]:
    """Text headings win when native and heuristic observations share a start."""
    units = []
    seen_heading_starts: set[int] = set()
    for o in text_obs:
        if o.kind is not ObservationKind.HEADING:
            continue
        r = _range(o)
        assert r is not None
        if r.start in seen_heading_starts:
            continue  # a native producer and the text observer saw the same heading
        seen_heading_starts.add(r.start)
        level = LEVELS.get(o.value("level"), 2)
        units.append(Unit(kind=LEVEL_KINDS.get(level, NodeKind.SUBSECTION), start=r.start, end=r.end, title=o.value("title") or None,
                           number=o.value("number") or None, number_origin="explicit" if o.value("number") else "none",
                           level=level, observations=(o.id,), confidence=o.confidence))
    return units


def _outline_units(
    text: str, page_obs: list[StructuralObservation], page_spans: Mapping[int, RepresentationSpan] | None,
    heading_starts: set[int], diagnostics: list[Diagnostic],
) -> list[Unit]:
    """Locate native titles within their page, disclosing unmatched or unlocated entries."""
    units = []
    for o in page_obs:
        if o.kind is not ObservationKind.OUTLINE_ENTRY:
            continue
        page = _page(o)
        span = (page_spans or {}).get(page) if page is not None else None
        if span is None:
            diagnostics.append(Diagnostic(code="outline_unlocated", detail=f"outline entry {o.value('title')!r} names page {page} with no text span", severity="info", page_index=page))
            continue
        title = o.value("title")
        window = text[span.start:span.end]
        found = window.find(title) if title else -1
        if found >= 0 and span.start + found in heading_starts:
            continue  # the text heading already produced this unit
        depth = int(o.value("depth", "0") or 0)
        level = min(depth + 1, 3)
        match = NUMBERED_TITLE.match(title)
        number = match.group("number") if match else None
        if found >= 0:
            start, end, boundary = span.start + found, span.start + found + len(title), "high"
        else:
            start, end, boundary = span.start, span.start, "probable"
            diagnostics.append(Diagnostic(code="outline_title_unmatched", detail=f"outline entry {title!r} was not found on page {page}; anchored to the page start", severity="info", page_index=page))
        if start in heading_starts:
            continue
        heading_starts.add(start)
        units.append(Unit(kind=LEVEL_KINDS.get(level, NodeKind.SUBSECTION), start=start, end=end, title=(match.group("title") if match else title) or None,
                           number=number, number_origin="explicit" if number else "none", level=level, boundary=boundary,
                           observations=(o.id,), confidence=o.confidence))
    return units


def _leaf_units(text: str, text_obs: list[StructuralObservation], boundaries: list[int], diagnostics: list[Diagnostic]) -> list[Unit]:
    """Keep explicit environments and delimit statements and proofs with honest boundaries."""
    units = []
    proof_ends = sorted(r.end for o in text_obs if o.kind is ObservationKind.PROOF_END and (r := _range(o)) is not None)
    for o in text_obs:
        r = _range(o)
        assert r is not None
        if o.kind is ObservationKind.STATEMENT_START:
            kind = KINDS_BY_KEYWORD.get(o.value("kind"), NodeKind.UNKNOWN)
            limit = _next_boundary(boundaries, r.start)
            end = _trim(text, r.start, limit)
            # A statement that runs to the end of the text with no structural marker
            # after it may have swallowed following prose; say so rather than claim it.
            boundary = "high" if limit < len(text) else "probable"
            units.append(Unit(kind=kind, start=r.start, end=end, title=o.value("name") or None, number=o.value("number") or None,
                               number_origin=o.value("number_origin", "none"), label=o.value("label") or None, statement_end=end, boundary=boundary,
                               observations=(o.id,), confidence=o.confidence))
        elif o.kind is ObservationKind.ENVIRONMENT:
            # A display environment the source delimits itself: equation,
            # figure, table, diagram. Its span is exactly the environment.
            kind = {"equation": NodeKind.EQUATION, "figure": NodeKind.FIGURE, "table": NodeKind.TABLE, "diagram": NodeKind.DIAGRAM}.get(o.value("kind"), NodeKind.UNKNOWN)
            units.append(Unit(kind=kind, start=r.start, end=r.end, label=o.value("label") or None, boundary="high", observations=(o.id,), confidence=o.confidence))
        elif o.kind is ObservationKind.PROOF_START:
            limit = _next_boundary(boundaries, r.start)
            end_index = bisect_left(proof_ends, r.end)
            if end_index < len(proof_ends) and proof_ends[end_index] <= limit:
                end = proof_ends[end_index]
                boundary = "high"
            else:
                end = _trim(text, r.start, limit)
                boundary = "probable"
                diagnostics.append(Diagnostic(code="proof_end_unresolved", detail=f"no end-of-proof marker before the next unit at {limit}", severity="info", representation=r.representation))
            target = None
            label = o.value("label")
            proof_of = re.search(r"of\s+(?:Theorem|Lemma|Proposition|Corollary|Claim)\s+([0-9]+(?:\.[0-9]+)*)", text[r.start:r.end])
            if proof_of:
                target = proof_of.group(1)
            units.append(Unit(kind=NodeKind.PROOF, start=r.start, end=end, label=label, boundary=boundary, observations=(o.id,),
                               confidence=o.confidence, proof_target_number=target))

    return units


def _tree_nodes(
    artifact_sha256: str, rep: str, text: str, units: list[Unit], *,
    page_spans: Mapping[int, RepresentationSpan] | None, builder_version: str,
) -> tuple[list[SourceNode], dict[str, Unit]]:
    """Materialize nested units and uncovered text with stable, content-derived identities."""
    # 3. Container spans: a heading runs to the next heading of the same or a higher level.
    containers = _ContainerLayout.of(text, units)

    # 4. Nodes with content-derived identities.
    nodes: list[SourceNode] = []
    ids: dict[int, str] = {}
    counters: dict[str | None, int] = {}
    taken: set[str] = set()

    def make_node(unit: Unit, parent: str | None, start: int, end: int) -> SourceNode:
        order = counters.get(parent, 0)
        counters[parent] = order + 1
        node = node_record(artifact_sha256, rep, text, unit, parent=parent, start=start, end=end, order=order,
                           page_spans=page_spans, taken=taken, provenance=f"{BUILDER}/{builder_version}")
        taken.add(node.id)
        ids[id(unit)] = node.id
        nodes.append(node)
        return node

    for unit in units:
        if unit.kind not in CONTAINER_KINDS:
            continue
        parent_unit = containers.parents[id(unit)]
        make_node(unit, ids.get(id(parent_unit)) if parent_unit else None, unit.start, containers.ends[id(unit)])
    for unit in units:
        if unit.kind in CONTAINER_KINDS:
            continue
        owner = containers.owner_at(unit.start)
        make_node(unit, ids.get(id(owner)) if owner else None, unit.start, unit.end)

    # 5. Unknown regions: text no unit covers, inside each container and at top level.
    for start, end in _uncovered_ranges(text, nodes, containers.units):
        while start < end and text[start] in " \t\n\f":
            start += 1
        end = _trim(text, start, end)
        if start >= end:
            continue
        owner = containers.owner_at(start)
        unit = Unit(kind=NodeKind.UNKNOWN, start=start, end=end, boundary="high")
        make_node(unit, ids.get(id(owner)) if owner else None, start, end)
    nodes = reading_order(nodes)

    unit_by_node = {ids[id(u)]: u for u in units if id(u) in ids}
    return nodes, unit_by_node


@dataclass(frozen=True)
class _NodeIndex:
    """Printed names and container-local statements for association, never identity."""

    numbers: dict[str, list[SourceNode]]
    labels: dict[str, list[SourceNode]]
    statements: dict[str | None, list[SourceNode]]

    @classmethod
    def of(cls, nodes: list[SourceNode]) -> _NodeIndex:
        numbers: dict[str, list[SourceNode]] = {}
        labels: dict[str, list[SourceNode]] = {}
        statements: dict[str | None, list[SourceNode]] = {}
        for node in nodes:
            if node.kind not in CONTAINER_KINDS and node.label:
                labels.setdefault(node.label, []).append(node)
            if node.kind in STATEMENT_KINDS:
                if node.number:
                    numbers.setdefault(node.number, []).append(node)
                statements.setdefault(node.parent, []).append(node)
        return cls(numbers, labels, statements)


def _proof_edges(
    nodes: list[SourceNode], unit_by_node: Mapping[str, Unit], lookup: _NodeIndex, diagnostics: list[Diagnostic],
) -> list[SourceEdge]:
    """Associate an explicit proof target or the preceding statement in its container."""
    edges = []
    statement_ends = {parent: [n.span.ranges[0].end for n in group] for parent, group in lookup.statements.items()}
    for node in nodes:
        if node.kind is not NodeKind.PROOF:
            continue
        unit = unit_by_node.get(node.id)
        target = None
        if unit is not None and unit.proof_target_number and unit.proof_target_number in lookup.numbers:
            target = lookup.numbers[unit.proof_target_number][0]
        else:
            group = lookup.statements.get(node.parent, [])
            index = bisect_right(statement_ends.get(node.parent, []), node.span.ranges[0].start) - 1
            target = group[index] if index >= 0 else None
        if target is not None:
            edges.append(SourceEdge(kind=SourceEdgeKind.PROOF_OF, source=node.id, target=target.id))
        else:
            diagnostics.append(Diagnostic(code="proof_without_statement", detail=f"proof {node.id} has no preceding statement in its container", severity="info"))
    return edges


def _reference_holders(nodes: list[SourceNode], references: list[tuple[int, StructuralObservation]]) -> dict[int, SourceNode]:
    # Sweep once. The heap retains the last containing leaf in reading order,
    # including overlapping display/statement spans.
    leaf_nodes = [n for n in nodes if n.kind not in CONTAINER_KINDS]
    holders: dict[int, SourceNode] = {}
    active: list[tuple[int, int]] = []
    cursor = 0
    for index, o in sorted(references, key=lambda item: item[1].anchor.locator.start):
        r = _range(o)
        assert r is not None
        while cursor < len(leaf_nodes) and leaf_nodes[cursor].span.ranges[0].start <= r.start:
            part = leaf_nodes[cursor].span.ranges[0]
            heapq.heappush(active, (-cursor, max(part.end, part.start + 1)))
            cursor += 1
        while active and active[0][1] <= r.start:
            heapq.heappop(active)
        if active:
            holders[index] = leaf_nodes[-active[0][0]]
    return holders


def _reference_edges(
    nodes: list[SourceNode], text_obs: list[StructuralObservation], text: str, lookup: _NodeIndex, diagnostics: list[Diagnostic],
) -> list[SourceEdge]:
    """Resolve printed references as mentions, disclosing unresolved names."""
    references = [(index, o) for index, o in enumerate(text_obs) if o.kind is ObservationKind.REFERENCE]
    holders = _reference_holders(nodes, references)
    edges = []
    for index, o in references:
        r = _range(o)
        assert r is not None
        holder = holders.get(index)
        if holder is None:
            continue
        if o.value("kind") == "citation":
            continue
        label_reference = o.value("kind") == "label"
        reference = (o.value("label") or o.value("number")) if label_reference else o.value("number")
        grouped_reference = label_reference and text[r.start:r.end].startswith((r"\cref{", r"\Cref{"))
        references_to_resolve = [label.strip() for label in reference.split(",")] if grouped_reference else [reference]
        wanted_kind = KINDS_BY_KEYWORD.get(o.value("kind"))
        for name in references_to_resolve:
            targets = lookup.labels.get(name, []) if label_reference else lookup.numbers.get(name, [])
            target = next((t for t in targets if wanted_kind is None or t.kind is wanted_kind), None)
            if target is None:
                diagnostics.append(Diagnostic(code="unresolved_reference", detail=f"{o.value('kind')} {name} is referenced but no such unit was recovered", severity="info", representation=r.representation))
                continue
            if target.id != holder.id:
                edges.append(SourceEdge(kind=SourceEdgeKind.SOURCE_REFERS_TO, source=holder.id, target=target.id, anchor=o.anchor))

    return edges


def page_spans_from(mapping: RepresentationMapping) -> dict[int, RepresentationSpan]:
    """Page index -> span of one representation, from a mapping's whole-page pairs."""
    found: dict[int, RepresentationSpan] = {}
    for left, right in mapping.pairs:
        if isinstance(left, RepresentationSpan) and isinstance(right, PageRegion) and right.precision == "page":
            found[right.page_index] = left
        elif isinstance(right, RepresentationSpan) and isinstance(left, PageRegion) and left.precision == "page":
            found[left.page_index] = right
    return found


def validate_tree(tree: SourceTree, representations: Mapping[str, DerivedRepresentation], texts: Mapping[str, str]) -> tuple[Diagnostic, ...]:
    """Mechanical checks; any error diagnostic makes the tree inadmissible."""
    problems: list[Diagnostic] = []
    ids = [n.id for n in tree.nodes]
    if len(set(ids)) != len(ids):
        problems.append(_tree_problem("duplicate_node_id", "two nodes share an id"))
    by_id = {n.id: n for n in tree.nodes}
    for rep in tree.representations:
        record = representations.get(rep)
        if record is None or record.artifact_sha256 != tree.artifact_sha256:
            problems.append(_tree_problem("foreign_representation", f"representation {rep} is not derived from artifact {tree.artifact_sha256}"))
    for node in tree.nodes:
        for span in (node.span, node.statement_span):
            if span is not None:
                problems.extend(_span_problems(node.id, span, tree, texts))
        problems.extend(_parent_problems(node, by_id))
    for parent in {n.parent for n in tree.nodes}:
        children = tree.children(parent)
        starts = [c.span.ranges[0].start for c in children]
        if starts != sorted(starts):
            problems.append(_tree_problem("reading_order", f"children of {parent!r} are not in reading order"))
    for edge in tree.edges:
        problems.extend(_edge_problems(edge, by_id))
    return tuple(problems)


def _tree_problem(code: str, detail: str) -> Diagnostic:
    return Diagnostic(code=code, detail=detail, severity="error")


def _span_problems(node_id: str, span: SourceSpan, tree: SourceTree, texts: Mapping[str, str]) -> list[Diagnostic]:
    """Check attribution, resolvable ranges and the digest of the available content."""
    problems = []
    if span.artifact_sha256 != tree.artifact_sha256:
        problems.append(_tree_problem("foreign_span", f"node {node_id} spans a different artifact"))
    content = []
    for part in span.ranges:
        if part.representation not in tree.representations:
            problems.append(_tree_problem("unmapped_span", f"node {node_id} names representation {part.representation} the tree does not use"))
            continue
        text = texts.get(part.representation)
        if text is None:
            problems.append(_tree_problem("representation_unavailable", f"representation {part.representation} text is not available for validation"))
            continue
        if part.end > len(text):
            problems.append(_tree_problem("span_out_of_bounds", f"node {node_id} runs past the end of {part.representation}"))
            continue
        content.append(text[part.start:part.end])
    if content and text_digest("".join(content)) != span.content_sha256:
        problems.append(_tree_problem("span_digest_mismatch", f"node {node_id} span content does not match its digest"))
    return problems


def _parent_problems(node: SourceNode, by_id: Mapping[str, SourceNode]) -> list[Diagnostic]:
    problems = []
    if node.parent is not None and node.parent not in by_id:
        problems.append(_tree_problem("missing_parent", f"node {node.id} names unknown parent {node.parent}"))
    seen = {node.id}
    cursor = node.parent
    while cursor is not None:
        if cursor in seen:
            problems.append(_tree_problem("parent_cycle", f"node {node.id} is its own ancestor"))
            break
        seen.add(cursor)
        cursor = by_id[cursor].parent if cursor in by_id else None
    return problems


def _edge_problems(edge: SourceEdge, by_id: Mapping[str, SourceNode]) -> list[Diagnostic]:
    if edge.source not in by_id or edge.target not in by_id:
        return [_tree_problem("dangling_edge", f"{edge.kind.value} edge names an unknown node")]
    problems = []
    if edge.kind is SourceEdgeKind.PROOF_OF:
        if by_id[edge.source].kind is not NodeKind.PROOF:
            problems.append(_tree_problem("proof_of_source", f"{edge.source} is not a proof node"))
        if by_id[edge.target].kind not in STATEMENT_KINDS:
            problems.append(_tree_problem("proof_of_target", f"{edge.target} is not a statement node"))
    if edge.kind is SourceEdgeKind.CONTAINS and by_id[edge.target].parent != edge.source:
        problems.append(_tree_problem("contains_mismatch", f"{edge.target} is not a child of {edge.source}"))
    return problems


class TreeStore:
    """Preserve admitted tree versions and journal retrieval preferences separately."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._journal = Journal(self.root / "journal", types=JOURNAL_TYPES)

    def admit(self, tree: SourceTree, representations: Mapping[str, DerivedRepresentation], texts: Mapping[str, str]) -> SourceTree:
        problems = validate_tree(tree, representations, texts)
        if problems:
            raise TreeError("tree refused: " + "; ".join(f"{p.code}: {p.detail}" for p in problems))
        guard = WriteGuard(self.root / tree.artifact_sha256, create=True)
        name = f"{tree.id}.json"
        if guard.path(name).is_file():
            held = self.get(tree.artifact_sha256, tree.id)
            if held.digest != tree.digest and held.model_copy(update={"built_at": tree.built_at}).digest != tree.digest:
                raise TreeError(f"tree {tree.id} already exists with different content")
            return held
        guard.write_bytes(name, (tree.model_dump_json(indent=2) + "\n").encode("utf-8"))
        return self.get(tree.artifact_sha256, tree.id)

    def get(self, artifact_sha256: str, tree_id: str) -> SourceTree:
        """Load one immutable tree record, refusing an artifact or tree identity mismatch."""
        try:
            tree = SourceTree.model_validate_json(read_text(self.root, f"{artifact_sha256}/{tree_id}.json"))
        except FileNotFoundError:
            raise TreeError(f"tree {tree_id} of artifact {artifact_sha256} is not held") from None
        except (OSError, ValueError, LayoutError) as error:
            raise TreeError(f"tree {tree_id} is corrupt: {error}") from error
        if tree.id != tree_id or tree.artifact_sha256 != artifact_sha256:
            raise TreeError(f"tree {tree_id} names a different identity")
        return tree

    def list(self, artifact_sha256: str) -> tuple[SourceTree, ...]:
        """Recorded trees in version, build-time and id order, including superseded versions."""
        directory = self.root / artifact_sha256
        if not directory.is_dir() or directory.is_symlink():
            return ()
        trees = [self.get(artifact_sha256, p.stem) for p in sorted(directory.iterdir(), key=lambda p: p.relative_to(directory).as_posix()) if p.suffix == ".json" and p.stem.startswith("tree-") and not p.is_symlink()]
        return tuple(sorted(trees, key=lambda t: (t.version, t.built_at, t.id)))

    def node(self, artifact_sha256: str, tree_id: str, node_id: str) -> SourceNode:
        return self.get(artifact_sha256, tree_id).node(node_id)

    def revision(self) -> int:
        """Concurrency token for preference and correspondence decisions in the shared journal."""
        return self._journal.read().revision

    def preferred(self, artifact_sha256: str) -> SourceTree | None:
        """The last journaled preference, falling back to the latest recorded tree."""
        chosen = None
        for record in self._journal.read().of(TreePreference):
            if record.artifact_sha256 == artifact_sha256:
                chosen = record.tree
        if chosen is None:
            trees = self.list(artifact_sha256)
            return trees[-1] if trees else None
        return self.get(artifact_sha256, chosen)

    def prefer(self, artifact_sha256: str, tree_id: str, *, reason: str, expected_revision: int) -> None:
        self.get(artifact_sha256, tree_id)
        self._journal.append([TreePreference(artifact_sha256=artifact_sha256, tree=tree_id, reason=reason)], expected_revision=expected_revision)

    def correspondences(self, artifact_sha256: str) -> tuple[SourceCorrespondence, ...]:
        """Recorded cross-source relations naming this artifact on either side."""
        return tuple(r for r in self._journal.read().of(SourceCorrespondence) if artifact_sha256 in (r.left_artifact, r.right_artifact))

    def add_correspondence(self, record: SourceCorrespondence, *, expected_revision: int) -> None:
        if record.status == "authoritative" and not (record.decided_by and (record.decided_by.startswith("user:") or record.evidence)):
            raise TreeError("an authoritative correspondence needs evidence and a decider")
        self._journal.append([record], expected_revision=expected_revision, validate=_validate_correspondence)

    def observations_digest(self, observations: tuple[StructuralObservation, ...]) -> str:
        """Digest the supplied observation records in their exact order."""
        return json_digest([o.model_dump(mode="json") for o in observations])


def _validate_correspondence(before: JournalSnapshot, after: JournalSnapshot) -> None:
    ids = [r.id for r in after.of(SourceCorrespondence)]
    if len(ids) != len(set(ids)):
        raise TreeError("correspondence ids must be unique")
    for record in after.records[len(before.records):]:
        if isinstance(record, SourceCorrespondence) and record.left_artifact == record.right_artifact and record.left == record.right:
            raise TreeError("a correspondence relates two different source units")
