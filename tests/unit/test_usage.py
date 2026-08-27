"""The spend ledger: what a session has cost, as the provider reported it.

The property under test throughout is that *unreported* and *zero* stay
distinguishable. A backend that says nothing must never be rendered as a number,
because a reader cannot tell a free session from an unmeasured one, and the
whole point of putting the meter in front of a user mid-session is that they act
on it.
"""

from __future__ import annotations

import pytest

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


def _cumulative(exchanges: int, session: str = "thread-1") -> dict:
    """A report as the CLI actually sends one: every figure session-to-date.

    Both come from state the resume path restores -- `Ot.totalCostUSD` for the
    cost, and `Ot.modelUsage` for the counts, which `qya()` sums to build
    `usage`. So after `exchanges` identical exchanges of $0.50 and 135 tokens,
    the report carries the running total, not the last exchange's own.
    """
    return {
        "type": "result",
        "session_id": session,
        "cost_usd": 0.5 * exchanges,
        "usage": {key: value * exchanges for key, value in REPORT["usage"].items()},
    }


def test_cumulative_token_reports_are_differenced_too():
    """`usage` is `qya()`, which sums `Ot.modelUsage` -- and the resume path
    (`aEo` -> `Tws` -> `z$r`) restores that map from `lastModelUsage`. So the
    counters are session-to-date exactly as the cost is, and summing 100 then
    300 input tokens would store 400 for a session that used 300."""
    spent = (
        Usage()
        .record({"type": "result", "session_id": "t1", "usage": {"input_tokens": 100, "output_tokens": 10}})
        .record({"type": "result", "session_id": "t1", "usage": {"input_tokens": 300, "output_tokens": 40}})
    )
    assert spent.input_tokens == 300
    assert spent.output_tokens == 40
    assert spent.total_tokens == 340


def test_a_token_counter_that_restarts_is_not_read_as_negative_usage():
    spent = (
        Usage()
        .record({"type": "result", "session_id": "t1", "usage": {"input_tokens": 500}})
        .record({"type": "result", "session_id": "t2", "usage": {"input_tokens": 120}})
    )
    assert spent.input_tokens == 620


def test_cumulative_cost_reports_are_differenced_rather_than_summed():
    """Summing them is triangular: 0.50 + 1.00 + 1.50 = $3.00 for a session
    that cost $1.50, and the error grows with the square of the turn count."""
    spent = Usage().record(_cumulative(1)).record(_cumulative(2)).record(_cumulative(3))
    assert spent.turns == 3
    assert spent.cost_usd == 1.5
    assert spent.total_tokens == 405   # 135 an exchange; summing would give 810


def test_a_provider_counter_that_restarts_is_not_read_as_a_refund():
    """The CLI only restores the running total when the session it is resuming
    is the last one it saw; another session in between leaves the counter at
    zero. A difference against the old baseline would be negative."""
    spent = Usage().record(_cumulative(2)).record(_cumulative(4)).record(_cumulative(1))
    assert spent.cost_usd == 2.5   # $1.00, then $1.00 more, then a restarted $0.50


def test_a_new_provider_session_starts_its_counter_over():
    """Told by the session id rather than inferred from the magnitude, which
    is the case a smaller-than-last test cannot catch on its own."""
    spent = Usage().record(_cumulative(2)).record(_cumulative(3, session="thread-2"))
    assert spent.cost_usd == 2.5   # $1.00, then a fresh session's $1.50


def test_a_restart_is_seen_even_where_one_figure_happens_to_rise():
    """A restart resets every counter at once -- `z$r` writes them together --
    so it is a property of the report, not of one field. A cost that climbs
    past its old baseline while the token counts fall off a cliff is a fresh
    counter, and differencing the cost against the old baseline would lose the
    whole of the session before it."""
    spent = (
        Usage()
        .record({"type": "result", "session_id": "t1", "cost_usd": 0.10,
                 "usage": {"cache_read_input_tokens": 5_000}})
        # The CLI did not restore: a new exchange costing $0.20, but reading
        # only what its own context needed.
        .record({"type": "result", "session_id": "t1", "cost_usd": 0.20,
                 "usage": {"cache_read_input_tokens": 400}})
    )
    assert spent.cost_usd == pytest.approx(0.30)     # not 0.20
    assert spent.cache_read_tokens == 5_400


def test_an_ordinary_continuation_is_not_mistaken_for_a_restart():
    """Every figure climbing is what a restored counter looks like."""
    spent = (
        Usage()
        .record({"type": "result", "session_id": "t1", "cost_usd": 0.10,
                 "usage": {"input_tokens": 100, "cache_read_input_tokens": 5_000}})
        .record({"type": "result", "session_id": "t1", "cost_usd": 0.25,
                 "usage": {"input_tokens": 260, "cache_read_input_tokens": 9_000}})
    )
    assert spent.cost_usd == pytest.approx(0.25)
    assert spent.input_tokens == 260
    assert spent.cache_read_tokens == 9_000


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
    # And the total does not read as the whole of something three-quarters
    # unreported.
    total = next(line for line in body if line.startswith("Total:"))
    assert "reported counters only" in total


def test_a_total_covering_only_some_exchanges_says_how_many():
    """Cost spanning the whole session beside tokens spanning part of it is
    two numbers about different things, printed as if they matched."""
    spent = Usage().record({"type": "result", "cost_usd": 0.5}).record(
        {"type": "result", "cost_usd": 1.0, "usage": {"input_tokens": 900}}   # session-to-date
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


def test_nothing_stored_reads_as_no_ledger_rather_than_an_empty_one():
    """None, not `Usage()`. The caller pairs a ledger with a replay cursor,
    and "read, and it was empty" would licence trusting a cursor that belongs
    to no ledger at all."""
    assert Usage.from_dict(None) is None
    assert Usage.from_dict({}) is None


def test_a_corrupted_ledger_is_refused_rather_than_crashing_the_session():
    """`session.json` is a file on disk a user can edit. Refusing to open the
    workspace over a bad counter would cost them the workspace, not the counter.
    """
    assert Usage.from_dict({"turns": "seven", "input_tokens": None}) is None
    # A bad cost alone is enough: a total half-read is not a total, and there
    # is no honest way to show four of five numbers as if they belonged
    # together.
    assert Usage.from_dict({"turns": 2, "cost_usd": "free"}) is None
    assert Usage.from_dict({"turns": 2, "cost_usd": -1.0}) is None


# -- the run record -------------------------------------------------------


def test_a_summary_states_every_figure_the_provider_reported():
    stated = Usage().record(REPORT).summary()
    assert stated["exchanges"] == 1
    assert stated["cost_usd"] == 0.5
    assert stated["input_tokens"] == 10
    assert stated["output_tokens"] == 20
    assert stated["cache_write_tokens"] == 5
    assert stated["cache_read_tokens"] == 100
    assert stated["total_tokens"] == 135
    assert stated["reported"] == dict.fromkeys(("cost_usd", *Usage.COUNTERS), 1)


def test_a_summary_leaves_an_unreported_figure_null_rather_than_zero():
    """The same rule the chrome keeps, kept in the file someone compares runs by.

    A `0` here is worse than one on screen: `/status` can spell out
    "not reported by this backend" beside it, and a JSON reader will only ever
    see the number.
    """
    partial = Usage().record({"type": "result", "usage": {"input_tokens": 10}})
    stated = partial.summary()
    assert stated["input_tokens"] == 10
    assert stated["output_tokens"] is None
    assert stated["cost_usd"] is None
    assert stated["reported"] == {"cost_usd": 0, "input_tokens": 1, "output_tokens": 0,
                                  "cache_write_tokens": 0, "cache_read_tokens": 0}


def test_a_summary_of_an_exchange_nobody_reported_on_is_not_a_summary_of_zero():
    """A run the wall clock cut short gets no report, and was still billed."""
    stated = Usage().record({}).summary()
    assert stated["exchanges"] == 1
    assert stated["cost_usd"] is None
    assert stated["total_tokens"] is None
    assert not any(stated["reported"].values())
