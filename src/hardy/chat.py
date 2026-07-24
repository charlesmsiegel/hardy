from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .latex import LatexTools
from .lean import LeanTools
from .models import Request, ToolResult

CHAT_TOOLS = [
    {"type": "function", "function": {"name": "check_lean", "description": "Run Lean on a complete candidate source file. This does not save it.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_lean", "description": "Check and save Main.lean. Completed saved work must contain no sorry or admit.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "check_latex", "description": "Compile a complete LaTeX document without saving it.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_latex", "description": "Compile and save writeup.tex.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_workspace", "description": "Read the current Lean, LaTeX, naming, and assumption artifacts.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "record_name", "description": "Record the durable correspondence between a Lean declaration and its LaTeX label/name.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "latex_name": {"type": "string"}, "description": {"type": "string"}}, "required": ["formal_name", "latex_name", "description"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "request_assumption", "description": "Ask the human for permission to introduce an axiom when a result is unavailable. Never assume approval.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "lean_statement": {"type": "string"}, "latex_name": {"type": "string"}, "informal_statement": {"type": "string"}, "source": {"type": "string"}, "reason": {"type": "string"}}, "required": ["formal_name", "lean_statement", "latex_name", "informal_statement", "source", "reason"], "additionalProperties": False}}},
]

SYSTEM_PROMPT = """You are Hardy, an interactive mathematical research agent. Explore mathematics with the user while maintaining linked formal and human artifacts.

Use Lean for every formal claim you report as verified. Distinguish speculation, heuristic review, LaTeX compilation, and kernel verification. Never change a claim silently to make a proof pass. Keep Main.lean and writeup.tex aligned through the naming registry: record each important declaration with record_name and use its latex_name as a LaTeX label. Check artifacts before saving them. A LaTeX compile is not mathematical verification.

If a needed theorem is not in Mathlib or the user's imports, search/check first. If it must be assumed, call request_assumption with the exact Lean statement, informal statement, source identity, and reason. Only use the axiom after explicit human approval, and make the assumption visible in both artifacts. Partial work is welcome when holes and assumptions are explicit.

Generated Lean and LaTeX are not sandboxed. Keep tool use focused. Explain progress conversationally after tool calls."""


class ChatRuntime(Protocol):
    model: str
    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


def provenance(runtime: Any) -> dict[str, Any]:
    """What produced a turn: the model alone does not identify the provider.

    The same `claude-opus-5` answered by Anthropic and by an OpenAI-compatible
    gateway are different experimental conditions, and a transcript that records
    only the identity cannot tell them apart afterwards.
    """
    return {"model": runtime.model, "backend": getattr(runtime, "backend", None), "endpoint": getattr(runtime, "endpoint", None)}


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class MathematicsSession:
    def __init__(self, workspace: Path, runtime: ChatRuntime, lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, str]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0):
        self.workspace = workspace
        self.runtime = runtime
        self.confirm = confirm
        self.workspace.mkdir(parents=True, exist_ok=True)
        placeholder = Request("example : True", "interactive workspace", ("Mathlib",))
        self.lean = LeanTools(placeholder, lean_command, timeout=lean_timeout, project=lean_project)
        self.latex = LatexTools(latex_command)
        self.state_path = workspace / "session.json"
        self.transcript_path = workspace / "transcript.jsonl"
        self.state = self._load_state()
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT + self._context()},
            *self._load_messages(),
        ]

    def set_runtime(self, runtime: ChatRuntime) -> None:
        """Continue this conversation on a different model.

        The transcript records the change because which model produced which
        turn is part of the experiment's identity, not a UI detail.
        """
        previous = {key: self.state.get(key) for key in ("model", "backend", "endpoint")}
        self.runtime = runtime
        self.state.update(provenance(runtime))
        _atomic_json(self.state_path, self.state)
        self._record({"type": "model", "previous": previous, **provenance(runtime)})

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        state = {"schema_version": 1, **provenance(self.runtime), "names": [], "assumptions": []}
        _atomic_json(self.state_path, state)
        return state

    def _context(self) -> str:
        return f"\n\nWorkspace: {self.workspace}\nExisting manifest:\n{json.dumps(self.state, ensure_ascii=False)}"

    def _load_messages(self) -> list[dict[str, Any]]:
        if not self.transcript_path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line in self.transcript_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") in {"user", "assistant", "tool"} and isinstance(event.get("message"), dict):
                messages.append(event["message"])
        return messages

    def _record(self, event: dict[str, Any]) -> None:
        event = {"timestamp": time.time(), **event}
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _run_lean_source(self, source: str, *, final: bool) -> ToolResult:
        if final and self.lean.has_holes(source):
            return ToolResult(False, "saved Lean artifacts may not contain sorry or admit", source)
        if final:
            approved = {item["formal_name"]: " ".join(item["lean_statement"].split()) for item in self.state["assumptions"]}
            declarations = re.findall(r"(?m)^\s*(?:axiom|constant)\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*(.+?)\s*$", source)
            for name, statement in declarations:
                if approved.get(name) != " ".join(statement.split()):
                    return ToolResult(False, f"unapproved or altered assumption `{name}`; use request_assumption first", source)
            missing_names = [item["formal_name"] for item in self.state["names"] if not re.search(rf"\b{re.escape(item['formal_name'])}\b", source)]
            if missing_names:
                return ToolResult(False, f"Lean source is missing registered names: {missing_names}", source)
        return self.lean.run_source(source)

    def _tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name == "check_lean":
            return self._run_lean_source(str(arguments["source"]), final=False)
        if name == "save_lean":
            source = str(arguments["source"])
            result = self._run_lean_source(source, final=True)
            if result.ok:
                (self.workspace / "Main.lean").write_text(source.rstrip() + "\n", encoding="utf-8")
            return result
        if name == "check_latex":
            return self.latex.check(str(arguments["source"]))
        if name == "save_latex":
            source = str(arguments["source"])
            missing_labels = [item["latex_name"] for item in self.state["names"] if f"\\label{{{item['latex_name']}}}" not in source]
            if missing_labels:
                return ToolResult(False, f"LaTeX source is missing registered labels: {missing_labels}", source)
            result = self.latex.check(source, output_dir=self.workspace)
            if result.ok:
                (self.workspace / "writeup.tex").write_text(source.rstrip() + "\n", encoding="utf-8")
            return result
        if name == "read_workspace":
            payload = {"manifest": self.state}
            for filename in ("Main.lean", "writeup.tex"):
                path = self.workspace / filename
                payload[filename] = path.read_text(encoding="utf-8") if path.exists() else None
            return ToolResult(True, json.dumps(payload, ensure_ascii=False))
        if name == "record_name":
            entry = {key: str(arguments[key]) for key in ("formal_name", "latex_name", "description")}
            existing = next((item for item in self.state["names"] if item["formal_name"] == entry["formal_name"] or item["latex_name"] == entry["latex_name"]), None)
            if existing and existing != entry:
                return ToolResult(False, f"name conflicts with existing mapping: {existing}")
            if not existing:
                self.state["names"].append(entry)
                _atomic_json(self.state_path, self.state)
            return ToolResult(True, f"recorded mapping: {entry}")
        if name == "request_assumption":
            proposal = {key: str(arguments[key]) for key in ("formal_name", "lean_statement", "latex_name", "informal_statement", "source", "reason")}
            if not self.confirm(proposal):
                return ToolResult(False, "The user declined this assumption. Do not use it.")
            proposal["status"] = "user-approved"
            if not any(item["formal_name"] == proposal["formal_name"] for item in self.state["assumptions"]):
                self.state["assumptions"].append(proposal)
                mapping = {"formal_name": proposal["formal_name"], "latex_name": proposal["latex_name"], "description": proposal["informal_statement"]}
                self.state["names"].append(mapping)
                _atomic_json(self.state_path, self.state)
            declaration = f"axiom {proposal['formal_name']} : {proposal['lean_statement']}"
            return ToolResult(True, f"User approved. Declare exactly `{declaration}` and disclose source `{proposal['source']}` in the writeup.")
        return ToolResult(False, f"unknown tool: {name}")

    def send(self, text: str, *, max_tool_rounds: int = 12) -> str:
        message = {"role": "user", "content": text}
        self.messages.append(message)
        self._record({"type": "user", "message": message})
        for _ in range(max_tool_rounds):
            response = self.runtime.complete(self.messages, tools=CHAT_TOOLS)
            self.messages.append(response)
            self._record({"type": "assistant", "message": response})
            calls = response.get("tool_calls") or []
            if not calls:
                return str(response.get("content") or "")
            for call in calls:
                try:
                    arguments = json.loads(call["function"].get("arguments", "{}"))
                    result = self._tool(call["function"]["name"], arguments)
                except (KeyError, TypeError, json.JSONDecodeError) as error:
                    result = ToolResult(False, f"invalid tool call: {error}")
                tool_message = {"role": "tool", "tool_call_id": call.get("id", "missing"), "content": json.dumps(result.as_dict())}
                self.messages.append(tool_message)
                self._record({"type": "tool", "name": call.get("function", {}).get("name"), "message": tool_message})
        warning = "Tool-round limit reached; work is partial."
        self._record({"type": "limit", "message": warning})
        return warning
