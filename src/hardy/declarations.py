"""Which declarations the installed packages ship, read from their sources.

This module exists because of a measured failure of the alternative. Lean's
own `#find` was the backend for declaration search, and on the pinned
toolchain (Mathlib 79d0395a1825, Lean 4.33.0-rc1) it never answered: run
directly, `#find IsSimpleGroup` and `#find _ + _ = _ + _` both still ran at
300 seconds, while `example (n : Nat) : n = n := by exact?` finished in 22 --
so Lean and Mathlib were healthy and `#find` specifically was not going to
answer. It was not a budget problem either: raising the process deadline from
30s to 180s moved the failure from a 30-second timeout to a 180-second timeout
and changed nothing else. The command exists -- it elaborates rather than
erroring -- but Hardy runs every search as a fresh `lake env lean` process,
and whatever `#find` does before its first result costs more than any
per-process budget Hardy can afford. Whether it would ever return was not
observed; ten times the budget was.

A session paid for that with a wrong conclusion: told nothing matched
`IsSimpleGroup`, it decided Mathlib has no notion of a simple group and
requested four classical theorems as axioms, all of which Mathlib proves. The
names it needed are sitting on disk in plain text, so this reads them the way
`modules.py` reads the module index: from the packages' own sources, once,
with Lean never running.

What a hit and a miss here each mean, said precisely because they are not
symmetric:

- A **hit** names a declaration head that is textually present in a package's
  sources, with the namespace prefix the surrounding `namespace` blocks give
  it. It is a lead, not evidence -- `inspect_declarations` is what confirms a
  name elaborates.
- A **miss** is weak evidence. The scan is textual: a name assembled by a
  macro, or written in grammar this does not model, is invisible to it. An
  empty answer therefore says "not in the index", never "not in Mathlib", and
  the tool text that carries one says so.

Only package sources are read -- the workspace's own files hold the model's
work in progress, and a package's nested `.lake` build tree can hold a stale
copy of anything. Two further filters narrow the read toward what the frozen
environment can actually reach, each degrading open when its evidence is
missing: a readable `lake-manifest.json` decides which package directories
count (Lake does not sweep a removed package's checkout), and a package's
root index files decide which of its modules count (a checkout ships test
trees and scripts its umbrella never imports). A hand-assembled project
without either is read whole -- the direction that costs precision rather
than hiding a lead. Private declarations are skipped: offering a name the
caller's own file could never elaborate would manufacture the exact wrong
lead this index exists to prevent.
"""

from __future__ import annotations

import heapq
import json
import re
import threading
from pathlib import Path

from .lean import (
    _ATTRIBUTES,
    _MODIFIERS,
    DeclarationRecord,
    DeclarationSearch,
    LeanDiagnostic,
)
from .workspace import ANY_NAME, QUALIFIED_NAME, WRAPPER, parse_imports, strip_comments

# One declaration head. The keyword list is Lean's surface grammar for named
# declarations; `example` is deliberately absent (anonymous by construction)
# and an `instance` without a name simply fails the name group and is skipped.
# `class inductive` is matched before `class` alone would swallow `inductive`
# as the name. An approximation of Lean's grammar, like every scan in Hardy
# that reads Lean as text, and documented as one where it is offered.
_KEYWORD = r"theorem|lemma|abbrev|structure|class(?:\s+inductive)?|inductive|instance|opaque|axiom|def"
# `workspace.WRAPPER` reads through `set_option x y in`, `open Foo in` and the
# rest of Lean's same-line command scoping, for the same reason it matters
# there: a declaration behind one is a declaration the sources really ship.
# Multiline (`(?m)`, matched over the whole blanked source) for the reason
# `workspace.DECLARATION`'s comment states: Lean allows a newline between the
# keyword and the name, and a line-oriented match loses the declaration
# entirely. The same crossing lets an attribute sit on its own line.
DECLARATION = re.compile(
    rf"(?m)^[ \t]*{WRAPPER}{_ATTRIBUTES}({_MODIFIERS})({_KEYWORD})\s+({QUALIFIED_NAME})"
)

# Scope lines. `namespace Foo.Bar` opens one scope per component, because that
# is how Lean closes them: `end Foo.Bar` closes both while `end Bar` closes
# only the inner one. Components are read with `ANY_NAME` rather than split on
# `.`, because `namespace «my scope»` is one component whose name may contain
# anything. A `section` may carry a name and may be `noncomputable`; a
# `mutual` block ends with a bare `end` that must not pop anything else.
_NAMESPACE = re.compile(rf"^\s*namespace\s+({QUALIFIED_NAME})\s*$")
# Anchored to the whole line, exactly like `workspace.SECTION`: a line that
# merely begins with the word must not push a scope whose phantom `end` then
# swallows a real namespace close.
_SECTION = re.compile(rf"^\s*(?:noncomputable\s+)?section(?:\s+{QUALIFIED_NAME})?\s*$")
_MUTUAL = re.compile(r"^\s*mutual\s*$")
_END = re.compile(rf"^\s*end(?:\s+({QUALIFIED_NAME}))?\s*$")
_COMPONENT = re.compile(ANY_NAME)

# The same bound `retrieval.py` puts on a signature a model reads, restated
# here so this module does not import the one that imports it.
_MAX_SIGNATURE_CHARACTERS = 512

# Where a declaration's head ends and its body begins: the definition marker,
# a structure's field block, or a standalone deriving clause. `\b` keeps
# `foo_where` whole. An approximation stated as one -- a binder default like
# `(prio := 100)` cuts the head early -- and the cost of cutting early is a
# shorter signature, never a wrong name.
_BODY = re.compile(r":=|\bwhere\b|\bderiving\b")

# The version of this index's *algorithm*: the scan grammar, the namespace
# model, and the search ordering. It travels in the source identity the way
# `retrieval.RANKER` travels in the provenance, because a ranking's order
# depends on this code as much as on the corpus text -- two Hardy revisions
# reading identical sources can order differently, and an identity that
# stayed byte-for-byte equal across them would let `reproducible` promise a
# replay neither can give the other. Any change to what `_file` reads, how
# names are qualified, or how `search` orders its answers must bump this.
INDEX_ALGORITHM = "hardy-declaration-index/1"

# Root files that are not module sources; same reasoning as `modules.py`.
_NOT_A_SOURCE = frozenset({"lakefile.lean"})


class DeclarationIndex:
    """The named declarations the installed packages ship, read once and held.

    Nothing invalidates it, exactly like `ModuleIndex`: a session holds one
    for its lifetime, and a Mathlib changing under a running session is out of
    scope. The read is lazy because it walks every `.lean` file Mathlib ships,
    which is seconds of work that only a session that searches should pay.
    """

    def __init__(self, project: Path | None) -> None:
        self.project = project
        # (name, lowercased name, signature, module, line) per declaration.
        # Plain tuples rather than models: Mathlib ships hundreds of thousands
        # of declarations, and a `DeclarationRecord` is built per *result*.
        self._records: list[tuple[str, str, str, str, int]] | None = None
        # One scan, even under concurrency. The index is shared between the
        # plain search and the ranking's source, and the MCP server can field
        # both at once; the retriever's admission lock serializes rankings
        # only, so without this both calls could see an unbuilt index and
        # each walk the whole source tree -- the one-time cost paid twice.
        self._guard = threading.Lock()

    def count(self) -> int:
        """How many declaration names were read, for a refusal to cite."""
        return len(self._read())

    @property
    def read(self) -> bool:
        """Whether the one read has happened yet.

        The retrieval meter admits a source against its declared worst case,
        and this index has two: a cold first call walks every source file a
        package ships, while a warm one is a substring pass over names already
        in memory. Which bound applies is a fact the index holds.
        """
        return self._records is not None

    def search(self, query: str, limit: int = 20) -> tuple[DeclarationRecord, ...]:
        """Declarations whose name contains the query's words, best match first.

        Case-insensitive substring per word, matching *any* word rather than
        all of them: names fuse words (`IsSimpleGroup` contains both `simple`
        and `group`), so a name matching every word of a concept-shaped query
        is almost always the name that was meant, and it sorts first. Within a
        tier, a match on the final component beats one buried in a namespace
        -- the ordering `ModuleIndex.search` settled on, for the same reason
        -- and the name itself breaks ties so the answer is deterministic.
        """
        tokens = [token.lower() for token in query.split()]
        if not tokens:
            return ()

        def matches():
            for name, lowered, signature, module, line in self._read():
                matched = sum(1 for token in tokens if token in lowered)
                if not matched:
                    continue
                leaf = lowered.rsplit(".", 1)[-1]
                in_leaf = any(token in leaf for token in tokens)
                yield (-matched, not in_leaf, name, signature, module, line)

        # Top-k, not sort-everything: a broad fragment can match hundreds of
        # thousands of names, and the caller wants at most a couple of dozen.
        # `nsmallest` over these tuples returns exactly what a full sort's
        # head would, name included in the key, so the answer stays
        # deterministic while a warm search stops scaling with its own
        # popularity.
        return tuple(
            DeclarationRecord(name=name, signature=signature, source_file=module, line=line)
            for _, _, name, signature, module, line in heapq.nsmallest(limit, matches())
        )

    def _read(self) -> list[tuple[str, str, str, str, int]]:
        if self._records is None:
            with self._guard:
                if self._records is None:
                    self._records = [] if self.project is None else self._scan()
        return self._records

    def _scan(self) -> list[tuple[str, str, str, str, int]]:
        assert self.project is not None
        records: list[tuple[str, str, str, str, int]] = []
        packages = self.project / ".lake" / "packages"
        named = self._manifest_packages()
        for root in sorted(path for path in packages.glob("*") if path.is_dir()):
            # Lake does not sweep a removed package's checkout, and a stale
            # directory's declarations are not in the environment the corpus
            # identity describes. Filtered only when the manifest can say so.
            if named is not None and root.name not in named:
                continue
            declared = self._declared_modules(root)
            for path in sorted(root.rglob("*.lean")):
                # A package can hold its own `.lake` build tree, which can hold
                # a stale copy of anything; only what the package ships counts.
                relative = path.relative_to(root)
                if ".lake" in relative.parts or path.name in _NOT_A_SOURCE:
                    continue
                module = ".".join((*relative.parts[:-1], relative.stem))
                # A checkout ships more than its library -- test trees,
                # scripts, modules the umbrella deliberately omits -- and
                # `import Mathlib` reaches none of it. The root index is the
                # list of what a package ships, the same reading `modules.py`
                # settled on, so where one exists it decides.
                if declared is not None and module not in declared:
                    continue
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    # An unreadable file costs its declarations, never the index.
                    continue
                records.extend(self._file(source, module))
        return records

    def _manifest_packages(self) -> set[str] | None:
        """The package names the manifest resolves, or None to read them all.

        None on any failure to read or parse, not an empty set: a project
        whose manifest is missing or malformed should degrade toward extra
        leads, never toward an index that silently holds nothing.
        """
        assert self.project is not None
        try:
            manifest = json.loads(
                (self.project / "lake-manifest.json").read_text(encoding="utf-8")
            )
            names = {
                str(package["name"])
                for package in manifest["packages"]
                if isinstance(package, dict) and "name" in package
            }
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
        return names or None

    def _declared_modules(self, root: Path) -> set[str] | None:
        """What this package's root index files say it ships, or None for all.

        The same files `ModuleIndex._index_files` reads, for the same reason:
        `Mathlib.lean` is thousands of lines of nothing but imports, and it is
        the package's own statement of which modules are the library. The
        stems join the set because an index ships the module it is named for.
        A package with no readable root index is scanned whole.
        """
        declared: set[str] = set()
        for index in sorted(root.glob("*.lean")):
            if index.name in _NOT_A_SOURCE:
                continue
            try:
                source = index.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            declared.update(parse_imports(source))
            declared.add(index.stem)
        return declared or None

    def _file(self, source: str, module: str) -> list[tuple[str, str, str, str, int]]:
        """One file's declaration heads, with their namespace prefixes.

        The scan runs over comment- and string-blanked text so a doc comment
        quoting `theorem foo` cannot invent a declaration, but the signature
        shown is the original line: it is context for a reader, and the
        blanked copy has holes where its strings were.

        Scope tracking is an approximation stated plainly: `namespace X.Y`
        pushes one scope per component because `end Y` may close the inner one
        alone; a bare `end` closes whatever is innermost, which is right for
        sections and `mutual` blocks and merely degrades -- never crashes --
        on Lean this model does not cover.
        """
        found: list[tuple[str, str, str, str, int]] = []
        raw = source.splitlines()
        blanked_source = strip_comments(source)
        blanked = blanked_source.splitlines()
        # First pass: the namespace prefix in effect at each line. scopes
        # holds namespace components as strings and None for any scope
        # (`section`, `mutual`) that contributes nothing to a name.
        scopes: list[str | None] = []
        prefixes: list[tuple[str, ...]] = []
        for line in blanked:
            prefixes.append(tuple(part for part in scopes if part is not None))
            opened = _NAMESPACE.match(line)
            if opened:
                scopes.extend(_COMPONENT.findall(opened.group(1)))
            elif _SECTION.match(line) or _MUTUAL.match(line):
                scopes.append(None)
            else:
                closed = _END.match(line)
                if closed:
                    # An `end` naming components closes that many namespace
                    # frames -- unless the innermost scope is a `section` or
                    # `mutual` (a None entry), which is one frame whatever its
                    # name looks like: `end Foo.Bar` after `section Foo.Bar`
                    # closes the section alone, not the namespace around it.
                    if not closed.group(1) or (scopes and scopes[-1] is None):
                        depth = 1
                    else:
                        depth = len(_COMPONENT.findall(closed.group(1)))
                    del scopes[max(0, len(scopes) - depth) :]
        # Second pass: declarations, over the whole blanked source rather than
        # line by line -- Lean allows a newline between the keyword and the
        # name, and `workspace.DECLARATION`'s comment records losing exactly
        # those. The prefix is taken at the *keyword's* line, which is also
        # the line the record reports and the signature starts from.
        for head in DECLARATION.finditer(blanked_source):
            if "private" in head.group(1):
                continue
            written = head.group(3)
            number = blanked_source.count("\n", 0, head.start(2)) + 1
            # `_root_.` escapes every open namespace -- that is what it is for
            # -- so the prefix is dropped along with the marker. Mathlib's own
            # `Sylow.lean` declares `_root_.IsPGroup.toSylow` inside
            # `namespace Sylow`, and joining produced a name nothing can
            # elaborate.
            if written.startswith("_root_."):
                name = written[len("_root_.") :]
            else:
                name = ".".join((*prefixes[number - 1], written))
            found.append(
                (name, name.lower(), _signature(raw, blanked, number - 1), module, number)
            )
        return found


def _signature(raw: list[str], blanked: list[str], start: int) -> str:
    """The declaration head, including the lines it was wrapped onto.

    Mathlib routinely puts a head's binders and result type on indented
    continuation lines under the keyword, and recording only the first
    physical line handed a ranking `theorem foo` where a type should be --
    with the index's rendering preferred over Loogle's complete one. So the
    continuation is gathered the way `retrieval._conclusion_lines` gathers a
    wrapped goal, and the body is cut off at `_BODY`: what follows `:=` or
    `where` is the proof or the fields, not the head.

    The marker is *found* on the blanked copy and the cut *applied* to the
    raw, because `theorem t : "a := b" = ... := rfl` carries the marker
    inside a literal, and cutting at the first raw occurrence recorded a
    malformed fragment as though it were the head. `strip_comments` blanks
    in place, so the two texts are index-aligned line by line and the offset
    carries over; the raw is what is shown, holes and all intact.
    """
    taken = [start]
    length = len(raw[start])
    for index in range(start + 1, len(blanked)):
        # An unindented or blank line is the next declaration, not this head;
        # a body marker means the head is already complete; and past the cap
        # there is nothing left to show anyway.
        line = blanked[index]
        if not line.strip() or not line[:1].isspace():
            break
        if _BODY.search(blanked[taken[-1]]) or length >= _MAX_SIGNATURE_CHARACTERS:
            break
        taken.append(index)
        length += len(raw[index])
    joined_raw = " ".join(raw[index] for index in taken)
    body = _BODY.search(" ".join(blanked[index] for index in taken))
    if body:
        joined_raw = joined_raw[: body.start()]
    return " ".join(joined_raw.split())[:_MAX_SIGNATURE_CHARACTERS]


def search_result(
    index: DeclarationIndex,
    query: str,
    limit: int = 10,
    *,
    inspect_tool: str = "inspect_declarations",
) -> DeclarationSearch:
    """One bounded index search, in the shape every search surface answers with.

    The bounds mirror what the `#find`-backed service method enforced, so the
    tool contract did not move when the backend did. `success` is always true
    and `timed_out` always false, and honestly so: there is no process to time
    out, which was the entire point of the change.

    An empty result carries its own reading as an information diagnostic. A
    finished name search that matched nothing is evidence about the *index* --
    a macro-built name is invisible to a textual scan -- and the sentence
    travels inside the answer so no surface can present the miss as Lean's
    word on Mathlib. `inspect_tool` is the recovery step's name *on the
    calling surface*: the staged and MCP tools are prefixed `lean_`, and a
    diagnostic naming the chat spelling there sends the model to a tool that
    does not exist exactly when it needs one that does.
    """
    if not 1 <= len(query) <= 512 or "\n" in query or "\r" in query:
        raise ValueError("declaration search query must be one bounded line")
    if not 1 <= limit <= 20:
        raise ValueError("declaration search limit must be between 1 and 20")
    # One past the limit, so `truncated` reports there was more to see.
    found = index.search(query, limit + 1)
    diagnostics: tuple[LeanDiagnostic, ...] = ()
    if not found:
        where = f" under {index.project}" if index.project else ""
        diagnostics = (
            LeanDiagnostic(
                severity="information",
                message=(
                    f"no declaration name in the index contains `{query}`; "
                    f"{index.count()} names were read from the package sources{where}. "
                    "An index miss is not Lean's word -- a name a macro builds is "
                    "invisible to a textual scan -- so try other spellings with "
                    f"{inspect_tool} before concluding anything is absent."
                ),
            ),
        )
    return DeclarationSearch(
        query=query,
        results=found[:limit],
        truncated=len(found) > limit,
        success=True,
        timed_out=False,
        diagnostics=diagnostics,
    )
