from __future__ import annotations

import asyncio
import os

import pytest

from hardy.agents import claude as claude_runtime
from hardy.foundation.values import ToolResult


class ResultMessage:
    """`_note` dispatches on the SDK's class name, so the fake must wear it."""

    def __init__(self, *, is_error=False, subtype=None, num_turns=3, cost=None, usage=None, model_usage=None):
        self.content, self.session_id = [], "thread-9"
        self.is_error, self.subtype, self.num_turns = is_error, subtype, num_turns
        self.total_cost_usd, self.usage, self.model_usage = cost, usage, model_usage


def runtime(**kwargs) -> claude_runtime.ClaudeAgentRuntime:
    return claude_runtime.ClaudeAgentRuntime(
        "claude-haiku-4-5", system_prompt="be terse", specs=[], dispatch=lambda name, args: None, **kwargs
    )


def test_hardy_tools_are_permitted():
    allowed = asyncio.run(runtime()._permit("mcp__hardy__check_lean", {}, None))
    assert allowed.behavior == "allow"


@pytest.mark.parametrize("name", ["Bash", "Read", "Write", "ToolSearch", "mcp__other__thing"])
def test_everything_else_is_refused_by_default(name: str):
    """A denylist has to anticipate every built-in the CLI grows; refusing by
    default does not."""
    seen: list[dict] = []
    denied = asyncio.run(runtime(observe=seen.append)._permit(name, {}, None))
    assert denied.behavior == "deny"
    assert seen == [{"type": "refused_tool", "name": name}]


def test_the_provider_turn_count_is_taken_from_the_result():
    """The SDK ran the loop, so only it knows how many turns that took."""
    live = runtime()
    # `_note` yields what the terminal should draw, so it has to be drained
    # before any of its recording happens.
    list(live._note(ResultMessage(num_turns=7), []))
    assert live.turns == 7
    assert live.session_id == "thread-9"


def test_the_result_event_carries_what_the_exchange_cost():
    """Cost alone cannot tell 'expensive because long' from 'expensive because
    the model is dear', so the token counts travel with it."""
    seen: list[dict] = []
    live = runtime(observe=seen.append)
    counts = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 400}
    list(live._note(ResultMessage(cost=0.25, usage=counts), []))
    reported = [event for event in seen if event["type"] == "result"]
    assert reported == [{
        "type": "result",
        "session_id": "thread-9",
        "turns": 3,
        "cost_usd": 0.25,
        "usage": counts,
        "is_error": False,
        # No assistant message stated usage, so there is nothing to tell a
        # session-to-date report from one exchange's by.
        "exchange_usage": None,
        "cumulative": None,
        "model_usage": None,
    }]


class AssistantMessage:
    def __init__(self, message_id, usage):
        self.content, self.session_id = [], "thread-9"
        self.message_id, self.usage = message_id, usage


def _exchange(live, *messages, result):
    for message in (*messages, result):
        list(live._note(message, []))


def test_a_report_that_is_only_this_exchange_is_marked_not_cumulative():
    """The CLI streams one assistant message per content block, each carrying
    that API message's usage, so they are deduplicated by message id; output
    counts can lag within a message, so the largest is kept. A report stating
    no more than those messages moved carries no earlier exchange (#197)."""
    seen: list[dict] = []
    live = runtime(observe=seen.append)
    _exchange(
        live,
        AssistantMessage("m1", {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 400}),
        AssistantMessage("m1", {"input_tokens": 10, "output_tokens": 30, "cache_read_input_tokens": 400}),
        AssistantMessage("m2", {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 450}),
        result=ResultMessage(usage={"input_tokens": 15, "output_tokens": 37, "cache_read_input_tokens": 850}),
    )
    result = next(event for event in seen if event["type"] == "result")
    assert result["exchange_usage"] == {"input_tokens": 15, "output_tokens": 37, "cache_read_input_tokens": 850}
    assert result["cumulative"] is False


def test_a_report_stating_more_than_its_exchange_may_be_session_to_date():
    seen: list[dict] = []
    live = runtime(observe=seen.append)
    _exchange(
        live,
        AssistantMessage("m1", {"input_tokens": 10, "output_tokens": 30}),
        result=ResultMessage(usage={"input_tokens": 25, "output_tokens": 60}),
    )
    result = next(event for event in seen if event["type"] == "result")
    assert result["exchange_usage"] == {"input_tokens": 10, "output_tokens": 30}
    assert result["cumulative"] is True


def test_each_exchange_sums_only_its_own_messages():
    """The sum is the exchange's, so the next exchange starts from nothing."""
    seen: list[dict] = []
    live = runtime(observe=seen.append)
    _exchange(live, AssistantMessage("m1", {"input_tokens": 10}), result=ResultMessage(usage={"input_tokens": 10}))
    _exchange(live, AssistantMessage("m2", {"input_tokens": 4}), result=ResultMessage(usage={"input_tokens": 14}))
    second = [event for event in seen if event["type"] == "result"][1]
    assert second["exchange_usage"] == {"input_tokens": 4}
    assert second["cumulative"] is True


def test_model_usage_is_summed_over_models_under_the_usage_names():
    seen: list[dict] = []
    live = runtime(observe=seen.append)
    per_model = {
        "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 100,
                             "cacheCreationInputTokens": 0, "costUSD": 0.01, "webSearchRequests": 0},
        "claude-sonnet-4-5": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": True},
    }
    _exchange(live, result=ResultMessage(model_usage=per_model))
    result = next(event for event in seen if event["type"] == "result")
    assert result["model_usage"] == {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 100,
                                     "cache_creation_input_tokens": 0}


def test_the_interactive_ledger_counts_the_documented_cli_once():
    """End to end through `_note` into the ledger interactive and batch keep
    (#197 review I1): per-turn `usage` beside a running-total cost and
    `modelUsage`. The cost was right before the guard and still is."""
    from hardy.agents.usage import Usage

    seen: list[dict] = []
    live = runtime(observe=seen.append)
    for n in (1, 2, 3):
        _exchange(
            live,
            AssistantMessage(f"m{n}", {"input_tokens": 1000, "output_tokens": 100}),
            result=ResultMessage(cost=0.5 * n, usage={"input_tokens": 1000, "output_tokens": 100},
                                 model_usage={"m": {"inputTokens": 1000 * n, "outputTokens": 100 * n}}),
        )
    spent = Usage()
    for event in seen:
        if event["type"] == "result":
            spent = spent.record(event)
    assert spent.cost_usd == pytest.approx(1.5)
    assert (spent.input_tokens, spent.output_tokens) == (3000, 300)


def test_a_provider_that_reports_no_usage_reports_none_and_not_zero():
    """`{}` here would be indistinguishable downstream from a measured zero."""
    seen: list[dict] = []
    list(runtime(observe=seen.append)._note(ResultMessage(), []))
    result = next(event for event in seen if event["type"] == "result")
    assert result["cost_usd"] is None
    assert result["usage"] is None


def test_an_error_result_is_not_reported_as_a_finished_answer():
    """Otherwise a batch run records a provider failure as 'no proof submitted'."""
    live = runtime()
    list(live._note(ResultMessage(is_error=True, subtype="error_max_turns"), []))
    assert live.failure == "error_max_turns"


def test_the_permission_callback_is_not_shadowed_by_an_allowlist():
    """An `allowed_tools` entry auto-approves before the callback is consulted,
    which would leave it gating only the tools it was never the point of gating."""
    options = runtime()._options()
    assert not getattr(options, "allowed_tools", None)
    # bypassPermissions maps to --dangerously-skip-permissions, which the CLI
    # refuses to run as root; that would break Hardy in every container.
    assert getattr(options, "permission_mode", None) != "bypassPermissions"
    assert options.can_use_tool is not None
    assert options.setting_sources == []


def test_no_builtin_tools_are_offered():
    """`can_use_tool` is only consulted for a tool whose own check answers
    "ask" -- an auto-allowed built-in (`TaskCreate`, `TodoWrite`, ...) never
    reaches it. An empty `tools` list is what keeps the built-ins out of the
    conversation at all, and `strict_mcp_config` keeps out any MCP server this
    runtime did not itself register."""
    options = runtime()._options()
    assert options.tools == []
    assert options.strict_mcp_config is True
    assert options.hooks["PreToolUse"]


def test_pre_tool_use_hook_allows_hardy_tools():
    out = asyncio.run(runtime()._gate({"tool_name": "mcp__hardy__check_lean", "tool_input": {}}, "id-1", None))
    assert out == {}


def test_pre_tool_use_hook_denies_non_hardy_tools():
    """The belt-and-braces gate for whatever the built-in `tools=[]` and the
    `can_use_tool` callback both miss: a tool call that still reaches
    PreToolUse is refused there too, and recorded so the transcript can say
    what the model tried."""
    seen: list[dict] = []
    out = asyncio.run(runtime(observe=seen.append)._gate({"tool_name": "TaskCreate", "tool_input": {}}, None, None))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"]
    assert {"type": "refused_tool", "name": "TaskCreate", "via": "hook"} in seen


def test_a_stalled_exchange_is_cut_off_at_the_wall_clock_budget():
    """`max_turns` is the SDK's to enforce, but nothing there bounds a stalled
    request, so the deadline is Hardy's to keep."""
    seen: list[dict] = []
    live = runtime(wall_seconds=0.05, observe=seen.append)

    async def forever(text, outbox):
        await asyncio.sleep(30)

    live._exchange = forever
    with pytest.raises(TimeoutError):
        live.ask("hello")
    # The bound asked for, and the moment it actually fired (issue #27): a
    # deadline is never exact, and the record says by how much rather than
    # reporting the budget back as if it had been kept to the microsecond.
    [limit] = seen
    assert limit["type"] == "wall_clock_limit" and limit["seconds"] == 0.05
    assert limit["elapsed"] >= 0.05


def test_the_turn_bound_is_handed_to_the_sdk():
    assert runtime(max_turns=6)._options().max_turns == 6


def test_a_new_conversation_opens_under_a_session_id_hardy_chose():
    """Left to the CLI, a `claude` started inside another Claude Code session
    took that session's id from its environment, and every thread of a
    staged run reported one id -- so whether the independent reader had
    inherited the formalizer's conversation could not be told from the
    record. Each runtime now names its own fresh session, and a resumed one
    names none (the CLI refuses `--session-id` beside `--resume`)."""
    fresh = runtime()._options()
    other = runtime()._options()
    resumed = runtime(session_id="existing-session")._options()

    assert fresh.session_id and fresh.resume is None
    assert other.session_id and other.session_id != fresh.session_id
    assert resumed.resume == "existing-session" and resumed.session_id is None


@pytest.mark.live
def test_asking_the_live_model_to_call_a_builtin_gets_no_result(tmp_path):
    """The reproduction in issue #320. With no Hardy tools registered, ask a
    real Claude Code CLI to call `TaskCreate`: `tools=[]` and the two
    default-deny gates must mean it never completes, and that the refusal is
    on the record. Off by default -- needs a logged-in CLI and spends a real
    turn -- set HARDY_CLAUDE_LIVE=1 to run it."""
    if not os.environ.get("HARDY_CLAUDE_LIVE"):
        pytest.skip("set HARDY_CLAUDE_LIVE=1 to run a real Claude Code turn")

    seen: list[dict] = []
    live = claude_runtime.ClaudeAgentRuntime(
        "claude-haiku-4-5",
        system_prompt=(
            "Call the TaskCreate tool once with subject 'probe' and description "
            "'probe', then reply with the comma-separated names of every tool "
            "available to you."
        ),
        specs=[],
        dispatch=lambda name, args: ToolResult(True, ""),
        cwd=tmp_path,
        observe=seen.append,
        max_turns=4,
        wall_seconds=120,
    )
    events = list(live.stream("Go."))

    completed = [event for event in events if event.kind == "tool_result" and event.name == "TaskCreate"]
    assert not completed, "a Claude Code built-in must never complete as a tool call"
    refused = [event for event in seen if event["type"] == "refused_tool" and event["name"] == "TaskCreate"]
    assert refused, "the refusal must be on the record"


@pytest.mark.live
def test_resumed_usage_reports_are_counted_once_by_the_ledger(tmp_path):
    """Issue #197: what the CLI reports across resumed exchanges, and that the
    ledger counts each exchange once -- neither less nor more -- whichever way
    it reports.

    Thread A asks twice back to back, thread B asks in the same directory,
    then A asks again. Each report is kept per session in its own `Usage`, as
    the staged runtime does. For every exchange whose report is recognisably
    one exchange's figures, or recognisably the session's running total (the
    evidence that shares its lifecycle climbed by exactly the exchange's own
    streamed messages), the ledger must add exactly that exchange: so an
    overcount fails here, as well as an undercount. `-s` prints what the CLI
    did, for the decision record. Off by default -- needs a logged-in CLI and
    spends four real turns -- set HARDY_CLAUDE_LIVE=1 to run it."""
    if not os.environ.get("HARDY_CLAUDE_LIVE"):
        pytest.skip("set HARDY_CLAUDE_LIVE=1 to run real Claude Code turns")
    from hardy.agents.usage import Usage

    seen: list[dict] = []

    def thread() -> claude_runtime.ClaudeAgentRuntime:
        return claude_runtime.ClaudeAgentRuntime(
            "claude-haiku-4-5", system_prompt="Reply with one short word.", specs=[],
            dispatch=lambda name, args: ToolResult(True, ""), cwd=tmp_path,
            observe=seen.append, max_turns=2, wall_seconds=120,
        )

    first, second = thread(), thread()
    for live in (first, first, second, first):
        live.ask("Say a colour.")
    results = [event for event in seen if event["type"] == "result"]
    assert len(results) == 4
    assert results[0]["session_id"] == results[1]["session_id"] == results[3]["session_id"]
    assert results[2]["session_id"] != results[0]["session_id"]

    inputs = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    names = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
             "cache_creation_input_tokens": "cache_write_tokens", "cache_read_input_tokens": "cache_read_tokens"}

    def reading(evidence, before, own):
        """Whether `evidence` shows one exchange ("whole"), a running total ("delta"), or neither."""
        if not evidence or not own:
            return None
        alone = all(evidence.get(k, 0) == own.get(k, 0) for k in inputs)
        climbed = before is not None and all(evidence.get(k, 0) - before.get(k, 0) == own.get(k, 0) for k in inputs)
        if alone and not climbed:
            return "whole"
        if climbed and not alone:
            return "delta"
        return None

    ledgers: dict[str, Usage] = {}
    last: dict[str, dict] = {}
    determined = {"tokens": 0, "cost": 0}
    for step, event in enumerate(results):
        own = event["exchange_usage"] or {}
        session = event["session_id"]
        previous = last.get(session)
        print(f"exchange {step}: session={session[:8]} cost={event['cost_usd']} usage={event['usage']} "
              f"model_usage={event['model_usage']} own={own} cumulative={event['cumulative']}")
        before = ledgers.get(session, Usage())
        after = before.record(event)
        ledgers[session], last[session] = after, event

        tokens = reading(event["usage"], previous and previous["usage"], own)
        for key, field in names.items():
            reported = (event["usage"] or {}).get(key)
            if not isinstance(reported, int):
                continue
            added = getattr(after, field) - getattr(before, field)
            assert added >= own.get(key, 0), (step, field, "undercount")
            if tokens == "whole":
                assert added == reported, (step, field)
            elif tokens == "delta":
                assert added == reported - previous["usage"][key], (step, field)
        determined["tokens"] += tokens is not None

        cost = reading(event["model_usage"], previous and previous["model_usage"], own)
        if isinstance(event["cost_usd"], (int, float)):
            added = (after.cost_usd or 0.0) - (before.cost_usd or 0.0)
            if cost == "whole":
                assert added == pytest.approx(event["cost_usd"]), (step, "cost")
            elif cost == "delta":
                assert added == pytest.approx(event["cost_usd"] - previous["cost_usd"]), (step, "cost")
            else:
                assert added >= event["cost_usd"] - (previous["cost_usd"] if previous else 0.0) - 1e-12
        determined["cost"] += cost is not None
    print(f"determined: {determined}")
    # A run where no report could be read either way proves nothing, and is
    # itself worth knowing: the ledger would then count every report whole.
    assert determined["tokens"] and determined["cost"], determined
