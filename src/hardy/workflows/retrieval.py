"""Rebuildable project/shared discovery, with authenticated delivery at each use.

An index describes current ledger records; it establishes no mathematical fact.
Stable identities and active scoped aliases outrank bounded text. Delivery binds
requesting project, scope, context and frozen source content, then delegates
proof/admission authority to B2 and exact artifact/import checks to named readers.
Shared-library provenance requires an explicit reader; a matching Lean environment
alone is insufficient. Missing sources and rejected discoveries remain receipts.
No separate memory store or formal name-ranking path is introduced. Semantic
summaries remain attributed records, including unresolved goals and dead ends.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import Field

from hardy.formal.contracts import EnvironmentIdentity
from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    EvidenceRef,
    MathematicalContext,
    Obligation,
    ProjectItem,
    ResearchState,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.validation import references

SourceStatus = Literal["available", "absent", "unreadable", "disabled"]
RetrievalGroup = Literal["verified_formal", "approved_external_assumptions",
    "concept_representation_context", "goals_conjectures", "approaches_deadends"]
INDEX_SCHEMA = "hardy.project-retrieval/v1"
MAX_INDEX_ENTRIES = 100_000
MAX_SUMMARY_CHARACTERS = 8_192


@dataclass(frozen=True)
class RetrievalSource:
    id: str
    snapshot: LedgerSnapshot | None = None
    kind: Literal["project", "shared_library"] = "project"
    status: SourceStatus = "available"
    provenance: ArtifactRef | None = None
    detail: str = ""


class SourceIdentity(FrozenModel):
    id: str
    kind: Literal["project", "shared_library"]
    status: SourceStatus
    content_digest: str
    provenance: ArtifactRef | None = None
    detail: str = ""


class ScopedAlias(FrozenModel):
    symbol: str
    binding: VersionRef
    context: VersionRef


class RelatedReference(FrozenModel):
    relation: VersionRef
    kind: str
    ref: VersionRef


class HistoricalAssessment(FrozenModel):
    ref: VersionRef
    research: ResearchState


class IndexEntry(FrozenModel):
    source_id: str
    ref: VersionRef
    kind: str
    group: RetrievalGroup
    context: VersionRef | None = None
    parent_context: VersionRef | None = None
    members: tuple[VersionRef, ...] = ()
    name: str
    summary: str
    summary_truncated: bool = False
    aliases: tuple[ScopedAlias, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    related: tuple[RelatedReference, ...] = ()
    historical_assessments: tuple[HistoricalAssessment, ...] = ()
    metadata_truncated: bool = False


class ProjectRetrievalIndex(FrozenModel):
    schema_version: Literal["hardy.project-retrieval/v1"] = INDEX_SCHEMA
    sources: tuple[SourceIdentity, ...]
    entries: tuple[IndexEntry, ...]

    @property
    def digest(self) -> str:
        return json_digest(self.model_dump(mode="json"))


class RetrievalQuery(FrozenModel):
    """max_characters bounds the serialized nonempty delivered-match envelope.

    The fixed empty-result control envelope and rejected diagnostics are excluded.
    """
    project_source: str
    text: str = Field(min_length=1, max_length=512)
    scope: VersionRef
    context: VersionRef | None = None
    environment: EnvironmentIdentity | None = None
    available_imports: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    limit: int = Field(default=10, ge=1, le=100, strict=True)
    max_characters: int = Field(default=8_192, ge=1, le=32_768, strict=True)

    @property
    def digest(self) -> str:
        return json_digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class FormalReadRequest:
    source: SourceIdentity
    subject: ProjectItem
    evidence: EvidenceRef
    query: RetrievalQuery


class FormalReading(FrozenModel):
    """Owner-authenticated checked declaration, including present importability."""
    source_id: str
    source_digest: str
    subject: VersionRef
    evidence: EvidenceRef
    scope: VersionRef
    context: VersionRef | None
    artifact: ArtifactRef
    declaration: str = Field(min_length=1)
    environment: EnvironmentIdentity
    required_imports: tuple[str, ...] = ()
    used_assumptions: tuple[VersionRef, ...] = ()
    importable: bool


@dataclass(frozen=True)
class SharedReadRequest:
    source: SourceIdentity
    query: RetrievalQuery


class SharedAuthorization(FrozenModel):
    source_id: str
    source_digest: str
    provenance: ArtifactRef
    project_source: str
    scope: VersionRef
    context: VersionRef | None


class RetrievalMatch(FrozenModel):
    entry: IndexEntry
    group: RetrievalGroup
    accepted: bool
    reason: str
    rank: int
    formal: FormalReading | None = None


class RetrievalResult(FrozenModel):
    query: RetrievalQuery
    index_digest: str
    sources: tuple[SourceIdentity, ...]
    matches: tuple[RetrievalMatch, ...]
    total_candidates: int
    truncated: bool
    characters_delivered: int

    @property
    def delivered(self) -> tuple[RetrievalMatch, ...]:
        return tuple(hit for hit in self.matches if hit.accepted)

    @property
    def rejected(self) -> tuple[RetrievalMatch, ...]:
        return tuple(hit for hit in self.matches if not hit.accepted)


def render_delivery(query: RetrievalQuery, index_digest: str, matches: tuple[RetrievalMatch, ...]) -> str:
    """Exact consumer text, including all accepted metadata and its identity."""
    return json.dumps({"index_digest": index_digest, "query_digest": query.digest,
        "delivered": [hit.model_dump(mode="json") for hit in matches if hit.accepted]},
        ensure_ascii=False, sort_keys=True)


def ledger_source(id: str, store: LedgerStore, *, enabled: bool = True,
                  kind: Literal["project", "shared_library"] = "project",
                  provenance: ArtifactRef | None = None) -> RetrievalSource:
    """Read one explicit store, distinguishing no ledger from a known empty one."""
    if not enabled:
        return RetrievalSource(id, kind=kind, status="disabled", provenance=provenance)
    try:
        if not (store.project / "ledger").exists():
            return RetrievalSource(id, kind=kind, status="absent", provenance=provenance)
        return RetrievalSource(id, store.read(), kind=kind, provenance=provenance)
    except (OSError, ValueError, TypeError) as error:
        return RetrievalSource(id, kind=kind, status="unreadable", provenance=provenance, detail=str(error))


def source_identity(source: RetrievalSource) -> SourceIdentity:
    if not source.id or source.status == "available" and source.snapshot is None:
        raise ValueError("available retrieval source requires identity and explicit snapshot")
    content = None if source.snapshot is None else {
        "records": [(type(record).__name__, record.model_dump(mode="json")) for record in source.snapshot.records],
        "active_context": source.snapshot.active_context.model_dump() if source.snapshot.active_context else None,
    }
    return SourceIdentity(id=source.id, kind=source.kind, status=source.status,
        content_digest=json_digest({"schema": INDEX_SCHEMA, "id": source.id, "kind": source.kind,
            "status": source.status, "content": content,
            "provenance": source.provenance.model_dump() if source.provenance else None}),
        provenance=source.provenance, detail=source.detail)


def _entries(source: RetrievalSource) -> tuple[IndexEntry, ...]:
    if source.status != "available":
        return ()
    snapshot = source.snapshot
    for record in snapshot.records:
        for ref in references(record):
            snapshot.get(ref)
    contexts = snapshot.current(MathematicalContext)
    contexts_by_member = {ref: context.ref for context in contexts for ref in context.declarations + context.bindings}
    aliases: dict[VersionRef, list[ScopedAlias]] = {}
    for binding in snapshot.current(ScopedBinding):
        if binding.target and binding.ref in contexts_by_member:
            aliases.setdefault(binding.target, []).append(ScopedAlias(symbol=binding.symbol,
                binding=binding.ref, context=contexts_by_member[binding.ref]))
    assumptions = {ref for scope in snapshot.current(Scope) for ref in scope.allowed_background + scope.allowed_interfaces}
    result = []
    graph = LedgerGraph(snapshot)
    related_kinds = {"pursues", "targets", "poses", "blocked_by", "interprets", "refines"}
    for record in (*snapshot.current(ProjectItem), *contexts):
        parent_context, members = None, ()
        if isinstance(record, MathematicalContext):
            group, name, context = "concept_representation_context", record.label, record.ref
            active = LedgerGraph(snapshot).active_context(record.ref)
            parent_context = record.parent
            members = tuple(member.ref for member in (*active.declarations, *active.bindings))
            summary = "\n".join((record.label,
                *(f"{d.id}@{d.digest}: {d.declaration.symbol}: {d.declaration.semantic_type} [{d.declaration.role.value}]"
                  for d in active.declarations),
                *(f"{b.id}@{b.digest}: {b.symbol}: {b.meaning}" for b in active.bindings)))
            artifacts = ()
        else:
            if record.ref in assumptions:
                group = "approved_external_assumptions"
            elif record.kind in {"theorem", "lemma", "proposition", "corollary", "claim", "external_result"}:
                group = "verified_formal"
            elif record.kind in {"goal", "question", "conjecture"}:
                group = "goals_conjectures"
            elif record.kind in {"approach", "research_note"}:
                group = "approaches_deadends"
            elif record.kind in {"concept", "representation", "definition", "standard_object", "declaration"}:
                group = "concept_representation_context"
            else:
                continue
            name = record.name
            summary = "\n".join(filter(None, (record.name, record.statement,
                record.research.model_dump_json() if record.research else None,
                *(f"{key}: {value}" for key, value in record.semantics))))
            context = record.context or contexts_by_member.get(record.ref)
            artifacts = record.artifacts
        related = tuple(RelatedReference(relation=edge.ref, kind=edge.kind.value,
            ref=edge.target if edge.source == record.ref else edge.source)
            for edge in graph.relations if edge.kind in related_kinds and record.ref in {edge.source, edge.target})
        history = tuple(HistoricalAssessment(ref=old.ref, research=old.research)
            for old in snapshot.records if isinstance(record, ProjectItem) and record.kind == "approach"
            and isinstance(old, ProjectItem) and old.id == record.id and old.ref != record.ref and old.research)
        selected_history = history[-16:]
        if selected_history:
            summary += "\n" + "\n".join(f"Historical assessment {h.ref.id}@{h.ref.digest}: {h.research.model_dump_json()}"
                                         for h in selected_history)
        result.append(IndexEntry(source_id=source.id, ref=record.ref, kind=type(record).__name__ if
            isinstance(record, MathematicalContext) else record.kind.value, group=group,
            context=context, parent_context=parent_context, members=members, name=name, summary=summary[:MAX_SUMMARY_CHARACTERS],
            summary_truncated=len(summary) > MAX_SUMMARY_CHARACTERS,
            aliases=tuple(aliases.get(record.ref, ())), artifacts=artifacts,
            related=related[:64], historical_assessments=selected_history,
            metadata_truncated=len(related) > 64 or len(history) > 16))
    return tuple(result)


def build_index(sources: tuple[RetrievalSource, ...]) -> ProjectRetrievalIndex:
    if len({source.id for source in sources}) != len(sources):
        raise ValueError("duplicate retrieval source identity")
    ordered = sorted(sources, key=lambda source: source.id)
    identities = tuple(source_identity(source) for source in ordered)
    entries = tuple(sorted((entry for source in ordered for entry in _entries(source)),
                           key=lambda entry: (entry.source_id, entry.ref.id, entry.ref.digest)))
    if len(entries) > MAX_INDEX_ENTRIES:
        raise ValueError("retrieval index entry bound exceeded")
    return ProjectRetrievalIndex(sources=identities, entries=entries)


class ProjectRetriever:
    def __init__(self, index: ProjectRetrievalIndex, *, read_source: Callable[[str], RetrievalSource],
                 policies: Mapping[str, LedgerPolicy] | None = None,
                 read_formal: Callable[[FormalReadRequest], FormalReading | None] | None = None,
                 read_artifact: Callable[[ArtifactRef], bytes | None] | None = None,
                 read_shared: Callable[[SharedReadRequest], SharedAuthorization | None] | None = None):
        self.index = ProjectRetrievalIndex.model_validate(index.model_dump())
        self.read_source = read_source
        self.policies = dict(policies or {})
        self.read_formal = read_formal
        self.read_artifact = read_artifact
        self.read_shared = read_shared

    def _read(self, identifier: str) -> RetrievalSource:
        try:
            source = self.read_source(identifier)
            if not isinstance(source, RetrievalSource) or source.id != identifier:
                raise ValueError("retrieval source reader returned another identity")
            source_identity(source)
            return source
        except (OSError, ValueError, TypeError) as error:
            return RetrievalSource(identifier, status="unreadable", detail=str(error))

    @staticmethod
    def _contexts(snapshot: LedgerSnapshot, query: RetrievalQuery) -> set[VersionRef]:
        return {context.ref for context in LedgerGraph(snapshot).context_chain(query.context)} if query.context else set()

    def _rank(self, entry: IndexEntry, source: RetrievalSource, query: RetrievalQuery) -> int | None:
        text = query.text.casefold().strip()
        if text == entry.ref.id.casefold():
            return 0
        if source.snapshot is not None:
            try:
                contexts = self._contexts(source.snapshot, query) if source.kind == "project" else set()
                if entry.kind != "MathematicalContext" and entry.context and entry.context not in contexts:
                    return None  # Transient local vocabulary is not a global hit.
                active = LedgerGraph(source.snapshot).active_context(query.context) if query.context and source.kind == "project" else None
                if entry.kind == "declaration" and (active is None or entry.ref not in {d.ref for d in active.declarations}):
                    return None
                active_bindings = {binding.ref for binding in active.bindings} if active else set()
                if any(alias.symbol.casefold() == text and alias.binding in active_bindings for alias in entry.aliases):
                    return 1
            except (ValueError, TypeError):
                return None
        if text == entry.name.casefold():
            return 2
        if any(related.ref.id.casefold() == text for related in entry.related):
            return 3
        searchable = entry.name if entry.kind == "MathematicalContext" else entry.summary
        words = set(re.findall(r"\w+", searchable.casefold()))
        query_words = set(re.findall(r"\w+", text))
        if query_words and query_words <= words:
            return 4
        return None

    @staticmethod
    def _proof_works(source: SourceIdentity, snapshot: LedgerSnapshot, subject: ProjectItem,
                     query: RetrievalQuery, scope: Scope, policy: LedgerPolicy | None) -> tuple[Obligation, ...]:
        if policy is None:
            raise ValueError("current source ledger policy is unavailable")
        context = query.context if source.kind == "project" else None
        return tuple(work for work in snapshot.current(Obligation)
            if work.item == subject.ref and (source.kind == "shared_library" or work.scope == scope)
            and work.kind in {"prove", "resolve_goal"} and work.status == "resolved"
            and work.resolution is not None and policy.is_accepted(snapshot, work.resolution)
            and policy.premise_allowed(snapshot, subject.ref, scope=work.scope, context=context))

    def _formal(self, source: SourceIdentity, snapshot: LedgerSnapshot, subject: ProjectItem,
                query: RetrievalQuery, scope: Scope, policy: LedgerPolicy | None) -> FormalReading:
        if self.read_formal is None or self.read_artifact is None or query.environment is None:
            raise ValueError("formal evidence/artifact reader or verifier environment unavailable")
        works = self._proof_works(source, snapshot, subject, query, scope, policy)
        context = query.context if source.kind == "project" else None
        evidence = tuple((work, reference) for work in works for reference in work.resolution.evidence
                         if reference.kind == "formal")
        for work, reference in evidence:
            reading = self.read_formal(FormalReadRequest(source, subject, reference, query))
            if not isinstance(reading, FormalReading):
                continue
            reading = FormalReading.model_validate(reading.model_dump())
            if ((reading.source_id, reading.source_digest, reading.subject, reading.evidence, reading.scope, reading.context)
                    != (source.id, source.content_digest, subject.ref, reference, query.scope, query.context)
                    or reference.kind != "formal" or reference.subject != subject.ref
                    or reading.environment != query.environment or not reading.importable
                    or not set(reading.required_imports) <= set(query.available_imports)
                    or not set(reading.used_assumptions) <= set(scope.allowed_background + scope.allowed_interfaces)
                    or reading.artifact not in subject.artifacts):
                continue
            content = self.read_artifact(reading.artifact)
            if (isinstance(content, bytes) and sha256(content).hexdigest() == reading.artifact.digest
                    and policy.is_accepted(snapshot, work.resolution)
                    and policy.premise_allowed(snapshot, subject.ref, scope=work.scope, context=context)):
                return reading
        raise ValueError("exact kernel proof, declaration, artifact or importability could not be authenticated")

    def _check_current_evidence(self, match: RetrievalMatch, source: RetrievalSource,
                                identity: SourceIdentity, project: RetrievalSource, query: RetrievalQuery) -> None:
        """Check cached formal readings after every declaration/artifact reader ran."""
        scope = project.snapshot.get(query.scope)
        policy = self.policies.get(source.id)
        if match.formal is not None:
            subject = source.snapshot.get(match.entry.ref)
            works = self._proof_works(identity, source.snapshot, subject, query, scope, policy)
            if not any(match.formal.evidence in work.resolution.evidence for work in works):
                raise ValueError("kernel evidence revoked before retrieval delivery")
        elif match.group == "approved_external_assumptions":
            if policy is None or not policy.premise_allowed(source.snapshot, match.entry.ref, scope=scope, context=query.context):
                raise ValueError("external assumption revoked before retrieval delivery")

    def _deliver(self, entry: IndexEntry, source: RetrievalSource, identity: SourceIdentity,
                 project: RetrievalSource, query: RetrievalQuery) -> FormalReading | None:
        if source.status != "available" or source.snapshot is None:
            raise ValueError(f"source {source.status}: {source.detail}")
        frozen = next(s for s in self.index.sources if s.id == source.id)
        if identity != frozen:
            raise ValueError("source content changed since index construction")
        if project.status != "available" or project.snapshot is None:
            raise ValueError(f"requesting project source {project.status}")
        scope = project.snapshot.get(query.scope)
        if not isinstance(scope, Scope) or project.snapshot.head(scope.id) != scope:
            raise ValueError("requesting scope is stale or invalid")
        if query.context:
            context = project.snapshot.get(query.context)
            if not isinstance(context, MathematicalContext) or project.snapshot.head(context.id) != context:
                raise ValueError("requesting context is stale or invalid")
        snapshot = source.snapshot
        if entry not in _entries(source):
            raise ValueError("indexed content does not match the current exact source record")
        subject = snapshot.get(entry.ref)
        if snapshot.head(subject.id) != subject:
            raise ValueError("indexed record is no longer current")
        if source.kind == "project" and source.id != query.project_source:
            raise ValueError("foreign project records require explicit cross-project admission")
        if entry.kind != "MathematicalContext" and entry.context and (source.kind == "shared_library" or entry.context not in self._contexts(snapshot, query)):
            raise ValueError("context-local item is unavailable in the requesting context")
        if isinstance(subject, ProjectItem) and subject.declaration:
            active = LedgerGraph(snapshot).active_context(query.context) if query.context else None
            if active is None or subject.ref not in {declaration.ref for declaration in active.declarations}:
                raise ValueError("context-local declaration is shadowed or unavailable")
        if source.kind == "shared_library":
            authorization = self.read_shared(SharedReadRequest(identity, query)) if self.read_shared else None
            if (not isinstance(authorization, SharedAuthorization) or identity.provenance is None
                    or (authorization.source_id, authorization.source_digest, authorization.provenance,
                        authorization.project_source, authorization.scope, authorization.context)
                    != (identity.id, identity.content_digest, identity.provenance, query.project_source, query.scope, query.context)):
                raise ValueError("shared-library source provenance is not authenticated for this use")
        policy = self.policies.get(source.id)
        if entry.group == "approved_external_assumptions":
            if (source.kind != "project" or subject.ref not in scope.allowed_background + scope.allowed_interfaces
                    or policy is None or not policy.premise_allowed(snapshot, subject.ref, scope=scope, context=query.context)):
                raise ValueError("external assumption is not approved in the requesting scope")
        elif entry.group == "verified_formal":
            return self._formal(identity, snapshot, subject, query, scope, policy)
        return None

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        query = RetrievalQuery.model_validate(query.model_dump())
        known = {source.id for source in self.index.sources}
        if query.project_source not in known or not set(query.source_ids) <= known:
            raise ValueError("query names a source absent from the frozen index")
        live = {id: self._read(id) for id in sorted(known)}
        identities = {id: source_identity(source) for id, source in live.items()}
        candidates = [(rank, entry) for entry in self.index.entries
            if (not query.source_ids or entry.source_id in query.source_ids)
            and (rank := self._rank(entry, live[entry.source_id], query)) is not None]
        candidates.sort(key=lambda pair: (pair[0], pair[1].source_id, pair[1].ref.id, pair[1].ref.digest))
        matches = []
        truncated = len(candidates) > query.limit or any(entry.summary_truncated or entry.metadata_truncated for _, entry in candidates[:query.limit])
        for rank, entry in candidates[:query.limit]:
            if len(entry.summary) > query.max_characters:
                truncated = True
                matches.append(RetrievalMatch(entry=entry.model_copy(update={"summary": ""}), group=entry.group,
                    accepted=False, reason="retrieval text budget exhausted", rank=rank))
                continue
            try:
                formal = self._deliver(entry, live[entry.source_id], identities[entry.source_id],
                                       live[query.project_source], query)
                accepted, reason = True, "Authenticated kernel declaration" if formal else "Recorded " + entry.group
            except (OSError, ValueError, TypeError) as error:
                formal, accepted, reason = None, False, str(error)
            matches.append(RetrievalMatch(entry=entry, group=entry.group, accepted=accepted, reason=reason, rank=rank, formal=formal))
        # Later declaration/artifact readers can revoke an earlier capability
        # receipt without a ledger edit. Audit cached readings after all such
        # calls; calling those readers again here would reopen the same gap.
        for position, match in enumerate(matches):
            if not match.accepted:
                continue
            try:
                self._check_current_evidence(match, live[match.entry.source_id], identities[match.entry.source_id],
                                             live[query.project_source], query)
            except (OSError, ValueError, TypeError) as error:
                matches[position] = match.model_copy(update={"accepted": False, "formal": None, "reason": str(error)})
        # Readers may mutate durable sources while authenticating. Never deliver
        # a mixture of source revisions, even when the indexed item is unchanged.
        for id, previous in identities.items():
            if source_identity(self._read(id)) != previous:
                matches = [match.model_copy(update={"accepted": False, "formal": None,
                    "reason": "source changed during retrieval authentication"}) for match in matches]
                break
        delivered: list[RetrievalMatch] = []
        characters = 0
        for position, match in enumerate(matches):
            if not match.accepted:
                continue
            size = len(render_delivery(query, self.index.digest, (*delivered, match)))
            if size > query.max_characters:
                truncated = True
                matches[position] = match.model_copy(update={"accepted": False, "formal": None,
                    "reason": "serialized retrieval delivery budget exhausted"})
            else:
                delivered.append(match)
                characters = size
        return RetrievalResult(query=query, index_digest=self.index.digest,
            sources=tuple(identities.values()), matches=tuple(matches), total_candidates=len(candidates),
            truncated=truncated, characters_delivered=characters)


