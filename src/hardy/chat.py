from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol

from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .latex import LatexTools
from .lean import LeanTools
from .models import Request, ToolResult, TurnEvent
from .prompts import CHAT_SYSTEM_PROMPT, chat_cas_prompt

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

# The text lives in prompts/chat.md.j2. Kept under the old name because it is
# what a reader of _build expects to see, and what the tests reach for.
SYSTEM_PROMPT = CHAT_SYSTEM_PROMPT


class ChatRuntime(Protocol):
    model: str
    def stream(self, text: str) -> Iterator[TurnEvent]: ...
    def ask(self, text: str) -> str: ...
    def cancel(self) -> None: ...


def final_text(events: Iterable[TurnEvent]) -> str:
    """Drain a turn and keep the reply it settled on.

    The one place a blocking caller turns a stream back into the string it
    used to get. It reads the `reply` event and never the `text` deltas: the
    deltas are for drawing, and assembling the answer from them as well would
    return every word twice.
    """
    reply = ""
    for event in events:
        if event.kind == "reply":
            reply = event.text
    return reply


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
    def __init__(self, workspace: Path, make_runtime: Callable[..., ChatRuntime], lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, str]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0, cas: CasToolRuntime | None = None, cas_detail: str = ""):
        self.workspace = workspace
        self.confirm = confirm
        # None when no backend was discovered. Nothing downstream advertises a
        # cas_* tool in that case, rather than offering one that always fails.
        self.cas = cas
        # `cas_tools.build_runtime`'s second return value: a version string
        # when `cas` is not None, the reason it is None otherwise. Not used
        # by this class itself -- carried only so a caller showing a banner
        # (the interactive session, real or plain) has it without needing to
        # keep its own `build_runtime` call in sync with this one.
        self.cas_detail = cas_detail
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
        # Set for the rest of a turn once it is cancelled, and cleared when the
        # next one starts. Read by `_dispatch` on the SDK's own tool threads,
        # which is why it is an Event rather than a bare bool.
        self._cancelled = threading.Event()
        # `session.json` is written from more than one thread -- a tool call
        # under the gate above, and `_observed` remembering the provider thread
        # on the runtime's own thread -- and `_atomic_json` replaces a temporary
        # file at a fixed path. Two writers at once would interleave into it.
        self._writes = threading.Lock()
        # State first: the runtime is built from the system prompt, which embeds
        # the manifest, and it resumes the provider thread the state remembers.
        self.state = self._read_state()
        # The runtime needs a way to reach the tools, and the tools need the
        # workspace, so it is built here rather than handed in ready-made.
        self.runtime = self._build(session_id=self.state.get("provider_session"))
        self._sync_provenance()

    def _build(self, model: str | None = None, session_id: str | None = None) -> ChatRuntime:
        prompt = SYSTEM_PROMPT
        if self.cas is not None:
            prompt += "\n\n" + chat_cas_prompt(self.cas.session.backend.name)
        return self._make_runtime(
            model=model,
            system_prompt=prompt + self._context() + self._carried(session_id),
            specs=CHAT_TOOLS + (CAS_TOOLS if self.cas is not None else []),
            dispatch=self._dispatch,
            cwd=self.workspace,
            session_id=session_id,
            observe=self._observed,
        )

    def _observed(self, event: dict[str, Any]) -> None:
        """What the runtime reports, recorded and acted on.

        `_stream`'s teardown remembers the provider thread for a turn somebody
        drained. This covers one nobody did -- `stream` supports that, and the
        runtime's worker is eager, so the turn really happens. Left to the
        generator alone, reopening the workspace would start from nothing while
        the artifacts on disk implied a conversation that had already taken
        place.
        """
        self._record(event)
        if event.get("type") == "result":
            self._remember_thread()

    def switch_model(self, model: str) -> None:
        """Continue this conversation on a different model.

        The transcript records the change because which model produced which
        turn is part of the experiment's identity, not a UI detail. The provider
        thread is carried over, so the new model inherits the conversation.
        """
        previous = {key: self.state.get(key) for key in ("model", "backend", "endpoint")}
        self.runtime = self._build(model=model, session_id=self.state.get("provider_session"))
        self.state.update(provenance(self.runtime))
        self._save_state()
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

    def _save_state(self) -> None:
        """The one door `session.json` is written through, from any thread."""
        with self._writes:
            _atomic_json(self.state_path, self.state)

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
        self._save_state()
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
        if name in CAS_TOOL_NAMES:
            return self._cas_tool(name, arguments)
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
                self._save_state()
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
                self._save_state()
            declaration = f"axiom {proposal['formal_name']} : {proposal['lean_statement']}"
            return ToolResult(True, f"User approved. Declare exactly `{declaration}` and disclose source `{proposal['source']}` in the writeup.")
        return ToolResult(False, f"unknown tool: {name}")

    def _cas_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """The computer algebra tools. Errors are answers, not exceptions.

        A model that asked for a cell and got a traceback learns nothing it can
        act on; a model told the budget is gone, or the session is poisoned and
        needs a reset, can do something about it.
        """
        if self.cas is None:
            return ToolResult(False, "no computer algebra backend is configured")
        try:
            if name == "cas_run":
                result = self.cas.run(str(arguments["source"]))
                return ToolResult(result.status == "ok", result.model_dump_json())
            if name == "cas_state":
                return ToolResult(True, self.cas.state().model_dump_json())
            if name == "cas_reset":
                return ToolResult(True, self.cas.reset().model_dump_json())
            if name == "cas_export":
                report = export_session(self.cas.session, self.workspace / "cas")
                self.state["cas_export"] = {
                    "script": report.script_path,
                    "notebook": report.notebook_path,
                    "reproduces": report.reproduces,
                }
                self._save_state()
                return ToolResult(True, report.model_dump_json())
        except CasError as error:
            return ToolResult(False, str(error))
        return ToolResult(False, f"unknown tool: {name}")

    def stream(self, text: str) -> Iterator[TurnEvent]:
        """One exchange, as it arrives. The SDK decides how many tools to call.

        Hardy no longer counts the turns — see issue #23. What it still does is
        run every tool the model asks for, and write down what happened.

        The events are for whoever is drawing the turn. What lands in
        `transcript.jsonl` is unchanged and still comes from `observe` and
        `_dispatch`: the record holds whole blocks and tool results, because a
        transcript of ten thousand token deltas would be worse evidence, not
        better.
        """
        # Deliberately not a generator itself, and neither is the runtime's
        # `stream`. A generator body does not run until it is first iterated,
        # which would make the record of the turn wait on a consumer that may
        # never come -- and the whole point of `record_abandonment` is that a
        # turn nobody waited for still leaves a trace.
        #
        # It also decides where the per-turn reset below happens. The terminal
        # iterates on a worker thread, so a lazy body would clear the flag
        # *after* an Esc pressed in the same input batch as the Enter that
        # started the turn, wiping a cancellation the transcript had already
        # recorded. Starting a turn belongs on the thread that sequenced it;
        # only the waiting belongs on the worker.
        self._record({"type": "user", "message": {"role": "user", "content": text}})
        # Cleared here rather than in `cancel`: a turn cancelled during the
        # previous exchange must not silently disarm this one's tool gate.
        self._cancelled.clear()
        return self._stream(self.runtime.stream(text))

    def _stream(self, events: Iterator[TurnEvent]) -> Iterator[TurnEvent]:
        # An explicit `yield`, not `yield from`. A consumer that unwinds --
        # Ctrl+C in `--plain`, most of all -- closes this generator, and with
        # `yield from` that teardown would reach the runtime first: it
        # interrupts the model and then waits on its worker, all while this
        # session's tool gate is still open and the provider can dispatch one
        # more call. Yielding here means the gate shuts before any of that.
        iterator = iter(events)
        try:
            while True:
                try:
                    event = next(iterator)
                except StopIteration:
                    return
                try:
                    yield event
                except BaseException:
                    self._cancelled.set()
                    raise
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
            # Even a failed exchange belongs to a provider thread, and that turn
            # and its tool calls are only reachable again by resuming it.
            self._remember_thread()

    def send(self, text: str) -> str:
        """`stream`, for a caller with nothing to draw it on."""
        return final_text(self.stream(text))

    def cancel(self, reason: str = "user_cancelled") -> None:
        """Stop the turn. Idempotent, and callable from any thread.

        What this can honestly promise is that the model stops and that no
        *further* tool call will run. It cannot promise that a Lean or LaTeX
        process already started will stop, or that a file such a call has
        already written will be unwritten — so `_dispatch` lets work in flight
        finish rather than tearing it out from under the workspace.
        """
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        self._record({"type": "turn", "status": "cancelled", "reason": reason})
        cancel = getattr(self.runtime, "cancel", None)
        if cancel is not None:
            cancel()

    def record_abandonment(self, reason: str) -> None:
        """Write down that a turn was walked away from.

        The terminal shows a notice, but a notice dies with the session and
        `transcript.jsonl` is what replay and evaluation read. Without this, a
        turn the user abandoned is indistinguishable from one they waited for.
        """
        self._record({"type": "turn", "status": "abandoned", "reason": reason})

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """The single door every tool call goes through, whoever asked for it.

        Recorded here rather than by the caller: the SDK reports that it *asked*
        for a tool, but only Hardy knows what running it produced, and a
        trajectory without the results is not an account of what happened.
        """
        # Checked before the gate, not inside it: a cancelled turn's queued
        # tool calls must not first wait behind the Lean check that is still
        # finishing. Refusing is all cancellation can do here — a call already
        # past this point owns a subprocess and its workspace writes, and
        # interrupting it halfway would leave worse behind than letting it end.
        if self._cancelled.is_set():
            return self._refuse_cancelled(name, arguments)
        with self._gate:
            # Checked again, now that the gate is held. The SDK may launch
            # several calls at once: one of them can pass the check above,
            # block here behind a Lean run that takes minutes, and reach this
            # line long after the turn was cancelled. Without the second look
            # it would then start fresh work and write to the workspace, which
            # is exactly what `cancel` promises will not happen.
            if self._cancelled.is_set():
                return self._refuse_cancelled(name, arguments)
            try:
                result = self._tool(name, arguments)
            except (KeyError, TypeError, ValueError) as error:
                result = ToolResult(False, f"invalid tool call: {error}")
            self._record({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
            return result

    def _refuse_cancelled(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Still recorded: a trajectory that simply omitted the call would not
        show that the model asked for it."""
        result = ToolResult(False, "the turn was cancelled before this tool call was made")
        self._record({"type": "tool", "name": name, "arguments": arguments, "result": result.as_dict()})
        return result

    def _remember_thread(self) -> None:
        thread = getattr(self.runtime, "session_id", None)
        if thread and self.state.get("provider_session") != thread:
            self.state["provider_session"] = thread
            self._save_state()
