"""E4 reads persisted mathematics through the real session summary."""
from test_chat import FakeChatRuntime, session

from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.ledger.contracts import (
    ArtifactRef,
    CitationContract,
    Obligation,
    ProjectItem,
    Relation,
    ResearchState,
    Scope,
)
from hardy.workflows.ledger.policy import LedgerPolicy, ScopeChangeDecision
from hardy.workflows.ledger.store import LedgerStore


def populate(path):
    store = LedgerStore(path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="ambient")
    context = manager.extend(root.ref, id="compact-context", label="compact case", declarations=(
        DeclarationSpec(id="X", symbol="X", semantic_type="manifold"),
        DeclarationSpec(id="compact", symbol="h", semantic_type="X is compact", role="local_hypothesis"),
    ), bindings=(BindingSpec(id="notation", kind="notation", symbol="M", meaning="the manifold X"),))
    def item(id, kind, **kw):
        return ProjectItem(id=id, name=id, kind=kind, origin="human_authored", **kw)
    conjecture = item("Conjecture", "conjecture", statement="Every X has property P", context=context.ref)
    failed = item("Approach", "approach", research=ResearchState(status="blocked", reason="needs compactness"))
    concept = item("Manifold", "concept")
    representation = item("Charts", "representation")
    external = item("External", "external_result", statement="A cited fact")
    scope = Scope(id="scope", must_prove=(conjecture.ref,), allowed_background=(external.ref,))
    work = Obligation(id="transport", kind="justify_transport", item=conjecture.ref,
                      scope=scope, reason="WLOG mapping is missing")
    policy = LedgerPolicy(read_scope_change=lambda old, new: ScopeChangeDecision(
        old.ref if old else None, new.ref, policy.digest))
    store.append((conjecture, failed, concept, representation, external, scope, work,
        Relation(id="charts", kind="interprets", source=representation.ref, target=concept.ref),
        Relation(id="uses-external", kind="uses", source=conjecture.ref, target=external.ref),
    ), expected_revision=store.read().revision,
        validate=policy.validate)
    return store


def test_summary_reads_project_context_and_keeps_trust_and_research_distinct(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store = populate(tmp_path)
    before = store.read()
    summary = chat.summary()
    text = summary.text()
    assert "Mathematical project" in text
    assert "compact case" in text and "X is compact" in text
    assert "Local hypotheses" in text and "M = the manifold X" in text
    assert "conjecture: Conjecture" in text
    assert "Approach: blocked; needs compactness" in text
    assert "Manifold" in text and "Charts" in text
    assert "WLOG mapping is missing" in text
    assert "Allowed background (permission only)" in text
    assert "External@" in text
    assert "Evidence authentication unavailable" in text
    assert "Publication readiness" in text and "blocked" in text
    assert any("transport" in line for line in summary.obligations)
    assert store.read() == before
    assert session(tmp_path, FakeChatRuntime([])).summary().text() == text


def test_empty_ledger_does_not_create_project_state_or_change_legacy_summary(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    assert "Mathematical project" not in chat.summary().text()
    assert not (tmp_path / "ledger").exists()


def test_corrupt_ledger_is_not_silently_reported_as_empty(tmp_path):
    import pytest
    chat = session(tmp_path, FakeChatRuntime([]))
    populate(tmp_path)
    next((tmp_path / "ledger").glob("*.json")).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="ledger"):
        chat.summary()


def test_summary_retains_exact_citation_and_stale_exposition(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store = populate(tmp_path)
    main = store.read().head("Conjecture")
    old = store.read().head("External")
    external = store.read().head("External")
    prose = ProjectItem(id="paragraph", name="old paragraph", kind="exposition",
                        origin="human_authored", statement="The original claim.")
    citation = CitationContract(id="citation", use_site=main.ref, required_claim=external.ref,
        paper_id="paper", paper_version="v1", source_statement=ArtifactRef(uri="paper.tex", digest="a" * 64),
        conclusion="A cited fact")
    store.append((prose, citation, Relation(id="documents", kind="documents", source=prose.ref, target=old.ref)),
                 expected_revision=store.read().revision)
    changed = old.model_copy(update={"statement": "A different claim"})
    store.append((changed,), expected_revision=store.read().revision)
    text = chat.summary().text()
    assert f"open: citation@{citation.digest}" in text
    stale = text.split("Stale exposition and artifacts\n", 1)[1]
    assert f"External@{old.digest}" in stale
    assert f"current External@{changed.digest}" in stale
    assert "paragraph@" in stale


def test_omitted_target_reports_publication_refusal_without_losing_context(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store = populate(tmp_path)
    omitted = ProjectItem(id="omitted", name="omitted", kind="theorem",
        origin="human_authored", publication_visibility="omitted")
    scope = Scope(id="omitted-scope", must_prove=(omitted.ref,))
    store.append((omitted, scope), expected_revision=store.read().revision)
    text = chat.summary().text()
    assert "explicitly omitted item" in text
    assert "compact case" in text


def test_nonitem_scope_target_does_not_crash_the_summary(tmp_path):
    chat = session(tmp_path, FakeChatRuntime([]))
    store = populate(tmp_path)
    store.append((Scope(id="context-scope", must_prove=(store.read().active_context,)),),
                 expected_revision=store.read().revision)
    assert "target is not a project item" in chat.summary().text()
