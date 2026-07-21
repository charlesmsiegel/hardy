"""Where the real REPL lives and how to launch it.

The repl binary is built in its own repo (vendor/repl) but must run from
inside lean_project via `lake env`, so Mathlib's oleans are on LEAN_PATH.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAN_PROJECT = REPO_ROOT / "lean_project"
REPL_BIN = REPO_ROOT / "vendor" / "repl" / ".lake" / "build" / "bin" / "repl"


def repl_argv() -> list[str]:
    """Argv for the real REPL. Run with cwd=LEAN_PROJECT."""
    return ["lake", "env", str(REPL_BIN)]


_RESET_SCRIPT = (
    # Kill every process except the pid-1 repl and this shell: untrusted
    # elaboration (#eval IO) can fork background children that would
    # otherwise survive into — and interfere with — later checks. Then wipe
    # scratch. (No `ps` in the image; walk /proc directly.)
    "for p in /proc/[0-9]*; do p=${p#/proc/}; "
    '[ "$p" = 1 ] && continue; [ "$p" = "$$" ] && continue; '
    "kill -9 $p 2>/dev/null; done; "
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
