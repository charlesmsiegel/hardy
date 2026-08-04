from __future__ import annotations

import asyncio

import pytest

from hardy import claude_runtime


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
    assert seen == [{"type": "wall_clock_limit", "seconds": 0.05}]


def test_the_turn_bound_is_handed_to_the_sdk():
    assert runtime(max_turns=6)._options().max_turns == 6
