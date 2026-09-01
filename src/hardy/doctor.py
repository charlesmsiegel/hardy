from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CAS_BACKEND, Config
from .lean import LeanTools
from .models import Request
from .process import run_guarded

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
        # Guarded like every other child: `/doctor` runs on a worker so the
        # terminal stays live, and a probe that could not be reached by Esc
        # would leave the user watching a 120-second check they had asked to
        # stop.
        process = run_guarded(command, cwd=cwd, timeout=timeout)
        if process.interrupted:
            return False, "interrupted"
        if process.timed_out:
            return False, f"timed out after {timeout:.0f}s"
    except FileNotFoundError:
        return False, f"{command[0]} not found on PATH"
    except NotADirectoryError:
        return False, f"working directory not usable: {cwd}"
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


def _toolchain_pin_check(config: Config) -> Check:
    """Whether the project is the one Hardy pins, said rather than assumed.

    Advisory, not required: running against a project someone deliberately
    repinned is supported, and every result records the identity it actually
    ran against. What is not supported is not knowing -- so a project on a
    different Lean or Mathlib is named here beside what Hardy pins.
    """
    from .installers import LEAN_TOOLCHAIN, MATHLIB_REVISION

    project = config.lean_project
    if project is None or not project.is_dir():
        return Check("toolchain pin", True, "no configured project to compare against Hardy's pins", required=False)
    drift = []
    pin = project / "lean-toolchain"
    pinned = pin.read_text(encoding="utf-8").strip() if pin.is_file() else None
    if pinned != LEAN_TOOLCHAIN:
        drift.append(f"lean-toolchain is {pinned!r}, Hardy pins {LEAN_TOOLCHAIN!r}")
    manifest = project / "lake-manifest.json"
    requested = None
    if manifest.is_file():
        try:
            packages = json.loads(manifest.read_text(encoding="utf-8")).get("packages", [])
            requested = next((item.get("inputRev") for item in packages if item.get("name") == "mathlib"), None)
        except (ValueError, AttributeError):
            requested = None
    if requested != MATHLIB_REVISION:
        drift.append(f"Mathlib is required at {requested!r}, Hardy pins {MATHLIB_REVISION!r}")
    if drift:
        return Check("toolchain pin", False, "; ".join(drift) + " (results record what actually ran)", required=False)
    return Check("toolchain pin", True, f"{LEAN_TOOLCHAIN} with Mathlib {MATHLIB_REVISION}", required=False)


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
        finished = run_guarded([cli, "auth", "status"], timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return Check("claude login", False, f"could not run {cli}: {error}")
    if finished.interrupted or finished.timed_out:
        return Check("claude login", False, f"{cli} auth status did not finish")
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


def _cas_check(config: Config) -> Check:
    """Start the configured kernel and ask it what it is.

    Required only when a non-default backend was named. Asking for Singular and
    not having it is a broken machine; not having installed anything is the
    ordinary case, and SymPy is a dependency, so its absence is a warning.
    """
    import tempfile

    from .cas_tools import build_runtime

    required = config.cas_backend != DEFAULT_CAS_BACKEND
    with tempfile.TemporaryDirectory(prefix="hardy-cas-") as directory:
        runtime, detail = build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=Path(directory) / "cells.jsonl",
            cwd=Path(directory),
        )
        if runtime is not None:
            runtime.session.close()
    return Check("cas", runtime is not None, detail, required=required)


def run_checks(config: Config, *, deep: bool = False) -> list[Check]:
    """Report whether this machine can actually run an interactive Hardy session."""
    checks = [
        Check("python", sys.version_info >= (3, 11), f"{sys.version.split()[0]} at {sys.executable}"),
        _lean_project_check(config),
        _toolchain_pin_check(config),
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

    checks.append(_cas_check(config))
    checks.append(Check("model", bool(config.model), config.model or "unset; set model in the config file or HARDY_MODEL"))
    checks.extend(_subscription_checks())

    if deep:
        checks.append(_mathlib_check(config))
    return checks


def describe(checks: list[Check]) -> list[str]:
    """The report as lines, so a caller that is not a terminal can render it."""
    return [check.line() for check in checks]


def report(checks: list[Check]) -> int:
    for line in describe(checks):
        print(line)
    failures = [check for check in checks if check.required and not check.ok]
    print("\nHardy is ready." if not failures else f"\n{len(failures)} required check(s) failed; Hardy will not work until they are fixed.")
    return 1 if failures else 0
