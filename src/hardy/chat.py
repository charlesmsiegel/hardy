from __future__ import annotations

import json
import os
import re
import threading
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

# A migrated workspace carries a bounded tail of its old conversation, enough to
# resume sensibly without pretending the whole history is still in context.
MIGRATED_TURNS = 20
MIGRATED_CHARACTERS = 8000

SYSTEM_PROMPT = """You are Hardy, an interactive mathematical research agent. Explore mathematics with the user while maintaining linked formal and human artifacts.

Use Lean for every formal claim you report as verified. Distinguish speculation, heuristic review, LaTeX compilation, and kernel verification. Never change a claim silently to make a proof pass. Keep Main.lean and writeup.tex aligned through the naming registry: record each important declaration with record_name and use its latex_name as a LaTeX label. Check artifacts before saving them. A LaTeX compile is not mathematical verification.

If a needed theorem is not in Mathlib or the user's imports, search/check first. If it must be assumed, call request_assumption with the exact Lean statement, informal statement, source identity, and reason. Only use the axiom after explicit human approval, and make the assumption visible in both artifacts. Partial work is welcome when holes and assumptions are explicit.

Generated Lean and LaTeX are not sandboxed. Keep tool use focused. Explain progress conversationally after tool calls."""


class ChatRuntime(Protocol):
    model: str
    def ask(self, text: str) -> str: ...


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
    def __init__(self, workspace: Path, make_runtime: Callable[..., ChatRuntime], lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, str]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0):
        self.workspace = workspace
        self.confirm = confirm
        self.workspace.mkdir(parents=True, exist_ok=True)
        placeholder = Request("example : True", "interactive workspace", ("Mathlib",))
        self.lean = LeanTools(placeholder, lean_command, timeout=lean_timeout, project=lean_project)
        self.latex = LatexTools(latex_command)
        self.state_path = workspace / "session.json"
        self.transcript_path = workspace / "transcript.jsonl"
        self._make_runtime = make_runtime
        # The SDK may call several tools at once, each on its own thread, but
        # these run Lean, rewrite session.json, and stop to ask a human for
        # approval. None of that is safe to interleave.
        self._gate = threading.Lock()
        # State first: the runtime is built from the system prompt, which embeds
        # the manifest, and it resumes the provider thread the state remembers.
        self.state = self._read_state()
        # The runtime needs a way to reach the tools, and the tools need the
        # workspace, so it is built here rather than handed in ready-made.
        self.runtime = self._build(session_id=self.state.get("provider_session"))
        self._sync_provenance()

    def _build(self, model: str | None = None, session_id: str | None = None) -> ChatRuntime:
        return self._make_runtime(
            model=model,
            system_prompt=SYSTEM_PROMPT + self._context() + self._carried(session_id),
            specs=CHAT_TOOLS,
            dispatch=self._dispatch,
            cwd=self.workspace,
            session_id=session_id,
            observe=self._record,
        )

    def switch_model(self, model: str) -> None:
        """Continue this conversation on a different model.

        The transcript records the change because which model produced which
        turn is part of the experiment's identity, not a UI detail. The provider
        thread is carried over, so the new model inherits the conversation.
        """
        previous = {key: self.state.get(key) for key in ("model", "backend", "endpoint")}
        self.runtime = self._build(model=model, session_id=self.state.get("provider_session"))
        self.state.update(provenance(self.runtime))
        _atomic_json(self.state_path, self.state)
        self._record({"type": "model", "reason": "switched", "previous": previous, **provenance(self.runtime)})

    def _carried(self, session_id: str | None) -> str:
        """What a workspace from before the SDK backend brings with it.

        Its transcript is on disk but belongs to no provider thread, so without
        this the first exchange after upgrading would start from nothing while
        the artifacts on disk implied a conversation that had already happened.
        """
        if session_id or not self.transcript_path.exists():
            return ""
        said = []
        for line in self.transcript_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Not every event carries a message object: a `limit` event from the
            # runtime this migration exists to leave behind carries a bare
            # string, and reaching into it would stop the workspace reopening.
            if event.get("type") not in {"user", "assistant"}:
                continue
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if content:
                said.append(f"{event['type']}: {content}")
        if not said:
            return ""
        # From the end: a long older message must not displace the exchange
        # the conversation actually left off in.
        carried = "\n".join(said[-MIGRATED_TURNS:])[-MIGRATED_CHARACTERS:]
        self._record({"type": "migration", "carried_turns": min(len(said), MIGRATED_TURNS)})
        return f"\n\nThis workspace predates the current provider session. Earlier conversation, for context only:\n{carried}"

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"schema_version": 1, "names": [], "assumptions": []}

    def _sync_provenance(self) -> None:
        """Make the record agree with what is actually about to answer.

        Reopening a workspace under a different model is a change of
        experimental condition like any other, and resumed turns must not be
        attributed to the model that produced the earlier ones.
        """
        current = provenance(self.runtime)
        if all(self.state.get(key) == value for key, value in current.items()):
            return
        previous = {key: self.state.get(key) for key in current}
        started = any(previous.values())
        self.state.update(current)
        _atomic_json(self.state_path, self.state)
        if started:
            self._record({"type": "model", "reason": "session_resumed", "previous": previous, **current})

    def _context(self) -> str:
        return f"\n\nWorkspace: {self.workspace}\nExisting manifest:\n{json.dumps(self.state, ensure_ascii=False)}"

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

    def send(self, text: str) -> str:
        """One exchange. The provider's SDK decides how many tools to call.

        Hardy no longer counts the turns — see issue #23. What it still does is
        run every tool the model asks for, and write down what happened.
        """
        self._record({"type": "user", "message": {"role": "user", "content": text}})
        try:
            return self.runtime.ask(text)
        finally:
            # Even a failed exchange belongs to a provider thread, and that turn
            # and its tool calls are only reachable again by resuming it.
            self._remember_thread()

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """The single door every tool call goes through, whoever asked for it.

        Recorded here rather than by the caller: the SDK reports that it *asked*
        for a tool, but only Hardy knows what running it produced, and a
        trajectory without the results is not an account of what happened.
        """
        with self._gate:
            try:
                result = self._tool(name, arguments)
            except (KeyError, TypeError, ValueError) as error:
                result = ToolResult(False, f"invalid tool call: {error}")
            self._record({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
            return result

    def _remember_thread(self) -> None:
        thread = getattr(self.runtime, "session_id", None)
        if thread and self.state.get("provider_session") != thread:
            self.state["provider_session"] = thread
            _atomic_json(self.state_path, self.state)
