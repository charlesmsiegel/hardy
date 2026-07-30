from __future__ import annotations

import json

from hardy.audit import AxiomReport, Verdict, classify, dependents, describe, parse, unestablished


def test_parses_a_dependency_line():
    output = "'HardyTarget' depends on axioms: [propext, Classical.choice, Quot.sound]"
    assert parse(output, ("HardyTarget",)) == (
        AxiomReport("HardyTarget", ("propext", "Classical.choice", "Quot.sound")),
    )


def test_parses_the_no_axioms_form():
    """Real Lean says this rather than printing an empty list."""
    assert parse("'HardyTarget' does not depend on any axioms", ("HardyTarget",)) == (
        AxiomReport("HardyTarget", ()),
    )


def test_ignores_a_file_and_severity_prefix():
    output = "Main.lean:7:0: information: 'Foo.bar' depends on axioms: [propext]"
    assert parse(output, ("Foo.bar",)) == (AxiomReport("Foo.bar", ("propext",)),)


def test_gathers_a_list_wrapped_across_lines():
    """Lean wraps long axiom lists at its formatter width."""
    output = "'T' depends on axioms: [propext,\n  Classical.choice,\n  Quot.sound]"
    assert parse(output, ("T",)) == (
        AxiomReport("T", ("propext", "Classical.choice", "Quot.sound")),
    )


def test_parses_a_name_containing_an_apostrophe():
    """`add_comm'`-style names are everywhere in Mathlib.

    A generic `'([^']+)'` capture finds nothing here at all, so every primed
    declaration would be rejected as an unestablished audit.
    """
    output = "'Nat.add_comm'' depends on axioms: [propext]"
    assert parse(output, ("Nat.add_comm'",)) == (AxiomReport("Nat.add_comm'", ("propext",)),)


def test_a_name_is_not_matched_by_a_longer_one_that_contains_it():
    output = "'Foo.bar' depends on axioms: [sorryAx]"
    assert parse(output, ("bar",)) is None


def test_a_missing_report_is_not_an_empty_one():
    """Silence must never read as 'depends on nothing'."""
    assert parse("'Other' does not depend on any axioms", ("HardyTarget",)) is None


def test_a_duplicated_report_fails_closed():
    """A model can print its own lookalike line; Hardy will not pick a winner."""
    output = "'T' does not depend on any axioms\n'T' depends on axioms: [sorryAx]"
    assert parse(output, ("T",)) is None


def test_garbage_output_fails_closed():
    assert parse("error: unknown identifier 'T'", ("T",)) is None


def test_an_empty_expected_set_is_not_an_audit():
    """Auditing nothing must not come back as an audit that found nothing."""
    assert parse("'T' does not depend on any axioms", ()) is None


def test_reports_follow_the_requested_order():
    output = "'B' depends on axioms: [propext]\n'A' does not depend on any axioms"
    assert [report.declaration for report in parse(output, ("A", "B"))] == ["A", "B"]


def test_an_unterminated_list_is_not_read_as_a_short_one():
    """A truncated tail must fail closed rather than drop the axioms it cut."""
    assert parse("'T' depends on axioms: [propext, Papers.Smith.main", ("T",)) is None


def test_a_list_does_not_run_past_its_own_closing_bracket():
    """Two reports must not merge into one when the first is read greedily."""
    output = "'A' depends on axioms: [propext]\n'B' depends on axioms: [sorryAx]"
    assert parse(output, ("A", "B")) == (
        AxiomReport("A", ("propext",)),
        AxiomReport("B", ("sorryAx",)),
    )


def report(*axioms: str, name: str = "T") -> AxiomReport:
    return AxiomReport(name, axioms)


def test_standard_axioms_alone_are_clean():
    verdict = classify([report("propext", "Classical.choice", "Quot.sound")], ())
    assert verdict.status == "clean"
    assert verdict.assumed == () and verdict.unapproved == ()


def test_no_axioms_at_all_is_clean():
    assert classify([report()], ()).status == "clean"


def test_an_empty_report_set_is_rejected():
    """Grading nothing is not grading something clean."""
    assert classify([], ()).status == "rejected"


def test_an_unapproved_axiom_is_rejected():
    verdict = classify([report("propext", "Papers.Smith.main")], ())
    assert verdict.status == "rejected"
    assert verdict.unapproved == ("Papers.Smith.main",)


def test_an_approved_axiom_downgrades_to_modulo():
    verdict = classify([report("propext", "Papers.Smith.main")], {"Papers.Smith.main"})
    assert verdict.status == "modulo"
    assert verdict.assumed == ("Papers.Smith.main",)
    assert verdict.unapproved == ()


def test_sorry_ax_is_rejected_even_when_someone_approved_it():
    """A hole is not an assumption, and no approval can make it one."""
    verdict = classify([report("sorryAx")], {"sorryAx"})
    assert verdict.status == "rejected"
    assert verdict.forbidden == ("sorryAx",)
    assert verdict.assumed == ()


def test_axioms_are_collected_across_declarations_without_duplicates():
    verdict = classify([report("propext", "X", name="A"), report("X", name="B")], ())
    assert verdict.unapproved == ("X",)


def test_dependents_names_who_needs_an_axiom():
    reports = [report("X", name="A"), report("propext", name="B"), report("X", name="C")]
    assert dependents(reports, "X") == ("A", "C")


def test_describe_is_readable_for_each_status():
    assert describe(classify([report("propext")], ())) == "standard axioms only"
    assert "sorryAx" in describe(classify([report("sorryAx")], ()))
    assert "Papers.Smith.main" in describe(
        classify([report("Papers.Smith.main")], {"Papers.Smith.main"})
    )


def test_as_dict_is_json_plain():
    verdict = classify([report("propext", "X")], {"X"})
    assert json.loads(json.dumps(verdict.as_dict())) == {
        "status": "modulo",
        "declarations": [{"name": "T", "axioms": ["propext", "X"]}],
        "forbidden": [],
        "unapproved": [],
        "assumed": ["X"],
    }


def test_unestablished_is_json_plain_and_says_why():
    record = unestablished("no report")
    assert json.loads(json.dumps(record)) == record
    assert record["status"] == "not established"
    assert record["reason"] == "no report"


def test_a_verdict_is_frozen():
    """The record of what an artifact rests on is not edited after the fact."""
    verdict = Verdict("clean", (), (), (), ())
    try:
        verdict.status = "modulo"
    except AttributeError:
        return
    raise AssertionError("Verdict must be immutable")
