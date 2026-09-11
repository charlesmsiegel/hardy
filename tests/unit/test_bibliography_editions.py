"""Books and papers cite through one controlled path; editions never collapse on look-alikes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hardy.literature.bibliography import (
    Bibliography,
    BibliographyError,
    Entry,
    edition_cite_key,
    edition_identities,
)
from hardy.literature.sources.contracts import BibliographicWork, EditionOrVersion, WorkKind

PDF = "a" * 64
EPUB = "b" * 64
SCAN = "c" * 64

HARTSHORNE = BibliographicWork(id="work-hartshorne", kind=WorkKind.BOOK, title="Algebraic Geometry", authors=("Robin Hartshorne",))
GTM52 = EditionOrVersion(id="edition-gtm52-1977", work=HARTSHORNE.id, label="Graduate Texts in Mathematics 52", venue="Springer", year="1977",
                         identifiers=(("isbn", "9780387902449"), ("doi", "10.1007/978-1-4757-3849-0")))
REPRINT = EditionOrVersion(id="edition-gtm52-1997", work=HARTSHORNE.id, label="Graduate Texts in Mathematics 52, corrected eighth printing", venue="Springer", year="1997",
                           identifiers=(("isbn", "9780387902449"), ("doi", "10.1007/978-1-4757-3849-0")))
DONAGI = BibliographicWork(id="work-donagi-prym", kind=WorkKind.PAPER, title="The fibers of the Prym map", authors=("Ron Donagi",))
DONAGI_V1 = EditionOrVersion(id="edition-donagi-v1", work=DONAGI.id, label="arXiv v1", year="1992", identifiers=(("arxiv", "alg-geom/9206008v1"),))
DONAGI_V2 = EditionOrVersion(id="edition-donagi-v2", work=DONAGI.id, label="arXiv v2", year="1992", identifiers=(("arxiv", "alg-geom/9206008v2"),))


def test_book_edition_cited_through_controlled_path(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    entry, added = bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,))
    assert added and entry.key.startswith("hartshorne1977algebraic-")
    assert entry.identities[0] == f"edition:{GTM52.id}" and "isbn:9780387902449" in entry.identities
    assert entry.content_sha256 == PDF and entry.read_artifacts == (PDF,) and entry.edition == GTM52.id and entry.work == HARTSHORNE.id
    rendered = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert f"\\bibitem{{{entry.key}}}" in rendered and "Springer" in rendered and "ISBN 9780387902449" in rendered
    store = json.loads((tmp_path / "bibliography.json").read_text(encoding="utf-8"))
    assert store["entries"][0]["edition"] == GTM52.id
    with pytest.raises(BibliographyError, match="at least one artifact"):
        bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=())
    with pytest.raises(BibliographyError, match="belongs to work"):
        bibliography.cite_edition(DONAGI, GTM52, read_artifacts=(PDF,))


def test_pdf_and_epub_of_one_edition_share_one_entry_with_both_digests(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,), spans=("span-1",))
    second, added = bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(EPUB,), spans=("span-2",))
    assert not added and second.key == first.key
    assert second.content_sha256 == PDF and second.read_artifacts == (PDF, EPUB) and second.also_read == (EPUB,)
    assert second.cited_spans == ("span-1", "span-2")
    assert len(bibliography.entries()) == 1
    again, _ = bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(EPUB, PDF))
    assert again == second


def test_distinct_editions_do_not_deduplicate_on_title_isbn_or_doi(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,))
    second, added = bibliography.cite_edition(HARTSHORNE, REPRINT, read_artifacts=(SCAN,))
    assert added and second.key != first.key and len(bibliography.entries()) == 2
    assert set(first.identities) & set(second.identities) >= {"isbn:9780387902449"}  # shared aliases, separate entries
    v1, _ = bibliography.cite_edition(DONAGI, DONAGI_V1, read_artifacts=("d" * 64,))
    v2, added = bibliography.cite_edition(DONAGI, DONAGI_V2, read_artifacts=("e" * 64,))
    assert added and v1.key != v2.key and len(bibliography.entries()) == 4


def test_edition_cite_key_is_order_independent(tmp_path: Path):
    forward = Bibliography(tmp_path / "forward")
    backward = Bibliography(tmp_path / "backward")
    a1, _ = forward.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,))
    a2, _ = forward.cite_edition(DONAGI, DONAGI_V1, read_artifacts=("d" * 64,))
    b2, _ = backward.cite_edition(DONAGI, DONAGI_V1, read_artifacts=("d" * 64,))
    b1, _ = backward.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,))
    assert a1.key == b1.key == edition_cite_key(HARTSHORNE, GTM52) and a2.key == b2.key
    assert edition_identities(HARTSHORNE, GTM52)[0] == f"edition:{GTM52.id}"


def test_entry_without_edition_fields_still_parses_and_arxiv_path_is_unchanged(tmp_path: Path):
    legacy = Entry(key="perelman2002entropy-0123456789", identities=("arxiv:math.DG/0211159v1",), title="The entropy formula", authors=("Grigori Perelman",),
                   content_sha256="f" * 64)
    assert legacy.edition is None and legacy.read_artifacts == () and legacy.rendered().startswith("\\bibitem{perelman2002entropy-0123456789}")
    with pytest.raises(ValueError, match="read_artifacts"):
        Entry(key="k", identities=("edition:x",), title="t", authors=("a",), content_sha256="f" * 64, read_artifacts=("not-a-digest",))


def test_a_malformed_digest_on_a_merge_is_refused_and_the_store_stays_readable(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=(PDF,))
    with pytest.raises(BibliographyError, match="sha256"):
        bibliography.cite_edition(HARTSHORNE, GTM52, read_artifacts=("not-a-digest",))
    entries = bibliography.entries()
    assert len(entries) == 1 and entries[0].read_artifacts == (PDF,)
