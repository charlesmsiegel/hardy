from __future__ import annotations

import os
import re
import shlex
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import layout
from .domain import RunLimits

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_LEAN_COMMAND = "lake env lean"
# Importing Mathlib costs tens of seconds on a cold machine, so the default is
# generous; a fast environment simply never reaches it.
DEFAULT_LEAN_TIMEOUT = 180.0
DEFAULT_LATEX_COMMAND = "pdflatex -interaction=nonstopmode -halt-on-error"
DEFAULT_RUNS_ROOT = "runs"
DEFAULT_LAKE = "lake"
DEFAULT_ELAN = "elan"
DEFAULT_TECTONIC = "tectonic"
# The bundle is pinned by URL and digest together: a writeup is only
# reproducible if the TeX distribution behind it is the one that built it.
DEFAULT_TECTONIC_BUNDLE = "https://data1.fullyjustified.net/tlextras-2022.0r0.tar"
DEFAULT_TECTONIC_BUNDLE_SHA256 = (
    "6ffe055852f8faf66c0acbe1a7fb27f87b869a90bad1204f3bf4d9683f597c7c"
)

# Every setting, and the environment variable that overrides the config file.
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
    "project_context": "HARDY_PROJECT_CONTEXT",
}

# What a project's own committed config may say. Deliberately tiny: the file
# travels with a clone, and Hardy runs the configured CAS executable before the
# prompt appears. A repository gets to say which problem is active. It does not
# get to say which programs run.
PROJECT_SETTINGS = frozenset({"project"})

# SymPy is the default because it is a Python dependency and therefore always
# present. Singular and Macaulay2 are far better at algebraic geometry and far
# worse at Windows, so they are opt-in rather than assumed.
CAS_BACKENDS = ("sympy", "singular", "macaulay2")
DEFAULT_CAS_BACKEND = "sympy"


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


def migrate_global(source: Path | None = None, destination: Path | None = None) -> bool:
    """Move a pre-`~/.hardy/` config into place, keeping the settings that exist.

    A translation rather than a copy. `read_file` refuses any key outside
    `SETTINGS`, and every installer-written config carries `workspace`, which
    this change removes -- so relocating the file verbatim would leave Hardy
    unable to load its own configuration.

    An ALLOWLIST, not a list of known-retired keys. That is the whole of the
    difference between a migration and a brick. Excluding a fixed
    `RETIRED_SETTINGS` copied every OTHER unrecognised key through verbatim,
    and `read_file` refuses those just as flatly -- so a legacy file carrying
    anything Hardy no longer knows (a setting retired in some later version, a
    typo, a key from a fork) produced a destination that cannot be loaded, and
    then DELETED the source. Reproduced: a legacy config of
    `model = "x"`, `workspace = ".hardy"`, `legacy_thing = "y"` migrated to a
    destination still carrying `legacy_thing`, after which every hardy
    invocation -- `doctor` included, so there was nothing left to diagnose
    with -- failed on an unknown setting, with the original gone. Keeping only
    what `SETTINGS` names cannot fail that way for any key, present or future.

    Parsed and re-serialized, not line-filtered: a legacy file may spell a
    retired key as a multiline value -- `workspace = \"\"\"` with the string
    and the closing delimiter on their own following lines -- and dropping
    only the assignment line would leave those continuation lines behind.
    Since the source is then deleted, the destination would be a file
    `tomllib` cannot parse, and Hardy would not start. TOML's grammar is not
    line-oriented, so only a real parse can tell where a value actually ends
    -- and a real parse also means a quoted key (`"workspace" = ...`, which a
    line-based regex would have to special-case) needs no special-casing at
    all: `tomllib` already treats it as the same key either way.

    Returns whether anything moved. An absent source and an existing
    destination are both ordinary: the destination is the newer file and is
    never overwritten.
    """
    source = source or legacy_config_path()
    destination = destination or (layout.global_dir() / "config.toml")
    if not source.is_file() or destination.exists():
        return False
    values = tomllib.loads(source.read_text(encoding="utf-8-sig"))
    kept = {key: value for key, value in values.items() if key in SETTINGS}
    lines = [_render_toml_line(key, value) for key, value in kept.items()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    source.unlink()
    return True


def _render_toml_line(key: str, value: Any) -> str:
    """One `key = value` line, typed the way `tomllib` would read it back.

    `migrate_global` re-serializes rather than copying source text, so a
    survivor's type has to be reconstructed explicitly: a number written back
    as a quoted string (`lean_timeout = "90"`) would still parse, but nothing
    else in this module ever writes a config that way, and a hand-inspecting
    user comparing before and after would see a spurious change.
    """
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    return f'{key} = "{_toml_string(str(value))}"'


def _toml_string(text: str) -> str:
    """`text` as the body of a TOML basic string, control characters included.

    A basic string may not contain a raw control character at all, and a legacy
    config is free to hold one: a triple-quoted `model` whose text runs over
    two lines parses to a value with a newline in it, which the old escaping
    passed through untouched into a single-line quoted value. The result was a
    `config.toml` that `tomllib` refuses
    -- written after the source had been read and just before it was DELETED,
    so the settings were not recoverable and Hardy would not start. Every
    control character gets an escape here, so whatever a legacy file held
    round-trips into a file that parses back to the same string.
    """
    escaped = []
    for character in text:
        if character in {"\\", '"'}:
            escaped.append("\\" + character)
        elif character in _TOML_ESCAPES:
            escaped.append(_TOML_ESCAPES[character])
        elif character < " " or character == "\x7f":
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return "".join(escaped)


#: The escapes TOML spells with a letter. Everything else that is a control
#: character goes out as `\uXXXX`, which the grammar accepts anywhere a basic
#: string does.
_TOML_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


@dataclass(frozen=True)
class Config:
    """Resolved settings. Later sources win: file, then environment, then flags."""

    model: str | None
    lean_command: tuple[str, ...]
    lean_project: Path | None
    lean_timeout: float
    latex_command: tuple[str, ...]
    root: Path
    project: str
    # Where staged `prove` runs are kept, and the pinned toolchain that builds
    # their documents. The budgets a run is frozen under travel with them.
    runs_root: Path = Path(DEFAULT_RUNS_ROOT)
    lake: Path = Path(DEFAULT_LAKE)
    elan: Path = Path(DEFAULT_ELAN)
    tectonic: Path = Path(DEFAULT_TECTONIC)
    tectonic_bundle: str = DEFAULT_TECTONIC_BUNDLE
    tectonic_bundle_sha256: str = DEFAULT_TECTONIC_BUNDLE_SHA256
    # The computer algebra kernel. `cas_command` is unset for SymPy, which runs
    # on Hardy's own interpreter; the other backends need an executable.
    cas_backend: str = DEFAULT_CAS_BACKEND
    cas_command: Path | None = None
    # Whether an interactive session reads the project's own `AGENTS.md` (or
    # `HARDY.md`). Only interactive: `prove` and `batch` never read it at all,
    # so this setting cannot make a graded run depend on a project-local file.
    project_context: bool = True
    limits: RunLimits = field(default_factory=RunLimits)
    path: Path | None = None
    requested_path: Path | None = None

    @property
    def config_path(self) -> Path:
        """Where settings are read from and written to, existing or not.

        `path` is None until the file exists, so it cannot answer this: a
        `--config` naming a file yet to be created would otherwise send a write
        to the platform default instead of the file the user asked for.
        """
        return self.requested_path or self.path or default_config_path()

    @property
    def layout(self) -> layout.Layout:
        """Where this configuration says the active problem's parts live."""
        return layout.Layout(root=self.root, slug=self.project)


def read_file(path: Path) -> dict[str, Any]:
    """Read one config file, rejecting keys Hardy does not understand.

    Read as `utf-8-sig` because Windows editors and PowerShell write UTF-8 with
    a byte-order mark: read as plain utf-8 the mark joins the first key and
    tomllib rejects a file that looks perfectly ordinary on screen. Writing
    stays plain utf-8, so saving a setting also drops the mark.
    """
    if not path.exists():
        return {}
    values = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    unknown = sorted(set(values) - set(SETTINGS))
    if unknown:
        raise ValueError(f"{path}: unknown settings {unknown}; known settings are {sorted(SETTINGS)}")
    return {key: value for key, value in values.items() if str(value).strip() != ""}


def existing_projects(root: Path) -> list[str]:
    """The slugs under `root` that already hold a record, sorted.

    A directory counts as a project when Hardy has written its record there.
    An empty directory a user happened to create is not one, and neither is
    `.hardy/`, which `validate_slug` refuses anyway.

    Every name is put through `validate_slug` before it is offered. This list
    is not only shown: `active_project` will RETURN one of these as the slug a
    session opens when the root holds exactly one project, and a directory can
    carry a name no slug is allowed to have -- `com1/`, `trailing /`, one with
    a colon in it -- because a checkout, an unpacked archive or another tool
    put it there rather than Hardy. Handing such a name back would smuggle
    past the very check every other route into a slug goes through.
    """
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / layout.RECORD).is_file():
            continue
        try:
            # Compared, not just called: `validate_slug` trims outer whitespace
            # as a convenience for a hand-typed value, so `" main"` comes back
            # as `"main"` -- a slug naming a directory that is not this one.
            if layout.validate_slug(child.name) == child.name:
                found.append(child.name)
        except layout.LayoutError:
            continue
    return found


def active_project(
    root: Path,
    stated: str | None,
    project_values: dict[str, Any],
    choose: Callable[[list[str]], str | None] | None = None,
) -> str:
    """Which problem this run opens.

    It never reads stdin itself: prompting on a piped launch would hang, fail
    at EOF, or take the first chat message for a slug. Without a `choose` it is
    entirely deterministic, and that path is unchanged -- one recorded problem
    opens itself, anything else opens `main`.

    `choose` is what a caller WITH a terminal supplies, and it is the promise
    the old docstring made and nothing kept. A root holding several recorded
    problems, no `project` in either config layer and no `--project` is an
    ambiguity, and an interactive launch resolved it in silence by opening --
    or creating -- `main`, so a user with `sylow/` and `burnside/` on disk got
    a third, empty problem and no hint that the other two existed. It is
    consulted only where the ambiguity is real: a stated slug, a configured
    one, or a single recorded problem is an answer already, and asking about
    an answer nobody is missing is how a prompt becomes noise. Declining --
    returning None, which is what an empty line means -- keeps the old default.
    """
    for candidate in (stated, project_values.get("project")):
        if candidate:
            return layout.validate_slug(str(candidate))
    present = existing_projects(root)
    if len(present) == 1:
        return present[0]
    if choose is not None and len(present) > 1:
        chosen = choose(present)
        if chosen:
            return layout.validate_slug(str(chosen))
    return layout.DEFAULT_SLUG


def load(
    path: Path | None = None,
    *,
    root: Path | None = None,
    project: str | None = None,
    choose: Callable[[list[str]], str | None] | None = None,
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
    project_path = resolved_root / layout.HARDY_DIR / "config.toml"
    project_file = read_file(project_path)
    project_values = {key: value for key, value in project_file.items() if key in PROJECT_SETTINGS}
    # Said out loud, once. A key Hardy knows but this layer may not set is
    # dropped in silence otherwise, and a user who put `model = ...` in the
    # committed config would watch Hardy go on using the old model with
    # nothing anywhere to say why. One line naming the count, the file and
    # what the layer accepts is enough to end that hunt.
    dropped = len(project_file) - len(project_values)
    if dropped:
        print(f"ignoring {dropped} settings in {project_path}; a project config may only set: {', '.join(sorted(PROJECT_SETTINGS))}")
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

    def flag(key: str, default: bool) -> bool:
        """A boolean setting, spelled the way each layer can spell it.

        TOML has real booleans; an environment variable and a flag do not, so
        `HARDY_PROJECT_CONTEXT=0` has to mean what `project_context = false`
        means. A value that is neither is refused here rather than quietly
        read as true -- a user who wrote `off` and meant it should not have to
        discover from a transcript that their setting did nothing.
        """
        value = values.get(key, default)
        if isinstance(value, bool):
            return value
        spelling = str(value).strip().lower()
        if spelling in {"1", "true", "yes", "on"}:
            return True
        if spelling in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{key} must be true or false, not {value!r}")

    cas_backend = text("cas_backend", DEFAULT_CAS_BACKEND)
    # Rejected here rather than at first use: an unknown backend is a typo in a
    # config file, and the place to say so is where the file is read.
    if cas_backend not in CAS_BACKENDS:
        raise ValueError(f"cas_backend must be one of {list(CAS_BACKENDS)}, not {cas_backend!r}")

    return Config(
        model=str(values["model"]) if values.get("model") else DEFAULT_MODEL,
        lean_command=tuple(shlex.split(text("lean_command", DEFAULT_LEAN_COMMAND))),
        lean_project=location("lean_project"),
        lean_timeout=lean_timeout,
        latex_command=tuple(shlex.split(text("latex_command", DEFAULT_LATEX_COMMAND))),
        root=resolved_root,
        # `choose` reaches here rather than the caller asking first because
        # the root the question is about is resolved in this function, from
        # three layers the caller does not otherwise take apart.
        project=active_project(resolved_root, project, values, choose),
        runs_root=location("runs_root") or Path(DEFAULT_RUNS_ROOT),
        lake=location("lake") or Path(DEFAULT_LAKE),
        elan=location("elan") or Path(DEFAULT_ELAN),
        tectonic=location("tectonic") or Path(DEFAULT_TECTONIC),
        tectonic_bundle=text("tectonic_bundle", DEFAULT_TECTONIC_BUNDLE),
        tectonic_bundle_sha256=text("tectonic_bundle_sha256", DEFAULT_TECTONIC_BUNDLE_SHA256),
        cas_backend=cas_backend,
        cas_command=location("cas_command"),
        project_context=flag("project_context", True),
        path=path if path.exists() else None,
        requested_path=path,
    )


def _upsert(lines: list[str], key: str, value: str) -> list[str]:
    """`lines` with `key` set to `value`, replacing its line or appending one."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    rendered = f'{key} = "{escaped}"'
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            return lines
    lines.append(rendered)
    return lines


def write_setting(path: Path, key: str, value: str) -> None:
    """Upsert one setting in the user's own config file.

    Line-based rather than a parse-and-rewrite so the installer's comments and
    any hand-written ordering survive; the file is often edited by a human.

    For the USER's file only -- `~/.hardy/config.toml`, or wherever `--config`
    points. The project layer inside a checkout goes through
    `write_project_setting`, which has a different threat model and a
    different door.
    """
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}; known settings are {sorted(SETTINGS)}")
    header = ["# Written by Hardy. Every value can be overridden by a", "# HARDY_* environment variable or a command-line flag."]
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else list(header)
    _rewrite(path, _upsert(lines, key, value))


PROJECT_HEADER = (
    "# Written by Hardy, and committed with this checkout. It says which",
    "# problem is active here; nothing else may be set from this layer.",
)


def write_project_setting(root: Path, key: str, value: str) -> None:
    """Upsert one setting in `<root>/.hardy/config.toml`, through the guard.

    The same upsert as `write_setting` and a deliberately different door,
    because the file is in a different place in the threat model: it arrives
    with a clone, so every path around it -- the directory, the file, and the
    temporary the write goes through -- is attacker-chosen.

    `_rewrite`'s fixed `<name>.tmp` is exactly the hole `WriteGuard.write_bytes`
    was written to close, and it is worse here than it was for the record: a
    repository shipping `.hardy/config.toml.tmp` as a link to a file the user
    can write gets Hardy's new bytes written straight THROUGH it, `chmod 0600`
    applied to the victim, and then the `os.replace` renames the link over the
    config -- so the target is destroyed and nothing is left to show it
    happened. Reproduced before this was written, on a `victim` outside the
    root: it came back holding this function's own output at mode 0600.

    Reads go through the guard too. A symlinked `config.toml` read here and
    rewritten would carry another file's lines into the checkout's config.
    """
    if key not in PROJECT_SETTINGS:
        raise ValueError(
            f"{key!r} may not be set from a project config; this layer may only set: "
            f"{', '.join(sorted(PROJECT_SETTINGS))}"
        )
    guard = layout.WriteGuard(root / layout.HARDY_DIR, create=True)
    name = "config.toml"
    try:
        with guard.open(name, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        lines = list(PROJECT_HEADER)
    guard.write_bytes(name, ("\n".join(_upsert(lines, key, value)) + "\n").encode("utf-8"))


def remove_setting(path: Path, key: str) -> None:
    """Delete one setting, leaving every other line alone.

    The counterpart to `write_setting`: saving has to be able to say a setting
    no longer applies, not only what it is now. A line left behind from an
    earlier save would go on outranking the value it was replaced by.
    """
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}; known settings are {sorted(SETTINGS)}")
    if not path.exists():
        return
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    kept = [line for line in lines if not pattern.match(line)]
    if len(kept) != len(lines):
        _rewrite(path, kept)


def _rewrite(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
