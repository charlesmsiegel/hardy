from __future__ import annotations

from hardy.tui import transcript


def test_a_user_turn_is_marked_with_an_angle_bracket():
    assert transcript.user_lines("hello", width=40) == ["> hello"]


def test_a_hardy_turn_is_marked_with_a_dot():
    assert transcript.hardy_lines("hello", width=40) == ["● hello"]


def test_continuation_lines_are_indented_under_the_marker():
    lines = transcript.hardy_lines("alpha beta gamma delta", width=14)
    assert lines[0] == "● alpha beta"
    assert all(line.startswith("  ") for line in lines[1:])


def test_explicit_newlines_are_preserved_and_indented():
    assert transcript.user_lines("one\ntwo", width=40) == ["> one", "  two"]


def test_a_notice_has_no_marker_but_keeps_the_indent():
    assert transcript.notice_lines("switched", width=40) == ["  switched"]


def test_a_word_longer_than_the_width_stays_on_one_line():
    """Counting characters is not enough: chopping the word preserves the count.

    textwrap.wrap defaults to break_long_words=True, so assert the line count
    as well, which is the thing that actually distinguishes the two behaviours.
    """
    word = "x" * 30
    lines = transcript.hardy_lines(word, width=10)
    assert len(lines) == 1
    assert lines[0].count("x") == 30


def test_an_empty_message_renders_nothing():
    assert transcript.hardy_lines("", width=40) == []
    assert transcript.user_lines("   ", width=40) == []


def test_a_hyphenated_path_is_not_split():
    """Hardy prints paths constantly; a hyphen-split path cannot be copied.

    Stripped-and-concatenated lines reconstruct a hyphen break losslessly (the
    hyphen stays attached to its preceding chunk, so no character is lost),
    so that join can't distinguish a split path from an intact one. Joining
    with a real newline, the way the lines are actually printed one per
    `out()` call, is what makes a split path fail to appear as one substring.
    """
    path = "/tmp/pytest-of-charl/pytest-123/test_something_long0/workspace"
    lines = transcript.notice_lines(f"Workspace: {path}", width=40)
    assert path in "\n".join(lines)
