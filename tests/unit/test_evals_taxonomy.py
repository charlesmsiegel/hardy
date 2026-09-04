"""MSC lookups: names, roll-ups, reporting groups, and the arXiv derivation."""
from __future__ import annotations

import json

import pytest

from hardy.evals import taxonomy
from hardy.evals.taxonomy import (
    UnknownCode,
    arxiv_classes,
    arxiv_of,
    field_of,
    group_of,
    is_known,
    name_of,
)


def test_a_full_code_has_a_name_a_field_and_a_group():
    assert name_of("13A15") == "Ideals and multiplicative ideal theory in commutative rings"
    assert field_of("13A15") == "Commutative algebra"
    assert group_of("13A15") == "commutative-algebra"
    assert arxiv_of("13A15") == "math.AC"


def test_twentysix_and_twentyeight_roll_up_to_one_reporting_group():
    """The reason `group_of` exists apart from `field_of` (spec §5)."""
    assert field_of("26Axx") != field_of("28Axx")
    assert group_of("26Axx") == group_of("28Axx") == "analysis"


def test_an_unknown_code_raises_rather_than_guessing():
    assert not is_known("99Z99")
    with pytest.raises(UnknownCode):
        name_of("99Z99")


def test_the_arxiv_codomain_is_what_an_override_may_name():
    classes = arxiv_classes()
    assert "math.AC" in classes and "math.NT" in classes
    assert "math.AC " not in classes


def test_an_invented_tail_is_not_rolled_up_as_if_it_were_its_class():
    """`13ZZZ` resolves on the prefix while the invented tail is never looked
    at, so the roll-up would report it as valid commutative algebra. `Entry`
    rejects unknown codes; this is what protects every other caller -- the
    editor, a report, a third party's script over the published corpus.
    """
    assert not is_known("13ZZZ")
    for lookup in (field_of, group_of, arxiv_of, name_of):
        with pytest.raises(UnknownCode):
            lookup("13ZZZ")


def test_lookups_resolve_from_the_corpus_being_loaded_not_hardys_own(tmp_path):
    """`--corpus` may point at an extracted release or a newer checkout.

    `manifest_digest` binds *that* corpus's taxonomy files, so validating its
    entries against Hardy's own vendored tables would reject codes the
    selected corpus carries, accept codes it removed, and report groups from
    a different mapping.
    """
    taxonomy_dir = tmp_path / "taxonomy"
    taxonomy_dir.mkdir(parents=True)
    (taxonomy_dir / "msc2020.json").write_text(
        json.dumps({"schema_version": 1, "codes": {"99Z99": "Invented studies"}}), encoding="utf-8")
    (taxonomy_dir / "msc-to-arxiv.json").write_text(json.dumps({
        "schema_version": 1, "arxiv": {"99": "math.XX"},
        "fields": {"99": "Invented"}, "groups": {"99": "invented"},
    }), encoding="utf-8")

    assert not is_known("99Z99")
    with taxonomy.using(tmp_path):
        assert is_known("99Z99") and name_of("99Z99") == "Invented studies"
        assert group_of("99Z99") == "invented" and arxiv_of("99Z99") == "math.XX"
        assert not is_known("13A15"), "Hardy's own table must not leak into the selected corpus"
    assert is_known("13A15"), "the default root is restored"


def test_a_rollup_mapping_a_class_to_a_non_string_is_refused(tmp_path):
    """Dict-ness alone let `group_of` return a list, `corpus check` pass, and
    `corpus report` crash on an unhashable `Counter` key."""
    (tmp_path / "taxonomy").mkdir(parents=True)
    (tmp_path / "taxonomy" / "msc2020.json").write_text(
        json.dumps({"codes": {"13A15": "Ideals"}}), encoding="utf-8")
    (tmp_path / "taxonomy" / "msc-to-arxiv.json").write_text(json.dumps({
        "arxiv": {"13": "math.AC"}, "fields": {"13": "Commutative algebra"},
        "groups": {"13": ["commutative-algebra"]},
    }), encoding="utf-8")
    with taxonomy.using(tmp_path), pytest.raises(taxonomy.MalformedTaxonomy, match="strings to strings"):
        group_of("13A15")


def test_the_vendored_table_is_the_whole_of_msc2020():
    """Nine hand-written codes meant a correct tag outside that handful came
    back as unknown. `scripts/vendor_msc2020.py` regenerates this from the
    official CSV, so the table is the classification rather than a sample."""
    from hardy.evals.taxonomy import _codes, _mapping

    codes = _codes()
    assert len(codes) > 6000, len(codes)
    for code in ("11Axx", "12F10", "13A15", "20Dxx", "26Dxx", "46Lxx", "97Uxx"):
        assert is_known(code), code
    # Every 2-digit class carries a field name, a group and an arXiv class, so
    # no valid code can resolve to `UnknownCode` on the roll-up.
    classes = {c[:2] for c in codes}
    mapping = _mapping()
    for table in ("fields", "groups", "arxiv"):
        assert classes <= set(mapping[table]), sorted(classes - set(mapping[table]))


def test_a_split_class_resolves_by_section_before_class():
    """MSC 12 is the worst case: one class across four arXiv archives. A
    class-only table filed a third of it under the wrong archive."""
    assert arxiv_of("12F10") == "math.NT", "Galois theory: arXiv files it under math.NT"
    assert arxiv_of("12J10") == "math.AC", "valued fields"
    assert arxiv_of("12K05") == "math.RA", "near-fields are not commutative"
    assert arxiv_of("12L12") == "math.LO", "model theory of fields"
    assert arxiv_of("12E15") == "math.RA", "skew fields, inside a math.NT class"
    assert arxiv_of("12E05") == "math.NT", "and the class default still applies"
    # One reporting group regardless: the split is about archives, not fields.
    assert {group_of(c) for c in ("12F10", "12J10", "12K05", "12L12")} == {"field-theory"}
