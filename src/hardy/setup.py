"""Dependency discovery and smoke tests for a staged run.

Discovery is not enough on its own: a tool that exists on PATH but cannot run,
or a Mathlib that is present but not built, would otherwise be found and then
fail in the middle of a proof. Every tool here is executed once before the
environment is called healthy, and Mathlib is asked to elaborate a real import.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from .config import Config
from .domain import FrozenModel
from .process import ProcessResult, ProcessSpec, run_process


class ToolStatus(FrozenModel):
    name: Literal["backend", "elan", "lean", "lake", "tectonic", "cas"]
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


def probe_api() -> tuple[bool, str]:
    """Report whether the Messages API transport has what it needs here.

    Its credentials are a key in this process, not a signed-in CLI, so this
    asks about the two things that decide whether a run can start: the SDK and
    the key. Whether one is set, never what it is -- a setup report is pasted
    into issues, and a key printed there is a key that has been disclosed.
    """
    from importlib import metadata

    # Imported, not merely looked up. Distribution metadata survives a partial
    # installation and says nothing about whether the package will load -- a
    # missing runtime dependency leaves the version readable and the import
    # broken, and `hardy setup` reports its final health from this probe. It
    # would have called an unusable installation ready and left the failure for
    # the first provider turn.
    try:
        import anthropic
    except ImportError as error:
        return False, f"anthropic is not importable: {error}"
    version = getattr(anthropic, "__version__", None) or _installed_version(metadata)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, f"anthropic {version}, ANTHROPIC_API_KEY unset"
    return True, f"anthropic {version}"


def _installed_version(metadata: Any) -> str:
    try:
        return str(metadata.version("anthropic"))
    except metadata.PackageNotFoundError:
        return "unknown version"


def backend_probe(backend: str) -> Callable[[], tuple[bool, str]]:
    """The probe for the *selected* transport, the way the doctor chooses one.

    `discover_environment` defaulted to `probe_claude` for every caller, and
    `hardy setup` never overrode it -- so its exit status answered a question
    about a backend the machine may not be using. Both directions were wrong:
    an API-only machine with a good key was reported broken for lacking the
    Claude CLI, and a Claude-configured machine passed setup without the key
    the `api` runtime needs and failed at its first request instead.
    """
    return probe_api if backend == "api" else probe_claude


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
    cas_status = _cas_status(config)
    tools = (backend_status, elan_status, lean_status, lake_status, tectonic_status)
    healthy = authenticated and mathlib_ready and all(tool.healthy for tool in tools)
    # A run is healthy without computer algebra. The status is reported so a
    # result records which kernel was reachable, not to gate the run on one.
    tools = tools + (cas_status,)
    return EnvironmentReport(
        tools=tools,
        authenticated=authenticated,
        mathlib_ready=mathlib_ready,
        healthy=healthy,
    )


def _cas_status(config: Config) -> ToolStatus:
    """Start the kernel and ask its version: found is not the same as working."""
    from .cas_tools import build_runtime

    with tempfile.TemporaryDirectory(prefix="hardy-cas-") as directory:
        runtime, detail = build_runtime(
            backend_name=config.cas_backend,
            command=config.cas_command,
            limits=config.limits,
            log_path=Path(directory) / "cells.jsonl",
            cwd=Path(directory),
        )
        version = None
        if runtime is not None:
            version = runtime.session.version
            runtime.session.close()
    return ToolStatus(
        name="cas",
        path=config.cas_command,
        version=version,
        healthy=runtime is not None,
        detail=detail if runtime is None else "smoke test passed",
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
