"""The release audit reads a verified-modulo run as a verified run.

An audit that only recognised `kernel_verified` would treat every assumed run
as unverified and skip the checks that matter most for one -- the evidence, the
axiom report, the document -- which is the opposite of what a wider trust base
calls for.
"""

from __future__ import annotations

import importlib


def test_a_modulo_grade_is_audited_like_a_verified_one() -> None:
    acceptance = importlib.import_module("hardy.acceptance")
    domain = importlib.import_module("hardy.domain")

    assert domain.FormalStatus.VERIFIED_MODULO in acceptance.VERIFIED_GRADES
    assert domain.FormalStatus.KERNEL_VERIFIED in acceptance.VERIFIED_GRADES
    assert domain.FormalStatus.PARTIAL not in acceptance.VERIFIED_GRADES


def test_a_modulo_run_may_admit_exactly_the_axioms_it_declared() -> None:
    """The standard allowlist plus what the manifest says was assumed, and
    nothing else: an axiom in neither is the failure this check exists for."""
    acceptance = importlib.import_module("hardy.acceptance")

    assert acceptance.permitted_axioms(("Papers.a.one",)) == frozenset(
        {*acceptance.ALLOWED_AXIOMS, "Papers.a.one"}
    )
    assert acceptance.permitted_axioms(()) == frozenset(acceptance.ALLOWED_AXIOMS)


def test_a_hole_is_never_permitted_however_much_was_assumed() -> None:
    assert "sorryAx" not in acceptance_permitted()


def acceptance_permitted():
    acceptance = importlib.import_module("hardy.acceptance")
    return acceptance.permitted_axioms(("sorryAx",))


def test_a_recorded_run_predating_a_grade_field_still_reconciles(tmp_path) -> None:
    """The trajectory's terminal event and the manifest are written by one run
    and must agree. Comparing the recorded JSON against a re-serialized model
    made every field added afterwards look like a disagreement about a run
    that never disagreed -- so both sides are read through the same model, and
    a real difference in any grade still fails."""
    acceptance = importlib.import_module("hardy.acceptance")
    domain = importlib.import_module("hardy.domain")
    grades = domain.Grades(formal=domain.FormalStatus.PARTIAL, known_gaps=("one",))
    recorded = grades.model_dump(mode="json")
    recorded.pop("assumed")

    assert acceptance.grades_agree(recorded, grades)
    assert not acceptance.grades_agree({**recorded, "known_gaps": ["other"]}, grades)
    assert not acceptance.grades_agree({"nonsense": True}, grades)
