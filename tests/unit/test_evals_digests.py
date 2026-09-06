"""Component digests: what each one covers, and what it deliberately does not."""
from __future__ import annotations

import inspect

from hardy.evals.digests import (
    environment_digest,
    fixture_set_digest,
    procedure_digest,
    prompt_digest,
    statement_digest,
)

STATEMENT = {
    "name": "OddSquares",
    "binders": "(a b : ℤ)",
    "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)",
    "imports": ("Mathlib",),
    "witness": "⟨1, 1⟩",
    "witness_note": None,
}


def test_a_statement_digest_is_stable_and_sixtyfour_hex():
    first = statement_digest(**STATEMENT)
    assert first == statement_digest(**STATEMENT)
    assert len(first) == 64
    int(first, 16)


def test_editing_the_conclusion_or_the_witness_changes_the_statement_digest():
    base = statement_digest(**STATEMENT)
    assert statement_digest(**{**STATEMENT, "conclusion": "True"}) != base
    assert statement_digest(**{**STATEMENT, "witness": "⟨2, 2⟩"}) != base
    assert statement_digest(**{**STATEMENT, "witness_note": "none available"}) != base


def test_a_fixture_edit_stays_out_of_the_statement_and_prompt_digests():
    """The component boundary the whole arrangement exists to draw (spec §3).

    A4, A5 and B4 depend on `fixture_set_digest`; A1-A3, A6 and B1-B3 load no
    fixture at all. Folding fixture contents into the statement digest would
    reach every one of them through `prompt_digest` too, and editing a shared
    fixture would force re-sweeps and model re-runs whose outcomes cannot
    change.
    """
    assert "fixture" not in inspect.signature(statement_digest).parameters
    assert "fixture" not in inspect.signature(prompt_digest).parameters
    assert fixture_set_digest(("c" * 64,)) != fixture_set_digest(("d" * 64,))


def test_editing_input_changes_the_prompt_digest_but_not_the_statement_digest():
    statement = statement_digest(**STATEMENT)
    first = prompt_digest(statement=statement, input="If a and b are odd...", expected="true", twin_of=None)
    reworded = prompt_digest(statement=statement, input="Given odd a and b...", expected="true", twin_of=None)
    assert reworded != first
    assert statement == statement_digest(**STATEMENT), "the A-group must stay fresh"


def test_flipping_expected_changes_the_prompt_digest():
    """`expected` and `twin_of` decide which mode the entry executes in."""
    statement = statement_digest(**STATEMENT)
    true_side = prompt_digest(statement=statement, input="x", expected="true", twin_of=None)
    twin_side = prompt_digest(statement=statement, input="x", expected="false", twin_of="odd-squares")
    assert true_side != twin_side


def test_the_fixture_set_digest_is_order_independent():
    assert fixture_set_digest(("a" * 64, "b" * 64)) == fixture_set_digest(("b" * 64, "a" * 64))


def test_environment_and_procedure_digests_track_their_inputs():
    env = {"lean_version": "4.33.1", "mathlib_revision": "0df444a"}
    assert environment_digest(env) != environment_digest({**env, "mathlib_revision": "deadbee"})
    proc = {"hardy_revision": "abc123", "singles": ["simp"], "heartbeat_budget": 200000}
    assert procedure_digest(proc) != procedure_digest({**proc, "singles": ["simp", "aesop"]})


def test_source_digest_ignores_line_endings():
    from hardy.evals import digests

    assert digests.source_digest(b"a = 1\nb = 2\n") == digests.source_digest(b"a = 1\r\nb = 2\r\n")


def test_source_digest_sees_real_edits():
    from hardy.evals import digests

    assert digests.source_digest(b"a = 1\n") != digests.source_digest(b"a = 2\n")
