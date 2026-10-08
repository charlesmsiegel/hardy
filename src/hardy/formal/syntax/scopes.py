"""Which namespace is open where: the scope walk the declaration scans share."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping

from hardy.formal.syntax.names import _components

# The commands that open and close a scope. `noncomputable section` is a
# `section` token like any other, and `mutual ... end` is a scope a bare `end`
# closes; missing either let that `end` close the namespace around it.
SCOPE_KEYWORDS = frozenset({"namespace", "section", "end", "mutual"})
# Words that are never the optional name of a `section` or an `end`, because
# they begin the next command. Lean knows its keywords from its token table;
# this is the list a Hardy workspace or Mathlib puts after a scope command.
NOT_A_SCOPE_NAME = frozenset({
    "abbrev", "add_decl_doc", "alias", "assert_not_exists", "attribute", "axiom", "class",
    "declare_syntax_cat", "def", "deriving", "elab", "elab_rules", "end", "example",
    "export", "import", "include", "inductive", "infix", "infixl", "infixr", "initialize",
    "instance", "irreducible_def", "lemma", "library_note", "local", "macro", "macro_rules",
    "meta", "mutual", "namespace", "noncomputable", "nonrec", "notation", "omit", "opaque",
    "open", "partial", "postfix", "prefix", "private", "protected", "public", "run_cmd",
    "scoped", "section", "set_option", "structure", "suppress_compilation", "syntax",
    "theorem", "universe", "unsafe", "variable",
})


def _scopes(text: str, tokens: Mapping[int, int]) -> list[tuple[int, tuple[str, ...]]]:
    """The namespace prefix in force from each offset of an already-stripped source on.

    Returned as `(offset, prefix)` marks in order; `_prefix_at` reads one. Read
    from the token stream rather than line by line, because `end Foo theorem t`
    closes `Foo` before `t` is declared, and a walk that recognised a scope
    command only when it filled its line qualified `t` as `Foo.t` -- a name Lean
    never gave anything, so the audit asked about the wrong declaration.
    Callers blank bounded syntax quotations first, so `` `(command| namespace
    Bar) `` is data rather than a scope; a projection (`(i).end`) is never a
    token here at all.

    Every kind of scope, because a bare `end` closes whichever is innermost and
    only a namespace contributes to a name. Tracking namespaces alone would let
    `section ... end` pop a namespace that is still open, and every later
    declaration would be recorded under a name Lean never gave it. `namespace
    A.B` opens one scope per component, as Lean does: `end B` then closes only
    the inner one and `end A.B` both.

    `namespace` always takes the identifier after it: Lean requires one, and
    `namespace constant` is ordinary Lean. An `end` or a `section` takes the
    identifier after it when it is on the same line, or on a later one indented
    past the keyword (Lean's `checkColGt`); an `end` takes it whenever it names
    a scope that is open, and otherwise only when it is not a command keyword.

    One copy, shared by the declaration scan and the assumption scan. They had
    a walk each, and the pair drifted twice: the second defined its own
    `NAMESPACE`/`END` that silently replaced the first's at import time --
    dropping indented and guillemet-quoted namespaces from *both* -- and never
    popped on a bare `end`, so every axiom after one was qualified by a
    namespace that had closed.
    """
    scope: list[tuple[str, str | None]] = []
    marks: list[tuple[int, tuple[str, ...]]] = [(0, ())]
    starts = sorted(tokens)
    position = 0
    while position < len(starts):
        start = starts[position]
        word = text[start : tokens[start]]
        position += 1
        if word not in SCOPE_KEYWORDS:
            continue
        name = None
        after = tokens[start]
        if word != "mutual" and position < len(starts):
            following = starts[position]
            candidate = text[following : tokens[following]]
            gap = text[after:following]
            adjacent = not gap.strip() and (
                "\n" not in gap or _column(text, following) > _column(text, start)
            )
            if adjacent and (
                word == "namespace"
                or (word == "end" and _names_open_scope(scope, candidate))
                or candidate not in NOT_A_SCOPE_NAME
            ):
                name = candidate
                after = tokens[following]
                position += 1
        if word == "namespace":
            if name is not None:
                scope.extend(("namespace", part) for part in _components(name))
        elif word in {"section", "mutual"}:
            scope.append((word, name))
        elif name is None:
            if scope:
                scope.pop()
        else:
            _close(scope, name)
        marks.append((after, tuple(item for kind, item in scope if kind == "namespace" and item)))
    return marks


def _names_open_scope(scope: list[tuple[str, str | None]], name: str) -> bool:
    parts = _components(name)
    names = [item for _, item in scope]
    return any(names[index : index + len(parts)] == parts for index in range(len(names)))


def _close(scope: list[tuple[str, str | None]], name: str) -> None:
    """Close the scope a named `end` names, and anything still open inside it."""
    parts = _components(name)
    for index in range(len(scope) - 1, -1, -1):
        first = index - len(parts) + 1
        if first >= 0 and [item for _, item in scope[first : index + 1]] == parts:
            del scope[first:]
            return
        if scope[index][1] == name:
            del scope[index:]
            return


def _column(text: str, offset: int) -> int:
    return offset - (text.rfind("\n", 0, offset) + 1)


def _prefix_at(marks: list[tuple[int, tuple[str, ...]]], offset: int) -> tuple[str, ...]:
    """The namespace prefix `_scopes` says is in force at `offset`."""
    return marks[bisect_right(marks, offset, key=lambda mark: mark[0]) - 1][1]
