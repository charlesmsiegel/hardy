"""Which library sources a problem uses, as refs only.

A seed names an exact artifact (and optionally the edition, tree and subtree
it was seeded under), a priority, and the intent behind it. It holds no bytes
and no text, so a problem directory committed to a repository carries the
identity of what it read and nothing a publisher could object to. The journal
lives at `<problem>/sources/` and is append-only: removing a seed is a record.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hardy.foundation.journal import Journal, JournalSnapshot
from hardy.foundation.values import FrozenModel

from .contracts import Digest, StableId

DIRECTORY = "sources"


class SourceSeed(FrozenModel):
    id: StableId
    artifact_sha256: Digest
    edition: StableId | None = None
    tree: StableId | None = None
    subtree: StableId | None = None
    priority: int = 0
    intent: str | None = None
    seeded_at: str


class SeedRemoval(FrozenModel):
    seed: StableId
    reason: str = ""


TYPES = {t.__name__: t for t in (SourceSeed, SeedRemoval)}


class SeedStore:
    def __init__(self, problem: Path) -> None:
        self.problem = Path(problem)
        self._journal = Journal(self.problem / DIRECTORY, types=TYPES)

    def revision(self) -> int:
        return self._journal.read().revision

    def seeds(self) -> tuple[SourceSeed, ...]:
        snapshot = self._journal.read()
        removed = {r.seed for r in snapshot.of(SeedRemoval)}
        active = [s for s in snapshot.of(SourceSeed) if s.id not in removed]
        return tuple(sorted(active, key=lambda s: (-s.priority, s.seeded_at, s.id)))

    def add(self, seed: SourceSeed, *, expected_revision: int) -> JournalSnapshot:
        return self._journal.append([seed], expected_revision=expected_revision, validate=_validate)

    def remove(self, seed_id: str, *, expected_revision: int, reason: str = "") -> JournalSnapshot:
        return self._journal.append([SeedRemoval(seed=seed_id, reason=reason)], expected_revision=expected_revision, validate=_validate)

    def by_prefix(self, prefix: str) -> tuple[SourceSeed, ...]:
        """Seeds whose artifact digest or id starts with `prefix`."""
        return tuple(s for s in self.seeds() if s.artifact_sha256.startswith(prefix) or s.id == prefix)


def new_seed(artifact_sha256: str, *, edition: str | None = None, tree: str | None = None, subtree: str | None = None,
             priority: int = 0, intent: str | None = None, now: datetime | None = None) -> SourceSeed:
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    return SourceSeed(id=f"seed-{artifact_sha256[:16]}-{stamp.replace(':', '').replace('+', 'p')}", artifact_sha256=artifact_sha256,
                      edition=edition, tree=tree, subtree=subtree, priority=priority, intent=intent, seeded_at=stamp)


def _validate(before: JournalSnapshot, after: JournalSnapshot) -> None:
    ids = [s.id for s in after.of(SourceSeed)]
    if len(ids) != len(set(ids)):
        raise ValueError("seed ids must be unique")
    known = set(ids)
    for record in after.records[len(before.records):]:
        if isinstance(record, SeedRemoval) and record.seed not in known:
            raise ValueError(f"cannot remove unknown seed {record.seed}")
