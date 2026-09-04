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
    # A `theorem` must be a proposition -- `: Nat` is a type error, which is
    # what this test said before anything ever ran it. Excluded middle is the
    # ordinary way to reach choice, and reaches it through core Lean alone.
    tools = _tools('theorem HardyTarget (p : Prop) : p ∨ ¬p')
    result = tools.check_proof('Classical.em p', final=True)

    assert result.ok, result.output
    reports = parse(result.report, ('HardyTarget',))
    assert reports is not None, result.report
    assert 'Classical.choice' in reports[0].axioms
    assert classify(reports, ()).status == 'clean'


@pytest.mark.real_toolchain
def test_real_lean_reports_sorry_ax_and_the_audit_names_it_a_hole() -> None:
    """`sorry` reaches the kernel as an axiom. This is the case the whole gate
    exists for, and the only place it can be confirmed is against real Lean.

    The grade is `open` rather than `rejected`: an unfinished proof is a
    different fact from an unacceptable one, and an interactive session keeps
    the former so that a long proof has somewhere to be built. What that does
    not change is anything here -- `1 = 2` closed by `sorryAx` is still named
    as a hole, still not `clean`, and so still refused by every caller that
    requires a clean grade, which is every unattended one.
    """
    tools = _tools('theorem HardyTarget : 1 = 2')
    # Past the textual hole check on purpose: `sorryAx` is not `sorry`, and the
    # audit rather than the regex is what has to stop this.
    result = tools.check_proof('by exact sorryAx _ true', final=True)

    assert result.ok, result.output
    reports = parse(result.report, ('HardyTarget',))
    assert reports is not None, result.report
    assert reports[0].axioms == ('sorryAx',)
    verdict = classify(reports, ())
    assert verdict.status == 'open'
    assert verdict.status != 'clean', 'nothing unattended may accept this'
    assert verdict.forbidden == ('sorryAx',)


@pytest.mark.real_toolchain
def test_a_primed_declaration_name_is_still_auditable() -> None:
    """The bug the shared parser fixed: `\\badd_comm'\\b` cannot match."""
    tools = _tools("theorem hardy_target' : 2 = 2")

    assert tools.target_name == "hardy_target'"
    result = tools.check_proof('by rfl', final=True)
    assert result.ok, result.output
    assert parse(result.report, ("hardy_target'",)) is not None, result.report


def _elaborate(source: str):
    """A6's source against the real toolchain, core Lean only.

    Same reasoning as `_tools` above: written against Mathlib this skipped
    everywhere, CI included, and a skipped test reads as coverage while proving
    nothing. `Nat`, `∃`, `True`, `trivial` and `decide` are all core.
    """
    from hardy.lean import elaborate

    lake = shutil.which('lake')
    if lake is None:
        pytest.skip('lake is not installed')
    if not (LEAN_PROJECT / 'lake-manifest.json').exists():
        pytest.skip('the pinned Lean project is not built; run `hardy setup`')
    return elaborate(source, argv=(lake, 'env', 'lean'), cwd=LEAN_PROJECT, timeout_seconds=120)


def _witness_entry(witness: str):
    from hardy.evals.problems import Entry

    return Entry(id='pos-nat', input='...', name='PosNat', binders='(n : Nat) (h : n > 0)',
                 conclusion='n ≥ 1', imports=('Init',), expected='true', source='textbook',
                 msc=('11A',), difficulty='routine', rationale='A6 real-toolchain check',
                 witness=witness)


@pytest.mark.real_toolchain
def test_a6_witness_is_accepted_or_refused_by_the_real_kernel() -> None:
    """The hermetic tests script the elaborator, so nothing there would notice
    if `witness_source` built Lean the kernel accepts vacuously -- the exact
    failure A6 exists to catch."""
    from hardy.evals.sweep import witness_verdict

    assert witness_verdict(_witness_entry('⟨1, by decide, trivial⟩'), elaborate=_elaborate) == 'witnessed'
    assert witness_verdict(_witness_entry('⟨0, by decide, trivial⟩'), elaborate=_elaborate) == 'broken'


@pytest.mark.real_toolchain
def test_a6_refuses_a_witness_that_is_a_hole() -> None:
    """`sorry` is a *warning*, so the elaboration succeeds and only the axiom
    report separates a witness from a hole wearing a term's clothes."""
    from hardy.evals.sweep import witness_verdict

    assert witness_verdict(_witness_entry('⟨0, sorry, trivial⟩'), elaborate=_elaborate) == 'broken'
