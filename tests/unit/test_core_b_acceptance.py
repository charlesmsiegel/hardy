"""Synthetic project acceptance; capability stand-ins are not real Lean evidence."""
from dataclasses import replace

from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    EvidenceRef,
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Resolution,
    Scope,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.ledger.views import LedgerViews


def test_persistent_project_retains_context_representations_research_and_transport(tmp_path):
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="ambient")
    setup = manager.extend(root.ref, id="setup", label="Let X be a smooth manifold", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),
    ))
    x = store.read().head("X")
    base = manager.extend(setup.ref, id="base", label="Write M for X", bindings=(
        BindingSpec(id="M", kind="alias", symbol="M", meaning="X", target=x.ref),
    ))
    compact = manager.extend(base.ref, id="compact", label="Suppose X is compact", declarations=(
        DeclarationSpec(id="compactness", symbol="h", semantic_type="X compact",
                        role="local_hypothesis", dependencies=(x.ref,)),
    ))
    concept = ProjectItem(id="manifold", kind="concept", name="Smooth manifold", origin="human_authored")
    reps = tuple(ProjectItem(id=name, kind="representation", name=name, origin="generated_local")
                 for name in ("charts", "atlas"))
    conjecture = ProjectItem(id="conjecture", kind="conjecture", name="Irreducibility",
        statement="Z is irreducible", origin="human_authored", context=base.ref)
    goal = ProjectItem(id="goal", kind="goal", name="Prove or refute", statement=conjecture.statement,
                       origin="human_authored", context=base.ref)
    approaches = tuple(ProjectItem(id=name, kind="approach", name=name, origin="generated_local",
        research=ResearchState(status=status, reason=reason)) for name, status, reason in (
            ("degeneration", "blocked", "Loses polarization data"),
            ("generic-fiber", "promising", "Retains the relevant structure")))
    scope = Scope(id="scope", must_prove=(conjecture.ref,))
    edges = (
        *(Relation(id=f"{r.id}-interprets", kind="interprets", source=r.ref, target=concept.ref) for r in reps),
        Relation(id="target", kind="targets", source=goal.ref, target=conjecture.ref),
        *(Relation(id=f"{a.id}-pursues", kind="pursues", source=a.ref, target=goal.ref) for a in approaches),
    )
    store.append((concept, *reps, conjecture, goal, *approaches, scope, *edges),
                 expected_revision=store.read().revision)
    transport = manager.transport(base.ref, id="normal", label="WLOG put p at [1:0:0]",
                                  subject=goal.ref, scope=scope.ref, mappings=(("p", "[1:0:0]"),))
    manager.activate(base.ref)
    restarted = LedgerStore(tmp_path).read()
    views = LedgerViews(restarted)
    assert restarted.active_context == base.ref
    assert restarted.get(compact.ref).parent == base.ref
    assert views.context(compact.ref).local_hypotheses[0].id == "compactness"
    assert views.context(base.ref).local_hypotheses == ()
    assert views.context().bindings[0].target == x.ref
    assert len(views.concepts()[0].representations) == 2
    assert views.research().failed_approaches == (approaches[0],)
    assert conjecture in views.research().open_questions
    assert LedgerGraph(restarted).approaches(goal.ref) == tuple(sorted(approaches, key=lambda a: a.id))
    assert transport.outstanding[0] in views.obligations()
    assert not views.policy.premise_allowed(restarted, conjecture.ref, scope=scope, context=base.ref)
    assert LedgerGraph(restarted).transport_paths(base.ref, transport.context.ref) == ()


def test_authenticated_contextual_result_and_publication_survive_restart_without_trust_widening(tmp_path):
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="ambient")
    context = manager.extend(root.ref, id="setup", label="Fix X", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="space"),
    ))
    x = store.read().head("X")
    theorem = ProjectItem(id="T", kind="theorem", name="T", statement="An exact conditional statement",
                          context=context.ref, origin="human_authored", publication_visibility="public")
    scope = Scope(id="scope", must_prove=(theorem.ref,))
    work = Obligation(id="proof", item=theorem.ref, kind="prove", scope=scope, context=context.ref)
    dependency = Relation(id="uses-X", kind="depends_on", source=theorem.ref, target=x.ref)
    store.append((theorem, scope, work, dependency), expected_revision=store.read().revision)
    evidence = EvidenceRef(kind="formal", subject=theorem.ref, producer="test-only-kernel-stand-in",
                           artifact=ArtifactRef(uri="test-only-proof.json", digest="a" * 64))
    proposal = Resolution(id="resolution", obligation=work.ref, item=theorem.ref, evidence=(evidence,))
    receipt = ArtifactRef(uri="test-only-decision.json", digest="b" * 64)
    authenticated = {evidence.artifact: AuthenticatedEvidence(
        reference=evidence, scope=scope.ref, context=context.ref, outcome="kernel_proof")}
    decisions = {}
    policy = LedgerPolicy(read_evidence=lambda e: authenticated.get(e.artifact), read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, theorem.ref, scope.ref, context.ref, policy.digest)
    accepted = policy.accept(store.read(), proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                       "status": "resolved", "resolution": accepted})
    store.append((closed,), expected_revision=store.read().revision, validate=policy.validate)
    restarted = LedgerStore(tmp_path).read()
    views = LedgerViews(restarted, policy)
    boundary = views.trust_boundary(theorem.ref, scope)
    assert boundary.authenticated and boundary.external_assumptions == ()
    assert boundary.local_context.parameters == (x,)
    publication = views.publication(theorem.ref, scope)
    assert publication.ready
    assert publication.required_declarations == (x,)
    # A citation is relevant through its contract, even without a graph edge or
    # a separately scheduled CHECK_CITATION obligation.
    citation = CitationContract(id="citation", use_site=theorem.ref, required_claim=theorem.ref,
        paper_id="paper", paper_version="v1", conclusion="T",
        source_statement=ArtifactRef(uri="unread-paper", digest="c" * 64))
    store.append((citation,), expected_revision=restarted.revision)
    with_citation = LedgerViews(store.read(), policy)
    assert citation.ref in with_citation.coverage().citations_open
    assert with_citation.publication(theorem.ref, scope).citations_open == (citation.ref,)
    assert not with_citation.publication(theorem.ref, scope).ready
    assert not policy.premise_allowed(restarted, theorem.ref, scope=scope, context=root.ref)
    assert not LedgerViews(restarted).publication(theorem.ref, scope).ready
    # Authentication is checked again on use, not remembered as a true flag.
    authenticated[evidence.artifact] = replace(authenticated[evidence.artifact], outcome="elaborated")
    assert not views.publication(theorem.ref, scope).ready
