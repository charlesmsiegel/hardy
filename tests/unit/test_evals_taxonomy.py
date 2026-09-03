"""MSC lookups: names, roll-ups, reporting groups, and the arXiv derivation."""
from __future__ import annotations

import pytest

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
    assert name_of("13A15") == "Ideals and multiplicative ideal theory"
    assert field_of("13A15") == "Commutative algebra"
    assert group_of("13A15") == "commutative-algebra"
    assert arxiv_of("13A15") == "math.AC"


def test_twentysix_and_twentyeight_roll_up_to_one_reporting_group():
    """The reason `group_of` exists apart from `field_of` (spec §5)."""
    assert field_of("26A") != field_of("28A")
    assert group_of("26A") == group_of("28A") == "analysis"


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
