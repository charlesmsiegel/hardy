"""The axiom audit, against a real Lean rather than a stand-in.

The hermetic suite proves the parser. It cannot prove that Lean still *emits*
what the parser expects, and the whole gate rests on two sentences of Lean's
output. These run only with a real toolchain present, which is the point: they
are the tests that would catch a report whose wording moved.

They ask for core Lean alone, so a bare Lake project is enough to run them and
CI can provision one in seconds. Written against the `Mathlib` a request
defaults to, they skipped everywhere -- including in CI -- and a skipped test
reads as coverage while proving nothing.
"""

import shutil
from pathlib import Path

import pytest

from hardy.audit import classify, parse
from hardy.lean import LeanTools
from hardy.models import Request

ROOT = Path(__file__).parents[2]
LEAN_PROJECT = ROOT / 'lean_project'


def _tools(declaration: str) -> LeanTools:
    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    if not (LEAN_PROJECT / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    # `Init` rather than the `Mathlib` a request defaults to. What these probe is
    # the wording of Lean's own `#print axioms`, and every declaration below is
    # core: `rfl`, `Classical.choice`, `sorryAx`. Requiring Mathlib would mean a
    # multi-gigabyte download before any of it could run, which is how these came
    # to skip everywhere and prove nothing. A Mathlib-backed run would answer a
    # different question -- whether importing it changes what is reported -- and
    # belongs in its own, much heavier job.
    request = Request.from_dict(
        {'declaration': declaration, 'informal_claim': 'a claim', 'imports': ['Init']}
    )
    return LeanTools(
        request, (lake, 'env', 'lean'), timeout=120, project=LEAN_PROJECT
    )


@pytest.mark.real_toolchain
def test_real_lean_reports_no_axioms_in_the_form_the_parser_expects() -> None:
    tools = _tools('theorem HardyTarget : 2 = 2')
    result = tools.check_proof('by rfl', final=True)

    assert result.ok, result.output
    reports = parse(result.report, ('HardyTarget',))
    assert reports is not None, result.report
    assert reports[0].axioms == ()
    assert classify(reports, ()).status == 'clean'


@pytest.mark.real_toolchain
def test_real_lean_names_a_standard_axiom_the_proof_actually_used() -> None:
    """A proof that reaches for choice must report it, or the allowlist is
    checking something the kernel is not."""
    tools = _tools('theorem HardyTarget (h : Nonempty Nat) : Nat')
    result = tools.check_proof('Classical.choice h', final=True)

    assert result.ok, result.output
    reports = parse(result.report, ('HardyTarget',))
    assert reports is not None, result.report
    assert 'Classical.choice' in reports[0].axioms
    assert classify(reports, ()).status == 'clean'


@pytest.mark.real_toolchain
def test_real_lean_reports_sorry_ax_and_the_audit_refuses_it() -> None:
    """`sorry` reaches the kernel as an axiom. This is the case the whole gate
    exists for, and the only place it can be confirmed is against real Lean."""
    tools = _tools('theorem HardyTarget : 1 = 2')
    # Past the textual hole check on purpose: `sorryAx` is not `sorry`, and the
    # audit rather than the regex is what has to stop this.
    result = tools.check_proof('by exact sorryAx _ true', final=True)

    assert result.ok, result.output
    reports = parse(result.report, ('HardyTarget',))
    assert reports is not None, result.report
    assert reports[0].axioms == ('sorryAx',)
    verdict = classify(reports, ())
    assert verdict.status == 'rejected'
    assert verdict.forbidden == ('sorryAx',)


@pytest.mark.real_toolchain
def test_a_primed_declaration_name_is_still_auditable() -> None:
    """The bug the shared parser fixed: `\\badd_comm'\\b` cannot match."""
    tools = _tools("theorem hardy_target' : 2 = 2")

    assert tools.target_name == "hardy_target'"
    result = tools.check_proof('by rfl', final=True)
    assert result.ok, result.output
    assert parse(result.report, ("hardy_target'",)) is not None, result.report
