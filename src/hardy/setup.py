"""Dependency discovery and smoke tests for a staged run.

Discovery is not enough on its own: a tool that exists on PATH but cannot run,
or a Mathlib that is present but not built, would otherwise be found and then
fail in the middle of a proof. Every tool here is executed once before the
environment is called healthy, and Mathlib is asked to elaborate a real import.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from .config import Config
from .domain import FrozenModel
from .process import ProcessResult, ProcessSpec, run_process


class ToolStatus(FrozenModel):
    name: Literal["backend", "elan", "lean", "lake", "tectonic"]
    path: Path | None
    version: str | None
    healthy: bool
    detail: str


class EnvironmentReport(FrozenModel):
    tools: tuple[ToolStatus, ...]
    authenticated: bool
    mathlib_ready: bool
    healthy: bool


def probe_codex(
    *,
    client_factory: Callable[[], Any] | None = None,
    sdk_version: str | None = None,
) -> tuple[bool, str]:
    """Ask the Codex SDK whether a ChatGPT subscription is signed in."""
    from importlib import metadata

    from .codex_runtime import load_sdk

    version = sdk_version or metadata.version("openai-codex")
    factory = client_factory or load_sdk().Codex
    client = factory()
    try:
        response = client.account(refresh_token=False)
        authenticated = response.account is not None
    finally:
        client.close()
    return authenticated, f"openai-codex {version}"


def ensure_codex_login(
    *,
    confirmer: Callable[[str], bool],
    client_factory: Callable[[], Any] | None = None,
) -> bool:
    from .codex_runtime import load_sdk

    factory = client_factory or load_sdk().Codex
    client = factory()
    try:
        login = client.login_chatgpt()
        prompt = f"Open this Codex ChatGPT authorization URL and continue? {login.auth_url}"
        if not confirmer(prompt):
            return False
        return bool(login.wait().success)
    finally:
        client.close()


def probe_claude() -> tuple[bool, str]:
    """Report whether the Claude Code CLI the agent SDK drives is present."""
    from importlib import metadata

    try:
        version = metadata.version("claude-agent-sdk")
    except metadata.PackageNotFoundError:
        return False, "claude-agent-sdk is not installed"
    return shutil.which("claude") is not None, f"claude-agent-sdk {version}"


def resolve_executable(
    configured: Path | None,
    which: Callable[[str], str | None],
    common_locations: Iterable[Path],
    *,
    name: str = "",
) -> Path | None:
    if configured is not None and configured.is_file():
        return configured.resolve()
    discovered = which(name)
    if discovered is not None:
        path = Path(discovered)
        if path.is_file():
            return path.resolve()
    for candidate in common_locations:
        if candidate.is_file():
            return candidate.resolve()
    return None


def discover_environment(
    config: Config,
    *,
    runner: Callable[[ProcessSpec], ProcessResult] = run_process,
    backend_probe: Callable[[], tuple[bool, str]] = probe_claude,
    which: Callable[[str], str | None] = shutil.which,
    common_locations: dict[str, tuple[Path, ...]] | None = None,
) -> EnvironmentReport:
    common = common_locations or {}
    elan = resolve_executable(config.elan, which, common.get("elan", ()), name="elan")
    lake = resolve_executable(config.lake, which, common.get("lake", ()), name="lake")
    tectonic = resolve_executable(
        config.tectonic, which, common.get("tectonic", ()), name="tectonic"
    )
    authenticated, backend_version = backend_probe()
    backend_status = ToolStatus(
        name="backend",
        path=None,
        version=backend_version,
        healthy=authenticated,
        detail="authenticated" if authenticated else "authentication required",
    )
    elan_status = _version_status(
        "elan", elan, (str(elan), "--version") if elan else (), config, runner
    )
    lake_status = _version_status(
        "lake", lake, (str(lake), "--version") if lake else (), config, runner
    )
    lean_status = _version_status(
        "lean",
        lake,
        (str(lake), "env", "lean", "--version") if lake else (),
        config,
        runner,
    )
    tectonic_status = _version_status(
        "tectonic",
        tectonic,
        (str(tectonic), "--version") if tectonic else (),
        config,
        runner,
    )
    mathlib_ready = _smoke_mathlib(config, lake, runner)
    tools = (backend_status, elan_status, lean_status, lake_status, tectonic_status)
    healthy = authenticated and mathlib_ready and all(tool.healthy for tool in tools)
    return EnvironmentReport(
        tools=tools,
        authenticated=authenticated,
        mathlib_ready=mathlib_ready,
        healthy=healthy,
    )


def _version_status(
    name: Literal["elan", "lean", "lake", "tectonic"],
    path: Path | None,
    argv: tuple[str, ...],
    config: Config,
    runner: Callable[[ProcessSpec], ProcessResult],
) -> ToolStatus:
    if path is None:
        return ToolStatus(name=name, path=None, version=None, healthy=False, detail="not found")
    result = runner(
        ProcessSpec(
            argv=argv,
            cwd=config.lean_project or Path.cwd(),
            timeout_seconds=config.limits.lean_process_seconds,
            max_output_bytes=config.limits.process_output_bytes,
        )
    )
    healthy = result.returncode == 0 and not result.timed_out and not result.output_overflow
    version = result.stdout.strip() or result.stderr.strip() or None
    return ToolStatus(
        name=name,
        path=path,
        version=version,
        healthy=healthy,
        detail="smoke test passed" if healthy else "version smoke test failed",
    )


def _smoke_mathlib(
    config: Config,
    lake: Path | None,
    runner: Callable[[ProcessSpec], ProcessResult],
) -> bool:
    if lake is None:
        return False
    with tempfile.TemporaryDirectory(prefix="hardy-mathlib-") as temporary:
        smoke = Path(temporary) / "Main.lean"
        smoke.write_text("import Mathlib\n#check Nat.add_comm\n", encoding="utf-8")
        result = runner(
            ProcessSpec(
                argv=(str(lake), "env", "lean", str(smoke)),
                cwd=config.lean_project or Path.cwd(),
                timeout_seconds=config.limits.lean_process_seconds,
                max_output_bytes=config.limits.process_output_bytes,
            )
        )
    return result.returncode == 0 and not result.timed_out and not result.output_overflow
