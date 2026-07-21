from hardy.sandbox.runner import Mount, SandboxConfig, docker_argv


def cfg(**kwargs) -> SandboxConfig:
    kwargs.setdefault("image", "hardy-lean:dev")
    return SandboxConfig(**kwargs)


def test_hard_isolation_flags_always_present():
    argv = docker_argv(cfg(), ["true"])
    joined = " ".join(argv)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined


def test_quotas_rendered():
    argv = docker_argv(
        cfg(tmpfs_size_mb=256, tmpfs_inodes=5000, pids_limit=64, memory_mb=1024, cpus=1.5),
        ["true"],
    )
    joined = " ".join(argv)
    assert "--tmpfs /scratch:rw,size=256m,nr_inodes=5000" in joined
    assert "--pids-limit 64" in joined
    assert "--memory 1024m" in joined
    assert "--cpus 1.5" in joined


def test_tmpdir_env_defaults_to_scratch():
    assert "TMPDIR=/scratch" in " ".join(docker_argv(cfg(), ["true"]))


def test_mounts_workdir_and_interactive():
    argv = docker_argv(
        cfg(
            mounts=[Mount(host="/data/staging", container="/work", mode="rw")],
            workdir="/work",
        ),
        ["tectonic", "--untrusted", "main.tex"],
        interactive=True,
    )
    joined = " ".join(argv)
    assert "-v /data/staging:/work:rw" in joined
    assert "-w /work" in joined
    assert "-i" in argv
    # image, then command, at the very end
    assert argv[-4:] == ["hardy-lean:dev", "tectonic", "--untrusted", "main.tex"]


def test_mounts_default_read_only():
    argv = docker_argv(cfg(mounts=[Mount(host="/a", container="/b")]), ["true"])
    assert "-v /a:/b:ro" in " ".join(argv)


def test_container_name_rendered():
    argv = docker_argv(cfg(name="hardy-repl-abc123"), ["true"])
    assert "--name hardy-repl-abc123" in " ".join(argv)
    assert "--name" not in docker_argv(cfg(), ["true"])
