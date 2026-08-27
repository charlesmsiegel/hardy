#!/usr/bin/env python3
"""Run a Hardy-exported script the way the fake driver kernel would.

The fake kernel in `fake_cas.py` is an interpreter for a made-up language, so
its exported scripts need an interpreter too -- otherwise the export check
would have nothing to execute and the hermetic suite could not exercise the
path at all. It answers each cell with `fake_cas.answer` and prints what a
script prints: captured stdout, then the value on its own line.
"""

import re
import sys
from pathlib import Path


def main() -> None:
    # Imported inside the function because the path this needs is only known
    # once the process is running, and a module-level import after a
    # `sys.path` edit is a lint error.
    sys.path.insert(0, str(Path(__file__).parent))
    from fake_cas import answer

    at_exit: list[str] = []
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        source = line.strip()
        if not source or source.startswith("#"):
            continue
        # The transcript brackets Hardy writes around a script's own output.
        # A real interpreter runs them as ordinary statements; this one has to
        # do the same, and must not hand them to `answer`, whose reply counter
        # would then be one ahead of the session's for every cell after them.
        #
        # The closing one is registered with `atexit` rather than written at
        # the foot of the file, so that it cannot resolve a name the cells
        # have had a chance to rebind. Held to the end here for the same
        # reason a real interpreter holds it: that is when it runs.
        registered = re.fullmatch(
            r'__import__\("atexit"\)\.register\('
            r'__import__\("builtins"\)\.print, "(.*)"\)',
            source,
        )
        if registered is not None:
            at_exit.append(registered.group(1))
            continue
        literal = re.fullmatch(r'(?:__import__\("builtins"\)\.)?print\("(.*)"\)', source)
        if literal is not None:
            sys.stdout.write(literal.group(1) + "\n")
            continue
        reply = answer(source)
        sys.stdout.write(reply["stdout"])
        if reply["value_repr"]:
            sys.stdout.write(reply["value_repr"] + "\n")
        sys.stderr.write(reply["stderr"])
    # LIFO, as `atexit` runs them: Hardy registers its closing marker before
    # any cell, so it is the last thing the process prints.
    for marker in reversed(at_exit):
        sys.stdout.write(marker + "\n")
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    main()
