#!/usr/bin/env python3
"""A Lean that models just enough of the real one to test the harness.

It answers the questions real Lean answers here: does this file elaborate, does
an import resolve, where does the olean go, and what does a declaration depend
on. An import resolves only against a built olean on LEAN_PATH, exactly as
Lean's own module resolution does -- a stand-in that resolved imports against
source files would let a broken build look like a working one.

A test chooses what `#print axioms` reports with a `-- axioms: a, b` marker in
the source. The marker is copied into the olean this run writes, so a later
elaboration that *imports* that module sees the same axioms -- which is how the
real thing behaves, and the only way the interactive audit, which runs over a
built workspace rather than over source, can be exercised hermetically.
"""

import os
import pathlib
import re
import sys

# Real Lean writes UTF-8 whatever the console codepage is, and Hardy decodes it
# as UTF-8. This stand-in has to do the same or it cannot print a goal marker.
sys.stdout.reconfigure(encoding="utf-8")

# Modules the toolchain resolves for itself, which no workspace build provides.
BUILTIN = {"Mathlib", "Init", "Std", "Lean", "Batteries"}
# Word boundaries, like the real hole check: `sorryAx` is an axiom's name, not
# a hole, and a test naming it in a marker must not read as a `sorry`.
HOLE = re.compile(r"\b(sorry|admit)\b")
MARKER = re.compile(r"--\s*axioms:\s*(.*)")
OLEAN_PREFIX = b"olean-fake\n"

argv = sys.argv[1:]
output = None
for index, item in enumerate(argv):
    if item == "-o" and index + 1 < len(argv):
        output = pathlib.Path(argv[index + 1])

path = pathlib.Path(argv[-1])
source = path.read_text(encoding="utf-8")

search = [part for part in os.environ.get("LEAN_PATH", "").split(os.pathsep) if part]


def marked(text: str) -> list[str]:
    found = MARKER.search(text)
    return [item.strip() for item in found.group(1).split(",") if item.strip()] if found else []


# What this elaboration would report: what the source itself declares, plus what
# every workspace module it imports already carried into its olean.
axioms = marked(source)
for line in source.splitlines():
    stripped = line.strip()
    if not stripped.startswith("import "):
        continue
    name = stripped.removeprefix("import ").strip()
    if name.split(".")[0] in BUILTIN:
        continue
    relative = pathlib.Path(*name.split(".")).with_suffix(".olean")
    found = next(
        (pathlib.Path(directory) / relative for directory in search
         if (pathlib.Path(directory) / relative).is_file()),
        None,
    )
    if found is None:
        print(f"{path.name}:1:0: error: unknown module prefix '{name}'")
        raise SystemExit(1)
    axioms.extend(
        item for item in marked(found.read_text(encoding="utf-8", errors="replace"))
        if item not in axioms
    )


def report_axioms() -> None:
    """Stand in for `#print axioms`, in both of real Lean's two forms."""
    for name in re.findall(r"#print axioms (\S+)", source):
        if axioms:
            print(f"'{name}' depends on axioms: [{', '.join(axioms)}]")
        else:
            print(f"'{name}' does not depend on any axioms")
    # `#print <name>` on its own prints the declaration Lean resolves.
    for name in re.findall(r"(?m)^#print (?!axioms )(\S+)", source):
        print(f"axiom {name} : True")


def write_olean() -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    # The marker rides along so an importer of this module sees the same axioms.
    trailer = f"-- axioms: {', '.join(axioms)}\n".encode() if axioms else b""
    output.write_bytes(OLEAN_PREFIX + trailer)


# A file that only imports and asks questions elaborates on its own -- real
# Lean is perfectly happy with a header and a `#print`, and that is the shape
# the interactive axiom audit sends over a workspace that is already built.
body = [
    line.strip()
    for line in source.splitlines()
    if line.strip() and not line.strip().startswith(("import ", "#", "--"))
]
if not body:
    report_axioms()
    raise SystemExit(0)

if "exact True.intro" in source and not HOLE.search(source):
    report_axioms()
    write_olean()
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
if "#check True.intro" in source:
    print("True.intro : True")
    raise SystemExit(0)
lookup = re.search(r"#check (\S+)", source)
if lookup:
    print(f"{lookup.group(1)} : (statement from fake Lean)")
    raise SystemExit(0)
print("Main.lean:3:28: error: type mismatch")
raise SystemExit(1)
