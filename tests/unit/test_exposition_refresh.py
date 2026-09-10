"""Explicit prose refresh preserves exact mathematical and exposition history."""
import importlib

import pytest

from hardy.workflows.ledger.contracts import ArtifactRef, ProjectItem, Relation, Scope
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationPlanner, PublicationRequest
from hardy.workflows.representation import RepresentationModel


@pytest.fixture
def exposition():
    try:
        return importlib.import_module("hardy.workflows.exposition")
    except ModuleNotFoundError:
        pytest.fail("G3 explicit exposition refresh is not implemented")


@pytest.fixture
def project(tmp_path, request):
    store = LedgerStore(tmp_path / "project")
    old = ProjectItem(id="theorem", name="Theorem", kind="theorem", origin="human_authored",
                      statement="Original claim", publication_visibility="public")
    prose = ProjectItem(id="paragraph", name="Explanation", kind=getattr(request, "param", "exposition"), origin="human_authored",
                        statement="Original paragraph", publication_visibility="public",
                        artifacts=(ArtifactRef(uri="old.tex", digest="a" * 64),))
    other = prose.model_copy(update={"id": "other", "statement": "Unrequested paragraph"})
    relation = Relation(id="documents", kind="documents", source=prose.ref, target=old.ref)
    other_relation = relation.model_copy(update={"id": "other-documents", "source": other.ref})
    scope = Scope(id="scope", must_prove=(old.ref,))
    store.append((old, prose, other, relation, other_relation, scope), expected_revision=0)
    new = old.model_copy(update={"statement": "Revised claim with an additional hypothesis"})
    store.append((new,), expected_revision=1)
    return store, old, new, prose, relation, scope


def request(module, records, **updates):
    store, old, new, prose, relation, _ = records
    values = dict(project=store.project, prose=prose.ref, relation=relation.ref,
                  old_target=old.ref, new_target=new.ref)
    values.update(updates)
    return module.RefreshExpositionRequest(**values)


def refresher(module, store, callback):
    return module.ExpositionRefresher(store, model=RepresentationModel(
        provider="scripted", model="prose-fixture", configuration=(("temperature", "0"),)),
        refresh_prose=callback)


@pytest.mark.parametrize("project", ["exposition", "document_fragment"], indirect=True)
def test_only_requested_paragraph_refreshes_in_one_durable_transaction(exposition, project):
    store, old, new, prose, relation, scope = project
    before = store.read()

    def refresh(query):
        assert query.prose == prose
        assert query.relation == relation
        assert query.old_target == old and query.new_target == new
        return "Revised paragraph explaining the additional hypothesis."

    result = refresher(exposition, store, refresh).refresh(request(exposition, project))
    after = LedgerStore(store.project).read()
    assert after.revision == before.revision + 1
    assert after.records == (*before.records, result.prose, result.relation)
    assert result.prose.id == prose.id
    assert result.prose.statement == "Revised paragraph explaining the additional hypothesis."
    assert result.prose.origin == "generated_local"
    assert result.prose.publication_visibility == prose.publication_visibility
    assert not result.prose.artifacts and not result.prose.evidence
    model = RepresentationModel.model_validate_json(dict(result.prose.semantics)["model"])
    assert model.provider == "scripted" and model.model == "prose-fixture"
    assert model.configuration == (("temperature", "0"),)
    persisted_request = exposition.RefreshExpositionRequest.model_validate_json(
        dict(result.prose.semantics)["exposition-refresh"])
    assert persisted_request == request(exposition, project)
    assert result.relation.id == relation.id
    assert result.relation.source == result.prose.ref and result.relation.target == new.ref
    assert after.get(old.ref) == old and after.head(new.id) == new
    assert after.get(prose.ref) == prose and after.get(relation.ref) == relation
    assert after.head("other") == before.head("other")

    planner = PublicationPlanner(store)
    current = planner.plan(PublicationRequest(roots=(new.ref,), scope=scope.ref))
    assert [p.prose.id for p in current.stale_exposition] == ["other"]
    assert [p.prose.ref for p in current.exposition] == [result.prose.ref]
    historical = planner.plan(PublicationRequest(roots=(old.ref,), scope=scope.ref))
    assert {p.prose.statement for p in historical.exposition} == {
        "Original paragraph", "Unrequested paragraph"}


def test_ordinary_math_revision_and_planning_do_not_call_prose(exposition, project):
    store, _, new, prose, _, scope = project

    def forbidden(_):
        pytest.fail("Prose may run only for an explicit refresh request")

    refresher(exposition, store, forbidden)
    changed = new.model_copy(update={"statement": "Another mathematical revision"})
    before = store.read()
    store.append((changed,), expected_revision=before.revision)
    plan = PublicationPlanner(store).plan(PublicationRequest(roots=(changed.ref,), scope=scope.ref))
    assert len(plan.stale_exposition) == 2
    assert store.read().records == (*before.records, changed)
    assert store.read().head(prose.id) == prose


@pytest.mark.parametrize("changed", ["prose", "relation", "target"])
def test_stale_request_is_rejected_before_callback(exposition, project, changed):
    store, _, new, prose, relation, _ = project
    candidate = request(exposition, project)
    record = {"prose": prose, "relation": relation, "target": new}[changed]
    updates = {"statement": "Concurrent edit"} if changed != "relation" else {"publication_role": "appendix"}
    store.append((record.model_copy(update=updates),), expected_revision=store.read().revision)
    before = store.read()

    def forbidden(_):
        pytest.fail("Stale requests must not spend a prose call")

    with pytest.raises(ValueError, match="stale"):
        refresher(exposition, store, forbidden).refresh(candidate)
    assert store.read() == before


@pytest.mark.parametrize("changed", ["prose", "relation", "target", "unrelated"])
def test_callback_concurrent_edit_prevents_partial_append(exposition, project, changed):
    store, _, new, prose, relation, _ = project
    concurrent = []

    def refresh(_):
        before = store.read()
        record = {"prose": prose, "relation": relation, "target": new,
                  "unrelated": before.head("other")}[changed]
        updates = {"statement": "Concurrent edit"} if changed != "relation" else {"publication_role": "appendix"}
        concurrent.append(store.append((record.model_copy(update=updates),), expected_revision=before.revision))
        return "Obsolete generated paragraph"

    with pytest.raises(ValueError, match="stale ledger revision"):
        refresher(exposition, store, refresh).refresh(request(exposition, project))
    assert store.read() == concurrent[0]


@pytest.mark.parametrize("bad_text", [None, 7, "", "  \n\t", {"statement": "text", "target": "replacement"}])
def test_invalid_callback_text_does_not_append(exposition, project, bad_text):
    store = project[0]
    before = store.read()
    with pytest.raises(ValueError, match="text"):
        refresher(exposition, store, lambda _: bad_text).refresh(request(exposition, project))
    assert store.read() == before


def test_callback_exception_does_not_append(exposition, project):
    store = project[0]
    before = store.read()

    def failed(_):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        refresher(exposition, store, failed).refresh(request(exposition, project))
    assert store.read() == before


@pytest.mark.parametrize("case", ["not-stale", "different-item", "wrong-source", "wrong-old-target",
                                 "wrong-kind", "foreign-project", "non-prose", "non-item", "unknown"])
def test_mismatched_refresh_is_rejected_before_callback(exposition, project, tmp_path, case):
    store, old, new, prose, relation, _ = project
    updates = {}
    if case == "not-stale":
        updates["new_target"] = old.ref
    elif case == "different-item":
        other = new.model_copy(update={"id": "different-theorem"})
        store.append((other,), expected_revision=store.read().revision)
        updates["new_target"] = other.ref
    elif case == "wrong-source":
        updates["prose"] = store.read().head("other").ref
    elif case == "wrong-old-target":
        updates["old_target"] = new.ref
    elif case == "wrong-kind":
        changed = Relation(id="different-relation", kind="uses", source=prose.ref, target=old.ref)
        store.append((changed,), expected_revision=store.read().revision)
        updates["relation"] = changed.ref
    elif case == "non-prose":
        updates["prose"] = new.ref
    elif case == "non-item":
        updates["old_target"] = store.read().head("scope").ref
    elif case == "unknown":
        updates["new_target"] = new.ref.model_copy(update={"digest": "f" * 64})
    else:
        # Identical record content in another store still does not authorize this project.
        foreign = LedgerStore(tmp_path / "foreign")
        foreign.append((old, prose, relation), expected_revision=0)
        foreign.append((new,), expected_revision=1)
        updates["project"] = foreign.project
    before = store.read()

    def forbidden(_):
        pytest.fail("Mismatched requests must not spend a prose call")

    with pytest.raises(ValueError):
        refresher(exposition, store, forbidden).refresh(request(exposition, project, **updates))
    assert store.read() == before
