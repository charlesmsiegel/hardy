"""Lean's word for a wrong import is a missing file. Hardy's is a wrong import.

Record 35 of the graded transcript: given `object file '...Sylow/Basic.olean'
... does not exist`, the model wrote "The Mathlib cache is missing" and never
attempted Lean again. Mathlib was installed and complete; the module had been
flattened to `Mathlib.GroupTheory.Sylow`.
"""

from __future__ import annotations

from hardy.lean import translate_missing_modules


class FakeIndex:
    """Enough of a `ModuleIndex` for the translation to ask its two questions."""

    project = "/lean"

    def __init__(self, names: tuple[str, ...] = ("Mathlib.GroupTheory.Sylow",)) -> None:
        self._names = names

    def names(self) -> tuple[str, ...]:
        return self._names

    def nearest(self, missing: str, limit: int = 5) -> tuple[str, ...]:
        return tuple(n for n in self._names if missing.startswith(f"{n}."))[:limit]


LEAN_SAID = (
    "exit=1 elapsed=2.296s\n"
    "C:\\tmp\\Main.lean:1:0: error: object file "
    "'C:\\lean\\Mathlib\\GroupTheory\\Sylow\\Basic.olean' of module "
    "Mathlib.GroupTheory.Sylow.Basic does not exist"
)


def test_the_module_is_named_and_the_nearest_offered() -> None:
    answer = translate_missing_modules(LEAN_SAID, FakeIndex())

    assert "unknown module Mathlib.GroupTheory.Sylow.Basic" in answer
    assert "Mathlib.GroupTheory.Sylow" in answer


def test_the_misreading_is_addressed_directly() -> None:
    """The whole point. "Does not exist" about an `.olean` is true and reads as
    a damaged installation."""
    answer = translate_missing_modules(LEAN_SAID, FakeIndex())

    assert "not a broken installation" in answer


def test_lean_s_own_words_are_kept() -> None:
    answer = translate_missing_modules(LEAN_SAID, FakeIndex())

    assert LEAN_SAID in answer


def test_an_empty_index_says_so_rather_than_suggesting_nothing() -> None:
    answer = translate_missing_modules(LEAN_SAID, FakeIndex(names=()))

    assert "Mathlib.GroupTheory.Sylow.Basic" in answer
    assert "no module index" in answer


def test_output_with_no_such_error_is_returned_unchanged() -> None:
    other = "exit=1 elapsed=0.1s\nMain.lean:3:0: error: unsolved goals"

    assert translate_missing_modules(other, FakeIndex()) == other


def test_no_index_at_all_returns_the_output_unchanged() -> None:
    assert translate_missing_modules(LEAN_SAID, None) == LEAN_SAID


def test_each_missing_module_is_named_once() -> None:
    """Lean repeats the error once per importing file; a wall of identical
    paragraphs is how a translation becomes noise."""
    doubled = f"{LEAN_SAID}\n{LEAN_SAID}"

    answer = translate_missing_modules(doubled, FakeIndex())

    assert answer.count("unknown module Mathlib.GroupTheory.Sylow.Basic") == 1
