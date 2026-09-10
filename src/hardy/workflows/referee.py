"""Audit manuscript-linked project claims, with exact coverage and trust limits.

Theory: the lexical inventory locates source; explicit semantic readings bind it
to project mathematics, and policy alone authenticates proof or citation evidence.
Reused: A5 inventory, D2 critique, B graph/context/trust views and C2 resolver values.
This owner selects main dependency paths and exposes omitted or unresolved work;
it does not infer semantics from TeX, repair claims, or approve external contracts.
New semantic readers can supply the same exact spans/context observations. Their
completeness is unverified, as are unsupported scanner constructs. Cost is bounded
by selected claims/citations and repeated existing graph/store traversals.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from hardy.foundation.values import json_digest
from hardy.literature.manuscript import Inventory, SourceSpan, inventory
from hardy.workflows.acquisition.contracts import ResolverResult
from hardy.workflows.citation_audit import (
    CitationAudit,
    CitationExpansion,
    CitationUse,
    RecursiveCitationAudit,
    audit_recursive,
)
from hardy.workflows.critique import (
    LAYERS,
    CritiqueFinding,
    CritiqueOperations,
    CritiqueRequest,
    CritiqueResult,
    CritiqueWorkflow,
    ReviewInput,
    ReviewPass,
)
from hardy.workflows.formalization import SemanticRequirement
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    MathematicalContext,
    Obligation,
    ProjectItem,
    Relation,
    Scope,
    ScopedBinding,
    VersionRef,
)
from hardy.workflows.ledger.graph import TRANSPORT, LedgerGraph
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import ContextView, LedgerViews, TrustView

_CLAIM_KINDS = {"theorem", "lemma", "proposition", "corollary", "claim", "conjecture", "definition"}


@dataclass(frozen=True)
class ManuscriptClaim:
    """A source-bound semantic reading, never a faithfulness certificate."""

    environment: SourceSpan
    statement: SourceSpan
    item: VersionRef
    proof_context: VersionRef | None = None
    observed_bindings: tuple[VersionRef, ...] = ()
    required_representations: tuple[VersionRef, ...] = ()
    requirements: tuple[SemanticRequirement, ...] = ()


@dataclass(frozen=True)
class RefereeRequest:
    sources: Mapping[str, str]
    scope: VersionRef
    main_results: tuple[VersionRef, ...]
    claims: tuple[ManuscriptClaim, ...]
    citations: tuple[CitationUse, ...] = ()
    citation_depth: int = 0
    citation_max_nodes: int = 32


@dataclass(frozen=True)
class RefereeReport:
    inventory: Inventory
    scope: VersionRef
    main_results: tuple[VersionRef, ...]
    critical_path: tuple[VersionRef, ...]
    unreviewed_dependencies: tuple[VersionRef, ...]
    selected: tuple[VersionRef, ...]
    verified: tuple[VersionRef, ...]
    probed: tuple[VersionRef, ...]
    unresolved_claims: tuple[VersionRef, ...]
    unmapped_claims: tuple[SourceSpan, ...]
    unselected_claims: tuple[VersionRef, ...]
    unmapped_citations: tuple[SourceSpan, ...]
    contexts: tuple[tuple[VersionRef, ContextView], ...]
    critiques: tuple[CritiqueResult, ...]
    structural_findings: tuple[tuple[VersionRef, tuple[CritiqueFinding, ...]], ...]
    citations: tuple[CitationAudit, ...]
    trust: tuple[tuple[VersionRef, TrustView], ...]

    recursive_citations: tuple[RecursiveCitationAudit, ...] = ()
    citation_depth: int = 0

    @property
    def recursive_coverage_complete(self) -> bool:
        """Whether pinned-source traversal completed; never certifies whole-paper semantics."""
        return self.citation_depth > 0 and not self.unmapped_citations and not self.inventory.unsupported and all(
            node.status == "expanded" and not node.unmapped and not node.limitations
            and all(child.checked for child in node.children)
            for node in self.recursive_citations) and all(c.checked for c in self.citations)

    @property
    def summary(self) -> str:
        contracts = tuple(c for audit in self.citations for c in audit.contracts)
        exact = ", ".join(f"{c.id}@{c.digest}" for c in contracts) or "none checked"
        return (f"{len(self.selected)} selected claims: {len(self.verified)} verified, "
                f"{len(self.probed)} probed, {len(self.unresolved_claims)} unresolved. "
                f"Exact external contracts (acceptance reported separately): {exact}. "
                f"Unmapped claim blocks: {len(self.unmapped_claims)}; "
                f"unselected claims: {len(self.unselected_claims)}; "
                f"unreviewed dependencies: {len(self.unreviewed_dependencies)}; "
                f"unmapped citations: {len(self.unmapped_citations)}; "
                f"recursive citation depth: {self.citation_depth}, coverage complete: {self.recursive_coverage_complete}; "
                f"structural findings: {sum(len(findings) for _, findings in self.structural_findings)}; "
                f"lexical limitations: {len(self.inventory.unsupported)}. "
                "Recursive coverage covers only C2 pinned source files. "
                "Semantic reading completeness is unverified; review layers are listed per claim.")


class RefereeWorkflow:
    def __init__(self, store: LedgerStore, *, critique: CritiqueOperations,
                 resolve_citation: Callable[[LedgerSnapshot, Obligation], ResolverResult] | None = None,
                 policy: LedgerPolicy | None = None,
                 expand_citation: Callable[[LedgerSnapshot, CitationContract, Scope], CitationExpansion] | None = None):
        self.store = store
        self.critique = critique
        self.resolve_citation = resolve_citation
        self.expand_citation = expand_citation
        self.policy = policy or LedgerPolicy()

    @staticmethod
    def _current(snapshot: LedgerSnapshot, ref: VersionRef, kind):
        record = snapshot.get(ref)
        if not isinstance(record, kind) or snapshot.head(ref.id).ref != ref:
            raise ValueError("stale or incorrectly typed referee reference")
        return record

    def _validate(self, request: RefereeRequest, scanned: Inventory,
                  snapshot: LedgerSnapshot) -> Scope:
        if type(request.citation_depth) is not int or request.citation_depth not in {0, 1, 2}:
            raise ValueError("citation_depth must be 0, 1 or 2")
        if type(request.citation_max_nodes) is not int or request.citation_max_nodes < 1:
            raise ValueError("citation_max_nodes must be a positive integer")
        scope = self._current(snapshot, request.scope, Scope)
        environments = {env.opening: env for env in scanned.environments if env.kind != "proof"}
        if len({c.environment for c in request.claims}) != len(request.claims):
            raise ValueError("duplicate manuscript claim binding")
        if len({c.item for c in request.claims}) != len(request.claims):
            raise ValueError("one project claim cannot hide multiple manuscript statements")
        mapped = {c.item for c in request.claims}
        if not request.main_results or not set(request.main_results) <= mapped:
            raise ValueError("main results require inventoried claim bindings")
        if not set(request.main_results) <= set(scope.must_prove):
            raise ValueError("main manuscript results must be protected by must_prove scope")
        graph = LedgerGraph(snapshot)
        for claim in request.claims:
            item = self._current(snapshot, claim.item, ProjectItem)
            if item.kind not in _CLAIM_KINDS:
                raise ValueError("manuscript binding requires a claim, conjecture or definition")
            env = environments.get(claim.environment)
            span = claim.statement
            text = request.sources.get(span.path, "")
            if (env is None or env.closing is None or span.path != env.opening.path
                    or not span.valid_for(span.path, text)
                    or not env.opening.end <= span.start < span.end <= env.closing.start
                    or text[span.start:span.end] != item.statement):
                raise ValueError("manuscript statement span differs from exact project statement")
            if claim.proof_context is not None:
                self._current(snapshot, claim.proof_context, MathematicalContext)
            for ref in claim.observed_bindings:
                self._current(snapshot, ref, ScopedBinding)
            for ref in claim.required_representations:
                representation = self._current(snapshot, ref, ProjectItem)
                if representation.kind != "representation":
                    raise ValueError("required representation reference has wrong kind")
        spans = {citation.key_span for citation in scanned.citations}
        if len({c.span for c in request.citations}) != len(request.citations):
            raise ValueError("duplicate manuscript citation binding")
        for use in request.citations:
            required = self._current(snapshot, use.required_claim, ProjectItem)
            if (use.span not in spans or use.use_site not in mapped
                    or use.required_claim not in graph.dependency_closure(use.use_site)
                    or required.kind != "external_result"):
                raise ValueError("citation use must bind an inventoried use to its external dependency")
        return scope

    def _structural_findings(self, snapshot: LedgerSnapshot, claim: ManuscriptClaim,
                             scope: Scope) -> tuple[CritiqueFinding, ...]:
        item = snapshot.get(claim.item)
        graph = LedgerGraph(snapshot)
        findings = [CritiqueFinding(kind=r.kind, reason=r.reason) for r in claim.requirements]
        if claim.proof_context is not None and claim.proof_context != item.context:
            findings.append(CritiqueFinding(kind="resolve_declaration",
                reason="Proof context differs from the statement context; discharge hidden or changed hypotheses explicitly."))
        active = graph.active_context(item.context) if item.context else None
        declared = {binding.symbol: binding for binding in active.bindings} if active else {}
        for ref in claim.observed_bindings:
            binding = snapshot.get(ref)
            if declared.get(binding.symbol) != binding:
                findings.append(CritiqueFinding(kind="resolve_declaration",
                    reason=f"Proof notation {binding.symbol!r} differs from the statement's exact binding."))
        closure = set(graph.dependency_closure(item.ref, include_roots=True))
        for ref in claim.required_representations:
            if ref not in closure:
                findings.append(CritiqueFinding(kind="refine_representation",
                    reason=f"Later proof requires stronger or different representation {ref.id}@{ref.digest}; no explicit dependency records that interpretation."))
        context_refs = {c.ref for c in graph.context_chain(item.context)} if item.context else set()
        if claim.proof_context:
            context_refs.update(c.ref for c in graph.context_chain(claim.proof_context))
        for relation in graph.relations:
            if (relation.kind in TRANSPORT and relation.source in closure | context_refs
                    and not self.policy.transport_accepted(snapshot, relation, scope=scope)):
                findings.append(CritiqueFinding(kind="justify_transport",
                    reason=f"Unjustified transport {relation.id}@{relation.digest}; preserve exact endpoints and hypotheses."))
        return tuple(findings)

    def _citation(self, use: CitationUse, scope: Scope) -> VersionRef:
        snapshot = self.store.read()
        item = self._current(snapshot, use.required_claim, ProjectItem)
        identity = "referee:citation:" + json_digest({
            "use": use.use_site.model_dump(), "required": use.required_claim.model_dump(),
            "source": (use.span.path, use.span.digest, use.span.start, use.span.end),
            "scope": scope.ref.model_dump()})
        existing = {o.id: o for o in snapshot.current(Obligation)}.get(identity)
        work = Obligation(id=identity, item=item.ref, context=item.context, scope=scope,
                          kind="check_citation", reason="Audit exact manuscript citation use.")
        if existing is not None:
            if (existing.item, existing.context, existing.scope, existing.kind) != (
                work.item, work.context, work.scope, work.kind
            ):
                raise ValueError("citation obligation identity refers to different work")
            work = existing
        # The digest identifies a use but cannot reconstruct it. Persist its
        # exact manuscript owner and source span for later version auditing.
        use_link = Relation(id=identity + ":use", kind="cites", source=use.use_site,
            target=work.ref, artifacts=(ArtifactRef(uri="manuscript:" + use.span.path,
                digest=use.span.digest, locator=f"{use.span.start}:{use.span.end}"),))
        old_link = next((r for r in snapshot.current(Relation) if r.id == use_link.id), None)
        additions = [] if existing else [work]
        if old_link is None:
            additions.append(use_link)
        elif (old_link.kind, old_link.source, old_link.target.id, old_link.artifacts) != (
                use_link.kind, use_link.source, work.id, use_link.artifacts):
            raise ValueError("citation use association changed identity")
        if additions:
            snapshot = self.store.append(additions, expected_revision=snapshot.revision, validate=self.policy.validate)
        if self.resolve_citation is None or self._accepted(snapshot, work):
            return work.ref
        result = self.resolve_citation(snapshot, work)
        if not isinstance(result, ResolverResult):
            raise ValueError("citation owner must return a ResolverResult")
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale citation callback: ledger changed during review")
        additions = []
        heads = {record.id: record for record in snapshot.records}
        for record in (*result.records, *result.children):
            if isinstance(record, Obligation):
                if (record.item, record.scope, record.context, record.status, record.resolution) != (
                    work.item, scope, work.context, "open", None
                ):
                    raise ValueError("citation candidate cannot certify or change its subject")
            elif isinstance(record, CitationContract):
                if record.required_claim != work.item or record.use_site != work.item or record.status != "open":
                    raise ValueError("citation contract refers to another use or grants authority")
            elif isinstance(record, ProjectItem):
                if record.kind not in {"research_note", "external_result"}:
                    raise ValueError("citation owner may only propose source and reading records")
            elif isinstance(record, Relation):
                if record.kind != "cites" or record.source != work.item:
                    raise ValueError("citation owner may only link its exact source use")
            else:
                raise ValueError("citation candidate cannot write scope or authority records")
            if record.id in heads:
                if heads[record.id] != record:
                    raise ValueError("citation candidate cannot overwrite existing project state")
            else:
                additions.append(record)
                heads[record.id] = record
        # Record which candidates and child work belong to this exact use. A
        # shared external statement alone cannot attribute a prior paper audit.
        for record in (*result.records, *result.children):
            if not isinstance(record, (CitationContract, Obligation)):
                continue
            link = Relation(id="referee:citation-link:" + json_digest(
                (work.ref.model_dump(), record.ref.model_dump())),
                kind="cites" if isinstance(record, CitationContract) else "blocked_by",
                source=work.ref, target=record.ref)
            if link.id not in heads:
                additions.append(link)
                heads[link.id] = link
            elif heads[link.id] != link:
                raise ValueError("citation candidate association changed identity")
        if additions:
            self.store.append(additions, expected_revision=snapshot.revision, validate=self.policy.validate)
        return work.ref

    def _accepted(self, snapshot: LedgerSnapshot, obligation: Obligation) -> bool:
        return (obligation.status == "resolved" and obligation.resolution is not None
                and self.policy.is_accepted(snapshot, obligation.resolution))

    def _citation_audit(self, snapshot: LedgerSnapshot, use: CitationUse,
                        reference: VersionRef) -> CitationAudit:
        original = snapshot.get(reference)
        work = snapshot.head(reference.id)
        if not isinstance(work, Obligation) or (work.item, work.scope, work.kind, work.context) != (
            original.item, original.scope, original.kind, original.context
        ):
            raise ValueError("citation audit work changed identity")
        history = {record.ref for record in snapshot.records if isinstance(record, Obligation)
                   and record.id == work.id and (record.item, record.scope, record.kind, record.context)
                   == (work.item, work.scope, work.kind, work.context)}
        graph = LedgerGraph(snapshot)
        contracts = tuple(dict.fromkeys(snapshot.get(relation.target) for relation in graph.relations
            if relation.kind == "cites" and relation.source in history
            and isinstance(snapshot.get(relation.target), CitationContract)))
        accepted = self._accepted(snapshot, work)
        if accepted:
            contracts = tuple(contract for contract in contracts if contract.evidence
                              and set(contract.evidence) <= set(work.resolution.evidence))
        closure = graph.dependency_closure(history, include_roots=True)
        pending = tuple(o.ref for o in snapshot.current(Obligation) if o.scope == work.scope
                        and any(ref.id == o.id for ref in closure) and not self._accepted(snapshot, o))
        return CitationAudit(use, work.ref, contracts, accepted and bool(contracts) and not pending, pending)

    def run(self, request: RefereeRequest) -> RefereeReport:
        request = replace(request, sources=dict(request.sources))
        scanned = inventory(request.sources)
        snapshot = self.store.read()
        scope = self._validate(request, scanned, snapshot)
        path = LedgerGraph(snapshot).dependency_closure(request.main_results, include_roots=True)
        closure = set(path)
        selected = tuple(c.item for c in request.claims if c.item in closure)
        uses = tuple(use for use in request.citations if use.use_site in selected)
        citation_work = tuple(self._citation(use, scope) for use in uses)
        recursive = audit_recursive(store=self.store, policy=self.policy, scope=scope,
            roots=tuple(self._citation_audit(self.store.read(), use, work)
                        for use, work in zip(uses, citation_work, strict=True)),
            depth=request.citation_depth, max_nodes=request.citation_max_nodes,
            expand=self.expand_citation, check=self._citation, audit=self._citation_audit) if request.citation_depth else ()
        critiques = []
        structural_findings = []
        for claim in request.claims:
            if claim.item not in selected:
                continue
            before = self.store.read()
            self._validate(request, scanned, before)
            structural = self._structural_findings(before, claim, scope)
            if structural:
                structural_findings.append((claim.item, structural))
            provider_findings = []

            def adversarial(review_input: ReviewInput, structural=structural,
                            revision=before.revision, observed=provider_findings) -> ReviewPass:
                if review_input.snapshot.revision != revision:
                    raise ValueError("stale structural review: ledger changed during semantic checks")
                result = (self.critique.adversarial(review_input) if self.critique.adversarial else
                          ReviewPass(subject=review_input.subject.ref, scope=review_input.scope.ref,
                                     revision=review_input.snapshot.revision))
                if not isinstance(result, ReviewPass):
                    raise ValueError("adversarial operation must return a ReviewPass")
                observed.extend(result.findings)
                return result.model_copy(update={"findings": (*result.findings, *structural)})

            operations = replace(self.critique, adversarial=adversarial) if structural else self.critique
            result = CritiqueWorkflow(self.store, operations=operations, policy=self.policy).run(
                CritiqueRequest(subject=claim.item, scope=scope.ref,
                                requirements=claim.requirements if before.get(claim.item).context else ()))
            if structural:
                # D2 persists the deterministic observations as ordinary gaps;
                # its adapter is not an adversarial mathematical review provider.
                # Attribute that coverage separately even when a provider also ran.
                layers = tuple(layer for layer in result.layers_run
                               if layer != "adversarial" or self.critique.adversarial is not None)
                result = replace(result, layers_run=layers,
                    layers_skipped=tuple(layer for layer in LAYERS if layer not in layers),
                    findings=tuple((layer, finding) for layer, finding in result.findings
                                   if layer != "adversarial" or finding in provider_findings))
            critiques.append(result)
        snapshot = self.store.read()
        self._validate(request, scanned, snapshot)
        if LedgerGraph(snapshot).dependency_closure(request.main_results, include_roots=True) != path:
            raise ValueError("stale main dependency path changed during referee review")
        views = LedgerViews(snapshot, self.policy)
        verified = tuple(ref for ref in selected if self.policy.premise_allowed(
            snapshot, ref, scope=scope, context=snapshot.get(ref).context))
        pending = tuple(o for o in views.obligations() if o.scope == scope)
        citations = tuple(self._citation_audit(snapshot, use, reference)
                          for use, reference in zip(uses, citation_work, strict=True))
        # Capability receipts can be revoked without changing ledger revision.
        # Refresh recursive acceptance at the same final boundary as direct uses.
        recursive = tuple(replace(node, children=tuple(
            self._citation_audit(snapshot, child.use, child.obligation)
            for child in node.children)) for node in recursive)
        unresolved = tuple(ref for ref in selected if ref not in verified or
                           any(o.item in LedgerGraph(snapshot).dependency_closure(ref, include_roots=True) for o in pending))
        report = RefereeReport(
            inventory=scanned, scope=scope.ref, main_results=request.main_results,
            recursive_citations=recursive, citation_depth=request.citation_depth,
            critical_path=path,
            unreviewed_dependencies=tuple(ref for ref in path if ref not in selected
                                          and isinstance(snapshot.get(ref), ProjectItem)
                                          and snapshot.get(ref).kind in _CLAIM_KINDS),
            selected=selected, verified=verified,
            probed=tuple(c.subject for c in critiques if "formalization" in c.layers_run),
            unresolved_claims=unresolved,
            unmapped_claims=tuple(e.opening for e in scanned.environments if e.kind != "proof"
                  and e.opening not in {c.environment for c in request.claims}),
            unselected_claims=tuple(c.item for c in request.claims if c.item not in selected),
            unmapped_citations=tuple(c.key_span for c in scanned.citations if c.key_span not in {u.span for u in uses}),
            contexts=tuple((ref, views.context(snapshot.get(ref).context) if snapshot.get(ref).context else ContextView(None))
                           for ref in selected), critiques=tuple(critiques), citations=citations,
            structural_findings=tuple(structural_findings),
            trust=tuple((ref, views.trust_boundary(ref, scope)) for ref in selected),
        )
        if self.store.read().revision != snapshot.revision:
            raise ValueError("stale referee report: ledger changed during evidence authentication")
        return report
