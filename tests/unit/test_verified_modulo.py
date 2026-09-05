"""A run may declare what it is allowed to stand on, and is graded against it.

Three grades, not two. `kernel_verified` is Lean's own foundations and nothing
else; `verified_modulo` is a proof that used exactly the assumptions the run
declared; and anything reaching for an axiom nobody declared is not verified at
all. The manifest carries the exact set the proof used -- read from `#print
axioms`, never from what was declared -- because a summary is what lets an
assumption disappear between the run and the paper about it.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def _claim(domain):
    proposal = domain.FormalizationProposal(
        restatement="Two equals two.",
        domains=(),
        quantifiers=(),
        assumptions=(),
        interpretation_choices=(),
        theorem_name="two_eq_two",
        binders="",
        proposition="2 = 2",
    )
    environment = domain.EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b",
        mathlib_revision="81a5d257",
        lake_manifest_sha256="b" * 64,
        imports=("Mathlib",),
    )
    return domain.freeze_claim("Two equals two.", proposal, environment, NOW)


def _store(storage, tmp_path):
    return storage.RunStore.create(tmp_path, "modulo", now=NOW, run_id=RUN_ID)


def _result(process, spec, *, stdout="", returncode=0):
    return process.ProcessResult(
        argv=spec.argv,
        cwd=spec.cwd,
        returncode=returncode,
        stdout=stdout,
        stderr="",
        timed_out=False,
        output_overflow=False,
        duration_ms=4,
    )


def _axiom_line(axioms: str) -> str:
    return json.dumps(
        {"severity": "information", "data": f"two_eq_two depends on axioms: [{axioms}]"}
    )


def _assumption(domain, **overrides):
    fields = {
        "name": "Papers.perelman.no_local_collapsing",
        "statement": "True",
        "source": "arXiv:math.DG/0211159v1 (thm:collapse)",
        "justification": "Assumed from the paper; Mathlib has no Ricci flow theory.",
    }
    fields.update(overrides)
    return domain.DeclaredAssumption(**fields)


def _verifier(verifier, domain, claim, tmp_path, runner, allowed=()):
    return verifier.FinalVerifier(
        lake=tmp_path / "lake.exe",
        lean_project=tmp_path / "lean-project",
        environment=claim.environment,
        limits=domain.RunLimits(),
        runner=runner,
        allowed=allowed,
    )


# --- The grade ---------------------------------------------------------------------


def test_verified_modulo_is_its_own_grade() -> None:
    domain = importlib.import_module("hardy.domain")

    assert domain.FormalStatus.VERIFIED_MODULO.value == "verified_modulo"
    assert domain.FormalStatus.VERIFIED_MODULO is not domain.FormalStatus.KERNEL_VERIFIED


def test_a_kernel_verified_grade_may_not_carry_assumptions(tmp_path) -> None:
    """The whole distinction: `kernel_verified` means Lean's own axioms and
    nothing else, so a grade naming an assumption cannot wear it."""
    domain = importlib.import_module("hardy.domain")
    claim = _claim(domain)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256="a" * 64,
        axioms=("propext", "Papers.perelman.no_local_collapsing"),
        toolchain=claim.environment,
    )

    with pytest.raises(ValidationError, match="verified_modulo"):
        domain.Grades(
            formal=domain.FormalStatus.KERNEL_VERIFIED,
            faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
            faithfulness_review=_agreeing(domain, claim),
            verification_sha256=evidence.digest,
            verification_evidence=evidence,
            assumed=("Papers.perelman.no_local_collapsing",),
        )


def test_a_verified_modulo_grade_must_name_what_it_stands_on() -> None:
    """Otherwise it is `kernel_verified` under a name that reads worse, and a
    reader has no idea what the result rests on."""
    domain = importlib.import_module("hardy.domain")
    claim = _claim(domain)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256="a" * 64,
        axioms=("propext",),
        toolchain=claim.environment,
    )

    with pytest.raises(ValidationError, match="assumption"):
        domain.Grades(
            formal=domain.FormalStatus.VERIFIED_MODULO,
            faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
            faithfulness_review=_agreeing(domain, claim),
            verification_sha256=evidence.digest,
            verification_evidence=evidence,
            assumed=(),
        )


def test_the_manifest_lists_the_assumptions_exactly(tmp_path) -> None:
    """Not a count, not a summary: every name, so a downstream reader can
    check each one against the paper it came from."""
    domain = importlib.import_module("hardy.domain")
    claim = _claim(domain)
    evidence = domain.VerificationEvidence(
        claim_sha256=claim.content_hash,
        source_sha256="a" * 64,
        axioms=("propext", "Papers.a.one", "Papers.b.two"),
        toolchain=claim.environment,
    )
    grades = domain.Grades(
        formal=domain.FormalStatus.VERIFIED_MODULO,
        faithfulness=domain.FaithfulnessStatus.USER_APPROVED,
        faithfulness_review=_agreeing(domain, claim),
        verification_sha256=evidence.digest,
        verification_evidence=evidence,
        assumed=("Papers.a.one", "Papers.b.two"),
    )

    payload = grades.model_dump(mode="json")

    assert payload["assumed"] == ["Papers.a.one", "Papers.b.two"]


def _agreeing(domain, claim):
    return domain.FaithfulnessVerdict(
        claim_sha256=claim.content_hash,
        reviewer_model="reader",
        prompt_sha256="c" * 64,
        response_schema_sha256="d" * 64,
        outcome=domain.FaithfulnessOutcome.AGREED,
        review=domain.FaithfulnessReview(
            formalization_entails_claim=True,
            claim_entails_formalization=True,
        ),
    )


# --- The verifier ---------------------------------------------------------------------


def test_a_declared_assumption_is_rendered_into_the_verified_source(tmp_path) -> None:
    """The proof has to be able to use it, and the independent verifier
    rebuilds from the claim rather than from the run's workspace -- so what
    the run declared has to be in the file it elaborates."""
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    seen = {}

    def runner(spec):
        seen["source"] = Path(spec.argv[-1]).read_text(encoding="utf-8")
        return _result(
            process, spec, stdout=_axiom_line("propext, Papers.perelman.no_local_collapsing")
        )

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified, result.diagnostics
    assert "axiom Papers.perelman.no_local_collapsing : True" in seen["source"]
    assert seen["source"].index("axiom Papers") < seen["source"].index("theorem two_eq_two")


def test_a_proof_using_exactly_the_declared_assumptions_is_verified_modulo(tmp_path) -> None:
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(
            process, spec, stdout=_axiom_line("propext, Papers.perelman.no_local_collapsing")
        )

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified
    assert result.assumed == ("Papers.perelman.no_local_collapsing",)


def test_a_proof_that_used_none_of_them_is_kernel_verified(tmp_path) -> None:
    """Declaring an assumption permits it; it does not spend it. A proof that
    turned out not to need the paper is graded on what it used."""
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line("propext, Quot.sound"))

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified
    assert result.assumed == ()


def test_an_axiom_nobody_declared_is_still_refused(tmp_path) -> None:
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line("propext, Papers.other.smuggled"))

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.UNEXPECTED_AXIOM
    assert "Papers.other.smuggled" in " ".join(item.message for item in result.diagnostics)


def test_a_hole_is_refused_however_much_was_declared(tmp_path) -> None:
    """`sorryAx` is not an assumption and no declaration may launder one."""
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line("propext, sorryAx"))

    final = _verifier(
        verifier,
        domain,
        claim,
        tmp_path,
        runner,
        allowed=(_assumption(domain, name="sorryAx"),),
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.UNEXPECTED_AXIOM


@pytest.mark.parametrize(
    "statement",
    ["True := trivial", "True\ntheorem sneaky : False", "True #eval dangerous"],
)
def test_a_declared_statement_that_is_not_a_type_never_reaches_lean(
    tmp_path, statement
) -> None:
    """The declaration file is written by Hardy into the source the kernel
    checks, so what goes in it is not the run's to choose freely."""
    domain = importlib.import_module("hardy.domain")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    final = _verifier(
        verifier,
        domain,
        claim,
        tmp_path,
        lambda spec: pytest.fail("a malformed assumption must not reach Lean"),
        allowed=(_assumption(domain, statement=statement),),
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE


def test_a_comment_in_a_declared_statement_is_not_an_injection(tmp_path) -> None:
    """`strip_comments` exists so a *mention* is not a use. Refusing this
    would be a false positive on an ordinary statement, and a gate whose
    first refusal is of honest input is a gate people learn to work around."""
    domain = importlib.import_module("hardy.domain")
    process = importlib.import_module("hardy.process")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(
            process, spec, stdout=_axiom_line("propext, Papers.perelman.no_local_collapsing")
        )

    final = _verifier(
        verifier,
        domain,
        claim,
        tmp_path,
        runner,
        allowed=(_assumption(domain, statement="True -- as the paper states it"),),
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified, result.diagnostics


def test_a_declared_name_that_is_not_an_identifier_never_reaches_lean(tmp_path) -> None:
    domain = importlib.import_module("hardy.domain")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    final = _verifier(
        verifier,
        domain,
        claim,
        tmp_path,
        lambda spec: pytest.fail("a malformed assumption must not reach Lean"),
        allowed=(_assumption(domain, name="foo : True := by trivial\ntheorem bar"),),
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE


def test_a_declared_assumption_may_not_shadow_the_theorem_being_proved(tmp_path) -> None:
    """Assuming the goal is not a proof of it, and this is the one shape that
    would make every run trivially succeed."""
    domain = importlib.import_module("hardy.domain")
    storage = importlib.import_module("hardy.storage")
    verifier = importlib.import_module("hardy.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    final = _verifier(
        verifier,
        domain,
        claim,
        tmp_path,
        lambda spec: pytest.fail("a self-assuming run must not reach Lean"),
        allowed=(_assumption(domain, name="two_eq_two", statement="2 = 2"),),
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.FORBIDDEN_HOLE
