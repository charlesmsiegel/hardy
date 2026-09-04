"""One bibliography, one writer, and keys that do not move.

A duplicate entry or a key that changes between runs breaks every citation in
an already-compiled document, and neither announces itself: the PDF still
builds, it just points somewhere else. So the properties are asserted directly
-- same paper twice is one entry, same paper in two orders is the same key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from hardy.arxiv import PaperRecord, digest
from hardy.bibliography import (
    Bibliography,
    BibliographyError,
    Entry,
    base_key,
    cite_key,
    hand_written_bibliography,
    is_generated,
)
from hardy.latex import typeset
from hardy.storage import FileLock


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


def test_a_key_reads_as_author_year_and_title_word():
    assert cite_key(_record()).startswith("perelman2002entropy-")


def test_a_surname_written_either_way_round_gives_one_key():
    assert cite_key(_record(authors=("Perelman, Grigori",))) == cite_key(_record())


def test_a_key_is_a_function_of_the_paper_and_nothing_else():
    """No store, no order, no collision handling.

    Two runs, two workspaces, either order, anything else cited beside it:
    the same paper is the same key.
    """
    assert cite_key(_record()) == cite_key(_record())


def test_a_key_ignores_a_leading_stopword():
    assert "classification" in base_key(_record(title="On the classification of finite groups"))


def test_the_year_never_comes_from_the_identifier():
    """`2401` in `2401.12345` is a year and a month glued together."""
    assert base_key(_record(arxiv_id="2401.12345v1", published="")) == "perelmanentropy"


def test_citing_writes_the_store_and_the_generated_file(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    entry, added = bibliography.cite(_record())
    assert added
    assert entry.key == cite_key(_record())
    assert (tmp_path / "bibliography.json").is_file()
    rendered = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "\\begin{thebibliography}" in rendered
    assert f"\\bibitem{{{entry.key}}}" in rendered


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


def test_two_papers_wanting_one_readable_key_still_get_two_keys(tmp_path: Path):
    """Same first author, same year, same first title word."""
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record(arxiv_id="math.DG/0211159v1"))
    second, added = bibliography.cite(_record(arxiv_id="math.DG/0303109v1"))
    assert added
    assert second.key != first.key
    assert base_key(_record()) in first.key
    assert base_key(_record()) in second.key
    assert len(bibliography.entries()) == 2


def test_neither_key_depends_on_which_was_cited_first(tmp_path: Path):
    """The guarantee the digest in the key buys.

    Before it, the first of two colliding papers took the readable key and
    the second took a suffix -- so citing the same pair in the opposite order
    in a fresh workspace changed BOTH keys, and a document carrying one of
    them stopped resolving.
    """
    one = _record(arxiv_id="math.DG/0211159v1")
    two = _record(arxiv_id="math.DG/0303109v1")
    forwards = Bibliography(tmp_path / "a")
    forwards_keys = (forwards.cite(one)[0].key, forwards.cite(two)[0].key)
    backwards = Bibliography(tmp_path / "b")
    backwards_two = backwards.cite(two)[0].key
    backwards_one = backwards.cite(one)[0].key
    assert (backwards_one, backwards_two) == forwards_keys


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


def test_two_versions_sharing_a_doi_are_two_entries(tmp_path: Path):
    """The DOI is minted for the paper, not for the version that was read.

    Matching on it alone handed back a cite key whose entry still described
    v1 -- v1's metadata, v1's digest -- for a paper the reader had read at
    v2. The bibliography would then be saying something false about which
    bytes the citation was made against, which is the one thing it is for.
    """
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record(arxiv_id="2401.00001v1", doi="10.1/x"))
    second, added = bibliography.cite(_record(arxiv_id="2401.00001v2", doi="10.1/x"))
    assert added
    assert second.key != first.key
    assert second.arxiv_id == "2401.00001v2"
    assert first.arxiv_id == "2401.00001v1"


def test_a_doi_still_merges_an_entry_that_names_no_arxiv_version(tmp_path: Path):
    """The case the DOI match is actually for."""
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record())
    second, added = bibliography.cite(_record(doi="10.1/x"))
    assert not added
    assert second.key == first.key


def test_a_hand_written_bibitem_is_refused(tmp_path: Path):
    """The promise is about what a reader sees, not about the store.

    `cite_paper` cannot be talked into an invented reference, but
    `save_latex` takes arbitrary LaTeX -- and a `\\bibitem{invented2020}`
    written straight into the writeup resolves, compiles, and is published
    with nothing behind it.
    """
    refusal = hand_written_bibliography(
        "writeup.tex", "\\begin{thebibliography}{9}\n\\bibitem{invented2020} Nobody.\n"
    )
    assert refusal
    assert "cite_paper" in refusal


def test_every_way_of_declaring_a_reference_is_refused():
    for command in (
        "\\bibitem{x} A.",
        "\\bibliography{refs}",
        "\\addbibresource{refs.bib}",
        "\\printbibliography",
        "\\nocite{*}",
        "\\begin{thebibliography}{9}",
    ):
        assert hand_written_bibliography("writeup.tex", command), command


def test_a_bibitem_inside_a_comment_is_not_a_reference():
    """TeX never reads it, so refusing the document over it refuses nothing."""
    assert not hand_written_bibliography("writeup.tex", "% \\bibitem{x} an example\nText.\n")


def test_the_generated_file_may_not_be_written_by_hand():
    refusal = hand_written_bibliography("references.tex", "Anything at all.\n")
    assert "regenerated" in refusal
    assert not hand_written_bibliography("sections/one.tex", "Anything at all.\n")


def test_a_citation_needs_the_lock_it_cannot_get(tmp_path: Path):
    """A lost citation is silent, so the refusal must not be."""
    bibliography = Bibliography(tmp_path, lock_timeout=0.2)
    with (
        FileLock(tmp_path / ".local" / "bibliography.lock"),
        pytest.raises(BibliographyError, match="another session"),
    ):
        bibliography.cite(_record(), now=None)


def test_a_lock_file_nobody_holds_does_not_refuse_a_citation(tmp_path: Path):
    """The file outlives its holder; the lock does not.

    A session killed mid-citation leaves the file behind, and under the
    earlier design the next citation had to wait out a staleness window
    before it could take it -- or, with a clock stepped backwards, never.
    """
    lock = tmp_path / ".local" / "bibliography.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("999999", encoding="utf-8")
    assert Bibliography(tmp_path, lock_timeout=0.2).cite(_record())[1]


def test_the_lock_is_released_when_a_citation_finishes(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record())
    # Released by the kernel, not by deleting the file: what proves it is that
    # the next citation gets the lock, not that the path is gone.
    assert bibliography.cite(_record(arxiv_id="2401.00002v1"))[1]
    # And it lives in `.local/`, which is machine-local and ignored, so the
    # file it does leave behind is never committed.
    assert (tmp_path / ".local" / "bibliography.lock").exists()
    assert not (tmp_path / "bibliography.lock").exists()


def test_a_symlinked_store_is_refused_rather_than_followed(tmp_path: Path):
    """A store read from outside would make the record machine-dependent.

    The next citation merges whatever it found into the project's own
    reference list, so the bibliography -- and the source identities in it --
    would differ by whoever opened the clone.
    """
    outside = tmp_path / "outside.json"
    outside.write_text('{"schema_version": 1, "entries": []}', encoding="utf-8")
    problem = tmp_path / "problem"
    problem.mkdir()
    (problem / "bibliography.json").symlink_to(outside)
    with pytest.raises(BibliographyError):
        Bibliography(problem).read()


def test_a_paper_read_twice_as_different_bytes_records_both(tmp_path: Path):
    """A clone with the bibliography but not the machine-local library.

    The paper is fetched again and arXiv's metadata, an intermediary, or the
    parser has moved. Keeping only the first digest would leave the entry
    identifying bytes the second reader did not see; refusing would call one
    of two honest reads false.
    """
    bibliography = Bibliography(tmp_path)
    first, _ = bibliography.cite(_record())
    moved = _record(title="The entropy formula for the Ricci flow (revised)")
    moved = moved.model_copy(update={"arxiv_id": _record().arxiv_id})
    second, added = bibliography.cite(moved)
    assert not added
    assert second.content_sha256 == first.content_sha256
    assert second.also_read == (moved.content_sha256,)


def test_building_a_control_sequence_by_name_is_refused():
    r"""`\csname bibitem\endcsname` is a `\bibitem` nobody can see."""
    for command in ("\\csname bibitem\\endcsname{x}", "\\expandafter\\def", "\\@namedef{x}"):
        refusal = hand_written_bibliography("writeup.tex", command)
        assert refusal, command
        assert "control sequences by name" in refusal


def test_citing_a_paper_already_present_still_regenerates_the_file(tmp_path: Path):
    """The generated list is derived state a clone or a merge can lose.

    An early return that skipped the write left a missing or hand-edited
    `references.tex` missing or edited after `cite_paper` reported success --
    and there may be no new paper to cite merely to bring it back.
    """
    bibliography = Bibliography(tmp_path)
    entry, _ = bibliography.cite(_record())
    generated = tmp_path / "tex" / "references.tex"
    generated.unlink()
    again, added = bibliography.cite(_record())
    assert not added
    assert again.key == entry.key
    assert f"\\bibitem{{{entry.key}}}" in generated.read_text(encoding="utf-8")


def test_a_key_stays_short_enough_for_tex(tmp_path: Path):
    """Both halves of the readable stem are arXiv's text, not Hardy's."""
    key = cite_key(_record(authors=("A" * 5_000,), title="B" * 5_000))
    assert len(key) < 100
    # Two papers whose readable stems are both cut to the same characters are
    # still two keys: the digest half is what separates them.
    other = cite_key(
        _record(arxiv_id="2401.00001v1", authors=("A" * 5_000,), title="B" * 5_000)
    )
    assert other != key
    assert len(other) < 100


def test_a_cite_key_is_never_cut_across_two_lines(tmp_path: Path):
    r"""The one string the document `\cite`s has to be findable in the file."""
    bibliography = Bibliography(tmp_path)
    entry, _ = bibliography.cite(_record(authors=("A" * 300,), title="B" * 300))
    generated = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert f"\\bibitem{{{entry.key}}}\n" in generated


def test_a_generated_entry_is_folded_to_lines_tex_can_read(tmp_path: Path):
    """arXiv metadata has no bounded length; a TeX input buffer does.

    A paper with three thousand authors renders as one physical line of tens
    of thousands of characters, and `cite_paper` reports success on a
    `references.tex` no compiler will read -- in a file the model is
    forbidden to repair.
    """
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(authors=tuple(f"Author {n}" for n in range(3_000))))
    generated = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert len(generated) > 30_000
    assert max(len(line) for line in generated.splitlines()) <= 96


def test_folding_a_long_run_reassembles_to_exactly_what_it_was(tmp_path: Path):
    r"""A line ending is a space to TeX, and `Weier strass` is a wrong name.

    A run with nothing to break at is still cut -- it is the run that
    overruns the buffer -- so each cut carries the `%` that says this line
    ending is not a space, and what TeX reads back is the run itself.
    """
    bibliography = Bibliography(tmp_path)
    entry, _ = bibliography.cite(_record(title="W" * 400))
    lines = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8").splitlines()
    body = lines[lines.index("\\begin{thebibliography}{1}") + 2 : -1]
    # What TeX makes of it: a `%` eats its line ending, any other ending is a
    # space.
    read_back = ""
    for line in body:
        read_back += line[:-1] if line.endswith("%") else line + " "
    assert read_back.split() == entry.rendered().split()[1:]
    assert "W" * 400 in read_back


def test_a_line_break_never_falls_among_a_command_s_letters(tmp_path: Path):
    r"""`\textbackslash` cut in half is an undefined control sequence.

    The `%` that makes a cut invisible cannot help here: it ends the control
    word early, and the letters after it are read as text.
    """
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(title="\\" * 200, authors=("A" * 300,)))
    lines = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8").splitlines()
    for line, following in zip(lines, lines[1:], strict=False):
        if not line.endswith("%"):
            continue
        trailing = re.search(r"\\[a-zA-Z]*$", line[:-1])
        assert not (trailing and following[:1].isalpha()), (line, following)


def test_an_author_pdflatex_cannot_set_does_not_wedge_every_later_compile(
    tmp_path: Path,
):
    """The default interactive compiler stops on a character it has no map for.

    Left verbatim, one `cite_paper` makes every writeup fail to compile, and
    the generated file is the one file the model may not edit -- so there is
    no way out of it from inside the session.
    """
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(authors=("Григорий",)))
    generated = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert generated.isascii(), generated
    assert "[U+0413]" in generated


def test_an_accented_name_keeps_its_letters(tmp_path: Path):
    r"""A bibliography is read by a person: `Erd\H{o}s` is a name.

    Spelling every accent as a codepoint would be safe and unreadable, and
    pdfLaTeX sets the accent commands perfectly well.
    """
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(authors=("Paul Erdős", "Kurt Gödel", "Lars Hørmander")))
    generated = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "Erd\\H{o}s" in generated
    assert 'G\\"{o}del' in generated
    assert "H\\o{}rmander" in generated


def test_a_writeup_may_quote_the_commands_it_may_not_use():
    r"""A section explaining why references are generated has to be writable.

    TeX sets `\verb` and a `verbatim` block as literal text and creates no
    entry from either, so refusing the document over one is refusing text no
    compiler runs -- and the one document most likely to contain it is a
    writeup describing this very rule.
    """
    quoted = (
        "Hardy generates the reference list, so a \\verb|\\bibitem{x}| written\n"
        "by hand is refused.\n"
        "\\begin{verbatim}\n\\begin{thebibliography}{9}\n\\bibitem{x} No.\n"
        "\\end{thebibliography}\n\\end{verbatim}\n"
        "\\begin{lstlisting}\n\\bibliography{refs}\n\\end{lstlisting}\n"
    )
    assert hand_written_bibliography("writeup.tex", quoted) == ""


def test_quoting_one_bibitem_does_not_excuse_writing_another():
    r"""Only what is inside the verbatim is literal text."""
    refusal = hand_written_bibliography(
        "writeup.tex",
        "A \\verb|\\bibitem{quoted}| is text.\n\\bibitem{executed} But this is not.\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_only_the_generated_file_at_the_tree_root_is_reserved():
    """`references.tex` is a name a workspace is entitled to use elsewhere.

    Reserved on the basename, an ordinary `sections/references.tex` was
    Hardy's file: refused at the check, refused at the save, and undeletable.
    """
    assert is_generated("references.tex")
    assert not is_generated("sections/references.tex")
    assert not is_generated("appendix/references.tex")
    reserved = hand_written_bibliography("references.tex", "\\section{Notes}\n")
    assert "written by Hardy" in reserved
    assert hand_written_bibliography("sections/references.tex", "\\section{Notes}\n") == ""


def test_a_generated_file_that_drifted_from_the_store_is_rewritten(tmp_path: Path):
    r"""The key check covers keys, and nothing else about the entry.

    An edited or stale `tex/references.tex` keeping a vouched key while
    changing the authors or the title under it passed every gate: the key was
    recorded, and the source-level check exempts this path by name because
    Hardy writes it.
    """
    bibliography = Bibliography(tmp_path)
    entry, _ = bibliography.cite(_record())
    generated = tmp_path / "tex" / "references.tex"
    forged = generated.read_text(encoding="utf-8").replace(
        "Grigori Perelman", "Somebody Else"
    )
    generated.write_text(forged, encoding="utf-8")
    assert bibliography.regenerate() is True
    restored = generated.read_text(encoding="utf-8")
    assert "Grigori Perelman" in restored
    assert "Somebody Else" not in restored
    assert f"\\bibitem{{{entry.key}}}" in restored
    # And a file that already agrees is left alone, and says so.
    assert bibliography.regenerate() is False


def test_a_missing_generated_file_is_put_back(tmp_path: Path):
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record())
    generated = tmp_path / "tex" / "references.tex"
    generated.unlink()
    assert bibliography.regenerate() is True
    assert generated.is_file()


def test_alltt_is_not_verbatim():
    r"""It keeps line breaks; it does not stop TeX reading commands.

    `alltt` leaves the backslash and the braces active, so a `\bibitem`
    inside one is executed and lands in the compiler's own record. Exempting
    it turned a verbatim-looking block into a way to put a fabricated entry
    in front of a reader under a key `cite_paper` had already vouched for.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\begin{alltt}\n\\begin{thebibliography}{9}\n"
        "\\bibitem{perelman2002entropy-abcdef0123} Somebody Else.\n"
        "\\end{thebibliography}\n\\end{alltt}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_comparison_in_a_title_is_not_typeset_as_punctuation(tmp_path: Path):
    r"""Under OT1 a bare `<` sets as an inverted exclamation mark.

    So the entry compiled and showed the reader a different title -- silently
    wrong, which is what this escape table exists to prevent rather than
    merely avoiding errors.
    """
    bibliography = Bibliography(tmp_path)
    bibliography.cite(_record(title="Bounds for 0 < x < 1"))
    generated = (tmp_path / "tex" / "references.tex").read_text(encoding="utf-8")
    assert "\\textless{}" in generated
    assert "0 <" not in generated


def test_a_commented_verbatim_opener_cannot_hide_executable_source():
    r"""The order of comment-stripping and verbatim removal is the whole thing.

    `% \begin{verbatim}`, a real `thebibliography`, then `% \end{verbatim}`:
    TeX runs every line of that, and removing verbatim regions first cut it
    out whole. An earlier round called that "the right way round to be wrong"
    because the compiler's `\bibcite` record was the real check -- but that
    record covers the KEYS, so a forged entry reusing a vouched key passed
    both.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "Text.\n% \\begin{verbatim}\n\\begin{thebibliography}{9}\n"
        "\\bibitem{perelman2002entropy-abcdef0123} Somebody Else.\n"
        "\\end{thebibliography}\n% \\end{verbatim}\nMore.\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_percent_inside_verbatim_does_not_swallow_its_closer():
    r"""`%` is an ordinary character in there, so the region ends where it says.

    Stripping comments inside a verbatim region could eat the `\end`, leaving
    it open to the end of the file and hiding every command after it.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\begin{verbatim}\n50% of \\end{verbatim}\n\\bibitem{executed2020} Nobody.\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_percent_as_a_verb_delimiter_does_not_hide_what_follows():
    r"""`%` is a legal `\verb` delimiter, and TeX closes at the second one.

    Stripping comments before finding `\verb` spans read the OPENING
    delimiter as a comment and dropped the rest of the line -- so a
    `thebibliography` written after it vanished from the check while the
    compiler executed it.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\verb%x%\\begin{thebibliography}{1}\\bibitem{known2020} Fake."
        "\\end{thebibliography}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_verb_delimited_by_percent_is_still_verbatim():
    r"""And the other direction: the span itself is text, not commands."""
    assert hand_written_bibliography("writeup.tex", "Show \\verb%\\bibitem{x}% inline.\n") == ""


def test_a_verb_written_inside_a_comment_is_not_a_verb():
    r"""Whichever comes first in the line wins, which is what TeX does."""
    refusal = hand_written_bibliography(
        "writeup.tex", "% \\verb|a|\n\\bibitem{y2020} No.\n"
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_an_escaped_backslash_does_not_open_a_verbatim_region():
    r"""TeX reads `\\begin{verbatim}` as `\\` and then the word "begin".

    Searching for the opener in text the scan had already cleaned found one
    starting at the SECOND backslash, opening a region TeX never opens -- so
    a real bibliography on the following lines was removed from inspection
    while the compiler executed it.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "Text.\\\\begin{verbatim}\n\\bibitem{known2020} Fake.\n% \\end{verbatim}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_macro_body_cannot_open_a_verbatim_region():
    r"""A definition that merely stores an opener does not open anything.

    `\newcommand{\x}{\begin{verbatim}}` runs nothing until `\x` is expanded,
    but the scan is stateful -- so meeting one put it into verbatim mode and
    suppressed every line until a closer, which a commented `\end{verbatim}`
    supplies quite happily. A real `thebibliography` in between was never
    inspected.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\newcommand{\\x}{\\begin{verbatim}}\n"
        "\\begin{thebibliography}{1}\n\\bibitem{known2020} Fake.\n"
        "\\end{thebibliography}\n% \\end{verbatim}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_definition_printed_inside_verbatim_does_not_open_a_body():
    r"""The other half of the same decision, and it used to be lost.

    The macro bodies were found by a pass over the raw source, run BEFORE
    anything knew where the verbatim regions were. So a document that PRINTS
    a definition -- a paper about LaTeX, showing `\newcommand{\x}{` in a
    `verbatim` block -- opened a body there, and the closing brace was hunted
    for in live source: the block's own `\end{verbatim}` fell inside that
    span and was blanked, the region never closed, and everything after it
    dropped out of the check, a hand-written bibliography included.

    Neither pass could run first, because each needed the other's answer.
    Both are decided in one left-to-right scan now, in the order TeX meets
    them, which is what the comment and `\verb` handling already needed.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "To define one, write:\n"
        "\\begin{verbatim}\n\\newcommand{\\x}{\n\\end{verbatim}\n"
        "\\begin{thebibliography}{1}\n\\bibitem{known2020} Fake.\n"
        "\\end{thebibliography}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_bibitem_written_into_a_macro_body_is_still_caught():
    r"""Only the delimiters are defused; the body is still read.

    A `\bibitem` in a macro body is one this check means to catch -- the same
    reason `\csname` is refused by rule rather than chased through expansion.
    """
    refusal = hand_written_bibliography(
        "writeup.tex", "\\newcommand{\\y}{\\bibitem{z2020} Nobody.}\n\\y\n"
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_comment_swallows_its_own_line_ending():
    r"""TeX discards a comment AND the line ending it sits on.

    So `\begin{thebibliogr%` followed by `aphy}` is one
    `\begin{thebibliography}` that the compiler runs. The scan stopped at the
    `%` and rejoined the pieces with a newline, putting a break where TeX has
    none -- and a bibliography command split across a comment executed while
    this check saw two innocent fragments and refused nothing. Reusing a
    vouched key then carries fabricated metadata past the auxiliary check too.
    """
    for source in (
        "\\begin{thebibliogr%hidden\naphy}{9}\n\\bibitem{a} A.\n\\end{thebibliography}\n",
        "\\bib%hidden\nitem{known2020} Nobody.\n",
    ):
        refusal = hand_written_bibliography("writeup.tex", source)
        assert refusal, source
        assert "writes its own bibliography" in refusal


def test_an_ordinary_comment_still_joins_its_line_to_the_next():
    r"""The same rule, where it is not hiding anything.

    `Text. % note` then `More text.` is one line to TeX, and this is what
    says the join is TeX's rule rather than a special case for `\bibitem`.
    """
    assert typeset("Text. % note\nMore.\n") == "Text. More."


def test_a_gdef_body_cannot_open_a_verbatim_region():
    r"""`\gdef` is `\def` for this purpose, and so are `\xdef` and `\edef`.

    All four store a body and run none of it where it is written, but only
    `\def` was recognised -- so `\gdef\x{\begin{verbatim}}` put the scan into
    verbatim mode and hid a real `thebibliography` after it until a commented
    closer. The prefixed forms need no clause of their own: `\global\def` is
    two control sequences and the scan meets the second at its own backslash.
    """
    for definition in (
        "\\gdef\\x",
        "\\xdef\\x",
        "\\edef\\x",
        "\\global\\def\\x",
        "\\long\\gdef\\x",
    ):
        refusal = hand_written_bibliography(
            "writeup.tex",
            f"{definition}{{\\begin{{verbatim}}}}\n"
            "\\begin{thebibliography}{1}\n\\bibitem{known2020} Fake.\n"
            "\\end{thebibliography}\n% \\end{verbatim}\n",
        )
        assert refusal, definition
        assert "writes its own bibliography" in refusal


def test_an_entry_with_no_digest_is_not_a_citation():
    r"""The digest is the whole reason the entry travels.

    The paper library is machine-local and `bibliography.json` is not, so an
    entry with no `content_sha256` names bytes nobody can check -- and it
    defaulted to the empty string, so `regenerate` still published the
    `\bibitem` and `_vouched_references` still accepted its key as a paper
    somebody had read.
    """
    fields = {
        "key": "nobody2020-0123456789",
        "identities": ("arxiv:2401.00001v1",),
        "title": "A paper",
        "authors": ("Nobody",),
    }
    with pytest.raises(ValidationError):
        Entry(**fields)
    with pytest.raises(ValidationError):
        Entry(**fields, content_sha256="")
    with pytest.raises(ValidationError):
        Entry(**fields, content_sha256="not-a-digest")
    # And a real one still loads.
    assert Entry(**fields, content_sha256="a" * 64).content_sha256 == "a" * 64


def test_a_let_binding_cannot_open_a_verbatim_region():
    r"""`\let` hands a token over without running it.

    `\let\x=\begin{verbatim}` gives `\x` the meaning of `\begin` and typesets
    `{verbatim}` as ordinary text -- nothing opens. The scan read a live
    opener and put every line after it into a region TeX never entered, which
    a commented `\end{verbatim}` then closed, taking a real `thebibliography`
    out of the check.
    """
    for binding in ("\\let\\x=\\begin{verbatim}", "\\let\\x\\begin{verbatim}"):
        refusal = hand_written_bibliography(
            "writeup.tex",
            f"{binding}\n"
            "\\begin{thebibliography}{1}\n\\bibitem{known2020} Fake.\n"
            "\\end{thebibliography}\n% \\end{verbatim}\n",
        )
        assert refusal, binding
        assert "writes its own bibliography" in refusal


def test_a_real_verbatim_block_after_a_let_still_opens():
    r"""And the swallowing stops after the two tokens `\let` takes.

    Otherwise the fix would be the dangerous direction of the same mistake:
    a `\bibitem` printed inside a genuine `verbatim` block read as executable.
    """
    assert not hand_written_bibliography(
        "writeup.tex",
        "\\let\\x=\\relax\n"
        "\\begin{verbatim}\n\\bibitem{ghost2020} Printed, not run.\n\\end{verbatim}\n",
    )


def test_a_let_that_redefines_bibitem_is_still_visible():
    r"""Only the meaning `\let` copies is dormant; the name is not.

    `\let\bibitem\wrapper` is a document rewriting `\bibitem` and saying so in
    as many words. Erasing both operands took that out of the source-level
    check entirely -- a wrapper can then emit the auxiliary record through the
    saved original while replacing the entry a reader sees, and the key the
    compiler writes is one that was vouched for.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\let\\originalbibitem\\bibitem\n"
        "\\def\\wrapper#1{Fabricated.}\n"
        "\\let\\bibitem\\wrapper\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_bracketed_default_is_not_the_macro_body():
    r"""`\newcommand{\x}[1][{default}]{...}` has a brace group in its OPTIONAL
    argument, and the body is the one after it.

    Taking the first `{` anywhere as the body opener made `{default}` the
    body: it closed, the count reached zero, and the real body became live
    text -- so a dormant `\begin{verbatim}` in it was honoured and hid a real
    `thebibliography` until a commented closer.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\newcommand{\\x}[1][{default}]{\\begin{verbatim}}\n"
        "\\begin{thebibliography}{1}\n\\bibitem{known2020} Fake.\n"
        "\\end{thebibliography}\n% \\end{verbatim}\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_an_entry_with_nothing_in_it_is_not_a_citation():
    r"""A digest says the entry names some bytes, not that it is readable.

    `title=""`, `authors=()` and `identities=()` with a well-formed digest was
    schema-valid, so a cloned store carrying one had `regenerate` publish a
    blank `\bibitem` and `_vouched_references` accept its key as a paper Hardy
    had recorded. Required here because it is required at admission.
    """
    whole = {
        "key": "nobody2020-0123456789",
        "identities": ("arxiv:2401.00001v1",),
        "title": "A paper",
        "authors": ("Nobody",),
        "content_sha256": "a" * 64,
    }
    assert Entry(**whole).title == "A paper"
    for field, empty in (("title", ""), ("authors", ()), ("identities", ())):
        with pytest.raises(ValidationError):
            Entry(**{**whole, field: empty})


def test_a_let_alias_of_a_forbidden_command_is_still_visible():
    r"""Erasing the copied meaning hid every alias of what this scan rejects.

    `\let\start\thebibliography`, `\let\entry\bibitem`,
    `\let\stop\endthebibliography` and then those three aliases writes a real
    reference list with none of the forbidden names left in the executed text.
    What `\let` actually does is copy a token WITHOUT running it, which is
    exactly one thing -- it cannot open a region -- so the text stays and only
    the opener match is skipped.
    """
    refusal = hand_written_bibliography(
        "writeup.tex",
        "\\let\\start\\thebibliography\n"
        "\\let\\entry\\bibitem\n"
        "\\let\\stop\\endthebibliography\n"
        "\\start{1}\\entry{known2020} Fake.\\stop\n",
    )
    assert refusal
    assert "writes its own bibliography" in refusal


def test_a_definition_left_open_at_the_end_of_a_file_is_refused():
    r"""A file is not a complete TeX input.

    `\newcommand{\x}` at the end of one takes its body from whatever includes
    it, and this sweep reads each file from a standing start -- so the body
    reads as live text, a `\begin{verbatim}` in it opens a region TeX never
    enters, and a real `thebibliography` after it drops out of the check.

    Answered rather than chased: following the body across the `\input` means
    scanning in TeX's inclusion order, which has no answer at all for a file
    nothing includes.
    """
    for opener in ("\\newcommand{\\x}", "\\def\\x", "\\newenvironment{e}{a}", "\\let\\x="):
        refusal = hand_written_bibliography("writeup.tex", f"Text.\n{opener}\n")
        assert refusal, opener
        assert "no body yet" in refusal
    # And a definition that finishes in its own file is nobody's business.
    assert not hand_written_bibliography("writeup.tex", "\\newcommand{\\x}{body}\n")


def test_a_blank_string_inside_a_required_tuple_is_not_metadata():
    r"""`authors=[""]` is not an empty tuple, and is just as blank.

    The first version of this validator asked only whether the tuple itself
    was empty, so an entry with one empty author, one empty identity, or a
    whitespace-only title still loaded, regenerated as a `\bibitem` with
    nothing readable in it, and had its key vouched for.
    """
    whole = {
        "key": "nobody2020-0123456789",
        "identities": ("arxiv:2401.00001v1",),
        "title": "A paper",
        "authors": ("Nobody",),
        "content_sha256": "a" * 64,
    }
    for field, blank in (
        ("title", "   "),
        ("authors", ("",)),
        ("authors", ("Nobody", " ")),
        ("identities", ("",)),
    ):
        with pytest.raises(ValidationError):
            Entry(**{**whole, field: blank})
