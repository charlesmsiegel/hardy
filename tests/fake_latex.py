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
import time

INPUT = re.compile(r"\\input\{([^}]*)\}")
LABEL = re.compile(r"\\label\{([^}]*)\}")
VERB = re.compile(r"\\verb(.)(.*?)\1", re.DOTALL)


def _uncommented(text: str) -> str:
    r"""Drop what LaTeX would never execute: comments and \verb content."""
    text = VERB.sub("", text)
    kept = []
    for line in text.splitlines():
        cut = 0
        while True:
            found = line.find("%", cut)
            if found < 0:
                kept.append(line)
                break
            run = len(line[:found]) - len(line[:found].rstrip("\\"))
            if run % 2 == 0:
                kept.append(line[:found])
                break
            cut = found + 1
    return "\n".join(kept)

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

# `% slow: 30` makes this stand-in grind, so a test can stop it. A real TeX run
# over a long document is the child Esc has to reach; one that returns instantly
# cannot stand in for it. The touch file lets a test wait for the child to
# genuinely exist rather than guess how long that takes.
SLOW = re.search(r"%\s*slow:\s*([0-9.]+)", source)
if SLOW:
    READY = re.search(r"%\s*ready:\s*(\S+)", source)
    if READY:
        pathlib.Path(READY.group(1)).write_text("ready", encoding="utf-8")
    time.sleep(float(SLOW.group(1)))

if "\\begin{document}" in source and "\\end{document}" in source:
    pathlib.Path("writeup.pdf").write_bytes(b"%PDF-fake")
    # Real LaTeX records the labels it created in an .aux file, and Hardy reads
    # that rather than the source text. Modelling it here is what lets a test
    # tell a label the compiler made from one that only appears in the text --
    # inside a comment, or inside \verb.
    body = "".join(text for _, text in [(None, source)] + [(t, t.read_text()) for t in sorted(seen)])
    labels = []
    for name in LABEL.findall(_uncommented(body)):
        labels.append(f"\\newlabel{{{name}}}{{{{1}}{{1}}}}")
    pathlib.Path("writeup.aux").write_text("\n".join(labels) + "\n", encoding="utf-8")
    print("Output written on writeup.pdf")
    raise SystemExit(0)
print("! Emergency stop.")
raise SystemExit(1)
