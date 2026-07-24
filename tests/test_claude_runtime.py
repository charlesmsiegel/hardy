from __future__ import annotations

import asyncio

import pytest

from hardy import claude_runtime


class ResultMessage:
    """`_note` dispatches on the SDK's class name, so the fake must wear it."""

    def __init__(self, *, is_error=False, subtype=None, num_turns=3):
        self.content, self.session_id = [], "thread-9"
        self.is_error, self.subtype, self.num_turns = is_error, subtype, num_turns


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
    live._note(ResultMessage(num_turns=7), [])
    assert live.turns == 7
    assert live.session_id == "thread-9"


def test_an_error_result_is_not_reported_as_a_finished_answer():
    """Otherwise a batch run records a provider failure as 'no proof submitted'."""
    live = runtime()
    live._note(ResultMessage(is_error=True, subtype="error_max_turns"), [])
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
