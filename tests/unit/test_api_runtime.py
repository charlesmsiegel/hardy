"""The Messages API transport (#23).

This module translates and nothing else: the loop decides when to call, and
Hardy still runs every tool. What is worth testing is therefore the
translation itself — the shapes the API refuses are the ones that would end a
run — plus the two promises the runtime makes about itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from hardy.api_runtime import (
    AnthropicProvider,
    ApiRuntime,
    _usage,
    as_messages,
    tool_schema,
)
from hardy.loop import Message, ToolCall
from hardy.models import ToolResult


class Block:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class Reply:
    def __init__(self, content, usage=None, stop_reason="end_turn") -> None:
        self.content, self.usage, self.stop_reason = content, usage, stop_reason


class FakeClient:
    base_url = "https://api.anthropic.test"

    def __init__(self, replies) -> None:
        self.replies = list(replies)
        self.sent: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **request: Any) -> Reply:
        self.sent.append(request)
        return self.replies.pop(0)


def test_tool_specs_become_the_input_schema_shape() -> None:
    tools = tool_schema([
        {"type": "function", "function": {"name": "save_lean", "description": "save it", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}}}}
    ])

    assert tools == [{
        "name": "save_lean",
        "description": "save it",
        "input_schema": {"type": "object", "properties": {"source": {"type": "string"}}},
    }]


def test_a_spec_without_parameters_still_gets_a_schema() -> None:
    # The API requires one. An absent `parameters` is a tool that takes no
    # arguments, not a tool that cannot be described.
    assert tool_schema([{"function": {"name": "status"}}])[0]["input_schema"] == {
        "type": "object", "properties": {}
    }


def test_an_assistant_turn_carries_its_text_and_its_calls_together() -> None:
    sent = as_messages([
        Message("user", text="prove it"),
        Message("assistant", text="checking", tool_calls=(ToolCall("c1", "check_proof", {"proof": "by rfl"}),)),
        Message("tool_result", call_id="c1", name="check_proof", ok=True, text="accepted"),
    ])

    assert sent[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "c1", "name": "check_proof", "input": {"proof": "by rfl"}},
        ],
    }


def test_consecutive_tool_results_arrive_as_one_user_message() -> None:
    # The API pairs every `tool_use` with a `tool_result` in the message that
    # follows it. Sent one message each, the first call would still be
    # unanswered while the second was already being asked about.
    sent = as_messages([
        Message("user", text="prove it"),
        Message("assistant", tool_calls=(ToolCall("c1", "check_proof", {}), ToolCall("c2", "check_proof", {}))),
        Message("tool_result", call_id="c1", ok=True, text="one"),
        Message("tool_result", call_id="c2", ok=False, text="two"),
    ])

    assert len(sent) == 3
    assert sent[2]["role"] == "user"
    assert [block["tool_use_id"] for block in sent[2]["content"]] == ["c1", "c2"]
    assert [block["is_error"] for block in sent[2]["content"]] == [False, True]


def test_consecutive_user_entries_are_joined() -> None:
    # Hardy puts its own words in the user role -- a compaction summary, a
    # declined turn -- so two can land in a row with nothing said in between,
    # and the API expects the two sides to alternate.
    sent = as_messages([Message("user", text="summary"), Message("user", text="prove it")])

    assert len(sent) == 1
    assert [block["text"] for block in sent[0]["content"]] == ["summary", "prove it"]


def test_text_after_a_tool_result_joins_it_behind_the_results() -> None:
    # A tool result has to come first in the message that carries it, which
    # appending leaves true.
    sent = as_messages([
        Message("assistant", tool_calls=(ToolCall("c1", "check_proof", {}),)),
        Message("tool_result", call_id="c1", ok=True, text="accepted"),
        Message("user", text="carry on"),
    ])

    assert len(sent) == 2
    assert [block["type"] for block in sent[1]["content"]] == ["tool_result", "text"]


def test_an_empty_assistant_turn_is_dropped() -> None:
    # The API refuses empty content, and a turn that said nothing and called
    # nothing has nothing in it to preserve.
    assert as_messages([Message("assistant")]) == []


def test_a_provider_turn_is_read_into_text_calls_and_thinking() -> None:
    client = FakeClient([Reply([
        Block(type="thinking", thinking="..."),
        Block(type="text", text="here goes"),
        Block(type="tool_use", id="c1", name="check_proof", input={"proof": "by rfl"}),
    ], usage=Block(input_tokens=11, output_tokens=3, model_dump=lambda: {"input_tokens": 11, "output_tokens": 3}))])
    provider = AnthropicProvider("claude-test", client=client)

    turn = provider.complete(system="be exact", messages=[Message("user", text="hi")], specs=[])

    assert turn.text == "here goes"
    assert turn.thinking is True
    assert turn.tool_calls == (ToolCall("c1", "check_proof", {"proof": "by rfl"}),)
    assert turn.usage == {"input_tokens": 11, "output_tokens": 3}
    assert provider.endpoint == "messages api (https://api.anthropic.test)"


def test_a_provider_that_stated_no_usage_reports_none_not_zero() -> None:
    # `usage.Usage` reads an absent report as "not stated" and an empty one as
    # a measured zero, and only one of those is true of a provider that said
    # nothing.
    assert _usage(None) is None
    assert _usage({}) is None
    assert _usage({"input_tokens": 0}) == {"input_tokens": 0}


def test_the_runtime_owns_both_bounds_and_says_so() -> None:
    runtime = ApiRuntime(
        "claude-test",
        system_prompt="be exact",
        specs=[],
        dispatch=lambda name, arguments: ToolResult(True, "ok"),
        provider=AnthropicProvider("claude-test", client=FakeClient([Reply([Block(type="text", text="done")])])),
        max_turns=4,
        wall_seconds=30,
    )

    assert runtime.enforcement == {"turns": "hardy", "wall_clock": "hardy"}
    assert runtime.ask("prove it") == "done"
    assert runtime.turns == 1


def test_the_runtime_claims_no_provider_thread() -> None:
    # There is none to claim: the conversation is the loop's own message list
    # and it ends with the process. Handing back an id that resumes nothing
    # would let a workspace believe it had continued a conversation.
    runtime = ApiRuntime(
        "claude-test",
        system_prompt="",
        specs=[],
        dispatch=lambda name, arguments: ToolResult(True, "ok"),
        session_id="carried-from-somewhere-else",
        provider=AnthropicProvider("claude-test", client=FakeClient([])),
    )

    assert runtime.session_id is None
    assert runtime.backend == "anthropic-api"


def test_the_key_is_required_before_anything_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider("claude-test")

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        provider.complete(system="", messages=[Message("user", text="hi")], specs=[])


def test_a_provider_nobody_calls_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run whose cheap closers close the statement asks no provider anything.
    Built eagerly, such a run died on a missing key *after* Lean had accepted
    the proof, writing none of the artifacts it had earned."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    provider = AnthropicProvider("claude-test")

    # Constructed, and its identity readable, without a client existing.
    assert provider.endpoint == "messages api"
    assert provider.max_tokens > 0


def test_a_transport_timeout_is_reported_as_one() -> None:
    """The SDK's own timeout is an `APITimeoutError` -- not a `TimeoutError` --
    so a request that ran out of the wall clock Hardy handed it would reach the
    runner as an ordinary failure and be graded `runtime_error` instead of
    `wall_clock_limit`."""
    class APITimeoutError(Exception):
        pass

    class Timing(FakeClient):
        def create(self, **request):
            raise APITimeoutError("request timed out")

    provider = AnthropicProvider("claude-test", client=Timing([]))

    with pytest.raises(TimeoutError, match="12s budget"):
        provider.complete(system="", messages=[Message("user", text="hi")], specs=[], timeout=12)


def test_an_ordinary_provider_failure_is_left_alone() -> None:
    class Failing(FakeClient):
        def create(self, **request):
            raise RuntimeError("rate limited")

    provider = AnthropicProvider("claude-test", client=Failing([]))

    with pytest.raises(RuntimeError, match="rate limited"):
        provider.complete(system="", messages=[Message("user", text="hi")], specs=[])


def test_the_loops_remaining_clock_becomes_the_requests_timeout() -> None:
    client = FakeClient([Reply([Block(type="text", text="done")])])
    provider = AnthropicProvider("claude-test", client=client)

    provider.complete(system="", messages=[Message("user", text="hi")], specs=[], timeout=12.5)

    assert client.sent[0]["timeout"] == 12.5


def test_an_unbounded_loop_leaves_the_clients_own_timeout_alone() -> None:
    # Passing `None` would read as an instruction to wait forever, which is
    # the opposite of what an unbounded loop means here.
    client = FakeClient([Reply([Block(type="text", text="done")])])
    provider = AnthropicProvider("claude-test", client=client)

    provider.complete(system="", messages=[Message("user", text="hi")], specs=[], timeout=None)

    assert "timeout" not in client.sent[0]


def test_a_replacement_runtime_can_take_over_the_conversation() -> None:
    # `/model` builds a new runtime rather than mutating the live one, and on
    # this transport the conversation is the runtime's own list.
    runtime = ApiRuntime(
        "claude-test",
        system_prompt="",
        specs=[],
        dispatch=lambda name, arguments: ToolResult(True, "ok"),
        provider=AnthropicProvider("claude-test", client=FakeClient([])),
    )

    runtime.adopt_conversation([Message("user", text="earlier"), Message("assistant", text="answer")])

    assert [item.text for item in runtime.conversation] == ["earlier", "answer"]


def test_the_output_cap_is_part_of_what_a_run_is_recorded_as() -> None:
    """Change the cap and the same model on the same backend truncates at a
    different point and gets a different amount of room to reach a submission.
    A record naming model, backend and limits but not this would call two
    conditions the same run."""
    from hardy.chat import provenance

    runtime = ApiRuntime(
        "claude-test",
        system_prompt="",
        specs=[],
        dispatch=lambda name, arguments: ToolResult(True, "ok"),
        provider=AnthropicProvider("claude-test", client=FakeClient([]), max_tokens=4096),
    )

    assert runtime.output_limit == 4096
    assert provenance(runtime)["output_limit"] == 4096


def test_a_backend_that_imposes_no_cap_states_none() -> None:
    # Absent rather than null: a key that is present and empty would claim a
    # measurement about a transport that made none.
    from hardy.chat import provenance

    class Subscription:
        model, backend, endpoint = "m", "claude", "fake"

    assert "output_limit" not in provenance(Subscription())


def test_a_custom_gateway_is_recorded_before_the_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provenance is written at session start, and the lazy client does not
    exist yet. Read off the client alone, a session that then ran every turn
    against a private gateway recorded only `messages api`, with nothing
    re-synchronising the record afterwards."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.internal")

    assert AnthropicProvider("claude-test").endpoint == "messages api (https://gateway.internal)"


def test_an_existing_client_still_answers_for_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ignored.example")
    provider = AnthropicProvider("claude-test", client=FakeClient([]))

    assert provider.endpoint == "messages api (https://api.anthropic.test)"


def test_the_client_is_built_without_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every retry is handed the same timeout -- the whole remaining budget --
    so a request with five minutes left could spend that three times over,
    plus backoff, while the trajectory claimed Hardy kept the bound."""
    import hardy.api_runtime as module

    seen: dict[str, object] = {}

    class Sdk:
        @staticmethod
        def Anthropic(**options):
            seen.update(options)
            return FakeClient([Reply([Block(type="text", text="ok")])])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(module, "load_sdk", lambda: Sdk)

    module.AnthropicProvider("claude-test").client()

    assert seen["max_retries"] == 0


def test_a_dribbling_endpoint_does_not_outlast_the_budget() -> None:
    """The client's `timeout` is HTTPX's, and HTTPX bounds the wait for each
    chunk rather than the whole exchange -- so an endpoint that keeps sending
    can hold the call open past `wall_seconds` while the record says Hardy
    enforced it."""
    import time

    class Dribbling(FakeClient):
        def create(self, **request):
            time.sleep(5)
            return Reply([Block(type="text", text="eventually")])

    provider = AnthropicProvider("claude-test", client=Dribbling([]))

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="0.2s budget"):
        provider.complete(system="", messages=[Message("user", text="hi")], specs=[], timeout=0.2)

    # Hardy stopped when it said it would; the abandoned request is a stated
    # limit, not a silent wait.
    assert time.monotonic() - started < 2
