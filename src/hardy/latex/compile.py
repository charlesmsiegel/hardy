"""Compile-check LaTeX the way Lean output is kernel-checked (DESIGN.md
Component 5): errors come back structured, and generated TeX is treated as
untrusted code — shell-escape stays disabled (`--untrusted`), the compiler
sees only its staging, and the subprocess environment is scrubbed to
PATH + HOME (+ explicit extra_env) so nothing can leak through it.

Host-side containment: compiler output is read through a hard byte cap (a
document that spews diagnostics in a loop is killed, never buffered into
host memory for the whole timeout), the log is read tail-only, and stale
main.pdf/main.log are deleted before each run so a failed compile can
never serve a previous run's artifacts.

Two entry points: compile_tex runs a local (or arbitrary-argv) engine;
compile_tex_sandboxed runs tectonic in the hardy-tex:dev container with NO
writable host mount at all — the untrusted container sees a read-only
/staging plus its quota'd /scratch tmpfs, streams artifacts back as a tar
on stdout, and the trusted host side extracts exactly main.pdf/main.log
under a byte cap. A writable host-backed bind mount would let untrusted
output bypass the tmpfs size/inode quotas and fill host storage, so none
is ever exposed.
"""

import io
import os
import re
import selectors
import subprocess
import tarfile
import time
from pathlib import Path

from pydantic import BaseModel

DEFAULT_ENGINE = ["tectonic", "--untrusted", "--chatter", "minimal"]
_OUTPUT_CAP = 1_000_000            # bytes of diagnostics before the compile is killed
_ARTIFACT_CAP = 64 * 1024 * 1024   # bytes of tar-streamed artifacts accepted back
_LOG_TAIL_CAP = 256 * 1024         # bytes of main.log we ever read

_STDERR_RE = re.compile(r"^error: (?:(?P<file>[^:\s][^:]*):(?P<line>\d+):\s*)?(?P<msg>.+)$")
_LOG_RE = re.compile(r"^! (?P<msg>.+)$")


class TexError(BaseModel):
    file: str | None = None
    line: int | None = None
    message: str


class CompileResult(BaseModel):
    success: bool
    errors: list[TexError] = []
    log_tail: str = ""
    pdf_path: Path | None = None


def _run_streams_capped(
    argv: list[str], env: dict[str, str], timeout: float, out_cap: int, err_cap: int
) -> tuple[int | None, bytes, str, str | None]:
    """Run argv with stdout and stderr read separately under byte caps.

    Returns (returncode, stdout_bytes, stderr_text, abort_reason);
    returncode is None (and abort_reason set) when the process was killed
    for timeout or exceeding a cap."""
    proc = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    sel = selectors.DefaultSelector()
    for pipe, tag in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        os.set_blocking(pipe.fileno(), False)
        sel.register(pipe, selectors.EVENT_READ, tag)
    bufs = {"stdout": bytearray(), "stderr": bytearray()}
    caps = {"stdout": out_cap, "stderr": err_cap}
    deadline = time.monotonic() + timeout
    open_pipes = 2
    abort: str | None = None
    while open_pipes and abort is None:
        if time.monotonic() > deadline:
            abort = f"timed out after {timeout}s"
            break
        for key, _ in sel.select(timeout=0.1):
            chunk = key.fileobj.read(65536)
            if chunk is None:
                continue
            if chunk == b"":
                sel.unregister(key.fileobj)
                open_pipes -= 1
                continue
            bufs[key.data].extend(chunk)
            if len(bufs[key.data]) > caps[key.data]:
                abort = f"{key.data} exceeded {caps[key.data]} byte cap"
                break
    if abort is not None:
        proc.kill()
        proc.wait()
        code = None
    else:
        try:
            code = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code, abort = None, f"timed out after {timeout}s"
    return code, bytes(bufs["stdout"]), bufs["stderr"].decode(errors="replace"), abort


def _extract_artifacts(tar_bytes: bytes, staging: Path) -> None:
    """Write exactly main.pdf / main.log from the tar stream into staging.

    The stream comes from the untrusted container: any other member —
    other names, directories, links, path tricks — is silently dropped,
    and a garbled stream yields no artifacts at all.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            for member in tar.getmembers():
                if member.name in ("main.pdf", "main.log") and member.isreg():
                    handle = tar.extractfile(member)
                    if handle is not None:
                        (staging / member.name).write_bytes(handle.read())
    except tarfile.TarError:
        pass  # no/garbled artifacts: the compile is graded by exit code + logs


def compile_tex_sandboxed(
    source: str,
    staging: Path,
    *,
    image: str = "hardy-tex:dev",
    timeout: float = 120.0,
) -> CompileResult:
    """compile_tex, but inside the sandbox with NO writable host mount.

    The untrusted container sees only a read-only /staging and its quota'd
    /scratch tmpfs, so nothing it runs can write host-backed storage.
    Artifacts return as a tar stream on the container's stdout (tectonic's
    own chatter is diverted to stderr); the trusted host side extracts
    exactly main.pdf/main.log under _ARTIFACT_CAP. The container is named,
    and any host-side abort docker-kills it — killing only the docker
    client is never proxied, and a lingering compile could otherwise drop
    artifacts into a later attempt. The in-container `timeout` matches the
    requested budget; the host allows a small grace on top so the inner
    timeout normally fires first and the container exits cleanly. The
    image (hardy-tex:dev) contains no Lean project or repo state, so TeX
    \\input primitives have nothing to disclose beyond staging itself.
    """
    import uuid

    from hardy.sandbox.runner import Mount, SandboxConfig, docker_argv

    staging.mkdir(parents=True, exist_ok=True)
    for stale in ("main.pdf", "main.log"):
        # Never grade this run against a previous run's artifacts.
        (staging / stale).unlink(missing_ok=True)
    (staging / "main.tex").write_text(source)

    name = f"hardy-tex-{uuid.uuid4().hex[:12]}"
    cfg = SandboxConfig(
        image=image,
        name=name,
        mounts=[Mount(host=str(staging.resolve()), container="/staging", mode="ro")],
    )
    script = (
        "cp /staging/main.tex /scratch/ && cd /scratch && "
        f"timeout {max(1, int(timeout))} "
        "tectonic --untrusted --only-cached --chatter minimal main.tex >&2; "
        "status=$?; tar -cf - main.pdf main.log 2>/dev/null; exit $status"
    )
    argv = docker_argv(cfg, ["/bin/sh", "-c", script])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    code, tar_bytes, stderr_text, abort = _run_streams_capped(
        argv, env, timeout + 15, _ARTIFACT_CAP, _OUTPUT_CAP
    )
    if abort is not None:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=60)
        return CompileResult(
            success=False,
            errors=[TexError(message=f"compile aborted: {abort}")],
            log_tail=stderr_text[-2000:],
        )
    _extract_artifacts(tar_bytes, staging)
    errors = _parse_errors(stderr_text, _log_tail(staging / "main.log"))
    pdf = staging / "main.pdf"
    success = code == 0 and pdf.exists()
    if not success and not errors:
        errors = [TexError(message=(stderr_text.strip() or "compile failed")[-500:])]
    return CompileResult(
        success=success,
        errors=[] if success else errors,
        log_tail=stderr_text[-2000:],
        pdf_path=pdf if success else None,
    )


def _run_capped(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float
) -> tuple[int | None, str, bool]:
    """Run argv with stderr merged into stdout, retaining at most _OUTPUT_CAP
    bytes. Returns (returncode, output, over_cap); returncode is None when
    the process was killed for timeout or output volume."""
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    os.set_blocking(proc.stdout.fileno(), False)
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = proc.stdout.read(65536)
        if chunk is None:  # no data available yet
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return None, b"".join(chunks).decode(errors="replace"), False
            time.sleep(0.05)
            continue
        if chunk == b"":  # EOF: all writers closed
            # A process can close its pipes yet keep running; keep enforcing the
            # deadline so it can't hang the harness past the requested timeout.
            remaining = deadline - time.monotonic()
            try:
                code = proc.wait(timeout=max(0.0, remaining))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return None, b"".join(chunks).decode(errors="replace"), False
            return code, b"".join(chunks).decode(errors="replace"), False
        total += len(chunk)
        chunks.append(chunk)
        if total > _OUTPUT_CAP:
            proc.kill()
            proc.wait()
            return None, b"".join(chunks)[-_OUTPUT_CAP:].decode(errors="replace"), True


def _log_tail(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    with log_path.open("rb") as handle:
        handle.seek(max(0, log_path.stat().st_size - _LOG_TAIL_CAP))
        return handle.read().decode(errors="replace")


def _parse_errors(output: str, log_text: str) -> list[TexError]:
    errors = []
    for raw in output.splitlines():
        if match := _STDERR_RE.match(raw.strip()):
            errors.append(
                TexError(
                    file=match["file"],
                    line=int(match["line"]) if match["line"] else None,
                    message=match["msg"],
                )
            )
    for raw in log_text.splitlines():
        if match := _LOG_RE.match(raw):
            errors.append(TexError(message=match["msg"]))
    return errors


def compile_tex(
    source: str,
    staging: Path,
    *,
    engine: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> CompileResult:
    """Compile with a local engine argv. For sandboxed compilation use
    compile_tex_sandboxed — do NOT wrap a docker argv as `engine` here: this
    path cannot kill the container on abort and would need a writable mount."""
    staging.mkdir(parents=True, exist_ok=True)
    for stale in ("main.pdf", "main.log"):
        # Never grade this run against a previous run's artifacts.
        (staging / stale).unlink(missing_ok=True)
    (staging / "main.tex").write_text(source)
    argv = list(engine or DEFAULT_ENGINE) + ["main.tex"]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(staging)}
    env.update(extra_env or {})
    code, output, over_cap = _run_capped(argv, staging, env, timeout)
    if code is None:
        reason = (
            f"output exceeded {_OUTPUT_CAP} byte cap"
            if over_cap
            else f"timed out after {timeout}s"
        )
        return CompileResult(
            success=False,
            errors=[TexError(message=f"compile aborted: {reason}")],
            log_tail=output[-2000:],
        )
    errors = _parse_errors(output, _log_tail(staging / "main.log"))
    pdf = staging / "main.pdf"
    success = code == 0 and pdf.exists()
    if not success and not errors:
        errors = [TexError(message=(output.strip() or "compile failed")[-500:])]
    return CompileResult(
        success=success,
        errors=[] if success else errors,
        log_tail=output[-2000:],
        pdf_path=pdf if success else None,
    )
