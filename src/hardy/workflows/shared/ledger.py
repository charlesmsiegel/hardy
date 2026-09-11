"""The shared mathematical ledger: reusable exact claims in Hardy's own ledger format.

There is one mathematical semantics system. A reusable claim is a `ProjectItem`
of a theorem-like kind in a user-level `LedgerStore`, related to other claims
through ordinary `Relation` records, with evidence pointing at literature and
faithfulness records the owning capabilities produced. The store is delivered
to projects through `workflows/retrieval.py` as a `shared_library` source,
which is what makes shared existence and project admissibility separate: a
project sees a shared claim only through an authorization naming its own
scope, and gains no trust from the claim being there.

Names, aliases and words find candidates; only an exact ref is an identity.
A near duplicate is clustered and kept separate, because a false merge hands
one claim's realizations to another and a duplicate costs a second look.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from hardy.foundation.paths import global_library
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    ProjectItem,
    ProjectItemKind,
    ProjectOrigin,
    Relation,
    RelationKind,
    VersionRef,
)
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.retrieval import (
    RetrievalSource,
    SharedAuthorization,
    SharedReadRequest,
    ledger_source,
    source_identity,
)

SHARED_SOURCE_ID = "hardy-shared-library"
CLAIM_KINDS: frozenset[ProjectItemKind] = frozenset({
    ProjectItemKind.THEOREM, ProjectItemKind.LEMMA, ProjectItemKind.PROPOSITION, ProjectItemKind.COROLLARY,
    ProjectItemKind.CLAIM, ProjectItemKind.CONJECTURE, ProjectItemKind.DEFINITION, ProjectItemKind.CONCEPT,
    ProjectItemKind.REPRESENTATION, ProjectItemKind.STANDARD_OBJECT, ProjectItemKind.EXTERNAL_RESULT,
})
SHARED_ORIGINS: frozenset[ProjectOrigin] = frozenset({
    ProjectOrigin.BACKGROUND_PAPER, ProjectOrigin.IMPORTED_PROJECT, ProjectOrigin.HUMAN_AUTHORED, ProjectOrigin.MATHLIB,
})
CLAIM_RELATIONS: frozenset[RelationKind] = frozenset({
    RelationKind.EQUIVALENT_TO, RelationKind.GENERALIZES, RelationKind.SPECIALIZES, RelationKind.REFINES,
    RelationKind.INTERPRETS, RelationKind.TRANSPORTED_FROM, RelationKind.SUPERSEDES, RelationKind.DEPENDS_ON,
    RelationKind.IDENTIFIED_WITH, RelationKind.USES,
})
ALIASES_KEY = "aliases"
WORD = re.compile(r"\w+")


class SharedLedgerError(ValueError):
    """A shared-ledger transaction that would corrupt reusable identity."""


def shared_store(root: Path | None = None) -> LedgerStore:
    return LedgerStore(Path(root) if root is not None else global_library())


def shared_provenance(store: LedgerStore) -> ArtifactRef:
    """The exact state of the shared ledger, as an artifact a project's authorization can name."""
    snapshot = store.read()
    digest = json_digest([snapshot.revision, [r.ref.model_dump() for r in snapshot.records]])
    return ArtifactRef(uri=f"hardy-library:ledger:{store.project.as_posix()}", digest=digest, locator=f"revision:{snapshot.revision}")


def shared_source(store: LedgerStore, *, enabled: bool = True) -> RetrievalSource:
    provenance = shared_provenance(store) if (store.project / "ledger").exists() else None
    return ledger_source(SHARED_SOURCE_ID, store, enabled=enabled, kind="shared_library", provenance=provenance)


def authorize_shared_read(
    request: SharedReadRequest, *, store: LedgerStore, allowed: Callable[[str], bool] = lambda project: True,
) -> SharedAuthorization | None:
    """Authenticate one shared read for one project use against the ledger as it is now.

    The authorization names the requesting project, scope and context, so it
    grants nothing beyond that use; it is refused when the frozen identity no
    longer matches the live ledger or when the project is not allowed to read.
    """
    if request.source.id != SHARED_SOURCE_ID or request.source.kind != "shared_library":
        return None
    if not allowed(request.query.project_source):
        return None
    live = source_identity(shared_source(store))
    if live.content_digest != request.source.content_digest or live.provenance != request.source.provenance or live.provenance is None:
        return None
    return SharedAuthorization(source_id=live.id, source_digest=live.content_digest, provenance=live.provenance,
                               project_source=request.query.project_source, scope=request.query.scope, context=request.query.context)


class ClaimHit(FrozenModel):
    ref: VersionRef
    kind: ProjectItemKind
    name: str
    rank: int
    match: str


def aliases_of(item: ProjectItem) -> tuple[str, ...]:
    for key, value in item.semantics:
        if key == ALIASES_KEY:
            try:
                loaded = json.loads(value)
            except ValueError:
                return (value,)
            return tuple(str(a) for a in loaded) if isinstance(loaded, list) else (str(loaded),)
    return ()


class SharedClaims:
    """Thin façade over the shared `LedgerStore` for claim records."""

    def __init__(self, store: LedgerStore, *, policy: LedgerPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or LedgerPolicy()

    def snapshot(self) -> LedgerSnapshot:
        return self.store.read()

    def _validate(self, before: LedgerSnapshot, after: LedgerSnapshot) -> None:
        for record in after.records[len(before.records):]:
            if isinstance(record, ProjectItem):
                if record.kind not in CLAIM_KINDS and record.kind is not ProjectItemKind.RESEARCH_NOTE:
                    raise SharedLedgerError(f"{record.kind.value} is not a shared claim kind")
                if record.context is not None:
                    raise SharedLedgerError("a shared claim cannot be context-local; contextual items stay in their project")
                if record.origin not in SHARED_ORIGINS:
                    raise SharedLedgerError(f"origin {record.origin.value} is not admissible in the shared ledger")
            elif isinstance(record, Relation):
                if record.kind not in CLAIM_RELATIONS:
                    raise SharedLedgerError(f"{record.kind.value} is not a claim relation")
                if record.source.id == record.target.id:
                    raise SharedLedgerError("a claim relation joins two distinct claims")
                if record.kind in {RelationKind.EQUIVALENT_TO, RelationKind.IDENTIFIED_WITH, RelationKind.TRANSPORTED_FROM} and not record.evidence and not record.justification:
                    raise SharedLedgerError(f"{record.kind.value} needs evidence or a justification; similarity is not equivalence")
            else:
                raise SharedLedgerError(f"{type(record).__name__} records are not part of the shared claim ledger")
        self.policy.validate(before, after)

    def append(self, records: Iterable, *, expected_revision: int) -> LedgerSnapshot:
        return self.store.append(records, expected_revision=expected_revision, validate=self._validate)

    def add_claim(self, item: ProjectItem, *, expected_revision: int, relations: tuple[Relation, ...] = ()) -> LedgerSnapshot:
        return self.append((item, *relations), expected_revision=expected_revision)

    def relate(self, relation: Relation, *, expected_revision: int) -> LedgerSnapshot:
        return self.append((relation,), expected_revision=expected_revision)

    def claims(self) -> tuple[ProjectItem, ...]:
        return tuple(i for i in self.snapshot().current(ProjectItem) if i.kind in CLAIM_KINDS)

    def get(self, ref: VersionRef) -> ProjectItem:
        record = self.snapshot().get(ref)
        if not isinstance(record, ProjectItem):
            raise SharedLedgerError(f"{ref.id} is not a claim")
        return record

    def head(self, id: str) -> ProjectItem:
        record = self.snapshot().head(id)
        if not isinstance(record, ProjectItem):
            raise SharedLedgerError(f"{id} is not a claim")
        return record

    def relations_for(self, ref: VersionRef) -> tuple[Relation, ...]:
        return tuple(r for r in self.snapshot().current(Relation) if ref.id in (r.source.id, r.target.id))

    def search(self, query: str, *, limit: int = 20) -> tuple[ClaimHit, ...]:
        text = query.strip()
        lowered = text.casefold()
        words = {w.casefold() for w in WORD.findall(text)}
        hits: list[ClaimHit] = []
        for item in self.claims():
            if item.id == text:
                rank, why = 0, "exact id"
            elif any(alias.casefold() == lowered for alias in aliases_of(item)):
                rank, why = 1, "alias"
            elif item.name.casefold() == lowered:
                rank, why = 2, "exact name"
            else:
                haystack = {w.casefold() for w in WORD.findall(f"{item.name} {item.statement or ''}")}
                if words and words <= haystack:
                    rank, why = 3, "all words (fuzzy)"
                elif words and words & haystack and len(words & haystack) >= max(1, len(words) // 2):
                    rank, why = 4, "some words (fuzzy)"
                else:
                    continue
            hits.append(ClaimHit(ref=item.ref, kind=item.kind, name=item.name, rank=rank, match=why))
        hits.sort(key=lambda h: (h.rank, h.ref.id))
        return tuple(hits[:limit])

    def cluster(self, item: VersionRef, candidates: tuple[VersionRef, ...], *, reason: str, expected_revision: int) -> ProjectItem:
        """Record that these claims may be duplicates, without merging any of them."""
        members = sorted({item.id, *(c.id for c in candidates)})
        note = ProjectItem(
            id=f"cluster-{json_digest(members)[:16]}", kind=ProjectItemKind.RESEARCH_NOTE, name=f"near-duplicate cluster of {len(members)} claims",
            origin=ProjectOrigin.HUMAN_AUTHORED, statement=reason,
            semantics=(("cluster", json.dumps(members)), ("reason", reason), ("merged", "false")),
        )
        self.append((note,), expected_revision=expected_revision)
        return note

    def clusters_for(self, ref: VersionRef) -> tuple[ProjectItem, ...]:
        return tuple(n for n in self.snapshot().current(ProjectItem)
                     if n.kind is ProjectItemKind.RESEARCH_NOTE and any(k == "cluster" and ref.id in json.loads(v) for k, v in n.semantics))
