"""The corpus viewer: what the page is handed, and the two routes that exist."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from corpus_helpers import rebind_changelog, write_corpus

from hardy.evals.problems import Entry
from hardy.evals.corpus import load_corpus
from hardy.evals.viewer import PAGE, ReviewRefused, payload, record_review, serve

ROOT = Path(__file__).resolve().parents[2]


def _entry(**kw) -> Entry:
    base = dict(id="odd-squares", input=r"If $a$ and $b$ are odd, $a^2+b^2$ is not a square.",
                name="OddSquares", binders="(a b : ℤ)", conclusion="¬ IsSquare (a ^ 2 + b ^ 2)",
                expected="true", source="classical", msc=("11Axx",), difficulty="substantial",
                rationale="smoke", witness=None, witness_note="none yet")
    base.update(kw)
    return Entry(**base)


def test_the_page_ships_beside_the_module():
    """Served from the installed package, not from a checkout of the repo."""
    assert PAGE.is_file() and PAGE.suffix == ".html"
    assert "/api/corpus" in PAGE.read_text(encoding="utf-8")


def test_an_entry_arrives_classified_so_the_page_derives_nothing():
    """The roll-up runs here, not in JS: a second implementation in the
    browser would be a second thing to keep correct."""
    entry = next(e for e in payload(ROOT / "corpus")["entries"] if e["id"] == "sqrt-two-irrational")
    assert entry["msc"] == [{"code": "11J72", "name": "Irrationality; linear independence over a field"}]
    assert entry["field"] == "Number theory"
    assert entry["group"] == "number-theory"
    assert entry["arxiv"] == "math.NT"
    assert entry["declaration"].startswith("theorem ")
    assert len(entry["statement_digest"]) == 64


def test_the_shipped_corpus_reports_clean_with_its_counts():
    got = payload(ROOT / "corpus")
    assert got["issues"] == []
    assert got["counts"] == {"entries": 20, "twins": 5, "active": 0,
                             "unwitnessed": 20, "unsourced": 20}
    assert got["corpus_version"]


def test_a_broken_corpus_is_reported_rather_than_raised(tmp_path):
    """The common case while entries are written by hand: the page must still
    render and say what is wrong, or the tool is useless exactly when needed.
    """
    write_corpus(tmp_path / "corpus", (_entry(),))
    (tmp_path / "corpus" / "problems" / "11.json").write_text("{ not json", encoding="utf-8")
    got = payload(tmp_path / "corpus")
    assert got["entries"] == []
    assert any("11.json" in i for i in got["issues"])


def test_the_taxonomy_comes_from_the_corpus_being_viewed(tmp_path):
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    (root / "taxonomy" / "msc2020.json").write_text(
        json.dumps({"codes": {"11Axx": "Invented by this corpus"}}), encoding="utf-8")
    entry = payload(root)["entries"][0]
    assert entry["msc"][0]["name"] == "Invented by this corpus"


@pytest.fixture
def running(tmp_path):
    write_corpus(tmp_path / "corpus", (_entry(),))
    server = serve(tmp_path / "corpus", port=0, report=lambda _: None, serve_forever=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_the_two_routes_serve_and_everything_else_is_refused(running):
    """No path from a URL to an arbitrary file on the machine."""
    with urllib.request.urlopen(f"{running}/") as page:
        assert page.headers["Content-Type"].startswith("text/html")
        assert b"Hardy corpus" in page.read()
    with urllib.request.urlopen(f"{running}/api/corpus") as api:
        assert api.headers["Cache-Control"] == "no-store", "a cached corpus defeats refresh"
        assert json.loads(api.read())["entries"][0]["id"] == "odd-squares"
    for path in ("/etc/passwd", "/../pyproject.toml", "/corpus/problems/11.json"):
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{running}{path}")
        assert raised.value.code == 404


def test_an_edit_is_visible_on_the_next_request(running, tmp_path):
    """The whole point: add an entry, refresh, see it."""
    write_corpus(tmp_path / "corpus", (_entry(), _entry(id="second", name="Second")))
    with urllib.request.urlopen(f"{running}/api/corpus") as api:
        assert {e["id"] for e in json.loads(api.read())["entries"]} == {"odd-squares", "second"}


# The globals the page's CDN scripts install on `window`. A top-level
# declaration of any of these in the page's own script silently replaces the
# library: `function katex(el)` shadowed KaTeX itself, and
# `renderMathInElement` then died with "katex is not a function". The failure
# is invisible wherever the CDN is unreachable -- the page degrades to raw
# LaTeX either way -- so a browser is the wrong place to catch it.
CDN_GLOBALS = {"katex", "renderMathInElement"}
TOP_LEVEL = re.compile(r"^(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)


def test_the_page_shadows_none_of_the_globals_it_loads():
    declared = set(TOP_LEVEL.findall(PAGE.read_text(encoding="utf-8")))
    assert declared, "no top-level declarations found; the regex has drifted"
    assert not declared & CDN_GLOBALS, sorted(declared & CDN_GLOBALS)


def test_every_cdn_global_this_guards_is_one_the_page_actually_loads():
    """Otherwise the guard above rots into a list of names nobody uses."""
    page = PAGE.read_text(encoding="utf-8")
    assert "katex.min.js" in page and "auto-render.min.js" in page
    assert "renderMathInElement" in page


def test_every_remote_file_the_page_loads_is_pinned_by_digest():
    """A CDN script runs in the page's origin and could read `/api/corpus`.

    The tree behind `--corpus` may be unpublished authoring work, so a
    compromised or substituted CDN response is a disclosure, not a cosmetic
    problem. Integrity pins it; the policy below denies it anywhere to send
    what it read.
    """
    page = PAGE.read_text(encoding="utf-8")
    remote = re.findall(r'<(?:script|link)\b[^>]*?(https://[^"\']+)["\'][^>]*>', page, re.DOTALL)
    assert remote, "no remote resources found; the regex has drifted"
    for tag in re.findall(r"<(?:script|link)\b[^>]*>", page, re.DOTALL):
        if "https://" not in tag:
            continue
        assert re.search(r'integrity="sha(?:256|384|512)-[A-Za-z0-9+/=]+"', tag), tag
        assert 'crossorigin="anonymous"' in tag, tag


def test_the_page_declares_a_policy_that_keeps_the_corpus_on_this_machine():
    page = PAGE.read_text(encoding="utf-8")
    policy = re.search(r'http-equiv="Content-Security-Policy" content="(.*?)"', page, re.DOTALL)
    assert policy, "no content-security policy"
    directives = {
        part.split()[0]: part.split()[1:]
        for part in (p.strip() for p in policy.group(1).split(";")) if part
    }
    assert directives["default-src"] == ["'none'"]
    # The one that matters: a script that got here anyway has nowhere to post to.
    assert directives["connect-src"] == ["'self'"]
    for source in directives["script-src"] + directives["style-src"] + directives["font-src"]:
        assert source in ("'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"), source


def test_the_policy_permits_every_host_the_page_actually_loads_from():
    """Otherwise the page silently degrades to raw LaTeX in every browser."""
    page = PAGE.read_text(encoding="utf-8")
    policy = re.search(r'http-equiv="Content-Security-Policy" content="(.*?)"', page, re.DOTALL).group(1)
    hosts = {re.match(r"https://[^/]+", url).group(0)
             for url in re.findall(r'(?:src|href)="(https://[^"]+)"', page)}
    assert hosts
    for host in hosts:
        assert host in policy, host


def test_no_corpus_field_reaches_the_page_unescaped():
    """Corpus JSON is authored by hand and by agents, not by this repository.

    An entry field interpolated straight into `innerHTML` is markup the page
    then renders -- `${o.locator.join(".")}` was exactly that. The loop names
    the corpus objects (`e` an entry, `o` an occurrence, `m` an MSC code,
    `src` a source), so any value read off one of them and not passed through
    `esc` is the bug. Comparisons and ternaries are excluded: they yield the
    page's own literals, not the corpus's text.
    """
    page = PAGE.read_text(encoding="utf-8")
    script = page[page.index("<script>\nlet DATA"):]
    # Holes nest -- a ternary whose branch is another template literal -- so
    # collect them innermost-first, blanking each one before looking again.
    # A single pass would see only the innermost of a nested pair and skip the
    # expression wrapped around it.
    holes, rest = [], script
    while (found := re.findall(r"\$\{([^{}]*)\}", rest)):
        holes.extend(found)
        rest = re.sub(r"\$\{[^{}]*\}", "_", rest)
    assert holes, "no template interpolations found; the regex has drifted"
    values = [h for h in holes
              if "===" not in h and "?" not in h and not h.strip().endswith(".length")]
    unescaped = [h for h in values
                 if re.match(r"\s*(?:e|o|m|src)\.", h) and not re.search(r"\besc\b", h)]
    assert unescaped == [], unescaped


def test_a_taxonomy_edit_is_visible_on_the_next_request_too(tmp_path):
    """The tables are cached per root and this process outlives many edits.

    `corpus serve` promises the corpus as it is on disk. Without dropping the
    cache, an MSC code added to `msc2020.json` would keep being rejected --
    and the entry citing it would keep failing `check` -- until a restart the
    page gives no reason to suspect is needed.
    """
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    assert payload(root)["entries"][0]["msc"][0]["name"] != "Renamed mid-session"
    table = root / "taxonomy" / "msc2020.json"
    codes = json.loads(table.read_text(encoding="utf-8"))
    codes["codes"]["11Axx"] = "Renamed mid-session"
    table.write_text(json.dumps(codes), encoding="utf-8")
    assert payload(root)["entries"][0]["msc"][0]["name"] == "Renamed mid-session"


def test_a_malformed_sources_file_is_reported_rather_than_raised(tmp_path):
    """`sources.json` is hand-authored, so half-written is its normal state.

    Raising here would take down the whole response -- so the page could not
    show the objection that says what is wrong with the file.
    """
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    (root / "sources.json").write_text("{ not json", encoding="utf-8")
    got = payload(root)
    assert got["sources"] == {}
    assert got["issues"], "the objection must still reach the page"
    assert len(got["entries"]) == 1, "the entries are unaffected by a bad sidecar"


def test_a_sources_file_that_is_not_a_mapping_leaves_the_page_a_mapping(tmp_path):
    """The page indexes `DATA.sources[o.source_id]`; a list there is a bug."""
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    (root / "sources.json").write_text('{"sources": []}', encoding="utf-8")
    assert payload(root)["sources"] == {}


def test_an_entry_is_unwitnessed_by_a6s_own_rule_not_by_the_field_being_null(tmp_path):
    """A witness with no binders closes nothing: `∃ , True` has no content.

    `witness_source` returns `None` there and the sweep records the entry
    unwitnessed, so a count keyed on `witness is not None` would tell the
    author the entry is covered while A6 never ran on it.
    """
    covered = _entry(id="covered", name="Covered", binders="(a : ℤ)",
                     conclusion="a = a", witness="⟨1, trivial⟩", witness_note="")
    hollow = _entry(id="hollow", name="Hollow", binders="", conclusion="True",
                    witness="trivial", witness_note="")
    root = write_corpus(tmp_path / "corpus", (covered, hollow))
    got = payload(root)
    by_id = {e["id"]: e for e in got["entries"]}
    assert by_id["covered"]["witness"] and by_id["hollow"]["witness"], "both fill the field"
    assert by_id["covered"]["unwitnessed"] is False
    assert by_id["hollow"]["unwitnessed"] is True, "no binders, so A6 has nothing to close"
    assert got["counts"]["unwitnessed"] == 1


def test_the_first_load_draws_the_list_before_anything_is_selected():
    """A fresh `corpus serve` opens on no hash, so no entry is selected.

    `renderList` is the only thing that fills `#items`, and the boot path
    reaches it only through `renderDetail`; an early return on the
    nothing-selected branch therefore left the page blank -- twenty entries
    on disk, an empty list on screen -- until the user happened to type in a
    filter. Confirmed in Chromium before and after the fix; pinned here
    because the suite has no browser.
    """
    page = PAGE.read_text(encoding="utf-8")
    body = page[page.index("function renderDetail()"):page.index("async function load()")]
    early = body[:body.index("return;")]
    assert "renderList()" in early, "the nothing-selected branch leaves #items untouched"
    boot = page[page.index("async function load()"):]
    assert "renderDetail();" in boot, "load() must reach the renderer at all"


def test_a_taxonomy_missing_a_rollup_is_reported_rather_than_raised(tmp_path):
    """`load_corpus` checks only that a full MSC code exists.

    A class temporarily removed from `fields`, `groups` or `arxiv` -- the
    ordinary state of `msc-to-arxiv.json` mid-edit -- therefore loads fine and
    then blows up in `_classified`, which is the one place that asks for the
    roll-up. That killed the response before `check_issues` could report it,
    so the page went blank on exactly the malformed taxonomy it promises to
    render.
    """
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    table = root / "taxonomy" / "msc-to-arxiv.json"
    mapping = json.loads(table.read_text(encoding="utf-8"))
    for name in ("fields", "groups", "arxiv"):
        mapping[name].pop("11", None)
    table.write_text(json.dumps(mapping), encoding="utf-8")
    got = payload(root)
    assert got["issues"], "the objection must reach the page"
    assert any("11" in issue for issue in got["issues"])
    assert got["entries"] == [], "an entry that cannot be classified is not shown as classified"


def test_the_witness_pane_agrees_with_the_count_in_the_header():
    """A binderless entry storing a term is unwitnessed however the field
    reads, so a detail pane that renders the term as evidence contradicts the
    header two panels away -- and A6 never ran on it either way."""
    page = PAGE.read_text(encoding="utf-8")
    witness = page[page.index("const witness ="):page.index("const review =")]
    assert "e.unwitnessed" in witness, "the pane branches on the stored field, not on A6's verdict"


@pytest.mark.parametrize("name", ["sources.json", "tombstones.json",
                                  "taxonomy/msc2020.json", "taxonomy/msc-to-arxiv.json"])
def test_a_sidecar_of_the_wrong_json_shape_still_renders_the_page(tmp_path, name):
    """`[]` parses, so the objection has to come from a shape check.

    The viewer's fallbacks all sit *after* `check_issues`, so a `TypeError`
    raised inside it took down the response before any of them ran.
    """
    root = write_corpus(tmp_path / "corpus", (_entry(),))
    (root / name).write_text("[]", encoding="utf-8")
    got = payload(root)
    assert got["issues"], "the objection must reach the page"
    assert isinstance(got["sources"], dict)


# --- The write path: a human records a faithfulness read (spec §2.2, §12) ---


def _authored(id: str, **kw) -> Entry:
    return _entry(id=id, name="".join(p.title() for p in id.split("-")), binders="(n : ℕ)",
                  conclusion="n = n", msc=("13A15",), witness="⟨0, trivial⟩", witness_note=None, **kw)


@pytest.fixture
def reviewable(tmp_path):
    root = write_corpus(tmp_path / "corpus", (_authored("alpha"), _authored("beta")))
    # `sources.json` is content the manifest covers, so the head is re-bound after it lands.
    (root / "sources.json").write_text('{"schema_version": 1, "sources": {}}', encoding="utf-8")
    rebind_changelog(root)
    return root


def test_a_faithful_verdict_promotes_the_entry_and_binds_the_review_to_its_digests(reviewable):
    before = load_corpus(reviewable).by_id("alpha")

    result = record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    after = load_corpus(reviewable).by_id("alpha")
    assert after.status == "active"
    assert after.review is not None
    assert after.review.reviewer == "Ada Lovelace"
    assert after.review.verdict == "faithful"
    assert after.review.statement_digest == before.statement_digest()
    assert after.review.prompt_digest == before.prompt_digest()
    assert after.review.msc == before.msc and after.review.group == "commutative-algebra"
    assert result["status"] == "active" and result["review"]["reviewer"] == "Ada Lovelace"


def test_an_unfaithful_verdict_needs_a_reason_and_leaves_the_entry_a_candidate(reviewable):
    with pytest.raises(ReviewRefused, match="reason|why"):
        record_review(reviewable, "alpha", verdict="unfaithful", reviewer="Ada Lovelace")
    assert load_corpus(reviewable).by_id("alpha").review is None, "a refused review writes nothing"

    record_review(reviewable, "alpha", verdict="unfaithful", reviewer="Ada Lovelace", reason="binders drop 0 < n")

    after = load_corpus(reviewable).by_id("alpha")
    assert after.status == "candidate"
    assert after.review.verdict == "unfaithful" and after.review.reason == "binders drop 0 < n"


def test_a_review_of_an_unknown_entry_or_by_nobody_is_refused(reviewable):
    with pytest.raises(ReviewRefused, match="gamma"):
        record_review(reviewable, "gamma", verdict="faithful", reviewer="Ada Lovelace")
    with pytest.raises(ReviewRefused, match="reviewer"):
        record_review(reviewable, "alpha", verdict="faithful", reviewer="   ")


def test_recording_a_review_leaves_the_rest_of_the_shard_alone(reviewable):
    shard = reviewable / "problems" / "13.json"
    beta_before = next(r for r in json.loads(shard.read_text(encoding="utf-8"))["entries"] if r["id"] == "beta")

    record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    rows = json.loads(shard.read_text(encoding="utf-8"))["entries"]
    assert [r["id"] for r in rows] == ["alpha", "beta"], "order and membership are preserved"
    assert next(r for r in rows if r["id"] == "beta") == beta_before
    assert not (reviewable / "problems" / "13.json.tmp").exists()


def test_a_recorded_review_leaves_only_the_release_objection_standing(reviewable):
    """Content changed, so the manifest no longer matches the changelog head:
    that objection is expected and is cleared by the release cut before a push.
    Anything else would mean the write produced a corpus the CLI rejects."""
    from hardy.evals.corpus import check_issues

    assert check_issues(reviewable) == []
    record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")
    issues = check_issues(reviewable)
    assert len(issues) == 1 and "manifest" in issues[0], issues


@pytest.fixture
def reviewing(reviewable):
    server = serve(reviewable, port=0, report=lambda _: None, serve_forever=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", reviewable
    server.shutdown()
    server.server_close()


def _post(url: str, body: dict):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(request)


def test_the_review_route_records_a_verdict_and_returns_the_updated_entry(reviewing):
    url, root = reviewing
    with _post(f"{url}/api/review", {"id": "alpha", "verdict": "faithful", "reviewer": "Ada Lovelace"}) as res:
        body = json.loads(res.read())
    assert body["status"] == "active" and body["review"]["reviewer"] == "Ada Lovelace"
    assert load_corpus(root).by_id("alpha").status == "active"


def test_the_review_route_refuses_with_the_reason_and_writes_nothing(reviewing):
    url, root = reviewing
    for body in ({"id": "gamma", "verdict": "faithful", "reviewer": "Ada"},
                 {"id": "alpha", "verdict": "unfaithful", "reviewer": "Ada"},
                 {"id": "alpha", "verdict": "faithful", "reviewer": ""}):
        with pytest.raises(urllib.error.HTTPError) as raised:
            _post(f"{url}/api/review", body)
        assert raised.value.code == 400
        assert "error" in json.loads(raised.value.read())
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(urllib.request.Request(f"{url}/api/review", data=b"not json", method="POST"))
    assert raised.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(urllib.request.Request(f"{url}/api/corpus", data=b"{}", method="POST"))
    assert raised.value.code == 404, "POST exists for one route only"
    assert load_corpus(root).by_id("alpha").review is None


# --- Citations: AMS alpha labels and the per-source locator conventions ---

from hardy.evals.viewer import cite_locator, format_ams  # noqa: E402


AM = {"citation_key": "AM69", "authors": ["M. F. Atiyah", "I. G. Macdonald"],
      "title": "Introduction to commutative algebra", "publisher": "Addison-Wesley Publishing Co.",
      "address": "Reading, Mass.-London-Don Mills, Ont.", "year": 1969, "locator_style": "chapter-item"}


@pytest.mark.parametrize("style, locator, text", [
    ("chapter-item", [1, 0, 11], "1.11"),
    ("chapter-item", [3, 1, 18], "Ex. 3.18"),
    ("section-item", [7, 4, 11], "§7.4, no. 11"),
    ("section-item", [7, 4, 127], "§7.4, Ex. 27"),
    ("numbered-section", [1, 1, 3], "Thm. 1.3"),
    ("numbered-section", [1, 1, 105], "Ex. 1.5"),
    ("numbered-section", [1, 1, 201], "§1, Example 1"),
    ("paragraph", [1, 8, 0], "1.8"),
    ("paragraph", [1, 8, 1], "1.8, Cor."),
    ("paragraph", [1, 99, 10], "Ex. 1.10"),
    ("paragraph", [5, 12, 0], "5.12"),
])
def test_a_locator_renders_the_way_the_book_is_cited(style, locator, text):
    assert cite_locator(style, locator) == text


def test_an_unknown_style_or_shape_falls_back_to_the_bare_locator():
    assert cite_locator("nonsense", [1, 2, 3]) == "1.2.3"
    assert cite_locator("chapter-item", [4]) == "4"


def test_a_source_renders_as_an_ams_alpha_bibliography_entry():
    assert format_ams(AM) == (
        "M. F. Atiyah and I. G. Macdonald, <i>Introduction to commutative algebra</i>, "
        "Addison-Wesley Publishing Co., Reading, Mass.-London-Don Mills, Ont., 1969.")
    reid = {"citation_key": "Rei95", "authors": ["Miles Reid"], "title": "Undergraduate commutative algebra",
            "series": "London Mathematical Society Student Texts", "volume": 29,
            "publisher": "Cambridge University Press", "address": "Cambridge", "year": 1995}
    assert format_ams(reid) == (
        "Miles Reid, <i>Undergraduate commutative algebra</i>, London Mathematical Society Student Texts, "
        "vol. 29, Cambridge University Press, Cambridge, 1995.")
    df = {"citation_key": "DF04", "authors": ["David S. Dummit", "Richard M. Foote"], "title": "Abstract algebra",
          "edition": "third", "publisher": "John Wiley & Sons, Inc.", "address": "Hoboken, NJ", "year": 2004}
    assert format_ams(df) == (
        "David S. Dummit and Richard M. Foote, <i>Abstract algebra</i>, third ed., "
        "John Wiley &amp; Sons, Inc., Hoboken, NJ, 2004.")
    three = {"authors": ["A. One", "B. Two", "C. Three"], "title": "T", "year": 2000}
    assert format_ams(three) == "A. One, B. Two, and C. Three, <i>T</i>, 2000."


def test_the_payload_carries_the_citation_beside_each_occurrence(tmp_path):
    root = write_corpus(tmp_path / "corpus", (_entry(occurrences=({"source_id": "am", "locator": (1, 0, 11)},), rationale=None),))
    (root / "sources.json").write_text(json.dumps({"schema_version": 1, "sources": {"am": AM}}), encoding="utf-8")
    entry = payload(root)["entries"][0]
    assert entry["occurrences"][0]["citation"] == {"key": "AM69", "locator": "1.11"}
    assert "<i>Introduction to commutative algebra</i>" in payload(root)["sources"]["am"]["ams"]


def test_the_bibliography_page_is_served_and_lists_every_source(reviewing):
    url, _ = reviewing
    with urllib.request.urlopen(f"{url}/bibliography") as page:
        assert page.headers["Content-Type"].startswith("text/html")
        assert b"Bibliography" in page.read()


# --- Navigation: previous/next through the filtered list, with keys ---

import shutil  # noqa: E402
import subprocess  # noqa: E402


def _neighbours(all_ids, shown_ids, current):
    """Run the page's own `neighbours` under Node, so the test reads the real function."""
    page = PAGE.read_text(encoding="utf-8")
    start = page.index("function neighbours(")
    end = page.index("\n}\n", start) + 3
    script = page[start:end] + (
        f"\nconsole.log(JSON.stringify(neighbours({json.dumps(all_ids)}, "
        f"{json.dumps(shown_ids)}, {json.dumps(current)})));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_next_and_previous_step_through_the_filtered_list_and_wrap():
    every = ["a", "b", "c", "d", "e"]
    assert _neighbours(every, ["a", "c", "e"], "c") == {"prev": "a", "next": "e"}
    assert _neighbours(every, ["a", "c", "e"], "e") == {"prev": "c", "next": "a"}, "wraps forward"
    assert _neighbours(every, ["a", "c", "e"], "a") == {"prev": "e", "next": "c"}, "wraps backward"


@needs_node
def test_an_entry_that_left_the_filter_is_skipped_in_both_directions():
    """Approve `c` under a candidate-only filter: it drops out of the list.
    Next from it goes on to `e`; previous from `e` then lands on `a`, not `c`."""
    every = ["a", "b", "c", "d", "e"]
    assert _neighbours(every, ["a", "e"], "c") == {"prev": "a", "next": "e"}
    assert _neighbours(every, ["a", "e"], "e") == {"prev": "a", "next": "a"}


@needs_node
def test_with_one_or_no_entries_shown_there_is_nowhere_to_go():
    every = ["a", "b", "c"]
    assert _neighbours(every, ["b"], "b") == {"prev": None, "next": None}
    assert _neighbours(every, [], "b") == {"prev": None, "next": None}
    assert _neighbours(every, ["a"], "b") == {"prev": "a", "next": "a"}, "one other entry is still somewhere to go"


def test_the_page_binds_the_keys_it_documents():
    page = PAGE.read_text(encoding="utf-8")
    legend = page[page.index('class="keys"'):]
    for key in ("←", "→", "F", "U"):
        assert key in legend[:600], f"{key} is not in the legend"
    handler = page[page.index("function onKey("):]
    for name in ('"ArrowLeft"', '"ArrowRight"', '"f"', '"u"'):
        assert name in handler[:1500], f"{name} is not handled"
    assert "INPUT" in handler[:1500] and "SELECT" in handler[:1500], "typing in a filter must not navigate"
