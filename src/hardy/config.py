from __future__ import annotations

import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import catalog

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_LEAN_COMMAND = "lake env lean"
# Importing Mathlib costs tens of seconds on a cold machine, so the default is
# generous; a fast environment simply never reaches it.
DEFAULT_LEAN_TIMEOUT = 180.0
DEFAULT_LATEX_COMMAND = "pdflatex -interaction=nonstopmode -halt-on-error"
DEFAULT_WORKSPACE = ".hardy"

# Every setting, and the environment variable that overrides the config file.
SETTINGS = {
    "model": "HARDY_MODEL",
    "backend": "HARDY_BACKEND",
    "base_url": "HARDY_BASE_URL",
    "api_key": "HARDY_API_KEY",
    "api_key_env": "HARDY_API_KEY_ENV",
    "anthropic_api_key": "HARDY_ANTHROPIC_API_KEY",
    "anthropic_api_key_env": "HARDY_ANTHROPIC_API_KEY_ENV",
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
    backend: str | None = None
    anthropic_api_key: str = ""
    anthropic_api_key_env: str = DEFAULT_ANTHROPIC_API_KEY_ENV
    path: Path | None = None

    def active_backend(self) -> str:
        """Which provider the configured model implies, unless one is pinned."""
        return self.backend or catalog.backend_for(self.model)

    def _credentials(self, backend: str) -> tuple[str, str]:
        if backend == catalog.ANTHROPIC:
            return self.anthropic_api_key, self.anthropic_api_key_env
        return self.api_key, self.api_key_env

    def resolved_api_key(self, backend: str | None = None) -> str:
        literal, variable = self._credentials(backend or self.active_backend())
        return literal or os.environ.get(variable, "")

    def key_source(self, backend: str | None = None) -> str:
        literal, variable = self._credentials(backend or self.active_backend())
        return "config file" if literal else f"${variable}"

    def requires_api_key(self, backend: str | None = None) -> bool:
        """Whether a missing key for this backend is certainly fatal.

        Anthropic always authenticates, and so does anything reached over the
        network — a custom `base_url` is far more often a hosted gateway that
        needs a key than one that does not. Only a self-hosted endpoint gets the
        benefit of the doubt, because llama.cpp and vLLM ship with no auth at
        all and demanding a key there is a false alarm.
        """
        backend = backend or self.active_backend()
        return backend == catalog.ANTHROPIC or not catalog.is_local_endpoint(self.base_url)

    def base_url_for(self, backend: str | None = None) -> str:
        """`base_url` configures the OpenAI-compatible endpoint only.

        The Anthropic SDK resolves its own endpoint, so pointing `base_url` at a
        local server must not accidentally redirect Claude traffic there.
        """
        backend = backend or self.active_backend()
        return "" if backend == catalog.ANTHROPIC else self.base_url


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

    backend = str(values["backend"]).strip().lower() if values.get("backend") else None
    if backend is not None and backend not in catalog.BACKENDS:
        raise ValueError(f"backend must be one of {list(catalog.BACKENDS)}, not {backend!r}")

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
        backend=backend,
        anthropic_api_key=text("anthropic_api_key", ""),
        anthropic_api_key_env=text("anthropic_api_key_env", DEFAULT_ANTHROPIC_API_KEY_ENV),
        path=path if path.exists() else None,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
