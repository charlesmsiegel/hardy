"""The four verbs, and the one thing they make impossible.

The property worth testing is negative: there is no sequence of tool calls
that puts a reference Hardy never fetched into the bibliography. `cite_paper`
takes an identifier and nothing else, so a fabricated citation has nowhere to
enter from.
"""

from __future__ import annotations

import json
from pathlib import Path

from hardy import arxiv
from hardy.bibliography import Bibliography
from hardy.paper_tools import PAPER_TOOL_NAMES, PaperToolRuntime, build_runtime

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
  </entry>
</feed>
"""


def _feed(
    identifier: str = "math.DG/0211159v1",
    title: str = "The entropy formula for the Ricci flow",
    abstract: str = "We present a monotonic expression for the Ricci flow.",
) -> bytes:
    return FEED.format(identifier=identifier, title=title, abstract=abstract).encode("utf-8")


def _runtime(tmp_path: Path, *bodies: bytes, observation_bytes: int = 32 * 1024):
    """A runtime whose arXiv is a script and whose clock never sleeps."""
    library = arxiv.PaperLibrary(tmp_path / "papers")
    answers = list(bodies) or [_feed()]

    def transport(url: str, timeout: float) -> bytes:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    client = arxiv.ArxivClient(
        library, transport=transport, clock=lambda: 1_000_000.0, sleep=lambda seconds: None
    )
    return PaperToolRuntime(
        library,
        Bibliography(tmp_path / "problem"),
        client=client,
        observation_bytes=observation_bytes,
    )


def _json(result) -> dict:
    return json.loads(result.output)


def test_the_four_verbs_are_what_is_offered():
    assert PAPER_TOOL_NAMES == ("search_papers", "fetch_paper", "read_paper", "cite_paper")


def test_a_search_returns_leads_and_records_nothing(tmp_path: Path):
    runtime = _runtime(tmp_path)
    result = runtime.call("search_papers", {"query": "ricci flow"})
    assert result.ok
    payload = _json(result)
    assert payload["results"][0]["paper_id"] == "math.DG/0211159v1"
    assert not payload["results"][0]["held"]
    assert runtime.library.stored() == ()


def test_a_fetch_pins_a_version(tmp_path: Path):
    runtime = _runtime(tmp_path)
    payload = _json(runtime.call("fetch_paper", {"paper_id": "math.DG/0211159"}))
    assert payload["paper_id"] == "math.DG/0211159v1"
    assert not payload["already_held"]
    assert payload["content_sha256"]
    assert runtime.library.stored() == ("math.DG/0211159v1",)


def test_a_second_fetch_says_it_was_already_held(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    assert _json(runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"}))["already_held"]


def test_reading_an_unfetched_paper_says_to_fetch_it(tmp_path: Path):
    result = _runtime(tmp_path).call("read_paper", {"paper_id": "2401.00001v1"})
    assert not result.ok
    assert "fetch_paper" in result.output


def test_reading_serves_what_was_stored(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok
    assert "monotonic expression" in result.output
    assert "arXiv:math.DG/0211159v1" in result.output


def test_a_long_record_comes_back_bounded_and_says_how_to_read_on(tmp_path: Path):
    runtime = _runtime(
        tmp_path, _feed(abstract="\n".join(f"line {n}" for n in range(500))), observation_bytes=256
    )
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok
    assert "start_line" in result.output
    assert len(result.output.encode("utf-8")) < 2_000
    later = runtime.call(
        "read_paper", {"paper_id": "math.DG/0211159v1", "start_line": 20}
    )
    assert later.ok
    assert later.output != result.output


def test_citing_an_unfetched_paper_is_refused(tmp_path: Path):
    """The whole mechanism: a citation is only possible for what Hardy holds."""
    runtime = _runtime(tmp_path)
    result = runtime.call("cite_paper", {"paper_id": "2401.99999v1"})
    assert not result.ok
    assert "fetch_paper" in result.output
    assert runtime.bibliography.entries() == ()


def test_citing_an_unversioned_identifier_is_refused(tmp_path: Path):
    """An unversioned citation cites whatever has been uploaded since."""
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("cite_paper", {"paper_id": "math.DG/0211159"})
    assert not result.ok
    assert "no version" in result.output


def test_citing_returns_a_key_and_writes_the_bibliography(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    payload = _json(runtime.call("cite_paper", {"paper_id": "math.DG/0211159v1"}))
    assert payload["cite_key"] == "perelman2002entropy"
    assert payload["added"]
    assert "\\input" in payload["note"]
    rendered = (tmp_path / "problem" / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "\\bibitem{perelman2002entropy}" in rendered


def test_citing_the_same_paper_twice_adds_one_entry(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    first = _json(runtime.call("cite_paper", {"paper_id": "math.DG/0211159v1"}))
    second = _json(runtime.call("cite_paper", {"paper_id": "arXiv:math.DG/0211159v1"}))
    assert second["cite_key"] == first["cite_key"]
    assert not second["added"]
    assert second["entries"] == 1


def test_an_empty_search_says_it_is_about_the_query(tmp_path: Path):
    """"Nothing matched" must not read as "the literature does not have it"."""
    empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    result = _runtime(tmp_path, empty).call("search_papers", {"query": "nonsense"})
    assert result.ok
    assert _json(result)["results"] == []
    assert "about the query" in _json(result)["note"]


def test_a_transport_failure_reaches_the_model_as_a_sentence(tmp_path: Path):
    library = arxiv.PaperLibrary(tmp_path / "papers")

    def refuse(url: str, timeout: float) -> bytes:
        raise arxiv.ArxivError("arXiv could not be reached: network unreachable")

    runtime = PaperToolRuntime(
        library,
        Bibliography(tmp_path / "problem"),
        client=arxiv.ArxivClient(
            library, transport=refuse, clock=lambda: 0.0, sleep=lambda seconds: None
        ),
    )
    result = runtime.call("search_papers", {"query": "ricci flow"})
    assert not result.ok
    assert "could not be reached" in result.output


def test_an_unreadable_identifier_is_an_answer_rather_than_a_crash(tmp_path: Path):
    result = _runtime(tmp_path).call("fetch_paper", {"paper_id": "the Ricci flow one"})
    assert not result.ok
    assert "not an arXiv identifier" in result.output


def test_the_runtime_finds_the_library_under_the_root(tmp_path: Path):
    runtime = build_runtime(tmp_path / "sylow", tmp_path)
    assert runtime.library.root == tmp_path / ".hardy" / "papers"
    assert runtime.bibliography.path == tmp_path / "sylow" / "bibliography.json"
