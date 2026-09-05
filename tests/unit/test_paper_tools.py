"""The literature verbs, and the one thing they make impossible.

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


def test_the_five_verbs_are_what_is_offered():
    assert PAPER_TOOL_NAMES == (
        "search_papers",
        "fetch_paper",
        "fetch_source",
        "read_paper",
        "cite_paper",
    )


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
    from hardy.domain import RunLimits

    workspace = tmp_path / "problem"
    workspace.mkdir()
    runtime = FakeChatRuntime([])
    session = MathematicsSession(
        workspace,
        factory(type(runtime), runtime.script),
        (sys.executable, "-c", ""),
        (sys.executable, "-c", ""),
        lambda proposal: False,
        limits=RunLimits(model_observation_bytes=4096),
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


def test_a_result_set_that_cannot_be_represented_is_refused_not_shrunk(tmp_path: Path):
    """The one thing that must never happen here is quietly returning fewer.

    With a budget too small even for bare identifiers, the last level was
    returned anyway -- an oversized answer in the context the budget exists to
    protect. Refusing says so; shortening the list would read exactly like a
    search that found fewer papers.
    """
    for budget in (64, 120, 200):
        runtime = _runtime(tmp_path, observation_bytes=budget)
        result = runtime.call("search_papers", {"query": "ricci flow"})
        assert not result.ok, budget
        # The refusal is an observation too, so it is measured like the rest.
        assert len(result.output.encode("utf-8")) <= budget, budget
        # And whatever survives the shedding still says papers were found and
        # that this is not the list of them.
        assert "1 matched" in result.output, budget


def test_a_fetch_answer_fits_the_budget_even_when_the_identity_does_not(tmp_path: Path):
    """"Always fits" was a claim about the identity being short, not a measurement.

    A 64-character digest and a sentence do not fit in 256 bytes, and nothing
    puts a floor under the configured limit -- so the documented-as-bounded
    path was the one that overran it.
    """
    runtime = _runtime(tmp_path, observation_bytes=128)
    result = runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 128
    assert "math.DG/0211159v1" in result.output


def test_an_empty_search_answer_is_measured_like_every_other(tmp_path: Path):
    """Clipping the echo bounded the query and not the answer.

    With a small budget the clipped echo plus the fixed note still overran
    it, on the one branch that has no papers to shed.
    """
    empty = b'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    runtime = _runtime(tmp_path, empty, observation_bytes=128)
    result = runtime.call("search_papers", {"query": "ricci " * 500})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 128
    assert "matched nothing" in result.output


def test_a_citation_answer_is_measured_and_never_loses_its_key(tmp_path: Path):
    """The citation is already made, so there is nothing to refuse.

    What is shed is the advice and then the counts -- never the key, because
    a caller that does not get it cannot cite the paper it just recorded.
    """
    runtime = _runtime(tmp_path, observation_bytes=96)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("cite_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 96
    assert json.loads(result.output)["cite_key"]


def test_a_window_that_cannot_fit_is_refused_not_returned_oversized(tmp_path: Path):
    """Returning it anyway put it in the context the budget protects.

    And worse: the clipped first line counts as consumed, so the `start_line`
    offered next skips the part that was cut -- a page that cannot be turned.
    """
    runtime = _runtime(tmp_path, _feed(abstract="word " * 5_000), observation_bytes=96)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    refused = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1"})
    assert not refused.ok
    assert "does not fit" in refused.output
    # And the refusal is measured like the answer it declined to give. It was
    # not: the budget that made the window too big is the same budget the
    # explanation has to fit in, and this one quotes both the identifier --
    # an unbounded tool argument -- and the limit back.
    assert len(refused.output.encode("utf-8")) <= 96


def test_a_start_line_past_the_end_is_refused_within_the_budget(tmp_path: Path):
    """The other refusal `read_paper` writes, measured the same way."""
    runtime = _runtime(tmp_path, _feed(), observation_bytes=96)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "start_line": 9_999})
    assert not result.ok
    assert len(result.output.encode("utf-8")) <= 96


def test_a_refusal_is_measured_like_an_answer(tmp_path: Path):
    """A refusal quotes what it refused, and nothing bounds a tool argument.

    `parse_id` puts the whole identifier in its message deliberately, so a
    model can see what it typed -- but a 10 KB `paper_id` then came back as a
    10 KB observation under a 128-byte budget. Every success path measured
    and the failure paths not, which is the wrong way round.
    """
    runtime = _runtime(tmp_path, observation_bytes=128)
    for tool in ("fetch_paper", "read_paper", "cite_paper"):
        result = runtime.call(tool, {"paper_id": "not-an-id-" * 1_000})
        assert not result.ok, tool
        assert len(result.output.encode("utf-8")) <= 128, tool


def test_a_long_cite_key_sheds_the_json_around_it(tmp_path: Path):
    r"""A budget a key fits does not necessarily fit the JSON around it.

    `base_key` allows a sixty-character stem and `cite_key` appends eleven
    more, so the smallest JSON rung -- `{"cite_key": "..."}` -- is about
    eighty-seven bytes for a paper whose first author has a long surname, and
    it was returned unconditionally under budgets the tests already exercise.
    The key on its own is the answer; the field name is packaging.
    """
    surname = "Vandenberghe" * 6
    feed = _feed().decode("utf-8").replace("Grigori Perelman", surname)
    # Between the two: a 71-byte key fits, and the 87 bytes of the smallest
    # JSON carrying it does not.
    runtime = _runtime(tmp_path, feed.encode("utf-8"), observation_bytes=80)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    result = runtime.call("cite_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 80
    # The key survives whole -- it is the whole answer, and a truncated cite
    # key is a different key that no `\bibitem` defines.
    entry = runtime.bibliography.entries()[0]
    assert entry.key in result.output


def test_a_window_whose_first_line_was_clipped_is_refused(tmp_path: Path):
    r"""A page that only appears to fit is not better than one that refuses.

    `truncate` returns one line even when that line alone does not fit --
    right for a file that is one enormous line, wrong here, because the line
    counts as read and `next_line` then points PAST the suffix that was cut.
    The budget-shrinking loop walked straight into it: with a small enough
    budget the assembled payload fits, `read_paper` reports success, and the
    omitted middle is unreachable by any later call.
    """
    runtime = _runtime(tmp_path, _feed(abstract="word " * 400), observation_bytes=160)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    # Line 11 is a 94-byte wrapped abstract line, and the continuation note
    # for it runs to about 120 bytes -- so no shrunken budget holds both, and
    # what used to come back was that line with its tail cut off and a
    # `start_line` pointing past the cut.
    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "start_line": 11})
    assert not result.ok, result.output
    assert "does not fit" in result.output


def test_a_long_identity_sheds_down_to_the_paper_id(tmp_path: Path):
    """The fetch answer has a shorter TRUE answer; the cite key does not.

    The identity rung below the full one was returned unconditionally, and
    for an old-style identifier under a small budget it is still about 57
    bytes. `already_held` is a convenience the caller can recompute by asking
    again, so there is something left to shed here -- unlike a cite key,
    where shortening the answer would name a different paper.
    """
    runtime = _runtime(tmp_path, _feed(), observation_bytes=48)
    result = runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 48
    assert json.loads(result.output)["paper_id"] == "math.DG/0211159v1"


def test_an_empty_search_fits_the_smallest_budget(tmp_path: Path):
    """The sentence is not the answer; the empty list is.

    Every rung of the empty-search ladder carried the note in words, so the
    smallest was still 49 bytes and the last one was returned unmeasured. What
    a caller must not lose is that the search RAN and found nothing, which an
    empty list says on its own.
    """
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom" '
        b'xmlns:arxiv="http://arxiv.org/schemas/atom"></feed>'
    )
    runtime = _runtime(tmp_path, empty, observation_bytes=48)
    result = runtime.call("search_papers", {"query": "ricci flow"})
    assert result.ok, result.output
    assert len(result.output.encode("utf-8")) <= 48
    assert json.loads(result.output)["results"] == []


# --- The source bundle, as a tool surface ------------------------------------


def _tar_bundle(*members: tuple[str, bytes]) -> bytes:
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


MAIN_TEX = b"\\documentclass{article}\n\\begin{document}\n\\section{One}\nBody.\n\\end{document}\n"


def _sourced(tmp_path: Path, *members: tuple[str, bytes]):
    """A runtime whose arXiv answers both the API and the e-print endpoint."""
    library = arxiv.PaperLibrary(tmp_path / "papers")
    bundle = _tar_bundle(*(members or (("main.tex", MAIN_TEX),)))

    def transport(url: str, timeout: float, limit: int | None = None) -> bytes:
        return bundle if "e-print" in url else _feed()

    client = arxiv.ArxivClient(
        library, transport=transport, clock=lambda: 1_000_000.0, sleep=lambda seconds: None
    )
    return PaperToolRuntime(
        library, Bibliography(tmp_path / "problem"), client=client, observation_bytes=32 * 1024
    )


def test_the_source_verbs_are_offered_beside_the_others():
    assert "fetch_source" in PAPER_TOOL_NAMES


def test_a_source_cannot_be_fetched_before_the_paper_is(tmp_path: Path):
    runtime = _sourced(tmp_path)

    result = runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})

    assert not result.ok
    assert "fetch_paper" in result.output


def test_fetching_a_source_lists_what_it_holds(tmp_path: Path):
    runtime = _sourced(tmp_path, ("main.tex", MAIN_TEX), ("figures/plot.png", b"\x89PNG\x00\x01"))
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})

    result = runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})

    assert result.ok, result.output
    payload = _json(result)
    assert payload["paper_id"] == "math.DG/0211159v1"
    assert "main.tex" in payload["files"]
    assert payload["archive_sha256"]
    # A binary file is held but is not offered as something to read.
    assert "figures/plot.png" not in payload["files"]


def test_a_source_file_is_read_through_read_paper(tmp_path: Path):
    runtime = _sourced(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})

    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "file": "main.tex"})

    assert result.ok, result.output
    assert "\\section{One}" in result.output


def test_reading_a_file_of_a_source_nobody_fetched_says_how_to_get_it(tmp_path: Path):
    runtime = _sourced(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})

    result = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "file": "main.tex"})

    assert not result.ok
    assert "fetch_source" in result.output


def test_a_file_the_source_does_not_hold_is_refused(tmp_path: Path):
    runtime = _sourced(tmp_path)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})

    result = runtime.call(
        "read_paper", {"paper_id": "math.DG/0211159v1", "file": "../record.json"}
    )

    assert not result.ok
    assert "not in the source" in result.output


def test_a_long_source_file_is_paged_like_any_other_read(tmp_path: Path):
    body = b"\\documentclass{article}\n" + b"\n".join(
        f"Line {number} of the paper.".encode() for number in range(400)
    )
    runtime = _sourced(tmp_path, ("main.tex", body))
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})
    runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})
    runtime.observation_bytes = 400

    first = runtime.call("read_paper", {"paper_id": "math.DG/0211159v1", "file": "main.tex"})

    assert first.ok, first.output
    assert len(first.output.encode("utf-8")) <= 400
    assert "start_line=" in first.output


def test_a_refused_archive_is_reported_as_a_tool_answer(tmp_path: Path):
    """An `ArchiveError` is a refusal the model must be able to read, not a
    traceback that ends the turn."""
    library = arxiv.PaperLibrary(tmp_path / "papers")

    def transport(url: str, timeout: float, limit: int | None = None) -> bytes:
        return b"<html>down for maintenance</html>" if "e-print" in url else _feed()

    client = arxiv.ArxivClient(
        library, transport=transport, clock=lambda: 1_000_000.0, sleep=lambda seconds: None
    )
    runtime = PaperToolRuntime(library, Bibliography(tmp_path / "problem"), client=client)
    runtime.call("fetch_paper", {"paper_id": "math.DG/0211159v1"})

    result = runtime.call("fetch_source", {"paper_id": "math.DG/0211159v1"})

    assert not result.ok
    assert "gzip, tar, or PDF" in result.output
