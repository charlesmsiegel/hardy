"""Politeness and immutability, with a scripted arXiv in place of the real one.

No test here reaches the network: the transport, the clock and the sleep are
injected, so what is exercised is the throttle, the cache, the parsing, and
the rules that keep a stored record from ever moving. The live service is
`tests/integration`'s business, not this file's.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from hardy import arxiv
from hardy.layout import LayoutError
from hardy.storage import FileLock

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


def test_a_symlinked_library_directory_is_refused(tmp_path: Path):
    """A clone is a hostile artifact, and `.hardy/papers` is not exempt.

    `Layout.ensure` proves `.hardy` and stops, because it cannot know what a
    tool will put inside it -- so a repository shipping `.hardy/papers` as a
    link had every downloaded byte, every cached query and the throttle clock
    written through it, outside the project.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    hardy = tmp_path / ".hardy"
    hardy.mkdir()
    (hardy / "papers").symlink_to(outside, target_is_directory=True)
    library = arxiv.PaperLibrary(hardy / "papers")
    with pytest.raises(LayoutError):
        library.note_request(1.0)
    with pytest.raises(LayoutError):
        library.cache_query("abc", b"{}", now=1.0)
    assert list(outside.iterdir()) == []


def test_a_symlinked_records_directory_is_refused(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "papers"
    root.mkdir(parents=True)
    (root / "records").symlink_to(outside, target_is_directory=True)
    library = arxiv.PaperLibrary(root)
    client = arxiv.ArxivClient(
        library, transport=Recorder(_feed()), clock=lambda: 0.0, sleep=lambda seconds: None
    )
    with pytest.raises(LayoutError):
        client.fetch("math.DG/0211159v1")
    assert list(outside.iterdir()) == []


def test_a_response_that_is_not_an_answer_is_not_cached(tmp_path: Path):
    """A maintenance page cached for a day fails every retry after recovery."""
    transport = Recorder(b"<html>down for maintenance</html>", _feed())
    client, _, _ = _client(tmp_path, transport)
    with pytest.raises(arxiv.ArxivError):
        client.search("ricci flow")
    # The same query again reaches arXiv rather than the cache, and works.
    assert client.search("ricci flow")
    assert len(transport.urls) == 2


def test_a_paper_answered_under_another_identifier_is_not_cached(tmp_path: Path):
    transport = Recorder(_feed("2401.99999v1"), _feed("2401.00001v1"))
    client, _, _ = _client(tmp_path, transport)
    with pytest.raises(arxiv.ArxivError, match="refusing to store"):
        client.fetch("2401.00001v1")
    record, _ = client.fetch("2401.00001v1")
    assert record.arxiv_id == "2401.00001v1"
    assert len(transport.urls) == 2


def test_an_edited_record_is_refused_even_when_its_content_file_matches(tmp_path: Path):
    """`read_paper` serves the record's fields, not the file on disk.

    Checking only `content.txt` against the digest let an edit to the title or
    the abstract in `record.json` change what a reader is served while the
    verified file sat untouched beside it.
    """
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    path = library.path_for(record.identifier) / "record.json"
    path.write_text(
        record.model_copy(update={"title": "A completely different paper"}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(arxiv.ArxivError, match="does not match its recorded digest"):
        library.read(record.identifier)


def test_a_response_that_fails_mid_body_is_an_arxiv_error(monkeypatch):
    """An `OSError` out of `read` escaped every caller and ended the turn.

    `urlopen` succeeding is not the transfer succeeding: a connection reset
    halfway through the body raises after the response object exists, outside
    the handler that covered the connect. The tool dispatcher catches
    `ArxivError` and argument errors, so that one came out as a traceback
    where a failed tool result belonged.
    """

    class Collapsing:
        def read(self, size: int) -> bytes:
            raise ConnectionResetError("connection reset by peer")

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(arxiv.urllib.request, "urlopen", lambda *a, **k: Collapsing())
    with pytest.raises(arxiv.ArxivError, match="failed after 0 bytes"):
        arxiv._http("https://export.arxiv.org/api/query?x=1", 5.0)


def test_a_connection_that_never_opens_is_an_arxiv_error(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(arxiv.urllib.request, "urlopen", refuse)
    with pytest.raises(arxiv.ArxivError, match="could not be reached"):
        arxiv._http("https://export.arxiv.org/api/query?x=1", 5.0)


def test_the_throttle_serialises_across_processes(tmp_path: Path):
    """A timestamp on disk is not a mutex; the lock around it is.

    Two idle processes both read the old value, both compute no wait, and
    both fire -- which is the promise of machine-wide spacing not being kept.
    """
    clock = Clock()
    client, library, _ = _client(tmp_path, Recorder(_feed()), clock)
    client.search("ricci flow")
    # Taken for the read-wait-reserve sequence and released afterwards, so the
    # next process is not left waiting on a lock nobody holds. The file stays
    # -- releasing is closing the descriptor, not unlinking -- so what proves
    # it is that the lock can be taken again.
    with FileLock(library.lock_path, timeout=0.2) as lock:
        assert lock.held


def test_a_stuck_lock_slows_the_throttle_rather_than_stopping_a_fetch(tmp_path: Path):
    """Politeness degrades; the paper still arrives.

    Refusing to fetch because another process is slow would trade a real
    failure for an imagined discourtesy.
    """
    clock = Clock()
    library = arxiv.PaperLibrary(tmp_path / "papers")
    library.note_request(clock.now - 10)
    library.lock_path.write_text("999999", encoding="utf-8")
    client = arxiv.ArxivClient(
        library,
        transport=Recorder(_feed()),
        clock=clock.time,
        sleep=clock.sleep,
        interval=0.01,
        lock_timeout=0.05,
    )
    assert client.search("ricci flow")


def test_a_record_filed_under_another_papers_identifier_is_refused(tmp_path: Path):
    """The digests say a record is consistent, not that it is *this* record.

    A directory holding another paper's `record.json` and `content.txt` --
    an interrupted move, a hand-copied cache -- passes both digest checks,
    and then `read_paper(A)` serves B and `cite_paper(A)` records B under A.
    """
    client, library, _ = _client(tmp_path, Recorder(_feed(), _feed("2401.00001v1")))
    first, _ = client.fetch("math.DG/0211159v1")
    other, _ = client.fetch("2401.00001v1")
    stolen = library.path_for(first.identifier)
    # The whole record, response included, so this reaches the identity check
    # rather than tripping the response digest on the way.
    for name in ("record.json", "content.txt", "response.xml"):
        (stolen / name).write_bytes((library.path_for(other.identifier) / name).read_bytes())
    with pytest.raises(arxiv.ArxivError, match="under another's identifier"):
        library.read(first.identifier)


def test_an_unversioned_request_still_has_to_be_answered_about_that_paper(tmp_path: Path):
    """A version may change under a bare id. The paper may not."""
    client, library, _ = _client(tmp_path, Recorder(_feed("2401.99999v1")))
    with pytest.raises(arxiv.ArxivError, match="refusing to store"):
        client.fetch("2401.00001")
    assert library.stored() == ()


def test_an_unversioned_request_accepts_the_version_it_is_told(tmp_path: Path):
    client, library, _ = _client(tmp_path, Recorder(_feed("2401.00001v3")))
    record, _ = client.fetch("2401.00001")
    assert record.arxiv_id == "2401.00001v3"
    assert library.stored() == ("2401.00001v3",)


def test_a_doctype_behind_padding_is_still_refused(tmp_path: Path):
    """A prefix search is defeated by legal whitespace before the DOCTYPE."""
    padded = b'<?xml version="1.0"?>' + b" " * 8192 + b'<!DOCTYPE feed [<!ENTITY a "aa">]><feed/>'
    client, _, _ = _client(tmp_path, Recorder(padded))
    with pytest.raises(arxiv.ArxivError, match="DOCTYPE"):
        client.search("anything")


def test_the_throttle_lock_is_not_opened_through_a_symlink(tmp_path: Path):
    """`FileLock` creates its parent and deletes what it finds stale.

    Pointed through `.hardy/papers -> somewhere`, that is Hardy removing a
    stranger's file before any guarded call could refuse the link.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "state.lock"
    victim.write_text("someone else's file", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(victim, (old, old))
    hardy = tmp_path / ".hardy"
    hardy.mkdir()
    (hardy / "papers").symlink_to(outside, target_is_directory=True)
    library = arxiv.PaperLibrary(hardy / "papers")
    client = arxiv.ArxivClient(
        library, transport=Recorder(_feed()), clock=lambda: 0.0, sleep=lambda seconds: None
    )
    with pytest.raises(LayoutError):
        client.search("ricci flow")
    assert victim.read_text(encoding="utf-8") == "someone else's file"


def test_a_cache_entry_dated_in_the_future_is_stale(tmp_path: Path):
    """A clock that moved backwards must not freeze the cache.

    The throttle treats the same jump as "no idea"; a cache that read it as
    "very fresh indeed" went on serving a day-old answer for as long as the
    jump lasted, so an unversioned fetch kept resolving to a superseded
    version.
    """
    clock = Clock()
    transport = Recorder(_feed(), _feed("math.DG/0211159v2"))
    client, library, _ = _client(tmp_path, transport, clock)
    client.search("ricci flow")
    clock.now -= 10 * arxiv.QUERY_TTL_SECONDS
    client.search("ricci flow")
    assert len(transport.urls) == 2


def test_an_edited_response_is_refused(tmp_path: Path):
    """The record says its metadata came from these exact bytes.

    Checking only `record.json` and `content.txt` left that claim standing
    over a file that had been edited or deleted underneath it.
    """
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    response = library.path_for(record.identifier) / "response.xml"
    response.write_bytes(response.read_bytes() + b"<!-- edited -->")
    with pytest.raises(arxiv.ArxivError, match="where its metadata came from"):
        library.read(record.identifier)


def test_a_deleted_response_is_refused(tmp_path: Path):
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    (library.path_for(record.identifier) / "response.xml").unlink()
    with pytest.raises(arxiv.ArxivError, match="could not be read"):
        library.read(record.identifier)


def test_a_record_with_no_response_digest_is_refused(tmp_path: Path):
    """An empty digest was an opt-out from the provenance check."""
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    path = library.path_for(record.identifier) / "record.json"
    path.write_text(
        record.model_copy(update={"response_sha256": ""}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(arxiv.ArxivError, match="no response digest"):
        library.read(record.identifier)


def test_a_symlinked_response_is_refused(tmp_path: Path):
    """The one read beside two guarded ones that followed links."""
    client, library, _ = _client(tmp_path, Recorder(_feed()))
    record, _ = client.fetch("math.DG/0211159v1")
    stored = library.path_for(record.identifier) / "response.xml"
    outside = tmp_path / "elsewhere.xml"
    outside.write_bytes(stored.read_bytes())
    stored.unlink()
    stored.symlink_to(outside)
    with pytest.raises(arxiv.ArxivError, match="could not be read"):
        library.read(record.identifier)


def test_a_record_admitted_from_the_cache_says_when_the_bytes_arrived(tmp_path: Path):
    """Not when they were read back.

    A fetch whose admission failed -- a full disk -- and succeeded on a retry
    an hour later would otherwise record the retry as the moment its source
    was obtained.
    """
    clock = Clock()
    client, library, _ = _client(tmp_path, Recorder(_feed()), clock)
    obtained = clock.now
    first, _ = client.fetch("math.DG/0211159v1")
    # The admission is undone and the query cache left in place: the shape of
    # a fetch that reached arXiv and then failed to land, retried later.
    shutil.rmtree(library.path_for(first.identifier))
    clock.now += 3_600
    again, _ = client.fetch("math.DG/0211159v1")
    assert again.fetched_at == arxiv._stamp(obtained)
    assert again.fetched_at != arxiv._stamp(clock.now)


def test_a_stored_abstract_is_wrapped_so_every_line_can_be_read(tmp_path: Path):
    client, _, _ = _client(tmp_path, Recorder(_feed(abstract="word " * 2_000)))
    record, _ = client.fetch("math.DG/0211159v1")
    assert max(len(line) for line in record.content().splitlines()) <= arxiv.ABSTRACT_COLUMNS
