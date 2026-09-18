"""The installed packages' declaration index, read in the background and served by name.

Two questions the editor asks: *what names look like this* (the search behind
the composer's completion and the at-cursor rail) and *what is this name,
exactly, and where is it written* (the Mathlib trace view). Both are answered
from `hardy.formal.declarations.DeclarationIndex`, which already walks every
`.lean` file the installed packages ship and holds a name, a signature, a
module and a line per declaration.

Nothing here changes `formal/`. That package is the kernel-facing half of the
system and what it already offers is enough: the two gaps this module fills --
that the first read is expensive, and that a module name is not a file path --
are both facts about serving the index over HTTP rather than about the index.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from hardy.formal.declarations import DeclarationIndex

#: How many lines either side of a declaration the trace view carries. A
#: Mathlib file is tens of thousands of lines and the panel wants the
#: declaration in its immediate context, not the file.
EXCERPT_BEFORE = 8
EXCERPT_AFTER = 32
#: The most results one search returns, whatever the caller asks for. The
#: index holds hundreds of thousands of names and a one-letter query matches
#: most of them.
MAX_RESULTS = 50


class Declarations:
    """One declaration index per Lean project, read once, held for the server's life.

    That is the lifetime `DeclarationIndex` itself assumes -- "a session holds
    one for its lifetime, and a Mathlib changing under a running session is out
    of scope" -- so nothing here invalidates it either.

    The read is started on the first question and the first question is
    answered without it. A cold `_read()` walks every source file Mathlib
    ships, which is seconds to minutes, and a browser tab must not hang on a
    cost that has nothing to do with what it asked. `DeclarationIndex.read` is
    the index's own statement of whether that one read has happened -- it
    exists because the retrieval meter admits a cold search against a different
    bound than a warm one -- and here it decides between an answer and
    *not reported*. The page says the index has not been read yet, which is
    true, rather than drawing a spinner that promises a result is coming.
    """

    def __init__(self, lean_project: Path | None) -> None:
        self.project = lean_project
        self.index = DeclarationIndex(lean_project)
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()

    # -- the one read ----------------------------------------------------

    def _begin(self) -> None:
        """Start the scan if nothing has started it. Never blocks."""
        with self._guard:
            if self._thread is None and not self.index.read:
                # `count()` is the cheapest call that forces `_read()`. The
                # index guards its own scan, so a second thread arriving here
                # would block rather than rescan -- this flag only keeps us
                # from spawning threads that would immediately wait.
                self._thread = threading.Thread(
                    target=self.index.count, name="hardy-declaration-index", daemon=True
                )
                self._thread.start()

    def wait_for_index(self, timeout: float | None = None) -> bool:
        """Block until the scan finishes. For tests, and for nothing else."""
        self._begin()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.index.read

    def _cold(self) -> dict[str, Any]:
        return {
            "indexed": False,
            "count": None,
            "reason": "the declaration index has not been read yet",
        }

    # -- the two questions -----------------------------------------------

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Declarations whose name matches `query`, best match first."""
        self._begin()
        if not self.index.read:
            return {**self._cold(), "results": []}
        bounded = max(1, min(int(limit or 20), MAX_RESULTS))
        found = self.index.search(query, bounded) if query.strip() else ()
        return {
            "indexed": True,
            "count": self.index.count(),
            "results": [
                {
                    "name": record.name,
                    "signature": record.signature,
                    "module": record.source_file,
                    "line": record.line,
                }
                for record in found
            ],
        }

    def lookup(self, name: str) -> dict[str, Any]:
        """One exact declaration, with its source at the line the index recorded.

        Exact, and exact by construction rather than by filtering.
        `DeclarationIndex.search` matches any word of the query as a substring
        and caps its results, so asking it for `Sylow.card` and taking the
        first row back would answer with `Sylow.card_modEq_one` -- a real
        declaration, a real signature, a real line, and not the thing that was
        asked about. A near miss reads as correct until somebody checks it,
        which is the worst kind of wrong for a panel whose whole purpose is to
        show a reader the actual source. Filtering `search`'s answer fixes the
        near miss and keeps the cap: a name outside the window reads as absent
        from an index that holds it. `exact` has no window.
        """
        self._begin()
        wanted = name.strip()
        if not self.index.read:
            return {**self._cold(), "found": False, "name": wanted}
        # `exact`, not a filter over `search`. `search` is a ranked substring
        # match with a result cap, so a qualified name with more than
        # `MAX_RESULTS` lexicographically earlier names containing the same
        # text fell outside the window and answered `found: false` while the
        # index held it.
        record = self.index.exact(wanted)
        if record is None:
            return {"indexed": True, "found": False, "name": wanted,
                    "signature": None, "module": None, "line": None,
                    "source_path": None, "excerpt": None}
        source = self._source_path(record.source_file)
        return {
            "indexed": True,
            "found": True,
            "name": record.name,
            "signature": record.signature,
            "module": record.source_file,
            "line": record.line,
            "source_path": None if source is None else source.as_posix(),
            "excerpt": None if source is None else self._excerpt(source, record.line),
        }

    # -- module name to file ---------------------------------------------

    def _source_path(self, module: str) -> Path | None:
        """`Mathlib.GroupTheory.Sylow` -> the file on disk, or None.

        `DeclarationIndex` builds a module name from a path relative to a
        package root and then drops the root, so the package is not
        recoverable from a record. Globbing the package directories back is
        this layer's own work: widening the index's tuple to carry the root
        would move `run_procedure_digest` for a reason that has nothing to do
        with what a run does.

        Nothing is guessed. A module that resolves to no file, or to more than
        one, answers None. A reader shown the wrong file's line 602 has been
        told something false with the full confidence of a line number, and
        *not reported* is the honest answer when Hardy cannot find the file.
        """
        if self.project is None:
            return None
        relative = Path(*module.split(".")).with_suffix(".lean")
        packages = self.project / ".lake" / "packages"
        if not packages.is_dir():
            return None
        found = [
            candidate
            for package in sorted(path for path in packages.glob("*") if path.is_dir())
            for candidate in (package / relative,)
            if candidate.is_file() and not candidate.is_symlink()
        ]
        return found[0] if len(found) == 1 else None

    def _excerpt(self, path: Path, line: int) -> dict[str, Any] | None:
        """The declaration in its immediate context, with the line it starts at.

        `from` travels with the text because the client numbers the lines from
        it. Without it the panel would either renumber from 1 -- claiming the
        declaration is at line 9 of Mathlib -- or print no numbers at all,
        and the line number is half of what the trace view is for.
        """
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        if not lines:
            return None
        start = max(1, line - EXCERPT_BEFORE)
        end = min(len(lines), line + EXCERPT_AFTER)
        return {"from": start, "text": "\n".join(lines[start - 1:end])}
