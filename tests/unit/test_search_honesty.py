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


class Inspection:
    """Enough of a `DeclarationInspection` for the façade to judge it."""

    def __init__(self, *, resolved=(), unavailable=(), success=True, timed_out=False):
        self.resolved = resolved
        self.unavailable = unavailable
        self.success = success
        self.timed_out = timed_out

    def model_dump_json(self) -> str:
        return (
            f'{{"resolved": [], "unavailable": {list(self.unavailable)!r}, '
            f'"success": {str(self.success).lower()}}}'
        ).replace("'", '"')


class Service:
    def __init__(self, answer):
        self.answer = answer

    def inspect_declarations(self, names):
        return self.answer


def _inspect(answer, names=("IsCyclic",)):
    runtime = search_tools.SearchToolRuntime.__new__(search_tools.SearchToolRuntime)
    runtime.service = Service(answer)
    return runtime.inspect_declarations(list(names))


def test_a_stopped_inspection_is_refused_not_reported_as_absence() -> None:
    result = _inspect(Inspection(unavailable=("IsCyclic",), success=False, timed_out=True))

    assert not result.ok
    assert "NOT a report that nothing matched" in result.output


def test_a_completed_inspection_that_resolved_nothing_hints_at_spellings() -> None:
    result = _inspect(Inspection(unavailable=("IsCyclic",)))

    assert result.ok
    assert result.output.startswith(search_tools.SPELLINGS_HINT)
    assert '"unavailable": ["IsCyclic"]' in result.output


def test_a_completed_inspection_that_resolved_something_has_no_hint() -> None:
    result = _inspect(Inspection(resolved=("x",)))

    assert result.ok
    assert search_tools.SPELLINGS_HINT not in result.output
