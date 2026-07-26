#!/usr/bin/env python3
"""A LaTeX that models just enough of a real one to test the harness.

It resolves `\\input` against files that actually exist in the compile
directory. Without that, a document whose fragment had been deleted would still
"compile", and a test asserting the deletion is refused would pass for the
wrong reason.
"""

import pathlib
import re
import sys

INPUT = re.compile(r"\\input\{([^}]*)\}")

path = pathlib.Path(sys.argv[-1])
source = path.read_text()

pending = [(path, source)]
seen = set()
while pending:
    current, text = pending.pop()
    for name in INPUT.findall(text):
        target = current.parent / name
        if not target.suffix:
            target = target.with_suffix(".tex")
        if not target.is_file():
            print(f"! LaTeX Error: File `{name}' not found.")
            raise SystemExit(1)
        if target in seen:
            continue
        seen.add(target)
        pending.append((target, target.read_text()))

if "\\begin{document}" in source and "\\end{document}" in source:
    pathlib.Path("writeup.pdf").write_bytes(b"%PDF-fake")
    print("Output written on writeup.pdf")
    raise SystemExit(0)
print("! Emergency stop.")
raise SystemExit(1)
