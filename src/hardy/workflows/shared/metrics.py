"""Semantic and reuse instrumentation over the shared ledger, links, realizations and promotions.

Counts by status and class, kept apart: links proposed against admitted
against review-needed, realizations by origin and status, promotions by
outcome and blocker kind, and how a set of reuse resolutions classed. A
labelled set of expected source-to-claim links gives exact-match precision
and recall and counts false merges, which the architecture weighs far above
a temporary duplicate. Nothing is folded into a single number.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from hardy.foundation.values import FrozenModel

from .claims import LinkStatus, LinkStore
from .ledger import SharedClaims
from .promotion import PromotionStore
from .realizations import RealizationStore
from .reuse import ReuseResult


class ExpectedLink(FrozenModel):
    artifact_sha256: str
    node: str
    claim_id: str


class LinkComparison(FrozenModel):
    expected: int
    admitted: int
    correct: int
    false_merges: int
    missing: int

    @property
    def precision(self) -> float | None:
        return None if self.admitted == 0 else self.correct / self.admitted

    @property
    def recall(self) -> float | None:
        return None if self.expected == 0 else self.correct / self.expected


class SemanticReport(FrozenModel):
    claims: int
    claims_by_kind: tuple[tuple[str, int], ...]
    relations_by_kind: tuple[tuple[str, int], ...]
    clusters: int
    links_by_status: tuple[tuple[str, int], ...]
    links_with_faithfulness: int
    links_with_approval: int
    claims_with_source_links: int
    realizations_by_origin: tuple[tuple[str, int], ...]
    realizations_by_status: tuple[tuple[str, int], ...]
    claims_with_attached_realizations: int
    promotions_by_status: tuple[tuple[str, int], ...]
    promotion_blockers: tuple[tuple[str, int], ...]
    promoted_modules: int


def _pairs(counter: Counter) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counter.items()))


def semantic_report(claims: SharedClaims, links: LinkStore, realizations: RealizationStore, promotions: PromotionStore) -> SemanticReport:
    items = claims.claims()
    kinds: Counter = Counter(i.kind.value for i in items)
    relations: Counter = Counter(r.kind.value for r in claims.snapshot().current(__import__("hardy.workflows.ledger.contracts", fromlist=["Relation"]).Relation))
    clusters = sum(1 for n in claims.snapshot().current(__import__("hardy.workflows.ledger.contracts", fromlist=["ProjectItem"]).ProjectItem)
                   if n.kind.value == "research_note" and any(k == "cluster" for k, _ in n.semantics))
    heads = links.heads()
    statuses: Counter = Counter(link.status.value for link in heads.values())
    with_faithfulness = sum(1 for link in heads.values() if link.status is LinkStatus.ADMITTED and link.faithfulness is not None)
    with_approval = sum(1 for link in heads.values() if link.status is LinkStatus.ADMITTED and link.approval is not None)
    linked_claims = {link.claim.id for link in heads.values() if link.status is LinkStatus.ADMITTED and link.claim}
    reals = realizations.heads()
    origins: Counter = Counter(r.origin.value for r in reals.values())
    real_status: Counter = Counter(r.status for r in reals.values())
    attached_claims = {r.claim.id for r in reals.values() if r.status == "attached"}
    promo = promotions.heads()
    promo_status: Counter = Counter(p.status for p in promo.values())
    blockers: Counter = Counter(b.kind for p in promo.values() for b in p.blockers)
    promoted_modules = sum(len(p.shared_modules) for p in promo.values() if p.status == "admitted")
    return SemanticReport(
        claims=len(items), claims_by_kind=_pairs(kinds), relations_by_kind=_pairs(relations), clusters=clusters, links_by_status=_pairs(statuses),
        links_with_faithfulness=with_faithfulness, links_with_approval=with_approval, claims_with_source_links=len(linked_claims),
        realizations_by_origin=_pairs(origins), realizations_by_status=_pairs(real_status), claims_with_attached_realizations=len(attached_claims),
        promotions_by_status=_pairs(promo_status), promotion_blockers=_pairs(blockers), promoted_modules=promoted_modules,
    )


def compare_links(links: LinkStore, expected: Iterable[ExpectedLink]) -> LinkComparison:
    """Exact-match precision and recall of admitted links against a labelled set; a wrong claim is a false merge."""
    wanted = {(e.artifact_sha256, e.node): e.claim_id for e in expected}
    admitted = [link for link in links.heads().values() if link.status is LinkStatus.ADMITTED and link.claim is not None]
    correct = false = 0
    seen: set[tuple[str, str]] = set()
    matched: set[tuple[str, str]] = set()
    for link in admitted:
        key = (link.artifact_sha256, link.node)
        seen.add(key)
        if key in wanted and wanted[key] == link.claim.id:
            if key not in matched:  # several admitted links may name one relation; the relation is found once
                matched.add(key)
                correct += 1
        elif key in wanted:
            false += 1
    missing = sum(1 for key in wanted if key not in seen)
    return LinkComparison(expected=len(wanted), admitted=len(admitted), correct=correct, false_merges=false, missing=missing)


def reuse_summary(results: Iterable[ReuseResult]) -> tuple[tuple[str, int], ...]:
    """How a batch of reuse resolutions classed, and how many were usable now."""
    counter: Counter = Counter()
    for result in results:
        counter[result.cls.value] += 1
        if result.reusable_now:
            counter["reusable_now"] += 1
    return _pairs(counter)
