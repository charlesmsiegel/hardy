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
EXPORTS = re.compile(r"--\s*exports:\s*(.*)")
# A declaration another module can name. `private` deliberately makes one that
# nothing outside this file can reach, which is exactly the visibility a caller
# building `#print axioms` over an import has to respect.
DECLARED = re.compile(
    r"(?m)^\s*(?:(private|protected)\s+)?(?:theorem|lemma)\s+(«[^»\n]+»|\S+)"
)
# Everything else a module puts in the environment under a global name. Not a
# `#print axioms` target, but it still occupies the name -- two imported modules
# defining the same `helper` collide exactly as two theorems would, and a
# stand-in that only tracked theorems could not fail on that.
DEFINED = re.compile(
    r"(?m)^\s*(?:(private|protected)\s+)?(?:def|abbrev|structure|instance)\s+(«[^»\n]+»|\S+)"
)
OLEAN_PREFIX = b"olean-fake\n"

argv = sys.argv[1:]
output = None
for index, item in enumerate(argv):
    if item == "-o" and index + 1 < len(argv):
        output = pathlib.Path(argv[index + 1])

path = pathlib.Path(argv[-1])
source = path.read_text(encoding="utf-8")

# `-- slow: 30` makes this stand-in grind, so a test can stop it. Real Lean
# elaborating against Mathlib is the child Esc exists for, and a check that
# returns instantly cannot stand in for one. The touch file is how a test waits
# for the child to genuinely exist rather than guessing at a delay.
slow = re.search(r"--\s*slow:\s*([0-9.]+)", source)
if slow:
    import time

    ready = re.search(r"--\s*ready:\s*(\S+)", source)
    if ready:
        pathlib.Path(ready.group(1)).write_text("ready", encoding="utf-8")
    time.sleep(float(slow.group(1)))

search = [part for part in os.environ.get("LEAN_PATH", "").split(os.pathsep) if part]


def code_only(text: str) -> str:
    """`text` with comments and string bodies blanked, for the hole check.

    Real Lean does not read a `sorry` inside `-- rewrite this without sorry` or
    inside a string literal, so this stand-in must not either. Reading one
    there had two costs, in both directions: a finished proof mentioning the
    word was audited as resting on a hole, and a candidate that cannot
    elaborate at all was accepted as one that elaborates *with* a hole, because
    the branch below treats a hole as a successful elaboration.

    Newlines are kept so nothing shifts line for line. Block comments nest, as
    they do in Lean. Applied only to the hole check -- the `-- axioms:` marker a
    test drives this with is itself a comment, and blanking it before `marked`
    reads it would leave every fixture reporting no axioms at all.
    """
    out: list[str] = []
    depth = 0
    index = 0
    in_string = False
    while index < len(text):
        rest = text[index:]
        character = text[index]
        if not in_string and rest.startswith("/-"):
            depth += 1
            out.append("  ")
            index += 2
        elif depth and rest.startswith("-/"):
            depth -= 1
            out.append("  ")
            index += 2
        elif depth:
            out.append("\n" if character == "\n" else " ")
            index += 1
        elif not in_string and rest.startswith("--"):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
            out.append(" " * (end - index))
            index = end
        elif character == '"':
            in_string = not in_string
            out.append('"')
            index += 1
        elif in_string:
            # An escaped quote does not close the literal, and the escape has
            # to be consumed with it or the next character reopens one.
            step = 2 if character == "\\" and index + 1 < len(text) else 1
            out.append(" " * step)
            index += step
        else:
            out.append(character)
            index += 1
    return "".join(out)


def marked(text: str) -> list[str]:
    found = MARKER.search(text)
    return [item.strip() for item in found.group(1).split(",") if item.strip()] if found else []


def listed(pattern: re.Pattern[str], text: str) -> list[str]:
    found = pattern.search(text)
    return [item.strip() for item in found.group(1).split(",") if item.strip()] if found else []


# What this elaboration would report: what the source itself declares, plus what
# every workspace module it imports already carried into its olean.
axioms = marked(source)
# A literal `sorry` is what real Lean reports as `sorryAx`, so this stand-in
# has to as well. Without it a test could only fake a hole through the marker,
# which models a hole reached through an *import* and not one written here.
code = code_only(source)
if HOLE.search(code) and "sorryAx" not in axioms:
    axioms.append("sorryAx")
# What an importer of this file would be able to name. Private declarations are
# left out on purpose: Lean mangles them so no other module can refer to them.
exports = [
    name
    for modifier, name in DECLARED.findall(source) + DEFINED.findall(source)
    if modifier != "private"
]
visible: list[str] = []
# Names two different imported modules both export. Lean's environment maps a
# name to one declaration, so importing both is an error where the duplicate is
# found -- not later, at whichever name the importer happens to mention. A
# stand-in that only complained when the duplicate was *queried* would answer
# happily for a probe that real Lean refuses to start.
ambiguous: set[str] = set()
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
    carried = found.read_text(encoding="utf-8", errors="replace")
    axioms.extend(item for item in marked(carried) if item not in axioms)
    for item in listed(EXPORTS, carried):
        if item in visible:
            ambiguous.add(item)
        else:
            visible.append(item)

if ambiguous:
    print(
        f"{path.name}:1:0: error: import failed, environment already contains "
        f"'{sorted(ambiguous)[0]}'"
    )
    raise SystemExit(1)


def report_axioms() -> None:
    """Stand in for `#print axioms`, in both of real Lean's two forms."""
    # To end of line, not to the first space: `theorem «first result»` is an
    # ordinary Lean declaration and `\S+` would report half its name.
    for name in re.findall(r"(?m)^#print axioms (.+?)\s*$", source):
        # Asking about a name this file cannot see is an error, not silence --
        # a stand-in that answered anyway would hide the caller's real bug.
        # Matched on the last component too, because this stand-in does not
        # track namespaces and real Lean resolves `Hardy.one` for a `theorem
        # one` inside `namespace Hardy`. A private declaration never reaches
        # `exports` at all, so the check that matters here still bites.
        reachable = set(exports) | set(visible)
        if name not in reachable and name.rsplit(".", 1)[-1] not in reachable:
            print(f"{path.name}:1:0: error: unknown identifier '{name}'")
            raise SystemExit(1)
        if axioms:
            print(f"'{name}' depends on axioms: [{', '.join(axioms)}]")
        else:
            print(f"'{name}' does not depend on any axioms")
    # `#print <name>` on its own prints the declaration Lean resolves.
    for name in re.findall(r"(?m)^#print (?!axioms )(.+?)\s*$", source):
        print(f"axiom {name} : True")


def write_olean() -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    # The marker rides along so an importer of this module sees the same axioms.
    trailer = f"-- axioms: {', '.join(axioms)}\n".encode() if axioms else b""
    # And the names an importer may use, for the same reason.
    trailer += f"-- exports: {', '.join(exports)}\n".encode() if exports else b""
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

if "exact True.intro" in source and not HOLE.search(code):
    report_axioms()
    write_olean()
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
# A hole elaborates. Real Lean accepts `:= by sorry`, warns that the
# declaration uses it, and answers `sorryAx` when asked what it rests on --
# which is exactly how an interactive session holds an unfinished proof. A
# stand-in that refused the file could not exercise that at all.
#
# After `trace_state`, because the goal probe is built as `by trace_state
# sorry` and its caller wants the goal printed rather than an olean written.
if HOLE.search(code):
    print(f"{path.name}:1:0: warning: declaration uses 'sorry'")
    report_axioms()
    write_olean()
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
