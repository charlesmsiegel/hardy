"""The shared ledger is Hardy's ledger, reached only through an authorized retrieval."""

from __future__ import annotations

import json

import pytest

from hardy.formal.contracts import EnvironmentIdentity
from hardy.workflows import retrieval
from hardy.workflows.ledger.contracts import ProjectItem, Relation, Scope
from hardy.workflows.ledger.policy import LedgerPolicy
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.shared.ledger import (
    SHARED_SOURCE_ID,
    SharedClaims,
    SharedLedgerError,
    authorize_shared_read,
    shared_provenance,
    shared_source,
    shared_store,
)

ENV = EnvironmentIdentity(lean_version="4", lean_commit="lean", mathlib_revision="mathlib", lake_manifest_sha256="a" * 64)


def claim(id, name, statement, **extra):
    fields = {"origin": "background_paper", **extra}
    return ProjectItem(id=id, kind="theorem", name=name, statement=statement, **fields)


def test_shared_claims_use_ledger_policy_and_reject_contextual_items(tmp_path):
    claims = SharedClaims(shared_store(tmp_path / "library"))
    rr = claim("rr-divisors", "Riemann-Roch for divisors", "For a divisor D on a smooth projective curve, l(D) - l(K - D) = deg D + 1 - g")
    claims.add_claim(rr, expected_revision=0)
    assert [c.id for c in claims.claims()] == ["rr-divisors"]
    contextual = claim("local", "local claim", "P", context=rr.ref)
    with pytest.raises(ValueError, match="context"):
        claims.add_claim(contextual, expected_revision=1)
    with pytest.raises(SharedLedgerError, match="not a shared claim kind"):
        claims.add_claim(ProjectItem(id="goal", kind="goal", name="goal", origin="background_paper"), expected_revision=1)
    with pytest.raises(SharedLedgerError, match="origin"):
        claims.add_claim(claim("local-origin", "x", "y", origin="target_paper"), expected_revision=1)
    with pytest.raises(ValueError):
        claims.add_claim(rr.model_copy(update={"kind": "lemma"}), expected_revision=1)  # kind cannot change identity
    assert (tmp_path / "library" / "ledger" / "00000000000000000001.json").is_file()


def test_fuzzy_match_cannot_merge_claims(tmp_path):
    claims = SharedClaims(shared_store(tmp_path / "library"))
    divisors = claim("rr-divisors", "Riemann-Roch for divisors", "l(D) - l(K - D) = deg D + 1 - g for divisors on a smooth projective curve",
                     semantics=(("aliases", json.dumps(["RR", "Riemann Roch"])),))
    bundles = claim("rr-bundles", "Riemann-Roch for line bundles", "h0(L) - h1(L) = deg L + 1 - g for line bundles on a smooth projective curve")
    claims.add_claim(divisors, expected_revision=0)
    claims.add_claim(bundles, expected_revision=1)
    hits = claims.search("Riemann-Roch")
    assert [h.match for h in hits] == ["alias", "some words (fuzzy)"] or all(h.rank >= 1 for h in hits)
    assert not any(h.rank == 0 for h in hits)
    exact = claims.search("rr-divisors")
    assert exact[0].rank == 0 and exact[0].ref == divisors.ref
    note = claims.cluster(divisors.ref, (bundles.ref,), reason="both called Riemann-Roch; different objects", expected_revision=2)
    assert dict(note.semantics)["merged"] == "false"
    assert len(claims.claims()) == 2
    assert claims.clusters_for(bundles.ref) == (note,)


def test_stronger_weaker_pair_stays_separate_with_relation(tmp_path):
    claims = SharedClaims(shared_store(tmp_path / "library"))
    weak = claim("rr-curves-C", "Riemann-Roch over C", "Riemann-Roch for curves over the complex numbers")
    strong = claim("rr-curves-k", "Riemann-Roch over any field", "Riemann-Roch for curves over an arbitrary algebraically closed field")
    claims.add_claim(weak, expected_revision=0)
    claims.add_claim(strong, expected_revision=1)
    relation = Relation(id="rr-generalizes", kind="generalizes", source=strong.ref, target=weak.ref, mappings=(("k", "C"),))
    claims.relate(relation, expected_revision=2)
    assert claims.relations_for(weak.ref) == (relation,)
    assert len(claims.claims()) == 2
    with pytest.raises(SharedLedgerError, match="similarity is not equivalence"):
        claims.relate(Relation(id="rr-equiv", kind="equivalent_to", source=strong.ref, target=weak.ref), expected_revision=3)
    with pytest.raises(SharedLedgerError, match="distinct"):
        claims.relate(Relation(id="self", kind="generalizes", source=weak.ref, target=weak.ref), expected_revision=3)


def test_shared_existence_does_not_widen_project_scope(tmp_path):
    library = shared_store(tmp_path / "library")
    claims = SharedClaims(library)
    shared = claim("rr-divisors", "Riemann-Roch for divisors", "the divisor form of Riemann-Roch")
    claims.add_claim(shared, expected_revision=0)
    project = LedgerStore(tmp_path / "project")
    scope = Scope(id="scope")
    project.append((scope,), expected_revision=0)
    snapshot = project.read()
    policy = LedgerPolicy()
    assert policy.premise_allowed(snapshot, shared.ref, scope=scope, context=None) is False
    assert snapshot.current(Scope)[0].allowed_background == ()

    def read_source(id):
        return shared_source(library) if id == SHARED_SOURCE_ID else retrieval.ledger_source("project", LedgerStore(tmp_path / "project"))

    index = retrieval.build_index((shared_source(library), retrieval.ledger_source("project", project)))
    query = retrieval.RetrievalQuery(project_source="project", text="rr-divisors", scope=scope.ref, environment=ENV, source_ids=(SHARED_SOURCE_ID,))
    unauthorized = retrieval.ProjectRetriever(index, read_source=read_source, policies={SHARED_SOURCE_ID: policy})
    result = unauthorized.retrieve(query)
    assert not result.delivered and result.rejected and "not authenticated" in result.rejected[0].reason
    authorized = retrieval.ProjectRetriever(index, read_source=read_source, policies={SHARED_SOURCE_ID: policy},
                                            read_shared=lambda request: authorize_shared_read(request, store=library))
    result = authorized.retrieve(query)
    assert not result.delivered  # found, but a theorem with no authenticated kernel proof is not delivered as verified
    assert result.rejected[0].entry.ref == shared.ref
    assert policy.premise_allowed(project.read(), shared.ref, scope=scope, context=None) is False


def test_authorization_is_bound_to_the_live_ledger_state(tmp_path):
    library = shared_store(tmp_path / "library")
    claims = SharedClaims(library)
    claims.add_claim(claim("c1", "one", "one"), expected_revision=0)
    identity = retrieval.source_identity(shared_source(library))
    scope = Scope(id="scope")
    query = retrieval.RetrievalQuery(project_source="project", text="one", scope=scope.ref)
    request = retrieval.SharedReadRequest(identity, query)
    granted = authorize_shared_read(request, store=library)
    assert granted is not None and granted.project_source == "project" and granted.scope == scope.ref
    assert authorize_shared_read(request, store=library, allowed=lambda project: False) is None
    claims.add_claim(claim("c2", "two", "two"), expected_revision=1)
    assert authorize_shared_read(request, store=library) is None  # the frozen identity no longer matches
    assert shared_provenance(library).locator == "revision:2"
    foreign = retrieval.SharedReadRequest(identity.model_copy(update={"id": "other"}), query)
    assert authorize_shared_read(foreign, store=library) is None


def test_empty_library_is_an_absent_source_not_an_error(tmp_path):
    source = shared_source(shared_store(tmp_path / "library"))
    assert source.status == "absent" and source.kind == "shared_library"
