"""The corpus viewer: what the page is handed, and the two routes that exist."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from corpus_helpers import write_corpus

from hardy.evals.problems import Entry
from hardy.evals.viewer import PAGE, payload, serve

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
