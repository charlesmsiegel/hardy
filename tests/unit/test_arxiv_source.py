"""Fetching a paper's source bundle, and storing it as immutably as its record.

The archive itself is `archives.py`'s business and is tested there. What is
tested here is everything around it: that a bundle is only admitted for a
paper Hardy already holds, that it lands whole or not at all, that what is
served afterwards is checked against the digest it was admitted under, and
that fetching one costs a request only when it must.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from hardy import archives, arxiv

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{identifier}</id>
    <published>2002-11-11T18:00:00Z</published>
    <updated>2002-11-11T18:00:00Z</updated>
    <title>The entropy formula for the Ricci flow</title>
    <summary>A monotonic expression for the Ricci flow.</summary>
    <author><name>Grigori Perelman</name></author>
    <arxiv:primary_category term="math.DG"/>
    <category term="math.DG"/>
  </entry>
</feed>
"""

PAPER = "math.DG/0211159v1"
MAIN = b"\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n"


def _feed(identifier: str = PAPER) -> bytes:
    return FEED.format(identifier=identifier).encode("utf-8")


def _bundle(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members or (("main.tex", MAIN),):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class Answers:
    """A transport that answers by URL and counts what it was asked."""

    def __init__(self, **by_prefix: bytes) -> None:
        self.by_prefix = by_prefix
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float, limit: int | None = None) -> bytes:
        self.urls.append(url)
        for prefix, body in self.by_prefix.items():
            if prefix in url:
                return body
        raise AssertionError(f"nothing scripted for {url}")


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(tmp_path: Path, transport: Answers) -> tuple[arxiv.ArxivClient, arxiv.PaperLibrary]:
    library = arxiv.PaperLibrary(tmp_path / "papers")
    clock = Clock()
    return (
        arxiv.ArxivClient(library, transport=transport, clock=clock.time, sleep=clock.sleep),
        library,
    )


@pytest.fixture
def held(tmp_path: Path) -> tuple[arxiv.ArxivClient, arxiv.PaperLibrary, Answers]:
    """A client whose library already holds the paper's record."""
    transport = Answers(**{"api/query": _feed(), "e-print": _bundle()})
    client, library = _client(tmp_path, transport)
    client.fetch(PAPER)
    transport.urls.clear()
    return client, library, transport


# --- Admission -------------------------------------------------------------------


def test_a_source_tree_is_admitted_beside_the_record(held) -> None:
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)

    manifest = library.admit_source(
        identifier, _bundle(), source_url="https://example.invalid/e-print", fetched_at="2026-01-01T00:00:00Z"
    )

    assert library.holds_source(identifier)
    assert [item.path for item in manifest.files] == ["main.tex"]
    assert manifest.files[0].sha256 == hashlib.sha256(MAIN).hexdigest()
    assert manifest.archive_sha256 == hashlib.sha256(_bundle()).hexdigest()
    assert manifest.arxiv_id == PAPER
    assert manifest.source_url == "https://example.invalid/e-print"
    stored = library.path_for(identifier) / "source" / "main.tex"
    assert stored.read_bytes() == MAIN


def test_a_source_for_a_paper_nobody_fetched_is_refused(tmp_path: Path) -> None:
    """The record is the paper's identity. A source tree with no record is a
    pile of files nothing can say the provenance of."""
    library = arxiv.PaperLibrary(tmp_path / "papers")

    with pytest.raises(arxiv.ArxivError, match="no record"):
        library.admit_source(
            arxiv.parse_id(PAPER), _bundle(), source_url="u", fetched_at="t"
        )


def test_an_unversioned_identifier_admits_nothing(held) -> None:
    _, library, _ = held
    with pytest.raises(arxiv.ArxivError, match="version"):
        library.admit_source(
            arxiv.parse_id("math.DG/0211159"), _bundle(), source_url="u", fetched_at="t"
        )


def test_a_hostile_archive_leaves_the_record_untouched(held) -> None:
    """Refused whole. Not a `source/` holding the members read before the bad
    one, which is what a naive extractor into the final location leaves."""
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        good = tarfile.TarInfo("main.tex")
        good.size = len(MAIN)
        tar.addfile(good, io.BytesIO(MAIN))
        escape = tarfile.TarInfo("../escape.tex")
        escape.size = 4
        tar.addfile(escape, io.BytesIO(b"evil"))

    with pytest.raises(archives.ArchiveError):
        library.admit_source(identifier, buffer.getvalue(), source_url="u", fetched_at="t")

    assert not library.holds_source(identifier)
    assert not (library.path_for(identifier) / "source").exists()
    assert not (library.path_for(identifier).parent / "escape.tex").exists()
    # And the record itself still reads, so a refused source costs the paper
    # nothing.
    assert library.read(identifier).arxiv_id == PAPER


def test_a_source_already_held_is_not_rewritten(held) -> None:
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    first = library.admit_source(identifier, _bundle(), source_url="first", fetched_at="t")

    again = library.admit_source(
        identifier, _bundle(("main.tex", b"different\n")), source_url="second", fetched_at="t"
    )

    assert again == first
    assert again.source_url == "first"
    assert library.read_source(identifier, "main.tex") == MAIN.decode()


# --- Reading it back --------------------------------------------------------------


def test_a_source_file_is_checked_against_its_digest_before_it_is_served(held) -> None:
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    library.admit_source(identifier, _bundle(), source_url="u", fetched_at="t")
    (library.path_for(identifier) / "source" / "main.tex").write_bytes(b"rewritten\n")

    with pytest.raises(arxiv.ArxivError, match="digest"):
        library.read_source(identifier, "main.tex")


def test_a_path_the_manifest_does_not_name_is_refused(held) -> None:
    """Including one that exists: what may be read is what was admitted."""
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    library.admit_source(identifier, _bundle(), source_url="u", fetched_at="t")
    (library.path_for(identifier) / "source" / "extra.tex").write_bytes(b"planted\n")

    with pytest.raises(arxiv.ArxivError, match="not in the source"):
        library.read_source(identifier, "extra.tex")
    with pytest.raises(arxiv.ArxivError, match="not in the source"):
        library.read_source(identifier, "../record.json")


def test_a_binary_file_is_not_served_as_text(held) -> None:
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    library.admit_source(
        identifier,
        _bundle(("main.tex", MAIN), ("plot.png", b"\x89PNG\x00\x00\x01")),
        source_url="u",
        fetched_at="t",
    )

    with pytest.raises(arxiv.ArxivError, match="not text"):
        library.read_source(identifier, "plot.png")


def test_a_manifest_that_does_not_match_its_archive_digest_is_refused(held) -> None:
    """The manifest says which archive these files came out of. Editing the
    files and recomputing their digests must not leave that claim standing."""
    _, library, _ = held
    identifier = arxiv.parse_id(PAPER)
    library.admit_source(identifier, _bundle(), source_url="u", fetched_at="t")
    path = library.path_for(identifier) / "source" / "source.json"
    path.write_text(path.read_text().replace(hashlib.sha256(_bundle()).hexdigest(), "0" * 64))

    with pytest.raises(arxiv.ArxivError, match="digest"):
        library.read_source(identifier, "main.tex")


# --- Fetching one ------------------------------------------------------------------


def test_fetching_a_source_asks_the_e_print_endpoint_once(held) -> None:
    client, library, transport = held
    identifier = arxiv.parse_id(PAPER)

    manifest, already = client.fetch_source(PAPER)

    assert not already
    assert len(transport.urls) == 1
    assert transport.urls[0].endswith("/e-print/math.DG/0211159v1")
    assert library.holds_source(identifier)
    assert [item.path for item in manifest.files] == ["main.tex"]


def test_a_source_already_held_costs_no_request(held) -> None:
    client, _, transport = held
    client.fetch_source(PAPER)
    transport.urls.clear()

    manifest, already = client.fetch_source(PAPER)

    assert already
    assert transport.urls == []
    assert [item.path for item in manifest.files] == ["main.tex"]


def test_a_source_fetch_takes_a_throttle_slot(held) -> None:
    """arXiv's interval covers every request, not only the API's."""
    client, library, _ = held
    before = library.last_request()

    client.fetch_source(PAPER)

    assert library.last_request() > before


def test_a_source_cannot_be_fetched_for_a_paper_with_no_record(tmp_path: Path) -> None:
    transport = Answers(**{"e-print": _bundle()})
    client, _ = _client(tmp_path, transport)

    with pytest.raises(arxiv.ArxivError, match="fetch_paper"):
        client.fetch_source(PAPER)

    assert transport.urls == []


def test_an_unversioned_source_fetch_is_refused(held) -> None:
    client, _, transport = held

    with pytest.raises(arxiv.ArxivError, match="version"):
        client.fetch_source("math.DG/0211159")

    assert transport.urls == []


def test_a_single_file_submission_becomes_one_readable_source(held) -> None:
    """arXiv serves a one-file paper as a bare gzip, with no tar around it."""
    client, library, transport = held
    transport.by_prefix["e-print"] = gzip.compress(MAIN)

    manifest, _ = client.fetch_source(PAPER)

    assert [item.path for item in manifest.files] == ["main.tex"]
    assert library.read_source(arxiv.parse_id(PAPER), "main.tex") == MAIN.decode()


def test_an_archive_the_service_refused_is_reported_rather_than_stored(held) -> None:
    client, library, transport = held
    transport.by_prefix["e-print"] = b"<html>we are down</html>"

    with pytest.raises(archives.ArchiveError):
        client.fetch_source(PAPER)

    assert not library.holds_source(arxiv.parse_id(PAPER))
