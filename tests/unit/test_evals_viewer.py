"""The corpus viewer: what the page is handed, and the two routes that exist."""
from __future__ import annotations

import json
import os
import re
import socket
import stat
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from corpus_helpers import rebind_changelog, write_corpus

from hardy.app.corpus_viewer import (
    PAGE,
    ReviewRefused,
    _allowed_hosts,
    _announce_host,
    _HTTPServerV6,
    _server_class,
    payload,
    record_review,
    serve,
)
from hardy.corpus.catalog import load_corpus
from hardy.corpus.problems import Entry

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
                             "unwitnessed": 20, "unsourced": 20,
                             # No tier file is passed, so nothing is tiered.
                             "tiered": 0, "broken": 0}
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
    from hardy.corpus.catalog import check_issues

    assert check_issues(reviewable) == []
    record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")
    issues = check_issues(reviewable)
    assert len(issues) == 1 and "manifest" in issues[0], issues


@pytest.fixture
def reviewing(reviewable):
    server = serve(reviewable, port=0, report=lambda _: None, serve_forever=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", reviewable, server.token
    server.shutdown()
    server.server_close()


def _post(url: str, body: dict, *, token: str):
    origin = urlsplit(url)
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Origin": f"{origin.scheme}://{origin.netloc}",
                 "X-Hardy-Token": token})
    return urllib.request.urlopen(request)


def test_the_review_route_records_a_verdict_and_returns_the_updated_entry(reviewing):
    url, root, token = reviewing
    with _post(f"{url}/api/review", {"id": "alpha", "verdict": "faithful", "reviewer": "Ada Lovelace"},
              token=token) as res:
        body = json.loads(res.read())
    assert body["status"] == "active" and body["review"]["reviewer"] == "Ada Lovelace"
    assert load_corpus(root).by_id("alpha").status == "active"


def test_the_review_route_refuses_with_the_reason_and_writes_nothing(reviewing):
    url, root, token = reviewing
    for body in ({"id": "gamma", "verdict": "faithful", "reviewer": "Ada"},
                 {"id": "alpha", "verdict": "unfaithful", "reviewer": "Ada"},
                 {"id": "alpha", "verdict": "faithful", "reviewer": ""}):
        with pytest.raises(urllib.error.HTTPError) as raised:
            _post(f"{url}/api/review", body, token=token)
        assert raised.value.code == 400
        assert "error" in json.loads(raised.value.read())
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(urllib.request.Request(
            f"{url}/api/review", data=b"not json", method="POST",
            headers={"Content-Type": "application/json", "Origin": url, "X-Hardy-Token": token}))
    assert raised.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(urllib.request.Request(f"{url}/api/corpus", data=b"{}", method="POST"))
    assert raised.value.code == 404, "POST exists for one route only"
    assert load_corpus(root).by_id("alpha").review is None


# --- Request authentication: Host, Origin and the per-process token (#217) ---


def test_a_wildcard_bind_admits_the_resolved_hostname_address_and_loopback():
    """`--host 0.0.0.0` has no address of its own: the admitted `Host` values
    come from the machine's own name and what it resolves to, not the bind
    string, plus loopback -- never an arbitrary name an attacker's DNS
    points here."""
    hosts = _allowed_hosts("0.0.0.0", 9, resolve=lambda: ("mybox", "mybox.local", ["10.0.0.5"]))
    assert hosts == {"mybox:9", "mybox.local:9", "10.0.0.5:9",
                     "127.0.0.1:9", "localhost:9", "[::1]:9"}
    assert "evil.example:9" not in hosts


def test_a_resolver_failure_on_a_wildcard_bind_still_yields_loopback():
    """A machine with no DNS or a sandboxed hostname lookup must not stop the
    server from answering on loopback at all."""
    def _boom() -> tuple[str, str, list[str]]:
        raise OSError("no resolver here")

    assert _allowed_hosts("0.0.0.0", 9, resolve=_boom) == {"127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_a_resolver_raising_unicode_error_on_a_wildcard_bind_still_yields_loopback():
    """`getaddrinfo` IDNA-encodes its argument before resolving it, and
    raises `UnicodeError` -- a `ValueError`, not an `OSError` -- for one that
    fails to encode. An injected resolver that fails this way must be caught
    exactly like one that raises `OSError`."""
    def _bad_name() -> tuple[str, str, list[str]]:
        raise UnicodeError("label empty or too long")

    assert _allowed_hosts("0.0.0.0", 9, resolve=_bad_name) == {"127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_the_real_resolver_swallows_a_getaddrinfo_unicode_error_too(monkeypatch):
    """Exercises `_resolve_names` itself, not just the injected seam: a
    non-ASCII or malformed hostname or FQDN must not crash `serve()` either,
    which a bare `except OSError` around `getaddrinfo` would miss."""
    monkeypatch.setattr(socket, "gethostname", lambda: "mybox")
    monkeypatch.setattr(socket, "getfqdn", lambda: "mybox.local")

    def _boom(*args: object, **kwargs: object) -> None:
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert _allowed_hosts("0.0.0.0", 9) == {"mybox:9", "mybox.local:9",
                                            "127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_a_specific_host_admits_only_itself():
    """`--host 192.168.1.5` names one address; nothing else -- not even
    loopback, which is not how a client would reach this bind -- is it."""
    assert _allowed_hosts("192.168.1.5", 9) == {"192.168.1.5:9"}


def test_a_specific_loopback_host_admits_the_usual_aliases_too():
    """`localhost` and `127.0.0.1`/`::1` are used interchangeably for a
    loopback bind; admitting only the one spelled on the command line would
    refuse the others for no security reason."""
    assert _allowed_hosts("127.0.0.1", 9) == {"127.0.0.1:9", "localhost:9", "[::1]:9"}
    assert _allowed_hosts("localhost", 9) == {"127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_an_ipv6_host_is_admitted_bracketed():
    assert _allowed_hosts("2001:db8::1", 9) == {"[2001:db8::1]:9"}


def test_a_non_canonical_ipv6_loopback_literal_is_admitted_by_its_canonical_form():
    """A WHATWG-URL client (a browser, `fetch`, `curl`) canonicalises an IP
    literal before ever sending it in `Host` -- `0:0:0:0:0:0:0:1` becomes
    `::1`. Admitting the literal spelling `--host` happened to be given,
    rather than the address it names, would 403 every such client."""
    hosts = _allowed_hosts("0:0:0:0:0:0:0:1", 9)
    assert hosts == {"[::1]:9", "127.0.0.1:9", "localhost:9"}


def test_the_announced_host_for_a_non_canonical_loopback_literal_is_admitted():
    assert _announce_host("0:0:0:0:0:0:0:1") == "[::1]"
    assert f"{_announce_host('0:0:0:0:0:0:0:1')}:9" in _allowed_hosts("0:0:0:0:0:0:0:1", 9)


def test_an_ipv4_literal_is_unchanged_by_canonicalisation():
    assert _allowed_hosts("192.168.1.5", 9) == {"192.168.1.5:9"}
    assert _announce_host("192.168.1.5") == "192.168.1.5"


def test_a_wildcard_binds_admitted_names_are_lowercase():
    """`gethostname()`/`getfqdn()` keep whatever casing the machine gives
    them -- "DESKTOP-ABC" is typical on Windows -- but a `Host` header is a
    DNS name, and DNS names are case-insensitive: a client is free to spell
    it differently. The admitted set is normalised to lowercase so a
    case-sensitive membership check cannot refuse a legitimate host over
    casing alone; `desktop-abc:9`, not `DESKTOP-ABC:9`, is what a lowercased
    incoming `Host` is compared against."""
    hosts = _allowed_hosts("0.0.0.0", 9, resolve=lambda: ("DESKTOP-ABC", "DESKTOP-ABC.LOCAL", ["10.0.0.5"]))
    assert hosts == {"desktop-abc:9", "desktop-abc.local:9", "10.0.0.5:9",
                     "127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_a_specific_hosts_admitted_name_is_lowercase_too():
    assert _allowed_hosts("DESKTOP-ABC", 9) == {"desktop-abc:9"}
    assert _allowed_hosts("LOCALHOST", 9) == {"127.0.0.1:9", "localhost:9", "[::1]:9"}


def test_a_host_header_of_different_case_from_the_admitted_one_is_allowed(running):
    """The server side of the same normalisation: an admitted host must stay
    admitted no matter how a client capitalizes its `Host` header."""
    port = urlsplit(running).port
    request = urllib.request.Request(f"{running}/api/corpus", headers={"Host": f"Localhost:{port}"})
    with urllib.request.urlopen(request) as response:
        assert response.status == 200


def test_port_80_also_admits_the_portless_form_of_a_specific_host():
    """A client reaching the default HTTP port sends a bare `Host: host`,
    with no `:80` on it (`curl http://127.0.0.1:80/` sends `Host: 127.0.0.1`)
    -- refusing that on the one port where it is normal, not short, would
    403 every ordinary request to it."""
    hosts = _allowed_hosts("127.0.0.1", 80)
    assert hosts == {"127.0.0.1:80", "localhost:80", "[::1]:80",
                     "127.0.0.1", "localhost", "[::1]"}


def test_port_80_admits_the_portless_form_on_a_wildcard_bind_too():
    hosts = _allowed_hosts("0.0.0.0", 80, resolve=lambda: ("mybox", "mybox.local", ["10.0.0.5"]))
    assert hosts == {"mybox:80", "mybox.local:80", "10.0.0.5:80",
                     "127.0.0.1:80", "localhost:80", "[::1]:80",
                     "mybox", "mybox.local", "10.0.0.5",
                     "127.0.0.1", "localhost", "[::1]"}


def test_a_non_80_port_carries_no_portless_forms():
    """The portless admission is specific to port 80 -- an ordinary port
    still requires the port a client actually connects to."""
    hosts = _allowed_hosts("127.0.0.1", 8765)
    assert hosts == {"127.0.0.1:8765", "localhost:8765", "[::1]:8765"}


def test_wildcard_hosts_announce_a_url_that_is_actually_admitted():
    """`serve()` used to print the bind string itself for a wildcard bind --
    `http://0.0.0.0:.../` -- which `_allowed_hosts` does not admit, so the
    link it had just printed 403'd. It must announce a host the same
    machine's `_allowed_hosts` call actually lets through."""
    assert _announce_host("0.0.0.0") == "127.0.0.1"
    assert _announce_host("") == "127.0.0.1"
    assert _announce_host("::") == "[::1]"
    for host in ("0.0.0.0", ""):
        assert f"{_announce_host(host)}:9" in _allowed_hosts(host, 9)
    assert f"{_announce_host('::')}:9" in _allowed_hosts("::", 9)


def test_a_specific_hosts_announced_url_is_itself_bracketed_if_ipv6():
    assert _announce_host("192.168.1.5") == "192.168.1.5"
    assert _announce_host("2001:db8::1") == "[2001:db8::1]"


def test_a_mixed_case_hosts_announced_url_is_lowercase():
    """`_allowed_hosts` admits a specific host lowercased; the announced URL
    must name that same lowercase host, or the two would disagree about what
    this server answers to and the printed link would 403 itself."""
    host = "MyBox.Local"
    assert _announce_host(host) == "mybox.local"
    assert f"{_announce_host(host)}:9" in _allowed_hosts(host, 9)


def test_the_af_inet6_server_class_is_bound_to_af_inet6():
    assert _HTTPServerV6.address_family == socket.AF_INET6
    assert issubclass(_HTTPServerV6, HTTPServer)


def test_server_class_selection_needs_no_real_ipv6_stack():
    """`serve()`'s choice of server class is a pure function of the bind
    string -- checkable without ever opening a socket, IPv6-capable host or
    not, unlike the actual bind exercised by the (possibly skipped) live test
    below."""
    for host in ("::", "::1", "2001:db8::1", ""):
        assert _server_class(host) is (_HTTPServerV6 if ":" in host else HTTPServer)
    for host in ("127.0.0.1", "0.0.0.0", "localhost", "mybox"):
        assert _server_class(host) is HTTPServer


def test_a_wildcard_binds_report_line_names_an_admitted_host(tmp_path):
    write_corpus(tmp_path / "corpus", (_entry(),))
    lines: list[str] = []
    server = serve(tmp_path / "corpus", host="0.0.0.0", port=0, report=lines.append, serve_forever=False)
    try:
        match = re.search(r"http://([^/]+)/", lines[0])
        assert match, lines[0]
        assert match.group(1) in server.allowed_hosts
    finally:
        server.server_close()


def _ipv6_bindable() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_bindable(), reason="IPv6 loopback is not bindable on this host")
def test_a_wildcard_ipv6_bind_actually_serves_over_af_inet6(tmp_path):
    """`--host ::` is documented as a wildcard bind, but a plain `HTTPServer`
    is hardcoded to `AF_INET`: binding `::` on it raised `socket.gaierror`
    before anything was served."""
    write_corpus(tmp_path / "corpus", (_entry(),))
    server = serve(tmp_path / "corpus", host="::", port=0, report=lambda _: None, serve_forever=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        request = urllib.request.Request(
            f"http://[::1]:{port}/api/corpus", headers={"Host": f"[::1]:{port}"}
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_a_cross_site_post_is_refused_and_the_shard_is_unchanged(reviewing):
    """`Origin` not matching `Host` is an ordinary cross-site form post."""
    url, root, token = reviewing
    shard = root / "problems" / "13.json"
    before = shard.read_bytes()
    request = urllib.request.Request(
        f"{url}/api/review",
        data=json.dumps({"id": "alpha", "verdict": "faithful", "reviewer": "Ada"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Origin": "https://evil.example",
                 "X-Hardy-Token": token})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request)
    assert raised.value.code == 403
    assert shard.read_bytes() == before
    assert load_corpus(root).by_id("alpha").review is None


def test_a_get_with_a_foreign_host_is_refused(reviewing):
    """A name an attacker's DNS resolved to this loopback address must not
    be treated as this server -- otherwise DNS rebinding defeats every other
    check, which all trust `Host`."""
    url, _, _ = reviewing
    request = urllib.request.Request(f"{url}/", headers={"Host": "evil.example:1"})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request)
    assert raised.value.code == 403


def test_a_post_with_no_token_is_refused(reviewing):
    """Host and Origin alone are not enough: a page served from this very
    origin by something else must not be able to drive it either."""
    url, root, _ = reviewing
    request = urllib.request.Request(
        f"{url}/api/review",
        data=json.dumps({"id": "alpha", "verdict": "faithful", "reviewer": "Ada"}).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json", "Origin": url})
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request)
    assert raised.value.code == 403
    assert load_corpus(root).by_id("alpha").review is None


_TOKEN_META = re.compile(r'<meta name="hardy-token" content="([^"]+)">')


def test_a_post_with_the_token_scraped_from_the_page_succeeds(reviewing):
    url, root, _ = reviewing
    with urllib.request.urlopen(f"{url}/") as page:
        html = page.read().decode("utf-8")
    scraped = _TOKEN_META.search(html)
    assert scraped, "the page must stamp the token into a <meta> tag"
    with _post(f"{url}/api/review", {"id": "alpha", "verdict": "faithful", "reviewer": "Ada"},
              token=scraped.group(1)) as res:
        body = json.loads(res.read())
    assert body["status"] == "active"
    assert load_corpus(root).by_id("alpha").status == "active"


# --- Byte-exact writes (#237) ---


def test_an_accepted_review_writes_no_carriage_return_even_if_write_text_would(monkeypatch, reviewable):
    """`write_text` without `newline=` translates every `\\n` to the platform
    default; the fix must not go through it for the shard at all, so forcing
    that translation must not be able to reach the bytes on disk."""
    real_write_text = Path.write_text

    def _crlf_write_text(self, data, *args, **kwargs):
        if "newline" not in kwargs and len(args) < 3:
            kwargs["newline"] = "\r\n"
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _crlf_write_text)
    record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")
    shard = (reviewable / "problems" / "13.json").read_bytes()
    assert b"\r" not in shard


def test_a_new_objection_reverts_the_shard_to_the_original_bytes(monkeypatch, reviewable):
    """Any exception while checking the write -- not only a clean list of
    objections -- must revert to the original bytes before propagating,
    rather than leaving an unvalidated write on disk."""
    import hardy.app.corpus_viewer as corpus_viewer

    shard = reviewable / "problems" / "13.json"
    original = shard.read_bytes()
    calls = iter([[], ["x: a new objection"]])
    monkeypatch.setattr(corpus_viewer, "check_issues", lambda root: next(calls))

    with pytest.raises(ReviewRefused):
        record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    assert shard.read_bytes() == original


def test_check_issues_raising_reverts_the_shard_and_propagates(monkeypatch, reviewable):
    """`check_issues` blowing up outright -- not returning a fresh objection --
    must be treated the same as one: the write is not left in place, and the
    fault is not swallowed on its way out."""
    import hardy.app.corpus_viewer as corpus_viewer

    shard = reviewable / "problems" / "13.json"
    original = shard.read_bytes()
    calls = iter([[], None])

    def _check_issues(root):
        result = next(calls)
        if result is None:
            raise RuntimeError("check_issues blew up")
        return result

    monkeypatch.setattr(corpus_viewer, "check_issues", _check_issues)

    with pytest.raises(RuntimeError, match="check_issues blew up"):
        record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    assert shard.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits do not apply on Windows")
def test_a_reviewed_shard_keeps_its_existing_permission_bits(reviewable):
    """`NamedTemporaryFile` creates its file `0600`; a review must not leave
    the shard less readable than it was, e.g. by a teammate's git checkout at
    the usual `0644`."""
    shard = reviewable / "problems" / "13.json"
    shard.chmod(0o644)

    record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    assert stat.S_IMODE(shard.stat().st_mode) == 0o644


def test_a_failed_write_leaves_no_temp_file_behind(monkeypatch, reviewable):
    """A write that is reverted (or that fails outright) must not leave a
    stray `tmpXXXXXX` file beside the shard."""
    import hardy.app.corpus_viewer as corpus_viewer

    calls = iter([[], ["x: a new objection"]])
    monkeypatch.setattr(corpus_viewer, "check_issues", lambda root: next(calls))

    with pytest.raises(ReviewRefused):
        record_review(reviewable, "alpha", verdict="faithful", reviewer="Ada Lovelace")

    leftovers = [p for p in (reviewable / "problems").iterdir() if p.name != "13.json"]
    assert leftovers == [], leftovers


# --- Citations: AMS alpha labels and the per-source locator conventions ---

from hardy.app.corpus_viewer import cite_locator, format_ams  # noqa: E402

AM = {"citation_key": "AM69", "authors": ["M. F. Atiyah", "I. G. Macdonald"],
      "title": "Introduction to commutative algebra", "publisher": "Addison-Wesley Publishing Co.",
      "address": "Reading, Mass.-London-Don Mills, Ont.", "year": 1969, "locator_style": "chapter-item"}


@pytest.mark.parametrize("style, locator, text", [
    ("chapter-item", [1, 0, 11], "1.11"),
    ("chapter-item", [3, 1, 18], "Ex. 3.18"),
    ("section-item", [7, 4, 11], "§7.4, no. 11"),
    ("section-item", [7, 4, 127], "§7.4, Ex. 27"),
    ("section-item", [1, 99, 19], "Ex. 1.19"),
    ("numbered-section", [1, 1, 3], "Thm. 1.3"),
    ("numbered-section", [1, 1, 105], "Ex. 1.5"),
    ("numbered-section", [1, 1, 201], "§1, Example 1"),
    ("paragraph", [1, 8, 0], "1.8"),
    ("paragraph", [1, 8, 1], "1.8, Cor."),
    ("paragraph", [1, 99, 10], "Ex. 1.10"),
    ("paragraph", [5, 12, 0], "5.12"),
    ("section-theorem", [4, 5, 1], "4.5.1"),
    ("section-theorem", [4, 5, 103], "Ex. 4.5.3"),
    ("competition-problem", [1, 0, 1], "A1"),
    ("competition-problem", [2, 0, 6], "B6"),
])
def test_a_locator_renders_the_way_the_book_is_cited(style, locator, text):
    assert cite_locator(style, locator) == text


def test_an_unknown_style_or_shape_falls_back_to_the_bare_locator():
    assert cite_locator("nonsense", [1, 2, 3]) == "1.2.3"
    assert cite_locator("chapter-item", [4]) == "4"
    # A competition paper has exactly two sessions and no sub-numbering, so a
    # triple naming neither is provenance nobody can check: print it as it
    # stands rather than inventing a session letter for it.
    assert cite_locator("competition-problem", [3, 0, 1]) == "3.0.1"
    assert cite_locator("competition-problem", [1, 7, 1]) == "1.7.1"


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
    url, _, _ = reviewing
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


def test_the_payload_carries_tier_and_elaborates_from_the_baseline(tmp_path):
    """The tier is measurement over an entry, not a property of it, so it
    comes from the tier file rather than the shard."""
    root = ROOT / "corpus"
    baseline = tmp_path / "baseline.json"
    problems = load_corpus(root)
    first, second = problems.entries[0].id, problems.entries[1].id
    baseline.write_text(json.dumps({"entries": {
        first: {"tier": 3, "elaborates": True},
        second: {"tier": 0, "elaborates": False},
    }}), encoding="utf-8")
    got = payload(root, baseline)
    by_id = {e["id"]: e for e in got["entries"]}
    assert by_id[first]["tier"] == 3 and by_id[first]["elaborates"] is True
    assert by_id[second]["tier"] == 0 and by_id[second]["elaborates"] is False
    assert got["counts"]["tiered"] == 2
    assert got["counts"]["broken"] == 1


def test_an_entry_the_sweep_has_not_reached_has_no_tier_rather_than_a_default():
    """Tier 0 would claim automation closes it and tier 3 would claim nothing
    does. Both are assertions the sweep has not made."""
    got = payload(ROOT / "corpus", None)
    assert all(e["tier"] is None and e["elaborates"] is None for e in got["entries"])
    assert got["counts"]["tiered"] == 0
    assert got["counts"]["broken"] == 0


def test_a_missing_or_malformed_tier_file_is_not_a_corpus_objection(tmp_path):
    """`evals/` is ignored and regenerable, so a checkout that never swept is
    a normal state -- not something to report beside the corpus's own issues."""
    clean = payload(ROOT / "corpus", None)["issues"]
    assert payload(ROOT / "corpus", tmp_path / "absent.json")["issues"] == clean
    junk = tmp_path / "junk.json"
    junk.write_text("{not json", encoding="utf-8")
    assert payload(ROOT / "corpus", junk)["issues"] == clean
    assert payload(ROOT / "corpus", junk)["counts"]["tiered"] == 0
