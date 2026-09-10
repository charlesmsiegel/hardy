"""Recursive Referee composes actual C2 source reading and ledger work."""
from dataclasses import replace

import pytest
from test_acquisition_literature import fixture, resolver
from test_referee import manuscript

from hardy.literature.manuscript import inventory
from hardy.workflows import referee
from hardy.workflows.acquisition.contracts import ClassifiedGap
from hardy.workflows.critique import CritiqueOperations
from hardy.workflows.ledger.contracts import (
    Obligation,
    ProjectItem,
    Scope,
)
from hardy.workflows.representation import RepresentationModel


def setup(tmp_path):
    store, request, _, _ = manuscript(tmp_path / "project")
    library, _, _, _ = fixture(tmp_path / "library")
    literature, _ = resolver(tmp_path / "library", library)
    calls = []

    def resolve(snapshot, work):
        calls.append(work.item)
        return literature.resolve(snapshot, work, ClassifiedGap(
            obligation=work, kind="literature", reason="Exact citation",
            searches=(), model=RepresentationModel(provider="fixture", model="reader", configuration=())))

    return store, request, resolve, calls


def test_depth_zero_keeps_existing_c2_direct_contract_and_never_expands(tmp_path):
    store, request, resolve, calls = setup(tmp_path)
    def unexpected(*args):
        pytest.fail("depth zero called expansion")
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(),
        resolve_citation=resolve, expand_citation=unexpected).run(request)
    assert len(calls) == 1
    assert len(report.citations[0].contracts) == 1
    assert not report.citations[0].checked
    assert report.recursive_citations == ()


@pytest.mark.parametrize("depth", [-1, 3, True, 0.5])
def test_invalid_depth_is_rejected_before_ledger_work(tmp_path, depth):
    store, request, resolve, _ = setup(tmp_path)
    request = replace(request, citation_depth=depth)
    revision = store.read().revision
    with pytest.raises(ValueError, match="citation_depth"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve).run(request)
    assert store.read().revision == revision


def test_missing_expansion_reader_is_explicit_and_keeps_c2_hypotheses(tmp_path):
    store, request, resolve, _ = setup(tmp_path)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve).run(
        replace(request, citation_depth=1))
    node, = report.recursive_citations
    assert node.status == "missing_reader"
    assert node.contract == report.citations[0].contracts[0].ref
    assert not report.recursive_coverage_complete
    assert any(store.read().get(ref).kind == "discharge_citation_hypotheses"
               for ref in report.citations[0].outstanding)


def test_unavailable_exact_source_is_explicit(tmp_path):
    store, request, resolve, _ = setup(tmp_path)
    def expand(snapshot, contract, scope):
        return referee.CitationExpansion(contract=contract.ref, scope=scope.ref, revision=snapshot.revision,
                                         detail="Exact source C is unavailable")
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=expand).run(replace(request, citation_depth=2))
    node, = report.recursive_citations
    assert node.status == "unavailable"
    assert node.detail == "Exact source C is unavailable"
    assert not report.recursive_coverage_complete


def chain(tmp_path, *, branching=False):
    """A cites B; actual C2 reads B and C; their source citations are cyclic."""
    import tarfile
    from io import BytesIO

    from test_acquisition_literature import NOW, Operations

    from hardy.literature.client import ArxivClient
    from hardy.literature.library import PaperLibrary
    from hardy.literature.metadata import parse_id
    from hardy.workflows.acquisition.literature import (
        HypothesisComparison,
        LiteratureComparison,
        LiteratureResolver,
        LiteratureSelection,
    )
    from hardy.workflows.ledger.store import LedgerStore

    store = LedgerStore(tmp_path / "ledger")
    library = PaperLibrary(tmp_path / "papers")
    sources = {}
    identifiers = {"B": "2401.00003v1", "C": "2401.00004v1"}
    for name, other in (("B", "C"), ("C", "B")):
        identifier = identifiers[name]
        feed = f'''<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
          <id>http://arxiv.org/abs/{identifier}</id><title>Paper {name}</title>
          <summary>Synthetic cyclic fixture.</summary><author><name>Fixture</name></author>
          <published>2024-01-01T00:00:00Z</published><updated>2024-01-01T00:00:00Z</updated>
          <arxiv:primary_category term="math.GN"/><category term="math.GN"/>
          </entry></feed>'''.encode()
        ArxivClient(library, transport=lambda *_args, body=feed: body,
                    clock=lambda: 1000000.0, sleep=lambda _: None).fetch(identifier)
        text = r"\documentclass{article}\begin{document}\begin{theorem}\label{main}" + f"Theorem {name}." + r"\end{theorem}\begin{proof}\cite{" + other + r"}\end{proof}"
        if branching and name == "B":
            text += r"\cite{B}"
        sources[name] = {"main.tex": text}
        body = text.encode()
        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("main.tex")
            info.size = len(body)
            archive.addfile(info, BytesIO(body))
        library.admit_source(parse_id(identifier), buffer.getvalue(),
            source_url=f"https://arxiv.org/src/{identifier}", fetched_at=NOW.isoformat())
    a = ProjectItem(id="A", kind="theorem", name="A", origin="target_paper", statement="Theorem A.")
    b = ProjectItem(id="B", kind="external_result", name="B", origin="background_paper", statement="Theorem B.")
    c = ProjectItem(id="C", kind="external_result", name="C", origin="background_paper", statement="Theorem C.")
    scope = Scope(id="scope", must_prove=(a.ref,))
    from hardy.workflows.ledger.contracts import Relation
    store.append((a, b, c, scope, Relation(id="A-B", kind="depends_on", source=a.ref, target=b.ref)), expected_revision=0)
    manuscript_source = {"a.tex": r"\begin{theorem}Theorem A.\end{theorem}\begin{proof}\cite{B}\end{proof}"}
    scanned = inventory(manuscript_source)
    opening = scanned.environments[0].opening
    from hardy.literature.manuscript import SourceSpan
    request = referee.RefereeRequest(sources=manuscript_source, scope=scope.ref, main_results=(a.ref,),
        claims=(referee.ManuscriptClaim(environment=opening,
            statement=SourceSpan(opening.path, opening.digest, opening.end, opening.end + len(a.statement)), item=a.ref),),
        citations=(referee.CitationUse(span=scanned.citations[0].key_span, use_site=a.ref, required_claim=b.ref),))
    operations = Operations(tmp_path / "runs")
    model = RepresentationModel(provider="fixture", model="source-reader", configuration=())
    literature = LiteratureResolver(library=library, model=model,
        search=lambda query: (LiteratureSelection(paper_id=identifiers[query.subject.name], statement_ref="main"),),
        compare=lambda query, source: LiteratureComparison(required_statement=query.subject.statement,
            required_hypotheses=(), hypotheses=(HypothesisComparison(hypothesis="Unproved premise", availability="unresolved",
                reason="Fixture deliberately retains its premise"),), conclusion=query.subject.statement,
            conclusion_matches=True, conclusion_reason="Scripted exact statement reading"),
        prepare=operations.prepare, review=operations.review, request_admission=operations.admit)
    resolved = []
    expanded = []
    def resolve(snapshot, work):
        resolved.append(snapshot.get(work.item).name)
        return literature.resolve(snapshot, work, ClassifiedGap(obligation=work, kind="literature", reason="Read cited source",
            searches=(), model=model))
    def expand(snapshot, contract, scope):
        name = "B" if contract.paper_id == "2401.00003" else "C"
        expanded.append(name)
        source = next(item for item in snapshot.current(ProjectItem) if item.kind == "external_result"
                      and contract.source_statement in item.artifacts)
        uses = tuple(referee.CitationUse(span=citation.key_span, use_site=source.ref,
                     required_claim=c.ref if citation.key == "C" else b.ref)
                     for citation in inventory(sources[name]).citations)
        return referee.CitationExpansion(contract=contract.ref, scope=scope.ref, revision=snapshot.revision,
            source=source.ref, sources=sources[name], uses=uses, model=model, detail=f"Scripted reading of {name}")
    return store, request, resolve, expand, resolved, expanded


@pytest.mark.parametrize("depth,expected_resolved,expected_expanded,statuses", [
    (0, ["B"], [], []),
    (1, ["B", "C"], ["B"], ["expanded", "depth_limit"]),
    (2, ["B", "C", "B"], ["B", "C"], ["expanded", "expanded", "cycle"]),
])
def test_cyclic_chain_visits_exact_requested_depth_using_real_c2(tmp_path, depth, expected_resolved, expected_expanded, statuses):
    store, request, resolve, expand, resolved, expanded = chain(tmp_path)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=expand).run(replace(request, citation_depth=depth))
    assert resolved == expected_resolved
    assert expanded == expected_expanded
    assert [node.status for node in report.recursive_citations] == statuses
    assert not report.recursive_coverage_complete
    assert not report.citations[0].checked
    assert all(work.status == "open" for work in store.read().current(Obligation))
    assert len([w for w in store.read().current(Obligation) if w.kind == "discharge_citation_hypotheses"]) == len(resolved)


def test_child_node_budget_prevents_c2_work_and_reports_cutoff(tmp_path):
    store, request, resolve, expand, resolved, expanded = chain(tmp_path)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=expand).run(replace(request, citation_depth=2, citation_max_nodes=1))
    assert resolved == ["B"]
    assert expanded == ["B"]
    assert [node.status for node in report.recursive_citations] == ["expanded", "node_limit"]
    assert not report.recursive_coverage_complete


@pytest.mark.parametrize("attack", ["scope", "use_site", "source", "bytes", "revision", "target", "overwrite", "authority",
                                    "model", "duplicate", "unavailable", "type"])
def test_expansion_rejects_foreign_or_authoritative_candidate_before_append(tmp_path, attack):
    store, request, resolve, expand, _, _ = chain(tmp_path)
    before = []
    rejected = []
    def malicious(snapshot, contract, scope):
        result = expand(snapshot, contract, scope)
        before.append(snapshot.revision)
        if attack == "type":
            return "invented reading"
        if attack == "model":
            return replace(result, model=None)
        if attack == "duplicate":
            return replace(result, uses=result.uses * 2)
        if attack == "unavailable":
            return replace(result, source=None)
        if attack == "scope":
            return replace(result, scope=Scope(id="foreign").ref)
        if attack == "use_site":
            return replace(result, uses=(replace(result.uses[0], use_site=request.main_results[0]),))
        if attack == "source":
            return replace(result, source=result.uses[0].required_claim)
        if attack == "bytes":
            return replace(result, sources={"main.tex": result.sources["main.tex"] + " changed"})
        if attack == "revision":
            return replace(result, revision=snapshot.revision - 1)
        if attack == "target":
            return replace(result, uses=(replace(result.uses[0], required_claim=request.main_results[0]),))
        if attack == "authority":
            return replace(result, records=(Scope(id="granted"),))
        child = snapshot.get(result.uses[0].required_claim).model_copy(update={"statement": "Silently weakened"})
        rejected.append(child)
        return replace(result, records=(child,), uses=(replace(result.uses[0], required_claim=child.ref),))
    with pytest.raises(ValueError):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
            expand_citation=malicious).run(replace(request, citation_depth=2))
    assert store.read().revision == before[0]
    assert all(record not in store.read().records for record in rejected)


def test_expansion_detects_concurrent_callback_mutation(tmp_path):
    store, request, resolve, expand, _, _ = chain(tmp_path)
    def stale(snapshot, contract, scope):
        result = expand(snapshot, contract, scope)
        store.append((ProjectItem(id="concurrent", kind="research_note", name="Concurrent", origin="human_authored"),),
            expected_revision=snapshot.revision)
        return result
    with pytest.raises(ValueError, match="stale"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
            expand_citation=stale).run(replace(request, citation_depth=2))



def test_unavailable_c_after_reading_b_is_reported_at_its_actual_depth(tmp_path):
    store, request, resolve, expand, resolved, _ = chain(tmp_path)
    def unavailable(snapshot, contract, scope):
        if contract.paper_id == "2401.00004":
            return referee.CitationExpansion(contract=contract.ref, scope=scope.ref, revision=snapshot.revision,
                detail="C source unavailable to expansion reader")
        return expand(snapshot, contract, scope)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=unavailable).run(replace(request, citation_depth=2))
    assert resolved == ["B", "C"]
    assert [(n.depth, n.status) for n in report.recursive_citations] == [(0, "expanded"), (1, "unavailable")]
    assert "C source unavailable" in report.recursive_citations[1].detail


def test_recursive_snapshot_cannot_survive_later_semantic_reader_mutation(tmp_path):
    from hardy.workflows.critique import ReviewPass
    store, request, resolve, expand, _, _ = chain(tmp_path)
    def adversarial(review):
        source = next(item for item in review.snapshot.current(ProjectItem)
                      if item.kind == "external_result" and item.artifacts)
        store.append((source.model_copy(update={"statement": "Source changed after recursive audit"}),),
            expected_revision=review.snapshot.revision)
        return ReviewPass(subject=review.subject.ref, scope=review.scope.ref, revision=review.snapshot.revision)
    with pytest.raises(ValueError, match="stale"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(adversarial=adversarial), resolve_citation=resolve,
            expand_citation=expand).run(replace(request, citation_depth=2))


def test_node_budget_reserves_siblings_before_descending(tmp_path):
    store, request, resolve, expand, resolved, expanded = chain(tmp_path, branching=True)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=expand).run(replace(request, citation_depth=2, citation_max_nodes=3))
    assert resolved == ["B", "C", "B"]
    assert expanded == ["B", "C"]
    assert any(node.status == "node_limit" for node in report.recursive_citations)
    assert not report.recursive_coverage_complete


def test_expansion_reading_persists_exact_uses_not_only_digest(tmp_path):
    import json
    store, request, resolve, expand, _, _ = chain(tmp_path)
    referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
        expand_citation=expand).run(replace(request, citation_depth=1))
    note, = (item for item in store.read().current(ProjectItem) if item.id.startswith("referee:expansion:"))
    semantics = dict(note.semantics)
    uses = json.loads(semantics["uses"])
    assert uses[0]["required_claim"]["id"] == "C"
    assert uses[0]["span"]["path"] == "main.tex"
    assert json.loads(semantics["model"])["model"] == "source-reader"


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_node_bound_is_rejected_before_any_citation_work(tmp_path, limit):
    store, request, resolve, _, _, _ = chain(tmp_path)
    before = store.read().revision
    with pytest.raises(ValueError, match="citation_max_nodes"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve).run(
            replace(request, citation_depth=1, citation_max_nodes=limit))
    assert store.read().revision == before


def test_missing_child_contract_and_unmapped_source_citations_remain_explicit(tmp_path):
    from hardy.workflows.acquisition.contracts import ResolverResult
    store, request, resolve, expand, _, _ = chain(tmp_path, branching=True)
    def child_unavailable(snapshot, work):
        return resolve(snapshot, work) if work.item.id == "B" else ResolverResult(detail="C cannot be acquired")
    def partial(snapshot, contract, scope):
        result = expand(snapshot, contract, scope)
        return replace(result, uses=result.uses[:1])
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=child_unavailable,
        expand_citation=partial).run(replace(request, citation_depth=2))
    expanded, missing = report.recursive_citations
    assert expanded.status == "expanded" and len(expanded.unmapped) == 1
    assert missing.status == "no_contract" and missing.children[0].outstanding
    assert not report.recursive_coverage_complete


def test_missing_direct_contract_is_not_vacuous_recursive_coverage(tmp_path):
    store, request, _, _, _, _ = chain(tmp_path)
    report = referee.RefereeWorkflow(store, critique=CritiqueOperations()).run(replace(request, citation_depth=2))
    node, = report.recursive_citations
    assert node.status == "no_contract" and node.parent is None
    assert not report.recursive_coverage_complete


@pytest.mark.parametrize("attack", ["files", "bytes"])
def test_expansion_bounds_source_input_before_inventory_or_child_work(tmp_path, attack, monkeypatch):
    from hardy.workflows import citation_audit
    store, request, resolve, expand, resolved, _ = chain(tmp_path)
    def oversized(snapshot, contract, scope):
        result = expand(snapshot, contract, scope)
        if attack == "files":
            sources = {f"{index}.tex": "" for index in range(129)}
        else:
            sources = {"main.tex": "x" * 2_000_001}
        def forbidden(*args):
            pytest.fail("oversized source reached inventory")
        monkeypatch.setattr(citation_audit, "inventory", forbidden)
        return replace(result, sources=sources)
    with pytest.raises(ValueError, match="source input bound"):
        referee.RefereeWorkflow(store, critique=CritiqueOperations(), resolve_citation=resolve,
            expand_citation=oversized).run(replace(request, citation_depth=2))
    assert resolved == ["B"]
