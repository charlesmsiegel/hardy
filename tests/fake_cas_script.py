#!/usr/bin/env python3
"""Run a Hardy-exported script the way the fake driver kernel would.

The fake kernel in `fake_cas.py` is an interpreter for a made-up language, so
its exported scripts need an interpreter too -- otherwise the export check
would have nothing to execute and the hermetic suite could not exercise the
path at all. It answers each cell with `fake_cas.answer` and prints what a
script prints: captured stdout, then the value on its own line.
"""

import sys
from pathlib import Path


def main() -> None:
    # Imported inside the function because the path this needs is only known
    # once the process is running, and a module-level import after a
    # `sys.path` edit is a lint error.
    sys.path.insert(0, str(Path(__file__).parent))
    from fake_cas import answer

    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        source = line.strip()
        if not source or source.startswith("#"):
            continue
        reply = answer(source)
        sys.stdout.write(reply["stdout"])
        if reply["value_repr"]:
            sys.stdout.write(reply["value_repr"] + "\n")
        sys.stderr.write(reply["stderr"])
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    main()
