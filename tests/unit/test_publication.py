"""Publication selects a readable draft without erasing its actual prerequisites."""
import importlib

import pytest

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
from hardy.workflows.ledger.policy import AcceptanceDecision, AuthenticatedEvidence, LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore


@pytest.fixture
def publication():
    try:
        return importlib.import_module("hardy.workflows.publication")
    except ModuleNotFoundError:
        pytest.fail("D5 publication planner is not implemented")


def item(id, kind="theorem", **kwargs):
    return ProjectItem(id=id, name=id, kind=kind, origin="human_authored",
                       publication_visibility=kwargs.pop("publication_visibility", "public"), **kwargs)


def edge(id, source, target, kind="uses"):
    return Relation(id=id, source=source.ref, target=target.ref, kind=kind)


@pytest.fixture
def project(tmp_path):
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="root")
    c0 = manager.extend(root.ref, id="C0", label="arbitrary manifold", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="smooth manifold"),
        DeclarationSpec(id="unused", symbol="Y", semantic_type="unused manifold"),
    ))
    x = store.read().head("X")
    c1 = manager.extend(c0.ref, id="C1", label="compact case", declarations=(
        DeclarationSpec(id="compact", symbol="h", semantic_type="X compact",
                        role="local_hypothesis", dependencies=(x.ref,)),
    ), bindings=(BindingSpec(id="M", kind="alias", symbol="M", meaning="M := X", target=x.ref),))
    snapshot = store.read()
    main = item("Main", statement="M has the asserted property", context=c1.ref)
    lemma = item("Lemma", "lemma", statement="Supporting lemma", context=c0.ref)
    helper = item("Helper", "lemma", statement="Formal helper", publication_visibility="internal")
    external = item("External", "external_result", statement="Literature theorem")
    example = item("Example", "example", statement="A sphere illustrates Main", context=c1.ref)
    prose = item("Prose", "exposition", statement="The author's exact paragraph.")
    approach = item("Failed", "approach", research=ResearchState(status="failed", reason="lost polarization"))
    scope = Scope(id="scope", must_prove=(main.ref,))
    citation = CitationContract(id="Citation", use_site=main.ref, required_claim=external.ref,
        paper_id="paper", paper_version="v2", source_statement=ArtifactRef(uri="paper.txt", digest="a" * 64),
        conclusion="Literature theorem")
    records = (main, lemma, helper, external, example, prose, approach, scope, citation,
        edge("lemma", main, lemma), edge("helper", main, helper), edge("external", main, external),
        edge("hypothesis", main, snapshot.head("compact")), edge("notation", main, snapshot.head("M")),
        edge("illustrates", example, main, "illustrates"), edge("documents", prose, main, "documents"),
        edge("failed", approach, main, "pursues"))
    store.append(records, expected_revision=snapshot.revision)
    return store, main, scope


def make_plan(module, project, **kwargs):
    store, main, scope = project
    request = module.PublicationRequest(roots=(main.ref,), scope=scope.ref, **kwargs)
    return module.PublicationPlanner(store).plan(request)


def test_roadmap_fixture_selects_meaningful_material_and_minimal_context(publication, project):
    before = project[0].read()
    plan = make_plan(publication, project)
    assert {i.id for i in plan.items} == {"Main", "Lemma", "External", "Example"}
    assert {r.id for r in plan.closure} >= {"Helper", "X", "compact", "M"}
    context = next(c for c in plan.contexts if c.item == project[1].ref)
    assert [d.id for d in context.parameters] == ["X"]
    assert [d.id for d in context.local_hypotheses] == ["compact"]
    assert [b.id for b in context.bindings] == ["M"]
    assert [c.id for c in plan.citations] == ["Citation"]
    assert [p.prose.id for p in plan.exposition] == ["Prose"]
    assert project[1].ref not in plan.missing_exposition
    assert not plan.ready  # Recorded mathematics is not authenticated evidence.
    assert project[1].ref in plan.unestablished
    assert project[0].read() == before


def test_new_theorem_reports_old_prose_without_reusing_or_rewriting_it(publication, project):
    store, original, scope = project
    changed = original.model_copy(update={"statement": "A changed claim"})
    store.append((changed,), expected_revision=store.read().revision)
    plan = make_plan(publication, (store, changed, scope))
    assert not plan.exposition
    assert changed.ref in plan.missing_exposition
    assert len(plan.stale_exposition) == 1
    stale = plan.stale_exposition[0]
    assert stale.prose.statement == "The author's exact paragraph."
    assert stale.documented == original.ref and stale.target == changed.ref
    # Selecting history is intentional: the paragraph is current for that exact claim.
    historical = make_plan(publication, project)
    assert [p.prose.id for p in historical.exposition] == ["Prose"]
    assert not historical.stale_exposition


def test_visibility_does_not_prune_hidden_helpers_prerequisites(publication, project):
    store, main, scope = project
    dependency = item("Definition", "definition", statement="A necessary definition")
    store.append((dependency, edge("helper-definition", store.read().head("Helper"), dependency)),
                 expected_revision=store.read().revision)
    plan = make_plan(publication, project)
    assert "Definition" in {i.id for i in plan.items}
    assert "Helper" not in {i.id for i in plan.items}
    assert "Helper" in {i.id for i in make_plan(publication, project, include_internal=True).items}


def test_hidden_illustration_does_not_drag_unrelated_math_into_draft(publication, project):
    store, main, _ = project
    hidden = item("HiddenExample", "example", publication_visibility="internal")
    unrelated = item("Unrelated", "lemma", statement="Not needed by Main")
    store.append((hidden, unrelated, edge("hidden-example", hidden, main, "illustrates"),
                  edge("unrelated", hidden, unrelated)), expected_revision=store.read().revision)
    assert "Unrelated" not in {i.id for i in make_plan(publication, project).items}


def test_example_keeps_its_own_required_context_without_ambient_noise(publication, project):
    store, _, _ = project
    example = store.read().head("Example")
    store.append((edge("example-unused", example, store.read().head("unused")),),
                 expected_revision=store.read().revision)
    context = next(c for c in make_plan(publication, project).contexts if c.item == example.ref)
    assert [d.id for d in context.parameters] == ["unused"]
    assert not context.local_hypotheses and not context.bindings


def test_selection_rejects_empty_duplicate_and_nonitem_roots(publication, project):
    store, main, scope = project
    for roots in ((), (main.ref, main.ref)):
        with pytest.raises(ValueError):
            publication.PublicationRequest(roots=roots, scope=scope.ref)
    with pytest.raises(ValueError, match="project item"):
        publication.PublicationPlanner(store).plan(publication.PublicationRequest(roots=(scope.ref,), scope=scope.ref))


def test_explicit_omission_is_respected_even_when_internal_items_are_requested(publication, project):
    store, _, scope = project
    omitted = item("Omitted", statement="Never show", publication_visibility="omitted")
    store.append((omitted,), expected_revision=store.read().revision)
    with pytest.raises(ValueError, match="omitted"):
        make_plan(publication, (store, omitted, scope), include_internal=True)


def test_annotated_research_goal_does_not_become_publication_ready(publication, project):
    store, _, scope = project
    goal = item("Goal", "goal", statement="Prove the conjecture", research=ResearchState(status="done"))
    prose = item("GoalProse", "exposition", statement="A beautiful proof was found.")
    store.append((goal, prose, edge("goal-prose", prose, goal, "documents")), expected_revision=store.read().revision)
    assert not make_plan(publication, (store, goal, scope)).ready


def test_readiness_requires_authenticated_evidence_and_missing_prose_remains_a_gap(publication, tmp_path):
    store = LedgerStore(tmp_path)
    theorem = item("T", statement="True")
    scope = Scope(id="scope", must_prove=(theorem.ref,))
    work = Obligation(id="prove", kind="prove", item=theorem.ref, scope=scope)
    evidence = EvidenceRef(kind="formal", subject=theorem.ref, producer="test-formal-owner",
                           artifact=ArtifactRef(uri="test-proof.json", digest="a" * 64))
    proposal = Resolution(id="proof", obligation=work.ref, item=theorem.ref, evidence=(evidence,))
    receipt = ArtifactRef(uri="test-decision.json", digest="b" * 64)
    evidence_records = {evidence: AuthenticatedEvidence(evidence, scope.ref, None, "kernel_proof")}
    decisions = {}
    policy = LedgerPolicy(read_evidence=evidence_records.get, read_decision=decisions.get)
    decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, theorem.ref, scope.ref, None, policy.digest)
    snapshot = store.append((theorem, scope, work), expected_revision=0)
    accepted = policy.accept(snapshot, proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref, "status": "resolved", "resolution": accepted})
    store.append((closed,), expected_revision=1, validate=policy.validate)
    planner = publication.PublicationPlanner(store, policy=policy)
    request = publication.PublicationRequest(roots=(theorem.ref,), scope=scope.ref)
    missing = planner.plan(request)
    assert not missing.unestablished and not missing.obligations
    assert missing.missing_exposition == (theorem.ref,) and not missing.ready
    prose = item("P", "exposition", statement="The proof is immediate.")
    store.append((prose, edge("doc", prose, theorem, "documents")), expected_revision=2)
    complete = planner.plan(request)
    assert complete.ready
    assert publication.PublicationPlan.model_validate_json(complete.model_dump_json()) == complete
    evidence_records.clear()
    assert not planner.plan(request).ready
    assert complete.ready  # A frozen plan describes the evidence observed at its revision.


def test_shadowed_binding_does_not_replace_exact_parent_notation(publication, project):
    store, main, scope = project
    ContextManager(store).extend(main.context, id="Shadow", label="new notation",
        bindings=(BindingSpec(id="new-M", kind="alias", symbol="M", meaning="M := Y",
                             target=store.read().head("unused").ref),))
    plan = make_plan(publication, project)
    context = next(c for c in plan.contexts if c.item == main.ref)
    assert context.bindings[0].target == store.read().head("X").ref
    assert context.context == main.context
