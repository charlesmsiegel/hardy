"""Source-side evaluation instrumentation, derived from records and never a score.

Everything here is computed from what the stores hold: how many artifacts
have which extraction quality, how many trees have which node kinds and
boundary statuses, which diagnostics recur, how many correspondences and
repairs exist. A labelled fixture can be compared against a tree to give
precision and recall per node kind. The numbers are reported side by side;
collapsing them into one figure would hide exactly the trade-offs the
architecture asks to be measured.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from hardy.foundation.values import FrozenModel

from .contracts import CONTAINER_KINDS, STATEMENT_KINDS, NodeKind, SourceTree
from .library import ManagedLibrary


class LabelledUnit(FrozenModel):
    """A human label: this kind of unit starts and ends at these offsets of a representation."""

    kind: NodeKind
    representation: str
    start: int
    end: int
    number: str | None = None


class KindScore(FrozenModel):
    kind: NodeKind
    labelled: int
    recovered: int
    matched: int

    @property
    def precision(self) -> float | None:
        return None if self.recovered == 0 else self.matched / self.recovered

    @property
    def recall(self) -> float | None:
        return None if self.labelled == 0 else self.matched / self.labelled


class TreeComparison(FrozenModel):
    tree: str
    scores: tuple[KindScore, ...]
    boundary_exact: int
    boundary_overlapping: int


class SourceReport(FrozenModel):
    artifacts: int
    by_format: tuple[tuple[str, int], ...]
    extraction_quality: tuple[tuple[str, int], ...]
    extraction_outcomes: tuple[tuple[str, int], ...] = ()   # latest recorded pass per artifact; `never_extracted` when none
    extraction_passes: int = 0
    representations_by_kind: tuple[tuple[str, int], ...]
    trees: int
    tree_versions: tuple[tuple[str, int], ...]
    nodes_by_kind: tuple[tuple[str, int], ...]
    boundary_status: tuple[tuple[str, int], ...]
    diagnostics_by_code: tuple[tuple[str, int], ...]
    unknown_regions: int
    model_repairs: int
    correspondences: tuple[tuple[str, int], ...]
    authoritative_groupings: int
    pending_groupings: int
    weak_pages: int
    ocr_representations: int


def _pairs(counter: Counter) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items()))


def source_report(library: ManagedLibrary) -> SourceReport:
    formats: Counter = Counter()
    quality: Counter = Counter()
    kinds: Counter = Counter()
    versions: Counter = Counter()
    node_kinds: Counter = Counter()
    boundaries: Counter = Counter()
    diagnostics: Counter = Counter()
    outcomes: Counter = Counter()
    passes = 0
    unknown = repairs = trees = weak = ocr = 0
    digests = library.artifacts.stored()
    snapshot = library.catalog.snapshot()
    authoritative = snapshot.authoritative()
    decided = snapshot.decided()
    pending = sum(1 for p in snapshot.proposals() if p.id not in decided)
    for sha in digests:
        formats[library.artifacts.record(sha).format.value] += 1
        recorded = library.extractions.passes(sha)
        passes += len(recorded)
        outcomes[str(recorded[-1].get("status")) if recorded else "never_extracted"] += 1
        for record in library.representations.list(sha):
            kinds[record.kind.value] += 1
            if record.kind.value in {"native_text", "native_source", "dom"}:
                quality[record.quality.status] += 1
            if record.kind.value == "ocr_text":
                ocr += 1
            for diagnostic in record.quality.diagnostics:
                diagnostics[diagnostic.code] += 1
        weak += len(library.weak_regions(sha))
        for tree in library.trees.list(sha):
            trees += 1
            versions[str(tree.version)] += 1
            for node in tree.nodes:
                node_kinds[node.kind.value] += 1
                boundaries[node.boundary_status] += 1
                if node.kind is NodeKind.UNKNOWN:
                    unknown += 1
            for diagnostic in tree.diagnostics:
                diagnostics[diagnostic.code] += 1
                if diagnostic.code == "model_repair":
                    repairs += 1
    # A correspondence names two artifacts and is listed under both; the
    # latest revision of each id is counted once.
    latest_correspondence = {record.id: record for sha in digests for record in library.trees.correspondences(sha)}
    correspondences: Counter = Counter(f"{record.relation}:{record.status}" for record in latest_correspondence.values())
    return SourceReport(
        artifacts=len(digests), by_format=_pairs(formats), extraction_quality=_pairs(quality), extraction_outcomes=_pairs(outcomes),
        extraction_passes=passes, representations_by_kind=_pairs(kinds), trees=trees,
        tree_versions=_pairs(versions), nodes_by_kind=_pairs(node_kinds), boundary_status=_pairs(boundaries), diagnostics_by_code=_pairs(diagnostics),
        unknown_regions=unknown, model_repairs=repairs, correspondences=_pairs(correspondences), authoritative_groupings=len(authoritative),
        pending_groupings=pending, weak_pages=weak, ocr_representations=ocr,
    )


def compare_tree(tree: SourceTree, labels: Iterable[LabelledUnit], *, tolerance: int = 0) -> TreeComparison:
    """Precision and recall per kind: a recovered unit matches a label of the same kind with the same start (within tolerance)."""
    labelled = list(labels)
    recovered = [n for n in tree.nodes if n.kind in STATEMENT_KINDS or n.kind in CONTAINER_KINDS or n.kind is NodeKind.PROOF]
    matched_labels: set[int] = set()
    exact = overlapping = 0
    matches_by_kind: Counter = Counter()
    for node in recovered:
        part = node.span.ranges[0]
        for index, label in enumerate(labelled):
            if index in matched_labels or label.kind is not node.kind or label.representation != part.representation:
                continue
            if abs(label.start - part.start) <= tolerance:
                matched_labels.add(index)
                matches_by_kind[node.kind] += 1
                if label.end == part.end:
                    exact += 1
                else:
                    overlapping += 1
                break
    scores = []
    for kind in sorted({label.kind for label in labelled} | {n.kind for n in recovered}, key=lambda k: k.value):
        scores.append(KindScore(kind=kind, labelled=sum(1 for label in labelled if label.kind is kind), recovered=sum(1 for n in recovered if n.kind is kind),
                                matched=matches_by_kind[kind]))
    return TreeComparison(tree=tree.id, scores=tuple(scores), boundary_exact=exact, boundary_overlapping=overlapping)
