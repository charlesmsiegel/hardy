"""The Claude backend: Anthropic's Messages API behind Hardy's existing runtime shape.

Hardy keeps one canonical conversation format — the OpenAI chat-completions
shape already written to `transcript.jsonl` — and translates at the runtime
boundary. That is what lets `/model` move between providers mid-conversation and
still resume an old transcript: the harness, not the provider, owns the record.

Two details are load-bearing:

* Anthropic requires every tool result for one assistant turn to arrive in a
  single user message, so consecutive `role: "tool"` messages are grouped.
* Thinking blocks must be echoed back unmodified on the same model, and Hardy's
  canonical format has nowhere to put them. The raw content blocks are stashed
  on the assistant message under `_anthropic_content` and replayed verbatim when
  the model is unchanged. Switch models and they are dropped, which is what other
  models do with them anyway.
"""

from __future__ import annotations

import json
from typing import Any

from .runtime import TOOLS

RAW_CONTENT = "_anthropic_content"
RAW_MODEL = "_anthropic_model"

# Thinking counts against max_tokens, and a Lean proof plus commentary is not
# short. Streaming keeps a budget this size clear of request timeouts.
DEFAULT_MAX_TOKENS = 32000
DEFAULT_TIMEOUT = 600.0

REFUSAL_NOTICE = "The provider's safety classifier declined this request. Rephrase it, or use /model to switch models."
TRUNCATION_NOTICE = "\n\n[Hardy: the reply hit the output token limit and is incomplete.]"


def to_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI function tools to Anthropic tools."""
    converted = []
    for tool in tools:
        function = tool.get("function") if tool.get("type") == "function" else None
        if not function or not function.get("name"):
            continue
        converted.append({
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def _assistant_content(message: dict[str, Any], model: str) -> list[dict[str, Any]]:
    stashed = message.get(RAW_CONTENT)
    if stashed and message.get(RAW_MODEL) == model:
        return list(stashed)
    blocks: list[dict[str, Any]] = []
    text = message.get("content")
    if text:
        blocks.append({"type": "text", "text": str(text)})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        blocks.append({"type": "tool_use", "id": call.get("id", "missing"), "name": function.get("name", ""), "input": arguments})
    return blocks


def to_messages(messages: list[dict[str, Any]], model: str) -> tuple[str, list[dict[str, Any]]]:
    """Split Hardy's message list into an Anthropic system prompt and message list."""
    system: list[str] = []
    converted: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            converted.append({"role": "user", "content": pending})
            pending = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            text = str(message.get("content") or "").strip()
            if text:
                system.append(text)
            continue
        if role == "tool":
            pending.append({
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or "missing"),
                "content": str(message.get("content") or "(empty)"),
            })
            continue
        flush()
        if role == "assistant":
            blocks = _assistant_content(message, model)
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
            continue
        text = str(message.get("content") or "").strip()
        if text:
            converted.append({"role": "user", "content": text})
    flush()
    return "\n\n".join(system), converted


def from_blocks(blocks: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Anthropic content blocks back into Hardy's canonical assistant message."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            text_parts.append(str(block["text"]))
        elif kind == "tool_use":
            arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)
            tool_calls.append({"id": block.get("id", "missing"), "type": "function", "function": {"name": block.get("name", ""), "arguments": arguments}})
    message: dict[str, Any] = {"role": "assistant", "content": "\n\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    message[RAW_CONTENT] = blocks
    message[RAW_MODEL] = model
    return message


class AnthropicRuntime:
    """Speaks Hardy's runtime protocol; talks to Claude through the official SDK."""

    def __init__(self, api_key: str, model: str, *, base_url: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = DEFAULT_TIMEOUT):
        self.model, self.max_tokens, self.timeout = model, max_tokens, timeout
        self._api_key, self._base_url, self._client = api_key, base_url, None
        # Connect eagerly: a missing SDK must fail where the caller can still
        # recover, not on the next message with the switch already announced.
        self._connect()

    def _connect(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as error:  # pragma: no cover - depends on the install
                raise RuntimeError("the Claude backend needs the anthropic package: pip install anthropic") from error
            options: dict[str, Any] = {"api_key": self._api_key, "timeout": self.timeout}
            if self._base_url:
                options["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**options)
        return self._client

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        system, converted = to_messages(messages, self.model)
        # Per-request rather than per-client, so a caller with a wall-clock
        # budget can shrink `timeout` between turns and have it take effect.
        request: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens, "messages": converted, "timeout": self.timeout}
        converted_tools = to_tools(tools if tools is not None else TOOLS)
        if converted_tools:
            request["tools"] = converted_tools
        if system:
            # The system prompt and tool schemas are the stable prefix of every
            # turn, so they are the cheap thing to cache.
            request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        with self._connect().messages.stream(**request) as stream:
            reply = stream.get_final_message()
        if reply.stop_reason == "refusal":
            return {"role": "assistant", "content": REFUSAL_NOTICE}
        message = from_blocks([block.model_dump(mode="json") for block in reply.content], self.model)
        if reply.stop_reason == "max_tokens":
            message["content"] = (message.get("content") or "") + TRUNCATION_NOTICE
        return message
