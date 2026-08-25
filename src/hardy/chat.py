from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from . import audit, completion, process
from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .latex import ROOT_DOCUMENT, LatexTools
from .lean import LeanTools
from .models import Request, ToolResult, TurnEvent
from .prompts import CHAT_SYSTEM_PROMPT, chat_cas_prompt
from .usage import Usage
from .workspace import (
    BuildFailure,
    ImportCycle,
    LeanWorkspace,
    WorkspacePathError,
    assumptions,
    declarations,
    dependents,
    internal_imports,
    module_name,
    module_path,
    safe_relative,
    statements,
    unreadable_assumptions,
)

# Where the two artifact trees live inside a workspace. A session written
# before they existed kept one file of each at the top level; `_migrate_layout`
# moves those in rather than leaving a workspace that reads as empty.
LEAN_DIR = "lean"
BUILD_DIR = ".build/lean"
BUILD_DIR_TEX = ".build/tex"
TEX_DIR = "tex"
DEFAULT_LEAN_PATH = "Main.lean"
DEFAULT_TEX_PATH = ROOT_DOCUMENT

# What LaTeX wrote down about the labels it actually created, in its own .aux.
NEWLABEL = re.compile(r"\\newlabel\{([^}]*)\}")

# The manifest keys that exist for Hardy and not for the model. `audit` is
# withheld because the listing reports each verdict checked against the tree in
# front of it, and handing back the stored one as well would put two answers for
# the same module in one response. `usage` is withheld because what the session
# has cost is not something the model can act on, and letting it into the system
# prompt would make a resumed session's prompt differ from a fresh one by an
# amount that has nothing to do with the mathematics.
USAGE_KEY = "usage"
#: How far into `transcript.jsonl` the stored ledger has been brought up to
#: date. Hardy's own bookkeeping, and no more the model's business than the
#: ledger it belongs to.
CURSOR_KEY = "usage_cursor"
LEDGER_KEYS = (USAGE_KEY, CURSOR_KEY)
WITHHELD = ("audit", *LEDGER_KEYS)

CHAT_TOOLS = [
    {"type": "function", "function": {"name": "check_lean", "description": "Run Lean on a complete candidate source file without saving it. `path` is the workspace file it would become, defaulting to Main.lean; imports of other workspace files resolve against what is already saved.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_lean", "description": "Check and save one Lean file in the workspace tree, defaulting to Main.lean. Every file importing it is rebuilt and the save is refused whole if any of them breaks. Completed saved work must contain no sorry or admit.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "check_latex", "description": "Compile a candidate LaTeX file against the saved document tree without keeping it. `path` defaults to writeup.tex, the root document.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_latex", "description": "Compile and save one LaTeX file in the writeup tree, defaulting to writeup.tex. Fragments are \\input from the root document.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_workspace", "description": "List the workspace: the manifest, every Lean file with its module name and declarations, and every LaTeX file.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read one workspace file, Lean or LaTeX, by its path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete one workspace file. Refused if another workspace file imports it.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "record_name", "description": "Record the durable correspondence between a Lean declaration and its LaTeX label/name.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "latex_name": {"type": "string"}, "description": {"type": "string"}}, "required": ["formal_name", "latex_name", "description"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "request_assumption", "description": "Ask the human for permission to introduce an axiom when a result is unavailable. Never assume approval.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "lean_statement": {"type": "string"}, "latex_name": {"type": "string"}, "informal_statement": {"type": "string"}, "source": {"type": "string"}, "reason": {"type": "string"}}, "required": ["formal_name", "lean_statement", "latex_name", "informal_statement", "source", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "report_result", "description": "Report finished work. The only way to call anything proved, done, or complete: say it in prose and Hardy contradicts you in front of the user. Refused unless every theorem named is saved Lean the kernel audited, the writeup creates its label and quotes its exact Lean statement verbatim, and every assumption the work rests on is stated in an appendix in both Lean and prose.", "parameters": {"type": "object", "properties": {"theorems": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}, "required": ["theorems", "summary"], "additionalProperties": False}}},
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


def _toolchain_identity(lean_command: tuple[str, ...], lean_project: Path | None) -> str:
    """What an olean in this workspace was built by.

    An olean is only meaningful for the toolchain and project that produced it.
    Reopening a workspace after switching Lean project or bumping the pinned
    toolchain must rebuild, or a check would be reported as current while
    resting on an artifact from a different configuration. The project's
    `lean-toolchain` is read because it is what `elan` pins the compiler with,
    and reading a small file is cheaper than running Lean to ask its version.
    """
    parts = [" ".join(lean_command), str(lean_project or "")]
    # The compiler itself, so an upgrade behind an unchanged command still
    # invalidates. This is the only identity there is when no project is
    # configured, which is a supported way to run. Its size and mtime rather
    # than its contents: a toolchain binary is large, this runs on every save,
    # and either changing means it is not the executable that built the cache.
    executable = shutil.which(lean_command[0]) if lean_command else None
    if executable:
        try:
            stamp = Path(executable).stat()
            parts.append(f"{executable}:{stamp.st_size}:{stamp.st_mtime_ns}")
        except OSError:
            parts.append(f"{executable}:unreadable")
    for name in ("lean-toolchain",):
        # Also the toolchain pin beside the working directory, which is what
        # `elan` reads when Lean runs outside a configured project.
        local = (lean_project or Path.cwd()) / name
        if lean_project is None and local.is_file():
            try:
                parts.append(hashlib.sha256(local.read_bytes()).hexdigest())
            except OSError:
                parts.append(f"{name}:unreadable")
    if lean_project is not None:
        # The manifest as well as the pin: `lake update` can advance Mathlib
        # without touching `lean-toolchain`, and an olean built against the old
        # dependency would otherwise be reported as current.
        for name in ("lean-toolchain", "lake-manifest.json"):
            source = lean_project / name
            if not source.is_file():
                continue
            try:
                parts.append(hashlib.sha256(source.read_bytes()).hexdigest())
            except OSError:
                # An unreadable file is not a reason to refuse to work; it only
                # makes this identity coarser, and rebuilding is the safe way
                # for it to be wrong.
                parts.append(f"{name}:unreadable")
    return "\0".join(parts)


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
        # The Lean tree and the writeup tree. Both are directories now: a
        # development outgrows one file, and so does the document about it.
        self.tex_root = workspace / TEX_DIR
        self._lean_command = lean_command
        self._lean_project = lean_project
        # Resolved lazily and once: it costs a subprocess, and a session that
        # never builds Lean should never pay for it.
        self._search_path: tuple[Path, ...] | None = None
        # Kept on the session as well as handed to the workspace: it invalidates
        # the olean cache there, and stamps each audit verdict here. A verdict
        # describes what Lean reported under one toolchain and project, and
        # reopening a workspace against another does not make it false so much
        # as no longer about anything the session can see.
        self._environment = _toolchain_identity(lean_command, lean_project)
        self.lean_workspace = LeanWorkspace(
            workspace / LEAN_DIR,
            workspace / BUILD_DIR,
            self._compile_module,
            environment=self._environment,
            external=self._external_stamp,
        )
        self._migrate_layout()
        self._make_runtime = make_runtime
        # The SDK may call several tools at once, each on its own thread, but
        # these run Lean, rewrite session.json, and stop to ask a human for
        # approval. None of that is safe to interleave.
        self._gate = threading.Lock()
        # Set for the rest of a turn once it is cancelled, and cleared when the
        # next one starts. Read by `_dispatch` on the SDK's own tool threads,
        # which is why it is an Event rather than a bare bool.
        self._cancelled = threading.Event()
        # Whether the exchange in flight has had a `result` out of the provider
        # yet. An Event for the same reason `_cancelled` is one: it is set on
        # the runtime's thread and read on whichever thread drained the turn.
        self._reported = threading.Event()
        # Held across reading `_reported` and folding, so the runtime's worker
        # and the thread that drained the turn make one decision about who
        # records the exchange rather than two guesses.
        self._spend = threading.Lock()
        # `session.json` is written from more than one thread -- a tool call
        # under the gate above, and `_observed` remembering the provider thread
        # on the runtime's own thread -- and `_atomic_json` replaces a temporary
        # file at a fixed path. Two writers at once would interleave into it.
        self._writes = threading.Lock()
        # State first: the runtime is built from the system prompt, which embeds
        # the manifest, and it resumes the provider thread the state remembers.
        self.state = self._read_state()
        # What the workspace has already spent, so reopening it continues the
        # total rather than restarting it. Read before the first turn can add
        # to it.
        self.usage = self._recover_spend()
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
        offset = self._record(event)
        if event.get("type") != "result":
            return
        self._remember_thread()
        if not self._from_the_turn_in_flight():
            # A report the consumer already gave up waiting for. It is kept in
            # the transcript -- it happened -- but folding it would corrupt the
            # ledger rather than improve it: every figure in it is
            # session-to-date, and a *later* turn has since reported a larger
            # one, so this smaller figure is not new spend but an older view of
            # spend already counted. Differencing against it would read as a
            # counter restart and add the whole thing a second time. The
            # exchange it belongs to is in the ledger already, recorded as
            # unreported by its own teardown.
            #
            # The cursor still advances past it. Skipping is a decision, and a
            # replay after a crash must make the same one rather than folding
            # what this deliberately did not.
            self._skip_spend(offset)
            return
        self._reported.set()
        self._remember_spend(event, offset)

    def _from_the_turn_in_flight(self) -> bool:
        """Whether this report belongs to the turn the session is running now.

        Observation happens on the runtime's worker thread, and the runtime
        publishes the worker owning the current turn -- assigned before that
        thread starts, so a live worker either is that one or has been
        superseded. A runtime with no worker at all (the plain path, and the
        fakes) reports from the calling thread and cannot be stale.
        """
        worker = getattr(self.runtime, "worker", None)
        return worker is None or threading.current_thread() is worker

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
        for event in self._recorded():
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
        # `audit` is absent until the first save; a workspace written before the
        # audit existed has none either, and neither may read as a clean one.
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

    def _without(self, *keys: str) -> dict[str, Any]:
        """The manifest, minus the entries this reader has no business seeing."""
        return {key: value for key, value in self.state.items() if key not in keys}

    def _context(self) -> str:
        # The stored audit verdicts stay here, as they always have -- the system
        # prompt has no second, checked copy of them to contradict. Only the
        # spend ledger is withheld; `WITHHELD` says why.
        manifest = json.dumps(self._without(*LEDGER_KEYS), ensure_ascii=False)
        return f"\n\nWorkspace: {self.workspace}\nExisting manifest:\n{manifest}"

    def _record(self, event: dict[str, Any]) -> int:
        """Append one event, and say where the transcript now ends.

        The offset is what lets the ledger's cursor advance to the end of the
        event it just accounted for rather than to wherever the file happens
        to have reached -- two turns' reports can be in flight at once, and
        the file's current size may already include one nobody has folded.
        """
        event = {"timestamp": time.time(), **event}
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return handle.tell()

    def _final_gates(self, source: str) -> ToolResult | None:
        """What disqualifies a source from being saved, before Lean is asked.

        All of it is textual, so it costs nothing and runs first: there is no
        point spending a minute elaborating a file that a `sorry` or an
        unapproved axiom already rules out.
        """
        if self.lean.has_holes(source):
            return ToolResult(False, "saved Lean artifacts may not contain sorry or admit", source)
        found = declarations(source)
        # A `theorem` is what this workspace reports as a result, and a private
        # one can be neither audited nor cited: Lean mangles the name out of
        # reach of any other module, including the file the audit elaborates.
        # Refused rather than skipped, or a documented result would sit behind
        # a gate that never ran on it. `private lemma` stays free.
        hidden = [name for name in found["theorem"] if name in found["private"]]
        if hidden:
            return ToolResult(
                False,
                f"a private theorem cannot be audited or written up, because no other module can "
                f"name it: {hidden}. Drop `private`, or state it as a `private lemma` if it is "
                "scaffolding rather than a result.",
                source,
            )
        approved = {item["formal_name"]: " ".join(item["lean_statement"].split()) for item in self.state["assumptions"]}
        # Qualified by the namespace they sit in, so this gate and the audit
        # ask about the same name. A flat scan called it `bar` while Lean
        # reported `Foo.bar`, and no single approval could satisfy both.
        for name, statement in assumptions(source):
            if approved.get(name) != " ".join(statement.split()):
                return ToolResult(False, f"unapproved or altered assumption `{name}`; use request_assumption first", source)
        # An axiom the scan could not read is refused rather than skipped. It
        # cannot be compared against an approval -- the type Lean gives
        # `axiom Sneaky (P : Prop) : P` is `∀ P : Prop, P`, which is not the
        # text after the colon -- and skipping it let one pass unremarked.
        # `request_assumption` produces neither binders nor universe
        # parameters, so this refuses only shapes the approval flow cannot
        # reach.
        unreadable = unreadable_assumptions(source)
        if unreadable:
            return ToolResult(False, f"could not read `{unreadable[0]}` as `axiom NAME : STATEMENT`; an assumption must be approved by request_assumption and then declared in exactly that shape, without binders or universe parameters", source)
        return None

    def _run_lean_source(self, source: str) -> ToolResult:
        return self.lean.run_source(source, env={"LEAN_PATH": self.lean_workspace.lean_path()})

    def _compile_module(
        self, module: str, source_root: Path, build_root: Path, source_file: Path
    ) -> tuple[bool, str]:
        """Build one workspace module, for `LeanWorkspace` to sequence."""
        result = self.lean.compile_module(source_root, build_root, source_file)
        return result.ok, result.output

    def _lean_search_path(self) -> tuple[Path, ...]:
        """Where Lean looks for the modules a workspace file imports.

        Asked of Lake rather than assumed, because the answer includes Mathlib,
        the toolchain, and any local library the configured project provides,
        and only Lake knows where those are. Resolved once per session: it is a
        subprocess, and the answer does not move while a session runs.
        """
        if self._search_path is not None:
            return self._search_path
        found: list[Path] = []
        stopped = False
        command = self._lean_command
        # Only Lake can be asked this. Any other command -- a bare `lean`, or a
        # stand-in under test -- would be handed arguments it does not
        # understand, so the inherited variable is used instead.
        if len(command) >= 2 and Path(command[0]).stem == "lake" and command[1] == "env":
            try:
                # Asked through the interpreter already running rather than
                # `printenv`, which is a Unix coreutil and absent on native
                # Windows -- a documented way to run Hardy. There, the probe
                # failed and every external import fell back to the inherited
                # variable, which lacks Lake's computed package paths: each one
                # stamped `missing`, so a rebuilt dependency left the signature
                # unchanged and a stale verdict read as current.
                # Through the same ladder as every other child. It is a
                # probe, but it is `lake` -- it can stall on a lock or a
                # network fetch, and an unguarded stall is a sixty-second wait
                # that Esc cannot touch.
                probe = process.run_guarded(
                    [
                        command[0], "env", sys.executable, "-c",
                        "import os, sys; sys.stdout.write(os.environ.get('LEAN_PATH', ''))",
                    ],
                    cwd=self._lean_project or Path.cwd(),
                    timeout=60,
                )
                out = probe.stdout
                stopped = probe.interrupted or probe.timed_out
                # A probe that was stopped has no answer: reading its partial
                # output would silently narrow `LEAN_PATH` and stamp every
                # external import `missing`.
                if probe.returncode == 0:
                    found = [Path(part) for part in out.strip().split(os.pathsep) if part]
            except (OSError, subprocess.SubprocessError):
                found = []
        if not found:
            found = [Path(part) for part in os.environ.get("LEAN_PATH", "").split(os.pathsep) if part]
        if stopped:
            # Answered by nobody: the probe was interrupted, so `found` is the
            # inherited `LEAN_PATH` fallback rather than Lake's computed one.
            # Caching that would be durable damage rather than a slow turn --
            # a configured project's package paths would be missing, every
            # external import would stamp `missing`, and once those signatures
            # are committed a rebuilt dependency no longer invalidates the
            # build. Returned for this call and not remembered, so the next
            # turn asks Lake again.
            return tuple(found)
        self._search_path = tuple(found)
        return self._search_path

    def _external_stamp(self, module: str) -> str:
        """What the olean behind an import outside the workspace currently is.

        Mixed into the build signature so a workspace file is rebuilt when a
        module it imports from the configured Lake project is edited and
        rebuilt. Without it Hardy would reuse an olean compiled against source
        that has since changed and report the result as current -- and pointing
        `lean_project` at your own project is a documented way to work.

        Size and modification time rather than contents: Mathlib's oleans are
        large, this runs per module per save, and either changing already means
        it is not the artifact the cache was built against.

        Restatted every time rather than memoised for the session. Remembering
        it made a rebuild of the configured Lake project invisible until Hardy
        restarted -- the build cache went on reusing an olean compiled against
        the old dependency, and the audit went on being reported as current. The
        expensive part is finding the file, and the search path is still cached;
        what is not cached is what the file currently *is*.
        """
        relative = PurePosixPath(*module.split(".")).with_suffix(".olean")
        stamp = "missing"
        for directory in self._lean_search_path():
            candidate = directory / relative
            try:
                if candidate.is_file():
                    found = candidate.stat()
                    stamp = f"{candidate}:{found.st_size}:{found.st_mtime_ns}"
                    break
            except OSError:
                continue
        return stamp

    def _migrate_layout(self) -> None:
        """Move a workspace written before the trees existed into them.

        Without this, reopening a workspace would show an empty tree while
        `Main.lean` sat beside it, and the model would start again from nothing
        on top of work it could no longer see.
        """
        moves = ((DEFAULT_LEAN_PATH, self.lean_workspace.root), (DEFAULT_TEX_PATH, self.tex_root))
        moved = []
        for filename, destination in moves:
            legacy = self.workspace / filename
            if not legacy.is_file() or (destination / filename).exists():
                continue
            destination.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, destination / filename)
            moved.append(filename)
        if moved:
            self._record({"type": "migration", "reason": "layout", "moved": moved})

    def _check_lean(self, path: str, source: str) -> ToolResult:
        try:
            safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        failure = self._build_imports(self.lean_workspace, source)
        if isinstance(failure, ToolResult):
            return failure
        if failure is not None:
            return ToolResult(False, f"a workspace file this one imports does not build: {failure.module}\n{failure.output}", source)
        return self._run_lean_source(source)

    def _save_lean(self, path: str, source: str) -> ToolResult:
        try:
            relative = safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        gate = self._documentation_gate(source)
        if gate is not None:
            return ToolResult(False, gate, source)
        refusal = self._final_gates(source)
        if refusal is not None:
            return refusal
        text = source.rstrip() + "\n"
        # The shadow build elaborates this file itself, so there is no
        # pre-check run: with Mathlib imported each elaboration costs tens of
        # seconds, and checking the same source twice per save doubled the
        # expensive half of the operation. What Lean said is captured here
        # because the build reports only which module failed.
        seen: dict[str, ToolResult] = {}

        def capturing(module: str, source_root: Path, build_root: Path, source_file: Path) -> tuple[bool, str]:
            result = self.lean.compile_module(source_root, build_root, source_file)
            seen[module] = result
            return result.ok, result.output

        shadow, commit = self.lean_workspace.stage(relative, text, capturing)
        try:
            module = module_name(relative)
            try:
                affected = [module, *sorted(dependents(shadow.sources(), module))]
                failure = shadow.build_modules(affected)
            except ImportCycle as error:
                return ToolResult(False, f"{error}; nothing was written", source)
            if failure is not None:
                return ToolResult(False, f"this save breaks {failure.module}, so nothing was written:\n{failure.output}", source)
            # The registry and the Lean must stay in step. This was a per-file
            # check when the workspace was one file; a registered name now has
            # to survive somewhere in the tree, not in whichever file is being
            # saved -- but it must not be allowed to vanish from all of them.
            lost = self._missing_registered_names(shadow.sources())
            if lost:
                return ToolResult(False, f"this save would drop registered names from the workspace: {lost}", source)
            # Last, because it is the only gate that costs another Lean run,
            # and still before `commit`: a refused audit must leave the
            # workspace exactly as it was.
            audited = self._audit_tree(shadow, affected)
            if isinstance(audited, ToolResult):
                return audited
            records, note = audited
            commit()
        finally:
            LeanWorkspace.discard(shadow)
        # Published after the write, and not before: a verdict stored first
        # would survive a failed commit and describe a tree that never existed.
        # Stamped with what the module's build inputs hashed to, not merely with
        # the toolchain: the same signature the build cache is keyed on, which
        # already folds in the environment, the module's source, everything it
        # imports inside the workspace, and the olean behind every import
        # outside it. A verdict is an answer about those inputs and expires with
        # them.
        signatures = self.lean_workspace.current_signatures()
        self.state.setdefault("audit", {}).update(
            {
                module: {**record, "signature": signatures.get(module, "")}
                for module, record in records.items()
            }
        )
        self._save_state()
        # Absent from `seen` when the source was byte-identical to what was
        # already built, so the cache skipped it. Nothing was wrong with it.
        result = seen.get(module, ToolResult(True, "unchanged; already built", source))
        return ToolResult(
            result.ok,
            f"{result.output}\n\naxiom audit: {note}{self._owed_note()}",
            result.source,
        )

    def _owed_note(self) -> str:
        """The outstanding obligations, appended to a tool result.

        On every save, not only on the one that trips the ratchet. A model that
        is told what the work still owes while it is saving can settle it now;
        one told only when it is refused learns it a theorem too late.
        """
        owed = self._obligations()
        if not owed:
            return ""
        return (
            "\n\nNot reportable yet. This workspace still owes:\n"
            f"{completion.describe(owed)}"
        )

    def _approved_assumptions(self) -> set[str]:
        return {item["formal_name"] for item in self.state["assumptions"]}

    def _audit_tree(
        self, space: LeanWorkspace, modules: Sequence[str]
    ) -> ToolResult | tuple[dict[str, dict[str, Any]], str]:
        """What the built modules actually rest on: a record each, or a refusal.

        The textual gate in `_final_gates` sees an `axiom` written into the
        source in front of it and nothing else. An axiom reached through an
        import is invisible to it, and that is the case a saved artifact can
        be wrong about while looking right, so Lean is asked directly.

        Asked over the staged tree, before anything is committed, and over
        every module the save rebuilt rather than only the one it edited: a
        dependent inherits whatever the edit brought in, so its own claim
        changed too even though its source did not. Which is also why a module
        outside that set keeps its earlier record rather than being dropped --
        nothing it depends on moved.
        """
        sources = space.sources()
        # `declarations` strips comments and rescans the whole file, so it is
        # asked once per module rather than once per kind.
        found_in = {module: declarations(sources[module]) for module in modules}
        # Private declarations are left out because Lean will not let this probe
        # name one: it elaborates a file that *imports* the module, and a private
        # name is mangled out of reach from there. Asking anyway is an unknown
        # identifier, which would refuse every save of a file using the ordinary
        # `private lemma` idiom. Nothing is lost -- an exported declaration that
        # uses a private helper reports the helper's axioms as its own.
        declared = {
            module: tuple(
                name
                for name in found["theorem"] + found["lemma"]
                if name not in found["private"]
            )
            for module, found in found_in.items()
        }
        names = list(dict.fromkeys(name for module in modules for name in declared[module]))
        empty = {
            module: audit.unestablished(f"no theorem or lemma is declared in {module}")
            for module in modules
            if not declared[module]
        }
        if not names:
            # Nothing here claims to be a result, so there is nothing to grade.
            # Recorded as an audit that did not run rather than as a clean one.
            return empty, f"not established -- no theorem or lemma is declared in {list(modules)}"
        # Modules that never import each other may each declare a root-level
        # `step`, or a `def helper`, or anything else at the same name, and both
        # build. One probe importing all of them brings those together, and Lean
        # will not resolve a name that now means two things -- so a save Lean
        # accepts would be refused.
        #
        # Which names collide is not knowable from the audit targets: the clash
        # can be in a `def`, a `structure`, an `instance`, anything a module
        # exports. Rather than enumerate the kinds and still miss one, the cheap
        # probe is tried first and a failure is retried per module. That is
        # correct for every collision without naming any of them, and costs the
        # extra elaborations only on the trees that need them. Nothing is
        # loosened: each retry still asks about every declaration and still
        # requires a clean report, so a tree that is genuinely broken refuses
        # either way.
        attempts: list[list[list[str]]] = [[list(modules)]]
        if len(modules) > 1:
            attempts.append([[module] for module in modules])
        for index, groups in enumerate(attempts):
            outcome = self._probe_groups(space, groups, declared)
            if not isinstance(outcome, ToolResult):
                reports, covering = outcome
                break
            if index == len(attempts) - 1:
                return outcome
        else:  # pragma: no cover - `attempts` is never empty
            return ToolResult(False, "the axiom audit had nothing to run")
        approved = self._approved_assumptions()
        verdict = audit.classify(reports, approved)
        if verdict.forbidden:
            # Before anything else, and never offered for approval: a hole is
            # not an assumption and no human can make it one.
            return ToolResult(
                False,
                f"the axiom audit refused this save: {audit.describe(verdict)}. "
                f"{list(audit.dependents(reports, verdict.forbidden[0]))} depend on a hole, which cannot be approved.",
            )
        if verdict.unapproved:
            needed = {
                axiom: list(audit.dependents(reports, axiom)) for axiom in verdict.unapproved
            }
            return ToolResult(
                False,
                f"the axiom audit refused this save: {audit.describe(verdict)}. "
                f"These assumptions reached through imports have not been approved: {needed}. "
                "Call request_assumption for each before saving work that rests on it.",
            )
        # A record per module rather than one for the save, so a later save
        # elsewhere in the tree cannot overwrite what this one established.
        records = dict(empty)
        for module, reported in covering.items():
            records[module] = audit.classify(reported, approved).as_dict()
        return records, audit.describe(verdict)

    def _build_imports(self, space: LeanWorkspace, source: str) -> BuildFailure | ToolResult | None:
        """Make the workspace modules a candidate imports importable."""
        try:
            needed = internal_imports(source, space.sources())
            return space.build_modules(needed) if needed else None
        except ImportCycle as error:
            return ToolResult(False, str(error), source)

    def _resolves(self, formal_name: str, sources: dict[str, str]) -> bool:
        """Whether a registered name still names something in a tree.

        A qualified entry must find that exact declaration: with `A.result` and
        `B.result` both present, accepting `A.result` because *some* `result`
        exists would let the mapped declaration be deleted while the manifest
        went on pointing at it. A bare entry may match a qualified declaration,
        but only while exactly one carries that leaf.

        Declarations are matched by name rather than by text, because a
        `theorem one` inside `namespace Hardy` is `Hardy.one` and that string
        appears nowhere in the file. The textual fallback covers what
        `declarations` does not read -- an approved `axiom`, a `def`, a
        structure -- so a name backed by one of those is not reported as lost.
        """
        declared: set[str] = set()
        for source in sources.values():
            found = declarations(source)
            declared.update(found["theorem"])
            declared.update(found["lemma"])
        if formal_name in declared:
            return True
        if "." not in formal_name:
            sharing = [name for name in declared if name.rsplit(".", 1)[-1] == formal_name]
            if len(sharing) == 1:
                return True
        return any(
            re.search(rf"(?<![\w'.]){re.escape(formal_name)}(?![\w'])", source)
            for source in sources.values()
        )

    def _still_current(
        self, module: str, record: dict[str, Any], signatures: dict[str, str]
    ) -> dict[str, Any]:
        """An audit verdict as it stands against the tree in front of us.

        The axioms a declaration rests on are a fact about everything that went
        into building it: the toolchain and project, the module's own source,
        the workspace modules it imports, and the oleans behind the imports it
        takes from outside. Any of those can move without a save -- a file
        edited on disk, a local Lake project rebuilt, a different Lean -- and a
        stored verdict would otherwise sit in `session.json` and be handed to
        the model as the module's current audit until some later save happened
        to cover it again.

        So the check is the build signature, not the toolchain alone: it already
        folds in all of that, and it is recursive, so a change beneath a module
        expires the verdict above it too. A verdict written before verdicts
        carried a signature has none to match, and is treated the same way:
        unknown is not current. What it said is kept for reference rather than
        deleted -- it is the *status* that must not read as a pass.
        """
        if record.get("signature") and record["signature"] == signatures.get(module):
            return record
        return {
            **record,
            "status": "not established",
            "reason": "the module's Lean toolchain, source, or dependencies have changed since this was established; save it again",
            "stale": True,
        }

    def _probe_groups(
        self,
        space: LeanWorkspace,
        groups: list[list[str]],
        declared: dict[str, tuple[str, ...]],
    ) -> tuple[list[audit.AxiomReport], dict[str, list[audit.AxiomReport]]] | ToolResult:
        """Ask Lean what each group's declarations rest on.

        One elaboration per group. The modules are already oleans, so this
        imports rather than re-elaborates them.
        """
        reports: list[audit.AxiomReport] = []
        covering: dict[str, list[audit.AxiomReport]] = {}
        for group in groups:
            wanted = list(dict.fromkeys(name for module in group for name in declared[module]))
            if not wanted:
                continue
            probe = "".join(f"import {module}\n" for module in group)
            result = self.lean.run_source(
                probe,
                env={"LEAN_PATH": space.lean_path()},
                audit=tuple(f"axioms {name}" for name in wanted),
            )
            if not result.ok:
                return ToolResult(
                    False,
                    f"the axiom audit could not run over the saved tree, so nothing was written:\n{result.output}",
                )
            # The whole report, not the tail a model is shown: a tree with more
            # declarations than the observation window would otherwise be
            # refused for a report that was merely cut off.
            answered = audit.parse(result.report, tuple(wanted))
            if answered is None:
                return ToolResult(
                    False,
                    "the axiom audit could not be established for "
                    f"{wanted}, so nothing was written. Remove any #print axioms from your source; Hardy adds its own.",
                )
            reports.extend(answered)
            by_name = {report.declaration: report for report in answered}
            for module in group:
                if declared[module]:
                    covering[module] = [by_name[name] for name in declared[module]]
        return reports, covering

    def _missing_registered_names(self, sources: dict[str, str]) -> list[str]:
        """Registered formal names that survive nowhere in a tree.

        An approved assumption is exempt. This guard exists so a *workspace
        declaration* cannot vanish while the registry still points at it, and an
        axiom reached through an import was never a workspace declaration --
        `request_assumption` registers the name a human approved, nothing writes
        it into a file, and demanding one refused every later save with no tool
        to undo it. The exemption is deliberately narrow: a registered theorem
        that disappears is still caught.
        """
        approved = self._approved_assumptions()
        return [
            item["formal_name"]
            for item in self.state["names"]
            if item["formal_name"] not in approved
            and not self._resolves(item["formal_name"], sources)
        ]

    def _labels(self) -> set[str]:
        r"""Every label LaTeX actually created, from the last saved compile.

        Read from the compiler's own `.aux`, not from the source text. A
        `\label` can appear in a document without ever being created -- inside
        `\verb`, in a comment, in a branch that was not taken -- and counting
        those would release the ratchet for a theorem the document does not
        describe. The `.aux` is what LaTeX wrote down about what it did, which
        is the same reason Hardy believes Lean's kernel and not its own reading
        of a proof.

        Still derived rather than stored: the file is a build artifact of the
        last successful save, and if it is absent nothing is documented yet.
        """
        aux = self.workspace / BUILD_DIR_TEX / "writeup.aux"
        if not aux.is_file():
            return set()
        return set(NEWLABEL.findall(aux.read_text(encoding="utf-8", errors="replace")))

    def _saved_theorems(self) -> set[str]:
        found: set[str] = set()
        for source in self.lean_workspace.sources().values():
            found.update(declarations(source)["theorem"])
        return found

    def _saved_statements(self) -> dict[str, str]:
        """Every saved theorem, with the exact statement Lean was given.

        Theorems only. A `lemma` is scaffolding and owes nothing, which is the
        same line `_saved_theorems` draws and has to stay the same line: a
        writeup gate that demanded a paragraph for every helper would make
        splitting a proof into helpers the expensive way to work.
        """
        found: dict[str, str] = {}
        for source in self.lean_workspace.sources().values():
            theorems = set(declarations(source)["theorem"])
            found.update(
                {name: text for name, text in statements(source).items() if name in theorems}
            )
        return found

    def _shared_names(self) -> dict[str, list[str]]:
        """Theorem names more than one saved module declares.

        Lean permits it while nothing imports both, and the workspace does not
        make them import each other -- but everything downstream addresses a
        theorem *by name*: the registry, the label, the statement the document
        quotes. With two `result`s, one entry answers for both, and the second
        theorem passes the ratchet (its name already exists) while disappearing
        from the obligations entirely. So they are reported, and the model is
        asked to put one in a namespace.
        """
        holders: dict[str, list[str]] = {}
        for module, source in sorted(self.lean_workspace.sources().items()):
            for name in declarations(source)["theorem"]:
                holders.setdefault(name, []).append(module)
        return {name: found for name, found in holders.items() if len(found) > 1}

    def _tex_sources(self) -> dict[str, str]:
        """The writeup tree as text, keyed by workspace-relative path."""
        if not self.tex_root.is_dir():
            return {}
        found: dict[str, str] = {}
        for path in sorted(self.tex_root.rglob("*.tex")):
            try:
                found[path.relative_to(self.tex_root).as_posix()] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                # Unreadable is not empty, but a listing that raises here would
                # take the turn with it. The obligation it leaves standing is
                # the safe direction: the file cannot be shown to say anything.
                continue
        return found

    def _used_assumptions(self) -> set[str]:
        """Approved axioms the saved tree actually rests on.

        Both ways one can be reached: written into a workspace file, or
        inherited through an import and found by the audit. An approval nobody
        used is not an assumption this work depends on, and demanding an
        appendix entry for it would pad the appendix with disclaimers a reader
        has to rule out by hand.
        """
        used: set[str] = set()
        for source in self.lean_workspace.sources().values():
            used.update(name for name, _ in assumptions(source))
        for record in self.state.get("audit", {}).values():
            used.update(str(name) for name in record.get("assumed", ()))
        return used

    def _obligations(self) -> tuple[completion.Obligation, ...]:
        """What the workspace still owes, derived from the artifacts alone.

        Never stored. A flag saying the work was finished would outlive the
        file it described -- and the one thing this must not do is report a
        theorem as written up because it *was*, before the statement changed.
        """
        owed = completion.outstanding(
            theorems=self._saved_statements(),
            registry=self.state["names"],
            labels=self._labels(),
            assumptions=self.state["assumptions"],
            used=self._used_assumptions(),
            tex=self._tex_sources(),
        )
        # Ahead of the rest: while two modules answer to one name, every
        # obligation below is about whichever of them was read last.
        #
        # The audit gaps are here rather than only inside `report_result`
        # because all three surfaces have to agree. With them counted only at
        # report time, a workspace whose Lean was edited on disk refused the
        # report while `/status` and the end-of-turn notice said nothing was
        # outstanding -- so the claim the notice exists to contradict went
        # uncontradicted, and only a model that tried to report properly ever
        # found out.
        shared = [
            completion.Obligation(
                "lean",
                name,
                f"{modules} each declare a theorem called `{name}`, so one name cannot "
                "stand for both in the registry, the label, or the statement the writeup "
                "quotes. Put one of them in a namespace.",
            )
            for name, modules in sorted(self._shared_names().items())
        ]
        return (*shared, *self._audit_gaps(self._saved_theorems()), *self._stale_writeup(), *owed)

    def has_theorems(self) -> bool:
        """Whether anything here could be reported at all.

        No obligations means two different things -- everything is written up,
        or there is nothing to write up -- and a reader of `/status` must not
        be shown the first when the second is true.
        """
        return bool(self._saved_theorems())

    def obligations(self) -> tuple[completion.Obligation, ...]:
        """What the workspace owes, for the human rather than the model.

        `/status` asks this. It is the same answer `report_result` is refused
        by and the same one drawn at the end of a turn, deliberately: a user
        who suspects they are being told a result exists must be able to ask
        something other than the model.
        """
        return self._obligations()

    def _undocumented(self) -> tuple[str, ...]:
        """Saved theorems the writeup does not yet carry.

        A theorem is carried when the registry names it, the compiler really
        created that label, and the document quotes the statement Lean was
        given. The third is the one a reader needs: a paper that describes a
        theorem in prose alone cannot be checked against the Lean, and being
        checkable is the only reason the document exists.

        Derived from the artifacts every time it is asked for -- see
        `_obligations`, which is where the rules live.
        """
        return tuple(
            sorted(
                {
                    item.subject
                    for item in self._obligations()
                    if item.subject and item.kind in {"record", "label", "statement"}
                }
            )
        )

    def _documentation_gate(self, source: str) -> str | None:
        """The catch-up ratchet: write up the last theorem before the next.

        Refuses only when the tree already owes a writeup *and* this save would
        add a theorem it does not already contain. The first condition alone
        would trap the session: a model could no longer repair, restate, or
        delete the very theorem blocking it. The second alone would let one
        file absorb any number of undocumented claims.
        """
        owed = self._obligations()
        if not owed:
            return None
        existing = self._saved_theorems()
        introduced = [name for name in declarations(source)["theorem"] if name not in existing]
        if not introduced:
            return None
        return (
            f"the workspace owes the human-readable half of its work before a new theorem "
            f"({introduced[0]}) is added:\n"
            f"{completion.describe(owed)}\n"
            "Settle these with record_name and save_latex. A lemma carries no such "
            "requirement, so state scaffolding as a lemma."
        )

    def _check_latex(self, path: str, source: str) -> ToolResult:
        resolved = self._tex_path(path)
        if isinstance(resolved, ToolResult):
            return resolved
        relative, _ = resolved
        return self.latex.check(source, path=relative, tree=self.tex_root)

    def _save_latex(self, path: str, source: str) -> ToolResult:
        resolved = self._tex_path(path)
        if isinstance(resolved, ToolResult):
            return resolved
        # The normalised path, not the argument: `sections\one.tex` names one
        # file to `_tex_target` and, on a platform where a backslash is an
        # ordinary character, a different one to the compiler -- so the root
        # would be checked against the old fragment and then overwritten by a
        # candidate nothing had compiled.
        relative, target = resolved
        result = self.latex.check(
            source,
            path=relative,
            tree=self.tex_root,
            output_dir=self.workspace,
            aux_dir=self.workspace / BUILD_DIR_TEX,
        )
        if not result.ok:
            return result
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.rstrip() + "\n", encoding="utf-8")
        # Stamped after the write and only on a compile that succeeded: this is
        # the record that the tree on disk is the tree those labels came from.
        self.state["tex_signature"] = self._tex_signature()
        self._save_state()
        # Advisory rather than a refusal. With the save_lean ratchet in place a
        # hard gate here would deadlock: Lean blocked for want of a writeup,
        # and the writeup blocked for not yet covering everything registered.
        # Which is also why the writeup tree is the one place a save is never
        # refused for what it does not yet contain -- it is where every
        # obligation is settled.
        #
        # Two notes, not one. The obligations are about the *work*: what a
        # saved theorem still owes before anyone may report it. This one is
        # about the *registry*: a name recorded for something the document has
        # not labelled yet, which is a promise made and not yet kept even when
        # no theorem is waiting on it.
        missing = [item["latex_name"] for item in self.state["names"] if item["latex_name"] not in self._labels()]
        note = self._owed_note()
        if missing:
            note = f"\n\nStill missing labels for registered names: {missing}{note}"
        if note:
            return ToolResult(True, f"{result.output}\n\nSaved.{note}", source)
        return result

    def _tex_path(self, path: str) -> tuple[str, Path] | ToolResult:
        """The workspace-relative writeup path, and where it lives on disk."""
        cleaned = str(path).replace("\\", "/")
        if not cleaned.endswith(".tex"):
            return ToolResult(False, f"not a workspace LaTeX path: {path!r}")
        candidate = PurePosixPath(cleaned)
        # A colon is refused because `PurePosixPath("C:/out.tex").is_absolute()`
        # is False, while joining that to a Windows root discards the root and
        # yields `C:\out.tex` -- an escape that would let a tool read, overwrite,
        # or delete a file anywhere on the machine.
        if (
            candidate.is_absolute()
            or any(part in {"..", "."} for part in candidate.parts)
            or any(":" in part for part in candidate.parts)
        ):
            return ToolResult(False, f"path escapes the workspace: {path!r}")
        return str(candidate), self.tex_root / candidate

    def _tex_target(self, path: str) -> Path | ToolResult:
        resolved = self._tex_path(path)
        return resolved if isinstance(resolved, ToolResult) else resolved[1]

    def _workspace_listing(self) -> dict[str, Any]:
        """What is in the workspace, without its full contents.

        `read_file` fetches a body. Returning every file's text here was fine
        when there were two of them and would flood the context now.
        """
        # Read once. `sources()` walks and reads the whole tree, so calling it
        # per module made listing quadratic in the number of files.
        sources = self.lean_workspace.sources()
        lean = []
        for module, source in sorted(sources.items()):
            found = declarations(source)
            lean.append({
                "path": str(module_path(module)),
                "module": module,
                "imports": list(internal_imports(source, sources)),
                "theorems": list(found["theorem"]),
                "lemmas": list(found["lemma"]),
            })
        tex = (
            sorted(path.relative_to(self.tex_root).as_posix() for path in self.tex_root.rglob("*.tex"))
            if self.tex_root.is_dir()
            else []
        )
        # Hashed once for the whole listing rather than per module: each call
        # re-reads every source in the tree.
        try:
            current = self.lean_workspace.current_signatures()
        except ImportCycle:
            # Files edited directly on disk can form a cycle, and this listing is
            # how the model finds out and repairs it -- so it must not be the
            # thing that fails. No signatures means nothing matches, which marks
            # every verdict unestablished: correct for a tree that cannot be
            # ordered, let alone built.
            current = {}
        return {
            # Without the stored verdicts. They are reported below, checked
            # against the tree in front of us; handing back the raw ones as well
            # would put a `clean` and a `not established` for the same module in
            # one response, and a reader could believe either. See `WITHHELD`
            # for why the spend ledger is left out too.
            "manifest": self._without(*WITHHELD),
            "lean": lean,
            "tex": tex,
            "undocumented_theorems": list(self._undocumented()),
            # Everything standing between this workspace and a report anyone
            # may believe, in the same words the refusal would use.
            "obligations": [item.as_dict() for item in self._obligations()],
            # What each saved module was found to rest on, so the model can
            # report it rather than having to remember it. A module is absent
            # until a save covers it, and a verdict from another environment is
            # reported as no longer established rather than as current.
            "audit": {
                module: self._still_current(module, record, current)
                for module, record in self.state.get("audit", {}).items()
            },
        }

    def _read_file(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, _ = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        return ToolResult(True, target.read_text(encoding="utf-8"))

    def _delete_file(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, kind = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        if kind == "tex":
            return self._delete_tex(target, path)
        relative = safe_relative(str(path).replace("\\", "/"))
        module = module_name(relative)
        importers = dependents(self.lean_workspace.sources(), module)
        if importers:
            return ToolResult(False, f"{module} is imported by {sorted(importers)}; change those first")
        shadow, commit = self.lean_workspace.stage(relative, None)
        try:
            # Names the deletion strands go with it, rather than the deletion
            # being refused. Refusing was worse than the problem it solved: a
            # theorem registered but not yet written up could never be
            # abandoned, since no tool removes a mapping, and every later save
            # was then refused for dropping a name already gone. The contract
            # is that an undocumented theorem can always be walked away from.
            lost = self._missing_registered_names(shadow.sources())
            commit()
            # The audit record goes with the module. Left behind it would
            # describe declarations the workspace no longer has.
            if self.state.get("audit", {}).pop(module, None) is not None:
                self._save_state()
            if lost:
                self.state["names"] = [
                    item for item in self.state["names"] if item["formal_name"] not in lost
                ]
                self._save_state()
                # Written down: dropping a formal-to-writeup mapping is a change
                # to the record of what was claimed, not a bookkeeping detail.
                self._record({"type": "registry", "reason": "declaration_deleted", "path": path, "dropped": lost})
        finally:
            LeanWorkspace.discard(shadow)
        if lost:
            return ToolResult(True, f"deleted {path}; also dropped now-unbacked registry names: {lost}")
        return ToolResult(True, f"deleted {path}")

    def _delete_tex(self, target: Path, path: str) -> ToolResult:
        """Remove a writeup file, unless the document stops compiling without it.

        A fragment pulled in with `\\input` cannot simply be dropped: the root
        would no longer compile while the last `writeup.pdf` sat beside it,
        still describing content the workspace no longer has.
        """
        if target.resolve() == (self.tex_root / ROOT_DOCUMENT).resolve():
            return ToolResult(False, f"{ROOT_DOCUMENT} is the root document and cannot be deleted")
        kept = target.read_text(encoding="utf-8")
        target.unlink()
        root = self.tex_root / ROOT_DOCUMENT
        if root.is_file():
            checked = self.latex.check(
                root.read_text(encoding="utf-8"),
                tree=self.tex_root,
                output_dir=self.workspace,
                aux_dir=self.workspace / BUILD_DIR_TEX,
            )
            if not checked.ok:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(kept, encoding="utf-8")
                return ToolResult(False, f"the writeup no longer compiles without {path}, so it was kept:\n{checked.output}")
        return ToolResult(True, f"deleted {path}")

    def _resolve(self, path: str) -> tuple[Path, str] | ToolResult:
        """Where a tool path lives: the Lean tree or the writeup tree."""
        cleaned = str(path).replace("\\", "/")
        if cleaned.endswith(".lean"):
            try:
                return self.lean_workspace.root / safe_relative(cleaned), "lean"
            except WorkspacePathError as error:
                return ToolResult(False, str(error))
        if cleaned.endswith(".tex"):
            target = self._tex_target(cleaned)
            return target if isinstance(target, ToolResult) else (target, "tex")
        return ToolResult(False, f"not a workspace file: {path!r}")

    def _tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name == "check_lean":
            return self._check_lean(str(arguments.get("path") or DEFAULT_LEAN_PATH), str(arguments["source"]))
        if name == "save_lean":
            return self._save_lean(str(arguments.get("path") or DEFAULT_LEAN_PATH), str(arguments["source"]))
        if name == "check_latex":
            return self._check_latex(str(arguments.get("path") or DEFAULT_TEX_PATH), str(arguments["source"]))
        if name == "save_latex":
            return self._save_latex(str(arguments.get("path") or DEFAULT_TEX_PATH), str(arguments["source"]))
        if name in CAS_TOOL_NAMES:
            return self._cas_tool(name, arguments)
        if name == "read_workspace":
            return ToolResult(True, json.dumps(self._workspace_listing(), ensure_ascii=False))
        if name == "read_file":
            return self._read_file(str(arguments["path"]))
        if name == "delete_file":
            return self._delete_file(str(arguments["path"]))
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
            return ToolResult(
                True,
                f"User approved. Declare exactly `{declaration}`, disclose source "
                f"`{proposal['source']}`, and state it in the writeup's \\appendix -- both "
                f"that Lean line, verbatim, and \\label{{{proposal['latex_name']}}} on the "
                "prose statement of what was assumed. Nothing resting on it can be "
                "reported until the appendix carries both.",
            )
        if name == "report_result":
            claimed = arguments.get("theorems")
            return self._report_result(
                [str(item) for item in claimed] if isinstance(claimed, list) else [],
                str(arguments.get("summary") or ""),
            )
        return ToolResult(False, f"unknown tool: {name}")

    def _report_result(self, claimed: list[str], summary: str) -> ToolResult:
        """Say the work is done, and be refused until the artifacts say it too.

        The gap this closes is the whole reason it exists: every other gate
        here guards an artifact, and a model that never writes one walks past
        all of them. It can prove a theorem in the conversation, describe it
        beautifully, and finish a session having saved nothing -- or save Lean
        and leave the reader a document that never quotes it. So the claim
        itself is a tool call, and it is checked against the same two trees
        everything else here is checked against.

        What is checked is mechanical and stays mechanical: the theorem is
        saved and was audited when it was saved, the compiler really made its
        label, the document really quotes its statement, and every assumption
        the work rests on is in the appendix in both languages. Nothing here
        reads a proof or judges prose -- a report is not a verdict on the
        mathematics, only on whether both halves of the work exist.
        """
        if not summary.strip():
            return ToolResult(False, "a report needs a summary of what was established")
        saved = self._saved_statements()
        if not saved:
            return ToolResult(
                False,
                "this workspace holds no saved theorem, so nothing here is reportable. A "
                "result is reportable only as Lean the kernel checked, written up where a "
                "human can read it: prose alone is not a result, and a lemma is scaffolding "
                "that is not reportable either. State as a theorem what you would report.",
            )
        if not claimed:
            return ToolResult(
                False,
                f"name the theorems this report claims. Saved theorems: {sorted(saved)}",
            )
        resolved: dict[str, str] = {}
        unknown: list[str] = []
        for name in claimed:
            found = [
                candidate
                for candidate in saved
                if candidate == name or candidate.rsplit(".", 1)[-1] == name
            ]
            if len(found) == 1:
                resolved[found[0]] = name
            else:
                unknown.append(name)
        if unknown:
            return ToolResult(
                False,
                f"{unknown} does not name exactly one saved theorem, so it cannot be "
                f"reported. A lemma is not reportable either. Saved theorems: {sorted(saved)}",
            )
        owed = self._obligations()
        # Everything about a claimed theorem, every assumption obligation
        # whoever it belongs to -- an appendix that does not say what the work
        # rests on makes *this* report unbelievable, not somebody else's -- and
        # everything with no subject at all, which is what an obligation about
        # the document itself looks like.
        blocking = [
            item
            for item in owed
            if not item.subject
            or item.subject in resolved
            or item.kind in {"appendix", "assumption"}
        ]
        if blocking:
            return ToolResult(
                False,
                "this report is refused: the artifacts do not yet back it.\n"
                f"{completion.describe(blocking)}\n"
                "Settle every line above with save_lean, record_name and save_latex, then "
                "report again. Do not tell the user this is finished in the meantime.",
            )
        used = self._used_assumptions()
        rested = [
            item for item in self.state["assumptions"] if str(item["formal_name"]) in used
        ]
        entry = {
            "theorems": sorted(resolved),
            "summary": summary.strip(),
            "statements": {name: saved[name] for name in sorted(resolved)},
            "assumptions": [str(item["formal_name"]) for item in rested],
        }
        self.state.setdefault("reports", []).append(entry)
        self._save_state()
        self._record({"type": "report", **entry})
        elsewhere = [item for item in owed if item not in blocking]
        return ToolResult(
            True,
            f"Reported {entry['theorems']}. Each is saved Lean whose axioms were audited "
            "when it was saved, carries a label the compiler created, and has its exact "
            "statement quoted in the writeup where the reader can check it"
            + (
                f", modulo the assumptions the appendix states: {entry['assumptions']}."
                if entry["assumptions"]
                else ", resting on no assumption beyond Lean's own."
            )
            + (
                f"\nStill outstanding elsewhere in the workspace:\n{completion.describe(elsewhere)}"
                if elsewhere
                else ""
            ),
        )

    def _tex_signature(self) -> str:
        """What the writeup tree hashes to, as a whole."""
        digest = hashlib.sha256()
        for path, source in sorted(self._tex_sources().items()):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(source.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _stale_writeup(self) -> list[completion.Obligation]:
        """Whether the labels on hand describe the documents on hand.

        `_labels` reads the `.aux` the last successful compile wrote, and
        everything else reads the `.tex` files as they are now. A file edited
        on disk between the two answers a statement obligation with text
        nobody compiled, while still counting the labels of the document that
        was -- and can just as easily have made the document uncompilable.
        The Lean side expires a verdict by build signature for exactly this
        reason; this is that, for the half a reader actually holds.
        """
        stamped = self.state.get("tex_signature")
        if not self._tex_sources() or stamped == self._tex_signature():
            return []
        return [
            completion.Obligation(
                "label",
                "",
                "the writeup on disk is not the one that was compiled, so its labels and "
                "listings are not established. Run save_latex again."
                if stamped
                else "no compile of this writeup tree is on record, so its labels and "
                "listings are not established. Run save_latex again.",
            )
        ]

    def _audit_gaps(self, names: Iterable[str]) -> list[completion.Obligation]:
        """Claimed theorems with no current audit behind them.

        A save audits what it wrote and stamps the verdict with the build
        signature it was established under; `_still_current` expires it when
        anything beneath the module moves. Everything else here reads the
        *source* tree, which a file edited on disk, a rebuilt Lake project, or
        a workspace reopened from before the audit existed will happily satisfy
        -- so a report could carry a theorem nobody had asked Lean about. The
        strongest claim Hardy makes is the one place that must not be inferred
        from text alone.
        """
        try:
            signatures = self.lean_workspace.current_signatures()
        except ImportCycle as error:
            return [completion.Obligation("lean", "", f"the workspace does not order: {error}")]
        stored = self.state.get("audit", {})
        sources = self.lean_workspace.sources()
        gaps: list[completion.Obligation] = []
        for name in sorted(names):
            covering = [
                module
                for module, source in sources.items()
                if name in declarations(source)["theorem"]
            ]
            established = False
            reasons: list[str] = []
            for module in covering:
                record = stored.get(module)
                if record is None:
                    reasons.append(f"{module} has never been audited")
                    continue
                current = self._still_current(module, record, signatures)
                if current.get("stale"):
                    reasons.append(f"{module}'s audit is no longer established")
                elif not any(
                    entry.get("name") == name
                    for entry in current.get("declarations", ())
                ):
                    reasons.append(f"{module}'s audit does not cover {name}")
                else:
                    established = True
            if not established:
                gaps.append(
                    completion.Obligation(
                        "lean",
                        name,
                        "; ".join(reasons) or "nothing saved declares it",
                    )
                )
        return gaps

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
        # Same reasoning, and the same thread: what the last exchange reported
        # says nothing about whether this one will.
        with self._spend:
            self._reported.clear()
        # And the same for the children. A stop stays in force after `cancel`
        # so that a tool call already past the gate cannot spawn its child a
        # moment later and outlive the press that was spent on it -- which
        # means something has to lift it, or this turn's first child would be
        # killed on sight by the last turn's Esc.
        self.resume_work()
        return self._stream(self.runtime.stream(text))

    def _stream(self, events: Iterator[TurnEvent]) -> Iterator[TurnEvent]:
        # An explicit `yield`, not `yield from`. A consumer that unwinds --
        # Ctrl+C in `--plain`, most of all -- closes this generator, and with
        # `yield from` that teardown would reach the runtime first: it
        # interrupts the model and then waits on its worker, all while this
        # session's tool gate is still open and the provider can dispatch one
        # more call. Yielding here means the gate shuts before any of that.
        iterator = iter(events)
        tail: Iterator[TurnEvent] | None = None
        try:
            while True:
                if tail is None:
                    try:
                        event = next(iterator)
                    except StopIteration:
                        # The model has stopped talking; Hardy has not. What it
                        # says here is read off the artifacts, so a turn that
                        # ended "proved it" over a workspace with no Lean in it
                        # is contradicted in front of the user, in the same
                        # breath, every time.
                        tail = iter(self._closing_notice())
                        continue
                else:
                    try:
                        event = next(tail)
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
            # And it belongs in the ledger. A transport failure, or Hardy's own
            # wall clock firing after the request went out, ends the exchange
            # with no `result` at all -- but the provider may well have billed
            # for what it did before that. Counted with everything about it
            # unreported, because the alternative is a session that burned
            # tokens and still says `Nothing spent yet.` Final: the runtime's
            # worker can outlive the wait `_consume` gives it, but a report
            # arriving after this is stale rather than late -- `_observed`
            # says why it cannot be folded in afterwards.
            self._remember_spend({}, self._transcript_end(), unreported=True)

    def _closing_notice(self) -> list[TurnEvent]:
        """What the workspace owes, said by Hardy rather than by the model.

        Not part of the conversation and not something the model can suppress,
        shorten, or reword: it is drawn from the two trees on disk after the
        reply has been said, and it is written down as well, so a transcript
        shows what the user was told alongside what they were promised.
        """
        try:
            owed = self._obligations()
            saved = self._saved_theorems()
        except Exception:  # noqa: BLE001 - a status line must never end a turn
            return []
        if not saved:
            # The turn that started all this: no tool call, no artifact, and a
            # reply that says the thing is proved. There are no obligations to
            # list because there is nothing to owe them -- which is exactly what
            # the user has to be told, since silence here would read as assent.
            # Said whenever the workspace holds no theorem, so it cannot be
            # timed around by claiming a result before any work is saved.
            self._record({"type": "obligations", "outstanding": [], "saved_theorems": 0})
            return [
                TurnEvent(
                    "notice",
                    text=(
                        "Hardy: no theorem is saved in this workspace, so nothing here is "
                        "reportable. Anything said above rests on the conversation alone."
                    ),
                )
            ]
        if not owed:
            return []
        self._record({"type": "obligations", "outstanding": [item.as_dict() for item in owed]})
        return [
            TurnEvent(
                "notice",
                text=(
                    # About what is outstanding, not about the workspace as a
                    # whole: a theorem already reported was reportable, and a
                    # blanket "nothing here may be reported" contradicted a
                    # report Hardy itself had just accepted.
                    f"Hardy: {completion.summary(owed)}. None of this is reportable until "
                    f"it is settled:\n{completion.describe(owed)}"
                ),
            )
        ]

    def send(self, text: str) -> str:
        """`stream`, for a caller with nothing to draw it on."""
        return final_text(self.stream(text))

    def cancel(self, reason: str = "user_cancelled") -> int:
        """Stop the turn and the work it has already started. Any thread.

        The model stops, no *further* tool call runs, and every child process
        this session has in flight is asked to stop — a Lean elaboration, a
        Tectonic compile, the cell a CAS kernel is grinding on. Returns how
        many were asked, so a caller can say what it actually reached.

        Idempotent in the part that records the cancellation, deliberately not
        in the part that signals: `_cancelled` is what stops a *second*
        transcript entry and a second teardown of the runtime, and the children
        are asked once because that is all the first press has to do. A second
        press escalates, and goes through `escalate` rather than back through
        here.

        What this still cannot promise is that a file a tool call already wrote
        will be unwritten. An interrupted child leaves whatever it had already
        put on disk, which is why `_dispatch` refuses new calls rather than
        trying to undo finished ones.
        """
        if self._cancelled.is_set():
            return 0
        self._cancelled.set()
        self._record({"type": "turn", "status": "cancelled", "reason": reason})
        cancel = getattr(self.runtime, "cancel", None)
        if cancel is not None:
            cancel()
        return self.interrupt_work()

    def resume_work(self) -> None:
        """Lift a stop, so new work is allowed to run. Any thread.

        A stop stays in force after `cancel` so that work admitted a moment
        earlier cannot start its child after the press and outlive it. That
        makes lifting it somebody's job, and the job belongs to whatever is
        about to start work: a turn does it here, and the terminal does it
        before running a command, because a command is not a turn and an Esc
        pressed during one would otherwise still be in force over the next.
        """
        process.resume_children()
        if self.cas is not None:
            self.cas.session.resume()

    def interrupt_work(self) -> int:
        """Ask the children in flight to stop. Returns how many.

        The CAS kernel is asked through its own session rather than through
        `process`: it is persistent, and only the session knows whether a cell
        is actually in flight and how to read what comes back. Every other
        child registers itself with `process.tracked` -- `run_process` does it
        for Lean and Tectonic, and the interactive LaTeX check does it around
        the `Popen` it drives itself.

        Two children are still out of reach, both inside `cas_export`: the
        script it runs to check the export, and the fresh kernel it replays in.
        They belong to a `CasSession` built for the export and discarded with
        it, so an export is still bounded only by its own limits.

        The register is per *process*, not per session, so this reaches every
        tracked child running anywhere in this interpreter. Hardy runs one
        session per process, which is why that is the same set in practice —
        and the register is the only place a child started five call frames
        down inside a tool is reachable from at all, short of threading a
        cancellation token through every signature between here and there.
        """
        stopped = process.interrupt_children()
        if self.cas is not None and self.cas.session.interrupt():
            stopped += 1
        return stopped

    def escalate(self) -> int:
        """Stop waiting for the interrupts to be taken. Returns how many.

        The second press. An interrupt is a request; a child sitting in a loop
        that never checks for signals will not take it, and this is the way out
        of waiting on one. It costs what the timeout costs — a killed CAS
        kernel takes its namespace with it — which is why it is deliberately
        not what the first press does.
        """
        stopped = process.stop_children()
        if self.cas is not None and self.cas.session.escalate():
            stopped += 1
        return stopped

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

    def _recover_spend(self) -> Usage:
        """The workspace's running total, rebuilt from its history if need be.

        A workspace written before the ledger existed has no `usage` key, but
        its transcript is not silent about what it spent: `claude_runtime` has
        been recording a `result` event with `cost_usd` per exchange all along.
        Opening such a workspace to `Nothing spent yet.` would understate a
        session by its entire history, and the next exchange would then be
        written down as the whole of it.

        So the reports are replayed through the same `record` that a live turn
        uses -- which gives the recovered ledger the honesty of a live one for
        free: those events carry no token counts, so tokens come back as
        unreported rather than as zero, and once new exchanges do count them
        `/status` says which exchanges the token totals cover.

        Once. The result is saved immediately, so the next open takes the
        stored ledger and no transcript is ever counted twice.
        """
        # A ledger that would not read is treated exactly as a missing one, and
        # so is its cursor: the cursor's only meaning is "the ledger beside me
        # accounts for the transcript this far", and there is no such ledger
        # any more. Keeping it would pair an empty total with a cursor at the
        # end of the file -- nothing recovered, nothing recoverable, and the
        # next exchange written down as the whole session.
        held = Usage.from_dict(self.state.get(USAGE_KEY))
        recovered = held if held is not None else Usage()
        start = self._ledger_cursor(fresh=held is None)
        counted = 0
        for event in self._recorded(start):
            if event.get("type") == "result":
                recovered = recovered.record(event)
                counted += 1
        if not counted:
            return recovered
        self.state[USAGE_KEY] = recovered.as_dict()
        self._record({"type": "migration", "reason": "spend", "recovered_turns": counted})
        self._mark_ledger_read(self._transcript_end())
        return recovered

    def _transcript_end(self) -> int:
        return self.transcript_path.stat().st_size if self.transcript_path.exists() else 0

    def _ledger_cursor(self, *, fresh: bool) -> int:
        """Where in the transcript the stored ledger has already read to.

        Zero for a workspace with no ledger at all -- its whole transcript is
        history to recover. Otherwise the saved cursor, which is what makes the
        two writes behind a completed exchange survive being interrupted
        between: `_record` appends the `result` and `_remember_spend` saves the
        ledger, and a process killed in between leaves the transcript ahead of
        it. Reopening replays only that tail rather than trusting a ledger that
        is known to be short.

        A cursor past the end of the file means the transcript was truncated or
        replaced. The ledger is then the only surviving account, so it is kept
        as it stands and the cursor reset -- replaying a shorter file against a
        ledger already built from a longer one would count that history twice.
        """
        if fresh:
            return 0
        cursor = self.state.get(CURSOR_KEY)
        size = self._transcript_end()
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0 or cursor > size:
            # Set rather than advanced. `_mark_ledger_read` only ever moves the
            # cursor forward, which is right while the transcript only grows
            # and wrong here: the whole point is that this cursor is past the
            # end, and leaving it there would put the next appended result
            # below it, where a replay would never look again.
            #
            # (No cursor at all lands here too: a ledger written before this
            # existed has read its whole transcript by construction.)
            self.state[CURSOR_KEY] = size
            self._save_state()
            return size
        return cursor

    def _mark_ledger_read(self, offset: int | None = None) -> None:
        """Record that the ledger accounts for the transcript up to `offset`.

        The end of the event just handled, not the file's current size: two
        turns' reports can be in flight at once, and one of them may already
        have been appended by a thread still waiting to fold it. Advancing to
        the file's end would step over that one, and a crash before its thread
        got the lock would leave it skipped for good.

        Never backwards, because each result advances to its own end and they
        need not be handled in the order they were written. Saved with the
        ledger in one write, so an interruption loses both and the replay
        starts from the same place the ledger did rather than from a cursor
        that outran it.
        """
        if offset is None:
            offset = self.transcript_path.stat().st_size if self.transcript_path.exists() else 0
        held = self.state.get(CURSOR_KEY)
        held = held if isinstance(held, int) and not isinstance(held, bool) and held >= 0 else 0
        self.state[CURSOR_KEY] = max(held, offset)
        self._save_state()

    def _recorded(self, start: int = 0) -> Iterator[dict[str, Any]]:
        """Every event the transcript holds from `start` on, skipping non-events.

        Streamed rather than read whole: a long-running workspace's transcript
        is the largest file in it. A line that will not parse is skipped rather
        than raised -- the transcript is append-only and a process killed
        mid-write leaves exactly that, and one torn line is not a reason to
        refuse to open the workspace.

        `errors="replace"` is what makes that promise true rather than nearly
        true. `_record` writes with `ensure_ascii=False`, so a kill during an
        append can cut the last line inside a multi-byte character -- and
        decoding that strictly raises before `json.loads` is reached, past the
        guard below. Since this runs while the session is being constructed,
        the cost would be the workspace rather than the torn line. Replaced
        bytes turn it into something that merely fails to parse, which is the
        case already handled.
        """
        if not self.transcript_path.exists():
            return
        with self.transcript_path.open(encoding="utf-8", errors="replace") as handle:
            if start:
                handle.seek(start)
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event

    def _remember_spend(self, event: dict[str, Any], offset: int, *, unreported: bool = False) -> None:
        """Add one exchange's reported cost and tokens to the running total.

        Written after every exchange rather than at the end of the session: a
        session that is killed, or that ends by the window closing, still spent
        what it spent, and a total that only survives a clean exit is a total
        nobody can rely on.

        Exactly one record per exchange, made by whichever of two threads gets
        there. The runtime's worker brings the provider's report; the thread
        that drained the turn brings the news that there was not going to be
        one, having waited only as long as `_consume`'s teardown allows. The
        `_reported` flag is what stops the second adding a turn the first
        already added, and it is read and set under `_spend` so the two make
        one decision rather than two guesses.

        An exchange recorded as unreported stays that way. A report that turns
        up afterwards is not folded into it -- see `_observed` for why a stale
        session-to-date figure is worse than no figure -- so there is no
        provisional state here for a later report to settle, and none to
        persist for a reopen to reconstruct.

        `self.usage` is immutable, so each assignment publishes a whole new
        total rather than a half-updated one to the thread that draws it.
        """
        with self._spend:
            if unreported:
                if self._reported.is_set():
                    return
                self._reported.set()
            self.usage = self.usage.record(event)
            self.state[USAGE_KEY] = self.usage.as_dict()
            self._mark_ledger_read(offset)

    def _skip_spend(self, offset: int) -> None:
        """Account for a result the ledger deliberately did not fold."""
        with self._spend:
            self._mark_ledger_read(offset)
