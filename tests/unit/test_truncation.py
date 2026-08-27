"""The one place that decides how an observation is cut down for a model.

`read_file` was the one tool result with no bound on it while every other path
had its own; these pin the shared rule the two now share, and the deliberate
difference between them -- a file read keeps its head, Lean keeps its tail.
"""

from __future__ import annotations

from hardy.truncation import DEFAULT_BYTE_LIMIT, DEFAULT_LINE_LIMIT, truncate

LINES = "".join(f"line {index}\n" for index in range(1, 5001))


def test_a_short_text_comes_back_whole_and_unmarked() -> None:
    result = truncate("a\nb\n")

    assert result.text == "a\nb\n"
    assert result.truncated is False
    assert result.truncated_by is None
    assert (result.total_lines, result.output_lines) == (2, 2)
    assert result.next_line is None


def test_the_line_limit_keeps_the_head_and_names_where_to_resume() -> None:
    result = truncate(LINES, keep="head", byte_limit=None)

    assert result.truncated_by == "lines"
    assert result.output_lines == DEFAULT_LINE_LIMIT
    assert result.text.startswith("line 1\n")
    assert result.text.endswith(f"line {DEFAULT_LINE_LIMIT}\n")
    assert (result.first_line, result.next_line) == (1, DEFAULT_LINE_LIMIT + 1)
    assert result.total_lines == 5000


def test_the_byte_limit_binds_when_it_is_reached_first() -> None:
    """Two independent limits, whichever is hit first. A file of few very long
    lines is under the line count and still far too large to hand a model, and
    the report must name the limit that actually did the cutting -- a caller
    told `lines` while bytes did the work would raise the wrong one.
    """
    fat = "".join("x" * 5_000 + "\n" for _ in range(100))

    result = truncate(fat)

    assert result.truncated_by == "bytes"
    assert result.output_lines < 100
    assert len(result.text.encode("utf-8")) <= DEFAULT_BYTE_LIMIT


def test_a_cut_never_returns_half_a_line() -> None:
    """A fragment ending mid-token reads as a file that says something the
    file does not say. The cut lands on a line boundary whichever limit made
    it.
    """
    result = truncate(LINES, byte_limit=100)

    assert result.text.endswith("\n")
    assert all(line.startswith("line ") for line in result.text.splitlines())


def test_one_line_larger_than_the_budget_is_cut_rather_than_dropped() -> None:
    """The exception, and the only one: returning nothing at all for a file
    that is one enormous line is not a more honest answer than returning its
    beginning.
    """
    result = truncate("y" * 1_000 + "\n", byte_limit=40)

    assert result.text == "y" * 40
    assert result.truncated_by == "bytes"
    assert result.total_bytes == 1_001


def test_a_multibyte_character_is_never_split_by_the_byte_cut() -> None:
    """Lean's output is full of `∀`, `↑` and `ℝ`, and a cut landing inside one
    of them would hand the model a byte sequence no file contains.
    """
    result = truncate("∀" * 100 + "\n", byte_limit=50)

    assert result.text == "∀" * 16
    assert len(result.text.encode("utf-8")) <= 50


def test_the_tail_is_what_lean_keeps() -> None:
    """Lean's end is where the unsolved goal is, so `_observe` keeps it. That
    difference is deliberate and survives sharing this helper with the reads
    that want the opposite.
    """
    result = truncate(LINES, keep="tail", byte_limit=None)

    assert result.text.endswith("line 5000\n")
    assert result.first_line == 5000 - DEFAULT_LINE_LIMIT + 1
    assert result.next_line is None


def test_start_line_pages_forward_and_numbers_against_the_whole_text() -> None:
    """The answer to "then how do I see the rest". Line numbers stay against
    the file, not against the window, because the number the model was handed
    to ask with has to mean the same thing when it asks.
    """
    result = truncate(LINES, start_line=2_001, byte_limit=None)

    assert result.text.startswith("line 2001\n")
    assert (result.first_line, result.next_line) == (2_001, 4_001)
    assert result.total_lines == 5_000


def test_a_start_past_the_end_returns_nothing_rather_than_the_end() -> None:
    result = truncate("a\nb\n", start_line=9)

    assert result.text == ""
    assert result.output_lines == 0
    assert result.total_lines == 2


def test_the_last_page_reports_reaching_the_end_rather_than_a_cut() -> None:
    """A window that fits still needs a line said about it.

    A caller paging with `start_line` holds a fragment whether or not this
    call cut anything, and "truncated by None" is not a thing to tell a model
    about the end of a file.
    """
    result = truncate(LINES, start_line=4_991, byte_limit=None)

    assert result.truncated is False
    assert result.next_line is None
    assert result.summary == "lines 4991-5000 of 5000 (100 of 48893 bytes); to the end of the file"


def test_a_cut_window_names_the_limit_that_bound_it() -> None:
    assert truncate(LINES, byte_limit=None).summary.endswith("truncated by lines")
    assert truncate(LINES, line_limit=None, byte_limit=100).summary.endswith("truncated by bytes")
