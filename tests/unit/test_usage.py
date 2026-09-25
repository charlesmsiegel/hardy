"""The spend ledger: what a session has cost, as the provider reported it.

The property under test throughout is that *unreported* and *zero* stay
distinguishable. A backend that says nothing must never be rendered as a number,
because a reader cannot tell a free session from an unmeasured one, and the
whole point of putting the meter in front of a user mid-session is that they act
on it.
"""

from __future__ import annotations

import pytest

from hardy.agents.usage import Usage, combined


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


def test_a_restart_forgets_the_baselines_its_report_did_not_restate():
    """A restart resets every counter, not only those its report happened to
    state. A field the restart report omitted must not carry the old session's
    baseline forward, or the next report that does state it -- necessarily
    smaller -- reads as a second restart and is added whole (#322)."""
    spent = (
        Usage()
        .record({"session_id": "S1", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 50000}})
        # A restart that says nothing about cache reads.
        .record({"session_id": "S2", "usage": {"input_tokens": 100}})
        # The same session, 100 input tokens and 300 cache reads on.
        .record({"session_id": "S2", "usage": {"input_tokens": 200, "cache_read_input_tokens": 300}})
    )
    assert spent.input_tokens == 1200   # not 1300
    assert spent.cache_read_tokens == 50300


def test_a_restart_leaves_only_its_own_figures_as_baselines():
    """What persists in `session.json` after a restart holds no stale field,
    so a reopened workspace differences exactly as the live one would."""
    spent = (
        Usage()
        .record({"session_id": "S1", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 50000}})
        .record({"session_id": "S2", "usage": {"input_tokens": 100}})
    )
    assert spent.baselines == {"input_tokens": 100}
    reopened = Usage.from_dict(spent.as_dict())
    assert reopened is not None and reopened.baselines == {"input_tokens": 100}
    after = reopened.record({"session_id": "S2", "usage": {"input_tokens": 200, "cache_read_input_tokens": 300}})
    assert (after.input_tokens, after.cache_read_tokens) == (1200, 50300)


def test_a_report_marked_not_cumulative_is_counted_whole():
    """`ClaudeAgentRuntime._note` marks a report `cumulative: False` when it
    states no more than the exchange's own messages did -- so it carries no
    earlier exchange, however its figures compare with the last report's."""
    spent = (
        Usage()
        .record({"session_id": "t1", "cost_usd": 0.10, "usage": {"input_tokens": 100}})
        .record({"session_id": "t1", "cost_usd": 0.25, "usage": {"input_tokens": 260}, "cumulative": False})
    )
    assert spent.cost_usd == pytest.approx(0.35)
    assert spent.input_tokens == 360


def test_a_climb_smaller_than_the_exchanges_own_messages_is_a_restart():
    """Whichever way the CLI reports, a resumed exchange that restored the
    running total states at least the old total plus what this exchange's own
    messages moved. A report short of that did not restore, so it is this
    exchange alone and counts whole -- differencing it would count less than
    the messages the provider streamed."""
    spent = (
        Usage()
        .record({"session_id": "t1", "cost_usd": 0.10,
                 "usage": {"input_tokens": 100, "cache_read_input_tokens": 5_000}})
        .record({"session_id": "t1", "cost_usd": 0.25,
                 "usage": {"input_tokens": 260, "cache_read_input_tokens": 9_000},
                 "exchange_usage": {"input_tokens": 260, "cache_read_input_tokens": 9_000}})
    )
    assert spent.cost_usd == pytest.approx(0.35)
    assert (spent.input_tokens, spent.cache_read_tokens) == (360, 14_000)


def test_a_restored_total_is_still_differenced_when_the_messages_agree():
    """The same guard leaves a genuinely session-to-date report alone: it
    climbs by at least what the exchange's messages moved."""
    spent = (
        Usage()
        .record({"session_id": "t1", "cost_usd": 0.10,
                 "usage": {"input_tokens": 100, "cache_read_input_tokens": 5_000},
                 "model_usage": {"input_tokens": 100, "cache_read_input_tokens": 5_000}})
        .record({"session_id": "t1", "cost_usd": 0.25,
                 "usage": {"input_tokens": 260, "cache_read_input_tokens": 9_000}, "cumulative": True,
                 "exchange_usage": {"input_tokens": 160, "cache_read_input_tokens": 4_000},
                 "model_usage": {"input_tokens": 260, "cache_read_input_tokens": 9_000}})
    )
    assert spent.cost_usd == pytest.approx(0.25)
    assert (spent.input_tokens, spent.cache_read_tokens) == (260, 9_000)


def _guarded(session, cost, usage, own, model=None, **extra):
    """A report as `ClaudeAgentRuntime._note` writes one: the CLI's figures,
    the exchange's own streamed messages, and `modelUsage` summed."""
    return {"type": "result", "session_id": session, "cost_usd": cost, "usage": usage,
            "exchange_usage": own, "model_usage": model, **extra}


def _tokens(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _fold(*events) -> Usage:
    spent = Usage()
    for event in events:
        spent = spent.record(event)
    return spent


def test_the_documented_cli_counts_cost_once_and_per_turn_tokens_whole():
    """Claude Code 2.1.282 documents `total_cost_usd` and `modelUsage` as the
    resumed session's running total, and `usage` as possibly one turn's. Three
    $0.50 exchanges of 1000/100 tokens then report 0.50, 1.00, 1.50 beside
    per-turn usage -- $1.50 and 3000 tokens, not $3.00 (#197 review, I1)."""
    spent = _fold(*(
        _guarded("t", 0.5 * n, _tokens(1000, 100), _tokens(1000, 100), _tokens(1000 * n, 100 * n))
        for n in (1, 2, 3)
    ))
    assert spent.cost_usd == pytest.approx(1.5)
    assert (spent.input_tokens, spent.output_tokens) == (3000, 300)


def test_a_cli_that_resumes_from_zero_counts_every_report_whole():
    """Before 2.1.277 a headless resume started cost and usage at zero: every
    report is that exchange alone, and differencing any of them undercounts."""
    spent = _fold(*(_guarded("t", 0.5, _tokens(1000, 100), _tokens(1000, 100), _tokens(1000, 100)) for _ in range(3)))
    assert spent.cost_usd == pytest.approx(1.5)
    assert (spent.input_tokens, spent.output_tokens) == (3000, 300)


def test_a_cli_that_reports_every_figure_as_a_running_total_is_differenced_throughout():
    """What the interactive and batch ledgers already got right before the
    guard: every figure a running total, each exchange its own messages."""
    spent = _fold(*(
        _guarded("t", 0.5 * n, _tokens(1000 * n, 100 * n), _tokens(1000, 100), _tokens(1000 * n, 100 * n))
        for n in (1, 2, 3)
    ))
    assert spent.cost_usd == pytest.approx(1.5)
    assert (spent.input_tokens, spent.output_tokens) == (3000, 300)


def test_calls_the_cli_never_streamed_make_a_running_total_count_whole():
    """Compaction runs through the query pipeline and into `modelUsage`
    without streaming a message, so a running total climbs by more than the
    exchange's own. That is indistinguishable from a per-exchange report with
    such calls, and the ledger overcounts rather than risk the other reading:
    never less than the $1.00 spent, here $1.60."""
    spent = _fold(
        _guarded("t", 0.6, _tokens(1000, 100), _tokens(1000, 100), _tokens(1000, 100)),
        # 1000/100 streamed, plus a compaction call of 3000/400 that was not.
        _guarded("t", 1.0, _tokens(1000, 100), _tokens(1000, 100), _tokens(5000, 600)),
    )
    assert spent.cost_usd == pytest.approx(1.6)
    assert spent.cost_usd >= 1.0


def test_unstreamed_calls_larger_than_the_last_total_are_not_differenced_away():
    """#197 review M3: a per-exchange report of 40/20 whose exchange streamed
    20/10, after a session total of 10/5. Differencing gives 30/15 and loses
    the 20/10 of calls nobody streamed; the climb is not the exchange's own,
    so it counts whole."""
    spent = _fold(
        _guarded("t", 0.1, _tokens(10, 5), _tokens(10, 5), _tokens(10, 5)),
        _guarded("t", 0.4, _tokens(40, 20), _tokens(20, 10), _tokens(40, 20)),
    )
    assert (spent.input_tokens, spent.output_tokens) == (50, 25)
    assert spent.cost_usd == pytest.approx(0.5)


def test_a_cost_with_no_model_usage_to_weigh_it_against_counts_whole():
    """Without `modelUsage` nothing shares the cost's lifecycle, and a cost
    that merely climbed may still be one exchange's own."""
    spent = _fold(
        _guarded("t", 0.1, _tokens(100, 10), _tokens(100, 10)),
        _guarded("t", 0.3, _tokens(100, 10), _tokens(100, 10)),
    )
    assert spent.cost_usd == pytest.approx(0.4)


def test_a_guarded_report_with_no_floor_counts_whole():
    """#197 review M2: the runtime ran its guard, but no streamed message
    stated usage, so a climb cannot be told from an exchange of its own."""
    spent = _fold(
        _guarded("t", 0.1, _tokens(100, 10), _tokens(100, 10), _tokens(100, 10)),
        _guarded("t", 0.3, _tokens(300, 30), None, _tokens(300, 30)),
    )
    assert spent.cost_usd == pytest.approx(0.4)
    assert (spent.input_tokens, spent.output_tokens) == (400, 40)


def test_a_guarded_cost_with_no_token_counters_counts_whole():
    """#197 review M2: a report stating a cost and no tokens has nothing to
    prove the cost a running total by."""
    spent = _fold(
        _guarded("t", 0.1, _tokens(100, 10), _tokens(100, 10), _tokens(100, 10)),
        _guarded("t", 0.3, None, _tokens(100, 10), _tokens(200, 20)),
    )
    assert spent.cost_usd == pytest.approx(0.4)


def test_a_restart_in_one_family_keeps_the_others_baselines():
    """Cost proven a running total while tokens restarted: the cost baseline
    carries on, and the next report is still differenced against it."""
    spent = _fold(*(
        _guarded("t", 0.5 * n, _tokens(1000, 100), _tokens(1000, 100), _tokens(1000 * n, 100 * n))
        for n in (1, 2)
    ))
    assert spent.baselines["cost_usd"] == 1.0
    assert spent.baselines["model_usage.input_tokens"] == 2000
    reopened = Usage.from_dict(spent.as_dict())
    assert reopened is not None
    after = reopened.record(_guarded("t", 1.5, _tokens(1000, 100), _tokens(1000, 100), _tokens(3000, 300)))
    assert after.cost_usd == pytest.approx(1.5)


def test_ledgers_combine_figure_by_figure_keeping_silence_silent():
    """One ledger per provider session, summed for a run's record. A figure
    no ledger stated stays None rather than becoming a summed 0."""
    first = Usage().record({"session_id": "a", "cost_usd": 0.5, "usage": {"input_tokens": 10}})
    second = Usage().record({"session_id": "b", "usage": {"input_tokens": 5, "output_tokens": 2}})
    merged = combined([first.summary(), second.summary()])
    assert merged["exchanges"] == 2
    assert merged["cost_usd"] == 0.5 and merged["reported"]["cost_usd"] == 1
    assert merged["input_tokens"] == 15 and merged["reported"]["input_tokens"] == 2
    assert merged["output_tokens"] == 2
    assert merged["cache_read_tokens"] is None
    assert merged["total_tokens"] == 17
    assert combined([]) == Usage().summary()


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
