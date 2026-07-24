from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import catalog
from .config import Config
from .lean import LeanTools
from .models import Request

MATHLIB_PROBE = "import Mathlib\n\nexample : 2 + 2 = 4 := by norm_num\n"
LAKEFILES = ("lakefile.toml", "lakefile.lean")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True

    def line(self) -> str:
        mark = "ok  " if self.ok else ("FAIL" if self.required else "warn")
        return f"[{mark}] {self.name}: {self.detail}"


def _probe(command: list[str], *, cwd: Path | None = None, timeout: float = 120) -> tuple[bool, str]:
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        return False, f"{command[0]} not found on PATH"
    except NotADirectoryError:
        return False, f"working directory not usable: {cwd}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f}s"
    output = (process.stdout + process.stderr).strip().splitlines()
    detail = output[0] if output else f"exit={process.returncode}"
    return process.returncode == 0, detail[:200]


def _lean_project_check(config: Config) -> Check:
    project = config.lean_project
    if project is None:
        return Check("lean project", True, "not configured; Lean runs in the current directory", required=False)
    if not project.is_dir():
        return Check("lean project", False, f"missing directory {project}")
    if not any((project / name).exists() for name in LAKEFILES):
        return Check("lean project", False, f"{project} has no lakefile.toml or lakefile.lean")
    return Check("lean project", True, str(project))


def _anthropic_package_check() -> Check:
    try:
        import anthropic
    except ImportError:
        return Check("anthropic sdk", False, "not installed; the Claude backend needs it: pip install anthropic")
    return Check("anthropic sdk", True, f"anthropic {getattr(anthropic, '__version__', 'unknown')}")


def _mathlib_check(config: Config) -> Check:
    request = Request("example : True", "doctor probe", ("Mathlib",))
    lean = LeanTools(request, config.lean_command, timeout=max(config.lean_timeout, 900), project=config.lean_project)
    result = lean.run_source(MATHLIB_PROBE)
    detail = "import Mathlib and norm_num succeeded" if result.ok else result.output.strip().splitlines()[-1][:200]
    return Check("mathlib", result.ok, detail)


def run_checks(config: Config, *, deep: bool = False) -> list[Check]:
    """Report whether this machine can actually run an interactive Hardy session."""
    checks = [
        Check("python", sys.version_info >= (3, 11), f"{sys.version.split()[0]} at {sys.executable}"),
        _lean_project_check(config),
    ]

    lean_executable = config.lean_command[0]
    if shutil.which(lean_executable) is None:
        checks.append(Check("lean", False, f"{lean_executable} not found on PATH; install elan (see scripts/install.sh)"))
    else:
        ok, detail = _probe([*config.lean_command, "--version"], cwd=config.lean_project)
        checks.append(Check("lean", ok, detail))

    latex_executable = config.latex_command[0]
    if shutil.which(latex_executable) is None:
        checks.append(Check("latex", False, f"{latex_executable} not found on PATH; install a TeX distribution (see scripts/install.sh)"))
    else:
        ok, detail = _probe([latex_executable, "--version"], timeout=60)
        checks.append(Check("latex", ok, detail))

    backend = config.active_backend()
    checks.append(Check("model", bool(config.model), config.model or "unset; set model in the config file or HARDY_MODEL"))
    checks.append(Check("backend", True, f"{backend} ({'Anthropic Messages API' if backend == catalog.ANTHROPIC else config.base_url})", required=False))
    key = config.resolved_api_key(backend)
    if key:
        checks.append(Check("api key", True, f"present via {config.key_source(backend)}"))
    elif config.requires_api_key(backend):
        checks.append(Check("api key", False, f"unset for the {backend} backend; set it in the config file or export {config.key_source(backend).lstrip('$')}"))
    else:
        # A custom base_url is a deliberate choice, and local servers want no key.
        checks.append(Check("api key", True, f"unset; assuming {config.base_url} needs none", required=False))
    if backend == catalog.ANTHROPIC:
        checks.append(_anthropic_package_check())

    if deep:
        checks.append(_mathlib_check(config))
    return checks


def report(checks: list[Check]) -> int:
    for check in checks:
        print(check.line())
    failures = [check for check in checks if check.required and not check.ok]
    print("\nHardy is ready." if not failures else f"\n{len(failures)} required check(s) failed; Hardy will not work until they are fixed.")
    return 1 if failures else 0
