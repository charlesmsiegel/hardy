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
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath

from .domain import FrozenModel

COMPONENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")
DECLARATION = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+|nonrec\s+|noncomputable\s+)*"
    r"(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)"
)
NAMESPACE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")
END = re.compile(r"^\s*end\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")

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


def parse_imports(source: str) -> tuple[str, ...]:
    """The modules a source file imports.

    Lean requires imports before any declaration, so the header is scanned and
    abandoned at the first line that is not blank, a comment, or an import. A
    regex over the whole file would find the word in a string literal and
    invent a dependency that does not exist.
    """
    imports: list[str] = []
    depth = 0
    for line in source.splitlines():
        text = line.strip()
        while text:
            if depth:
                closing = text.find("-/")
                if closing < 0:
                    text = ""
                    break
                depth -= 1
                text = text[closing + 2 :].strip()
                continue
            if text.startswith("/-"):
                depth += 1
                text = text[2:]
                continue
            break
        if depth or not text or text.startswith("--"):
            continue
        if not text.startswith("import "):
            break
        match = MODULE.fullmatch(text.removeprefix("import ").strip())
        if match is None:
            break
        imports.append(match.group())
    return tuple(imports)


def declarations(source: str) -> dict[str, tuple[str, ...]]:
    """Top-level `theorem` and `lemma` names, one entry per declaration.

    A declaration inside a namespace is reported by its qualified name, which
    is the one Lean itself would print. Emitting the bare name as well would
    make one theorem look like two, and a caller counting what still owes a
    writeup would then demand two of them -- see `name_aliases` for the other
    half of this, which is that a *reader* of the registry must accept either.
    """
    found: dict[str, list[str]] = {"theorem": [], "lemma": []}
    scope: list[str] = []
    for line in source.splitlines():
        opening = NAMESPACE.match(line)
        if opening:
            scope.append(opening.group(1))
            continue
        closing = END.match(line)
        if closing and scope and scope[-1] == closing.group(1):
            scope.pop()
            continue
        match = DECLARATION.match(line)
        if match:
            kind, name = match.group(1), match.group(2)
            found[kind].append(".".join((*scope, name)) if scope else name)
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

    def __init__(self, root: Path, build: Path, compile: Compile) -> None:
        self.root = root
        self.build = build
        self._compile = compile

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
            digest = hashlib.sha256(sources[module].encode("utf-8"))
            for dependency in sorted(internal_imports(sources[module], sources)):
                digest.update(signatures[dependency].encode("ascii"))
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
        self, relative: PurePosixPath, source: str | None
    ) -> tuple[LeanWorkspace, Callable[[], None]]:
        """A copy of this workspace carrying one edit, and a way to keep it.

        A save that broke a module importing the edited one would leave the
        workspace red, so the edit is built somewhere else first and only
        copied back once everything depending on it still compiles. `source` of
        None stages a deletion. The caller drops the shadow either way, with
        `discard`.
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
        if source is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        shadow = LeanWorkspace(shadow_root, shadow_build, self._compile)

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
