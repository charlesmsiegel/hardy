#!/usr/bin/env python3
"""A Lean that models just enough of the real one to test the harness.

It answers the questions real Lean answers here: does this file elaborate, does
an import resolve, and where does the olean go. An import resolves only against
a built olean on LEAN_PATH, exactly as Lean's own module resolution does -- a
stand-in that resolved imports against source files would let a broken build
look like a working one.
"""

import os
import pathlib
import sys

# Real Lean writes UTF-8 whatever the console codepage is, and Hardy decodes it
# as UTF-8. This stand-in has to do the same or it cannot print a goal marker.
sys.stdout.reconfigure(encoding="utf-8")

# Modules the toolchain resolves for itself, which no workspace build provides.
BUILTIN = {"Mathlib", "Init", "Std", "Lean", "Batteries"}

argv = sys.argv[1:]
output = None
for index, item in enumerate(argv):
    if item == "-o" and index + 1 < len(argv):
        output = pathlib.Path(argv[index + 1])

path = pathlib.Path(argv[-1])
source = path.read_text(encoding="utf-8")

search = [part for part in os.environ.get("LEAN_PATH", "").split(os.pathsep) if part]
for line in source.splitlines():
    stripped = line.strip()
    if not stripped.startswith("import "):
        continue
    name = stripped.removeprefix("import ").strip()
    if name.split(".")[0] in BUILTIN:
        continue
    relative = pathlib.Path(*name.split(".")).with_suffix(".olean")
    if not any((pathlib.Path(directory) / relative).is_file() for directory in search):
        print(f"{path.name}:1:0: error: unknown module prefix '{name}'")
        raise SystemExit(1)

if "exact True.intro" in source and "sorry" not in source and "admit" not in source:
    if "#print axioms" in source:
        print("'HardyTarget' depends on axioms: []")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"olean-fake")
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
if "#check True.intro" in source:
    print("True.intro : True")
    raise SystemExit(0)
print("Main.lean:3:28: error: type mismatch")
raise SystemExit(1)
