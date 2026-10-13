"""Fixed, confirmed installation recipes for Hardy's pinned tools.

Every recipe asks before it acts and pins what it fetches. The Tectonic
download is checked against a recorded digest before anything is unpacked, and
a mismatch installs nothing at all rather than leaving a half-trusted binary
on disk. Windows uses these recipes directly; the POSIX shell installers opt into a
pinned Linux or macOS Tectonic target. No platform requires WSL.
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from hardy.foundation.locking import replace_with_retry
from hardy.foundation.process import ProcessResult, ProcessSpec
from hardy.foundation.values import FrozenModel

ELAN_VERSION = "4.2.1"
# The Lean and Mathlib every installer pins the shared project to, by identity
# rather than by "whatever Mathlib's master says today". The shell and
# PowerShell installers carry the same two strings, and a test holds all
# three in agreement; a result records what it actually ran against
# (`lean.environment_identity`), so a project someone repinned is reported as
# what it is rather than as this. (Issue #81.)
LEAN_TOOLCHAIN = "leanprover/lean4:v4.35.0-rc3"
MATHLIB_REVISION = "5e0c4e5239cb0a2d86d68a884bf52cfd963fce22"
LEAN_PACKAGE = "hardymath"
TECTONIC_VERSION = "0.16.9"
TECTONIC_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    "tectonic%400.16.9/tectonic-0.16.9-x86_64-pc-windows-msvc.zip"
)
TECTONIC_SHA256 = "131a24604785a9600989a3d91225f597df52ac06f00aeffe86fd529f99ee5cdd"

# POSIX shell installers opt into a target; the default recipe remains Windows.
TECTONIC_POSIX_SHA256 = {
    "aarch64-apple-darwin": "edb67c61aba768289f6da441c9e6f523cfaff4f8b2a5708523ef29c543f8e88e",
    "aarch64-unknown-linux-musl": "f9aa39017dbd51f111fdb93dda222178cbe51c8193508fc567b523cc74fff9c1",
    "x86_64-apple-darwin": "79d8839fa3594bfea9b2bf2ac0a0455bcc4d0de956a5e5c403107e9a72f79e86",
    "x86_64-unknown-linux-musl": "60b13a0826ae7ad9ce34b4a2df06bff2cfcfa6dda8a915477c0cbb84e1a4a902",
}


class InstallOutcome(FrozenModel):
    status: str
    manual_instructions: str
    installed_path: Path | None = None


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Hardy/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output)


def create_lean_project(
    *,
    lean_project: Path,
    confirmer: Callable[[str], bool],
) -> InstallOutcome:
    """The shared Lake project, pinned the way the installers pin it.

    Reuses a directory that is already a Lake project, whatever it pins, the
    way the installers do: a result records the environment it actually ran
    against, so a repinned project is reported as what it is. Refuses a
    directory holding anything else rather than writing a lakefile into it.
    """
    if (lean_project / "lakefile.toml").exists() or (lean_project / "lakefile.lean").exists():
        return InstallOutcome(
            status="present",
            manual_instructions=f"Using the existing Lake project at {lean_project}.",
            installed_path=lean_project,
        )
    if (lean_project.exists() or lean_project.is_symlink()) and not lean_project.is_dir():
        return InstallOutcome(
            status="failed",
            manual_instructions=f"{lean_project} exists and is not a directory; move it aside and rerun `hardy setup`.",
        )
    if lean_project.exists() and any(lean_project.iterdir()):
        return InstallOutcome(
            status="failed",
            manual_instructions=f"{lean_project} exists and is not a Lake project; move it aside and rerun `hardy setup`.",
        )
    prompt = f"Create a Lake project pinned to {LEAN_TOOLCHAIN} and Mathlib {MATHLIB_REVISION} at {lean_project}?"
    if not confirmer(prompt):
        return InstallOutcome(
            status="declined",
            manual_instructions="Set `lean_project` in the config file, or run `hardy setup` again when ready.",
        )
    lean_project.mkdir(parents=True, exist_ok=True)
    (lean_project / "lean-toolchain").write_text(LEAN_TOOLCHAIN + "\n", encoding="utf-8", newline="\n")
    (lean_project / "lakefile.toml").write_text(
        f'name = "{LEAN_PACKAGE}"\n'
        'defaultTargets = ["HardyMath"]\n'
        "\n"
        "[[require]]\n"
        'name = "mathlib"\n'
        'scope = "leanprover-community"\n'
        f'rev = "{MATHLIB_REVISION}"\n'
        "\n"
        "[[lean_lib]]\n"
        'name = "HardyMath"\n',
        encoding="utf-8",
        newline="\n",
    )
    (lean_project / "HardyMath.lean").write_text("import Mathlib\n", encoding="utf-8", newline="\n")
    return InstallOutcome(
        status="installed",
        manual_instructions=f"Created the pinned Lake project at {lean_project}.",
        installed_path=lean_project,
    )


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
    # An hour each: `lake update` on a new project clones Mathlib and its
    # dependencies, and the cache is several gigabytes. The installers set no
    # limit at all; two minutes is not enough for a first clone.
    for argv, timeout in (
        ((str(lake), "update"), 3600),
        ((str(lake), "exe", "cache", "get"), 3600),
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
    target: str | None = None,
) -> InstallOutcome:
    if target is not None and target not in TECTONIC_POSIX_SHA256:
        return InstallOutcome(status="failed", manual_instructions=f"No pinned Tectonic bootstrap for {target}; install it manually.")
    executable = "tectonic" if target else "tectonic.exe"
    url = (f"https://github.com/tectonic-typesetting/tectonic/releases/download/"
           f"tectonic%40{TECTONIC_VERSION}/tectonic-{TECTONIC_VERSION}-{target}.tar.gz") if target else TECTONIC_URL
    destination = destination_root / "tectonic" / TECTONIC_VERSION / executable
    prompt = f"Download Tectonic {TECTONIC_VERSION} from {url} to {destination}?"
    if not confirmer(prompt):
        return InstallOutcome(status="declined", manual_instructions="Install Tectonic manually, then resume with `hardy setup`.")
    destination_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hardy-tectonic-", dir=destination_root) as staged:
        archive = Path(staged) / ("tectonic.tar.gz" if target else "tectonic.zip")
        downloader(url, archive)
        if _sha256(archive) != (TECTONIC_POSIX_SHA256[target] if target else TECTONIC_SHA256):
            return InstallOutcome(status="failed", manual_instructions=(
                "Downloaded Tectonic failed its pinned checksum. Nothing was installed; rerun `hardy setup` to retry."
            ))
        staged_executable = Path(staged) / executable
        if target:
            with tarfile.open(archive, "r:gz") as bundle:
                info = bundle.getmember(executable)
                if not info.isfile():
                    raise ValueError("pinned Tectonic archive has no executable file")
                source = bundle.extractfile(info)
                if source is None:
                    raise ValueError("pinned Tectonic archive has no executable file")
                with source, staged_executable.open("wb") as output:
                    shutil.copyfileobj(source, output)
            staged_executable.chmod(0o755)
        else:
            with zipfile.ZipFile(archive) as bundle:
                info = bundle.getinfo(executable)
                if info.is_dir():
                    raise ValueError("pinned Tectonic archive has no executable file")
                with bundle.open(info) as source, staged_executable.open("wb") as output:
                    shutil.copyfileobj(source, output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        replace_with_retry(staged_executable, destination)
        return InstallOutcome(status="installed", manual_instructions="Tectonic installed; `hardy setup` will now run smoke tests.",
                              installed_path=destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
