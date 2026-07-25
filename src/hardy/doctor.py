from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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


def _subscription_checks() -> list[Check]:
    """Hardy authenticates as Claude Code does, so this is what it needs.

    No API key is involved: the credentials belong to the signed-in CLI, which
    is the whole point of running against a subscription.
    """
    checks = []
    try:
        import claude_agent_sdk
    except ImportError:
        checks.append(Check("claude sdk", False, "not installed; pip install claude-agent-sdk"))
    else:
        checks.append(Check("claude sdk", True, f"claude-agent-sdk {getattr(claude_agent_sdk, '__version__', 'unknown')}"))
    cli = shutil.which("claude")
    if not cli:
        checks.append(Check("claude cli", False, "not on PATH; npm install -g @anthropic-ai/claude-code"))
        return checks
    checks.append(Check("claude cli", True, cli))
    checks.append(_login_check(cli))
    return checks


def _login_check(cli: str) -> Check:
    """Whether the subscription is actually signed in.

    `--version` would prove only that the executable exists, and reporting that
    as authentication would let doctor call a logged-out machine ready — the
    failure would then arrive on the first model call instead of here.
    """
    try:
        finished = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return Check("claude login", False, f"could not run {cli}: {error}")
    try:
        status = json.loads(finished.stdout)
    except (json.JSONDecodeError, TypeError):
        return Check("claude login", False, "could not read `claude auth status`; run `claude login`")
    if not status.get("loggedIn"):
        return Check("claude login", False, "not signed in; run `claude login` to use your subscription")
    return Check("claude login", True, f"signed in via {status.get('authMethod', 'unknown method')}")


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

    checks.append(Check("model", bool(config.model), config.model or "unset; set model in the config file or HARDY_MODEL"))
    checks.extend(_subscription_checks())

    if deep:
        checks.append(_mathlib_check(config))
    return checks


def report(checks: list[Check]) -> int:
    for check in checks:
        print(check.line())
    failures = [check for check in checks if check.required and not check.ok]
    print("\nHardy is ready." if not failures else f"\n{len(failures)} required check(s) failed; Hardy will not work until they are fixed.")
    return 1 if failures else 0
