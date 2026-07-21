"""Where the real REPL lives and how to launch it.

The repl binary is built in its own repo (vendor/repl) but needs Mathlib's
oleans and the Lean core on its environment. It must be launched DIRECTLY
(not wrapped in `lake env`): `lake env <repl>` deadlocks the framed
async stdio the pool speaks. Instead repl_env() captures the toolchain
variables `lake env` would set — critically LEAN_SYSROOT (without it the
elaborator can't find core, so even `2 + 0` fails with "Unknown constant
OfNat") plus LEAN_PATH / LEAN_SRC_PATH / LD_LIBRARY_PATH — and the repl is
run with that environment.
"""

import functools
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAN_PROJECT = REPO_ROOT / "lean_project"
REPL_BIN = REPO_ROOT / "vendor" / "repl" / ".lake" / "build" / "bin" / "repl"

# Toolchain vars `lake env` exports; captured once and set directly so the repl
# runs without the `lake env` wrapper. LEAN_SYSROOT is the essential one.
_REPL_ENV_VARS = ("LEAN_PATH", "LEAN_SRC_PATH", "LEAN_SYSROOT", "LD_LIBRARY_PATH")


def repl_argv() -> list[str]:
    """Argv for the real REPL, launched directly. Run with cwd=LEAN_PROJECT and
    env=repl_env(); do NOT wrap in `lake env` (it deadlocks the framed stdio)."""
    return [str(REPL_BIN)]


@functools.cache
def repl_env() -> dict[str, str]:
    """os.environ plus the toolchain vars `lake env` would set for the repl.

    Captured once via `lake env printenv <VAR>` (cwd=LEAN_PROJECT). Must include
    LEAN_SYSROOT, or continuation commands lose core (OfNat, numeric literals).
    """
    env = dict(os.environ)
    for var in _REPL_ENV_VARS:
        result = subprocess.run(
            ["lake", "env", "printenv", var],
            cwd=LEAN_PROJECT,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        if value:
            env[var] = value
    return env


_RESET_SCRIPT = (
    # Kill every process except the pid-1 repl and this shell: untrusted
    # elaboration (#eval IO) can fork background children that would otherwise
    # survive into — and interfere with — later checks. A single /proc sweep
    # races descendants forked after the glob expands, so loop until two
    # consecutive scans find no survivors (bounded), then wipe scratch. (No `ps`
    # in the image; walk /proc directly.)
    #
    # NOTE: this is best-effort hardening, not atomic. A child that keeps
    # forking faster than we can reap could in theory outlast the loop; the
    # robust fix (a cgroup-atomic kill of the worker's non-REPL processes) lands
    # with the sandbox-image rework. A failed reset already recycles the worker.
    "i=0; while [ $i -lt 20 ]; do left=0; "
    "for p in /proc/[0-9]*; do p=${p#/proc/}; "
    '[ "$p" = 1 ] && continue; [ "$p" = "$$" ] && continue; '
    "kill -9 $p 2>/dev/null && left=1; done; "
    '[ "$left" = 0 ] && break; i=$((i+1)); done; '
    "find /scratch -mindepth 1 -delete"
)


def sandboxed_worker_spec(
    image: str = "hardy-lean:dev", memory_mb: int = 12_288
) -> "WorkerSpec":
    """WorkerSpec running the repl inside the sandbox (ReplPool spec_factory).

    Each call mints a unique container name so the pool's trusted
    side-channels can address the container itself: reset_argv kills any
    straggler processes and wipes /scratch between checks (one untrusted
    proof must not leave processes or files behind for the next), and
    cleanup_argv kills the container — killing only the docker client would
    strand a hung REPL, since SIGKILL is never proxied and --rm removes a
    container only after it exits on its own.

    LEAN_PATH was captured into repl-env.sh at image build time, so the
    read-only container never needs to run lake.
    """
    import uuid

    from hardy.lean.pool import WorkerSpec
    from hardy.sandbox.runner import SandboxConfig, docker_argv

    name = f"hardy-repl-{uuid.uuid4().hex[:12]}"
    cfg = SandboxConfig(image=image, memory_mb=memory_mb, name=name)
    command = [
        "/bin/sh",
        "-c",
        ". /home/hardy/repl-env.sh && exec /home/hardy/repl/.lake/build/bin/repl",
    ]
    return WorkerSpec(
        argv=docker_argv(cfg, command, interactive=True),
        reset_argv=["docker", "exec", name, "/bin/sh", "-c", _RESET_SCRIPT],
        cleanup_argv=["docker", "kill", name],
    )
