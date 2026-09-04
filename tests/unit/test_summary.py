"""The workspace's own account of a session, derived rather than narrated (#100)."""

from __future__ import annotations

from hardy import summary


def audit_record(name: str, axioms: list[str], *, assumed: list[str] = (), status: str = "clean"):
    return {
        "status": status,
        "declarations": [{"name": name, "axioms": axioms}],
        "forbidden": [],
        "unapproved": [],
        "assumed": list(assumed),
    }


def assemble(**overrides):
    fields = {
        "goal": "",
        "assumptions": [],
        "registry": [],
        "audit": {},
        "theorems": {},
        "open_theorems": (),
        "obligations": (),
    }
    return summary.assemble(**{**fields, **overrides})


def test_a_kernel_verified_theorem_says_so():
    text = assemble(
        theorems={"sylow": "theorem sylow : True"},
        audit={"Work": audit_record("sylow", ["propext"])},
    ).text()
    assert "sylow: kernel-verified -- standard axioms only" in text


def test_a_theorem_resting_on_an_approved_axiom_is_not_reported_as_verified():
    text = assemble(
        theorems={"sylow": "theorem sylow : True"},
        audit={"Work": audit_record("sylow", ["propext", "big"], assumed=["big"])},
    ).text()
    assert "rests on approved assumptions ['big']" in text
    assert "kernel-verified" not in text


def test_a_theorem_no_stored_verdict_names_is_not_audited_rather_than_clean():
    text = assemble(theorems={"sylow": "theorem sylow : True"}, audit={}).text()
    assert "sylow: not audited -- no stored verdict names it" in text


def test_an_open_theorem_is_listed_as_open_and_never_as_proved():
    assembled = assemble(
        theorems={"partial_": "theorem partial_ : True"},
        open_theorems={"partial_"},
        audit={"Work": audit_record("partial_", ["sorryAx"], status="open")},
    )
    text = assembled.text()
    assert "partial_: open -- rests on a hole" in text
    assert "Proved" in text and "no closed theorem is saved" in text


def test_an_assumption_carries_its_provenance_its_reason_and_its_approval():
    text = assemble(
        assumptions=[
            {
                "formal_name": "big",
                "lean_statement": "True",
                "source": "Aschbacher, Theorem 1.2",
                "reason": "Mathlib does not expose it",
                "status": "user-approved",
                "approved_at": "2026-09-04T10:00:00+00:00",
            }
        ]
    ).text()
    assert "Aschbacher, Theorem 1.2" in text
    assert "Mathlib does not expose it" in text
    assert "user-approved on 2026-09-04T10:00:00+00:00" in text


def test_an_assumption_approved_before_dates_were_recorded_says_so():
    text = assemble(
        assumptions=[{"formal_name": "old", "lean_statement": "True", "status": "user-approved"}]
    ).text()
    assert "(date not recorded)" in text


def test_an_empty_workspace_says_nothing_is_reportable():
    text = assemble().text()
    assert "no closed theorem is saved: nothing here is reportable." in text
    assert "not set (/goal)" in text


def test_the_summary_never_carries_spend():
    """`usage` is withheld from the model on purpose; a summary must not smuggle it back."""
    text = assemble(goal="Prove it").text().lower()
    assert "spend" not in text and "usd" not in text and "token" not in text


def test_failed_attempts_come_from_the_transcript_and_fold_by_subject():
    events = [
        {"type": "tool", "name": "save_lean", "arguments": {"path": "Work.lean"},
         "result": {"ok": False, "output": "unknown identifier 'foo'"}},
        {"type": "tool", "name": "save_lean", "arguments": {"path": "Work.lean"},
         "result": {"ok": False, "output": "unsolved goals"}},
        {"type": "tool", "name": "save_lean", "arguments": {"path": "Work.lean"},
         "result": {"ok": True, "output": "saved"}},
        {"type": "user", "message": {"role": "user", "content": "hello"}},
    ]
    found = summary.attempts(events)
    assert len(found) == 1
    assert found[0].count == 2
    assert found[0].subject == "Work.lean"
    assert "unsolved goals" in found[0].detail       # the most recent thing it said


def test_a_failed_attempt_never_quotes_the_whole_source_back():
    events = [
        {
            "type": "tool",
            "name": "save_lean",
            "arguments": {"path": "Work.lean", "source": "x" * 5000},
            "result": {"ok": False, "output": "y" * 5000},
        }
    ]
    line = str(summary.attempts(events)[0])
    assert len(line) < 300
    assert "xxxx" not in line


def test_only_the_most_recent_attempts_are_kept():
    events = [
        {"type": "tool", "name": "save_lean", "arguments": {"path": f"M{index}.lean"},
         "result": {"ok": False, "output": "no"}}
        for index in range(40)
    ]
    found = summary.attempts(events, limit=5)
    assert [item.subject for item in found] == [f"M{index}.lean" for index in range(35, 40)]


def test_the_registry_and_the_outstanding_work_are_both_shown():
    text = assemble(
        registry=[{"formal_name": "sylow", "latex_name": "thm:sylow", "description": "Sylow"}],
        obligations=("write up sylow",),
    ).text()
    assert "sylow <-> thm:sylow" in text
    assert "write up sylow" in text


def test_a_failed_report_is_named_by_the_theorem_it_claimed():
    events = [
        {
            "type": "tool",
            "name": "report_result",
            "arguments": {"theorems": ["hardyOne"], "summary": "done"},
            "result": {"ok": False, "output": "the writeup quotes no Lean"},
        }
    ]
    assert summary.attempts(events)[0].subject == "hardyOne"


def test_a_failure_with_nothing_to_name_it_by_still_appears():
    events = [
        {"type": "tool", "name": "read_workspace", "arguments": {},
         "result": {"ok": False, "output": "unreadable"}}
    ]
    found = summary.attempts(events)
    assert found[0].subject == ""
    assert str(found[0]) == "read_workspace: unreadable"


def test_a_stale_verdict_never_reads_as_verified():
    """The session marks an expired record; a summary that ignored that would
    call a theorem kernel-verified on the same page that reports its audit as
    no longer established."""
    stale = {
        **audit_record("sylow", ["propext"]),
        "status": "not established",
        "reason": "the toolchain moved since this was established",
        "stale": True,
    }
    text = assemble(theorems={"sylow": "theorem sylow : True"}, audit={"Work": stale}).text()
    assert "sylow: no longer established -- the toolchain moved" in text
    assert "kernel-verified" not in text


def test_an_assumption_is_attributed_only_to_the_declarations_that_use_it():
    """The stored `assumed` is the union for a whole module, not for a theorem."""
    record = {
        "status": "modulo",
        "declarations": [
            {"name": "clean_", "axioms": ["propext"]},
            {"name": "leans_", "axioms": ["propext", "big"]},
        ],
        "forbidden": [],
        "unapproved": [],
        "assumed": ["big"],
    }
    text = assemble(
        theorems={"clean_": "theorem clean_ : True", "leans_": "theorem leans_ : True"},
        audit={"Work": record},
    ).text()
    assert "clean_: kernel-verified -- standard axioms only" in text
    assert "leans_: rests on approved assumptions ['big']" in text


def test_an_open_theorem_still_discloses_the_axiom_it_also_rests_on():
    """Two limitations, and naming only the hole leaves the rest looking like
    Lean's own."""
    record = {
        "status": "open",
        "declarations": [{"name": "both_", "axioms": ["sorryAx", "big"]}],
        "forbidden": ["sorryAx"],
        "unapproved": [],
        "assumed": ["big"],
    }
    text = assemble(theorems={"both_": "theorem both_ : True"}, audit={"Work": record}).text()
    assert "both_: open -- rests on a hole; approved assumptions ['big']" in text


def test_an_unaudited_theorem_is_not_printed_under_proved():
    """The heading would contradict the line beneath it."""
    text = assemble(theorems={"sylow": "theorem sylow : True"}, audit={}).text()
    proved = text.split("Proved\n", 1)[1].split("\nNot established", 1)[0]
    assert "sylow" not in proved
    assert "Not established" in text
    assert "sylow: not audited" in text


def test_a_stale_verdict_is_not_printed_under_proved():
    stale = {
        **audit_record("sylow", ["propext"]),
        "status": "not established",
        "reason": "the toolchain moved",
        "stale": True,
    }
    text = assemble(theorems={"sylow": "theorem sylow : True"}, audit={"Work": stale}).text()
    proved = text.split("Proved\n", 1)[1].split("\nNot established", 1)[0]
    assert "sylow" not in proved


def test_a_verified_theorem_is_still_printed_under_proved():
    text = assemble(
        theorems={"sylow": "theorem sylow : True"},
        audit={"Work": audit_record("sylow", ["propext"])},
    ).text()
    proved = text.split("Proved\n", 1)[1].split("\nOpen", 1)[0].split("\nNot established", 1)[0]
    assert "sylow: kernel-verified" in proved


def test_a_name_two_modules_declare_is_not_graded():
    text = assemble(
        theorems={"result": "theorem result : True"},
        audit={"A": audit_record("result", ["propext"])},
        shared={"result": ["A", "B"]},
    ).text()
    assert "not graded" in text
    assert "kernel-verified" not in text


def test_a_long_refusal_keeps_the_end_where_the_diagnostic_is():
    """Lean prints setup and imports first and the error that failed the call
    last, so a head slice is the one part carrying no information."""
    noise = "building Mathlib " * 40
    found = summary.attempts([
        {
            "type": "tool",
            "name": "save_lean",
            "arguments": {"path": "Basic.lean"},
            "result": {"ok": False, "output": noise + "error: unknown identifier 'foo'"},
        }
    ])

    assert "unknown identifier 'foo'" in found[0].detail
    assert found[0].detail.startswith("…")


def test_a_tool_the_sdk_refused_counts_as_a_failed_attempt():
    """"The transcript records no refused tool call" over a run in which the
    model reached for `Bash` is the most misleading line this section has."""
    found = summary.attempts([{"type": "refused_tool", "name": "Bash"}])

    assert found and found[0].tool == "Bash"
    assert "never ran" in found[0].detail
