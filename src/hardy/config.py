from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_LEAN_COMMAND = "lake env lean"
# Importing Mathlib costs tens of seconds on a cold machine, so the default is
# generous; a fast environment simply never reaches it.
DEFAULT_LEAN_TIMEOUT = 180.0
DEFAULT_LATEX_COMMAND = "pdflatex -interaction=nonstopmode -halt-on-error"
DEFAULT_WORKSPACE = ".hardy"

# Every setting, and the environment variable that overrides the config file.
SETTINGS = {
    "model": "HARDY_MODEL",
    "base_url": "HARDY_BASE_URL",
    "api_key": "HARDY_API_KEY",
    "api_key_env": "HARDY_API_KEY_ENV",
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
    base_url: str
    api_key: str
    api_key_env: str
    lean_command: tuple[str, ...]
    lean_project: Path | None
    lean_timeout: float
    latex_command: tuple[str, ...]
    workspace: Path
    path: Path | None = None

    def resolved_api_key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env, "")


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
        model=str(values["model"]) if values.get("model") else None,
        base_url=text("base_url", DEFAULT_BASE_URL),
        api_key=text("api_key", ""),
        api_key_env=text("api_key_env", DEFAULT_API_KEY_ENV),
        lean_command=tuple(shlex.split(text("lean_command", DEFAULT_LEAN_COMMAND))),
        lean_project=location("lean_project"),
        lean_timeout=lean_timeout,
        latex_command=tuple(shlex.split(text("latex_command", DEFAULT_LATEX_COMMAND))),
        workspace=location("workspace") or Path(DEFAULT_WORKSPACE),
        path=path if path.exists() else None,
    )
