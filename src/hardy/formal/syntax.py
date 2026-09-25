"""Pure Lean source scanning and module dependency graphs.

These lexical checks preserve the source identity; they do not elaborate
or verify it. Workspace builds and document readers share this one grammar.
"""
from __future__ import annotations

import functools
import heapq
import re
from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

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
# A declaration is found at its keyword, wherever that stands. Lean commands
# are whitespace-insensitive, so `def a := 1 theorem sneaky : False := sorry`
# declares `sneaky` as surely as a line of its own does, and so do `end Foo
# theorem t`, `include h in theorem t`, and `def a := 1 open Nat theorem
# hidden ... in theorem shown` -- `c1 in c2` is a command for any `c1`, so a
# pattern that read a wrapper up to its `in` swallowed the first theorem whole.
# A scan anchored to line starts, or to anything but the keyword itself, never
# asked the audit about such a theorem, never reserved it to a registered
# result, and never counted it towards the writeup ratchet. Keywords are Lean's
# own tokens (`_code_tokens`): `«a theorem b»`, `mytheorem` and `(x).theorem`
# are names, while `1theorem` -- a numeral and then a keyword -- declares one.
# `theorem«name»` needs no space. The modifiers are the ones read backwards
# from the keyword; attributes before them change nothing here.
DECLARATION_KINDS = frozenset({"theorem", "lemma"})
_DECLARATION_NAME = re.compile(rf"(?:\s+|(?=«))({QUALIFIED_NAME})")
_HEAD_MODIFIERS = frozenset({"private", "protected", "nonrec", "noncomputable"})
_LINE_PREFIX = re.compile(rf"[ \t]*{WRAPPER}")


class _Head(NamedTuple):
    """One `theorem` or `lemma`.

    `keyword` is where the keyword starts and `end` where the name ends.
    `start` is where the whole head starts: its modifiers and attributes, and
    the start of its line when only indentation and `... in` wrappers precede
    it there -- what a reader slicing the declaration out of its file takes.
    """

    start: int
    keyword: int
    end: int
    modifiers: str
    kind: str
    name: str


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
    "declare_syntax_cat", "def", "deriving", "elab", "elab_rules", "end", "example",
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


# --- Lean's lexical grammar, read so that an ambiguity fails closed -------------
#
# Every scan in this module -- declarations, holes, assumptions, the proof-body
# command gate -- reads Lean after its comments and literals are blanked, so
# what the lexer calls a literal is what every gate cannot see. Where Lean's own
# reading is certain the lexer follows it exactly (Lean 4.35.0-rc3,
# `Lean/Parser/Basic.lean`); where it is not, the lexer keeps every reading
# that could be Lean's and a character is code when it is code in *any* of
# them. A gate then refuses what one reading shows even if another hides it.
# A false refusal is the price; a hidden `sorry`, axiom or command is not one
# this module may pay.
#
# What Lean cannot settle from characters alone is where a symbol token ends.
# The token table is extensible: core has `]'` (`xs[i]'h`) and `×'`, Mathlib
# `∑'`, `⁻¹'` and `//`, and a module may add its own with `notation`. So after a
# symbol character, a `'` may be a char literal or the end of that token, and
# `--` or `/-` may start a comment or continue one (`{x : Int //-1 = x}` is a
# subtype, not a comment -- checked against Lean). Both are read. A string may
# be interpolated (`s!"{'"'}"`, `throwError "..."`, any `interpolatedStr`
# syntax) or plain, and nothing lexical says which, so every string is read
# both ways too.

# Lean's identifier characters (`Init/Meta/Defs.lean`). Python's `isalpha` is
# both wider and narrower than these, and the difference is exactly where a
# quote after a character does or does not continue a name.
_LETTER_LIKE = (
    "α-κμ-ω"  # lower Greek, but lambda
    "Α-ΟΡ΢Τ-Ω"  # upper Greek, but Pi and Sigma
    "ϊ-ϻ"  # Coptic
    "ἀ-῾"  # polytonic Greek
    "℀-⅏"  # letter-like block
    "\U0001d49c-\U0001d59f"  # script, double-struck, Fraktur
    "À-ÖØ-öø-ÿ"  # Latin-1 letters, but × and ÷
    "Ā-ſ"  # Latin Extended-A
)
_SUBSCRIPTS = "₀-₉ₐ-ₜᵢ-ᵪⱼ"
_ID_PART = re.compile(f"[A-Za-z_{_LETTER_LIKE}][A-Za-z0-9_'!?{_LETTER_LIKE}{_SUBSCRIPTS}]*")
# `Char.isWhitespace`: nothing else separates tokens.
_LEAN_SPACE = frozenset(" \t\r\n")
# What may follow a backslash in a char literal besides `x` with two hex digits
# and `u` with four (`isQuotableCharDefault`). Lean refuses anything else.
_SIMPLE_ESCAPES = frozenset("\\\"'nrt")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_STRING_STOP = re.compile(r'[\\"{]')
_COMMENT_MARK = re.compile(r"/-|-/")
# More readings than this at once, or interpolation nested deeper, and the lexer
# stops distinguishing: everything from there on is code in some reading.
_MAX_READINGS = 32
_MAX_NESTING = 16


class _Overflow(Exception):
    """Too many simultaneous readings to keep apart."""


class Lexed:
    """One source, lexed under every reading that could be Lean's.

    Per character: whether any reading reads it as code, as code outside a
    `«...»` name (`plain`), as part of a literal, as a comment, as a literal's
    delimiter, or as part of a `«...»` name. `uncertain` is code in one reading
    and not in another.

    A span one reading opens is never trusted to hide what another reading
    calls code. The view scans read (`text`) shows every character any reading
    calls code; token boundaries come from `profiles`, so a boundary one reading
    has is kept even where another reading's code runs through it; and a
    `«...»` is one name only where every reading opens it (`certain_name`).
    """

    def __init__(self, source: str) -> None:
        self.source = source
        length = len(source)
        self.code = bytearray(length)
        self.literal = bytearray(length)
        self.comment = bytearray(length)
        self.delimiter = bytearray(length)
        self.name = bytearray(length)
        self.plain = bytearray(length)
        self.overflow: int | None = None
        _run(self)
        prefix = [0]
        total = 0
        for index in range(length):
            if self.code[index] and (self.literal[index] or self.comment[index]):
                total += 1
            prefix.append(total)
        self._uncertain = prefix
        # One byte per character saying which kinds of reading it has. Where
        # two neighbours differ, some reading may break a token there that
        # another reading runs through, so the tokenizer breaks there too.
        # Each flag byte is 0 or 1, so one shift of the whole array moves every
        # byte's bit without a carry into its neighbour.
        self.profiles = (
            int.from_bytes(self.code, "big")
            | int.from_bytes(self.literal, "big") << 1
            | int.from_bytes(self.comment, "big") << 2
            | int.from_bytes(self.delimiter, "big") << 3
        ).to_bytes(length, "big")

    def certain_name(self, index: int) -> bool:
        """Whether every reading reads `index` as part of a `«...»` name."""
        return bool(
            self.name[index]
            and not (self.plain[index] or self.literal[index] or self.comment[index])
        )

    def uncertain_names(self) -> tuple[int, ...]:
        """Each `«` that some reading opens a name at and another does not."""
        return tuple(
            index
            for index, character in enumerate(self.source)
            if character == "«" and self.name[index] and not self.certain_name(index)
        )
    def uncertain(self, start: int = 0, end: int | None = None) -> bool:
        """Whether any character in `[start, end)` is code in only some readings."""
        end = len(self.source) if end is None else end
        return self._uncertain[end] - self._uncertain[start] > 0

    def _render(self, keep: Callable[[int], bool]) -> str:
        return "".join(
            character if keep(index) else ("\n" if character == "\n" else " ")
            for index, character in enumerate(self.source)
        )

    @functools.cached_property
    def text(self) -> str:
        """Every character any reading calls code; the rest blanked.

        A delimiter one reading uses (a quote, a raw string's `r#`) stays
        visible where another reading calls it code: blanking it would hide
        code on a reading's word. The boundary it makes in the reading that
        uses it reaches the tokenizer through `profiles` instead.
        """
        code = self.code
        return self._render(lambda index: code[index])

    @functools.cached_property
    def kept(self) -> str:
        """Code and literals in any reading; only what every reading calls a comment blanked."""
        code, literal = self.code, self.literal
        return self._render(lambda index: code[index] or literal[index])

    @functools.cached_property
    def certain(self) -> str:
        """Code in every reading, and nothing else."""
        code, literal, comment, delimiter = self.code, self.literal, self.comment, self.delimiter
        return self._render(
            lambda index: code[index]
            and not (literal[index] or comment[index] or delimiter[index])
        )


@functools.lru_cache(maxsize=64)
def lex(source: str) -> Lexed:
    """`source` lexed under every reading that could be Lean's. Cached: one
    save runs half a dozen scans over the same text."""
    return Lexed(source)


def _ones(count: int) -> bytes:
    return b"\x01" * count


def _run(lexed: Lexed) -> None:
    source = lexed.source
    length = len(source)
    pending: dict[int, set[tuple[Any, ...]]] = {0: {("code", True, ())}}
    queue = [0]
    while queue:
        position = heapq.heappop(queue)
        states = pending.pop(position, None)
        if not states:
            continue
        try:
            if len(states) > _MAX_READINGS:
                raise _Overflow
            for state in states:
                for following, successor in _step(lexed, position, state):
                    if following >= length:
                        continue
                    bucket = pending.get(following)
                    if bucket is None:
                        pending[following] = {successor}
                        heapq.heappush(queue, following)
                    else:
                        bucket.add(successor)
        except _Overflow:
            # Fail closed: from here on every character is code in some
            # reading and not code in another, so every scan sees all of it
            # and every caller that asks is told the reading is uncertain.
            lexed.overflow = position
            lexed.code[position:] = _ones(length - position)
            lexed.plain[position:] = _ones(length - position)
            lexed.literal[position:] = _ones(length - position)
            return


def _step(lexed: Lexed, index: int, state: tuple[Any, ...]) -> list[tuple[int, tuple[Any, ...]]]:
    kind = state[0]
    source = lexed.source
    if kind == "code":
        return _code(lexed, index, state[1], state[2])
    if kind == "string":
        return _string(lexed, index, state[1], state[2])
    if kind == "raw":
        return _raw(lexed, index, state[1], state[2])
    if kind == "block":
        return _block(lexed, index, state[1], state[2])
    # The forced readings of one ambiguous position.
    if kind == "symbol":
        lexed.code[index] = 1
        lexed.plain[index] = 1
        return [(index + 1, ("code", False, state[1]))]
    if kind == "char":
        _mark_char(lexed, index, state[1])
        return [(state[1], ("code", True, state[2]))]
    if kind == "line":
        end = source.find("\n", index)
        end = len(source) if end == -1 else end
        lexed.comment[index:end] = _ones(end - index)
        return [(end, ("code", True, state[1]))]
    if kind == "open-block":
        lexed.comment[index : index + 2] = _ones(2)
        return [(index + 2, ("block", 1, state[1]))]
    if kind == "open-raw":
        width = 2 + state[1]
        lexed.literal[index : index + width] = _ones(width)
        lexed.delimiter[index : index + width] = _ones(width)
        return [(index + width, ("raw", state[1], state[2]))]
    raise AssertionError(kind)


def _code(lexed: Lexed, index: int, boundary: bool, context: tuple[int, ...]) -> list[tuple[int, tuple[Any, ...]]]:
    """Code until the next literal, comment or ambiguity.

    `boundary` says a token certainly starts here: at the start, after
    whitespace, a literal, a comment or an identifier. After a symbol
    character it may not, because the symbol's token may run on.
    `context` holds the brace depth of each interpolation this code sits in.
    """
    source, code = lexed.source, lexed.code
    plain = lexed.plain
    length = len(source)
    while index < length:
        character = source[index]
        if character in _LEAN_SPACE:
            end = index + 1
            while end < length and source[end] in _LEAN_SPACE:
                end += 1
            code[index:end] = _ones(end - index)
            plain[index:end] = _ones(end - index)
            index, boundary = end, True
            continue
        if context and character in "{}":
            depth = context[-1]
            if character == "}" and depth == 0:
                lexed.literal[index] = lexed.delimiter[index] = 1
                return [(index + 1, ("string", True, context[:-1]))]
            context = context[:-1] + (depth + (1 if character == "{" else -1),)
            code[index] = 1
            plain[index] = 1
            index, boundary = index + 1, False
            continue
        if character == '"':
            lexed.literal[index] = lexed.delimiter[index] = 1
            if len(context) >= _MAX_NESTING:
                raise _Overflow
            return [(index + 1, ("string", False, context)), (index + 1, ("string", True, context))]
        if character == "'":
            end = _char_end(source, index)
            if boundary and source.startswith("''", index):
                # Lean never starts a char literal at `''` (Mathlib's `f '' s`).
                code[index : index + 2] = _ones(2)
                plain[index : index + 2] = _ones(2)
                index, boundary = index + 2, False
                continue
            if end is not None and boundary:
                _mark_char(lexed, index, end)
                index, boundary = end, True
                continue
            if end is not None:
                return [(index, ("char", end, context)), (index, ("symbol", context))]
            code[index] = 1
            plain[index] = 1
            index, boundary = index + 1, False
            continue
        if source.startswith("--", index):
            if not boundary:
                return [(index, ("line", context)), (index, ("symbol", context))]
            end = source.find("\n", index)
            end = length if end == -1 else end
            lexed.comment[index:end] = _ones(end - index)
            index, boundary = end, True
            continue
        if source.startswith("/-", index):
            if not boundary:
                return [(index, ("open-block", context)), (index, ("symbol", context))]
            lexed.comment[index : index + 2] = _ones(2)
            return [(index + 2, ("block", 1, context))]
        if character == "r":
            hashes = _raw_hashes(source, index)
            if hashes is not None:
                opened = (index, ("open-raw", hashes, context))
                return [opened] if boundary else [opened, (index, ("symbol", context))]
        if character == "«":
            # An escaped name runs to the next `»`, newlines and all
            # (`identFnAux` takes until the closer).
            close = source.find("»", index + 1)
            if close != -1:
                code[index : close + 1] = _ones(close + 1 - index)
                lexed.name[index : close + 1] = _ones(close + 1 - index)
                index, boundary = close + 1, True
                continue
        identifier = _ID_PART.match(source, index)
        if identifier is not None:
            end = identifier.end()
            code[index:end] = _ones(end - index)
            plain[index:end] = _ones(end - index)
            index, boundary = end, True
            continue
        if "0" <= character <= "9":
            # A numeral leaves `boundary` as it found it: `ℝ≥0` is one Mathlib
            # token, so a digit after a symbol may still be inside it.
            end = _number_end(source, index)
            code[index:end] = _ones(end - index)
            plain[index:end] = _ones(end - index)
            index = end
            continue
        code[index] = 1
        plain[index] = 1
        index, boundary = index + 1, False
    return []


def _string(lexed: Lexed, index: int, interpolated: bool, context: tuple[int, ...]) -> list[tuple[int, tuple[Any, ...]]]:
    """The rest of a string; with `interpolated`, `{` opens code (`interpolatedStrFn`)."""
    source = lexed.source
    length = len(source)
    start = index
    while True:
        found = _STRING_STOP.search(source, index)
        if found is None:
            lexed.literal[start:length] = _ones(length - start)
            return []
        index = found.start()
        character = source[index]
        if character == "\\":
            index += 2
            continue
        if character == "{" and not interpolated:
            index += 1
            continue
        end = index + 1
        lexed.literal[start:end] = _ones(end - start)
        lexed.delimiter[index] = 1
        if character == '"':
            return [(end, ("code", True, context))]
        if len(context) >= _MAX_NESTING:
            raise _Overflow
        return [(end, ("code", True, context + (0,)))]


def _raw(lexed: Lexed, index: int, hashes: int, context: tuple[int, ...]) -> list[tuple[int, tuple[Any, ...]]]:
    """The rest of a raw string: a backslash is ordinary, and only `"` and the same hashes close it."""
    source = lexed.source
    closer = '"' + "#" * hashes
    found = source.find(closer, index)
    end = len(source) if found == -1 else found + len(closer)
    lexed.literal[index:end] = _ones(end - index)
    if found != -1:
        lexed.delimiter[found:end] = _ones(end - found)
        return [(end, ("code", True, context))]
    return []


def _block(lexed: Lexed, index: int, depth: int, context: tuple[int, ...]) -> list[tuple[int, tuple[Any, ...]]]:
    """The rest of a block comment, which nests (`finishCommentBlock`)."""
    source = lexed.source
    start = index
    while depth:
        found = _COMMENT_MARK.search(source, index)
        if found is None:
            lexed.comment[start:] = _ones(len(source) - start)
            return []
        depth += 1 if found.group() == "/-" else -1
        index = found.end()
    lexed.comment[start:index] = _ones(index - start)
    return [(index, ("code", True, context))]


def _mark_char(lexed: Lexed, start: int, end: int) -> None:
    lexed.literal[start:end] = _ones(end - start)
    lexed.delimiter[start] = lexed.delimiter[end - 1] = 1


def _char_end(source: str, index: int) -> int | None:
    """Where a char literal opening at `index` would end, or None if none can.

    `charLitFnAux`: one code point, or a backslash escape Lean accepts (`\\\\`
    `\\"` `\\'` `\\n` `\\r` `\\t`, `\\x` with two hex digits, `\\u` with four),
    then the closing quote. `''` never opens one.
    """
    length = len(source)
    body = index + 1
    if body >= length or source[body] == "'":
        return None
    if source[body] != "\\":
        close = body + 1
    else:
        escape = source[body + 1 : body + 2]
        digits = {"x": 2, "u": 4}.get(escape)
        if digits is not None:
            hexadecimal = source[body + 2 : body + 2 + digits]
            if len(hexadecimal) != digits or not set(hexadecimal) <= _HEX_DIGITS:
                return None
            close = body + 2 + digits
        elif escape and escape in _SIMPLE_ESCAPES:
            close = body + 2
        else:
            return None
    if close < length and source[close] == "'":
        return close + 1
    return None


def _raw_hashes(source: str, index: int) -> int | None:
    """The hash count of a raw-string opener `r#*"` at `index`, or None."""
    if source[index] != "r":
        return None
    hashes = 0
    while index + 1 + hashes < len(source) and source[index + 1 + hashes] == "#":
        hashes += 1
    if index + 1 + hashes >= len(source) or source[index + 1 + hashes] != '"':
        return None
    return hashes


def strip_comments(source: str, *, keep_strings: bool = False) -> str:
    """`source` with its comments blanked out, line structure preserved.

    One pass serves every scan, because each was getting comments wrong in its
    own way: a trailing `--` hid an import, a nested `/- /- -/ -/` closed
    early, and `/-- doc -/ theorem foo` hid a declaration behind a leading
    comment. Lean treats all of that as whitespace, so the honest fix is to do
    the same once, rather than teach every regex about comments separately.

    Comments are replaced by spaces rather than removed so that line and
    column positions still line up with the source a reader has open.

    String and char literals are blanked too, for the same reason and in the
    other direction: a `--` inside one must not start a comment, and a line
    reading `theorem fake : True` inside a multiline string must not be
    reported as a declaration.

    Where Lean's reading is ambiguous (see `lex`), a character is kept when
    any reading calls it code, so a scan of this text finds what any reading
    would. That can make it report something Lean does not -- a refusal the
    model can fix by adding a space -- and never the other way round.

    `keep_strings` copies literals through instead, for the caller that has to
    *compare* two pieces of Lean rather than scan one: with strings blanked,
    `"a" = "a"` and `"b" = "b"` are the same run of spaces, so a writeup could
    quote a proposition about different values and pass for quoting this one.
    That caller (`statements`) never scans with it -- it finds declarations on
    the blanked text and only reads their extent with strings intact, so a
    `theorem` inside a string still cannot invent a declaration.
    """
    lexed = lex(source)
    return lexed.kept if keep_strings else lexed.text


def normalise_lean(text: str) -> str:
    """Lean with its whitespace collapsed -- outside literals.

    Two pieces of Lean that differ only in how they were wrapped are the same
    Lean, which is what lets a paper break a long statement across lines. Two
    that differ *inside* a literal are not: `"a  b"` and `"a b"` are different
    strings, and collapsing both to the second let a writeup quote a
    proposition about one and match a theorem about the other -- the same
    mistake as blanking the literal, one layer further in.

    So whitespace is collapsed only where every reading of `lex` calls it
    code; anything a reading calls a literal, a comment, or part of a `«...»`
    name (`«a  b»` and `«a b»` are two names) is copied verbatim. Where the
    readings disagree that compares more strictly, never less.
    """
    lexed = lex(text)
    code, literal, comment, name = lexed.code, lexed.literal, lexed.comment, lexed.name
    out: list[str] = []
    gap = False
    for index, character in enumerate(text):
        if character.isspace() and code[index] and not (literal[index] or comment[index] or name[index]):
            gap = bool(out)
            continue
        if gap:
            out.append(" ")
            gap = False
        out.append(character)
    return "".join(out).strip()


def blank_bounded_quotations(lexed: Lexed, refuse: str = "") -> tuple[str, tuple[tuple[int, int], ...]]:
    """`lexed.text` with each syntax quotation whose extent Hardy can count blanked.

    A quotation is data: `` `(tactic| sorry) `` builds syntax a proof never
    runs, so it is not a hole. Its end is found by counting parentheses, and
    that count is exact only where every reading agrees what is code:
    parentheses inside literals and comments are already blank, and those
    inside a `«...»` name every reading opens are skipped. One only some
    reading opens is counted through, since another reading's parentheses may
    be inside it. A quotation holding an uncertain character, or any of
    `refuse`, is left visible and returned in the second element with every
    unbalanced one, so a caller can say it could not read it rather than guess.

    Even a certain count is Lean's only for Lean's tokens: a source that
    declares its own (`notation "⟪(" x => x`) can end a quotation somewhere
    else, and the blanked stretch then holds real code. Callers whose gate
    turns on what is hidden check `declares_tokens` first, and the declaration
    scans never let this remove a declaration or scope keyword.
    """
    blanked, unbounded = _quotation_spans(lexed, refuse)
    out = list(lexed.text)
    for low, high in blanked:
        for position in range(low, high):
            if out[position] != "\n":
                out[position] = " "
    return "".join(out), unbounded


def _quotation_spans(
    lexed: Lexed, refuse: str = ""
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """The quotations `blank_bounded_quotations` blanks, and the ones it cannot bound."""
    text = lexed.text
    blanked: list[tuple[int, int]] = []
    unbounded: list[tuple[int, int]] = []
    index = 0
    while (index := text.find("`(", index)) != -1:
        depth = 0
        end = None
        for position in range(index + 1, len(text)):
            if lexed.certain_name(position):
                continue
            if text[position] == "(":
                depth += 1
            elif text[position] == ")":
                depth -= 1
                if depth == 0:
                    end = position + 1
                    break
        if end is None:
            # Unbalanced: nothing bounds it, so nothing after it is hidden.
            unbounded.append((index, len(text)))
            break
        if lexed.uncertain(index, end) or any(mark in text[index:end] for mark in refuse):
            unbounded.append((index, end))
            index += 2
            continue
        blanked.append((index, end))
        index = end
    return tuple(blanked), tuple(unbounded)


# The commands that add tokens to Lean's table. A module declaring one can
# move where a quotation ends (`notation "⟪(" x => x` swallows a `(`), so a
# parenthesis count over its quotations is no longer Lean's.
TOKEN_COMMANDS = frozenset({
    "notation", "notation3", "syntax", "infix", "infixl", "infixr", "prefix", "postfix",
    "macro", "elab", "binder_predicate",
})


def declares_tokens(lexed: Lexed) -> bool:
    """Whether the source may add tokens of its own to Lean's table."""
    text = lexed.text
    return any(
        text[start:end] in TOKEN_COMMANDS
        for start, end in identifier_tokens(text, lexed).items()
    )


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
    and an exponent (`numberFnAux`). The end matters because a keyword may
    follow a numeral directly -- `1theorem x` declares `x` -- while `0xdef` is
    one hex numeral and declares nothing.
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
    if end < length and text[end] == "." and not text.startswith("..", end):
        end = _digits_end(text, end + 1)
        exponent = _exponent_end(text, end)
        return end if exponent is None else exponent
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


def _component_end(text: str, index: int, lexed: Lexed | None = None) -> int | None:
    """Where the name component starting at `index` ends, or None if none does.

    With `lexed`, a `«...»` is a component only where every reading opens a
    name at its `«`: a `«` that only some reading opens -- `('«')` read as a
    symbol, `"{«"` read as interpolated -- would otherwise swallow everything
    up to the next `»`, code another reading shows included. And an
    identifier stops where the readings' profile changes, so one reading's
    `a'theorem` cannot hide the `theorem` another reading's `'a'` exposes.
    """
    if text[index] == "«":
        if lexed is not None and not lexed.certain_name(index):
            return None
        closing = text.find("»", index + 1)
        return closing + 1 if closing > index + 1 else None
    found = _ID_PART.match(text, index)
    if found is None:
        return None
    return _clip(lexed, index, found.end())


def _clip(lexed: Lexed | None, start: int, end: int) -> int:
    """`end`, or the first position before it where the profile of `start` changes."""
    if lexed is None:
        return end
    profiles = lexed.profiles
    first = profiles[start]
    for position in range(start + 1, end):
        if profiles[position] != first:
            return position
    return end


def _code_tokens(text: str, lexed: Lexed | None = None) -> tuple[dict[int, int], frozenset[int]]:
    """Identifier tokens (start -> end) and numeral ends in already-stripped text.

    A small forward tokenizer rather than a lookbehind, because whether a
    keyword starts a token depends on what came before it in a way no
    fixed-width assertion sees: `x1theorem` is one identifier, `1theorem` is a
    numeral and then `theorem`, `0xdef` is a numeral, and `Foo.theorem` is a
    dotted name. A name straight after a `.` that no identifier precedes --
    `(i).end`, `xs[0].def`, `.theorem` -- is a field or a dot-identifier, which
    Lean reads with `rawIdent`, so it is never a keyword and is left out.

    With the `lexed` source this text came from, a token never runs across a
    change in the readings' profile and a `«` only some reading opens is a
    plain symbol (`_component_end`), so a keyword any reading shows is a token.
    """
    profiles = lexed.profiles if lexed is not None else None

    def same(first: int, second: int) -> bool:
        return profiles is None or profiles[first] == profiles[second]

    tokens: dict[int, int] = {}
    numerals: set[int] = set()
    index = 0
    length = len(text)
    while index < length:
        if _is_digit(text, index):
            index = _clip(lexed, index, _number_end(text, index))
            numerals.add(index)
            continue
        end = _component_end(text, index, lexed)
        if end is None:
            index += 1
            continue
        start = index
        while end + 1 < length and text[end] == "." and same(end, start) and same(end + 1, start):
            following = _component_end(text, end + 1, lexed)
            if following is None:
                break
            end = following
        if not (start and text[start - 1] == "." and same(start - 1, start)):
            tokens[start] = end
        index = end
    return tokens, frozenset(numerals)


def identifier_tokens(text: str, lexed: Lexed | None = None) -> dict[int, int]:
    """Start -> end of every identifier or keyword token in already-stripped text."""
    return _code_tokens(text, lexed)[0]


def numeral_ends(text: str, lexed: Lexed | None = None) -> frozenset[int]:
    """Where each numeral in already-stripped text ends."""
    return _code_tokens(text, lexed)[1]


def _keyword_matches(
    text: str, pattern: re.Pattern[str], tokens: Mapping[int, int], lexed: Lexed | None = None
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
            start in tokens or _component_end(text, start, lexed) is None
        ):
            found.append(match)
            position = max(match.end(), start + 1)
        else:
            position = start + 1
    return found


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

    The declaration is found on the blanked text, so a string cannot declare
    one, and its statement is read off the same positions with the literals
    left in, the way `statements` reads a theorem's. Read off the blanked text,
    `c = 'a'` came back as `c =` followed by spaces, and an approved statement
    about a character or a string could never be declared as approved. It is
    returned `normalise_lean`-ed, which collapses whitespace only where every
    reading calls it code: a literal is compared character for character.
    """
    text = strip_comments(source)
    # The same positions with the literals left in: what the statement says.
    kept = strip_comments(source, keep_strings=True)
    lines = text.splitlines()
    marks = _structure(source).marks
    starts = _line_starts(text)
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip())
        declared = ASSUMPTION.match(line[indent:])
        if declared is None:
            index += 1
            continue
        prefix = _prefix_at(marks, starts[index] + indent)
        name, begin = declared.group(1), starts[index] + indent + declared.start(2)
        end = starts[index] + len(line)
        index += 1
        while index < len(lines):
            following = lines[index]
            # Blank only if blank with its literals in: a line holding nothing
            # but a string is still part of the statement, and reading it in
            # can only make the comparison stricter.
            if not kept[starts[index] : starts[index] + len(following)].strip():
                break
            if COMMAND.match(following.strip()):
                break
            end = starts[index] + len(following)
            index += 1
        found.append((declared_name(name, prefix), normalise_lean(kept[begin:end])))
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
    starts = _line_starts(stripped)
    keyworded = {
        bisect_right(starts, start) - 1
        for start, end in identifier_tokens(stripped, lex(source)).items()
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
    for head, prefix in _scan(source):
        qualified = declared_name(head.name, prefix)
        found[head.kind].append(qualified)
        if PRIVATE.search(head.modifiers):
            found["private"].append(qualified)
    return {kind: tuple(names) for kind, names in found.items()}


# Every kind of top-level declaration that has a name Lean will report, for a
# reader that needs to know what a source *declares* rather than what the audit
# must ask about: a ledger item may cite a definition or an axiom by name.
# Still anchored to a line start, unlike `declarations`: this answers whether a
# cited name exists, and matching mid-line would add names (`deriving instance
# Repr for X` would declare `Repr`), which is the loose direction for it.
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
    stripped first, and a name is qualified by the namespace open where it is
    declared, exactly as `declarations` qualifies a theorem.
    """
    structure = _structure(source)
    return tuple(
        declared_name(match.group(3), _prefix_at(structure.marks, match.start(2)))
        for match in _keyword_matches(structure.text, ANY_DECLARATION, structure.tokens, lex(source))
    )


class _Structure(NamedTuple):
    """What the declaration and scope scans read, computed once per source."""

    text: str
    tokens: dict[int, int]
    marks: list[tuple[int, tuple[str, ...]]]
    heads: tuple[_Head, ...]
    problems: tuple[str, ...]


@functools.lru_cache(maxsize=64)
def _structure(source: str) -> _Structure:
    """Declarations, scopes, and what about them could not be read.

    Every `theorem` and `lemma` token in `strip_comments`' text is a
    declaration, syntax quotations included: where a quotation ends depends on
    Lean's token table, which a module extends with `notation`, so blanking
    one by counting parentheses could hide a real declaration after it (a
    `theorem` a macro quotes is reported instead, and the audit, asking Lean
    about a name nobody declared, refuses the save). Scopes are walked with
    bounded quotations blanked, so `` `(command| namespace Bar) `` moves no
    scope -- but a scope command inside any quotation, one whose extent is
    uncertain, or one at a character only some readings call code is a
    problem, and `unreadable_structure` reports it, because the names after
    it depend on whether it is code.
    """
    lexed = lex(source)
    text, unbounded = blank_bounded_quotations(lexed)
    blanked, _ = _quotation_spans(lexed)
    tokens = identifier_tokens(text, lexed)
    marks = _scopes(text, tokens)
    full = lexed.text
    every = identifier_tokens(full, lexed)
    heads: list[_Head] = []
    ends = {end: start for start, end in every.items()}
    problems: list[str] = []
    if lexed.overflow is not None:
        problems.append(
            f"line {source.count(chr(10), 0, lexed.overflow) + 1}: Hardy cannot tell where "
            "the strings and comments from here on end"
        )
    for index in lexed.uncertain_names():
        problems.append(
            f"line {source.count(chr(10), 0, index) + 1}: a `«` opens a name in one reading "
            "of this file and not in another, so Hardy cannot tell where the name ends"
        )
    for start in sorted(every):
        end = every[start]
        word = full[start:end]
        if word in SCOPE_KEYWORDS and (
            lexed.uncertain(start, end)
            or any(low <= start < high for low, high in (*unbounded, *blanked))
        ):
            problems.append(
                f"line {source.count(chr(10), 0, start) + 1}: `{word}` sits where Hardy cannot "
                "tell code from a literal or a syntax quotation, so the names declared after "
                "it cannot be qualified"
            )
        if word not in DECLARATION_KINDS:
            continue
        named = _DECLARATION_NAME.match(full, end)
        if named is None:
            continue
        modifiers: list[str] = []
        cursor = start
        while True:
            before = cursor
            while before and full[before - 1] in _LEAN_SPACE:
                before -= 1
            previous = ends.get(before)
            if previous is None or full[previous:before] not in _HEAD_MODIFIERS:
                break
            modifiers.append(full[previous:before])
            cursor = previous
        heads.append(
            _Head(_head_start(full, cursor), start, named.end(), " ".join(reversed(modifiers)), word, named.group(1))
        )
    return _Structure(text, tokens, marks, tuple(heads), tuple(problems))


def _head_start(text: str, cursor: int) -> int:
    """Back from a head's first modifier over its `@[...]` attributes, then to
    its line's start if only indentation and wrappers stand before it there."""
    while True:
        before = cursor
        while before and text[before - 1] in _LEAN_SPACE:
            before -= 1
        if not before or text[before - 1] != "]":
            break
        opened = text.rfind("@[", 0, before)
        if opened == -1 or "]" in text[opened + 2 : before - 1]:
            break
        cursor = opened
    line = text.rfind("\n", 0, cursor) + 1
    return line if _LINE_PREFIX.fullmatch(text, line, cursor) else cursor


def unreadable_structure(source: str) -> tuple[str, ...]:
    """Why the declarations in `source` cannot be named with confidence, if they cannot.

    Where Lean's reading is ambiguous the scans take every reading, which is
    enough for what a source declares or leaves open -- a `theorem` any
    reading shows is reported. It is not enough for *what a declaration is
    called*: a `namespace` only one reading shows qualifies every name after
    it one way or the other, and picking either could hand the audit a clean
    twin to ask about. So a caller that names declarations for a gate refuses
    these instead of guessing.
    """
    return _structure(source).problems


def _scan(source: str) -> list[tuple[_Head, tuple[str, ...]]]:
    """Every `theorem` and `lemma` in a source, with its namespace.

    Each is attributed to the scope open where its keyword sits -- which may be
    partway along a line, after an `end`.

    One walk, shared by `declarations` and `statements`. They must agree about
    what a declaration is called: a theorem the first names `Hardy.one` and the
    second names `one` would be one theorem to the writeup gate and another to
    the ratchet, and the statement the document was checked against would not
    be the statement anyone had to write up.
    """
    structure = _structure(source)
    return [(head, _prefix_at(structure.marks, head.keyword)) for head in structure.heads]


def _line_starts(text: str) -> list[int]:
    """Where each of `text.splitlines()` starts, whatever ends the line."""
    starts = []
    offset = 0
    for line in text.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
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
    scanned = _scan(source)
    # The extent is read over what every reading calls code, so a `:=` only
    # some reading shows cannot end a statement early: over-reading refuses a
    # save that should have passed, under-reading lets a truncated one pass.
    certain = lex(source).certain
    found: dict[str, str] = {}
    for position, (head, prefix) in enumerate(scanned):
        bound = scanned[position + 1][0].start if position + 1 < len(scanned) else len(text)
        start, end = head.keyword, _statement_end(certain, head.end, bound)
        # Found on the blanked text, read off the original. `strip_comments`
        # preserves every position, so the extent a scan established over text
        # that cannot lie about declarations can be sliced out of the source
        # that still has its string literals -- and a proposition about `"a"`
        # stays a proposition about `"a"` rather than about two spaces. Its own
        # comments still go, with the strings kept this time.
        stated = strip_comments(source[start:end], keep_strings=True)
        found[declared_name(head.name, prefix)] = normalise_lean(stated)
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


