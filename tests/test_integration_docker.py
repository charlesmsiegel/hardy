"""Integration tests against the built sandbox images.

Prereq (Nix-built images, no Docker Hub / GitHub releases):
    nix-build nix/tex-image.nix  && docker load < result   # hardy-tex:dev
    nix-build nix/lean-image.nix && docker load < result   # hardy-lean:dev
Run with: pytest -m docker
"""

import subprocess
import time

import pytest

from hardy.sandbox.runner import SandboxConfig, docker_argv

pytestmark = pytest.mark.docker

CFG = SandboxConfig(image="hardy-lean:dev")


def run(command, **kwargs):
    return subprocess.run(
        docker_argv(CFG, command), capture_output=True, text=True, timeout=300, **kwargs
    )


def test_no_network():
    # Use busybox wget (present in the image) and distinguish a real connection
    # failure from a missing binary — a command-not-found would pass spuriously.
    probe = run(["wget", "-T", "5", "-q", "-O", "/dev/null", "http://example.com"])
    assert probe.returncode != 0, "wget unexpectedly reached the network"
    assert "not found" not in (probe.stderr or "").lower(), "wget missing from image"
    # A DNS/connect probe (nslookup) must also fail with --network none.
    assert run(["nslookup", "example.com"]).returncode != 0


def test_rootfs_read_only_but_scratch_writable():
    assert run(["touch", "/forbidden"]).returncode != 0
    assert run(["touch", "/scratch/ok"]).returncode == 0


def test_pids_limit_applied():
    # Probe from the HOST via `docker top`: an in-container probe would
    # itself need a free pid slot, which is exactly what a working limit
    # denies — the test would fail precisely when the limit works.
    name = "hardy-test-pids"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    argv = docker_argv(
        SandboxConfig(image="hardy-lean:dev", pids_limit=16, name=name),
        ["/bin/sh", "-c", "for i in $(seq 1 64); do sleep 30 & done; sleep 30"],
    )
    proc = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(5)  # let it spawn as many sleeps as the limit allows
        top = subprocess.run(
            ["docker", "top", name], capture_output=True, text=True, timeout=30
        )
        rows = [line for line in top.stdout.splitlines()[1:] if line.strip()]
        assert 0 < len(rows) <= 17  # 64 requested; the 16-pid ceiling held
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True)
        proc.wait(timeout=30)


async def test_sandboxed_repl_checks_a_proof():
    from hardy.lean.launch import sandboxed_worker_spec
    from hardy.lean.repl import LeanRepl

    spec = sandboxed_worker_spec()
    repl = LeanRepl(spec.argv, cleanup_argv=spec.cleanup_argv, default_timeout=600)
    await repl.start()
    try:
        resp = await repl.run_command("theorem t : 2 + 2 = 4 := rfl")
        assert resp.env is not None and not resp.messages
    finally:
        await repl.close()


async def test_timeout_kills_the_container_not_just_the_client():
    from hardy.lean.launch import sandboxed_worker_spec
    from hardy.lean.repl import LeanRepl, ReplTimeout

    spec = sandboxed_worker_spec()
    name = spec.cleanup_argv[-1]
    repl = LeanRepl(spec.argv, cleanup_argv=spec.cleanup_argv)
    await repl.start()
    with pytest.raises(ReplTimeout):
        await repl.run_command(
            "#eval (List.range 100000000).foldl (·+·) 0", timeout=2
        )
    live = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name={name}"],
        capture_output=True, text=True,
    )
    assert live.stdout.strip() == ""  # cleanup_argv killed the container itself


def test_sandboxed_texlive_compiles_offline(tmp_path):
    from hardy.latex.compile import compile_tex_sandboxed
    from hardy.latex.template import render_writeup

    staging = tmp_path / "staging"
    staging.mkdir()
    source = render_writeup(
        title="Sandbox Check",
        statement=r"$1 + 1 = 2$.",
        informal_proof="Immediate.",
        formalization_status="not formalized",
    )
    result = compile_tex_sandboxed(source, staging, timeout=300)
    assert result.success, result.errors
    assert (staging / "main.pdf").exists()  # streamed back out of the tmpfs
    assert (staging / "main.tex").read_text() == source  # source persisted beside it
