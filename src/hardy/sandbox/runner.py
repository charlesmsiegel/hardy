"""Sandbox argv builder (DESIGN.md Component 7).

Model-generated Lean and TeX are untrusted code: elaboration can run IO
(#eval, native_decide) and TeX can read/write files. Every sandboxed
process therefore runs with no network, a read-only rootfs, dropped
capabilities, a pids limit, and a quota'd tmpfs scratch. Those are
hardcoded here — deliberately not configurable knobs.

This module only builds argv (pure, unit-testable without Docker); callers
hand the result to LeanRepl / compile_tex / subprocess.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Mount(BaseModel):
    host: str
    container: str
    mode: Literal["ro", "rw"] = "ro"


class SandboxConfig(BaseModel):
    image: str
    # Optional container name, so trusted side-channels (docker kill /
    # docker exec) can address the container itself — killing only the
    # docker client strands the container.
    name: str | None = None
    tmpfs_path: str = "/scratch"
    tmpfs_size_mb: int = 512
    tmpfs_inodes: int = 10_000
    pids_limit: int = 256
    memory_mb: int = 8192
    cpus: float = 2.0
    env: dict[str, str] = Field(default_factory=lambda: {"TMPDIR": "/scratch"})
    mounts: list[Mount] = Field(default_factory=list)
    workdir: str | None = None


def docker_argv(
    cfg: SandboxConfig, command: list[str], *, interactive: bool = False
) -> list[str]:
    argv = [
        "docker", "run", "--rm",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(cfg.pids_limit),
        "--memory", f"{cfg.memory_mb}m",
        "--cpus", str(cfg.cpus),
        "--tmpfs",
        f"{cfg.tmpfs_path}:rw,size={cfg.tmpfs_size_mb}m,nr_inodes={cfg.tmpfs_inodes}",
    ]
    if cfg.name:
        argv += ["--name", cfg.name]
    if interactive:
        argv.append("-i")
    for key, value in cfg.env.items():
        argv += ["-e", f"{key}={value}"]
    for mount in cfg.mounts:
        argv += ["-v", f"{mount.host}:{mount.container}:{mount.mode}"]
    if cfg.workdir:
        argv += ["-w", cfg.workdir]
    argv.append(cfg.image)
    return argv + command
