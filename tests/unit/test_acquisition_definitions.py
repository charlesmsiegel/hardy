"""Definition proposals prefer realizations; no proposal grants trust."""
import pytest

from hardy.workflows.acquisition.contracts import SearchMatch
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore


def fixture(tmp_path, *, must_prove=False):
    store = LedgerStore(tmp_path)
    item = c.ProjectItem(id="object", kind="definition", name="Object", origin="human_authored")
    scope = c.Scope(id="scope", must_prove=(item.ref,) if must_prove else ())
    obligation = c.Obligation(id="define-object", item=item.ref, kind="define", scope=scope)
    store.append((item, scope, obligation), expected_revision=0)
    return store, obligation


def operations(**overrides):
    from hardy.workflows.acquisition.contracts import SearchRecord
    from hardy.workflows.acquisition.definitions import DefinitionResolver
    from hardy.workflows.representation import RepresentationModel
    values = dict(
        model=RepresentationModel(provider="fixture", model="test", configuration=()),
        search_local=lambda q: SearchRecord(source="local", query=q.item.name, hits=(), complete=True),
        search_mathlib=lambda q: SearchRecord(source="mathlib", query=q.item.name, hits=(), complete=True),
        select_mapping=lambda q: None,
        create_local=lambda q: None,
        propose_opaque=lambda q: None,
        materialize=lambda q, candidate: pytest.fail("unexpected materialization"),
    )
    values.update(overrides)
    return DefinitionResolver(**values)


def test_mathlib_mapping_precedes_local_creation_and_is_only_a_proposal(tmp_path):
    from hardy.workflows.acquisition.contracts import SearchRecord
    from hardy.workflows.acquisition.definitions import DefinitionMapping, DefinitionMaterialization
    store, obligation = fixture(tmp_path)
    hit = SearchMatch(name="Nat", description="Natural number carrier")
    formal = c.ArtifactRef(uri="lean:Mapping.lean", digest="a" * 64)
    resolver = operations(
        search_mathlib=lambda q: SearchRecord(source="mathlib", query="Object", hits=(hit,), complete=True),
        select_mapping=lambda q: DefinitionMapping(source="mathlib", hit=hit,
            reason="The requested carrier and operations match Nat"),
        create_local=lambda q: pytest.fail("Mathlib should be preferred"),
        materialize=lambda q, candidate: DefinitionMaterialization(artifacts=(formal,)),
    )
    result = resolver.resolve(store.read(), obligation)
    assert result.records[0].research.status == "proposed"
    assert dict(result.records[0].semantics)["method"] == "mathlib"
    assert result.records[0].artifacts == (formal,)
    assert not result.evidence
    assert store.read().head(obligation.id) == obligation
    assert all(not isinstance(record, c.Resolution) for record in result.records)
    assert "model" in dict(result.records[0].semantics)


def test_real_local_definition_precedes_opaque_and_rejects_hidden_axioms(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionMaterialization, LocalDefinition
    store, obligation = fixture(tmp_path)
    candidate = LocalDefinition(name="Carrier", lean_type="Type", body="Nat", reason="A concrete carrier suffices")
    result = operations(create_local=lambda q: candidate,
        materialize=lambda q, c: DefinitionMaterialization(),
        propose_opaque=lambda q: pytest.fail("local definition succeeded")).resolve(store.read(), obligation)
    assert dict(result.records[0].semantics)["method"] == "local_definition"
    with pytest.raises(ValueError, match="single|command|assumption"):
        LocalDefinition(name="Carrier", lean_type="Type", body="Nat\naxiom secret : False", reason="bad")


def test_incomplete_search_stops_opaque_and_reports_search_failure(tmp_path):
    from hardy.workflows.acquisition.contracts import SearchRecord
    store, obligation = fixture(tmp_path)
    result = operations(search_mathlib=lambda q: SearchRecord(source="mathlib", query="Object", hits=(), complete=False),
        propose_opaque=lambda q: pytest.fail("incomplete search cannot justify opacity")).resolve(store.read(), obligation)
    assert "search" in result.detail.lower()
    assert not result.evidence


def test_opaque_exposes_carrier_and_all_properties_without_admitting_them(tmp_path):
    from types import SimpleNamespace

    from hardy.formal.lean import LeanDiagnostic
    from hardy.workflows.acquisition.definitions import CharacterizingAssumption, OpaqueDefinition
    from hardy.workflows.admission import ProbeOperations
    store, obligation = fixture(tmp_path)
    candidate = OpaqueDefinition(name="Carrier", lean_type="Type", reason="No computable realization available",
        assumptions=(CharacterizingAssumption(name="carrier_law", statement="∀ x : Nat, x + 0 = x"),))
    # A3's elaboration operation returns real typed diagnostics for each attempted proof.
    def elaborate(source):
        return SimpleNamespace(ok=False, output="unsolved goals", diagnostics=tuple(LeanDiagnostic(severity="error", message="unsolved goals", line=i)
            for i, line in enumerate(source.splitlines(), 1) if line.startswith("example")))
    resolver = operations(propose_opaque=lambda q: candidate,
        probes=ProbeOperations(elaborate=elaborate, refute=elaborate))
    result = resolver.resolve(store.read(), obligation)
    assumptions = [record for record in result.records if isinstance(record, c.ProjectItem) and record.id != result.records[0].id]
    assert len(assumptions) == 2
    assert {child.kind for child in result.children} == {c.ObligationKind.DEFINE, c.ObligationKind.PROVE}
    assert all(child.scope == obligation.scope for child in result.children)
    assert not result.evidence
    assert not any(isinstance(record, c.Scope) for record in result.records)
    store.append((*result.records, *result.children), expected_revision=store.read().revision)
    assert store.read().head("scope").allowed_interfaces == ()


def test_mapping_must_be_an_exact_searched_candidate(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionMapping
    store, obligation = fixture(tmp_path)
    resolver = operations(select_mapping=lambda q: DefinitionMapping(source="mathlib",
        hit=SearchMatch(name="Invented", description="never searched"), reason="guess"))
    with pytest.raises(ValueError, match="searched"):
        resolver.resolve(store.read(), obligation)


def test_failed_real_materialization_is_recorded_before_opaque_proposal(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionUnavailable, LocalDefinition
    store, obligation = fixture(tmp_path)
    def materialize(query, candidate):
        raise DefinitionUnavailable("Lean could not elaborate the local body")
    def opaque(options):
        assert options.local.complete and options.mathlib.complete
        assert "could not elaborate" in options.local_failure
        return None
    result = operations(create_local=lambda q: LocalDefinition(name="Carrier", lean_type="Type",
        body="Nat", reason="concrete"), materialize=materialize, propose_opaque=opaque).resolve(store.read(), obligation)
    assert "could not elaborate" in dict(result.records[0].semantics)["local-failure"]


def test_must_prove_definition_never_becomes_an_opaque_assumption(tmp_path):
    from hardy.workflows.acquisition.definitions import OpaqueDefinition
    store, obligation = fixture(tmp_path, must_prove=True)
    result = operations(propose_opaque=lambda q: OpaqueDefinition(name="Carrier", lean_type="Type", reason="guess")).resolve(store.read(), obligation)
    assert "must prove" in result.detail
    assert not result.children


@pytest.mark.parametrize("forged", [True, False])
def test_forged_materialization_boolean_is_not_evidence(tmp_path, forged):
    from hardy.workflows.acquisition.definitions import LocalDefinition
    store, obligation = fixture(tmp_path)
    resolver = operations(create_local=lambda q: LocalDefinition(name="Carrier", lean_type="Type", body="Nat", reason="body"),
        materialize=lambda q, c: forged)
    with pytest.raises((ValueError, TypeError, AttributeError)):
        resolver.resolve(store.read(), obligation)


def test_unavailable_selected_mapping_falls_back_to_real_local_definition(tmp_path):
    from hardy.workflows.acquisition.contracts import SearchRecord
    from hardy.workflows.acquisition.definitions import (
        DefinitionMapping,
        DefinitionMaterialization,
        DefinitionUnavailable,
        LocalDefinition,
    )
    store, obligation = fixture(tmp_path)
    hit = SearchMatch(name="Nat", description="candidate carrier")
    def materialize(query, candidate):
        if isinstance(candidate, DefinitionMapping):
            raise DefinitionUnavailable("Mapping did not elaborate at the requested type")
        return DefinitionMaterialization()
    result = operations(search_mathlib=lambda q: SearchRecord(source="mathlib", query="Object", hits=(hit,)),
        select_mapping=lambda q: DefinitionMapping(source="mathlib", hit=hit, reason="candidate"),
        create_local=lambda q: LocalDefinition(name="Carrier", lean_type="Type", body="Nat", reason="real body"),
        materialize=materialize).resolve(store.read(), obligation)
    semantics = dict(result.records[0].semantics)
    assert semantics["method"] == "local_definition"
    assert "did not elaborate" in semantics["mapping-failure"]


@pytest.mark.parametrize("contradictory", [False, True])
def test_opaque_properties_are_probed_over_local_carrier_without_assuming_other_laws(tmp_path, contradictory):
    from types import SimpleNamespace

    from hardy.formal.lean import LeanDiagnostic
    from hardy.workflows.acquisition.definitions import CharacterizingAssumption, OpaqueDefinition
    from hardy.workflows.admission import ProbeOperations

    store, obligation = fixture(tmp_path)
    candidate = OpaqueDefinition(name="Carrier", lean_type="Type", reason="Abstract nonempty carrier",
        assumptions=(CharacterizingAssumption(name="nonempty", statement="Nonempty Carrier"),
                     CharacterizingAssumption(name="law", statement=(
                         "False ∧ Nonempty Carrier" if contradictory else "∀ x y : Carrier, x = y"))))
    sources = []

    def elaborate(source):
        sources.append(source)
        lines = source.splitlines()
        scoped = lines[1] == "variable (Carrier : Type)"
        negation = any(line.startswith("example : ¬") for line in lines)
        disproved = scoped and negation and "False ∧ Nonempty Carrier" in source
        errors = tuple(LeanDiagnostic(severity="error", line=i,
            message="unsolved goals" if scoped else "unknown identifier Carrier")
            for i, line in enumerate(lines, 1)
            if (not scoped and "Carrier" in line)
            or (scoped and line.startswith("example") and "by sorry" not in line and not disproved))
        return SimpleNamespace(ok=not errors, diagnostics=errors,
                               output="unsolved goals" if scoped else "unknown identifier Carrier")

    result = operations(propose_opaque=lambda options: candidate,
                        probes=ProbeOperations(elaborate=elaborate, refute=elaborate)).resolve(
                            store.read(), obligation)

    assert sources and all(source.splitlines()[1] == "variable (Carrier : Type)" for source in sources)
    assert all("axiom Carrier" not in source for source in sources)
    assert all("axiom nonempty" not in source for source in sources if "law :" in source)
    assert not result.evidence
    if contradictory:
        assert not result.children
    else:
        assert len(result.children) == 3
        for record in result.records[1:]:
            assert "local parameter" in dict(record.semantics)["admission-check"]
