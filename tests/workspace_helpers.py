"""Reading a session's tool results back out of its transcript.

`session` builds its runtime through a factory, so the `FakeChatRuntime` a test
holds is not the one the session drove and its `results` list stays empty. The
transcript is the record that actually exists, and asserting against it is also
asserting that Hardy wrote the trajectory down.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def events(workspace: Path) -> list[dict[str, Any]]:
    transcript = workspace / "transcript.jsonl"
    if not transcript.exists():
        return []
    return [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]


def tool_calls(workspace: Path, name: str | None = None) -> list[dict[str, Any]]:
    found = [event for event in events(workspace) if event.get("type") == "tool"]
    return [event for event in found if name is None or event["name"] == name]


def results(workspace: Path, name: str | None = None) -> list[dict[str, Any]]:
    return [event["result"] for event in tool_calls(workspace, name)]
