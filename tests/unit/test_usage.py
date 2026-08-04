"""The spend ledger: what a session has cost, as the provider reported it.

The property under test throughout is that *unreported* and *zero* stay
distinguishable. A backend that says nothing must never be rendered as a number,
because a reader cannot tell a free session from an unmeasured one, and the
whole point of putting the meter in front of a user mid-session is that they act
on it.
"""

from __future__ import annotations

from hardy.usage import Usage


def _ledger(**fields) -> Usage:
    """A ledger as a backend that reports everything would have left it.

    Constructing `Usage(...)` directly leaves `reports` empty, which now means
    "nothing was ever stated" -- so a test that wants a fully measured session
    has to say which fields were measured, and over how many exchanges.
    """
    turns = fields.get("turns", 1)
    stated = [name for name in (*Usage.COUNTERS, "cost_usd") if name in fields]
    return Usage(**fields, reports=dict.fromkeys(stated, turns))


REPORT = {
    "type": "result",
    "cost_usd": 0.5,
    "usage": {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 100,
    },
}


def test_a_fresh_ledger_has_spent_nothing_and_says_so():
    empty = Usage()
    assert empty.turns == 0
    assert empty.cost_usd is None
    assert empty.brief() == ""
    assert empty.lines() == ["Nothing spent yet."]


def test_one_report_is_folded_into_the_total():
    spent = Usage().record(REPORT)
    assert spent.turns == 1
    assert spent.cost_usd == 0.5
    assert (spent.input_tokens, spent.output_tokens) == (10, 20)
    assert (spent.cache_write_tokens, spent.cache_read_tokens) == (5, 100)
    assert spent.total_tokens == 135


def test_reports_accumulate_across_exchanges():
    """The provider bills each exchange separately -- Hardy opens a fresh SDK
    client per turn -- so the session total is the sum, not the last report."""
    spent = Usage().record(REPORT).record(REPORT).record(REPORT)
    assert spent.turns == 3
    assert spent.cost_usd == 1.5
    assert spent.total_tokens == 405


def test_an_errored_exchange_still_cost_what_it_cost():
    """The provider charges for the tokens it burned before failing. Dropping
    the report would make a session of failures look free."""
    spent = Usage().record({**REPORT, "is_error": True})
    assert spent.turns == 1
    assert spent.cost_usd == 0.5


# -- honest degradation ---------------------------------------------------


def test_a_backend_that_reports_nothing_is_unreported_and_not_zero():
    spent = Usage().record({"type": "result"})
    assert spent.turns == 1
    assert spent.cost_usd is None
    assert spent.counted is False
    body = "\n".join(spent.lines())
    assert "$0.00" not in body
    # Cost and each of the four token counters, every one of them named.
    assert body.count(Usage.UNREPORTED) == 5


def test_a_reported_zero_cost_is_a_number_and_not_unreported():
    """A free or locally hosted backend really did cost nothing, and saying so
    is not the same failure as saying nothing and showing zero."""
    spent = Usage().record({"type": "result", "cost_usd": 0.0})
    assert spent.cost_usd == 0.0
    assert "$0.00" in "\n".join(spent.lines())


def test_cost_and_tokens_degrade_independently():
    """One provider reports cost without counts; another the reverse."""
    priced = Usage().record({"type": "result", "cost_usd": 0.25})
    assert priced.brief() == "$0.25"
    assert Usage.UNREPORTED in "\n".join(priced.lines())

    counted = Usage().record({"type": "result", "usage": {"input_tokens": 900}})
    assert counted.brief() == "900"
    assert counted.cost_usd is None


def test_a_counter_the_backend_omitted_is_not_shown_as_a_measured_zero():
    """The degradation is per counter, not per report. A backend that states
    input alone has not stated that its output was zero, and `0 tokens` on that
    row is the same lie as `$0.00` on the cost row."""
    spent = Usage().record({"type": "result", "usage": {"input_tokens": 900}})
    body = spent.lines()
    assert any(line.startswith("Input:") and "900 tokens" in line for line in body)
    for counter in ("Output:", "Cache write:", "Cache read:"):
        row = next(line for line in body if line.startswith(counter))
        assert Usage.UNREPORTED in row, row
    # No row anywhere states a bare zero it was never told.
    assert not [line for line in body if line.endswith("0 tokens") and " 900 " not in line]


def test_a_total_covering_only_some_exchanges_says_how_many():
    """Cost spanning the whole session beside tokens spanning part of it is
    two numbers about different things, printed as if they matched."""
    spent = Usage().record({"type": "result", "cost_usd": 0.5}).record(
        {"type": "result", "cost_usd": 0.5, "usage": {"input_tokens": 900}}
    )
    body = "\n".join(spent.lines())
    assert "$1.00" in body
    assert "(1 of 2 exchanges)" in body            # the token counters
    assert "$1.00 (2 of 2" not in body             # complete coverage stays quiet


def test_the_total_inherits_the_coverage_of_the_counters_it_sums():
    """A whole-looking total over partial-looking rows is the same mismatch."""
    spent = Usage().record({"type": "result"}).record(REPORT)
    total = next(line for line in spent.lines() if line.startswith("Total:"))
    assert "135 tokens (1 of 2 exchanges)" in total


def test_counters_spanning_different_exchanges_name_no_single_span():
    """Unreachable with a real backend, but a ledger carried across versions
    could hold it, and picking one span would be wrong for the other counters.
    """
    spent = (
        Usage()
        .record({"type": "result", "usage": {"input_tokens": 5}})
        .record({"type": "result", "usage": {"input_tokens": 5, "output_tokens": 7}})
    )
    total = next(line for line in spent.lines() if line.startswith("Total:"))
    assert "counters cover different exchanges" in total


def test_a_cost_reported_for_only_some_exchanges_is_marked_too():
    spent = Usage().record({"type": "result"}).record({"type": "result", "cost_usd": 0.5})
    row = next(line for line in spent.lines() if line.startswith("Cost:"))
    assert "$0.50" in row and "(1 of 2 exchanges)" in row


def test_a_report_that_arrives_after_silence_still_lands():
    """A backend need not be consistent turn to turn; one number is enough to
    stop the ledger claiming the session was never measured."""
    spent = Usage().record({"type": "result"}).record(REPORT)
    assert spent.turns == 2
    assert spent.cost_usd == 0.5
    assert spent.counted is True


def test_unusable_counts_are_ignored_rather_than_believed():
    """Nothing downstream validates a provider's own report, so a string or a
    negative number must not become part of a total a user is shown."""
    spent = Usage().record({"type": "result", "cost_usd": "free", "usage": {"input_tokens": -5, "output_tokens": "many"}})
    assert spent.cost_usd is None
    assert spent.counted is False
    assert spent.total_tokens == 0


# -- the abbreviated form the chrome carries ------------------------------


def test_the_brief_form_pairs_cost_with_a_compact_token_count():
    spent = _ledger(turns=1, input_tokens=82_431, cost_usd=1.34)
    assert spent.brief() == "$1.34 · 82k"


def test_sub_cent_spend_is_marked_rather_than_rounded_away():
    """`$0.00` after a real exchange reads as 'this backend reports nothing'."""
    assert _ledger(turns=1, cost_usd=0.004).brief() == "<$0.01"


def test_token_counts_stay_readable_across_magnitudes():
    def compact(count: int) -> str:
        return _ledger(turns=1, input_tokens=count).brief()

    assert compact(940) == "940"
    assert compact(1_500) == "1.5k"
    assert compact(82_431) == "82k"
    assert compact(2_400_000) == "2.4M"


# -- persistence ----------------------------------------------------------


def test_a_ledger_survives_a_round_trip_through_session_json():
    spent = Usage().record(REPORT).record(REPORT)
    assert Usage.from_dict(spent.as_dict()) == spent


def test_a_workspace_written_before_the_ledger_existed_reopens_empty():
    assert Usage.from_dict(None) == Usage()
    assert Usage.from_dict({}) == Usage()


def test_a_corrupted_ledger_is_read_as_empty_rather_than_crashing_the_session():
    """`session.json` is a file on disk a user can edit. Refusing to open the
    workspace over a bad counter would cost them the workspace, not the counter.
    """
    assert Usage.from_dict({"turns": "seven", "input_tokens": None}) == Usage()
    # A bad cost alone is enough: a total half-read is not a total, and there
    # is no honest way to show four of five numbers as if they belonged
    # together.
    assert Usage.from_dict({"turns": 2, "cost_usd": "free"}) == Usage()
    assert Usage.from_dict({"turns": 2, "cost_usd": -1.0}) == Usage()
