from __future__ import annotations

import asyncio
import os

import pytest

from hardy.agents import claude as claude_runtime
from hardy.foundation.values import ToolResult


class ResultMessage:
    """`_note` dispatches on the SDK's class name, so the fake must wear it."""

    def __init__(self, *, is_error=False, subtype=None, num_turns=3, cost=None, usage=None):
        self.content, self.session_id = [], "thread-9"
        self.is_error, self.subtype, self.num_turns = is_error, subtype, num_turns
        self.total_cost_usd, self.usage = cost, usage


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
    }]


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
