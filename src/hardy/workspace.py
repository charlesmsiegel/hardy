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
import os
import re
import shutil
import tempfile
from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath

from .domain import FrozenModel

# Lean identifiers are Unicode: `theorem α` and `theorem h₁` are ordinary, and
# an ASCII-only pattern would not see them -- so a theorem could be saved that
# never appeared in the listing and never owed a writeup. `[^\W\d]` is "any
# letter or underscore" under Python's Unicode-aware `\w`.
IDENTIFIER = r"[^\W\d][\w'!?]*"
# `theorem «first result»` is a valid declaration: Lean lets guillemets quote a
# name containing anything, spaces included. A pattern that could not see one
# would leave that theorem out of the listing, so it would never owe a writeup.
ESCAPED = r"«[^»\n]+»"
ANY_NAME = rf"(?:{IDENTIFIER}|{ESCAPED})"
QUALIFIED = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
QUALIFIED_NAME = rf"{ANY_NAME}(?:\.{ANY_NAME})*"

# Module and path components stay unescaped: they are file names on disk, and a
# guillemet in one is not something to invite.
COMPONENT = re.compile(IDENTIFIER)
MODULE = re.compile(QUALIFIED)
# Lean's module system spells an import `public import X` or `meta import X`,
# and opens such a file with `module`. Neither ends the header.
IMPORT_PREFIX = re.compile(r"^(?:(?:public|meta|private|protected)\s+)*")
HEADER_KEYWORDS = frozenset({"prelude", "module"})
# Scanned over the whole source rather than line by line, because Lean allows a
# newline between the keyword and the name and a line-oriented match would lose
# the declaration entirely.
DECLARATION = re.compile(
    rf"(?m)^[ \t]*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|nonrec|noncomputable)\s+)*"
    rf"(theorem|lemma)\s+({QUALIFIED_NAME})"
)
NAMESPACE = re.compile(rf"^\s*namespace\s+({QUALIFIED_NAME})\s*$")
# `section` may be anonymous, and `end` may be bare -- both are ordinary Lean.
SECTION = re.compile(rf"^\s*section(?:\s+({QUALIFIED_NAME}))?\s*$")
END = re.compile(rf"^\s*end(?:\s+({QUALIFIED_NAME}))?\s*$")

Compile = Callable[[str, Path, Path, Path], tuple[bool, str]]


class WorkspacePathError(ValueError):
    """A path that is not a Lean module inside the workspace."""


class ImportCycle(ValueError):
    """Workspace modules that import each other."""


def safe_relative(path: str) -> PurePosixPath:
    """The workspace-relative path `path` denotes, or a refusal.

    Everything that could reach outside the tree, or name a file Lean would not
    accept as a module, is refused here rather than at the filesystem: a tool
    argument is model output and gets no benefit of the doubt.
    """
    relative = PurePosixPath(str(path).replace("\\", "/"))
    if relative.is_absolute() or not relative.name.endswith(".lean"):
        raise WorkspacePathError(f"not a workspace Lean path: {path!r}")
    parts = relative.parts
    if not parts or any(part in {"..", "."} for part in parts):
        raise WorkspacePathError(f"path escapes the workspace: {path!r}")
    for part in (*parts[:-1], parts[-1][: -len(".lean")]):
        if not COMPONENT.fullmatch(part):
            raise WorkspacePathError(f"not a Lean identifier: {part!r} in {path!r}")
    return relative


def module_name(relative: PurePosixPath) -> str:
    return ".".join((*relative.parts[:-1], relative.name[: -len(".lean")]))


def module_path(name: str) -> PurePosixPath:
    return PurePosixPath(*name.split(".")).with_suffix(".lean")


def strip_comments(source: str) -> str:
    """`source` with its comments blanked out, line structure preserved.

    One pass serves both the import scan and the declaration scan, because
    both were getting comments wrong in their own way: a trailing `--` hid an
    import, a nested `/- /- -/ -/` closed early, and `/-- doc -/ theorem foo`
    hid a declaration behind a leading comment. Lean treats all of that as
    whitespace, so the honest fix is to do the same once, rather than teach
    every regex about comments separately.

    Comments are replaced by spaces rather than removed so that line and
    column positions still line up with the source a reader has open. String
    literals are respected, or a `"-- "` inside one would blank the rest of a
    real line.
    """
    out = list(source)
    index = 0
    depth = 0
    length = len(source)
    while index < length:
        character = source[index]
        if depth:
            if source.startswith("/-", index):
                depth += 1
                out[index] = out[index + 1] = " "
                index += 2
                continue
            if source.startswith("-/", index):
                depth -= 1
                out[index] = out[index + 1] = " "
                index += 2
                continue
            if character != "\n":
                out[index] = " "
            index += 1
            continue
        if character == '"':
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        if source.startswith("/-", index):
            depth = 1
            out[index] = out[index + 1] = " "
            index += 2
            continue
        if source.startswith("--", index):
            while index < length and source[index] != "\n":
                out[index] = " "
                index += 1
            continue
        index += 1
    return "".join(out)


def parse_imports(source: str) -> tuple[str, ...]:
    """The modules a source file imports.

    Lean requires imports before any declaration, so the header is scanned and
    abandoned at the first line that is neither blank nor an import. Comments
    are already gone by then; a regex over the raw file would find the word in
    a string literal and invent a dependency that does not exist.
    """
    imports: list[str] = []
    for line in strip_comments(source).splitlines():
        text = line.strip()
        if not text:
            continue
        # `prelude` suppresses the implicit `import Init`; `module` opens a file
        # using Lean's module system. Both sit before the imports, and reading
        # either as the end of the header would drop every import that follows
        # -- and an import Hardy cannot see is a dependency it will not rebuild.
        if text in HEADER_KEYWORDS and not imports:
            continue
        # `public import X` and `meta import X` are ordinary imports under the
        # module system, and carry the dependency just as a bare one does.
        text = IMPORT_PREFIX.sub("", text, count=1)
        if not text.startswith("import "):
            break
        rest = text.removeprefix("import ").strip()
        # `import all X` re-exports; the dependency is the same either way.
        if rest.startswith("all "):
            rest = rest.removeprefix("all ").strip()
        match = MODULE.fullmatch(rest)
        if match is None:
            break
        imports.append(match.group())
    return tuple(imports)


def external_imports(source: str, known: Collection[str]) -> tuple[str, ...]:
    """The imports of `source` that are not workspace modules."""
    return tuple(name for name in parse_imports(source) if name not in known)


def declarations(source: str) -> dict[str, tuple[str, ...]]:
    """Top-level `theorem` and `lemma` names, one entry per declaration.

    A declaration inside a namespace is reported by its qualified name, which
    is the one Lean itself would print. Emitting the bare name as well would
    make one theorem look like two, and a caller counting what still owes a
    writeup would then demand two of them -- see `name_aliases` for the other
    half of this, which is that a *reader* of the registry must accept either.
    """
    found: dict[str, list[str]] = {"theorem": [], "lemma": []}
    # Comments first: Lean reads `/-- explanation -/ theorem result ...` as a
    # declaration, and a scanner that saw the leading slash would miss it --
    # so the theorem would never be recorded and never owe a writeup.
    text = strip_comments(source)
    lines = text.splitlines()

    # Both kinds of scope, because a bare `end` closes whichever is innermost
    # and only a namespace contributes to a name. Tracking namespaces alone
    # would let `section ... end` pop a namespace that is still open, and every
    # later declaration would then be recorded under a name Lean never gave it.
    scope: list[tuple[str, str | None]] = []
    prefixes: list[tuple[str, ...]] = []
    for line in lines:
        opening = NAMESPACE.match(line)
        if opening:
            scope.append(("namespace", opening.group(1)))
        elif SECTION.match(line):
            scope.append(("section", SECTION.match(line).group(1)))
        else:
            closing = END.match(line)
            if closing:
                name = closing.group(1)
                if name is None:
                    if scope:
                        scope.pop()
                else:
                    # A named `end` closes that scope and anything still open
                    # inside it.
                    for index in range(len(scope) - 1, -1, -1):
                        if scope[index][1] == name:
                            del scope[index:]
                            break
        prefixes.append(tuple(item for kind, item in scope if kind == "namespace" and item))

    # Declarations are matched over the whole text so a name on the line after
    # its keyword is still found, then attributed to the scope open at the line
    # the keyword sits on.
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    for match in DECLARATION.finditer(text):
        index = bisect_right(starts, match.start()) - 1
        prefix = prefixes[index] if 0 <= index < len(prefixes) else ()
        kind, name = match.group(1), match.group(2)
        found[kind].append(".".join((*prefix, name)) if prefix else name)
    return {kind: tuple(names) for kind, names in found.items()}


def name_aliases(name: str) -> tuple[str, ...]:
    """The names a registry entry might reasonably use for one declaration.

    `Hardy.one` and `one` denote the same theorem, and a registry recording
    either must count as recording it. Only the last component is offered, not
    every suffix: `Hardy.Group.one` abbreviated to `Group.one` is not a name
    Lean would resolve from the root.
    """
    if "." not in name:
        return (name,)
    return (name, name.rsplit(".", 1)[1])


def internal_imports(source: str, known: Collection[str]) -> tuple[str, ...]:
    """The imports of `source` that name workspace modules.

    Anything else is Mathlib or a toolchain module, which the Lake environment
    already resolves and which this module must not try to build.
    """
    return tuple(name for name in parse_imports(source) if name in known)


def build_order(sources: Mapping[str, str], targets: Collection[str]) -> tuple[str, ...]:
    """`targets` and their transitive internal dependencies, dependencies first.

    Sorted at each step rather than left in dictionary order: a build that
    compiled the same tree in a different order on different runs could not be
    compared against its own cache.
    """
    order: list[str] = []
    placed: set[str] = set()
    active: list[str] = []

    def visit(module: str) -> None:
        if module in placed:
            return
        if module in active:
            cycle = " -> ".join([*active[active.index(module) :], module])
            raise ImportCycle(f"workspace modules import each other: {cycle}")
        active.append(module)
        for dependency in sorted(internal_imports(sources[module], sources)):
            visit(dependency)
        active.pop()
        placed.add(module)
        order.append(module)

    for target in sorted(targets):
        visit(target)
    return tuple(order)


def dependents(sources: Mapping[str, str], module: str) -> frozenset[str]:
    """Every module that reaches `module` through internal imports."""
    direct: dict[str, set[str]] = {name: set() for name in sources}
    for name, source in sources.items():
        for dependency in internal_imports(source, sources):
            direct[dependency].add(name)
    found: set[str] = set()
    frontier = list(direct.get(module, ()))
    while frontier:
        name = frontier.pop()
        if name in found or name == module:
            continue
        found.add(name)
        frontier.extend(direct.get(name, ()))
    return frozenset(found)


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

    @property
    def index_path(self) -> Path:
        return self.build / "index.json"

    def sources(self) -> dict[str, str]:
        if not self.root.is_dir():
            return {}
        found = {}
        for path in sorted(self.root.rglob("*.lean")):
            relative = PurePosixPath(path.relative_to(self.root).as_posix())
            found[module_name(relative)] = path.read_text(encoding="utf-8")
        return found

    def read(self, relative: PurePosixPath) -> str | None:
        path = self.root / relative
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def lean_path(self) -> str:
        return str(self.build)

    def olean(self, module: str) -> Path:
        return (self.build / PurePosixPath(*module.split("."))).with_suffix(".olean")

    def forget(self, module: str) -> None:
        """Drop a module's compiled artifact and its cache entry."""
        self.olean(module).unlink(missing_ok=True)
        index = self._index()
        if index.pop(module, None) is not None:
            self._write_index(index)

    def _index(self) -> dict[str, str]:
        if not self.index_path.is_file():
            return {}
        try:
            loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A half-written index must cost a rebuild, never a crash on open.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_index(self, index: dict[str, str]) -> None:
        self.build.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.index_path)

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
            shutil.copytree(self.root, shadow_root)
        else:
            shadow_root.mkdir(parents=True)
        if self.build.is_dir():
            shutil.copytree(self.build, shadow_build)
        else:
            shadow_build.mkdir(parents=True)
        target = shadow_root / relative
        shadow = LeanWorkspace(
            shadow_root,
            shadow_build,
            compile or self._compile,
            environment=self._environment,
            external=self._external,
        )
        if source is None:
            target.unlink(missing_ok=True)
            # The olean has to go with the source. Left behind, the module is
            # absent from `sources()` -- so Hardy reads any later `import` of
            # it as external and never builds it -- while Lean still resolves
            # the stale artifact from LEAN_PATH. A saved proof would then
            # depend on source that is no longer in the workspace.
            shadow.forget(module_name(relative))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")

        def commit() -> None:
            self.root.mkdir(parents=True, exist_ok=True)
            real = self.root / relative
            if source is None:
                real.unlink(missing_ok=True)
            else:
                real.parent.mkdir(parents=True, exist_ok=True)
                real.write_text(source, encoding="utf-8")
            if self.build.is_dir():
                shutil.rmtree(self.build)
            shutil.copytree(shadow_build, self.build)

        return shadow, commit

    @staticmethod
    def discard(shadow: LeanWorkspace) -> None:
        """Drop a staged copy, whether it was committed or refused."""
        shutil.rmtree(shadow.root.parent, ignore_errors=True)
