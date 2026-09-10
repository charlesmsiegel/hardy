from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from hardy.agents import compaction
from hardy.agents.contracts import ChatRuntime, TurnEvent, final_text, provenance
from hardy.agents.loop import Message
from hardy.agents.parsing import json_object
from hardy.agents.usage import Usage
from hardy.algebra.cas import CasError
from hardy.algebra.export import export_session
from hardy.algebra.tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from hardy.documents import completion
from hardy.documents.latex import ROOT_DOCUMENT, LatexTools, compiles_document, uncommented
from hardy.documents.writeup import escape_tex_text
from hardy.formal import audit, refute
from hardy.formal.contracts import Request
from hardy.formal.lean import DECLARATION_NAME, LeanTools
from hardy.formal.modules import ModuleIndex
from hardy.formal.search import SEARCH_TOOL_NAMES, SEARCH_TOOLS, SearchToolRuntime
from hardy.formal.workspace import (
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
    parse_imports,
    safe_relative,
    statements,
    strip_comments,
    unreadable_assumptions,
)
from hardy.foundation import process
from hardy.foundation.files import (
    LayoutError,
    WriteGuard,
    files_under,
    guard_for,
    read_bytes,
    read_text,
)
from hardy.foundation.paths import HARDY_DIR, global_build, global_lean
from hardy.foundation.truncation import truncate
from hardy.foundation.values import ToolResult
from hardy.literature import statements as assume_module
from hardy.literature.arxiv import ArxivError
from hardy.literature.bibliography import GENERATED as GENERATED_BIBLIOGRAPHY
from hardy.literature.bibliography import is_generated as is_generated_bibliography
from hardy.literature.tools import PAPER_TOOL_NAMES, PAPER_TOOLS, PaperToolRuntime
from hardy.literature.tools import build_runtime as build_paper_runtime
from hardy.prompts import (
    ASSUME_REVIEW_PROMPT,
    CHAT_SYSTEM_PROMPT,
    chat_cas_prompt,
    chat_project_context_prompt,
)
from hardy.workflows import admission as admission_policy
from hardy.workflows.admission import (
    UNREADABLE as UNREADABLE,
    VACUITY_STRIP_REFUSED as VACUITY_STRIP_REFUSED,
    _probe_suggestion as _probe_suggestion,
    _strip_hypotheses as _strip_hypotheses,
    _vacuity_source as _vacuity_source,
)
from hardy.workflows import ingest
from hardy.workflows.contracts import RunLimits
from hardy.workflows.interactive import summary as summary_module
from hardy.workflows.interactive.admission import AdmissionOperations, AssumptionAdmission
from hardy.workflows.interactive.context import (
    PROJECT_CONTEXT_EVENT,
    PROJECT_CONTEXT_KEY,
    ProjectContext,
    read_project_context,
)
from hardy.workflows.interactive.documents import (
    DocumentPolicy,
    DocumentService,
    FormalDocumentFacts,
)
from hardy.workflows.interactive.documents import WriteupNotSaved as WriteupNotSaved
from hardy.workflows.interactive.formal import FormalWorkspaceService, SavePolicy
from hardy.workflows.interactive.record import SchemaError as SchemaError
from hardy.workflows.interactive.record import SessionRecord
from hardy.workflows.interactive.turns import TurnCoordinator, TurnPersistence
from hardy.workflows.interactive.turns import _digest as _digest
from hardy.workflows.layout import LOCAL_DIR, LOCAL_STATE, RECORD, TRANSCRIPT, Layout

# Where the two artifact trees live inside a workspace, and the path a tool
# call gets when it names neither -- the one file most sessions ever need.
LEAN_DIR = "lean"
BUILD_DIR = ".build/lean"
BUILD_DIR_TEX = ".build/tex"
TEX_DIR = "tex"
DEFAULT_LEAN_PATH = "Main.lean"
DEFAULT_TEX_PATH = ROOT_DOCUMENT

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
# `automation` is withheld for `audit`'s reason exactly: the listing reports
# which saved statements one tactic closes checked against the tree in front of
# it, and the stored verdicts include entries whose statement has since moved.
WITHHELD = ("audit", "automation", PROJECT_CONTEXT_KEY)
#: Provenance keys a runtime states only when it has one to state. They are
#: dropped rather than merged over when the runtime changes: a value left
#: behind by the backend that stated it describes turns that never ran under
#: it, which is exactly what recording provenance exists to prevent.
OPTIONAL_PROVENANCE = ("output_limit",)
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
    {"type": "function", "function": {"name": "list_statements", "description": "List every statement a fetched paper's source makes -- theorems, lemmas, propositions, definitions -- in the order the paper states them, with the reference to name one by. Reading a paper assumes nothing; assume_statement is what mints an axiom, one statement at a time.", "parameters": {"type": "object", "properties": {"paper_id": {"type": "string"}, "start": {"type": "integer"}}, "required": ["paper_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "assume_statement", "description": "Ask the human to assume one statement of a fetched paper as a Lean axiom, named by the reference list_statements gave it. Hardy elaborates it, searches for a counterexample, has an independent reader compare your Lean against the paper's own words, and only then asks. The axiom is written into Papers.<CiteKey> with a docstring tying it to the paper -- never write that file yourself. `kind` is `statement` for a proposition, or `constant` for an opaque definition the paper uses that Mathlib lacks, which is more trust and is recorded as such.", "parameters": {"type": "object", "properties": {"paper_id": {"type": "string"}, "statement": {"type": "string"}, "formal_name": {"type": "string"}, "lean_statement": {"type": "string"}, "informal_statement": {"type": "string"}, "reason": {"type": "string"}, "kind": {"type": "string"}, "latex_name": {"type": "string"}}, "required": ["paper_id", "statement", "formal_name", "lean_statement", "informal_statement", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "report_result", "description": "Report finished work. The only way to call anything proved, done, or complete: say it in prose and Hardy contradicts you in front of the user. Refused unless every theorem named is saved Lean the kernel audited, the writeup creates its label and quotes its exact Lean statement verbatim, and every assumption the work rests on is stated in an appendix in both Lean and prose. A theorem still resting on a hole is graded partial rather than refused: the report names which, and the writeup must carry it on exactly the same terms, so a reader can see what was and was not proved.", "parameters": {"type": "object", "properties": {"theorems": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}, "required": ["theorems", "summary"], "additionalProperties": False}}},
]

# Always offered, unlike the cas_* tools, and refusing with a reason when
# there is no Lake project. See `search_tools` for why absence is reported
# rather than hidden.
CHAT_TOOLS += SEARCH_TOOLS
# Always offered for a second reason: they need no discovery at all. There is
# no binary to find and no version to probe, and everything already fetched
# can be read and cited with no network. See `paper_tools`.
CHAT_TOOLS += PAPER_TOOLS


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


class MathematicsSession:
    def __init__(self, workspace: Path, make_runtime: Callable[..., ChatRuntime], lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, Any]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0, cas: CasToolRuntime | None = None, cas_detail: str = "", search: SearchToolRuntime | None = None, search_detail: str = "", root: Path | None = None, project_context: bool = True, fresh_thread: bool = False, limits: RunLimits | None = None, context_window: int = compaction.CONTEXT_WINDOW):
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
        # The budgets in force for this session, held here rather than read
        # back off whichever runtime happens to exist. `_chat` builds the CAS
        # and the search independently, so a session with retrieval and no
        # kernel had no CAS `limits` object to scavenge and reported no
        # retrieval budget at all -- and the export's whole point is that two
        # runs under different budgets are distinguishable.
        self.limits = limits if limits is not None else RunLimits()
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
        self.record = SessionRecord(workspace, self._workspace_guard)
        self._local_guard = self.record._local_guard
        # The Lean tree and the writeup tree. Both are directories now: a
        # development outgrows one file, and so does the document about it.
        self.documents = DocumentService(workspace, self.latex)
        self.tex_root = self.documents.tex_root
        # The literature. Built here rather than handed in like `cas` and
        # `search` because there is nothing to discover: the paper library is
        # a directory under the root and the bibliography is a file beside the
        # record, so a caller cannot get this wrong and none of them is asked
        # to.
        # The operator's configured budget, not the module default. `cas` and
        # `search` are handed in already built against
        # `limits.model_observation_bytes`; this one is built here, and being
        # built here is exactly how it came to keep its own 32 KiB while a
        # workspace configured for less had every other tool respect that.
        self.papers: PaperToolRuntime = build_paper_runtime(
            workspace, self.root, observation_bytes=self.limits.model_observation_bytes
        )
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
        # The system prompt the current runtime was built with. Set by
        # `_build`; empty only in the window before the first one exists, which
        # nothing that reads it can reach.
        self._system_prompt = ""
        # What a compaction is trying to fit inside. A default rather than a
        # measurement: no transport here will count a conversation before it is
        # sent, so a caller that knows its model's real window is the one that
        # should say so.
        self.context_window = context_window
        # The SDK may call several tools at once, each on its own thread, but
        # these run Lean, rewrite session.json, and stop to ask a human for
        # approval. None of that is safe to interleave.
        self.turns = TurnCoordinator()
        self._gate = self.turns._gate
        # Set for the rest of a turn once it is cancelled, and cleared when the
        # next one starts. Read by `_dispatch` on the SDK's own tool threads,
        # which is why it is an Event rather than a bare bool.
        self._cancelled = self.turns._cancelled
        # Whether the exchange in flight has had a `result` out of the provider
        # yet. An Event for the same reason `_cancelled` is one: it is set on
        # the runtime's thread and read on whichever thread drained the turn.
        self._reported = self.turns._reported
        # Held across reading `_reported` and folding, so the runtime's worker
        # and the thread that drained the turn make one decision about who
        # records the exchange rather than two guesses.
        self._spend = self.turns._spend
        # `session.json` is written from more than one thread -- a tool call
        # under the gate above, and `_observed` remembering the provider thread
        # on the runtime's own thread -- and `WriteGuard.write_json` replaces a
        # temporary file at a fixed path. Two writers at once would interleave.
        self._writes = self.record._writes
        # This session's own tool use, in memory only: it describes behaviour,
        # not the workspace, so it belongs in neither manifest.
        self.formal = FormalWorkspaceService(self.lean, self.lean_workspace)
        self._save_streak = self.formal._save_streak
        # Streak key -> sha256 hex digests of sources that passed `check_lean`
        # on that path this turn. A green check on a path lifts the brake only
        # for the source it actually checked, not for whatever the model saves
        # next -- `check_lean` elaborates the source it is handed, never the
        # file, so a save of a *different* source has not been shown to fix
        # anything and must still count against the streak.
        self._checked_green = self.formal._checked_green

        # Whether a *completed* `inspect_declarations` batch has run since the
        # last axiom request. `_searched_since_request`, below, carries what it
        # found. `_inspect_attempts_since_request` counts every call, whether
        # it completed or not: a machine whose Lean cannot finish still has to
        # let a request through eventually, or the search-first gate below
        # would refuse every `request_assumption` forever and blame a search
        # that was, in fact, attempted.
        self.admission = AssumptionAdmission()
        # Prior statements this session requested under each name and did not
        # get approved, so a human sees a statement beside what it was
        # weakened from.
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
        # `--fresh-thread`: discard the resumable provider thread, before the
        # runtime is built from it. A per-run act asked for by a flag, never a
        # setting -- "always start fresh" would silently discard the
        # conversation on every launch. Everything else stays: the transcript
        # is the versioned record of the mathematics and keeps going, the
        # ledger keeps counting (a new conversation is not a new budget), and
        # nothing in the workspace is deleted. Only the thread id in
        # `.local/state.json`, machine-local and disposable by design, goes.
        self.fresh_thread_detail = self._discard_thread() if fresh_thread else ""
        # The runtime needs a way to reach the tools, and the tools need the
        # workspace, so it is built here rather than handed in ready-made.
        # After a discard `_carried_thread` finds nothing, which is the point:
        # the fresh session takes the same road every first-ever session takes.
        self.runtime = self._build(session_id=self._carried_thread())
        self._sync_provenance()
        self._sync_fresh_context()
        self._sync_project_context()

    @property
    def state(self) -> dict[str, Any]:
        return self.record.state

    @state.setter
    def state(self, value: dict[str, Any]) -> None:
        self.record.state = value

    @property
    def local(self) -> dict[str, Any]:
        return self.record.local

    @local.setter
    def local(self, value: dict[str, Any]) -> None:
        self.record.local = value

    @property
    def usage(self) -> Usage:
        return self.record.usage

    @usage.setter
    def usage(self, value: Usage) -> None:
        self.record.usage = value

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
        # Kept, because the runtime is handed it once and keeps it for the
        # life of the conversation. `_request_overhead` used to rebuild it from
        # the state as it stands now, so a goal shortened mid-session made the
        # estimate describe a prompt nobody was sending -- undercounting the
        # long one the runtime still holds, and letting `plan` conclude that a
        # request the provider then refuses needed no compaction.
        self._system_prompt = prompt + self._context()
        runtime = self._make_runtime(
            model=model,
            system_prompt=self._system_prompt,
            specs=CHAT_TOOLS + (CAS_TOOLS if self.cas is not None else []),
            dispatch=self._dispatch,
            cwd=self.workspace,
            session_id=session_id,
            observe=self._observed,
        )
        # Offered rather than passed in, and only to a runtime that says it can
        # take it. A backend whose SDK owns the loop cannot let Hardy choose
        # what a compaction keeps (issue #23), and handing it a compactor it
        # would silently drop would leave the record claiming a compaction
        # Hardy never got to make.
        attach = getattr(runtime, "attach_compactor", None)
        if attach is not None:
            attach(self.compact)
        return runtime

    def _observed(self, event: dict[str, Any]) -> None:
        return self.turns._observed(event, self._turn_persistence())

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

        A backend that has no provider thread carries the conversation itself,
        so it is handed over here explicitly. Without that, switching model on
        such a backend would discard every turn the session had taken while
        the thread-carrying backends kept theirs -- the same act meaning two
        different things depending on the transport.
        """
        previous = {key: self.state.get(key) for key in ("model", "backend", "endpoint", *OPTIONAL_PROVENANCE)}
        carried = getattr(self.runtime, "conversation", None)
        self.runtime = self._build(model=model, session_id=self._carried_thread())
        current = provenance(self.runtime)
        # The same drop `_sync_provenance` makes, for the same reason: a switch
        # to a backend that states no output cap must not leave the old one
        # standing in the record.
        #
        # And the prompt is rebuilt over the corrected record rather than left
        # holding the old one. `_build` freezes the state manifest into the
        # system prompt and hands it to the runtime once, for the life of the
        # conversation -- so a key dropped only from `self.state` left
        # `session.json` and the switch event naming the uncapped condition
        # while every later request still told the model it was generating
        # under the cap that had just been retired. Building twice is the price
        # of learning the new runtime's shape before the prompt is frozen, and
        # it is paid only on the switch that actually changes it: a runtime is
        # bookkeeping until its first turn, when the SDK is loaded.
        stale = [key for key in OPTIONAL_PROVENANCE if key not in current and key in self.state]
        for key in stale:
            self.state.pop(key, None)
        if stale:
            self.runtime = self._build(model=model, session_id=self._carried_thread())
        # Handed over after the last build, so a rebuilt runtime is not left
        # holding the empty conversation its replacement was given.
        adopt = getattr(self.runtime, "adopt_conversation", None)
        if adopt is not None and carried is not None:
            adopt(carried)
        self.state.update(current)
        self._save_state()
        self._record({"type": "model", "reason": "switched", "previous": previous, **current})

    def _read_state(self) -> dict[str, Any]:
        return self.record._read_state()

    def _read_local(self) -> dict[str, Any]:
        return self.record._read_local()

    def _save_state(self) -> None:
        return self.record._save_state()

    def _save_local(self) -> None:
        return self.record._save_local()

    def _sync_provenance(self) -> None:
        """Make the record agree with what is actually about to answer.

        Reopening a workspace under a different model is a change of
        experimental condition like any other, and resumed turns must not be
        attributed to the model that produced the earlier ones.
        """
        current = provenance(self.runtime)
        stale = [key for key in OPTIONAL_PROVENANCE if key not in current and key in self.state]
        if not stale and all(self.state.get(key) == value for key, value in current.items()):
            return
        previous = {key: self.state.get(key) for key in (*current, *stale)}
        started = any(previous.values())
        # Dropped, not merged over. A workspace opened once on the API backend
        # carries `output_limit`; reopened on a backend that states none, a
        # plain `update` left the old cap in the record and in the manifest the
        # system prompt embeds -- so subscription turns read as though they had
        # run under an API-only generation limit.
        for key in stale:
            self.state.pop(key, None)
        self.state.update(current)
        self._save_state()
        if stale:
            # Reopening is the other half of the same bug: `_build` ran above
            # this call and froze the manifest from the record as it stood on
            # disk, cap and all. See `switch_model` for why the prompt is
            # rebuilt rather than only the record corrected.
            self.runtime = self._build(session_id=self._carried_thread())
        if started:
            self._record({"type": "model", "reason": "session_resumed", "previous": previous, **current})

    def _sync_fresh_context(self) -> None:
        """Say when a session starts with no memory of the record it continues.

        A backend that resumes a provider thread carries the earlier turns into
        the next request. One that does not -- the `api` transport, whose
        conversation is the loop's own list and ends with the process -- starts
        empty every time, and `_sync_provenance` sees the same model and the
        same backend and records nothing. Later events then land in a
        `transcript.jsonl` that reads as one unbroken conversation while every
        request omitted everything above this point, so an auditor cannot say
        what context the model actually had.

        Only where there is a record to be continued: on a workspace's first
        open there is nothing above the boundary and nothing to disclose, which
        is the same rule `_discard_thread` follows about a thread it never had.
        """
        # Asked of the backend, not of this instance. A runtime that resumes
        # has no thread *yet* on a workspace nobody has spoken to, and reading
        # `session_id` alone would announce a fresh context on every first turn
        # of every backend.
        if getattr(self.runtime, "resumes_conversation", True) or not self._transcript_end():
            return
        self._record({"type": "thread", "reason": "fresh context: this backend resumes nothing"})

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
        return self.record._without(*keys)

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
        #
        # `automation` is withheld here as well as from the listing, and for
        # the opposite reason from `audit`'s staying: the model does get a
        # checked copy -- the steering block carries the flags validated
        # against the tree on every turn -- so a raw record whose statement
        # moved on disk between sessions would sit in the prompt contradicting
        # it.
        manifest = json.dumps(
            self._without(PROJECT_CONTEXT_KEY, "automation"), ensure_ascii=False
        )
        return f"\n\nWorkspace: {self.workspace}\nExisting manifest:\n{manifest}" + self._project_context_prompt()

    def _record(self, event: dict[str, Any]) -> int:
        return self.record._record(event)

    def _generated_module_refusal(self, relative: Any) -> str | None:
        """Why this path is not the workspace's to write, or None."""
        parts = str(relative).replace("\\", "/").split("/")
        # Case-folded, because macOS and Windows resolve `papers/x.lean` and
        # `Papers/x.lean` to one file: a comparison that only matched the
        # spelling Hardy writes left the other spelling open on exactly the
        # platforms where it reaches the same bytes.
        if parts and parts[0].casefold() == self.PAPERS_DIR.casefold():
            return (
                f"{'/'.join(parts)} is generated by Hardy from the assumptions a human "
                "approved, and is regenerated whole on every mint. Use assume_statement to "
                "add a paper's statement; nothing else may write this tree."
            )
        return None

    def _final_gates(self, source: str) -> ToolResult | None:
        return self.formal._final_gates(source, self.record.snapshot())

    def _run_lean_source(self, source: str, timeout: float | None = None) -> ToolResult:
        return self.lean.run_source(
            source, env={"LEAN_PATH": self._lean_path()}, timeout=timeout
        )

    def _probe_lean_source(self, source: str, timeout: float | None = None) -> ToolResult:
        """Run a probe file against the configured environment alone.

        No `LEAN_PATH` entry for the workspace or the shared builds: `lake
        env` computes its own path, so `import Mathlib` here can only mean the
        configured package. With the workspace build on the path, a saved
        module named `Mathlib` answered for it -- and then the theorem under
        question was in scope and `exact?` closed its own example by citing
        it, which is exactly the self-citation the automation probe's
        declaration-free layout exists to rule out.
        """
        return self.lean.run_source(source, timeout=timeout)

    def _probe_environment(self) -> str:
        """What an automation verdict is valid under.

        The toolchain identity the audit records carry, plus what the Mathlib
        olean the probe imports currently *is* -- `_external_stamp`'s size-
        and-mtime identity, restatted on every ask for `_external_stamp`'s
        reason: a configured Lake project whose Mathlib is edited and rebuilt
        changes what the probe's tactics can do without moving the pin or the
        manifest, and a verdict from before that rebuild is not current.

        Resolved over Lake's own search path alone, not the default that
        looks in the shared builds first, because `_probe_lean_source` runs
        with no shared entries: a shared library that happened to provide a
        `Mathlib.olean` would otherwise stamp the verdict with an artifact
        the probe never imported -- kept current past a rebuild of the real
        one, and expired by rebuilds of one it does not use.
        """
        return f"{self._toolchain}|{self._external_stamp('Mathlib', self._lean_search_path())}"

    def _assumption_shape(self, formal_name: str, lean_statement: str) -> str | None:
        return self.admission._assumption_shape(formal_name, lean_statement)

    PROBES = admission_policy.PROBES
    WITNESSES = admission_policy.WITNESSES

    # Consecutive refused `save_lean` calls on one path before the next is
    # refused without running Lean. A failing run made 21 in a row.
    SAVE_STREAK_LIMIT = 3

    def _assumption_probe(self, declaration: str) -> tuple[str | None, str]:
        return admission_policy.assumption_probe(declaration, run_source=self._admission_elaborate_source)

    def _vacuity_probe(self, statement: str) -> str:
        return admission_policy.vacuity_probe(statement, run_source=self._admission_elaborate_source)

    def _automation_probe(self, proposed: Mapping[str, str]) -> dict[str, str] | None:
        return self.formal._automation_probe(proposed, probes=self.PROBES, probe_seconds=PROBE_SECONDS, run_source=self._probe_lean_source)

    def _refresh_automation(self) -> str:
        return self.formal._refresh_automation(current=self._theorem_statements(), stored=self.record.snapshot().get("automation", {}), environment=self._probe_environment(), probe=self._automation_probe, publish=self.record.publish_automation)

    def _automation_closed(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        return self.formal._automation_closed(stored=self.record.snapshot().get("automation", {}), current=lambda: self._theorem_statements(sources), environment=self._probe_environment)

    def automation_closed(self) -> dict[str, str]:
        """The same answer, for `/status`: which saved theorems fall to one
        tactic, so a user can see the caveat the document's banner carries
        without opening the PDF."""
        return dict(sorted(self._automation_closed().items()))

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

    def _external_stamp(self, module: str, directories: Sequence[Path] | None = None) -> str:
        """What the olean behind an import outside the workspace currently is.

        `directories` narrows where the artifact may be found; the default is
        every place a workspace import resolves -- the shared builds first,
        then Lake's answer. `_probe_environment` passes Lake's path alone,
        because the probe it stamps for runs with no shared entries.

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
        searched = (
            (*(build for _, build in self.shared_roots), *self._lean_search_path())
            if directories is None
            else directories
        )
        for directory in searched:
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
        return self.formal._check_lean(path, source, build_shared=self.build_shared, run_source=self._run_lean_source)

    def _tally(self, name: str, ok: bool) -> None:
        # Every tool name, not only `save_lean`/`check_lean`: `_steering_block`
        # reads this to decide whether the session has done *anything* at all,
        # and a session that got three axioms approved and wrote nothing else
        # must not read as having made no tool call.
        return self.turns._tally(name, ok)

    def _streak_key(self, path: str) -> str:
        return self.formal._streak_key(path)

    @staticmethod
    def _save_digest(source: str) -> str:
        return FormalWorkspaceService._save_digest(source)

    def _streak_refusal(self, path: str, source: str) -> ToolResult | None:
        return self.formal._streak_refusal(path, source)

    def _formal_save_policy(self) -> SavePolicy:
        return SavePolicy(
            generated_refusal=self._generated_module_refusal,
            result_gate=self._result_gate,
            documentation_gate=self._documentation_gate,
            final_gates=self._final_gates,
            compile_path=self._compile_path,
            build_shared=self.build_shared,
            missing_names=self._missing_registered_names,
            audit_tree=self._audit_tree,
            closes_and_adds=self._closes_and_adds,
            publish_audit=self.record.publish_audit,
            refresh_automation=self._refresh_automation,
            persist=self._save_state,
            owed_note=self._owed_note,
        )

    def _save_lean(self, path: str, source: str) -> ToolResult:
        return self.formal._save_lean(path, source, self._save_lean_unbraked)

    def _save_lean_unbraked(
        self, path: str, source: str, *, ratchet: bool = True, generated: bool = False
    ) -> ToolResult:
        return self.formal._save_lean_unbraked(path, source, policy=self._formal_save_policy(), ratchet=ratchet, generated=generated)

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
        return self.formal._audit_tree(space, modules, approved=self._approved_assumptions(), probe_groups=self._probe_groups)

    def _build_imports(self, space: LeanWorkspace, source: str) -> BuildFailure | ToolResult | None:
        return self.formal._build_imports(space, source)

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

        The fallback reads code, not comments or strings. A helper file saying
        `-- HardyLater will go here` made this answer that the result already
        existed, and tidying that line away later then read as the declaration
        vanishing -- refusing a save over a name nothing ever declared.
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
            re.search(rf"(?<![\w'.]){re.escape(formal_name)}(?![\w'])", strip_comments(source))
            for source in sources.values()
        )

    def _still_current(
        self, module: str, record: dict[str, Any], signatures: dict[str, str]
    ) -> dict[str, Any]:
        return self.formal._still_current(module, record, signatures)

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
        return self.documents._labels()

    def _saved_theorems(self, sources: dict[str, str] | None = None) -> set[str]:
        """The public theorems the tree declares.

        The same set `_theorem_statements` keys, and it has to be: a private
        theorem has no statement here (Lean mangles the name, so nothing
        outside its module can refer to it), and counting one anyway sent that
        name to `_audit_gaps`, which asked for an audit that cannot be
        established -- an obligation with no way to satisfy it.
        """
        found: set[str] = set()
        for source in (self.lean_workspace.sources() if sources is None else sources).values():
            declared = declarations(source)
            found.update(set(declared["theorem"]) - set(declared["private"]))
        return found

    def _theorem_statements(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        """Every saved theorem, with the exact statement Lean was given.

        Theorems only. A `lemma` is scaffolding and owes nothing, which is the
        same line `_saved_theorems` draws and has to stay the same line: a
        writeup gate that demanded a paragraph for every helper would make
        splitting a proof into helpers the expensive way to work.

        Open ones included: a report may name one, and the document has to
        carry it on the same terms as any other.
        """
        found: dict[str, str] = {}
        for source in (self.lean_workspace.sources() if sources is None else sources).values():
            declared = declarations(source)
            # Private ones left out, and this is not merely tidiness. The map
            # is keyed by NAME, so a `private theorem result` in a later module
            # overwrote the public `theorem result` an earlier one declared --
            # and a private declaration is not in the axiom audit, so the page
            # then showed the private statement under the public theorem's
            # clean verdict. Lean mangles the private name; nothing outside its
            # module can refer to it, so nothing outside can owe a writeup for
            # it either.
            theorems = set(declared["theorem"]) - set(declared["private"])
            found.update(
                {name: text for name, text in statements(source).items() if name in theorems}
            )
        return found

    def _saved_statements(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        """The closed ones, which is what the writeup obligations are about.

        A theorem whose proof still has a hole is not a result yet; demanding
        that the document carry it would ask for a paragraph asserting
        something nobody has proved, and would block the next save behind it.
        Its obligation is that it is open, and the writeup obligations attach
        the moment the hole closes -- or at a report that names it, which asks
        for the carrying directly.
        """
        opened = self._open_theorems(sources)
        return {
            name: text
            for name, text in self._theorem_statements(sources).items()
            if name not in opened
        }

    def _shared_names(self, sources: dict[str, str] | None = None) -> dict[str, list[str]]:
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
        theorems: set[str] = set()
        snapshot = self.lean_workspace.sources() if sources is None else sources
        for module, source in sorted(snapshot.items()):
            found = declarations(source)
            # Lemmas too, not only theorems. The audit records every
            # declaration a module has, and a verdict is looked up BY NAME: an
            # audited `lemma result` in one module answers for an unaudited
            # `theorem result` in another, and the page would print the second
            # statement as kernel-verified on the strength of the first. What
            # collides is the name, so what is counted is every declaration
            # that can carry one.
            # `private` is a subset of the two above rather than a fourth
            # kind, and Lean mangles a private name so it cannot collide with
            # anything outside its own module. Counting one made two modules
            # that both spell a helper `private lemma step` look ambiguous, and
            # the obligation asking the model to namespace one of them could
            # not be satisfied -- there was nothing wrong to fix.
            hidden = set(found["private"])
            for name in (*found["theorem"], *found["lemma"]):
                if name in hidden:
                    continue
                if name in found["theorem"]:
                    # Which names a THEOREM answers to somewhere. A lemma is
                    # scaffolding: two disconnected modules both spelling one
                    # `step` collide with nothing a report, a registry entry or
                    # a label can name, and the obligation below -- which says
                    # they "each declare a theorem" -- was both false and
                    # impossible to satisfy.
                    theorems.add(name)
                modules = holders.setdefault(name, [])
                # Once per module: Lean will not let one module declare a name
                # twice, and counting a repeat as a collision would report a
                # module as ambiguous with itself.
                if module not in modules:
                    modules.append(module)
        # A lemma is still counted above, because a lemma in one module and a
        # theorem in another DO collide -- the audit records both and a verdict
        # is looked up by name. What is dropped is a collision no theorem is
        # part of.
        return {
            name: found
            for name, found in holders.items()
            if len(found) > 1 and name in theorems
        }

    def _tex_sources(self) -> dict[str, str]:
        return self.documents._tex_sources()

    def _tex_paths(self) -> list[str]:
        return self.documents._tex_paths()

    def _unreached_tex(self) -> list[str]:
        return self.documents._unreached_tex()

    def _theorem_counts(self) -> tuple[int, int]:
        """(machine-checked theorems, open theorems), from `_obligations` and
        `_saved_theorems`.

        `_stamp` and `_steering_block` both report these two numbers -- one to
        the document, one to the model -- and had each grown their own copy of
        the same set arithmetic. One place computing it is one place to get it
        right.
        """
        owed = self._obligations()
        gaps = {item.subject for item in owed if item.kind == "lean"}
        opened = {item.subject for item in owed if item.kind == "open"}
        saved = self._saved_theorems()
        return len(saved - gaps - opened), len(saved & opened)

    def _steering_block(self) -> str:
        """What the workspace and this session amount to, for the model.

        The end-of-turn notice tells the *user* that nothing is saved. A
        failing run was told eight times; the model saw none of them, and
        wrote itself a status report saying the work was done. This is the
        same arithmetic, put where the model reads, and nothing it wrote.

        Everything below is wrapped in one `try`: `stream()` calls this ahead
        of recording the `user` event for the turn, and `lean_workspace.sources()`,
        `_obligations()` and `_tex_paths()` can all raise for reasons that have
        nothing to do with whether the turn should proceed -- a status line
        must never be the thing that aborts the turn it is reporting on.
        """
        try:
            # `calls` and `no_tools` moved inside the `try`, and `list(...)`
            # snapshots the values: `_tally` on an SDK thread may resize
            # `_tool_tally` (`setdefault` on a tool name seen for the first
            # time) while a new turn starts `_steering_block` on the
            # sequencing thread, and iterating a dict that resizes underneath
            # you raises rather than returning stale-but-safe data.
            calls = self._tool_tally
            no_tools = all(count[0] == 0 for count in list(calls.values()))
            # File existence, not `self.tex_root.is_dir()`: a session that creates
            # `tex/` at init but has written nothing into it must still count as
            # having no writeup, or a fresh workspace would get a block on its
            # first turn purely because the directory happens to exist.
            if no_tools and not self.lean_workspace.sources() and not self._tex_paths():
                return ""
            checked, opened_count = self._theorem_counts()
            lines = [
                "[Hardy workspace state — written by Hardy, not the user]",
                f"saved theorems: {checked} machine-checked, "
                f"{opened_count} open (resting on a hole)",
                f"approved assumptions: {len(self.state['assumptions'])}",
                f"this session: {calls['save_lean'][0]} save_lean calls, "
                f"{calls['save_lean'][1]} accepted; {calls['check_lean'][0]} check_lean calls, "
                f"{calls['check_lean'][1]} passed",
            ]
            flagged = self._automation_closed()
            if flagged:
                # The same fact the banner prints, put where the model reads:
                # a statement one tactic closes may assert far less than its
                # name suggests, and the model is the one that can still
                # strengthen it.
                lines.insert(
                    2,
                    "statements closed by a single automation call: "
                    + ", ".join(
                        f"{name} (by {tactic})" for name, tactic in sorted(flagged.items())
                    ),
                )
            unreached = self._unreached_tex()
            if unreached:
                # "Not yet reached", not "not reached": the model is told to
                # `\input` a fragment into the writeup before the root ever
                # mentions it, so this line names an ordinary mid-session state,
                # not a finished tree with an orphan left in it.
                lines.append(f"tex files not yet reached from writeup.tex: {', '.join(unreached)}")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - a status line must never end a turn
            return ""

    def _used_assumptions(self, sources: dict[str, str] | None = None) -> set[str]:
        """Approved axioms the saved tree actually rests on.

        Both ways one can be reached: written into a workspace file, or
        inherited through an import and found by the audit. An approval nobody
        used is not an assumption this work depends on, and demanding an
        appendix entry for it would pad the appendix with disclaimers a reader
        has to rule out by hand.
        """
        used: set[str] = set()
        snapshot = self.lean_workspace.sources() if sources is None else sources
        for source in snapshot.values():
            used.update(name for name, _ in assumptions(source))
        for record in self.state.get("audit", {}).values():
            used.update(str(name) for name in record.get("assumed", ()))
        return used

    def _obligations(
        self, sources: dict[str, str] | None = None, tex: dict[str, str] | None = None
    ) -> tuple[completion.Obligation, ...]:
        written = self._tex_sources() if tex is None else tex
        state = self.record.snapshot()
        opened = self._open_theorems(sources)
        facts = FormalDocumentFacts(
            statements=self._saved_statements(sources),
            saved_statements=self._theorem_statements(sources),
            used_assumptions=self._used_assumptions(sources),
            shared_names=self._shared_names(sources),
            open_theorems=opened,
            audit_gaps=tuple(self._audit_gaps(self._saved_theorems(sources) - opened, sources)),
        )
        return self.documents._obligations(
            facts=facts, registry=state["names"], assumptions=state["assumptions"],
            stale_writeup=tuple(self._stale_writeup(sources, written)), tex=written,
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
        """Set the goal, or leave it exactly as it was.

        The write can fail -- an unwritable workspace, a full disk, a refused
        record -- and the in-memory value was being changed first. A session
        that then went on answering about a goal `session.json` does not carry
        is a session whose own record disagrees with it, which is the one thing
        this class is for. So the previous value is put back before the failure
        is passed on.
        """
        previous = self.state.get("goal")
        self.state["goal"] = text.strip()
        try:
            self._save_state()
        except BaseException:
            if previous is None:
                self.state.pop("goal", None)
            else:
                self.state["goal"] = previous
            raise

    def has_theorems(self) -> bool:
        """Whether anything here could be reported at all.

        No obligations means two different things -- everything is written up,
        or there is nothing to write up -- and a reader of `/status` must not
        be shown the first when the second is true.
        """
        return bool(self._saved_theorems())

    def _current_audit(
        self, sources: dict[str, str] | None = None
    ) -> dict[str, dict[str, Any]]:
        return self.formal._current_audit(sources, stored=self.record.snapshot().get("audit", {}))

    def summary(self) -> summary_module.Summary:
        """This session, read off the workspace rather than remembered (#100).

        The mechanical half of compaction, and useful on its own: the naming
        registry, the approved assumptions and the stored audit verdicts are in
        the record, the declarations are in the Lean tree, and what is
        outstanding follows from both. A summary assembled from those is
        checkable, which is the whole difference between this and asking a
        model what it remembers doing.

        The failed attempts are the exception and come from the transcript --
        an attempt that failed left nothing in the workspace by definition --
        so what was tried and what Lean said is read from the record of it.

        Carries no spend. `usage` and the ledger are withheld from the model
        deliberately, and a summary is precisely the shape of thing that would
        put them back in a prompt; `/status` prints them separately, to the
        human.
        """
        # Under the gate, and that is the whole of what makes the answer sound.
        # `/status --full` is safe in flight, so a `save_lean` on another thread
        # can commit between two of the reads below -- and the pair that must
        # not straddle one is the audit and the sources. A verdict validated
        # against the old signatures, paired with the statement the save has
        # just written, says "kernel-verified" about content Lean never saw.
        # The gate is the session's own consistency boundary: every tool call
        # that writes holds it, so taking it here is what "one snapshot" means.
        # It also removes the weaker reason the copies below existed -- a list
        # being appended to while it is iterated.
        with self._gate:
            return self._summary()

    def _summary(self) -> summary_module.Summary:
        """`summary`'s body, with the gate already held.

        One read of the Lean tree, shared by everything derived from it. The
        gate serializes Hardy's own tool calls and nothing else -- editing a
        `.lean` file behind Hardy is supported -- so two reads could straddle
        an edit and pair a still-current verdict with a statement that verdict
        was never about.
        """
        # Before any signature is computed, exactly as `list_lean` does it: a
        # shared source under `.hardy/lean` edited in the user's own editor has
        # already invalidated every stored verdict, and an identity fixed at
        # startup would let `_still_current` go on matching the signature of a
        # dependency that has moved -- so the page would print
        # "kernel-verified" for a theorem whose imports changed underneath it.
        self._refresh_shared_identity()
        sources = self.lean_workspace.sources()
        tex = self._tex_sources()
        return summary_module.assemble(
            goal=self.goal(),
            assumptions=list(self.state["assumptions"]),
            registry=list(self.state["names"]),
            audit=self._current_audit(sources),
            theorems=self._theorem_statements(sources),
            open_theorems=self._open_theorems(sources),
            obligations=self._obligations(sources, tex),
            failed=summary_module.attempts(self._recorded()),
            modules=sorted(sources),
            # Already computed for the obligations, and needed here for the
            # same reason: a name two modules declare cannot be graded, because
            # the statement shown and the verdict over it may come from
            # different ones.
            shared=self._shared_names(sources),
            # Under the same gate as everything else here, for the reason the
            # obligations are: read separately, this disclosure could name a
            # theorem the sections beside it do not have.
            automation=self._automation_closed(sources),
        )

    def export_material(self) -> dict[str, Any]:
        """Everything one exportable account of this session needs (#105).

        Gathered here rather than reached for from outside, for the reason
        `summary` is: the rules about what a theorem rests on, which writeup
        the tree carries and which axiom a human approved live in this class,
        and an exporter that re-derived them would be a second opinion nobody
        checked against the first.

        The spend and the model switches are in it deliberately. They are
        withheld from the MODEL (`WITHHELD`, and the ledger in
        `.local/state.json`) and never from the person holding the artifact:
        what a result cost and which model produced it are exactly what a
        collaborator weighing it wants, and the export is written for them.
        """
        # Under the gate, for `summary`'s reason: the audit and the sources it
        # grades must come from one moment, or the page pairs an old verdict
        # with a new statement.
        with self._gate:
            return self._export_material()

    def _export_material(self) -> dict[str, Any]:
        """`export_material`'s body, with the gate already held.

        One read of the Lean tree, for `_summary`'s reason: the verdict, the
        statement it grades and the source the page prints all have to come
        from the same moment, and a file edited behind Hardy between two reads
        is a supported thing for a user to do.
        """
        # Before any signature is computed, exactly as `list_lean` does it: a
        # shared source under `.hardy/lean` edited in the user's own editor has
        # already invalidated every stored verdict, and an identity fixed at
        # startup would let `_still_current` go on matching the signature of a
        # dependency that has moved -- so the page would print
        # "kernel-verified" for a theorem whose imports changed underneath it.
        self._refresh_shared_identity()
        sources = self.lean_workspace.sources()
        tex = self._tex_sources()
        # Read here, next to the identity that was just refreshed, and carried
        # down rather than read again where it is used. `_refresh_shared_identity`
        # validates every stored verdict against the shared bytes as they were a
        # moment ago; a user editing `.hardy/lean` in their own editor is
        # supported and is not serialised by the tool gate, so reading the
        # modules later could put a different dependency on a page that has
        # already badged the theorem kernel-verified. `_shared_moved` below says
        # whether that happened between the two.
        shared_sources = self._shared_sources(sources)
        shared_moved = self._shared_digest() != self._shared_stamp
        document = self.workspace / "writeup.pdf"
        # Not through a link. `is_file` and `stat` both follow one, so a
        # checked-out `writeup.pdf -> /etc/passwd` would have the export state
        # that Hardy compiled a document and report that file's size. The Lean
        # and TeX reads already refuse a link and so does the publisher; this
        # is the same rule for the one path that was reading a leaf directly.
        linked = document.is_symlink()
        compiled = document.is_file() and not linked
        return {
            "project": self.workspace.name,
            "workspace": str(self.workspace),
            "goal": self.goal(),
            "assumptions": list(self.state["assumptions"]),
            "registry": list(self.state["names"]),
            "audit": self._current_audit(sources),
            "theorems": self._theorem_statements(sources),
            "open": sorted(self._open_theorems(sources)),
            "shared": self._shared_names(sources),
            "lean": sources,
            "tex": tex,
            # What arrived from outside rather than being written here. The
            # sources above carry no trace of it, so without this the page
            # presents an imported module exactly like one Hardy authored, and
            # the origin path and arriving digest -- the only things that let a
            # reader check it against the file it came from -- are lost.
            "imported": list(self.state.get("imported", [])),
            # The shared modules a saved theorem may import. They are elaborated
            # with it and their text is hashed into the identity that stamps
            # every verdict, so a page that omits them shows verdicts resting on
            # source it does not carry -- and `shared` above is a duplicate-name
            # map, not the source. Locally authored, so a recipient has no other
            # copy to compare against: without this the export is not standalone
            # for exactly the workspaces that wrote their own library.
            "shared_sources": shared_sources,
            # Whether the shared library changed under the gather. It is not
            # enough to read the modules once: the verdicts above were validated
            # against the identity taken before that read, so if the digest has
            # moved since, the page is showing source the audit was not
            # established against. Said rather than silently reconciled --
            # re-running the identity here would make the badges agree with the
            # new bytes without anything having re-checked them.
            "shared_moved": shared_moved,
            # The disclosure the compiled document's banner carries. The export
            # embeds no PDF, so without this a theorem Hardy knows closes with
            # one `simp` reads as kernel-verified and nothing more -- the page
            # would be dropping a warning the workspace holds about the very
            # results it is presenting.
            "automation": self._automation_closed(sources),
            "obligations": [str(item) for item in self._obligations(sources, tex)],
            # A refusal is not an absence. Reporting the link as "no document
            # was found" told the reader something false about the workspace --
            # the file is there, and Hardy declined to read it. Everywhere else
            # a link raises and takes the export with it; this leaf is reported
            # instead, because a document Hardy never embeds is not worth
            # losing the whole page over.
            "document": (
                f"{document.name} was compiled by Hardy ({document.stat().st_size} bytes). "
                "It is not embedded here: this file carries no external assets."
                if compiled and self._document_is_hardys(document)
                # A regular file is not evidence that Hardy made it. A clone
                # carries whatever `writeup.pdf` was committed, and a user may
                # drop one in; "was compiled" then credited Hardy with a
                # document it never produced, beside an outstanding section
                # that may be asking for the compile. `tex_signature` is
                # stamped only by `_stamp_writeup`, after a compile Hardy ran,
                # so its absence settles the question.
                else f"{document.name} is present ({document.stat().st_size} bytes), but "
                "these are not bytes Hardy is recorded as having produced: the file came "
                "with the workspace, was put there by hand, or replaced one Hardy built. "
                "It is not embedded here either."
                if compiled
                else f"{document.name} is a symlink; Hardy did not read it, so nothing "
                "here reports on a compiled document. That is a refusal, not a finding "
                "that none exists."
                if linked
                else "No compiled document was found in this workspace."
            ),
            "usage": self.usage.lines(),
            "provenance": provenance(self.runtime),
            "toolchain": self._toolchain,
            "environment": self._environment,
            # The settings that decide what the model could find out. Two
            # sessions on the same model and the same toolchain are still
            # different experiments if one gave Lean thirty seconds and the
            # other three minutes, or if one had a computer algebra kernel and
            # a literature search and the other had neither: the same prompt
            # then reaches a different set of finished audits and observed
            # computations. Identity without them cannot tell those apart.
            "settings": self._effective_settings(),
            "transcript": list(self._recorded()),
        }
    def _record_overflow(self, plan: compaction.Plan) -> None:
        return self.turns._record_overflow(plan, self.context_window, self._turn_persistence())

    def compact(self, messages: list[Message]) -> list[Message] | None:
        return self.turns.compact(messages, context_window=self.context_window, request_overhead=self._request_overhead, output_cap=self._output_cap, summary=self._summary, persistence=self._turn_persistence())

    def _output_cap(self) -> int:
        """What the runtime says it may write, charged against the same window.

        Zero when it states none -- a backend that imposes no cap of its own
        has nothing to reserve for beyond the proportional allowance, and a
        key that is present and empty would claim a measurement nobody made.
        """
        return int(getattr(self.runtime, "output_limit", None) or 0)

    def _request_overhead(self) -> int:
        """What every request carries before a message is added.

        The system prompt and the tool schemas are charged against the same
        window the conversation is. Left out, a workspace whose `AGENTS.md` is
        in the prompt -- up to 50 KB of it, which Hardy supports on purpose --
        could be told no compaction was needed for a request the provider then
        refuses.
        """
        specs = CHAT_TOOLS + (CAS_TOOLS if self.cas is not None else [])
        # The prompt the runtime was actually built with, not the one the
        # current state would produce. They differ the moment anything in
        # `_context()` changes -- a goal, an assumption, a registered name --
        # and the provider charges for the one it was sent.
        return compaction.overhead(self._system_prompt, specs)

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

    def _closes_and_adds(
        self, source: str, affected: Sequence[str], records: Mapping[str, dict[str, Any]]
    ) -> str | None:
        """The catch-up ratchet again, once the audit knows what this save closed.

        `_documentation_gate` runs before Lean, which is what makes it cheap and
        what makes it blind here: an open theorem owes no writeup, so a tree
        holding one owes nothing and the gate admits a new theorem -- and then
        the very same save closes the hole, so *both* land undocumented, which
        is the one thing the ratchet exists to prevent.

        Only elaboration can say which holes a source closes, so the question is
        asked again with the audit's answer in hand and before anything is
        committed. Conservative on purpose: a closure and an addition in one
        save are refused together rather than checked for whether the closed one
        happens to be written up already. Splitting them into two saves is one
        extra call and leaves the ratchet asking its ordinary question about
        each; guessing at the document from here would be a third place that
        has to agree with `completion` about what a writeup is.
        """
        introduced = [
            name
            for name in declarations(source)["theorem"]
            if name not in self._saved_theorems()
        ]
        if not introduced:
            return None
        stored = self.state.get("audit", {})
        before = {
            name
            for module in affected
            for name in audit.open_declarations(stored.get(module, {}))
        }
        after = {name for record in records.values() for name in audit.open_declarations(record)}
        closed = (before & self._open_theorems()) - after
        if not closed:
            return None
        return (
            f"this save closes {sorted(closed)} and introduces {introduced[0]} at once. A "
            "theorem that has just been closed owes its writeup before another is added, "
            "and until this save ran there was no hole-free theorem here to owe one. Save "
            "the closed proof on its own, settle what it owes with record_name and "
            "save_latex, and add the new theorem after."
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
        # `request_assumption` records its own naming entry, so an approved
        # axiom's name is in this registry too -- and it is not a result
        # mapping. Left in, an axiom approved as `t` authorised a `theorem A.t`
        # through the leaf rule below, and `completion` reading the registry by
        # the same rule then let the *axiom's* label answer for the theorem's.
        registered = {
            item["formal_name"] for item in self.state["names"]
        } - self._approved_assumptions()
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
        # An approved axiom that only an *unfinished* proof leans on is not a
        # claim owed to a reader yet, so its appendix obligation is not what
        # stops the next skeleton either. It is still owed -- it stays in the
        # obligations, on the screen, and in what `report_result` refuses over
        # the moment the open theorem is named.
        disclosed = self._rests_on(self._settled_declarations())
        owed = [
            item
            for item in self._obligations()
            if item.kind != "open"
            and not (item.kind in {"appendix", "assumption"} and item.subject not in disclosed)
        ]
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

    def _document_policy(self) -> DocumentPolicy:
        return DocumentPolicy(
            bibliography_refusal=self._bibliography_refusal,
            stamp=self._stamp,
            vouch=self._vouched_references,
            stamp_writeup=self._stamp_writeup,
            registry=lambda: self.record.snapshot()["names"],
            owed_note=self._owed_note,
        )

    def _check_latex(self, path: str, source: str) -> ToolResult:
        return self.documents._check_latex(path, source, policy=self._document_policy())

    def _save_latex(self, path: str, source: str) -> ToolResult:
        return self.documents._save_latex(path, source, policy=self._document_policy())

    def _tex_root_source(self) -> str:
        return self.documents._tex_root_source()

    def _vouched_references(self, keys: tuple[str, ...]) -> str:
        return self.documents._vouched_references(keys, frozenset(entry.key for entry in self.papers.bibliography.entries()))

    def _bibliography_refusal(self, relative: str, source: str) -> str:
        return self.documents._bibliography_refusal(relative, source, self.papers.bibliography.regenerate)

    def _compilable_paths(self) -> list[str]:
        return self.documents._compilable_paths()

    def _tex_path(self, path: str) -> tuple[str, Path] | ToolResult:
        return self.documents._tex_path(path)

    def _tex_target(self, path: str) -> Path | ToolResult:
        return self.documents._tex_target(path)

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
            # Files no `\input` chain from the root reaches: in no PDF,
            # whatever they say.
            "tex_unreached": self._unreached_tex(),
            # Statements an independent reader refused. Reported rather than
            # left in the record alone: a model that cannot see why a name is
            # refused will keep proposing it, and a reader of the workspace
            # is owed the list of what was tried and rejected.
            #
            # The most recent ones, and how many there are. The record keeps
            # every entry -- it is what `_final_gates` refuses from -- but this
            # listing is re-sent whole on every `read_workspace`, so a model
            # that keeps proposing bad Lean was growing its own context with
            # its own rejected statements until the turn died.
            "quarantine": self._recent_quarantine(),
            "quarantine_count": len(self.state.get("quarantine", ())),
            # The Lean this project may import but did not author, and which of
            # its own modules answer to a shared name instead. Reported rather
            # than left implicit: a model that cannot see the library cannot
            # import it, and one that cannot see a collision would cite a
            # theorem out of the wrong file.
            "shared": self._shared_listing(shadowed),
            "undocumented_theorems": list(self._undocumented()),
            # Saved theorems whose statement a single automation call closes
            # outright, by the tactic that closed each. A disclosure the
            # banner also prints, never an obligation: a lemma that falls to
            # one tactic is still a lemma, but a statement this list names may
            # assert far less than its name or the prose around it suggests.
            "automation": self.automation_closed(),
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
            if is_generated_bibliography(path):
                # Hardy's file, not the workspace's. Deleting it leaves every
                # `\input{references}` in the writeup unresolvable until the
                # next citation puts it back, which is a broken document
                # produced by a tool call that looked like tidying up.
                return ToolResult(
                    False,
                    f"{GENERATED_BIBLIOGRAPHY} is generated by Hardy from bibliography.json "
                    "and is not the workspace's to delete",
                )
            return self._delete_tex(target, path)
        relative = safe_relative(str(path).replace("\\", "/"))
        owned = self._generated_module_refusal(relative)
        if owned is not None:
            # Deleting it would silently drop axioms a human approved while
            # the record still lists them, so the record and the tree would
            # disagree about what the work rests on.
            return ToolResult(False, owned)
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
            # And so does imported provenance: an entry naming a path that no
            # longer exists would attribute whatever is saved there next to
            # the old origin and digest.
            self._forget_import(f"{LEAN_DIR}/{relative.as_posix()}")
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
        # The source-level rule too, over the tree as it will be once this file
        # is gone. Checked before the unlink so a refusal costs nothing to undo.
        refusal = self._bibliography_refusal(relative, "")
        if refusal:
            return ToolResult(
                False, f"the writeup cannot be published as it stands, so {path} was kept: {refusal}"
            )
        compiled_against = self._bibliography_identity()
        guard, name = guard_for(self.tex_root, relative)
        kept = read_text(self.tex_root, relative)
        # BEFORE the unlink, not after the failure. Everything below can
        # publish `writeup.pdf`, and clearing the stamp afterwards is itself
        # a write to `session.json` -- one that can fail for the same reason
        # the publish did. A disk that filled between the PDF and the
        # auxiliary file left the old signature on disk beside the new PDF,
        # and a restart read it back and accepted the mismatch. Recovery
        # cannot depend on a write taken after the thing it is recovering
        # from.
        #
        # So the claim is dropped while there is still room to drop it, and
        # put back only on the paths that establish it again. The window is
        # the other way round now: an interruption anywhere in here leaves
        # the writeup reading stale, which is what it is.
        stamped = self.state.get("tex_signature", "")
        self._unstamp_writeup()
        guard.unlink(name)

        def _restamp() -> None:
            """Put the claim back, for a path that changed nothing after all.

            A compile the checker refused published nothing -- `check`
            publishes only once the compile resolves -- and `_restore` has
            put the tree back byte for byte, so the signature stamped before
            the deletion describes it again. Without this a refused deletion
            would leave a perfectly good writeup reading stale, and the only
            way out is a save that changes nothing.
            """
            self.state["tex_signature"] = stamped
            self._save_state()

        def _restore() -> None:
            """Put the fragment back exactly as it was.

            The unlink has already happened by the time anything can go
            wrong, so every way out of the compile below runs this -- not
            only the one that returns a refusal. `vouched` reads
            `bibliography.json`, which raises when the store is unreadable or
            malformed; that exception came out of `check`, went past the
            restoration, and left the fragment permanently deleted by an
            operation the caller was told had failed.
            """
            guard.mkdir()
            with guard.open(name, "w", encoding="utf-8") as handle:
                handle.write(kept)

        root = self.tex_root / ROOT_DOCUMENT
        if root.is_file():
            try:
                checked = self.latex.check(
                    self._tex_root_source(),
                    tree=self.tex_root,
                    output_dir=self.workspace,
                    aux_dir=self.workspace / BUILD_DIR_TEX,
                    # This path publishes writeup.pdf too, and re-stamps the
                    # signature afterwards. Without the banner, deleting a
                    # fragment silently replaced a stamped PDF with an
                    # unstamped one and recorded it as current.
                    stamp=self._stamp(),
                    # And it is a publish, so it owes the same bibliography
                    # gate a save does. Without it, deleting an unrelated
                    # fragment published whatever reference list the remaining
                    # files happened to build -- the ordinary
                    # unresolved-reference check sees nothing wrong with an
                    # invented `\bibitem` that resolves.
                    vouched=self._vouched_references,
                )
            except BaseException:
                # The stamp is already gone -- dropped before the unlink, for
                # exactly this. `check` publishes `writeup.pdf` and
                # `writeup.aux` once the compile resolves, and an exception
                # raised part of the way through that leaves a PUBLISHED
                # document built without this fragment while `_restore` puts
                # the fragment back. The tree would then match the signature
                # stamped before the deletion again, and `report_result`
                # would accept a PDF that does not describe it. A root using
                # `\IfFileExists` compiles happily either way, which is what
                # makes the mismatch reachable rather than theoretical.
                #
                # Clearing rather than rolling the outputs back: restoring a
                # PDF is itself a publish that can fail the same way, and
                # there is nothing to gain from a rollback that needs a
                # rollback. Nothing is written here at all, which is the
                # point -- a disk with no room left still recovers.
                _restore()
                raise
            if not checked.ok:
                _restore()
                # Nothing was published and the tree is back as it was, so
                # the claim dropped above is true again.
                _restamp()
                return ToolResult(False, f"the writeup no longer compiles without {path}, so it was kept:\n{checked.output}")
            # This compile is as good as a save's, and the tree it compiled is
            # the tree on disk -- so it is stamped like one. Without this a
            # deletion left a freshly compiled writeup reading as stale, and
            # the only way out was a save that changed nothing.
            self._stamp_writeup(compiled_against)
        # After the point of no return: a deletion the compile above refused
        # was restored, and its provenance must survive with it.
        self._forget_import(f"{TEX_DIR}/{relative}")
        return ToolResult(True, f"deleted {path}")

    # -- Ingestion (#112): an existing pile, triaged and promoted -----------
    #
    # Human-directed on purpose: there is no model tool here. A model that
    # could pull arbitrary host files into the audited tree would make "what
    # is in this workspace" a question about the whole machine, and weeding a
    # pile is the user's judgment call anyway. The slash command is the door.

    def triage_pile(self, pile: Path) -> ToolResult:
        """Sort a directory of existing files without touching any of them.

        The useful output of a first pass over a pile is a triage list --
        compiles clean / compiles with holes / does not compile / is not
        really mathematics -- not a refusal. Nothing is written into the
        project or the pile; the one durable effect is a transcript event
        recording each file's digest and verdict, which is the provenance a
        later promotion refers back to.
        """
        with self._gate:
            try:
                return self._triage_pile(pile)
            except (LayoutError, OSError) as error:
                return ToolResult(False, f"could not triage {pile}: {error}")

    def _triage_pile(self, pile: Path) -> ToolResult:
        candidate = pile.expanduser()
        if not candidate.is_dir():
            return ToolResult(False, f"{pile} is not a directory Hardy can read")
        resolved = candidate.resolve()
        problem = self.workspace.resolve()
        if resolved == problem or problem in resolved.parents or resolved in problem.parents:
            return ToolResult(
                False,
                f"{pile} is this project's own tree (or contains it); "
                "triage is for files that are not part of the project yet",
            )
        found = ingest.discover(resolved)
        if not found.lean and not found.tex:
            # The skips still get reported: a pile holding one symlinked
            # `.lean` and nothing else is not the same fact as an empty one,
            # and dropping the reasons here would break the promise that
            # nothing is silently omitted.
            reasons = "".join(f"\n  {note}" for note in found.skipped)
            return ToolResult(False, f"no .lean or .tex files under {resolved}" + (f"; not read:{reasons}" if reasons else ""))
        # Once, before any per-file Lean: an `import CommAlg` in the pile
        # resolves against an olean, and nothing builds that olean but this.
        if found.lean:
            self.build_shared()
        lean_rows = self._triage_lean(resolved, found.lean)
        if lean_rows is None:
            # Interrupted, so the verdicts gathered are not the pile's: every
            # remaining file would have graded "broken" only because its Lean
            # was stopped on arrival. A partial list recorded as the triage
            # would be a false record, so nothing is recorded at all.
            return ToolResult(False, f"triage of {resolved} was interrupted; nothing was recorded")
        tex_rows = self._triage_tex(resolved, found.tex)
        # A verdict is an answer about an environment, and expires with it:
        # the same identity that keys the olean cache and stamps every audit
        # verdict -- toolchain plus the shared-source digest -- and the
        # current signature of each saved module a pile file could have
        # imported, so a reader after a toolchain or dependency change can
        # tell these verdicts were not produced under it. A saved tree broken
        # enough that its signatures cannot even be computed -- a hand-edited
        # import cycle -- records the reason instead: the per-file verdicts
        # already carry that breakage where it applies, and crashing the
        # whole triage over the record's footnote would be backwards.
        try:
            signatures: dict[str, str] = self.lean_workspace.current_signatures()
        except (ImportCycle, LayoutError, OSError) as error:
            signatures = {"unavailable": str(error)}
        self._record({
            "type": "import_triage",
            "pile": str(resolved),
            "environment": self._environment,
            "project_signatures": signatures,
            "lean": [row.as_dict() for row in lean_rows],
            "tex": [row.as_dict() for row in tex_rows],
            "skipped": list(found.skipped),
        })
        return ToolResult(True, ingest.render(resolved, lean_rows, tex_rows, found.skipped))

    def _triage_lean(self, pile: Path, files: Sequence[PurePosixPath]) -> list[ingest.Triaged] | None:
        """One verdict per Lean file, each earned by an actual elaboration.

        The pile's readable files are copied into a scratch tree first, so
        that files importing each other triage the way they will build after
        promotion -- and so the compile never reads the pile itself through a
        workspace walk that would refuse the first symlink it met.

        None means the pass was interrupted. Esc reaches the Lean child in
        flight and `process.tracked` stops any spawned after it, but neither
        tells this loop to stop scheduling more -- so it asks between files,
        rather than grinding through the rest of the pile spawning children
        that each arrive only to be stopped.
        """
        rows: list[ingest.Triaged] = []
        texts: dict[PurePosixPath, tuple[bytes, str]] = {}
        for relative in files:
            try:
                content = Path(pile, *relative.parts).read_bytes()
            except OSError as error:
                rows.append(ingest.Triaged(str(relative), "", ingest.UNREADABLE, detail=str(error)))
                continue
            try:
                texts[relative] = (content, content.decode("utf-8"))
            except UnicodeDecodeError:
                rows.append(ingest.Triaged(str(relative), ingest.digest(content), ingest.UNREADABLE, detail="not UTF-8 text"))
        if not texts:
            return sorted(rows, key=lambda row: row.path)
        scratch = Path(tempfile.mkdtemp(prefix="hardy-ingest-"))
        try:
            source_root = scratch / "src"
            build_root = scratch / "build"
            for relative, (_, text) in texts.items():
                # Through the guard even though the scratch tree is Hardy's
                # own, seconds old: it is the idiom every project write uses,
                # and the walk that found `relative` is not the code that
                # writes it -- the guard re-proves each component at the
                # moment of the write, exactly as `stage` does for its shadow.
                guard, name = guard_for(source_root, relative, create=True)
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(text)
            build_root.mkdir()

            def compiling(module: str, src: Path, build: Path, source_file: Path) -> tuple[bool, str]:
                # The scratch build first, then the problem's own build and
                # the shared libraries: a pile file may import its neighbours,
                # this project's saved modules, or a reference library, and
                # triage must answer for the tree a promotion would create.
                lean_path = os.pathsep.join([str(build), self._lean_path()])
                result = self.lean.compile_module(src, build, source_file, lean_path=lean_path)
                return result.ok, result.output

            space = LeanWorkspace(
                source_root, build_root, compiling,
                environment=self._environment, external=self._external_stamp,
            )
            sources = space.sources()
            approved = self._approved_assumptions()
            # The problem's own saved sources, read once and BUILT per file
            # rather than as one whole-workspace pass up front. A saved tree
            # broken by a hand edit -- an import cycle, a module that no
            # longer compiles -- would fail that pass before the first pile
            # file was looked at, refusing (or crashing) a triage the broken
            # module may have nothing to do with. Building exactly the saved
            # modules each file imports keeps an unrelated breakage out of
            # its verdict and attaches a related one to it.
            mine = self.lean_workspace.sources()
            for relative, (content, text) in texts.items():
                if process.stopping():
                    return None
                rows.append(self._triage_one(space, sources, mine, approved, str(relative), content, text))
            # Asked once more after the last elaboration, not only before
            # each. Esc landing during the final file leaves an interrupted
            # Lean run graded "does not compile", and with no next iteration
            # to notice the stop, a completed triage would be recorded
            # carrying a verdict the interruption manufactured.
            if process.stopping():
                return None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return sorted(rows, key=lambda row: row.path)

    def _triage_one(
        self,
        space: LeanWorkspace,
        sources: dict[str, str],
        mine: dict[str, str],
        approved: set[str],
        posix: str,
        content: bytes,
        text: str,
    ) -> ingest.Triaged:
        sha = ingest.digest(content)
        if not ingest.looks_like_lean(text):
            return ingest.Triaged(posix, sha, ingest.NOTES)
        declared = tuple(name for name, _ in assumptions(text))
        unapproved = tuple(name for name in declared if name not in approved)
        notes: list[str] = []
        unreadable = unreadable_assumptions(text)
        if unreadable:
            notes.append(
                f"declares an axiom Hardy cannot read as `axiom NAME : STATEMENT` "
                f"({unreadable[0]}); promotion into the authored tree will refuse it"
            )
        # A name the pile and the project both use is a verdict caveat, not a
        # silent choice. The scratch build sits first on LEAN_PATH, so this
        # file elaborated against the PILE's copy -- a tree the advertised
        # one-file promotion cannot create, because the project's module of
        # that name cannot be overwritten. Said here so the verdict is read
        # for what it is.
        shadowed = sorted(set(internal_imports(text, sources)) & set(mine))
        if shadowed:
            notes.append(
                f"imports {shadowed} from the pile, but this project already saves "
                "modules of the same name: the verdict was graded against the pile's "
                "copy, which a one-file promotion cannot put in its place"
            )
        module = module_name(PurePosixPath(posix))
        if module in mine:
            notes.append(
                f"this project already saves a module named {module}; promotion to "
                "the same path will be refused as an overwrite"
            )
        verdict, complaint = self._triage_compile(space, sources, mine, text)
        return ingest.Triaged(
            posix, sha, verdict,
            detail="\n".join(part for part in (complaint, *notes) if part),
            axioms=declared, unapproved=unapproved,
        )

    def _triage_compile(
        self, space: LeanWorkspace, sources: dict[str, str], mine: dict[str, str], text: str
    ) -> tuple[str, str]:
        try:
            # Only the saved modules THIS file imports -- see `_triage_lean`
            # for why the whole workspace is not built up front. A cycle in
            # the saved tree surfaces here too, as this file's verdict rather
            # than as an exception ending the whole pass.
            saved = internal_imports(text, mine) if mine else ()
            failure = self.lean_workspace.build_modules(saved) if saved else None
            if failure is None:
                needed = internal_imports(text, sources)
                failure = space.build_modules(needed) if needed else None
        except ImportCycle as error:
            return ingest.BROKEN, str(error)
        if failure is not None:
            return ingest.BROKEN, f"import {failure.module} does not build: {ingest.brief(failure.output)}"
        result = self.lean.run_source(
            text, env={"LEAN_PATH": os.pathsep.join([space.lean_path(), self._lean_path()])}
        )
        if not result.ok:
            return ingest.BROKEN, ingest.brief(result.output)
        return (ingest.HOLES if self.lean.has_holes(text) else ingest.CLEAN), ""

    def _triage_tex(self, pile: Path, files: Sequence[PurePosixPath]) -> list[ingest.Triaged]:
        """What kind of thing each TeX file is; deliberately no compile.

        A stray fragment is not part of the one document until `writeup.tex`
        \\inputs it, so where it belongs is a decision about the document a
        human makes -- there is nothing to compile it against that would not
        presuppose that decision.
        """
        rows: list[ingest.Triaged] = []
        for relative in files:
            posix = str(relative)
            try:
                content = Path(pile, *relative.parts).read_bytes()
            except OSError as error:
                rows.append(ingest.Triaged(posix, "", ingest.UNREADABLE, detail=str(error)))
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                rows.append(ingest.Triaged(posix, ingest.digest(content), ingest.UNREADABLE, detail="not UTF-8 text"))
                continue
            # Over `uncommented` text: old piles keep commented-out preambles,
            # and `% copied from \documentclass{article}` is a fragment TeX
            # never reads as a document -- classifying it as one would hand
            # the user the wrong promotion guidance.
            verdict = ingest.DOCUMENT if "\\documentclass" in uncommented(text) else ingest.FRAGMENT
            rows.append(ingest.Triaged(posix, ingest.digest(content), verdict))
        return sorted(rows, key=lambda row: row.path)

    def import_lean(self, source_path: Path, dest: str | None = None) -> ToolResult:
        """Promote one outside Lean file into the authored tree, gates and all.

        Through the same save path every authored file takes -- assumption
        approval, the shadow build, dependents rebuilt, registered names
        preserved, the axiom audit -- because a file that arrived from outside
        gets no weaker a check than one Hardy wrote. What it skips is the
        authorship ratchet (`ratchet=False` at the save): those gates steer
        how a model writes NEW work, and an imported theorem's writeup debt is
        charged through the obligations instead of refused at the door.
        """
        with self._gate:
            loaded = self._read_import(source_path)
            if isinstance(loaded, ToolResult):
                return loaded
            origin, content, text = loaded
            try:
                relative = safe_relative(dest or origin.name)
            except WorkspacePathError as error:
                return ToolResult(
                    False,
                    f"{error}; pass a destination Lean accepts as a module path, "
                    f"e.g. /import lean {source_path} Imported.lean",
                )
            if (self.lean_workspace.root / relative).exists():
                return ToolResult(
                    False,
                    f"{LEAN_DIR}/{relative.as_posix()} already exists; importing never "
                    "overwrites work. Choose another destination or delete the file first.",
                )
            result = self._save_lean_unbraked(relative.as_posix(), text, ratchet=False)
            if not result.ok:
                return result
            entry = self._remember_import("lean", f"{LEAN_DIR}/{relative.as_posix()}", origin, content)
            return ToolResult(
                True,
                f"imported {origin} as {entry['path']} (sha256 {entry['sha256']})\n\n{result.output}",
            )

    def import_reference(self, source_path: Path, dest: str | None = None) -> ToolResult:
        """Bring one outside Lean file in as assumed background, not as work.

        The destination is the project's shared library (`.hardy/lean/`), the
        tree #109 reserved for exactly this: Lean the user brings but did not
        author here. No save gate runs -- reference material is not a claim --
        but nothing is weakened by that: the axiom audit elaborates whatever a
        saved theorem imports, so an axiom or a hole in a reference file is
        charged to every theorem resting on it exactly as before, and the
        arrival itself is recorded under the file's digest.
        """
        with self._gate:
            loaded = self._read_import(source_path)
            if isinstance(loaded, ToolResult):
                return loaded
            origin, content, text = loaded
            try:
                relative = safe_relative(dest or origin.name)
            except WorkspacePathError as error:
                return ToolResult(
                    False,
                    f"{error}; a reference module needs a path Lean accepts, e.g. CommAlg.lean",
                )
            shared = Layout(root=self.root, slug=self.workspace.name).shared_lean
            if (shared / Path(*relative.parts)).exists():
                return ToolResult(
                    False,
                    f"{HARDY_DIR}/lean/{relative.as_posix()} already exists; importing never "
                    "overwrites work. Choose another destination or remove the file first.",
                )
            try:
                guard, name = guard_for(shared, relative, create=True)
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(text.rstrip() + "\n")
            except (LayoutError, OSError) as error:
                return ToolResult(False, f"could not write into {shared}: {error}")
            # Compiled now rather than on the next Lean call, so the user is
            # told immediately when the library they just brought does not
            # build -- and so the shared identity moves before any verdict
            # could be stamped against the old tree.
            self.build_shared()
            notes = []
            if self._shared_failures:
                notes.append(
                    "shared libraries that do not build:\n  " + "\n  ".join(self._shared_failures)
                )
            declared = tuple(name for name, _ in assumptions(text)) + unreadable_assumptions(text)
            if declared:
                notes.append(
                    f"carries axiom declarations ({', '.join(declared)}): a theorem "
                    "importing this module will not save until each is approved through "
                    "request_assumption"
                )
            if self.lean.has_holes(text):
                notes.append(
                    "carries holes (sorry/admit): a theorem importing this module "
                    "will be reported as still open"
                )
            entry = self._remember_import("reference", f"{HARDY_DIR}/lean/{relative.as_posix()}", origin, content)
            message = (
                f"imported {origin} as {entry['path']} (sha256 {entry['sha256']}); "
                f"it is assumed background this project may import, not audited work"
            )
            if notes:
                message += "\n" + "\n".join(f"- {note}" for note in notes)
            return ToolResult(True, message)

    def import_tex(self, source_path: Path, dest: str | None = None) -> ToolResult:
        """Bring one outside TeX file into the writeup tree, via the save path.

        The compile-and-save gate is `save_latex`'s own. What a save cannot
        decide is where the file belongs in a document that already exists: a
        fragment is not part of the writeup until `writeup.tex` \\inputs it,
        and the answer says so rather than guessing at a place.
        """
        with self._gate:
            loaded = self._read_import(source_path)
            if isinstance(loaded, ToolResult):
                return loaded
            origin, content, text = loaded
            resolved = self._tex_path(dest or origin.name)
            if isinstance(resolved, ToolResult):
                return resolved
            relative, target = resolved
            if target.exists():
                return ToolResult(
                    False,
                    f"{TEX_DIR}/{relative} already exists; importing never overwrites "
                    "work. Choose another destination or delete the file first.",
                )
            result = self._save_latex(relative, text)
            if not result.ok:
                return result
            entry = self._remember_import("tex", f"{TEX_DIR}/{relative}", origin, content)
            message = f"imported {origin} as {entry['path']} (sha256 {entry['sha256']})"
            if not compiles_document(self._tex_sources(), relative):
                message += (
                    f"\n- not yet part of the writeup: nothing \\inputs {relative}. "
                    "Where it belongs in the document is yours to decide; the check "
                    "compiled it through a probe document only."
                )
            return ToolResult(True, f"{message}\n\n{result.output}")

    def _read_import(self, source_path: Path) -> tuple[Path, bytes, str] | ToolResult:
        """One outside file's identity and text, or the refusal to ingest it."""
        candidate = source_path.expanduser()
        try:
            origin = candidate.resolve(strict=True)
            if origin.is_dir():
                return ToolResult(
                    False,
                    f"{source_path} is a directory; /import brings in one file at a "
                    "time (triage the directory first to see what is in it)",
                )
            # Regular files only, for the same reason the pile walk requires
            # them: reading a FIFO with no writer blocks forever, with no
            # tracked child for Esc or a timeout to reach.
            if not origin.is_file():
                return ToolResult(False, f"{source_path} is not a regular file; Hardy will not read it")
            content = origin.read_bytes()
        except OSError as error:
            return ToolResult(False, f"{source_path} cannot be read: {error}")
        # Not from this problem's own tree. "Imported" is a provenance claim
        # -- this arrived from outside -- and recording the problem's own
        # authored work under it would make the record's origin classification
        # false; the reference variant would go further and reclassify
        # authored work as assumed background. Another project's tree is
        # still a legitimate origin: outside means outside this problem.
        problem = self.workspace.resolve()
        if origin == problem or problem in origin.parents:
            return ToolResult(
                False,
                f"{source_path} is inside this problem's own tree; importing is for files "
                "that arrived from outside. Authored work is edited with a save, not re-imported.",
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(False, f"{source_path} is not UTF-8 text; Hardy cannot ingest it")
        return origin, content, text

    def _remember_import(self, kind: str, path: str, origin: Path, content: bytes) -> dict[str, Any]:
        """The provenance an imported file gets instead of authorship.

        The record's ordinary entries imply Hardy wrote what they describe.
        For a file it did not, the honest statement is different in kind --
        this arrived from outside, here is where from, here is the digest of
        what arrived -- so it gets its own entry in the manifest (which the
        model reads too) and its own event in the transcript. The digest is
        over the arriving bytes, before the save normalised anything, which is
        what lets a reader check the record against the user's original file.
        """
        entry = {"kind": kind, "path": path, "origin": str(origin), "sha256": ingest.digest(content)}
        stored = self.state.setdefault("imported", [])
        stored[:] = [item for item in stored if item.get("path") != path]
        stored.append(entry)
        self._save_state()
        self._record({"type": "imported", **entry})
        return entry

    def _forget_import(self, path: str) -> None:
        """Drop the imported-provenance entry for a path that was deleted.

        The transcript keeps the arrival -- history is append-only -- but the
        manifest describes the workspace as it is now, and an entry naming a
        path that no longer exists would attribute whatever authored work is
        later saved at that path to the old origin and digest.
        """
        stored = self.state.get("imported")
        if not stored:
            return
        kept = [item for item in stored if item.get("path") != path]
        if len(kept) == len(stored):
            return
        if kept:
            self.state["imported"] = kept
        else:
            # Gone entirely rather than left as `[]`: a workspace that never
            # imported anything and one whose imports were all deleted should
            # read the same way, and an empty list in every manifest would put
            # a key in front of the model that means nothing.
            del self.state["imported"]
        self._save_state()

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
            path = str(arguments.get("path") or DEFAULT_LEAN_PATH)
            source = str(arguments["source"])
            result = self._check_lean(path, source)
            if result.ok:
                # Remembered by digest, not used to clear the streak outright:
                # see `_streak_refusal` for why a green check only ever lifts
                # the brake for the exact source it checked.
                key = self._streak_key(path)
                digest = self._save_digest(source)
                self._checked_green.setdefault(key, set()).add(digest)
            return result
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
        if name in PAPER_TOOL_NAMES:
            return self.papers.call(name, arguments)
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
            result = self._request_assumption(proposal)
            if not result.ok:
                # Every refusal is remembered under the name it was refused
                # for, so a later request under the same name can show a human
                # what changed -- gate refusal, probe refusal, and a plain
                # decline are all "not approved" from here.
                self.admission.rejected(proposal["formal_name"], proposal["lean_statement"])
            return result
        if name == "list_statements":
            return self._list_statements(
                str(arguments["paper_id"]), int(arguments.get("start", 1) or 1)
            )
        if name == "assume_statement":
            return self._assume_statement(
                {
                    key: str(arguments.get(key) or "")
                    for key in (
                        "paper_id",
                        "statement",
                        "formal_name",
                        "lean_statement",
                        "informal_statement",
                        "reason",
                        "kind",
                        "latex_name",
                    )
                }
            )
        if name == "report_result":
            claimed = arguments.get("theorems")
            return self._report_result(
                [str(item) for item in claimed] if isinstance(claimed, list) else [],
                str(arguments.get("summary") or ""),
            )
        return ToolResult(False, f"unknown tool: {name}")

    def _consume_search_evidence(self) -> None:
        return self.admission._consume_search_evidence()

    def _admission_elaborate_source(self, source: str):
        return self._run_lean_source(source, timeout=max(self.lean.timeout, PROBE_SECONDS))

    def _admission_refute_source(self, source: str):
        return self._probe_lean_source(source, timeout=max(self.lean.timeout, refute.PROBE_SECONDS))

    def _admission_operations(self) -> AdmissionOperations:
        return AdmissionOperations(
            probes=admission_policy.ProbeOperations(
                elaborate=self._admission_elaborate_source,
                refute=self._admission_refute_source,
            ),
            faithfulness=self._faithfulness,
            confirm=self.confirm,
            goal=self.goal,
            event=self._record,
            assumptions=lambda: self.record.snapshot()["assumptions"],
            admit=self.record.admit_assumption,
            revoke=self.record.revoke_assumption,
            locate=self.record.locate_assumption,
            quarantine=self.record.quarantine,
            persist=self._save_state,
            paper_statements=self._paper_statements,
            cite=self.papers.bibliography.cite,
            write_module=self._write_papers_module,
        )

    @property
    def _inspect_attempts_since_request(self):
        return self.admission.search.attempts

    @_inspect_attempts_since_request.setter
    def _inspect_attempts_since_request(self, value):
        self.admission.search.attempts = value

    @property
    def _inspected_since_request(self):
        return self.admission.search.inspected

    @_inspected_since_request.setter
    def _inspected_since_request(self, value):
        self.admission.search.inspected = value

    @property
    def _searched_since_request(self):
        return self.admission.search.searched

    @_searched_since_request.setter
    def _searched_since_request(self, value):
        self.admission.search.searched = value

    @property
    def _rejected(self):
        return self.admission._rejected

    @_rejected.setter
    def _rejected(self, value):
        self.admission._rejected = value

    def _request_assumption(self, proposal: dict[str, str]) -> ToolResult:
        return self.admission._request_assumption(proposal, search_available=self.search is not None, operations=self._admission_operations())

    # --- Assumed-paper libraries ------------------------------------------

    #: Where minted paper axioms live, and the one path that may write them.
    PAPERS_DIR = "Papers"

    def _paper_statements(self, paper_id: str):
        """The inventory of a held paper, with the record it belongs to.

        Both come back because every caller needs both: the statements to
        choose from, and the record whose identity a minted axiom carries.
        """
        record = self.papers._held(paper_id)
        identifier = record.identifier
        if not self.papers.library.holds_source(identifier):
            raise ArxivError(
                f"{record.arxiv_id} has no source in the library, and a statement can only be "
                "assumed from the paper's own words. Call fetch_source first."
            )
        return record, assume_module.survey(self.papers.library.source_texts(identifier))

    def _list_statements(self, paper_id: str, start: int = 1) -> ToolResult:
        """What the paper claims, bounded and resumable. Nothing is minted.

        The whole point of listing separately from minting: reading a paper
        that states ninety results must cost nothing in trust, and the axioms
        that follow must be the ones the proof actually needed.
        """
        try:
            record, reading = self._paper_statements(paper_id)
        except (ArxivError, assume_module.AssumeError) as error:
            return ToolResult(False, str(error))
        statements = reading.statements
        if not statements:
            # Which document was read, and which were not. "The paper states
            # nothing" is a claim about the paper, and this branch returned
            # before the payload carrying `unread_documents` was built -- so
            # a bundle whose root was chosen wrongly said the paper is silent
            # and never mentioned the file it had not opened.
            elsewhere = (
                f" It read {reading.root} and not {list(reading.unread)}; if the paper is "
                "one of those, read it with read_paper."
                if reading.unread
                else ""
            )
            return ToolResult(
                False,
                f"{record.arxiv_id} states nothing Hardy recognises as a theorem, lemma, "
                f"proposition or definition.{elsewhere} Read its source with read_paper "
                "instead.",
            )
        first = max(1, start)
        # Bounded like every other observation, and resumable rather than
        # clipped: a paper's last theorem is as assumable as its first, and a
        # listing that silently stops is indistinguishable from a paper that
        # stops there.
        shown: list[dict[str, Any]] = []
        for item in statements[first - 1 :]:
            shown.append(item.as_dict())
            payload = self._statements_payload(record, reading, first, shown)
            if len(payload.encode("utf-8")) > self.papers.observation_bytes:
                shown.pop()
                break
        if not shown:
            return ToolResult(
                False,
                f"statement {first} of {record.arxiv_id} does not fit the "
                f"{self.papers.observation_bytes}-byte observation budget",
            )
        return ToolResult(True, self._statements_payload(record, reading, first, shown))

    def _statements_payload(
        self, record: Any, reading: Any, first: int, shown: Sequence[dict[str, Any]]
    ) -> str:
        following = first + len(shown)
        statements = reading.statements
        # The root actually read, and every other document in the bundle that
        # was not. Which root wins is decided by a filename, so a bundle
        # carrying a second one has a whole document going unlisted -- and a
        # reader weighing an assumption is owed that rather than left to infer
        # the paper states only what this names.
        unread = list(reading.unread)
        return json.dumps(
            {
                "paper_id": record.arxiv_id,
                "total": len(statements),
                "statements": list(shown),
                **(
                    {"next_start": following}
                    if following <= len(statements)
                    else {}
                ),
                # Said rather than left to silence: a listing cut at the bound
                # looks exactly like a paper that stops there.
                **({"truncated": True} if reading.truncated else {}),
                **({"unread_documents": unread} if unread else {}),
                "note": (
                    "Nothing here is assumed. assume_statement mints one of these as an axiom, "
                    "after a human approves it. Assume only what your proof needs."
                ),
            },
            ensure_ascii=False,
        )

    def _assume_statement(self, request: dict[str, str]) -> ToolResult:
        return self.admission._assume_statement(request, search_available=self.search is not None, operations=self._admission_operations())

    def _mint(
        self,
        request: dict[str, str],
        record: Any,
        entry: Any,
        wanted: Any,
        namespace: str,
        qualified: str,
        kind: str,
    ) -> ToolResult:
        return self.admission._mint(request, record, entry, wanted, namespace, qualified, kind, operations=self._admission_operations())

    def _refutation_probe(self, statement: str) -> refute.Verdict:
        return admission_policy.refutation_probe(statement, run_source=self._admission_refute_source)

    def _faithfulness(
        self, request: dict[str, str], record: Any, wanted: Any, qualified: str
    ) -> tuple[bool, bool, tuple[str, ...]]:
        """Whether a reader was reached, whether it agreed, and what it found.

        Fail-closed in every direction: nothing here mints an axiom unless a
        reader was reached *and* agreed. But the two failures are not the same
        fact, and the record has to keep them apart. Quarantine is durable,
        nothing clears an entry, and `Papers/` is closed to hand-written
        saves -- so folding an unreachable reader into "the reader found this
        unfaithful" let one provider 503 blacklist a name for the life of the
        project under a verdict nobody ever reached.

        An answer that is not a review counts as not reached for the same
        reason: no verdict was obtained. What was obtained is a refusal the
        model can act on by trying again.
        """
        try:
            agreed, divergences = self._review_assumption(
                paper=f"arXiv:{record.arxiv_id} -- {record.title}",
                reference=wanted.ref,
                paper_text=wanted.text,
                formal_name=qualified,
                lean_statement=request["lean_statement"].strip(),
                informal_statement=request["informal_statement"],
            )
        except Exception as error:  # noqa: BLE001 - an unreachable reader is not an agreement
            return False, False, (f"the independent reader could not be reached: {error}",)
        if agreed is None:
            return False, False, tuple(str(item) for item in divergences)
        return True, bool(agreed), tuple(str(item) for item in divergences)

    def _review_assumption(
        self,
        *,
        paper: str,
        reference: str,
        paper_text: str,
        formal_name: str,
        lean_statement: str,
        informal_statement: str,
    ) -> tuple[bool | None, tuple[str, ...]]:
        """One independent read of one translation, on its own thread.

        `None` for "no review was obtained" -- an answer that is not a review
        is not a verdict, and the caller keeps that apart from a verdict of
        "this is not what the paper says".

        Independent of *context*, not merely of weights, for the reason
        `faithfulness.py` gives at length: a reader handed the conversation
        that produced a translation reads the translation through it. This one
        gets the paper's sentence and the Lean, no tools, and no session
        history.
        """
        runtime = self._make_runtime(
            model=getattr(self.runtime, "model", None),
            system_prompt=ASSUME_REVIEW_PROMPT,
            specs=[],
            dispatch=lambda name, arguments: ToolResult(
                False, "the reader is given no tools"
            ),
            cwd=self.workspace,
            session_id=None,
            observe=lambda event: None,
        )
        answer = runtime.ask(
            json.dumps(
                {
                    "paper": paper,
                    "reference": reference,
                    "paper_states": paper_text,
                    "lean_name": formal_name,
                    "lean_statement": lean_statement,
                    "informal_rendering": informal_statement,
                },
                ensure_ascii=False,
            )
        )
        return self._read_review(str(answer))

    @staticmethod
    def _read_review(answer: str) -> tuple[bool | None, tuple[str, ...]]:
        """The reader's verdict, or `None` where it did not give one.

        Parsed the way every structured answer in Hardy is: the first
        balanced JSON object in the text, because a model that adds a
        sentence around it has still answered.

        The verdict must be an actual boolean. `bool("false")` is `True`, so
        coercing the field let every non-empty string -- "false", "no",
        "disagree" -- read as agreement: a reader that refused and spelled
        out the divergence had its axiom minted anyway and its findings
        thrown away. And a field that is missing or of the wrong type is not
        a verdict of "no", it is no verdict: answering `False` there would
        quarantine the name durably on a model's paraphrase of its own
        schema, which is the same permanent blacklisting the unreachable
        case exists to avoid, arriving by the schema instead of the wire.
        """
        found = json_object(answer)
        try:
            payload = json.loads(found) if found else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return None, ("the reader did not answer with a review",)
        divergences = payload.get("divergences")
        reported = tuple(
            str(item) for item in (divergences if isinstance(divergences, list) else ())
        )
        agrees = payload.get("agrees")
        if not isinstance(agrees, bool):
            return None, (
                f"the reader's answer carried no verdict Hardy can read (agrees={agrees!r})",
            )
        return agrees, reported

    def _quarantine(
        self,
        request: dict[str, str],
        record: Any,
        entry: Any,
        wanted: Any,
        qualified: str,
        kind: str,
        divergences: tuple[str, ...],
    ) -> None:
        return self.admission._quarantine(request, record, entry, wanted, qualified, kind, divergences, operations=self._admission_operations())

    #: How many refused translations `read_workspace` shows. The record keeps
    #: all of them; this is what fits in a response that is sent again on
    #: every read.
    QUARANTINE_SHOWN = 10

    def _recent_quarantine(self) -> list[dict[str, Any]]:
        """The last few refusals, each cut to what a model has to act on."""
        held = list(self.state.get("quarantine", ()))
        return [
            {
                "formal_name": item.get("formal_name", ""),
                "lean_statement": str(item.get("lean_statement", ""))[:400],
                "divergences": [str(reason)[:400] for reason in item.get("divergences", ())][:5],
                "paper": item.get("paper", {}),
            }
            for item in held[-self.QUARANTINE_SHOWN :]
        ]

    def _quarantined_names(self) -> set[str]:
        return {str(item["formal_name"]) for item in self.state.get("quarantine", ())}

    def _papers_module_path(self, cite_key: str) -> str:
        return assume_module.module_path_for(cite_key)

    def _write_papers_module(
        self, record: Any, entry: Any, minted: Any, *, minted_already: bool = False
    ) -> str | ToolResult:
        """Regenerate one paper's module, with this statement added.

        Whole rather than appended to, and from the record rather than from
        the file, for the reason the bibliography is: a generated file
        assembled by successive edits drifts from what it is a rendering of,
        and there is then no answer to which of the two the run rests on.
        """
        relative = self._papers_module_path(entry.key)
        held = [
            assume_module.Minted(
                formal_name=str(item["formal_name"]).rsplit(".", 1)[-1],
                lean_statement=str(item["lean_statement"]),
                informal_statement=str(item["informal_statement"]),
                kind=str(item.get("kind") or "statement"),
                ref=str(item.get("paper", {}).get("ref", "")),
                heading=str(item.get("paper", {}).get("heading", "")),
                # Absent from records written before this was stored; those
                # modules render as they always did rather than failing.
                paper_text=str(item.get("paper", {}).get("text", "")),
            )
            for item in self.state["assumptions"]
            if item.get("paper", {}).get("cite_key") == entry.key
        ]
        # `held` is read from the record, which already carries this
        # statement when the caller recorded it first. Rendering it twice
        # would declare the same axiom twice and refuse the whole module.
        statements = tuple(held) if minted_already else (*held, minted)
        source = assume_module.render_module(
            cite_key=entry.key,
            arxiv_id=record.arxiv_id,
            title=record.title,
            statements=statements,
        )
        # Through the ordinary save path, so a minted module is checked,
        # built, and audited exactly as authored Lean is -- every gate, the
        # authorship ratchet included, since the module declares no `theorem`
        # for those to bite on anyway. `generated=True` says only that Hardy
        # wrote it, which is what lifts the refusal that keeps everything else
        # out of `Papers/`.
        result = self._save_lean_unbraked(relative, source, ratchet=True, generated=True)
        if not result.ok:
            return ToolResult(
                False,
                "the assumption was approved but its module could not be saved, so nothing "
                f"was minted: {result.output}",
            )
        return relative

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

    @staticmethod
    def _tex_ascii(value: str) -> str:
        """`value` escaped for TeX, and safe for a compiler that is not Unicode.

        The banner is injected into the author's own document, and the default
        interactive compiler is `pdflatex`, which stops on a character it has
        no mapping for. A Lean identifier is routinely Unicode -- `α`, `h₁` --
        and so is a stated goal, so naming one here could have failed every
        later `save_latex` over a character the author never typed, with an
        error pointing at a line Hardy wrote.

        The codepoint is spelled out rather than dropped or transliterated: a
        reader must still be able to tell *which* theorem is unproved, and a
        placeholder that lost the name would trade one dishonesty for another.
        Only applies here. The one-shot writeup path compiles with Tectonic,
        which reads Unicode happily, and mangling it there would be a loss.
        """
        return "".join(
            character if character.isascii() else f"[U+{ord(character):04X}]"
            for character in escape_tex_text(value)
        )

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

        The automation clause is the one thing here the obligations do not
        compute, because it is not an obligation: a statement one tactic
        closes is still a theorem, owing nothing. It is a recorded probe
        verdict, read through `_automation_closed` so it expires with the
        statement it was established against -- and it is in the banner
        because "1 theorem machine-checked" was true of a vacuous restatement
        of Sylow III while saying more than the theorem did.
        """
        owed = self._obligations()
        unbacked = sum(1 for item in owed if item.kind == "theorem")
        opened = {item.subject for item in owed if item.kind == "open"}
        checked, _ = self._theorem_counts()
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
            listed = ", ".join(self._tex_ascii(name) for name in sorted(opened))
            parts.append(
                f"{count} theorem{'' if count == 1 else 's'} here "
                f"{'is' if count == 1 else 'are'} still open ({listed})"
            )
        flagged = self._automation_closed()
        if flagged:
            # Named, for the open clause's reason: this is about particular
            # claims printed on the pages in front of the reader, and a count
            # alone leaves them unable to tell which. Disclosure, not judgment
            # -- a lemma that falls to one tactic is still a lemma; what the
            # banner must not do is count a vacuous statement under a grand
            # name on the same terms as one with content, silently.
            count = len(flagged)
            listed = ", ".join(
                f"{self._tex_ascii(name)} by {self._tex_ascii(tactic)}"
                for name, tactic in sorted(flagged.items())
            )
            parts.append(
                f"{count} theorem statement{'' if count == 1 else 's'} here "
                f"{'is' if count == 1 else 'are'} closed outright by a single "
                f"automation call ({listed})"
            )
        text = ". ".join(parts) + "."
        goal = self.goal()
        if goal:
            text += f"\\\\ Goal, as stated by the user: {self._tex_ascii(goal)}"
        return text

    def _tex_tree_digest(self, without: str | None = None) -> str:
        return self.documents._tex_tree_digest(without)

    def _tex_signature(
        self,
        open_names: Sequence[str] | None = None,
        *,
        sources: dict[str, str] | None = None,
        tex: dict[str, str] | None = None,
    ) -> str:
        return self.documents._tex_signature(stamp_inputs=lambda: self._stamp_inputs(open_names, sources), tex=tex)

    def _bibliography_identity(self) -> str:
        return self.documents._bibliography_identity()

    def _stamp_inputs(
        self, open_names: Sequence[str] | None = None, sources: dict[str, str] | None = None
    ) -> dict[str, Any]:
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
            # A theorem flagged as automation-closed after the compile is the
            # overstating direction too: the published banner goes on counting
            # it on the same terms as every other theorem, with the disclosure
            # missing. The flag clearing -- a strengthened statement, whose
            # verdict expires with it -- changes what the banner says as well,
            # and the signature cannot tell the two directions apart.
            "automation": sorted(self._automation_closed().items()),
            # A theorem that was closed when the PDF was compiled and has since
            # been reopened is the overstating direction: the banner goes on
            # calling it machine-checked. The signature cannot tell the two
            # directions apart, so closing a hole stales the writeup too -- and
            # costs nothing, because a theorem that has just closed owes the
            # document a label and its statement anyway, so it was going to be
            # recompiled regardless.
            "open": (
                sorted(self._open_theorems(sources)) if open_names is None else sorted(open_names)
            ),
        }

    def _shared_sources(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        """The shared Lean this workspace actually imports, by module name.

        The import CLOSURE of the saved sources, not every module the machine
        happens to offer. `~/.hardy/lean` is a personal library shared by every
        project on the machine, and copying all of it into a shareable report
        would disclose an unrelated body of work -- rendered verbatim, with the
        credential filter deliberately off, because these are audited sources.
        Exporting one project must not publish another. It would also have made
        the section's own heading false: it says "imports", and it meant "was
        lying around".

        Resolved project tree first, matching `shared_roots`' documented order.
        Lean takes the first module of a given name it finds on that path, so
        the export has to keep the same one -- otherwise the page carries a
        different source from the one the kernel elaborated and the audit
        graded, which is the failure this whole section exists to prevent.

        Read through the same guard `_shared_digest` uses, so a link anywhere in
        the tree is refused rather than followed. Keyed by module name because
        that is how a theorem refers to one and what a recipient matches their
        own copy against. An unreadable file is left out rather than taking the
        export down.
        """
        # Resolved the way the COMPILER resolves, not by a second rule
        # invented here. `_shared_listing` already states the shadowing half --
        # a name the problem's own tree declares is the problem's module, and
        # offering the shared one would advertise an import that silently means
        # something else -- and `_compile_path` states the nesting half: a
        # shared library sees only the libraries further out than itself, which
        # is why `shared_roots` is in resolution order. A page that resolved
        # differently from the build would show source the kernel never saw,
        # which is the one thing carrying these at all is for.
        trees = [
            (index, source, dict(self._modules_under(source)))
            for index, (source, _build) in enumerate(self.shared_roots)
        ]
        if not trees:
            return {}
        shadowed = set(self.shadowed_modules())

        def resolve(name: str, depth: int) -> tuple[int, Path, Path] | None:
            """Where an import from a module in tree `depth` actually lands."""
            if name in shadowed:
                # The workspace's own module wins, and it is already carried in
                # `lean`. Following the shared copy here would publish an
                # unrelated file under the name of one the page already has.
                return None
            for index, source, modules in trees:
                if index < depth:
                    continue
                if name in modules:
                    return index, source, modules[name]
            return None

        mine = self.lean_workspace.sources() if sources is None else sources
        found: dict[str, str] = {}
        seen: set[tuple[str, int]] = set()
        # The saved sources import at depth 0: they see every shared tree.
        pending: list[tuple[str, int]] = [
            (name, 0) for text in mine.values() for name in parse_imports(text)
        ]
        while pending:
            name, depth = pending.pop()
            if (name, depth) in seen:
                continue
            seen.add((name, depth))
            landed = resolve(name, depth)
            if landed is None:
                continue
            index, source, path = landed
            try:
                text = read_text(source, path.relative_to(source), errors="replace")
            except OSError:
                continue
            found[name] = text
            # Transitively, and from THIS module's depth: a personal library
            # importing `B` gets the personal `B`, not the project's.
            pending.extend((further, index) for further in parse_imports(text))
        return found

    def _effective_settings(self) -> dict[str, str]:
        """What this session was actually configured with, as the reader needs it.

        Only the settings that can change what the model was able to establish
        -- not every field of the file. A path or a project name says where the
        work happened; the Lean timeout says whether an audit had time to come
        back, and the presence of a kernel or a search backend says whether a
        whole class of observation was available at all.

        Strings rather than numbers, because this is for a human reading a page
        and not for a machine to compare. The digests that automation compares
        are the toolchain and environment identities beside it.
        """
        settings = {
            "Lean timeout": f"{self.lean.timeout:.0f}s per call",
            "Computer algebra": self.cas_detail
            or ("available" if self.cas is not None else "none: no kernel was configured"),
            "Literature search": self.search_detail
            or ("available" if self.search is not None else "none: no backend was configured"),
        }
        # Read off the runtimes that ENFORCE them rather than from a config
        # file read separately: what the page reports is what was actually in
        # force, and a setting overridden after startup would otherwise be
        # reported as whatever the file still says. A cell that times out, a
        # session budget that runs out, and an observation the model saw only a
        # summary of are three different reasons a computation is missing from
        # the record -- and a reader comparing two exports needs to be able to
        # tell a different question from a different budget.
        limits = self.limits
        if self.cas is not None:
            settings["Computer algebra limits"] = (
                f"{limits.cas_cell_seconds}s per cell, "
                f"{limits.cas_session_seconds}s per session, "
                f"{limits.cas_output_bytes} bytes captured"
            )
        if self.search is not None:
            settings["Literature search budget"] = (
                f"{limits.retrieval_seconds}s of wall clock across the session, "
                f"{limits.lean_process_seconds}s per Lean process it starts"
            )
        settings["Observed by the model"] = (
            f"{limits.model_observation_bytes} bytes per tool result; "
            "more than that was summarised"
        )
        return settings

    def _document_is_hardys(self, document: Path) -> bool:
        """Whether the PDF on disk is the one Hardy's last compile produced.

        A truthy `tex_signature` says only that Hardy compiled something in
        this workspace at some point. It says nothing about the bytes now at
        `writeup.pdf`, which a user may have replaced afterwards -- and an
        export that reads the signature alone credited Hardy with a document it
        never made. The digest stamped by `_stamp_writeup` is what answers the
        question actually being asked.

        A workspace stamped before the digest existed has the signature and no
        digest. That reads as "not established" rather than as "Hardy's": the
        page can say what it does not know, and must not guess in the direction
        of a stronger claim.
        """
        stamped = str(self.state.get("writeup_sha256") or "")
        if not stamped:
            return False
        try:
            return hashlib.sha256(document.read_bytes()).hexdigest() == stamped
        except OSError:
            # Unreadable is not evidence of authorship either.
            return False

    def _stamp_writeup(
        self, compiled_against: str | None = None, compiled_tree: str | None = None
    ) -> None:
        return self.documents._stamp_writeup(compiled_against, compiled_tree, signature_for=self._tex_signature, open_theorems=self._open_theorems, publish=self.record.publish_writeup, persist=self._save_state)

    def _unstamp_writeup(self) -> None:
        """Say that nothing on disk is known to describe the compiled writeup.

        Used where an operation that publishes went wrong partway: the
        outputs may or may not have moved, and the sources may have been put
        back, so the honest answer to "is the PDF current" is "no idea",
        which reads the same as "no". Never widened into a general reset --
        the open set is left alone, because it is a fact about the workspace
        rather than a claim about the PDF.
        """
        self.state["tex_signature"] = ""
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

    def _stale_writeup(
        self, sources: dict[str, str] | None = None, tex: dict[str, str] | None = None
    ) -> list[completion.Obligation]:
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
        written = self._tex_sources() if tex is None else tex
        if not written or stamped == self._tex_signature(sources=sources, tex=written):
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

    def _open_declarations(self, sources: dict[str, str] | None = None) -> set[str]:
        """Every saved declaration Lean reported resting on a hole.

        Read from the stored audit records, which are stamped with the build
        signature they were established under, and skipping the ones that no
        longer hold: a stale record is not evidence that a theorem is open, and
        it is not evidence that it is closed either. `_audit_gaps` already
        reports a stale record as its own obligation, so nothing is lost here.
        """
        try:
            signatures = self.lean_workspace.current_signatures(sources)
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

    def _settled_declarations(self) -> set[str]:
        """Every audited declaration that does *not* rest on a hole.

        The counterpart of `_open_declarations`, and read the same way: from
        the stored records, skipping the ones no longer established. What it is
        for is attributing an approved assumption to finished work or to
        unfinished work, which are owed at different moments.
        """
        try:
            signatures = self.lean_workspace.current_signatures()
        except ImportCycle:
            return set()
        found: set[str] = set()
        for module, record in self.state.get("audit", {}).items():
            current = self._still_current(module, record, signatures)
            if current.get("stale"):
                continue
            opened = set(audit.open_declarations(current))
            found.update(
                str(entry.get("name"))
                for entry in current.get("declarations", ())
                if str(entry.get("name")) not in opened
            )
        return found

    def _open_theorems(self, sources: dict[str, str] | None = None) -> set[str]:
        """The open declarations that are theorems, which is what is reportable.

        An open `lemma` is reported to the model by the save's own audit note,
        which names every declaration in the rebuilt modules that rests on a
        hole. The obligations answer a narrower question -- what stands between
        this workspace and a report -- and a lemma was never reportable.
        """
        return self._open_declarations(sources) & self._saved_theorems(sources)

    def _audit_gaps(
        self, names: Iterable[str], snapshot: dict[str, str] | None = None
    ) -> list[completion.Obligation]:
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
            signatures = self.lean_workspace.current_signatures(snapshot)
        except ImportCycle as error:
            return [completion.Obligation("lean", "", f"the workspace does not order: {error}")]
        stored = self.state.get("audit", {})
        sources = self.lean_workspace.sources() if snapshot is None else snapshot
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
        names = [str(item) for item in names]
        # Validated here, before the attempt counter moves, the same way
        # `LeanService.inspect_declarations` validates before it runs `lean`.
        # That service raises on exactly this shape, but by the time it does
        # Lean was never started -- counting the call anyway let a malformed
        # request (an empty list, no `names` key, a natural-language query)
        # satisfy `_request_assumption`'s search-first gate and then tell the
        # human "none finished", which is false: nothing was ever asked.
        # Finding #2 of the second brutal review. Refusing with the same
        # words the service would means the model sees one error either way.
        if not 1 <= len(names) <= 20:
            return ToolResult(False, "declaration inspection requires between 1 and 20 names")
        if any(not DECLARATION_NAME.fullmatch(item) for item in names):
            return ToolResult(False, "declaration names must be qualified Lean identifiers")
        # Counted whether or not this finishes: a machine on which Lean keeps
        # timing out still attempted a search, and `_request_assumption`'s
        # gate needs to be able to tell that apart from never having tried.
        self.admission.attempted_inspection()
        result = self.search.inspect_declarations(names)
        if result.ok:
            self._note_inspected(names, result.output)
        return result

    def _note_inspected(self, names: list[str], output: str) -> None:
        return self.admission._note_inspected(names, output)

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

    @property
    def _tool_tally(self) -> dict[str, list[int]]:
        return self.turns._tool_tally

    @_tool_tally.setter
    def _tool_tally(self, value: dict[str, list[int]]) -> None:
        self.turns._tool_tally = value

    def _turn_persistence(self) -> TurnPersistence:
        return TurnPersistence(
            event=self._record,
            remember_thread=self._remember_thread,
            current_turn=self._from_the_turn_in_flight,
            read_usage=lambda: self.record.usage,
            publish_usage=self.record.publish_usage,
            mark_read=self._mark_ledger_read,
            end=self._transcript_end,
        )

    def stream(self, text: str) -> Iterator[TurnEvent]:
        return self.turns.stream(text, runtime=self.runtime, persistence=self._turn_persistence(), steering=self._steering_block, reset_formal=self.formal.begin_turn, resume_work=self.resume_work, closing_notice=self._closing_notice)

    def _stream(self, events: Iterator[TurnEvent]) -> Iterator[TurnEvent]:
        # An explicit `yield`, not `yield from`. A consumer that unwinds --
        # Ctrl+C in `--plain`, most of all -- closes this generator, and with
        # `yield from` that teardown would reach the runtime first: it
        # interrupts the model and then waits on its worker, all while this
        # session's tool gate is still open and the provider can dispatch one
        # more call. Yielding here means the gate shuts before any of that.
        return self.turns._stream(events, persistence=self._turn_persistence(), closing_notice=self._closing_notice)

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
        return self.turns.cancel(reason, runtime=self.runtime, interrupt_work=self.interrupt_work, persistence=self._turn_persistence())

    def resume_work(self) -> None:
        return self.turns.resume_work(self.cas.session if self.cas is not None else None)

    def interrupt_work(self) -> int:
        return self.turns.interrupt_work(self.cas.session if self.cas is not None else None)

    def escalate(self) -> int:
        return self.turns.escalate(self.cas.session if self.cas is not None else None)

    def record_abandonment(self, reason: str) -> None:
        return self.turns.record_abandonment(reason, self._turn_persistence())

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return self.turns._dispatch(name, arguments, tool=self._tool, persistence=self._turn_persistence())

    def _refuse_cancelled(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return self.turns._refuse_cancelled(name, arguments, self._turn_persistence())

    def _transcript_identity(self, length: int | None = None) -> dict[str, Any]:
        return self.record._transcript_identity(length)

    def _carried_thread(self) -> str | None:
        return self.record._carried_thread()

    def _discard_thread(self) -> str:
        return self.record._discard_thread()

    def _remember_thread(self) -> None:
        return self.record._remember_thread(getattr(self.runtime, "session_id", None))

    def _recover_spend(self) -> Usage:
        return self.record._recover_spend()

    def _transcript_end(self) -> int:
        return self.record._transcript_end()

    def _ledger_cursor(self, *, fresh: bool) -> int:
        return self.record._ledger_cursor(fresh=fresh)

    def _mark_ledger_read(self, offset: int | None = None) -> None:
        return self.record._mark_ledger_read(offset)

    def _recorded(self, start: int = 0) -> Iterator[dict[str, Any]]:
        return self.record._recorded(start)

    def _remember_spend(self, event: dict[str, Any], offset: int, *, unreported: bool = False) -> None:
        return self.turns._remember_spend(event, offset, unreported=unreported, persistence=self._turn_persistence())

    def _skip_spend(self, offset: int) -> None:
        return self.turns._skip_spend(offset, self._turn_persistence())
