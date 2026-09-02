"""The problem list: what an entry must carry, and how every consumer assembles Lean from it."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hardy.evals.problems import Entry, ProblemSet, load_problems, sha256_of

ROOT = Path(__file__).resolve().parents[2]


def _entry(**overrides) -> dict:
    base = {
        "id": "odd-squares", "input": "If a and b are odd, a^2+b^2 is not a square.",
        "name": "OddSquares", "binders": "(a b : ℤ) (ha : Odd a) (hb : Odd b)",
        "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)", "expected": "true",
        "source": "classical", "area": "number theory",
    }
    base.update(overrides)
    return base


def test_the_declaration_proposition_and_negation_are_assembled_one_way():
    entry = Entry(**_entry())
    assert entry.declaration() == "theorem OddSquares (a b : ℤ) (ha : Odd a) (hb : Odd b) : ¬ IsSquare (a ^ 2 + b ^ 2)"
    assert entry.proposition() == "∀ (a b : ℤ) (ha : Odd a) (hb : Odd b), ¬ IsSquare (a ^ 2 + b ^ 2)"
    assert entry.negation() == "¬ (∀ (a b : ℤ) (ha : Odd a) (hb : Odd b), ¬ IsSquare (a ^ 2 + b ^ 2))"


def test_an_entry_without_binders_has_no_stray_space_or_quantifier():
    entry = Entry(**_entry(binders="", conclusion="Irrational (Real.sqrt 2)"))
    assert entry.declaration() == "theorem OddSquares : Irrational (Real.sqrt 2)"
    assert entry.proposition() == "Irrational (Real.sqrt 2)"
    assert entry.negation() == "¬ (Irrational (Real.sqrt 2))"


@pytest.mark.parametrize("bad", [
    {"id": "Bad Slug"}, {"id": ""}, {"name": "not an ident"}, {"conclusion": ""},
    {"conclusion": "x := 1"}, {"expected": "maybe"}, {"source": "blog"}, {"extra": 1},
])
def test_a_malformed_entry_is_refused(bad):
    with pytest.raises(ValidationError):
        Entry(**_entry(**bad))


def test_twins_point_at_true_entries_and_true_entries_point_nowhere():
    true = _entry()
    twin = _entry(id="squares", name="Squares", binders="(a b : ℤ)", expected="false", twin_of="odd-squares")
    ProblemSet(entries=(Entry(**true), Entry(**twin)))
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**true), Entry(**_entry(id="x", name="X", expected="false"))))  # twin with no target
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**_entry(twin_of="odd-squares")),))                               # true entry pointing
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**true), Entry(**twin), Entry(**_entry(id="y", name="Y", expected="false", twin_of="squares"))))  # twin of a twin


def test_ids_and_names_are_unique():
    with pytest.raises(ValidationError, match="duplicate id"):
        ProblemSet(entries=(Entry(**_entry()), Entry(**_entry(name="Other"))))
    with pytest.raises(ValidationError, match="duplicate name"):
        ProblemSet(entries=(Entry(**_entry()), Entry(**_entry(id="other"))))


def test_the_committed_list_loads_and_has_fifteen_true_entries_and_five_twins():
    problems = load_problems(ROOT / "evals" / "problems.json")
    assert len(problems.true_entries) == 15 and len(problems.twins) == 5
    assert {t.twin_of for t in problems.twins} <= {e.id for e in problems.true_entries}
    assert problems.by_id("sqrt-two-plus-sqrt-three").expected == "true"


def test_sha256_is_over_the_bytes(tmp_path):
    path = tmp_path / "p.json"
    path.write_bytes(b'{"schema_version": 1, "entries": []}')
    first = sha256_of(path)
    path.write_bytes(b'{"schema_version": 1, "entries": [] }')
    assert sha256_of(path) != first
    assert len(first) == 64
