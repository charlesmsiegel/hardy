"""Error classification for the interpreters that cannot report status.

A misclassified error is worse than a missed one: it is accepted into
replayable state, and the session then rebuilds from a cell that never worked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy.cas import backend_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "cas"


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_real_error_banners_are_classified_as_errors(backend) -> None:
    text = (FIXTURES / f"{backend}-errors.txt").read_text(encoding="utf-8")
    assert backend_for(backend).classify(text) == "error"


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_each_error_line_is_recognised_on_its_own(backend) -> None:
    text = (FIXTURES / f"{backend}-errors.txt").read_text(encoding="utf-8")
    for line in text.strip().splitlines():
        assert backend_for(backend).classify(line) == "error", line


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_ordinary_output_is_not_mistaken_for_an_error(backend) -> None:
    text = (FIXTURES / f"{backend}-clean.txt").read_text(encoding="utf-8")
    assert backend_for(backend).classify(text) == "ok"


def test_a_singular_comment_is_not_an_error() -> None:
    """`// ** redefining f **` is routine and must not poison a session."""
    assert backend_for("singular").classify("// ** redefining f **") == "ok"
