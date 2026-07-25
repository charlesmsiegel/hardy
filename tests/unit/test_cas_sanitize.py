"""`sanitize` and `parse_version`: pure functions, tested directly.

Both were previously verified only by the real-backend CI job (Linux, real
Singular/Macaulay2 binaries) -- the hermetic suite's fake sentinel kernel
never echoes stdin and never writes stderr, so it could not have noticed a
regression here. These tests exercise the Macaulay2 implementations against
the exact output shapes captured from real CI transcripts (run 30167127782
and run 30168174637), and confirm the identity default holds for the
backends that do not need to touch stdout at all.
"""

from __future__ import annotations

from hardy.cas import backend_for


def test_macaulay2_sanitize_strips_prompt_lines_and_blanks_counters() -> None:
    """Verbatim shape from CI run 30167127782's transcript dump for the cell
    `R = QQ[x, y]; f = x^2 + y^2; f`: an `iN :` prompt echoing the whole
    input line, a multi-line value with M2's own exponent art, and an
    `oN : ClassName` annotation -- all under counters that drift between a
    live session and the fresh kernel export verification replays into.
    """
    raw = 'i2 : R = QQ[x, y]; f = x^2 + y^2; f\n\n      2    2\no4 = x  + y\n\no4 : R\n'
    sanitized = backend_for("macaulay2").sanitize(raw)
    assert sanitized == '\n      2    2\no = x  + y\n\no : R\n'
    # The prompt line is gone outright -- it is an echo of source Hardy
    # already has on the CellRecord -- and no digit survives on an `oN`.
    assert "i2 :" not in sanitized
    assert "o4" not in sanitized


def test_macaulay2_sanitize_normalises_the_counter_width_not_only_its_digits() -> None:
    """The counter is a column as well as a token.

    M2 lays a value out as a net and pads every row after the first to the
    width of the `oN = ` prefix: five columns at `o4`, six at `o12`. Blanking
    the digits made the value lines agree and left the alignment row above them
    differing by one space, so the identical polynomial computed in a live
    session and in a fresh replay compared unequal the moment their counters
    differed in digit count -- which they do routinely, since every cell costs
    a live kernel two extra statements for its own sentinel markers and the
    exported script has none. The result was a false `diverged` on export and,
    through `_restore`, a poisoned session over a cell that had reproduced.
    """
    backend = backend_for("macaulay2")
    single = 'i2 : x^2 + y^2\n\n      2    2\no4 = x  + y\n\no4 : R\n'
    double = 'i9 : x^2 + y^2\n\n       2    2\no12 = x  + y\n\no12 : R\n'
    assert backend.sanitize(single) == backend.sanitize(double)
    assert backend.sanitize(double) == '\n      2    2\no = x  + y\n\no : R\n'


def test_macaulay2_sanitize_normalises_a_matrix_written_below_its_counter() -> None:
    """The same padding, on the rows M2 writes *after* the marker line."""
    backend = backend_for("macaulay2")
    single = 'o5 = | 1 2 |\n     | 3 4 |\n\no5 : Matrix\n'
    double = 'o100 = | 1 2 |\n       | 3 4 |\n\no100 : Matrix\n'
    assert backend.sanitize(single) == backend.sanitize(double)
    assert backend.sanitize(double) == 'o = | 1 2 |\n     | 3 4 |\n\no : Matrix\n'


def test_macaulay2_sanitize_does_not_dedent_across_a_block_boundary() -> None:
    """Only the run of padded lines touching the marker belongs to it.

    A blank line, a prompt, or the next marker ends the block, so a wide
    counter cannot reach back and eat indentation that is somebody else's
    content -- indentation is content in M2 more than anywhere else Hardy
    drives.
    """
    backend = backend_for("macaulay2")
    raw = 'o11 = 1\n\n       kept\n'
    assert backend.sanitize(raw) == 'o = 1\n\n       kept\n'


def test_macaulay2_sanitize_is_a_noop_on_text_without_prompts_or_counters() -> None:
    assert backend_for("macaulay2").sanitize("just some text\n") == "just some text\n"


def test_macaulay2_parse_version_strips_the_value_marker() -> None:
    """Verbatim shape from CI run 30168174637: `session.probe_version()`
    returned the literal string `'o = 1.26.06'` before this hook existed.
    """
    assert backend_for("macaulay2").parse_version("o = 1.26.06\n") == "1.26.06"


def test_macaulay2_parse_version_drops_a_following_type_annotation_line() -> None:
    """`sanitize` leaves an `o : ClassName` annotation line in place (real,
    useful context on an ordinary cell -- confirmed of a ring element,
    `o4 : R`, in the same transcript as the value-marker test above). If M2
    ever prints one after the version string too, only the first line -- the
    version itself, which never contains a newline -- may reach
    `session.version` and the exported script's header comment.
    """
    text = "o = 1.26.06\n\no : String\n"
    assert backend_for("macaulay2").parse_version(text) == "1.26.06"


def test_singular_sanitize_and_parse_version_are_identity() -> None:
    """Singular does not echo stdin in `-q` mode, so there is nothing for
    `sanitize` to strip, and no `parse_version` override is needed."""
    backend = backend_for("singular")
    text = "4310\n"
    assert backend.sanitize(text) == text
    assert backend.parse_version(text) == text


def test_sympy_parse_version_is_identity() -> None:
    """SymPy answers over the length-framed driver protocol -- `sanitize` is
    never called for it at all, but `parse_version` still needs to exist and
    be a no-op, since `probe_version` calls it unconditionally."""
    backend = backend_for("sympy")
    assert backend.parse_version("1.13.0") == "1.13.0"
