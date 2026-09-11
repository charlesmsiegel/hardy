"""A Lean declaration realizes a claim only when its exact type was read and its meaning checked."""

from __future__ import annotations

import pytest

from hardy.formal.contracts import EnvironmentIdentity
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef, ProjectItem
from hardy.workflows.shared.claims import HumanApproval
from hardy.workflows.shared.realizations import (
    FormalRealization,
    MathlibResolver,
    RealizationError,
    RealizationOrigin,
    RealizationStore,
    realization_id,
    revalidate,
)

ENV = EnvironmentIdentity(lean_version="4.32.0", lean_commit="abc", mathlib_revision="m1", lake_manifest_sha256="1" * 64)
NEWER = ENV.model_copy(update={"mathlib_revision": "m2"})


def claim(id="cyclic-subgroups", name="Subgroups of cyclic groups are cyclic"):
    return ProjectItem(id=id, kind="theorem", name=name, origin="background_paper", statement="Every subgroup of a cyclic group is cyclic.")


def verdict(agreed=True):
    review = FaithfulnessReview(formalization_entails_claim=agreed, claim_entails_formalization=agreed, divergences=() if agreed else ("wrong quantifier",))
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="reader", reviewer_backend="test", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED if agreed else FaithfulnessOutcome.DISPUTED, review=review)


def formal_evidence(subject, uri="mathlib:m1/Subgroup.isCyclic"):
    return EvidenceRef(kind="formal", subject=subject, producer="hardy.formal.declarations", artifact=ArtifactRef(uri=uri, digest="e" * 64))


def realization(c, *, origin=RealizationOrigin.MATHLIB, module="Mathlib.GroupTheory.SpecificGroups.Cyclic", declaration="Subgroup.isCyclic",
                formal_type="{G : Type} [Group G] [IsCyclic G] (H : Subgroup G) : IsCyclic H", environment=ENV, project=None):
    return FormalRealization(id=realization_id(c.ref, origin, module, declaration, environment), claim=c.ref, origin=origin, module=module,
                             declaration=declaration, formal_type=formal_type, environment=environment, imports=(module,), project=project, at="now")


def test_one_claim_can_have_mathlib_and_shared_realizations(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    mathlib = store.propose(realization(c))
    shared = store.propose(realization(c, origin=RealizationOrigin.HARDY_SHARED, module="HardyShared.Cyclic", declaration="Hardy.Cyclic.subgroup_isCyclic"))
    assert mathlib.id != shared.id
    assert {r.origin for r in store.for_claim(c.id)} == {RealizationOrigin.MATHLIB, RealizationOrigin.HARDY_SHARED}
    for r in (mathlib, shared):
        store.attach(r.id, verification=formal_evidence(c.ref), faithfulness=verdict(), actor="test")
    assert all(r.status == "attached" for r in store.for_claim(c.id))
    assert len(store.for_claim(c.id)) == 2


def test_candidate_matching_records_exact_type_before_attachment(tmp_path):
    c = claim()
    inspected = {}

    def search(query):
        return ("Subgroup.isCyclic", "IsCyclic.subgroup", "Nonexistent.name") if "cyclic" in query.lower() else ()

    def inspect(names):
        inspected["names"] = names
        return {"Subgroup.isCyclic": ("{G : Type} [Group G] [IsCyclic G] (H : Subgroup G) : IsCyclic H", "Mathlib.GroupTheory.SpecificGroups.Cyclic"),
                "IsCyclic.subgroup": ("(H : Subgroup G) : IsCyclic H", "Mathlib.GroupTheory.SpecificGroups.Cyclic")}

    resolver = MathlibResolver(search=search, inspect=inspect, environment=ENV)
    candidates = resolver.candidates(c)
    assert inspected["names"] == ("Subgroup.isCyclic", "IsCyclic.subgroup", "Nonexistent.name")
    assert [r.declaration for r in candidates] == ["Subgroup.isCyclic", "IsCyclic.subgroup"]
    assert all(r.status == "candidate" and r.formal_type and r.environment == ENV and r.verification is None for r in candidates)
    store = RealizationStore(tmp_path / "realizations")
    proposed = store.propose(candidates[0])
    assert store.get(proposed.id).status == "candidate"


def test_formally_valid_but_unfaithful_is_not_attached(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    r = store.propose(realization(c))
    with pytest.raises(RealizationError, match="agreeing"):
        store.attach(r.id, verification=formal_evidence(c.ref), faithfulness=verdict(agreed=False), actor="test")
    with pytest.raises(RealizationError, match="lead"):
        store.attach(r.id, verification=formal_evidence(c.ref), actor="test")
    with pytest.raises(RealizationError, match="names a user"):
        store.attach(r.id, verification=formal_evidence(c.ref), approval=HumanApproval(actor="model:x", reason="looks right", at="now"), actor="test")
    unverified = EvidenceRef(kind="faithfulness", subject=c.ref, producer="reader", artifact=ArtifactRef(uri="x", digest="f" * 64))
    with pytest.raises(RealizationError, match="formal evidence"):
        store.attach(r.id, verification=unverified, faithfulness=verdict(), actor="test")
    assert store.get(r.id).status == "candidate"
    attached = store.attach(r.id, verification=formal_evidence(c.ref), approval=HumanApproval(actor="user:c", reason="read the docstring and type", at="now"), actor="user:c")
    assert attached.status == "attached" and attached.semantically_attached


def test_stale_environment_is_flagged_not_delivered(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    r = store.attach(store.propose(realization(c)).id, verification=formal_evidence(c.ref), faithfulness=verdict(), actor="t")
    assert revalidate(r, environment=ENV, importable=lambda real: True) == "importable"
    assert revalidate(r, environment=NEWER, importable=lambda real: True) == "stale_environment"
    assert revalidate(r, environment=ENV, importable=lambda real: False) == "unimportable"

    def explode(real):
        raise RuntimeError("lake is broken")

    assert revalidate(r, environment=ENV, importable=explode) == "unimportable"
    stale = store.mark(r.id, "stale", reason="Mathlib moved to m2")
    assert stale.status == "stale" and stale.history[-1].startswith("stale:")


def test_project_local_realization_is_not_importable_elsewhere(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    local = store.propose(realization(c, origin=RealizationOrigin.PROJECT, module="Prym.Donagi", declaration="Prym.donagi_fibers", project="prym-1"))
    attached = store.attach(local.id, verification=formal_evidence(c.ref, uri="prym-1/lean/verification.json"), faithfulness=verdict(), actor="t")
    assert attached.status == "attached" and attached.globally_importable is False and attached.project == "prym-1"


def test_meaning_change_creates_new_realization_with_supersession(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    old = store.attach(store.propose(realization(c, origin=RealizationOrigin.HARDY_SHARED, module="HardyShared.Cyclic", declaration="Hardy.cyclic")).id,
                       verification=formal_evidence(c.ref), faithfulness=verdict(), actor="t")
    with pytest.raises(RealizationError, match="new realization"):
        store._append(old.model_copy(update={"formal_type": "changed type"}), precondition=lambda heads: None)
    replacement = realization(c, origin=RealizationOrigin.HARDY_SHARED, module="HardyShared.Cyclic", declaration="Hardy.cyclic_v2", formal_type="stronger type")
    new = store.supersede(old.id, replacement, reason="generalized to all fields")
    assert new.supersedes == old.id and new.id != old.id
    assert store.get(old.id).status == "superseded"
    assert [r.status for r in store.history(old.id)] == ["candidate", "attached", "superseded"]
    with pytest.raises(RealizationError, match="superseded"):
        store.attach(old.id, verification=formal_evidence(c.ref), faithfulness=verdict(), actor="t")
    other = claim("other-claim", "Other")
    with pytest.raises(RealizationError, match="same claim"):
        store.supersede(new.id, realization(other, origin=RealizationOrigin.HARDY_SHARED), reason="x")


def test_restart_preserves_realizations(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c = claim()
    r = store.attach(store.propose(realization(c)).id, verification=formal_evidence(c.ref), faithfulness=verdict(), actor="t")
    assert RealizationStore(tmp_path / "realizations").get(r.id) == r


def test_evidence_about_another_claim_does_not_attach(tmp_path):
    store = RealizationStore(tmp_path / "realizations")
    c, other = claim(), claim(id="other-claim", name="Something else")
    r = store.propose(realization(c))
    with pytest.raises(RealizationError, match="does not transfer"):
        store.attach(r.id, verification=formal_evidence(other.ref), faithfulness=verdict(), actor="test")
    assert store.get(r.id).status == "candidate"
    assert store.attach(r.id, verification=formal_evidence(c.ref), faithfulness=verdict(), actor="test").status == "attached"
