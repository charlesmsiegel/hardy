"""Component digests: what each one covers, and what it deliberately does not."""
from __future__ import annotations

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
    "fixture_digests": (),
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


def test_a_fixture_edit_reaches_the_statement_digest_through_its_content():
    bare = statement_digest(**STATEMENT)
    fixtured = statement_digest(**{**STATEMENT, "fixture_digests": ("c" * 64,)})
    edited = statement_digest(**{**STATEMENT, "fixture_digests": ("d" * 64,)})
    assert fixtured != bare
    assert edited != fixtured


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
