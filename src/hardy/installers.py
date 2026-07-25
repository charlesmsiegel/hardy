"""Fixed, confirmed installation recipes for Hardy's pinned tools.

Every recipe asks before it acts and pins what it fetches. The Tectonic
download is checked against a recorded digest before anything is unpacked, and
a mismatch installs nothing at all rather than leaving a half-trusted binary
on disk. These are the Windows recipes; the shell installers cover Linux and
macOS, and neither requires WSL.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .domain import FrozenModel
from .process import ProcessResult, ProcessSpec

ELAN_VERSION = "4.2.1"
TECTONIC_VERSION = "0.16.9"
TECTONIC_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    "tectonic%400.16.9/tectonic-0.16.9-x86_64-pc-windows-msvc.zip"
)
TECTONIC_SHA256 = "131a24604785a9600989a3d91225f597df52ac06f00aeffe86fd529f99ee5cdd"


class InstallOutcome(FrozenModel):
    status: str
    manual_instructions: str
    installed_path: Path | None = None


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Hardy/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def prepare_mathlib(
    *,
    lake: Path,
    lean_project: Path,
    confirmer: Callable[[str], bool],
    runner: Callable[[ProcessSpec], ProcessResult],
) -> InstallOutcome:
    prompt = f"Resolve the pinned Mathlib project and cache under {lean_project}?"
    if not confirmer(prompt):
        return InstallOutcome(
            status="declined",
            manual_instructions="Run `hardy setup` again when ready to prepare Mathlib.",
        )
    for argv, timeout in (
        ((str(lake), "update"), 120),
        ((str(lake), "exe", "cache", "get"), 600),
    ):
        result = runner(
            ProcessSpec(
                argv=argv,
                cwd=lean_project,
                timeout_seconds=timeout,
                max_output_bytes=4 * 1024 * 1024,
            )
        )
        if result.returncode != 0 or result.timed_out or result.output_overflow:
            return InstallOutcome(
                status="failed",
                manual_instructions="Mathlib setup failed; inspect the process log and retry.",
            )
    return InstallOutcome(
        status="installed",
        manual_instructions="Pinned Mathlib dependencies and cache are ready.",
    )


def install_elan(
    *,
    winget: Path,
    cwd: Path,
    confirmer: Callable[[str], bool],
    runner: Callable[[ProcessSpec], ProcessResult],
) -> InstallOutcome:
    prompt = f"Install Lean.Elan {ELAN_VERSION} for the current user with winget?"
    if not confirmer(prompt):
        return InstallOutcome(
            status="declined",
            manual_instructions="Install Elan manually, then resume with `hardy setup`.",
        )
    argv = (
        str(winget),
        "install",
        "--id",
        "Lean.Elan",
        "--version",
        ELAN_VERSION,
        "--exact",
        "--scope",
        "user",
        "--disable-interactivity",
        "--accept-package-agreements",
        "--accept-source-agreements",
    )
    result = runner(
        ProcessSpec(
            argv=argv,
            cwd=cwd,
            timeout_seconds=300,
            max_output_bytes=4 * 1024 * 1024,
        )
    )
    if result.returncode == 0 and not result.timed_out and not result.output_overflow:
        return InstallOutcome(
            status="installed",
            manual_instructions="Elan installed; `hardy setup` will now rediscover it.",
        )
    return InstallOutcome(
        status="failed",
        manual_instructions=(
            "Winget did not install Elan; install it manually and rerun `hardy setup`."
        ),
    )


def install_tectonic(
    *,
    destination_root: Path,
    confirmer: Callable[[str], bool],
    downloader: Callable[[str, Path], None],
) -> InstallOutcome:
    destination = destination_root / "tectonic" / TECTONIC_VERSION / "tectonic.exe"
    prompt = f"Download Tectonic {TECTONIC_VERSION} from {TECTONIC_URL} to {destination}?"
    if not confirmer(prompt):
        return InstallOutcome(
            status="declined",
            manual_instructions="Install Tectonic manually, then resume with `hardy setup`.",
        )
    destination_root.mkdir(parents=True, exist_ok=True)
    # Staged in a temporary directory so a failed digest check leaves nothing
    # behind that a later run could mistake for a verified install.
    with tempfile.TemporaryDirectory(prefix="hardy-tectonic-", dir=destination_root) as staged:
        archive = Path(staged) / "tectonic.zip"
        downloader(TECTONIC_URL, archive)
        if _sha256(archive) != TECTONIC_SHA256:
            return InstallOutcome(
                status="failed",
                manual_instructions=(
                    "Downloaded Tectonic failed its pinned checksum. "
                    "Nothing was installed; rerun `hardy setup` to retry."
                ),
            )
        staged_executable = Path(staged) / "tectonic.exe"
        with zipfile.ZipFile(archive) as bundle:
            info = bundle.getinfo("tectonic.exe")
            if info.is_dir():
                raise ValueError("pinned Tectonic archive has no executable file")
            with bundle.open(info) as source, staged_executable.open("wb") as target:
                shutil.copyfileobj(source, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_executable, destination)
        return InstallOutcome(
            status="installed",
            manual_instructions="Tectonic installed; `hardy setup` will now run smoke tests.",
            installed_path=destination,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
