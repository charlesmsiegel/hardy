"""Frozen problem core, per-worker research brief, staged initial context, and its manifest.

Every worker on one target receives the same correctness-critical core: the
exact statement, its mathematical context, its trust scope, the exact
dependency revisions and the open work blocking them, pinned to one project
revision and hashed so two workers can be shown to have attacked the same
target state. Diversity is the brief's business and never mutates the core.

Initial context is built in stages. The mandatory kernel is deterministic and
comes first; a structural map follows; supplemental candidates are ranked and
fitted to an explicit preload budget; hidden selectors remove candidates but
can never remove what the graph says the target needs, and a kernel larger
than the budget is an overflow condition, never a truncation. Nothing here
calls a provider or writes the ledger.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Literal

from pydantic import Field

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.context import ContextManager
from hardy.workflows.ledger.contracts import (
    LedgerRecord,
    Obligation,
    ObligationKind,
    ObligationStatus,
    ProjectItem,
    ProjectItemKind,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import DEPENDENCIES, LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore

BUILDER = "hardy.delegation.context/v1"
WORKER_MARKER = "[Hardy delegation worker — written by Hardy, not the user]"

_PROOF_WORK = frozenset({ObligationKind.PROVE, ObligationKind.RESOLVE_GOAL})


class ProblemCore(FrozenModel):
    """The shared, hashable target semantics. Reproducible from the ledger revision."""

    target: VersionRef
    kind: str
    name: str
    statement: str | None
    context: VersionRef | None
    context_text: str
    scope: VersionRef
    trusted_assumptions: tuple[VersionRef, ...] = ()
    dependencies: tuple[VersionRef, ...] = ()
    verified_dependencies: tuple[VersionRef, ...] = ()
    blockers: tuple[VersionRef, ...] = ()
    project_revision: int

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/ProblemCore/v1", "value": self.model_dump(mode="json")})


class ResearchBrief(FrozenModel):
    """One worker's intentional search variation. It cannot weaken the core."""

    target: VersionRef
    task_mode: str
    framing: str = ""
    reasoning_direction: str = "forward"
    preferred_representations: tuple[str, ...] = ()
    required_methods: tuple[str, ...] = ()
    discouraged_methods: tuple[str, ...] = ()
    forbidden_methods: tuple[str, ...] = ()
    retrieval_intent: str = ""
    independence: Literal["shared", "blind"] = "shared"
    diversity: tuple[tuple[str, str], ...] = ()
    model: str | None = None
    seed: int | None = None

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/ResearchBrief/v1", "value": self.model_dump(mode="json")})


class ContextResolution(str, Enum):
    FULL = "full"
    STATEMENT = "statement"
    SUMMARY = "summary"
    POINTER = "pointer"


SelectedBy = Literal["mandatory", "deterministic", "portfolio_planner", "user", "parent", "promotion"]


class ContextItem(FrozenModel):
    """One preloaded or discoverable item, with why it is there and what it cost."""

    ref: VersionRef | None = None
    source: str | None = None
    kind: str = ""
    resolution: ContextResolution
    inclusion_reason: str
    selected_by: SelectedBy
    trust: str = "unverified"
    estimated_tokens: int = Field(ge=0, strict=True)
    preload: bool = True
    text: str = ""


class ContextPolicy(FrozenModel):
    """Explicit steering over ranking: budget, pins, exclusions and hidden selectors."""

    preload_tokens: int = Field(default=6000, ge=1, strict=True)
    hidden_ids: tuple[str, ...] = ()
    hidden_refs: tuple[VersionRef, ...] = ()
    pinned_refs: tuple[VersionRef, ...] = ()
    excluded_refs: tuple[VersionRef, ...] = ()
    #: Sources made prominent at launch as pointers with an index line; their
    #: text is retrieved lazily, never pasted wholesale.
    seeded_sources: tuple[str, ...] = ()

    def hides(self, ref: VersionRef) -> bool:
        return ref.id in self.hidden_ids or ref in self.hidden_refs

    @property
    def hidden_selectors(self) -> tuple[str, ...]:
        return (*self.hidden_ids, *(f"{ref.id}@{ref.digest}" for ref in self.hidden_refs))

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/ContextPolicy/v1", "value": self.model_dump(mode="json")})


class ContextManifest(FrozenModel):
    """What a worker was launched with; enough to reconstruct its initial view."""

    id: str
    problem_core_digest: str
    research_brief_digest: str
    project_revision: int
    included_refs: tuple[VersionRef, ...]
    included_items: tuple[ContextItem, ...] = ()
    hidden_selectors: tuple[str, ...] = ()
    project_retrieval: Literal["permitted", "denied"] = "permitted"
    literature_retrieval: Literal["permitted", "denied"] = "permitted"
    preload_budget: int = 0
    context_policy_digest: str | None = None
    builder: str = Field(default=BUILDER)


class InitialWorkingSet(FrozenModel):
    items: tuple[ContextItem, ...]
    structural_map: str
    budget_tokens: int
    overflow: bool
    selection: Literal["deterministic", "planner"] = "deterministic"
    project_revision: int
    manifest_hidden: tuple[str, ...] = ()
    policy_digest: str

    def manifest(self, id: str, *, problem_core_digest: str, research_brief_digest: str) -> ContextManifest:
        return ContextManifest(
            id=id, problem_core_digest=problem_core_digest, research_brief_digest=research_brief_digest,
            project_revision=self.project_revision,
            included_refs=tuple(item.ref for item in self.items if item.ref is not None),
            included_items=self.items, hidden_selectors=self.manifest_hidden,
            preload_budget=self.budget_tokens, context_policy_digest=self.policy_digest,
        )

    def rendered(self, *, without: VersionRef | None = None) -> str:
        lines = []
        for item in self.items:
            if not item.preload or item.ref == without:
                continue
            lines.append(f"- [{item.resolution.value}; {item.trust}; {item.inclusion_reason}] {item.text}")
        return "\n".join(lines)


# -- estimation and rendering -----------------------------------------------------

def estimate_tokens(text: str) -> int:
    """An upper bound: one token per four bytes, never zero for nonempty text."""
    return len(text.encode("utf-8")) // 4 + 1


def _short(ref: VersionRef) -> str:
    return f"{ref.id}@{ref.digest[:12]}"


def _trust(snapshot: LedgerSnapshot, ref: VersionRef) -> str:
    """What the ledger's own obligations say, and nothing more."""
    open_work = []
    for obligation in snapshot.current(Obligation):
        if obligation.item.id != ref.id:
            continue
        if obligation.status is ObligationStatus.RESOLVED and obligation.kind in _PROOF_WORK:
            return "verified"
        if obligation.status not in {ObligationStatus.RESOLVED, ObligationStatus.DISMISSED}:
            open_work.append(obligation.kind.value)
    return f"open: {', '.join(sorted(set(open_work)))}" if open_work else "no open work"


def _render(record: LedgerRecord, resolution: ContextResolution) -> str:
    if isinstance(record, ProjectItem):
        head = f"{record.kind.value} {record.id}"
        if record.declaration is not None:
            head += f" ({record.declaration.symbol} : {record.declaration.semantic_type})"
        if resolution is ContextResolution.POINTER:
            return f"{head}: {record.name}"
        if resolution is ContextResolution.FULL:
            return json.dumps({"id": record.id, "kind": record.kind.value, "name": record.name,
                               "statement": record.statement, "digest": record.digest[:12]},
                              ensure_ascii=False)
        return f"{head}: {record.statement or record.name}"
    if isinstance(record, ScopedBinding):
        return f"binding {record.symbol} ({record.kind.value}): {record.meaning}"
    if isinstance(record, Scope):
        return (f"scope {record.id}: must_prove {[r.id for r in record.must_prove]}, "
                f"allowed_background {[r.id for r in record.allowed_background]}")
    if isinstance(record, Obligation):
        return f"obligation {record.id}: {record.kind.value} {record.item.id} [{record.status.value}]"
    return f"{type(record).__name__} {record.id}"


def _item(snapshot: LedgerSnapshot, ref: VersionRef, resolution: ContextResolution, reason: str,
          selected_by: SelectedBy) -> ContextItem:
    record = snapshot.get(ref)
    text = _render(record, resolution)
    kind = record.kind.value if isinstance(record, ProjectItem) else type(record).__name__.lower()
    return ContextItem(ref=ref, kind=kind, resolution=resolution, inclusion_reason=reason,
                       selected_by=selected_by, trust=_trust(snapshot, ref),
                       estimated_tokens=estimate_tokens(text), text=text)


# -- the core --------------------------------------------------------------------

def build_problem_core(store: LedgerStore, target: VersionRef, *, scope: VersionRef) -> ProblemCore:
    snapshot = store.read()
    item = snapshot.get(target)
    if not isinstance(item, ProjectItem):
        raise ValueError("a problem core requires a project item target")
    if snapshot.head(item.id).ref != target:
        raise ValueError("stale target: the project head has moved past this revision")
    policy = snapshot.get(scope)
    if not isinstance(policy, Scope):
        raise ValueError("a problem core requires a trust scope")
    graph = LedgerGraph(snapshot)
    dependencies = graph.dependency_closure(target)
    verified = tuple(ref for ref in dependencies if _trust(snapshot, ref) == "verified")
    context_text = ContextManager(store).render(item.context) if item.context is not None else ""
    return ProblemCore(
        target=target, kind=item.kind.value, name=item.name, statement=item.statement,
        context=item.context, context_text=context_text, scope=scope,
        trusted_assumptions=policy.allowed_background, dependencies=dependencies,
        verified_dependencies=verified,
        blockers=tuple(o.ref for o in graph.blockers(target)),
        project_revision=snapshot.revision,
    )


# -- the stages -------------------------------------------------------------------

def mandatory_kernel(snapshot: LedgerSnapshot, target: VersionRef, scope: VersionRef,
                     policy: ContextPolicy) -> tuple[ContextItem, ...]:
    """Stage A: what the graph says the target needs. Deterministic; never omitted."""
    graph = LedgerGraph(snapshot)
    item = snapshot.get(target)
    if not isinstance(item, ProjectItem):
        raise ValueError("a working set requires a project item target")
    items = [_item(snapshot, target, ContextResolution.FULL, "the exact target", "mandatory")]
    if item.context is not None:
        # The context the statement is made in: its declarations and bindings
        # fix what the symbols mean, whether or not the graph records a use.
        active = graph.active_context(item.context)
        for record in (*active.declarations, *active.bindings):
            items.append(_item(snapshot, record.ref, ContextResolution.FULL,
                               "declaration or binding in the target's mathematical context", "mandatory"))
    for ref in graph.dependency_closure(target):
        items.append(_item(snapshot, ref, ContextResolution.STATEMENT, "exact dependency of the target", "mandatory"))
    items.append(_item(snapshot, scope, ContextResolution.POINTER, "trust scope", "mandatory"))
    policy_record = snapshot.get(scope)
    if isinstance(policy_record, Scope):
        for ref in policy_record.allowed_background:
            items.append(_item(snapshot, ref, ContextResolution.POINTER, "trusted background", "mandatory"))
    for obligation in graph.blockers(target):
        items.append(_item(snapshot, obligation.ref, ContextResolution.POINTER,
                           "open work blocking a dependency", "mandatory"))
    seen: dict[VersionRef, ContextItem] = {}
    for entry in items:
        seen.setdefault(entry.ref, entry)
    for ref in seen:
        if policy.hides(ref):
            raise ValueError(f"isolation policy would remove correctness-critical material: {ref.id}")
    return tuple(seen.values())


def structural_map(snapshot: LedgerSnapshot, target: VersionRef, *, depth: int = 3) -> str:
    """Stage B: navigation, not evidence. Dependencies down, direct consumers across."""
    graph = LedgerGraph(snapshot)

    def label(ref: VersionRef) -> str:
        record = snapshot.get(ref)
        kind = record.kind.value if isinstance(record, ProjectItem) else type(record).__name__.lower()
        return f"{ref.id} ({kind}) [{_trust(snapshot, ref)}]"

    def children(ref: VersionRef) -> list[VersionRef]:
        return sorted({r.target for r in graph.relations if r.source == ref and r.kind in DEPENDENCIES},
                      key=lambda r: r.id)

    lines = [f"Target {label(target)}"]

    def walk(ref: VersionRef, prefix: str, remaining: int) -> None:
        if remaining == 0:
            return
        kids = children(ref)
        for index, child in enumerate(kids):
            last = index == len(kids) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}depends_on {label(child)}")
            walk(child, prefix + ("    " if last else "│   "), remaining - 1)

    walk(target, "", depth)
    consumers = sorted({r.source for r in graph.relations if r.target == target and r.kind in DEPENDENCIES},
                       key=lambda r: r.id)
    for index, consumer in enumerate(consumers):
        lines.append(f"{'└── ' if index == len(consumers) - 1 else '├── '}used_by {label(consumer)}")
    return "\n".join(lines)


def candidate_pool(snapshot: LedgerSnapshot, target: VersionRef, policy: ContextPolicy, *,
                   exclude: frozenset[VersionRef] = frozenset()) -> tuple[ContextItem, ...]:
    """Stage C: optional material with a recorded reason each; hidden and excluded refs never enter."""
    graph = LedgerGraph(snapshot)
    candidates: list[tuple[VersionRef, str]] = []
    dependencies = set(graph.dependency_closure(target))
    for relation in graph.relations:
        if relation.target == target and relation.kind in DEPENDENCIES:
            candidates.append((relation.source, "consumer of the target"))
    for record in graph.research_neighborhood(target):
        if record.ref != target:
            candidates.append((record.ref, f"research neighbourhood ({record.kind.value})"))
    for record in snapshot.current(ProjectItem):
        if record.ref == target or record.kind in {ProjectItemKind.DECLARATION, ProjectItemKind.REPRESENTATION}:
            continue
        shared = dependencies & set(graph.dependency_closure(record.ref))
        if shared and record.kind is ProjectItemKind.EXAMPLE:
            candidates.append((record.ref, "example over shared definitions"))
        elif shared:
            candidates.append((record.ref, "sibling sharing a dependency"))
    for record in snapshot.current(ProjectItem):
        if record.kind is ProjectItemKind.RESEARCH_NOTE:
            candidates.append((record.ref, "research note"))
    pool: dict[VersionRef, ContextItem] = {}
    for ref, reason in candidates:
        if ref in pool or ref in exclude or policy.hides(ref) or ref in policy.excluded_refs:
            continue
        pool[ref] = _item(snapshot, ref, ContextResolution.STATEMENT, reason, "deterministic")
    return tuple(pool.values())


def seeded_items(policy: ContextPolicy, sources: Mapping[str, str] | None) -> tuple[ContextItem, ...]:
    """A seeded source is prominent as a pointer plus its index line; the body stays behind read_source."""
    items = []
    for paper_id in policy.seeded_sources:
        index = (sources or {}).get(paper_id, "(no index available)")
        text = f"seeded source {paper_id}: {index} — retrieve exact text with read_source"
        items.append(ContextItem(source=paper_id, kind="source", resolution=ContextResolution.POINTER,
                                 inclusion_reason="seeded by the user", selected_by="user",
                                 trust="source lead; not evidence", estimated_tokens=estimate_tokens(text),
                                 text=text))
    return tuple(items)


def fit_to_budget(mandatory: tuple[ContextItem, ...], candidates: tuple[ContextItem, ...],
                  policy: ContextPolicy, *, already_preloaded: frozenset[VersionRef] = frozenset(),
                  seeded: tuple[ContextItem, ...] = ()) -> tuple[tuple[ContextItem, ...], bool]:
    """Stage E: the kernel always; then seeds and pins; then novel candidates; then the rest."""
    used = sum(item.estimated_tokens for item in mandatory)
    overflow = used > policy.preload_tokens
    chosen = list(mandatory)
    pinned = set(policy.pinned_refs)
    ordered = (
        list(seeded)
        + [item.model_copy(update={"selected_by": "user", "inclusion_reason": "pinned by the user or parent"})
           for item in candidates if item.ref in pinned]
        + [item for item in candidates if item.ref not in pinned and item.ref not in already_preloaded]
        + [item.model_copy(update={"inclusion_reason": item.inclusion_reason + "; also preloaded by a sibling"})
           for item in candidates if item.ref not in pinned and item.ref in already_preloaded]
    )
    for item in ordered:
        if overflow or used + item.estimated_tokens > policy.preload_tokens:
            continue
        chosen.append(item)
        used += item.estimated_tokens
    return tuple(chosen), overflow


Planner = Callable[[tuple[ContextItem, ...]], tuple[ContextItem, ...]]


def build_working_set(store: LedgerStore, target: VersionRef, scope: VersionRef, brief: ResearchBrief,
                      policy: ContextPolicy, *, portfolio: tuple[ContextManifest, ...] = (),
                      planner: Planner | None = None,
                      sources: Mapping[str, str] | None = None) -> InitialWorkingSet:
    """Stages A-E over one snapshot. Routine jobs use no model; a planner only reorders candidates."""
    snapshot = store.read()
    mandatory = mandatory_kernel(snapshot, target, scope, policy)
    kernel_refs = frozenset(item.ref for item in mandatory)
    candidates = candidate_pool(snapshot, target, policy, exclude=kernel_refs)
    selection: Literal["deterministic", "planner"] = "deterministic"
    if planner is not None and len(candidates) > 1:
        planned = planner(candidates)
        if {item.ref for item in planned} != {item.ref for item in candidates}:
            raise ValueError("a context planner may reorder candidates, not invent or drop them")
        candidates = tuple(item.model_copy(update={"selected_by": "portfolio_planner"}) for item in planned)
        selection = "planner"
    sibling_preloads = frozenset(
        item.ref for manifest in portfolio for item in manifest.included_items
        if item.ref is not None and item.selected_by != "mandatory" and item.preload)
    items, overflow = fit_to_budget(mandatory, candidates, policy, already_preloaded=sibling_preloads,
                                    seeded=seeded_items(policy, sources))
    return InitialWorkingSet(
        items=items, structural_map=structural_map(snapshot, target), budget_tokens=policy.preload_tokens,
        overflow=overflow, selection=selection, project_revision=snapshot.revision,
        manifest_hidden=policy.hidden_selectors, policy_digest=policy.digest,
    )


# -- the launch prompt --------------------------------------------------------------

def render_launch_prompt(core: ProblemCore, brief: ResearchBrief,
                         working: InitialWorkingSet | None = None) -> str:
    lines = [
        WORKER_MARKER,
        f"Task mode: {brief.task_mode}",
        f"Target: {core.kind} {core.name} ({_short(core.target)}), project revision {core.project_revision}",
        "Exact statement:",
        core.statement or "(no statement recorded)",
    ]
    if core.context_text:
        lines += ["Mathematical context (exact ledger records, JSON):", core.context_text]
    if core.dependencies:
        lines.append("Exact dependencies: " + ", ".join(_short(ref) for ref in core.dependencies))
    if core.verified_dependencies:
        lines.append("Verified dependencies: " + ", ".join(_short(ref) for ref in core.verified_dependencies))
    if core.blockers:
        lines.append("Open work blocking dependencies: " + ", ".join(_short(ref) for ref in core.blockers))
    if core.trusted_assumptions:
        lines.append("Trusted background: " + ", ".join(_short(ref) for ref in core.trusted_assumptions))
    lines.append(f"Trust scope: {_short(core.scope)}")
    if working is not None:
        lines += ["", "Structural map (navigation, not evidence):", working.structural_map]
        preload = working.rendered(without=core.target)
        if preload:
            lines += ["", "Preloaded material (exact ledger records; trust is what the ledger records):", preload]
        if working.overflow:
            lines.append("Note: the mandatory kernel exceeded the preload budget; nothing optional was preloaded.")
    lines.append("")
    lines.append("Research brief (search variation only; the statement above is fixed):")
    lines.append(f"- framing: {brief.framing or 'none'}")
    lines.append(f"- reasoning direction: {brief.reasoning_direction}")
    lines.append(f"- information exposure: {brief.independence}")
    if brief.retrieval_intent:
        lines.append(f"- literature retrieval intent: {brief.retrieval_intent}")
    for label, values in (("preferred representations", brief.preferred_representations),
                          ("required methods", brief.required_methods),
                          ("discouraged methods", brief.discouraged_methods),
                          ("forbidden methods", brief.forbidden_methods)):
        if values:
            lines.append(f"- {label}: {', '.join(values)}")
    return "\n".join(lines)
