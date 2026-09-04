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
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from . import audit, completion, ingest, process
from . import summary as summary_module
from .cas import CasError
from .cas_export import export_session
from .cas_tools import CAS_TOOL_NAMES, CAS_TOOLS, CasToolRuntime
from .domain import RunLimits
from .latex import ROOT_DOCUMENT, LatexTools, compiles_document, uncommented, unreached_fragments
from .layout import (
    HARDY_DIR,
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
from .lean import DECLARATION_NAME, LeanTools
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
    IDENTIFIER,
    QUALIFIED_NAME,
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
    parse_imports,
    safe_relative,
    statements,
    strip_comments,
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
# `automation` is withheld for `audit`'s reason exactly: the listing reports
# which saved statements one tactic closes checked against the tree in front of
# it, and the stored verdicts include entries whose statement has since moved.
WITHHELD = ("audit", "automation", PROJECT_CONTEXT_KEY)
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

# The head of a saved theorem's statement as `statements` reports it: the
# keyword, then the declared name, then the signature an anonymous `example`
# can carry verbatim. The name is `QUALIFIED_NAME` -- the same alphabet the
# declaration scan reads, where any component may be a `«...»` quotation
# carrying whitespace -- because a whitespace split read `«obvious` as the
# name of `theorem «obvious result» : True`, and a guillemet-only alternative
# still misread the qualified `theorem Foo.«obvious result» : True`. An
# explicit universe binder (`theorem vacuous.{u} ...`) is captured apart from
# both: it belongs to neither the name nor the signature -- `example` cannot
# carry one, so the probe redeclares its names with a `universe` command.
THEOREM_HEAD = re.compile(rf"^theorem\s+({QUALIFIED_NAME})(\.\{{[^}}]*\}})?\s*(.*)$")
# What may name a universe in that binder: `IDENTIFIER`, exactly. A binder
# this cannot read would put an unparseable `universe` command on the probe
# file's own lines, and a parse error there takes every verdict with it.
UNIVERSE_NAME = re.compile(rf"^{IDENTIFIER}$")

# Shown in `checked` when `_strip_hypotheses` refuses a statement that had
# hypotheses to strip. Distinct wording from every vacuity warning, so a
# reader -- and a test -- cannot mistake "the question was never asked" for
# "the question was asked and came back concerning".
VACUITY_STRIP_REFUSED = (
    "Hypothesis stripping was not attempted: the statement's binders could "
    "not be read, so the vacuity question was not asked."
)


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


_BINDER = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|\((?:[^()]|\([^()]*\))*\)")
_DATA_TYPE = re.compile(r"^(?:Type|Sort|Prop)\b|^(?:ℕ|ℤ|ℚ|ℝ|ℂ|Nat|Int|Rat|Real|Complex|Bool|String)$")


def _split_top(text: str, separator: str) -> list[str]:
    """`text` split on `separator` outside every bracket."""
    parts, depth, start = [], 0, 0
    index = 0
    while index < len(text):
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _mentions(name: str, text: str) -> bool:
    """Whether `name` occurs as a whole word in `text`.

    Lean's dot notation glues a name to what follows with `.` (`H.index`), so
    a boundary of `.` or `'` must not disqualify a match the way an ordinary
    word character would.
    """
    return re.search(rf"(?<![\w.']){re.escape(name)}(?![\w'])", text) is not None


_TOP_LEVEL_QUANTIFIER_SYMBOLS = "∀∃Σλ"


def _first_top_level_quantifier(text: str) -> int:
    """Index in `text` of the first `∀`/`∃`/`∃!`/`Σ`/`λ`/`fun` outside every
    bracket, or `len(text)` if none occurs.

    Marks where an arrow premise chain has to stop. `∃ f : α → Prop, …`
    holds an arrow that belongs to the bound variable's own type, not a
    premise separator -- splitting on it is the bug behind finding #3's
    first and fourth rows. Nothing at or past the first top-level quantifier
    is a candidate premise boundary, whatever punctuation it contains.
    """
    depth = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0:
            if character in _TOP_LEVEL_QUANTIFIER_SYMBOLS:
                return index
            before_ok = index == 0 or not (text[index - 1].isalnum() or text[index - 1] == "_")
            after = index + 3
            after_ok = after >= len(text) or not (text[after].isalnum() or text[after] == "_")
            if before_ok and after_ok and text.startswith("fun", index):
                return index
        index += 1
    return len(text)


def _split_top_before(text: str, separator: str, limit: int) -> list[str]:
    """`text` split on `separator` outside every bracket, using only splits
    that start strictly before `limit`.

    Everything from `limit` on -- see `_first_top_level_quantifier` -- lands
    unsplit in the final part, even if it contains `separator` itself.
    """
    parts, depth, start = [], 0, 0
    index = 0
    while index < limit:
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _first_top_level(text: str, separator: str, limit: int) -> int:
    """Index of the first top-level `separator` in `text[:limit]`, or -1.

    Same bracket-depth tracking as `_split_top`/`_split_top_before`, kept as
    its own function because `_strip_hypotheses` needs the *position* of an
    arrow relative to an equivalence, not a split on it.
    """
    depth = 0
    index = 0
    while index < limit:
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            return index
        index += 1
    return -1


# Returned by `_strip_hypotheses` when one of its fail-closed checks fires,
# so a caller can tell "Hardy could not read this safely" apart from `None`
# ("there was nothing here to strip"). The two used to be one value, and a
# bare `∀ n : ℕ, …` -- which has no hypotheses at all -- was reported to the
# human as unreadable exactly like a binder whose type really was lost.
UNREADABLE = object()


def _split_binder_colon(inner: str) -> tuple[str, str] | None:
    """A binder's inner text split on the first `:` that introduces its
    type, or None if it has none.

    Skips `:=` (a default value) and `::` (list cons, or a namespace
    separator) and any colon inside a further bracket, so `(hp:Nat.Prime 2)`
    -- no space around the colon -- is read as a hypothesis rather than,
    under a literal `" : "` search, as an untyped binder that is always
    kept.
    """
    depth = 0
    index = 0
    while index < len(inner):
        character = inner[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0:
            before = inner[index - 1] if index > 0 else ""
            after = inner[index + 1] if index + 1 < len(inner) else ""
            if after == ":":
                index += 2
                continue
            if before == ":" or after == "=":
                index += 1
                continue
            return inner[:index].strip(), inner[index + 1:].strip()
        index += 1
    return None


def _strip_hypotheses(statement: str) -> Any:
    """`statement` with its hypotheses removed; `None` if it has none; or
    `UNREADABLE` if reading its binders or its premise chain could not be
    trusted.

    What the vacuity probe elaborates, so a wrong answer here is not a
    missing warning but a false one shown to a human relying on it -- this
    fails closed rather than guess. Whitespace is collapsed with
    `normalise_lean` rather than a bare `split`/`join`, because the latter
    collapses whitespace inside string literals and `«…»` names too, turning
    `"a  b"` into `"a b"` -- a different Lean string reported as if it were
    the one the statement actually has. Returns `UNREADABLE` outright when a
    `"` or `«` survives that collapse, because every split below (on `, `,
    ` → `, ` ↔ `) has no notion of a literal and cannot tell a separator
    sitting inside one from one that actually separates binders or premises.
    Also returns `UNREADABLE` when a
    strict-implicit binder (`⦃…⦄`) appears anywhere, since `_BINDER` has no
    alternative for that bracket and would silently drop it and its name;
    when a top-level `↔` precedes the first top-level arrow in the body,
    since `↔` binds looser than `→` and that arrow is not a premise
    separator at all but sits inside the equivalence's own right-hand side
    (`A ↔ B → C` is `A ↔ (B → C)`) -- splitting on it anyway reports a
    hypothesis the statement never had; and when `binders` holds text
    `_BINDER` did not consume *and* the binder list was written with at
    least one wrapping bracket (`(…)`/`{…}`/`[…]`), because a nested paren
    one level deeper than `_BINDER` handles is losing real hypothesis text.
    A binder list with no wrapping bracket at all (`∀ n : ℕ, …`, `∀ x ∈ s,
    …`) has never been something this function could parse, and if there is
    also no top-level premise arrow behind it, nothing was going to be
    stripped even had it parsed -- that case returns `None`, not
    `UNREADABLE`, so an entirely ordinary quantifier is not reported to the
    human as unreadable. A binder is a hypothesis unless it is an instance,
    its type is a universe or a known data type, its type is exactly a name
    bound earlier in the same statement, or it is depended on -- named in
    the conclusion, or in the type of another binder that is kept. An arrow
    premise before the statement's first top-level quantifier is always a
    hypothesis; an arrow at or after that quantifier is left untouched,
    because it sits inside the quantifier's own binder type rather than
    separating premises. Returns `None` when there is nothing to strip -- no
    leading `∀`/`forall`, or one with nothing after a top-level comma -- so
    the caller probes the statement whole, exactly as it was given.
    """
    text = normalise_lean(statement).strip()
    if "⦃" in text or "⦄" in text:
        return UNREADABLE
    if '"' in text or "«" in text:
        # `normalise_lean` collapses whitespace literal-safely, but the
        # splits below (`_split_top` on `, `, `_first_top_level` on ` → `
        # and ` ↔ `) have no notion of a literal at all -- a separator
        # sitting inside a string or a guillemet-quoted name looks exactly
        # like one that actually separates binders or premises. Rather than
        # split blind and risk reporting a hypothesis the statement never
        # had (or hiding one it did), this fails closed.
        return UNREADABLE
    binders, body = "", text
    for keyword in ("∀ ", "forall "):
        if text.startswith(keyword):
            head = text[len(keyword):]
            parts = _split_top(head, ", ")
            if len(parts) < 2:
                return None
            binders, body = parts[0], ", ".join(parts[1:])
            break
    quantifier_index = _first_top_level_quantifier(body)
    arrow_index = _first_top_level(body, " → ", quantifier_index)
    iff_index = _first_top_level(body, " ↔ ", quantifier_index)
    # `↔` only corrupts the split when it precedes a top-level arrow; with no
    # such arrow there is no premise chain for it to mis-split, so an
    # arrow-free equivalence -- however many binders it carries, including
    # none -- is safe to strip and probe like any other statement.
    if arrow_index != -1 and iff_index != -1 and iff_index < arrow_index:
        return UNREADABLE
    premises = _split_top_before(body, " → ", quantifier_index)
    conclusion = premises[-1].strip()
    consumed = "".join(_BINDER.findall(binders))
    if "".join(consumed.split()) != "".join(binders.split()):
        bracket_led = bool(binders) and binders.lstrip()[:1] in "({[⦃"
        if not bracket_led and len(premises) == 1:
            return None
        return UNREADABLE
    if not binders and len(premises) == 1:
        return None

    # Each binder as [its group text, its names, its type text, whether kept].
    parsed: list[list] = []
    bound = set()
    for group in _BINDER.findall(binders):
        inner = group[1:-1]
        split = _split_binder_colon(inner)
        names, typ = (inner, "") if split is None else split
        keep = group[0] == "[" or split is None or bool(_DATA_TYPE.match(typ)) or typ in bound
        parsed.append([group, names.split(), typ, keep])
        if keep:
            bound.update(names.split())

    # A binder none of the rules above keep is still data if something kept
    # depends on it -- the conclusion, or the type of another kept binder.
    # Loop to a fixed point: keeping one binder is sometimes what makes an
    # earlier one, referenced only from that one's type, worth keeping too.
    changed = True
    while changed:
        changed = False
        kept_text = conclusion + " " + " ".join(entry[2] for entry in parsed if entry[3])
        for entry in parsed:
            if not entry[3] and any(_mentions(name, kept_text) for name in entry[1]):
                entry[3] = True
                changed = True

    kept = [entry[0] for entry in parsed if entry[3]]
    if kept:
        return f"∀ {' '.join(kept)}, {conclusion}"
    return conclusion


def _vacuity_source(stripped: str, *, include_probes: bool = True) -> tuple[str, list[str]]:
    """Build the vacuity probe's Lean source and tactics for this stripped statement.

    The integration test elaborates the same file, so there is one place the layout lives.

    `include_probes` is False when `stripped` strips nothing off the original
    statement: `_assumption_probe` has just run `PROBES` against that exact
    text and failed, so running them again here would ask Lean the same
    question twice and, if it happened to close, describe an unstripped
    statement as proved "with every hypothesis removed".
    """
    # Start with PROBES, add WITNESSES only if the conclusion is a plain ∃.
    tactics = list(MathematicsSession.PROBES) if include_probes else []

    # Extract the conclusion (after leading binders' top-level comma).
    if stripped.startswith("∀ "):
        conclusion = ", ".join(_split_top(stripped[2:], ", ")[1:])
    else:
        conclusion = stripped

    # `∃!` is unique existence: a bare witness (`⊥`, `⊤`) proves something
    # exists, never that it is the only one, so trying `WITNESSES` against a
    # `∃!` conclusion can only ever fail and is not worth the elaboration.
    stripped_conclusion = conclusion.lstrip()
    if stripped_conclusion.startswith("∃") and not stripped_conclusion.startswith("∃!"):
        tactics.extend(MathematicsSession.WITNESSES)

    # Build the Lean source.
    examples = "\n".join(f"example : {stripped} := by {tactic}" for tactic in tactics)
    source = f"import Mathlib\n\n{examples}\n"

    return source, tactics


class MathematicsSession:
    def __init__(self, workspace: Path, make_runtime: Callable[..., ChatRuntime], lean_command: tuple[str, ...], latex_command: tuple[str, ...], confirm: Callable[[dict[str, Any]], bool], lean_project: Path | None = None, lean_timeout: float = 180.0, cas: CasToolRuntime | None = None, cas_detail: str = "", search: SearchToolRuntime | None = None, search_detail: str = "", root: Path | None = None, project_context: bool = True, fresh_thread: bool = False, limits: RunLimits | None = None):
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
        # This session's own tool use, in memory only: it describes behaviour,
        # not the workspace, so it belongs in neither manifest.
        self._save_streak: dict[str, int] = {}
        # Streak key -> sha256 hex digests of sources that passed `check_lean`
        # on that path this turn. A green check on a path lifts the brake only
        # for the source it actually checked, not for whatever the model saves
        # next -- `check_lean` elaborates the source it is handed, never the
        # file, so a save of a *different* source has not been shown to fix
        # anything and must still count against the streak.
        self._checked_green: dict[str, set[str]] = {}
        self._tool_tally: dict[str, list[int]] = {"save_lean": [0, 0], "check_lean": [0, 0]}
        # Whether a *completed* `inspect_declarations` batch has run since the
        # last axiom request. `_searched_since_request`, below, carries what it
        # found. `_inspect_attempts_since_request` counts every call, whether
        # it completed or not: a machine whose Lean cannot finish still has to
        # let a request through eventually, or the search-first gate below
        # would refuse every `request_assumption` forever and blame a search
        # that was, in fact, attempted.
        self._inspected_since_request = False
        self._searched_since_request: list[str] = []
        self._inspect_attempts_since_request = 0
        # Prior statements this session requested under each name and did not
        # get approved, so a human sees a statement beside what it was
        # weakened from.
        self._rejected: dict[str, list[str]] = {}
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

    # Tried on the stripped statement when its conclusion is an existential.
    # `exact?` and `aesop` do not synthesise a witness, and the bad axiom the
    # failing run approved -- `∃ P : Subgroup G, P.Normal` -- is closed by the
    # first of these.
    WITNESSES = (
        "exact ⟨⊥, inferInstance⟩",
        "exact ⟨⊤, inferInstance⟩",
        "exact ⟨⊥, by simp⟩",
        "exact ⟨⊤, by simp⟩",
        "exact ⟨1, by simp⟩",
    )

    # Consecutive refused `save_lean` calls on one path before the next is
    # refused without running Lean. A failing run made 21 in a row.
    SAVE_STREAK_LIMIT = 3

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
        # An error before the probes even started -- `import Mathlib` failing
        # on line 1, or landing on line 2's blank line -- or after the
        # declaration is not about any probe tactic closing the goal. Reading
        # the *absence* of an error on a probe's own line as "that tactic
        # closed the goal" is only sound once Lean actually reached the
        # probes; an error here means it did not, and every probe line then
        # looks clean for the same reason a killed process would.
        stray = any(
            item.line is not None and (item.line < first_probe or item.line > declaration_line)
            for item in errors
        )
        # The declaration is read first, and an error Lean could not place is
        # read against it. A statement Lean will not accept fails on every probe
        # line too, and "every tactic failed" would otherwise be reported back as
        # a clean assumption.
        if unplaced or stray or declaration_line in placed:
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

    def _vacuity_probe(self, statement: str) -> str:
        """Whether the conclusion holds with the hypotheses gone. A warning or "".

        Run only after `_assumption_probe` returned no refusal, as its own
        elaboration: nothing here needs the axiom in scope, and the first
        file's layout is pinned by its tests. A statement `_strip_hypotheses`
        reports `UNREADABLE` is not probed, and says so -- that sentinel
        means there was something to say no to, unlike `None`, which means
        the statement never had hypotheses in the first place (a bare
        statement such as `True`, or an ordinary quantifier `_strip_hypotheses`
        was never going to strip anything from) and stays silent exactly as
        it always has. When every binder turns out to be data -- nothing was
        actually stripped -- `PROBES` is left out of the file
        `_vacuity_source` builds: `_assumption_probe` already ran them
        against this exact text and failed, so running them again would only
        risk describing that same, unstripped statement as proved "with every
        hypothesis removed".

        Reads each `example` line's diagnostic by its line number alone, which
        assumes Lean reached every `example` in the file: a parse-level error
        that aborts elaboration before the first one would leave every later
        line with no diagnostic of its own, and this would read that silence
        as every tactic having closed its goal. The same exposure
        `_assumption_probe` carries, for the same reason.
        """
        normalised = normalise_lean(statement).strip()
        stripped = _strip_hypotheses(normalised)
        if stripped is UNREADABLE:
            return VACUITY_STRIP_REFUSED
        if stripped is None:
            return ""
        hypotheses_removed = stripped != normalised
        source, tactics = _vacuity_source(stripped, include_probes=hypotheses_removed)
        if not tactics:
            # Nothing was stripped and the conclusion is not existential: there
            # is nothing left worth asking Lean that `_assumption_probe` has
            # not already asked.
            return ""
        try:
            result = self._run_lean_source(source, timeout=max(self.lean.timeout, PROBE_SECONDS))
        except Exception as error:  # noqa: BLE001 - a warning that cannot be computed is itself reported
            return f"The vacuity probe could not be run ({error})."
        if getattr(result, "timed_out", False) or getattr(result, "interrupted", False):
            return "The vacuity probe could not be run (the elaboration did not finish)."
        errors = [item for item in result.diagnostics if item.severity == "error"]
        if not result.ok and not errors:
            return "The vacuity probe could not be run (Lean failed without diagnostics)."
        # An error outside the `example` lines -- unplaced, or on `import
        # Mathlib`'s line 1 before the probes even ran -- is not a probe
        # having closed the goal. Reading the *absence* of an error on a
        # probe's own line as success is only sound once Lean actually
        # reached the probes; this is the same exposure `_assumption_probe`
        # carries, and a warning built on it would misreport a Lean failure
        # as the assumption being vacuous.
        tactic_lines = range(3, 3 + len(tactics))
        if any(item.line is None or item.line not in tactic_lines for item in errors):
            return "The vacuity probe could not be run (Lean failed before reaching the probes)."
        placed = {item.line for item in errors}
        for index, tactic in enumerate(tactics):
            line = 3 + index
            if line in placed:
                continue
            proof = _probe_suggestion(result, line) or f"by {tactic}"
            if hypotheses_removed:
                return (
                    "Lean elaborated this statement and could not prove it as stated — but "
                    f"proves it with every hypothesis removed (`{proof}`): the conclusion "
                    f"`{stripped}` holds without the hypotheses. This assumption may be vacuous."
                )
            return (
                "Lean elaborated this statement and could not prove it with standard "
                f"automation — but a direct witness closes it (`{proof}`). It is a "
                "theorem, not an assumption."
            )
        return ""

    def _automation_probe(self, proposed: Mapping[str, str]) -> dict[str, str] | None:
        """Which of these saved statements one `PROBES` tactic closes outright.

        The same ladder `_assumption_probe` runs against a proposed axiom,
        asked of theorems being saved -- because the handwave migrates: a live
        run, refused an axiom for Sylow III, saved

            theorem sylow_count_congruence ... :
                ∃ (n_p : ℕ), n_p ∣ Nat.card G ∧ n_p ≡ 1 [MOD p] := by aesop

        `n_p = 1` satisfies both conjuncts, the comment claimed Sylow, and the
        banner counted it machine-checked without a word. The answer here is a
        *disclosure*, never a refusal: plenty of legitimate scaffolding is
        `simp`-closable, and a lemma that falls to one tactic is still a
        lemma. What must not happen is the provenance banner counting it on
        the same terms as a theorem with content, silently.

        `proposed` maps each theorem's name to its statement as `statements`
        reports it -- `theorem NAME binders : type`, whitespace-normalised to
        one line, which is what keeps the line arithmetic below sound. Each
        becomes one `example` per tactic, rewritten to carry no name so the
        goal is real, plus one `sorry` sentinel: `sorry` closes any goal a
        statement that elaborates can pose (a warning, never an error), so an
        error on the sentinel line means the *statement* does not elaborate
        here -- section `variable`s left behind, a workspace-local definition
        -- and the five probe errors above it are about the statement, not the
        tactics. Without the sentinel that shape was recorded as "closed by
        nothing", which is a clean bill of health the probe never issued.

        `import Mathlib` alone, exactly as the assumption probe imports: the
        workspace's own modules are deliberately absent, because the theorem
        under question is already declared in one of them and `exact?` would
        close every statement by citing it -- the same self-citation
        `_assumption_probe` dodges by declaring the axiom last, which no
        ordering can dodge once the declaration lives in an import. The cost
        is stated rather than hidden: a statement that does not elaborate
        outside its workspace cannot be probed at all, and is reported as
        exactly that. A filter, not a decision procedure, in the sense
        `PROBES` documents.

        Returns each name mapped to the tactic that closed it, "" when every
        tactic was tried and failed, or None when the statement did not
        elaborate here; the whole answer is None when Lean could not be asked
        at all, and then nothing is stored, so the next save asks again.
        Every conclusion is drawn from which line an error landed on, so the
        reading rules are `_assumption_probe`'s -- an unplaced error, or one
        outside the `example` lines, means Lean never reached the probes --
        plus one of this probe's own: output that overflowed the process
        limit was cut before the later lines' diagnostics were written, and
        the silence of a line nobody heard from is not a tactic succeeding.
        """
        ordered = sorted(proposed)
        block = (*self.PROBES, "sorry")
        lines: list[str] = []
        signed: list[str] = []
        verdicts: dict[str, str | None] = {}
        universes: set[str] = set()
        for name in ordered:
            if "\n" in proposed[name] or "\r" in proposed[name]:
                # `normalise_lean` preserves a newline inside a string
                # literal, and every conclusion below is drawn from which
                # line an error landed on -- an example spanning several
                # physical lines would attribute its neighbours' errors to
                # the wrong tactic. Recorded as unanswered rather than
                # guessed at.
                verdicts[name] = None
                continue
            found = THEOREM_HEAD.match(proposed[name])
            if found is None or not found.group(3).strip():
                # No proposition to probe. A tree holding it could not have
                # built, so nothing real is lost by leaving it unanswered.
                continue
            binder = found.group(2)
            bound = [part.strip() for part in binder[2:-1].split(",")] if binder else []
            if bound and not all(UNIVERSE_NAME.match(part) for part in bound):
                # A binder the `universe` command below could not redeclare.
                # Emitting it anyway puts a parse error on the probe file's
                # own lines, which takes every statement's verdict with it.
                verdicts[name] = None
                continue
            universes.update(bound)
            signed.append(name)
            lines.extend(f"example {found.group(3).strip()} := by {tactic}" for tactic in block)
        if not lines:
            return verdicts
        preamble = "import Mathlib\n\n"
        first = 3
        if universes:
            # One command redeclares every statement's universe names:
            # `example` cannot carry a `.{u}` binder of its own, and without
            # this a universe-polymorphic theorem's examples referenced names
            # nothing bound. File-global on purpose -- universes have no
            # scope to collide in -- and it costs the line arithmetic exactly
            # one line, accounted for in `first`.
            preamble += f"universe {' '.join(sorted(universes))}\n"
            first = 4
        source = preamble + "\n".join(lines) + "\n"
        try:
            result = self._probe_lean_source(source, timeout=max(self.lean.timeout, PROBE_SECONDS))
        except Exception:  # noqa: BLE001 - an unrunnable probe withholds a disclosure, never a save
            return None
        if (
            getattr(result, "timed_out", False)
            or getattr(result, "interrupted", False)
            or getattr(result, "output_overflow", False)
        ):
            return None
        errors = [item for item in result.diagnostics if item.severity == "error"]
        if not result.ok and not errors:
            return None
        if any(
            item.line is None or item.line < first or item.line >= first + len(lines)
            for item in errors
        ):
            return None
        placed = {item.line for item in errors}
        for position, name in enumerate(signed):
            start = first + position * len(block)
            if start + len(block) - 1 in placed:
                # The sentinel errored: the statement itself does not
                # elaborate here, and the probe lines above it failed for
                # that reason rather than because any tactic was tried.
                verdicts[name] = None
                continue
            verdicts[name] = next(
                # The lines for one statement are in `PROBES` order, so the
                # first clean line is the earliest tactic -- and the order is
                # part of the message, exactly as it is for an axiom.
                (
                    tactic
                    for offset, tactic in enumerate(self.PROBES)
                    if start + offset not in placed
                ),
                "",
            )
        return verdicts

    def _refresh_automation(self) -> str:
        """Probe every saved theorem whose verdict is missing or expired, and
        record what came back. A note for the save's result, or "".

        Called after a save commits and before its state is written, so the
        verdicts land in the same `_save_state` the audit records do. Keyed by
        theorem name with the exact statement the verdict was established
        against and the toolchain it was established under, because those are
        what expire it: `_automation_closed` ignores a record either has moved
        out from under, and an expired record lands back in `needed` here. A
        record still current is not re-asked -- the answer depends on nothing
        but the statement and the environment, and `import Mathlib` costs the
        same tens of seconds every time.

        Over the whole tree rather than only the file just saved, for the
        price of the same single elaboration: a statement can move without its
        file being saved -- edited on disk, or its name taken over by another
        module's declaration while a shared-name obligation stands -- and
        probing only the saved file left that record expired until its own
        file happened to be saved again.

        A verdict of None -- the statement does not elaborate outside its
        workspace -- is stored as `"tactic": None`, which is a different fact
        from "": nothing closed it because nothing could be tried. It is
        named in the note once, and not re-asked while the statement stands,
        because the answer will not change until the statement does.

        The note is appended to the save's own result: the model that just
        saved a flagged theorem is the one that can still strengthen the
        statement, and telling it only through the banner tells it a compile
        too late.
        """
        current = self._theorem_statements()
        stored = self.state.setdefault("automation", {})
        for name in [found for found in stored if found not in current]:
            del stored[name]
        environment = self._probe_environment()
        needed = {
            name: text
            for name, text in current.items()
            if stored.get(name, {}).get("statement") != text
            or stored.get(name, {}).get("environment") != environment
        }
        if not needed:
            return ""
        probed = self._automation_probe(needed)
        if probed is None:
            return (
                "\n\nautomation probe: Lean could not be asked whether a single tactic "
                "closes these statements outright; nothing was recorded, and the next "
                "save will ask again."
            )
        for name, tactic in probed.items():
            stored[name] = {
                "statement": needed[name],
                "tactic": tactic,
                "environment": environment,
            }
        notes = []
        flagged = {name: tactic for name, tactic in probed.items() if tactic}
        if flagged:
            listed = ", ".join(
                f"`{name}` (by `{tactic}`)" for name, tactic in sorted(flagged.items())
            )
            notes.append(
                f"automation probe: a single automation call closes {listed} outright. "
                "Saved all the same -- this is a disclosure, not a refusal -- but the "
                "writeup banner, /status and read_workspace will all say so, because a "
                "statement one tactic closes may assert far less than its name or the "
                "prose around it suggests. If that is not what you meant to prove, "
                "strengthen the statement."
            )
        unreached = sorted(name for name, tactic in probed.items() if tactic is None)
        if unreached:
            names = ", ".join(f"`{name}`" for name in unreached)
            notes.append(
                f"automation probe: {names} could not be probed in isolation "
                "(section variables, a local definition, or a multi-line string "
                "literal), so whether one tactic closes it was not established in "
                "either direction."
            )
        return "".join(f"\n\n{note}" for note in notes)

    def _automation_closed(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        """Saved theorems one automation call closes: name to the tactic.

        Read from the recorded probe verdicts, and only while the statement a
        verdict was established against is still the statement saved and the
        toolchain is still the one it was asked under -- a record that
        outlives its inputs is the exact failure `_obligations`' "never
        stored" rule exists to prevent, so the expiry is checked here on every
        read rather than trusted to cleanup. The environment check is the
        audit's rule: what standard automation closes moves with Mathlib and
        the toolchain, and a verdict from another environment is not current.
        A theorem no probe has covered yet is simply absent, the same terms
        `state["audit"]` gives a module no save has covered.
        """
        stored = self.state.get("automation", {})
        if not stored:
            return {}
        current = self._theorem_statements(sources)
        environment = self._probe_environment()
        return {
            name: str(record.get("tactic"))
            for name, record in stored.items()
            if record.get("tactic")
            and current.get(name) == record.get("statement")
            and record.get("environment") == environment
        }

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

    def _tally(self, name: str, ok: bool) -> None:
        # Every tool name, not only `save_lean`/`check_lean`: `_steering_block`
        # reads this to decide whether the session has done *anything* at all,
        # and a session that got three axioms approved and wrote nothing else
        # must not read as having made no tool call.
        counts = self._tool_tally.setdefault(name, [0, 0])
        counts[0] += 1
        counts[1] += int(ok)

    def _streak_key(self, path: str) -> str:
        """The `_save_streak` key `path` counts against.

        Its safe-relative form, so `Main.lean` and `./Main.lean` share one
        streak instead of two half-sized ones nothing ever brakes. Falls back
        to `path` itself when `safe_relative` refuses it: the unbraked save
        refuses the same path for the same reason, so a streak keyed on a
        spelling Hardy will never accept costs nothing.
        """
        try:
            return str(safe_relative(path))
        except WorkspacePathError:
            return path

    @staticmethod
    def _save_digest(source: str) -> str:
        """The identity a green `check_lean` vouches for and a save spends.

        Hashed over `source.rstrip() + "\\n"` -- exactly what
        `_save_lean_unbraked` writes to disk -- rather than over `source`
        verbatim, so `check_lean(X)` vouches for `save_lean(X)` *and* for
        `save_lean(X + "\\n")`: the two calls write identical bytes to the
        workspace, and finding #4 of the second brutal review was this
        digest treating them as different sources and braking the second.
        """
        return hashlib.sha256((source.rstrip() + "\n").encode("utf-8")).hexdigest()

    def _streak_refusal(self, path: str, source: str) -> ToolResult | None:
        key = self._streak_key(path)
        if self._save_streak.get(key, 0) < self.SAVE_STREAK_LIMIT:
            return None
        # The brake promises "until `check_lean` passes on the exact source
        # you intend to save" -- so it is lifted only by a green check of
        # this exact source, not by any `check_lean` call that happens to
        # land on the same path. A save of something else has not been shown
        # to fix anything.
        digest = self._save_digest(source)
        green = self._checked_green.get(key)
        if green is not None and digest in green:
            # Spend the vouch: one green check admits one save, not every
            # save of that source for the rest of the turn. Finding #3 of
            # the second brutal review left this exemption permanent, so a
            # single `check_lean` on a byte string that then failed
            # `save_lean`'s stricter gates (result/documentation/shadow
            # build) bought an unbounded run of refused saves the brake
            # never fired on again.
            green.discard(digest)
            return None
        return ToolResult(
            False,
            f"{self.SAVE_STREAK_LIMIT} consecutive saves of `{path}` have been refused. "
            "Hardy will not elaborate another until `check_lean` passes on the exact "
            "source you intend to save on this path. Check a smaller piece — split "
            "the file, or reduce it to what already compiles — then save that "
            "checked source.",
        )

    def _save_lean(self, path: str, source: str) -> ToolResult:
        refusal = self._streak_refusal(path, source)
        if refusal is not None:
            return refusal
        result = self._save_lean_unbraked(path, source)
        key = self._streak_key(path)
        if result.ok:
            self._save_streak.pop(key, None)
        else:
            self._save_streak[key] = self._save_streak.get(key, 0) + 1
        return result

    def _save_lean_unbraked(self, path: str, source: str, *, ratchet: bool = True) -> ToolResult:
        try:
            relative = safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        # `ratchet=False` is how an imported file enters (#112). The two gates
        # it skips are authorship steering -- `theorem` reserved to registered
        # results, the writeup catch-up -- rules about how a model writes new
        # work, which an imported file has already been written without. The
        # verification gates all still run: assumption approval, the shadow
        # build, registered-name preservation, and the axiom audit are what
        # "no weaker a check than one Hardy wrote" means, and the writeup debt
        # an imported theorem brings is not waived either -- it lands in the
        # obligations like any other saved theorem's.
        gate = (self._result_gate(source) or self._documentation_gate(source)) if ratchet else None
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
            stale = self._closes_and_adds(source, affected, records)
            if stale is not None:
                return ToolResult(False, stale, source)
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
        # After the commit -- the answer is a disclosure about a saved theorem,
        # never a gate on saving one -- and before `_save_state`, so the
        # verdicts persist in the same write the audit records do.
        automation = self._refresh_automation()
        self._save_state()
        # Absent from `seen` when the source was byte-identical to what was
        # already built, so the cache skipped it. Nothing was wrong with it.
        result = seen.get(module, ToolResult(True, "unchanged; already built", source))
        return ToolResult(
            result.ok,
            f"{result.output}\n\naxiom audit: {note}{automation}{self._owed_note()}",
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

    def _tex_paths(self) -> list[str]:
        """Workspace-relative paths of every `.tex` file under the writeup root.

        An absent `tex/` is not an error -- a workspace that has not written
        any LaTeX yet is the ordinary starting state -- so this returns an
        empty list rather than raising, and both `_unreached_tex` and the
        steering block's omission check share this one place that decides it.
        Caught as `ValueError` rather than naming `WorkspacePathError`
        specifically: `files_under` raises the sibling `LayoutError` for a
        symlink anywhere under `tex/`, and both are `ValueError` subclasses,
        so one broad clause catches whichever this workspace produces.
        """
        if not self.tex_root.is_dir():
            return []
        try:
            return [relative.as_posix() for relative in files_under(self.tex_root, ".tex")]
        except (OSError, ValueError):
            return []

    def _unreached_tex(self) -> list[str]:
        """Writeup files no `\\input` chain from the root reaches.

        Read with `errors="replace"`, as `_tex_sources` is, so a `.tex` file
        that is not valid UTF-8 degrades to a fragment with replacement
        characters rather than raising. Each file is read inside its own
        `try`: one that cannot be read at all -- an `OSError`, or a
        `LayoutError`/`WorkspacePathError` from a symlink met between
        `_tex_paths`'s listing and this read -- is left out of `sources`
        rather than aborting the whole method, so one bad file does not hide
        every other file's orphan status.
        """
        paths = self._tex_paths()
        if not paths:
            return []
        sources: dict[str, str] = {}
        for path in paths:
            try:
                sources[path] = read_text(self.tex_root, path, errors="replace")
            except (OSError, ValueError):
                continue
        return unreached_fragments(sources)

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
        """What the workspace still owes, derived from the artifacts alone.

        Never stored. A flag saying the work was finished would outlive the
        file it described -- and the one thing this must not do is report a
        theorem as written up because it *was*, before the statement changed.

        A caller holding a snapshot of the trees passes it in, and everything
        below is derived from that one moment. `/export` is why: it prints the
        results from a snapshot and these obligations beside them, and editing
        a `.lean` file behind Hardy is supported. A later read that no longer
        declares an undocumented theorem answers "nothing outstanding" next to
        that same theorem's statement, which reads as a page saying the work is
        written up. A later read can ask for LESS, not only for more.
        """
        written = self._tex_sources() if tex is None else tex
        owed = completion.outstanding(
            theorems=self._saved_statements(sources),
            registry=self.state["names"],
            labels=self._labels(),
            assumptions=self.state["assumptions"],
            used=self._used_assumptions(sources),
            tex=written,
            # Open theorems owe nothing, but they are still saved theorems: they
            # back a `\begin{theorem}` the document asserts, and they decide
            # whether a leaf name is unambiguous. Left out, a document asserting
            # one read as backed by nothing.
            saved=self._theorem_statements(sources),
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
            for name, modules in sorted(self._shared_names(sources).items())
        ]
        # `_audit_gaps` is asked only about closed theorems. An open one has a
        # current audit record -- being current is how Hardy knows it is open --
        # so it has no gap to report, and reporting it would say the same thing
        # the `open` obligation beside it already says.
        opened = self._open_theorems(sources)
        holes = tuple(
            completion.Obligation("open", name, "still open -- rests on a hole")
            for name in sorted(opened)
        )
        return (
            *holes,
            *shared,
            *self._audit_gaps(self._saved_theorems(sources) - opened, sources),
            *self._stale_writeup(sources, written),
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

    def _current_audit(
        self, sources: dict[str, str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """The stored verdicts, each measured against the tree in front of us.

        `session.json` keeps a verdict for reference after the module beneath
        it has moved; `_still_current` is what says whether it still describes
        anything, and every existing reader of the audit -- `_open_theorems`,
        `_settled_declarations`, `_audit_gaps` -- goes through it. `/status
        --full` and `/export` must too, and the first version of both did not:
        they read `state["audit"]` raw, so a theorem whose toolchain had moved
        was rendered "kernel-verified" on the same page that reports its audit
        as no longer established. A disclosure that contradicts the obligation
        beside it is worse than none.

        A signature that cannot be computed -- a tree that does not order --
        expires everything rather than passing it through. `_audit_gaps`
        reports the cycle; nothing here may grade a workspace it cannot read.
        """
        stored = self.state.get("audit", {})
        try:
            signatures = self.lean_workspace.current_signatures(sources)
        except ImportCycle as error:
            return {
                module: {
                    **record,
                    "status": "not established",
                    "reason": f"the workspace does not order: {error}",
                    "stale": True,
                }
                for module, record in stored.items()
            }
        return {
            module: self._still_current(module, record, signatures)
            for module, record in stored.items()
        }

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
            "shared_sources": self._shared_sources(sources),
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
            # Files no `\input` chain from the root reaches: in no PDF,
            # whatever they say.
            "tex_unreached": self._unreached_tex(),
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
            if not compiles_document(self._tex_root_source(), relative):
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
                self._rejected.setdefault(proposal["formal_name"], []).append(
                    proposal["lean_statement"]
                )
            return result
        if name == "report_result":
            claimed = arguments.get("theorems")
            return self._report_result(
                [str(item) for item in claimed] if isinstance(claimed, list) else [],
                str(arguments.get("summary") or ""),
            )
        return ToolResult(False, f"unknown tool: {name}")

    def _consume_search_evidence(self) -> None:
        """Spend the search evidence gathered for the request just handled.

        Shared by every exit out of `_request_assumption` once the
        search-first gate has passed, because the evidence a request
        consults belongs to *that* request, not to whichever one asks next.
        A request the search gate itself refused never reaches here -- it
        looked at nothing, so there is nothing to spend -- but a request the
        gate let through and `_assumption_shape` or `_assumption_probe` then
        refused has: a human's `inspect_declarations` call was already
        looked at to decide the refusal, and letting it sit unconsumed let a
        next request under a different `formal_name` walk through the
        search gate on evidence that was never about it.
        """
        self._inspected_since_request = False
        self._searched_since_request = []
        self._inspect_attempts_since_request = 0

    def _request_assumption(self, proposal: dict[str, str]) -> ToolResult:
        if self.search is not None and self._inspect_attempts_since_request == 0:
            # Three axioms were approved on a failing run with the reason
            # "Mathlib does not expose this" and nothing had been searched
            # for. When search is available, a request is refused until
            # `inspect_declarations` has actually been *tried* since the last
            # request -- the reason given in `reason` is free text and proves
            # nothing on its own. Gated on attempts, not completions: a
            # machine whose Lean cannot finish an inspection still tried, and
            # refusing it forever with a message claiming nothing was even
            # attempted is the failure this fix exists to close. Below, once
            # an attempt has been made, the request goes through even if none
            # of them finished -- `searched` tells the human that state.
            return ToolResult(
                False,
                "no `inspect_declarations` has been run since the last assumption "
                "request. Look for the result before assuming it: pass several "
                "candidate spellings and let Lean say which exist.",
            )
        # Both gates below run before `confirm`. Nobody should be asked to
        # approve a statement Hardy has not read, and nobody should be asked
        # at all about one that could never be declared or that Lean proves
        # itself. Everything from here on is wrapped in `try`/`finally`: a
        # request that gets this far has passed the search gate, so evidence
        # was spent looking at it -- whether `_assumption_shape` or
        # `_assumption_probe` goes on to refuse it, or a human declines it,
        # or it is approved, the search that justified even asking is gone
        # either way, and the next request -- even under a different
        # `formal_name` -- owes a fresh one. Only the search gate itself,
        # above, returns without spending anything: it refused before any of
        # this request's evidence was looked at.
        try:
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
            # unchecked statement is owed the word "unchecked", and one whose
            # hypotheses turn out to be doing no work is owed that too. Only run
            # when the first probe actually elaborated: a caveat already means
            # Lean was unreachable or unreadable, and a second full Lean run
            # would spend up to PROBE_SECONDS on an answer `or caveat` discards.
            warning = self._vacuity_probe(proposal["lean_statement"]) if not caveat else ""
            # The elaboration sentence always leads: it is the one fact that is
            # true of every request that reaches this line, so a vacuity warning
            # or a strip-refused note is appended to it rather than displacing
            # it -- finding #5 of the second brutal review, where a stripper
            # refusal used to replace the only sentence saying Lean had read the
            # statement at all.
            elaborated = "Lean elaborated this statement and could not prove it."
            proposal["checked"] = caveat or (f"{elaborated} {warning}" if warning else elaborated)
            proposal["goal"] = self.goal()
            if self._inspect_attempts_since_request and not self._inspected_since_request:
                # Every attempt since the last request was stopped before it
                # could report anything -- `_searched_since_request` is empty for
                # the honest reason that nothing to put in it ever finished, not
                # because nothing was tried. Say which is true, in the human's
                # own count, rather than leave the list looking untouched.
                proposal["searched"] = [
                    f"{self._inspect_attempts_since_request} inspection(s) attempted "
                    "since the last request, none finished"
                ]
            else:
                searched = list(self._searched_since_request)
                if len(searched) > 20:
                    # A session that inspects in large batches across many
                    # requests can pile up a `searched` list a human is never
                    # going to read in full. Show the count and the most recent
                    # 20 -- what was just asked, not the whole session's
                    # history -- rather than let the field grow without bound.
                    searched = [f"{len(searched)} names inspected; last 20:"] + searched[-20:]
                proposal["searched"] = searched
            # A name refused or declined earlier this session gets its last
            # statement shown beside the new one: `sylow_unique_normal` lost a
            # conjunct between a refused request and an approved one, unseen,
            # because nothing put the two statements side by side.
            earlier = self._rejected.get(proposal["formal_name"])
            if earlier:
                proposal["previous"] = earlier[-1]
            # `checked`, `searched` and `previous` reach `confirm` but never
            # `record` below, so without this nothing durable ever says what
            # evidence the human was actually shown when they approved (or
            # refused) an axiom -- a nit from the second brutal review.
            self._record({
                "type": "assumption_prompt",
                "formal_name": proposal["formal_name"],
                "checked": proposal["checked"],
                "searched": proposal["searched"],
                "previous": proposal.get("previous", ""),
            })
            if not self.confirm(proposal):
                return ToolResult(False, "The user declined this assumption. Do not use it.")
            # `checked`, `goal`, `searched` and `previous` describe this one
            # request, not the assumption, and have no business in the durable
            # record.
            record = {key: value for key, value in proposal.items() if key not in {"checked", "goal", "searched", "previous"}}
            record["status"] = "user-approved"
            # Except the goal, kept under a name that says what it is. `goal`
            # is a singleton that `/goal` overwrites, so an export rendered
            # after the goal moved showed the NEW goal above an axiom approved
            # for the old one -- an approval attributed to a question it was
            # never asked about. Hardy sets `proposal["goal"]` from its own
            # state rather than taking the model's word for it, so this is the
            # workspace's goal at the moment the user said yes.
            # Written unconditionally, empty string included. Dropping the key
            # when no goal was set made "the user approved this with no goal in
            # front of them" indistinguishable from "this record predates the
            # field" -- and the renderers say different things about those. The
            # key's presence is the evidence that the question was asked.
            record["goal_at_approval"] = str(proposal.get("goal") or "").strip()
            # When a human said yes, in UTC. Additive, so `schema_version`
            # stays 2 for the reason `goal` gives: a record written before
            # this existed simply lacks the key, and every reader of it says
            # "date not recorded" rather than inventing one. An export meant
            # to leave the machine has to be able to say who approved what and
            # when (#105), and the transcript's own `assumption_prompt` event
            # is not enough on its own -- it records the asking, not the answer.
            record["approved_at"] = datetime.now(UTC).isoformat()
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
        finally:
            self._consume_search_evidence()

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

    def _tex_signature(
        self,
        open_names: Sequence[str] | None = None,
        *,
        sources: dict[str, str] | None = None,
        tex: dict[str, str] | None = None,
    ) -> str:
        """What the writeup tree hashes to, as a whole.

        `open_names` substitutes an open set for the one the workspace has now,
        which is how `_documentation_gate` asks what this signature *would* be
        if only that had not moved. Everything else is read live.
        """
        digest = hashlib.sha256()
        for path, source in sorted((self._tex_sources() if tex is None else tex).items()):
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
        digest.update(
            json.dumps(self._stamp_inputs(open_names, sources), sort_keys=True).encode("utf-8")
        )
        digest.update(b"\0")
        return digest.hexdigest()

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
        # And what the compile actually produced. A signature says only that
        # Hardy compiled *something* here once; a user who then drops another
        # `writeup.pdf` over it leaves the signature truthy and the bytes
        # someone else's, and the export would still credit Hardy with them.
        # Additive like the two above, and absent for a workspace stamped
        # before it existed -- which the reader is told rather than guessed at.
        document = self.workspace / "writeup.pdf"
        if document.is_file() and not document.is_symlink():
            self.state["writeup_sha256"] = hashlib.sha256(
                document.read_bytes()
            ).hexdigest()
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
        self._inspect_attempts_since_request += 1
        result = self.search.inspect_declarations(names)
        if result.ok:
            self._note_inspected(names, result.output)
        return result

    def _note_inspected(self, names: list[str], output: str) -> None:
        """Remember what a completed inspection asked, and what it found."""
        resolved: set[str] = set()
        try:
            payload = json.loads(output[output.index("{"):])
            resolved = {item["name"] for item in payload.get("resolved", [])}
        except (ValueError, KeyError, TypeError):
            # A hint line with no JSON after it, or JSON in an unexpected
            # shape -- either way, nothing was resolved as far as this can
            # tell, and every name below is recorded as not found.
            pass
        for name in names:
            self._searched_since_request.append(f"{name} {'✓' if name in resolved else '✗'}")
        self._inspected_since_request = True

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
        # Computed before the `user` event: the block reports the tally and
        # the workspace as they stood when the turn started, and it must
        # appear in the transcript ahead of the text it is prepended to, or a
        # reader replaying the record would see the model's context before
        # the human line that is supposed to have come first.
        block = self._steering_block()
        if block:
            self._record({"type": "steering", "text": block})
        self._record({"type": "user", "message": {"role": "user", "content": text}})
        # Cleared here rather than in `cancel`: a turn cancelled during the
        # previous exchange must not silently disarm this one's tool gate.
        self._cancelled.clear()
        # A new turn is a new chance; the tally is not reset, the streak is --
        # and with it, which sources a `check_lean` this turn has vouched for.
        self._save_streak.clear()
        self._checked_green.clear()
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
        # Sent to the model as one string ahead of what the person typed,
        # rather than as a separate message: the runtime's history is a
        # sequence of turns, and a block that arrived as its own turn would
        # read back as something one of the parties said, not as the
        # workspace's own arithmetic addressed to whoever reads next.
        return self._stream(self.runtime.stream(f"{block}\n\n{text}" if block else text))

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
            self._tally(name, result.ok)
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

    def _discard_thread(self) -> str:
        """Drop the resumable provider thread, and say what that amounted to.

        The returned sentence is the banner's and `/status`'s: a user who asked
        for `--fresh-thread` knows, but the next person reading the terminal
        does not.

        Only a thread `_carried_thread` would actually have resumed counts as
        discarded. A workspace with none -- a first open, a fresh clone, a
        thread whose transcript no longer fits it -- starts empty on every
        open, so the flag changed nothing and the transcript gets no event,
        exactly as an unchanged model or an unchanged `AGENTS.md` appends
        nothing. Asking for a discard with nothing to discard is a no-op, not
        a refusal: the condition the user asked for is the condition they get.

        When there is one, the local state is cleared FIRST and the event
        appended second, the reverse of `_sync_project_context`'s order and
        for the same crash-shaped reason: interrupted between the two, this
        way loses only the event -- the next open starts empty like any fresh
        clone, which the record already accounts for. The other way round, the
        record would say the conversation was discarded while the next open
        quietly resumed it.

        The event carries no thread id. The id is machine-local by design --
        the reason it lives in `.local/state.json` and not the record -- and
        the boundary the event marks is its own position in the transcript:
        turns above it were produced on a conversation the turns below have no
        memory of.
        """
        if self._carried_thread() is None:
            return "started fresh (--fresh-thread); there was no prior conversation to discard"
        for key in (THREAD_KEY, "transcript_length", "transcript_digest"):
            self.local.pop(key, None)
        self._save_local()
        self._record({"type": "thread", "reason": "fresh"})
        return "started fresh (--fresh-thread); the prior conversation was discarded"

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
