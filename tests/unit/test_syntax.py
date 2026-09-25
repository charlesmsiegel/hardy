"""The Lean lexer every declaration, hole and assumption scan stands on.

Char-literal behaviour was checked against Lean 4.35.0-rc3: `'"'`, `'\\''`,
`'\\\\'`, `'\\x41'` and `'\\u0041'` are characters, a newline between the
quotes is one too, `'''` is not a literal at all, and a quote straight after
an identifier character (`x'`, `f''`) continues the name.
"""

from __future__ import annotations

import pytest

from hardy.formal import syntax
from hardy.formal.lean import LeanTools, scannable
from hardy.formal.verifier import proof_body_violation

# --- #192: a char literal is a literal ---------------------------------------

PHANTOM = "def q : Char := '\"'\ntheorem bad : False := sorry\naxiom cheat : False\ndef s := \"x\"\n"


def test_a_quote_char_does_not_open_a_string_that_hides_what_follows():
    """`'"'` is one `Char`. Read as a string opener, it blanked every line to
    the next `"` -- a theorem the audit never asked about, an axiom never
    compared against its approval, and a `sorry` no hole scan saw."""
    assert syntax.declarations(PHANTOM)["theorem"] == ("bad",)
    assert syntax.assumptions(PHANTOM) == (("cheat", "False"),)
    assert LeanTools.has_holes(PHANTOM)


@pytest.mark.parametrize(
    "literal",
    ["'\"'", "'\\''", "'\\\\'", "'a'", "'\\\"'", "'\\n'", "'\\x41'", "'\\u0041'", "'α'", "' '", "'-'", "'/'", "'«'"],
)
def test_char_literals_are_blanked_like_strings(literal):
    source = f"def c : Char := {literal}\ntheorem after : True := trivial -- {literal}\n"
    stripped = syntax.strip_comments(source)
    assert stripped == f"def c : Char := {' ' * len(literal)}\ntheorem after : True := trivial{' ' * (len(literal) + 4)}\n"
    assert syntax.declarations(source)["theorem"] == ("after",)


@pytest.mark.parametrize("literal", ["'\"'", "'\\''", "'\\\\'", "'a'"])
def test_keep_strings_copies_a_char_literal_through(literal):
    source = f"theorem t : ({literal} : Char) = {literal} := rfl\n"
    assert syntax.strip_comments(source, keep_strings=True) == source


def test_a_newline_between_the_quotes_is_a_char_and_keeps_its_line():
    source = "def c : Char := '\n'\ntheorem after : True := trivial\n"
    stripped = syntax.strip_comments(source)
    assert len(stripped) == len(source)
    assert stripped.splitlines()[1].strip() == ""
    assert syntax.declarations(source)["theorem"] == ("after",)


@pytest.mark.parametrize(
    "source",
    [
        "theorem x' : True := trivial\ndef s := \"a\"\ntheorem y : True := trivial\n",
        "def f'' (n : Nat) := n\n#eval f'' 1\ntheorem y : \"a\" = \"a\" := rfl\n",
        "theorem add_comm' : True := trivial\ntheorem y : \"'\" = \"'\" := rfl\n",
        "theorem h₁' : True := trivial\ntheorem y : True := by simp [h₁']\n",
    ],
)
def test_a_primed_name_is_still_a_name(source):
    """A quote after an identifier character continues the identifier, so the
    `'` in `x'` never opens a literal -- or `x' ... '` would blank its span."""
    stripped = syntax.strip_comments(source)
    assert len(stripped) == len(source)
    assert stripped.count("\n") == source.count("\n")
    names = syntax.declarations(source)["theorem"]
    assert names[-1] == "y"
    for name in ("x'", "add_comm'", "h₁'"):
        if f"theorem {name}" in source:
            assert name in names
            assert name in stripped


def test_two_quotes_are_not_an_empty_char():
    """Lean does not start a literal at `''` (Mathlib's `f '' s` is an
    image), so neither may the lexer, or the next quote would pair wrongly."""
    source = "theorem t : f '' s = t := sorry\ndef s := \"x\"\n"
    assert syntax.strip_comments(source).startswith("theorem t : f '' s = t := sorry\n")
    assert LeanTools.has_holes(source)


def test_an_unclosed_quote_is_not_a_literal():
    """`'ab'` is no char literal, so nothing after the quote is blanked."""
    source = "def x := 'ab'\ntheorem t : True := sorry\n"
    assert syntax.strip_comments(source) == source
    assert LeanTools.has_holes(source)


@pytest.mark.parametrize(
    "literal", ["'\\u{41}'", "'\\x4'", "'\\q'", "'\\0'"],
)
def test_an_escape_lean_refuses_is_not_a_literal(literal):
    """Lean 4.35 refuses these, and the lexer blanks only what Lean reads as a
    character: recognising more would hide text Lean treats as code."""
    source = f"def c := {literal}\n"
    assert syntax.strip_comments(source) == source


def test_normalise_lean_keeps_a_char_literal_whole():
    """`'"'` must not start a string whose body `normalise_lean` then refuses
    to collapse -- and whose end would start a phantom one over real code."""
    assert syntax.normalise_lean("f '\"'   x \"a  b\"") == "f '\"' x \"a  b\""
    assert syntax.normalise_lean("f  ' '   x") == "f ' ' x"
    assert syntax.normalise_lean("f  x'   y'") == "f x' y'"


def test_a_paren_char_inside_a_quotation_does_not_unbalance_it():
    """`scannable` counts parentheses to find a quotation's end. A `'('` read
    as a parenthesis ran the quotation on past a real `sorry` and blanked it."""
    source = "theorem t : True := (by have := `(term| '('); exact sorry)\n"
    assert LeanTools.has_holes(source)
    assert "sorry" in scannable(source)


def test_a_char_literal_does_not_hide_a_command_from_the_body_gate():
    """The deferred wave-1 item: with `'"'` opening a phantom string, the
    `macro_rules` after it was blank to `proof_body_violation`, so a body could
    rewrite Hardy's own `#print axioms` line."""
    body = (
        "by\n  have c : Char := '\"'\n  exact trivial\n\n"
        "macro_rules | `(#print axioms $_) => `(#print \"'T' depends on axioms: []\")"
    )
    refusal = proof_body_violation(body)
    assert refusal is not None
    assert "macro_rules" in refusal


# --- #297: a declaration need not start its line ------------------------------
#
# Lean commands are whitespace-insensitive: checked against Lean 4.35.0-rc3,
# every source below declares the theorem named, and `#check` finds it under
# the name asserted.


@pytest.mark.parametrize(
    "source",
    [
        "lemma good : True := trivial\ndef a : Nat := 1 theorem sneaky : 1 = 2 := sorry\n",
        "lemma good : True := trivial\nomit h in theorem sneaky : 1 = 2 := sorry\n",
        "lemma good : True := trivial\ninclude h in theorem sneaky : 1 = 2 := sorry\n",
        "namespace Foo\nend Foo theorem sneaky : False := sorry\n",
        "def a := 1theorem sneaky : True := trivial\n",
        "def a := 0x1Ftheorem sneaky : True := trivial\n",
        "def a := 0xdeftheorem sneaky : True := trivial\n",
        "def a := 1_0.5e+3theorem sneaky : True := trivial\n",
        "def a := (1,2).1theorem sneaky : True := trivial\n",
        "def a := 'a'theorem sneaky : True := trivial\n",
        "def a := \"s\"theorem sneaky : True := trivial\n",
        "def a := (1)theorem sneaky : True := trivial\n",
        "def «y» := 1\ndef a := «y»theorem sneaky : True := trivial\n",
        "theorem«sneaky» : True := trivial\n",
        "/- note -/theorem sneaky : True := trivial\n",
    ],
)
def test_a_theorem_after_other_tokens_on_its_line_is_found(source):
    """A theorem the scan misses is never asked about by `#print axioms`, never
    reserved to a registered result, and never owes a writeup."""
    found = syntax.declarations(source)["theorem"]
    assert found in (("sneaky",), ("«sneaky»",))
    assert set(syntax.statements(source)) >= set(found)


@pytest.mark.parametrize(
    "source",
    [
        "def «a theorem» := 1\n",
        "def «a theorem b» := 1\n",
        "def mytheorem x := 1\n",
        "def x := Foo.theorem y\n",
        "def theorems x := 1\n",
        "def x1theorem y := 1\n",
        "def x'theorem y := 1\n",
        "def get!theorem y := 1\n",
    ],
)
def test_the_word_inside_a_name_is_not_a_declaration(source):
    assert syntax.declarations(source)["theorem"] == ()


def test_a_private_modifier_mid_line_is_read_with_its_theorem():
    source = "def a := 1 private theorem p : True := trivial\ndef xprivate theorem q : True := trivial\n"
    found = syntax.declarations(source)
    assert found["theorem"] == ("p", "q")
    assert found["private"] == ("p",)


def test_a_same_line_statement_is_read_from_its_own_keyword():
    source = "def a : Nat := 1 theorem sneaky : 1 = 2 := sorry\n"
    assert syntax.statements(source) == {"sneaky": "theorem sneaky : 1 = 2"}


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("namespace Foo\ntheorem x : True := trivial\nend Foo theorem y : True := trivial\n", ("Foo.x", "y")),
        ("namespace Foo theorem t : True := trivial end Foo theorem u : True := trivial\n", ("Foo.t", "u")),
        ("section S namespace N theorem t : True := trivial end N end S\n", ("N.t",)),
        ("namespace«Foo» theorem t : True := trivial end«Foo»\ntheorem u : True := trivial\n", ("«Foo».t", "u")),
        ("namespace Foo\nsection\nend theorem t : True := trivial end Foo\n", ("Foo.t",)),
        # `noncomputable section` and `mutual` open scopes a bare `end` closes;
        # read as nothing, that `end` closed the namespace around them.
        ("namespace Foo\nnoncomputable section\nend\ntheorem y : True := trivial\nend Foo\n", ("Foo.y",)),
        (
            "namespace Foo\nmutual\ntheorem a : True := trivial\ntheorem b : True := trivial\nend\n"
            "theorem y : True := trivial\nend Foo\n",
            ("Foo.a", "Foo.b", "Foo.y"),
        ),
        # `namespace A.B` opens two scopes; `end B` closes only the inner one.
        ("namespace A.B\nend B\ntheorem t : True := trivial\nend A\n", ("A.t",)),
        ("namespace A.B\ntheorem t : True := trivial\nend A.B\ntheorem u : True := trivial\n", ("A.B.t", "u")),
        # An `end` takes a name on a later line only when it is indented past
        # the `end` (Lean's `checkColGt`); a keyword is never its name.
        ("namespace Foo\nnamespace Bar\nend\n Bar\ntheorem x : True := trivial\nend Foo\n", ("Foo.x",)),
        ("namespace Foo\nsection\nend\ntheorem x : True := trivial\nend Foo\n", ("Foo.x",)),
        ("namespace Foo\nsection\nend theorem x : True := trivial\nend Foo\n", ("Foo.x",)),
        # Words that only look like scope commands.
        ("namespace Foo\ndef x := end_of\ntheorem y : True := trivial\nend Foo\n", ("Foo.y",)),
        ("namespace Foo\ndef «end» := 1\ntheorem y : True := trivial\nend Foo\n", ("Foo.y",)),
    ],
)
def test_scopes_are_read_from_the_token_stream(source, names):
    assert syntax.declarations(source)["theorem"] == names


def test_the_assumption_scan_qualifies_by_the_same_scopes():
    source = "namespace Foo\nnoncomputable section\nend\naxiom cheat : False\nend Foo\n"
    assert syntax.assumptions(source) == (("Foo.cheat", "False"),)


@pytest.mark.parametrize(
    "source",
    [
        "def a := 1axiom cheat : False\n",
        "def a := 0b1axiom cheat : False\n",
        "def a := 1 axiom cheat : False\n",
        "namespace Foo\nend Foo axiom cheat : False\n",
    ],
)
def test_an_axiom_after_other_tokens_is_refused_as_unreadable(source):
    """`assumptions` reads an axiom only at the start of its line. One anywhere
    else must still reach the refusal, or its statement is never compared
    against what a human approved."""
    assert syntax.unreadable_assumptions(source)


def test_a_hex_numeral_that_swallows_the_keyword_declares_nothing():
    """`0x1Faxiom` is the numeral `0x1Fa` and then `xiom`, to Lean."""
    assert syntax.unreadable_assumptions("def a := 0x1Faxiom cheat : False\n") == ()


# --- Review round 1: every reading Lean's grammar leaves open -----------------
#
# Each module source below was elaborated with Lean 4.35.0-rc3 (core only): it
# elaborates, and `#print axioms` gives the qualified name asserted here and
# `sorryAx` for the `bad`/`x` theorem. A symbol token may end in `'` (core's
# `]'` and `×'`, Mathlib's `∑'`, anything `notation` adds), a string may be
# interpolated or plain, and `//` may swallow the `/` of a `/-`; Hardy cannot
# tell from characters alone, so it keeps every reading and a scan sees what
# any of them shows.

SYMBOL_QUOTE_MODULES = [
    # C1: `×'` and `]'` are core tokens; after them `'"'` is not a char.
    "theorem good : True := trivial\n"
    "def q : Lean.MacroM Lean.Syntax := `(Nat ×'\"'\")\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    "theorem good : True := trivial\n"
    "def q : Lean.MacroM Lean.Syntax := `(xs[0]'\"'\")\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    # C2: `'"'` inside an interpolation, and after `!`/`λ`, is a char.
    "theorem good : True := trivial\ndef s : String := s!\"{'\"'}\"\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    "theorem good : True := trivial\ndef q : Lean.MacroM Lean.Syntax := `(!'\"')\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    "theorem good : True := trivial\ndef q : Lean.MacroM Lean.Syntax := `(λ'\"' => 0)\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    # A brace inside an interpolated string's code is not the string's end.
    "theorem good : True := trivial\ndef s : String := s!\"{\"}\"}\"\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
    # `//` is Lean's subtype token, so `//-1` is `// -1`, not a comment.
    "theorem good : True := trivial\ndef p := {x : Int //-1 = x}\n"
    "theorem bad : False := sorry\n/- -/\n",
    # An escaped name runs to its `»` across a newline.
    "theorem good : True := trivial\ndef «a\n\"» := 1\n"
    "theorem bad : False := sorry\ndef t := \"x\"\n",
]


@pytest.mark.parametrize("source", SYMBOL_QUOTE_MODULES)
def test_every_reading_of_an_ambiguous_quote_is_scanned(source):
    assert syntax.declarations(source)["theorem"] == ("good", "bad")
    assert LeanTools.has_holes(source)


@pytest.mark.parametrize("prefix", ["Π", "Σ", "!", "λ", "×", "]", "∑", "⁻¹", "x.", "("])
def test_a_quote_after_any_symbol_is_read_both_ways(prefix):
    """Whatever token the symbol belongs to, a `sorry` behind `'"'` is seen.
    `Π`, `Σ` and `λ` are Greek but not letter-like to Lean, so they are symbols."""
    source = f"def q := f {prefix}'\"'\ntheorem bad : False := sorry\ndef t := \"x\"\n"
    assert "bad" in syntax.declarations(source)["theorem"]
    assert LeanTools.has_holes(source)


@pytest.mark.parametrize("name", ["x", "x!", "x™", "é", "h₁"])
def test_a_quote_after_an_identifier_continues_it(name):
    """Lean's identifier characters include `!`, `?`, `'`, the letter-like
    block (`™`) and Latin-1 letters, so `x™'` is one name and the `"` after it
    opens a string -- in every reading, so nothing is uncertain."""
    source = f"def q := {name}'\"a\"\ntheorem t : True := trivial\n"
    assert not syntax.lex(source).uncertain()
    assert syntax.declarations(source)["theorem"] == ("t",)


def test_a_quote_after_a_numeral_starts_a_char():
    """`2'"'` is the numeral 2 and the char `"` to Lean -- no ambiguity."""
    source = "def g := f 2'\"'\ntheorem bad : False := sorry\n"
    assert syntax.strip_comments(source).startswith("def g := f 2   \ntheorem bad")


def test_an_ambiguous_quote_leaves_the_reading_uncertain_and_a_certain_one_does_not():
    assert syntax.lex("def q := `(xs[0]'\"'\")\n").uncertain()
    assert not syntax.lex("def q : Char := '\"'\ndef s := \"x\"\n").uncertain()
    assert not syntax.lex("theorem t : f '' s = t := sorry\n").uncertain()


# C3: the scope walk names what Lean names.

@pytest.mark.parametrize(
    ("source", "names"),
    [
        # (a) `constant` is not a Lean 4 keyword, so it is a namespace name.
        ("theorem t : True := trivial\nnamespace constant\ntheorem t : False := sorry\nend constant\n",
         ("t", "constant.t")),
        ("theorem t : True := trivial\nnamespace alias\ntheorem t : False := sorry\nend alias\n",
         ("t", "alias.t")),
        # (c) a keyword after a projection dot is a field name.
        ("structure I where\n  «end» : Nat\ntheorem x : True := trivial\nnamespace Foo\n"
         "def i : I := ⟨1⟩\ndef e : Nat := (i).end\ntheorem x : False := sorry\nend Foo\n",
         ("x", "Foo.x")),
    ],
)
def test_the_scope_walk_is_not_misled_by_names_quotations_or_fields(source, names):
    assert syntax.declarations(source)["theorem"] == names
    assert syntax.unreadable_structure(source) == ()


@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("theorem Bar.bad : True := trivial\nopen Lean in\n"
         "def q : MacroM Syntax := `(command| namespace Bar)\ntheorem bad : False := sorry\n",
         ("Bar.bad", "bad")),
        ("theorem x : True := trivial\nnamespace Foo\nopen Lean in\n"
         "def q : MacroM Syntax := `(command| end Foo)\ntheorem x : False := sorry\nend Foo\n",
         ("x", "Foo.x")),
    ],
)
def test_a_scope_command_in_a_quotation_moves_no_scope_and_is_refused(source, names):
    """C3 (b): read as data, the quoted scope names nothing -- the names are
    Lean's. But a module's own `notation` can move where a quotation ends
    (review round 3, N4), so whether it *is* data is not something Hardy can
    count; a gate naming declarations refuses the file instead."""
    assert syntax.declarations(source)["theorem"] == names
    assert syntax.unreadable_structure(source)


def test_named_declarations_share_the_scope_walk():
    source = (
        "theorem Bar.bad : True := trivial\nopen Lean in\n"
        "def q : MacroM Syntax := `(command| namespace Bar)\ntheorem bad : False := sorry\n"
    )
    assert syntax.named_declarations(source) == ("Bar.bad", "q", "bad")


def test_a_scope_command_hardy_cannot_place_is_unreadable():
    """Behind an ambiguous quote, `namespace Bar` is code in one reading and
    string in another, and the names after it depend on which."""
    source = "def q := `(xs[0]'\"'\nnamespace Bar \")\ntheorem bad : False := sorry\n"
    assert syntax.unreadable_structure(source)
    unbalanced = "def q := `(command| namespace Bar\ntheorem bad : False := sorry\n"
    assert syntax.unreadable_structure(unbalanced)


def test_a_theorem_inside_a_command_quotation_is_reported():
    """Review round 3, N4: where a quotation ends depends on the token table,
    so a `theorem` inside one is reported. For a macro's quoted theorem that
    is a name Lean never declares -- the audit cannot find it and the save is
    refused, which is the side to be wrong on."""
    source = 'macro "mk" : command => `(theorem x : True := trivial)\ntheorem y : True := trivial\n'
    assert syntax.declarations(source)["theorem"] == ("x", "y")


# I1: a wrapper span cannot swallow a theorem.

def test_a_theorem_inside_a_wrapper_before_its_in_is_found():
    source = (
        "theorem good : True := trivial\n"
        "def a := 1 open Nat theorem hidden : False := sorry in theorem shown : True := trivial\n"
    )
    assert syntax.declarations(source)["theorem"] == ("good", "hidden", "shown")
    column_zero = "open Nat theorem hidden : False := sorry in theorem shown : True := trivial\n"
    assert syntax.declarations(column_zero)["theorem"] == ("hidden", "shown")


def test_modifiers_are_read_back_from_the_keyword():
    source = "@[simp] private noncomputable theorem p : True := trivial\nprotected lemma q : True := trivial\n"
    found = syntax.declarations(source)
    assert found["private"] == ("p",)
    assert found["lemma"] == ("q",)


def test_too_many_readings_fail_closed():
    """Past a bound the lexer stops telling readings apart: everything after is
    code in some reading, and the module is unreadable."""
    source = 'def s := "{"\n' * 60 + "theorem t : True := trivial -- sorry\n"
    lexed = syntax.lex(source)
    assert lexed.overflow is not None
    assert syntax.unreadable_structure(source)
    assert LeanTools.has_holes(source)


def test_normalise_lean_compares_ambiguous_text_verbatim():
    """Where a reading calls whitespace part of a literal, it is not collapsed:
    the comparison gets stricter, never looser."""
    assert syntax.normalise_lean("f  `(xs[0]'\"'  a  \")") == "f `(xs[0]'\"'  a  \")"
    assert syntax.normalise_lean("f   «a  b»   x") == "f «a  b» x"


# --- Review round 2 (N1): a `«` only some reading opens hides nothing ---------
#
# Each module elaborates under Lean 4.35.0-rc3 and `#print axioms` reports the
# holed theorem named here as resting on `sorryAx`. In `('«')` Lean reads a
# char, and in `"{«"` a plain string; the other reading opens a name at the
# `«` that runs to the next `»` -- in a comment, or a later real name -- and
# the combined view used to read that whole stretch as one name.

GUILLEMET_MODULES = [
    ("theorem good : True := trivial\ndef c : Char := ('«')\ntheorem bad : False := sorry\n-- »\n",
     ("good", "bad")),
    ('theorem good : True := trivial\ndef s : String := "{«"\ntheorem bad : False := sorry\n-- »\n',
     ("good", "bad")),
    ("theorem x : True := trivial\ndef c : Char := ('«')\nnamespace Foo\ntheorem x : False := sorry\n"
     "end Foo\ndef «y» := 1\n",
     ("x", "Foo.x")),
    ("theorem good : True := trivial\ndef c : Char := ('«')\n"
     "def q : Lean.MacroM Lean.Syntax := `(x)\ntheorem bad : False := sorry\ndef «y» := (1)\n",
     ("good", "bad")),
]


@pytest.mark.parametrize(("source", "names"), GUILLEMET_MODULES)
def test_a_guillemet_only_some_reading_opens_hides_nothing(source, names):
    assert syntax.declarations(source)["theorem"] == names
    assert LeanTools.has_holes(source)
    # And the file is reported: where the name ends is not something Hardy
    # can say, so a gate naming declarations refuses rather than guesses.
    assert any("«" in problem for problem in syntax.unreadable_structure(source))


def test_a_guillemet_every_reading_opens_is_still_one_name():
    source = "theorem «a theorem b» : True := trivial\ndef c := «sorry»\n"
    assert syntax.declarations(source)["theorem"] == ("«a theorem b»",)
    assert not LeanTools.has_holes(source)
    assert syntax.unreadable_structure(source) == ()
    assert syntax.lex(source).uncertain_names() == ()


def test_a_delimiter_one_reading_uses_stays_visible_where_another_reads_code():
    """N2: after `×`, `r#"` may open a raw string or be `r` and `#` in a token.
    The combined view shows `r#` because one reading calls it code; the token
    boundary the raw reading makes still reaches the tokenizer."""
    source = 'def q := x ×r#"a"# theorem t : True := trivial\n'
    text = syntax.strip_comments(source)
    assert text.startswith("def q := x ×r#")
    assert syntax.declarations(source)["theorem"] == ("t",)


def test_a_boundary_one_reading_makes_splits_the_token_for_all():
    """One reading's `'a'` literal ends right before `theorem`; the other reads
    the identifier `a'theorem`. The keyword is found either way."""
    source = "def q := x ×'a'theorem t : True := sorry\n"
    assert "t" in syntax.declarations(source)["theorem"]
    assert LeanTools.has_holes(source)


# --- Review round 3 (N4): a module's notation moves where a quotation ends ----
#
# Lean 4.35.0-rc3 elaborates both with rc=0 and reports `bad` / `Foo.x` as
# resting on `sorryAx`: `⟪(` swallows the quotation's `(`, so the quotation
# ends at `⸨)`'s parenthesis rather than at the first `)`, and everything
# between is code. Counting parentheses blanked it.

NOTATION_QUOTATION = (
    'notation "⟪(" x => x\nnotation:max x "⸨)" => x\ntheorem good : True := trivial\n'
    "open Lean in\ndef q : MacroM Syntax := `(⟪( 1)\ntheorem bad : False := sorry\n"
    "def r : Nat := 1 ⸨)\n"
)


def test_a_quotation_a_notation_extends_hides_no_declaration_or_hole():
    assert syntax.declarations(NOTATION_QUOTATION)["theorem"] == ("good", "bad")
    # Where the count says `bad` is quoted, the file is unreadable (round 2).
    assert any("theorem bad`" in problem and "quotation" in problem
               for problem in syntax.unreadable_structure(NOTATION_QUOTATION))
    assert LeanTools.has_holes(NOTATION_QUOTATION)
    assert "sorry" in scannable(NOTATION_QUOTATION)


def test_a_scope_inside_a_quotation_a_notation_extends_is_refused():
    source = (
        'notation "⟪(" x => x\nnotation:max x "⸨)" => x\ntheorem x : True := trivial\n'
        "open Lean in\ndef q : MacroM Syntax := `(⟪( 1)\nnamespace Foo\n"
        "theorem x : False := sorry\nend Foo\ndef r : Nat := 1 ⸨)\n"
    )
    problems = syntax.unreadable_structure(source)
    assert any("namespace" in problem for problem in problems)
    assert LeanTools.has_holes(source)


def test_quotations_stay_data_for_the_hole_scan_without_local_tokens():
    """Without a `notation`, `syntax`, `macro`, ... of its own, the table is
    Lean's and the imports', and a quoted `sorry` is still not a hole."""
    source = "open Lean in\ndef q : MacroM Syntax := `(tactic| sorry)\n"
    assert not LeanTools.has_holes(source)
    assert LeanTools.has_holes('notation "⊕⊕" => 1\n' + source)


def test_a_simp_like_tactic_declaration_declares_a_token():
    """`declare_simp_like_tactic` adds its string to Lean's token table, like
    `syntax` does, so a module using it keeps its quotations visible."""
    declared = 'declare_simp_like_tactic mySimp "my_simp(" fun c => c\n'
    assert syntax.declares_tokens(syntax.lex(declared))
    source = "open Lean in\ndef q : MacroM Syntax := `(tactic| sorry)\n"
    assert LeanTools.has_holes(declared + source)


# --- Codex on #393: a quoted `theorem` that repeats a real one's name ----------
#
# Lean 4.35.0-rc3 elaborates `QUOTED_TWIN` with rc=0 and reports `'t' does not
# depend on any axioms`: the quoted `theorem t : False` is syntax, not a
# declaration. Round 3 scans `theorem` inside quotations on purpose (a
# module's notation can move where one ends, N4), so the scan sees two heads
# named `t`, and `statements()` kept the quoted one -- the writeup gate then
# accepted `theorem t : False` for a theorem the kernel checked as `True`.
# Lean refuses a real name declared twice (`t` has already been declared), so
# a repeat is never a module Lean accepts with both real.

QUOTED_TWIN = (
    "open Lean in\n"
    "theorem t : let s : MacroM Syntax := `(command| theorem t : False := by sorry); True := by\n"
    "  intro _; trivial\n"
)


def test_a_name_declared_twice_makes_the_structure_unreadable():
    problems = syntax.unreadable_structure(QUOTED_TWIN)
    assert any("`t`" in problem and "twice" in problem for problem in problems), problems


def test_statements_refuse_a_name_declared_twice_rather_than_overwrite():
    with pytest.raises(syntax.DuplicateDeclaration, match="`t`"):
        syntax.statements(QUOTED_TWIN)


def test_named_declarations_refuse_a_theorem_name_declared_twice():
    with pytest.raises(syntax.DuplicateDeclaration, match="`t`"):
        syntax.named_declarations(QUOTED_TWIN)


@pytest.mark.parametrize(
    "source",
    [
        "theorem t : True := trivial\ntheorem t : False := sorry\n",
        # A private repeat collides too: `a non-private declaration `t` has
        # already been declared`.
        "theorem t : True := trivial\nprivate theorem t : True := trivial\n",
        # Guillemets change the spelling, not the name.
        "theorem t : True := trivial\nlemma «t» : True := trivial\n",
        # Qualified the same way by the namespace in force.
        "namespace A\ntheorem t : True := trivial\nend A\ntheorem A.t : True := trivial\n",
    ],
)
def test_every_spelling_of_a_repeat_is_refused(source):
    assert any("twice" in problem for problem in syntax.unreadable_structure(source))
    with pytest.raises(syntax.DuplicateDeclaration):
        syntax.statements(source)


# --- Codex on #393, round 2: a quoted head is never a boundary or a declaration --
#
# Lean 4.35.0-rc3 elaborates both with rc=0 (`theorem` in place of Mathlib's
# `lemma`) and `T` depends on no axioms: the quoted head is syntax. Read as a
# head it ended `T`'s statement at `` `(command| ``, so a writeup quoting that
# prefix passed, and the quoted name -- one Mathlib already declares -- was a
# declaration the audit could resolve.

QUOTED_HEAD = (
    "open Lean in\n"
    "theorem T : let q : MacroM Syntax := `(command| lemma Nat.add_comm : False := by sorry); True := by\n"
    "  intro _; trivial\n"
)
QUOTED_IN_PROOF = (
    "open Lean in\ntheorem T : True := by\n  have _h : True := trivial\n"
    "  let _q : MacroM Syntax := `(command| lemma Nat.add_comm : False := by sorry)\n"
    "  exact True.intro\n"
)


@pytest.mark.parametrize("source", [QUOTED_HEAD, QUOTED_IN_PROOF, QUOTED_TWIN], ids=["statement", "proof", "twin"])
def test_a_head_inside_a_quotation_makes_the_structure_unreadable(source):
    problems = syntax.unreadable_structure(source)
    assert any("quotation" in problem and ("lemma" in problem or "theorem" in problem) for problem in problems), problems


@pytest.mark.parametrize("source", [QUOTED_HEAD, QUOTED_IN_PROOF], ids=["statement", "proof"])
def test_statements_refuse_rather_than_bound_at_a_quoted_head(source):
    with pytest.raises(syntax.QuotedDeclaration, match="Nat.add_comm"):
        syntax.statements(source)
    with pytest.raises(syntax.DeclarationRefused):
        syntax.named_declarations(source)


def test_a_quoted_head_is_still_scanned():
    """N4: a module's notation can move where a quotation ends, so the head is
    still seen -- it refuses the file rather than disappearing."""
    assert syntax.declarations(QUOTED_HEAD)["lemma"] == ("Nat.add_comm",)


def test_the_same_leaf_in_two_namespaces_is_not_a_repeat():
    source = "namespace A\ntheorem t : True := trivial\nend A\ntheorem t : True := trivial\n"
    assert syntax.unreadable_structure(source) == ()
    assert set(syntax.statements(source)) == {"A.t", "t"}
