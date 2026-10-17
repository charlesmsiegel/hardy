"""OpenAI Chat Completions and OpenRouter, with Hardy owning every tool call."""
from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from types import ModuleType
from typing import TYPE_CHECKING, Any

from hardy.agents.api import redacted
from hardy.agents.api_transport import ApiProvider
from hardy.agents.loop import Message, ProviderTurn, ToolCall
from hardy.agents.usage import token_count

if TYPE_CHECKING:
    from openai import OpenAI

ENDPOINTS = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1"}
KEYS = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
BASE_URLS = {"openai": "OPENAI_BASE_URL", "openrouter": "OPENROUTER_BASE_URL"}
SDK_MISSING = "OpenAI-compatible backends need pip install 'hardy-prover[openai]'"


def load_sdk() -> ModuleType:
    """The OpenAI SDK, imported on first use: it is an optional install."""
    try:
        import openai
    except ImportError as error:
        raise RuntimeError(SDK_MISSING) from error
    return openai


def openrouter_client(sdk: ModuleType, **options: Any) -> OpenAI:
    """An OpenAI SDK client that sends OpenRouter exactly its own headers."""

    class OpenRouterClient(sdk.OpenAI):
        @property
        def default_headers(self) -> dict[str, str]:
            # The SDK merges OPENAI_CUSTOM_HEADERS and organization/project
            # settings even with an explicit API key. Own the entire public
            # header boundary so no spelling or unrelated gateway header can
            # cross into OpenRouter. SDK copies preserve this subclass.
            return {"Accept": "application/json", "Content-Type": "application/json",
                "User-Agent": self.user_agent, **self.auth_headers}
    return OpenRouterClient(**options)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def as_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Hardy's conversation in the Chat Completions shape, one wire message per
    message, leaving out an empty user turn and an assistant turn that said
    and called nothing."""
    return [wire for message in messages if (wire := _wire(message)) is not None]


def _wire(message: Message) -> dict[str, Any] | None:
    if message.role == "tool_result":
        return {"role": "tool", "tool_call_id": message.call_id, "content": message.text or ""}
    if message.role == "user":
        return {"role": "user", "content": message.text} if message.text else None
    sent = message.blocks[0] if message.blocks else None
    if isinstance(sent, dict) and sent.get("role") == "assistant":
        # Opaque provider reasoning/signature fields remain in memory for
        # tool continuations, outside transcripts and visible responses.
        return deepcopy(sent)
    if not (message.text or message.tool_calls):
        return None
    entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
    if message.tool_calls:
        entry["tool_calls"] = [{"id": call.id, "type": "function", "function": {
            "name": call.name, "arguments": json.dumps(call.arguments)}} for call in message.tool_calls]
    return entry


def usage(reported: object) -> dict[str, int] | None:
    """A Chat Completions usage report, translated into `REPORTED_COUNTERS`.

    `prompt_tokens` includes the cached and cache-written ones, which the
    reported vocabulary counts apart, so input is what remains after both. A
    report whose cache figures exceed its prompt is refused rather than
    rounded: it cannot be divided honestly. None when nothing was stated.
    """
    if reported is None:
        return None
    prompt = token_count(_field(reported, "prompt_tokens"))
    completion = token_count(_field(reported, "completion_tokens"))
    details = _field(reported, "prompt_tokens_details")
    cached = token_count(_field(details, "cached_tokens"))
    created = token_count(_field(details, "cache_write_tokens"))
    result = {}
    if prompt is not None:
        if (cached or 0) + (created or 0) > prompt:
            raise ValueError("provider cache counters exceed total prompt tokens")
        result["input_tokens"] = prompt - (cached or 0) - (created or 0)
    if completion is not None:
        result["output_tokens"] = completion
    if cached is not None:
        result["cache_read_input_tokens"] = cached
    if created is not None:
        result["cache_creation_input_tokens"] = created
    return result or None


#: Chat Completions finish reasons, in the stop vocabulary the loop reads.
FINISH_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


class OpenAIProvider(ApiProvider):
    """One Chat Completions call to OpenAI or OpenRouter. The loop decides
    whether to make it; `ApiProvider` keeps its deadline and admission."""

    def __init__(self, model: str, *, backend: str = "openai", base_url: str | None = None, **kwargs):
        if backend not in ENDPOINTS:
            raise ValueError("OpenAI-compatible provider requires openai or openrouter backend")
        super().__init__(model, **kwargs)
        self.backend = "openai-api" if backend == "openai" else "openrouter"
        self._selected = backend
        self._base_url = base_url or os.environ.get(BASE_URLS[backend]) or ENDPOINTS[backend]

    def client(self) -> OpenAI:
        """The SDK client, built on the first call that needs one, from the
        selected backend's own key and endpoint."""
        if self._client is None:
            key = os.environ.get(KEYS[self._selected])
            if not key:
                raise RuntimeError(f"the {self._selected} backend needs {KEYS[self._selected]}")
            sdk = load_sdk()
            options = dict(api_key=key, base_url=self._base_url, max_retries=0)
            self._client = openrouter_client(sdk, **options) if self._selected == "openrouter" else sdk.OpenAI(**options)
        return self._client

    @property
    def endpoint(self) -> str:
        """Where this transport sends, redacted for the record."""
        base = getattr(self._client, "base_url", None) if self._client is not None else self._base_url
        return f"chat completions ({redacted(str(base or self._base_url))})"

    def _request(self, system, messages, specs):
        request = {"model": self.model, "messages": as_messages(messages)}
        if system:
            request["messages"].insert(0, {"role": "system", "content": system})
        # OpenRouter accepts the common gateway cap. OpenAI's current API also
        # counts reasoning within max_completion_tokens.
        request["max_tokens" if self._selected == "openrouter" else "max_completion_tokens"] = self.max_tokens
        if specs:
            request["tools"] = deepcopy(list(specs))
        return request

    def _send(self, request):
        return self.client().chat.completions.create(**request)

    def _usage(self, reply):
        reported = _field(reply, "usage")
        result = usage(reported)
        # OpenRouter explicitly denominates usage.cost in USD. A generic
        # compatible endpoint's arbitrary extension does not establish units.
        cost = _field(reported, "cost") if self._selected == "openrouter" else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            result = {**(result or {}), "cost_usd": cost}
        return result

    def _turn(self, reply, reported):
        choices = _field(reply, "choices") or []
        if len(choices) != 1:
            raise ValueError("provider must return exactly one completion choice")
        selected = choices[0]
        message = _field(selected, "message")
        text = _field(message, "content") or ""
        if not isinstance(text, str):
            raise ValueError("provider returned non-text completion content")
        wire: dict[str, Any] = {"role": "assistant", "content": text or None}
        parsed = [_tool_call(call) for call in _field(message, "tool_calls") or []]
        if parsed:
            wire["tool_calls"] = [entry for _, entry in parsed]
        reasoning = []
        for field in ("reasoning_details", "reasoning_content"):
            value = _field(message, field)
            if value is not None:
                wire[field] = value
                reasoning.append({field: value})
        finish = _field(selected, "finish_reason")
        stop = "refusal" if _field(message, "refusal") else FINISH_REASONS.get(finish, finish or "unknown")
        return ProviderTurn(text=text, tool_calls=tuple(call for call, _ in parsed), blocks=(wire,),
            thinking=bool(reasoning), reasoning=tuple(reasoning), usage=reported, stop_reason=stop)


def _tool_call(call: object) -> tuple[ToolCall, dict[str, Any]]:
    """One function call from a reply: as the loop runs it, and as it goes
    back on the wire, with its arguments exactly as the provider encoded them."""
    function = _field(call, "function")
    identifier, name = _field(call, "id"), _field(function, "name")
    if _field(call, "type") != "function" or not identifier or not name:
        raise ValueError("provider returned an invalid function call")
    encoded = _field(function, "arguments")
    try:
        arguments = json.loads(encoded)
    except (ValueError, TypeError) as error:
        raise ValueError("provider function arguments must be a JSON object") from error
    if not isinstance(arguments, dict):
        raise ValueError("provider function arguments must be a JSON object")
    wire = {"id": identifier, "type": "function", "function": {"name": name, "arguments": encoded}}
    return ToolCall(identifier, name, arguments), wire
