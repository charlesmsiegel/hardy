from __future__ import annotations

import json

import pytest

from hardy.audit import (
    AxiomReport,
    Verdict,
    classify,
    dependents,
    describe,
    open_declarations,
    parse,
    unestablished,
)


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


def test_an_unquoted_report_is_read_too():
    """Lean quotes the name, but a report is evidence however it is spelled."""
    assert parse("two_eq_two depends on axioms: [propext]", ("two_eq_two",)) == (
        AxiomReport("two_eq_two", ("propext",)),
    )


def test_the_bare_form_is_still_bounded_by_the_whole_name():
    assert parse("Foo.bar depends on axioms: [sorryAx]", ("bar",)) is None
    assert parse("barn depends on axioms: [sorryAx]", ("bar",)) is None


def test_the_quoted_and_bare_forms_do_not_double_count_one_report():
    """Otherwise every real report would read as a duplicate and fail closed."""
    assert parse("'T' depends on axioms: [propext]", ("T",)) == (
        AxiomReport("T", ("propext",)),
    )


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


def test_a_native_decide_proof_is_refused_unattended():
    """`native_decide` closes a goal by trusting the compiler, and Lean records
    that as `Lean.ofReduceBool`. It is not one of Lean's three, so a batch or
    staged run refuses it — a real consequence of this gate, pinned here so
    nobody has to rediscover it from a failing run.
    """
    verdict = classify([report("propext", "Lean.ofReduceBool", "Lean.trustCompiler")], ())
    assert verdict.status == "rejected"
    assert verdict.unapproved == ("Lean.ofReduceBool", "Lean.trustCompiler")


def test_sorry_ax_is_never_laundered_by_an_approval():
    """A hole is not an assumption, and no approval can make it one.

    It grades `open` rather than `rejected` now -- an unfinished proof is a
    different fact from an unacceptable one -- but the approval buys nothing
    either way: it is still reported as a hole and never as an assumption, and
    every caller that requires `clean` still refuses it.
    """
    verdict = classify([report("sorryAx")], {"sorryAx"})
    assert verdict.status == "open"
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
    with pytest.raises(AttributeError):
        verdict.status = "modulo"


def test_a_hole_is_open_rather_than_rejected():
    """An unfinished proof is not an unacceptable one, and the two need names."""
    verdict = classify([AxiomReport("thm", ("propext", "sorryAx"))], ())
    assert verdict.status == "open"
    assert verdict.forbidden == ("sorryAx",)


def test_a_hole_beside_an_unapproved_axiom_is_still_rejected():
    """The unapproved axiom is the actionable half, and it does not become one."""
    verdict = classify([AxiomReport("thm", ("sorryAx", "Smith.main"))], ())
    assert verdict.status == "rejected"
    assert verdict.unapproved == ("Smith.main",)


def test_a_hole_beside_an_approved_assumption_is_open():
    """Open outranks modulo: the proof is unfinished whatever it also assumes."""
    verdict = classify([AxiomReport("thm", ("sorryAx", "Smith.main"))], ("Smith.main",))
    assert verdict.status == "open"
    assert verdict.assumed == ("Smith.main",)


def test_an_open_verdict_reads_as_a_hole():
    verdict = classify([AxiomReport("thm", ("sorryAx",))], ())
    assert "hole" in describe(verdict)
    assert "['thm']" in describe(verdict)


def test_open_declarations_names_what_rests_on_a_hole():
    record = classify(
        [AxiomReport("open_one", ("sorryAx",)), AxiomReport("done", ("propext",))], ()
    ).as_dict()
    assert open_declarations(record) == ("open_one",)


def test_a_record_that_never_graded_has_no_open_declarations():
    """`unestablished` and `not audited` carry no declarations to read."""
    assert open_declarations(unestablished("nothing to grade")) == ()


def test_an_open_verdict_still_names_what_was_assumed():
    """A partial result is valid only when its holes *and* its assumptions are
    explicit. Reporting the hole and stopping there dropped an approved axiom
    the unfinished proof rests on out of the one line a reader is shown."""
    verdict = classify([report("sorryAx", "Papers.Smith.main")], {"Papers.Smith.main"})
    assert verdict.status == "open"
    assert "hole" in describe(verdict)
    assert "Papers.Smith.main" in describe(verdict)


def test_an_open_verdict_says_nothing_of_assumptions_when_there_are_none():
    assert describe(classify([report("sorryAx")], ())) == (
        "open -- ['T'] rest on a hole ['sorryAx']"
    )
