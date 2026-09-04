"""Politeness and immutability, with a scripted arXiv in place of the real one.

No test here reaches the network: the transport, the clock and the sleep are
injected, so what is exercised is the throttle, the cache, the parsing, and
the rules that keep a stored record from ever moving. The live service is
`tests/integration`'s business, not this file's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy import arxiv

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{identifier}</id>
    <published>2002-11-11T18:00:00Z</published>
    <updated>2002-11-11T18:00:00Z</updated>
    <title>{title}</title>
    <summary>{abstract}</summary>
    <author><name>Grigori Perelman</name></author>
    <arxiv:primary_category term="math.DG"/>
    <category term="math.DG"/>
    <category term="math.GT"/>
    {extra}
  </entry>
</feed>
"""


def _feed(
    identifier: str = "math.DG/0211159v1",
    title: str = "The entropy formula for the Ricci flow",
    abstract: str = "We present a monotonic expression for the Ricci flow.",
    extra: str = "",
) -> bytes:
    return FEED.format(
        identifier=identifier, title=title, abstract=abstract, extra=extra
    ).encode("utf-8")


class Recorder:
    """A transport that answers from a script and counts what it was asked."""

    def __init__(self, *bodies: bytes) -> None:
        self.bodies = list(bodies)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.urls.append(url)
        return self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]


class Clock:
    """A wall clock a test can move, and a sleep that moves it."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(tmp_path: Path, transport: Recorder, clock: Clock | None = None):
    clock = clock or Clock()
    library = arxiv.PaperLibrary(tmp_path / "papers")
    return (
        arxiv.ArxivClient(
            library, transport=transport, clock=clock.time, sleep=clock.sleep
        ),
        library,
        clock,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2401.12345", "2401.12345"),
        ("2401.12345v2", "2401.12345v2"),
        ("arXiv:2401.12345v2", "2401.12345v2"),
        ("https://arxiv.org/abs/2401.12345v2", "2401.12345v2"),
        ("https://arxiv.org/pdf/2401.12345v2.pdf", "2401.12345v2"),
        ("math.GT/0211159v1", "math.GT/0211159v1"),
        ("  2401.12345  ", "2401.12345"),
    ],
)
def test_every_spelling_of_an_identifier_reaches_the_same_paper(raw: str, expected: str):
    assert str(arxiv.parse_id(raw)) == expected


def test_something_that_is_not_an_identifier_is_refused():
    with pytest.raises(arxiv.ArxivError, match="not an arXiv identifier"):
        arxiv.parse_id("the one about Ricci flow")


def test_a_record_is_stored_under_the_version_arxiv_reported(tmp_path: Path):
    """An unversioned request must not become an unversioned record."""
    transport = Recorder(_feed())
    client, library, _ = _client(tmp_path, transport)
    record, held = client.fetch("math.DG/0211159")
    assert record.arxiv_id == "math.DG/0211159v1"
    assert not held
    assert library.stored() == ("math.DG/0211159v1",)
    assert "id_list=math.DG" in transport.urls[0].replace("%2F", "/")


def test_a_second_fetch_of_a_held_version_makes_no_request(tmp_path: Path):
    transport = Recorder(_feed())
    client, _, clock = _client(tmp_path, transport)
    client.fetch("math.DG/0211159v1")
    clock.now += arxiv.QUERY_TTL_SECONDS * 10
    record, held = client.fetch("math.DG/0211159v1")
    assert held
    assert record.title.startswith("The entropy formula")
    assert len(transport.urls) == 1


def test_a_new_version_is_a_new_record_beside_the_old_one(tmp_path: Path):
    """The promise the whole file is for: a version is never an overwrite."""
    client, library, clock = _client(tmp_path, Recorder(_feed()))
    client.fetch("math.DG/0211159v1")
    second, _, _ = _client(tmp_path, Recorder(_feed("math.DG/0211159v2")), clock)
    second.fetch("math.DG/0211159v2")
    assert library.stored() == ("math.DG/0211159v1", "math.DG/0211159v2")


def test_a_stored_record_is_never_rewritten(tmp_path: Path):
    """Even when arXiv answers with different bytes under the same version.

    A citation that resolved to one abstract yesterday has to resolve to the
    same abstract today; that is the whole point of pinning a version.
    """
    client, library, clock = _client(tmp_path, Recorder(_feed()))
    client.fetch("math.DG/0211159v1")
    rewritten, _, _ = _client(
        tmp_path, Recorder(_feed(abstract="Something else entirely.")), clock
    )
    clock.now += arxiv.QUERY_TTL_SECONDS * 10
    record, held = rewritten.fetch("math.DG/0211159v1")
    assert held
    assert "monotonic expression" in record.abstract


def test_a_record_edited_on_disk_is_refused_rather_than_served(tmp_path: Path):
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    content = library.path_for(record.identifier) / "content.txt"
    content.write_text(content.read_text(encoding="utf-8") + "and P=NP.\n", encoding="utf-8")
    with pytest.raises(arxiv.ArxivError, match="does not match its recorded digest"):
        library.read(record.identifier)


def test_the_digest_covers_what_read_paper_serves(tmp_path: Path):
    client, _, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    assert record.content_sha256 == arxiv.digest(record.content())
    assert record.content_bytes == len(record.content().encode("utf-8"))


def test_a_request_waits_out_the_interval(tmp_path: Path):
    clock = Clock()
    transport = Recorder(_feed("2401.00001v1"), _feed("2401.00002v1"))
    client, _, _ = _client(tmp_path, transport, clock)
    client.fetch("2401.00001v1")
    client.fetch("2401.00002v1")
    assert clock.slept == [arxiv.MIN_INTERVAL_SECONDS]


def test_the_interval_is_shared_between_processes(tmp_path: Path):
    """The clock is a file, so a second Hardy throttles against the first."""
    clock = Clock()
    first, _, _ = _client(tmp_path, Recorder(_feed("2401.00001v1")), clock)
    first.fetch("2401.00001v1")
    second, _, _ = _client(tmp_path, Recorder(_feed("2401.00002v1")), clock)
    second.fetch("2401.00002v1")
    assert clock.slept == [arxiv.MIN_INTERVAL_SECONDS]


def test_a_clock_that_jumped_backwards_waits_the_whole_interval(tmp_path: Path):
    clock = Clock()
    client, library, _ = _client(tmp_path, Recorder(_feed()), clock)
    library.note_request(clock.now + 10_000)
    client.search("ricci flow")
    assert clock.slept == [arxiv.MIN_INTERVAL_SECONDS]


def test_a_repeated_query_is_answered_from_the_cache(tmp_path: Path):
    transport = Recorder(_feed())
    client, _, _ = _client(tmp_path, transport)
    client.search("ricci flow")
    client.search("ricci flow")
    assert len(transport.urls) == 1


def test_a_stale_cached_query_is_asked_again(tmp_path: Path):
    clock = Clock()
    transport = Recorder(_feed())
    client, _, _ = _client(tmp_path, transport, clock)
    client.search("ricci flow")
    clock.now += arxiv.QUERY_TTL_SECONDS + 1
    client.search("ricci flow")
    assert len(transport.urls) == 2


def test_a_search_result_is_not_admitted_to_the_library(tmp_path: Path):
    """A lead is not a citation. Only a deliberate fetch pins a version."""
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    assert client.search("ricci flow")
    assert library.stored() == ()


def test_a_doi_and_a_journal_reference_are_kept(tmp_path: Path):
    extra = (
        "<arxiv:doi>10.1090/S0002-9947-00-02676-3</arxiv:doi>"
        "<arxiv:journal_ref>Trans. AMS 353 (2001) 21-40</arxiv:journal_ref>"
    )
    client, _, _ = _client(tmp_path, Recorder(_feed(extra=extra)))
    record, _ = client.fetch("math.DG/0211159v1")
    assert record.doi == "10.1090/S0002-9947-00-02676-3"
    assert record.journal_ref == "Trans. AMS 353 (2001) 21-40"


def test_a_wrapped_title_comes_back_as_one_line(tmp_path: Path):
    client, _, _ = _client(
        tmp_path, Recorder(_feed(title="The entropy formula\n  for the Ricci flow"))
    )
    record, _ = client.fetch("math.DG/0211159v1")
    assert record.title == "The entropy formula for the Ricci flow"


def test_a_response_carrying_a_doctype_is_refused(tmp_path: Path):
    """`xml.etree` expands internal entities, so a DOCTYPE is a bomb's home."""
    bomb = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa">]><feed/>'
    client, _, _ = _client(tmp_path, Recorder(bomb))
    with pytest.raises(arxiv.ArxivError, match="DOCTYPE"):
        client.search("anything")


def test_an_error_entry_is_not_read_as_a_paper(tmp_path: Path):
    """arXiv answers a bad id with an entry whose id is an error URL."""
    error = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        "<id>http://arxiv.org/api/errors#incorrect_id_format</id>"
        "<title>Error</title><summary>incorrect id format</summary>"
        "</entry></feed>"
    )
    client, library, _ = _client(tmp_path, Recorder(error.encode("utf-8")))
    with pytest.raises(arxiv.ArxivError, match="no paper"):
        client.fetch("2401.00001v1")
    assert library.stored() == ()


def test_a_paper_answered_under_another_identifier_is_refused(tmp_path: Path):
    client, library, _ = _client(tmp_path, Recorder(_feed("2401.99999v1")))
    with pytest.raises(arxiv.ArxivError, match="refusing to store"):
        client.fetch("2401.00001v1")
    assert library.stored() == ()


def test_a_response_that_is_not_xml_is_refused(tmp_path: Path):
    client, _, _ = _client(tmp_path, Recorder(b"<html>down for maintenance</html>"))
    with pytest.raises(arxiv.ArxivError):
        client.search("anything")


def test_an_old_style_identifier_is_one_flat_directory(tmp_path: Path):
    """`math.DG/0211159v1` cannot be a path component as it stands."""
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    directory = library.path_for(record.identifier)
    assert directory.parent == library.records
    assert directory.name == "math.DG_0211159v1"


def test_the_untouched_response_is_kept_beside_the_record(tmp_path: Path):
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    stored = (library.path_for(record.identifier) / "response.xml").read_bytes()
    assert stored == _feed()
