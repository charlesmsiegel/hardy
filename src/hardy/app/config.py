from __future__ import annotations

import os
import re
import shlex
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hardy.agents import compaction
from hardy.agents.spend_budget import SpendPolicy
from hardy.foundation.locking import replace_with_retry
from hardy.prompts.user import unquoted
from hardy.workflows import layout
from hardy.workflows.contracts import RunLimits

#: Whether a configured command is split the way a POSIX shell would.
#:
#: A plain `os.name` check, but named and read as a module attribute rather
#: than inlined at each call site: a test exercises the Windows-mode splitter
#: on every OS by patching this flag rather than `os.name` itself, which
#: would also perturb `legacy_config_path` and anything else in the process
#: that asks the platform what it is.
_POSIX = os.name != "nt"

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_LEAN_COMMAND = "lake env lean"
# Importing Mathlib costs tens of seconds on a cold machine, so the default is
# generous; a fast environment simply never reaches it.
DEFAULT_LEAN_TIMEOUT = 180.0
# How long a Lean check, a Lean save, a LaTeX check or save, or a computer
# algebra cell may hold an interactive turn before it is detached into a
# background job and the turn goes on without it. Ten seconds keeps a quick
# check inline and hands a Mathlib-sized one to the background; zero disables
# detaching and every call blocks the turn as it used to.
DEFAULT_COMPUTE_DETACH_SECONDS = 10.0
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
    "faithfulness_model": "HARDY_FAITHFULNESS_MODEL",
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
    "backend": "HARDY_BACKEND",
    "cas_backend": "HARDY_CAS_BACKEND",
    "cas_command": "HARDY_CAS_COMMAND",
    "project_context": "HARDY_PROJECT_CONTEXT",
    "context_window": "HARDY_CONTEXT_WINDOW",
    "provider_budget": "HARDY_PROVIDER_BUDGET",
    "delegation_workers": "HARDY_DELEGATION_WORKERS",
    "compute_detach_seconds": "HARDY_COMPUTE_DETACH_SECONDS",
}

# What a project's own committed config may say. Deliberately tiny: the file
# travels with a clone, and Hardy runs the configured CAS executable before the
# prompt appears. A repository gets to say which problem is active. It does not
# get to say which programs run.
PROJECT_SETTINGS = frozenset({"project"})

# SymPy is the default because it is a Python dependency and therefore always
# present. Singular and Macaulay2 are far better at algebraic geometry and far
# worse at Windows, so they are opt-in rather than assumed.
# Which transport a conversation is carried by. `claude` is the default
# because it is the one that needs no API key: it authenticates through the
# Claude Code agent SDK and a Claude Max subscription. `api` is the opt-in
# harness-owned loop of issue #23 -- Hardy decides when a provider call is
# made, keeps both bounds itself, and can decline a call outright -- and it
# needs `ANTHROPIC_API_KEY`.
BACKENDS = ("claude", "api")
DEFAULT_BACKEND = "claude"

#: How each backend is paid for, in the words a session banner and the model
#: picker use. Stated once because both of them tell a user which credentials
#: are about to be spent, and "Claude Code subscription" over a metered API key
#: is not a cosmetic error -- it is the wrong answer to the question the line
#: exists to answer.
AUTHENTICATION = {
    "claude": "Claude Code subscription",
    "api": "Anthropic API key (metered)",
}


def authentication(backend: str) -> str:
    """What a session on `backend` is billed against."""
    return AUTHENTICATION.get(backend, backend)

#: The context window Hardy plans compaction against, in tokens.
#:
#: Deliberately not derived from the model identity: the curated catalog does
#: not establish the capacity this account and endpoint offer. Guessing higher is the
#: unrecoverable direction -- the compactor would never run and the provider
#: would refuse every request -- while guessing lower only cuts sooner than it
#: had to.
#:
#: Settable because the figure is a property of the endpoint, not of Hardy: a
#: gateway answering `claude-opus-5` may offer a smaller window than Anthropic
#: does, and a user who knows that needs somewhere to say so. What was used is
#: written into the compaction event, so a transcript states the window its
#: cuts were planned against rather than leaving it to be inferred.
#:
#: The figure itself lives in `compaction`, beside the reserve and recent
#: budgets it is spent against, so the default and the planner cannot drift.
DEFAULT_CONTEXT_WINDOW = compaction.CONTEXT_WINDOW

#: How many background delegation workers an interactive session runs at once.
#: A small pool by default; a machine that can carry more says so in its
#: config, and nothing here caps what it may say -- the ceiling is the
#: machine's and the provider's, not Hardy's.
DEFAULT_DELEGATION_WORKERS = 4

#: The largest reply the API transport will ask for, and therefore the smallest
#: window that can hold one. Stated here rather than imported from
#: `api_runtime`, which pulls in the whole chat stack to read one number; a test
#: pins the two together so neither can move without the other.
MINIMUM_CONTEXT_WINDOW = 8192

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


def _parse_toml(text: str, path: Path) -> dict[str, Any]:
    """Parse `text` as TOML, naming `path` and explaining Windows escaping on failure.

    `tomllib.TOMLDecodeError` names a line and column, never the file: `hardy
    doctor` on a bad `config.toml` used to fail with a message an unfamiliar
    user could not connect back to the file they had just edited. The three
    call sites that read a config file (`read_file`, `migrate_global`,
    `write_project_setting`) all route through here, so all three name the
    file the same way.

    The hint is worth adding even when the failure was not a backslash: a
    literal `\\` in a double-quoted TOML string is by far the most common
    cause of a Windows user's config failing to parse at all, since it is
    exactly how Explorer and PowerShell show a path.
    """
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"{path}: {error}. On Windows write paths with forward slashes "
            f"(\"C:/Users/me/lean\"), in single quotes ('C:\\Users\\me\\lean'), "
            f"or with doubled backslashes."
        ) from None


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
    values = _parse_toml(source.read_text(encoding="utf-8-sig"), source)
    kept = {key: value for key, value in values.items() if key in SETTINGS}
    lines = [_render_toml_line(key, value) for key, value in kept.items()]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    temporary.chmod(0o600)
    replace_with_retry(temporary, destination)
    source.unlink()
    return True


def _render_toml_line(key: str, value: Any) -> str:
    """One `key = value` line, typed the way `tomllib` would read it back.

    `migrate_global` re-serializes rather than copying source text, so a
    survivor's type has to be reconstructed explicitly: a number written back
    as a quoted string (`lean_timeout = "90"`) would still parse, but nothing
    else in this module ever writes a config that way, and a hand-inspecting
    user comparing before and after would see a spurious change.

    A list is rendered as a TOML array of its own rendered elements, not
    `str(value)`: `str(["lake", "env"])` is the Python repr `"['lake', 'env']"`,
    a single string that reads back as one nonsense token rather than the
    list `lean_command` (issue #260) accepts. This is what lets
    `migrate_global` round-trip a legacy `lean_command = [...]`.
    """
    if isinstance(value, list):
        return f"{key} = [{', '.join(_render_toml_scalar(item) for item in value)}]"
    return f"{key} = {_render_toml_scalar(value)}"


def _render_toml_scalar(value: Any) -> str:
    """One TOML value literal (no `key = `), for a scalar or a list element."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{_toml_string(str(value))}"'


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

#: The characters an unescaped backslash before `t`, `n`, `b`, `f` or `r`
#: silently becomes inside a double-quoted TOML string (issue #303). Refusing
#: them is scoped to path and command settings, never every setting: a
#: legacy `model` may legitimately hold a real newline (`_toml_string`'s
#: docstring), and that must keep loading.
_CONTROL_CHARACTERS = frozenset(_TOML_ESCAPES)


def _reject_control_characters(where: str, value: str, *, from_toml: bool) -> str:
    """Refuse `value` if it holds a control character, naming where it came from.

    A path or a command hand-written as `"C:\\temp\\new"` parses without
    error -- `tomllib` turns `\\t` and `\\n` into TAB and LF -- and Hardy used
    to go on to report a directory named with a literal TAB as merely
    "missing", with nothing to say why. Caught here, at the setting that
    actually holds the bad value, the message names the setting and its
    source: the file, the environment variable, or the command line (`where`).

    Only a value read from a TOML file can have got its control character
    from a backslash escape, so only then does the message say so. From the
    environment or a flag the character was really there, and blaming TOML
    would send the user to the wrong place.
    """
    if not any(character in value for character in _CONTROL_CHARACTERS):
        return value
    if from_toml:
        raise ValueError(
            f"{where} contains a control character: a backslash there was read by "
            f"TOML as an escape (\\t, \\n, \\b, \\f, or \\r), not a literal backslash. Write "
            f"it with forward slashes, in single quotes, or with doubled backslashes."
        )
    raise ValueError(
        f"{where} contains a control character (a tab, newline, backspace, form feed "
        f"or carriage return), which no path or command holds; check how the value "
        f"was quoted where it was set."
    )


def split_command(
    value: str | list[str], *, posix: bool | None = None, setting: str = "command"
) -> tuple[str, ...]:
    """Argv for a configured command (`lean_command`, `latex_command`), split for the platform.

    A TOML array is taken verbatim, after checking every element is a string:
    no shell-splitting rule applies to it, because nothing had to guess where
    the arguments end -- the list already says. `str(list)` used to reach
    `shlex.split` instead and produce garbage like `["['lake',"]` (issue #260).

    A string is split with POSIX `shlex` rules, or, off POSIX, with
    `shlex.split(value, posix=False)` and then unquoted by hand with
    `hardy.prompts.user.unquoted` -- the same fix `/import` already needed for
    the same reason: POSIX mode reads every backslash as an escape, and a
    Windows path is full of them.

    `posix` defaults to `None`, which reads the module flag `_POSIX` at call
    time rather than baking a default into the function's signature: a test
    selects the platform by monkeypatching `_POSIX`, and a bound default
    parameter would freeze the value `_POSIX` held when this module was first
    imported and never see that patch.

    `setting` names the value in every refusal -- `load` passes the key and
    where it was set -- so an unbalanced quote or an empty command is reported
    against the setting that holds it when the config is read, not as a bare
    `shlex` error, or an `IndexError` at the first Lean or LaTeX call.
    """
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ValueError(f"{setting} list entries must be strings, not {value!r}")
        argv = tuple(value)
    else:
        if posix is None:
            posix = _POSIX
        try:
            words = shlex.split(value, posix=posix)
        except ValueError as error:
            raise ValueError(
                f"{setting} cannot be split into arguments ({error}): close the quote, "
                f"or write the command as a TOML array of arguments"
            ) from None
        argv = tuple(words) if posix else tuple(unquoted(word) for word in words)
    if not argv or not argv[0].strip():
        raise ValueError(f"{setting} is empty: it must name the program to run")
    return argv


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
    # Which chat of the project this launch opens. A per-launch choice like
    # `--fresh-thread`, not a standing preference: persisted, it would reopen
    # a stale chat under an unrelated later launch. `main` is the transcript
    # beside the record; the browser creates others under `chats/<id>/`.
    chat: str = layout.DEFAULT_CHAT
    # Where staged `prove` runs are kept, and the pinned toolchain that builds
    # their documents. The budgets a run is frozen under travel with them.
    runs_root: Path = Path(DEFAULT_RUNS_ROOT)
    # Who reads the translation back before proof search. Unset means the run's
    # own model, on a thread of its own -- already independent of the
    # conversation that wrote the formalization, which is the shared context
    # the gate has to defeat. Naming a different model here buys independent
    # weights as well.
    faithfulness_model: str | None = None
    lake: Path = Path(DEFAULT_LAKE)
    elan: Path = Path(DEFAULT_ELAN)
    tectonic: Path = Path(DEFAULT_TECTONIC)
    tectonic_bundle: str = DEFAULT_TECTONIC_BUNDLE
    tectonic_bundle_sha256: str = DEFAULT_TECTONIC_BUNDLE_SHA256
    # Which transport carries the conversation, and with it who owns the turn
    # loop. `claude` authenticates through the Claude Code agent SDK, needs no
    # API key, and leaves the loop to the SDK (issue #23). `api` calls the
    # Messages API directly with `ANTHROPIC_API_KEY` and runs the loop here,
    # which is what makes Hardy's own turn bound, its wall clock and its cheap
    # closers real rather than declared. They are different experimental
    # conditions and both are recorded as such.
    backend: str = DEFAULT_BACKEND
    # See DEFAULT_CONTEXT_WINDOW: what compaction plans against, in tokens.
    context_window: int = DEFAULT_CONTEXT_WINDOW
    provider_budget: SpendPolicy | None = None
    # Concurrent background workers a session may run; see DEFAULT_DELEGATION_WORKERS.
    delegation_workers: int = DEFAULT_DELEGATION_WORKERS
    # See DEFAULT_COMPUTE_DETACH_SECONDS: the grace a computation gets before
    # it is detached from the turn into a background job.
    compute_detach_seconds: float = DEFAULT_COMPUTE_DETACH_SECONDS
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
        return layout.Layout(root=self.root, slug=self.project, chat=self.chat)


def read_file(path: Path) -> dict[str, Any]:
    """Read one config file, rejecting keys Hardy does not understand.

    Read as `utf-8-sig` because Windows editors and PowerShell write UTF-8 with
    a byte-order mark: read as plain utf-8 the mark joins the first key and
    tomllib rejects a file that looks perfectly ordinary on screen. Writing
    stays plain utf-8, so saving a setting also drops the mark.
    """
    if not path.exists():
        return {}
    values = _parse_toml(path.read_text(encoding="utf-8-sig"), path)
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


def _whole_workers(raw: Any) -> int:
    """A worker count is a whole number from every source: TOML `3.9` is refused like `"3.9"`."""
    if isinstance(raw, bool):
        raise ValueError(f"delegation_workers must be a number of workers, not {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        raise ValueError(f"delegation_workers must be a whole number of workers, not {raw!r}")
    text = str(raw).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        float(text)
    except ValueError:
        raise ValueError(f"delegation_workers must be a number of workers, not {raw!r}") from None
    raise ValueError(f"delegation_workers must be a whole number of workers, not {raw!r}")


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
    # Where each value came from, as a phrase for a refusal to name, and
    # whether TOML parsed it (and so could have turned a backslash into a
    # control character). Kept beside `values` and overwritten with it, so a
    # message blames the layer that actually won.
    origins: dict[str, tuple[str, bool]] = {key: (f"{key} in {path}", True) for key in values}

    # The root is resolved before the project layer is located, because the
    # project layer lives inside it. Reading the environment afterwards would
    # make HARDY_ROOT advertised and inert: Hardy would take the project config
    # from the current directory and open the wrong problem there.
    def _root_from(value: Any, where: str, *, from_toml: bool) -> Path | None:
        if not value:
            return None
        return Path(_reject_control_characters(where, str(value), from_toml=from_toml)).expanduser()

    resolved_root = (
        _root_from(root, "root given on the command line", from_toml=False)
        or _root_from(
            os.environ.get("HARDY_ROOT"), "root from the environment variable HARDY_ROOT", from_toml=False
        )
        or _root_from(values.get("root"), f"root in {path}", from_toml=True)
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
    origins.update({key: (f"{key} in {project_path}", True) for key in project_values})
    for key, variable in SETTINGS.items():
        value = os.environ.get(variable)
        if value:
            values[key] = value
            origins[key] = (f"{key} from the environment variable {variable}", False)
    for key, value in overrides.items():
        if value is not None:
            values[key] = value
            origins[key] = (f"{key} given on the command line", False)

    def checked(key: str, value: str, *, element: int | None = None) -> str:
        """`value` for `key`, refused if it holds a control character."""
        where, from_toml = origins.get(key, (key, False))
        if element is not None:
            where = f"{where} (argument {element + 1})"
        return _reject_control_characters(where, value, from_toml=from_toml)

    def text(key: str, default: str) -> str:
        return str(values.get(key) or default)

    def location(key: str) -> Path | None:
        value = values.get(key)
        if not value:
            return None
        return Path(checked(key, str(value))).expanduser()

    def command(key: str, default: str) -> tuple[str, ...]:
        """Argv for a command setting: a TOML list verbatim, or checked and split text.

        An absent setting takes the default. One that is present but empty --
        `[]`, `[""]`, or a blank value given on the command line -- is refused
        by `split_command` rather than quietly defaulted: it was written, so it
        was meant. (A blank value in a file never gets here: `read_file` reads
        it as unset.)
        """
        if key not in values:
            return split_command(default, setting=key)
        value = values[key]
        if isinstance(value, list):
            value = [
                checked(key, item, element=index) if isinstance(item, str) else item
                for index, item in enumerate(value)
            ]
        else:
            value = checked(key, str(value))
        return split_command(value, setting=origins.get(key, (key, False))[0])

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

    try:
        context_window = int(values.get("context_window", DEFAULT_CONTEXT_WINDOW))
    except (TypeError, ValueError):
        raise ValueError(f"context_window must be a number of tokens, not {values['context_window']!r}") from None
    # A window no request could fit inside is a typo, not a preference, and a
    # compactor told to plan against it would cut every conversation to
    # nothing while still overflowing. Refused where the file is read.
    #
    # The floor is the transport's output cap rather than zero: the reserve is
    # never smaller than what the model may write, so a window at or below the
    # cap leaves nothing at all for the request -- the planner would report
    # zero available space and the loop would send the request anyway. A
    # configuration that cannot hold an answer is not a small window, it is a
    # wrong one, and the place to say so is where the file is read.
    if context_window <= MINIMUM_CONTEXT_WINDOW:
        raise ValueError(
            f"context_window must leave room for a reply: more than "
            f"{MINIMUM_CONTEXT_WINDOW} tokens, not {context_window}"
        )

    delegation_workers = _whole_workers(values.get("delegation_workers", DEFAULT_DELEGATION_WORKERS))
    if delegation_workers < 1:
        raise ValueError(f"delegation_workers must be at least 1, not {delegation_workers}")

    raw_detach = values.get("compute_detach_seconds", DEFAULT_COMPUTE_DETACH_SECONDS)
    try:
        compute_detach_seconds = float(raw_detach)
    except (TypeError, ValueError):
        raise ValueError(f"compute_detach_seconds must be a number of seconds, not {raw_detach!r}") from None
    if compute_detach_seconds != compute_detach_seconds or compute_detach_seconds < 0 or compute_detach_seconds == float("inf"):
        raise ValueError(f"compute_detach_seconds must be zero or a finite number of seconds, not {raw_detach!r}")

    backend = text("backend", DEFAULT_BACKEND)
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {list(BACKENDS)}, not {backend!r}")
    provider_budget = None
    if values.get("provider_budget"):
        budget_path = Path(checked("provider_budget", str(values["provider_budget"]))).expanduser()
        if not budget_path.is_absolute():
            budget_path = path.parent / budget_path
        provider_budget = SpendPolicy.model_validate_json(budget_path.read_text(encoding="utf-8"))
        if backend != "api":
            raise ValueError("provider budgets require the harness-owned API backend")
    cas_backend = text("cas_backend", DEFAULT_CAS_BACKEND)
    # Rejected here rather than at first use: an unknown backend is a typo in a
    # config file, and the place to say so is where the file is read.
    if cas_backend not in CAS_BACKENDS:
        raise ValueError(f"cas_backend must be one of {list(CAS_BACKENDS)}, not {cas_backend!r}")

    return Config(
        model=str(values["model"]) if values.get("model") else DEFAULT_MODEL,
        lean_command=command("lean_command", DEFAULT_LEAN_COMMAND),
        lean_project=location("lean_project"),
        lean_timeout=lean_timeout,
        latex_command=command("latex_command", DEFAULT_LATEX_COMMAND),
        root=resolved_root,
        # `choose` reaches here rather than the caller asking first because
        # the root the question is about is resolved in this function, from
        # three layers the caller does not otherwise take apart.
        project=active_project(resolved_root, project, values, choose),
        runs_root=location("runs_root") or Path(DEFAULT_RUNS_ROOT),
        faithfulness_model=(
            str(values["faithfulness_model"]) if values.get("faithfulness_model") else None
        ),
        lake=location("lake") or Path(DEFAULT_LAKE),
        elan=location("elan") or Path(DEFAULT_ELAN),
        tectonic=location("tectonic") or Path(DEFAULT_TECTONIC),
        tectonic_bundle=text("tectonic_bundle", DEFAULT_TECTONIC_BUNDLE),
        tectonic_bundle_sha256=text("tectonic_bundle_sha256", DEFAULT_TECTONIC_BUNDLE_SHA256),
        backend=backend,
        cas_backend=cas_backend,
        cas_command=location("cas_command"),
        project_context=flag("project_context", True),
        context_window=context_window,
        delegation_workers=delegation_workers,
        compute_detach_seconds=compute_detach_seconds,
        provider_budget=provider_budget,
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

    Parsed and re-serialized rather than line-edited, for `migrate_global`'s
    reason and in the same place it applies: TOML's grammar is not
    line-oriented, so a triple-quoted `project` with the value and the closing
    delimiter on following lines is one valid assignment that `read_file`
    resolves to an ordinary slug. Replacing the line it starts on leaves the
    continuation behind -- `project = "burnside"` followed by an orphaned
    `sylow` and a stray delimiter -- which is a file `tomllib` refuses, so the
    first switch in such a checkout would brick every launch after it.
    Reproduced before this was written; only a real parse can tell where a
    value ends.

    Every key the file held is written back, not only the ones this layer may
    set: `load` reports an unpermitted key and ignores it, and quietly
    deleting it on the way past would be a different thing entirely. Comments
    and ordering are what a parse costs, and this file is Hardy's own two-line
    header plus one setting -- unlike the user's config, which `write_setting`
    keeps line-based precisely because a human arranges it.

    And a value this function cannot write back UNCHANGED stops the write
    rather than being mangled by it. `_render_toml_line` renders scalars; a
    list or a table reached it as `str(value)` and came back a quoted Python
    repr, so `model = ["a", "b"]` became `model = "[\'a\', \'b\']"` and a
    `[tectonic]` table became a string -- silently, in a tracked file, as the
    side effect of switching projects. Preserving keys was the whole point of
    parsing rather than line-editing, and that is not preservation. Refusing
    is: `_remember` reports it and the switch stands, so the cost is a
    recorded preference rather than anybody's data.
    """
    if key not in PROJECT_SETTINGS:
        raise ValueError(
            f"{key!r} may not be set from a project config; this layer may only set: "
            f"{', '.join(sorted(PROJECT_SETTINGS))}"
        )
    guard = layout.WriteGuard(root / layout.HARDY_DIR, create=True)
    filename = "config.toml"
    try:
        with guard.open(filename, encoding="utf-8-sig") as handle:
            values = _parse_toml(handle.read(), guard.path(filename))
    except FileNotFoundError:
        values = {}
    values[key] = value
    unwritable = sorted(
        setting for setting, held in values.items()
        if not isinstance(held, (str, int, float, bool))
    )
    if unwritable:
        described = ", ".join(f"{setting} ({type(values[setting]).__name__})" for setting in unwritable)
        raise ValueError(
            f"{guard.path(filename)} holds values this layer cannot rewrite without changing "
            f"them: {described}. Remove them to let the active project be recorded here."
        )
    lines = [*PROJECT_HEADER, *(_render_toml_line(setting, held) for setting, held in values.items())]
    guard.write_bytes(filename, ("\n".join(lines) + "\n").encode("utf-8"))


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
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    temporary.chmod(0o600)
    replace_with_retry(temporary, path)
