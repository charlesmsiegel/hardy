"""Interface materialization writes only the selected plan's required closure."""
from hashlib import sha256

import pytest

from hardy.foundation.files import WriteGuard
from hardy.workflows.ledger import contracts as c
from hardy.workflows.ledger.store import LedgerStore
from hardy.workflows.representation import RepresentationModel


def fixture(tmp_path, *, mismatch=False):
    store = LedgerStore(tmp_path)
    representation = c.ProjectItem(id="representation", kind="representation", name="Point carrier",
        statement="A carrier with a selected point", origin="generated_local",
        research=c.ResearchState(status="planned", reason="Only a point is needed"))
    context = c.MathematicalContext(id="context", label="Local context", origin="human_authored")
    if mismatch:
        representation = representation.model_copy(update={"context": context.ref})
    scope = c.Scope(id="scope")
    obligation = c.Obligation(id="interface", kind="construct_interface", item=representation.ref, scope=scope)
    store.append((context, representation, scope, obligation), expected_revision=0)
    return store, representation, obligation


def resolver(plan, writer, **options):
    from hardy.workflows.acquisition.interfaces import InterfaceResolver
    return InterfaceResolver(model=RepresentationModel(provider="fixture", model="test", configuration=()),
        select_plan=lambda snapshot, obligation: plan, write_interface=writer, **options)


def test_required_field_dependency_closure_is_written_without_unused_properties(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionMaterialization
    from hardy.workflows.acquisition.interfaces import InterfaceField, InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=("point",), fields=(
            InterfaceField(name="unused_property", lean_type="True"),
            InterfaceField(name="point", lean_type="carrier", depends_on=("carrier",)),
            InterfaceField(name="carrier", lean_type="Type")))
    def write(request):
        guard = WriteGuard(tmp_path / "formal", create=True)
        guard.write_bytes(request.path, request.source.encode("utf-8"))
        return DefinitionMaterialization(artifacts=(c.ArtifactRef(uri=request.path, digest=request.source_digest),))
    result = resolver(plan, write).resolve(store.read(), obligation)
    source = (tmp_path / "formal" / "PointCarrier.lean").read_text(encoding="utf-8")
    assert source == "import Mathlib\n\nstructure PointCarrier where\n  carrier : Type\n  point : carrier\n"
    assert result.records[0].artifacts[0].digest == sha256(source.encode()).hexdigest()
    assert dict(result.records[0].semantics)["source"] == source
    assert not result.children and not result.evidence
    assert store.read().head(obligation.id).status == "open"


def test_missing_required_field_creates_define_obligation_and_does_not_write(tmp_path):
    from hardy.workflows.acquisition.interfaces import InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=("carrier",), fields=())
    result = resolver(plan, lambda request: pytest.fail("incomplete interface cannot be written")).resolve(store.read(), obligation)
    assert len(result.children) == 1
    assert result.children[0].kind == c.ObligationKind.DEFINE
    assert result.children[0].item == representation.ref
    assert result.children[0].scope == obligation.scope
    assert "carrier" in result.children[0].reason
    assert not result.evidence
    store.append((*result.records, *result.children), expected_revision=store.read().revision)


def test_only_required_fields_expose_child_obligations(tmp_path):
    from hardy.workflows.acquisition.interfaces import (
        InterfaceField,
        InterfacePlan,
        InterfaceRequirement,
    )
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=("carrier",), fields=(
            InterfaceField(name="carrier", lean_type="Type", requirements=(InterfaceRequirement(
                kind="justify_transport", reason="Prove the carrier identification preserves the selected interpretation"),)),
            InterfaceField(name="unused", lean_type="Nat", requirements=(InterfaceRequirement(
                kind="prove", reason="Do not create an unused proof"),))))
    result = resolver(plan, lambda request: pytest.fail("transport unresolved")).resolve(store.read(), obligation)
    assert len(result.children) == 1
    assert result.children[0].kind == c.ObligationKind.JUSTIFY_TRANSPORT
    assert result.children[0].context == obligation.context


@pytest.mark.parametrize("path", ["../Outside.lean", "C:/Outside.lean", "bad.txt"])
def test_interface_paths_cannot_escape_formal_workspace(tmp_path, path):
    from hardy.workflows.acquisition.interfaces import InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    with pytest.raises(ValueError, match="path|workspace|Lean"):
        plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path=path,
            required=(), fields=())
        resolver(plan, lambda request: pytest.fail("invalid path reached writer")).resolve(store.read(), obligation)


def test_dependency_cycle_is_rejected_before_writer(tmp_path):
    from hardy.workflows.acquisition.interfaces import InterfaceField, InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=("a",), fields=(InterfaceField(name="a", lean_type="Nat", depends_on=("b",)),
                                 InterfaceField(name="b", lean_type="Nat", depends_on=("a",))))
    with pytest.raises(ValueError, match="cycle"):
        resolver(plan, lambda request: pytest.fail("cyclic interface reached writer")).resolve(store.read(), obligation)


def test_written_artifact_must_bind_exact_generated_source(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionMaterialization
    from hardy.workflows.acquisition.interfaces import InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=(), fields=())
    with pytest.raises(ValueError, match="artifact|source"):
        resolver(plan, lambda request: DefinitionMaterialization(artifacts=(
            c.ArtifactRef(uri=request.path, digest="a" * 64),))).resolve(store.read(), obligation)


def test_plan_cannot_replace_selected_representation(tmp_path):
    from hardy.workflows.acquisition.interfaces import InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.model_copy(update={"statement": "stronger"}).ref,
        name="PointCarrier", path="PointCarrier.lean", required=(), fields=())
    with pytest.raises(ValueError, match="representation"):
        resolver(plan, lambda request: pytest.fail("different representation reached writer")).resolve(store.read(), obligation)


def test_resuming_prerequisites_reauthenticates_exact_b2_evidence(tmp_path):
    from hardy.workflows.acquisition.definitions import DefinitionMaterialization
    from hardy.workflows.acquisition.interfaces import InterfacePlan, InterfaceRequirement
    from hardy.workflows.ledger.policy import (
        AcceptanceDecision,
        AuthenticatedEvidence,
        LedgerPolicy,
    )
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=(), fields=(), requirements=(InterfaceRequirement(kind="justify_transport", reason="justify interpretation"),))
    result = resolver(plan, lambda request: pytest.fail("prerequisite remains open")).resolve(store.read(), obligation)
    child = result.children[0]
    store.append((*result.records, child), expected_revision=store.read().revision)
    evidence = tuple(c.EvidenceRef(kind=kind, producer="fixture-owner", subject=representation.ref,
        artifact=c.ArtifactRef(uri=kind, digest=digest * 64)) for kind, digest in (("formal", "a"), ("faithfulness", "b")))
    owner_records = {reference: AuthenticatedEvidence(reference=reference, scope=obligation.scope.ref,
        context=obligation.context, outcome=outcome) for reference, outcome in zip(evidence, ("kernel_proof", "faithful"), strict=True)}
    decisions = {}
    policy = LedgerPolicy(read_evidence=owner_records.get, read_decision=decisions.get)
    proposal = c.Resolution(id="transport-resolution", obligation=child.ref, item=representation.ref, evidence=evidence)
    receipt = c.ArtifactRef(uri="decision", digest="c" * 64)
    decisions[receipt] = AcceptanceDecision(proposal=proposal.ref, obligation=child.ref, item=representation.ref,
        scope=obligation.scope.ref, context=obligation.context, policy_digest=policy.digest)
    accepted = policy.accept(store.read(), proposal, receipt)
    completed = child.model_copy(update={"previous": child.ref, "status": c.ObligationStatus.RESOLVED, "resolution": accepted})
    store.append((accepted, completed), expected_revision=store.read().revision, validate=policy.validate)
    unavailable = resolver(plan, lambda request: pytest.fail("serialized acceptance is not authority")).resolve(store.read(), obligation)
    assert unavailable.children == (completed,)
    checked = resolver(plan, lambda request: DefinitionMaterialization(artifacts=(
        c.ArtifactRef(uri=request.path, digest=request.source_digest),)), policy=policy).resolve(store.read(), obligation)
    assert not checked.children
    assert checked.records[0].artifacts


def test_missing_dependency_does_not_invent_a_carrier(tmp_path):
    from hardy.workflows.acquisition.interfaces import InterfaceField, InterfacePlan
    store, representation, obligation = fixture(tmp_path)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=("point",), fields=(InterfaceField(name="point", lean_type="carrier", depends_on=("carrier",)),))
    result = resolver(plan, lambda request: pytest.fail("missing carrier must remain a gap")).resolve(store.read(), obligation)
    assert result.children[0].kind == c.ObligationKind.DEFINE
    assert "carrier" in result.children[0].reason
    assert "source" not in dict(result.records[0].semantics)


def test_mismatched_context_is_rejected_before_writing(tmp_path):
    from hardy.workflows.acquisition.interfaces import InterfacePlan
    store, representation, obligation = fixture(tmp_path, mismatch=True)
    plan = InterfacePlan(representation=representation.ref, name="PointCarrier", path="PointCarrier.lean",
        required=(), fields=())
    with pytest.raises(ValueError, match="context|representation"):
        resolver(plan, lambda request: pytest.fail("mismatched context reached writer")).resolve(store.read(), obligation)
