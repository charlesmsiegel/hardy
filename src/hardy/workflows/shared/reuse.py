"""The reuse resolver: what reusable mathematics exists for a need, and how much it is worth.

Results are classed rather than scored, because "found a theorem with a
similar name" and "an exact claim with an importable, attached realization"
are different answers a caller acts on differently. The order follows the
architecture's preference: an established result in the current project,
then an exact claim with a Mathlib realization, then one with a shared Hardy
realization, then a related claim reachable through a recorded relation,
then an exact claim known only from sources, then candidates and leads. An
empty answer says the search found nothing, never that nothing exists.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.values import FrozenModel
from hardy.workflows.ledger.contracts import Relation, RelationKind, VersionRef

from .claims import LinkStatus, LinkStore
from .ledger import SharedClaims
from .realizations import FormalRealization, RealizationOrigin, RealizationStore, revalidate

REUSE_RELATIONS = frozenset({RelationKind.EQUIVALENT_TO, RelationKind.GENERALIZES, RelationKind.SPECIALIZES, RelationKind.REFINES, RelationKind.TRANSPORTED_FROM})
ORIGIN_ORDER = {RealizationOrigin.PROJECT: 0, RealizationOrigin.MATHLIB: 1, RealizationOrigin.HARDY_SHARED: 2, RealizationOrigin.EXTERNAL: 3}


class ReuseClass(str, Enum):
    PROJECT_ESTABLISHED = "project_established"
    EXACT_CLAIM_WITH_REALIZATION = "exact_claim_with_realization"
    RELATED_CLAIM_WITH_RELATION = "related_claim_with_relation"
    EXACT_CLAIM_SOURCE_ONLY = "exact_claim_source_only"
    FORMAL_CANDIDATE = "formal_candidate"
    RELATED_FAMILY = "related_family"
    SOURCE_LEAD = "source_lead"
    NONE = "none"


CLASS_ORDER = {
    ReuseClass.PROJECT_ESTABLISHED: 0, ReuseClass.EXACT_CLAIM_WITH_REALIZATION: 1, ReuseClass.RELATED_CLAIM_WITH_RELATION: 2,
    ReuseClass.EXACT_CLAIM_SOURCE_ONLY: 3, ReuseClass.FORMAL_CANDIDATE: 4, ReuseClass.RELATED_FAMILY: 5, ReuseClass.SOURCE_LEAD: 6, ReuseClass.NONE: 7,
}


class ReuseResult(FrozenModel):
    cls: ReuseClass
    claim: VersionRef | None = None
    claim_name: str | None = None
    realization: FormalRealization | None = None
    availability: str | None = None
    relation: Relation | None = None
    links: tuple[str, ...] = ()
    reason: str

    @property
    def reusable_now(self) -> bool:
        return self.cls in {ReuseClass.PROJECT_ESTABLISHED, ReuseClass.EXACT_CLAIM_WITH_REALIZATION} and self.availability in {"importable", "project"}


def resolve_reusable_claim(
    query: str, *, claims: SharedClaims, links: LinkStore, realizations: RealizationStore,
    environment: EnvironmentIdentity, importable: Callable[[FormalRealization], bool],
    project_results: tuple[ReuseResult, ...] = (), limit: int = 20,
) -> tuple[ReuseResult, ...]:
    results: list[ReuseResult] = list(project_results)
    hits = claims.search(query, limit=limit)
    exact = [h for h in hits if h.rank <= 2]
    related = [h for h in hits if h.rank > 2]
    for hit in exact:
        attached = [r for r in realizations.for_claim(hit.ref.id) if r.status == "attached"]
        candidates = [r for r in realizations.for_claim(hit.ref.id) if r.status == "candidate"]
        admitted_links = tuple(link.id for link in links.links_for_claim(hit.ref.id) if link.status is LinkStatus.ADMITTED)
        for realization in sorted(attached, key=lambda r: (ORIGIN_ORDER.get(r.origin, 9), r.id)):
            if realization.globally_importable:
                availability = revalidate(realization, environment=environment, importable=importable)
                note = {"importable": "importable in the requesting environment", "stale_environment": "checked in a different Lean environment; revalidate before use",
                        "unimportable": "not importable in the requesting environment"}[availability]
            else:
                availability = "project_local"
                note = f"project-local to {realization.project or 'its project'}; not importable elsewhere until promoted"
            results.append(ReuseResult(cls=ReuseClass.EXACT_CLAIM_WITH_REALIZATION, claim=hit.ref, claim_name=hit.name, realization=realization,
                                       availability=availability, links=admitted_links, reason=f"exact claim ({hit.match}); {realization.origin.value} realization {realization.declaration}, {note}"))
        for relation in claims.relations_for(hit.ref):
            if relation.kind not in REUSE_RELATIONS:
                continue
            other = relation.source if relation.target.id == hit.ref.id else relation.target
            for realization in realizations.for_claim(other.id):
                if realization.status != "attached" or not realization.globally_importable:
                    continue
                availability = revalidate(realization, environment=environment, importable=importable)
                results.append(ReuseResult(cls=ReuseClass.RELATED_CLAIM_WITH_RELATION, claim=other, claim_name=claims.head(other.id).name, realization=realization,
                                           availability=availability, relation=relation,
                                           reason=f"{other.id} {relation.kind.value} {hit.ref.id}; reuse needs the recorded relation's transport, not a name match"))
        if not attached and admitted_links:
            results.append(ReuseResult(cls=ReuseClass.EXACT_CLAIM_SOURCE_ONLY, claim=hit.ref, claim_name=hit.name, links=admitted_links,
                                       reason=f"exact claim ({hit.match}) known from {len(admitted_links)} admitted source link(s); no reusable formal realization"))
        for realization in candidates:
            results.append(ReuseResult(cls=ReuseClass.FORMAL_CANDIDATE, claim=hit.ref, claim_name=hit.name, realization=realization, availability="unverified",
                                       reason=f"{realization.origin.value} declaration {realization.declaration} was found by name and is not semantically authenticated"))
        if not attached and not admitted_links and not candidates:
            results.append(ReuseResult(cls=ReuseClass.EXACT_CLAIM_SOURCE_ONLY, claim=hit.ref, claim_name=hit.name,
                                       reason=f"exact claim ({hit.match}) is registered with no admitted source link and no realization"))
    for hit in related:
        results.append(ReuseResult(cls=ReuseClass.RELATED_FAMILY, claim=hit.ref, claim_name=hit.name, reason=f"{hit.match}; navigational only, not an identity match"))
    if not results:
        results.append(ReuseResult(cls=ReuseClass.NONE, reason="no shared claim matched the query; this is not evidence that none exists or that Mathlib lacks one"))
    results.sort(key=lambda r: (CLASS_ORDER[r.cls], 0 if r.availability in {"importable", "project"} else 1, r.claim.id if r.claim else ""))
    return tuple(results[:limit])
