"""What `request_assumption` shows the human, beyond the statement.

Three axioms were approved on a failing run with the reason "Mathlib does not
expose this". Nothing had been searched for; the reason was free text.
"""

from __future__ import annotations

import json

from hardy.lean import LeanDiagnostic, LeanToolResult
from hardy.models import ToolResult


class Search:
    """An `inspect_declarations` that answers with a scripted result."""

    def __init__(self, resolved=(), unavailable=(), ok=True):
        self.answer = ToolResult(
            ok,
            json.dumps({
                "resolved": [{"name": name} for name in resolved],
                "unavailable": list(unavailable),
            }),
        )

    def inspect_declarations(self, names):
        return self.answer

    def rank_premises(self, goal, limit=10):
        return ToolResult(True, "{}")

    def search_declarations(self, query, limit=10):
        return ToolResult(True, "{}")

    def search_modules(self, query, limit=20):
        return ToolResult(True, "{}")


def _request(**overrides):
    request = {
        "formal_name": "sylow",
        "lean_statement": "True",
        "latex_name": "Sylow",
        "informal_statement": "Sylow's theorems",
        "source": "Dummit and Foote",
        "reason": "not in Mathlib",
    }
    request.update(overrides)
    return request


def _searching_session(session_factory, approvals, search):
    def confirm(proposal):
        approvals.append(dict(proposal))
        return True

    session = session_factory(confirm=confirm, search=search, search_detail="fake")

    # The real `fake_lean` fixture patches `_run_lean_source` on a *different*
    # session object (the plain `session` fixture), so a `_searching_session`
    # needs its own stand-in. `tests/fake_lean.py` prints exactly one error on
    # a source it does not recognise, which the probe would read as the last
    # probe line (`exact?`) closing the goal -- refusing every request here as
    # "Lean proves this outright" before the search gate could be exercised.
    # So this fakes a genuine assumption instead: every `example` line fails
    # to close its goal, and the `axiom` line elaborates cleanly.
    def fake_run_lean_source(source, timeout=None):
        diagnostics = []
        for index, line in enumerate(source.splitlines(), start=1):
            if line.startswith("example"):
                diagnostics.append(
                    LeanDiagnostic(severity="error", message="unsolved goals", line=index, column=0)
                )
        return LeanToolResult(False, "", source, diagnostics=tuple(diagnostics))

    session._run_lean_source = fake_run_lean_source
    return session


def test_a_request_with_no_search_behind_it_is_refused(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, Search())

    result = session._tool("request_assumption", _request())

    assert not result.ok
    assert "no `inspect_declarations` has been run" in result.output
    assert approvals == []


def test_a_completed_inspection_unlocks_one_request(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, Search(unavailable=("Sylwo",)))
    session._tool("inspect_declarations", {"names": ["Sylwo"]})

    first = session._tool("request_assumption", _request())
    second = session._tool("request_assumption", _request(formal_name="other", latex_name="o"))

    assert first.ok
    assert not second.ok


def test_the_human_sees_what_was_searched(session_factory, approvals) -> None:
    session = _searching_session(
        session_factory, approvals, Search(resolved=("IsCyclic",), unavailable=("Sylwo",))
    )
    session._tool("inspect_declarations", {"names": ["IsCyclic", "Sylwo"]})

    session._tool("request_assumption", _request())

    assert approvals[0]["searched"] == ["IsCyclic ✓", "Sylwo ✗"]


def test_a_stopped_inspection_does_not_count(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, Search(unavailable=("X",), ok=False))
    session._tool("inspect_declarations", {"names": ["X"]})

    result = session._tool("request_assumption", _request())

    assert not result.ok


def test_a_session_that_cannot_search_is_not_asked_to(session, approvals, fake_lean) -> None:
    """`search=None` is how every existing fixture is built."""
    result = session._tool("request_assumption", _request())

    assert result.ok


def test_a_resubmitted_name_shows_the_human_the_previous_statement(
    session_factory, approvals, fake_lean
) -> None:
    """`sylow_unique_normal` lost its `Fintype.card P = p` conjunct between
    a refused request and an approved one, and nobody saw the change."""
    declined = []

    def confirm(proposal):
        declined.append(dict(proposal))
        return len(declined) > 1

    session = session_factory(confirm=confirm)
    session._tool("request_assumption", _request(lean_statement="True ∧ True"))

    session._tool("request_assumption", _request(lean_statement="True"))

    assert declined[1]["previous"] == "True ∧ True"
    assert "previous" not in declined[0]


def test_a_gate_refused_statement_is_also_remembered(session, approvals) -> None:
    session._tool("request_assumption", _request(lean_statement="axiom f : True"))

    assert session._rejected["sylow"] == ["axiom f : True"]


def test_previous_is_not_written_into_the_durable_record(session_factory, fake_lean) -> None:
    answers = iter([False, True])
    session = session_factory(confirm=lambda proposal: next(answers))
    session._tool("request_assumption", _request(lean_statement="True ∧ True"))
    session._tool("request_assumption", _request(lean_statement="True"))

    assert "previous" not in session.state["assumptions"][0]
