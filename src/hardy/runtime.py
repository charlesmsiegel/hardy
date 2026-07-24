from __future__ import annotations

import json
import urllib.request
from typing import Any

from . import catalog


TOOLS = [
    {"type": "function", "function": {"name": "check_proof", "description": "Elaborate a complete candidate proof against the unchanged theorem statement.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_goal", "description": "Show the Lean goal after an optional tactic prefix; this intermediate tool uses a visible sorry.", "parameters": {"type": "object", "properties": {"tactic": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_declaration", "description": "Ask Lean to check and print one exact declaration name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "submit_proof", "description": "Submit a candidate as the final proof. Hardy independently checks it and rejects holes.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
]


def portable(message: dict[str, Any]) -> dict[str, Any]:
    """One message without the private keys a backend stashed on it.

    A conversation can change provider mid-flight, so every message may carry
    another backend's bookkeeping. Providers reject request fields they do not
    recognise, so strip anything underscored before sending.
    """
    return {key: value for key, value in message.items() if not key.startswith("_")}


class OpenAICompatibleRuntime:
    backend = catalog.OPENAI

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.endpoint = base_url.rstrip("/")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = [portable(message) for message in messages]
        body = json.dumps({"model": self.model, "messages": payload, "tools": tools or TOOLS, "tool_choice": "auto"}).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            answer = json.load(response)
        return answer["choices"][0]["message"]


def build(model: str, backend: str, api_key: str, base_url: str, max_tokens: int | None = None) -> Any:
    """The runtime for one model. The backend decides which provider is called."""
    if backend == catalog.ANTHROPIC:
        from .anthropic_runtime import DEFAULT_MAX_TOKENS, AnthropicRuntime

        return AnthropicRuntime(api_key, model, max_tokens=max_tokens or DEFAULT_MAX_TOKENS)
    return OpenAICompatibleRuntime(base_url, api_key, model)
