"""Books and papers cite through one controlled path; editions never collapse on look-alikes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hardy.literature.bibliography import (
    Bibliography,
    BibliographyError,
    Entry,
    Store,
    cite_key,
    edition_cite_key,
    edition_identities,
)
from hardy.literature.metadata import PaperRecord, digest
from hardy.literature.sources.arxiv_adapter import catalog_records
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


@pytest.mark.parametrize("edition_first", [False, True])
def test_arxiv_and_edition_paths_share_entry_and_keep_first_key(tmp_path, edition_first):
    from hardy.literature.metadata import PaperRecord
    from hardy.literature.sources.arxiv_adapter import catalog_records

    record = PaperRecord(arxiv_id="2401.12345v2", title="Entropy formula", authors=("Grigori Perelman",),
                         abstract="x", published="2002-11-11T00:00:00Z", content_sha256=PDF)
    work, edition = catalog_records(record)
    bibliography = Bibliography(tmp_path)
    if edition_first:
        first, _ = bibliography.cite_edition(work, edition, read_artifacts=(EPUB,), spans=("span-1",))
        second, added = bibliography.cite(record)
    else:
        first, _ = bibliography.cite(record)
        bibliography.cite(record.model_copy(update={"content_sha256": SCAN}))
        second, added = bibliography.cite_edition(work, edition, read_artifacts=(EPUB,), spans=("span-1",))
    assert not added and second.key == first.key
    assert len(bibliography.entries()) == 1
    assert set(second.identities) >= {"arxiv:2401.12345v2", f"edition:{edition.id}"}
    assert second.edition == edition.id and second.work == work.id and second.edition_label == edition.label
    assert second.cited_spans == ("span-1",)
    assert set((second.content_sha256, *second.also_read)) >= {PDF, EPUB}
    assert set(second.read_artifacts) >= {PDF, EPUB}
    if not edition_first:
        assert SCAN in second.also_read and SCAN in second.read_artifacts


def _perelman() -> PaperRecord:
    record = PaperRecord(arxiv_id="math/0211159v1", title="The entropy formula for the Ricci flow and its geometric applications",
                         authors=("Grigori Perelman",), abstract="x", published="2002-11-11T18:00:00Z")
    return record.model_copy(update={"content_sha256": digest(record.content())})


def test_one_arxiv_paper_gets_one_key_whichever_path_cites_it(tmp_path: Path):
    """The stable-key promise, across the two façades over one admission.

    `cite` minted from the arXiv identity and `cite_edition` from the catalog
    identity, so the same paper cited first through the managed library in one
    workspace and through `cite_paper` in another carried two keys -- and a
    `\\cite` copied between the two documents stopped resolving.
    """
    record = _perelman()
    work, edition = catalog_records(record)
    by_paper, _ = Bibliography(tmp_path / "paper").cite(record)
    by_edition, _ = Bibliography(tmp_path / "edition").cite_edition(work, edition, read_artifacts=(record.content_sha256,))
    assert by_paper.key == by_edition.key == cite_key(record) == edition_cite_key(work, edition)


@pytest.mark.parametrize("path", ["cite", "cite_edition"])
def test_a_stored_key_minted_under_the_catalog_identity_is_kept(tmp_path: Path, path: str):
    """A key already in a store is never minted again, whatever rule minted it.

    Before arXiv editions were keyed by their arXiv identity, `cite_edition`
    keyed one by its catalog identity. A store written then must read back,
    and be cited again through either path, under the key it holds.
    """
    record = _perelman()
    work, edition = catalog_records(record)
    stem = cite_key(record).rsplit("-", 1)[0]
    stored = f"{stem}-{hashlib.sha256(f'edition:{edition.id}'.encode()).hexdigest()[:10]}"
    assert stored != cite_key(record)
    entry = Entry(key=stored, identities=edition_identities(work, edition), title=work.title, authors=work.authors,
                  year="2002", arxiv_id=str(record.identifier), content_sha256=EPUB, work=work.id, edition=edition.id,
                  edition_label=edition.label, publisher=edition.venue, read_artifacts=(EPUB,))
    (tmp_path / "bibliography.json").write_text(Store(entries=(entry,)).model_dump_json(indent=2) + "\n", encoding="utf-8")
    bibliography = Bibliography(tmp_path)
    assert [held.key for held in bibliography.entries()] == [stored]
    if path == "cite":
        again, added = bibliography.cite(record)
    else:
        again, added = bibliography.cite_edition(work, edition, read_artifacts=(PDF,))
    assert not added and again.key == stored and len(bibliography.entries()) == 1


def test_a_preprint_does_not_merge_into_a_published_edition_sharing_its_doi(tmp_path: Path):
    """A DOI is an alias for discovery on both paths, never the identity read.

    The journal edition and the arXiv preprint share a DOI and are different
    texts; handing back the journal entry for the preprint would record bytes
    the reader never saw under a description of a version they did not read.
    """
    journal = EditionOrVersion(id="edition-perelman-journal", work="work-perelman", label="Journal version", year="2003",
                               identifiers=(("doi", "10.1/x"),))
    work = BibliographicWork(id="work-perelman", kind=WorkKind.PAPER, title="The entropy formula", authors=("Grigori Perelman",))
    bibliography = Bibliography(tmp_path)
    published, _ = bibliography.cite_edition(work, journal, read_artifacts=(PDF,))
    preprint, added = bibliography.cite(_perelman().model_copy(update={"doi": "10.1/x"}))
    assert added and preprint.key != published.key and len(bibliography.entries()) == 2


@pytest.mark.parametrize("alias", ["2401.12345", "not-an-arxiv-id"])
def test_ambiguous_arxiv_alias_does_not_merge_distinct_editions(tmp_path, alias):
    bibliography = Bibliography(tmp_path)
    first_edition = GTM52.model_copy(update={"identifiers": (("arxiv", alias),)})
    second_edition = REPRINT.model_copy(update={"identifiers": (("arxiv", alias),)})
    first, _ = bibliography.cite_edition(HARTSHORNE, first_edition, read_artifacts=(PDF,))
    second, added = bibliography.cite_edition(HARTSHORNE, second_edition, read_artifacts=(EPUB,))
    assert added and len(bibliography.entries()) == 2
    assert second.key != first.key and second.edition == second_edition.id
    assert bibliography.entries()[0] == first


def test_versioned_arxiv_subject_aliases_match_canonical_identity(tmp_path):
    bibliography = Bibliography(tmp_path)
    first_edition = GTM52.model_copy(update={"identifiers": (("arxiv", "math.DG/0211159v1"),)})
    second_edition = REPRINT.model_copy(update={"identifiers": (("arxiv", "math/0211159v1"),)})
    first, _ = bibliography.cite_edition(HARTSHORNE, first_edition, read_artifacts=(PDF,))
    second, added = bibliography.cite_edition(HARTSHORNE, second_edition, read_artifacts=(EPUB,))
    assert not added and second.key == first.key and len(bibliography.entries()) == 1
    assert second.edition == first_edition.id and second.edition_label == first_edition.label


def test_ambiguous_catalog_and_arxiv_matches_refuse_without_mutation(tmp_path):
    bibliography = Bibliography(tmp_path)
    catalog = DONAGI_V1.model_copy(update={'identifiers': ()})
    bibliography.cite_edition(DONAGI, catalog, read_artifacts=(PDF,))
    bibliography.cite_edition(DONAGI, DONAGI_V2, read_artifacts=(EPUB,))
    before = bibliography.read()
    corrected = catalog.model_copy(update={'identifiers': DONAGI_V2.identifiers})
    with pytest.raises(BibliographyError, match='several.*entries'):
        bibliography.cite_edition(DONAGI, corrected, read_artifacts=(SCAN,))
    assert bibliography.read() == before
