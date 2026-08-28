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


class RaisingSearch(Search):
    """Finding #2 (second brutal review): a call `_search_tool` refuses on
    its arguments must never reach `inspect_declarations` at all -- Lean was
    never asked, so nothing here should run. Raising, rather than merely
    counting, is what makes these tests fail loudly if that stops being
    true."""

    def inspect_declarations(self, names):
        raise AssertionError("inspect_declarations must not run for a call _search_tool refused")


def test_an_empty_names_list_leaves_the_search_gate_untouched(session_factory, approvals) -> None:
    """A schema-valid but empty `names` list is exactly the shape `lean.py`'s
    `inspect_declarations` refuses with `ValueError` -- Lean is never
    started. The old code counted this as an attempt anyway, opened the
    search-first gate, and told the human "1 inspection(s) attempted...none
    finished", which is false: nothing was ever tried."""
    session = _searching_session(session_factory, approvals, RaisingSearch())

    refusal = session._tool("inspect_declarations", {"names": []})

    assert not refusal.ok
    assert "declaration inspection requires between 1 and 20 names" in refusal.output

    result = session._tool("request_assumption", _request())

    assert not result.ok
    assert "no `inspect_declarations` has been run" in result.output
    assert approvals == []


def test_a_missing_names_key_leaves_the_search_gate_untouched(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, RaisingSearch())

    refusal = session._tool("inspect_declarations", {})

    assert not refusal.ok
    assert "declaration inspection requires between 1 and 20 names" in refusal.output

    result = session._tool("request_assumption", _request())

    assert not result.ok
    assert "no `inspect_declarations` has been run" in result.output
    assert approvals == []


def test_a_natural_language_query_leaves_the_search_gate_untouched(session_factory, approvals) -> None:
    """`search_tools.py` adds `CONCEPT_HINT` precisely because models put
    concepts where names belong -- this is not an adversarial input."""
    session = _searching_session(session_factory, approvals, RaisingSearch())

    refusal = session._tool("inspect_declarations", {"names": ["Sylow's theorem on normal subgroups"]})

    assert not refusal.ok
    assert "declaration names must be qualified Lean identifiers" in refusal.output

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


def test_a_shape_refused_request_still_consumes_the_search(session_factory, approvals) -> None:
    """A request refused by `_assumption_shape` reaches that gate only after
    the search-first gate already let it through -- a human's
    `inspect_declarations` call was already looked at to decide the
    refusal. Leaving `_searched_since_request` and friends untouched let the
    NEXT request, even under a different `formal_name`, walk through the
    search gate on evidence that was never about it."""
    session = _searching_session(session_factory, approvals, Search(unavailable=("X",)))
    session._tool("inspect_declarations", {"names": ["X"]})

    refused = session._tool(
        "request_assumption", _request(lean_statement="axiom f : True")
    )
    assert not refused.ok

    second = session._tool(
        "request_assumption", _request(formal_name="other", latex_name="o")
    )

    assert not second.ok
    assert "no `inspect_declarations` has been run" in second.output


class ClosesWithSimp:
    """A `_run_lean_source` stand-in whose `example` lines close exactly on
    `closes_with`, mirroring `conftest.fake_lean`'s `Fake` -- but installed
    directly on a `_searching_session` session, whose own
    `fake_run_lean_source` fails every `example` line unconditionally and so
    can never drive `_assumption_probe` into a refusal."""

    closes_with: str | None = None

    def __call__(self, source: str, timeout: float | None = None):
        diagnostics = []
        for number, line in enumerate(source.splitlines(), start=1):
            if line.startswith("example"):
                tactic = line.split(" := by ", 1)[1]
                if tactic == self.closes_with:
                    continue
                diagnostics.append(
                    LeanDiagnostic(severity="error", message="unsolved goals", line=number, column=0)
                )
        return LeanToolResult(not diagnostics, "", source, diagnostics=tuple(diagnostics))


def test_a_probe_refused_request_still_consumes_the_search(session_factory, approvals) -> None:
    """Same as above, for the other gate `_request_assumption` can be
    refused by after the search gate has already passed."""
    session = _searching_session(session_factory, approvals, Search(unavailable=("X",)))
    session._tool("inspect_declarations", {"names": ["X"]})
    session._run_lean_source = ClosesWithSimp()
    session._run_lean_source.closes_with = "simp"

    refused = session._tool("request_assumption", _request())
    assert not refused.ok
    assert "by simp" in refused.output

    second = session._tool(
        "request_assumption", _request(formal_name="other", latex_name="o")
    )

    assert not second.ok
    assert "no `inspect_declarations` has been run" in second.output


def test_the_human_sees_what_was_searched(session_factory, approvals) -> None:
    session = _searching_session(
        session_factory, approvals, Search(resolved=("IsCyclic",), unavailable=("Sylwo",))
    )
    session._tool("inspect_declarations", {"names": ["IsCyclic", "Sylwo"]})

    session._tool("request_assumption", _request())

    assert approvals[0]["searched"] == ["IsCyclic ✓", "Sylwo ✗"]


def test_a_stopped_inspection_still_lets_the_request_through(session_factory, approvals) -> None:
    """A machine whose Lean cannot finish must not be one where
    `request_assumption` is refused forever. `inspect_declarations` returning
    `ok=False` is exactly `_did_not_finish` -- the failing run's `#check`
    elaborations stopped, not the case where nothing was tried at all. One
    stopped attempt is enough to open the gate, and the human is told,
    verbatim, that none of the attempts finished."""
    session = _searching_session(session_factory, approvals, Search(unavailable=("X",), ok=False))
    session._tool("inspect_declarations", {"names": ["X"]})

    result = session._tool("request_assumption", _request())

    assert result.ok
    assert approvals[0]["searched"] == ["1 inspection(s) attempted since the last request, none finished"]


def test_with_zero_attempts_the_gate_still_refuses_with_the_existing_wording(
    session_factory, approvals
) -> None:
    """No `inspect_declarations` call at all -- not even one that failed to
    finish -- must still be refused, and with the same message a search-first
    request has always been refused with."""
    session = _searching_session(session_factory, approvals, Search())

    result = session._tool("request_assumption", _request())

    assert not result.ok
    assert "no `inspect_declarations` has been run" in result.output
    assert approvals == []


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


def _transcript_events(session):
    return [
        json.loads(line)
        for line in session.transcript_path.read_text(encoding="utf-8").splitlines()
    ]


def test_the_transcript_keeps_what_the_human_was_shown_when_approving(
    session, approvals, fake_lean
) -> None:
    """Nit (b), second brutal review: `checked`, `searched` and `previous`
    reach `confirm` but are stripped out of the durable `state["assumptions"]`
    record, and nothing else wrote them anywhere durable -- a later reader
    could never answer "what evidence was the human shown when they approved
    this axiom?". An `assumption_prompt` event carries them into
    `transcript.jsonl`."""
    session._tool("request_assumption", _request(lean_statement="True ∧ True"))

    prompts = [event for event in _transcript_events(session) if event["type"] == "assumption_prompt"]

    assert len(prompts) == 1
    assert prompts[0]["formal_name"] == "sylow"
    assert prompts[0]["checked"] == approvals[0]["checked"]
    assert prompts[0]["searched"] == approvals[0]["searched"]


def test_the_transcript_records_the_prompt_before_confirm_is_asked(
    session_factory, fake_lean
) -> None:
    """The record must exist even for a request the human declines --
    otherwise a refusal is the one outcome the evidence trail forgets."""
    session = session_factory(confirm=lambda proposal: False)

    session._tool("request_assumption", _request())

    prompts = [event for event in _transcript_events(session) if event["type"] == "assumption_prompt"]
    assert len(prompts) == 1


def test_a_long_search_history_is_truncated_to_the_last_20_for_the_human(
    session_factory, approvals
) -> None:
    """Nit (c), second brutal review: a session that inspects across several
    calls (`inspect_declarations` refuses more than 20 names in one call --
    finding #2's own validation) can still pile up a `searched` list beyond
    20 names for one `request_assumption`, and nobody is going to read all
    of it."""
    names = [f"N{index}" for index in range(25)]
    search = Search(resolved=tuple(names))
    session = _searching_session(session_factory, approvals, search)
    session._tool("inspect_declarations", {"names": names[:20]})
    session._tool("inspect_declarations", {"names": names[20:]})

    session._tool("request_assumption", _request())

    searched = approvals[0]["searched"]
    assert searched[0] == "25 names inspected; last 20:"
    assert len(searched) == 21
    assert searched[1:] == [f"N{index} ✓" for index in range(5, 25)]
