#!/usr/bin/env python3
import pathlib
import sys

source = pathlib.Path(sys.argv[-1]).read_text()
if "\\begin{document}" in source and "\\end{document}" in source:
    pathlib.Path("writeup.pdf").write_bytes(b"%PDF-fake")
    print("Output written on writeup.pdf")
    raise SystemExit(0)
print("! Emergency stop.")
raise SystemExit(1)
