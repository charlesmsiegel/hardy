"""One bibliography, one writer, and keys that do not move.

A duplicate entry or a key that changes between runs breaks every citation in
an already-compiled document, and neither announces itself: the PDF still
builds, it just points somewhere else. So the properties are asserted directly
-- same paper twice is one entry, same paper in two orders is the same key.
"""

from __future__ import annotations

from pathlib import Path

from hardy.arxiv import PaperRecord, digest
from hardy.bibliography import Bibliography, base_key


def _record(
    arxiv_id: str = "math.DG/0211159v1",
    title: str = "The entropy formula for the Ricci flow",
    authors: tuple[str, ...] = ("Grigori Perelman",),
    doi: str | None = None,
    published: str = "2002-11-11T18:00:00Z",
) -> PaperRecord:
    record = PaperRecord(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract="A monotonic expression for the Ricci flow.",
        published=published,
        updated=published,
        doi=doi,
        abs_url=f"https://arxiv.org/abs/{arxiv_id}",
    )
    return record.model_copy(update={"content_sha256": digest(record.content())})


def test_a_key_is_author_year_and_title_word():
    assert base_key(_record()) == "perelman2002entropy"


def test_a_surname_written_either_way_round_gives_one_key():
    assert base_key(_record(authors=("Perelman, Grigori",))) == "perelman2002entropy"


def test_a_key_ignores_a_leading_stopword():
    assert base_key(_record(title="On the classification of finite groups")).endswith(
        "classification"
    )


def test_the_year_never_comes_from_the_identifier():
    """`2401` in `2401.12345` is a year and a month glued together."""
    assert base_key(_record(arxiv_id="2401.12345v1", published="")) == "perelmanentropy"


def test_citing_writes_the_store_and_the_generated_file(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    entry, added = bibliography.cite(_record())
    assert added
    assert entry.key == "perelman2002entropy"
    assert (tmp_path / "bibliography.json").is_file()
    rendered = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "\\begin{thebibliography}" in rendered
    assert "\\bibitem{perelman2002entropy}" in rendered


def test_the_same_paper_twice_is_one_entry(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    first, added_first = bibliography.cite(_record())
    second, added_second = bibliography.cite(_record())
    assert added_first and not added_second
    assert first.key == second.key
    assert len(bibliography.entries()) == 1


def test_a_paper_reached_by_two_routes_is_one_entry(tmp_path: Path):
    """The same paper met once without a DOI and once with one."""
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record())
    second, added = bibliography.cite(_record(doi="10.1090/s0002"))
    assert not added
    assert second.key == first.key
    assert len(bibliography.entries()) == 1
    assert "doi:10.1090/s0002" in second.identities


def test_two_different_papers_never_share_a_key(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record(arxiv_id="math.DG/0211159v1"))
    second, added = bibliography.cite(_record(arxiv_id="math.DG/0303109v1"))
    assert added
    assert second.key != first.key
    assert second.key.startswith(first.key)
    assert len(bibliography.entries()) == 2


def test_a_collision_suffix_does_not_depend_on_the_order_they_arrived(tmp_path: Path):
    """A counter would renumber the moment two runs cited in another order.

    The first to claim the base key keeps it -- an already-compiled `\\cite`
    depends on that -- and the other's suffix comes from its own identity, so
    it is the same suffix whichever order they were cited in.
    """
    one = _record(arxiv_id="math.DG/0211159v1")
    two = _record(arxiv_id="math.DG/0303109v1")
    forwards = Bibliography(tmp_path / "a")
    first_in = forwards.cite(one)[0]
    displaced = forwards.cite(two)[0]
    backwards = Bibliography(tmp_path / "b")
    assert backwards.cite(two)[0].key == first_in.key
    # Whoever arrives second is displaced, and the two are displaced onto
    # different keys: the suffix is a function of the paper, not of the slot.
    assert backwards.cite(one)[0].key != displaced.key
    # And the same paper displaced again lands on the same key it did before.
    third = Bibliography(tmp_path / "c")
    third.cite(one)
    assert third.cite(two)[0].key == displaced.key


def test_an_existing_entry_keeps_its_key_when_it_is_cited_again(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record())
    stored = (tmp_path / "bibliography.json").read_text(encoding="utf-8")
    bibliography.cite(_record(doi="10.1000/x"))
    assert bibliography.entries()[0].key == first.key
    assert first.key in stored


def test_a_title_full_of_tex_specials_does_not_break_the_document(tmp_path: Path):
    """The title is an author's, arriving from arXiv. Nobody proof-reads it."""
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(title="100% of $R\\&D$ in {the} #1 case_study ~x^2"))
    rendered = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "100\\%" in rendered
    assert "\\&" in rendered
    assert "\\textbackslash{}" in rendered
    # The escape for a backslash must not itself have been escaped.
    assert "\\textbackslash\\{" not in rendered


def test_an_empty_bibliography_renders_something_that_compiles(tmp_path: Path):
    """An empty `thebibliography` is a LaTeX error.

    A writeup that already `\\input`s the file must not start failing the
    moment its last citation is dropped.
    """
    assert "\\begin{thebibliography}" not in Bibliography(tmp_path).render()


def test_the_generated_file_is_rebuilt_whole_rather_than_appended(tmp_path: Path):
    """Hand edits to the generated file are undone, not merged."""
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record())
    generated = tmp_path / "tex" / "references.tex"
    generated.write_text("\\bibitem{invented2020} Nobody.\n", encoding="utf-8")
    bibliography.cite(_record(arxiv_id="2401.00001v1", title="Another thing"))
    rendered = generated.read_text(encoding="utf-8")
    assert "invented2020" not in rendered
    assert rendered.count("\\bibitem") == 2


def test_a_second_bibliography_object_sees_the_first_ones_writes(tmp_path: Path):
    """Read back per call, so two sessions converge instead of clobbering."""
    Bibliography(tmp_path).cite(_record())
    other = Bibliography(tmp_path)
    other.cite(_record(arxiv_id="2401.00001v1", title="Another thing"))
    assert len(Bibliography(tmp_path).entries()) == 2


def test_an_entry_carries_the_digest_of_what_was_read(tmp_path: Path):
    """The paper library is machine-local; this file is not."""
    record = _record()
    entry, _ = Bibliography(tmp_path).cite(record)
    assert entry.content_sha256 == record.content_sha256


def test_a_paper_can_be_found_by_either_of_its_identities(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(doi="10.1090/S0002"))
    assert bibliography.find("arxiv:math.DG/0211159v1") is not None
    assert bibliography.find("doi:10.1090/s0002") is not None
    assert bibliography.find("arxiv:2401.00001v1") is None
