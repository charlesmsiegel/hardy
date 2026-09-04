"""The four verbs, and the one thing they make impossible.

The property worth testing is negative: there is no sequence of tool calls
that puts a reference Hardy never fetched into the bibliography. `cite_paper`
takes an identifier and nothing else, so a fabricated citation has nowhere to
enter from.
"""

from __future__ import annotations

import json
import re
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
    assert payload["cite_key"].startswith("perelman2002entropy-")
    assert payload["added"]
    assert "\\input" in payload["note"]
    rendered = (tmp_path / "problem" / "tex" / "references.tex").read_text(encoding="utf-8")
    assert f"\\bibitem{{{payload['cite_key']}}}" in rendered


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


def test_a_search_answer_is_bounded_like_every_other_observation(tmp_path: Path):
    """`read_paper` was bounded and this was not.

    Twenty-five abstracts -- a feed may approach the response cap on its own
    -- went into the model's context and the transcript whole, from a tool
    whose answer is meant to be a list of leads.
    """
    runtime = _runtime(
        tmp_path, _feed(abstract="x" * 40_000), observation_bytes=4_096
    )
    result = runtime.call("search_papers", {"query": "ricci flow"})
    assert result.ok
    assert len(result.output.encode("utf-8")) <= 4_096
    payload = _json(result)
    # The list is never cut: a shortened list hides papers the search found,
    # and a model cannot tell that from a search that found fewer.
    assert len(payload["results"]) == 1
    assert payload["results"][0]["paper_id"] == "math.DG/0211159v1"


def test_a_search_abstract_is_clipped_before_the_whole_thing_is_dropped(tmp_path: Path):
    runtime = _runtime(tmp_path, _feed(abstract="y" * 5_000))
    payload = _json(runtime.call("search_papers", {"query": "ricci flow"}))
    abstract = payload["results"][0]["abstract"]
    assert len(abstract) < 5_000
    assert "read_paper" in abstract


def test_a_filesystem_failure_is_a_tool_result_rather_than_a_traceback(tmp_path: Path):
    """The session dispatcher catches argument errors, and nothing else.

    A full disk or a read-only library ended the turn with a traceback, no
    tool result and no trajectory event -- the one shape of failure Hardy's
    own record cannot describe afterwards.
    """
    runtime = _runtime(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    runtime.bibliography.cite = refuse
    result = runtime.call("cite_paper", {"paper_id": "math.DG/0211159v1"})
    assert not result.ok
    assert "could not be written" in result.output


def test_a_search_answer_fits_even_when_the_metadata_alone_is_huge(tmp_path: Path):
    """A collaboration author list is metadata, and metadata can be enormous.

    Dropping abstracts and returning without another size check left the
    bound a claim rather than a fact.
    """
    authors = "".join(f"<author><name>Author {n}</name></author>" for n in range(2_000))
    feed = FEED.format(
        identifier="math.DG/0211159v1", title="T" * 4_000, abstract="a" * 4_000
    ).replace("<author><name>Grigori Perelman</name></author>", authors)
    runtime = _runtime(tmp_path, feed.encode("utf-8"), observation_bytes=2_048)
    result = runtime.call("search_papers", {"query": "ricci flow"})
    assert result.ok
    assert len(result.output.encode("utf-8")) <= 2_048
    payload = _json(result)
    # Every paper survives, whatever else does not: a shortened list is
    # indistinguishable from a search that found fewer.
    assert [item["paper_id"] for item in payload["results"]] == ["math.DG/0211159v1"]


def test_the_throttle_is_shared_across_project_roots(tmp_path: Path):
    """arXiv sees one caller per machine, not one per project directory."""
    first = build_runtime(tmp_path / "one" / "problem", tmp_path / "one")
    second = build_runtime(tmp_path / "two" / "problem", tmp_path / "two")
    assert first.library.root != second.library.root
    assert first.library.state_path == second.library.state_path
    assert first.library.lock_path == second.library.lock_path


def test_a_fetch_answer_is_bounded_too(tmp_path: Path):
    """The one answer that put whatever arrived straight into the context."""
    authors = "".join(f"<author><name>Author {n}</name></author>" for n in range(3_000))
    feed = FEED.format(
        identifier="math.DG/0211159v1", title="T" * 5_000, abstract="a"
    ).replace("<author><name>Grigori Perelman</name></author>", authors)
    runtime = _runtime(tmp_path, feed.encode("utf-8"), observation_bytes=4_096)
    result = runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok
    assert len(result.output.encode("utf-8")) <= 4_096
    payload = _json(result)
    assert payload["paper_id"] == "math.DG/0211159v1"
    assert "more" in payload["authors"][-1]


def test_a_fetch_answer_fits_even_when_one_field_is_enormous(tmp_path: Path):
    """Clipping by count leaves a hole a single huge field walks through."""
    runtime = _runtime(
        tmp_path,
        FEED.format(
            identifier="math.DG/0211159v1", title="T" * 200_000, abstract="a"
        ).encode("utf-8"),
        observation_bytes=1_024,
    )
    result = runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok
    assert len(result.output.encode("utf-8")) <= 1_024
    assert _json(result)["paper_id"] == "math.DG/0211159v1"


def test_every_line_of_a_stored_record_can_be_reached(tmp_path: Path):
    """An abstract arriving as one enormous line used to strand its tail.

    `truncate` clips a line too long for the window and counts it consumed, so
    `next_line` steps past it -- and `read_paper` pages by line, so the rest
    of that line could never be asked for. The stored record is wrapped, so
    every line fits and paging reaches the end.
    """
    runtime = _runtime(
        tmp_path,
        FEED.format(
            identifier="math.DG/0211159v1", title="T", abstract="word " * 4_000
        ).encode("utf-8"),
        observation_bytes=2_048,
    )
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    seen = ""
    start = 1
    for _ in range(200):
        result = runtime.call(
            "read_paper", {"paper_id": "math.DG/0211159v1", "start_line": start}
        )
        assert result.ok, result.output
        seen += result.output
        found = re.search(r"start_line=(\d+)", result.output)
        if not found:
            break
        start = int(found.group(1))
    else:  # pragma: no cover - a loop that never ends is the bug
        raise AssertionError("paging never reached the end of the record")
    # The last word of the abstract is reachable, which is the whole claim.
    assert seen.count("word") > 3_900


def test_the_session_builds_its_paper_runtime_on_the_configured_budget(tmp_path: Path):
    """`cas` and `search` are handed in already built against the operator's
    `limits.model_observation_bytes`; this runtime is built by the session
    itself, and being built there is how it came to keep its own 32 KiB
    default while every other tool in the same workspace respected a smaller
    configured limit.
    """
    import sys

    from test_chat import FakeChatRuntime, factory

    from hardy.chat import MathematicsSession

    workspace = tmp_path / "problem"
    workspace.mkdir()
    runtime = FakeChatRuntime([])
    session = MathematicsSession(
        workspace,
        factory(type(runtime), runtime.script),
        (sys.executable, "-c", ""),
        (sys.executable, "-c", ""),
        lambda proposal: False,
        observation_bytes=4096,
    )
    assert session.papers.observation_bytes == 4096


def test_a_paged_read_stays_inside_the_configured_budget(tmp_path: Path):
    """The note is part of the answer, so it is part of the budget.

    Letting `truncate` spend the whole limit and then prepending the paper
    id, the summary and the continuation line put every truncated window over
    the configured ceiling -- by a little, on every long read.
    """
    runtime = _runtime(tmp_path, _feed(abstract="word " * 20_000), observation_bytes=2048)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 2048
    assert "start_line=" in result.output
    # And the continuation it offers is itself inside the budget.
    line = int(result.output.split("start_line=")[1].split(" ")[0].rstrip("."))
    more = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "start_line": line})
    assert more.ok, more.output
    assert len(more.output.encode("utf-8")) <= 2048


def test_a_query_longer_than_the_budget_does_not_come_back_whole(tmp_path: Path):
    """Shedding detail cannot help with the part of the answer that is not detail.

    Every level of detail echoes the query, so a query longer than the limit
    made all of them oversized -- and the last was returned anyway.
    """
    runtime = _runtime(tmp_path, observation_bytes=2048)
    result = runtime.call("search_papers", {"query": "ricci " * 5_000})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 2048


def test_an_empty_result_does_not_echo_an_unbounded_query(tmp_path: Path):
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    runtime = _runtime(tmp_path, empty, observation_bytes=2048)
    result = runtime.call("search_papers", {"query": "ricci " * 5_000})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 2048
    assert "matched nothing" in result.output
