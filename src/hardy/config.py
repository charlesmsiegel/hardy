from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import catalog

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_LEAN_COMMAND = "lake env lean"
# Importing Mathlib costs tens of seconds on a cold machine, so the default is
# generous; a fast environment simply never reaches it.
DEFAULT_LEAN_TIMEOUT = 180.0
DEFAULT_LATEX_COMMAND = "pdflatex -interaction=nonstopmode -halt-on-error"
DEFAULT_WORKSPACE = ".hardy"

# Every setting, and the environment variable that overrides the config file.
SETTINGS = {
    "model": "HARDY_MODEL",
    "lean_command": "HARDY_LEAN_COMMAND",
    "lean_project": "HARDY_LEAN_PROJECT",
    "lean_timeout": "HARDY_LEAN_TIMEOUT",
    "latex_command": "HARDY_LATEX_COMMAND",
    "workspace": "HARDY_WORKSPACE",
}


def default_config_path() -> Path:
    """The config file Hardy reads when no path is given."""
    override = os.environ.get("HARDY_CONFIG")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "hardy" / "config.toml"
    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(home) / "hardy" / "config.toml"


@dataclass(frozen=True)
class Config:
    """Resolved settings. Later sources win: file, then environment, then flags."""

    model: str | None
    lean_command: tuple[str, ...]
    lean_project: Path | None
    lean_timeout: float
    latex_command: tuple[str, ...]
    workspace: Path
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


def read_file(path: Path) -> dict[str, Any]:
    """Read one config file, rejecting keys Hardy does not understand."""
    if not path.exists():
        return {}
    values = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(values) - set(SETTINGS))
    if unknown:
        raise ValueError(f"{path}: unknown settings {unknown}; known settings are {sorted(SETTINGS)}")
    return {key: value for key, value in values.items() if str(value).strip() != ""}


def load(path: Path | None = None, **overrides: Any) -> Config:
    """Resolve configuration from the config file, environment, and CLI flags."""
    path = path or default_config_path()
    values: dict[str, Any] = read_file(path)
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

    return Config(
        model=str(values["model"]) if values.get("model") else DEFAULT_MODEL,
        lean_command=tuple(shlex.split(text("lean_command", DEFAULT_LEAN_COMMAND))),
        lean_project=location("lean_project"),
        lean_timeout=lean_timeout,
        latex_command=tuple(shlex.split(text("latex_command", DEFAULT_LATEX_COMMAND))),
        workspace=location("workspace") or Path(DEFAULT_WORKSPACE),
        path=path if path.exists() else None,
        requested_path=path,
    )


def write_setting(path: Path, key: str, value: str) -> None:
    """Upsert one setting in a config file, leaving every other line alone.

    Line-based rather than a parse-and-rewrite so the installer's comments and
    any hand-written ordering survive; the file is often edited by a human.
    """
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}; known settings are {sorted(SETTINGS)}")
    header = ["# Written by Hardy. Every value can be overridden by a", "# HARDY_* environment variable or a command-line flag."]
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else list(header)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    rendered = f'{key} = "{escaped}"'
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            break
    else:
        lines.append(rendered)
    _rewrite(path, lines)


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
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not pattern.match(line)]
    if len(kept) != len(lines):
        _rewrite(path, kept)


def _rewrite(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
