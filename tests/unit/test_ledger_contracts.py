"""A0 acceptance records: schema consistency is distinct from B2 evidence policy."""
from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError


@pytest.fixture
def c():
    # Import within the fixture so the first red run reports the missing feature.
    try:
        return importlib.import_module("hardy.workflows.ledger.contracts")
    except ModuleNotFoundError:
        pytest.fail("A0 ledger contracts are not implemented")


def ref(c, name, digest="a" * 64):
    return c.VersionRef(id=name, digest=digest)


def item(c, name, kind="theorem", **kwargs):
    return c.ProjectItem(id=name, kind=kind, name=name,
                         origin="human_authored", **kwargs)


def scope(c):
    return c.Scope(id="scope", must_prove=(ref(c, "main"),),
                   allowed_background=(ref(c, "external"),))


def obligation(c, **kwargs):
    if kwargs.get('resolution') is not None:
        kwargs.setdefault('previous', obligation(c).ref)
    return c.Obligation(id="proof", item=ref(c, "main"),
                        kind="prove", scope=scope(c), **kwargs)


def resolution(c, **kwargs):
    return c.Resolution(id="attempt",
                        obligation=obligation(c).ref, item=ref(c, "main"), **kwargs)


def test_fixture_preserves_dependency_and_publication_versions(c):
    items = [item(c, name, kind) for name, kind in (
        ("main", "theorem"), ("lemma", "lemma"), ("definition", "definition"),
        ("example", "example"), ("prose", "exposition"), ("external", "external_result"))]
    by_id = {value.id: value for value in items}
    edges = tuple(c.Relation(id=f"r{n}", kind=kind,
                            source=by_id[source].ref, target=by_id[target].ref)
                  for n, (source, kind, target) in enumerate((
                      ("main", "depends_on", "lemma"),
                      ("lemma", "depends_on", "definition"),
                      ("example", "illustrates", "main"),
                      ("prose", "documents", "main"), ("main", "uses", "external"))))
    repaired = item(c, "main", statement="The original theorem, with all hypotheses.")
    changed = c.ProjectItem.model_validate({**repaired.model_dump(), "statement": "A repaired theorem."})
    repair = c.Obligation(id="repair", item=ref(c, "main"),
                         kind="repair", scope=scope(c))
    assert edges[3].target == by_id["main"].ref
    assert edges[3].target.digest != changed.digest
    assert repaired.statement == "The original theorem, with all hypotheses."
    assert repair.status == "open"
    assert scope(c).must_prove != scope(c).allowed_background
    assert c.ProjectItem.model_validate_json(items[0].model_dump_json()) == items[0]


def test_context_extension_alias_and_multiple_representations_preserve_identity(c):
    declaration = item(c, "X", "declaration", declaration=c.DeclarationDetails(
        context_id="C0", symbol="X", semantic_type="SmoothManifold", role="arbitrary"))
    function = item(c, "f", "declaration", declaration=c.DeclarationDetails(
        context_id="C0", symbol="f", semantic_type="X to real, smooth", role="arbitrary",
        dependencies=(declaration.ref,)))
    alias = c.ScopedBinding(id="M", context_id="C0", kind="alias", symbol="M",
                            meaning="Write M for X", target=declaration.ref)
    c0 = c.MathematicalContext(id="C0", label="Smooth setup", origin="human_authored",
                              declarations=[declaration.ref, function.ref], bindings=[alias.ref])
    before = c0.model_dump_json()
    compact = item(c, "compact", "declaration", declaration=c.DeclarationDetails(
        context_id="C1", symbol="h", semantic_type="X is compact", role="local_hypothesis",
        dependencies=(declaration.ref,)))
    c1 = c.MathematicalContext(id="C1", parent=c0.ref, label="Compact case",
                              origin="human_authored", declarations=(compact.ref,))
    representations = tuple(c.Relation(id=name, kind="interprets",
                                       source=ref(c, name), target=ref(c, "SmoothManifold"))
                            for name in ("Charts", "Atlas"))
    claims = (item(c, "ClaimA", context=c0.ref), item(c, "ClaimB", context=c1.ref))
    assert c0.model_dump_json() == before
    assert c1.parent == c0.ref and isinstance(c0.declarations, tuple)
    assert alias.target == declaration.ref
    assert c0.declarations == (declaration.ref, function.ref)
    assert compact.declaration.role == "local_hypothesis"
    assert compact.id not in {r.id for r in scope(c).allowed_background}
    assert representations[0].target == representations[1].target
    assert claims[0].context != claims[1].context
    with pytest.raises(ValidationError):
        compact.declaration.semantic_type = "True"

def test_research_state_and_unjustified_transport_are_not_a_resolution(c):
    conjecture = item(c, "C", "conjecture", statement="Z is irreducible",
                      research=c.ResearchState(status="unproved"))
    question = item(c, "Q", "question", statement="Is Z irreducible?")
    goal = item(c, "G", "goal")
    blocked = item(c, "A1", "approach", research=c.ResearchState(
        status="blocked", reason="Loses polarization data", author="model:recorded-model"))
    promising = item(c, "A2", "approach", research=c.ResearchState(status="promising"))
    edges = tuple(c.Relation(id=f"e{n}", kind=kind,
                            source=ref(c, source), target=ref(c, target))
                  for n, (source, kind, target) in enumerate((
                      ("G", "targets", "C"), ("A1", "pursues", "G"),
                      ("A2", "pursues", "G"), ("A1", "blocked_by", "obstruction"),
                      ("Xnormal", "equivalent_to", "X"))))
    c2 = c.MathematicalContext(id="C2", parent=ref(c, "C0"),
                              label="Normalized coordinates", origin="generated_local",
                              declarations=(ref(c, "Xnormal"),))
    transport = c.Obligation(id="J", item=ref(c, "G"),
                            kind="justify_transport", scope=scope(c), context=ref(c, "C2"))
    proposed = resolution(c, outstanding=(ref(c, "J"),))
    assert not conjecture.evidence and not question.evidence and not goal.evidence
    assert blocked.research.status == "blocked" and promising.research.status == "promising"
    assert edges[-1].target.id == "X" and c2.parent.id == "C0"
    assert transport.status == "open" and obligation(c, resolution=proposed).status == "open"
    with pytest.raises(ValidationError, match="acceptance"):
        obligation(c, status="resolved", resolution=proposed)


@pytest.mark.parametrize("value", ["", " ", "two words", "../file", "name\n"])
def test_rejects_malformed_stable_identity(c, value):
    with pytest.raises(ValidationError):
        ref(c, value)


@pytest.mark.parametrize("value", ["", "a" * 63, "z" * 64, "A" * 64])
def test_rejects_noncanonical_digest(c, value):
    with pytest.raises(ValidationError):
        ref(c, "valid", value)


def test_rejects_inconsistent_declarations_and_aliases(c):
    details = c.DeclarationDetails(context_id="C0", symbol="X", semantic_type="Manifold", role="arbitrary")
    for kwargs in ({}, {"declaration": details, "context": ref(c, "wrong")}, {"context": ref(c, "C0")}):
        with pytest.raises(ValidationError):
            item(c, "X", "declaration", **kwargs)
    with pytest.raises(ValidationError):
        item(c, "X", "theorem", declaration=details)
    with pytest.raises(ValidationError):
        c.DeclarationDetails(context_id="C0", symbol="x", semantic_type="P x", role="chosen")
    chosen = c.DeclarationDetails(context_id="C0", symbol="x", semantic_type="P x", role="chosen",
                                  justification=ref(c, "existence-obligation"))
    assert chosen.justification.id == "existence-obligation"
    with pytest.raises(ValidationError):
        c.ScopedBinding(id="alias", context_id="C0",
                        kind="alias", symbol="M", meaning="M means X")
    with pytest.raises(ValidationError):
        item(c, "A", "approach", research=c.ResearchState(status="blocked"))


def test_rejects_conflicting_scope_and_duplicate_or_self_context_entries(c):
    with pytest.raises(ValidationError):
        c.Scope(id="scope", must_prove=(ref(c, "main"),),
                allowed_background=(ref(c, "main", "b" * 64),))
    for kwargs in ({"parent": ref(c, "C0")},
                   {"declarations": (ref(c, "X"), ref(c, "X", "b" * 64))}):
        with pytest.raises(ValidationError):
            c.MathematicalContext(id="C0", label="context",
                                  origin="human_authored", **kwargs)


def test_resolution_records_require_exact_target_and_policy_provenance(c):
    receipt = c.ArtifactRef(uri="ledger/decisions/acceptance.json", digest="b" * 64)
    proposed = resolution(c)
    with pytest.raises(ValidationError, match="acceptance"):
        obligation(c, status="resolved", resolution=proposed)
    for field in ("obligation", "item"):
        wrong = c.Resolution.model_validate({**proposed.model_dump(),
                                            field: ref(c, "other").model_dump()})
        with pytest.raises(ValidationError, match="identity"):
            obligation(c, resolution=wrong)
    with pytest.raises(ValidationError):
        resolution(c, accepted_by=receipt)
    accepted = resolution(c, accepted_by=receipt, policy_digest="c" * 64)
    # This is a recorded B2 decision, not proof that the named policy ever ran.
    closed = obligation(c, status="resolved", resolution=accepted)
    assert closed.status == "resolved"
    assert closed.previous == obligation(c).ref
    assert closed.ref != closed.previous
    with pytest.raises(ValidationError, match="outstanding"):
        resolution(c, accepted_by=receipt, policy_digest="c" * 64,
                   outstanding=(ref(c, "J"),))


def test_evidence_and_citations_pin_sources_and_hypothesis_gaps(c):
    source = c.ArtifactRef(uri="papers/1234.5678v2/main.tex", digest="d" * 64,
                           locator="Theorem 7, lines 42-57")
    evidence = c.EvidenceRef(kind="literature", artifact=source, subject=ref(c, "external"),
                             producer="literature/source-inventory")
    citation = c.CitationContract(id="cite7",
                                  use_site=ref(c, "main"), required_claim=ref(c, "needed"),
                                  paper_id="1234.5678", paper_version="v2", source_statement=source,
                                  source_hypotheses=("X is compact",),
                                  hypothesis_mapping=(c.HypothesisMapping(
                                      hypothesis="X is compact", obligation=ref(c, "compactness")),),
                                  conclusion="A finite subcover exists", formal_declaration="Papers.T7",
                                  evidence=(evidence,))
    restored = c.CitationContract.model_validate_json(citation.model_dump_json())
    assert restored.source_statement.digest == "d" * 64
    assert restored.hypothesis_mapping[0].obligation.id == "compactness"
    assert restored.status == "open"
    with pytest.raises(ValidationError):
        c.EvidenceRef(kind="model_proposal", artifact=source, subject=ref(c, "main"),
                      producer="model")
    with pytest.raises(ValidationError):
        c.CitationContract.model_validate({**citation.model_dump(), "source_hypotheses": ()})


def test_public_api_exports_required_contracts(c):
    package = importlib.import_module("hardy.workflows.ledger")
    for name in ("ProjectItem", "ProjectItemKind", "ProjectOrigin", "MathematicalContext",
                 "ScopedBinding", "Obligation", "ObligationKind", "ObligationStatus", "Relation",
                 "RelationKind", "Scope", "Resolution", "EvidenceRef", "ArtifactRef", "CitationContract"):
        assert getattr(package, name) is getattr(c, name)


def test_project_owned_identity_is_derived_from_all_semantic_content(c):
    original = item(c, "claim", statement="Every X has P")
    changed = c.ProjectItem.model_validate({**original.model_dump(), "statement": "Some X has P"})
    restored = c.ProjectItem.model_validate_json(original.model_dump_json())
    assert original.ref.id == changed.ref.id
    assert original.ref.digest != changed.ref.digest
    assert restored.ref == original.ref
    with pytest.raises(ValidationError):
        c.ProjectItem.model_validate({**changed.model_dump(), "digest": original.digest})


def test_publication_and_context_changes_are_independent_versioned_state(c):
    original = item(c, "main", publication_visibility="public", publication_role="main")
    hidden = c.ProjectItem.model_validate({**original.model_dump(), "publication_visibility": "omitted"})
    assert original.ref != hidden.ref
    assert original.publication_visibility == "public"
    c0 = c.MathematicalContext(id="C0", label="original context", origin="human_authored")
    fork = c.MathematicalContext(id="C1", label="stronger context", origin="human_authored",
                                parent=c0.ref, declarations=(ref(c, "hypothesis"),))
    claim = item(c, "claim", context=c0.ref)
    assert fork.ref != c0.ref and claim.context == c0.ref
    original_binding = c.ScopedBinding(id="alias", context_id="C0", kind="alias", symbol="X",
                                       meaning="X names the original object", target=ref(c, "object1"))
    shadow = c.ScopedBinding.model_validate({**original_binding.model_dump(),
                                            "context_id": "C1", "target": ref(c, "object2").model_dump()})
    assert shadow.ref != original_binding.ref and original_binding.target.id == "object1"


def test_resolution_rejects_missing_predecessor_and_same_id_wrong_version(c):
    prior = obligation(c)
    proposed = resolution(c)
    with pytest.raises(ValidationError, match="identity"):
        c.Obligation.model_validate({**prior.model_dump(), "resolution": proposed.model_dump()})
    for field, name in (("item", "main"), ("obligation", "proof")):
        wrong = c.Resolution.model_validate({**proposed.model_dump(),
                                            field: ref(c, name, "f" * 64).model_dump()})
        with pytest.raises(ValidationError, match="identity"):
            obligation(c, resolution=wrong)
    with pytest.raises(ValidationError, match="stable ID"):
        c.Obligation.model_validate({**prior.model_dump(), "previous": ref(c, "other").model_dump()})
    with pytest.raises(ValidationError, match="acceptance"):
        obligation(c, status="resolved")


def test_canonical_record_digest_uses_versioned_schema_and_payload(c):
    import hashlib
    import json

    value = c.ScopedBinding(id="alias", context_id="C0", kind="alias", symbol="M",
                            meaning="M means X", target=ref(c, "X"))
    payload = {"schema": "hardy.ledger/ScopedBinding/v1", "value": {
        "id": "alias", "context_id": "C0", "kind": "alias", "symbol": "M",
        "meaning": "M means X", "target": {"id": "X", "digest": "a" * 64},
    }}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()
    assert value.digest == expected
