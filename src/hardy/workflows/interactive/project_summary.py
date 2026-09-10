"""Render one project snapshot using the ledger's shared semantic views.

The session record and mathematical ledger have separate revisions. This report
names its ledger revision and never infers proof from stored status. Future
capability readers enter through LedgerPolicy; rendering cannot grant authority.
All sections share one immutable snapshot, including trust and publication gaps.
"""
from dataclasses import replace

from hardy.workflows.interactive.summary import Section, Summary
from hardy.workflows.ledger.contracts import ProjectItem, Scope, VersionRef
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.views import LedgerViews
from hardy.workflows.publication import PublicationRequest, plan_publication


def _ref(ref: VersionRef) -> str:
    return f"{ref.id}@{ref.digest}"


def with_project(base: Summary, snapshot: LedgerSnapshot, *,
                 policy: LedgerPolicy | None = None) -> Summary:
    if not snapshot.records:
        return base
    views = LedgerViews(snapshot, policy)
    context, research, coverage = views.context(), views.research(), views.coverage()
    pending = views.obligations()
    work = tuple(f"{o.kind.value}: {_ref(o.ref)} for {_ref(o.item)}; {o.reason or o.status.value}"
                 for o in pending)
    sections = [Section("Mathematical project", (
        f"Ledger revision: {snapshot.revision}",
        "Evidence authentication unavailable; recorded acceptance is not proof."
        if policy is None else "Evidence authenticated only through the configured ledger policy.",
    ))]
    sections.append(Section("Active mathematical context", (
        f"{snapshot.get(context.context).label}: {_ref(context.context)}",
    ) if context.context else (), empty="not set"))
    sections.append(Section("Declarations", tuple(
        f"{d.name}: {d.declaration.role.value}; {d.declaration.semantic_type}; {_ref(d.ref)}"
        for d in context.parameters), empty="none"))
    sections.append(Section("Local hypotheses", tuple(
        f"{d.name}: {d.declaration.semantic_type}; {_ref(d.ref)}" for d in context.local_hypotheses),
        empty="none; these are distinct from external assumptions"))
    sections.append(Section("Scoped notation and conventions", tuple(
        f"{b.symbol} = {b.meaning}; {b.kind.value}; {_ref(b.ref)}" for b in context.bindings), empty="none"))
    scopes = snapshot.current(Scope)
    sections.append(Section("Open research questions, conjectures and goals", tuple(
        f"{i.kind.value}: {i.name}; {i.statement or 'no statement'}; {_ref(i.ref)}; "
        f"scope={_ref(scope.ref) if scope else 'not selected'}"
        for scope in (scopes or (None,)) for i in views.research(scope).open_questions),
        empty="none in the recorded scopes"))
    sections.append(Section("Mathematical approaches and dead ends", tuple(
        f"{i.name}: {i.research.status if i.research else 'unassessed'}; "
        f"{i.research.reason or 'no reason recorded' if i.research else 'no assessment'}; {_ref(i.ref)}"
        for i in dict.fromkeys((*research.approaches, *research.failed_approaches))), empty="none"))
    sections.append(Section("Concepts and known representations", tuple(
        f"{c.concept.name}: {_ref(c.concept.ref)}; representations: "
        + (", ".join(f"{r.name} ({_ref(r.ref)})" for r in c.representations) or "unresolved")
        for c in views.concepts()), empty="none"))
    sections.append(Section("Project blockers and unresolved choices", work, empty="none recorded"))
    sections.append(Section("Project items (recorded, not certificates)", tuple(
        f"{i.kind.value}: {i.name}; {_ref(i.ref)}; visibility={i.publication_visibility.value}"
        for i in snapshot.current(ProjectItem))))
    trust, readiness = [], []
    for scope in scopes:
        trust.extend((f"Scope: {_ref(scope.ref)}",
            "Must prove: " + (", ".join(map(_ref, scope.must_prove)) or "none"),
            "Allowed background (permission only): " + (", ".join(map(_ref, scope.allowed_background)) or "none"),
            "Allowed interfaces (permission only): " + (", ".join(map(_ref, scope.allowed_interfaces)) or "none")))
        for root in scope.must_prove:
            if not isinstance(snapshot.get(root), ProjectItem):
                trust.append(f"{_ref(root)}: unsupported target kind; used trust unavailable")
                readiness.append(f"{_ref(root)}: unavailable; target is not a project item")
                continue
            boundary = views.trust_boundary(root, scope)
            trust.append(f"{_ref(root)}: actual used external trust " + (
                (", ".join(map(_ref, boundary.external_assumptions)) or "none")
                if boundary.authenticated else "unestablished"))
            try:
                plan = plan_publication(snapshot, PublicationRequest(roots=(root,), scope=scope.ref), policy=policy)
            except ValueError as error:
                readiness.append(f"{_ref(root)} in {_ref(scope.ref)}: unavailable; {error}")
                continue
            readiness.append(f"{_ref(root)} in {_ref(scope.ref)}: " + ("ready" if plan.ready else
                f"blocked; {len(plan.unestablished)} unestablished, {len(plan.obligations)} obligations, "
                f"{len(plan.citations_open)} open citations, {len(plan.stale_exposition)} stale prose, "
                f"{len(plan.missing_exposition)} missing prose"))
    sections.append(Section("External trust boundary and research targets", tuple(trust), empty="no scope recorded"))
    sections.append(Section("Citation status", tuple(
        [f"checked: {_ref(r)}" for r in coverage.citations_checked]
        + [f"open: {_ref(r)}" for r in coverage.citations_open]), empty="none recorded"))
    sections.append(Section("Stale exposition and artifacts", tuple(
        f"Historical link: {_ref(s.record)} documents {_ref(s.expected)}; current {_ref(s.current)}"
        for s in views.stale_artifacts()), empty="none"))
    sections.append(Section("Publication readiness for scope targets", tuple(readiness), empty="no target selected by a scope"))
    obligations = base.obligations + work
    original = tuple(replace(s, lines=obligations) if s.title == "Next steps" else s for s in base.sections)
    return replace(base, sections=original + tuple(sections), obligations=obligations)
