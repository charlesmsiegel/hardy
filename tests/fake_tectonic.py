"""Fake TeX engine for unit tests. Mode via FAKE_TEX_MODE env var (the tests
pass it through compile_tex's extra_env — which also proves extra_env works):
  ok (default) — writes main.pdf, exit 0
  fail         — tectonic-style stderr error + `! ...` log line, exit 1
  hang         — sleeps forever (timeout tests)
  dump-env     — prints its environment variable names to stderr, exit 1
  spew         — floods stderr with ~4 MB of diagnostics (output-cap tests)
"""

import os
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_TEX_MODE", "ok")

if mode == "hang":
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
