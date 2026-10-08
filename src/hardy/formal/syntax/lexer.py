"""Lean's lexical grammar, read under every reading that could be Lean's.

Comments, string and char literals, raw strings, interpolation, `«...»`
names, numerals and syntax quotations, with the uncertainty between readings
kept rather than resolved; and the token boundaries the scans read. Every
other scan in this package reads Lean through what this module renders.
"""
from __future__ import annotations

import functools
import heapq
import re
from collections.abc import Callable
from typing import Any

# --- Lean's lexical grammar, read so that an ambiguity fails closed -------------
#
# Every scan in this package -- declarations, holes, assumptions, the proof-body
# command gate -- reads Lean after its comments and literals are blanked, so
# what the lexer calls a literal is what every gate cannot see. Where Lean's own
# reading is certain the lexer follows it exactly (Lean 4.35.0-rc3,
# `Lean/Parser/Basic.lean`); where it is not, the lexer keeps every reading
# that could be Lean's and a character is code when it is code in *any* of
# them. A gate then refuses what one reading shows even if another hides it.
# A false refusal is the price; a hidden `sorry`, axiom or command is not one
# this package may pay.
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
    "macro", "elab", "binder_predicate", "declare_simp_like_tactic",
})


def declares_tokens(lexed: Lexed) -> bool:
    """Whether the source may add tokens of its own to Lean's table."""
    text = lexed.text
    return any(
        text[start:end] in TOKEN_COMMANDS
        for start, end in identifier_tokens(text, lexed).items()
    )


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
