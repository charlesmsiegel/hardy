"""Project retrieval rebuilds discovery but authenticates delivery with real B2 policy."""
import json
from hashlib import sha256

import pytest
from test_ledger_policy import PolicyAuthority

from hardy.formal.contracts import EnvironmentIdentity
from hardy.workflows import retrieval
from hardy.workflows.ledger.contracts import ArtifactRef, Obligation, ProjectItem, Scope
from hardy.workflows.ledger.store import LedgerStore

ENV = EnvironmentIdentity(lean_version="4", lean_commit="lean", mathlib_revision="mathlib",
                          lake_manifest_sha256="a" * 64)


def project(tmp_path):
    store = LedgerStore(tmp_path / "project")
    artifact = ArtifactRef(uri="Fixture.lean", digest=sha256(b"theorem fixture : True := by trivial").hexdigest())
    theorem = ProjectItem(id="stable-lemma", name="Reusable lemma", kind="lemma", origin="generated_local",
                          statement="True", artifacts=(artifact,))
    scope = Scope(id="scope", must_prove=(theorem.ref,))
    work = Obligation(id="proof", item=theorem.ref, kind="prove", scope=scope)
    authority = PolicyAuthority()
    store.append((theorem, scope, work), expected_revision=0, validate=authority.policy.validate)
    proposal, receipt = authority.proposal(work)
    accepted = authority.policy.accept(store.read(), proposal, receipt)
    closed = Obligation.model_validate({**work.model_dump(), "previous": work.ref,
                                       "status": "resolved", "resolution": accepted})
    store.append((closed,), expected_revision=1, validate=authority.policy.validate)
    data = {artifact: b"theorem fixture : True := by trivial"}
    observed = []
    def read_formal(request):
        observed.append(request)
        return retrieval.FormalReading(source_id=request.source.id, source_digest=request.source.content_digest,
            subject=request.subject.ref, evidence=request.evidence, scope=request.query.scope,
            context=request.query.context, artifact=artifact, declaration="Fixture.fixture", environment=ENV,
            required_imports=("Fixture",), importable=True)
    source = retrieval.ledger_source("project", store)
    index = retrieval.build_index((source,))
    def retriever(**changes):
        values = dict(index=index, read_source=lambda _: retrieval.ledger_source("project", LedgerStore(store.project)),
                      policies={"project": authority.policy}, read_formal=read_formal, read_artifact=data.get)
        values.update(changes)
        return retrieval.ProjectRetriever(**values)
    query = retrieval.RetrievalQuery(project_source="project", text=theorem.id, scope=scope.ref,
                                    environment=ENV, available_imports=("Fixture",))
    return store, theorem, scope, authority, data, observed, index, retriever, query


def test_verified_lemma_is_authenticated_after_restart_and_index_is_only_discovery(tmp_path):
    store, theorem, _, _, _, observed, index, retriever, query = project(tmp_path)
    assert not observed
    rebuilt = retrieval.build_index((retrieval.ledger_source("project", LedgerStore(store.project)),))
    assert rebuilt == index and rebuilt.digest == index.digest
    result = retriever().retrieve(query)
    hit, = result.delivered
    assert hit.entry.ref == theorem.ref and hit.entry.source_id == "project"
    assert hit.group == "verified_formal"
    assert hit.formal.declaration == "Fixture.fixture"
    assert hit.formal.artifact in theorem.artifacts
    assert hit.formal.environment == ENV
    assert len(observed) == 1  # The final boundary checks B2 without reinvoking the formal reader.
    assert result.index_digest == index.digest
    assert result.total_candidates == 1 and not result.truncated


@pytest.mark.parametrize("attack", ["revoked", "artifact", "environment", "imports", "missing_reader", "stale", "foreign"])
def test_delivery_rejects_current_invalid_authority_or_source(tmp_path, attack):
    store, theorem, _, authority, data, _, index, make, query = project(tmp_path)
    changes = {}
    if attack == "revoked":
        authority.evidence.clear()
    elif attack == "artifact":
        data[theorem.artifacts[0]] = b"tampered artifact"
    elif attack == "environment":
        query = query.model_copy(update={"environment": ENV.model_copy(update={"lean_commit": "different"})})
    elif attack == "imports":
        query = query.model_copy(update={"available_imports": ()})
    elif attack == "missing_reader":
        changes["read_formal"] = None
    elif attack == "stale":
        store.append((theorem.model_copy(update={"statement": "False"}),), expected_revision=store.read().revision)
    else:
        source = retrieval.ledger_source("foreign", store)
        changes.update(index=retrieval.build_index((source, retrieval.ledger_source("project", store))),
                       read_source=lambda name: retrieval.ledger_source(name, store))
        query = query.model_copy(update={"source_ids": ("foreign",)})
    result = make(**changes).retrieve(query)
    assert not result.delivered
    assert result.rejected and all(hit.reason for hit in result.rejected)
    if attack == "foreign":
        assert "foreign project" in result.rejected[0].reason
    assert index.digest == result.index_digest or attack == "foreign"


def test_source_identity_covers_records_and_active_context_not_only_revision(tmp_path):
    from hardy.workflows.ledger.contracts import MathematicalContext
    from hardy.workflows.ledger.state import LedgerSnapshot
    context = MathematicalContext(id="C", label="Context", origin="human_authored")
    item = ProjectItem(id="idea", name="Concept", kind="concept", origin="human_authored")
    first = LedgerSnapshot((context, item), revision=1)
    second = LedgerSnapshot((context, item.model_copy(update={"name": "Changed"})), revision=1)
    third = LedgerSnapshot((context, item), revision=1, active_context=context.ref)
    indexes = [retrieval.build_index((retrieval.RetrievalSource(id="p", snapshot=snapshot),))
               for snapshot in (first, second, third)]
    assert len({index.digest for index in indexes}) == 3


def test_missing_disabled_and_unreadable_sources_are_explicit(tmp_path):
    missing = retrieval.ledger_source("absent", LedgerStore(tmp_path / "missing"))
    disabled = retrieval.ledger_source("disabled", LedgerStore(tmp_path / "disabled"), enabled=False)
    malformed = LedgerStore(tmp_path / "corrupt")
    malformed.project.mkdir()
    (malformed.project / "ledger").mkdir()
    (malformed.project / "ledger" / "00000000000000000001.json").write_text("not JSON")
    unreadable = retrieval.ledger_source("bad", malformed)
    index = retrieval.build_index((missing, disabled, unreadable))
    assert [s.status for s in index.sources] == ["absent", "unreadable", "disabled"]
    assert not index.entries


def test_serialized_index_cannot_relabel_unproved_fact_as_semantic_summary(tmp_path):
    _, _, _, authority, _, _, index, make, query = project(tmp_path)
    authority.evidence.clear()
    entry = index.entries[0].model_copy(update={"group": "concept_representation_context"})
    forged = retrieval.ProjectRetrievalIndex.model_validate_json(index.model_copy(update={"entries": (entry,)}).model_dump_json())
    result = make(index=forged).retrieve(query)
    assert not result.delivered
    assert "indexed content" in result.rejected[0].reason


def test_approved_external_assumption_is_not_kernel_verified(tmp_path):
    from hardy.workflows.ledger.policy import LedgerPolicy, ScopeChangeDecision
    store = LedgerStore(tmp_path)
    assumption = ProjectItem(id="paper-result", name="Imported theorem", kind="external_result",
                             origin="background_paper", statement="An admitted statement")
    scope = Scope(id="scope", allowed_background=(assumption.ref,))
    policy = LedgerPolicy(read_scope_change=lambda before, after: ScopeChangeDecision(
        before.ref if before else None, after.ref, policy.digest))
    store.append((assumption, scope), expected_revision=0, validate=policy.validate)
    index = retrieval.build_index((retrieval.ledger_source("p", store),))
    query = retrieval.RetrievalQuery(project_source="p", text=assumption.id, scope=scope.ref)
    service = retrieval.ProjectRetriever(index, read_source=lambda _: retrieval.ledger_source("p", store), policies={"p": policy})
    result = service.retrieve(query)
    hit, = result.delivered
    assert hit.group == "approved_external_assumptions" and hit.formal is None
    revoked = Scope(id="other-scope")
    store.append((revoked,), expected_revision=store.read().revision)
    updated = retrieval.build_index((retrieval.ledger_source("p", store),))
    service = retrieval.ProjectRetriever(updated, read_source=lambda _: retrieval.ledger_source("p", store), policies={"p": policy})
    assert not service.retrieve(query.model_copy(update={"scope": revoked.ref})).delivered


def test_active_context_shadowing_ranks_only_active_alias_and_keeps_locals_scoped(tmp_path):
    from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
    store = LedgerStore(tmp_path)
    first = ProjectItem(id="first", name="First concept", kind="concept", origin="human_authored")
    second = first.model_copy(update={"id": "second", "name": "Second concept"})
    scope = Scope(id="scope")
    store.append((first, second, scope), expected_revision=0)
    manager = ContextManager(store)
    base = manager.create_root(id="root", label="Base")
    outer = manager.extend(base.ref, id="outer", label="Outer", bindings=(
        BindingSpec(id="alias-outer", kind="alias", symbol="M", meaning="First", target=first.ref),))
    inner = manager.extend(outer.ref, id="inner", label="Inner", declarations=(
        DeclarationSpec(id="local-X", symbol="X", semantic_type="space"),), bindings=(
        BindingSpec(id="alias-inner", kind="alias", symbol="M", meaning="Second", target=second.ref),))
    index = retrieval.build_index((retrieval.ledger_source("p", store),))
    service = retrieval.ProjectRetriever(index, read_source=lambda _: retrieval.ledger_source("p", store))
    query = retrieval.RetrievalQuery(project_source="p", text="M", scope=scope.ref, context=inner.ref)
    assert [hit.entry.ref for hit in service.retrieve(query).delivered] == [second.ref]
    assert [hit.entry.ref for hit in service.retrieve(query.model_copy(update={"context": outer.ref})).delivered] == [first.ref]
    assert not service.retrieve(query.model_copy(update={"context": None})).delivered
    local = service.retrieve(query.model_copy(update={"text": "local-X"}))
    assert local.delivered[0].entry.context == inner.ref
    assert local.delivered[0].group == "concept_representation_context" and local.delivered[0].formal is None
    foreign = service.retrieve(query.model_copy(update={"text": "local-X", "context": outer.ref}))
    assert not foreign.delivered and "context-local" in foreign.rejected[0].reason


def test_goal_and_dead_end_discovery_preserves_recorded_status_and_query_limits(tmp_path):
    from hardy.workflows.ledger.contracts import ResearchState
    store = LedgerStore(tmp_path)
    scope = Scope(id="scope")
    goal = ProjectItem(id="goal", name="Compact goal", kind="goal", origin="human_authored", statement="Show compactness")
    approach = ProjectItem(id="approach", name="Compact route", kind="approach", origin="generated_local",
        statement="Try finite covering", research=ResearchState(status="blocked", reason="The space need not be finite", author="fixture"))
    store.append((scope, goal, approach), expected_revision=0)
    index = retrieval.build_index((retrieval.ledger_source("p", store),))
    service = retrieval.ProjectRetriever(index, read_source=lambda _: retrieval.ledger_source("p", store))
    query = retrieval.RetrievalQuery(project_source="p", text="Compact", scope=scope.ref)
    result = service.retrieve(query)
    assert {hit.group for hit in result.delivered} == {"goals_conjectures", "approaches_deadends"}
    assert any("blocked" in hit.entry.summary for hit in result.delivered)
    limited = service.retrieve(query.model_copy(update={"limit": 1}))
    assert limited.total_candidates == 2 and limited.truncated and len(limited.matches) == 1
    bounded = service.retrieve(query.model_copy(update={"max_characters": 1}))
    assert not bounded.delivered and bounded.truncated and bounded.characters_delivered == 0
    assert all(hit.entry.summary == "" for hit in bounded.matches)


def test_shared_library_requires_bridge_and_current_source_policy(tmp_path):
    store, theorem, _, authority, data, _, _, make, query = project(tmp_path)
    provenance = ArtifactRef(uri="library-manifest.json", digest="d" * 64)
    def source(id):
        return retrieval.ledger_source(id, store, kind="shared_library" if id == "library" else "project",
                                       provenance=provenance if id == "library" else None)
    index = retrieval.build_index((source("library"), source("project")))
    query = query.model_copy(update={"source_ids": ("library",)})
    def bridge(request):
        return retrieval.SharedAuthorization(source_id=request.source.id, source_digest=request.source.content_digest,
            provenance=request.source.provenance, project_source=request.query.project_source,
            scope=request.query.scope, context=request.query.context)
    without = make(index=index, read_source=source, policies={"library": authority.policy})
    assert not without.retrieve(query).delivered
    with_bridge = make(index=index, read_source=source, policies={"library": authority.policy}, read_shared=bridge)
    result = with_bridge.retrieve(query)
    hit, = result.delivered
    assert hit.entry.source_id == "library" and hit.formal.artifact == theorem.artifacts[0]
    assert result.sources[0].provenance == provenance
    authority.evidence.clear()
    assert not with_bridge.retrieve(query).delivered
    assert data  # The bytes alone do not substitute for revoked ledger authority.


def test_shared_library_cannot_skip_ledger_policy_even_with_matching_environment(tmp_path):
    store, _, _, _, _, _, _, make, query = project(tmp_path)
    provenance = ArtifactRef(uri="source-manifest", digest="d" * 64)
    sources = {id: retrieval.ledger_source(id, store, kind="shared_library" if id == "library" else "project",
        provenance=provenance if id == "library" else None) for id in ("project", "library")}
    def bridge(request):
        return retrieval.SharedAuthorization(source_id=request.source.id, source_digest=request.source.content_digest,
            provenance=request.source.provenance, project_source=request.query.project_source,
            scope=request.query.scope, context=request.query.context)
    service = make(index=retrieval.build_index(tuple(sources.values())), read_source=sources.get, policies={}, read_shared=bridge)
    result = service.retrieve(query.model_copy(update={"source_ids": ("library",)}))
    assert not result.delivered


def test_shadowed_local_declaration_is_not_delivered_under_its_old_printed_name(tmp_path):
    from hardy.workflows.context import ContextManager, DeclarationSpec
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="Root")
    outer = manager.extend(root.ref, id="outer", label="Outer", declarations=(
        DeclarationSpec(id="old-X", symbol="X", semantic_type="space"),))
    inner = manager.extend(outer.ref, id="inner", label="Inner", declarations=(
        DeclarationSpec(id="new-X", symbol="X", semantic_type="group"),))
    scope = Scope(id="scope")
    store.append((scope,), expected_revision=store.read().revision)
    def source(_):
        return retrieval.ledger_source("p", store)
    service = retrieval.ProjectRetriever(retrieval.build_index((source("p"),)), read_source=source)
    query = retrieval.RetrievalQuery(project_source="p", text="X", scope=scope.ref, context=inner.ref)
    delivered = service.retrieve(query).delivered
    assert [hit.entry.ref.id for hit in delivered] == ["new-X"]


def test_formal_callback_cannot_leave_revoked_policy_evidence_in_delivered_result(tmp_path):
    _, _, _, authority, _, _, _, make, query = project(tmp_path)
    service = make()
    original = service.read_formal
    def revoke(request):
        result = original(request)
        authority.evidence.clear()
        return result
    service.read_formal = revoke
    result = service.retrieve(query)
    assert not result.delivered


def test_context_branch_can_be_discovered_by_stable_id_before_activation(tmp_path):
    from hardy.workflows.context import ContextManager, DeclarationSpec
    store = LedgerStore(tmp_path)
    manager = ContextManager(store)
    root = manager.create_root(id="root", label="Root")
    child = manager.extend(root.ref, id="branch", label="Compact branch", declarations=(
        DeclarationSpec(id="local-h", symbol="h", semantic_type="X is compact", role="local_hypothesis"),))
    scope = Scope(id="scope")
    store.append((scope,), expected_revision=store.read().revision)
    def source(_):
        return retrieval.ledger_source("p", store)
    service = retrieval.ProjectRetriever(retrieval.build_index((source("p"),)), read_source=source)
    before = store.read()
    result = service.retrieve(retrieval.RetrievalQuery(project_source="p", text="branch", scope=scope.ref))
    hit, = result.delivered
    assert hit.entry.ref == child.ref and hit.group == "concept_representation_context"
    assert "X is compact" in hit.entry.summary and hit.formal is None
    assert store.read() == before
    assert not service.retrieve(retrieval.RetrievalQuery(project_source="p", text="h", scope=scope.ref)).delivered


@pytest.mark.parametrize("status", ["absent", "unreadable", "disabled"])
def test_previously_indexed_source_becoming_unavailable_retains_rejected_candidate_and_receipt(tmp_path, status):
    _, _, _, _, _, _, _, make, query = project(tmp_path)
    result = make(read_source=lambda name: retrieval.RetrievalSource(name, status=status)).retrieve(query)
    assert not result.delivered
    assert result.sources[0].status == status
    assert result.rejected[0].entry.ref.id == "stable-lemma"
    assert status in result.rejected[0].reason


def test_concurrent_source_change_during_authentication_rejects_delivery(tmp_path):
    store, theorem, _, _, _, _, _, make, query = project(tmp_path)
    service = make()
    original = service.read_formal
    def changed(request):
        result = original(request)
        store.append((theorem.model_copy(update={"name": "Changed name"}),), expected_revision=store.read().revision)
        return result
    service.read_formal = changed
    result = service.retrieve(query)
    assert not result.delivered
    assert "source changed during" in result.rejected[0].reason


def test_shared_global_name_search_works_inside_requesting_project_local_context(tmp_path):
    from hardy.workflows.context import ContextManager
    store, _, _, authority, _, _, _, make, query = project(tmp_path)
    shared_snapshot = store.read()
    context = ContextManager(store).create_root(id="local", label="Requesting local context")
    provenance = ArtifactRef(uri="shared-manifest", digest="d" * 64)
    def source(id):
        return (retrieval.RetrievalSource(id, shared_snapshot, kind="shared_library", provenance=provenance)
                if id == "library" else retrieval.ledger_source("project", store))
    def bridge(request):
        return retrieval.SharedAuthorization(source_id=request.source.id, source_digest=request.source.content_digest,
            provenance=request.source.provenance, project_source=request.query.project_source,
            scope=request.query.scope, context=request.query.context)
    service = make(index=retrieval.build_index((source("project"), source("library"))), read_source=source,
                   policies={"library": authority.policy}, read_shared=bridge)
    result = service.retrieve(query.model_copy(update={"text": "Reusable lemma", "source_ids": ("library",), "context": context.ref}))
    hit, = result.delivered
    assert hit.entry.source_id == "library" and hit.formal.context == context.ref


@pytest.mark.parametrize("revoke_on_read", [1, 2])
def test_later_hit_revocation_is_reauthenticated_before_returning_earlier_proof(tmp_path, revoke_on_read):
    store, theorem, scope, authority, _, observed, _, make, query = project(tmp_path)
    second = theorem.model_copy(update={"id": "zz-second", "name": "Reusable second"})
    work = Obligation(id="second-proof", item=second.ref, scope=scope, kind="prove")
    store.append((second, work), expected_revision=store.read().revision)
    proposal, receipt = authority.proposal(work)
    accepted = authority.policy.accept(store.read(), proposal, receipt)
    store.append((Obligation.model_validate({**work.model_dump(), "previous": work.ref,
        "status": "resolved", "resolution": accepted}),), expected_revision=store.read().revision, validate=authority.policy.validate)
    index = retrieval.build_index((retrieval.ledger_source("project", store),))
    service = make(index=index)
    original = service.read_formal
    def revoke_previous(request):
        result = original(request)
        if request.subject.ref == second.ref and sum(r.subject.ref == second.ref for r in observed) == revoke_on_read:
            for ref in tuple(authority.evidence):
                if ref.subject == theorem.ref:
                    del authority.evidence[ref]
        return result
    service.read_formal = revoke_previous
    result = service.retrieve(query.model_copy(update={"text": "Reusable"}))
    assert all(any(ref.subject == hit.entry.ref for ref in authority.evidence) for hit in result.delivered)
    assert [hit.entry.ref for hit in result.delivered] == ([second.ref] if revoke_on_read == 1 else [theorem.ref, second.ref])
    assert len(observed) == 2  # One formal reader invocation per candidate; final B2 checks are separate.


def test_delivery_budget_counts_complete_serialized_formal_payload(tmp_path):
    _, _, _, _, _, _, _, make, query = project(tmp_path)
    complete = make().retrieve(query)
    payload = json.dumps({"index_digest": complete.index_digest, "query_digest": complete.query.digest,
        "delivered": [hit.model_dump(mode="json") for hit in complete.delivered]}, ensure_ascii=False, sort_keys=True)
    assert complete.characters_delivered == len(payload)
    result = make().retrieve(query.model_copy(update={"max_characters": len(complete.delivered[0].entry.summary) + 50}))
    assert not result.delivered and result.truncated and result.characters_delivered == 0


def test_large_historical_assessment_cannot_bypass_delivery_budget(tmp_path):
    from hardy.workflows.ledger.contracts import ResearchState
    store = LedgerStore(tmp_path)
    old = ProjectItem(id="route", name="Route", kind="approach", origin="human_authored",
        research=ResearchState(status="blocked", reason="x" * 100000, author="reader"))
    scope = Scope(id="scope")
    store.append((old, scope), expected_revision=0)
    current = old.model_copy(update={"research": ResearchState(status="investigating", reason="retry", author="reader")})
    store.append((current,), expected_revision=1)
    def source(_):
        return retrieval.ledger_source("p", store)
    service = retrieval.ProjectRetriever(retrieval.build_index((source("p"),)), read_source=source)
    result = service.retrieve(retrieval.RetrievalQuery(project_source="p", text="route", scope=scope.ref, max_characters=8192))
    assert not result.delivered and result.truncated and result.characters_delivered == 0
    assert "budget" in result.rejected[0].reason


def test_rejected_diagnostic_text_does_not_spend_later_delivery_budget(tmp_path):
    store = LedgerStore(tmp_path)
    rejected = ProjectItem(id="a-rejected", name="Reusable rejected", kind="lemma", origin="human_authored",
        statement="x" * 1500)
    accepted = ProjectItem(id="b-accepted", name="Reusable accepted", kind="concept", origin="human_authored",
        statement="y" * 500)
    scope = Scope(id="scope")
    store.append((rejected, accepted, scope), expected_revision=0)
    def source(_):
        return retrieval.ledger_source("p", store)
    service = retrieval.ProjectRetriever(retrieval.build_index((source("p"),)), read_source=source)
    result = service.retrieve(retrieval.RetrievalQuery(project_source="p", text="Reusable", scope=scope.ref, max_characters=2000))
    assert [hit.entry.ref for hit in result.delivered] == [accepted.ref]
    assert result.rejected[0].entry.ref == rejected.ref and "reader" in result.rejected[0].reason
    assert result.characters_delivered <= 2000 and not result.truncated


def test_goal_identity_recovers_linked_approach_and_earlier_blocked_assessment(tmp_path):
    from hardy.workflows.ledger.contracts import Relation, ResearchState
    store = LedgerStore(tmp_path)
    goal = ProjectItem(id="goal-topology", name="Topology question", kind="goal", origin="human_authored")
    old = ProjectItem(id="route", name="Covering method", kind="approach", origin="generated_local",
        research=ResearchState(status="blocked", reason="Finite covering is unavailable", author="first-reader"))
    scope = Scope(id="scope")
    edge = Relation(id="pursues", kind="pursues", source=old.ref, target=goal.ref)
    store.append((goal, old, scope, edge), expected_revision=0)
    current = old.model_copy(update={"research": ResearchState(status="investigating", reason="Try a weaker covering", author="second-reader")})
    store.append((current, edge.model_copy(update={"source": current.ref})), expected_revision=1)
    def source(_):
        return retrieval.ledger_source("p", LedgerStore(store.project))
    service = retrieval.ProjectRetriever(retrieval.build_index((source("p"),)), read_source=source)
    result = service.retrieve(retrieval.RetrievalQuery(project_source="p", text=goal.id, scope=scope.ref))
    assert [hit.entry.ref for hit in result.delivered] == [goal.ref, current.ref]
    approach = result.delivered[1].entry
    assert approach.related[0].ref == goal.ref
    assert approach.historical_assessments[0].ref == old.ref
    assert approach.historical_assessments[0].research.reason == "Finite covering is unavailable"
    assert "historical" in approach.summary.lower()
