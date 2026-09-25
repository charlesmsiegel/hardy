"""Pure Lean source scanning and module dependency graphs.

These lexical checks preserve the source identity; they do not elaborate
or verify it. Workspace builds and document readers share this one grammar.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath

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
# Lean's module system spells an import `public import X` or `meta import X`,
# and opens such a file with `module`. Neither ends the header.
IMPORT_PREFIX = re.compile(r"^(?:(?:public|meta|private|protected)\s+)*")
HEADER_KEYWORDS = frozenset({"prelude", "module"})
# Scanned over the whole source rather than line by line, because Lean allows a
# newline between the keyword and the name and a line-oriented match would lose
# the declaration entirely.
# `set_option x y in`, `open Foo in`, `attribute [...] in` -- Lean's way of
# scoping one command. What follows is an ordinary declaration, and a scanner
# that stops at the wrapper misses it: an unseen theorem is one the audit never
# asks about, and an unseen axiom is one whose statement is never compared
# against what a human approved.
WRAPPER = r"(?:(?:set_option|open|attribute|universe|variable|section)\b[^\n]*?\sin\s+)*"
# Not anchored to the start of a line. Lean commands are whitespace-insensitive,
# so `def a := 1 theorem sneaky : False := sorry` declares `sneaky` as surely as
# a line of its own does, and so do `end Foo theorem t` and `include h in
# theorem t`. A scan that looked only at line starts never asked the audit about
# such a theorem, never reserved it to a registered result, and never counted
# it towards the writeup ratchet. Where a match may start is decided instead by
# Lean's own token boundaries (`identifier_tokens`), so `«a theorem b»`,
# `mytheorem` and `Foo.theorem` are names while `1theorem` -- a numeral and
# then a keyword, to Lean -- is a declaration. `theorem«name»` needs no space.
DECLARATION = re.compile(
    rf"{WRAPPER}(?:@\[[^\]]*\]\s*)*((?:(?:private|protected|nonrec|noncomputable)\s+)*)"
    rf"(theorem|lemma)(?:\s+|(?=«))({QUALIFIED_NAME})"
)
# `private` is the one modifier that changes who can name a declaration: Lean
# mangles the name so no importing module can reach it. Anything that has to
# address a declaration from outside its own file -- the axiom audit does --
# needs to know which ones those are.
PRIVATE = re.compile(r"(?:^|\s)private(?:\s|$)")
# The commands that open and close a scope. `noncomputable section` is a
# `section` token like any other, and `mutual ... end` is a scope a bare `end`
# closes; missing either let that `end` close the namespace around it.
SCOPE_KEYWORDS = frozenset({"namespace", "section", "end", "mutual"})
# Words that are never the optional name of a `section` or an `end`, because
# they begin the next command. Lean knows its keywords from its token table;
# this is the list a Hardy workspace or Mathlib puts after a scope command.
NOT_A_SCOPE_NAME = frozenset({
    "abbrev", "add_decl_doc", "alias", "assert_not_exists", "attribute", "axiom", "class",
    "constant", "declare_syntax_cat", "def", "deriving", "elab", "elab_rules", "end", "example",
    "export", "import", "include", "inductive", "infix", "infixl", "infixr", "initialize",
    "instance", "irreducible_def", "lemma", "library_note", "local", "macro", "macro_rules",
    "meta", "mutual", "namespace", "noncomputable", "nonrec", "notation", "omit", "opaque",
    "open", "partial", "postfix", "prefix", "private", "protected", "public", "run_cmd",
    "scoped", "section", "set_option", "structure", "suppress_compilation", "syntax",
    "theorem", "universe", "unsafe", "variable",
})

Compile = Callable[[str, Path, Path, Path], tuple[bool, str]]


class WorkspacePathError(ValueError):
    """A path that is not a Lean module inside the workspace."""


class ImportCycle(ValueError):
    """Workspace modules that import each other."""


def safe_relative(path: str) -> PurePosixPath:
    """The workspace-relative path `path` denotes, or a refusal.

    Everything that could reach outside the tree, or name a file Lean would not
    accept as a module, is refused here rather than at the filesystem: a tool
    argument is model output and gets no benefit of the doubt.
    """
    relative = PurePosixPath(str(path).replace("\\", "/"))
    if relative.is_absolute() or not relative.name.endswith(".lean"):
        raise WorkspacePathError(f"not a workspace Lean path: {path!r}")
    parts = relative.parts
    if not parts or any(part in {"..", "."} for part in parts):
        raise WorkspacePathError(f"path escapes the workspace: {path!r}")
    for part in (*parts[:-1], parts[-1][: -len(".lean")]):
        if not COMPONENT.fullmatch(part):
            raise WorkspacePathError(f"not a Lean identifier: {part!r} in {path!r}")
    return relative


def module_name(relative: PurePosixPath) -> str:
    return ".".join((*relative.parts[:-1], relative.name[: -len(".lean")]))


def module_path(name: str) -> PurePosixPath:
    return PurePosixPath(*name.split(".")).with_suffix(".lean")


def _olean_relative(name: str) -> PurePosixPath:
    """Where a module's compiled artifact sits under a build directory."""
    return PurePosixPath(*name.split(".")).with_suffix(".olean")


def _olean_module(relative: PurePosixPath) -> str:
    """The module an artifact path names, the inverse of `_olean_relative`.

    Not `module_name`: that one strips exactly the five characters of `.lean`,
    so `Foo.olean` came back as the module `Foo.o` and an orphan sweep keyed on
    it would delete nothing while reporting a name nobody recognises.
    """
    return ".".join((*relative.parts[:-1], relative.name.removesuffix(".olean")))


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


def _scopes(text: str, tokens: Mapping[int, int]) -> list[tuple[int, tuple[str, ...]]]:
    """The namespace prefix in force from each offset of an already-stripped source on.

    Returned as `(offset, prefix)` marks in order; `_prefix_at` reads one. Read
    from the token stream rather than line by line, because `end Foo theorem t`
    closes `Foo` before `t` is declared, and a walk that recognised a scope
    command only when it filled its line qualified `t` as `Foo.t` -- a name Lean
    never gave anything, so the audit asked about the wrong declaration.

    Every kind of scope, because a bare `end` closes whichever is innermost and
    only a namespace contributes to a name. Tracking namespaces alone would let
    `section ... end` pop a namespace that is still open, and every later
    declaration would be recorded under a name Lean never gave it. `namespace
    A.B` opens one scope per component, as Lean does: `end B` then closes only
    the inner one and `end A.B` both.

    An `end` or a `section` takes the identifier after it as its name when it
    is on the same line, or on a later one indented past the keyword (Lean's
    `checkColGt`); a keyword is never a name.

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
            if (
                not gap.strip()
                and candidate not in NOT_A_SCOPE_NAME
                and ("\n" not in gap or _column(text, following) > _column(text, start))
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


def _components(name: str) -> list[str]:
    """`A.«b.c».D` as `["A", "«b.c»", "D"]`: a guillemet may hold a dot."""
    return re.findall(ANY_NAME, name)


def _column(text: str, offset: int) -> int:
    return offset - (text.rfind("\n", 0, offset) + 1)


def _prefix_at(marks: list[tuple[int, tuple[str, ...]]], offset: int) -> tuple[str, ...]:
    """The namespace prefix `_scopes` says is in force at `offset`."""
    return marks[bisect_right(marks, offset, key=lambda mark: mark[0]) - 1][1]


def _number_end(text: str, index: int) -> int:
    """Where the numeral starting at `index` ends, by Lean 4.35's grammar.

    `0x`, `0b` and `0o` literals, and decimals with `_` separators, a fraction
    and an exponent. The end matters because a keyword may follow a numeral
    directly -- `1theorem x` declares `x` -- while `0xdef` is one hex numeral
    and declares nothing.
    """
    length = len(text)
    radix = {"0x": "0123456789abcdefABCDEF_", "0b": "01_", "0o": "01234567_"}.get(
        text[index : index + 2].lower()
    )
    if radix is not None:
        end = index + 2
        while end < length and text[end] in radix:
            end += 1
        return end
    end = _digits_end(text, index)
    if end < length and text[end] == "." and (
        _is_digit(text, end + 1) or _exponent_end(text, end + 1) is not None
    ):
        end = _digits_end(text, end + 1)
    exponent = _exponent_end(text, end)
    return end if exponent is None else exponent


def _is_digit(text: str, index: int) -> bool:
    return index < len(text) and "0" <= text[index] <= "9"


def _digits_end(text: str, index: int) -> int:
    while index < len(text) and (_is_digit(text, index) or text[index] == "_"):
        index += 1
    return index


def _exponent_end(text: str, index: int) -> int | None:
    if index >= len(text) or text[index] not in "eE":
        return None
    index += 1
    if index < len(text) and text[index] in "+-":
        index += 1
    return _digits_end(text, index) if _is_digit(text, index) else None


def _component_end(text: str, index: int) -> int | None:
    """Where the name component starting at `index` ends, or None if none does."""
    character = text[index]
    if character == "«":
        closing = text.find("»", index + 1)
        newline = text.find("\n", index + 1)
        if closing > index + 1 and (newline == -1 or closing < newline):
            return closing + 1
        return None
    if not (character.isalpha() or character == "_"):
        return None
    end = index + 1
    while end < len(text) and (text[end].isalnum() or text[end] in "_'!?"):
        end += 1
    return end


def identifier_tokens(text: str) -> dict[int, int]:
    """Start -> end of every identifier or keyword token in already-stripped text.

    A small forward tokenizer rather than a lookbehind, because whether a
    keyword starts a token depends on what came before it in a way no
    fixed-width assertion sees: `x1theorem` is one identifier, `1theorem` is a
    numeral and then `theorem`, `0xdef` is a numeral, and `Foo.theorem` is a
    dotted name. Strings, comments and char literals are already blank, and
    `«...»` components are part of the name they sit in.
    """
    tokens: dict[int, int] = {}
    index = 0
    length = len(text)
    while index < length:
        if _is_digit(text, index):
            index = _number_end(text, index)
            continue
        end = _component_end(text, index)
        if end is None:
            index += 1
            continue
        start = index
        while end + 1 < length and text[end] == ".":
            following = _component_end(text, end + 1)
            if following is None:
                break
            end = following
        tokens[start] = end
        index = end
    return tokens


def _keyword_matches(
    text: str, pattern: re.Pattern[str], tokens: Mapping[int, int]
) -> list[re.Match[str]]:
    """Every match of `pattern` whose first word and keyword (group 2) are tokens.

    Searched from each rejected start plus one, not from its end, so a match
    that began inside a name cannot swallow a real declaration after it.
    """
    found: list[re.Match[str]] = []
    position = 0
    while (match := pattern.search(text, position)) is not None:
        start = match.start()
        if tokens.get(match.start(2)) == match.end(2) and (
            start in tokens or _component_end(text, start) is None
        ):
            found.append(match)
            position = max(match.end(), start + 1)
        else:
            position = start + 1
    return found


def _continues_identifier(character: str) -> bool:
    """Whether `character`, just before a token, would make it part of a name.

    `for"` is the identifier `for` and then a string, not a raw-string opener,
    and `x'` is the identifier `x'`, not `x` and then a char literal.
    """
    return character.isalnum() or character in "_'!?."


def _raw_string_opener(source: str, index: int) -> int | None:
    """The hash count of a raw-string opener at `index`, or None.

    Lean writes raw strings `r"..."`, `r#"..."#`, `r##"..."##`. The `r` must be
    a token of its own -- `for"` is not an opener -- so the character before it
    may not continue an identifier.
    """
    if source[index] != "r":
        return None
    if index and _continues_identifier(source[index - 1]):
        return None
    hashes = 0
    while index + 1 + hashes < len(source) and source[index + 1 + hashes] == "#":
        hashes += 1
    if index + 1 + hashes >= len(source) or source[index + 1 + hashes] != '"':
        return None
    return hashes


# What may follow a backslash in a Lean char literal, besides `x` with two hex
# digits and `u` with four. Lean 4.35 refuses anything else (`'\0'`,
# `'\u{41}'`), and a literal Lean refuses is not one this lexer may blank.
_SIMPLE_ESCAPES = frozenset("\\\"'nrt")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _char_literal_end(source: str, index: int) -> int | None:
    """Where the char literal opening at `index` ends (exclusive), or None.

    `'"'` is one `Char`. A lexer that knew only strings read its `"` as an
    opener and blanked everything to the next `"` in the file -- a theorem, an
    axiom and a `sorry` all vanished from every scan built on this one.

    Only what Lean itself reads as a character is recognised, checked against
    Lean 4.35.0-rc3. The quote must start a token, by the rule
    `_raw_string_opener` uses: after an identifier character it continues the
    name (`x'`, `f''`, `add_comm'`). It may not be followed by another quote
    (`''` is not a literal; Mathlib's `f '' s` is an image). Then exactly one
    code point, or one backslash escape, and the closing quote. Anything else
    is left alone: recognising a literal Lean does not see would blank text
    that Lean reads as code.
    """
    length = len(source)
    if source[index] != "'" or (index and _continues_identifier(source[index - 1])):
        return None
    body = index + 1
    if body >= length or source[body] == "'":
        return None
    if source[body] != "\\":
        close = body + 1
    else:
        escape = source[body + 1 : body + 2]
        digits = {"x": 2, "u": 4}.get(escape)
        if digits is not None:
            code = source[body + 2 : body + 2 + digits]
            if len(code) != digits or not set(code) <= _HEX_DIGITS:
                return None
            close = body + 2 + digits
        elif escape and escape in _SIMPLE_ESCAPES:
            close = body + 2
        else:
            return None
    if close < length and source[close] == "'":
        return close + 1
    return None


def strip_comments(source: str, *, keep_strings: bool = False) -> str:
    """`source` with its comments blanked out, line structure preserved.

    One pass serves both the import scan and the declaration scan, because
    both were getting comments wrong in their own way: a trailing `--` hid an
    import, a nested `/- /- -/ -/` closed early, and `/-- doc -/ theorem foo`
    hid a declaration behind a leading comment. Lean treats all of that as
    whitespace, so the honest fix is to do the same once, rather than teach
    every regex about comments separately.

    Comments are replaced by spaces rather than removed so that line and
    column positions still line up with the source a reader has open.

    String literals are blanked rather than merely skipped, for the same reason
    and in the other direction: a `--` inside one must not start a comment, and
    a line reading `theorem fake : True` inside a multiline string must not be
    reported as a declaration. It is not one, and a caller that has to *name*
    every declaration -- the axiom audit does -- would ask Lean about something
    that does not exist and refuse the file forever.

    `keep_strings` copies literals through instead, for the caller that has to
    *compare* two pieces of Lean rather than scan one: with strings blanked,
    `"a" = "a"` and `"b" = "b"` are the same run of spaces, so a writeup could
    quote a proposition about different values and pass for quoting this one.
    That caller (`statements`) never scans with it -- it finds declarations on
    the blanked text and only reads their extent with strings intact, so a
    `theorem` inside a string still cannot invent a declaration.
    """
    out = list(source)
    index = 0
    depth = 0
    length = len(source)
    while index < length:
        character = source[index]
        if depth:
            if source.startswith("/-", index):
                depth += 1
                out[index] = out[index + 1] = " "
                index += 2
                continue
            if source.startswith("-/", index):
                depth -= 1
                out[index] = out[index + 1] = " "
                index += 2
                continue
            if character != "\n":
                out[index] = " "
            index += 1
            continue
        if character == "«":
            # A guillemet-quoted identifier is one token, and `--` inside it is
            # part of the name. Blanking from there left `theorem «result` and
            # no declaration at all, so the module recorded "not established"
            # and saved anyway -- an ordinary literal theorem slipping past both
            # the audit and the writeup ratchet. Copied through rather than
            # blanked, because unlike a string this *is* the name the scan needs.
            closing = source.find("»", index + 1)
            newline = source.find("\n", index + 1)
            if closing != -1 and (newline == -1 or closing < newline):
                index = closing + 1
                continue
        raw = _raw_string_opener(source, index)
        if raw is not None and keep_strings:
            closer = '"' + "#" * raw
            found = source.find(closer, index + 1 + raw + 1)
            index = length if found == -1 else found + len(closer)
            continue
        if raw is not None:
            # `r"..."`, `r#"..."#`, `r##"..."##`. A backslash is an ordinary
            # character here, and the literal ends only at a quote followed by
            # the same run of hashes -- so a bare `"` inside `r#"..."#` does not
            # end it, and a trailing `\` does not escape the one that does.
            closer = '"' + "#" * raw
            for offset in range(1 + raw + 1):
                out[index + offset] = " "
            index += 1 + raw + 1
            while index < length and not source.startswith(closer, index):
                if source[index] != "\n":
                    out[index] = " "
                index += 1
            for offset in range(len(closer)):
                if index + offset < length:
                    out[index + offset] = " "
            index = min(index + len(closer), length)
            continue
        literal = _char_literal_end(source, index)
        if literal is not None:
            # A char literal is a literal like a string: blanked, or copied
            # through, by the same rule. Its newline -- `'` newline `'` is a
            # character -- stays a newline so no line moves.
            if not keep_strings:
                for position in range(index, literal):
                    if source[position] != "\n":
                        out[position] = " "
            index = literal
            continue
        if character == '"' and keep_strings:
            index = _string_end(source, index)
            continue
        if character == '"':
            out[index] = " "
            index += 1
            while index < length:
                if source[index] == "\\":
                    # The escape and whatever it escapes, both blanked -- but a
                    # newline stays a newline, or every position after a
                    # line-continuation would shift.
                    for offset in (0, 1):
                        if index + offset < length and source[index + offset] != "\n":
                            out[index + offset] = " "
                    index += 2
                    continue
                if source[index] == '"':
                    out[index] = " "
                    index += 1
                    break
                if source[index] != "\n":
                    out[index] = " "
                index += 1
            continue
        if source.startswith("/-", index):
            depth = 1
            out[index] = out[index + 1] = " "
            index += 2
            continue
        if source.startswith("--", index):
            while index < length and source[index] != "\n":
                out[index] = " "
                index += 1
            continue
        index += 1
    return "".join(out)


def _string_end(text: str, start: int) -> int:
    """Scan past an ordinary quoted Lean string, including escaped quotes.

    An unterminated escape may step one character past EOF, as both callers
    historically did; slicing still preserves all available source text.
    """
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return index


def normalise_lean(text: str) -> str:
    """Lean with its whitespace collapsed -- outside string literals.

    Two pieces of Lean that differ only in how they were wrapped are the same
    Lean, which is what lets a paper break a long statement across lines. Two
    that differ *inside* a literal are not: `"a  b"` and `"a b"` are different
    strings, and collapsing both to the second let a writeup quote a
    proposition about one and match a theorem about the other -- the same
    mistake as blanking the literal, one layer further in.

    Raw strings are copied whole for the same reason, and because a backslash
    in one is an ordinary character. So are guillemet-quoted identifiers:
    `«a  b»` and `«a b»` are two different names, and Lean is as literal inside
    those as it is inside a string.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        raw = _raw_string_opener(text, index)
        if raw is not None:
            closer = '"' + "#" * raw
            found = text.find(closer, index + 1 + raw + 1)
            end = length if found == -1 else found + len(closer)
            out.append(text[index:end])
            index = end
            continue
        if character == "«":
            found = text.find("»", index + 1)
            end = length if found == -1 else found + 1
            out.append(text[index:end])
            index = end
            continue
        literal = _char_literal_end(text, index)
        if literal is not None:
            # `' '` is a character, not whitespace to collapse, and `'"'` is
            # not a string opener: read as one, the phantom string's end
            # would open another over real code, whose whitespace -- and
            # whose string literals' -- would then be treated backwards.
            out.append(text[index:literal])
            index = literal
            continue
        if character == '"':
            start = index
            index = _string_end(text, index)
            out.append(text[start:index])
            continue
        if character.isspace():
            if out and out[-1] != " ":
                out.append(" ")
            index += 1
            continue
        out.append(character)
        index += 1
    return "".join(out).strip()


# `opaque` belongs here beside `axiom` and `constant`: all three put a
# declaration in the environment with no proof to check, and an `opaque` is
# the *stronger* claim, since it asserts something of that type exists. It is
# also the keyword `assume.render_module` writes for an assumed definition, so
# a scanner blind to it let a quarantined name be declared under the one
# spelling this feature mints.
ASSUMPTION = re.compile(
    rf"^{WRAPPER}(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|scoped|local)\s+)*"
    rf"(?:axiom|constant|opaque)\s+({QUALIFIED_NAME})\s*:(.*)$"
)
# The keyword itself, for finding an axiom this pattern cannot read. The
# boundary is `IDENTIFIER`'s alphabet with `!` and `?`, so `axiom?` and `get!`
# are names rather than keywords, and the guillemets go with them because
# `def «axiom» : Nat` names a declaration rather than making one.
AXIOM_KEYWORD = re.compile(r"(?<![\w'!?.«])(?:axiom|constant|opaque)(?![\w'!?»])")
# Where a declaration stops, so the one before it is not read as running on.
# Approximate on purpose: over-reading appends text to a statement and the
# comparison refuses a save that should have passed, which is visible and
# recoverable, while under-reading truncates one and is not.
COMMAND = re.compile(
    r"^(?:@\[|#)|"
    r"^(?:axiom|constant|theorem|lemma|def|abbrev|instance|structure|class|inductive"
    r"|example|namespace|end|section|open|variable|variables|universe|import|attribute"
    r"|macro|macro_rules|notation|syntax|deriving|mutual|set_option|run_cmd"
    r"|private|protected|noncomputable|nonrec|unsafe|partial|scoped|local)\b"
)


def assumptions(source: str) -> tuple[tuple[str, str], ...]:
    """Axioms a source declares, under the names Lean will report them by.

    A flat scan reads `namespace Foo ... axiom bar` as `bar`, but Lean reports
    it as `Foo.bar`. With one gate checking the short name and the audit
    checking the qualified one, no single approval could satisfy both and the
    module could not be saved at all. The scope walk is shared with
    `declarations` so both gates agree on the name, and so the two cannot drift
    apart again -- which they did, twice, when this kept its own.

    A statement is gathered across lines, because `axiom trusted :` with its
    type on the next line is ordinary Lean and a line-anchored read of it
    returned *nothing* -- so the one place Hardy compares a declared statement
    against the one a human approved was skipped entirely, and the axiom passed
    on its name alone. A wrapped statement fared no better: it was truncated at
    the first newline and then failed a comparison it should have passed.
    """
    text = strip_comments(source)
    lines = text.splitlines()
    marks = _scopes(text, identifier_tokens(text))
    starts = _line_starts(lines)
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        declared = ASSUMPTION.match(lines[index].strip())
        if declared is None:
            index += 1
            continue
        line = lines[index]
        prefix = _prefix_at(marks, starts[index] + len(line) - len(line.lstrip()))
        name, parts = declared.group(1), [declared.group(2).strip()]
        index += 1
        while index < len(lines):
            following = lines[index].strip()
            if not following or COMMAND.match(following):
                break
            parts.append(following)
            index += 1
        statement = " ".join(part for part in parts if part)
        found.append((declared_name(name, prefix), statement))
    return tuple(found)


def unreadable_assumptions(source: str) -> tuple[str, ...]:
    """Lines declaring an axiom that `assumptions` cannot read, verbatim.

    `assumptions` skips a line it fails to match, which is the wrong direction
    for a gate: an axiom wearing binders (`axiom Sneaky (n : Nat) : False`) or
    universe parameters (`axiom Sneaky.{u} : Sort u`) simply passed, offered
    for approval by nobody and refused by nobody.

    Matching those shapes instead is worse, and was tried. The statement Lean
    gives `axiom Sneaky (P : Prop) : P` is `∀ P : Prop, P`, not the `P` after
    the colon, so comparing the tail accepts it against an approval of `P` --
    reading a declaration the gate cannot reconstruct is how a materially
    stronger axiom comes to look checked. Nothing here reconstructs a type.

    So they are reported, and the caller refuses the save. `request_assumption`
    never produces a binder or a universe parameter, so refusing one costs a
    shape the approval flow cannot reach anyway, and the model is told what to
    write instead. Any *other* unreadable spelling -- a binder type holding
    nested delimiters, whatever Lean grows next -- lands here too, because this
    asks whether the line parsed rather than whether it matched some list of
    known-bad forms.
    """
    stripped = strip_comments(source)
    lines = stripped.splitlines()
    # By token as well as by pattern: `def a := 1axiom cheat : False` declares
    # `cheat`, and `AXIOM_KEYWORD`'s lookbehind reads the `1` as the start of a
    # name. Either finding is enough to refuse.
    starts = _line_starts(lines)
    keyworded = {
        bisect_right(starts, start) - 1
        for start, end in identifier_tokens(stripped).items()
        if stripped[start:end] in {"axiom", "constant", "opaque"}
    }
    found: list[str] = []
    for number, line in enumerate(lines):
        text = line.strip()
        if (number in keyworded or AXIOM_KEYWORD.search(text)) and ASSUMPTION.match(text) is None:
            found.append(text)
    return tuple(found)


def parse_imports(source: str) -> tuple[str, ...]:
    """The modules a source file imports.

    Lean requires imports before any declaration, so the header is scanned and
    abandoned at the first line that is neither blank nor an import. Comments
    are already gone by then; a regex over the raw file would find the word in
    a string literal and invent a dependency that does not exist.
    """
    imports: list[str] = []
    for line in strip_comments(source).splitlines():
        text = line.strip()
        if not text:
            continue
        # `prelude` suppresses the implicit `import Init`; `module` opens a file
        # using Lean's module system. Both sit before the imports, and reading
        # either as the end of the header would drop every import that follows
        # -- and an import Hardy cannot see is a dependency it will not rebuild.
        if text in HEADER_KEYWORDS and not imports:
            continue
        # `public import X` and `meta import X` are ordinary imports under the
        # module system, and carry the dependency just as a bare one does.
        text = IMPORT_PREFIX.sub("", text, count=1)
        if not text.startswith("import "):
            break
        rest = text.removeprefix("import ").strip()
        # `import all X` re-exports; the dependency is the same either way.
        if rest.startswith("all "):
            rest = rest.removeprefix("all ").strip()
        match = MODULE.fullmatch(rest)
        if match is None:
            break
        imports.append(match.group())
    return tuple(imports)


def external_imports(source: str, known: Collection[str]) -> tuple[str, ...]:
    """The imports of `source` that are not workspace modules."""
    return tuple(name for name in parse_imports(source) if name not in known)


def declarations(source: str) -> dict[str, tuple[str, ...]]:
    """Top-level `theorem` and `lemma` names, one entry per declaration.

    A declaration inside a namespace is reported by its qualified name, which
    is the one Lean itself would print. Emitting the bare name as well would
    make one theorem look like two, and a caller counting what still owes a
    writeup would then demand two of them -- see `name_aliases` for the other
    half of this, which is that a *reader* of the registry must accept either.

    The `private` key repeats whichever of those names carried the `private`
    modifier. It is a subset of the other two rather than a fourth kind, so a
    caller that only wants "what is declared here" can keep ignoring it, and
    one that has to *name* a declaration from another module -- which Lean will
    not let it do for a private one -- can leave those out.
    """
    found: dict[str, list[str]] = {"theorem": [], "lemma": [], "private": []}
    # Comments first: Lean reads `/-- explanation -/ theorem result ...` as a
    # declaration, and a scanner that saw the leading slash would miss it --
    # so the theorem would never be recorded and never owe a writeup.
    for match, prefix in _scan(strip_comments(source)):
        modifiers, kind, name = match.group(1), match.group(2), match.group(3)
        qualified = declared_name(name, prefix)
        found[kind].append(qualified)
        if PRIVATE.search(modifiers):
            found["private"].append(qualified)
    return {kind: tuple(names) for kind, names in found.items()}


# Every kind of top-level declaration that has a name Lean will report, for a
# reader that needs to know what a source *declares* rather than what the audit
# must ask about: a ledger item may cite a definition or an axiom by name.
ANY_DECLARATION = re.compile(
    rf"(?m)^[ \t]*{WRAPPER}(?:@\[[^\]]*\]\s*)*"
    rf"((?:(?:private|protected|nonrec|noncomputable|partial|unsafe|local|scoped)\s+)*)"
    rf"(theorem|lemma|def|abbrev|axiom|structure|inductive|class|instance|opaque|constant)"
    rf"\s+({QUALIFIED_NAME})"
)


def named_declarations(source: str) -> tuple[str, ...]:
    """The qualified name of every top-level declaration of any kind, in order.

    Broader than `declarations`, which reports what the audit asks about; this
    answers whether a name a ledger item cites is declared at all. Comments are
    stripped first, and a name is qualified by the namespace open at its line,
    exactly as `declarations` qualifies a theorem.
    """
    return tuple(declared_name(match.group(3), prefix)
                 for match, prefix in _scan(strip_comments(source), ANY_DECLARATION))


def _scan(text: str, pattern: re.Pattern[str] = DECLARATION) -> list[tuple[re.Match[str], tuple[str, ...]]]:
    """Every declaration in an already-stripped source, with its namespace.

    Declarations are matched over the whole text so a name on the line after
    its keyword is still found, then attributed to the scope open where the
    keyword sits -- which may be partway along a line, after an `end`.

    One walk, shared by `declarations` and `statements`. They must agree about
    what a declaration is called: a theorem the first names `Hardy.one` and the
    second names `one` would be one theorem to the writeup gate and another to
    the ratchet, and the statement the document was checked against would not
    be the statement anyone had to write up.
    """
    tokens = identifier_tokens(text)
    marks = _scopes(text, tokens)
    return [(match, _prefix_at(marks, match.start(2))) for match in _keyword_matches(text, pattern, tokens)]


def _line_starts(lines: list[str]) -> list[int]:
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    return starts


# What a statement may nest, and what closes it. Depth is tracked so that the
# `:=` in a binder's default value (`(n : Nat := 3)`) is not read as the start
# of the proof, which would truncate the statement a human is asked to check.
CLOSERS = {"(": ")", "[": "]", "{": "}", "⟨": "⟩"}
OPENERS = {closer: opener for opener, closer in CLOSERS.items()}
PROOF = ":="
# The binders a proposition may carry that own a `:=` of their own. Their
# assignment is part of the statement, not the start of the proof.
BINDERS = frozenset({"let", "have", "suffices"})
# The other way a declaration opens its proof. `theorem p : A ∧ B where left :=
# ...` ends its statement at `where`, and reading on to that first field
# assignment recorded `theorem p : A ∧ B where left` -- not a statement at all,
# and one a writeup could quote followed by `:=` to satisfy the gate.
# `by` is deliberately not here: a truncated statement is the *easy* one to
# satisfy, so a boundary that fires wrongly weakens the gate. `where` is
# reachable in ordinary Lean and `by` at depth zero before any `:=` is not.
OPENS_PROOF = frozenset({"where"})


def statements(source: str) -> dict[str, str]:
    """Each theorem and lemma's statement, whitespace-normalised.

    The declaration head and nothing else: from the keyword through the `:=`
    that opens the proof, exclusive. This is what a reader of the writeup has
    to be able to compare against the document in front of them, so it is also
    what the writeup gate looks for -- a paper that quotes a *different*
    statement than the one Lean checked is worse than one that quotes none.

    Attributes and modifiers are dropped and whitespace is collapsed, because
    neither changes the proposition and both differ harmlessly between the
    Lean file and the listing in the paper. Nothing else is normalised: the
    proposition is compared character for character.
    """
    text = strip_comments(source)
    scanned = _scan(text)
    found: dict[str, str] = {}
    for position, (match, prefix) in enumerate(scanned):
        bound = scanned[position + 1][0].start() if position + 1 < len(scanned) else len(text)
        start, end = match.start(2), _statement_end(text, match.end(), bound)
        # Found on the blanked text, read off the original. `strip_comments`
        # preserves every position, so the extent a scan established over text
        # that cannot lie about declarations can be sliced out of the source
        # that still has its string literals -- and a proposition about `"a"`
        # stays a proposition about `"a"` rather than about two spaces. Its own
        # comments still go, with the strings kept this time.
        head = strip_comments(source[start:end], keep_strings=True)
        found[declared_name(match.group(3), prefix)] = normalise_lean(head)
    return found


def _statement_end(text: str, start: int, bound: int) -> int:
    """Where a declaration's statement stops: its own `:=`, or `bound`.

    A declaration whose proof is written some other way -- `where`, a `| pat`
    branch, or nothing at all because the file is mid-edit -- has no `:=` to
    find, and stopping at the next declaration keeps the statement bounded
    rather than swallowing the rest of the file.

    Not every top-level `:=` is the proof. `theorem t : let n := 1; n = 1 :=
    by rfl` is an ordinary Lean statement whose *proposition* contains one, and
    stopping there recorded the statement as `theorem t : let n` -- leaving the
    rest of the proposition outside what a writeup has to quote, which is the
    one thing this extent is for. So each binder that owns a `:=` consumes it,
    and the first one left over opens the proof.

    And not every proof opens with one: `where` begins a structure proof whose
    first field assignment is not the declaration's.
    """
    depth = 0
    binders = 0
    index = start
    while index < bound:
        character = text[index]
        if character in CLOSERS:
            depth += 1
        elif character in OPENERS:
            depth = max(depth - 1, 0)
        elif depth == 0 and text.startswith(PROOF, index):
            if not binders:
                return index
            binders -= 1
            index += len(PROOF)
            continue
        elif depth == 0 and _word_at(text, index, OPENS_PROOF):
            return index
        elif depth == 0 and _word_at(text, index, BINDERS):
            binders += 1
        index += 1
    return bound


def _word_at(text: str, index: int, words: Collection[str]) -> bool:
    """Whether one of `words` begins at `index` as a whole token.

    `let` in `letter` is not a binder, and neither is the one in `x.let`.
    """
    if index and (text[index - 1].isalnum() or text[index - 1] in "_'.«"):
        return False
    for word in words:
        if text.startswith(word, index):
            after = index + len(word)
            if after >= len(text) or not (text[after].isalnum() or text[after] in "_'!?"):
                return True
    return False


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


def internal_imports(source: str, known: Collection[str]) -> tuple[str, ...]:
    """The imports of `source` that name workspace modules.

    Anything else is Mathlib or a toolchain module, which the Lake environment
    already resolves and which this module must not try to build.
    """
    return tuple(name for name in parse_imports(source) if name in known)


def build_order(sources: Mapping[str, str], targets: Collection[str]) -> tuple[str, ...]:
    """`targets` and their transitive internal dependencies, dependencies first.

    Sorted at each step rather than left in dictionary order: a build that
    compiled the same tree in a different order on different runs could not be
    compared against its own cache.
    """
    order: list[str] = []
    placed: set[str] = set()
    active: list[str] = []

    def visit(module: str) -> None:
        if module in placed:
            return
        if module in active:
            cycle = " -> ".join([*active[active.index(module) :], module])
            raise ImportCycle(f"workspace modules import each other: {cycle}")
        active.append(module)
        for dependency in sorted(internal_imports(sources[module], sources)):
            visit(dependency)
        active.pop()
        placed.add(module)
        order.append(module)

    for target in sorted(targets):
        visit(target)
    return tuple(order)


def dependents(sources: Mapping[str, str], module: str) -> frozenset[str]:
    """Every module that reaches `module` through internal imports."""
    direct: dict[str, set[str]] = {name: set() for name in sources}
    for name, source in sources.items():
        for dependency in internal_imports(source, sources):
            direct[dependency].add(name)
    found: set[str] = set()
    frontier = list(direct.get(module, ()))
    while frontier:
        name = frontier.pop()
        if name in found or name == module:
            continue
        found.add(name)
        frontier.extend(direct.get(name, ()))
    return frozenset(found)


