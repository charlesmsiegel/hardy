# On-Disk Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Hardy's authored work and its record out of the gitignored `.hardy/` scratch directory into a versioned, per-problem directory, leaving `.hardy/` as committed project tooling and gitignoring only what is genuinely recomputable or machine-local.

**Architecture:** A new `layout.py` owns every path decision and is the only module that knows where things live; `config.py` becomes two layers (global `~/.hardy/`, project `<root>/.hardy/`); `chat.py` keeps taking one problem directory, so its internals barely move, but its machine-local state splits out to `<slug>/.local/state.json`. A new `lakefile.py` owns the optional registration with a host Lake project.

**Tech Stack:** Python 3.11+ (`tomllib`), pytest, `uv`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-25-on-disk-layout-design.md`

## Global Constraints

- **No workspace migration.** Hardy creates and reads only the new layout. `_migrate_layout` is deleted; `--workspace` and `HARDY_WORKSPACE` are removed outright, with no alias and no back-compat branch.
- **`session.json` starts at `schema_version` 2.** No reader for version 1.
- **No POSIX-only assumptions.** Windows is a supported platform; use `pathlib` and `os.replace`, never shell path handling. The no-WSL rule holds.
- **Never prompt when stdin is not a TTY.** Both project selection and lakefile registration have deterministic non-interactive fallbacks and never read stdin to decide.
- **The slug is untrusted input.** It reaches path derivation from a committed, hand-editable file.
- **Generated ignore rules are anchored** (`/.build/`, not `.build/`).
- **The audit path, the verifier and the record keep their current guarantees** wherever they end up living: refuse-whole on a broken dependent, dependents rebuilt, per-module audit verdicts, the documentation ratchet.
- Run the suite with `uv run --extra test pytest`. It has a coverage floor configured in `pyproject.toml`.
- Keep `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` consistent (repository rule in `AGENTS.md`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/hardy/layout.py` (new) | Every path decision: root discovery, slug validation, problem/global directory derivation, directory creation, anchored `.gitignore` writing. The only module that knows the shape on disk. |
| `src/hardy/lakefile.py` (new) | The optional registration of `<slug>/lean` with a host `lakefile.toml`, including the duplicate-module refusal. |
| `src/hardy/config.py` | Two configuration layers and the one-file relocation into `~/.hardy/`. |
| `src/hardy/chat.py` | Machine-local state split to `.local/state.json`; transcript identity bound to the provider thread; `_migrate_layout` deleted; shared-library `LEAN_PATH` order. |
| `src/hardy/usage.py` | `provider_session` read and written from local state rather than the record. |
| `src/hardy/cli.py` | `--root`, `--project`, `--register-lakefile` wiring; project-relative CAS paths. |
| `src/hardy/cas_export.py` | Export references stored relative to the problem directory. |
| `src/hardy/tui/shell.py` | Input history under `.local/`; `/status` names the active project. |
| `scripts/`, `docs/INSTALL.md` | The config path each hard-codes. |

---

## Phase 1 — Paths (no behaviour change yet)

### Task 1: Slug validation and path derivation

**Files:**
- Create: `src/hardy/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class LayoutError(ValueError)`; `validate_slug(slug: str) -> str`; `@dataclass(frozen=True) class Layout` with fields `root: Path`, `slug: str` and properties `problem: Path`, `lean: Path`, `tex: Path`, `cas: Path`, `build: Path`, `local: Path`, `record: Path`, `transcript: Path`, `local_state: Path`, `hardy_dir: Path`, `shared_lean: Path`, `shared_build: Path`; `global_dir() -> Path`; `global_lean() -> Path`; `global_build() -> Path`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_layout.py
"""Where everything lives, and what a slug is allowed to be."""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy import layout


def test_a_plain_slug_is_accepted():
    assert layout.validate_slug("sylow") == "sylow"


@pytest.mark.parametrize(
    "bad",
    [
        "../other",          # escapes the root
        "/absolute",         # names somewhere else entirely
        "a/b",               # more than one component
        "a\\b",              # the same, spelled for Windows
        ".",                 # the root itself
        "..",                # the parent
        "",                  # nothing at all
        "   ",               # nothing at all, with whitespace
        ".hardy",            # collides with the tooling directory
        ".git",              # would put the record inside the repository's git directory
        ".anything",         # every dot-prefixed name, for the same reason
    ],
)
def test_a_slug_that_could_escape_or_collide_is_refused(bad: str):
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(bad)


def test_the_problem_directory_sits_directly_beneath_the_root(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.problem == tmp_path / "sylow"
    assert resolved.problem.parent == tmp_path


def test_every_path_hangs_off_the_problem_directory(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    problem = tmp_path / "sylow"
    assert resolved.lean == problem / "lean"
    assert resolved.tex == problem / "tex"
    assert resolved.cas == problem / "cas"
    assert resolved.build == problem / ".build"
    assert resolved.local == problem / ".local"
    assert resolved.record == problem / "session.json"
    assert resolved.transcript == problem / "transcript.jsonl"
    assert resolved.local_state == problem / ".local" / "state.json"


def test_the_tooling_directory_belongs_to_the_root_not_the_problem(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.hardy_dir == tmp_path / ".hardy"
    assert resolved.shared_lean == tmp_path / ".hardy" / "lean"
    assert resolved.shared_build == tmp_path / ".hardy" / ".build" / "lean"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.layout'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/hardy/layout.py
"""Where a project's parts live on disk, and nothing else.

One module owns every path decision so the shape is stated once. A slug reaches
here from a committed, hand-editable config file that travels with a clone, so
it is validated as untrusted input rather than trusted as a name someone typed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The tooling directory, which is Hardy's own and never a problem.
HARDY_DIR = ".hardy"
BUILD_DIR = ".build"
LOCAL_DIR = ".local"
RECORD = "session.json"
TRANSCRIPT = "transcript.jsonl"
LOCAL_STATE = "state.json"
DEFAULT_SLUG = "main"


class LayoutError(ValueError):
    """A slug that is not a single safe directory beneath the root."""


def validate_slug(slug: str) -> str:
    """The slug `slug` denotes, or a refusal.

    A single path component and nothing else. Anything that could reach outside
    the root -- a separator, a parent, an absolute path -- is refused here
    rather than at the filesystem, because the value arrives from a file a
    clone brings with it and Hardy writes the record through it.
    """
    text = str(slug).strip()
    if not text:
        raise LayoutError("a project slug may not be empty")
    if text in {".", ".."}:
        raise LayoutError(f"a project slug may not be {text!r}")
    if text.startswith("."):
        # Refuses `.hardy` and `.git` alike, and everything else beginning with
        # a dot. `.git` was accepted before this: a committed project config
        # naming it put `session.json` and `transcript.jsonl` inside the
        # repository's own git directory. It also settles a mismatch --
        # `existing_projects` skips dot-prefixed children, so such a project
        # was nameable but never discoverable.
        raise LayoutError(f"a project slug may not begin with a dot: {slug!r}")
    # Both separators, on every platform: a backslash is an ordinary character
    # on POSIX, so a value written on Windows must not become a one-component
    # name here that names two directories there.
    if "/" in text or "\\" in text or os.sep in text or (os.altsep and os.altsep in text):
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
    if Path(text).is_absolute() or Path(text).name != text:
        raise LayoutError(f"a project slug is one directory name, not a path: {slug!r}")
    return text


@dataclass(frozen=True)
class Layout:
    """Every path a single problem owns, derived from a root and a slug."""

    root: Path
    slug: str

    @property
    def problem(self) -> Path:
        return self.root / self.slug

    @property
    def lean(self) -> Path:
        return self.problem / "lean"

    @property
    def tex(self) -> Path:
        return self.problem / "tex"

    @property
    def cas(self) -> Path:
        return self.problem / "cas"

    @property
    def build(self) -> Path:
        return self.problem / BUILD_DIR

    @property
    def local(self) -> Path:
        return self.problem / LOCAL_DIR

    @property
    def record(self) -> Path:
        return self.problem / RECORD

    @property
    def transcript(self) -> Path:
        return self.problem / TRANSCRIPT

    @property
    def local_state(self) -> Path:
        return self.local / LOCAL_STATE

    @property
    def hardy_dir(self) -> Path:
        return self.root / HARDY_DIR

    @property
    def shared_lean(self) -> Path:
        return self.hardy_dir / "lean"

    @property
    def shared_build(self) -> Path:
        return self.hardy_dir / BUILD_DIR / "lean"


def global_dir() -> Path:
    """The user-level Hardy directory."""
    return Path.home() / HARDY_DIR


def global_lean() -> Path:
    return global_dir() / "lean"


def global_build() -> Path:
    return global_dir() / BUILD_DIR / "lean"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: PASS, 13 tests (one plain, nine refusals, three path assertions)

- [ ] **Step 5: Commit**

```bash
git add src/hardy/layout.py tests/test_layout.py
git commit -m "Name the parts of a project in one place, since a slug is untrusted"
```

---

### Task 2: Creating a project, and ignore rules that are true

**Files:**
- Modify: `src/hardy/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `Layout` from Task 1.
- Produces: `Layout.ensure() -> None` (creates directories and writes both `.gitignore` files, idempotently); module constants `PROBLEM_IGNORE: str` and `TOOLING_IGNORE: str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_layout.py`:

```python
def test_ensure_creates_the_trees_a_problem_needs(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    for directory in (resolved.problem, resolved.lean, resolved.tex, resolved.cas, resolved.local):
        assert directory.is_dir(), directory


def test_ensure_is_idempotent(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.lean / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    resolved.ensure()
    assert (resolved.lean / "Main.lean").read_text(encoding="utf-8") == "import Mathlib\n"


def test_the_ignore_rules_are_anchored_to_the_problem_root(tmp_path: Path):
    """Unanchored `.local/` would match at any depth.

    `git check-ignore` reports `lean/.local/draft` excluded under a bare
    `.local/` rule, so authored work containing such a directory would vanish
    from the versioned project -- the opposite of the point.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules
    assert "/.local/" in rules
    assert ".build/" not in rules
    assert ".local/" not in rules


def test_the_tooling_directory_ignores_only_its_build(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    rules = (resolved.hardy_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.build/" in rules


def test_ensure_does_not_overwrite_an_edited_ignore_file(tmp_path: Path):
    """The file is the user's once it exists; Hardy writes it, then leaves it."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    (resolved.problem / ".gitignore").write_text("/.build/\n/.local/\nnotes.txt\n", encoding="utf-8")
    resolved.ensure()
    assert "notes.txt" in (resolved.problem / ".gitignore").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_layout.py -k ensure -v`
Expected: FAIL with `AttributeError: 'Layout' object has no attribute 'ensure'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/hardy/layout.py`, after the `Layout` properties:

```python
# Anchored, and deliberately so. A bare `.build/` matches a directory of that
# name at any depth, so a CAS script or an authored subtree that legitimately
# created `cas/.build/` would be silently excluded from the versioned project.
# The leading slash confines each rule to the directory the file sits in.
PROBLEM_IGNORE = (
    "# Written by Hardy. Everything here is recomputable from the sources\n"
    "# beside it, or belongs to this machine and this account.\n"
    "/.build/\n"
    "/.local/\n"
)
TOOLING_IGNORE = (
    "# Written by Hardy. The oleans for this project's shared Lean library,\n"
    "# rebuilt on demand and never committed.\n"
    "/.build/\n"
)
```

And the method, inside `Layout`:

```python
    def ensure(self) -> None:
        """Make the directories exist and say what is not to be committed.

        Idempotent: a second call must not disturb a tree that already holds
        work, and must not rewrite an ignore file a user has since edited --
        Hardy writes it once to state the rule, and it is theirs afterwards.
        """
        for directory in (self.problem, self.lean, self.tex, self.cas, self.local, self.hardy_dir):
            directory.mkdir(parents=True, exist_ok=True)
        _write_once(self.problem / ".gitignore", PROBLEM_IGNORE)
        _write_once(self.hardy_dir / ".gitignore", TOOLING_IGNORE)
```

And the helper, at module level:

```python
def _write_once(path: Path, text: str) -> None:
    """Write `text` to `path` only if nothing is there yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 5: Prove the anchoring claim against real git**

Add to `tests/test_layout.py`:

```python
import shutil
import subprocess


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_itself_agrees_the_rules_are_anchored(tmp_path: Path):
    """Asserting against git, not against our reading of gitignore syntax."""
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    subprocess.run(["git", "init", "-q"], cwd=resolved.problem, check=True)
    (resolved.lean / ".local").mkdir()
    (resolved.lean / ".local" / "draft").write_text("x", encoding="utf-8")
    (resolved.build).mkdir(exist_ok=True)
    (resolved.build / "olean").write_text("x", encoding="utf-8")

    def ignored(relative: str) -> bool:
        done = subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=resolved.problem
        )
        return done.returncode == 0

    assert ignored(".build/olean"), "the problem's own build must be excluded"
    assert not ignored("lean/.local/draft"), "authored work must not be excluded"
```

- [ ] **Step 6: Run it**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/layout.py tests/test_layout.py
git commit -m "Create a project's trees, and ignore only what is genuinely disposable"
```

---

### Task 3: The global directory, and moving the config file into it

**Files:**
- Modify: `src/hardy/config.py:60-75` (`default_config_path`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `layout.global_dir()` from Task 1.
- Produces: `config.default_config_path() -> Path` now returns `~/.hardy/config.toml`; `config.legacy_config_path() -> Path` returns the XDG/APPDATA location; `config.migrate_global(source: Path | None = None, destination: Path | None = None) -> bool` returns True when it moved a file.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_the_default_config_lives_in_the_global_hardy_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert config.default_config_path() == tmp_path / ".hardy" / "config.toml"


def test_the_environment_override_still_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HARDY_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config.default_config_path() == tmp_path / "elsewhere.toml"


def test_a_legacy_config_moves_into_the_global_directory(tmp_path: Path):
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "claude-opus-5"\nlean_timeout = 90\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    assert config.migrate_global(legacy, destination) is True

    assert not legacy.exists()
    moved = destination.read_text(encoding="utf-8")
    assert 'model = "claude-opus-5"' in moved
    assert "lean_timeout = 90" in moved


def test_the_move_drops_the_setting_that_no_longer_exists(tmp_path: Path):
    """`read_file` raises on an unknown key, so a verbatim copy would not start.

    Every installer-written config carries `workspace`, and that setting is
    being removed. Copying the file unchanged would leave Hardy refusing to
    load its own migrated configuration.
    """
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "x"\nworkspace = ".hardy"\nlean_timeout = 90\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    config.migrate_global(legacy, destination)

    moved = destination.read_text(encoding="utf-8")
    assert "workspace" not in moved
    assert 'model = "x"' in moved
    assert "lean_timeout = 90" in moved
    # The proof that matters: the migrated file loads.
    assert config.read_file(destination)["model"] == "x"


def test_the_move_does_not_clobber_a_config_that_already_exists(tmp_path: Path):
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "old"\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"
    destination.parent.mkdir(parents=True)
    destination.write_text('model = "current"\n', encoding="utf-8")

    assert config.migrate_global(legacy, destination) is False
    assert 'model = "current"' in destination.read_text(encoding="utf-8")


def test_nothing_to_move_is_not_an_error(tmp_path: Path):
    assert config.migrate_global(tmp_path / "absent.toml", tmp_path / "new.toml") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_config.py -k "global or legacy or move" -v`
Expected: FAIL — `AttributeError: module 'hardy.config' has no attribute 'migrate_global'`, and the default-path test asserting `.config/hardy` where `.hardy` is expected.

- [ ] **Step 3: Write minimal implementation**

Replace `default_config_path` in `src/hardy/config.py` and add the two new functions beside it:

```python
def default_config_path() -> Path:
    """The global config file Hardy reads when no path is given.

    `~/.hardy/config.toml` on every platform. One directory holds the user's
    Hardy settings, skills, prompts and shared Lean, so there is one place to
    look rather than a different one per operating system.
    """
    override = os.environ.get("HARDY_CONFIG")
    if override:
        return Path(override).expanduser()
    return layout.global_dir() / "config.toml"


def legacy_config_path() -> Path:
    """Where the config used to live, before `~/.hardy/` existed."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "hardy" / "config.toml"
    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(home) / "hardy" / "config.toml"


#: Settings that existed once and do not any more. A migrated file carrying one
#: would be rejected by `read_file`, so the move drops them.
RETIRED_SETTINGS = ("workspace", "runs_root")


def migrate_global(source: Path | None = None, destination: Path | None = None) -> bool:
    """Move a pre-`~/.hardy/` config into place, dropping retired settings.

    A translation rather than a copy. `read_file` refuses any key outside
    `SETTINGS`, and every installer-written config carries `workspace`, which
    this change removes -- so relocating the file verbatim would leave Hardy
    unable to load its own configuration.

    Returns whether anything moved. An absent source and an existing
    destination are both ordinary: the destination is the newer file and is
    never overwritten.
    """
    source = source or legacy_config_path()
    destination = destination or (layout.global_dir() / "config.toml")
    if not source.is_file() or destination.exists():
        return False
    retired = re.compile(rf"^\s*(?:{'|'.join(RETIRED_SETTINGS)})\s*=")
    kept = [
        line
        for line in source.read_text(encoding="utf-8-sig").splitlines()
        if not retired.match(line)
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    source.unlink()
    return True
```

Add the import at the top of `src/hardy/config.py`, after the existing `from .domain import RunLimits`:

```python
from . import layout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/test_config.py -v`
Expected: PASS. If `test_defaults` at `tests/test_config.py:28` fails on `settings.workspace`, leave it failing — Task 4 removes that setting and updates the test.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/config.py tests/test_config.py
git commit -m "Give the user one Hardy directory, and translate the old config into it"
```

---

### Task 4: Two configuration layers, and the end of `--workspace`

**Files:**
- Modify: `src/hardy/config.py` (`SETTINGS`, `Config`, `load`)
- Modify: `src/hardy/cli.py:64-75` (`_config`), `src/hardy/cli.py:773`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `layout.Layout`, `layout.validate_slug`, `layout.DEFAULT_SLUG`, `config.default_config_path` from Tasks 1 and 3.
- Produces: `Config.root: Path` and `Config.project: str` replace `Config.workspace`; `Config.layout -> layout.Layout`; `load(path=None, *, root=None, project=None, interactive=False, **overrides) -> Config`. `SETTINGS` gains `"project": "HARDY_PROJECT"` and `"root": "HARDY_ROOT"` and loses `"workspace"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_the_workspace_setting_is_gone(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('workspace = ".hardy"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown settings"):
        config.read_file(path)


def test_the_project_config_names_the_active_problem(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    settings = config.load(tmp_path / "absent-global.toml", root=tmp_path)
    assert settings.project == "sylow"
    assert settings.layout.problem == tmp_path / "sylow"


def test_a_custom_global_config_does_not_suppress_the_project_config(tmp_path: Path):
    """`HARDY_CONFIG` selects the global layer only.

    Letting it win over everything would mean a wrapper pointing it at its own
    settings file silently opened -- and wrote -- the wrong problem's record.
    """
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    custom = tmp_path / "custom-global.toml"
    custom.write_text('model = "claude-opus-5"\n', encoding="utf-8")

    settings = config.load(custom, root=tmp_path)

    assert settings.model == "claude-opus-5"
    assert settings.project == "sylow"


def test_the_flag_beats_the_project_config(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path, project="galois")
    assert settings.project == "galois"


def test_the_only_project_present_is_the_default(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / "galois").mkdir()
    (tmp_path / "galois" / "session.json").write_text("{}", encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "galois"


def test_an_empty_root_falls_back_to_main_without_reading_stdin(tmp_path: Path):
    """Non-interactive selection must be deterministic.

    Prompting here would hang `hardy batch` and CI, fail at EOF, or consume the
    first piped message as a slug.
    """
    settings = config.load(tmp_path / "absent.toml", root=tmp_path, interactive=False)
    assert settings.project == "main"


def test_two_projects_and_no_active_setting_falls_back_to_main(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    for slug in ("galois", "sylow"):
        (tmp_path / slug).mkdir()
        (tmp_path / slug / "session.json").write_text("{}", encoding="utf-8")
    settings = config.load(tmp_path / "absent.toml", root=tmp_path, interactive=False)
    assert settings.project == "main"


def test_a_project_config_cannot_name_an_executable(tmp_path: Path):
    """The project layer selects a problem; it does not choose programs.

    `.hardy/config.toml` is committed and arrives with any clone, and `_chat`
    builds the CAS runtime -- which runs the configured executable to probe its
    version -- before the prompt appears. An unrestricted merge would let a
    repository run an arbitrary program the moment someone starts Hardy in it.
    """
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text(
        'project = "sylow"\ncas_command = "/tmp/evil"\nlean_command = "/tmp/evil"\n',
        encoding="utf-8",
    )
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.project == "sylow"
    assert settings.cas_command is None
    assert "/tmp/evil" not in " ".join(settings.lean_command)


def test_the_environment_names_the_root_before_the_project_config_is_found(tmp_path: Path, monkeypatch):
    """Otherwise HARDY_ROOT is advertised and inert.

    The project layer lives inside the root, so a root resolved after the
    environment is read would send Hardy to the current directory for its
    project config and open the wrong problem there.
    """
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".hardy").mkdir(parents=True)
    (elsewhere / ".hardy" / "config.toml").write_text('project = "sylow"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HARDY_ROOT", str(elsewhere))

    settings = config.load(tmp_path / "absent.toml")

    assert settings.root == elsewhere
    assert settings.project == "sylow"


def test_runs_root_survives(tmp_path: Path):
    """Staged runs are out of scope; `prove` and `accept` still read this."""
    settings = config.load(tmp_path / "absent.toml", root=tmp_path)
    assert settings.runs_root == Path("runs")


def test_a_slug_that_escapes_the_root_is_refused(tmp_path: Path):
    (tmp_path / ".hardy").mkdir()
    (tmp_path / ".hardy" / "config.toml").write_text('project = "../other"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="one directory name"):
        config.load(tmp_path / "absent.toml", root=tmp_path)
```

Update the existing default assertion at `tests/test_config.py:28`:

```python
    assert settings.project == "main"
    assert settings.root == Path.cwd()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_config.py -v`
Expected: FAIL — `TypeError: load() got an unexpected keyword argument 'root'`

- [ ] **Step 3: Write minimal implementation**

In `src/hardy/config.py`, remove `DEFAULT_WORKSPACE` **but keep `DEFAULT_RUNS_ROOT`** — `runs_root` is out of scope per the spec and is live in `workflow.py:132`, `acceptance.py:254,275`, and `cli.py:536,588`; removing it breaks `prove` and `accept`. Also correct Task 3's `RETIRED_SETTINGS` to `("workspace",)` — dropping `runs_root` during migration would silently discard a live user setting. Change `SETTINGS`:

```python
SETTINGS = {
    "model": "HARDY_MODEL",
    "lean_command": "HARDY_LEAN_COMMAND",
    "lean_project": "HARDY_LEAN_PROJECT",
    "lean_timeout": "HARDY_LEAN_TIMEOUT",
    "latex_command": "HARDY_LATEX_COMMAND",
    "root": "HARDY_ROOT",
    "project": "HARDY_PROJECT",
    "runs_root": "HARDY_RUNS_ROOT",
    "lake": "HARDY_LAKE",
    "elan": "HARDY_ELAN",
    "tectonic": "HARDY_TECTONIC",
    "tectonic_bundle": "HARDY_TECTONIC_BUNDLE",
    "tectonic_bundle_sha256": "HARDY_TECTONIC_BUNDLE_SHA256",
    "cas_backend": "HARDY_CAS_BACKEND",
    "cas_command": "HARDY_CAS_COMMAND",
}

# What a project's own committed config may say. Deliberately tiny: the file
# travels with a clone, and Hardy runs the configured CAS executable before the
# prompt appears. A repository gets to say which problem is active. It does not
# get to say which programs run.
PROJECT_SETTINGS = frozenset({"project"})
```

In the `Config` dataclass, replace only the `workspace: Path` field (keep `runs_root` exactly as it is):

```python
    root: Path
    project: str
```

and add the property:

```python
    @property
    def layout(self) -> layout.Layout:
        """Where this configuration says the active problem's parts live."""
        return layout.Layout(root=self.root, slug=self.project)
```

Add the project-discovery helper at module level:

```python
def existing_projects(root: Path) -> list[str]:
    """The slugs under `root` that already hold a record, sorted.

    A directory counts as a project when Hardy has written its record there.
    An empty directory a user happened to create is not one, and neither is
    `.hardy/`, which `validate_slug` refuses anyway.
    """
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / layout.RECORD).is_file():
            found.append(child.name)
    return found


def active_project(root: Path, stated: str | None, project_values: dict[str, Any]) -> str:
    """Which problem this run opens.

    Deterministic, and it never reads stdin: prompting on a piped launch would
    hang, fail at EOF, or take the first chat message for a slug. The caller
    that has a TTY may ask first and pass the answer in as `stated`.
    """
    for candidate in (stated, project_values.get("project")):
        if candidate:
            return layout.validate_slug(str(candidate))
    present = existing_projects(root)
    if len(present) == 1:
        return present[0]
    return layout.DEFAULT_SLUG
```

Rewrite `load`:

```python
def load(
    path: Path | None = None,
    *,
    root: Path | None = None,
    project: str | None = None,
    interactive: bool = False,
    **overrides: Any,
) -> Config:
    """Resolve configuration from both layers, the environment, and CLI flags.

    Two layers, not one. The global file holds settings that belong to the
    user; the project file, at `<root>/.hardy/config.toml`, holds settings that
    belong to this checkout -- above all which problem is active. `HARDY_CONFIG`
    selects the global file *only*: letting it win over everything, as it did
    when there was one file, would mean a wrapper pointing it elsewhere
    silently opened and wrote the wrong problem's record.

    Precedence: global file, then project file, then environment, then flags.
    """
    # The migration runs before the default path is read, and only when the
    # caller named no file of its own: an explicit --config or HARDY_CONFIG is
    # a deliberate choice about which file to use, not an upgrade to perform.
    # Without this the relocation would be implemented, unit-tested, and never
    # reached -- an upgrading user's model and toolchain settings would be
    # silently ignored.
    if path is None and not os.environ.get("HARDY_CONFIG"):
        migrate_global()
    path = path or default_config_path()
    values: dict[str, Any] = read_file(path)

    # The root is resolved before the project layer is located, because the
    # project layer lives inside it. Reading the environment afterwards would
    # make HARDY_ROOT advertised and inert: Hardy would take the project config
    # from the current directory and open the wrong problem there.
    def _root_from(source: dict[str, Any]) -> Path | None:
        value = source.get("root")
        return Path(str(value)).expanduser() if value else None

    resolved_root = (
        (Path(root).expanduser() if root else None)
        or (Path(os.environ["HARDY_ROOT"]).expanduser() if os.environ.get("HARDY_ROOT") else None)
        or _root_from(values)
        or Path.cwd()
    )

    # Only PROJECT_SETTINGS are honoured from the project layer. That file is
    # committed and arrives with any clone, and `_chat` builds the CAS runtime
    # -- which calls `probe_version()` on the configured executable
    # (`cas_tools.py:108`) -- before the prompt appears. An unrestricted merge
    # would therefore let a repository run an arbitrary program the moment
    # someone starts Hardy inside it. Selecting the active problem is what this
    # layer is for; naming executables is not.
    project_values = {
        key: value
        for key, value in read_file(resolved_root / layout.HARDY_DIR / "config.toml").items()
        if key in PROJECT_SETTINGS
    }
    values.update(project_values)
    for key, variable in SETTINGS.items():
        value = os.environ.get(variable)
        if value:
            values[key] = value
    for key, value in overrides.items():
        if value is not None:
            values[key] = value

    def text(key: str, default: str) -> str:
        return str(values.get(key) or default)

    def location(key: str) -> Path | None:
        value = values.get(key)
        return Path(str(value)).expanduser() if value else None

    try:
        lean_timeout = float(values.get("lean_timeout", DEFAULT_LEAN_TIMEOUT))
    except (TypeError, ValueError):
        raise ValueError(f"lean_timeout must be a number of seconds, not {values['lean_timeout']!r}") from None

    cas_backend = text("cas_backend", DEFAULT_CAS_BACKEND)
    if cas_backend not in CAS_BACKENDS:
        raise ValueError(f"cas_backend must be one of {list(CAS_BACKENDS)}, not {cas_backend!r}")

    return Config(
        model=str(values["model"]) if values.get("model") else DEFAULT_MODEL,
        lean_command=tuple(shlex.split(text("lean_command", DEFAULT_LEAN_COMMAND))),
        lean_project=location("lean_project"),
        lean_timeout=lean_timeout,
        latex_command=tuple(shlex.split(text("latex_command", DEFAULT_LATEX_COMMAND))),
        root=resolved_root,
        project=active_project(resolved_root, project, values),
        runs_root=location("runs_root") or Path(DEFAULT_RUNS_ROOT),
        lake=location("lake") or Path(DEFAULT_LAKE),
        elan=location("elan") or Path(DEFAULT_ELAN),
        tectonic=location("tectonic") or Path(DEFAULT_TECTONIC),
        tectonic_bundle=text("tectonic_bundle", DEFAULT_TECTONIC_BUNDLE),
        tectonic_bundle_sha256=text("tectonic_bundle_sha256", DEFAULT_TECTONIC_BUNDLE_SHA256),
        cas_backend=cas_backend,
        cas_command=location("cas_command"),
        path=path if path.exists() else None,
        requested_path=path,
    )
```

In `src/hardy/cli.py`, change `_config` (line 64) to pass the new arguments:

```python
def _config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> configuration.Config:
    try:
        return configuration.load(
            args.config,
            root=getattr(args, "root", None),
            project=getattr(args, "project", None),
            model=args.model,
            lean_command=args.lean_command,
            lean_project=args.lean_project,
            latex_command=args.latex_command,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
```

And replace the `--workspace` argument (line 773) with:

```python
    chat.add_argument("--root", type=Path, help="project root (default: the current directory)")
    chat.add_argument("--project", help=f"which problem to open (default: the active one, or {layout.DEFAULT_SLUG})")
```

Add `from . import layout` to `src/hardy/cli.py`'s imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Fix the callers the removed setting broke**

Run: `uv run --extra test pytest -q 2>&1 | tail -30`

Every remaining failure is a reference to `config.workspace`. Replace each with `config.layout.problem` (or the specific sub-path — `config.layout.cas` at `cli.py:100-101` and `cli.py:171`). Do not add a compatibility property; the setting is gone.

- [ ] **Step 6: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Read settings in two layers, so a custom global file cannot pick the problem"
```

---

## Phase 2 — The record and what leaves it

### Task 5: Machine-local state moves out of the record

**Files:**
- Modify: `src/hardy/chat.py:180-251` (`__init__`), `:353-363` (`_read_state`, `_save_state`), `:1446-1450` (`_remember_thread`), `:1477-1548` (ledger)
- Modify: `src/hardy/usage.py:340-365`
- Test: `tests/test_chat_usage.py`

**Interfaces:**
- Consumes: `layout.Layout` from Task 1.
- Produces: `MathematicsSession.local_path: Path`; `MathematicsSession.local: dict[str, Any]`; `MathematicsSession._save_local() -> None`. `WITHHELD` shrinks to `("audit",)` because the other three keys are no longer in the record at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_usage.py`:

```python
def test_the_record_carries_no_machine_local_state(tmp_path: Path):
    """The record is versioned; a provider thread and a spend ledger are not.

    They belong to this machine and this account, which is why `WITHHELD`
    already kept all three out of the model's sight.
    """
    runtime = FakeChatRuntime([{"role": "assistant", "content": "done"}])
    chat = session(tmp_path, runtime)
    chat.send("hello")

    record = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert "provider_session" not in record
    assert "usage" not in record
    assert "usage_cursor" not in record

    local = json.loads((tmp_path / ".local" / "state.json").read_text(encoding="utf-8"))
    assert "usage" in local


def test_the_spend_total_still_continues_across_a_reopen(tmp_path: Path):
    runtime = FakeChatRuntime([{"role": "assistant", "content": "one"}])
    first = session(tmp_path, runtime)
    first.send("hello")
    spent = first.usage.turns

    reopened = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "two"}]))
    assert reopened.usage.turns == spent


def test_the_record_still_carries_the_things_that_are_evidence(tmp_path: Path):
    runtime = FakeChatRuntime([{"role": "assistant", "content": "done"}])
    chat = session(tmp_path, runtime)
    chat.send("hello")
    record = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert "names" in record
    assert "assumptions" in record
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_usage.py -k "machine_local or schema" -v`
Expected: FAIL — `assert 'provider_session' not in record` fails, and `.local/state.json` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `src/hardy/chat.py`, replace the `WITHHELD` block (lines 63-66) with:

```python
# The manifest key that exists for Hardy and not for the model. The listing
# reports each verdict checked against the tree in front of it, and handing back
# the stored one as well would put two answers for the same module in one
# response. The ledger and the provider thread are no longer withheld here
# because they are no longer in the record: they live in `.local/state.json`.
WITHHELD = ("audit",)
USAGE_KEY = "usage"
CURSOR_KEY = "usage_cursor"
THREAD_KEY = "provider_session"
```

In `__init__`, after `self.transcript_path = ...`, add:

```python
        # Machine-local state, beside the record but never part of it. The
        # record is versioned and describes the mathematics; the provider
        # thread and the spend ledger describe this machine and this account,
        # and a clone of the project must not inherit either.
        self.local_path = workspace / LOCAL_DIR / LOCAL_STATE
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
```

and change the state-reading line from `self.state = self._read_state()` to:

```python
        self.state = self._read_state()
        self.local = self._read_local()
```

Replace `_read_state` and add its counterpart:

```python
    def _read_state(self) -> dict[str, Any]:
        """The record, refusing a schema this version does not read.

        There is deliberately no reader for version 1. Accepting one anyway
        would carry its `provider_session`, `usage` and `usage_cursor` into a
        record that is now versioned -- and, since `WITHHELD` no longer names
        those keys, into the model's context as well. Refusing is the honest
        failure.
        """
        if self.state_path.exists():
            stored = json.loads(self.state_path.read_text(encoding="utf-8"))
            version = stored.get("schema_version")
            if version != 2:
                raise ValueError(
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
            loaded = json.loads(self.local_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # UnicodeDecodeError is in the list deliberately: it is not a
            # subclass of the others, and without it a file of invalid bytes
            # would refuse to open the project rather than being treated as the
            # disposable state this docstring promises it is.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _save_local(self) -> None:
        """The one door `.local/state.json` is written through, from any thread."""
        with self._writes:
            _atomic_json(self.local_path, self.local)
```

Then, throughout `chat.py`, move exactly three keys from `self.state` to `self.local`:

- `_remember_thread`: `self.state.get(THREAD_KEY)` → `self.local.get(THREAD_KEY)`; `self.state[THREAD_KEY] = thread` → `self.local[THREAD_KEY] = thread`; `self._save_state()` → `self._save_local()`.
- `__init__`'s runtime build: `session_id=self.state.get("provider_session")` → `session_id=self.local.get(THREAD_KEY)`.
- `_sync_provenance`'s runtime rebuild: the same substitution.
- **`switch_model` (`chat.py:320`)**: `session_id=self.state.get("provider_session")` is a THIRD site and is easy to miss. Its docstring promises "the provider thread is carried over, so the new model inherits the conversation" — left unchanged, every `/model` switch would silently start a new thread and break that promise.
- `_recover_spend`, `_ledger_cursor`, `_mark_ledger_read`, `_remember_spend`: every `self.state[USAGE_KEY]`, `self.state.get(USAGE_KEY)`, `self.state[CURSOR_KEY]`, `self.state.get(CURSOR_KEY)` becomes the `self.local` equivalent, and every `self._save_state()` in those methods becomes `self._save_local()`.

Add the import to `chat.py`: `from .layout import LOCAL_DIR, LOCAL_STATE`.

In `src/hardy/usage.py`, `Usage.from_dict` already reads `provider_session` out of whatever mapping it is handed, so it needs no change — only its caller does. Verify with `grep -n "provider_session" src/hardy/usage.py` that every use is on a passed-in mapping.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_usage.py tests/test_chat.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Keep this machine's ledger out of a record that is about to be versioned"
```

---

### Task 6: The provider thread dies with a transcript it cannot account for

**Files:**
- Modify: `src/hardy/chat.py` (`_remember_thread`, and a new `_transcript_identity`)
- Test: `tests/test_chat_usage.py`

**Interfaces:**
- Consumes: `MathematicsSession.local` from Task 5.
- Produces: `MathematicsSession._transcript_identity(length: int | None = None) -> dict[str, Any]`; `MathematicsSession._carried_thread() -> str | None`. Local state gains `transcript_length: int` and `transcript_digest: str`, written with `provider_session`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_usage.py`:

```python
import hashlib


def test_a_shortened_transcript_clears_the_provider_thread(tmp_path: Path):
    """A checkout that rewinds the record must not leave a resumable thread.

    `.local/` is gitignored, so it survives the checkout that rewinds the
    versioned transcript. Resuming the thread would answer from turns the
    record does not contain.
    """
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "one"}]))
    chat.send("hello")
    assert chat.local.get("provider_session")

    transcript = tmp_path / "transcript.jsonl"
    kept = transcript.read_text(encoding="utf-8").splitlines()[:1]
    transcript.write_text("\n".join(kept) + "\n", encoding="utf-8")

    reopened = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "two"}]))
    assert reopened._carried_thread() is None


def test_a_divergent_transcript_of_the_same_length_clears_it_too(tmp_path: Path):
    """The size test alone passes this case, which is why it is not the whole test.

    A different branch, not a truncation: the file is the same length and the
    cursor stays arithmetically valid against a history that never produced it.
    """
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "one"}]))
    chat.send("hello")

    transcript = tmp_path / "transcript.jsonl"
    original = transcript.read_bytes()
    # Same length, different content: flip one byte deep in the file.
    diverged = bytearray(original)
    diverged[len(diverged) // 2] ^= 0x20
    transcript.write_bytes(bytes(diverged))
    assert len(transcript.read_bytes()) == len(original)

    reopened = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "two"}]))
    assert reopened._carried_thread() is None


def test_an_untouched_transcript_keeps_the_thread(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "one"}]))
    chat.send("hello")
    thread = chat.local["provider_session"]

    reopened = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "two"}]))
    assert reopened._carried_thread() == thread


def test_the_identity_is_recorded_with_the_thread_not_with_the_cursor(tmp_path: Path):
    """The two spans differ, and the gap is real.

    `_observed` appends the result and remembers the thread *before* the spend
    fold advances the cursor, so a crash in that window leaves a thread whose
    last turn sits beyond the ledger's prefix.
    """
    chat = session(tmp_path, FakeChatRuntime([{"role": "assistant", "content": "one"}]))
    chat.send("hello")
    length = chat.local["transcript_length"]
    digest = chat.local["transcript_digest"]

    seen = (tmp_path / "transcript.jsonl").read_bytes()[:length]
    assert hashlib.sha256(seen).hexdigest() == digest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_usage.py -k "transcript or thread" -v`
Expected: FAIL — `AttributeError: 'MathematicsSession' object has no attribute '_carried_thread'`

- [ ] **Step 3: Write minimal implementation**

Replace `_remember_thread` in `src/hardy/chat.py` and add the two helpers beside it:

```python
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
            with self.transcript_path.open("rb") as handle:
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
```

Change the two places that resume a thread to go through the check. In `__init__`:

```python
        self.runtime = self._build(session_id=self._carried_thread())
```

and in `_sync_provenance`'s rebuild, the same substitution.

Add `import hashlib` to the imports at the top of `chat.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_usage.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Drop a provider thread the transcript on disk can no longer account for"
```

---

### Task 7: Delete the migration that no longer has anything to migrate

**Files:**
- Modify: `src/hardy/chat.py:553-570` (`_migrate_layout` and its call site)
- Modify: `tests/test_chat_workspace.py` (delete the migration test)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `MathematicsSession.__init__` no longer calls `_migrate_layout`; `DEFAULT_LEAN_PATH` and `DEFAULT_TEX_PATH` remain, because they are still the default tool arguments.

- [ ] **Step 1: Find what refers to it**

Run: `grep -rn "_migrate_layout\|migration" src/ tests/`
Expected: the definition, the call in `__init__`, and one test.

- [ ] **Step 2: Delete the method and its call**

Remove the `_migrate_layout` method from `src/hardy/chat.py` entirely, and remove the `self._migrate_layout()` line from `__init__`. Update the comment above `LEAN_DIR` (line 40) which explains the trees in terms of the migration:

```python
# Where the two artifact trees live inside a problem directory. Both are
# directories: a development outgrows one file, and so does the document
# about it.
LEAN_DIR = "lean"
```

- [ ] **Step 3: Delete the test**

Remove the migration test from `tests/test_chat_workspace.py` (the one asserting a top-level `Main.lean` is moved into `lean/`).

- [ ] **Step 4: Run the suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Delete a migration whose only input never reached anyone"
```

---

### Task 8: CAS references stored relative to the problem

**Files:**
- Modify: `src/hardy/chat.py:1222-1230`
- Modify: `src/hardy/cli.py:171`
- Test: `tests/test_chat_files.py`

**Interfaces:**
- Consumes: `MathematicsSession.workspace`.
- Produces: `session.json`'s `cas_export` entry holds `script` and `notebook` as POSIX-style paths relative to the problem directory.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_files.py`:

```python
def test_cas_export_references_are_relative_to_the_problem(tmp_path: Path):
    """An absolute root would otherwise put a source-machine path in the record.

    Once the record is versioned, an absolute path is stale the moment the
    project is cloned or moved.
    """
    absolute = (tmp_path / "root" / "sylow").resolve()
    absolute.mkdir(parents=True)
    runtime = FakeChatRuntime([call("cas_export", {}, "export"), {"role": "assistant", "content": "exported"}])
    chat = session(absolute, runtime, cas=fake_cas_runtime())
    chat.send("export the session")

    record = json.loads((absolute / "session.json").read_text(encoding="utf-8"))
    for reference in (record["cas_export"]["script"], record["cas_export"]["notebook"]):
        assert not Path(reference).is_absolute(), reference
        assert reference.startswith("cas/"), reference
        assert (absolute / reference).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_files.py -k cas_export_references -v`
Expected: FAIL — the stored reference is an absolute path.

- [ ] **Step 3: Write minimal implementation**

In `src/hardy/chat.py`, replace the `cas_export` branch:

```python
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
```

and add the helper:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_files.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Store an export reference the record can still resolve after a clone"
```

---

## Phase 3 — Shared libraries and the host project

### Task 9: Shared Lean libraries on LEAN_PATH, with shadowing reported

**Files:**
- Modify: `src/hardy/chat.py` (`_run_lean_source`, `_lean_search_path`, `__init__`)
- Test: `tests/test_workspace_build.py`

**Interfaces:**
- Consumes: `layout.Layout.shared_lean`, `layout.Layout.shared_build`, `layout.global_lean`, `layout.global_build` from Task 1.
- Produces: `MathematicsSession.shared_roots: tuple[tuple[Path, Path], ...]` (source, build) in resolution order; `MathematicsSession.shadowed_modules() -> dict[str, Path]`; `MathematicsSession.build_shared() -> None`; `MathematicsSession._shared_digest() -> str`.

**Three requirements this task must meet that an earlier draft of it missed. Read these before writing code:**

1. **A path on `LEAN_PATH` is not a library.** Adding `<root>/.hardy/.build/lean` to the variable does nothing unless something compiles `.hardy/lean/*.lean` into it. `build_shared()` does that, on the same `compile_module` path the problem's own tree uses, and it runs before the first Lean invocation. Without it the advertised import simply fails.
2. **The test must import, not match a string.** Asserting `str(path) in chat._lean_path()` passes against a completely non-functional implementation. The test must save a problem module that actually does `import CommAlg` and assert Lean accepted it.
3. **The shared sources must reach the build and audit identities.** The spec requires their digests be stamped into the record "so a verdict names what it was computed against". `_shared_digest()` returns a digest over the shared sources, and it is mixed into `self._environment` — which already keys the olean cache and stamps each audit verdict. Without it, editing a shared library leaves both a stale olean and a stale verdict reading as current, which is exactly the class of failure the audit exists to prevent.
4. **Shadowing must be reported to someone.** A `shadowed_modules()` that only its unit test ever calls satisfies nothing. Surface it in the `read_workspace` tool result, so the model sees it, and record a transcript event when a collision is first observed, so the record does.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace_build.py`:

```python
def test_a_shared_library_module_is_importable(tmp_path: Path):
    root = tmp_path / "root"
    problem = root / "sylow"
    shared = root / ".hardy" / "lean"
    shared.mkdir(parents=True)
    (shared / "CommAlg.lean").write_text("import Mathlib\n\ntheorem shared : True := trivial\n", encoding="utf-8")
    problem.mkdir(parents=True)

    chat = session(problem, FakeChatRuntime([{"role": "assistant", "content": "ok"}]), root=root)

    assert str(chat.lean_workspace.build) in chat._lean_path()
    assert str(root / ".hardy" / ".build" / "lean") in chat._lean_path()


def test_the_problem_wins_a_name_collision_and_the_shadowing_is_reported(tmp_path: Path):
    """A shadowed shared module is reported, never silently preferred.

    Two files answering to one module name is a fact the session must be able
    to state, because which one a proof rests on is not a detail.
    """
    root = tmp_path / "root"
    problem = root / "sylow"
    shared = root / ".hardy" / "lean"
    shared.mkdir(parents=True)
    (shared / "CommAlg.lean").write_text("import Mathlib\n", encoding="utf-8")
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "CommAlg.lean").write_text("import Mathlib\n", encoding="utf-8")

    chat = session(problem, FakeChatRuntime([{"role": "assistant", "content": "ok"}]), root=root)

    shadowed = chat.shadowed_modules()
    assert "CommAlg" in shadowed
    assert shadowed["CommAlg"] == shared / "CommAlg.lean"


def test_nothing_is_reported_when_no_name_collides(tmp_path: Path):
    root = tmp_path / "root"
    problem = root / "sylow"
    shared = root / ".hardy" / "lean"
    shared.mkdir(parents=True)
    (shared / "CommAlg.lean").write_text("import Mathlib\n", encoding="utf-8")
    (problem / "lean").mkdir(parents=True)
    (problem / "lean" / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")

    chat = session(problem, FakeChatRuntime([{"role": "assistant", "content": "ok"}]), root=root)

    assert chat.shadowed_modules() == {}
```

Extend the `session` helper in that test module to accept and pass `root=`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_workspace_build.py -k "shared or shadow" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'root'`

- [ ] **Step 3: Write minimal implementation**

Give `MathematicsSession.__init__` a `root: Path | None = None` keyword, defaulting to `workspace.parent`, and after the `lean_workspace` is built add:

```python
        # The libraries a problem may import but did not author, in resolution
        # order: the project's own, then the user's. The problem's build comes
        # first on LEAN_PATH, so its modules win a name collision -- and
        # `shadowed_modules` makes that collision reportable rather than silent.
        self.shared_roots = tuple(
            (source, build)
            for source, build in (
                (root / HARDY_DIR / "lean", root / HARDY_DIR / BUILD_DIR / "lean"),
                (global_lean(), global_build()),
            )
            if source.is_dir()
        )
```

Add the two methods:

```python
    def _lean_path(self) -> str:
        """Where Lean looks for a module, nearest first.

        The problem's own build, then the project's shared library, then the
        user's, then whatever Mathlib's environment already provides. `lake env`
        augments an inherited LEAN_PATH rather than replacing it, which is what
        lets these sit beside Mathlib's package directories.
        """
        entries = [self.lean_workspace.lean_path(), *(str(build) for _, build in self.shared_roots)]
        return os.pathsep.join(entries)

    def shadowed_modules(self) -> dict[str, Path]:
        """Shared modules a problem module answers to instead, by name.

        Resolution order already decides which one Lean loads. This says so out
        loud: which file a theorem rests on is not a detail a session may leave
        implicit.
        """
        mine = set(self.lean_workspace.sources())
        found: dict[str, Path] = {}
        for source_root, _ in self.shared_roots:
            for path in sorted(source_root.rglob("*.lean")):
                name = module_name(PurePosixPath(path.relative_to(source_root).as_posix()))
                if name in mine and name not in found:
                    found[name] = path
        return found
```

Change `_run_lean_source` to use it:

```python
    def _run_lean_source(self, source: str) -> ToolResult:
        return self.lean.run_source(source, env={"LEAN_PATH": self._lean_path()})
```

Add to the imports in `chat.py`: `from .layout import BUILD_DIR, HARDY_DIR, LOCAL_DIR, LOCAL_STATE, global_build, global_lean` and `from pathlib import PurePosixPath` if not already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_workspace_build.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS — in particular the save-time guarantees in `tests/test_chat_ratchet.py` and `tests/test_chat_audit.py`, which must be untouched by this.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Let a project import a library it did not author, and say when it shadows one"
```

---

### Task 10: Registering a problem with a host Lake project

**Files:**
- Create: `src/hardy/lakefile.py`
- Test: `tests/test_lakefile.py`

**Interfaces:**
- Consumes: `layout.Layout` from Task 1.
- Produces: `class RegistrationRefused(Exception)`; `exposed_modules(lean_root: Path) -> set[str]`; `registered_libraries(lakefile: Path) -> dict[str, str]` mapping library name to its `srcDir`; `register(lakefile: Path, root: Path, slug: str) -> str` returning the text to append, raising `RegistrationRefused` with a reason.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lakefile.py
"""Registering a problem with a host Lake project, and refusing to when it collides."""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy import lakefile


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_problem_is_registered_as_its_own_library(tmp_path: Path):
    host = write(tmp_path / "lakefile.toml", 'name = "host"\n')
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    added = lakefile.register(host, tmp_path, "sylow")

    assert "[[lean_lib]]" in added
    assert 'name = "sylow"' in added
    assert "sylow/lean" in added


def test_registering_twice_is_refused_rather_than_duplicated(tmp_path: Path):
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "sylow"\nsrcDir = "other/lean"\n',
    )
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    with pytest.raises(lakefile.RegistrationRefused, match="already defines"):
        lakefile.register(host, tmp_path, "sylow")


def test_a_duplicate_module_name_is_refused_and_names_the_holder(tmp_path: Path):
    """A distinct Lake target does not rename the modules under it.

    Two problems both holding the documented default `lean/Main.lean` expose
    two modules named `Main` to one build, whatever their targets are called.
    """
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n',
    )
    write(tmp_path / "galois" / "lean" / "Main.lean", "import Mathlib\n")
    write(tmp_path / "sylow" / "lean" / "Main.lean", "import Mathlib\n")

    with pytest.raises(lakefile.RegistrationRefused) as refusal:
        lakefile.register(host, tmp_path, "sylow")

    assert "Main" in str(refusal.value)
    assert "galois" in str(refusal.value)


def test_distinct_module_names_register_cleanly(tmp_path: Path):
    host = write(
        tmp_path / "lakefile.toml",
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n',
    )
    write(tmp_path / "galois" / "lean" / "Galois.lean", "import Mathlib\n")
    write(tmp_path / "sylow" / "lean" / "Sylow.lean", "import Mathlib\n")

    added = lakefile.register(host, tmp_path, "sylow")
    assert 'name = "sylow"' in added


def test_modules_are_named_by_their_path(tmp_path: Path):
    write(tmp_path / "lean" / "Group" / "Sylow.lean", "import Mathlib\n")
    write(tmp_path / "lean" / "Main.lean", "import Mathlib\n")
    assert lakefile.exposed_modules(tmp_path / "lean") == {"Group.Sylow", "Main"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_lakefile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.lakefile'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/hardy/lakefile.py
"""Registering a problem's Lean with the host Lake project, or refusing to.

Registration is for the user's own toolchain and editor. Hardy's resolution
never depends on it, so declining always costs nothing -- which is what makes
refusing a collision the right answer rather than a hard case.
"""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

from .workspace import module_name


class RegistrationRefused(Exception):
    """A registration that would make the host build ambiguous."""


def exposed_modules(lean_root: Path) -> set[str]:
    """Every module name a source directory would put into a build."""
    if not lean_root.is_dir():
        return set()
    return {
        module_name(PurePosixPath(path.relative_to(lean_root).as_posix()))
        for path in lean_root.rglob("*.lean")
    }


def registered_libraries(lakefile: Path) -> dict[str, str]:
    """The `lean_lib` entries a host lakefile already declares, name to srcDir."""
    if not lakefile.is_file():
        return {}
    try:
        loaded = tomllib.loads(lakefile.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise RegistrationRefused(f"{lakefile} could not be read: {error}") from None
    libraries = loaded.get("lean_lib") or []
    if isinstance(libraries, dict):
        libraries = [libraries]
    return {
        str(entry["name"]): str(entry.get("srcDir", ""))
        for entry in libraries
        if isinstance(entry, dict) and entry.get("name")
    }


def register(lakefile: Path, root: Path, slug: str) -> str:
    """The lakefile stanza that registers `slug`, or a refusal with a reason.

    Refused on two collisions, and a distinct Lake target only settles the
    first of them. A `lean_lib` name is a target name; it does not rename the
    modules underneath it, so two problems both holding the documented default
    `lean/Main.lean` still put two modules named `Main` into one build.
    """
    existing = registered_libraries(lakefile)
    source = f"{slug}/lean"
    if slug in existing and existing[slug] != source:
        raise RegistrationRefused(
            f"{lakefile.name} already defines a library named {slug!r} for {existing[slug]!r}"
        )
    mine = exposed_modules(root / slug / "lean")
    for name, directory in sorted(existing.items()):
        if name == slug:
            continue
        clashing = sorted(mine & exposed_modules(root / directory))
        if clashing:
            raise RegistrationRefused(
                f"{slug!r} and the registered library {name!r} both expose "
                f"{clashing[0]!r}; rename the file in one of them, or decline "
                "registration -- Hardy's own resolution does not need it"
            )
    return f'\n[[lean_lib]]\nname = "{slug}"\nsrcDir = "{source}"\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_lakefile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/lakefile.py tests/test_lakefile.py
git commit -m "Refuse a registration that would put two modules of one name in a build"
```

---

### Task 11: Wiring registration into the CLI, without ever prompting a pipe

**Files:**
- Modify: `src/hardy/cli.py` (parser and `_chat`)
- Test: `tests/test_hardy.py`

**Interfaces:**
- Consumes: `lakefile.register`, `lakefile.RegistrationRefused` from Task 10.
- Produces: `cli.offer_registration(config, *, interactive: bool, choice: bool | None) -> str | None` returning a message to print, or None.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hardy.py`:

```python
def test_registration_is_declined_off_a_tty_without_reading_stdin(tmp_path: Path, monkeypatch):
    """A second prompt on a surviving path, with the same failure as the first.

    On a piped launch under a root holding a lakefile, asking would block at
    EOF or take the first chat message for an answer.
    """
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)

    def explode():
        raise AssertionError("stdin must not be read when there is no TTY")

    monkeypatch.setattr("builtins.input", lambda *_: explode())
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    assert cli.offer_registration(settings, interactive=False, choice=None) is None
    assert 'name = "sylow"' not in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_an_explicit_flag_registers_without_a_tty(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "sylow" in message
    assert 'name = "sylow"' in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_registering_a_colliding_module_reports_the_refusal(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text(
        'name = "host"\n\n[[lean_lib]]\nname = "galois"\nsrcDir = "galois/lean"\n', encoding="utf-8"
    )
    for slug in ("galois", "sylow"):
        (tmp_path / slug / "lean").mkdir(parents=True)
        (tmp_path / slug / "lean" / "Main.lean").write_text("import Mathlib\n", encoding="utf-8")
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    message = cli.offer_registration(settings, interactive=False, choice=True)

    assert "Main" in message
    assert "galois" in message
    assert 'name = "sylow"' not in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


def test_registering_twice_does_not_append_twice(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "host"\n', encoding="utf-8")
    (tmp_path / "sylow" / "lean").mkdir(parents=True)
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")

    cli.offer_registration(settings, interactive=False, choice=True)
    cli.offer_registration(settings, interactive=False, choice=True)

    assert (tmp_path / "lakefile.toml").read_text(encoding="utf-8").count('name = "sylow"') == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_hardy.py -k registration -v`
Expected: FAIL — `AttributeError: module 'hardy.cli' has no attribute 'offer_registration'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/hardy/cli.py`:

```python
def offer_registration(
    config: configuration.Config,
    *,
    interactive: bool,
    choice: bool | None,
    ask: Callable[[str], str] = input,
) -> str | None:
    """Register this problem with a host Lake project, if asked to.

    Never reads stdin. `choice` is what a flag or a TTY prompt already decided;
    None off a TTY means declined, because asking on a piped launch would block
    at EOF or take the first chat message for an answer. Declining is always
    safe -- Hardy's own resolution does not depend on registration.
    """
    host = config.root / "lakefile.toml"
    if not host.is_file() or choice is False:
        return None
    slug = config.project
    source = f"{slug}/lean"
    existing = lakefile.registered_libraries(host)
    # Idempotent ONLY when the existing entry is the one we would write. A
    # library of this name pointing somewhere else is a conflict the user needs
    # told about, and returning here would swallow `register`'s refusal and
    # leave `--register-lakefile` silently doing nothing.
    if existing.get(slug) == source:
        return None
    if choice is None:
        if not interactive:
            return None
        # The offer this function exists to make. Without it registration is
        # reachable only through the flag, and the promise that Hardy "offers
        # to register" is never kept on the interactive path it was written for.
        answer = ask(f"Register {slug}/lean with {host.name} so `lake build` sees it? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            return None
    try:
        stanza = lakefile.register(host, config.root, slug)
    except lakefile.RegistrationRefused as refusal:
        return f"Not registering {slug} with {host.name}: {refusal}"
    with host.open("a", encoding="utf-8") as handle:
        handle.write(stanza)
    return f"Registered {slug} with {host.name} as a lean_lib; `lake build` now sees its modules."
```

Add the flag to the `chat` subparser:

```python
    registration = chat.add_mutually_exclusive_group()
    registration.add_argument(
        "--register-lakefile",
        dest="register_lakefile",
        action="store_true",
        default=None,
        help="add this problem to the host lakefile.toml as a lean_lib",
    )
    registration.add_argument(
        "--no-register-lakefile",
        dest="register_lakefile",
        action="store_false",
        help="never touch the host lakefile.toml",
    )
```

In `_chat`, before building the session, ensure the layout exists and make the offer:

```python
    config.layout.ensure()
    notice = offer_registration(
        config,
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
        choice=getattr(args, "register_lakefile", None),
    )
    if notice:
        print(notice)
```

`_chat` will need the parsed `args`; pass them through from the caller rather than reading globals.

Add `from . import lakefile` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/test_hardy.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Offer registration on a terminal, and decline it on a pipe"
```

---

## Phase 4 — The surfaces a user touches

### Task 12: Input history off the record, and `/status` naming the project

**Files:**
- Modify: `src/hardy/tui/shell.py:225`, and the `/status` handler
- Test: `tests/tui/` (the existing shell tests)

**Interfaces:**
- Consumes: `layout.LOCAL_DIR` from Task 1, `Config.layout` from Task 4.
- Produces: `tui.shell.history_path(config) -> Path` and `tui.shell.status_line(config) -> str`. Both are NEW public names, and the `/status` handler must be routed through `status_line` rather than formatting its own string — otherwise the test calls a helper that does not exist and `tests/tui` fails with `NameError`.

- [ ] **Step 1: Write the failing test**

Add to the shell test module:

```python
def test_input_history_is_machine_local(tmp_path: Path):
    """It holds text typed and never sent, which never entered the transcript.

    Drafts, corrections, abandoned lines -- none of it is part of the record,
    and none of it belongs in a repository.
    """
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")
    settings.layout.ensure()

    history = history_path(settings)

    assert history == settings.layout.local / "input-history"
    assert history.parent.name == ".local"


def test_status_names_the_active_project(tmp_path: Path):
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")
    assert "sylow" in status_line(settings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/tui -v`
Expected: FAIL — no `history_path` helper exists.

- [ ] **Step 3: Write minimal implementation**

In `src/hardy/tui/shell.py`, extract the path into a named helper so a test can reach it, and point it at `.local/`:

```python
def history_path(config) -> Path:
    """Where the prompt's history lives.

    Machine-local, and deliberately not beside the record: it holds text the
    user typed and then did not send, which never entered the transcript and
    must not enter the repository.
    """
    return config.layout.local / "input-history"
```

and change line 225 to `history = FileHistory(str(history_path(config)))`.

Update the `/status` handler to include `config.project` in what it reports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra test pytest tests/tui -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Keep abandoned drafts out of a directory that is now committed"
```

---

### Task 13: The installers and the documentation

**Files:**
- Modify: `scripts/lib/common.sh:18`, `scripts/install-windows.ps1:71`, `scripts/uninstall-windows.ps1:57`, `scripts/uninstall.sh`
- Modify: `docs/INSTALL.md`, `README.md:171-215`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html`
- Modify: `.gitignore:19`
- Test: `tests/test_install_scripts.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no code interfaces; the installers write to `~/.hardy/config.toml`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_install_scripts.py`:

```python
def test_the_shell_installer_defaults_to_the_global_hardy_directory():
    """The path is hard-coded in more places than config.py.

    `common.sh` also passes HARDY_CONFIG into `hardy doctor`, so an installer
    left pointing at the old location would keep writing a second config there
    and the uninstaller would remove the wrong one.
    """
    text = Path("scripts/lib/common.sh").read_text(encoding="utf-8")
    assert '$HOME/.hardy/config.toml' in text
    assert "XDG_CONFIG_HOME" not in text


def test_the_windows_installers_default_to_the_global_hardy_directory():
    """Asserted against the config assignment, not against the token.

    Both scripts legitimately keep `$env:LOCALAPPDATA` for the install prefix
    and the bin directory, and `LOCALAPPDATA` contains the substring `APPDATA` —
    so banning the token outright is an assertion that can never pass.
    """
    for script in ("scripts/install-windows.ps1", "scripts/uninstall-windows.ps1"):
        text = Path(script).read_text(encoding="utf-8")
        assignment = next(line for line in text.splitlines() if "$ConfigPath" in line and "=" in line)
        assert ".hardy" in assignment, script
        assert "$env:APPDATA" not in assignment, script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_install_scripts.py -v`
Expected: FAIL — the scripts still name XDG and APPDATA.

- [ ] **Step 3: Update the scripts**

`scripts/lib/common.sh:18`:

```sh
HARDY_CONFIG="${HARDY_CONFIG:-$HOME/.hardy/config.toml}"
```

`scripts/install-windows.ps1:71` and `scripts/uninstall-windows.ps1:57`:

```powershell
$ConfigPath = if ($env:HARDY_CONFIG) { $env:HARDY_CONFIG } else { Join-Path $HOME '.hardy\config.toml' }
```

Check `scripts/uninstall.sh` picks up the new `HARDY_CONFIG` default from `common.sh` and needs no separate change.

- [ ] **Step 4: Fix the repository's own `.gitignore`**

Replace the `.hardy/` block (lines 17-19) with:

```
# A project Hardy creates in this checkout: its build trees and machine-local
# state. The sources, the writeup, and the record beside them are versioned --
# each project carries its own .gitignore saying exactly that.
.hardy/.build/
*/.build/
*/.local/
```

- [ ] **Step 5: Update the documentation**

- `README.md:171-215`: rewrite the "Use" section for the new layout — `<root>/<slug>/` with `lean/`, `tex/`, `cas/`, the record, and the two gitignored directories; `.hardy/` as project tooling; `~/.hardy/` as the global directory; `--root` and `--project` in place of `--workspace`.
- `docs/INSTALL.md`: every mention of the config path.
- `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html`: the workspace model wherever it is described.

- [ ] **Step 6: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 7: Check nothing still refers to the old world**

Run: `grep -rn "workspace\b" README.md DESIGN.md FEATURES.md docs/INSTALL.md src/hardy/ scripts/ | grep -v "lean_workspace\|LeanWorkspace\|workspace tree"`
Expected: no hits describing `.hardy/` as a workspace or `--workspace` as a flag.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Point the installers and the prose at the layout that now exists"
```

---

### Task 14: Close the escapes `layout.py` left open

Corrective, added after review of the committed Tasks 1-2 found two real escapes and one portability gap. Both P1s were **reproduced** before this task was written.

**Files:**
- Modify: `src/hardy/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `Layout`, `validate_slug`, `_write_once` from Tasks 1-2.
- Produces: `Layout.resolved_problem() -> Path` (raises `LayoutError` if it escapes); `RESERVED_NAMES: frozenset[str]`; `_write_once` replaced by `_ensure_rules(path, header, rules)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_symlinked_problem_directory_is_refused(tmp_path: Path):
    r"""Validating the name is not validating the path.

    A repository can contain `main -> ..`. The slug passes every check in
    `validate_slug` — it is one component, not `..`, not absolute — and
    `ensure()` then follows the link and creates `lean/`, `tex/`, `cas/`,
    `.local/` and `.gitignore` OUTSIDE the root. Reproduced before this test
    was written: with root `/tmp/x/root` and `main -> ..`, `ensure()` created
    `/tmp/x/lean`.
    """
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "main").symlink_to("..")
    resolved = layout.Layout(root=tmp_path / "root", slug="main")

    with pytest.raises(layout.LayoutError, match="outside"):
        resolved.ensure()

    assert not (tmp_path / "lean").exists(), "nothing may be created outside the root"


def test_an_ordinary_directory_still_resolves(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    assert resolved.resolved_problem() == (tmp_path / "sylow").resolve()


def test_missing_rules_are_added_to_an_existing_ignore_file(tmp_path: Path):
    """A pre-existing .gitignore must not leave machine-local state exposed.

    Reproduced before this test was written: with `*.log` already in the
    problem's .gitignore, `ensure()` returned without adding anything, so
    `.local/` — the provider session id, the usage ledger, and the terminal
    input history, which holds text typed and never sent — sat as ordinary
    untracked files ready to be committed.
    """
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.problem.mkdir(parents=True)
    (resolved.problem / ".gitignore").write_text("*.log\n", encoding="utf-8")

    resolved.ensure()

    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in rules, "the user's own rules are preserved"
    assert "/.local/" in rules
    assert "/.build/" in rules


def test_rules_already_present_are_not_duplicated(tmp_path: Path):
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    resolved.ensure()
    resolved.ensure()
    rules = (resolved.problem / ".gitignore").read_text(encoding="utf-8")
    assert rules.count("/.local/") == 1
    assert rules.count("/.build/") == 1


@pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT1", "trailing.", "trailing "])
def test_a_windows_reserved_slug_is_refused_on_every_platform(reserved: str):
    """Checked everywhere, not only on Windows.

    The slug can arrive from a committed config that travels with a clone, so a
    project accepted on Linux must not become uncreatable — or, with a trailing
    dot, silently alias a different directory — when the same checkout is
    opened on Windows.
    """
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(reserved)


@pytest.mark.parametrize("bad", ['a:b', 'a*b', 'a?b', 'a"b', "a<b", "a>b", "a|b"])
def test_windows_reserved_characters_are_refused(bad: str):
    with pytest.raises(layout.LayoutError):
        layout.validate_slug(bad)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: the symlink test fails by creating files outside the root; the ignore-file test fails on the missing rules; the reserved-name tests fail with no exception raised.

- [ ] **Step 3: Implement**

Add to `src/hardy/layout.py`:

```python
# Names Windows cannot use as a directory, whatever the extension, plus the
# characters it forbids. Enforced on every platform because a slug reaches here
# from a committed config file that travels with a clone: a project created on
# Linux must not be one its author cannot open on Windows.
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)
RESERVED_CHARACTERS = frozenset(':*?"<>|')
```

In `validate_slug`, after the existing separator checks and before the return:

```python
    if text.rstrip(". ") != text:
        raise LayoutError(f"a project slug may not end in a dot or a space: {slug!r}")
    if text.partition(".")[0].lower() in RESERVED_NAMES:
        raise LayoutError(f"{slug!r} is a reserved device name on Windows")
    if set(text) & RESERVED_CHARACTERS:
        raise LayoutError(f"a project slug may not contain any of {''.join(sorted(RESERVED_CHARACTERS))}: {slug!r}")
```

Add the path check to `Layout`:

```python
    def resolved_problem(self) -> Path:
        """The problem directory, proven to be inside the root.

        `validate_slug` checks the NAME; this checks the PATH, and they are not
        the same question. A repository may ship `main -> ..` as a symlink: the
        slug passes every name check, and following it would put this project's
        sources, record and ignore file outside the root Hardy was pointed at.
        """
        root = self.root.resolve()
        problem = self.problem.resolve()
        if problem.parent != root:
            raise LayoutError(
                f"{self.problem} resolves to {problem}, which is outside {root}"
            )
        return problem
```

and call it first thing in `ensure()`:

```python
    def ensure(self) -> None:
        # Before anything is created: a symlinked problem directory would
        # otherwise have every mkdir below land outside the root.
        self.root.mkdir(parents=True, exist_ok=True)
        self.problem.mkdir(parents=True, exist_ok=True)
        self.resolved_problem()
        for directory in (self.lean, self.tex, self.cas, self.local, self.hardy_dir):
            directory.mkdir(parents=True, exist_ok=True)
        _ensure_rules(self.problem / ".gitignore", PROBLEM_HEADER, PROBLEM_RULES)
        _ensure_rules(self.hardy_dir / ".gitignore", TOOLING_HEADER, TOOLING_RULES)
```

Replace `PROBLEM_IGNORE`/`TOOLING_IGNORE`/`_write_once` with rule lists and an appender:

```python
PROBLEM_HEADER = (
    "# Written by Hardy. Everything here is recomputable from the sources
"
    "# beside it, or belongs to this machine and this account.
"
)
PROBLEM_RULES = ("/.build/", "/.local/")
TOOLING_HEADER = (
    "# Written by Hardy. The oleans for this project's shared Lean library,
"
    "# rebuilt on demand and never committed.
"
)
TOOLING_RULES = ("/.build/",)


def _ensure_rules(path: Path, header: str, rules: tuple[str, ...]) -> None:
    """Make sure `rules` are in `path`, leaving whatever else is there alone.

    Appending rather than writing once. A problem directory may already carry a
    `.gitignore` a user wrote, and returning early on that basis left `.local/`
    unignored -- so the provider session id, the spend ledger, and the terminal
    input history, which holds text typed and never sent, sat as ordinary
    untracked files waiting to be committed.
    """
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [rule for rule in rules if rule not in existing]
    if not missing:
        return
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    if not existing:
        lines.extend(header.rstrip("\n").splitlines())
    lines.extend(missing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the tests**

Run: `uv run --extra test pytest tests/test_layout.py -v`
Expected: PASS, including the six earlier tests from Task 2 — `test_ensure_does_not_overwrite_an_edited_ignore_file` must still pass, because appending a missing rule is not overwriting an edit.

- [ ] **Step 5: Wire `ensure()` into the path that opens a project**

Write the test first, in `tests/unit/test_chat_wiring.py`:

```python
def test_opening_a_project_creates_its_layout(tmp_path: Path):
    """Otherwise every ignore rule this plan writes is inert.

    `grep -rn "ensure()" src/` returned nothing before this test: the
    directories and the anchored ignore rules existed only in unit tests, so a
    real run left `.build/` and `.local/` as ordinary trackable files.
    """
    settings = configuration.load(tmp_path / "absent.toml", root=tmp_path, project="sylow")
    cli.prepare_layout(settings)

    problem = tmp_path / "sylow"
    assert (problem / "lean").is_dir()
    assert "/.local/" in (problem / ".gitignore").read_text(encoding="utf-8")
```

Then in `src/hardy/cli.py`, add the function and call it at the top of `_chat`, **before** `cas_tools.build_runtime` — the CAS runtime writes its log under `<slug>/cas/`, so the directories must exist first:

```python
def prepare_layout(config: configuration.Config) -> None:
    """Make the project's directories and ignore rules exist before anything writes.

    Called for its side effects at the start of every path that opens a
    project. Without it `Layout.ensure` is reachable only from its own tests,
    and a real run leaves the build tree and the machine-local state as
    ordinary trackable files -- which is the whole thing this layout exists to
    prevent.
    """
    config.layout.ensure()
    config.layout.unignore_tooling(config.root / ".gitignore")
```

- [ ] **Step 6: Stop a legacy root ignore from silencing the tooling directory**

Test, in `tests/test_layout.py`:

```python
def test_a_legacy_hardy_rule_is_removed_from_the_root_ignore(tmp_path: Path):
    """Git does not traverse into an excluded directory.

    Reproduced: with `.hardy/` in the root .gitignore, `git add -A` tracked
    only .gitignore itself and `git check-ignore` reported `.hardy/config.toml`
    excluded. Writing `.hardy/.gitignore` inside it changes nothing, so
    repurposing `.hardy/` as committed tooling means the old rule must go.
    """
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("*.log\n.hardy/\ndist/\n", encoding="utf-8")
    resolved = layout.Layout(root=tmp_path, slug="sylow")

    assert resolved.unignore_tooling(root_ignore) is True

    kept = root_ignore.read_text(encoding="utf-8").splitlines()
    assert ".hardy/" not in kept
    assert "*.log" in kept, "the user's other rules are untouched"
    assert "dist/" in kept


def test_an_ignore_file_without_the_legacy_rule_is_left_alone(tmp_path: Path):
    root_ignore = tmp_path / ".gitignore"
    root_ignore.write_text("*.log\n", encoding="utf-8")
    resolved = layout.Layout(root=tmp_path, slug="sylow")
    assert resolved.unignore_tooling(root_ignore) is False
    assert root_ignore.read_text(encoding="utf-8") == "*.log\n"
```

Implementation, in `Layout`:

```python
    def unignore_tooling(self, root_ignore: Path) -> bool:
        """Drop a legacy rule excluding the whole tooling directory.

        `.hardy/` used to be scratch, and roots created then still say so. Git
        will not descend into an excluded directory, so the `.gitignore` this
        layout writes *inside* `.hardy/` cannot make its config and shared Lean
        trackable while the parent rule stands. Only the exact whole-directory
        forms are removed; anything more specific a user wrote is theirs.
        """
        if not root_ignore.is_file():
            return False
        legacy = {HARDY_DIR, f"{HARDY_DIR}/", f"/{HARDY_DIR}", f"/{HARDY_DIR}/"}
        lines = root_ignore.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if line.strip() not in legacy]
        if len(kept) == len(lines):
            return False
        root_ignore.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return True
```

- [ ] **Step 7: Match a quoted key in the config migration**

TOML permits a quoted key, which `tomllib` decodes as the ordinary setting but the line-based regex in `migrate_global` does not match. The migration would then delete the source and install a destination still carrying the retired key — and every later load rejects it as unknown, so Hardy will not start at all.

Test, in `tests/test_config.py`:

```python
def test_a_quoted_retired_key_is_dropped_too(tmp_path: Path):
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('model = "x"\n"workspace" = ".hardy"\n', encoding="utf-8")
    destination = tmp_path / ".hardy" / "config.toml"

    config.migrate_global(legacy, destination)

    assert config.read_file(destination)["model"] == "x"
```

In `config.py`, widen the pattern so an optionally-quoted key matches. Build it from `RETIRED_SETTINGS` as before, allowing an optional single or double quote on each side of the name before the `=`.

- [ ] **Step 8: Fix this repository's own `.gitignore`**

Replace the `.hardy/` block (the one whose comment calls it "Per-run state, never committed") with rules that are true of the new layout:

```
# A project Hardy creates in this checkout keeps its build tree and its
# machine-local state out of git; its sources, writeup and record beside them
# are versioned, and each project carries its own .gitignore saying so.
.hardy/.build/
*/.build/
*/.local/
```

- [ ] **Step 9: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Check the path and not only the name, and make the rules actually run"
```

---

## Self-Review


**Spec coverage.** Every section of the spec maps to a task: the layout and anchored ignores to Tasks 1–2; `~/.hardy/` and the translated config move to Task 3; two config layers, `HARDY_CONFIG` scoping, slug validation, non-TTY project selection and the `--workspace` removal to Task 4; the `.local/` split to Task 5; the transcript identity bound to the thread to Task 6; the deleted migration to Task 7; relative CAS references to Task 8; shared libraries and shadow reporting to Task 9; lakefile registration with both refusals to Tasks 10–11; input history and `/status` to Task 12; installers, `.gitignore` and prose to Task 13.

**Not covered, and deliberately:** approver identity (spec's Open questions, its own issue), `runs_root` (spec's Out of scope), imported-axiom approval (deferred by the spec), and #112's ingestion.

**Type consistency.** `Layout` is constructed as `Layout(root=..., slug=...)` throughout; `Config.layout` returns it; `layout.validate_slug` is the single validator, called from `config.active_project`; `LOCAL_DIR`/`LOCAL_STATE`/`HARDY_DIR`/`BUILD_DIR` are defined once in `layout.py` and imported everywhere else; `_carried_thread` is the only reader of `provider_session` after Task 6.

**One risk the executor should know:** Task 4 breaks every caller of `config.workspace` at once, and Step 5 of that task is where they are all fixed. Expect a red suite in the middle of Task 4 — that is the design, not a mistake.
