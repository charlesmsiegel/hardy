"""Which modules a Lean project can import, and what a missing one probably meant.

Lean's answer for an import that does not resolve names a *file*: `object file
'.../Sylow/Basic.olean' of module Mathlib.GroupTheory.Sylow.Basic does not
exist`. That sentence is true and it reads as a broken installation, which is
what one session concluded before abandoning Lean entirely and writing prose
instead. Mathlib was complete; the module it wanted is
`Mathlib.GroupTheory.Sylow`, flat, and the answer was one prefix lookup away.

The names are read from each package's root index file -- `Mathlib.lean` is
thousands of lines of nothing but imports -- and not from the build tree.
Walking `.lake/**/*.olean` was tried and took over two minutes on Windows,
which is not a cost an error message may impose.
"""

from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path

from .workspace import parse_imports

# `lakefile.lean` sits beside the index files, opens with `import Lake`, and is
# not a module index. Read as one it contributes the module `Lake`, which no
# suggestion should ever name.
NOT_AN_INDEX = frozenset({"lakefile.lean"})


class ModuleIndex:
    """The modules importable in one Lean project, read once and held.

    Nothing invalidates it. A session holds one for its lifetime, and a Mathlib
    that changes underneath a running session is out of scope -- saying so is
    cheaper than an mtime dance that would be wrong in a subtler way.
    """

    def __init__(self, project: Path | None) -> None:
        self.project = project
        self._names: tuple[str, ...] | None = None

    def names(self) -> tuple[str, ...]:
        if self._names is None:
            self._names = self._read()
        return self._names

    def _read(self) -> tuple[str, ...]:
        if self.project is None:
            return ()
        found: set[str] = set()
        for path in self._index_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # An unreadable index costs suggestions, never the session.
                continue
            found.update(parse_imports(source))
            # The index ships the module it is named for, and nothing imports
            # it -- so without this, `import Mathlib` names a module this index
            # says does not exist.
            found.add(path.stem)
        return tuple(sorted(found))

    def _index_files(self) -> list[Path]:
        """Each package's root index. Deliberately not the project's own sources.

        `parse_imports` reports what a file *imports*, not what exists. Reading
        the workspace's own `Main.lean` would therefore have put
        `Mathlib.GroupTheory.Sylow.Basic` into this index the moment the model
        wrote that import -- and `nearest` would have answered that the missing
        module is installed, turning the one tool that could have corrected the
        mistake into one that confirms it. An index is a list of what a package
        ships, and only a package's root index is one.

        Depth 1: a package's index sits at its root, and recursing would read
        every source file in Mathlib to learn what `Mathlib.lean` already says.

        A package shipping no root `.lean` index is invisible here. `nearest`
        then has nothing to offer and says so, which is the failure direction
        that costs a suggestion rather than inventing one.
        """
        assert self.project is not None
        packages = self.project / ".lake" / "packages"
        return [
            path
            for root in sorted(packages.glob("*"))
            if root.is_dir()
            for path in sorted(root.glob("*.lean"))
            if path.name not in NOT_AN_INDEX
        ]

    def search(self, query: str, limit: int = 20) -> tuple[str, ...]:
        """Modules whose name contains `query`, last component first.

        Someone searching `Sylow` wants `Mathlib.GroupTheory.Sylow` ahead of
        `Mathlib.SylowExtras.Other`: a module *about* a thing is named for it at
        the end, and a middle-component match is usually a different subject
        that happens to share a word.
        """
        wanted = query.strip().lower()
        if not wanted:
            return ()
        leaf = [name for name in self.names() if wanted in name.rsplit(".", 1)[-1].lower()]
        seen = set(leaf)
        rest = [name for name in self.names() if wanted in name.lower() and name not in seen]
        return tuple((*leaf, *rest))[:limit]

    def nearest(self, missing: str, limit: int = 5) -> tuple[str, ...]:
        """What `missing` was most likely meant to be.

        Exact structural answers first and fuzzy matching last, because the two
        structural cases are the two that actually happen. A module that was
        flattened loses its trailing component (`X.Basic` becomes `X`) and one
        that grew gains one (`X` becomes `X.Basic`); both are near-certainties,
        while a close match is a guess and is offered as the fallback it is.

        Only the *immediate* parent counts as a prefix answer, not every prefix.
        Every module descends from its package, so `Mathlib` is a prefix of all
        8,619 of them: asked about `Mathlib.GroupTheory.SimpleGroup` a general
        prefix rule answers `Mathlib` first, which is true, useless, and pushes
        the real candidates down the list. One component missing is evidence
        that a module was flattened; three are a coincidence.
        """
        names = set(self.names())
        if not names:
            return ()
        parts = missing.split(".")
        parent = ".".join(parts[:-1])
        found = [parent] if parent in names else []
        found.extend(
            sorted(
                name
                for name in names
                if name.startswith(f"{missing}.") and name not in found
            )
        )
        if len(found) < limit:
            found.extend(
                name
                for name in get_close_matches(missing, sorted(names), n=limit, cutoff=0.7)
                if name not in found
            )
        return tuple(found)[:limit]
