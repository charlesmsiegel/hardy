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
from .latex import ROOT_DOCUMENT, LatexTools, compiles_document
from .layout import (
    LOCAL_DIR,
    LOCAL_STATE,
    RECORD,
    TRANSCRIPT,
    Layout,
    LayoutError,
    WriteGuard,
    files_under,
    global_build,
    global_lean,
    guard_for,
    read_bytes,
    read_text,
)
from .lean import LeanTools
from .models import Request, ToolResult, TurnEvent
from .modules import ModuleIndex
from .project_context import (
    PROJECT_CONTEXT_EVENT,
    PROJECT_CONTEXT_KEY,
    ProjectContext,
    read_project_context,
)
from .prompts import CHAT_SYSTEM_PROMPT, chat_cas_prompt, chat_project_context_prompt
from .search_tools import SEARCH_TOOL_NAMES, SEARCH_TOOLS, SearchToolRuntime
from .truncation import truncate
from .usage import Usage
from .workspace import (
    COMMAND,
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
    normalise_lean,
    safe_relative,
    statements,
    unreadable_assumptions,
)
from .writeup import escape_tex_text

# Where the two artifact trees live inside a workspace, and the path a tool
# call gets when it names neither -- the one file most sessions ever need.
LEAN_DIR = "lean"
BUILD_DIR = ".build/lean"
BUILD_DIR_TEX = ".build/tex"
TEX_DIR = "tex"
DEFAULT_LEAN_PATH = "Main.lean"
DEFAULT_TEX_PATH = ROOT_DOCUMENT

# What LaTeX wrote down about the labels it actually created, in its own .aux.
NEWLABEL = re.compile(r"\\newlabel\{([^}]*)\}")

# The manifest key that exists for Hardy and not for the model. The listing
# reports each verdict checked against the tree in front of it, and handing back
# the stored one as well would put two answers for the same module in one
# response. The ledger and the provider thread are no longer withheld here
# because they are no longer in the record: they live in `.local/state.json`.
# `project_context` joins it for a different reason than `audit`'s, and the
# reason is what makes the withheld condition reproducible. The model is given
# the instructions file itself, in its own block, or it is given nothing; the
# manifest entry is Hardy's bookkeeping for noticing the next edit, and a
# second, weaker statement of the same thing -- a name and a digest -- adds
# nothing beside the block. It subtracts, in the one case that matters:
# reopening a workspace with the context switched off would otherwise put the
# file's name, digest and size in front of the model in the run whose whole
# point is that no project-derived input reaches it, and make
# `--no-project-context` mean something different on a workspace that had once
# read a file than on one that never had. Filtered from the system prompt as
# well as from the listing, which is why `_context` names it: unlike `audit`,
# it has no business in either.
WITHHELD = ("audit", PROJECT_CONTEXT_KEY)
USAGE_KEY = "usage"
#: How far into `transcript.jsonl` the stored ledger has been brought up to
#: date. Hardy's own bookkeeping, and no more the model's business than the
#: ledger it belongs to.
CURSOR_KEY = "usage_cursor"
THREAD_KEY = "provider_session"
#: How many `result` events `_recover_spend` replayed to build the stored
#: ledger. Machine-local, like the ledger: the transcript it was rebuilt from
#: is the versioned record of the mathematics, and a fresh clone opening a
#: project must not append a line to it saying that this machine had no ledger
#: yet -- `.local/` is gitignored, so that is true of every clone there is.
RECOVERED_KEY = "usage_recovered_turns"


class WriteupNotSaved(ValueError):
    """A writeup file the compiler accepted and the filesystem would not take.

    Raised out of the callback `latex.check` runs before it publishes
    anything, so that a save which cannot land takes the PDF and the labels
    with it. Its own type because the callback runs deep inside the compile
    and the refusal has to be told apart there from a compiler problem: the
    tool answers "this file could not be saved", not "LaTeX failed".
    """


class SchemaError(ValueError):
    """A record whose `schema_version` this build does not read.

    Its own type, not a bare `ValueError`: a caller opening a project needs
    to tell "this workspace predates a format change, refused on purpose"
    apart from every other way constructing a session can fail, and render
    it as the one clean line it is -- not as a session-startup problem to
    fall back away from, nor as a stack trace.
    """


CHAT_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "check_lean", "description": "Run Lean on a complete candidate source file without saving it. `path` is the workspace file it would become, defaulting to Main.lean; imports of other workspace files resolve against what is already saved.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_lean", "description": "Check and save one Lean file in the workspace tree, defaulting to Main.lean. Every file importing it is rebuilt and the save is refused whole if any of them breaks. Completed saved work must contain no sorry or admit.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "check_latex", "description": "Compile a candidate LaTeX file against the saved document tree without keeping it. `path` defaults to writeup.tex, the root document.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_latex", "description": "Compile and save one LaTeX file in the writeup tree, defaulting to writeup.tex. Fragments are \\input from the root document.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_workspace", "description": "List the workspace: the manifest, every Lean file with its module name and declarations, and every LaTeX file.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read one workspace file, Lean or LaTeX, by its path. Long files come back truncated from the top; the reply says so and names the `start_line` to pass to read the next part. `start_line` is 1-based and defaults to 1.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete one workspace file. Refused if another workspace file imports it.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "record_name", "description": "Record the durable correspondence between a Lean declaration and its LaTeX label/name.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "latex_name": {"type": "string"}, "description": {"type": "string"}}, "required": ["formal_name", "latex_name", "description"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "request_assumption", "description": "Ask the human for permission to introduce an axiom when a result is unavailable. Never assume approval.", "parameters": {"type": "object", "properties": {"formal_name": {"type": "string"}, "lean_statement": {"type": "string"}, "latex_name": {"type": "string"}, "informal_statement": {"type": "string"}, "source": {"type": "string"}, "reason": {"type": "string"}}, "required": ["formal_name", "lean_statement", "latex_name", "informal_statement", "source", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "report_result", "description": "Report finished work. The only way to call anything proved, done, or complete: say it in prose and Hardy contradicts you in front of the user. Refused unless every theorem named is saved Lean the kernel audited, the writeup creates its label and quotes its exact Lean statement verbatim, and every assumption the work rests on is stated in an appendix in both Lean and prose. A theorem still resting on a hole is graded partial rather than refused: the report names which, and the writeup must carry it on exactly the same terms, so a reader can see what was and was not proved.", "parameters": {"type": "object", "properties": {"theorems": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}, "required": ["theorems", "summary"], "additionalProperties": False}}},
]

# Always offered, unlike the cas_* tools, and refusing with a reason when
# there is no Lake project. See `search_tools` for why absence is reported
# rather than hidden.
CHAT_TOOLS += SEARCH_TOOLS


def _reportability(owed: Sequence[completion.Obligation]) -> str:
    """How an outstanding set bears on a report, in one sentence.

    Said in two places -- the note appended to every save and the notice drawn
    at the end of a turn -- and they must not disagree, because between them
    they are the only thing contradicting a model that says the work is done.

    An open theorem is the case that needed separating. Both used to say
    nothing here was reportable, which stopped being true when `report_result`
    began grading a claim resting on a hole as partial: Hardy would have
    contradicted a report it had just accepted. What holds of every obligation
    here, open or not, is that none of them may be reported as *proved*.
    """
    if all(item.kind == "open" for item in owed):
        return (
            "Reportable only as a partial result, never as proved, until the holes "
            "are closed:"
        )
    return "None of this may be reported as proved until it is settled:"

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


# What one assumption probe may spend. Generous because it is paid once per
# axiom request rather than per turn, and because the alternative -- timing out
# and telling the human the statement is unchecked -- is the outcome the probe
# exists to avoid.
PROBE_SECONDS = 600.0


def _probe_suggestion(result: Any, line: int) -> str:
    """What `exact?` offered on `line`, if anything.

    `exact?` reports its term as an informational diagnostic. Every other probe
    reports nothing at all when it succeeds, so the caller falls back to naming
    the tactic. The literal `Try this:` prefix is Lean's and is not pinned by
    any fixture here -- treated as a bonus, and nothing depends on it.
    """
    for diagnostic in getattr(result, "diagnostics", ()):
        if diagnostic.line == line and "Try this:" in diagnostic.message:
            return diagnostic.message.split("Try this:", 1)[1].strip()
    return ""


class MathematicsSession:
    def __init__(self, workspace: Path, make_runtime: Callable[..., ChatRuntime], lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, str]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0, cas: CasToolRuntime | None = None, cas_detail: str = "", search: SearchToolRuntime | None = None, search_detail: str = "", root: Path | None = None, project_context: bool = True):
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
        # None when no pinned Lake project was found. Unlike `cas`, the tools
        # are still advertised and refuse with the reason: a CAS backend is
        # optional, a Lean project is what Hardy is for, and a model handed no
        # search tool concludes Hardy cannot search rather than that this
        # machine is not set up. That conclusion is not hypothetical -- a
        # session that guessed `Mathlib.GroupTheory.Sylow.Basic` had no way to
        # find out it wanted `Mathlib.GroupTheory.Sylow`, and stopped writing
        # Lean.
        self.search = search
        self.search_detail = search_detail
        # Every file this session writes into the problem goes through one of
        # these two, and nothing else may. `Layout.ensure` proved the shape of
        # the tree before the process got here; that proof was true at startup
        # and says nothing about an append minutes later, and `transcript.jsonl`
        # is a tracked file a clone can ship as a symlink pointing anywhere.
        # The guard re-proves the directory at each write -- see `WriteGuard`.
        #
        # `create=True` rather than `mkdir` because `mkdir(exist_ok=True)` on a
        # workspace that is already a symlink succeeds silently, on someone
        # else's directory.
        self._workspace_guard = WriteGuard(workspace, create=True)
        # The root the problem sits in. Derived rather than demanded, because
        # every caller already knows the problem directory and none of them
        # should have to restate the layout to get the project's shared Lean.
        self.root = root if root is not None else workspace.parent
        # The libraries a problem may import but did not author, in resolution
        # order: the project's own, then the user's. The problem's own build
        # comes first on LEAN_PATH, so its modules win a name collision -- and
        # `shadowed_modules` makes that collision reportable rather than
        # silent. Absent trees are dropped here, which is the normal case: a
        # project with no shared library must cost nothing and error nowhere.
        self.shared_roots: tuple[tuple[Path, Path], ...] = self._discover_shared()
        placeholder = Request("example : True", "interactive workspace", ("Mathlib",))
        # One index for the session, shared by the two things that need to know
        # what this project ships: the `search_modules` tool, and the sentence
        # `LeanTools` puts above Lean's "object file ... does not exist" so a
        # wrong import stops reading as a broken toolchain.
        self.modules = ModuleIndex(lean_project)
        self.lean = LeanTools(
            placeholder,
            lean_command,
            timeout=lean_timeout,
            project=lean_project,
            modules=self.modules,
        )
        self.latex = LatexTools(latex_command)
        # Named through `layout`, not spelled again here: these two paths and
        # the names the guard is asked for have to agree, and two string
        # literals that must match are one edit away from not matching.
        self.state_path = workspace / RECORD
        self.transcript_path = workspace / TRANSCRIPT
        # Machine-local state, beside the record but never part of it. The
        # record is versioned and describes the mathematics; the provider
        # thread and the spend ledger describe this machine and this account,
        # and a clone of the project must not inherit either.
        self.local_path = workspace / LOCAL_DIR / LOCAL_STATE
        self._local_guard = WriteGuard(workspace / LOCAL_DIR, create=True)
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
        self._toolchain = _toolchain_identity(lean_command, lean_project)
        # The shared libraries are part of the problem's identity but not of
        # their own: a problem module that imports one is only as current as
        # the source behind it, while a shared module is stale only when its
        # own text or the toolchain moves. Folding the digest into both would
        # rebuild an entire shared library over an edit to one of its files.
        self._shared_stamp = self._shared_digest()
        self._environment = self._shared_identity(self._shared_stamp)
        self.lean_workspace = LeanWorkspace(
            workspace / LEAN_DIR,
            workspace / BUILD_DIR,
            self._compile_module,
            environment=self._environment,
            external=self._external_stamp,
        )
        # One workspace per shared tree, built by the same `compile_module`
        # path the problem's own modules take. A tree compiled by some other
        # route would drift from the one whose staleness rules the session
        # trusts, and the whole point of these is that they are ordinary Lean.
        self._shared_spaces = self._shared_workspaces()
        # What was last put in the record about the shared libraries. Compared
        # rather than re-recorded, so a fact that has not changed does not bury
        # a long session's turns under repetitions of itself.
        self._shared_observed: dict[str, Any] = {"shadowed": {}, "unbuildable": []}
        self._shared_failures: tuple[str, ...] = ()
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
        # on the runtime's own thread -- and `WriteGuard.write_json` replaces a
        # temporary file at a fixed path. Two writers at once would interleave.
        self._writes = threading.Lock()
        # State first: the runtime is built from the system prompt, which embeds
        # the manifest, and it resumes the provider thread the local state
        # remembers.
        self.state = self._read_state()
        self.local = self._read_local()
        # What the project itself says it is for, read before the runtime is
        # built because the system prompt embeds it. One file at the root and
        # no ancestor of it; absent, unreadable or switched off, and nothing
        # is added. `project_context=False` is a deliberate choice, not an
        # error, and is recorded as one. It governs what this run's system
        # prompt carries and not what a resumed provider thread remembers --
        # see `_sync_project_context` for why the record makes that sound, and
        # `_carried_thread` for the case where a thread IS dropped.
        self.project_context: ProjectContext | None
        self.project_context, self.project_context_detail = (
            read_project_context(self.root) if project_context else (None, "not read (project_context is off)")
        )
        # What the workspace has already spent, so reopening it continues the
        # total rather than restarting it. Read before the first turn can add
        # to it.
        self.usage = self._recover_spend()
        # The runtime needs a way to reach the tools, and the tools need the
        # workspace, so it is built here rather than handed in ready-made.
        self.runtime = self._build(session_id=self._carried_thread())
        self._sync_provenance()
        self._sync_project_context()

    def _build(self, model: str | None = None, session_id: str | None = None) -> ChatRuntime:
        """The runtime, with the system prompt this project's record implies.

        Nothing is carried in from the transcript for a workspace that has no
        provider thread. There used to be: a `_carried` step read the tail of
        `transcript.jsonl`, appended "This workspace predates the current
        provider session" to the prompt, and wrote a `migration` event back
        into the transcript. Its trigger was the absence of local state -- and
        `.local/` is gitignored by design, so that absence is the NORMAL state
        of a fresh clone, not evidence of an old workspace. Every clone, on
        every first open, appended a line to the versioned trajectory before
        any mathematics had happened and left the checkout dirty; open it on
        another machine and it happened again. The claim was false too, since
        a clone does not predate anything. `_recover_spend` had the identical
        bug and lost it in `b15ed30`; there are no pre-SDK workspaces at
        `schema_version` 2 for this one to serve, so it is simply gone rather
        than moved into `.local/state.json`.
        """
        prompt = SYSTEM_PROMPT
        if self.cas is not None:
            prompt += "\n\n" + chat_cas_prompt(self.cas.session.backend.name)
        return self._make_runtime(
            model=model,
            system_prompt=prompt + self._context(),
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
        self.runtime = self._build(model=model, session_id=self._carried_thread())
        self.state.update(provenance(self.runtime))
        self._save_state()
        self._record({"type": "model", "reason": "switched", "previous": previous, **provenance(self.runtime)})

    def _read_state(self) -> dict[str, Any]:
        """The record, refusing anything this version does not read.

        There is deliberately no reader for version 1. Accepting one anyway
        would carry its `provider_session`, `usage` and `usage_cursor` into a
        record that is now versioned -- and, since `WITHHELD` no longer names
        those keys, into the model's context as well. Refusing is the honest
        failure.

        Being unreadable at all is refused the same way, and that is the point
        of the three lines below. `session.json` is versioned: it comes back
        with a merge conflict in it, gets hand-edited, gets truncated by a
        full disk. Left to `json.loads` and `dict.get`, a conflicted record
        raised `JSONDecodeError` and a record holding `[]` raised
        `AttributeError` -- neither a `SchemaError`, so the interactive shell
        did not recognise either as a deliberate refusal, announced a fallback
        to the plain session, ran the identical load a second time, and ended
        the session on a stack trace. That is exactly the failure `SchemaError`
        exists to prevent, so every way the record can fail to be a version-2
        object is translated into one.
        """
        if self.state_path.exists():
            try:
                with self._workspace_guard.open(RECORD, encoding="utf-8") as handle:
                    stored = json.loads(handle.read())
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise SchemaError(f"{self.state_path} is not readable JSON: {error}") from None
            if not isinstance(stored, dict):
                raise SchemaError(
                    f"{self.state_path} holds a {type(stored).__name__}, not the record object "
                    "this Hardy reads"
                )
            version = stored.get("schema_version")
            if version != 2:
                raise SchemaError(
                    f"{self.state_path} is schema version {version!r}; this Hardy reads version 2 only"
                )
            return stored
        # `audit` is absent until the first save; a workspace with none may not
        # read as a clean one.
        return {"schema_version": 2, "names": [], "assumptions": []}

    def _read_local(self) -> dict[str, Any]:
        """This machine's state, or an empty one.

        Unreadable is treated as absent rather than raised. The file is
        gitignored and disposable by construction, and losing a resumable
        thread is never a reason to refuse to open the project.
        """
        if not self.local_path.exists():
            return {}
        try:
            with self._local_guard.open(LOCAL_STATE, encoding="utf-8") as handle:
                loaded = json.loads(handle.read())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # UnicodeDecodeError is in the list deliberately: it is not a
            # subclass of the others, and without it a file of invalid bytes
            # would refuse to open the project rather than being treated as the
            # disposable state this docstring promises it is.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save_state(self) -> None:
        """The one door `session.json` is written through, from any thread."""
        with self._writes:
            self._workspace_guard.write_json(RECORD, self.state)

    def _save_local(self) -> None:
        """The one door `.local/state.json` is written through, from any thread."""
        with self._writes:
            self._local_guard.write_json(LOCAL_STATE, self.local)

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

    def _sync_project_context(self) -> None:
        """Make the record say which project instructions this run was given.

        The same treatment `_sync_provenance` gives a model switch, and for the
        same reason: what the model was told is part of the experiment's
        identity, and a change to it is a change of experimental condition.
        This is the whole reconciliation with `setting_sources=[]`. The
        objection to inheriting a user's `CLAUDE.md` was never that Hardy read
        the user's context; it was that nothing recorded it. Recorded context
        satisfies "a run is the run its record claims" completely, and only the
        full text does: a digest of a file the reader does not have proves
        nothing about what was asked for.

        In that order, and the order is the point. `session.json` committed
        first, a process that dies before the append leaves the record naming a
        file whose contents the transcript never received -- and leaves it
        permanently, because every later session then finds the stored digest
        agreeing with the file, returns early, and never repairs the missing
        event. Appended first, the worst a crash costs is one duplicate event,
        both of them true, in a file that is append-only anyway.

        So the transcript takes the text and `session.json` keeps the digest,
        which is what makes this quiet on the ordinary path. `AGENTS.md` and
        the record are both versioned, so a fresh clone opening the project
        finds the stored digest already agreeing with the file beside it and
        appends nothing -- the bug `_build` and `_recover_spend` both had, where
        the NORMAL state of a clone was mistaken for evidence of an old
        workspace and left every checkout dirty before any mathematics had
        happened.
        """
        stored = self.state.get(PROJECT_CONTEXT_KEY)
        current = self.project_context
        if current is None:
            # Nothing to show, and the record already agrees. Note that a run
            # started with the context switched off records the withdrawal
            # rather than leaving the old digest standing: the record would
            # otherwise claim instructions this run never saw.
            if stored is None:
                return
            self._record({"type": PROJECT_CONTEXT_EVENT, "reason": "withheld", "previous": stored})
            del self.state[PROJECT_CONTEXT_KEY]
            self._save_state()
            return
        if stored == current.stored():
            return
        self._record({"type": PROJECT_CONTEXT_EVENT, "reason": "changed" if stored else "read", **current.event()})
        self.state[PROJECT_CONTEXT_KEY] = current.stored()
        self._save_state()

    def _project_context_prompt(self) -> str:
        """The user's instructions as the system prompt carries them, or nothing.

        Appended last, after Hardy's own constraints and after the manifest, so
        that the text stating what outranks what has already been read by the
        time the block itself is.
        """
        current = self.project_context
        if current is None:
            return ""
        return "\n\n" + chat_project_context_prompt(
            name=current.name,
            text=current.text,
            truncated=current.truncated,
            shown=len(current.text.encode("utf-8")),
            total=current.bytes,
        )

    def _without(self, *keys: str) -> dict[str, Any]:
        """The manifest, minus the entries this reader has no business seeing."""
        return {key: value for key, value in self.state.items() if key not in keys}

    def _context(self) -> str:
        # The stored audit verdicts stay here, as they always have -- the system
        # prompt has no second, checked copy of them to contradict. The spend
        # ledger used to be withheld here too, by name; now it lives in
        # `self.local` and was never in `self.state` to begin with, so
        # filtering `self.state` for it would be a no-op that reads as if it
        # still needed filtering.
        #
        # `project_context` is named rather than taken from `WITHHELD`, which
        # would drop the audit too: the two are withheld from different readers
        # for different reasons, and only this one is withheld from both. See
        # the note on `WITHHELD` for why the block, or nothing, is the whole of
        # what the model is owed about the file.
        manifest = json.dumps(self._without(PROJECT_CONTEXT_KEY), ensure_ascii=False)
        return f"\n\nWorkspace: {self.workspace}\nExisting manifest:\n{manifest}" + self._project_context_prompt()

    def _record(self, event: dict[str, Any]) -> int:
        """Append one event, and say where the transcript now ends.

        The offset is what lets the ledger's cursor advance to the end of the
        event it just accounted for rather than to wherever the file happens
        to have reached -- two turns' reports can be in flight at once, and
        the file's current size may already include one nobody has folded.
        """
        event = {"timestamp": time.time(), **event}
        with self._workspace_guard.open(TRANSCRIPT, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            return handle.tell()

    def _final_gates(self, source: str) -> ToolResult | None:
        """What disqualifies a source from being saved, before Lean is asked.

        All of it is textual, so it costs nothing and runs first: there is no
        point spending a minute elaborating a file that an unapproved axiom
        already rules out.

        A hole is not here. `sorry` is how a proof of any size gets built, and
        refusing it meant the unfinished part of a development could never
        reach disk -- so a thousand-line proof lived in the model's context and
        was re-sent in full on every check. What a hole costs is charged where
        a claim is made instead: the audit records it, the obligations name it,
        and `report_result` grades it partial.
        """
        found = declarations(source)
        # The audit asks `#print axioms` about theorems and lemmas, and about
        # nothing else -- so those are the only declarations a hole can be
        # *reported* through. A file that declares neither and carries one
        # would put a hole in the workspace that `/status`, the end-of-turn
        # notice and the banner all stay silent about, which is the one thing
        # keeping holes was not allowed to cost.
        if self.lean.has_holes(source) and not (found["theorem"] or found["lemma"]):
            return ToolResult(
                False,
                "this file has a hole in it and declares no theorem or lemma, so nothing "
                "here can report the hole as open: Hardy tracks one by asking Lean what "
                "each saved theorem and lemma rests on. State the work as a `lemma` -- a "
                "lemma may carry a hole and is free to save -- and the hole is then "
                "reported until you close it.",
                source,
            )
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

    def _run_lean_source(self, source: str, timeout: float | None = None) -> ToolResult:
        return self.lean.run_source(
            source, env={"LEAN_PATH": self._lean_path()}, timeout=timeout
        )

    def _assumption_shape(self, formal_name: str, lean_statement: str) -> str | None:
        """Why this could never be declared, or None.

        `request_assumption` used to accept anything and wrap it in
        `axiom NAME : ...`, so it could approve text `save_lean` would refuse
        forever. That is not hypothetical: a session was told to declare
        `axiom cyclic_of_prime_order : axiom cyclic_of_prime_order (G : Type*)
        ... : ...` -- a double header nothing can parse -- and spent ten turns
        discovering there was no spelling that satisfied both ends. Matching the
        approval required binders the parser refuses; satisfying the parser
        produced a statement that no longer matched the approval.

        So both ends now ask the same code about the same string. `COMMAND` is
        what recognises a line opening a declaration, and an axiom's statement
        is a type, never a command. `unreadable_assumptions` is what `save_lean`
        itself calls.

        A statement is also one line. `True\\naxiom extra : False` is two
        declarations and `ASSUMPTION` reads both happily, so without this the
        request round-trips and an approval granted for the first carries the
        second. Approved statements are stored whitespace-collapsed anyway, so
        refusing a newline costs nothing a caller needed.

        Not sufficient, and not meant to be. A binder-only statement --
        `(G : Type*) : True` -- matches neither check, because
        `axiom f : (G : Type*) : True` parses by taking everything after the
        first colon. It is not valid Lean and only elaboration can say so, which
        is what `_assumption_probe` is for. `opaque`, and any declaration
        keyword `COMMAND` does not list, land there too.
        """
        statement = lean_statement.strip()
        if "\n" in statement or "\r" in statement:
            return (
                "a statement is one line and one type. More than one line can carry a "
                "second declaration, which an approval of the first would not cover. "
                "Collapse it to one line."
            )
        if COMMAND.match(statement):
            return (
                f"a statement may not itself be a declaration, and `{statement[:60]}` "
                f"opens one. Pass only the statement -- the type after the colon -- and "
                f"Hardy writes `axiom {formal_name} :` in front of it. Binders belong "
                f"inside the statement as `forall`, not before the colon."
            )
        declaration = f"axiom {formal_name} : {statement}"
        if unreadable_assumptions(declaration):
            return (
                f"`{declaration[:80]}` cannot be read as `axiom NAME : STATEMENT`, so "
                f"save_lean could never accept it. An assumption carries no binders and "
                f"no universe parameters."
            )
        return None

    # Tried in order, and the order is part of the message: `trivial` closing a
    # statement is damning, while `exact?` closing it says the result was in
    # Mathlib all along.
    #
    # What this catches is what standard automation closes, which is not the
    # same as every logically weak statement, and the difference is worth
    # stating. The graded appendix offered `exists a b : G, a * b = b * a` as the
    # meaning of "abelian". It is true in every group -- `exact <1, 1, rfl>`
    # closes it -- and *none* of these tactics find that witness, `exact?`
    # included; only writing the term does. So this gate is a filter, not a
    # decision procedure. It did catch `exists P : Sylow p G, True` on a live
    # run, which is the same species of vacuity stated a little more carelessly.
    PROBES = ("trivial", "simp", "tauto", "aesop", "exact?")

    def _assumption_probe(self, declaration: str) -> tuple[str | None, str]:
        r"""Ask Lean about a proposed axiom before any human is asked.

        Two questions in one elaboration, because Lean reports diagnostics per
        declaration and a second process buys nothing: does this elaborate at
        all, and can any of `PROBES` close it.

        A statement Lean proves is not an assumption -- it is a theorem nobody
        has saved yet. See `PROBES` for what that does and does not reach: it is
        a filter over what standard automation closes, not a decision procedure,
        and the graded appendix's own error is outside it.

        `import Mathlib` rather than the workspace's own imports. An assumption
        may mention anything, and a narrower import set turns "that name does
        not exist" into "I did not import that name", which is a different
        sentence and a misleading one.

        Returns a refusal or None, and a caveat that is empty unless the probe
        could not be run. A machine whose Lean will not start must not be one
        where every axiom is approved unchecked, nor one where none can be: the
        caveat carries the uncertainty to the human, who is the one deciding.
        """
        head, _, tail = declaration.partition(":")
        # Collapsed to one line before anything else. Which tactic closed the
        # goal is read from `LeanDiagnostic.line`, and Hardy keeps only a
        # diagnostic's start line -- Lean's `endPos` is discarded -- so a
        # two-line statement would attribute an error to the wrong tactic and
        # could report a probe as succeeding when it failed.
        statement = normalise_lean(tail).strip()
        examples = "\n".join(f"example : {statement} := by {tactic}" for tactic in self.PROBES)
        # The probes come FIRST and the axiom LAST, which is the whole design of
        # this file rather than a formatting choice. With the axiom declared
        # above them it is in scope, and `exact?` closes every statement by
        # citing it:
        #
        #     theorem sylow_first : ... := exact fun {G} ... => sylow_first p a
        #
        # A live run refused seven honest requests that way, Sylow's theorems
        # among them, each "proved" from itself. Lean resolves names in order, so
        # putting the axiom after the probes is what makes the question real.
        #
        # Exactly this layout, and `test_assumption_gates` asserts it: the import
        # on line 1, blank, one example per line from line 3, blank, then the
        # declaration last. The arithmetic below is that layout.
        source = f"import Mathlib\n\n{examples}\n\n{head.strip()} : {statement}\n"
        try:
            # Its own timeout, not the session's. `import Mathlib` costs about
            # 20 seconds warm and over three minutes cold, against a session
            # default of 180 -- so on a cold machine the *first* axiom request,
            # which is the one most worth checking, would have degraded to
            # "could not be checked" every time.
            result = self._run_lean_source(source, timeout=max(self.lean.timeout, PROBE_SECONDS))
        except Exception as error:  # noqa: BLE001 - an unrunnable probe is a caveat, never a crash
            return None, f"Lean could not be checked ({error})."
        if getattr(result, "timed_out", False) or getattr(result, "interrupted", False):
            return None, "Lean could not be checked (the elaboration did not finish)."
        errors = [item for item in result.diagnostics if item.severity == "error"]
        if not result.ok and not errors:
            # Lean failed and said nothing this can read. Every conclusion below
            # is drawn from *which line* an error landed on, so with no errors
            # to place, "no error on line 5" would read as "`trivial` closed the
            # goal" -- turning an unusable answer into a confident refusal.
            return None, "Lean could not be checked (it failed without diagnostics Hardy could read)."
        placed = {item.line for item in errors if item.line is not None}
        # An error Lean could not place counts against the declaration and never
        # in a probe's favour: "no error on that line" must mean the tactic
        # closed the goal, not that Hardy could not tell where the error was.
        unplaced = any(item.line is None for item in errors)
        first_probe = 3
        declaration_line = first_probe + len(self.PROBES) + 1
        # The declaration is read first, and an error Lean could not place is
        # read against it. A statement Lean will not accept fails on every probe
        # line too, and "every tactic failed" would otherwise be reported back as
        # a clean assumption.
        if unplaced or declaration_line in placed:
            return (
                f"Lean does not accept this statement, so nothing can be built on it:\n"
                f"{result.output}\n"
                f"Fix the statement and request it again.",
                "",
            )
        for index, tactic in enumerate(self.PROBES):
            if first_probe + index in placed:
                continue
            proof = _probe_suggestion(result, first_probe + index) or f"by {tactic}"
            return (
                f"Lean proves this outright, so it is a theorem, not an assumption:\n"
                f"  theorem {head.strip().removeprefix('axiom').strip()} : "
                f"{statement} := {proof}\n"
                f"Save it with save_lean instead of assuming it.",
                "",
            )
        return None, ""

    def _lean_path(self, space: LeanWorkspace | None = None) -> str:
        """Where Lean looks for a module, nearest first.

        The problem's own build, then the project's shared library, then the
        user's, then whatever Mathlib's environment already provides. `lake
        env` augments an inherited `LEAN_PATH` rather than replacing it, which
        is what lets these sit beside Mathlib's own package directories.

        Joined with `os.pathsep` and not with a colon: `LEAN_PATH` is separated
        by a semicolon on Windows, which Hardy supports, and a colon there
        would hand Lean one unresolvable path made of two real ones -- and
        `C:\\...` would be split apart into the bargain.

        `space` names a workspace other than this session's own, which is how
        the audit probe reaches a staged tree without losing the shared
        libraries the tree it is grading was compiled against.
        """
        entries = [
            (space or self.lean_workspace).lean_path(),
            *(str(build) for _, build in self.shared_roots),
        ]
        return os.pathsep.join(entries)

    def _compile_module(
        self, module: str, source_root: Path, build_root: Path, source_file: Path
    ) -> tuple[bool, str]:
        """Build one workspace module, for `LeanWorkspace` to sequence.

        Serves the problem's tree and every shared tree alike, so a library the
        user brought is compiled by exactly the path the problem's own modules
        take rather than by a second, subtly different one.
        """
        result = self.lean.compile_module(
            source_root, build_root, source_file, lean_path=self._compile_path(build_root)
        )
        return result.ok, result.output

    def _compile_path(self, build_root: Path) -> str:
        """`LEAN_PATH` for compiling one module into `build_root`.

        The build being written to comes first, then the shared builds. Without
        the shared entries a problem module that imports `CommAlg` elaborates
        fine under `check_lean` -- which searches the full path -- and then
        fails to compile on save, which is the worst shape this bug can take:
        the workspace looks green right up to the moment it is written to.
        """
        builds = [build for _, build in self.shared_roots]
        # A shared library sees only the libraries FURTHER OUT than itself.
        # `shared_roots` is in resolution order -- the project's `.hardy` first,
        # the user's `~/.hardy` after it -- so a project library may rest on the
        # personal one and not the reverse. Handing the project build to the
        # personal library's compile let a global olean be produced against
        # whichever project happened to run last: the same
        # `~/.hardy/lean/CommAlg.olean` would then mean different things in
        # different checkouts, and the first project to open would import the
        # other's build without either saying so.
        visible = builds[builds.index(build_root) + 1 :] if build_root in builds else builds
        entries = [str(build_root), *(str(build) for build in visible)]
        return os.pathsep.join(entries)

    def _modules_under(self, source: Path) -> Iterator[tuple[str, Path]]:
        """Every Lean module a shared source tree offers, by name.

        Through `files_under`, which refuses a symlink anywhere in the tree, so
        this cannot advertise a module `build_shared` will not build: that pass
        reads the same tree through the same walk and reports the refusal in
        `unbuildable`, which is where the user is told what is wrong. Yielding
        the link's target here instead would put a host file's name in the
        model's listing and in the shared digest, for a module nothing can
        compile -- the container proven, the thing inside it not.
        """
        try:
            found = files_under(source, ".lean")
        except (LayoutError, OSError):
            return
        for relative in found:
            yield module_name(relative), Path(source, *relative.parts)

    def _shared_digest(self) -> str:
        """What every shared Lean source this session can import currently is.

        Mixed into `self._environment`, which keys the olean cache and stamps
        each audit verdict. Without it, editing `.hardy/lean/CommAlg.lean`
        leaves a problem module that imports it resting on an olean built
        against the old text, *and* leaves that module's stored verdict --
        whose signature would not have moved either -- reading as current. A
        verdict that outlives the sources it was computed against is precisely
        the failure the axiom audit exists to prevent, so the shared sources
        are part of the identity or the identity is a lie.

        Contents rather than size and mtime, unlike the Mathlib oleans in
        `_external_stamp`: a shared library is small hand-written source, and a
        digest over it cannot be defeated by an editor that preserves a
        timestamp.
        """
        if not self.shared_roots:
            # Empty, not a digest of nothing: `_shared_identity` reads it as
            # "there is no shared library here", so a project that has none
            # keeps exactly the identity it had before this feature existed
            # rather than having its whole olean cache and every stored verdict
            # invalidated by an upgrade that changed nothing it can see.
            return ""
        digest = hashlib.sha256()
        for source, _ in self.shared_roots:
            # The root itself, so adding a second shared tree is a change even
            # if its files happen to hash to what the first tree's did.
            digest.update(str(source).encode("utf-8"))
            for name, path in self._modules_under(source):
                digest.update(b"\0")
                digest.update(name.encode("utf-8"))
                try:
                    # `read_bytes`, not `path.read_bytes`. `files_under` proved
                    # the whole tree a moment ago, and re-proving the one file
                    # at the moment it is read is what `sources()` does too --
                    # this digest stamps every audit verdict the session
                    # stores, so it may not be taken over a file that is not
                    # the one the walk found.
                    content = read_bytes(source, path.relative_to(source))
                    digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
                except OSError:
                    # An unreadable file makes the identity coarser, not the
                    # session dead. Constant rather than timestamped: a value
                    # that moved on its own would rebuild the whole tree every
                    # turn, which is a worse failure than a coarse digest.
                    digest.update(b"unreadable")
        return digest.hexdigest()

    def _discover_shared(self) -> tuple[tuple[Path, Path], ...]:
        """The shared source and build trees that exist, in resolution order.

        Through `Layout` rather than by rejoining the names here, so there is
        one owner of where a project's shared Lean lives and this cannot drift
        from the directory the rest of Hardy creates and ignores.
        """
        shared = Layout(root=self.root, slug=self.workspace.name)
        return tuple(
            (source, build)
            for source, build in (
                (shared.shared_lean, shared.shared_build),
                (global_lean(), global_build()),
            )
            if source.is_dir()
        )

    def _shared_workspaces(self) -> tuple[LeanWorkspace, ...]:
        return tuple(
            LeanWorkspace(
                source,
                build,
                self._compile_module,
                environment=self._toolchain,
                external=self._external_stamp,
            )
            for source, build in self.shared_roots
        )

    def _adopt_shared(self) -> None:
        """Notice a shared tree that was not there when the session opened.

        Neither reserved directory is created by `Layout.ensure`, so the
        ordinary way a project acquires one is a user making it by hand -- very
        possibly while a session is already running, right after being told the
        directory exists. Fixed at startup, `shared_roots` would answer "no
        such library" until Hardy was restarted, for a library sitting in plain
        sight. Two `is_dir` calls is the whole cost of not doing that.
        """
        found = self._discover_shared()
        if found == self.shared_roots:
            return
        self.shared_roots = found
        self._shared_spaces = self._shared_workspaces()

    def _shared_identity(self, digest: str) -> str:
        """The build and audit identity `digest` implies."""
        return f"{self._toolchain}\0shared:{digest}" if digest else self._toolchain

    def _refresh_shared_identity(self) -> None:
        """Move the build and audit identity if a shared source has moved.

        Cheap enough to run before anything that reads a signature, which is
        the point: a user edits `.hardy/lean` in their own editor while a
        session is open, and an identity fixed at startup would let the olean
        cache and every stored verdict go on describing the file they used to
        be about. `_shared_spaces` are deliberately left alone -- their own
        recursive source digests already catch their own edits.
        """
        self._adopt_shared()
        digest = self._shared_digest()
        if digest == self._shared_stamp:
            return
        self._shared_stamp = digest
        self._environment = self._shared_identity(digest)
        self.lean_workspace.rebind_environment(self._environment)

    def build_shared(self) -> None:
        """Compile the libraries this project imports but did not author.

        A directory on `LEAN_PATH` is not a library. `import CommAlg` resolves
        against `CommAlg.olean`, and nothing creates that unless something
        compiles `CommAlg.lean` -- so without this pass the advertised import
        simply fails, and the reserved directory is decoration.

        Run before each Lean invocation rather than once at startup, because a
        shared tree is the user's own and changes underneath a live session.
        The same pass moves `self._environment`, so the edit that needs a
        rebuild is the edit that expires the verdicts resting on it.

        A shared library that will not build is reported, never raised: it is
        someone else's code, the problem's own tree may not import it at all,
        and a session that died on a stranger's syntax error would be a worse
        answer than one that says what happened.
        """
        self._refresh_shared_identity()
        failures: list[str] = []
        # Built in reverse resolution order -- the user's personal library
        # before the project's. A project library may reasonably rest on the
        # personal one; the reverse cannot, since a personal library is shared
        # across projects and can name none of them. Built the other way round,
        # such an import would fail on the first pass and succeed only on the
        # second, which reads as a flaky build rather than an ordering bug.
        for space in reversed(self._shared_spaces):
            try:
                # Before anything is built, and on every pass. A module deleted
                # or renamed in the user's own editor leaves its olean on
                # `LEAN_PATH`, so a problem could import a name whose source is
                # gone and save an AUDITED theorem resting on it. Reconciling
                # here is what makes the shared digest recorded beside that
                # save describe a build made only from files that exist.
                space.prune_orphans()
                sources = space.sources()
                failure = space.build_modules(tuple(sources)) if sources else None
            except ImportCycle as error:
                failure = BuildFailure(module=str(space.root), output=str(error))
            except (LayoutError, OSError) as error:
                # A shared tree that cannot be read is a fact to report, not a
                # traceback out of a tool call the model asked for.
                failure = BuildFailure(module=str(space.root), output=str(error))
            if failure is not None:
                failures.append(f"{failure.module}: {failure.output}")
        self._shared_failures = tuple(failures)
        self._note_shared()

    def shadowed_modules(self) -> dict[str, Path]:
        """Shared modules a problem module answers to instead, by name.

        Resolution order already decides which one Lean loads. This says so out
        loud: which file a theorem rests on is not a detail a session may leave
        implicit, and a reader of the record who cannot tell `CommAlg` from
        `CommAlg` has no way to reproduce the proof.
        """
        mine = set(self.lean_workspace.sources())
        found: dict[str, Path] = {}
        for source_root, _ in self.shared_roots:
            for name, path in self._modules_under(source_root):
                if name in mine and name not in found:
                    found[name] = path
        return found

    def _shared_listing(self, shadowed: dict[str, Path]) -> dict[str, Any]:
        """What the model needs to know about libraries it did not author.

        An import nobody is told about is not an import: the model cannot ask
        for `CommAlg` unless something says `CommAlg` is there. A name the
        problem's own tree already uses is reported as shadowed rather than as
        available, because Lean resolves the problem's module and offering the
        shared one here would advertise an import that silently means something
        else.
        """
        available: dict[str, str] = {}
        for source, _ in self.shared_roots:
            for name, path in self._modules_under(source):
                if name not in shadowed and name not in available:
                    available[name] = str(path)
        return {
            "roots": [str(source) for source, _ in self.shared_roots],
            "modules": available,
            "shadowed": {name: str(path) for name, path in shadowed.items()},
            "unbuildable": list(self._shared_failures),
        }

    def _note_shared(self, shadowed: dict[str, Path] | None = None) -> None:
        """Put a shadowed or unbuildable shared module in the transcript.

        A `shadowed_modules` only its own unit test ever calls reports nothing
        to anybody. The listing tells the model; this tells the record, which
        is what a reader has afterwards and the only place a collision that was
        resolved silently could ever be recovered from. Written when what is
        true changes, not on every Lean call.
        """
        if shadowed is None:
            shadowed = self.shadowed_modules()
        observed = {
            "shadowed": {name: str(path) for name, path in shadowed.items()},
            "unbuildable": list(self._shared_failures),
        }
        if observed == self._shared_observed:
            return
        self._shared_observed = observed
        # Nothing to say when nothing is wrong -- including when a collision
        # that was reported has since been resolved, which the comparison above
        # has already recorded as the new truth.
        if not shadowed and not self._shared_failures:
            return
        self._record({
            "type": "shared_library",
            "roots": [str(source) for source, _ in self.shared_roots],
            **observed,
        })

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
        # The shared builds first, in resolution order, then Lake's own answer.
        # A module imported from `.hardy/lean` is external to the problem's
        # workspace and Lake has never heard of it, so searching Lake alone
        # stamped every such import `missing` -- a stamp that never moves, for
        # a file that does.
        for directory in (*(build for _, build in self.shared_roots), *self._lean_search_path()):
            candidate = directory / relative
            try:
                if candidate.is_file():
                    found = candidate.stat()
                    stamp = f"{candidate}:{found.st_size}:{found.st_mtime_ns}"
                    break
            except OSError:
                continue
        return stamp

    def _check_lean(self, path: str, source: str) -> ToolResult:
        try:
            safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        # Before the first Lean call of this check: an `import CommAlg` that
        # names a shared library resolves against an olean, and nothing builds
        # that olean but this.
        self.build_shared()
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
        gate = self._result_gate(source) or self._documentation_gate(source)
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
            result = self.lean.compile_module(
                source_root, build_root, source_file, lean_path=self._compile_path(build_root)
            )
            seen[module] = result
            return result.ok, result.output

        # Before the shadow is staged, so the staged build is keyed on the
        # identity the shared sources currently have. Staging first would copy
        # `_environment` into the shadow while it still named the old shared
        # text, and the save would be committed under a signature that was
        # already false when it was computed.
        self.build_shared()
        # Before staging: what the tree holds now is what a registered name may
        # be judged to have vanished *from*.
        committed = self.lean_workspace.sources()
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
            lost = self._missing_registered_names(shadow.sources(), committed)
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
        return f"\n\n{_reportability(owed)}\n{completion.describe(owed)}"

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
        # On the status, not on the presence of a finding. A hole grades `open`
        # and is kept: it is an unfinished proof, not an unacceptable one, and
        # the refusal for it happens where a claim is made. An unapproved axiom
        # still rejects, and a save carrying both is refused for the axiom --
        # the half the model can do something about.
        if verdict.status == "rejected":
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
            # Reached only for a forbidden axiom that is not a hole. `FORBIDDEN`
            # holds exactly `sorryAx` today, so `classify` never produces one --
            # but deleting the branch would lose the message the moment it grows,
            # and leave `verdict.forbidden[0]` read off a branch nobody wrote.
            return ToolResult(
                False,
                f"the axiom audit refused this save: {audit.describe(verdict)}. "
                f"{list(audit.dependents(reports, verdict.forbidden[0]))} depend on "
                "something no human may approve.",
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
                env={"LEAN_PATH": self._lean_path(space)},
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

    def _missing_registered_names(
        self, sources: dict[str, str], before: dict[str, str]
    ) -> list[str]:
        """Registered formal names that this save would remove from the tree.

        An approved assumption is exempt. This guard exists so a *workspace
        declaration* cannot vanish while the registry still points at it, and an
        axiom reached through an import was never a workspace declaration --
        `request_assumption` registers the name a human approved, nothing writes
        it into a file, and demanding one refused every later save with no tool
        to undo it. The exemption is deliberately narrow: a registered theorem
        that disappears is still caught.

        Judged against `before` as well as against the staged tree, because a
        name is now also registered *ahead* of the declaration it maps: a
        `theorem` may only be stated once `record_name` has mapped it, so the
        order is register and then save, and in between the registry names
        something the tree does not have yet. Asked only of the staged tree,
        that refused every save in between -- including the save that would
        have introduced the theorem in another file. A name that never existed
        has not vanished.
        """
        approved = self._approved_assumptions()
        return [
            item["formal_name"]
            for item in self.state["names"]
            if item["formal_name"] not in approved
            and self._resolves(item["formal_name"], before)
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

        Read through the guard, like every other file in a project. `.build/`
        is gitignored but not untrackable -- a repository that ships
        `.build/tex/writeup.aux` as a link to a file full of `\\newlabel` lines
        would have had Hardy count those labels as ones LaTeX created here,
        which is the completion gate released by a file nobody in this project
        wrote. Absent is still nothing documented yet; a link is a refusal.
        """
        try:
            written = read_text(
                self.workspace, f"{BUILD_DIR_TEX}/writeup.aux", errors="replace"
            )
        except (FileNotFoundError, NotADirectoryError):
            return set()
        return set(NEWLABEL.findall(written))

    def _saved_theorems(self) -> set[str]:
        found: set[str] = set()
        for source in self.lean_workspace.sources().values():
            found.update(declarations(source)["theorem"])
        return found

    def _theorem_statements(self) -> dict[str, str]:
        """Every saved theorem, with the exact statement Lean was given.

        Theorems only. A `lemma` is scaffolding and owes nothing, which is the
        same line `_saved_theorems` draws and has to stay the same line: a
        writeup gate that demanded a paragraph for every helper would make
        splitting a proof into helpers the expensive way to work.

        Open ones included: a report may name one, and the document has to
        carry it on the same terms as any other.
        """
        found: dict[str, str] = {}
        for source in self.lean_workspace.sources().values():
            theorems = set(declarations(source)["theorem"])
            found.update(
                {name: text for name, text in statements(source).items() if name in theorems}
            )
        return found

    def _saved_statements(self) -> dict[str, str]:
        """The closed ones, which is what the writeup obligations are about.

        A theorem whose proof still has a hole is not a result yet; demanding
        that the document carry it would ask for a paragraph asserting
        something nobody has proved, and would block the next save behind it.
        Its obligation is that it is open, and the writeup obligations attach
        the moment the hole closes -- or at a report that names it, which asks
        for the carrying directly.
        """
        opened = self._open_theorems()
        return {
            name: text
            for name, text in self._theorem_statements().items()
            if name not in opened
        }

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
        """The writeup tree as text, keyed by workspace-relative path.

        Discovered and read through the layout guard, exactly as
        `LeanWorkspace.sources` is. `rglob` reports a symlinked `tex/leak.tex`
        as an ordinary fragment and `read_text` follows it without a word, so
        a repository could put a host file into the writeup: its text answered
        the statement obligations, it was hashed into `tex_signature` as the
        project's own, and `read_file` handed it back verbatim. A symlink under
        `tex/` is refused rather than skipped, for `files_under`'s reason --
        leaving it out silently would make the tree Hardy judges differ from
        the tree a reader sees.
        """
        if not self.tex_root.is_dir():
            return {}
        found: dict[str, str] = {}
        for relative in files_under(self.tex_root, ".tex"):
            try:
                found[relative.as_posix()] = read_text(
                    self.tex_root, relative, errors="replace"
                )
            except OSError:
                # Unreadable is not empty, but a listing that raises here would
                # take the turn with it. The obligation it leaves standing is
                # the safe direction: the file cannot be shown to say anything.
                # A `LayoutError` is deliberately NOT swallowed here: that one
                # is a refusal about what the tree is, not a file that happens
                # to be unreadable.
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
            # Open theorems owe nothing, but they are still saved theorems: they
            # back a `\begin{theorem}` the document asserts, and they decide
            # whether a leaf name is unambiguous. Left out, a document asserting
            # one read as backed by nothing.
            saved=self._theorem_statements(),
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
        # `_audit_gaps` is asked only about closed theorems. An open one has a
        # current audit record -- being current is how Hardy knows it is open --
        # so it has no gap to report, and reporting it would say the same thing
        # the `open` obligation beside it already says.
        opened = self._open_theorems()
        holes = tuple(
            completion.Obligation("open", name, "still open -- rests on a hole")
            for name in sorted(opened)
        )
        return (
            *holes,
            *shared,
            *self._audit_gaps(self._saved_theorems() - opened),
            *self._stale_writeup(),
            *owed,
        )

    def goal(self) -> str:
        """What the user said this session is for, or "".

        Additive and optional, so `schema_version` stays 2: that version exists
        to refuse records this build cannot read, and a string it can ignore is
        not one of those. A record written before goals existed loads with "".

        Read at every axiom approval and printed on the writeup. Hardy makes no
        judgment about it -- the claim is narrow and is the whole point: a human
        is never asked to approve an axiom with the assignment off-screen. The
        session that approved `no_simple_nonabelian_composite_orders`, which is
        the assignment itself for 28 of the orders, spent 170 seconds reading a
        well-argued paragraph with nothing beside it to compare against.
        """
        return str(self.state.get("goal") or "")

    def set_goal(self, text: str) -> None:
        self.state["goal"] = text.strip()
        self._save_state()

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

    def _result_gate(self, source: str) -> str | None:
        """`theorem` is reserved to results a human will be shown.

        The writeup ratchet turns on the keyword: a `theorem` owes a paragraph
        and a `lemma` owes nothing, so that splitting a proof into helpers is
        the cheap way to work. In practice the model states every intermediate
        step as a `theorem`, the exemption never fires, and the ratchet stops a
        development that has done nothing wrong. Asking for `lemma` in the
        prompt did not change that, and a rule a model can talk its way past is
        not a rule.

        So the keyword is not left to taste. A result is something `record_name`
        has already mapped to a place in the document -- which costs a
        `latex_name` and a description, and is a promise the ratchet then
        collects on -- and everything else is a `lemma`, which is free.

        Only what this save *introduces*, like the ratchet beside it, so a
        workspace written before this rule can still be repaired, restated, or
        deleted.
        """
        registered = {item["formal_name"] for item in self.state["names"]}
        existing = self._saved_theorems()
        # A bare entry covers the qualified declaration carrying that leaf, which
        # is the rule `_resolves` and `completion.outstanding` already read this
        # registry by: `record_name` maps `one` and the file declares
        # `Hardy.one`. An exact match here would have made the reservation of
        # `theorem` mean something narrower than every other reader of the same
        # mapping, and demanded a second entry for a name already recorded.
        # Whether that leaf is unambiguous is not this gate's question --
        # `outstanding` asks it, and answers by refusing to count an ambiguous
        # one as documented. What is asked here is only whether the declaration
        # is a result somebody registered or scaffolding that should be a lemma.
        unregistered = [
            name
            for name in declarations(source)["theorem"]
            if name not in existing
            and name not in registered
            and name.rsplit(".", 1)[-1] not in registered
        ]
        if not unregistered:
            return None
        return (
            f"`{unregistered[0]}` is not a registered result, so it may not be stated as a "
            "`theorem`. State it as a `lemma` if it is scaffolding or an intermediate step -- "
            "a lemma owes no writeup and is free to save. If it is a result you will write up, "
            "call record_name for it first."
        )

    def _documentation_gate(self, source: str) -> str | None:
        """The catch-up ratchet: write up the last theorem before the next.

        Refuses only when the tree already owes a writeup *and* this save would
        add a theorem it does not already contain. The first condition alone
        would trap the session: a model could no longer repair, restate, or
        delete the very theorem blocking it. The second alone would let one
        file absorb any number of undocumented claims.

        `open` obligations are not counted. They are not a writeup this save is
        running ahead of -- an open theorem owes no writeup at all yet -- and
        counting them would stop a development the moment it held one
        unfinished result, which is the state a long proof is in for most of
        its life.
        """
        owed = [item for item in self._obligations() if item.kind != "open"]
        if owed and self._stale_only_from_holes():
            # A banner out of date only because a theorem opened is not a
            # writeup this save is running ahead of. It is still reported --
            # the compiled PDF counts an open theorem out of "machine-checked",
            # so one compiled before the hole appeared overstates -- but
            # counting it here made every second skeleton wait on a LaTeX
            # recompile that no obligation about a closed theorem asked for.
            stale = self._stale_writeup()
            owed = [item for item in owed if item not in stale]
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
        return self.latex.check(source, path=relative, tree=self.tex_root, stamp=self._stamp())

    def _save_latex(self, path: str, source: str) -> ToolResult:
        resolved = self._tex_path(path)
        if isinstance(resolved, ToolResult):
            return resolved
        # The normalised path, not the argument: `sections\one.tex` names one
        # file to `_tex_target` and, on a platform where a backslash is an
        # ordinary character, a different one to the compiler -- so the root
        # would be checked against the old fragment and then overwritten by a
        # candidate nothing had compiled.
        relative, _ = resolved

        def _write() -> None:
            # `guard_for`, not a bare write to `target`. `_tex_path` proves the
            # NAME is a relative, dot-free, colon-free path; it does not and
            # cannot say where the directories of that name lead.
            # `tex/sections -> $HOME` passed every one of its checks, and
            # `save_latex("sections/one.tex")` then wrote a file of the model's
            # choosing into the user's home directory. The guard proves each
            # component against the one above it, at the moment of the write,
            # and refuses a symlinked leaf outright -- which is also what stops
            # `writeup.tex -> ~/.bashrc`.
            try:
                guard, name = guard_for(self.tex_root, relative, create=True)
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(source.rstrip() + "\n")
            except OSError as error:
                # Raised on, never swallowed: the whole point of running here
                # is that `check` publishes nothing when this fails. Wrapped
                # so the answer names the save -- a directory sitting where
                # the file should be, a full disk -- instead of reading as a
                # compiler failure. A `LayoutError` needs no wrapper: it
                # already says which path it refused and why.
                raise WriteupNotSaved(f"{relative} could not be saved: {error}") from None

        # Handed to `check` rather than run after it. `check` publishes
        # `writeup.pdf` and `.build/tex/writeup.aux` from the candidate, and
        # doing that first meant a write the guard refused left a committed PDF
        # and a set of labels describing source that is not on disk, while the
        # unchanged `tex_signature` reported the writeup as freshly compiled.
        # The save is now the last thing that can fail before anything is
        # published, so a failure leaves the workspace as it was.
        try:
            result = self.latex.check(
                source,
                path=relative,
                tree=self.tex_root,
                output_dir=self.workspace,
                aux_dir=self.workspace / BUILD_DIR_TEX,
                commit=_write,
                stamp=self._stamp(),
            )
        except WriteupNotSaved as error:
            return ToolResult(False, str(error), source)
        if not result.ok:
            return result
        # Stamped after the write, on a compile that succeeded, and only when
        # what was compiled is the writeup itself. Saving a fragment the root
        # does not include yet is checked through a probe document, which says
        # the fragment is sound and nothing about the writeup -- stamping that
        # would mark the tree established on the strength of a document nobody
        # will read.
        if compiles_document(self._tex_root_source(), relative):
            self._stamp_writeup()
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
        # Hardy first, pdfTeX second. The graded run's last save returned 4,879
        # bytes, of which the part that mattered -- "Still missing labels for
        # registered names" -- was the last line, under a wall of font paths.
        #
        # The log is kept whole. Filtering it on success was tried and withdrawn:
        # a filter cannot know which of pdfTeX's lines a caller needed, and it
        # loses the continuation lines of a multi-line warning, `Overfull` boxes,
        # `No file ...` notices, rerun instructions, and any `\typeout` a model
        # wrote to ask the engine a question. Reordering costs nothing and fixes
        # the same problem -- and `check` tail-truncates its output, so with the
        # note appended a long enough log could push it out entirely.
        if note:
            return ToolResult(True, f"Saved.{note}\n\n{result.output}", source)
        return result

    def _tex_root_source(self) -> str:
        """The saved root document's text, or empty when there is none.

        Through the guard, because `writeup.tex` is versioned and a clone may
        ship it as a link: read with `Path.read_text` it was a host file whose
        `\\input` lines decided whether a save counted as compiling the writeup,
        and whose whole text was then handed to LaTeX as this project's root.
        """
        root = self.tex_root / ROOT_DOCUMENT
        if not root.is_file():
            return ""
        return read_text(self.tex_root, ROOT_DOCUMENT)

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
        # Before the signatures below are computed: a shared source edited
        # since the last Lean call has already invalidated every verdict, and
        # a listing that reported them against the stale identity would answer
        # `clean` for a module whose inputs have moved.
        self._refresh_shared_identity()
        shadowed = self.shadowed_modules()
        self._note_shared(shadowed)
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
        # `files_under`, not `rglob`: discovery is a read. A symlinked
        # `tex/leak.tex` was listed here as one of the project's own files,
        # which is the model being told to go and read a host file.
        tex = (
            sorted(relative.as_posix() for relative in files_under(self.tex_root, ".tex"))
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
            # The Lean this project may import but did not author, and which of
            # its own modules answer to a shared name instead. Reported rather
            # than left implicit: a model that cannot see the library cannot
            # import it, and one that cannot see a collision would cite a
            # theorem out of the wrong file.
            "shared": self._shared_listing(shadowed),
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

    def _read_file(self, path: str, start_line: int = 1) -> ToolResult:
        """One workspace file's text, bounded, and proven to BE that file.

        Reproduced, and it is why every read in this module now goes through
        the guard: a cloned problem shipping `tex/leak.tex -> ~/.ssh/id_rsa`
        made `read_file` return the key, because `Path.read_text` follows a
        link without a word and `_resolve` only ever proved the NAME was a
        workspace path. `read_file` puts whatever it returns straight into the
        model's context, so that is any file the user can read handed to the
        model provider by a repository they merely opened. The Lean half was
        the same hole with the same one line at the end of it.

        That same sentence -- whatever this returns goes straight into the
        model's context -- is why it is bounded. It was the one tool result
        with no limit on it, which held only because workspace files are
        model-written and small; a bounded context that is bounded except for
        one tool is not bounded. Head truncation, unlike Lean's: a file read
        wants the top, where the imports and the statement are, and an error
        wants the bottom.

        `start_line` is the answer to "then how do I see the rest", and the
        truncation notice names it. Without it the bound would be a wall
        rather than a page, and a model that cannot reach the end of a file it
        wrote is worse off than one handed the whole thing.
        """
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, kind, relative = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        if start_line < 1:
            return ToolResult(False, f"start_line is 1-based; got {start_line}")
        try:
            if kind == "lean":
                found = self.lean_workspace.read(PurePosixPath(relative))
                if found is None:
                    return ToolResult(False, f"no such workspace file: {path}")
            else:
                found = read_text(self.tex_root, relative)
        except OSError as error:
            # A `LayoutError` is left to the dispatcher, which reports it as
            # the refusal it is; this is for a file that is simply unreadable.
            return ToolResult(False, f"{path} could not be read: {error}")
        return self._bounded_file(path, found, start_line)

    @staticmethod
    def _bounded_file(path: str, source: str, start_line: int) -> ToolResult:
        """A file's text cut to fit, with a note saying so when it was cut.

        The note comes first and not last. A model reading a fragment from the
        top and stopping at the point it has what it wants would never reach a
        trailing notice, and the whole purpose of the notice is that it be
        read before the text is believed to be the file.

        Nothing is prepended to a whole small file: the common read stays
        exactly the bytes on disk, so a model quoting what it was handed
        quotes the file.
        """
        observation = truncate(source, keep="head", start_line=start_line)
        if not observation.truncated and start_line == 1:
            return ToolResult(True, observation.text)
        if not observation.text and start_line > observation.total_lines:
            return ToolResult(
                False,
                f"{path} has {observation.total_lines} lines; start_line={start_line} is past the end",
            )
        rest = (
            f" Call read_file again with start_line={observation.next_line} for the rest."
            if observation.next_line is not None
            else ""
        )
        note = f"{path}: {observation.summary}.{rest}"
        return ToolResult(True, f"{note}\n\n{observation.text}")

    def _delete_file(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, kind, _ = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        if kind == "tex":
            return self._delete_tex(target, path)
        relative = safe_relative(str(path).replace("\\", "/"))
        module = module_name(relative)
        committed = self.lean_workspace.sources()
        importers = dependents(committed, module)
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
            lost = self._missing_registered_names(shadow.sources(), committed)
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

        Guarded, like every other write into the writeup tree. Unlinking
        followed `tex/sections -> $HOME` all the way to a real file in the
        user's home directory, and the restore below then wrote a file back
        there; both go through the same proven chain now.
        """
        if target.resolve() == (self.tex_root / ROOT_DOCUMENT).resolve():
            return ToolResult(False, f"{ROOT_DOCUMENT} is the root document and cannot be deleted")
        relative = target.relative_to(self.tex_root).as_posix()
        guard, name = guard_for(self.tex_root, relative)
        kept = read_text(self.tex_root, relative)
        guard.unlink(name)
        root = self.tex_root / ROOT_DOCUMENT
        if root.is_file():
            checked = self.latex.check(
                self._tex_root_source(),
                tree=self.tex_root,
                output_dir=self.workspace,
                aux_dir=self.workspace / BUILD_DIR_TEX,
                # This path publishes writeup.pdf too, and re-stamps the
                # signature afterwards. Without the banner, deleting a fragment
                # silently replaced a stamped PDF with an unstamped one and
                # recorded it as current.
                stamp=self._stamp(),
            )
            if not checked.ok:
                guard.mkdir()
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(kept)
                return ToolResult(False, f"the writeup no longer compiles without {path}, so it was kept:\n{checked.output}")
            # This compile is as good as a save's, and the tree it compiled is
            # the tree on disk -- so it is stamped like one. Without this a
            # deletion left a freshly compiled writeup reading as stale, and
            # the only way out was a save that changed nothing.
            self._stamp_writeup()
        return ToolResult(True, f"deleted {path}")

    def _resolve(self, path: str) -> tuple[Path, str, str] | ToolResult:
        """Where a tool path lives: the Lean tree or the writeup tree.

        The tree-relative path comes back beside the absolute one because that
        is what a guarded read or write takes -- a guard is given a tree and a
        name inside it, never a path to open, and rebuilding the relative half
        at each call site is how one of them came to skip the guard entirely.
        """
        cleaned = str(path).replace("\\", "/")
        if cleaned.endswith(".lean"):
            try:
                relative = safe_relative(cleaned)
            except WorkspacePathError as error:
                return ToolResult(False, str(error))
            return self.lean_workspace.root / relative, "lean", relative.as_posix()
        if cleaned.endswith(".tex"):
            resolved = self._tex_path(cleaned)
            if isinstance(resolved, ToolResult):
                return resolved
            relative, target = resolved
            return target, "tex", relative
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
        if name in SEARCH_TOOL_NAMES:
            return self._search_tool(name, arguments)
        if name == "read_workspace":
            return ToolResult(True, json.dumps(self._workspace_listing(), ensure_ascii=False))
        if name == "read_file":
            return self._read_file(str(arguments["path"]), int(arguments.get("start_line", 1) or 1))
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
            # Both gates run before `confirm`. Nobody should be asked to approve
            # a statement Hardy has not read, and nobody should be asked at all
            # about one that could never be declared or that Lean proves itself.
            refusal = self._assumption_shape(proposal["formal_name"], proposal["lean_statement"])
            if refusal is not None:
                return ToolResult(False, refusal)
            # Built once and reused: the text elaborated, the text approved, and
            # the text the model is told to write are one string, which is the
            # whole point.
            declaration = f"axiom {proposal['formal_name']} : {proposal['lean_statement'].strip()}"
            refusal, caveat = self._assumption_probe(declaration)
            if refusal is not None:
                return ToolResult(False, refusal)
            # Carried to the prompt rather than swallowed: a human approving an
            # unchecked statement is owed the word "unchecked".
            proposal["checked"] = caveat or "Lean elaborated this statement and could not prove it."
            proposal["goal"] = self.goal()
            if not self.confirm(proposal):
                return ToolResult(False, "The user declined this assumption. Do not use it.")
            # `checked` and `goal` describe this one request, not the
            # assumption, and have no business in the durable record.
            record = {key: value for key, value in proposal.items() if key not in {"checked", "goal"}}
            record["status"] = "user-approved"
            if not any(item["formal_name"] == record["formal_name"] for item in self.state["assumptions"]):
                self.state["assumptions"].append(record)
                mapping = {"formal_name": record["formal_name"], "latex_name": record["latex_name"], "description": record["informal_statement"]}
                self.state["names"].append(mapping)
                self._save_state()
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
        saved = self._theorem_statements()
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
        #
        # Except that a claimed theorem is still open, which is what this grades
        # rather than what it refuses. A development that closed nine lemmas and
        # left a hole in the tenth has established something real and had
        # nowhere to say it: the alternatives were to claim a proof it does not
        # have, or to say nothing.
        blocking = [
            item
            for item in owed
            if item.kind != "open"
            and (
                not item.subject
                or item.subject in resolved
                or item.kind in {"appendix", "assumption"}
            )
        ]
        # What an open theorem owes the *document*, asked here rather than
        # standing against the workspace. Partial is not a discount on the
        # writeup: a reader who cannot see the statement of the theorem that is
        # still open cannot tell which half of the work was done.
        opened = sorted(self._open_theorems() & set(resolved))
        if opened:
            blocking += list(
                completion.outstanding(
                    theorems={name: saved[name] for name in opened},
                    registry=self.state["names"],
                    labels=self._labels(),
                    assumptions=self.state["assumptions"],
                    used=self._used_assumptions(),
                    tex=self._tex_sources(),
                    # The whole tree, not the claimed subset: with `A.t` claimed
                    # and `B.t` saved beside it, a subset made the leaf `t` look
                    # unique and accepted one label as documenting a theorem the
                    # document never named.
                    saved=saved,
                )
            )
        if blocking:
            return ToolResult(
                False,
                "this report is refused: the artifacts do not yet back it.\n"
                f"{completion.describe(blocking)}\n"
                "Settle every line above with save_lean, record_name and save_latex, then "
                "report again. Do not tell the user this is finished in the meantime.",
            )
        rested = [
            item
            for item in self.state["assumptions"]
            if str(item["formal_name"]) in self._rests_on(resolved)
        ]
        entry = {
            "theorems": sorted(resolved),
            "summary": summary.strip(),
            "statements": {name: saved[name] for name in sorted(resolved)},
            "assumptions": [str(item["formal_name"]) for item in rested],
            # Computed from the audit records rather than taken from the model,
            # for the same reason every other grade here is.
            # `partial` outranks `modulo`: a proof with a hole in it is not
            # established at all, which is worse news than one established on
            # an axiom a human approved. Saying `clean` here contradicted the
            # sentence beside it, which listed the assumptions in the same
            # breath -- and this is the durable grade, not the sentence.
            "status": "partial" if opened else ("modulo" if rested else "clean"),
            "open": opened,
        }
        self.state.setdefault("reports", []).append(entry)
        self._save_state()
        self._record({"type": "report", **entry})
        # The claimed theorems' own holes are the report's grade, not something
        # outstanding somewhere else, and the message names them already.
        elsewhere = [
            item
            for item in owed
            if item not in blocking and not (item.kind == "open" and item.subject in resolved)
        ]
        return ToolResult(
            True,
            f"Reported {entry['theorems']} as {entry['status']}. Each is saved Lean whose "
            "axioms were audited when it was saved, carries a label the compiler created, "
            "and has its exact statement quoted in the writeup where the reader can check it"
            + (
                f".\nThis is a partial result: {opened} still rest on a hole and are not "
                "proved. Say so wherever you describe this work"
                if opened
                else ""
            )
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

    def _rests_on(self, names: Iterable[str]) -> set[str]:
        """The approved assumptions these declarations actually depend on.

        Read from the per-declaration audit rather than from the workspace as a
        whole. A report naming only the clean theorem was recording every
        assumption anywhere in the tree as its own -- saying "verified modulo
        Sylow" about a result that never touched it, in the durable record, on
        evidence Lean had already given to the contrary.
        """
        wanted = set(names)
        found: set[str] = set()
        approved = self._approved_assumptions()
        for record in self.state.get("audit", {}).values():
            for entry in record.get("declarations", ()):
                if entry.get("name") in wanted:
                    found.update(set(entry.get("axioms", ())) & approved)
        return found

    def _stamp(self) -> str:
        r"""What the document is, printed in the document.

        Every count here is one the obligations already compute; nothing new is
        judged. It appears on every compile, clean or not: a banner that shows up
        only on failure is one a reader learns to read the absence of.

        "Machine-checked" is a saved theorem with no outstanding audit gap, not
        `len(self._saved_theorems())`. That is a textual scan of the sources and
        would call a theorem machine-checked while `_audit_gaps` was
        simultaneously reporting it unestablished. A banner that overstates is
        worse than no banner.

        An open theorem is not machine-checked either, and is the case that
        needs saying twice: its audit record is *current* -- being current is
        how Hardy knows it is open -- so `_audit_gaps` reports nothing about
        it, and counting it would put a proof with a hole in it under the word
        "machine-checked".

        It says nothing about whether a result was *reported*. That is the
        session's own bookkeeping rather than a property of the document, and
        counting it here made every accepted report stale the PDF -- so a second
        report was blocked behind a recompile that changed no source. What a
        reader needs is already here: how much Lean checked, how much was
        assumed, and how much the document asserts on neither footing.
        """
        owed = self._obligations()
        unbacked = sum(1 for item in owed if item.kind == "theorem")
        gaps = {item.subject for item in owed if item.kind == "lean"}
        opened = {item.subject for item in owed if item.kind == "open"}
        checked = len(self._saved_theorems() - gaps - opened)
        assumed = len(self.state["assumptions"])
        parts = [
            f"\\textbf{{Hardy}} --- {checked} theorem{'' if checked == 1 else 's'} "
            f"machine-checked by Lean, {assumed} assumption{'' if assumed == 1 else 's'} "
            f"approved by the user"
        ]
        if unbacked:
            parts.append(
                f"{unbacked} theorem environment{'' if unbacked == 1 else 's'} here "
                f"{'is' if unbacked == 1 else 'are'} backed by neither"
            )
        if opened:
            count = len(opened)
            # Named, not counted. Every other clause here is about the document
            # as a whole, and a reader can act on those knowing nothing else;
            # this one is about particular claims printed on the pages in front
            # of them, and "one theorem is still open" leaves them unable to
            # tell which. Escaped like the goal is: a Lean name carries `_`,
            # which is TeX's subscript, and an unescaped one breaks the
            # document it was added to be honest in.
            listed = ", ".join(escape_tex_text(name) for name in sorted(opened))
            parts.append(
                f"{count} theorem{'' if count == 1 else 's'} here "
                f"{'is' if count == 1 else 'are'} still open ({listed})"
            )
        text = ". ".join(parts) + "."
        goal = self.goal()
        if goal:
            text += f"\\\\ Goal, as stated by the user: {escape_tex_text(goal)}"
        return text

    def _tex_signature(self, open_names: Sequence[str] | None = None) -> str:
        """What the writeup tree hashes to, as a whole.

        `open_names` substitutes an open set for the one the workspace has now,
        which is how `_documentation_gate` asks what this signature *would* be
        if only that had not moved. Everything else is read live.
        """
        digest = hashlib.sha256()
        for path, source in sorted(self._tex_sources().items()):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(source.encode("utf-8"))
            digest.update(b"\0")
        # The banner is part of the published document, so a change to what it
        # would say makes the PDF as stale as an edit to the source does.
        # Without this, report_result succeeded and the published PDF went on
        # saying that no result had been reported, with `_stale_writeup` seeing
        # nothing wrong -- the counts live in the record, which this never read.
        #
        # The stamp's *inputs*, not `self._stamp()`. Calling it recurses:
        # `_stamp` asks for the obligations, `_stale_writeup` is one of them,
        # and it asks for this signature. Everything the banner is computed from
        # is either hashed above (the tex sources) or listed here.
        digest.update(json.dumps(self._stamp_inputs(open_names), sort_keys=True).encode("utf-8"))
        digest.update(b"\0")
        return digest.hexdigest()

    def _stamp_inputs(self, open_names: Sequence[str] | None = None) -> dict[str, Any]:
        """The banner inputs a stale PDF would *overstate*, and only those.

        Not everything `_stamp` reads. The distinction is which direction a
        stale banner errs in. A PDF compiled before the latest `save_lean` says
        one theorem is machine-checked where there are now two -- it understates,
        and the ratchet already forces the writeup to carry that theorem before
        anything is reportable. Counting it here bought nothing and cost a
        recompile after every Lean save.

        An assumption approved after the compile is the other direction: the
        banner goes on saying nothing was assumed while the work rests on
        something, which is the failure this whole design exists to prevent. A
        changed goal is the same -- the document prints the wrong assignment.
        Both stale the writeup.
        """
        return {
            "goal": self.goal(),
            "assumptions": sorted(str(item["formal_name"]) for item in self.state["assumptions"]),
            # A theorem that was closed when the PDF was compiled and has since
            # been reopened is the overstating direction: the banner goes on
            # calling it machine-checked. The signature cannot tell the two
            # directions apart, so closing a hole stales the writeup too -- and
            # costs nothing, because a theorem that has just closed owes the
            # document a label and its statement anyway, so it was going to be
            # recompiled regardless.
            "open": sorted(self._open_theorems()) if open_names is None else sorted(open_names),
        }

    def _stamp_writeup(self) -> None:
        """Record what this compile was made against.

        The open set is stored beside the signature rather than only folded
        into it, because two different questions are asked of the same stamp:
        whether the compiled document still describes this workspace, and --
        by `_documentation_gate` -- whether the open set is the only reason it
        does not.
        """
        self.state["tex_signature"] = self._tex_signature()
        self.state["tex_open"] = sorted(self._open_theorems())
        self._save_state()

    def _stale_only_from_holes(self) -> bool:
        """Whether the compiled writeup is out of date *only* because a theorem
        opened or closed since it was compiled.

        A workspace stamped before this key existed has no `tex_open` to
        substitute, so the recomputed signature will not match and this answers
        no -- the gate then behaves exactly as it did before, which is the safe
        direction for a question whose yes releases a refusal.
        """
        stamped = self.state.get("tex_signature")
        if not stamped:
            return False
        return bool(stamped == self._tex_signature(self.state.get("tex_open", [])))

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

    def _open_declarations(self) -> set[str]:
        """Every saved declaration Lean reported resting on a hole.

        Read from the stored audit records, which are stamped with the build
        signature they were established under, and skipping the ones that no
        longer hold: a stale record is not evidence that a theorem is open, and
        it is not evidence that it is closed either. `_audit_gaps` already
        reports a stale record as its own obligation, so nothing is lost here.
        """
        try:
            signatures = self.lean_workspace.current_signatures()
        except ImportCycle:
            # `_audit_gaps` reports the cycle. Answering "nothing is open" for a
            # tree that does not order would be a claim, and this has none.
            return set()
        found: set[str] = set()
        for module, record in self.state.get("audit", {}).items():
            current = self._still_current(module, record, signatures)
            if not current.get("stale"):
                found.update(audit.open_declarations(current))
        return found

    def _open_theorems(self) -> set[str]:
        """The open declarations that are theorems, which is what is reportable.

        An open `lemma` is reported to the model by the save's own audit note,
        which names every declaration in the rebuilt modules that rests on a
        hole. The obligations answer a narrower question -- what stands between
        this workspace and a report -- and a lemma was never reportable.
        """
        return self._open_declarations() & self._saved_theorems()

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

    def _search_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Search, or the reason this machine cannot.

        The refusal carries `search_detail` verbatim, because that string is
        the actionable part -- "lean_project is not set" is something the user
        can fix, and the model can only relay what it was told.
        """
        if self.search is None:
            # A reason, always. `search is unavailable: ` with nothing after the
            # colon is what a model actually got on one run, and it is worse
            # than no message: it names no fault anyone can fix, and the model
            # went back to guessing module names.
            return ToolResult(
                False,
                "search is unavailable: "
                + (self.search_detail or "no reason was recorded when this session was built"),
            )
        if name == "rank_premises":
            return self.search.rank_premises(
                str(arguments["goal"]), int(arguments.get("limit") or 10)
            )
        if name == "search_declarations":
            return self.search.search_declarations(
                str(arguments["query"]), int(arguments.get("limit") or 10)
            )
        if name == "search_modules":
            return self.search.search_modules(
                str(arguments["query"]), int(arguments.get("limit") or 20)
            )
        names = arguments.get("names") or []
        if not isinstance(names, list):
            return ToolResult(False, "names must be a list of declaration names")
        return self.search.inspect_declarations([str(item) for item in names])

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
                # Stored relative to the problem, because the record is
                # versioned: an absolute path names this machine and is stale
                # the moment the project is cloned or moved. Resolved against
                # the problem directory whenever it is read back.
                self.state["cas_export"] = {
                    "script": self._relative_reference(report.script_path),
                    "notebook": self._relative_reference(report.notebook_path),
                    "reproduces": report.reproduces,
                }
                self._save_state()
                return ToolResult(True, report.model_dump_json())
        except CasError as error:
            return ToolResult(False, str(error))
        return ToolResult(False, f"unknown tool: {name}")

    def _relative_reference(self, path: str) -> str:
        """A path inside this problem, as the record should carry it.

        POSIX separators regardless of platform, so a record written on Windows
        reads the same everywhere. A path that somehow falls outside the
        problem is stored as it came rather than forced: a wrong relative path
        would be worse than an honest absolute one.
        """
        try:
            return Path(path).resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return str(path)

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
                    f"Hardy: {completion.summary(owed)}. {_reportability(owed)}\n"
                    f"{completion.describe(owed)}"
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

    def _transcript_identity(self, length: int | None = None) -> dict[str, Any]:
        """What the transcript is, as far as `length` bytes in.

        A length alone cannot answer this. Checking out a divergent branch
        whose transcript is the same size or longer leaves every arithmetic
        check satisfied against a history that never produced this thread.
        """
        if length is None:
            length = self._transcript_end()
        digest = hashlib.sha256()
        if length and self.transcript_path.exists():
            with self._workspace_guard.open(TRANSCRIPT, "rb") as handle:
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
        return {"transcript_length": length, "transcript_digest": digest.hexdigest()}

    def _carried_thread(self) -> str | None:
        """The provider thread this project may resume, if the record still fits it.

        The thread is bound to the transcript it was recorded against, and the
        binding is checked here rather than trusted. A thread whose transcript
        has been shortened or replaced is dropped: losing a resumable
        conversation is cheap, and answering from context the record cannot
        account for is the thing this project exists to prevent.
        """
        thread = self.local.get(THREAD_KEY)
        if not thread:
            return None
        length = self.local.get("transcript_length")
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            return None
        if length > self._transcript_end():
            return None
        if self._transcript_identity(length)["transcript_digest"] != self.local.get("transcript_digest"):
            return None
        return str(thread)

    def _remember_thread(self) -> None:
        """Record the provider thread, and what the transcript was when it was.

        Written together and never apart: an identity that did not travel with
        the thread would describe some other moment, and a thread with no
        identity cannot be checked at all.
        """
        thread = getattr(self.runtime, "session_id", None)
        if not thread:
            return
        identity = self._transcript_identity()
        if self.local.get(THREAD_KEY) == thread and self.local.get("transcript_length") == identity["transcript_length"]:
            return
        self.local[THREAD_KEY] = thread
        self.local.update(identity)
        self._save_local()

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

        Nothing about it is written to `transcript.jsonl`. It used to append a
        `migration` event there, and `.local/state.json` is gitignored by
        design, so the absence this recovers from is the NORMAL state of a
        fresh clone rather than evidence of an old workspace -- which meant
        that merely opening a cloned project appended machine-local
        bookkeeping to the versioned trajectory, before any mathematics or any
        model interaction had happened, and left the checkout dirty. What was
        recovered is recorded in `.local/state.json` beside the ledger it
        describes, which is where a fact about this machine belongs.
        """
        # A ledger that would not read is treated exactly as a missing one, and
        # so is its cursor: the cursor's only meaning is "the ledger beside me
        # accounts for the transcript this far", and there is no such ledger
        # any more. Keeping it would pair an empty total with a cursor at the
        # end of the file -- nothing recovered, nothing recoverable, and the
        # next exchange written down as the whole session.
        held = Usage.from_dict(self.local.get(USAGE_KEY))
        recovered = held if held is not None else Usage()
        start = self._ledger_cursor(fresh=held is None)
        counted = 0
        for event in self._recorded(start):
            if event.get("type") == "result":
                recovered = recovered.record(event)
                counted += 1
        if not counted:
            return recovered
        self.local[USAGE_KEY] = recovered.as_dict()
        # Accumulated, not overwritten. This runs on every open, and the tail
        # case -- a `result` appended before the process died, folded in on the
        # next open -- would otherwise replace "3 exchanges rebuilt from the
        # transcript" with "1" and make the note say the opposite of the truth.
        held_turns = self.local.get(RECOVERED_KEY)
        held_turns = held_turns if isinstance(held_turns, int) and not isinstance(held_turns, bool) and held_turns >= 0 else 0
        self.local[RECOVERED_KEY] = held_turns + counted
        # Saves the ledger, the note above, and the cursor in one write.
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
        cursor = self.local.get(CURSOR_KEY)
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
            self.local[CURSOR_KEY] = size
            self._save_local()
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
        held = self.local.get(CURSOR_KEY)
        held = held if isinstance(held, int) and not isinstance(held, bool) and held >= 0 else 0
        self.local[CURSOR_KEY] = max(held, offset)
        self._save_local()

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
        # Guarded on the way in as well as on the way out. A symlinked
        # transcript that were refused only at append time would first be read
        # back as this workspace's own history -- counted as spend by
        # `_recover_spend` -- from a file belonging to whoever wrote the link.
        with self._workspace_guard.open(TRANSCRIPT, encoding="utf-8", errors="replace") as handle:
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
            self.local[USAGE_KEY] = self.usage.as_dict()
            self._mark_ledger_read(offset)

    def _skip_spend(self, offset: int) -> None:
        """Account for a result the ledger deliberately did not fold."""
        with self._spend:
            self._mark_ledger_read(offset)
