"""A search that did not finish is not a report that nothing matched.

Measured against the pinned toolchain, every `search_declarations` call timed
out -- and came back to the model as `ok=True` with `results: []`. There is no
way to read that except "Mathlib does not have it", and that is what a live
session concluded about `IsSimpleGroup`, which Mathlib has, before going on to
assume four classical theorems Mathlib proves.

This is the distinction `inspect_declarations` already refuses to blur when it
will not truncate a name list: "not found" and "not asked" are different
answers, and only one of them is evidence.
"""

from __future__ import annotations

import importlib

search_tools = importlib.import_module("hardy.search_tools")


class Answer:
    """Enough of a `DeclarationSearch` for `_answer` to judge it."""

    def __init__(self, *, success: bool = True, timed_out: bool = False) -> None:
        self.success = success
        self.timed_out = timed_out

    def model_dump_json(self) -> str:
        return f'{{"success": {str(self.success).lower()}, "results": []}}'


def _answer(value):
    runtime = search_tools.SearchToolRuntime.__new__(search_tools.SearchToolRuntime)
    return search_tools.SearchToolRuntime._answer(runtime, lambda: value)


def test_a_timed_out_search_is_not_a_successful_empty_search() -> None:
    result = _answer(Answer(success=False, timed_out=True))

    assert not result.ok
    assert "did not finish" in result.output
    assert "NOT a report that nothing matched" in result.output


def test_a_timed_out_search_says_not_to_conclude_absence() -> None:
    """The sentence has one job: stop the model reasoning from an empty list."""
    result = _answer(Answer(success=False, timed_out=True))

    assert "Do not conclude the result is absent" in result.output


def test_a_failed_search_is_also_refused() -> None:
    result = _answer(Answer(success=False, timed_out=False))

    assert not result.ok
    assert "NOT a report that nothing matched" in result.output


def test_a_search_that_finished_and_found_nothing_is_a_real_answer() -> None:
    """An empty result IS evidence when the search actually ran, and must stay
    an ordinary successful answer."""
    result = _answer(Answer(success=True, timed_out=False))

    assert result.ok
    assert "did not finish" not in result.output


def test_the_payload_travels_with_the_refusal() -> None:
    """Hardy never hides what a tool said."""
    result = _answer(Answer(success=False, timed_out=True))

    assert '"results": []' in result.output
