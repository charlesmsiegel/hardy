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

    # What the file's last statement left, and the lines its shutdown hooks
    # will print. Hardy registers two: one carrying the closing marker
    # literally, and one that prints whatever `_hardy_finished` holds when the
    # process ends -- which is nothing at all if the file never got there.
    finished = ""
    at_exit: list[str | None] = []
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
        # Anchored on the marker's own guillemets. The statement carries other
        # quoted strings -- the module names it imports -- and a looser match
        # registered `functools` as the closing marker. The registration that
        # carries no marker at all is the other one: it reads the namespace at
        # shutdown, so what it prints is decided then and not here.
        if source.startswith('__import__("atexit").register('):
            registered = re.search(r'"(\u00ab[^"]*\u00bb)"', source)
            at_exit.append(None if registered is None else registered.group(1))
            continue
        # The file's last statement. A bare assignment of a string, so that a
        # cell which has broken `print` or `__import__` cannot stop it.
        leaves = re.fullmatch(r'_hardy_finished = "(.*)"', source)
        if leaves is not None:
            finished = leaves.group(1)
            continue
        # The begin marker, and the file's own closing statement -- the one
        # that says it reached the end. The destination is named on that one
        # and not on the other, because only the second runs after cells have
        # had a chance to rebind `sys.stdout`; both print here.
        literal = re.fullmatch(
            r'(?:__import__\("builtins"\)\.)?print\("(.*?)"'
            r'(?:, file=__import__\("sys"\)\.stdout)?\)',
            source,
        )
        if literal is not None:
            sys.stdout.write(literal.group(1) + "\n")
            continue
        reply = answer(source)
        sys.stdout.write(reply["stdout"])
        if reply["value_repr"]:
            sys.stdout.write(reply["value_repr"] + "\n")
        sys.stderr.write(reply["stderr"])
    # LIFO, as `atexit` runs them: Hardy registers its closing marker before
    # any cell, so it is the last thing the process prints, and the completion
    # line registered after it comes out just inside.
    for marker in reversed(at_exit):
        sys.stdout.write((finished if marker is None else marker) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    main()
