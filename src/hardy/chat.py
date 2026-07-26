from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .latex import ROOT_DOCUMENT, LatexTools
from .lean import LeanTools
from .models import Request, ToolResult, TurnEvent
from .prompts import CHAT_SYSTEM_PROMPT, chat_cas_prompt
from .workspace import (
    BuildFailure,
    ImportCycle,
    LeanWorkspace,
    WorkspacePathError,
    declarations,
    dependents,
    internal_imports,
    module_name,
    module_path,
    name_aliases,
    safe_relative,
)

# Where the two artifact trees live inside a workspace. A session written
# before they existed kept one file of each at the top level; `_migrate_layout`
# moves those in rather than leaving a workspace that reads as empty.
LEAN_DIR = "lean"
BUILD_DIR = ".build/lean"
TEX_DIR = "tex"
DEFAULT_LEAN_PATH = "Main.lean"
DEFAULT_TEX_PATH = ROOT_DOCUMENT

LABEL = re.compile(r"\\label\{([^}]*)\}")

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


def _uncommented_tex(source: str) -> str:
    r"""`source` with its TeX comments removed.

    A `%` starts a comment unless it is escaped as `\%`, and a backslash before
    that is itself an escaped backslash -- so the run of backslashes has to be
    counted rather than just the one character before the `%`.
    """
    kept = []
    for line in source.splitlines():
        cut = 0
        while True:
            found = line.find("%", cut)
            if found < 0:
                kept.append(line)
                break
            backslashes = len(line[:found]) - len(line[:found].rstrip("\\"))
            if backslashes % 2 == 0:
                kept.append(line[:found])
                break
            cut = found + 1
    return "\n".join(kept)


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
        self.lean_workspace = LeanWorkspace(
            workspace / LEAN_DIR,
            workspace / BUILD_DIR,
            self._compile_module,
            environment=_toolchain_identity(lean_command, lean_project),
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

    def _final_gates(self, source: str) -> ToolResult | None:
        """What disqualifies a source from being saved, before Lean is asked.

        All of it is textual, so it costs nothing and runs first: there is no
        point spending a minute elaborating a file that a `sorry` or an
        unapproved axiom already rules out.
        """
        if self.lean.has_holes(source):
            return ToolResult(False, "saved Lean artifacts may not contain sorry or admit", source)
        approved = {item["formal_name"]: " ".join(item["lean_statement"].split()) for item in self.state["assumptions"]}
        # Named `assumed` rather than `declarations`: the module-level function
        # of that name is what reads theorems out of a source.
        assumed = re.findall(r"(?m)^\s*(?:axiom|constant)\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*(.+?)\s*$", source)
        for name, statement in assumed:
            if approved.get(name) != " ".join(statement.split()):
                return ToolResult(False, f"unapproved or altered assumption `{name}`; use request_assumption first", source)
        return None

    def _run_lean_source(self, source: str) -> ToolResult:
        return self.lean.run_source(source, env={"LEAN_PATH": self.lean_workspace.lean_path()})

    def _compile_module(
        self, module: str, source_root: Path, build_root: Path, source_file: Path
    ) -> tuple[bool, str]:
        """Build one workspace module, for `LeanWorkspace` to sequence."""
        result = self.lean.compile_module(source_root, build_root, source_file)
        return result.ok, result.output

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
            commit()
        finally:
            LeanWorkspace.discard(shadow)
        # Absent from `seen` when the source was byte-identical to what was
        # already built, so the cache skipped it. Nothing was wrong with it.
        return seen.get(module, ToolResult(True, "unchanged; already built", source))

    def _build_imports(self, space: LeanWorkspace, source: str) -> BuildFailure | ToolResult | None:
        """Make the workspace modules a candidate imports importable."""
        try:
            needed = internal_imports(source, space.sources())
            return space.build_modules(needed) if needed else None
        except ImportCycle as error:
            return ToolResult(False, str(error), source)

    def _missing_registered_names(self, sources: dict[str, str]) -> list[str]:
        """Registered formal names that survive nowhere in a tree.

        Declarations are matched by name rather than by text, because a
        `theorem one` inside `namespace Hardy` is `Hardy.one` and that string
        appears nowhere in the file. The textual fallback still covers what
        `declarations` does not read -- an approved `axiom`, a `def`, a
        structure -- so a registered name backed by one of those is not
        reported as lost.
        """
        declared: set[str] = set()
        for source in sources.values():
            found = declarations(source)
            for name in (*found["theorem"], *found["lemma"]):
                declared.update(name_aliases(name))
        return [
            item["formal_name"]
            for item in self.state["names"]
            if not declared.intersection(name_aliases(item["formal_name"]))
            and not any(
                re.search(rf"\b{re.escape(item['formal_name'])}\b", source)
                for source in sources.values()
            )
        ]

    def _labels(self) -> set[str]:
        r"""Every live `\label` in the saved writeup tree.

        Commented-out lines do not count. `% \label{thm:x}` is a placeholder,
        not a writeup: LaTeX never creates that label, and treating it as one
        would release the ratchet for a theorem the document does not describe.
        """
        if not self.tex_root.is_dir():
            return set()
        found: set[str] = set()
        for path in sorted(self.tex_root.rglob("*.tex")):
            found.update(LABEL.findall(_uncommented_tex(path.read_text(encoding="utf-8"))))
        return found

    def _saved_theorems(self) -> set[str]:
        found: set[str] = set()
        for source in self.lean_workspace.sources().values():
            found.update(declarations(source)["theorem"])
        return found

    def _undocumented(self) -> tuple[str, ...]:
        """Saved theorems with no writeup behind them.

        Derived from the registry and the writeup tree every time it is asked
        for. A stored flag would outlive the file it described, and
        `session.json` already carries enough state that has to be kept true.
        """
        labels = self._labels()
        documented = {item["formal_name"] for item in self.state["names"] if item["latex_name"] in labels}
        saved = self._saved_theorems()
        # A bare registry name may stand for a qualified declaration, but only
        # while it names exactly one. `A.result` and `B.result` both answer to
        # `result`, and letting one label cover both would report a theorem as
        # written up when nothing in the document refers to it.
        by_leaf: dict[str, list[str]] = {}
        for name in saved:
            by_leaf.setdefault(name.rsplit(".", 1)[-1], []).append(name)
        owed = []
        for name in sorted(saved):
            leaf = name.rsplit(".", 1)[-1]
            if name in documented or (leaf in documented and len(by_leaf[leaf]) == 1):
                continue
            owed.append(name)
        return tuple(owed)

    def _documentation_gate(self, source: str) -> str | None:
        """The catch-up ratchet: write up the last theorem before the next.

        Refuses only when the tree already owes a writeup *and* this save would
        add a theorem it does not already contain. The first condition alone
        would trap the session: a model could no longer repair, restate, or
        delete the very theorem blocking it. The second alone would let one
        file absorb any number of undocumented claims.
        """
        owed = self._undocumented()
        if not owed:
            return None
        existing = self._saved_theorems()
        introduced = [name for name in declarations(source)["theorem"] if name not in existing]
        if not introduced:
            return None
        return (
            f"the workspace owes a writeup for {list(owed)} before a new theorem "
            f"({introduced[0]}) is added. Call record_name for each, then save_latex "
            "with a \\label for each latex_name. A lemma carries no such requirement."
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
        result = self.latex.check(source, path=relative, tree=self.tex_root, output_dir=self.workspace)
        if not result.ok:
            return result
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.rstrip() + "\n", encoding="utf-8")
        # Advisory rather than a refusal. With the save_lean ratchet in place a
        # hard gate here would deadlock: Lean blocked for want of a writeup,
        # and the writeup blocked for not yet covering everything registered.
        missing = [item["latex_name"] for item in self.state["names"] if item["latex_name"] not in self._labels()]
        if missing:
            return ToolResult(True, f"{result.output}\n\nSaved. Still missing labels for registered names: {missing}", source)
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
        lean = []
        for module, source in sorted(self.lean_workspace.sources().items()):
            found = declarations(source)
            lean.append({
                "path": str(module_path(module)),
                "module": module,
                "imports": list(internal_imports(source, self.lean_workspace.sources())),
                "theorems": list(found["theorem"]),
                "lemmas": list(found["lemma"]),
            })
        tex = (
            sorted(path.relative_to(self.tex_root).as_posix() for path in self.tex_root.rglob("*.tex"))
            if self.tex_root.is_dir()
            else []
        )
        return {
            "manifest": self.state,
            "lean": lean,
            "tex": tex,
            "undocumented_theorems": list(self._undocumented()),
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
            # The same check a save makes. Without it a deletion could leave
            # the manifest naming a declaration that exists nowhere, and every
            # later save would then be refused for dropping a name that was
            # already gone -- with no tool able to clear it.
            lost = self._missing_registered_names(shadow.sources())
            if lost:
                return ToolResult(False, f"deleting {path} would drop registered names: {lost}; nothing was deleted")
            commit()
        finally:
            LeanWorkspace.discard(shadow)
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
                root.read_text(encoding="utf-8"), tree=self.tex_root, output_dir=self.workspace
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
