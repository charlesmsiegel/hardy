"""H1 measures discovery/delivery after restart, not model or theorem performance."""
import json

from test_project_retrieval import project

from hardy.workflows import retrieval
from hardy.workflows.context import BindingSpec, ContextManager, DeclarationSpec
from hardy.workflows.ledger.contracts import ProjectItem, Relation, ResearchState
from hardy.workflows.ledger.store import LedgerStore


def measure_reuse(tmp_path):
    store, lemma, scope, authority, _, observed, _, make, base_query = project(tmp_path)
    concept = ProjectItem(id="concept-space", kind="concept", name="Underlying space", origin="human_authored")
    representation = ProjectItem(id="representation-topology", kind="representation", name="Topological encoding",
        origin="generated_local", statement="Only the topology required by the current question is represented.")
    goal = ProjectItem(id="goal-compactness", kind="goal", name="Compactness question", origin="human_authored",
        statement="Determine whether the selected space is compact.",
        research=ResearchState(status="open", reason="No proof is recorded.", author="fixture"))
    blocked = ProjectItem(id="approach-covering", kind="approach", name="Covering argument", origin="generated_local",
        research=ResearchState(status="blocked", reason="A finite cover has not been established.", author="first-reader"))
    edge = Relation(id="covering-goal", kind="pursues", source=blocked.ref, target=goal.ref)
    store.append((concept, representation, goal, blocked, edge,
        Relation(id="interpretation", kind="interprets", source=representation.ref, target=concept.ref)),
        expected_revision=store.read().revision)
    revised = blocked.model_copy(update={"research": ResearchState(status="investigating",
        reason="Try an additional local condition.", author="second-reader")})
    store.append((revised, edge.model_copy(update={"source": revised.ref})), expected_revision=store.read().revision)
    manager = ContextManager(store)
    root = manager.create_root(id="context-root", label="Base context")
    outer = manager.extend(root.ref, id="context-space", label="An arbitrary space", declarations=(
        DeclarationSpec(id="local-X", symbol="X", semantic_type="space"),), bindings=(
        BindingSpec(id="alias-space", kind="alias", symbol="M", meaning="Underlying space", target=concept.ref),))
    inner = manager.extend(outer.ref, id="context-shadowed", label="Alternate interpretation", bindings=(
        BindingSpec(id="alias-representation", kind="alias", symbol="M", meaning="Topological encoding", target=representation.ref),))
    before = store.read()
    # Discard construction-time state: both index and delivery read a new store.
    def source(_):
        return retrieval.ledger_source("project", LedgerStore(store.project))
    index = retrieval.build_index((source("project"),))
    rebuilt = retrieval.ProjectRetrievalIndex.model_validate_json(index.model_dump_json())
    service = make(index=rebuilt, read_source=source)
    cases = (
        ("lemma", lemma.ref, base_query),
        ("concept", concept.ref, base_query.model_copy(update={"text": "M", "context": outer.ref})),
        ("representation", representation.ref, base_query.model_copy(update={"text": representation.id})),
        ("context", outer.ref, base_query.model_copy(update={"text": outer.id})),
        ("goal", goal.ref, base_query.model_copy(update={"text": goal.id})),
        ("approach", revised.ref, base_query.model_copy(update={"text": goal.id})),
    )
    rows = []
    for category, expected, query in cases:
        # The same deterministic consumer sees either no retrieved context or
        # the exact accepted H0 payload. It does not invent a remembered answer.
        for enabled in (False, True):
            result = service.retrieve(query) if enabled else None
            payload = {"retrieval": [] if result is None else
                       [match.model_dump(mode="json") for match in result.delivered]}
            delivered = json.loads(json.dumps(payload))  # The actual visible consumer input.
            refs = [hit["entry"]["ref"] for hit in delivered["retrieval"]]
            target_available = expected.model_dump(mode="json") in refs
            rows.append({"category": category, "retrieval": enabled,
                "target": expected.model_dump(mode="json"), "target_available": target_available,
                "delivered": refs, "characters": 0 if result is None else result.characters_delivered,
                "truncated": False if result is None else result.truncated})
            if result is not None:
                assert result.index_digest == rebuilt.digest
                assert not result.truncated
                if category != "lemma":
                    assert all(hit.formal is None for hit in result.delivered)
                if category == "approach":
                    hit = next(hit for hit in result.delivered if hit.entry.ref == revised.ref)
                    assert hit.entry.related[0].ref == goal.ref
                    assert hit.entry.historical_assessments[0].ref == blocked.ref
                    assert "finite cover" in hit.entry.historical_assessments[0].research.reason
    shadowed = service.retrieve(base_query.model_copy(update={"text": "M", "context": inner.ref}))
    assert [hit.entry.ref for hit in shadowed.delivered] == [representation.ref]
    unavailable = service.retrieve(base_query.model_copy(update={"text": "local-X"}))
    assert not unavailable.delivered and unavailable.rejected
    assert store.read() == before
    # A proof found before revocation cannot be delivered again just because
    # its source/index identity has not changed.
    authority.evidence.clear()
    revoked = service.retrieve(base_query)
    assert not revoked.delivered and revoked.rejected
    return {"schema": "hardy.reuse-fixture/v1", "index_digest": index.digest,
        "sources": [identity.model_dump(mode="json") for identity in index.sources],
        "rows": rows, "categories": len(cases),
        "retrieval_off_available": sum(row["target_available"] for row in rows if not row["retrieval"]),
        "retrieval_on_available": sum(row["target_available"] for row in rows if row["retrieval"]),
        "scoped_alias_correct": True, "out_of_context_local_rejected": True, "revoked_proof_rejected": True,
        "historical_assessment_retained": True, "formal_reader_calls": len(observed),
        "provider_calls": 0, "lean_process_calls": 0, "live_model_performance": "unmeasured",
        "measurement": "Deterministic context discovery and delivery after ledger/index restart; no proof search."}


def test_restart_reuse_delivers_all_six_categories_without_a_separate_memory_store(tmp_path):
    report = measure_reuse(tmp_path)
    assert report["retrieval_off_available"] == 0
    assert report["retrieval_on_available"] == report["categories"] == 6
    assert report["formal_reader_calls"] >= 1
    assert report["live_model_performance"] == "unmeasured"


def test_reuse_measurement_source_identity_is_reproducible_across_fresh_directories(tmp_path):
    first = measure_reuse(tmp_path / "first")
    second = measure_reuse(tmp_path / "second")
    assert first == second
