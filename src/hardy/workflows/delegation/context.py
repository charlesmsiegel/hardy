"""Frozen problem core, per-worker research brief, and the recorded launch manifest.

Every worker on one target receives the same correctness-critical core: the
exact statement, its mathematical context, its trust scope and the exact
dependency revisions, pinned to one project revision and hashed so two workers
can be shown to have attacked the same target state. Diversity is the brief's
business and never mutates the core. Nothing here calls a provider.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from hardy.foundation.values import FrozenModel, json_digest
from hardy.workflows.context import ContextManager
from hardy.workflows.ledger.contracts import ProjectItem, Scope, VersionRef
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.store import LedgerStore

BUILDER = "hardy.delegation.context/v1"
WORKER_MARKER = "[Hardy delegation worker — written by Hardy, not the user]"


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
    project_revision: int
    task_mode: str

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
    model: str | None = None
    seed: int | None = None

    @property
    def digest(self) -> str:
        return json_digest({"schema": "hardy.delegation/ResearchBrief/v1", "value": self.model_dump(mode="json")})


class ContextManifest(FrozenModel):
    """What a worker was launched with; enough to reconstruct its initial view."""

    id: str
    problem_core_digest: str
    research_brief_digest: str
    project_revision: int
    included_refs: tuple[VersionRef, ...]
    hidden_selectors: tuple[str, ...] = ()
    project_retrieval: Literal["permitted", "denied"] = "permitted"
    literature_retrieval: Literal["permitted", "denied"] = "permitted"
    builder: str = Field(default=BUILDER)


def build_problem_core(store: LedgerStore, target: VersionRef, *, scope: VersionRef,
                       task_mode: str) -> ProblemCore:
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
    context_text = ContextManager(store).render(item.context) if item.context is not None else ""
    return ProblemCore(
        target=target, kind=item.kind.value, name=item.name, statement=item.statement,
        context=item.context, context_text=context_text, scope=scope,
        trusted_assumptions=policy.allowed_background,
        dependencies=graph.dependency_closure(target),
        project_revision=snapshot.revision, task_mode=task_mode,
    )


def _short(ref: VersionRef) -> str:
    return f"{ref.id}@{ref.digest[:12]}"


def render_launch_prompt(core: ProblemCore, brief: ResearchBrief) -> str:
    lines = [
        WORKER_MARKER,
        f"Task mode: {core.task_mode}",
        f"Target: {core.kind} {core.name} ({_short(core.target)}), project revision {core.project_revision}",
        "Exact statement:",
        core.statement or "(no statement recorded)",
    ]
    if core.context_text:
        lines += ["Mathematical context (exact ledger records, JSON):", core.context_text]
    if core.dependencies:
        lines.append("Exact dependencies: " + ", ".join(_short(ref) for ref in core.dependencies))
    if core.trusted_assumptions:
        lines.append("Trusted background: " + ", ".join(_short(ref) for ref in core.trusted_assumptions))
    lines.append(f"Trust scope: {_short(core.scope)}")
    lines.append("")
    lines.append("Research brief (search variation only; the statement above is fixed):")
    lines.append(f"- framing: {brief.framing or 'none'}")
    lines.append(f"- reasoning direction: {brief.reasoning_direction}")
    for label, values in (("preferred representations", brief.preferred_representations),
                          ("required methods", brief.required_methods),
                          ("discouraged methods", brief.discouraged_methods),
                          ("forbidden methods", brief.forbidden_methods)):
        if values:
            lines.append(f"- {label}: {', '.join(values)}")
    return "\n".join(lines)
