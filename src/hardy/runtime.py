from __future__ import annotations

import json
import urllib.request
from typing import Any


TOOLS = [
    {"type": "function", "function": {"name": "check_proof", "description": "Elaborate a complete candidate proof against the unchanged theorem statement.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_goal", "description": "Show the Lean goal after an optional tactic prefix; this intermediate tool uses a visible sorry.", "parameters": {"type": "object", "properties": {"tactic": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_declaration", "description": "Ask Lean to check and print one exact declaration name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "submit_proof", "description": "Submit a candidate as the final proof. Hardy independently checks it and rejects holes.", "parameters": {"type": "object", "properties": {"proof": {"type": "string"}}, "required": ["proof"], "additionalProperties": False}}},
]


class OpenAICompatibleRuntime:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools or TOOLS, "tool_choice": "auto"}).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        return payload["choices"][0]["message"]
