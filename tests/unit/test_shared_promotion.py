"""Promotion exports a verified closure, blocks on local assumptions, and never claims success early."""

from __future__ import annotations

import shutil
from pathlib import PurePosixPath

import pytest

from hardy.formal.contracts import EnvironmentIdentity
from hardy.formal.syntax import declarations, module_path
from hardy.workflows.contracts import FaithfulnessOutcome, FaithfulnessReview, FaithfulnessVerdict
from hardy.workflows.ledger.contracts import ProjectItem
from hardy.workflows.shared.claims import LinkStore
from hardy.workflows.shared.ledger import SharedClaims, shared_store
from hardy.workflows.shared.promotion import (
    Promoter,
    PromotionError,
    PromotionRequest,
    PromotionStore,
    compute_closure,
    rewrite_imports,
    shared_head,
)
from hardy.workflows.shared.realizations import RealizationOrigin, RealizationStore
from hardy.workflows.shared.reuse import ReuseClass, resolve_reusable_claim

ENV = EnvironmentIdentity(lean_version="4.32.0", lean_commit="abc", mathlib_revision="m1", lake_manifest_sha256="1" * 64)

LEMMAS = "import Mathlib\n\ntheorem Prym.two_le_three : (2 : Nat) ≤ 3 := by decide\n"
MAIN = "import Mathlib\nimport Prym.Lemmas\n\ntheorem Prym.fibers_bound : (2 : Nat) ≤ 3 := Prym.two_le_three\n"
AXIOMS = "import Mathlib\n\naxiom Prym.local_hypothesis : (1 : Nat) = 1\n"
USES_AXIOM = "import Mathlib\nimport Prym.Axioms\n\ntheorem Prym.conditional : (1 : Nat) = 1 := Prym.local_hypothesis\n"
SOURCES = {"Prym.Lemmas": LEMMAS, "Prym.Main": MAIN}
CLEAN = {"status": "clean", "assumed": []}
AUDIT = {"Prym.Lemmas": CLEAN, "Prym.Main": CLEAN}


def verdict(agreed=True):
    review = FaithfulnessReview(formalization_entails_claim=agreed, claim_entails_formalization=agreed, divergences=() if agreed else ("differs",))
    return FaithfulnessVerdict(claim_sha256="c" * 64, reviewer_model="reader", reviewer_backend="test", reviewer_isolation="tools-refused",
                               prompt_sha256="p" * 64, outcome=FaithfulnessOutcome.AGREED if agreed else FaithfulnessOutcome.DISPUTED, review=review)


def fake_compile(compiled, failing=()):
    def compile(module, source_root, build_root, source_file):
        compiled.append(module)
        if module in failing:
            return False, f"{module}: error: unknown identifier"
        olean = (build_root / PurePosixPath(*module.split("."))).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        olean.write_bytes(b"olean")
        return True, ""

    return compile


def clean_audit(space, targets):
    """A clean audit that names what it established: the declarations of each staged module."""
    found = {}
    for name in targets:
        source = (space.root / module_path(name)).read_text(encoding="utf-8")
        names = [n for kind in ("theorem", "lemma") for n in declarations(source).get(kind, ())]
        found[name] = {"status": "clean", "assumed": [], "declarations": names}
    return found


def setup(tmp_path, *, failing=(), audit=clean_audit):
    root = tmp_path / "library"
    claims = SharedClaims(shared_store(root))
    claim = ProjectItem(id="fibers-bound", kind="theorem", name="Fibers bound", origin="imported_project", statement="2 <= 3")
    claims.add_claim(claim, expected_revision=0)
    compiled: list[str] = []
    promoter = Promoter(shared_root=root / "lean", shared_build=root / ".build" / "lean", compile=fake_compile(compiled, failing), environment=ENV,
                        claims=claims, realizations=RealizationStore(root / "realizations"), promotions=PromotionStore(root / "promotions"), audit=audit)
    request = PromotionRequest(project="prym-1", module="Prym.Main", declaration="Prym.fibers_bound", claim=claim.ref, faithfulness=verdict(),
                               actor="user:c", reason="reused by later Prym projects")
    return root, claims, claim, promoter, request, compiled


def test_closure_promotes_reusable_project_lemma_and_keeps_mathlib_import():
    closure = {e.module: e for e in compute_closure(SOURCES, "Prym.Main", shared_modules=(), audit=AUDIT)}
    assert closure["Mathlib"].disposition == "mathlib_import"
    assert closure["Prym.Lemmas"].disposition == "promote" and closure["Prym.Main"].disposition == "promote"
    shared = {e.module: e for e in compute_closure(SOURCES, "Prym.Main", shared_modules=("Prym.Lemmas",), audit=AUDIT)}
    assert shared["Prym.Lemmas"].disposition == "promote"  # a project module named like a shared one is still the project's own
    foreign = {e.module: e for e in compute_closure({"Prym.Main": "import Other.Thing\ntheorem x : True := trivial\n"}, "Prym.Main", shared_modules=(), audit=AUDIT)}
    assert foreign["Other.Thing"].disposition == "blocked" and "project-specific" in foreign["Other.Thing"].reason
    assert compute_closure(SOURCES, "Prym.Missing", shared_modules=(), audit=AUDIT)[0].disposition == "blocked"


def test_project_local_axiom_blocks_promotion(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    sources = {"Prym.Axioms": AXIOMS, "Prym.Conditional": USES_AXIOM}
    audit = {"Prym.Axioms": {"status": "not established", "assumed": []}, "Prym.Conditional": {"status": "modulo", "assumed": ["Prym.local_hypothesis"]}}
    blocked = promoter.prepare(request.model_copy(update={"module": "Prym.Conditional", "declaration": "Prym.conditional"}), sources, audit)
    assert blocked.status == "blocked"
    kinds = {b.kind for b in blocked.blockers}
    assert "project_local_assumption" in kinds
    with pytest.raises(PromotionError, match="blocked"):
        promoter.promote(blocked.id, request.model_copy(update={"module": "Prym.Conditional", "declaration": "Prym.conditional"}), sources)
    assert not (root / "lean").exists() and promoter.realizations.for_claim(claim.id) == ()
    unverified = promoter.prepare(request, SOURCES, {"Prym.Main": CLEAN})
    assert unverified.status == "blocked" and {b.kind for b in unverified.blockers} == {"unverified"}
    unfaithful = promoter.prepare(request.model_copy(update={"faithfulness": verdict(agreed=False)}), SOURCES, AUDIT)
    assert "unfaithful" in {b.kind for b in unfaithful.blockers}


def test_promotion_builds_in_a_shadow_before_admission_and_publishes_closure(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    assert prepared.status == "prepared"
    assert dict(prepared.shared_modules) == {"Prym.Lemmas": "HardyShared.prym1.Prym.Lemmas", "Prym.Main": "HardyShared.prym1.Prym.Main"}
    admitted = promoter.promote(prepared.id, request, SOURCES)
    assert admitted.status == "admitted" and admitted.realization and admitted.verification
    assert compiled == ["HardyShared.prym1.Prym.Lemmas", "HardyShared.prym1.Prym.Main"]
    main = (root / "lean" / module_path("HardyShared.prym1.Prym.Main")).read_text(encoding="utf-8")
    assert "import HardyShared.prym1.Prym.Lemmas" in main and "import Prym.Lemmas" not in main and "import Mathlib" in main
    assert (root / ".build" / "lean" / "HardyShared" / "prym1" / "Prym" / "Main.olean").exists()
    realization = promoter.realizations.get(admitted.realization)
    assert realization.origin is RealizationOrigin.HARDY_SHARED and realization.status == "attached"
    assert realization.module == "HardyShared.prym1.Prym.Main" and realization.declaration == "Prym.fibers_bound" and realization.globally_importable
    assert realization.verification.artifact.digest == realization.source_sha256
    assert [r.status for r in promoter.promotions.history(prepared.id)] == ["prepared", "admitted"]
    with pytest.raises(PromotionError, match="admitted"):
        promoter.promote(prepared.id, request, SOURCES)


def test_failed_build_leaves_no_shared_module_or_realization(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path, failing=("HardyShared.prym1.Prym.Main",))
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "build_failed" and "unknown identifier" in failed.blockers[-1].detail
    assert not (root / "lean").exists() and not (root / ".build").exists()
    assert promoter.realizations.for_claim(claim.id) == ()
    assert promoter.promotions.get(prepared.id).status == "failed"


def test_failed_audit_in_the_current_environment_is_not_admitted(tmp_path):
    def dirty(space, targets):
        return {name: {"status": "modulo", "assumed": ["Classical.choice_extra"]} for name in targets}

    root, claims, claim, promoter, request, compiled = setup(tmp_path, audit=dirty)
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "audit_failed"
    assert not (root / "lean").exists() and promoter.realizations.for_claim(claim.id) == ()


def test_stale_shared_head_is_refused(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    (root / "lean").mkdir(parents=True)
    (root / "lean" / "Other.lean").write_text("theorem other : True := trivial\n", encoding="utf-8")
    assert shared_head(root / "lean") != prepared.shared_head
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "stale_shared_head"
    assert not (root / "lean" / "HardyShared").exists()


def test_second_project_reuses_promoted_claim_without_new_formalization(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    project_a = tmp_path / "project-a"
    project_a.mkdir()
    (project_a / "Main.lean").write_text(MAIN, encoding="utf-8")
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    admitted = promoter.promote(prepared.id, request, SOURCES)
    shutil.rmtree(project_a)  # project A's workspace is gone; the shared library stands on its own
    results = resolve_reusable_claim("Fibers bound", claims=claims, links=LinkStore(root / "links"), realizations=promoter.realizations, environment=ENV,
                                     importable=lambda r: (root / "lean" / module_path(r.module)).is_file())
    best = results[0]
    assert best.cls is ReuseClass.EXACT_CLAIM_WITH_REALIZATION and best.reusable_now
    assert best.realization.id == admitted.realization and best.realization.module == "HardyShared.prym1.Prym.Main"
    assert "import HardyShared.prym1.Prym.Main" in f"import {best.realization.module}"
    assert compiled.count("HardyShared.prym1.Prym.Main") == 1  # nothing was re-proved or re-translated for project B


def test_rewrite_imports_only_touches_named_modules():
    rewritten = rewrite_imports("import Mathlib\nimport Prym.Lemmas\nimport Prym.LemmasExtra\n", {"Prym.Lemmas": "HardyShared.p.Prym.Lemmas"})
    assert rewritten == "import Mathlib\nimport HardyShared.p.Prym.Lemmas\nimport Prym.LemmasExtra\n"


def test_audit_must_cover_the_promoted_declaration(tmp_path):
    def elsewhere(space, targets):
        return {name: {"status": "clean", "assumed": [], "declarations": ["Prym.two_le_three"]} for name in targets}

    root, claims, claim, promoter, request, compiled = setup(tmp_path, audit=elsewhere)
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "audit_failed" and "does not cover Prym.fibers_bound" in failed.blockers[-1].detail
    assert not (root / "lean").exists() and promoter.realizations.for_claim(claim.id) == ()


def test_a_model_approval_is_blocked_before_anything_is_staged(tmp_path):
    from hardy.workflows.shared.claims import HumanApproval

    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    by_model = request.model_copy(update={"faithfulness": None, "approval": HumanApproval(actor="model:reader", reason="looks right", at="now")})
    blocked = promoter.prepare(by_model, SOURCES, AUDIT)
    assert blocked.status == "blocked" and [b.kind for b in blocked.blockers] == ["unfaithful"] and "not a human approval" in blocked.blockers[0].detail
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    failed = promoter.promote(prepared.id, by_model, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "unfaithful"
    assert compiled == [] and not (root / "lean").exists() and promoter.realizations.for_claim(claim.id) == ()


def test_a_promotion_landing_during_the_build_is_seen_before_the_commit(tmp_path):
    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    inner = promoter._compile

    def racing(module, source_root, build_root, source_file):
        (root / "lean").mkdir(parents=True, exist_ok=True)
        (root / "lean" / "Other.lean").write_text("theorem other : True := trivial\n", encoding="utf-8")
        return inner(module, source_root, build_root, source_file)

    promoter._compile = racing
    prepared = promoter.prepare(request, SOURCES, AUDIT)
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "stale_shared_head" and "during" in failed.blockers[-1].detail
    assert compiled  # the build ran; the head check inside the lock still caught the change
    assert not (root / "lean" / "HardyShared").exists() and not (root / ".build").exists()
    assert promoter.realizations.for_claim(claim.id) == ()


def test_failed_admission_restores_the_shared_tree(tmp_path):
    from hardy.workflows.shared.realizations import RealizationError

    root, claims, claim, promoter, request, compiled = setup(tmp_path)
    (root / "lean").mkdir(parents=True)
    (root / "lean" / "Other.lean").write_text("theorem other : True := trivial\n", encoding="utf-8")
    (root / ".build" / "lean").mkdir(parents=True)
    (root / ".build" / "lean" / "Other.olean").write_bytes(b"old olean")
    prepared = promoter.prepare(request, SOURCES, AUDIT)

    def refuse(*args, **kwargs):
        raise RealizationError("the realization journal refused the attachment")

    promoter.realizations.attach = refuse
    failed = promoter.promote(prepared.id, request, SOURCES)
    assert failed.status == "failed" and failed.blockers[-1].kind == "admission_failed" and "restored" in failed.blockers[-1].detail
    assert not (root / "lean" / "HardyShared").exists()
    assert (root / "lean" / "Other.lean").read_text(encoding="utf-8") == "theorem other : True := trivial\n"
    assert sorted(p.name for p in (root / ".build" / "lean").iterdir()) == ["Other.olean"]
    assert shared_head(root / "lean") == prepared.shared_head
    assert [r.status for r in promoter.realizations.for_claim(claim.id)] == ["rejected"]
