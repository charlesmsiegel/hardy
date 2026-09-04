"""The problem list: what an entry must carry, and how every consumer assembles Lean from it."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hardy.evals import taxonomy
from hardy.evals.problems import (
    Audit,
    Entry,
    Occurrence,
    ProblemSet,
    Review,
    sha256_of,
)

ROOT = Path(__file__).resolve().parents[2]


def _entry(**overrides) -> dict:
    base = {
        "id": "odd-squares", "input": "If $a$ and $b$ are odd, $a^2+b^2$ is not a square.",
        "name": "OddSquares", "binders": "(a b : ℤ) (ha : Odd a) (hb : Odd b)",
        "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)", "expected": "true",
        "source": "classical", "msc": ("11A",), "difficulty": "substantial",
        "occurrences": ({"source_id": "hardy-wright", "locator": (6, 1, 3)},),
        "status": "candidate", "witness": "⟨1, 1⟩",
    }
    base.update(overrides)
    return base


def test_the_declaration_proposition_and_negation_are_assembled_one_way():
    entry = Entry(**_entry())
    assert entry.declaration() == "theorem OddSquares (a b : ℤ) (ha : Odd a) (hb : Odd b) : ¬ IsSquare (a ^ 2 + b ^ 2)"
    assert entry.proposition() == "∀ (a b : ℤ) (ha : Odd a) (hb : Odd b), ¬ IsSquare (a ^ 2 + b ^ 2)"
    assert entry.negation() == "¬ (∀ (a b : ℤ) (ha : Odd a) (hb : Odd b), ¬ IsSquare (a ^ 2 + b ^ 2))"


def test_an_entry_without_binders_has_no_stray_space_or_quantifier():
    entry = Entry(**_entry(binders="", conclusion="Irrational (Real.sqrt 2)"))
    assert entry.declaration() == "theorem OddSquares : Irrational (Real.sqrt 2)"
    assert entry.proposition() == "Irrational (Real.sqrt 2)"
    assert entry.negation() == "¬ (Irrational (Real.sqrt 2))"


@pytest.mark.parametrize("bad", [
    {"id": "Bad Slug"}, {"id": ""}, {"name": "not an ident"}, {"conclusion": ""},
    {"conclusion": "x := 1"}, {"expected": "maybe"}, {"source": "blog"}, {"extra": 1},
])
def test_a_malformed_entry_is_refused(bad):
    with pytest.raises(ValidationError):
        Entry(**_entry(**bad))


def test_an_import_carrying_a_lean_command_after_a_newline_is_refused():
    """`imports` is joined one `import <name>\\n` per line and handed straight
    to a real Lean process (both the baseline sweep and the batch runner); an
    import carrying a newline followed by a Lean command would execute that
    command as soon as the sweep or a batch run started (item 6b).
    """
    with pytest.raises(ValidationError, match="Lean command injection|imports must be dotted"):
        Entry(**_entry(imports=["Mathlib\n#eval 1"]))


def test_a_conclusion_carrying_a_newline_is_refused():
    with pytest.raises(ValidationError, match="Lean command injection"):
        Entry(**_entry(conclusion="True\n#eval 1"))


def test_binders_and_name_carrying_a_newline_are_refused():
    with pytest.raises(ValidationError):
        Entry(**_entry(binders="(a : ℕ)\n#eval 1"))
    with pytest.raises(ValidationError):
        Entry(**_entry(name="Foo\n#eval 1"))


def test_twins_point_at_true_entries_and_true_entries_point_nowhere():
    true = _entry()
    twin = _entry(id="squares", name="Squares", binders="(a b : ℤ)", expected="false", twin_of="odd-squares")
    ProblemSet(entries=(Entry(**true), Entry(**twin)))
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**true), Entry(**_entry(id="x", name="X", expected="false"))))  # twin with no target
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**_entry(twin_of="odd-squares")),))                               # true entry pointing
    with pytest.raises(ValidationError, match="twin_of"):
        ProblemSet(entries=(Entry(**true), Entry(**twin), Entry(**_entry(id="y", name="Y", expected="false", twin_of="squares"))))  # twin of a twin


def test_ids_and_names_are_unique():
    with pytest.raises(ValidationError, match="duplicate id"):
        ProblemSet(entries=(Entry(**_entry()), Entry(**_entry(name="Other"))))
    with pytest.raises(ValidationError, match="duplicate name"):
        ProblemSet(entries=(Entry(**_entry()), Entry(**_entry(id="other"))))


def test_the_committed_corpus_loads_and_has_fifteen_true_entries_and_five_twins():
    from hardy.evals.corpus import load_corpus

    problems = load_corpus(ROOT / "corpus")
    assert len(problems.true_entries) == 15 and len(problems.twins) == 5
    assert {t.twin_of for t in problems.twins} <= {e.id for e in problems.true_entries}
    assert problems.by_id("sqrt-two-plus-sqrt-three").expected == "true"


def test_sha256_is_over_the_bytes(tmp_path):
    path = tmp_path / "p.json"
    path.write_bytes(b'{"schema_version": 1, "entries": []}')
    first = sha256_of(path)
    path.write_bytes(b'{"schema_version": 1, "entries": [] }')
    assert sha256_of(path) != first
    assert len(first) == 64


# --- The active gate (spec §2.2) ---


def _approval(entry: Entry, **overrides) -> dict:
    base = {
        "reviewer": "cms", "reviewed_at": "2026-09-03",
        "statement_digest": entry.statement_digest(), "prompt_digest": entry.prompt_digest(),
        "msc": list(entry.msc), "group": taxonomy.group_of(entry.msc[0]), "verdict": "faithful",
    }
    base.update(overrides)
    return base


def test_an_active_entry_must_carry_a_faithful_review():
    """Only active entries reach a headline, and nothing mechanical can tell a
    faithful formalisation from a plausible-looking wrong one."""
    candidate = Entry(**_entry())
    with pytest.raises(ValidationError, match="active"):
        Entry(**_entry(status="active"))
    with pytest.raises(ValidationError, match="faithful"):
        Entry(**_entry(status="active", review=_approval(candidate, verdict="unfaithful", reason="binders wrong")))
    assert Entry(**_entry(status="active", review=_approval(candidate))).status == "active"


def test_editing_a_reviewed_statement_or_prompt_revokes_its_active_status():
    """A reviewer approved a specific thing; an edit is a different thing."""
    reviewed = _approval(Entry(**_entry()))
    with pytest.raises(ValidationError, match="statement"):
        Entry(**_entry(status="active", conclusion="True", review=reviewed))
    with pytest.raises(ValidationError, match="prompt"):
        Entry(**_entry(status="active", input="reworded", review=reviewed))


def test_refiling_a_reviewed_entry_under_a_new_code_revokes_its_active_status():
    """A wrong-but-valid MSC code passes every mechanical check, so this
    review is the only gate between a misclassification and a field headline.
    """
    reviewed = _approval(Entry(**_entry()))
    with pytest.raises(ValidationError, match="classification"):
        Entry(**_entry(status="active", msc=("20D",), review=reviewed))


# --- Occurrences and locators (spec §2.1) ---


def test_a_locator_must_be_nonempty_and_nonnegative():
    assert Occurrence(source_id="am", locator=(3, 2, 12)).locator == (3, 2, 12)
    for bad in ((), (-1,), (1, -2)):
        with pytest.raises(ValidationError):
            Occurrence(source_id="am", locator=bad)


def test_occurrences_order_lexicographically_including_unequal_lengths():
    a = Occurrence(source_id="am", locator=(3,))
    b = Occurrence(source_id="am", locator=(3, 1))
    c = Occurrence(source_id="am", locator=(4,))
    assert a < b < c
    assert not (b < a)


# --- Review and audit records (spec §2.2, §9.2) ---


def _review(**overrides) -> dict:
    base = {
        "reviewer": "cms", "reviewed_at": "2026-09-03T00:00:00Z",
        "statement_digest": "a" * 64, "prompt_digest": "b" * 64,
        "msc": ("13A15",), "group": "commutative-algebra", "verdict": "faithful",
    }
    base.update(overrides)
    return base


def test_a_review_records_the_classification_it_approved():
    review = Review(**_review())
    assert review.verdict == "faithful"
    assert review.msc == ("13A15",) and review.group == "commutative-algebra"


def test_an_unfaithful_review_requires_a_reason():
    with pytest.raises(ValidationError):
        Review(**_review(verdict="unfaithful"))
    assert Review(**_review(verdict="unfaithful", reason="binders drop a hypothesis")).reason


def test_a_pending_audit_names_the_panel_that_raised_it():
    audit = Audit(panel="opus-vs-sonnet@0.2.0", raised_at="2026-09-03T00:00:00Z", verdict="pending")
    assert audit.verdict == "pending" and audit.panel == "opus-vs-sonnet@0.2.0"
    with pytest.raises(ValidationError):
        Audit(panel="p", raised_at="2026-09-03T00:00:00Z", verdict="broken")


# --- Classification (spec §2) ---


def test_an_msc_code_must_be_finer_than_its_own_two_digit_class():
    # `13` is itself a valid MSC2020 entry, so a vendored-list check alone
    # would accept it -- and the precision would be gone permanently.
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=("13",)))
    assert Entry(**_entry(msc=("13A15",))).msc == ("13A15",)


def test_the_shard_is_derived_and_not_stored():
    assert Entry(**_entry(msc=("13A15",))).shard == "13"
    with pytest.raises(ValidationError):
        Entry(**_entry(shard="13"))


def test_an_unknown_or_empty_msc_is_rejected():
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=("99Z99",)))
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=()))


def test_an_arxiv_override_needs_a_reason_and_must_name_a_real_class():
    with pytest.raises(ValidationError):
        Entry(**_entry(arxiv_override="math.NT"))
    with pytest.raises(ValidationError):
        Entry(**_entry(arxiv_override="math.AC ", override_reason="typo guard"))
    assert Entry(**_entry(arxiv_override="math.NT", override_reason="arithmetic geometry")).arxiv_override


# --- Lifecycle (spec §2.2) ---


def test_a_retired_entry_needs_a_reason():
    with pytest.raises(ValidationError):
        Entry(**_entry(status="retired"))
    assert Entry(**_entry(status="retired", retired_reason="mistranslated")).status == "retired"


def test_an_authored_entry_needs_a_rationale_and_cannot_carry_fixtures():
    with pytest.raises(ValidationError):
        Entry(**_entry(occurrences=()))
    assert Entry(**_entry(occurrences=(), rationale="states the pigeonhole bound")).rationale
    with pytest.raises(ValidationError):
        Entry(**_entry(occurrences=(), rationale="x", fixtures=("nakayama",)))


def test_a_null_witness_needs_a_note():
    with pytest.raises(ValidationError):
        Entry(**_entry(witness=None))
    assert Entry(**_entry(witness=None, witness_note="existence-heavy")).witness is None


def test_binders_may_not_mention_a_fixture():
    """An antecedent in binders reaches the bare condition too (spec §9.1)."""
    with pytest.raises(ValidationError):
        Entry(**_entry(binders="(nakayama : True)", fixtures=("nakayama",)))


def test_title_is_optional_and_never_reaches_the_declaration():
    entry = Entry(**_entry(title="Hilbert's Nullstellensatz"))
    assert entry.title == "Hilbert's Nullstellensatz"
    assert entry.name == "OddSquares"
    assert "Nullstellensatz" not in entry.declaration()


# --- ProblemSet (spec §4) ---


def test_duplicate_detection_and_lookup_are_linear():
    import time

    entries = tuple(Entry(**_entry(id=f"e-{i}", name=f"E{i}")) for i in range(5000))
    start = time.perf_counter()
    problems = ProblemSet(entries=entries)
    for i in range(0, 5000, 250):
        problems.by_id(f"e-{i}")
    assert time.perf_counter() - start < 5.0
    assert problems.by_id("e-4999").name == "E4999"


def test_a_false_twin_inherits_its_targets_primary_msc():
    target = Entry(**_entry(id="sq-ge", name="SqGe", msc=("26D",)))
    drifted = Entry(**_entry(id="sq-le", name="SqLe", msc=("11A",), expected="false", twin_of="sq-ge"))
    with pytest.raises(ValidationError):
        ProblemSet(entries=(target, drifted))

    ok = Entry(**_entry(id="sq-le", name="SqLe", msc=("26D",), expected="false", twin_of="sq-ge"))
    assert len(ProblemSet(entries=(target, ok)).entries) == 2


def test_a_review_must_identify_a_reviewer_and_a_date():
    """`min_length=1` accepts " " and "unknown", neither of which identifies
    anyone or anything -- and `active_ids` trusts the status this grants."""
    for bad in ({"reviewer": "   "}, {"reviewer": ""}):
        with pytest.raises(ValidationError, match="reviewer"):
            Review(**_review(**bad))
    for bad in ("unknown", "sometime", "2026-13-45"):
        with pytest.raises(ValidationError, match="ISO 8601"):
            Review(**_review(reviewed_at=bad))
    assert Review(**_review(reviewed_at="2026-09-03")).reviewed_at == "2026-09-03"
    assert Review(**_review(reviewed_at="2026-09-03T00:00:00Z")).reviewed_at.endswith("Z")
