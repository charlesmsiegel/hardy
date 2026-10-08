"""`hardy setup`: discover, install and record the pinned toolchain."""
from __future__ import annotations

import argparse
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hardy.app import config as configuration
from hardy.app.commands.settings import _load_config_argument


def _common_locations() -> dict[str, tuple[Path, ...]]:
    """Where these tools land when their own installers put them there."""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elan_bin = Path.home() / ".elan" / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return {
        "elan": (elan_bin / f"elan{suffix}",),
        "lake": (elan_bin / f"lake{suffix}",),
        "tectonic": (local / "Hardy" / "tools" / "tectonic" / "0.16.9" / "tectonic.exe",),
    }


def _print_report(report: Any) -> None:
    for tool in report.tools:
        state = "OK" if tool.healthy else "MISSING/FAILED"
        location = str(tool.path) if tool.path else "not registered"
        version = tool.version or "unknown version"
        print(f"{tool.name:9} {state:14} {version} [{location}] - {tool.detail}")
    print(f"mathlib   {'OK' if report.mathlib_ready else 'MISSING/FAILED'}")


def _confirm(prompt: str) -> bool:
    return input(prompt + " [y/N] ").strip().lower() in {"y", "yes"}


def run_setup(args: argparse.Namespace, *, confirmer: Callable[[str], bool] = _confirm) -> int:
    """Discover the pinned toolchain, offer to install what is missing, record it."""
    from hardy.app.installers import (
        create_lean_project,
        download_file,
        install_elan,
        install_tectonic,
        prepare_mathlib,
    )
    from hardy.app.setup import backend_probe, discover_environment
    from hardy.foundation.paths import shared_lean_project
    from hardy.foundation.process import run_process

    config, config_path = _load_config_argument(getattr(args, "config", None))
    # The probe for the backend this machine is configured to use. Left to the
    # default, `hardy setup` graded every machine on the Claude CLI and its
    # exit status answered a question about a transport the user may not have
    # selected.
    probe = backend_probe(config.backend)
    report = discover_environment(config, backend_probe=probe, common_locations=_common_locations())
    statuses = {item.name: item for item in report.tools}
    if not statuses["elan"].healthy:
        winget = shutil.which("winget")
        if winget:
            print(
                install_elan(
                    winget=Path(winget),
                    cwd=Path.cwd(),
                    confirmer=confirmer,
                    runner=run_process,
                ).manual_instructions
            )
        else:
            print(
                "elan was not found. Install it with the platform installer under scripts/, "
                "then rerun `hardy setup`."
            )
    if not statuses["tectonic"].healthy:
        if os.name == "nt":
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            print(
                install_tectonic(
                    destination_root=local / "Hardy" / "tools",
                    confirmer=confirmer,
                    downloader=download_file,
                ).manual_instructions
            )
        else:
            print(
                "tectonic was not found. Install it from your package manager or "
                "https://tectonic-typesetting.github.io, then rerun `hardy setup`."
            )
    rediscovered = discover_environment(config, backend_probe=probe, common_locations=_common_locations())
    tools = {item.name: item for item in rediscovered.tools}
    for setting in ("elan", "lake", "tectonic"):
        found = tools[setting].path
        if found is not None:
            configuration.write_setting(config_path, setting, str(found))
    if tools["lake"].path is not None and config.lean_project is None:
        # The installers' location, never the working directory: run from a
        # source checkout, "here" is how a multi-gigabyte Mathlib tree ended
        # up inside the repository.
        created = create_lean_project(lean_project=shared_lean_project(), confirmer=confirmer)
        print(created.manual_instructions)
        if created.installed_path is not None:
            configuration.write_setting(config_path, "lean_project", str(created.installed_path))
            config = configuration.load(config_path)
            rediscovered = discover_environment(config, backend_probe=probe, common_locations=_common_locations())
    if tools["lake"].path is not None and config.lean_project and not rediscovered.mathlib_ready:
        print(
            prepare_mathlib(
                lake=tools["lake"].path,
                lean_project=config.lean_project,
                confirmer=confirmer,
                runner=run_process,
            ).manual_instructions
        )
    reloaded = configuration.load(config_path)
    final = discover_environment(
        reloaded, backend_probe=backend_probe(reloaded.backend), common_locations=_common_locations()
    )
    _print_report(final)
    print(f"Configuration saved to {config_path}")
    return 0 if final.healthy else 1
