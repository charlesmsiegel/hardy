"""The Lean source tree an interactive session owns, and its build.

A workspace module is importable only if its `.olean` exists on `LEAN_PATH`, so
this module keeps a compiled mirror of the source tree and rebuilds the part of
it that an edit invalidates. `lake env` augments an inherited `LEAN_PATH` rather
than replacing it, which is what lets the mirror sit beside Mathlib's own
package directories without the shared `lakefile.toml` being touched.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath

from .domain import FrozenModel
from .layout import WriteGuard, files_under, guard_for, read_text

from .formal.syntax import (
    IDENTIFIER,
    ESCAPED,
    ANY_NAME,
    QUALIFIED,
    QUALIFIED_NAME,
    COMPONENT,
    MODULE,
    IMPORT_PREFIX,
    HEADER_KEYWORDS,
    WRAPPER,
    DECLARATION,
    PRIVATE,
    NAMESPACE,
    SECTION,
    END,
    Compile,
    WorkspacePathError,
    ImportCycle,
    safe_relative,
    module_name,
    module_path,
    _olean_relative,
    _olean_module,
    declared_name,
    _scope_prefixes,
    _raw_string_opener,
    strip_comments,
    normalise_lean,
    ASSUMPTION,
    AXIOM_KEYWORD,
    COMMAND,
    assumptions,
    unreadable_assumptions,
    parse_imports,
    external_imports,
    declarations,
    _scan,
    CLOSERS,
    OPENERS,
    PROOF,
    BINDERS,
    OPENS_PROOF,
    statements,
    _statement_end,
    _word_at,
    name_aliases,
    internal_imports,
    build_order,
    dependents,
)


class BuildFailure(FrozenModel):
    module: str
    output: str


class LeanWorkspace:
    """A Lean source tree and the compiled mirror that makes it importable."""

    def __init__(
        self,
        root: Path,
        build: Path,
        compile: Compile,
        environment: str = "",
        external: Callable[[str], str] | None = None,
    ) -> None:
        self.root = root
        self.build = build
        self._compile = compile
        # What a module imported from outside the workspace currently is. An
        # olean built against one version of a local Lake module stays valid
        # only while that module does; without this, editing and rebuilding a
        # module in the configured project would leave Hardy reusing a cached
        # workspace olean and reporting it as current.
        self._external = external
        # Mixed into every signature. An olean is only valid for the toolchain
        # and project that produced it, so a workspace reopened after the Lean
        # command, the Lake project, or the pinned toolchain changed must
        # rebuild rather than reuse artifacts from the old configuration and
        # report a check that never ran under the current one.
        self._environment = environment

    def rebind_environment(self, environment: str) -> None:
        """Say that what this workspace builds against has changed.

        The environment is normally fixed for a workspace's life, because a
        toolchain does not move while a session runs. A shared Lean library
        does: it is the user's own tree, edited in the user's own editor, and
        the session holding this workspace notices between one Lean call and
        the next. Rebuilding the identity rather than the workspace keeps one
        answer to "what was this built against" -- the signatures, the olean
        cache and every audit verdict stamped from them all move together, so
        an edit to a shared source cannot leave a cached artifact and a stored
        verdict agreeing with each other about a tree that no longer exists.
        """
        self._environment = environment

    @property
    def index_path(self) -> Path:
        return self.build / "index.json"

    def sources(self) -> dict[str, str]:
        """Every module in the tree, by name, read through the layout guard.

        Discovery is a read, and it was the last unguarded one. `rglob` reports
        a symlinked `lean/Imported.lean` as an ordinary module and `read_text`
        follows it without a word, so a repository could put a host file into
        the workspace: Hardy compiled it, the axiom audit graded it, and a
        kernel-checked theorem was saved against source that is not in the
        versioned problem and is not there at all on the next machine. That is
        the audit believing something it cannot check, so `files_under` refuses
        a symlink anywhere in the tree rather than skipping it.
        """
        if not self.root.is_dir():
            return {}
        found = {}
        for relative in files_under(self.root, ".lean"):
            found[module_name(relative)] = read_text(self.root, relative)
        return found

    def read(self, relative: PurePosixPath) -> str | None:
        """One module's text, or None if there is no such file.

        Guarded for the same reason `sources` is: what this returns is shown to
        the model as the workspace's own source and diffed against what it
        saves, so a link followed here is a host file presented as the
        problem's.
        """
        path = self.root / relative
        if not path.is_file():
            return None
        return read_text(self.root, relative)

    def lean_path(self) -> str:
        return str(self.build)

    def olean(self, module: str) -> Path:
        return self.build / _olean_relative(module)

    def forget(self, module: str) -> None:
        """Drop a module's compiled artifact and its cache entry.

        Guarded, because deletion follows every directory component on the way
        to the file even though it never follows the file itself: `.build/Foo`
        replaced by a link makes this unlink somebody else's `Bar.olean`.
        """
        guard, name = guard_for(self.build, _olean_relative(module))
        guard.unlink(name, missing_ok=True)
        index = self._index()
        if index.pop(module, None) is not None:
            self._write_index(index)

    def prune_orphans(self) -> tuple[str, ...]:
        """Drop every compiled artifact whose source is no longer in the tree.

        A shared library is the user's own directory, edited in the user's own
        editor, and deleting or renaming a module there left `CommAlg.olean`
        and its index entry behind. The build directory stays on `LEAN_PATH`,
        so a problem could go on importing a module that has no source: the
        proof compiled, the axiom audit graded it, and Hardy saved a
        kernel-checked theorem resting on a file nobody can read -- with a
        freshly recomputed shared digest recorded beside it, saying the build
        was current. `build_modules` cannot notice, because it only ever walks
        the modules that DO exist.

        Returns what it removed, so a caller can say so rather than silently
        changing what an import resolves to.
        """
        if not self.build.is_dir():
            return ()
        keep = set(self.sources())
        removed = []
        for relative in files_under(self.build, ".olean"):
            module = _olean_module(relative)
            if module not in keep:
                self.forget(module)
                removed.append(module)
        # The index as well as the artifacts: an entry naming a module with
        # neither source nor olean is a claim about a build that never
        # happened, and `forget` only reaches the ones that left a file behind.
        index = self._index()
        stale = [module for module in index if module not in keep]
        if stale:
            for module in stale:
                index.pop(module)
            self._write_index(index)
            removed.extend(module for module in stale if module not in removed)
        return tuple(sorted(removed))

    def _index(self) -> dict[str, str]:
        """What this build root has already compiled, or nothing.

        Read through the guard for the same reason `_write_index` writes
        through one: `.build/` is gitignored, which is not the same as
        untrackable, and a repository that ships `.build/lean/index.json` as a
        link had Hardy take another tree's record of what was built as this
        one's -- and skip compiling a module on the strength of it. A
        `LayoutError` is deliberately allowed out: a half-written index costs
        a rebuild, but a link is a repository saying something Hardy cannot
        honour, and every other project path answers that the same way.
        """
        if not self.index_path.is_file():
            return {}
        try:
            loaded = json.loads(read_text(self.build, "index.json"))
        except (json.JSONDecodeError, OSError):
            # A half-written index must cost a rebuild, never a crash on open.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_index(self, index: dict[str, str]) -> None:
        """Replace the build index, whole or not at all.

        Guarded like everything else Hardy writes into a problem. The old
        version wrote a fixed `index.json.tmp` beside it and renamed: a
        repository that shipped that name as a symlink got the new bytes
        written straight through it, and the rename then put the link where
        the index belongs. `write_bytes` opens its temporary with
        `O_CREAT | O_EXCL` under a name nobody can have shipped.
        """
        guard = WriteGuard(self.build)
        guard.mkdir()
        guard.write_bytes("index.json", (json.dumps(index, indent=2, sort_keys=True) + "\n").encode("utf-8"), sync=False)

    def _signatures(self, sources: Mapping[str, str], order: tuple[str, ...]) -> dict[str, str]:
        """What each module's build depends on, its dependencies included.

        A recursive digest means a change deep in the tree changes the
        signature of everything above it, so staleness needs no separate
        invalidation pass that could disagree with the graph.
        """
        signatures: dict[str, str] = {}
        for module in order:
            digest = hashlib.sha256(self._environment.encode("utf-8"))
            digest.update(b"\0")
            digest.update(sources[module].encode("utf-8"))
            for dependency in sorted(internal_imports(sources[module], sources)):
                digest.update(signatures[dependency].encode("ascii"))
            if self._external is not None:
                for name in sorted(external_imports(sources[module], sources)):
                    digest.update(b"\0")
                    digest.update(self._external(name).encode("utf-8"))
            signatures[module] = digest.hexdigest()
        return signatures

    def current_signatures(self, sources: dict[str, str] | None = None) -> dict[str, str]:
        """What every module's build inputs hash to *now*.

        Computed rather than read back from the index: the index records what a
        module was last built at, so comparing a stored value against it would
        agree with itself after the toolchain changed and nothing had rebuilt
        yet. Recomputing asks the question a caller actually has -- are this
        module's inputs still the ones some earlier answer was about -- and
        because `_signatures` is recursive, a change anywhere beneath a module
        changes its answer too.

        A caller may hand in the sources it already read. The gate serializes
        Hardy's own tool calls and nothing else, so a file edited on disk
        between two reads of the tree would otherwise let one caller pair a
        signature with a statement it was never computed over.
        """
        sources = self.sources() if sources is None else sources
        if not sources:
            return {}
        return self._signatures(sources, build_order(sources, tuple(sources)))

    def build_modules(self, targets: Collection[str]) -> BuildFailure | None:
        """Compile `targets` and whatever they need, dependencies first.

        Returns the first failure rather than raising: a broken proof is an
        answer the model has to read, not an exception the session dies of.
        """
        sources = self.sources()
        missing = [name for name in targets if name not in sources]
        if missing:
            return BuildFailure(
                module=missing[0], output=f"no such workspace module: {missing[0]}"
            )
        order = build_order(sources, targets)
        signatures = self._signatures(sources, order)
        index = self._index()
        for module in order:
            if index.get(module) == signatures[module] and self.olean(module).is_file():
                continue
            # Dropped before the attempt, so a module that fails is not left
            # recorded as built by an earlier run that succeeded.
            index.pop(module, None)
            self._write_index(index)
            self.olean(module).parent.mkdir(parents=True, exist_ok=True)
            ok, output = self._compile(
                module, self.root, self.build, self.root / module_path(module)
            )
            if not ok:
                return BuildFailure(module=module, output=output)
            index[module] = signatures[module]
            self._write_index(index)
        return None

    def stage(
        self, relative: PurePosixPath, source: str | None, compile: Compile | None = None
    ) -> tuple[LeanWorkspace, Callable[[], None]]:
        """A copy of this workspace carrying one edit, and a way to keep it.

        A save that broke a module importing the edited one would leave the
        workspace red, so the edit is built somewhere else first and only
        copied back once everything depending on it still compiles. `source` of
        None stages a deletion. The caller drops the shadow either way, with
        `discard`. `compile` overrides how the shadow builds, which is how a
        caller keeps what Lean said about each module -- the build itself
        reports only which module failed, not what a successful one printed.
        """
        temporary = Path(tempfile.mkdtemp(prefix="hardy-workspace-"))
        shadow_root = temporary / "lean"
        shadow_build = temporary / "build"
        if self.root.is_dir():
            # Proven before it is copied, not after. `copytree` follows a
            # symlink by CONTENT, so a linked `lean/Imported.lean` arrives in
            # the shadow as a real file with the host's bytes in it -- at which
            # point every later check in the shadow is asking about a tree that
            # nothing on disk actually is. `files_under` refuses a symlink
            # anywhere beneath the root, which is the same walk `sources` does.
            files_under(self.root, ".lean")
            shutil.copytree(self.root, shadow_root)
        else:
            shadow_root.mkdir(parents=True)
        if self.build.is_dir():
            shutil.copytree(self.build, shadow_build)
        else:
            shadow_build.mkdir(parents=True)
        shadow = LeanWorkspace(
            shadow_root,
            shadow_build,
            compile or self._compile,
            environment=self._environment,
            external=self._external,
        )
        # Through a guard in the shadow as well as in `commit`, and for the
        # same reason: the shadow is a `copytree` of the real tree, so a
        # subdirectory the real tree links out of the project arrives here as
        # a real directory holding whatever that link pointed at -- and a
        # writer that trusted "it is only a temporary directory" would be
        # trusting a shape a repository chose.
        shadow_guard, shadow_name = guard_for(shadow_root, relative, create=True)
        if source is None:
            shadow_guard.unlink(shadow_name, missing_ok=True)
            # The olean has to go with the source. Left behind, the module is
            # absent from `sources()` -- so Hardy reads any later `import` of
            # it as external and never builds it -- while Lean still resolves
            # the stale artifact from LEAN_PATH. A saved proof would then
            # depend on source that is no longer in the workspace.
            shadow.forget(module_name(relative))
        else:
            with shadow_guard.open(shadow_name, "w", encoding="utf-8") as handle:
                handle.write(source)

        def commit() -> None:
            # `guard_for`, not `self.root / relative`. `safe_relative` has
            # already proven this is a workspace-relative Lean path made of
            # identifiers, which is a statement about the NAME and says
            # nothing about where the directories of that name lead:
            # `lean/Escape -> /tmp/OUTSIDE` accepted `Escape/Owned.lean` and
            # wrote a model-chosen file outside the project entirely.
            # `guard_for` proves each component against the one above it, so
            # the chain says something about `self.root`.
            guard, name = guard_for(self.root, relative, create=True)
            if source is None:
                guard.unlink(name, missing_ok=True)
            else:
                with guard.open(name, "w", encoding="utf-8") as handle:
                    handle.write(source)
            if self.build.is_dir():
                shutil.rmtree(self.build)
            shutil.copytree(shadow_build, self.build)

        return shadow, commit

    @staticmethod
    def discard(shadow: LeanWorkspace) -> None:
        """Drop a staged copy, whether it was committed or refused."""
        shutil.rmtree(shadow.root.parent, ignore_errors=True)
