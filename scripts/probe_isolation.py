"""Measure the current launcher's authority using only disposable test assets.

This is a negative baseline, never a confinement acceptance test. The child
attempts to read/write sibling fixtures and connect to a parent-owned loopback
listener. It receives no credentials and contacts no external service.
"""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from hardy.foundation.process import ProcessSpec, run_process

CHILD = r'''
import json, socket, sys
from pathlib import Path
outside = Path(sys.argv[1])
observed = {}
for name, action in (
    ("outside_read", lambda: (outside / "input.txt").read_text() == "fixture only"),
    ("outside_write", lambda: (outside / "output.txt").write_text("fixture only") > 0),
    ("loopback_connect", lambda: socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=2).close() is None),
):
    try:
        observed[name] = {"allowed": action()}
    except OSError as error:
        observed[name] = {"allowed": False, "error_type": type(error).__name__, "errno": error.errno}
print(json.dumps(observed))
'''


def probe() -> dict:
    with TemporaryDirectory(prefix="hardy-isolation-baseline-") as temporary:
        root = Path(temporary)
        scratch = root / "scratch"
        outside = root / "outside"
        scratch.mkdir()
        outside.mkdir()
        (outside / "input.txt").write_text("fixture only")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            result = run_process(ProcessSpec(
                argv=(sys.executable, "-I", "-c", CHILD, str(outside), str(listener.getsockname()[1])),
                cwd=scratch, timeout_seconds=10, max_output_bytes=4096,
            ))
        return {
            "schema": "hardy.isolation-baseline/v1",
            "platform": platform.platform(), "python": platform.python_version(),
            "launcher": "foundation.process.run_process",
            "probe_sha256": hashlib.sha256(CHILD.encode()).hexdigest(),
            "returncode": result.returncode, "timed_out": result.timed_out,
            "output_overflow": result.output_overflow,
            "observations": json.loads(result.stdout) if result.returncode == 0 else {},
            "stderr": result.stderr,
            "confinement_established": False,
            "interpretation": "Only successful operations establish baseline authority; refusal may come from the host environment.",
        }


if __name__ == "__main__":
    print(json.dumps(probe(), indent=2))
