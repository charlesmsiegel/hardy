"""Compaction Hardy owns, with a summary read off the workspace (#100).

The claim this module has to make good on is that almost every heading of a
useful mathematical summary is derivable rather than narrated — so almost
every test here asserts that a heading came from something checkable, and none
of them needs a model.
"""

from __future__ import annotations

import pytest

from hardy import compaction
from hardy.loop import Message, ToolCall


def _facts(**overrides) -> compaction.Facts:
    base = dict(
        goal="Show every group of order 15 is cyclic.",
        assumptions=[{
            "formal_name": "sylow_three",
            "lean_statement": "∀ p : ℕ, True",
            "source": "Isaacs, Theorem 1.7",
            "status": "user-approved",
        }],
        proved=["order_fifteen_cyclic", "helper"],
        open_declarations=["hard_step"],
        names=[{"formal_name": "order_fifteen_cyclic", "latex_name": "thm:main", "description": "the main result"}],
        attempts=[compaction.Attempt("save_lean", "Main.lean", "error: unknown identifier 'foo'")],
        next_steps=["hard_step rests on a hole"],
        modules=["Main", "Support"],
    )
    base.update(overrides)
    return compaction.Facts(**base)


def test_the_summary_carries_every_heading_in_order() -> None:
    rendered = compaction.summarize(_facts()).render()

    titles = [line[3:] for line in rendered.splitlines() if line.startswith("## ")]
    assert titles == [
        "Goal", "Standing assumptions", "Proved", "Open",
        "Naming registry", "Workspace", "Failed attempts", "Next steps",
    ]


def test_an_assumption_is_quoted_with_its_provenance() -> None:
    # An assumption a later turn restates loosely is an assumption a later turn
    # has weakened, and the human approved these exact words.
    rendered = compaction.summarize(_facts()).render()

    assert "`sylow_three` : ∀ p : ℕ, True [Isaacs, Theorem 1.7] (user-approved)" in rendered


def test_an_empty_heading_says_none_rather_than_vanishing() -> None:
    # A heading that disappears when it is empty reads as a summary that
    # forgot to mention it, which is the opposite of what this is for.
    rendered = compaction.summarize(_facts(assumptions=[], proved=[])).render()

    assert "## Standing assumptions\n- none" in rendered
    assert "## Proved\n- none" in rendered


def test_the_summary_says_it_is_a_record_and_not_a_conversation() -> None:
    assert compaction.summarize(_facts()).render().startswith(compaction.PREAMBLE)


# -- failed attempts, read off the transcript -------------------------------


def test_only_failures_are_gathered_and_lean_is_quoted() -> None:
    events = [
        {"type": "tool", "name": "save_lean", "arguments": {"path": "Main.lean"}, "result": {"ok": False, "output": "error: type mismatch"}},
        {"type": "tool", "name": "save_lean", "arguments": {"path": "Main.lean"}, "result": {"ok": True, "output": "saved"}},
        {"type": "user", "message": {"role": "user", "content": "keep going"}},
    ]

    found = compaction.failed_attempts(events)

    assert len(found) == 1
    assert found[0].line() == "save_lean (Main.lean): error: type mismatch"


def test_a_long_lean_failure_is_cut_rather_than_carried_whole() -> None:
    events = [{"type": "tool", "name": "check_lean", "arguments": {}, "result": {"ok": False, "output": "x" * 5000}}]

    line = compaction.failed_attempts(events)[0].line()

    assert "cut from 5000 characters" in line
    assert len(line) < 2200


def test_only_the_most_recent_failures_are_kept() -> None:
    events = [
        {"type": "tool", "name": f"tool{index}", "arguments": {}, "result": {"ok": False, "output": "no"}}
        for index in range(20)
    ]

    found = compaction.failed_attempts(events, limit=3)

    assert [item.tool for item in found] == ["tool17", "tool18", "tool19"]


# -- deciding when and where to cut -----------------------------------------


def _long(role: str, size: int = 4000, **kwargs) -> Message:
    return Message(role, text="x" * size, **kwargs)


def test_a_small_conversation_is_left_alone() -> None:
    outcome = compaction.plan(
        [Message("user", text="hello")], context_window=1000, reserve_tokens=100, keep_tokens=100
    )

    assert not outcome.needed
    assert outcome.cut == 0


def test_a_conversation_over_the_reserve_is_cut() -> None:
    messages = [_long("user"), _long("assistant"), _long("user"), _long("assistant")]

    outcome = compaction.plan(messages, context_window=3000, reserve_tokens=500, keep_tokens=1200)

    assert outcome.needed
    assert 0 < outcome.cut < len(messages)
    assert outcome.after < outcome.before


def test_a_cut_never_separates_a_tool_result_from_its_call() -> None:
    messages = [
        _long("user"),
        _long("assistant", tool_calls=(ToolCall("c1", "check_lean", {}),)),
        _long("tool_result", call_id="c1"),
    ]

    outcome = compaction.plan(messages, context_window=2000, reserve_tokens=100, keep_tokens=1)

    # Whatever it keeps, it may not resume from the answer to a question the
    # provider can no longer see it asked.
    assert messages[outcome.cut].role != "tool_result"


def test_a_conversation_with_no_legal_cut_is_left_whole() -> None:
    # Everything above the tail is a tool result, so there is nowhere legal to
    # resume from. Keeping it all is sound; cutting into it is not.
    messages = [_long("assistant", tool_calls=(ToolCall("c1", "x", {}),)), _long("tool_result", call_id="c1")]

    outcome = compaction.plan(messages, context_window=100, reserve_tokens=10, keep_tokens=1)

    assert not outcome.needed


def test_the_compacted_conversation_is_the_summary_then_the_tail() -> None:
    messages = [Message("user", text="old"), Message("assistant", text="older"), Message("user", text="recent")]
    summary = compaction.summarize(_facts())

    rebuilt = compaction.compacted(messages, 2, summary)

    assert len(rebuilt) == 2
    assert rebuilt[0].role == "user"
    assert rebuilt[0].text.startswith(compaction.PREAMBLE)
    assert rebuilt[1].text == "recent"


def test_the_estimate_counts_tool_arguments_too() -> None:
    # A turn whose whole content is a saved Lean file is the largest thing in a
    # mathematical conversation, and it arrives as a tool argument.
    plain = compaction.estimate_tokens([Message("assistant", text="")])
    with_call = compaction.estimate_tokens([
        Message("assistant", text="", tool_calls=(ToolCall("c1", "save_lean", {"source": "x" * 1000}),))
    ])

    assert with_call > plain + 200


def test_an_empty_message_still_costs_something() -> None:
    """Roles, block framing and the ids that pair a call with its result all
    reach the wire. Counting only text made the estimate lightest exactly where
    a tool-heavy conversation is heaviest -- and light in the direction that
    sends a request the provider refuses."""
    empty = compaction.estimate_tokens([Message("assistant", text="")])
    result = compaction.estimate_tokens([
        Message("tool_result", text="", call_id="toolu_01ABCDEFGHIJKLMNOP", name="save_lean")
    ])

    assert empty > 0
    assert result > empty


@pytest.mark.parametrize("field", ["usage", "usage_cursor", "provider_session"])
def test_the_spend_ledger_is_not_a_field_a_summary_can_carry(field: str) -> None:
    # `Facts` is the whole of what a summary may be assembled from, so the
    # withheld keys cannot reach the model through one by construction rather
    # than by a filter somebody has to remember to apply.
    assert field not in compaction.Facts.__dataclass_fields__


def test_the_summary_is_charged_against_the_budget_it_will_be_sent_in() -> None:
    # `compacted()` prepends the summary, so counting only the tail produced a
    # "compacted" request that could still be over the window -- and reported
    # an `after` smaller than what it actually built.
    messages = [_long("user"), _long("assistant"), _long("user"), _long("assistant")]

    without = compaction.plan(messages, context_window=3000, reserve_tokens=500, keep_tokens=1200)
    with_summary = compaction.plan(
        messages, context_window=3000, reserve_tokens=500, keep_tokens=1200, summary_tokens=800
    )

    assert with_summary.after >= without.after + 800
    # And the tail it keeps shrinks to make room, rather than the summary
    # being added on top of a tail sized as though it were free.
    assert with_summary.cut >= without.cut


def test_a_summary_larger_than_the_window_is_recorded_as_not_fitting() -> None:
    # A workspace can genuinely have more standing assumptions than fit.
    # Compacting is still the best move available; claiming it was enough is
    # not.
    messages = [_long("user"), _long("assistant"), _long("user"), _long("assistant")]

    outcome = compaction.plan(
        messages, context_window=3000, reserve_tokens=500, keep_tokens=1200, summary_tokens=100_000
    )

    assert outcome.needed
    assert not outcome.fits


def test_a_compaction_that_shrinks_the_request_says_it_fits() -> None:
    # Sized in bytes, as the estimate is: `_long` writes 4,000 ASCII
    # characters, which the bound charges as 4,000 tokens.
    messages = [_long("user"), _long("assistant"), _long("user"), _long("assistant")]

    outcome = compaction.plan(messages, context_window=10_500, reserve_tokens=1_750, keep_tokens=4_200)

    assert outcome.fits
    assert outcome.after <= outcome.available


def test_fits_is_measured_against_the_window_and_not_against_the_old_size() -> None:
    """"Smaller than before" was the wrong test. An oversized newest message
    leaves a request still over the limit while the older ones make it smaller
    than it was -- `true` for a request the provider will reject."""
    messages = [_long("user"), _long("assistant"), _long("user", size=40_000)]

    outcome = compaction.plan(messages, context_window=3000, reserve_tokens=500, keep_tokens=1200)

    assert outcome.needed
    assert outcome.after < outcome.before  # what the old test would have accepted
    assert not outcome.fits                # and what the window actually says


def test_the_estimate_is_a_bound_rather_than_a_prose_ratio() -> None:
    """A BPE token covers at least one byte, so nothing costs more tokens than
    it has bytes. That is the only bound available without the provider's
    tokenizer, and every ratio tried here was an average dressed as a rule:
    `1/3.5` is an English-prose figure, while a Hardy transcript is full of `∀`
    and `⟨⟩`, may not be in ASCII at all, and even its ASCII is often a hash or
    a wall of JSON that tokenizes near one token per character."""
    assert compaction.estimate_text("a" * 350) == 350
    assert compaction.estimate_text("∀" * 350) == len("∀".encode()) * 350
    assert compaction.estimate_text("🔥" * 10) == len("🔥".encode()) * 10
    # Additive, so no arrangement of the same bytes is cheaper than another.
    mixed = "a" * 350 + "∀" * 350
    assert compaction.estimate_text(mixed) == compaction.estimate_text("a" * 350) + compaction.estimate_text("∀" * 350)


def test_a_small_configured_window_still_leaves_room_to_compact_into() -> None:
    """`context_window` is settable because the window belongs to the endpoint.
    Configured below four times the flat reserve, the whole window was reserved:
    `available` became zero, every plan reported that nothing legal could be
    kept, and a request that would have fitted went out with no compaction."""
    conversation = [
        Message("user", text="x" * 20_000),
        Message("assistant", text="y" * 20_000),
        Message("user", text="the recent part"),
    ]

    outcome = compaction.plan(
        conversation,
        context_window=8_192,
        reserve_tokens=compaction.RESERVE_TOKENS,
        keep_tokens=compaction.RECENT_TOKENS,
    )

    assert outcome.available > 0
    assert outcome.needed
    assert outcome.cut > 0


def test_a_window_large_enough_keeps_the_flat_reserve() -> None:
    """The scaling is a floor for small windows, not a new rule for every one."""
    conversation = [Message("user", text="x" * 1_000_000)]

    outcome = compaction.plan(
        conversation,
        context_window=200_000,
        reserve_tokens=compaction.RESERVE_TOKENS,
        keep_tokens=compaction.RECENT_TOKENS,
    )

    assert outcome.available == 200_000 - compaction.RESERVE_TOKENS


def test_each_tool_call_is_charged_its_own_framing() -> None:
    """A turn asking for six tools is seven content blocks, not one.

    Each `tool_use` is a separate structured block with its own field names and
    JSON punctuation, so charging the framing once per message understated a
    tool-heavy conversation -- and the shortfall grew with every call, which is
    the shape of conversation Hardy has.
    """
    quiet = [Message("assistant", text="thinking")]
    busy = [Message(
        "assistant",
        text="thinking",
        tool_calls=tuple(ToolCall(f"c{index}", "x", {}) for index in range(6)),
    )]

    extra = compaction.estimate_tokens(busy) - compaction.estimate_tokens(quiet)
    # Six blocks of framing, plus whatever the ids and names themselves cost.
    assert extra >= 6 * compaction.FRAMING_PER_BLOCK


def test_the_reserve_is_never_smaller_than_what_the_model_may_write() -> None:
    """A quarter of 16,384 is 4,096 while the transport asks for up to 8,192
    output tokens, so the planner called a request fitting that the endpoint
    has no room to answer. The reserve is an allowance for the reply; it cannot
    be smaller than the reply it allows for."""
    conversation = [Message("user", text="x" * 200_000)]

    outcome = compaction.plan(
        conversation,
        context_window=16_384,
        reserve_tokens=compaction.RESERVE_TOKENS,
        keep_tokens=compaction.RECENT_TOKENS,
        output_tokens=8_192,
    )

    assert outcome.available == 16_384 - 8_192
    # And a large window keeps the flat reserve, which already exceeds the cap.
    big = compaction.plan(
        conversation,
        context_window=200_000,
        reserve_tokens=compaction.RESERVE_TOKENS,
        keep_tokens=compaction.RECENT_TOKENS,
        output_tokens=8_192,
    )
    assert big.available == 200_000 - compaction.RESERVE_TOKENS


def test_a_request_nothing_can_be_cut_from_is_reported_as_overflow() -> None:
    """`needed=False` over an oversized request is not "the window is fine".

    A caller reading only `needed` sent it believing there was room, and the
    record showed nothing at all where an oversized request was about to go
    out. There is still no compaction to perform -- summarising nothing and
    keeping everything is not one -- so the plan says both things.
    """
    # One user message, so `first_legal_cut` can only land at 0: there is
    # nothing above the tail to summarise.
    outcome = compaction.plan(
        [compaction.Message("user", text="x" * 20_000)],
        context_window=9000,
        reserve_tokens=100,
        keep_tokens=10,
    )

    assert outcome.needed is False
    assert outcome.overflow is True
    assert outcome.before > outcome.available
    assert outcome.fits is False


def test_a_request_that_fits_is_not_an_overflow() -> None:
    """The converse, so `overflow` means what it says rather than "no cut"."""
    outcome = compaction.plan(
        [compaction.Message("user", text="short")],
        context_window=200_000,
        reserve_tokens=100,
        keep_tokens=1000,
    )

    assert outcome.needed is False
    assert outcome.overflow is False
    assert outcome.fits is True
