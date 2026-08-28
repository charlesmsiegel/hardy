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
copy of anything. Private declarations are skipped: offering a name the
caller's own file could never elaborate would manufacture the exact wrong
lead this index exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

from .lean import (
    _ATTRIBUTES,
    _MODIFIERS,
    DeclarationRecord,
    DeclarationSearch,
    LeanDiagnostic,
)
from .workspace import QUALIFIED, QUALIFIED_NAME, strip_comments

# One declaration head. The keyword list is Lean's surface grammar for named
# declarations; `example` is deliberately absent (anonymous by construction)
# and an `instance` without a name simply fails the name group and is skipped.
# `class inductive` is matched before `class` alone would swallow `inductive`
# as the name. An approximation of Lean's grammar, like every scan in Hardy
# that reads Lean as text, and documented as one where it is offered.
_KEYWORD = r"theorem|lemma|abbrev|structure|class(?:\s+inductive)?|inductive|instance|opaque|axiom|def"
DECLARATION = re.compile(
    rf"^\s*{_ATTRIBUTES}({_MODIFIERS})({_KEYWORD})\s+({QUALIFIED_NAME})"
)

# Scope lines. `namespace Foo.Bar` opens one scope per component, because that
# is how Lean closes them: `end Foo.Bar` closes both while `end Bar` closes
# only the inner one. A `section` may carry a name and may be `noncomputable`;
# a `mutual` block ends with a bare `end` that must not pop anything else.
_NAMESPACE = re.compile(rf"^\s*namespace\s+({QUALIFIED})\s*$")
_SECTION = re.compile(r"^\s*(?:noncomputable\s+)?section\b")
_MUTUAL = re.compile(r"^\s*mutual\s*$")
_END = re.compile(rf"^\s*end(?:\s+({QUALIFIED}))?\s*$")

# The same bound `retrieval.py` puts on a signature a model reads, restated
# here so this module does not import the one that imports it.
_MAX_SIGNATURE_CHARACTERS = 512

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
        found: list[tuple[int, bool, str, str, str, int]] = []
        for name, lowered, signature, module, line in self._read():
            matched = sum(1 for token in tokens if token in lowered)
            if not matched:
                continue
            leaf = lowered.rsplit(".", 1)[-1]
            in_leaf = any(token in leaf for token in tokens)
            found.append((-matched, not in_leaf, name, signature, module, line))
        found.sort()
        return tuple(
            DeclarationRecord(name=name, signature=signature, source_file=module, line=line)
            for _, _, name, signature, module, line in found[:limit]
        )

    def _read(self) -> list[tuple[str, str, str, str, int]]:
        if self._records is None:
            self._records = [] if self.project is None else self._scan()
        return self._records

    def _scan(self) -> list[tuple[str, str, str, str, int]]:
        assert self.project is not None
        records: list[tuple[str, str, str, str, int]] = []
        packages = self.project / ".lake" / "packages"
        for root in sorted(path for path in packages.glob("*") if path.is_dir()):
            for path in sorted(root.rglob("*.lean")):
                # A package can hold its own `.lake` build tree, which can hold
                # a stale copy of anything; only what the package ships counts.
                relative = path.relative_to(root)
                if ".lake" in relative.parts or path.name in _NOT_A_SOURCE:
                    continue
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    # An unreadable file costs its declarations, never the index.
                    continue
                module = ".".join((*relative.parts[:-1], relative.stem))
                records.extend(self._file(source, module))
        return records

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
        # scopes holds namespace components as strings and None for any scope
        # (`section`, `mutual`) that contributes nothing to a name.
        scopes: list[str | None] = []
        for number, line in enumerate(strip_comments(source).splitlines(), start=1):
            opened = _NAMESPACE.match(line)
            if opened:
                scopes.extend(opened.group(1).split("."))
                continue
            if _SECTION.match(line) or _MUTUAL.match(line):
                scopes.append(None)
                continue
            closed = _END.match(line)
            if closed:
                depth = len(closed.group(1).split(".")) if closed.group(1) else 1
                del scopes[max(0, len(scopes) - depth) :]
                continue
            head = DECLARATION.match(line)
            if head is None or "private" in head.group(1):
                continue
            written = head.group(3)
            # `_root_.` escapes every open namespace -- that is what it is for
            # -- so the prefix is dropped along with the marker. Mathlib's own
            # `Sylow.lean` declares `_root_.IsPGroup.toSylow` inside
            # `namespace Sylow`, and joining produced a name nothing can
            # elaborate.
            if written.startswith("_root_."):
                name = written[len("_root_.") :]
            else:
                prefix = [component for component in scopes if component is not None]
                name = ".".join((*prefix, written))
            signature = raw[number - 1].strip()[:_MAX_SIGNATURE_CHARACTERS]
            found.append((name, name.lower(), signature, module, number))
        return found


def search_result(index: DeclarationIndex, query: str, limit: int = 10) -> DeclarationSearch:
    """One bounded index search, in the shape every search surface answers with.

    The bounds mirror what the `#find`-backed service method enforced, so the
    tool contract did not move when the backend did. `success` is always true
    and `timed_out` always false, and honestly so: there is no process to time
    out, which was the entire point of the change.

    An empty result carries its own reading as an information diagnostic. A
    finished name search that matched nothing is evidence about the *index* --
    a macro-built name is invisible to a textual scan -- and the sentence
    travels inside the answer so no surface can present the miss as Lean's
    word on Mathlib.
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
                    "inspect_declarations before concluding anything is absent."
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
