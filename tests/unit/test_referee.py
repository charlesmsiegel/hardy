"""Referee audits inventoried source and explicit semantic readings, without authority."""
from dataclasses import replace

import pytest

from hardy.literature.manuscript import SourceSpan, inventory
from hardy.workflows import referee
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.critique import CritiqueOperations
from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Relation, Scope
from hardy.workflows.ledger.store import LedgerStore


def manuscript(tmp_path):
    sources = {"main.tex": r"\begin{theorem}Every X is compact.\end{theorem}"
               r"\begin{proof}Use compactness of X and \cite{outside}.\end{proof}"
               r"\begin{lemma}An unreviewed lemma.\end{lemma}"}
    scanned = inventory(sources)
    store = LedgerStore(tmp_path)
    contexts = ContextManager(store)
    ambient = contexts.create_root(id="C0", label="An arbitrary object")
    context = contexts.extend(ambient.ref, id="C1", label="X arbitrary",
                             declarations=(DeclarationSpec(id="X", symbol="X", semantic_type="space"),),
                             bindings=(BindingSpec(id="M1", kind="notation", symbol="M", meaning="X"),))
    hidden = contexts.extend(context.ref, id="C2", label="Hidden compactness",
                             declarations=(DeclarationSpec(id="compact", symbol="h", semantic_type="X is compact",
                                                           role="local_hypothesis"),),
                             bindings=(BindingSpec(id="M2", kind="notation", symbol="M", meaning="a finite subset"),))
    item = ProjectItem(id="main", kind="theorem", name="Main", origin="target_paper",
                       statement="Every X is compact.", context=context.ref)
    external = ProjectItem(id="external", kind="external_result", name="External", origin="background_paper",
                           statement="Every closed subset of a compact space is compact.")
    strong = ProjectItem(id="smooth", kind="representation", name="Smooth space", origin="generated_local")
    scope = Scope(id="scope", must_prove=(item.ref,))
    store.append((item, external, strong, scope,
                  Relation(id="uses", kind="depends_on", source=item.ref, target=external.ref),
                  Relation(id="wlog", kind="transported_from", source=hidden.ref, target=context.ref,
                           mappings=(("X", "compact X"),))),
                 expected_revision=store.read().revision)
    opening = scanned.environments[0].opening
    statement = SourceSpan(opening.path, opening.digest, opening.end, opening.end + len(item.statement))
    claim = referee.ManuscriptClaim(environment=opening, statement=statement, item=item.ref,
                                   proof_context=hidden.ref, observed_bindings=hidden.bindings,
                                   required_representations=(strong.ref,))
    citation = referee.CitationUse(span=scanned.citations[0].key_span, use_site=item.ref,
                                   required_claim=external.ref)
    request = referee.RefereeRequest(sources=sources, scope=scope.ref, main_results=(item.ref,),
                                    claims=(claim,), citations=(citation,))
    return store, request, item, external


def test_actual_inventory_drives_hidden_hypothesis_notation_representation_and_citation_gaps(tmp_path):
    store, request, item, external = manuscript(tmp_path)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations()).run(request)
    assert report.inventory == inventory(request.sources)
    assert report.selected == (item.ref,)
    assert len(report.unmapped_claims) == 1
    assert not report.verified
    assert not report.probed
    assert report.unresolved_claims == (item.ref,)
    assert report.citations[0].use.required_claim == external.ref
    assert not report.citations[0].contracts
    assert not report.citations[0].checked
    obligations = store.read().current(Obligation)
    assert {o.kind for o in obligations} >= {
        "resolve_declaration", "refine_representation", "check_citation", "justify_transport"}
    reasons = " ".join(o.reason for o in obligations)
    assert "context" in reasons and "notation" in reasons
    assert report.trust[0][0] == item.ref
    assert report.structural_findings
    assert report.critiques[0].layers_run == ()
    assert "adversarial" in report.critiques[0].layers_skipped
    assert not report.critiques[0].findings
    assert "paper correct" not in report.summary.lower()
    assert "0 verified" in report.summary and "unresolved" in report.summary


@pytest.mark.parametrize("change", ["source", "statement", "span", "main_scope"])
def test_referee_refuses_stale_or_misbound_manuscript_inputs(tmp_path, change):
    store, request, item, _ = manuscript(tmp_path)
    if change == "source":
        request = replace(request, sources={"main.tex": request.sources["main.tex"] + "changed"})
    elif change == "statement":
        revised = item.model_copy(update={"statement": "A weaker statement."})
        store.append((revised,), expected_revision=store.read().revision)
    elif change == "span":
        request = replace(request, claims=(replace(request.claims[0], statement=request.claims[0].environment),))
    else:
        scope = Scope(id="scope2")
        store.append((scope,), expected_revision=store.read().revision)
        request = replace(request, scope=scope.ref)
    with pytest.raises(ValueError):
        referee.RefereeWorkflow(store, critique=CritiqueOperations()).run(request)
    assert not store.read().current(Obligation)


def test_citation_owner_composition_preserves_exact_contract_and_unresolved_hypotheses(tmp_path):
    from test_acquisition_literature import fixture, resolver

    from hardy.workflows.acquisition.contracts import ClassifiedGap
    from hardy.workflows.representation import RepresentationModel

    store, request, _, _ = manuscript(tmp_path / "project")
    library, _, _, _ = fixture(tmp_path / "external")
    literature, _ = resolver(tmp_path / "external", library)

    def resolve_citation(snapshot, obligation):
        return literature.resolve(snapshot, obligation, ClassifiedGap(
            obligation=obligation, kind="literature", reason="Manuscript citation",
            searches=(), model=RepresentationModel(provider="fixture", model="reader", configuration=())))

    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(),
                                    resolve_citation=resolve_citation).run(request)
    contract, = report.citations[0].contracts
    assert contract.paper_id == "2401.00002" and contract.paper_version == "v2"
    assert not report.citations[0].checked
    assert len(report.citations[0].outstanding) >= 3
    assert all(store.read().get(ref).status == "open" for ref in report.citations[0].outstanding)
    assert not report.verified


def test_stale_citation_callback_cannot_publish_report_or_candidate(tmp_path):
    from hardy.workflows.acquisition.contracts import ResolverResult

    store, request, item, _ = manuscript(tmp_path)

    def resolve_citation(snapshot, obligation):
        store.append((item.model_copy(update={"statement": "Changed concurrently"}),),
                     expected_revision=snapshot.revision)
        return ResolverResult()

    with pytest.raises(ValueError, match="stale"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(),
                                resolve_citation=resolve_citation).run(request)


def test_dependency_path_cannot_hide_unmapped_ledger_lemma(tmp_path):
    store, request, item, _ = manuscript(tmp_path)
    lemma = ProjectItem(id="hidden-lemma", kind="lemma", name="Unmapped prerequisite",
                        origin="target_paper", statement="A necessary omitted lemma.")
    store.append((lemma, Relation(id="needs-lemma", kind="depends_on", source=item.ref, target=lemma.ref)),
                 expected_revision=store.read().revision)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations()).run(request)
    assert lemma.ref in report.critical_path
    assert report.unreviewed_dependencies == (lemma.ref,)


def test_citation_callback_cannot_grant_itself_a_new_scope(tmp_path):
    from hardy.workflows.acquisition.contracts import ResolverResult

    store, request, _, external = manuscript(tmp_path)
    forged = Scope(id="forged", allowed_background=(external.ref,))
    with pytest.raises(ValueError, match="authority"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=lambda *_:
                                ResolverResult(records=(forged,))).run(request)
    assert forged not in store.read().current(Scope)


def test_semantic_policy_callback_cannot_rebase_structural_findings_onto_changed_graph(tmp_path):
    from hardy.workflows.ledger.policy import LedgerPolicy

    store, request, item, _ = manuscript(tmp_path)

    class ChangedPolicy(LedgerPolicy):
        def transport_accepted(self, snapshot, relation, *, scope):
            store.append((ProjectItem(id="concurrent", kind="research_note", name="Concurrent change",
                                      origin="human_authored"),), expected_revision=snapshot.revision)
            return False

    with pytest.raises(ValueError, match="stale"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), policy=ChangedPolicy()).run(request)
    assert all(o.item != item.ref for o in store.read().current(Obligation))


def test_missing_adversarial_provider_is_skipped_even_when_structure_is_clean(tmp_path):
    store, request, _, _ = manuscript(tmp_path)
    claim = replace(request.claims[0], proof_context=None, observed_bindings=(),
                    required_representations=())
    request = replace(request, claims=(claim,))
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations()).run(request)
    assert not report.structural_findings
    assert report.critiques[0].layers_run == ()
    assert report.critiques[0].layers_skipped == ("kernel", "formalization", "adversarial")
    assert "No critique layers ran" in report.critiques[0].summary


def test_real_adversarial_provider_retains_its_own_findings_and_layer(tmp_path):
    from hardy.workflows.critique import CritiqueFinding, ReviewPass

    store, request, _, _ = manuscript(tmp_path)
    finding = CritiqueFinding(kind="check_informal_step", reason="The compactness argument is circular.")

    def adversarial(request):
        return ReviewPass(subject=request.subject.ref, scope=request.scope.ref,
                          revision=request.snapshot.revision, findings=(finding,))

    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(adversarial=adversarial)).run(request)
    assert report.critiques[0].layers_run == ("adversarial",)
    assert report.critiques[0].findings == (("adversarial", finding),)
    assert report.structural_findings


def test_new_citation_cannot_inherit_another_uses_authenticated_contract(tmp_path):
    from hardy.workflows.acquisition.contracts import ResolverResult
    from hardy.workflows.ledger.contracts import (
        ArtifactRef,
        CitationContract,
        EvidenceRef,
        Resolution,
    )
    from hardy.workflows.ledger.policy import (
        AcceptanceDecision,
        AuthenticatedEvidence,
        LedgerPolicy,
    )

    store, request, _, external = manuscript(tmp_path)
    scope = store.read().get(request.scope)
    evidence = {}
    decisions = {}
    policy = LedgerPolicy(read_evidence=evidence.get, read_decision=decisions.get)

    def contract(work, identifier):
        source = ArtifactRef(uri=identifier + ":source", digest="a" * 64)
        reading = ArtifactRef(uri=identifier + ":reading", digest="b" * 64)
        references = (EvidenceRef(kind="literature", artifact=source, subject=external.ref, producer="fixture"),
                      EvidenceRef(kind="faithfulness", artifact=reading, subject=external.ref, producer="fixture"))
        value = CitationContract(id=identifier, use_site=external.ref, required_claim=external.ref,
            paper_id=identifier, paper_version="v1", source_statement=source,
            conclusion=external.statement, evidence=references)
        for reference, outcome in zip(references, ("source_read", "faithful"), strict=True):
            evidence[reference] = AuthenticatedEvidence(reference, scope.ref, work.context, outcome,
                                                       citation=value.ref)
        return value

    def accept(work, value):
        proposal = Resolution(id=work.id + ":accept", obligation=work.ref, item=external.ref,
                              evidence=value.evidence)
        receipt = ArtifactRef(uri=proposal.id, digest=proposal.digest)
        decisions[receipt] = AcceptanceDecision(proposal.ref, work.ref, external.ref, scope.ref,
                                               work.context, policy.digest)
        accepted = policy.accept(store.read(), proposal, receipt)
        closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                            "status": "resolved", "resolution": accepted})
        store.append((closed,), expected_revision=store.read().revision, validate=policy.validate)

    old = Obligation(id="old-audit", kind="check_citation", item=external.ref, scope=scope)
    old_contract = contract(old, "prior-paper")
    store.append((old, old_contract), expected_revision=store.read().revision, validate=policy.validate)
    accept(old, old_contract)
    flow = referee.RefereeWorkflow(store, critique=CritiqueOperations(), policy=policy)
    report = flow.run(request)
    assert not report.citations[0].checked
    assert report.citations[0].contracts == ()

    flow.resolve_citation = lambda snapshot, work: ResolverResult(records=(contract(work, "current-paper"),))
    report = flow.run(request)
    current_contract, = report.citations[0].contracts
    assert current_contract.id == "current-paper"
    assert not report.citations[0].checked
    current_work = store.read().get(report.citations[0].obligation)
    accept(current_work, current_contract)
    restarted = flow.run(request)
    assert restarted.citations[0].checked
    assert restarted.citations[0].contracts == (current_contract,)

    recursive = flow.run(replace(request, citation_depth=1))
    assert recursive.citations[0].checked
    assert recursive.recursive_citations[0].status == "missing_reader"
    assert not recursive.recursive_coverage_complete
