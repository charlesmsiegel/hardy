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
