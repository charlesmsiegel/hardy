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
# Which of a module's declarations carry a literal hole. Written into the olean
# beside the axiom marker, because the audit elaborates a file that *imports*
# the module: without it, per-declaration attribution never reaches the probe
# that asks, and every export of a module with one hole reports `sorryAx`.
# Present only when there is at least one, so its absence means "no holes here".
HOLED_MARK = re.compile(r"--\s*holed:\s*(.*)")
EXPORTS = re.compile(r"--\s*exports:\s*(.*)")
# A declaration another module can name. `private` deliberately makes one that
# nothing outside this file can reach, which is exactly the visibility a caller
# building `#print axioms` over an import has to respect.
DECLARED = re.compile(
    r"(?m)^\s*(?:(private|protected)\s+)?(?:theorem|lemma)\s+(«[^»\n]+»|\S+)"
)
# Where any declaration begins, used to cut the source into one chunk each.
OPENS = re.compile(
    r"(?m)^[ \t]*(?:(?:private|protected|noncomputable|nonrec)\s+)*"
    r"(?:theorem|lemma|def|abbrev|instance|structure|example)\b"
)
# A proof body this stand-in is willing to call elaborated. Everything else is
# a type error, which is what keeps "only the hole is forgiven" honest: a file
# whose hole sits beside a broken proof must not pass because of the hole.
SETTLED = re.compile(r"\b(?:exact True\.intro|trivial|rfl|sorry|admit)\b")
# Everything else a module puts in the environment under a global name. Not a
# `#print axioms` target, but it still occupies the name -- two imported modules
# defining the same `helper` collide exactly as two theorems would, and a
# stand-in that only tracked theorems could not fail on that.
DEFINED = re.compile(
    r"(?m)^\s*(?:(private|protected)\s+)?(?:def|abbrev|structure|instance)\s+(«[^»\n]+»|\S+)"
)
OLEAN_PREFIX = b"olean-fake\n"
# An axiom or an opaque constant: a declaration with no body to elaborate.
DECLARES_AXIOM = re.compile(r"^\s*(?:axiom|opaque|constant)\s")
# The lines that only open or close a scope, which carry nothing to check.
SCOPING = re.compile(r"^\s*(?:namespace|end|section|open|universe|variable)\b")

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
code = code_only(source)

# `import` is a header command: real Lean refuses one that comes after any
# other content, and a file it refuses is not a file Hardy may accept. The
# stand-in was blind to position, so a renderer that put `axiom` above
# `import Mathlib` -- which real Lean rejects outright -- passed every test
# and shipped. Read over the comment-blanked text, because the word inside a
# docstring is not a command.
seen_content = False
for number, line in enumerate(code.splitlines(), start=1):
    stripped = line.strip()
    if not stripped:
        continue
    if stripped.startswith("import "):
        if seen_content:
            print(
                f"{path.name}:{number}:0: error: invalid 'import' command, "
                "it must be used in the beginning of the file"
            )
            raise SystemExit(1)
        continue
    seen_content = True


def qualifiers(text: str) -> list[tuple[int, str]]:
    """The namespace prefix in force at each offset, as (offset, prefix).

    `A.t` and `B.t` are two declarations, and real `#print axioms B.t` answers
    about the finished one. Matching by last component alone marked both open
    the moment either carried a hole, so the prefix has to be tracked -- which
    means tracking `end` too, since a bare one closes whichever scope is
    innermost.
    """
    scope: list[str] = []
    marks: list[tuple[int, str]] = []
    for match in re.finditer(r"(?m)^[ \t]*(namespace|section|end)\b[ \t]*(\S*)", text):
        keyword, name = match.group(1), match.group(2)
        if keyword == "namespace" and name:
            scope.append(name)
        elif keyword == "section":
            scope.append("")
        elif keyword == "end" and scope:
            scope.pop()
        marks.append((match.end(), ".".join(part for part in scope if part)))
    return marks


def prefix_at(marks: list[tuple[int, str]], offset: int) -> str:
    found = ""
    for at, prefix in marks:
        if at <= offset:
            found = prefix
        else:
            break
    return found


def carries(wanted: str, names: set[str] | list[str], among: list[str]) -> bool:
    """Whether `wanted` is one of `names`, by full name or by its sole leaf.

    The leaf fallback is only sound while exactly one declaration in `among`
    carries that last component -- putting leaves into the set outright brought
    a finished `B.t` within reach of `A.t`'s hole, which is the conflation this
    exists to prevent.
    """
    if wanted in names:
        return True
    if "." in wanted:
        return False
    leaf = wanted.rsplit(".", 1)[-1]
    sharing = [item for item in among if item.rsplit(".", 1)[-1] == leaf]
    return len(sharing) == 1 and sharing[0] in names


def spans(text: str) -> list[tuple[str | None, str]]:
    """Each declaration in `text`, as the name Lean would print and its body.

    A declaration runs to the start of the next one, which is as much structure
    as this stand-in needs: it is deciding what a body *looks* like, not
    elaborating it. The name carries its namespace, because two declarations
    may share a last component and only one of them may have the hole.
    """
    marks = qualifiers(text)
    starts = [match.start() for match in OPENS.finditer(text)]
    found: list[tuple[str | None, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chunk = text[start:end]
        named = DECLARED.search(chunk) or DEFINED.search(chunk)
        if named is None:
            found.append((None, chunk))
            continue
        # Guillemets are kept: `theorem «first result»` is printed by Lean, and
        # asked about, with them. Stripping them exported a name no probe could
        # resolve.
        bare = named.group(2)
        prefix = prefix_at(marks, start)
        found.append((f"{prefix}.{bare}" if prefix else bare, chunk))
    return found


# Which declarations carry a hole, rather than whether the module does. Real
# `#print axioms` answers about one declaration, so a stand-in that attached
# `sorryAx` to the whole module marked a finished theorem open whenever an
# unfinished one sat beside it -- and every test about closing one hole while
# another stays open would have been exercising the opposite of production.
holed = {name for name, body in spans(code) if name and HOLE.search(body)}
# Every declaration this file states, for the leaf fallback below: a bare name
# may stand for a qualified declaration only while exactly one carries it.
stated = [name for name, _ in spans(code) if name]
# The olean stays module-wide: an importer of a file with any hole in it is
# reaching into a module that rests on one, and this stand-in's import model
# has no finer grain than the module to offer.
#
# `from_hole` records that the hole written *here* is the only thing putting
# `sorryAx` in this list. A marker saying `-- axioms: sorryAx`, or an import
# carrying one, is module-wide by this stand-in's model and applies to every
# declaration -- so only the literal case may be narrowed per declaration.
# `plain` records that something said `sorryAx` without saying *which*
# declaration carries it -- an `-- axioms:` marker here, or an imported olean
# with no attribution. Then nothing may be narrowed: the stand-in has been told
# the module rests on a hole and not where.
plain = "sorryAx" in axioms
if HOLE.search(code) and "sorryAx" not in axioms:
    axioms.append("sorryAx")
# Holes reached through an import, and whether any import carries one at all.
# A declaration *here* may use an imported holed one, and this stand-in has no
# dependency graph to say which -- so when an import carries a hole, everything
# this file declares reports it. That is the direction that over-reports.
imported_holes: set[str] = set()
imports_holed = False
# What an importer of this file would be able to name, under the name Lean
# would print. Private declarations are left out on purpose: Lean mangles them
# so no other module can refer to them. Read from `code`, so a `theorem`
# written inside a comment or a string exports nothing -- it is not a
# declaration, and offering it as one would let a probe ask Lean about a name
# that does not exist.
#
# Qualified, like `holed` beside it: `A.t` and `B.t` are two exports, and a
# bare list reported them as one name declared twice, which this stand-in
# refuses as an ambiguous import.
exports = [
    name
    for name, chunk in spans(code)
    if name and not re.match(r"\s*private\b", chunk)
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
    carried_holes = listed(HOLED_MARK, carried)
    if carried_holes:
        imports_holed = True
        imported_holes.update(carried_holes)
    elif "sorryAx" in marked(carried):
        plain = True
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
        # Per declaration, like the real thing. `sorryAx` is added for the one
        # that actually carries the hole, not for everything the module exports.
        reported = list(axioms)
        wanted = name
        leaf = wanted.rsplit(".", 1)[-1]
        # By full name. A bare query falls back to the last component only while
        # exactly one declaration here carries it, which is the rule the product
        # reads its own registry by.
        rests = (
            carries(wanted, holed, stated)
            or carries(wanted, imported_holes, sorted(imported_holes))
            # Declared here, beside an import that carries a hole: this
            # stand-in cannot tell whether it uses one, so it says it does.
            or (imports_holed and leaf in {item.rsplit(".", 1)[-1] for item in exports})
        )
        if "sorryAx" in reported and not plain and not rests:
            reported.remove("sorryAx")
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
        if reported:
            print(f"'{name}' depends on axioms: [{', '.join(reported)}]")
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
    # Which declarations the hole belongs to, so an importer's probe can answer
    # about one of them rather than about the module.
    trailer += f"-- holed: {', '.join(sorted(holed))}\n".encode() if holed else b""
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

# Read over comment-blanked text, because a doc comment is not a declaration:
# the generated module states each axiom under a `/-- ... -/` naming the paper,
# and prose inside one is not something Lean elaborates.
#
# A file whose only declarations are axioms elaborates. Real Lean accepts
# `axiom foo : True` with no proof to check -- that is what an axiom is -- and
# the generated `Papers/` module is exactly that shape, so a stand-in that
# called it a type mismatch would make an approved assumption unsavable while
# real Lean saved it happily.
declared_only = [
    line.strip()
    for line in code.splitlines()
    if line.strip() and not line.strip().startswith(("import ", "#"))
]
#
# Behind the hole check, not in front of it: `axiom foo : (sorry : Prop)` is a
# hole wearing a declaration's clothes, and a bypass that ran first would have
# reported it as resting on nothing at all.
#
# What this deliberately does NOT do is resolve the names in an axiom's type --
# no stand-in here has an environment to resolve them against -- so a test
# whose subject is whether a type exists needs the real toolchain, not this.
if (
    declared_only
    and not HOLE.search(code)
    and all(DECLARES_AXIOM.match(line) or SCOPING.match(line) for line in declared_only)
):
    report_axioms()
    write_olean()
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
    # Only the hole is forgiven. A declaration beside it whose body this
    # stand-in does not recognise is still a type error, or a holed fixture
    # would be accepted here while real Lean rejected the file -- and the
    # feature's whole claim is that the file must otherwise elaborate.
    broken = [name for name, chunk in spans(code) if not SETTLED.search(chunk)]
    if broken:
        print(f"{path.name}:3:28: error: type mismatch in {broken[0]}")
        raise SystemExit(1)
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
