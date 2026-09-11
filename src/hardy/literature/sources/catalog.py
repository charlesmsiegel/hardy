"""Works, editions, and which exact artifacts belong to which edition.

Metadata similarity may propose that an artifact is a copy of some edition;
it never decides it. A grouping becomes authoritative only through a decision
that carries strong identity evidence (ISBN, DOI, exact arXiv version) or a
human's explicit confirmation, because a false grouping silently hands one
printing's claim links to another, while a temporary duplicate costs nothing
but a second look. The catalog is an append-only journal, so a decision can be
rejected or superseded and the history of how an artifact was identified stays
readable.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from hardy.foundation.journal import Journal, JournalSnapshot
from hardy.foundation.values import json_digest

from .contracts import (
    STRONG_EVIDENCE,
    BibliographicWork,
    EditionOrVersion,
    GroupingDecision,
    GroupingProposal,
    IdentityEvidence,
    MetadataAssertion,
    SourceArtifact,
    WorkKind,
)

TYPES = {t.__name__: t for t in (BibliographicWork, EditionOrVersion, GroupingProposal, GroupingDecision)}
IDENTIFIER_KINDS: dict[str, str] = {"isbn": "isbn", "doi": "doi", "arxiv": "arxiv_version"}
WORD = re.compile(r"[a-z0-9]+")


class CatalogError(ValueError):
    """A catalog transaction that would corrupt bibliographic identity."""


def strong_enough(evidence: Iterable[IdentityEvidence]) -> bool:
    return any(item.kind in STRONG_EVIDENCE for item in evidence)


def normalize_identifier(kind: str, value: str) -> str:
    kind = kind.lower()
    value = value.strip()
    if kind == "isbn":
        return re.sub(r"[^0-9Xx]", "", value).upper()
    if kind == "doi":
        return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", value, flags=re.I).lower()
    if kind == "arxiv":
        return re.sub(r"^(https?://arxiv\.org/abs/|arxiv:)", "", value, flags=re.I)
    return value


def _title_key(title: str) -> str:
    return " ".join(WORD.findall(title.lower()))


@dataclass(frozen=True)
class CatalogSnapshot:
    journal: JournalSnapshot

    @property
    def revision(self) -> int:
        return self.journal.revision

    @property
    def works(self) -> tuple[BibliographicWork, ...]:
        return tuple(_heads(self.journal.of(BibliographicWork)).values())

    @property
    def editions(self) -> tuple[EditionOrVersion, ...]:
        return tuple(_heads(self.journal.of(EditionOrVersion)).values())

    def work(self, id: str) -> BibliographicWork | None:
        return _heads(self.journal.of(BibliographicWork)).get(id)

    def edition(self, id: str) -> EditionOrVersion | None:
        return _heads(self.journal.of(EditionOrVersion)).get(id)

    def editions_of_work(self, work: str) -> tuple[EditionOrVersion, ...]:
        return tuple(e for e in self.editions if e.work == work)

    def proposals(self) -> tuple[GroupingProposal, ...]:
        return self.journal.of(GroupingProposal)

    def decisions(self) -> tuple[GroupingDecision, ...]:
        return self.journal.of(GroupingDecision)

    def authoritative(self) -> dict[str, str]:
        """artifact sha256 -> edition id, from authoritative decisions still standing."""
        proposals = {p.id: p for p in self.proposals()}
        grouped: dict[str, str] = {}
        for decision in self.decisions():
            proposal = proposals.get(decision.proposal)
            if proposal is None:
                continue
            if decision.status == "authoritative":
                grouped[proposal.artifact_sha256] = proposal.edition
        return grouped

    def decided(self) -> set[str]:
        return {d.proposal for d in self.decisions()}


def _heads(records: Iterable) -> dict[str, object]:
    heads: dict[str, object] = {}
    for record in records:
        heads[record.id] = record
    return heads


class Catalog:
    def __init__(self, directory: Path) -> None:
        self._journal = Journal(Path(directory), types=TYPES)

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(self._journal.read())

    def _append(self, records, *, expected_revision: int) -> CatalogSnapshot:
        return CatalogSnapshot(self._journal.append(records, expected_revision=expected_revision, validate=_validate))

    def add_work(self, work: BibliographicWork, *, expected_revision: int) -> CatalogSnapshot:
        return self._append([work], expected_revision=expected_revision)

    def add_edition(self, edition: EditionOrVersion, *, expected_revision: int) -> CatalogSnapshot:
        return self._append([edition], expected_revision=expected_revision)

    def propose_grouping(self, proposal: GroupingProposal, *, expected_revision: int) -> CatalogSnapshot:
        return self._append([proposal], expected_revision=expected_revision)

    def decide_grouping(self, decision: GroupingDecision, *, expected_revision: int) -> CatalogSnapshot:
        return self._append([decision], expected_revision=expected_revision)

    def edition_of(self, artifact_sha256: str) -> EditionOrVersion | None:
        snapshot = self.snapshot()
        edition = snapshot.authoritative().get(artifact_sha256)
        return snapshot.edition(edition) if edition else None

    def candidates_for(self, artifact_sha256: str) -> tuple[GroupingProposal, ...]:
        snapshot = self.snapshot()
        decided = snapshot.decided()
        return tuple(p for p in snapshot.proposals() if p.artifact_sha256 == artifact_sha256 and p.id not in decided)

    def artifacts_of(self, edition: str) -> tuple[str, ...]:
        return tuple(sorted(sha for sha, e in self.snapshot().authoritative().items() if e == edition))


def _validate(before: JournalSnapshot, after: JournalSnapshot) -> None:
    b, a = CatalogSnapshot(before), CatalogSnapshot(after)
    new = after.records[len(before.records):]
    works = {w.id for w in a.works}
    editions = {e.id: e for e in a.editions}
    proposals = {p.id: p for p in a.proposals()}
    for record in new:
        if isinstance(record, EditionOrVersion):
            if record.work not in works:
                raise CatalogError(f"edition {record.id} names unknown work {record.work}")
            earlier = b.edition(record.id)
            if earlier is not None and earlier.work != record.work:
                raise CatalogError(f"edition {record.id} cannot move from work {earlier.work} to {record.work}")
        elif isinstance(record, GroupingProposal):
            if record.edition not in editions:
                raise CatalogError(f"proposal {record.id} names unknown edition {record.edition}")
            if record.id in {p.id for p in b.proposals()}:
                raise CatalogError(f"proposal {record.id} already exists")
        elif isinstance(record, GroupingDecision):
            proposal = proposals.get(record.proposal)
            if proposal is None:
                raise CatalogError(f"decision {record.id} names unknown proposal {record.proposal}")
            if record.proposal in b.decided():
                raise CatalogError(f"proposal {record.proposal} was already decided")
            if record.status == "authoritative":
                if not strong_enough(proposal.evidence) and not record.decided_by.startswith("user:"):
                    raise CatalogError(
                        f"proposal {proposal.id} carries no strong identity evidence; authoritative grouping needs "
                        "an ISBN, DOI, exact arXiv version or explicit human confirmation"
                    )
                existing = b.authoritative().get(proposal.artifact_sha256)
                if existing is not None and existing != proposal.edition:
                    raise CatalogError(f"artifact {proposal.artifact_sha256} already belongs to edition {existing}")
        elif isinstance(record, BibliographicWork):
            continue
        else:
            raise CatalogError(f"unsupported catalog record {type(record).__name__}")


# --- proposing from metadata --------------------------------------------------


def propose_from_metadata(
    artifact: SourceArtifact, extracted: Mapping[str, str], snapshot: CatalogSnapshot, *, proposer: str,
) -> tuple[GroupingProposal, ...]:
    """Candidate editions for an artifact, from identifiers (strong) or title (weak). Never a decision."""
    proposals: list[GroupingProposal] = []
    identifiers = {k: normalize_identifier(k, v) for k, v in extracted.items() if k in IDENTIFIER_KINDS and v}
    title = _title_key(extracted.get("title", ""))
    for edition in snapshot.editions:
        evidence: list[IdentityEvidence] = []
        for kind, value in edition.identifiers:
            wanted = identifiers.get(kind)
            if wanted and normalize_identifier(kind, value) == wanted:
                evidence.append(IdentityEvidence(kind=IDENTIFIER_KINDS[kind], value=wanted, provenance=proposer))  # type: ignore[arg-type]
        work = snapshot.work(edition.work)
        if not evidence and title and work is not None and _title_key(work.title) == title:
            evidence.append(IdentityEvidence(kind="title_authors", value=extracted.get("title", ""), provenance=proposer))
        if evidence:
            proposals.append(GroupingProposal(
                id=f"prop-{json_digest([artifact.sha256, edition.id, [e.model_dump() for e in evidence]])[:16]}",
                artifact_sha256=artifact.sha256, edition=edition.id, evidence=tuple(evidence), proposer=proposer,
            ))
    return tuple(proposals)


def draft_edition(extracted: Mapping[str, str], *, kind: WorkKind, source: str) -> tuple[BibliographicWork, EditionOrVersion]:
    """A work and edition drafted from extracted metadata, every field marked as extracted."""
    title = extracted.get("title", "").strip() or "Untitled"
    authors = tuple(a.strip() for a in re.split(r";|,? and |,", extracted.get("author", "")) if a.strip())
    identifiers = tuple(
        (k, normalize_identifier(k, v)) for k, v in extracted.items() if k in IDENTIFIER_KINDS and v.strip()
    )
    year = extracted.get("year", "").strip() or None
    work_id = f"work-{json_digest([_title_key(title), [a.lower() for a in authors]])[:16]}"
    edition_id = f"edition-{json_digest([work_id, identifiers, year, extracted.get('venue', '')])[:16]}"
    assertions = tuple(
        MetadataAssertion(field=k, value=v, source=source, confidence="extracted") for k, v in extracted.items() if v.strip()
    )
    work = BibliographicWork(id=work_id, kind=kind, title=title, authors=authors,
                             assertions=tuple(a for a in assertions if a.field in {"title", "author"}))
    label = " ".join(p for p in (extracted.get("venue", "").strip(), year) if p) or "unlabelled edition"
    edition = EditionOrVersion(id=edition_id, work=work_id, label=label, venue=extracted.get("venue", "").strip() or None,
                               year=year, identifiers=identifiers,
                               assertions=tuple(a for a in assertions if a.field not in {"title", "author"}))
    return work, edition
