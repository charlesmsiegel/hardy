"""Container publication keeps document structure separate from mathematical use."""
import pytest

from hardy.workflows.context import ContextManager, DeclarationSpec
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    ProjectItem,
    Relation,
    Scope,
)
from hardy.workflows.ledger.graph import LedgerGraph
from hardy.workflows.ledger.state import LedgerSnapshot
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.publication import PublicationPlanner, PublicationRequest, plan_publication
from hardy.workflows.publish import assemble_publication


def item(id, kind="theorem", **kwargs):
    return ProjectItem(id=id, name=id, kind=kind, origin="human_authored",
                       publication_visibility=kwargs.pop("publication_visibility", "public"), **kwargs)


def edge(id, source, target, kind="contains"):
    return Relation(id=id, source=source.ref, target=target.ref, kind=kind)


def plan(store, root, scope, **kwargs):
    return PublicationPlanner(store).plan(PublicationRequest(roots=(root.ref,), scope=scope.ref, **kwargs))


@pytest.mark.parametrize("kind", ["section", "chapter", "book"])
def test_empty_container_needs_no_theorem_evidence_or_paragraph(tmp_path, kind):
    store = LedgerStore(tmp_path)
    root, scope = item("Root", kind), Scope(id="scope")
    store.append((root, scope), expected_revision=0)
    result = plan(store, root, scope)
    assert result.ready
    assert not result.unestablished and not result.missing_exposition
    assert assemble_publication(result).ready


def test_chapters_keep_ledger_order_and_deduplicate_shared_dependencies(tmp_path):
    store = LedgerStore(tmp_path)
    book = item("Book", "book")
    first, second = item("ZFirst", "chapter"), item("ASecond", "chapter")
    t1, t2 = item("FirstTheorem", statement="First exact statement"), item("SecondTheorem", statement="Second exact statement")
    lemma = item("Shared", "lemma", statement="One shared lemma")
    helper = item("Private", "lemma", statement="Do not render this helper", publication_visibility="internal")
    scope = Scope(id="scope")
    contains = (edge("z-first", book, first), edge("a-second", book, second),
                edge("first-result", first, t1), edge("second-result", second, t2))
    store.append((book, first, second, t1, t2, lemma, helper, scope, *contains,
                  edge("first-uses", t1, lemma, "uses"), edge("second-uses", t2, lemma, "uses"),
                  edge("hidden-use", lemma, helper, "uses")), expected_revision=0)
    result = plan(store, book, scope)
    assert not LedgerGraph(store.read()).dependency_closure(book.ref)
    assert result.containment == contains
    assert [p.item.id for p in result.structure] == ["Book", "ZFirst", "Shared", "FirstTheorem", "ASecond", "SecondTheorem"]
    shared = next(p for p in result.structure if p.item == lemma.ref)
    assert shared.containers == (book.ref, first.ref)
    assert helper.ref in result.closure and helper.ref in result.unestablished
    assert helper.ref not in {i.ref for i in result.items}
    assert {t1.ref, t2.ref, lemma.ref} <= set(result.missing_exposition)
    assert not {book.ref, first.ref, second.ref} & set(result.missing_exposition)
    source = assemble_publication(result).source
    assert source.count("One shared lemma") == 1
    assert "Do not render this helper" not in source
    assert source.index(r"\chapter*{Chapter: ZFirst}") < source.index("First exact statement") < source.index(r"\chapter*{Chapter: ASecond}")
    assert result == type(result).model_validate_json(result.model_dump_json())


def test_historical_container_keeps_exact_old_members_and_relation_versions(tmp_path):
    store = LedgerStore(tmp_path)
    chapter = item("Chapter", "chapter", statement="First edition")
    old, new = item("Old", statement="Old statement"), item("New", statement="New statement")
    scope = Scope(id="scope")
    original = edge("member", chapter, old)
    store.append((chapter, old, new, scope, original), expected_revision=0)
    revised = chapter.model_copy(update={"statement": "Second edition"})
    replacement = edge("member", revised, new)
    store.append((revised, replacement), expected_revision=1)
    historical, current = plan(store, chapter, scope), plan(store, revised, scope)
    assert old.ref in historical.closure and new.ref not in historical.closure
    assert new.ref in current.closure and old.ref not in current.closure
    assert historical.containment == (original,) and current.containment == (replacement,)
    # Replacing a link on an unchanged source selects its last exact revision.
    last = edge("member", revised, old)
    before = store.read()
    store.append((last,), expected_revision=2)
    assert plan(store, revised, scope).containment == (last,)
    assert plan_publication(before, current.request).containment == (replacement,)


def test_containment_cycles_fail_explicitly_even_through_hidden_sections(tmp_path):
    store = LedgerStore(tmp_path)
    a, b = item("A", "section"), item("B", "section", publication_visibility="internal")
    scope = Scope(id="scope")
    store.append((a, b, scope, edge("ab", a, b), edge("ba", b, a)), expected_revision=0)
    with pytest.raises(ValueError, match="containment cycle"):
        plan(store, a, scope)


def test_foreign_containment_reference_is_never_replaced_by_local_head():
    chapter = item("Chapter", "chapter")
    local, foreign = item("T", statement="Local"), item("T", statement="Foreign history")
    scope = Scope(id="scope")
    snapshot = LedgerSnapshot((chapter, local, scope, edge("member", chapter, foreign)))
    with pytest.raises(ValueError, match="unknown exact reference"):
        plan_publication(snapshot, PublicationRequest(roots=(chapter.ref,), scope=scope.ref))


def test_containment_targets_must_be_project_items(tmp_path):
    store = LedgerStore(tmp_path)
    chapter, scope = item("Chapter", "chapter"), Scope(id="scope")
    store.append((chapter, scope, edge("member", chapter, scope)), expected_revision=0)
    with pytest.raises(ValueError, match="containment.*project item"):
        plan(store, chapter, scope)


def test_two_chapter_theorems_keep_distinct_hypotheses_and_exact_citations(tmp_path):
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="C", label="ambient context")
    setup = manager.extend(root.ref, id="C0", label="objects", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="manifold"),
        DeclarationSpec(id="unused", symbol="Y", semantic_type="irrelevant ambient object"),
    ))
    first_context = manager.extend(setup.ref, id="C1", label="compact case", declarations=(
        DeclarationSpec(id="compact", symbol="h", semantic_type="X compact", role="local_hypothesis"),
    ))
    second_context = manager.extend(setup.ref, id="C2", label="connected case", declarations=(
        DeclarationSpec(id="connected", symbol="k", semantic_type="X connected", role="local_hypothesis"),
    ))
    book, first, second = item("Book", "book"), item("First", "chapter"), item("Second", "chapter")
    t1 = item("T1", statement="Compact conclusion", context=first_context.ref)
    t2 = item("T2", statement="Connected conclusion", context=second_context.ref)
    source = item("Source", "external_result", statement="Exact source conclusion")
    contract = CitationContract(id="citation", use_site=t2.ref, required_claim=source.ref,
        paper_id="Paper", paper_version="v2", source_hypotheses=("X connected",),
        source_statement=ArtifactRef(uri="source-v2.txt", digest="a" * 64, locator="Theorem 2"),
        conclusion=source.statement)
    scope = Scope(id="scope")
    snapshot = store.read()
    store.append((book, first, second, t1, t2, source, contract, scope,
        edge("chapter1", book, first), edge("chapter2", book, second),
        edge("theorem1", first, t1), edge("theorem2", second, t2),
        edge("hyp1", t1, snapshot.head("compact"), "uses"),
        edge("hyp2", t2, snapshot.head("connected"), "uses"),
        edge("object1", t1, snapshot.head("X"), "uses"),
        edge("object2", t2, snapshot.head("X"), "uses"),
        edge("citation-use", t2, source, "uses")), expected_revision=snapshot.revision)
    result = plan(store, book, scope)
    contexts = {c.item: c for c in result.contexts}
    assert [d.id for d in contexts[t1.ref].local_hypotheses] == ["compact"]
    assert [d.id for d in contexts[t2.ref].local_hypotheses] == ["connected"]
    assert result.citations == (contract,) and result.citations_open == (contract.ref,)
    source_text = assemble_publication(result).source
    first_text, second_text = source_text.split(r"\chapter*{Chapter: Second}")
    assert "Local hypothesis h: X compact" in first_text
    assert "Local hypothesis k: X connected" not in first_text
    assert "Local hypothesis k: X connected" in second_text
    assert "Local hypothesis h: X compact" not in second_text
    assert "irrelevant ambient object" not in source_text
    assert "Paper, version v2" in second_text and contract.digest in second_text


def test_changed_contained_theorem_reports_stale_prose_without_copying_it(tmp_path):
    store = LedgerStore(tmp_path)
    chapter, old, scope = item("Chapter", "chapter"), item("T", statement="Old claim"), Scope(id="scope")
    prose = item("Prose", "exposition", statement="This paragraph proves only the old claim.")
    store.append((chapter, old, scope, prose, edge("doc", prose, old, "documents"),
                  edge("member", chapter, old)), expected_revision=0)
    new = old.model_copy(update={"statement": "Changed claim"})
    store.append((new, edge("member", chapter, new)), expected_revision=1)
    result = plan(store, chapter, scope)
    assert result.stale_exposition[0].documented == old.ref
    assert result.stale_exposition[0].target == new.ref
    assert result.missing_exposition == (new.ref,)
    assert prose.statement not in assemble_publication(result).source


def test_noncontainer_cannot_silently_own_document_members(tmp_path):
    store = LedgerStore(tmp_path)
    theorem, member, scope = item("T"), item("Member"), Scope(id="scope")
    store.append((theorem, member, scope, edge("member", theorem, member)), expected_revision=0)
    with pytest.raises(ValueError, match="containment source"):
        plan(store, theorem, scope)
