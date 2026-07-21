import pytest

from hardy.latex.template import FORMALIZATION_STATUSES, render_writeup


def render(**overrides) -> str:
    kwargs = dict(
        title="Irrationality of the Square Root of Two",
        statement=r"There is no rational number $q$ with $q^2 = 2$.",
        informal_proof=r"Suppose $q = a/b$ in lowest terms\dots",
        formalization_status="not formalized",
    )
    kwargs.update(overrides)
    return render_writeup(**kwargs)


def test_contains_all_content():
    doc = render()
    assert r"\documentclass" in doc
    assert "Irrationality of the Square Root of Two" in doc
    assert r"There is no rational number $q$ with $q^2 = 2$." in doc
    assert r"\begin{theorem}" in doc and r"\begin{proof}" in doc


def test_two_grade_status_block():
    doc = render()
    assert "Formalization status: not formalized" in doc
    # Pre-M6, informal completeness never defaults upward (DESIGN.md).
    assert "Informal completeness: not assessed" in doc


def test_lean_file_line_present_when_given():
    doc = render(formalization_status="verified", lean_file="Sqrt2Irrational.lean")
    assert r"\texttt{Sqrt2Irrational.lean}" in doc


def test_lean_file_underscores_escaped():
    doc = render(formalization_status="verified", lean_file="sqrt2_irrational.lean")
    assert r"sqrt2\_irrational.lean" in doc


def test_no_lean_line_when_absent():
    assert r"\texttt{" not in render()


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        render(formalization_status="probably fine")


def test_no_citations_in_m0_template():
    doc = render()
    assert r"\cite" not in doc and "bibliography" not in doc


def test_all_statuses_accepted():
    for status in FORMALIZATION_STATUSES:
        assert f"Formalization status: {status}" in render(formalization_status=status)
