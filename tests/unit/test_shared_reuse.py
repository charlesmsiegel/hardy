"""The resolver classes what it found; a similar name is never 'solved'."""

from __future__ import annotations

from pdf_helpers import book_pages, build_pdf

from hardy.formal.contracts import EnvironmentIdentity
from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.library import ManagedLibrary
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ArtifactRef, EvidenceRef, ProjectItem, Relation
from hardy.workflows.shared.claims import ClaimLinker, InterpretationProposal, LinkStore
from hardy.workflows.shared.ledger import SharedClaims, shared_store
from hardy.workflows.shared.realizations import (
    FormalRealization,
    RealizationOrigin,
    RealizationStore,
    realization_id,
)
from hardy.workflows.shared.reuse import ReuseClass, ReuseResult, resolve_reusable_claim

ENV = EnvironmentIdentity(lean_version="4.32.0", lean_commit="abc", mathlib_revision="m1", lake_manifest_sha256="1" * 64)
OTHER_ENV = ENV.model_copy(update={"mathlib_revision": "m9"})


def claim(id, name, statement):
    return ProjectItem(id=id, kind="theorem", name=name, origin="background_paper", statement=statement)


def verdict():
    review = FaithfulnessReview(formalization_entails_claim=True, claim_entails_formalization=True)
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="reader", reviewer_backend="test", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED, review=review)


def evidence(subject):
    return EvidenceRef(kind="formal", subject=subject, producer="hardy.formal", artifact=ArtifactRef(uri="verification.json", digest="e" * 64))


def realization(c, origin, declaration, environment=ENV, project=None):
    module = "Mathlib.X" if origin is RealizationOrigin.MATHLIB else "HardyShared.X" if origin is RealizationOrigin.HARDY_SHARED else "Project.X"
    return FormalRealization(id=realization_id(c.ref, origin, module, declaration, environment), claim=c.ref, origin=origin, module=module,
                             declaration=declaration, formal_type="T", environment=environment, imports=(module,), project=project, at="now")


def library(tmp_path):
    root = tmp_path / "library"
    claims = SharedClaims(shared_store(root))
    links = LinkStore(root / "links")
    realizations = RealizationStore(root / "realizations")
    return root, claims, links, realizations


def resolve(query, claims, links, realizations, environment=ENV, importable=lambda r: True, project_results=()):
    return resolve_reusable_claim(query, claims=claims, links=links, realizations=realizations, environment=environment, importable=importable, project_results=project_results)


def test_resolver_distinguishes_exact_related_and_candidate(tmp_path):
    root, claims, links, realizations = library(tmp_path)
    exact = claim("cyclic-subgroups", "Subgroups of cyclic groups are cyclic", "Every subgroup of a cyclic group is cyclic.")
    weaker = claim("cyclic-finite", "Subgroups of finite cyclic groups are cyclic", "Every subgroup of a finite cyclic group is cyclic.")
    family = claim("cyclic-quotients", "Quotients of cyclic groups are cyclic", "Every quotient of a cyclic group is cyclic.")
    claims.add_claim(exact, expected_revision=0)
    claims.add_claim(weaker, expected_revision=1)
    claims.add_claim(family, expected_revision=2)
    claims.relate(Relation(id="gen", kind="generalizes", source=exact.ref, target=weaker.ref), expected_revision=3)
    attached = realizations.attach(realizations.propose(realization(exact, RealizationOrigin.MATHLIB, "Subgroup.isCyclic")).id,
                                   verification=evidence(exact.ref), faithfulness=verdict(), actor="t")
    realizations.propose(realization(exact, RealizationOrigin.HARDY_SHARED, "Hardy.cyclic_candidate"))
    results = resolve("Subgroups of cyclic groups are cyclic", claims, links, realizations)
    classes = [(r.cls, r.claim.id if r.claim else None) for r in results]
    assert classes[0] == (ReuseClass.EXACT_CLAIM_WITH_REALIZATION, "cyclic-subgroups")
    assert results[0].realization == attached and results[0].availability == "importable" and results[0].reusable_now
    assert (ReuseClass.FORMAL_CANDIDATE, "cyclic-subgroups") in classes
    assert any(r.cls is ReuseClass.RELATED_FAMILY for r in results)
    assert not any(r.cls is ReuseClass.RELATED_CLAIM_WITH_RELATION for r in results)  # the weaker claim has no realization of its own
    weaker_results = resolve("Subgroups of finite cyclic groups are cyclic", claims, links, realizations)
    related = next(r for r in weaker_results if r.cls is ReuseClass.RELATED_CLAIM_WITH_RELATION)
    assert related.claim.id == "cyclic-subgroups" and related.relation.kind.value == "generalizes" and "transport" in related.reason
    assert not any(r.reusable_now for r in weaker_results)


def test_resolver_prefers_project_then_mathlib_then_shared_and_flags_stale(tmp_path):
    root, claims, links, realizations = library(tmp_path)
    c = claim("rr", "Riemann-Roch for curves", "RR")
    claims.add_claim(c, expected_revision=0)
    shared = realizations.attach(realizations.propose(realization(c, RealizationOrigin.HARDY_SHARED, "Hardy.rr")).id, verification=evidence(c.ref), faithfulness=verdict(), actor="t")
    mathlib = realizations.attach(realizations.propose(realization(c, RealizationOrigin.MATHLIB, "AlgebraicGeometry.rr", environment=OTHER_ENV)).id,
                                  verification=evidence(c.ref), faithfulness=verdict(), actor="t")
    local = realizations.attach(realizations.propose(realization(c, RealizationOrigin.PROJECT, "Local.rr", project="p1")).id, verification=evidence(c.ref), faithfulness=verdict(), actor="t")
    project = ReuseResult(cls=ReuseClass.PROJECT_ESTABLISHED, claim=c.ref, claim_name=c.name, availability="project", reason="already proved here")
    results = resolve("Riemann-Roch for curves", claims, links, realizations, project_results=(project,))
    assert results[0].cls is ReuseClass.PROJECT_ESTABLISHED
    exact = [r for r in results if r.cls is ReuseClass.EXACT_CLAIM_WITH_REALIZATION]
    assert [r.realization.id for r in exact][0] == shared.id  # importable before stale
    stale = next(r for r in exact if r.realization.id == mathlib.id)
    assert stale.availability == "stale_environment" and not stale.reusable_now
    project_local = next(r for r in exact if r.realization.id == local.id)
    assert project_local.availability == "project_local" and not project_local.reusable_now
    unimportable = resolve("Riemann-Roch for curves", claims, links, realizations, importable=lambda r: False)
    assert all(not r.reusable_now for r in unimportable if r.cls is ReuseClass.EXACT_CLAIM_WITH_REALIZATION)


def test_source_only_claims_and_empty_answers_are_explicit(tmp_path):
    root, claims, links, realizations = library(tmp_path)
    lib = ManagedLibrary(root)
    sha = lib.import_source(ImportRequest(data=build_pdf(book_pages()))).outcome.artifact.sha256
    tree = lib.build_tree(sha)
    theorem = next(n for n in tree.nodes if n.number == "1.2")
    linker = ClaimLinker(library=lib, claims=claims, links=links)
    link = linker.admit(linker.propose(sha, tree.id, theorem.id, InterpretationProposal(
        new_claim=claim("cyclic-subgroups", "Subgroups of cyclic groups are cyclic", "Every subgroup of a cyclic group is cyclic."), interpreter="m")).id, verdict=verdict())
    results = resolve("cyclic-subgroups", claims, links, realizations)
    assert results[0].cls is ReuseClass.EXACT_CLAIM_SOURCE_ONLY and results[0].links == (link.id,) and not results[0].reusable_now
    nothing = resolve("Hodge conjecture", claims, links, realizations)
    assert [r.cls for r in nothing] == [ReuseClass.NONE] and "not evidence" in nothing[0].reason
