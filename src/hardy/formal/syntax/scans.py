"""Theorems, lemmas, axioms and their statements, and what about them cannot be read.

The scans that name a declaration for a gate -- the axiom audit, the writeup
gate, the ledger -- and the refusals they give instead of guessing when the
lexer reports more than one reading.
"""
from __future__ import annotations

import functools
import re
from bisect import bisect_right
from collections.abc import Collection, Mapping
from typing import NamedTuple

from hardy.formal.syntax.lexer import (
    _LEAN_SPACE,
    Lexed,
    _component_end,
    _quotation_spans,
    blank_bounded_quotations,
    identifier_tokens,
    lex,
    normalise_lean,
    strip_comments,
)
from hardy.formal.syntax.names import QUALIFIED_NAME, _components, declared_name
from hardy.formal.syntax.scopes import SCOPE_KEYWORDS, _prefix_at, _scopes

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


class DeclarationRefused(ValueError):
    """A source whose theorems and lemmas cannot be named or bounded with confidence.

    Raised by the scans that key a declaration by name or cut its statement
    out (`statements`, `named_declarations`) rather than answer from a reading
    that may be wrong: the writeup gate would compare the document with a
    statement nobody checked, or a ledger item would cite a name nothing
    declares. `unreadable_structure` reports the same sources, so a save
    refuses them first.
    """


class DuplicateDeclaration(DeclarationRefused):
    """A source declares one theorem or lemma name twice.

    Lean refuses a real repeat (`` `t` has already been declared ``), so one
    that reaches Hardy's scans has a copy inside a syntax quotation, which the
    scans read on purpose (see `_structure`). Which copy is real cannot be told
    from text, and every consumer addresses a declaration by name: the writeup
    gate compared the document with whichever statement was read last. So a
    repeat is refused, never resolved.
    """


class QuotedDeclaration(DeclarationRefused):
    """A `theorem` or `lemma` head sits inside a syntax quotation.

    Scanned on purpose, since a module's notation can move where a quotation
    ends (see `_structure`), so the head may be real -- or syntax a macro
    builds. Read as real, it ended the statement before it at the quotation's
    opening, and the writeup gate accepted that truncated prefix; its name
    became a declaration the audit could resolve to anything that name means.
    Neither reading is taken.
    """


class UncertainDeclaration(DeclarationRefused):
    """A `theorem` or `lemma` head only some reading of the file calls code.

    After a symbol, `--` and `/-` may open a comment or continue a token a
    notation declares, and a quote may open a literal or end one; the union
    view keeps every character any reading calls code, so a head written in
    what Lean reads as a comment is still seen. Read as real, it ended the
    statement before it inside the comment (`theorem T : 1 +--`) and named a
    declaration the audit could resolve to anything. Neither reading is taken.
    """


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

    Refused with `DuplicateDeclaration` when a theorem or lemma name is
    declared twice: whether a cited name is declared is answered by the real
    copy, and a scan that cannot tell which copy is real has no answer to give.
    """
    structure = _structure(source)
    _refuse_unreadable(structure)
    lexed = lex(source)
    # A declaration only some reading calls code -- a `def` in what Lean may
    # read as a comment -- is not credited: this answers whether a cited name
    # exists, and a name nothing may have declared must not answer yes.
    return tuple(
        declared_name(match.group(3), _prefix_at(structure.marks, match.start(2)))
        for match in _keyword_matches(structure.text, ANY_DECLARATION, structure.tokens, lexed)
        if not lexed.uncertain(match.start(2), match.end(3))
    )


class _Structure(NamedTuple):
    """What the declaration and scope scans read, computed once per source."""

    text: str
    tokens: dict[int, int]
    marks: list[tuple[int, tuple[str, ...]]]
    heads: tuple[_Head, ...]
    problems: tuple[str, ...]
    duplicates: tuple[str, ...]
    quoted: tuple[str, ...]
    uncertain: tuple[str, ...] = ()


def _refuse_unreadable(structure: _Structure) -> None:
    if structure.duplicates:
        raise DuplicateDeclaration(
            f"`{structure.duplicates[0]}` is declared twice in this file, so which "
            "statement is the real one cannot be told"
        )
    if structure.uncertain:
        raise UncertainDeclaration(
            f"`{structure.uncertain[0]}` is declared where only some reading of the file sees "
            "code, so whether it is a declaration, and where the statement before it ends, "
            "cannot be told"
        )
    if structure.quoted:
        raise QuotedDeclaration(
            f"`{structure.quoted[0]}` is declared inside a syntax quotation, so whether it "
            "is a declaration, and where the statement before it ends, cannot be told"
        )


def _name_key(name: str) -> tuple[str, ...]:
    """What Lean compares: `«t»` and `t` are one name, a guillemet only spells it."""
    return tuple(part.removeprefix("«").removesuffix("»") for part in _components(name))


@functools.lru_cache(maxsize=64)
def _structure(source: str) -> _Structure:
    """Declarations, scopes, and what about them could not be read.

    Every `theorem` and `lemma` token in `strip_comments`' text is a
    declaration, syntax quotations included: where a quotation ends depends on
    Lean's token table, which a module extends with `notation`, so blanking
    one by counting parentheses could hide a real declaration after it (a
    `theorem` a macro quotes is reported instead, and the audit, asking Lean
    about a name nobody declared, refuses the save; one that repeats a real
    declaration's name is a problem, since which copy is real cannot be told
    and every consumer addresses a declaration by name). Scopes are walked with
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
    # A name two heads resolve to. Lean refuses a real repeat, so one of them is
    # quoted -- and since quotations are scanned on purpose, nothing here can
    # say which. Every consumer addresses a declaration by name (the audit
    # dedupes, the registration gate passes both, `statements` kept the last),
    # so a repeat is refused rather than resolved either way.
    first: dict[tuple[str, ...], int] = {}
    duplicates: list[str] = []
    quoted: list[str] = []
    uncertain: list[str] = []
    spans = (*unbounded, *blanked)
    for head in heads:
        qualified = declared_name(head.name, _prefix_at(marks, head.keyword))
        # A head only some reading calls code -- after `+--`, `+/-` or a quote
        # that may open a literal -- may be inside a comment or a literal to
        # Lean. Taken as a head, it cut the real statement before it short and
        # named a declaration nobody wrote. Keyword and name must be code in
        # every reading, as a scope keyword must.
        if lexed.uncertain(head.keyword, head.end):
            uncertain.append(qualified)
            problems.append(
                f"line {source.count(chr(10), 0, head.keyword) + 1}: `{head.kind} {qualified}` sits "
                "where Hardy cannot tell code from a comment or a literal, so it cannot tell whether "
                "it is a declaration, nor where the statement before it ends; put a space before a "
                "`--`, `/-` or char literal that follows a symbol"
            )
        # A head the quotation count covers -- bounded, unbounded or of
        # uncertain extent -- is syntax a macro builds, or real code a module
        # token moved the count past (N4). Either reading changes what the
        # file declares and where the statement before it ends, so neither is
        # taken, as for a scope keyword in the same place.
        if any(low <= head.keyword < high for low, high in spans):
            quoted.append(qualified)
            problems.append(
                f"line {source.count(chr(10), 0, head.keyword) + 1}: `{head.kind} {qualified}` sits "
                "inside a syntax quotation, so Hardy cannot tell whether it is a declaration or "
                "syntax a macro builds, nor where the statement before it ends; build quoted "
                "commands from a name that is not a declaration keyword, in a separate file"
            )
        earlier = first.setdefault(_name_key(qualified), head.keyword)
        if earlier == head.keyword:
            continue
        duplicates.append(qualified)
        problems.append(
            f"line {source.count(chr(10), 0, head.keyword) + 1}: `{qualified}` is declared twice "
            f"(first on line {source.count(chr(10), 0, earlier) + 1}); Lean refuses a real repeat, "
            "so one copy sits inside a syntax quotation, and Hardy cannot tell which statement "
            "is the real one"
        )
    return _Structure(
        text, tokens, marks, tuple(heads), tuple(problems), tuple(duplicates), tuple(quoted), tuple(uncertain)
    )


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

    Keyed by name, so a name declared twice is refused with
    `DuplicateDeclaration` rather than keyed to whichever copy came last: that
    copy was a quoted `theorem t : False` beside a real `theorem t : True`, and
    the writeup gate accepted a document quoting the false one.
    """
    _refuse_unreadable(_structure(source))
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
