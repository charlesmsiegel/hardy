"""A source correspondence never certifies unchanged mathematics."""
import hashlib

import pytest

from hardy.literature.diff import compare_sources, map_span
from hardy.literature.manuscript import SourceSpan


def _span(text, fragment, path="paper.tex"):
    start = text.index(fragment)
    return SourceSpan(path, hashlib.sha256(text.encode()).hexdigest(), start, start + len(fragment))


def test_inserted_preamble_moves_exact_statement_without_equating_whole_sources():
    before = {"paper.tex": "Header\nThe statement.\nProof."}
    after = {"paper.tex": "New notation\n" + before["paper.tex"]}
    diff = compare_sources(before, after)
    mapped = map_span(diff, _span(before["paper.tex"], "The statement."))
    assert mapped.status == "exact_text"
    assert mapped.after.start > mapped.before.start
    assert mapped.after.digest != mapped.before.digest
    assert diff.changed_files == ("paper.tex",)
    assert mapped.semantic_equivalence is False


def test_changed_hypothesis_and_deleted_statement_remain_unmatched():
    diff = compare_sources({"paper.tex": "If x >= 0, P.\nDeleted theorem."},
                           {"paper.tex": "If x > 0, P."})
    assert map_span(diff, _span(diff.before["paper.tex"], "If x >= 0, P.")).status == "unmatched"
    assert map_span(diff, _span(diff.before["paper.tex"], "Deleted theorem.")).after is None


def test_multiple_equal_texts_need_explicit_mapping():
    diff = compare_sources({"paper.tex": "Claim."}, {"paper.tex": "Claim.\nClaim."})
    mapped = map_span(diff, _span("Claim.", "Claim."))
    assert mapped.status == "ambiguous" and mapped.after is None
    assert len(mapped.candidates) == 2


def test_unique_moved_statement_can_be_found_in_a_renamed_file():
    diff = compare_sources({"old.tex": "A theorem."}, {"new.tex": "Intro.\nA theorem."})
    mapped = map_span(diff, _span("A theorem.", "A theorem.", "old.tex"))
    assert mapped.after.path == "new.tex"
    assert diff.added_files == ("new.tex",) and diff.removed_files == ("old.tex",)


def test_stale_span_and_mutation_of_input_are_not_silently_accepted():
    before = {"paper.tex": "α = α"}
    diff = compare_sources(before, before)
    before["paper.tex"] = "changed"
    assert map_span(diff, _span("α = α", "α = α")).after.end == 5
    with pytest.raises(ValueError, match="source"):
        map_span(diff, _span("wrong", "wrong"))


def test_source_size_is_bounded_before_inventory():
    with pytest.raises(ValueError, match="limit"):
        compare_sources({"a.tex": "abc"}, {}, max_bytes=2)
