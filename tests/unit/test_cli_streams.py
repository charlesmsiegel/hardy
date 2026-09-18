import io

from hardy.app import cli


def _redirected(encoding: str) -> io.TextIOWrapper:
    """A stream the way Windows opens a redirected stdout: the ANSI codepage."""
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


def test_a_redirected_cp1252_stream_can_print_mathematics() -> None:
    """`hardy prove ... > log` on Windows died with UnicodeEncodeError the
    first time a formalization mentioned ℕ, which is almost immediately."""
    stream = _redirected("cp1252")

    cli._utf8_streams(stream)
    print("Quantifiers: Universal over p : ℕ", file=stream)
    stream.flush()

    # Line endings stay the platform's; only the encoding is changed.
    assert stream.buffer.getvalue().decode("utf-8").rstrip() == "Quantifiers: Universal over p : ℕ"


def test_a_stream_without_reconfigure_is_left_alone() -> None:
    """A test harness's capture object is not a TextIOWrapper."""
    stream = io.StringIO()

    cli._utf8_streams(stream)
    print("ℕ", file=stream)

    assert stream.getvalue() == "ℕ\n"
