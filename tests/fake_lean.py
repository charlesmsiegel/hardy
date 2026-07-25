#!/usr/bin/env python3
import pathlib
import sys

# Real Lean writes UTF-8 whatever the console codepage is, and Hardy decodes it
# as UTF-8. This stand-in has to do the same or it cannot print a goal marker.
sys.stdout.reconfigure(encoding="utf-8")

source = pathlib.Path(sys.argv[-1]).read_text(encoding="utf-8")
if "exact True.intro" in source and "sorry" not in source and "admit" not in source:
    if "#print axioms" in source:
        print("'HardyTarget' depends on axioms: []")
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
if "#check True.intro" in source:
    print("True.intro : True")
    raise SystemExit(0)
print("Main.lean:3:28: error: type mismatch")
raise SystemExit(1)
