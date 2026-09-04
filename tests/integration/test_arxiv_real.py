"""The one part of paper fetching that depends on somebody else's contract.

The hermetic suite pins the parser against a recorded Atom feed, which cannot
notice the day arXiv's feed changes. This asks the real service -- and asks it
about a paper whose identifier, title and first author are not going to move.

Off by default, because the hermetic suite must not depend on a network or on
a service being up: set HARDY_ARXIV_LIVE=1 to run it. It makes at most two
requests, three seconds apart, which is the interval arXiv asks for.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hardy.arxiv import ArxivClient, ArxivError, PaperLibrary

#: Perelman's first Ricci flow preprint. Old-style identifier, one version,
#: never revised -- so both the spelling and the immutability claim are
#: exercised against something that cannot drift.
PAPER = "math.DG/0211159"


@pytest.mark.live
def test_the_real_arxiv_still_answers_the_shape_the_parser_reads(tmp_path: Path) -> None:
    if not os.environ.get("HARDY_ARXIV_LIVE"):
        pytest.skip("set HARDY_ARXIV_LIVE=1 to query the real arXiv API")

    library = PaperLibrary(tmp_path / "papers")
    client = ArxivClient(library)
    try:
        record, held = client.fetch(PAPER)
    except ArxivError as error:
        # A service that is down is not a defect. A response Hardy cannot read
        # is exactly the drift this test exists to catch, so it is not caught
        # here -- and `ArxivError` covers both, so the message decides.
        if "could not be reached" in str(error) or "HTTP 5" in str(error):
            pytest.skip(f"arXiv did not answer: {error}")
        raise

    assert not held
    # Resolved to a version, whatever was asked for.
    assert record.arxiv_id.startswith(f"{PAPER}v")
    assert record.identifier.versioned
    assert "entropy formula" in record.title.lower()
    assert any("Perelman" in name for name in record.authors)
    assert record.abstract
    assert record.content_sha256

    # Held now, so a second fetch costs no request at all -- which is the
    # politeness claim, and is checkable without waiting for the interval.
    again, held_again = client.fetch(record.arxiv_id)
    assert held_again
    assert again.content_sha256 == record.content_sha256


@pytest.mark.live
def test_the_real_arxiv_still_answers_a_search(tmp_path: Path) -> None:
    if not os.environ.get("HARDY_ARXIV_LIVE"):
        pytest.skip("set HARDY_ARXIV_LIVE=1 to query the real arXiv API")

    client = ArxivClient(PaperLibrary(tmp_path / "papers"))
    try:
        found = client.search("all:ricci flow", 3)
    except ArxivError as error:
        if "could not be reached" in str(error) or "HTTP 5" in str(error):
            pytest.skip(f"arXiv did not answer: {error}")
        raise

    assert found, "arXiv returned no entries for a query it certainly matches"
    assert all(record.identifier.versioned for record in found)
    assert all(record.title for record in found)
