r"""A cheap search for an obvious counterexample, before an axiom is admitted.

An assumption is a promise that a statement is true. A *false* assumption is
not a smaller promise -- it makes everything provable, so every downstream
signal reads green while the result means nothing. The elaboration probe in the
interactive session already refuses a statement Lean proves outright (that is a
theorem, not an assumption); this asks the opposite question, which is the
dangerous one: can standard automation prove the statement's **negation**?

It is a filter, not a decision procedure. `decide` settles a false arithmetic
claim and `simp` settles a good deal of the rest; a subtly false analytic
statement will walk straight through, and nothing here should be read as
"checked". What it catches is the class that actually shows up -- a quantifier
the wrong way round, an off-by-one bound, a statement whose hypotheses do not
constrain what its conclusion claims.

The reading is asymmetric, and in the opposite direction from the elaboration
probe. There, an error on a probe line is good news. Here, the *absence* of an
error on a probe line means that tactic proved the negation, so anything that
could make a line look clean for the wrong reason -- an import that failed, an
error Lean could not place, a run that did not finish -- has to become a caveat
rather than a verdict. A refutation Hardy is not sure of would refuse an honest
request and send a model looking for a fault that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .workspace import normalise_lean

#: What is tried against the negation. Ordered cheapest first, though they all
#: run in one elaboration: `decide` settles a false closed arithmetic claim,
#: `norm_num` a false numeric one, `simp` and `tauto` the structural mistakes,
#: and `exact?` finds a Mathlib lemma that contradicts the statement outright.
TACTICS = ("decide", "norm_num", "simp", "tauto", "exact?")
#: The header the probe file needs, whoever supplies it.
PREAMBLE = "import Mathlib\n\n"
#: The line the first probe sits on: `import Mathlib`, a blank, then one
#: `example` per tactic. The verdict is read off line numbers, so this layout
#: is part of the contract and is asserted by the tests.
FIRST_PROBE = 3
#: Its own budget, like the elaboration probe's: `import Mathlib` costs about
#: twenty seconds warm and minutes cold, and a machine whose first assumption
#: request happens to be cold must not have this degrade to "unchecked".
PROBE_SECONDS = 240.0


@dataclass(frozen=True)
class Verdict:
    """What the probe established, or why it established nothing."""

    refuted: bool
    tactic: str = ""
    caveat: str = ""

    @property
    def checked(self) -> bool:
        return not self.caveat


def probe_source(
    statement: str, tactics: tuple[str, ...] = TACTICS, *, imported: bool = True
) -> tuple[str, tuple[str, ...]]:
    r"""The Lean file that asks whether anything proves `¬ statement`.

    `imported=False` returns the body without the `import Mathlib` header, for
    a caller that supplies its own -- `LeanService.check_scratch` prepends one.
    Either way the file Lean finally elaborates has the import on line 1, a
    blank on line 2, and the probes from `FIRST_PROBE`, which is the whole
    contract `judge` reads. Handing `check_scratch` a source that already
    carried an import put a second one on line 3 and shifted every probe by
    two: `judge` then read the sentinel's line as a tactic's, so the staged
    gate could not refute anything and called every honest assumption one that
    did not elaborate.

    The statement is collapsed to one line first. Which tactic closed a goal
    is read from a diagnostic's line number, and Hardy keeps only a
    diagnostic's *start* line, so a statement spanning two lines would
    attribute an answer to the wrong tactic.

    Nothing is declared before the probes -- no `axiom`, no `theorem`. The
    elaboration probe puts its declaration last for a reason (`exact?` will
    happily close a goal by citing an axiom in scope), and here there is
    nothing to declare at all: the question is about Mathlib and the statement,
    and anything else in scope could only answer it wrongly.

    The last line is a `sorry` sentinel. `sorry` closes any goal a statement
    that elaborates can pose, so an error *there* means the statement did not
    elaborate -- which makes every probe line above it clean for a reason that
    has nothing to do with the negation being provable.
    """
    text = normalise_lean(str(statement)).strip()
    # The collapse is not total: `normalise_lean` preserves newlines inside
    # string literals, raw strings and `«...»`, deliberately, because those
    # are part of the text rather than layout. A survivor here shifts every
    # probe below it and hands `judge` an answer attributed to the wrong
    # tactic, so it is refused where the contract is stated rather than left
    # to whichever caller remembered to filter first.
    if any(character in text for character in ("\n", "\r", "\x0b", "\x0c", "\u2028", "\u2029")):
        raise ValueError("a refutation probe needs a statement that fits on one line")
    examples = "\n".join(f"example : ¬ ({text}) := by {tactic}" for tactic in tactics)
    sentinel = f"example : ¬ ({text}) := by sorry"
    body = f"{examples}\n{sentinel}\n"
    return (f"{PREAMBLE}{body}" if imported else body), tactics


def judge(result: Any, tactics: tuple[str, ...] = TACTICS) -> Verdict:
    """Read one probe elaboration, fail-safe in the direction of not refuting.

    Takes the result rather than the raw diagnostics because three of the five
    things that make an answer unusable -- a timeout, an interruption, a run
    that failed without saying anything Hardy can read -- are properties of the
    run rather than of any line.
    """
    ok, unfinished = _reading(result)
    if unfinished:
        return Verdict(False, caveat="the refutation probe did not finish")
    errors = [item for item in getattr(result, "diagnostics", ()) if item.severity == "error"]
    if not ok and not errors:
        return Verdict(
            False, caveat="Lean failed the refutation probe without diagnostics Hardy could read"
        )
    if any(item.line is None for item in errors):
        # An error Lean could not place could belong to any line, and every
        # conclusion below is drawn from which line an error landed on.
        return Verdict(False, caveat="Lean reported an error the refutation probe could not place")
    placed = {item.line for item in errors}
    sentinel = FIRST_PROBE + len(tactics)
    if sentinel in placed:
        # `sorry` could not close it, so the statement itself did not
        # elaborate. The probe lines above are clean for that reason and say
        # nothing about the negation.
        return Verdict(False, caveat="the statement did not elaborate in the refutation probe")
    if any(line < FIRST_PROBE or line > sentinel for line in placed):
        # An error outside the block -- `import Mathlib` failing on line 1, or
        # anything after the sentinel -- means Lean never reached the probes,
        # and a clean probe line then means nothing at all.
        return Verdict(
            False, caveat="the refutation probe failed before it reached its own tactics"
        )
    for index, tactic in enumerate(tactics):
        if FIRST_PROBE + index not in placed:
            return Verdict(True, tactic=tactic)
    return Verdict(False)


def _reading(result: Any) -> tuple[bool, bool]:
    """Whether the elaboration succeeded, and whether it never finished.

    Two result shapes reach here: `LeanToolResult`, which the interactive
    session gets back and which carries `ok`/`timed_out` directly, and
    `LeanCheckResult`, which the staged `LeanService` returns with `success`
    and the process underneath it. One judge, so the two paths cannot come to
    disagree about what counts as a refutation.
    """
    ok = getattr(result, "ok", None)
    if ok is None:
        ok = bool(getattr(result, "success", False))
    child = getattr(result, "process", None)
    # Every "it never finished" signal is asked of both shapes. `LeanToolResult`
    # carries `output_overflow` as a field of its own and has no `.process`, so
    # reading only the child's meant a truncated answer -- `exact?` against
    # Mathlib will do it -- came back as a refutation: silent probe lines,
    # silent because Lean's output was cut off, read as tactics that closed the
    # negation.
    unfinished = bool(
        getattr(result, "timed_out", False)
        or getattr(result, "interrupted", False)
        or getattr(result, "output_overflow", False)
        or getattr(child, "timed_out", False)
        or getattr(child, "interrupted", False)
        or getattr(child, "output_overflow", False)
    )
    return bool(ok), unfinished


def describe(verdict: Verdict, statement: str) -> str:
    """What to tell whoever proposed a statement the probe refuted."""
    return (
        f"Lean proves the NEGATION of this statement with `{verdict.tactic}`, so as written it "
        f"is false and assuming it would make everything provable:\n  {statement}\n"
        "This is a cheap counterexample search, not a decision procedure -- but it found one. "
        "Restate what you actually need, or check the quantifiers and bounds."
    )
