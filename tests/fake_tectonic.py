"""Fake TeX engine for unit tests. Mode via FAKE_TEX_MODE env var (the tests
pass it through compile_tex's extra_env — which also proves extra_env works):
  ok (default) — writes main.pdf, exit 0
  fail         — tectonic-style stderr error + `! ...` log line, exit 1
  hang         — sleeps forever (timeout tests)
  closehang    — closes stdout+stderr, then sleeps forever (EOF-then-hang tests)
  grouphang    — spawns a child that writes HARDY_CHILD_MARKER after 2s, then
                 sleeps forever (process-group-kill tests)
  dump-env     — prints its environment variable names to stderr, exit 1
  spew         — floods stderr with ~4 MB of diagnostics (output-cap tests)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_TEX_MODE", "ok")

if mode == "hang":
    time.sleep(3600)
elif mode == "grouphang":
    # Spawn a child in this process's group, then hang so the harness aborts
    # on timeout. If the abort only kills this leader, the child survives and
    # writes the marker after 2s — proving the group was NOT killed.
    marker = os.environ["HARDY_CHILD_MARKER"]
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import time, pathlib; time.sleep(2); "
            f"pathlib.Path({marker!r}).write_text('leaked')",
        ]
    )
    time.sleep(3600)
elif mode == "closehang":
    # EOF on both pipes, but the process keeps running: the harness must still
    # enforce its timeout rather than blocking forever on wait().
    os.close(1)
    os.close(2)
    time.sleep(3600)
elif mode == "fail":
    sys.stderr.write("error: main.tex:3: Undefined control sequence\n")
    Path("main.log").write_text("! Undefined control sequence.\n")
    sys.exit(1)
elif mode == "dump-env":
    sys.stderr.write("\n".join(sorted(os.environ)) + "\n")
    sys.exit(1)
elif mode == "spew":
    junk = b"x" * 65536
    for _ in range(64):  # ~4 MB, well past the 1 MB cap
        sys.stderr.buffer.write(junk)
    sys.stderr.flush()
    Path("main.pdf").write_bytes(b"%PDF-1.4 fake")
    sys.exit(0)
else:
    Path("main.pdf").write_bytes(b"%PDF-1.4 fake")
    sys.exit(0)
