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
