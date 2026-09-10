"""Search and semantic routing never establish a prerequisite."""
import importlib

import pytest

from hardy.workflows.ledger.contracts import Obligation, ProjectItem, Scope
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


def fixture():
    item = ProjectItem(id="claim", kind="lemma", name="claim", origin="human_authored")
    scope = Scope(id="scope")
    work = Obligation(id="work", item=item.ref, kind="acquire_prerequisite", scope=scope)
    return LedgerSnapshot((item, scope, work)), work


def classifier(kind="unresolved", *, complete=True, selected=None):
    api = importlib.import_module("hardy.workflows.acquisition.classifier")
    values = importlib.import_module("hardy.workflows.acquisition.contracts")
    events = []
    def search(source):
        def run(snapshot, work):
            events.append(source)
            return values.SearchRecord(source=source, query="claim", complete=complete)
        return run
    def decide(query):
        events.append("decide")
        return values.GapDecision(kind=kind, reason="semantic assessment", selected=selected)
    return api.GapClassifier(search_local=search("local"), search_mathlib=search("mathlib"),
        decide=decide, model=RepresentationModel(provider="fixture", model="fake", configuration=())), events


@pytest.mark.parametrize("kind", ["cheap_local_definition", "cheap_local_proof", "literature",
    "resolve_representation", "refine_representation", "resolve_declaration", "justify_transport",
    "construct_interface", "unresolved"])
def test_searches_precede_semantic_absence_assessment_and_are_recorded(kind, tmp_path):
    state, work = fixture()
    service, events = classifier(kind)
    result = service.classify(state, work)
    assert result.kind == kind
    assert events == ["local", "mathlib", "decide"]
    note = result.record()
    assert note.research.author == "fixture/fake"
    assert '"source":"local"' in dict(note.semantics)["classification"]
    store = LedgerStore(tmp_path)
    store.append((*state.records, note), expected_revision=0)
    assert store.read().get(note.ref) == note


def test_incomplete_search_does_not_claim_no_existing_solution():
    state, work = fixture()
    service, _ = classifier("cheap_local_definition", complete=False)
    result = service.classify(state, work)
    assert result.kind == "unresolved"
    assert "incomplete" in result.reason


def test_target_id_remains_protected_even_when_scope_pins_an_old_version():
    state, work = fixture()
    old = state.get(work.item)
    new = old.model_copy(update={"name": "new version"})
    scope = Scope(id="scope", must_prove=(old.ref,))
    work = Obligation(id="work", item=new.ref, kind="prove", scope=scope)
    service, _ = classifier("literature")
    result = service.classify(LedgerSnapshot((old, new, scope, work)), work)
    assert result.kind == "target_paper"


def test_unsearched_selection_and_stale_obligation_are_refused():
    state, work = fixture()
    service, _ = classifier("local", selected=work.item)
    with pytest.raises(ValueError, match="search"):
        service.classify(state, work)
    revised = work.model_copy(update={"previous": work.ref, "reason": "new"})
    with pytest.raises(ValueError, match="current"):
        service.classify(LedgerSnapshot((*state.records, revised)), work)


@pytest.mark.parametrize("source", ["local", "mathlib"])
def test_existing_candidates_retain_exact_searched_identity(source):
    from hardy.workflows.acquisition.contracts import GapDecision, SearchMatch, SearchRecord
    state, work = fixture()
    service, _ = classifier()
    receipt = SearchRecord(source=source, query="claim", hits=(
        SearchMatch(name="claim", description="candidate", item=work.item),))
    setattr(service, "search_" + source, lambda *_: receipt)
    service.decide = lambda _: GapDecision(kind=source, reason="matches", selected=work.item)
    assert service.classify(state, work).selected == work.item


def test_target_paper_origin_is_not_an_external_source_even_without_scope_entry():
    state, work = fixture()
    item = ProjectItem.model_validate({**state.get(work.item).model_dump(), "origin": "target_paper"})
    work = work.model_copy(update={"item": item.ref})
    service, _ = classifier("literature")
    assert service.classify(LedgerSnapshot((item, work.scope, work)), work).kind == "target_paper"
