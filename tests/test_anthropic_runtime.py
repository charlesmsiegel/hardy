from __future__ import annotations

import json

from hardy import anthropic_runtime as backend
from hardy.chat import CHAT_TOOLS

MODEL = "claude-opus-5"


def test_chat_tools_convert_to_anthropic_schemas():
    tools = backend.to_tools(CHAT_TOOLS)
    assert [tool["name"] for tool in tools] == [entry["function"]["name"] for entry in CHAT_TOOLS]
    save_lean = next(tool for tool in tools if tool["name"] == "save_lean")
    assert save_lean["input_schema"]["properties"]["source"] == {"type": "string"}
    assert "parameters" not in save_lean


def test_system_messages_are_lifted_out_of_the_message_list():
    system, messages = backend.to_messages([{"role": "system", "content": "Be Hardy."}, {"role": "user", "content": "Hello"}], MODEL)
    assert system == "Be Hardy."
    assert messages == [{"role": "user", "content": "Hello"}]


def test_consecutive_tool_results_collapse_into_one_user_turn():
    """Anthropic rejects tool results split across messages; OpenAI splits them."""
    conversation = [
        {"role": "user", "content": "Check two things."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "check_lean", "arguments": '{"source": "example : True := trivial"}'}},
            {"id": "b", "type": "function", "function": {"name": "read_workspace", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "b", "content": '{"ok": true}'},
    ]
    _, messages = backend.to_messages(conversation, MODEL)
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assistant = messages[1]["content"]
    assert [block["type"] for block in assistant] == ["tool_use", "tool_use"]
    assert assistant[0]["input"] == {"source": "example : True := trivial"}
    results = messages[2]["content"]
    assert [block["tool_use_id"] for block in results] == ["a", "b"]
    assert all(block["type"] == "tool_result" for block in results)


def test_replies_become_canonical_openai_shaped_messages():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Checking the proof."},
        {"type": "tool_use", "id": "toolu_1", "name": "check_lean", "input": {"source": "example : True := trivial"}},
    ]
    message = backend.from_blocks(blocks, MODEL)
    assert message["role"] == "assistant"
    assert message["content"] == "Checking the proof."
    assert message["tool_calls"][0]["id"] == "toolu_1"
    assert message["tool_calls"][0]["function"]["name"] == "check_lean"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"source": "example : True := trivial"}


def test_thinking_blocks_survive_a_round_trip_on_the_same_model():
    """Anthropic requires thinking blocks echoed back unchanged, and Hardy's
    canonical message format has nowhere to put them."""
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "Done."}]
    message = backend.from_blocks(blocks, MODEL)
    _, messages = backend.to_messages([{"role": "user", "content": "Hi"}, message], MODEL)
    assert messages[1]["content"] == blocks


def test_thinking_blocks_are_dropped_when_the_model_changes():
    blocks = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "Done."}]
    message = backend.from_blocks(blocks, MODEL)
    _, messages = backend.to_messages([{"role": "user", "content": "Hi"}, message], "claude-sonnet-5")
    assert messages[1]["content"] == [{"type": "text", "text": "Done."}]


def test_empty_turns_are_omitted_rather_than_sent_as_empty_content():
    _, messages = backend.to_messages([{"role": "user", "content": ""}, {"role": "user", "content": "real"}], MODEL)
    assert messages == [{"role": "user", "content": "real"}]


def test_a_refusal_is_reported_instead_of_returning_an_empty_answer():
    runtime = backend.AnthropicRuntime("key", MODEL)
    runtime._client = _FakeClient(_FakeReply([], stop_reason="refusal"))
    answer = runtime.complete([{"role": "user", "content": "..."}])
    assert answer["content"] == backend.REFUSAL_NOTICE
    assert "tool_calls" not in answer


def test_a_truncated_reply_says_so():
    runtime = backend.AnthropicRuntime("key", MODEL)
    runtime._client = _FakeClient(_FakeReply([_FakeBlock({"type": "text", "text": "partial"})], stop_reason="max_tokens"))
    assert runtime.complete([{"role": "user", "content": "..."}])["content"].endswith(backend.TRUNCATION_NOTICE)


def test_the_request_carries_a_cacheable_system_prompt_and_converted_tools():
    runtime = backend.AnthropicRuntime("key", MODEL)
    client = _FakeClient(_FakeReply([_FakeBlock({"type": "text", "text": "ok"})]))
    runtime._client = client
    runtime.complete([{"role": "system", "content": "Be Hardy."}, {"role": "user", "content": "Hi"}], tools=CHAT_TOOLS)
    assert client.request["model"] == MODEL
    assert client.request["max_tokens"] == backend.DEFAULT_MAX_TOKENS
    assert client.request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert client.request["tools"][0]["name"] == "check_lean"


class _FakeBlock:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, mode: str = "python") -> dict:
        return self.payload


class _FakeReply:
    def __init__(self, content: list, stop_reason: str = "end_turn"):
        self.content, self.stop_reason = content, stop_reason


class _FakeStream:
    def __init__(self, reply: _FakeReply):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def get_final_message(self) -> _FakeReply:
        return self.reply


class _FakeMessages:
    def __init__(self, client: "_FakeClient"):
        self.client = client

    def stream(self, **request) -> _FakeStream:
        self.client.request = request
        return _FakeStream(self.client.reply)


class _FakeClient:
    def __init__(self, reply: _FakeReply):
        self.reply, self.request = reply, {}
        self.messages = _FakeMessages(self)
