"""The settings file a command names with `--config`, loaded."""
from __future__ import annotations

from pathlib import Path

from hardy.app import config as configuration


def _load_config_argument(value: str | None) -> tuple[configuration.Config, Path]:
    path = Path(value) if value else configuration.default_config_path()
    settings = configuration.load(path)
    return settings, settings.config_path
