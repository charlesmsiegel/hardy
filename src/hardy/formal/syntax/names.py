"""Lean names: identifier patterns, qualification and the aliases a registry may use.

The patterns every other scan builds on, and the one answer to what Lean
calls a declaration written under a namespace. Depends on nothing else here.
"""
from __future__ import annotations

import re

# Lean identifiers are Unicode: `theorem α` and `theorem h₁` are ordinary, and
# an ASCII-only pattern would not see them -- so a theorem could be saved that
# never appeared in the listing and never owed a writeup. `[^\W\d]` is "any
# letter or underscore" under Python's Unicode-aware `\w`.
IDENTIFIER = r"[^\W\d][\w'!?]*"
# `theorem «first result»` is a valid declaration: Lean lets guillemets quote a
# name containing anything, spaces included. A pattern that could not see one
# would leave that theorem out of the listing, so it would never owe a writeup.
ESCAPED = r"«[^»\n]+»"
ANY_NAME = rf"(?:{IDENTIFIER}|{ESCAPED})"
QUALIFIED = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
QUALIFIED_NAME = rf"{ANY_NAME}(?:\.{ANY_NAME})*"

# Module and path components stay unescaped: they are file names on disk, and a
# guillemet in one is not something to invite.
COMPONENT = re.compile(IDENTIFIER)
MODULE = re.compile(QUALIFIED)


def declared_name(name: str, prefix: tuple[str, ...] = ()) -> str:
    """The name Lean will report for a declaration written as `name`.

    `_root_.` is how a declaration says it is not in the namespace it sits in,
    so the prefix is dropped rather than prepended -- kept, the audit asks
    `#print axioms` about a name Lean never declared, and the module can never
    be saved. One function because three callers need this answer and each one
    that grew its own copy became a bug.
    """
    if name.startswith("_root_."):
        return name.removeprefix("_root_.")
    return ".".join((*prefix, name)) if prefix else name


def name_aliases(name: str) -> tuple[str, ...]:
    """The names a registry entry might reasonably use for one declaration.

    `Hardy.one` and `one` denote the same theorem, and a registry recording
    either must count as recording it. Only the last component is offered, not
    every suffix: `Hardy.Group.one` abbreviated to `Group.one` is not a name
    Lean would resolve from the root.
    """
    if "." not in name:
        return (name,)
    return (name, name.rsplit(".", 1)[1])


def _components(name: str) -> list[str]:
    """`A.«b.c».D` as `["A", "«b.c»", "D"]`: a guillemet may hold a dot."""
    return re.findall(ANY_NAME, name)
