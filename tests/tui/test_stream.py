"""The incremental wrapper: whole lines, once each, never rewritten."""

from __future__ import annotations

import pytest

from hardy.tui.stream import LineWriter, tool_finished, tool_started


def drain(writer: LineWriter, deltas: list[str]) -> list[str]:
    out: list[str] = []
    for delta in deltas:
        out.extend(writer.feed(delta))
    out.extend(writer.flush())
    return out


def bare(lines: list[str]) -> list[str]:
    """The text without its marker or indent. `str.strip` will not do it:
    the marker is `● `, and stripping whitespace leaves the bullet behind."""
    return [line.removeprefix("● ").strip() for line in lines]


def test_a_stream_of_deltas_renders_as_the_whole_message_would():
    """The only honest test of an incremental renderer: the same text, split
    every way, has to come out the same."""
    text = (
        "The Lean kernel accepted the statement, and the writeup compiles "
        "without warnings.\nA second paragraph follows the first."
    )
    whole = drain(LineWriter(40), [text])
    letter_by_letter = drain(LineWriter(40), list(text))
    assert letter_by_letter == whole


def test_no_line_is_ever_handed_out_twice():
    writer = LineWriter(24)
    seen = drain(writer, ["one two three four five six seven eight nine ten"])
    # Reconstructing the message from the lines must not repeat or lose a word.
    words = " ".join(bare(seen)).split()
    assert words == ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def test_a_partial_word_is_never_printed():
    """The last wrapped line can still grow, so it is withheld until it can't."""
    writer = LineWriter(20)
    early = writer.feed("supercalifragilistic")
    assert early == []
    later = writer.feed(" expialidocious done")
    # Whatever was released, it cannot be a fragment of a word still arriving.
    assert all("expialidocio" not in line or "expialidocious" in line for line in later)


def test_the_first_line_takes_the_marker_and_the_rest_are_indented():
    lines = drain(LineWriter(20, marker="● "), ["alpha beta gamma delta epsilon"])
    assert lines[0].startswith("● ")
    assert all(line.startswith("  ") for line in lines[1:])


def test_a_blank_line_between_paragraphs_survives():
    lines = drain(LineWriter(40), ["first\n\nsecond"])
    assert bare(lines) == ["first", "", "second"]


def test_flush_does_not_repeat_lines_a_feed_already_settled():
    writer = LineWriter(16)
    fed = writer.feed("alpha beta gamma delta\n")
    flushed = writer.flush()
    assert flushed == []
    assert bare(fed) == ["alpha beta", "gamma delta"]


def test_flush_releases_the_withheld_tail():
    writer = LineWriter(40)
    assert writer.feed("a short line") == []
    assert bare(writer.flush()) == ["a short line"]


def test_nothing_in_makes_nothing_out():
    writer = LineWriter(40)
    assert writer.feed("") == []
    assert writer.flush() == []
    # No marker spent, so nothing was drawn -- `transcript._render` returns []
    # for an empty message and a streamed one must not differ.
    assert not writer.wrote_anything


@pytest.mark.parametrize("width", [8, 1, 0, -5])
def test_an_absurd_width_still_produces_lines(width: int):
    """The shell asks the terminal for its size and a terminal can answer 0."""
    assert drain(LineWriter(width), ["alpha beta gamma"])


def test_tool_lines_name_the_tool_and_its_outcome():
    assert tool_started("check_lean") == "▸ check_lean"
    assert tool_finished("check_lean", True, 2.44).startswith("✓ check_lean")
    assert tool_finished("check_lean", False, 2.0).startswith("✗ check_lean")
    # An unnamed call still draws, rather than printing a bare marker.
    assert "tool" in tool_started("")
