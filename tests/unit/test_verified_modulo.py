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


def _axiom_line(spec, axioms: str) -> str:
    """The report, positioned on Hardy's `#print axioms` line as real Lean puts it."""
    line = Path(spec.argv[-1]).read_text(encoding="utf-8").count("\n")
    return json.dumps(
        {
            "severity": "information",
            "pos": {"line": line, "column": 0},
            "data": f"two_eq_two depends on axioms: [{axioms}]",
        }
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
    domain = importlib.import_module("hardy.workflows.contracts")

    assert domain.FormalStatus.VERIFIED_MODULO.value == "verified_modulo"
    assert domain.FormalStatus.VERIFIED_MODULO is not domain.FormalStatus.KERNEL_VERIFIED


def test_a_kernel_verified_grade_may_not_carry_assumptions(tmp_path) -> None:
    """The whole distinction: `kernel_verified` means Lean's own axioms and
    nothing else, so a grade naming an assumption cannot wear it."""
    domain = importlib.import_module("hardy.workflows.contracts")
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
    domain = importlib.import_module("hardy.workflows.contracts")
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
    domain = importlib.import_module("hardy.workflows.contracts")
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
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)
    seen = {}

    def runner(spec):
        seen["source"] = Path(spec.argv[-1]).read_text(encoding="utf-8")
        return _result(
            process, spec, stdout=_axiom_line(spec, "propext, Papers.perelman.no_local_collapsing")
        )

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified, result.diagnostics
    assert "axiom Papers.perelman.no_local_collapsing : True" in seen["source"]
    assert seen["source"].index("axiom Papers") < seen["source"].index("theorem two_eq_two")


def test_a_proof_using_exactly_the_declared_assumptions_is_verified_modulo(tmp_path) -> None:
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(
            process, spec, stdout=_axiom_line(spec, "propext, Papers.perelman.no_local_collapsing")
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
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line(spec, "propext, Quot.sound"))

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert result.verified
    assert result.assumed == ()


def test_an_axiom_nobody_declared_is_still_refused(tmp_path) -> None:
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line(spec, "propext, Papers.other.smuggled"))

    final = _verifier(
        verifier, domain, claim, tmp_path, runner, allowed=(_assumption(domain),)
    )

    result = final.verify(claim, "by rfl", store)

    assert not result.verified
    assert result.reason is domain.TerminalReason.UNEXPECTED_AXIOM
    assert "Papers.other.smuggled" in " ".join(item.message for item in result.diagnostics)


def test_a_hole_is_refused_however_much_was_declared(tmp_path) -> None:
    """`sorryAx` is not an assumption and no declaration may launder one."""
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(process, spec, stdout=_axiom_line(spec, "propext, sorryAx"))

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
    [
        "True := trivial",
        "True\ntheorem sneaky : False",
        "True #eval dangerous",
        # Every line terminator, not just `\n`. The declaration is rendered on
        # one line, so anything that ends a line starts whatever follows it.
        "True\raxiom sneaky : False",
        "True\u2028axiom sneaky : False",
    ],
)
def test_a_declared_statement_that_is_not_a_type_never_reaches_lean(
    tmp_path, statement
) -> None:
    """The declaration file is written by Hardy into the source the kernel
    checks, so what goes in it is not the run's to choose freely."""
    domain = importlib.import_module("hardy.workflows.contracts")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
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
    domain = importlib.import_module("hardy.workflows.contracts")
    process = importlib.import_module("hardy.foundation.process")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)
    store = _store(storage, tmp_path)

    def runner(spec):
        return _result(
            process, spec, stdout=_axiom_line(spec, "propext, Papers.perelman.no_local_collapsing")
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
    domain = importlib.import_module("hardy.workflows.contracts")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
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
    domain = importlib.import_module("hardy.workflows.contracts")
    storage = importlib.import_module("hardy.workflows.storage")
    verifier = importlib.import_module("hardy.formal.verifier")
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


def test_the_verified_source_is_a_file_lean_will_parse(tmp_path) -> None:
    """Lean 4 admits `import` only in the module header: any command above it
    is `invalid 'import' command`. Rendering the declarations before the
    imports made every `--assume` run fail final verification with a parse
    error, so `verified_modulo` could not be produced at all."""
    domain = importlib.import_module("hardy.workflows.contracts")
    verifier = importlib.import_module("hardy.formal.verifier")
    claim = _claim(domain)

    source = verifier.verification_source(claim, "by rfl", (_assumption(domain),))

    lines = source.splitlines()
    assert lines[0] == "import Mathlib", source
    # Every import first, then the declarations, then the theorem.
    imports = [index for index, line in enumerate(lines) if line.startswith("import ")]
    axiom = next(index for index, line in enumerate(lines) if line.startswith("axiom "))
    theorem = next(index for index, line in enumerate(lines) if line.startswith("theorem "))
    assert max(imports) < axiom < theorem, source


def test_a_declared_assumption_is_in_scope_for_the_loop_that_writes_the_proof(
    tmp_path,
) -> None:
    """The official in-loop check renders the same environment the verifier
    does. Without the declarations a proof citing one got `unknown identifier`
    from every check, so no proof using a declared assumption could ever be
    submitted -- the feature was unusable from both ends."""
    domain = importlib.import_module("hardy.workflows.contracts")
    lean = importlib.import_module("hardy.formal.lean")
    claim = _claim(domain)

    rendered = lean.render_theorem(claim, "by rfl", (_assumption(domain),))

    assert rendered.splitlines()[0] == "import Mathlib"
    assert "axiom Papers.perelman.no_local_collapsing : True" in rendered


# --- An approved assumption's statement, checked by Lean (#188) ---------------------
#
# The textual gate compares an `axiom` the model *wrote* against the approved
# statement. An axiom it did not write -- `run_cmd ... addDecl (.axiomDecl ...)`,
# or one hidden from the scan -- was matched by name alone, so an approval of
# `trusted : P` graded a proof resting on `trusted : False` as `modulo`. The
# audit now asks Lean, one line per approved name the reports carry, whether
# the constant's type is the approved statement, and reads the answer by line.


def _checked(source_lines, *, ok=True, diagnostics=(), **flags):
    lean = importlib.import_module("hardy.formal.lean")
    return lean.LeanToolResult(
        ok, "", "\n".join(source_lines), diagnostics=tuple(diagnostics), **flags
    )


def _error(line, message="Type mismatch"):
    lean = importlib.import_module("hardy.formal.lean")
    return lean.LeanDiagnostic(severity="error", message=message, line=line, column=0)


def test_each_approved_name_is_checked_on_its_own_known_line() -> None:
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    source, lines = formal.statement_checks(
        ["Main", "Helper"], {"trusted": "True", "Papers.smith2020.main": "∀ n : Nat,  n = n"}
    )

    rows = source.splitlines()
    assert rows[:3] == ["import Main", "import Helper", ""]
    assert lines == {4: "trusted", 5: "Papers.smith2020.main"}
    # Fully qualified from the root, so nothing the namespace declares can
    # stand in for the approved constant.
    assert rows[3] == "example : (type_of% @_root_.trusted) = (True) := rfl"
    # A minted axiom was elaborated inside its paper's namespace, where a
    # sibling constant is named by its leaf; the check is elaborated there too.
    assert rows[4] == (
        "namespace Papers.smith2020 "
        "example : (type_of% @_root_.Papers.smith2020.main) = (∀ n : Nat, n = n) := rfl "
        "end Papers.smith2020"
    )


def test_a_statement_that_cannot_sit_on_one_line_is_not_checked() -> None:
    """Which name failed is read off the line an error lands on, so a
    statement that spills onto a second line cannot be checked at all."""
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    refused = formal.statement_checks(["Main"], {"odd": 'f "a\nb" = 1'})

    assert isinstance(refused, str)
    assert "`odd`" in refused and "one line" in refused


def test_a_name_that_is_not_a_lean_name_is_refused_for_that_reason() -> None:
    """Failing closed either way, but the reason given has to be the true one:
    a malformed name is not a statement that spilled onto a second line."""
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    refused = formal.statement_checks(["Main"], {"not a name": "True"})

    assert isinstance(refused, str)
    assert "`not a name`" in refused and "qualified" in refused
    assert "one line" not in refused


def test_a_clean_check_establishes_every_statement() -> None:
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    verdict = formal.judge_statement_checks(_checked(["x"]), {3: "trusted"})

    assert verdict.established
    assert verdict.mismatched == ()


def test_an_error_on_a_check_line_is_a_different_statement() -> None:
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    verdict = formal.judge_statement_checks(
        _checked(["x"], ok=False, diagnostics=[_error(4)]), {3: "fine", 4: "trusted"}
    )

    assert verdict.established
    assert [name for name, _ in verdict.mismatched] == ["trusted"]


@pytest.mark.parametrize(
    "result",
    [
        # Nothing Lean said can be read as an answer: never a pass.
        _checked(["x"], ok=False),
        _checked(["x"], timed_out=True),
        _checked(["x"], interrupted=True),
        _checked(["x"], output_overflow=True),
        _checked(["x"], ok=False, diagnostics=[_error(None)]),
        # An import that failed: Lean never reached the checks.
        _checked(["x"], ok=False, diagnostics=[_error(1, "unknown module prefix")]),
    ],
    ids=["silent-failure", "timeout", "interrupted", "overflow", "unplaced", "import"],
)
def test_no_readable_answer_is_not_established(result) -> None:
    formal = importlib.import_module("hardy.workflows.interactive.formal")

    verdict = formal.judge_statement_checks(result, {3: "trusted"})

    assert not verdict.established
    assert verdict.caveat


def test_a_check_closed_by_sorry_is_not_a_pass() -> None:
    """`sorry` would make a check line clean for a reason that says nothing
    about the constant's type."""
    formal = importlib.import_module("hardy.workflows.interactive.formal")
    lean = importlib.import_module("hardy.formal.lean")
    warning = lean.LeanDiagnostic(
        severity="warning", message="declaration uses 'sorry'", line=3, column=0
    )

    verdict = formal.judge_statement_checks(_checked(["x"], diagnostics=[warning]), {3: "trusted"})

    assert not verdict.established
