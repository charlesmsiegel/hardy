"""The loop Hardy runs itself (#23).

Four things moved back across the boundary when Hardy stopped handing the turn
loop to a provider's SDK, and each of them is a test here: Hardy decides
whether a provider is called at all, it counts the turns, it keeps the wall
clock, and the conversation is a list it owns rather than a thread somebody
else resumes. The provider is scripted throughout — nothing here goes near a
network, which is the point of keeping the transport out of this module.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from hardy.loop import AgentLoop, Message, ProviderTurn, ToolCall, TurnLimitReached, first_legal_cut
from hardy.models import ToolResult


class ScriptedProvider:
    """Answers each call with the next turn in its script."""

    model = "scripted@test"

    def __init__(self, script: list[ProviderTurn]) -> None:
        self.script = list(script)
        self.seen: list[list[Message]] = []
        self.timeouts: list[float | None] = []
        self.calls = 0

    def complete(self, *, system, messages, specs, timeout=None) -> ProviderTurn:
        self.calls += 1
        self.seen.append(list(messages))
        self.timeouts.append(timeout)
        if not self.script:
            return ProviderTurn(text="nothing left to say")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return step


def _loop(script, dispatch=None, **kwargs) -> tuple[AgentLoop, ScriptedProvider, list]:
    provider = ScriptedProvider(script)
    observed: list[dict[str, Any]] = []
    loop = AgentLoop(
        provider,
        system_prompt="be exact",
        specs=[{"function": {"name": "check_proof", "parameters": {"type": "object"}}}],
        dispatch=dispatch or (lambda name, arguments: ToolResult(True, "ok")),
        observe=observed.append,
        **kwargs,
    )
    return loop, provider, observed


def _drain(loop: AgentLoop, text: str) -> list:
    return list(loop.run(text))


def test_a_reply_with_no_tool_call_ends_the_exchange() -> None:
    loop, provider, observed = _loop([ProviderTurn(text="two is even")])

    events = _drain(loop, "is two even?")

    assert provider.calls == 1
    assert [event.kind for event in events] == ["text", "reply"]
    assert events[-1].text == "two is even"
    assert loop.turns == 1
    assert [item["type"] for item in observed] == ["assistant", "result"]


def test_hardy_runs_the_tool_and_sends_its_result_back() -> None:
    ran: list[tuple[str, dict]] = []

    def dispatch(name, arguments):
        ran.append((name, arguments))
        return ToolResult(True, "Lean accepted it")

    loop, provider, _ = _loop(
        [
            ProviderTurn(tool_calls=(ToolCall("call-1", "check_proof", {"proof": "by rfl"}),)),
            ProviderTurn(text="done"),
        ],
        dispatch=dispatch,
    )

    events = _drain(loop, "prove it")

    assert ran == [("check_proof", {"proof": "by rfl"})]
    assert [event.kind for event in events] == ["tool_use", "tool_result", "text", "reply"]
    # The result went back as its own message, immediately after the assistant
    # turn that asked for it.
    roles = [message.role for message in loop.messages]
    assert roles == ["user", "assistant", "tool_result", "assistant"]
    assert loop.messages[2].text == "Lean accepted it"


def test_the_turn_bound_is_counted_here_and_stops_the_exchange() -> None:
    loop, provider, observed = _loop(
        [ProviderTurn(tool_calls=(ToolCall(f"c{index}", "check_proof", {}),)) for index in range(10)],
        max_turns=3,
    )

    with pytest.raises(TurnLimitReached, match="3-turn bound"):
        _drain(loop, "keep going")

    # Three provider calls, not the SDK's idea of three of something else.
    assert provider.calls == 3
    assert loop.turns == 3
    assert {"type": "turn_limit", "turns": 3, "max_turns": 3} in observed


def test_the_wall_clock_is_kept_here_too() -> None:
    def slow(name, arguments):
        time.sleep(0.05)
        return ToolResult(True, "ok")

    loop, provider, observed = _loop(
        [ProviderTurn(tool_calls=(ToolCall(f"c{index}", "check_proof", {}),)) for index in range(50)],
        dispatch=slow,
        wall_seconds=0.1,
    )

    with pytest.raises(TimeoutError, match="wall-clock budget"):
        _drain(loop, "keep going")

    assert any(item["type"] == "wall_clock_limit" for item in observed)
    # Bounded, which is the whole claim: it stopped long before the script did.
    assert provider.calls < 50


def test_hardy_can_decline_to_call_the_provider_at_all() -> None:
    loop, provider, observed = _loop(
        [ProviderTurn(text="never asked")],
        before_turn=lambda messages: "closed by `simp` before a model turn was spent",
    )

    events = _drain(loop, "prove it")

    assert provider.calls == 0
    assert events[-1].text == "closed by `simp` before a model turn was spent"
    assert any(item["type"] == "declined_turn" for item in observed)
    assert loop.turns == 0
    # Recorded as Hardy's own words. Kept in the assistant's role it would put
    # them in the model's mouth in every later exchange that reads this
    # conversation back.
    assert loop.messages[-1].role == "user"


def test_a_declined_turn_still_reports_the_exchange() -> None:
    # The ledger is entitled to know an exchange happened and cost nothing,
    # rather than being told nothing at all.
    loop, _, observed = _loop([], before_turn=lambda messages: "not this time")

    _drain(loop, "prove it")

    report = next(item for item in observed if item["type"] == "result")
    assert report["turns"] == 0
    assert report["usage"] is None
    assert report["enforced_by"] == "hardy"


def test_usage_is_reported_session_to_date() -> None:
    # `usage.Usage` differences each figure against the last one stated for it,
    # because the SDK reports session-to-date. A per-exchange report climbing
    # past its predecessor would be counted as the difference and undercount
    # every exchange after the first.
    loop, _, observed = _loop([ProviderTurn(text="one", usage={"input_tokens": 10, "output_tokens": 4})])
    _drain(loop, "first")
    loop._provider.script = [ProviderTurn(text="two", usage={"input_tokens": 30, "output_tokens": 6})]
    _drain(loop, "second")

    reports = [item for item in observed if item["type"] == "result"]
    assert reports[0]["usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert reports[1]["usage"] == {"input_tokens": 40, "output_tokens": 10}


def test_the_conversation_carries_across_exchanges() -> None:
    loop, provider, _ = _loop([ProviderTurn(text="first"), ProviderTurn(text="second")])

    _drain(loop, "one")
    _drain(loop, "two")

    assert [message.role for message in loop.messages] == ["user", "assistant", "user", "assistant"]
    # The second call saw the first exchange, which is what "Hardy owns the
    # conversation" has to mean if it means anything.
    assert len(provider.seen[1]) == 3


def test_cancelling_stops_the_next_provider_call() -> None:
    def dispatch(name, arguments):
        loop.cancel()
        return ToolResult(True, "ok")

    loop, provider, _ = _loop(
        [
            ProviderTurn(tool_calls=(ToolCall("c1", "check_proof", {}),)),
            ProviderTurn(text="should never be asked"),
        ],
        dispatch=lambda name, arguments: dispatch(name, arguments),
    )

    events = _drain(loop, "prove it")

    assert provider.calls == 1
    assert events[-1].kind == "reply"


def test_a_cancelled_turn_still_answers_every_tool_call() -> None:
    # A provider left with a `tool_use` and no `tool_result` cannot be sent
    # another message at all, so the answer is a refusal rather than silence.
    # The window this is really about: a cancel arriving from the terminal
    # while a Lean check is running, with more calls in the same batch behind
    # it. The one in flight is not unwound -- that limit is stated in
    # `AgentLoop` -- and the ones after it are refused rather than skipped.
    def dispatch(name, arguments):
        loop.cancel()
        return ToolResult(True, "Lean accepted it")

    loop, _, _ = _loop(
        [ProviderTurn(tool_calls=(ToolCall("c1", "check_proof", {}), ToolCall("c2", "check_proof", {})))],
        dispatch=lambda name, arguments: dispatch(name, arguments),
    )

    _drain(loop, "prove it")

    results = [message for message in loop.messages if message.role == "tool_result"]
    assert [message.ok for message in results] == [True, False]
    assert "cancelled" in results[1].text


def test_a_compactor_may_replace_the_conversation_before_a_turn() -> None:
    seen: list[int] = []

    def compact(messages):
        seen.append(len(messages))
        return [Message("user", text="summary")] if len(messages) > 2 else None

    loop, provider, _ = _loop(
        [ProviderTurn(text="a"), ProviderTurn(text="b")],
        compact=compact,
    )
    _drain(loop, "one")
    _drain(loop, "two")

    assert seen == [1, 3]
    assert [message.text for message in provider.seen[1]] == ["summary"]


# -- legal cut points -------------------------------------------------------


def _conversation() -> list[Message]:
    return [
        Message("user", text="one"),
        Message("assistant", tool_calls=(ToolCall("c1", "check_proof", {}),)),
        Message("tool_result", call_id="c1", name="check_proof", ok=True),
        Message("assistant", text="two"),
    ]


def test_a_cut_never_lands_on_a_tool_result() -> None:
    # Keeping two would cut between the call and its answer. Walking back is
    # the only direction that is safe: it keeps more than was asked for.
    assert first_legal_cut(_conversation(), 2) == 1


def test_a_cut_that_already_lands_legally_is_left_alone() -> None:
    assert first_legal_cut(_conversation(), 1) == 3


def test_asking_to_keep_everything_keeps_everything() -> None:
    assert first_legal_cut(_conversation(), 99) == 0
    assert first_legal_cut(_conversation(), 4) == 0


# -- the deadline, across a provider call rather than only between them ------


def test_the_provider_is_handed_the_wall_clock_it_has_left() -> None:
    loop, provider, _ = _loop([ProviderTurn(text="done")], wall_seconds=30)

    _drain(loop, "prove it")

    assert provider.timeouts[0] is not None
    assert 0 < provider.timeouts[0] <= 30


def test_an_unbounded_loop_hands_the_provider_no_deadline() -> None:
    loop, provider, _ = _loop([ProviderTurn(text="done")])

    _drain(loop, "prove it")

    assert provider.timeouts == [None]


def test_a_call_that_overruns_the_budget_is_a_timeout_not_a_finished_run() -> None:
    # The deadline is checked between calls, and the overrun happens *inside*
    # one. Without the check afterwards, a slow reply carrying no tool call
    # returned normally and the run was recorded as having finished.
    def slow() -> ProviderTurn:
        time.sleep(0.15)
        return ProviderTurn(text="took too long")

    loop, _, observed = _loop([slow], wall_seconds=0.05)

    with pytest.raises(TimeoutError, match="wall-clock budget"):
        _drain(loop, "prove it")

    assert any(item["type"] == "wall_clock_limit" for item in observed)


def test_a_provider_error_is_reported_as_a_failed_turn() -> None:
    loop, _, observed = _loop([RuntimeError("rate limited")])

    with pytest.raises(RuntimeError, match="rate limited"):
        _drain(loop, "prove it")

    report = next(item for item in observed if item["type"] == "result")
    assert report["is_error"] is True
    assert "rate limited" in report["error"]
    # The attempt was a provider call: it may have been billed for, and it
    # spent a turn of the bound.
    assert report["turns"] == 1
    assert loop.turns == 1


def test_a_failed_exchange_does_not_restate_the_previous_totals() -> None:
    # The running total is session-to-date, so an exchange that reported
    # nothing would otherwise hand back its predecessor's figures as though it
    # had stated them itself.
    loop, _, observed = _loop([ProviderTurn(text="one", usage={"input_tokens": 10})])
    _drain(loop, "first")
    loop._provider.script = [RuntimeError("connection reset")]
    with pytest.raises(RuntimeError):
        _drain(loop, "second")

    reports = [item for item in observed if item["type"] == "result"]
    assert reports[0]["usage"] == {"input_tokens": 10}
    assert reports[1]["usage"] is None


def test_a_tool_that_raises_is_still_answered() -> None:
    """A dangling `tool_use` is not a bad turn, it is a dead conversation.

    Every later request built from it is one the API refuses outright, so a
    single unlucky write would end the session rather than the turn.
    """
    def dispatch(name, arguments):
        raise OSError("disk full")

    loop, _, observed = _loop(
        [
            ProviderTurn(tool_calls=(ToolCall("c1", "save_lean", {}),)),
            ProviderTurn(text="carrying on"),
        ],
        dispatch=dispatch,
    )

    events = _drain(loop, "save it")

    answered = [message for message in loop.messages if message.role == "tool_result"]
    assert [message.ok for message in answered] == [False]
    assert "disk full" in answered[0].text
    assert any(item["type"] == "tool_error" for item in observed)
    # And the exchange continued rather than dying with the tool.
    assert events[-1].text == "carrying on"


def test_a_truncated_reply_is_not_presented_as_a_finished_one() -> None:
    loop, _, observed = _loop([ProviderTurn(text="half an ans", stop_reason="max_tokens")])

    events = _drain(loop, "explain it")

    assert any(item["type"] == "truncated" and item["stop_reason"] == "max_tokens" for item in observed)
    notice = next(event for event in events if event.kind == "notice")
    assert "cut off rather than complete" in notice.text


def test_an_ordinary_finish_says_nothing_extra() -> None:
    loop, _, _ = _loop([ProviderTurn(text="all of it", stop_reason="end_turn")])

    events = _drain(loop, "explain it")

    assert not [event for event in events if event.kind == "notice"]


def test_a_provider_that_states_no_stop_reason_is_not_accused_of_truncating() -> None:
    loop, _, _ = _loop([ProviderTurn(text="all of it")])

    assert not [event for event in _drain(loop, "explain it") if event.kind == "notice"]


def test_a_reply_that_lands_after_a_cancel_is_recorded_and_not_published() -> None:
    """This transport cannot abort a request in flight, so the answer can come
    back after Hardy has reported the turn stopped. Handing it to the user then
    is worse than handing them nothing."""
    def cancelling() -> ProviderTurn:
        loop.cancel()
        return ProviderTurn(text="the late answer")

    loop, _, observed = _loop([cancelling])

    events = _drain(loop, "prove it")

    assert "the late answer" not in events[-1].text
    discarded = next(item for item in observed if item["type"] == "discarded")
    assert discarded["message"]["content"] == "the late answer"
    # Not smuggled into the conversation either: the user never saw it.
    assert [message.role for message in loop.messages] == ["user"]


def test_the_budget_is_read_again_before_each_tool_call() -> None:
    """One response can ask for several Lean checks, each able to run to its
    own process timeout. Checked once for the batch, a nominally bounded run
    overran by minutes per queued call while nothing looked again."""
    ran: list[str] = []

    def slow(name, arguments):
        ran.append(name)
        time.sleep(0.12)
        return ToolResult(True, "ok")

    loop, _, observed = _loop(
        [ProviderTurn(tool_calls=tuple(ToolCall(f"c{i}", "check_proof", {}) for i in range(4)))],
        dispatch=slow,
        wall_seconds=0.1,
    )

    # The exchange ends on the deadline, as it should -- what is under test is
    # what the queued calls did on the way there.
    with pytest.raises(TimeoutError):
        _drain(loop, "prove it")

    # The first ran and ate the budget; the rest were refused rather than run.
    assert ran == ["check_proof"]
    assert sum(1 for item in observed if item["type"] == "skipped_tool") == 3
    # And every call still has an answer: a `tool_use` the provider issued and
    # nothing answered is a request it will refuse outright.
    answered = [message for message in loop.messages if message.role == "tool_result"]
    assert len(answered) == 4
    assert [message.ok for message in answered] == [True, False, False, False]


def test_a_hook_that_eats_the_budget_does_not_get_to_open_a_request() -> None:
    """`before_turn` is where a cheap-closer ladder runs, and running Lean is
    exactly the thing that eats a budget. Checked only at the top of the loop,
    a hook that spent the last of it still opened a request -- with a timeout
    of zero handed to the transport as though that were a bound somebody
    chose."""
    def slow_hook(messages):
        time.sleep(0.12)
        return None

    loop, provider, observed = _loop(
        [ProviderTurn(text="never asked")], wall_seconds=0.1, before_turn=slow_hook
    )

    with pytest.raises(TimeoutError):
        _drain(loop, "prove it")

    assert provider.calls == 0
    assert any(item["type"] == "wall_clock_limit" for item in observed)


def test_a_compactor_that_eats_the_budget_is_caught_by_the_same_check() -> None:
    def slow_compactor(messages):
        time.sleep(0.12)
        return None

    loop, provider, _ = _loop(
        [ProviderTurn(text="never asked")], wall_seconds=0.1, compact=slow_compactor
    )

    with pytest.raises(TimeoutError):
        _drain(loop, "prove it")

    assert provider.calls == 0


def test_a_counter_this_exchange_did_not_state_is_not_restated() -> None:
    """`usage.Usage` reads a field it is handed as reported, and advances that
    field's coverage count. A figure repeated from an earlier exchange would
    make the meter claim to cover turns it never measured."""
    loop, _, observed = _loop([ProviderTurn(text="one", usage={"input_tokens": 10, "cache_read_input_tokens": 400})])
    _drain(loop, "first")
    loop._provider.script = [ProviderTurn(text="two", usage={"input_tokens": 30})]
    _drain(loop, "second")

    reports = [item for item in observed if item["type"] == "result"]
    assert reports[0]["usage"] == {"cache_read_input_tokens": 400, "input_tokens": 10}
    # The cache counter is absent, not repeated at its stale value.
    assert reports[1]["usage"] == {"input_tokens": 40}


def test_a_hook_that_raises_is_reported_as_a_failed_exchange() -> None:
    """`before_turn` is Hardy's own work, and Hardy's own work can fail.

    Only the provider call used to be wrapped, so an exchange that died in a
    hook -- a closer ladder that could not reach Lean -- emitted `is_error:
    false` beside `turns: 0` from the `finally`, which reads as an exchange
    that finished without needing a turn rather than one that never got to
    take its first.
    """
    def hook(messages):
        raise RuntimeError("the Lean toolchain is missing")

    loop, _, observed = _loop([ProviderTurn(text="never asked")], before_turn=hook)

    with pytest.raises(RuntimeError, match="toolchain is missing"):
        _drain(loop, "prove it")

    report = next(item for item in observed if item["type"] == "result")
    assert report["is_error"] is True
    assert "the Lean toolchain is missing" in report["error"]
    # No provider call was made, so no turn was spent -- which is the fact the
    # `is_error` flag has to carry, because the turn count cannot.
    assert report["turns"] == 0


def test_a_compactor_that_raises_is_reported_the_same_way() -> None:
    def compact(messages):
        raise OSError("transcript.jsonl disappeared")

    loop, _, observed = _loop([ProviderTurn(text="never asked")])
    loop.attach_compactor(compact)

    with pytest.raises(OSError, match="transcript.jsonl"):
        _drain(loop, "prove it")

    report = next(item for item in observed if item["type"] == "result")
    assert report["is_error"] is True
    assert "transcript.jsonl disappeared" in report["error"]


def test_a_cancel_during_a_hook_stops_the_turn_it_was_meant_to_stop() -> None:
    """The cancel is read at the top of the loop, before the hooks run.

    A summary that scans the workspace or a ladder that elaborates Lean is
    exactly long enough for the user to press Escape during one, and this
    transport cannot abort a request once it is open -- so without a second
    read the stopped turn still opened one, and the user waited out a whole
    model call for a reply that was then discarded, having been billed for.
    """
    loop, provider, observed = _loop([ProviderTurn(text="should never be asked")])

    def compact(messages):
        loop.cancel()
        return None

    loop.attach_compactor(compact)
    _drain(loop, "prove it")

    assert provider.calls == 0
    report = next(item for item in observed if item["type"] == "result")
    assert report["turns"] == 0
    assert report["is_error"] is False


def test_a_cancel_during_the_before_turn_hook_is_read_too() -> None:
    def hook(messages):
        loop.cancel()
        return None

    loop, provider, _ = _loop([ProviderTurn(text="should never be asked")], before_turn=hook)

    _drain(loop, "prove it")

    assert provider.calls == 0
    assert loop.turns == 0


def test_a_truncated_reply_is_disclosed_even_when_it_asked_for_tools() -> None:
    """The case where the disclosure matters most was the case that lost it.

    A reply that ran out of output tokens *while* issuing tool calls used to
    skip the notice entirely: the calls dispatched, the exchange carried on,
    and nothing in the trajectory said the model had been cut off partway
    through deciding what to ask for.
    """
    loop, _, observed = _loop([
        ProviderTurn(
            text="I will check",
            tool_calls=(ToolCall("c1", "check_proof", {}),),
            stop_reason="max_tokens",
        ),
        ProviderTurn(text="done"),
    ])

    events = _drain(loop, "prove it")

    assert any(item["type"] == "truncated" for item in observed)
    notices = [event for event in events if event.kind == "notice"]
    assert notices and "max_tokens" in notices[0].text
    # And the tools still ran: the disclosure is added, not substituted.
    assert any(item["type"] == "tool_result" for item in observed) or any(
        event.kind == "tool_result" for event in events
    )


def test_every_tool_call_is_answered_even_if_nobody_drains_the_stream() -> None:
    """A consumer that stops iterating at the `tool_use` event suspends the
    generator there forever, and a closed generator may not yield -- so a
    cleanup that tried to append the missing result could not run. The
    conversation would keep an assistant `tool_use` with nothing answering it,
    and every later request built from it is one the API refuses."""
    loop, _, _ = _loop([
        ProviderTurn(tool_calls=(ToolCall("c1", "check_proof", {}),)),
        ProviderTurn(text="never reached"),
    ])

    stream = loop.run("prove it")
    for event in stream:
        if event.kind == "tool_use":
            break
    stream.close()

    results = [message for message in loop.messages if message.role == "tool_result"]
    assert len(results) == 1
    assert results[0].call_id == "c1"
    assert results[0].ok is False
    assert "abandoned" in results[0].text


def test_every_call_of_an_abandoned_batch_is_answered_not_just_the_first() -> None:
    """The assistant message carries the whole batch, and the API refuses an
    incomplete one as firmly as an empty one. Answering each call as its turn
    came left every later call of the same response unanswered when the
    consumer stopped at the first."""
    loop, _, _ = _loop([
        ProviderTurn(tool_calls=(
            ToolCall("c1", "check_proof", {}),
            ToolCall("c2", "check_proof", {}),
            ToolCall("c3", "check_proof", {}),
        )),
        ProviderTurn(text="never reached"),
    ])

    stream = loop.run("prove it")
    for event in stream:
        # After the first call has actually run, which is the sharper case: the
        # batch is now part-answered rather than wholly unanswered, and an
        # incomplete batch is refused just as firmly as an empty one.
        if event.kind == "tool_result":
            break
    stream.close()

    results = [message for message in loop.messages if message.role == "tool_result"]
    assert [message.call_id for message in results] == ["c1", "c2", "c3"]
    assert [message.ok for message in results] == [True, False, False]
    assert all("abandoned" in message.text for message in results[1:])


def test_a_batch_abandoned_before_anything_ran_is_answered_too() -> None:
    loop, _, _ = _loop([
        ProviderTurn(tool_calls=(ToolCall("c1", "check_proof", {}), ToolCall("c2", "check_proof", {}))),
        ProviderTurn(text="never reached"),
    ])

    stream = loop.run("prove it")
    for event in stream:
        if event.kind == "tool_use":
            break
    stream.close()

    results = [message for message in loop.messages if message.role == "tool_result"]
    assert [message.call_id for message in results] == ["c1", "c2"]
    assert not any(message.ok for message in results)


def test_a_drained_batch_keeps_every_real_result() -> None:
    """The placeholders are replaced, not appended beside: a batch nobody
    abandoned must read exactly as it did before they existed."""
    loop, _, _ = _loop([
        ProviderTurn(tool_calls=(ToolCall("c1", "check_proof", {}), ToolCall("c2", "check_proof", {}))),
        ProviderTurn(text="done"),
    ])

    _drain(loop, "prove it")

    results = [message for message in loop.messages if message.role == "tool_result"]
    assert [message.call_id for message in results] == ["c1", "c2"]
    assert all(message.ok for message in results)
    assert not any("abandoned" in message.text for message in results)
