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


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_near_miss_text_is_not_mistaken_for_an_error(backend) -> None:
    """The tell-tale character appears, but never in the banner's position.

    A pattern that matched on the bare substring (`?`, `error:`) rather than
    on anchoring and structure would misclassify this text as an error.
    """
    text = (FIXTURES / f"{backend}-near-miss.txt").read_text(encoding="utf-8")
    assert backend_for(backend).classify(text) == "ok"


def test_a_deeply_nested_singular_error_is_still_recognised() -> None:
    """Singular indents error banners by call-stack depth, not a fixed cap.

    A pattern that only tolerated a small, fixed indent would let an error
    raised inside a nested procedure through as `"ok"` -- silently accepting
    it into replayable state.
    """
    text = "      ? nested procedure call failed: undefined symbol"
    assert backend_for("singular").classify(text) == "error"


def test_a_macaulay2_error_without_the_depth_marker_is_still_recognised() -> None:
    """`:[N]:` was only ever seen from one M2 build (CI run 30167266358).

    A pattern that *required* it would read the older, no-depth-marker form
    -- what the pre-verification guess used, and what a different M2 build
    might still emit -- as `"ok"`: a false negative, accepted into
    replayable state, worse than a false positive because the session then
    rebuilds from a cell that never worked.
    """
    text = "stdio:1:1:(3): error: expected a ring"
    assert backend_for("macaulay2").classify(text) == "error"


def test_macaulay2_errors_on_stderr_alone_are_classified() -> None:
    """Confirmed of the real backend (CI run 30167266358): M2 writes its
    error text to stderr, with nothing error-shaped left on stdout at all.
    `classify` must catch this on the second argument, not just the first.
    """
    backend = backend_for("macaulay2")
    assert backend.classify("", "stdio:2:1:(3):[1]: error: division by zero") == "error"
    assert backend.classify("ordinary output", "") == "ok"
