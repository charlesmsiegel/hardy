"""Cheap refutation: asking Lean for an obvious counterexample before assuming.

A false axiom makes everything provable, so the cost of admitting one is the
whole run and every artifact it produced. This is not a decision procedure and
does not pretend to be -- it is the five-second question "does standard
automation close the *negation* of this", which is the question that catches a
statement nobody should have proposed.

The asymmetry runs the other way from the elaboration probe. There, silence
means the statement is safe to assume; here, silence about a probe line means
that tactic *closed the negation*, which is the refutation. So every reading
that is not clearly an answer has to become a caveat rather than a verdict.
"""

from __future__ import annotations

from hardy import refute
from hardy.lean import LeanDiagnostic, LeanToolResult, scratch_source


def _result(source: str, *, failing: set[int], ok: bool = False, output: str = "") -> LeanToolResult:
    """A Lean answer in which exactly the given lines carry an error."""
    return LeanToolResult(
        ok,
        output,
        source,
        diagnostics=tuple(
            LeanDiagnostic(severity="error", message="unsolved goals", line=line, column=0)
            for line in sorted(failing)
        ),
    )


def _lines(source: str) -> list[str]:
    return source.splitlines()


# --- The source it asks ----------------------------------------------------------


def test_the_probe_asks_about_the_negation() -> None:
    source, tactics = refute.probe_source("∀ n : Nat, n = n")

    assert "¬ (∀ n : Nat, n = n)" in source
    assert tactics == refute.TACTICS


def test_the_probes_start_on_line_three_one_per_line() -> None:
    """The verdict is read off which line an error landed on, so the layout
    is part of the contract rather than formatting."""
    source, tactics = refute.probe_source("True")
    lines = _lines(source)

    assert lines[0] == "import Mathlib"
    assert lines[1] == ""
    for index, tactic in enumerate(tactics):
        assert lines[refute.FIRST_PROBE - 1 + index].endswith(f":= by {tactic}")


def test_the_last_line_is_the_elaboration_sentinel() -> None:
    """`sorry` closes any goal a statement that elaborates can pose. An error
    on that line means the statement itself did not elaborate, which is not a
    refutation of anything."""
    source, tactics = refute.probe_source("True")

    assert _lines(source)[refute.FIRST_PROBE - 1 + len(tactics)].endswith(":= by sorry")


def test_a_multiline_statement_is_collapsed_first() -> None:
    source, _ = refute.probe_source("∀ n : Nat,\n  n = n")

    assert "\n  n = n" not in source
    assert sum(1 for line in _lines(source) if line.startswith("example")) == len(refute.TACTICS) + 1


# --- What it concludes -------------------------------------------------------------


def test_a_tactic_that_closes_the_negation_refutes_the_statement() -> None:
    source, tactics = refute.probe_source("(1 : Nat) = 2")
    sentinel = refute.FIRST_PROBE + len(tactics)
    # Every tactic fails except `decide`, which proves the negation.
    failing = {
        refute.FIRST_PROBE + index for index, name in enumerate(tactics) if name != "decide"
    }

    verdict = refute.judge(_result(source, failing=failing), tactics)

    assert verdict.refuted
    assert verdict.tactic == "decide"
    assert not verdict.caveat
    assert sentinel not in failing


def test_a_statement_nothing_refutes_passes() -> None:
    source, tactics = refute.probe_source("∀ n : Nat, n = n")
    failing = {refute.FIRST_PROBE + index for index in range(len(tactics))}

    verdict = refute.judge(_result(source, failing=failing), tactics)

    assert not verdict.refuted
    assert not verdict.caveat


def test_a_statement_that_does_not_elaborate_is_a_caveat_not_a_refutation() -> None:
    """Every probe line looks clean when Lean never got that far, and reading
    that as "the negation was proved" would refuse an honest request."""
    source, tactics = refute.probe_source("Sylwo.theorem")
    verdict = refute.judge(
        _result(source, failing={refute.FIRST_PROBE + len(tactics)}), tactics
    )

    assert not verdict.refuted
    assert "did not elaborate" in verdict.caveat


def test_an_error_lean_could_not_place_is_a_caveat() -> None:
    source, tactics = refute.probe_source("True")
    result = LeanToolResult(
        False,
        "error: something went wrong",
        source,
        diagnostics=(LeanDiagnostic(severity="error", message="boom", line=None, column=0),),
    )

    verdict = refute.judge(result, tactics)

    assert not verdict.refuted
    assert verdict.caveat


def test_an_error_before_the_probes_is_a_caveat() -> None:
    """`import Mathlib` failing makes every probe line look clean."""
    source, tactics = refute.probe_source("True")

    verdict = refute.judge(_result(source, failing={1}), tactics)

    assert not verdict.refuted
    assert verdict.caveat


def test_a_run_that_failed_with_nothing_readable_is_a_caveat() -> None:
    source, tactics = refute.probe_source("True")
    verdict = refute.judge(LeanToolResult(False, "", source, diagnostics=()), tactics)

    assert not verdict.refuted
    assert verdict.caveat


def test_a_timeout_is_a_caveat() -> None:
    source, tactics = refute.probe_source("True")
    result = LeanToolResult(False, "", source, diagnostics=(), timed_out=True)

    verdict = refute.judge(result, tactics)

    assert not verdict.refuted
    assert "finish" in verdict.caveat


def test_the_refusal_names_the_tactic_and_the_statement() -> None:
    source, tactics = refute.probe_source("(1 : Nat) = 2")
    failing = {
        refute.FIRST_PROBE + index for index, name in enumerate(tactics) if name != "decide"
    }
    verdict = refute.judge(_result(source, failing=failing), tactics)

    sentence = refute.describe(verdict, "(1 : Nat) = 2")

    assert "decide" in sentence
    assert "(1 : Nat) = 2" in sentence
    assert "counterexample" in sentence.lower() or "refut" in sentence.lower()


# --- What each caller actually hands Lean -------------------------------------


def test_a_caller_that_supplies_its_own_header_gets_the_body_alone() -> None:
    """`LeanService.check_scratch` prepends `import Mathlib` itself. Handing it
    a source that already carries one put a second import on line 3 and shifted
    every probe by two, so `judge` read the sentinel's line as a tactic's: the
    staged refutation gate could not refute anything, and reported every honest
    assumption as one that did not elaborate."""
    body, tactics = refute.probe_source("2 + 2 = 5", imported=False)

    assert not body.startswith("import")
    # Asked of production rather than spelled out again here: a double that
    # rebuilds the header asserts on its own arithmetic.
    elaborated = scratch_source(body).splitlines()
    assert elaborated[0] == "import Mathlib"
    for index, tactic in enumerate(tactics):
        assert elaborated[refute.FIRST_PROBE - 1 + index].endswith(f":= by {tactic}")
    assert elaborated[refute.FIRST_PROBE - 1 + len(tactics)].endswith(":= by sorry")


def test_both_spellings_put_the_probes_on_the_same_lines() -> None:
    """The line contract is what `judge` reads, so it has to hold whichever
    caller built the file."""
    whole, tactics = refute.probe_source("True")
    body, _ = refute.probe_source("True", imported=False)

    assert whole.splitlines() == scratch_source(body).splitlines()


def test_a_truncated_answer_is_a_caveat_not_a_refutation() -> None:
    """`exact?` against Mathlib can outrun the output limit. The interactive
    result carries `output_overflow` as a field of its own rather than on a
    child process, and reading only the child's meant a silent probe line --
    silent because Lean's answer was cut off -- was reported to a human as
    "Lean proves the NEGATION of this statement"."""
    source, tactics = refute.probe_source("True")
    result = LeanToolResult(
        False,
        "output limit reached",
        source,
        diagnostics=(
            LeanDiagnostic(severity="error", message="unsolved goals", line=refute.FIRST_PROBE, column=0),
        ),
        output_overflow=True,
    )

    verdict = refute.judge(result, tactics)

    assert not verdict.refuted
    assert verdict.caveat


def test_a_statement_that_survives_the_one_line_collapse_is_refused() -> None:
    """`normalise_lean` deliberately preserves newlines inside string literals
    and raw strings, so the collapse is not total. The verdict is read off
    which line a diagnostic landed on, so a surviving newline shifts every
    probe below it and attributes an answer to the wrong tactic. Both callers
    happen to filter first; the guard belongs beside the contract."""
    import pytest

    with pytest.raises(ValueError, match="one line"):
        refute.probe_source('True ∧ (s = "a\nb").isEmpty')
