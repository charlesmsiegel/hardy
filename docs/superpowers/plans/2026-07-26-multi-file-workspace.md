# Multi-file workspace and enforced writeup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the interactive session hold a Lean development spread over many files that import each other, and make a human-readable LaTeX writeup a condition of continuing rather than an optional extra.

**Architecture:** A new `workspace.py` owns a Lean source tree under `.hardy/lean/` and its compiled oleans under `.hardy/.build/lean/`. Because `lake env` augments an inherited `LEAN_PATH` rather than replacing it, the olean directory can sit on the search path beside Mathlib's without touching the shared `lakefile.toml`. `chat.py`'s tools gain paths, saves become shadow builds that refuse to break a dependent, and a catch-up ratchet refuses a save that introduces a new theorem while an old one is still undocumented.

**Tech Stack:** Python 3.12, pydantic (`FrozenModel`), Lean 4 / Lake, pytest, `uv`.

## Global Constraints

- Module name = path under `.hardy/lean/`, suffix dropped, separators to dots. `Group/Sylow.lean` → `Group.Sylow`.
- Compile invocation is exactly: `lake env lean --root=<lean/> -o <build>/<Mod>.olean <lean>/<Mod>.lean`, `cwd` the Lake project, with `LEAN_PATH=<build>` in `ProcessSpec.env`.
- `lean` does not create output directories; every olean's parent must be created first.
- Documentation status is **derived on demand** from the naming registry and the tex tree. Never stored in `session.json`.
- Only top-level `theorem` requires documentation. `lemma`, `def`, `instance`, `abbrev`, `example` are exempt.
- The ratchet refuses only when **both** hold: the committed tree has an undocumented theorem, **and** this save introduces a theorem name not already in the committed tree.
- Run tests with `uv run --extra test pytest`.
- Per `AGENTS.md`, `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` must stay consistent with the code.

---

### Task 1: Module naming, path safety, and import parsing

Pure functions only — no subprocess, no filesystem. This is the vocabulary every later task speaks.

**Files:**
- Create: `src/hardy/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WorkspacePathError`, `module_name(relative: PurePosixPath) -> str`, `module_path(name: str) -> PurePosixPath`, `safe_relative(path: str) -> PurePosixPath`, `parse_imports(source: str) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workspace.py
from pathlib import PurePosixPath

import pytest

from hardy.workspace import (
    WorkspacePathError,
    module_name,
    module_path,
    parse_imports,
    safe_relative,
)


def test_module_name_maps_directories_to_dots():
    assert module_name(PurePosixPath("Main.lean")) == "Main"
    assert module_name(PurePosixPath("Group/Sylow.lean")) == "Group.Sylow"


def test_module_path_is_the_inverse():
    assert module_path("Group.Sylow") == PurePosixPath("Group/Sylow.lean")


@pytest.mark.parametrize(
    "bad",
    ["/absolute/Main.lean", "../escape.lean", "Group/../../out.lean", "notes.txt", "", "9Bad.lean", "Group/.hidden.lean"],
)
def test_safe_relative_rejects_paths_that_are_not_lean_modules(bad):
    with pytest.raises(WorkspacePathError):
        safe_relative(bad)


def test_safe_relative_normalises_separators():
    assert safe_relative("Group\\Sylow.lean") == PurePosixPath("Group/Sylow.lean")


def test_parse_imports_reads_only_the_header():
    source = (
        "-- a leading comment\n"
        "/- a block\n   comment -/\n"
        "import Mathlib\n"
        "import Group.Sylow\n"
        "\n"
        "theorem t : True := by\n"
        "  have s := \"import NotReally\"\n"
        "  trivial\n"
    )
    assert parse_imports(source) == ("Mathlib", "Group.Sylow")


def test_parse_imports_stops_at_the_first_declaration():
    assert parse_imports("import A\ntheorem t : True := trivial\nimport B\n") == ("A",)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_workspace.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.workspace'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/workspace.py
"""The Lean source tree an interactive session owns, and its build.

A workspace module is importable only if its `.olean` exists on `LEAN_PATH`, so
this module keeps a compiled mirror of the source tree and rebuilds the part of
it an edit invalidates. `lake env` augments an inherited `LEAN_PATH` rather than
replacing it, which is what lets the mirror sit beside Mathlib's own package
directories without the shared `lakefile.toml` being touched.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

COMPONENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*")


class WorkspacePathError(ValueError):
    """A path that is not a Lean module inside the workspace."""


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_workspace.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workspace.py tests/test_workspace.py
git commit -m "feat: name, validate, and read the imports of a workspace Lean module (#33)"
```

---

### Task 2: The dependency graph — order, cycles, dependents

Still pure. Operates on a `{module: source}` mapping so it can be tested without a filesystem.

**Files:**
- Modify: `src/hardy/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `parse_imports`, `module_name` from Task 1.
- Produces: `ImportCycle`, `internal_imports(source: str, known: Collection[str]) -> tuple[str, ...]`, `build_order(sources: Mapping[str, str], targets: Collection[str]) -> tuple[str, ...]`, `dependents(sources: Mapping[str, str], module: str) -> frozenset[str]`.

`build_order` returns the target modules *and their transitive internal dependencies*, dependencies first. `dependents` returns the transitive set of modules importing `module`, excluding `module` itself.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_workspace.py
from hardy.workspace import ImportCycle, build_order, dependents, internal_imports

TREE = {
    "Basic": "import Mathlib\ndef a := 1\n",
    "Group.Sylow": "import Basic\ndef b := a\n",
    "Main": "import Group.Sylow\nimport Mathlib\ndef c := b\n",
    "Scratch": "import Mathlib\ndef d := 2\n",
}


def test_internal_imports_ignores_external_ones():
    assert internal_imports(TREE["Main"], TREE) == ("Group.Sylow",)


def test_build_order_puts_dependencies_first_and_omits_the_unrelated():
    assert build_order(TREE, ["Main"]) == ("Basic", "Group.Sylow", "Main")


def test_build_order_is_deterministic_across_independent_modules():
    assert build_order(TREE, ["Main", "Scratch"]) == ("Basic", "Group.Sylow", "Main", "Scratch")


def test_dependents_are_transitive_and_exclude_the_module_itself():
    assert dependents(TREE, "Basic") == frozenset({"Group.Sylow", "Main"})
    assert dependents(TREE, "Main") == frozenset()


def test_a_cycle_is_refused_by_name():
    cyclic = {"A": "import B\n", "B": "import A\n"}
    with pytest.raises(ImportCycle) as error:
        build_order(cyclic, ["A"])
    assert "A" in str(error.value) and "B" in str(error.value)


def test_a_missing_internal_import_is_simply_external():
    assert internal_imports("import Nowhere\n", TREE) == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_workspace.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_order'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/hardy/workspace.py
from collections.abc import Collection, Mapping


class ImportCycle(ValueError):
    """Workspace modules that import each other."""


def internal_imports(source: str, known: Collection[str]) -> tuple[str, ...]:
    """The imports of `source` that name workspace modules.

    Anything else is Mathlib or a toolchain module, which the Lake environment
    already resolves and which this module must not try to build.
    """
    return tuple(name for name in parse_imports(source) if name in known)


def build_order(sources: Mapping[str, str], targets: Collection[str]) -> tuple[str, ...]:
    """`targets` and their transitive internal dependencies, dependencies first.

    Sorted at each step rather than left in dictionary order: a build that
    compiles the same tree in a different order on different runs cannot be
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_workspace.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workspace.py tests/test_workspace.py
git commit -m "feat: order a workspace build and find what an edit invalidates (#33)"
```

---

### Task 3: `elaborate()` learns an environment and an output file

`lean.py` currently writes the candidate to a temp directory as `Main.lean` and runs Lean on it with no environment. Both façades share that core, so it is extended rather than duplicated.

**Files:**
- Modify: `src/hardy/lean.py:197-227` (`elaborate`), `src/hardy/lean.py:261-275` (`LeanTools._run`)
- Test: `tests/test_lean_elaborate.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `elaborate(source, *, argv, cwd, timeout_seconds, max_output_bytes=..., env=None, source_path=None, runner=run_process)`. When `source_path` is given, that file is elaborated in place and no temporary copy is made. `env` is passed through to `ProcessSpec.env`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lean_elaborate.py
from pathlib import Path

from hardy.lean import elaborate
from hardy.process import ProcessResult, ProcessSpec


def recorder(seen: list[ProcessSpec]):
    def run(spec: ProcessSpec) -> ProcessResult:
        seen.append(spec)
        return ProcessResult(argv=spec.argv, returncode=0, stdout="", stderr="", duration_ms=1, timed_out=False, output_overflow=False)

    return run


def test_elaborate_passes_the_environment_through(tmp_path: Path):
    seen: list[ProcessSpec] = []
    elaborate("def a := 1\n", argv=("lean",), cwd=tmp_path, timeout_seconds=5, env={"LEAN_PATH": "X"}, runner=recorder(seen))
    assert seen[0].env == {"LEAN_PATH": "X"}


def test_elaborate_uses_a_given_source_path_rather_than_a_copy(tmp_path: Path):
    seen: list[ProcessSpec] = []
    target = tmp_path / "Group" / "Sylow.lean"
    target.parent.mkdir(parents=True)
    target.write_text("def a := 1\n", encoding="utf-8")
    elaborate("def a := 1\n", argv=("lean",), cwd=tmp_path, timeout_seconds=5, source_path=target, runner=recorder(seen))
    assert seen[0].argv[-1] == str(target)


def test_elaborate_still_uses_a_temporary_file_by_default(tmp_path: Path):
    seen: list[ProcessSpec] = []
    elaborate("def a := 1\n", argv=("lean",), cwd=tmp_path, timeout_seconds=5, runner=recorder(seen))
    assert seen[0].argv[-1].endswith("Main.lean")
    assert not seen[0].argv[-1].startswith(str(tmp_path))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_lean_elaborate.py -q`
Expected: FAIL — `TypeError: elaborate() got an unexpected keyword argument 'env'`

- [ ] **Step 3: Write the implementation**

Replace the body of `elaborate` in `src/hardy/lean.py`:

```python
def elaborate(
    source: str,
    *,
    argv: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
    source_path: Path | None = None,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
) -> Elaboration:
    """Elaborate one Lean source file and return what Lean said about it.

    `source_path` elaborates a file already sitting in a tree, which a
    workspace build needs so that Lean's module name for it — derived from its
    path under a root — is the name other files import it by. Without one the
    source goes to a throwaway `Main.lean`, which is all a single-file check
    ever needed.
    """
    encoded = source.encode("utf-8")

    def run(path: Path) -> ProcessResult:
        return runner(
            ProcessSpec(
                argv=(*argv, str(path)),
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                env=dict(env or {}),
            )
        )

    if source_path is not None:
        process = run(source_path)
    else:
        with tempfile.TemporaryDirectory(prefix="hardy-lean-") as temporary:
            path = Path(temporary) / "Main.lean"
            path.write_bytes(encoded)
            process = run(path)
    diagnostics, open_goals = parse_lean_json(
        "\n".join(part for part in (process.stdout, process.stderr) if part)
    )
    return Elaboration(
        process=process,
        diagnostics=diagnostics,
        open_goals=open_goals,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
    )
```

Then give `LeanTools._run` an optional environment so the interactive tools can reach a workspace build:

```python
    def _run(self, source: str, *, env: dict[str, str] | None = None, source_path: Path | None = None) -> LeanToolResult:
        if self.project is not None and not self.project.is_dir():
            return LeanToolResult(False, f"Lean project directory not found: {self.project}", source)
        try:
            elaboration = elaborate(
                source,
                argv=(*self.lean_command, "--json"),
                cwd=self.project if self.project is not None else Path.cwd(),
                timeout_seconds=self.timeout,
                max_output_bytes=self.max_output_bytes,
                env=env,
                source_path=source_path,
                runner=self._runner,
            )
        except FileNotFoundError:
            return LeanToolResult(False, f"Lean executable not found: {self.lean_command[0]}", source)
        return self._observe(elaboration, source)
```

and widen `run_source` to match:

```python
    def run_source(self, source: str, *, env: dict[str, str] | None = None, source_path: Path | None = None) -> LeanToolResult:
        """Run a complete Lean source file, without claiming it is hole-free."""
        return self._run(source, env=env, source_path=source_path)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_lean_elaborate.py tests/test_hardy.py -q`
Expected: PASS — the existing suite must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/lean.py tests/test_lean_elaborate.py
git commit -m "feat: let an elaboration carry an environment and name its own file (#33)"
```

---

### Task 4: Building the tree — compile, cache, shadow

The part that runs Lean. `LeanWorkspace` is constructed with a callable that elaborates one file, so tests drive it without a Lean at all.

**Files:**
- Modify: `src/hardy/workspace.py`
- Test: `tests/test_workspace_build.py`

**Interfaces:**
- Consumes: `build_order`, `dependents`, `internal_imports`, `module_path`, `module_name` from Tasks 1-2.
- Produces:

```python
class BuildFailure(FrozenModel):
    module: str
    output: str

class LeanWorkspace:
    def __init__(self, root: Path, build: Path, compile: Callable[[str, Path, Path, Path], tuple[bool, str]]) -> None
    def sources(self) -> dict[str, str]
    def read(self, relative: PurePosixPath) -> str | None
    def lean_path(self) -> str
    def build_modules(self, targets: Collection[str]) -> BuildFailure | None
    def stage(self, relative: PurePosixPath, source: str) -> tuple[LeanWorkspace, Callable[[], None]]
```

`compile(module, source_root, build_root, source_file)` returns `(ok, output)`. `stage` returns a shadow workspace holding the edit plus a `commit` callable that copies the shadow's source and build back over the real one.

The build cache lives at `<build>/index.json` as `{module: signature}`, where `signature = sha256(source_sha + "".join(sorted(dependency signatures)))`. A recursive signature makes staleness through a transitive dependency correct without a separate invalidation pass.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workspace_build.py
import json
from pathlib import Path, PurePosixPath

from hardy.workspace import LeanWorkspace


def workspace(tmp_path: Path, compiled: list[str], failing: set[str] | None = None) -> LeanWorkspace:
    failing = failing or set()

    def compile(module, source_root, build_root, source_file):
        compiled.append(module)
        if module in failing:
            return False, f"{module}: type mismatch"
        (build_root / PurePosixPath(*module.split("."))).with_suffix(".olean").parent.mkdir(parents=True, exist_ok=True)
        (build_root / PurePosixPath(*module.split("."))).with_suffix(".olean").write_bytes(b"olean")
        return True, ""

    return LeanWorkspace(tmp_path / "lean", tmp_path / "build", compile)


def write(space: LeanWorkspace, name: str, source: str) -> None:
    path = space.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_build_compiles_dependencies_first(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "import Mathlib\ndef a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]
    assert (tmp_path / "build" / "Basic.olean").exists()


def test_a_second_build_compiles_nothing(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    compiled.clear()
    assert space.build_modules(["Basic"]) is None
    assert compiled == []


def test_editing_a_dependency_rebuilds_its_dependents(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    space.build_modules(["Main"])
    compiled.clear()
    write(space, "Basic.lean", "def a := 2\n")
    assert space.build_modules(["Main"]) is None
    assert compiled == ["Basic", "Main"]


def test_a_failure_names_the_module_and_stops_the_build(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    write(space, "Basic.lean", "def a := 1\n")
    write(space, "Main.lean", "import Basic\ndef b := a\n")
    failure = space.build_modules(["Main"])
    assert failure is not None and failure.module == "Basic"
    assert compiled == ["Basic"]
    assert json.loads((tmp_path / "build" / "index.json").read_text()) == {}


def test_a_failed_module_is_rebuilt_next_time(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled, failing={"Basic"})
    space_root = space.root
    space_root.mkdir(parents=True, exist_ok=True)
    (space_root / "Basic.lean").write_text("def a := 1\n", encoding="utf-8")
    space.build_modules(["Basic"])
    compiled.clear()
    space.build_modules(["Basic"])
    assert compiled == ["Basic"]


def test_stage_leaves_the_real_tree_untouched_until_committed(tmp_path: Path):
    compiled: list[str] = []
    space = workspace(tmp_path, compiled)
    write(space, "Basic.lean", "def a := 1\n")
    space.build_modules(["Basic"])
    shadow, commit = space.stage(PurePosixPath("Basic.lean"), "def a := 99\n")
    assert shadow.build_modules(["Basic"]) is None
    assert (space.root / "Basic.lean").read_text() == "def a := 1\n"
    commit()
    assert (space.root / "Basic.lean").read_text() == "def a := 99\n"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_workspace_build.py -q`
Expected: FAIL — `ImportError: cannot import name 'LeanWorkspace'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/hardy/workspace.py
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from .domain import FrozenModel

Compile = Callable[[str, Path, Path, Path], tuple[bool, str]]


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
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A half-written index must cost a rebuild, never a crash on open.
            return {}

    def _write_index(self, index: dict[str, str]) -> None:
        self.build.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.index_path)

    def _signatures(self, sources: dict[str, str], order: tuple[str, ...]) -> dict[str, str]:
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
        """Compile `targets` and whatever they need, newest work last.

        Returns the first failure rather than raising: a broken proof is an
        answer the model has to read, not an exception the session dies of.
        """
        sources = self.sources()
        missing = [name for name in targets if name not in sources]
        if missing:
            return BuildFailure(module=missing[0], output=f"no such workspace module: {missing[0]}")
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
            ok, output = self._compile(module, self.root, self.build, self.root / module_path(module))
            if not ok:
                return BuildFailure(module=module, output=output)
            index[module] = signatures[module]
            self._write_index(index)
        return None

    def stage(self, relative: PurePosixPath, source: str | None) -> tuple[LeanWorkspace, Callable[[], None]]:
        """A copy of this workspace carrying one edit, and a way to keep it.

        A save that broke a module importing the edited one would leave the
        workspace red, so the edit is built somewhere else first and only
        copied back when everything that depends on it still compiles.
        `source` of None stages a deletion.
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
            try:
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
            finally:
                shutil.rmtree(temporary, ignore_errors=True)

        return shadow, commit
```

Add a `discard` alongside `commit` is unnecessary: the caller drops the temporary directory by calling `shutil.rmtree` on `shadow.root.parent` when it refuses. Expose that as a second returned callable instead — amend the signature to return `(shadow, commit, discard)` and update the test accordingly if the reviewer prefers; this plan keeps two values and has the caller clean up via `shutil.rmtree(shadow.root.parent, ignore_errors=True)`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_workspace_build.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/workspace.py tests/test_workspace_build.py
git commit -m "feat: build a workspace Lean tree incrementally, and stage an edit (#33)"
```

---

### Task 5: Wire the workspace into the session, with migration

`MathematicsSession` gains a `LeanWorkspace` and moves an old flat workspace into the new layout on open.

**Files:**
- Modify: `src/hardy/chat.py:78-131` (`__init__`), `src/hardy/chat.py:227-249` (`_run_lean_source`, `_tool`)
- Test: `tests/test_chat_workspace.py`

**Interfaces:**
- Consumes: `LeanWorkspace`, `safe_relative`, `module_name` from Tasks 1-4.
- Produces: `MathematicsSession.lean_workspace: LeanWorkspace`, `MathematicsSession.tex_root: Path`, and a `_compile_module` bound to `LeanTools`.

Layout constants: `LEAN_DIR = "lean"`, `BUILD_DIR = ".build/lean"`, `TEX_DIR = "tex"`, `DEFAULT_LEAN_PATH = "Main.lean"`, `DEFAULT_TEX_PATH = "writeup.tex"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_workspace.py
from pathlib import Path

from tests.test_chat import FakeChatRuntime, session


def test_a_flat_workspace_is_migrated_on_open(tmp_path: Path):
    (tmp_path / "Main.lean").write_text("import Mathlib\ndef a := 1\n", encoding="utf-8")
    (tmp_path / "writeup.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    session(tmp_path, FakeChatRuntime([]))
    assert (tmp_path / "lean" / "Main.lean").read_text().startswith("import Mathlib")
    assert (tmp_path / "tex" / "writeup.tex").read_text().startswith("\\documentclass")
    assert not (tmp_path / "Main.lean").exists()
    transcript = (tmp_path / "transcript.jsonl").read_text()
    assert "migration" in transcript and "layout" in transcript


def test_a_new_workspace_needs_no_migration(tmp_path: Path):
    session(tmp_path, FakeChatRuntime([]))
    assert "migration" not in (tmp_path / "transcript.jsonl").read_text() if (tmp_path / "transcript.jsonl").exists() else True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_workspace.py -q`
Expected: FAIL — `Main.lean` still at the top level.

- [ ] **Step 3: Write the implementation**

In `chat.py`, add the constants and a compile closure, then in `__init__` after `self.workspace.mkdir(...)`:

```python
LEAN_DIR = "lean"
BUILD_DIR = ".build/lean"
TEX_DIR = "tex"
DEFAULT_LEAN_PATH = "Main.lean"
DEFAULT_TEX_PATH = "writeup.tex"
```

```python
        self.lean = LeanTools(placeholder, lean_command, timeout=lean_timeout, project=lean_project)
        self.tex_root = workspace / TEX_DIR
        self.lean_workspace = LeanWorkspace(
            workspace / LEAN_DIR, workspace / BUILD_DIR, self._compile_module
        )
        self._migrate_layout()
```

```python
    def _compile_module(self, module: str, source_root: Path, build_root: Path, source_file: Path) -> tuple[bool, str]:
        """Compile one workspace module to an olean.

        `--root` is not optional: without it Lean derives a module name from the
        directory it was started in and refuses a source file that is not
        underneath it. `LEAN_PATH` reaches the modules already built, and
        survives `lake env`, which augments the inherited value rather than
        replacing it.
        """
        result = self.lean.compile_module(source_root, build_root, source_file)
        return result.ok, result.output

    def _migrate_layout(self) -> None:
        """Move a workspace written before the tree existed into it."""
        moves = ((DEFAULT_LEAN_PATH, self.lean_workspace.root), (DEFAULT_TEX_PATH, self.tex_root))
        moved = []
        for name, destination in moves:
            legacy = self.workspace / name
            if not legacy.is_file() or (destination / name).exists():
                continue
            destination.mkdir(parents=True, exist_ok=True)
            os.replace(legacy, destination / name)
            moved.append(name)
        if moved:
            self._record({"type": "migration", "reason": "layout", "moved": moved})
```

Add `compile_module` to `LeanTools` in `lean.py`:

```python
    def compile_module(self, source_root: Path, build_root: Path, source_file: Path) -> LeanToolResult:
        """Build one file to an olean so other workspace files can import it."""
        source = source_file.read_text(encoding="utf-8")
        olean = (build_root / source_file.relative_to(source_root)).with_suffix(".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        argv = (*self.lean_command, "--json", f"--root={source_root}", "-o", str(olean))
        if self.project is not None and not self.project.is_dir():
            return LeanToolResult(False, f"Lean project directory not found: {self.project}", source)
        try:
            elaboration = elaborate(
                source,
                argv=argv,
                cwd=self.project if self.project is not None else Path.cwd(),
                timeout_seconds=self.timeout,
                max_output_bytes=self.max_output_bytes,
                env={"LEAN_PATH": str(build_root)},
                source_path=source_file,
                runner=self._runner,
            )
        except FileNotFoundError:
            return LeanToolResult(False, f"Lean executable not found: {self.lean_command[0]}", source)
        return self._observe(elaboration, source)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_chat_workspace.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py src/hardy/lean.py tests/test_chat_workspace.py
git commit -m "feat: give the session a Lean tree and migrate the flat layout (#33)"
```

---

### Task 6: Path-aware `check_lean` and `save_lean` with the shadow build

**Files:**
- Modify: `src/hardy/chat.py:20-28` (`CHAT_TOOLS`), `src/hardy/chat.py:227-249`
- Modify: `tests/fake_lean.py`
- Test: `tests/test_chat_multifile.py`

`fake_lean.py` must learn the new invocation: when `-o <path>` is present it writes a stand-in olean and exits 0, and it fails when a non-Mathlib import has no olean on `LEAN_PATH` — otherwise the tests could not tell a resolved import from an unresolved one.

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: `check_lean(path, source)` and `save_lean(path, source)` tool schemas with `path` optional, defaulting to `Main.lean`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_multifile.py
from pathlib import Path

from tests.test_chat import FakeChatRuntime, call, session

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"
MAIN = "import Basic\nlemma hardyMain : True := by exact True.intro\n"


def test_saving_two_files_lets_one_import_the_other(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        {"role": "assistant", "content": "Both files are saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Split the development.")
    assert (tmp_path / "lean" / "Basic.lean").exists()
    assert (tmp_path / "lean" / "Main.lean").exists()
    assert all(result.ok for result in runtime.results)


def test_a_save_that_breaks_a_dependent_is_refused_whole(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": MAIN}),
        call("save_lean", {"path": "Basic.lean", "source": "import Mathlib\nlemma hardyBasic : True := by exact False.elim\n"}),
        {"role": "assistant", "content": "The edit would break Main."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Break the base file.")
    assert runtime.results[-1].ok is False
    assert (tmp_path / "lean" / "Basic.lean").read_text() == BASIC


def test_a_path_outside_the_workspace_is_refused(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "../escape.lean", "source": BASIC}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Escape.")
    assert runtime.results[-1].ok is False
    assert not (tmp_path.parent / "escape.lean").exists()


def test_path_defaults_to_main(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": BASIC}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save the default file.")
    assert (tmp_path / "lean" / "Main.lean").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_multifile.py -q`
Expected: FAIL — `save_lean` ignores `path`.

- [ ] **Step 3: Write the implementation**

Update the two tool schemas:

```python
    {"type": "function", "function": {"name": "check_lean", "description": "Run Lean on a complete candidate source file without saving it. `path` is the workspace file it would become, defaulting to Main.lean; imports of other workspace files resolve against what is already saved.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "save_lean", "description": "Check and save one Lean file in the workspace tree, defaulting to Main.lean. Every file that imports it is rebuilt, and the save is refused whole if any of them breaks. Completed saved work must contain no sorry or admit.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "path": {"type": "string"}}, "required": ["source"], "additionalProperties": False}}},
```

Rewrite the two handlers in `_tool`:

```python
        if name == "check_lean":
            return self._check_lean(str(arguments.get("path") or DEFAULT_LEAN_PATH), str(arguments["source"]))
        if name == "save_lean":
            return self._save_lean(str(arguments.get("path") or DEFAULT_LEAN_PATH), str(arguments["source"]))
```

```python
    def _check_lean(self, path: str, source: str) -> ToolResult:
        try:
            relative = safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        failure = self._build_dependencies(self.lean_workspace, source)
        if failure is not None:
            return ToolResult(False, f"a workspace file this one imports does not build: {failure.module}\n{failure.output}", source)
        result = self._run_lean_source(source, final=False)
        return result

    def _save_lean(self, path: str, source: str) -> ToolResult:
        try:
            relative = safe_relative(path)
        except WorkspacePathError as error:
            return ToolResult(False, str(error), source)
        gate = self._documentation_gate(source)
        if gate is not None:
            return ToolResult(False, gate, source)
        checked = self._run_lean_source(source, final=True)
        if not checked.ok:
            return checked
        shadow, commit = self.lean_workspace.stage(relative, source.rstrip() + "\n")
        try:
            module = module_name(relative)
            failure = shadow.build_modules([module, *sorted(dependents(shadow.sources(), module))])
            if failure is not None:
                return ToolResult(False, f"this save breaks {failure.module}, so nothing was written:\n{failure.output}", source)
            commit()
        finally:
            shutil.rmtree(shadow.root.parent, ignore_errors=True)
        return checked

    def _build_dependencies(self, space: LeanWorkspace, source: str) -> BuildFailure | None:
        sources = space.sources()
        needed = internal_imports(source, sources)
        return space.build_modules(needed) if needed else None
```

`_run_lean_source` gains the workspace environment so a candidate's imports resolve:

```python
        return self.lean.run_source(source, env={"LEAN_PATH": self.lean_workspace.lean_path()})
```

`_documentation_gate` is a stub returning `None` until Task 8; leave a one-line comment saying so.

Update `tests/fake_lean.py` so it models the build:

```python
#!/usr/bin/env python3
import os
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

argv = sys.argv[1:]
output = None
root = None
for index, item in enumerate(argv):
    if item == "-o":
        output = pathlib.Path(argv[index + 1])
    elif item.startswith("--root="):
        root = pathlib.Path(item.removeprefix("--root="))

path = pathlib.Path(argv[-1])
source = path.read_text(encoding="utf-8")

# A workspace import only resolves if the module was built, exactly as Lean
# resolves one against an olean on LEAN_PATH rather than against a source file.
search = [pathlib.Path(part) for part in os.environ.get("LEAN_PATH", "").split(os.pathsep) if part]
for line in source.splitlines():
    if not line.startswith("import "):
        continue
    name = line.removeprefix("import ").strip()
    if name.split(".")[0] in {"Mathlib", "Init", "Std", "Lean", "Batteries"}:
        continue
    relative = pathlib.Path(*name.split(".")).with_suffix(".olean")
    if not any((directory / relative).is_file() for directory in search):
        print(f"{path.name}:1:0: error: unknown module prefix '{name}'")
        raise SystemExit(1)

broken = "False.elim" in source or ("sorry" in source or "admit" in source)
if not broken and ("exact True.intro" in source or "trivial" in source or source.strip().startswith("def ")):
    if "#print axioms" in source:
        print("'HardyTarget' depends on axioms: []")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"olean-fake")
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
if "#check True.intro" in source:
    print("True.intro : True")
    raise SystemExit(0)
print("Main.lean:3:28: error: type mismatch")
raise SystemExit(1)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_chat_multifile.py tests/test_chat.py -q`
Expected: PASS. `tests/test_chat.py` asserts on `tmp_path / "Main.lean"`; update those assertions to `tmp_path / "lean" / "Main.lean"` as part of this task.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/fake_lean.py tests/test_chat.py tests/test_chat_multifile.py
git commit -m "feat: check and save any file in the workspace Lean tree (#33)"
```

---

### Task 7: Reading and deleting workspace files

`read_workspace` currently returns the whole text of two hardcoded files, which does not survive a tree.

**Files:**
- Modify: `src/hardy/chat.py:20-28`, `src/hardy/chat.py:263-268`
- Test: `tests/test_chat_files.py`

**Interfaces:**
- Produces: `read_workspace()` returning `{"manifest", "lean": [{"path", "module", "theorems", "lemmas"}], "tex": [paths]}`; `read_file(path)`; `delete_file(path)`.

`read_file` accepts a path under `lean/` or `tex/`, distinguished by suffix.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_files.py
import json
from pathlib import Path

from tests.test_chat import FakeChatRuntime, call, session

BASIC = "import Mathlib\nlemma hardyBasic : True := by exact True.intro\n"


def test_read_workspace_lists_the_tree_rather_than_two_files(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("read_workspace", {}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("What is here?")
    payload = json.loads(runtime.results[-1].output)
    assert [entry["path"] for entry in payload["lean"]] == ["Basic.lean"]
    assert payload["lean"][0]["module"] == "Basic"
    assert payload["lean"][0]["lemmas"] == ["hardyBasic"]


def test_read_file_returns_contents(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("read_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Read."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Show me the file.")
    assert "hardyBasic" in runtime.results[-1].output


def test_delete_file_is_refused_when_something_imports_it(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": BASIC}),
        call("save_lean", {"path": "Main.lean", "source": "import Basic\nlemma hardyMain : True := by exact True.intro\n"}),
        call("delete_file", {"path": "Basic.lean"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Delete the base.")
    assert runtime.results[-1].ok is False
    assert (tmp_path / "lean" / "Basic.lean").exists()


def test_delete_file_removes_an_unimported_file(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Scratch.lean", "source": BASIC}),
        call("delete_file", {"path": "Scratch.lean"}),
        {"role": "assistant", "content": "Deleted."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Drop the scratch.")
    assert runtime.results[-1].ok is True
    assert not (tmp_path / "lean" / "Scratch.lean").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_files.py -q`
Expected: FAIL — `unknown tool: read_file`

- [ ] **Step 3: Write the implementation**

Add a declaration scanner to `workspace.py`:

```python
DECLARATION = re.compile(
    r"(?m)^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+|nonrec\s+|noncomputable\s+)*"
    r"(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)"
)
NAMESPACE = re.compile(r"(?m)^\s*namespace\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")
END = re.compile(r"(?m)^\s*end\s+([A-Za-z_][A-Za-z0-9_'.]*)\s*$")


def declarations(source: str) -> dict[str, tuple[str, ...]]:
    """Top-level `theorem` and `lemma` names, qualified by namespace.

    Both the bare and the qualified name are produced for a declaration inside
    a namespace, because the registry may reasonably record either and a
    mismatch would silently make a documented theorem look undocumented.
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
            found[kind].append(name)
            if scope:
                found[kind].append(".".join((*scope, name)))
    return {kind: tuple(names) for kind, names in found.items()}
```

Add the tool schemas and handlers:

```python
    {"type": "function", "function": {"name": "read_workspace", "description": "List the workspace: the manifest, every Lean file with its module name and declarations, and every LaTeX file.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read one workspace file, Lean or LaTeX, by its path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete one workspace file. Refused if another workspace file imports it.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
```

```python
        if name == "read_workspace":
            return ToolResult(True, json.dumps(self._workspace_listing(), ensure_ascii=False))
        if name == "read_file":
            return self._read_file(str(arguments["path"]))
        if name == "delete_file":
            return self._delete_file(str(arguments["path"]))
```

```python
    def _workspace_listing(self) -> dict[str, Any]:
        lean = []
        for module, source in sorted(self.lean_workspace.sources().items()):
            found = declarations(source)
            lean.append({
                "path": str(module_path(module)),
                "module": module,
                "theorems": list(found["theorem"]),
                "lemmas": list(found["lemma"]),
            })
        tex = sorted(str(path.relative_to(self.tex_root).as_posix()) for path in self.tex_root.rglob("*.tex")) if self.tex_root.is_dir() else []
        return {"manifest": self.state, "lean": lean, "tex": tex}

    def _resolve(self, path: str) -> tuple[Path, str] | ToolResult:
        """Where a tool path lives: the Lean tree or the TeX tree."""
        cleaned = str(path).replace("\\", "/").lstrip("./")
        if cleaned.endswith(".lean"):
            try:
                return self.lean_workspace.root / safe_relative(cleaned), "lean"
            except WorkspacePathError as error:
                return ToolResult(False, str(error))
        if cleaned.endswith(".tex"):
            candidate = (self.tex_root / cleaned).resolve()
            if not str(candidate).startswith(str(self.tex_root.resolve())):
                return ToolResult(False, f"path escapes the workspace: {path!r}")
            return candidate, "tex"
        return ToolResult(False, f"not a workspace file: {path!r}")

    def _read_file(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, _ = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        return ToolResult(True, target.read_text(encoding="utf-8"))

    def _delete_file(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolResult):
            return resolved
        target, kind = resolved
        if not target.is_file():
            return ToolResult(False, f"no such workspace file: {path}")
        if kind == "tex":
            target.unlink()
            return ToolResult(True, f"deleted {path}")
        relative = safe_relative(str(path).replace("\\", "/"))
        module = module_name(relative)
        importers = dependents(self.lean_workspace.sources(), module)
        if importers:
            return ToolResult(False, f"{module} is imported by {sorted(importers)}; change those first")
        shadow, commit = self.lean_workspace.stage(relative, None)
        try:
            commit()
        finally:
            shutil.rmtree(shadow.root.parent, ignore_errors=True)
        return ToolResult(True, f"deleted {path}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_chat_files.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py src/hardy/workspace.py tests/test_chat_files.py
git commit -m "feat: list, read, and delete files in the workspace tree (#33)"
```

---

### Task 8: The documentation ratchet

**Files:**
- Modify: `src/hardy/chat.py` (`_documentation_gate`, `save_latex` handler)
- Test: `tests/test_chat_ratchet.py`

**Interfaces:**
- Consumes: `declarations` from Task 7.
- Produces: `MathematicsSession._undocumented() -> tuple[str, ...]`, `MathematicsSession._documentation_gate(source) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_ratchet.py
from pathlib import Path

from tests.test_chat import FakeChatRuntime, call, session

FIRST = "import Mathlib\ntheorem hardyOne : True := by exact True.intro\n"
SECOND = "import Mathlib\ntheorem hardyTwo : True := by exact True.intro\n"
LEMMAS = "import Mathlib\nlemma hardyHelper : True := by exact True.intro\n"
TEX = "\\documentclass{article}\n\\begin{document}One.\\label{thm:one}\\end{document}\n"


def test_a_second_theorem_is_refused_while_the_first_is_undocumented(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Blocked."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove two things.")
    assert runtime.results[0].ok is True
    assert runtime.results[1].ok is False
    assert "hardyOne" in runtime.results[1].output
    assert not (tmp_path / "lean" / "Two.lean").exists()


def test_documenting_the_first_releases_the_ratchet(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("save_latex", {"source": TEX}),
        call("save_lean", {"path": "Two.lean", "source": SECOND}),
        {"role": "assistant", "content": "Both saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Prove, write up, prove again.")
    assert all(result.ok for result in runtime.results)
    assert (tmp_path / "lean" / "Two.lean").exists()


def test_lemmas_never_trip_the_ratchet(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "A.lean", "source": LEMMAS}),
        call("save_lean", {"path": "B.lean", "source": LEMMAS.replace("hardyHelper", "hardyOther")}),
        {"role": "assistant", "content": "Both saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save scaffolding.")
    assert all(result.ok for result in runtime.results)


def test_repairing_an_undocumented_theorem_is_allowed(tmp_path: Path):
    """Condition 2 of the ratchet: no new theorem name, so no refusal."""
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "One.lean", "source": FIRST}),
        call("save_lean", {"path": "One.lean", "source": FIRST.replace("True.intro", "trivial")}),
        {"role": "assistant", "content": "Repaired."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Fix the proof.")
    assert all(result.ok for result in runtime.results)


def test_a_partial_writeup_saves_with_an_advisory(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "hardyOne", "latex_name": "thm:one", "description": "One."}),
        call("record_name", {"formal_name": "hardyTwo", "latex_name": "thm:two", "description": "Two."}),
        call("save_latex", {"source": TEX}),
        {"role": "assistant", "content": "Partial writeup saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Write up what exists.")
    assert runtime.results[-1].ok is True
    assert "thm:two" in runtime.results[-1].output
    assert (tmp_path / "tex" / "writeup.tex").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_ratchet.py -q`
Expected: FAIL — the second save succeeds.

- [ ] **Step 3: Write the implementation**

```python
    def _labels(self) -> set[str]:
        """Every `\\label` in the saved TeX tree."""
        if not self.tex_root.is_dir():
            return set()
        found: set[str] = set()
        for path in self.tex_root.rglob("*.tex"):
            found.update(LABEL.findall(path.read_text(encoding="utf-8")))
        return found

    def _undocumented(self) -> tuple[str, ...]:
        """Saved theorems with no writeup behind them.

        Derived from the registry and the TeX tree every time it is asked for.
        A stored flag would outlive the file it described, and `session.json`
        already carries enough state that has to be kept true.
        """
        labels = self._labels()
        documented = {
            item["formal_name"]
            for item in self.state["names"]
            if item["latex_name"] in labels
        }
        saved: set[str] = set()
        for source in self.lean_workspace.sources().values():
            saved.update(declarations(source)["theorem"])
        return tuple(sorted(name for name in saved if name not in documented))

    def _documentation_gate(self, source: str) -> str | None:
        """The catch-up ratchet.

        Refuses only when the tree already owes a writeup *and* this save would
        add a theorem it does not already contain. The first condition alone
        would trap a session: a model could no longer repair, restate, or
        delete the very theorem blocking it. The second alone would let one
        file absorb any number of undocumented claims.
        """
        owed = self._undocumented()
        if not owed:
            return None
        existing: set[str] = set()
        for saved in self.lean_workspace.sources().values():
            existing.update(declarations(saved)["theorem"])
        introduced = [name for name in declarations(source)["theorem"] if name not in existing]
        if not introduced:
            return None
        return (
            f"the workspace owes a writeup for {list(owed)} before a new theorem "
            f"({introduced[0]}) is added. Call record_name for each, then save_latex "
            "with a \\label for each latex_name."
        )
```

with `LABEL = re.compile(r"\\label\{([^}]*)\}")` beside the other module-level patterns.

Then relax the `save_latex` refusal at `chat.py:254`:

```python
        if name == "save_latex":
            return self._save_latex(str(arguments.get("path") or DEFAULT_TEX_PATH), str(arguments["source"]))
```

```python
    def _save_latex(self, path: str, source: str) -> ToolResult:
        result = self.latex.check(source, path=path, tree=self.tex_root, output_dir=self.workspace)
        if not result.ok:
            return result
        target = self.tex_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.rstrip() + "\n", encoding="utf-8")
        # Advisory, not a refusal: with the save_lean ratchet in place a hard
        # gate here would deadlock -- Lean blocked for want of a writeup, the
        # writeup blocked for not yet covering everything.
        missing = [item["latex_name"] for item in self.state["names"] if item["latex_name"] not in self._labels()]
        if missing:
            return ToolResult(True, f"{result.output}\n\nSaved. Still missing labels for registered names: {missing}", source)
        return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_chat_ratchet.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/test_chat_ratchet.py
git commit -m "feat: make a writeup a condition of proving the next theorem (#33)"
```

---

### Task 9: LaTeX across files

**Files:**
- Modify: `src/hardy/latex.py:20-41`
- Test: `tests/test_latex_tree.py`

**Interfaces:**
- Produces: `LatexTools.check(source, *, path=DEFAULT_TEX_PATH, tree=None, output_dir=None)`. The whole `tree` is copied into the compile directory, the candidate overlaid at `path`, and the root document compiled.

The root document is always `writeup.tex`: compiling a fragment saved at `sections/one.tex` on its own would fail for want of a preamble, so the tree is compiled from its root whatever file the candidate is.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_latex_tree.py
import sys
from pathlib import Path

from hardy.latex import LatexTools

COMMAND = (sys.executable, str(Path(__file__).with_name("fake_latex.py")))
ROOT = "\\documentclass{article}\n\\begin{document}\\input{sections/one}\\end{document}\n"


def test_input_resolves_against_the_saved_tree(tmp_path: Path):
    tree = tmp_path / "tex"
    (tree / "sections").mkdir(parents=True)
    (tree / "writeup.tex").write_text(ROOT, encoding="utf-8")
    (tree / "sections" / "one.tex").write_text("Section one.\\label{sec:one}\n", encoding="utf-8")
    result = LatexTools(COMMAND).check("Section one, revised.\\label{sec:one}\n", path="sections/one.tex", tree=tree)
    assert result.ok


def test_a_candidate_root_overrides_the_saved_one(tmp_path: Path):
    tree = tmp_path / "tex"
    tree.mkdir(parents=True)
    (tree / "writeup.tex").write_text("broken", encoding="utf-8")
    result = LatexTools(COMMAND).check("\\documentclass{article}\n\\begin{document}Fine.\\end{document}\n", tree=tree)
    assert result.ok


def test_check_still_works_with_no_tree(tmp_path: Path):
    result = LatexTools(COMMAND).check("\\documentclass{article}\n\\begin{document}Fine.\\end{document}\n")
    assert result.ok
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_latex_tree.py -q`
Expected: FAIL — `check() got an unexpected keyword argument 'path'`

- [ ] **Step 3: Write the implementation**

```python
    def check(self, source: str, *, path: str = "writeup.tex", tree: Path | None = None, output_dir: Path | None = None) -> ToolResult:
        """Compile a candidate against the documents already saved.

        The whole tree is copied in so `\\input` resolves, and the root document
        is what gets compiled whatever file the candidate is: a fragment has no
        preamble and would fail on its own for a reason that says nothing about
        the mathematics.
        """
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as directory:
            work = Path(directory)
            if tree is not None and tree.is_dir():
                shutil.copytree(tree, work, dirs_exist_ok=True)
            candidate = work / path
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(source, encoding="utf-8")
            root = work / "writeup.tex"
            if not root.is_file():
                root.write_text(source, encoding="utf-8")
            try:
                process = subprocess.run(
                    [*self.command, root.name], cwd=work, capture_output=True,
                    text=True, timeout=self.timeout, check=False,
                )
                output = (process.stdout + process.stderr).strip()[-self.output_limit :]
                elapsed = time.monotonic() - started
                pdf = work / "writeup.pdf"
                if process.returncode == 0 and output_dir is not None and pdf.exists():
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(pdf, output_dir / "writeup.pdf")
                return ToolResult(process.returncode == 0, f"exit={process.returncode} elapsed={elapsed:.3f}s\n{output}", source)
            except subprocess.TimeoutExpired as error:
                output = ((error.stdout or "") + (error.stderr or ""))[-self.output_limit :]
                return ToolResult(False, f"timeout after {self.timeout:.1f}s\n{output}", source)
            except FileNotFoundError:
                return ToolResult(False, f"LaTeX executable not found: {self.command[0]}", source)
```

Add `path` to the `check_latex` and `save_latex` schemas in `chat.py`, and route `check_latex` through the tree the same way.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest tests/test_latex_tree.py tests/test_chat_ratchet.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/latex.py src/hardy/chat.py tests/test_latex_tree.py
git commit -m "feat: compile LaTeX against the saved document tree (#33)"
```

---

### Task 10: The prompt, and the documentation that must agree with it

`chat.md.j2:11` is the direct cause of the missing writeups and cannot survive unedited.

**Files:**
- Modify: `src/hardy/prompts/chat.md.j2`
- Modify: `README.md:143-150`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts.py
from hardy.prompts import CHAT_SYSTEM_PROMPT


def test_the_prompt_describes_the_file_tree_and_the_ratchet():
    text = CHAT_SYSTEM_PROMPT
    assert "path" in text and "import" in text
    assert "theorem" in text and "lemma" in text
    # The writeup requirement has to be stated, or the model meets it by surprise.
    assert "writeup" in text.lower()


def test_the_prompt_no_longer_forbids_writing_up_what_was_just_proved():
    assert "refactor the file" not in CHAT_SYSTEM_PROMPT
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_prompts.py -q`
Expected: FAIL — `"refactor the file"` is still present.

- [ ] **Step 3: Rewrite the prompt**

Replace lines 5-13 of `src/hardy/prompts/chat.md.j2` with text covering:

- `check_lean(path, source)` and `save_lean(path, source)`; the workspace is a tree under `lean/`, `path` defaults to `Main.lean`, and files import each other by module name (`Group/Sylow.lean` is `import Group.Sylow`).
- A save rebuilds every file importing the one edited and is refused whole if any breaks.
- `read_workspace`, `read_file`, `delete_file`.
- `check_latex(path, source)` / `save_latex(path, source)`; the writeup is a tree under `tex/` rooted at `writeup.tex`, and fragments are `\input` from it.
- The ratchet, stated plainly: a `theorem` you save must be recorded with `record_name` and given a `\label` in the writeup before you may save a *new* theorem. `lemma`, `def`, and `instance` carry no such requirement — so use `lemma` for scaffolding, and `theorem` for any result you report.
- The revised initiative paragraph: still do not choose the next piece of mathematics for the user, still ask when the goal or the level of formality is unclear — but writing up a result you have just proved is part of finishing it, not a new project, and needs no invitation.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --extra test pytest -q`
Expected: PASS — the whole suite.

- [ ] **Step 5: Update the prose documentation**

`README.md:143-150` describes a workspace containing `Main.lean` and `writeup.tex`; rewrite for the tree, the migration, and the ratchet. Mirror the change in `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` per `AGENTS.md`'s consistency rule.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/prompts/chat.md.j2 tests/test_prompts.py README.md DESIGN.md FEATURES.md ARCHITECTURE.html
git commit -m "docs: tell the model about the tree, and stop forbidding the writeup (#33)"
```

---

### Task 11: Real-toolchain integration test

The unit suite runs against `fake_lean.py`, which models the import rule rather than implementing it. One test must exercise real Lean, marked so it is skipped where no toolchain exists — the same shape as `tests/integration/test_lean_real.py`.

**Files:**
- Create: `tests/integration/test_workspace_real.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_workspace_real.py
"""Cross-file imports against a real Lean. Skipped without a toolchain."""

from pathlib import Path

import pytest

from hardy.config import load_config
from hardy.lean import LeanTools
from hardy.models import Request
from hardy.workspace import LeanWorkspace

pytestmark = pytest.mark.integration


def test_a_workspace_module_is_importable_by_another(tmp_path: Path):
    config = load_config()
    if config.lean_project is None or not config.lean_project.is_dir():
        pytest.skip("no Lean project configured")
    tools = LeanTools(Request("example : True", "workspace", ()), config.lean_command, timeout=config.lean_timeout, project=config.lean_project)
    space = LeanWorkspace(
        tmp_path / "lean",
        tmp_path / "build",
        lambda module, source_root, build_root, source_file: (
            lambda result: (result.ok, result.output)
        )(tools.compile_module(source_root, build_root, source_file)),
    )
    space.root.mkdir(parents=True)
    (space.root / "Basic.lean").write_text("def hardyAnswer : Nat := 42\n", encoding="utf-8")
    (space.root / "Main.lean").write_text("import Basic\ntheorem t : hardyAnswer = 42 := rfl\n", encoding="utf-8")
    assert space.build_modules(["Main"]) is None
    assert (tmp_path / "build" / "Basic.olean").is_file()
```

- [ ] **Step 2: Run it**

Run: `uv run --extra test pytest tests/integration/test_workspace_real.py -q`
Expected: PASS on a machine with Lean, SKIP otherwise. Deliberately imports no Mathlib, so it costs seconds rather than minutes.

- [ ] **Step 3: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_workspace_real.py
git commit -m "test: prove cross-file imports against a real Lean toolchain (#33)"
```

---

## Self-review

**Spec coverage.** Layout and migration → Task 5. `workspace.py` naming, graph, staleness, compile → Tasks 1, 2, 4. `elaborate` env/root → Task 3. Shadow build → Tasks 4, 6. Tool surface table → Tasks 6, 7, 9. Ratchet → Task 8. `save_latex` advisory → Task 8. LaTeX tree → Task 9. Prompt → Task 10. Testing section → distributed, plus Task 11 for the real toolchain. Known costs and out-of-scope need no task.

**Type consistency.** `compile(module, source_root, build_root, source_file) -> (ok, output)` is the same four-argument shape in Tasks 4, 5, and 11. `BuildFailure(module, output)` is consumed with those field names in Task 6. `declarations(source)` returns `{"theorem": (...), "lemma": (...)}` in Task 7 and is read with those keys in Tasks 7 and 8. `stage(relative, source)` returns two values everywhere, with the caller cleaning up via `shutil.rmtree(shadow.root.parent)`.

**Known deviation.** Task 4's note about a third `discard` return value is a design choice left to the implementer; the plan commits to two values and cleanup by the caller, and Tasks 6 and 7 are written that way.
