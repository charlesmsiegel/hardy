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
