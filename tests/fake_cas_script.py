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

    at_exit: list[tuple[str, ...]] = []
    # Whether the file's last statement ran. Hardy's closing marker says which
    # of two things happened -- the file finished, or the interpreter shut down
    # before it did -- and a fake interpreter that always reported the first
    # would be no test of the difference. These two statements are Hardy's own,
    # from `SympyBackend.transcript_prologue` and `transcript_epilogue`.
    reached_the_end = False
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        source = line.strip()
        if not source or source.startswith("#"):
            continue
        if source == "_hardy_reached_the_end = []":
            continue
        if source == "_hardy_reached_the_end.append(True)":
            reached_the_end = True
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
        # Anchored on the markers' own guillemets. The statement carries other
        # quoted strings -- the module names it imports -- and a looser match
        # registered `functools` as the closing marker. Two of them travel
        # together: the one for a file that finished and the one for a process
        # that shut down before it did.
        if source.startswith('__import__("atexit").register('):
            markers = re.findall(r'"(\u00ab[^"]*\u00bb)"', source)
            if markers:
                at_exit.append(tuple(markers))
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
    # any cell, so it is the last thing the process prints. Where two markers
    # were registered together, which one goes out is what the callback
    # decides -- the file's own report of whether it got to the end.
    for markers in reversed(at_exit):
        finished, cut_short = markers[0], markers[-1]
        sys.stdout.write((finished if reached_the_end else cut_short) + "\n")
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    main()
