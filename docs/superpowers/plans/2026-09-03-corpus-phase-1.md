# Corpus Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `evals/problems.json` into `corpus/`, a classified, versioned, shardable dataset with per-component staleness — so that authoring hundreds of problems in phase 3 never requires a hand re-tag.

**Architecture:** Four new/grown modules under `src/hardy/evals/`. `taxonomy.py` answers MSC questions from vendored tables. `digests.py` computes the five component digests as pure functions. `problems.py` grows the `Entry` schema and its validators. `corpus.py` owns sharded loading, the tombstone registry, the manifest, and the `check`/`report` commands. The `corpus/` directory itself is data only — no code, no derived values — so it stays extractable as a standalone dataset.

**Tech Stack:** Python 3, pydantic v2 (`FrozenModel` = `extra="forbid", frozen=True`), pytest, `hashlib` from the standard library.

**Spec:** `docs/superpowers/specs/2026-09-03-corpus-design.md`

## Global Constraints

- **The corpus holds statements only.** No tier, no discrimination, no solve rate, no `shard` field. Anything measured lives outside `corpus/`. (spec §1)
- **Every MSC code is strictly finer than its own 2-digit class.** `13` is rejected; `13A` and `13A15` are accepted. (spec §2)
- **The shard is derived** from `msc[0][:2]`, never stored. (spec §1)
- **`title` never enters `input`, the prompt, or any digest.** `name` — the Lean identifier — is a different field that necessarily does. (spec §12.2)
- **`occurrences` is outside every digest.** Adding a citation must not invalidate a measurement. (spec §2.1)
- Every model is a `FrozenModel` — immutable, `extra="forbid"`.
- Tests are hermetic. No Lean, no network, no model calls, except Task 11 which is marked `real_toolchain`.
- Run tests with `uv run --extra test pytest`.

---

### Task 1: The taxonomy module

**Files:**
- Create: `corpus/taxonomy/msc2020.json`
- Create: `corpus/taxonomy/msc-to-arxiv.json`
- Create: `src/hardy/evals/taxonomy.py`
- Test: `tests/unit/test_evals_taxonomy.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `name_of(code: str) -> str`, `field_of(code: str) -> str`, `group_of(code: str) -> str`, `arxiv_of(code: str) -> str`, `is_known(code: str) -> bool`, and `UnknownCode(KeyError)`.

- [ ] **Step 1: Write the vendored tables**

`corpus/taxonomy/msc2020.json` — code to name. Phase 1 needs only the four planned fields plus what the existing twenty entries use; a later task fails loudly on anything missing rather than guessing.

```json
{
  "schema_version": 1,
  "codes": {
    "05A": "Enumerative combinatorics",
    "11A": "Elementary number theory",
    "11J": "Diophantine approximation and transcendental number theory",
    "12F": "Field extensions",
    "13A": "General commutative ring theory",
    "13A15": "Ideals and multiplicative ideal theory",
    "15A": "Basic linear algebra",
    "20A": "Foundations of group theory",
    "20D": "Abstract finite groups",
    "26A": "Functions of one real variable",
    "26D": "Inequalities for real functions",
    "28A": "Classical measure theory"
  }
}
```

`corpus/taxonomy/msc-to-arxiv.json` — 2-digit class to arXiv category, plus the reporting groups.

```json
{
  "schema_version": 1,
  "arxiv": {
    "05": "math.CO", "11": "math.NT", "12": "math.AC", "13": "math.AC",
    "15": "math.RA", "20": "math.GR", "26": "math.CA", "28": "math.CA"
  },
  "fields": {
    "05": "Combinatorics", "11": "Number theory", "12": "Field theory",
    "13": "Commutative algebra", "15": "Linear algebra",
    "20": "Group theory", "26": "Real analysis", "28": "Measure theory"
  },
  "groups": {
    "05": "combinatorics", "11": "number-theory", "12": "algebra",
    "13": "commutative-algebra", "15": "linear-algebra",
    "20": "group-theory", "26": "analysis", "28": "analysis"
  }
}
```

Note `26` and `28` share the group `analysis` — that is the whole reason `group_of` exists apart from `field_of` (spec §5).

- [ ] **Step 2: Write the failing test**

```python
"""MSC lookups: names, roll-ups, reporting groups, and the arXiv derivation."""
from __future__ import annotations

import pytest

from hardy.evals.taxonomy import UnknownCode, arxiv_of, field_of, group_of, is_known, name_of


def test_a_full_code_has_a_name_a_field_and_a_group():
    assert name_of("13A15") == "Ideals and multiplicative ideal theory"
    assert field_of("13A15") == "Commutative algebra"
    assert group_of("13A15") == "commutative-algebra"
    assert arxiv_of("13A15") == "math.AC"


def test_twentysix_and_twentyeight_roll_up_to_one_reporting_group():
    assert field_of("26A") != field_of("28A")
    assert group_of("26A") == group_of("28A") == "analysis"


def test_an_unknown_code_raises_rather_than_guessing():
    assert not is_known("99Z99")
    with pytest.raises(UnknownCode):
        name_of("99Z99")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_taxonomy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.evals.taxonomy'`

- [ ] **Step 4: Write the implementation**

```python
"""MSC2020 lookups, read from the corpus's own vendored tables.

The tables live under `corpus/taxonomy/` rather than beside this module: they
are corpus data a third party gets when they take the dataset, not Hardy
configuration (spec §1).
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[3] / "corpus"


class UnknownCode(KeyError):
    """A code absent from the vendored MSC2020 table."""


@cache
def _codes() -> dict[str, str]:
    return json.loads((CORPUS / "taxonomy" / "msc2020.json").read_text(encoding="utf-8"))["codes"]


@cache
def _mapping() -> dict[str, dict[str, str]]:
    return json.loads((CORPUS / "taxonomy" / "msc-to-arxiv.json").read_text(encoding="utf-8"))


def is_known(code: str) -> bool:
    return code in _codes()


def _lookup(table: dict[str, str], key: str, code: str) -> str:
    try:
        return table[key]
    except KeyError as exc:
        raise UnknownCode(code) from exc


def name_of(code: str) -> str:
    """The MSC2020 name of the full code -- what §12.1's reviewer actually reads."""
    return _lookup(_codes(), code, code)


def field_of(code: str) -> str:
    return _lookup(_mapping()["fields"], code[:2], code)


def group_of(code: str) -> str:
    return _lookup(_mapping()["groups"], code[:2], code)


def arxiv_of(code: str) -> str:
    return _lookup(_mapping()["arxiv"], code[:2], code)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_taxonomy.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Commit**

```bash
git add corpus/taxonomy src/hardy/evals/taxonomy.py tests/unit/test_evals_taxonomy.py
git commit -m "Add the MSC2020 taxonomy and its vendored tables"
```

---

### Task 2: Locators and occurrences

**Files:**
- Modify: `src/hardy/evals/problems.py` (add above `Entry`)
- Test: `tests/unit/test_evals_problems.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Occurrence(source_id: str, locator: tuple[int, ...])`, comparable by `locator` via `<`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_locator_must_be_nonempty_and_nonnegative():
    from hardy.evals.problems import Occurrence

    assert Occurrence(source_id="am", locator=(3, 2, 12)).locator == (3, 2, 12)
    for bad in ((), (-1,), (1, -2)):
        with pytest.raises(ValidationError):
            Occurrence(source_id="am", locator=bad)


def test_occurrences_order_lexicographically_including_unequal_lengths():
    from hardy.evals.problems import Occurrence

    a = Occurrence(source_id="am", locator=(3,))
    b = Occurrence(source_id="am", locator=(3, 1))
    c = Occurrence(source_id="am", locator=(4,))
    assert a < b < c
    assert not (b < a)
```

Empty and negative locators are rejected *before* any ordering comparison because `()` sorts before every non-empty tuple and `(-1,)` before any real chapter — either would satisfy §9.0's "strictly earlier" antecedent gate without naming an earlier result.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -k locator -v`
Expected: FAIL with `ImportError: cannot import name 'Occurrence'`

- [ ] **Step 3: Write the implementation**

```python
class Occurrence(FrozenModel):
    """Where a result appears in a text: a source and an ordered position.

    `locator` is `(chapter, section, item)` compared lexicographically. The
    constraints are load-bearing rather than tidiness -- see spec §2.1.
    """

    source_id: str = Field(min_length=1)
    locator: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _nonnegative(self) -> Occurrence:
        if any(part < 0 for part in self.locator):
            raise ValueError(f"locator parts must be non-negative: {self.locator!r}")
        return self

    def __lt__(self, other: Occurrence) -> bool:
        return self.locator < other.locator
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -k "locator or occurrence" -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Commit**

```bash
git add src/hardy/evals/problems.py tests/unit/test_evals_problems.py
git commit -m "Add Occurrence with a validated, orderable locator"
```

---

### Task 3: Review and audit records

**Files:**
- Modify: `src/hardy/evals/problems.py` (add below `Occurrence`)
- Test: `tests/unit/test_evals_problems.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `Review(reviewer, reviewed_at, statement_digest, prompt_digest, msc, group, verdict)` and `Audit(panel, raised_at, verdict, note)`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_review_records_the_classification_it_approved():
    from hardy.evals.problems import Review

    review = Review(
        reviewer="cms", reviewed_at="2026-09-03T00:00:00Z",
        statement_digest="a" * 64, prompt_digest="b" * 64,
        msc=("13A15",), group="commutative-algebra", verdict="faithful",
    )
    assert review.verdict == "faithful"
    assert review.msc == ("13A15",)


def test_an_unfaithful_review_requires_a_reason():
    from hardy.evals.problems import Review

    with pytest.raises(ValidationError):
        Review(
            reviewer="cms", reviewed_at="2026-09-03T00:00:00Z",
            statement_digest="a" * 64, prompt_digest="b" * 64,
            msc=("13A15",), group="commutative-algebra", verdict="unfaithful",
        )


def test_a_pending_audit_names_the_panel_that_raised_it():
    from hardy.evals.problems import Audit

    audit = Audit(panel="opus-vs-sonnet@0.2.0", raised_at="2026-09-03T00:00:00Z", verdict="pending")
    assert audit.verdict == "pending"
```

The `msc` and `group` fields are what make a `faithful` verdict a digest-bound approval of the classification as well as the mathematics (spec §2, §12.1) — moving an entry to another valid field must invalidate its approval.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -k "review or audit" -v`
Expected: FAIL with `ImportError: cannot import name 'Review'`

- [ ] **Step 3: Write the implementation**

```python
class Review(FrozenModel):
    """A recorded human faithfulness read (spec §2.2).

    The digests and the classification are both in here: an edit to the
    statement, the prompt, or the field invalidates the approval, because a
    reviewer approved a specific thing filed in a specific place.
    """

    reviewer: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    statement_digest: str = Field(min_length=64, max_length=64)
    prompt_digest: str = Field(min_length=64, max_length=64)
    msc: tuple[str, ...] = Field(min_length=1)
    group: str = Field(min_length=1)
    verdict: Literal["faithful", "unfaithful"]
    reason: str | None = None

    @model_validator(mode="after")
    def _unfaithful_needs_a_reason(self) -> Review:
        if self.verdict == "unfaithful" and not (self.reason or "").strip():
            raise ValueError("an unfaithful verdict must record why")
        return self


class Audit(FrozenModel):
    """A spot-audit raised by C3 against one measurement panel (spec §9.2)."""

    panel: str = Field(min_length=1)
    raised_at: str = Field(min_length=1)
    verdict: Literal["pending", "sound", "broken"]
    note: str | None = None

    @model_validator(mode="after")
    def _resolution_needs_a_note(self) -> Audit:
        if self.verdict == "broken" and not (self.note or "").strip():
            raise ValueError("a broken verdict must record why")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -k "review or audit" -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add src/hardy/evals/problems.py tests/unit/test_evals_problems.py
git commit -m "Add Review and Audit records"
```

---

### Task 4: Component digests

**Files:**
- Create: `src/hardy/evals/digests.py`
- Test: `tests/unit/test_evals_digests.py`

**Interfaces:**
- Consumes: nothing (takes plain values, not `Entry`, so it has no import cycle with `problems.py`).
- Produces: `statement_digest(name, binders, conclusion, imports, witness, witness_note, fixture_digests) -> str`, `prompt_digest(statement, input, expected, twin_of) -> str`, `fixture_set_digest(resolved) -> str`, `environment_digest(mapping) -> str`, `procedure_digest(mapping) -> str`. All return 64-char hex.

- [ ] **Step 1: Write the failing test**

```python
"""Component digests: what each one covers, and what it deliberately does not."""
from __future__ import annotations

from hardy.evals.digests import prompt_digest, statement_digest

STATEMENT = {
    "name": "OddSquares", "binders": "(a b : ℤ)", "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)",
    "imports": ("Mathlib",), "witness": "⟨1, 1⟩", "witness_note": None, "fixture_digests": (),
}


def test_a_statement_digest_is_stable_and_sixtyfour_hex():
    first = statement_digest(**STATEMENT)
    assert first == statement_digest(**STATEMENT)
    assert len(first) == 64 and int(first, 16) >= 0


def test_editing_the_conclusion_or_the_witness_changes_the_statement_digest():
    base = statement_digest(**STATEMENT)
    assert statement_digest(**{**STATEMENT, "conclusion": "True"}) != base
    assert statement_digest(**{**STATEMENT, "witness": "⟨2, 2⟩"}) != base


def test_a_fixture_edit_changes_only_the_fixture_bearing_digest():
    bare = statement_digest(**STATEMENT)
    fixtured = statement_digest(**{**STATEMENT, "fixture_digests": ("c" * 64,)})
    assert fixtured != bare


def test_editing_input_changes_the_prompt_digest_but_not_the_statement_digest():
    statement = statement_digest(**STATEMENT)
    first = prompt_digest(statement=statement, input="If a and b are odd...", expected="true", twin_of=None)
    reworded = prompt_digest(statement=statement, input="Given odd a and b...", expected="true", twin_of=None)
    assert reworded != first
    assert statement == statement_digest(**STATEMENT)


def test_flipping_expected_changes_the_prompt_digest():
    statement = statement_digest(**STATEMENT)
    true_side = prompt_digest(statement=statement, input="x", expected="true", twin_of=None)
    twin_side = prompt_digest(statement=statement, input="x", expected="false", twin_of="odd-squares")
    assert true_side != twin_side
```

`expected` and `twin_of` are in the prompt digest because they *shape the run*: under a staged condition true entries run staged while twins run batch under separate limits (`runner.py:219-225`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_digests.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.evals.digests'`

- [ ] **Step 3: Write the implementation**

```python
"""The component digests of spec §3.

Each measurement records the subset it depends on, so editing a shared fixture
does not invalidate the fixture-free measurements of every dependent entry.
Digests take plain values rather than an `Entry` so this module stays free of
an import cycle with `problems.py`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SEP = "\x1f"


def _digest(kind: str, parts: list[Any]) -> str:
    payload = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def statement_digest(*, name: str, binders: str, conclusion: str, imports: tuple[str, ...],
                     witness: str | None, witness_note: str | None,
                     fixture_digests: tuple[str, ...]) -> str:
    """Governs the A-group. The witness is in here because the digest decides
    incremental reuse: editing a valid witness into one the kernel rejects
    would otherwise leave a cached A6 pass looking current."""
    return _digest("statement", [name, binders, conclusion, list(imports), witness,
                                 witness_note, sorted(fixture_digests)])


def fixture_set_digest(resolved: tuple[str, ...]) -> str:
    """The resolved contents of an entry's fixtures, transitively (spec §3)."""
    return _digest("fixture-set", [sorted(resolved)])


def prompt_digest(*, statement: str, input: str, expected: str, twin_of: str | None) -> str:
    """Governs the B-group. `input` reaches the model as `informal_claim`
    (`runner.py:252`); `expected` and `twin_of` decide which mode it runs in."""
    return _digest("prompt", [statement, input, expected, twin_of])


def environment_digest(environment: dict[str, Any]) -> str:
    """Lean version, Mathlib revision, lake manifest, host. Recording provenance
    is not the same as governing reuse -- this is what governs it."""
    return _digest("environment", [environment])


def procedure_digest(procedure: dict[str, Any]) -> str:
    """Hardy's own identity plus the ladder and budgets."""
    return _digest("procedure", [procedure])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_digests.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/hardy/evals/digests.py tests/unit/test_evals_digests.py
git commit -m "Add the five component digests"
```

---

### Task 5: The Entry schema

**Files:**
- Modify: `src/hardy/evals/problems.py:27-75` (the `Entry` class)
- Test: `tests/unit/test_evals_problems.py`

**Interfaces:**
- Consumes: `Occurrence`, `Review`, `Audit` (Tasks 2-3); `taxonomy.is_known` (Task 1); `digests.statement_digest` (Task 4).
- Produces: `Entry` with `title`, `msc`, `arxiv_override`, `override_reason`, `difficulty`, `occurrences`, `status`, `retired_reason`, `rationale`, `witness`, `witness_note`, `review`, `audit`, `fixtures`; plus `Entry.shard` (derived property) and `Entry.statement_digest()`.

- [ ] **Step 1: Write the failing test**

```python
def test_an_msc_code_must_be_finer_than_its_own_two_digit_class():
    # `13` is itself a valid MSC2020 entry, so a vendored-list check alone
    # would accept it -- and the precision would be gone permanently.
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=("13",)))
    assert Entry(**_entry(msc=("13A15",))).msc == ("13A15",)


def test_the_shard_is_derived_and_not_stored():
    entry = Entry(**_entry(msc=("13A15",)))
    assert entry.shard == "13"
    with pytest.raises(ValidationError):
        Entry(**_entry(shard="13"))


def test_an_unknown_msc_code_is_rejected():
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=("99Z99",)))


def test_an_empty_msc_is_rejected_rather_than_passing_vacuously():
    with pytest.raises(ValidationError):
        Entry(**_entry(msc=()))


def test_a_retired_entry_and_an_override_each_need_a_reason():
    with pytest.raises(ValidationError):
        Entry(**_entry(status="retired"))
    with pytest.raises(ValidationError):
        Entry(**_entry(arxiv_override="math.NT"))


def test_an_authored_entry_needs_a_rationale_and_cannot_carry_fixtures():
    with pytest.raises(ValidationError):
        Entry(**_entry(occurrences=()))
    assert Entry(**_entry(occurrences=(), rationale="states the pigeonhole bound")).rationale
    with pytest.raises(ValidationError):
        Entry(**_entry(occurrences=(), rationale="x", fixtures=("nakayama",)))


def test_a_null_witness_needs_a_note():
    with pytest.raises(ValidationError):
        Entry(**_entry(witness=None))
    assert Entry(**_entry(witness=None, witness_note="existence-heavy hypotheses")).witness is None


def test_title_is_optional_and_distinct_from_the_lean_name():
    entry = Entry(**_entry(title="Hilbert's Nullstellensatz"))
    assert entry.title == "Hilbert's Nullstellensatz"
    assert entry.name == "OddSquares"
    assert "Nullstellensatz" not in entry.declaration()
```

The last test is the §12.2 guard in its cheapest form: `declaration()` is what reaches the model, and `title` must not appear in it.

- [ ] **Step 2: Update the test helper**

Replace `_entry` at `tests/unit/test_evals_problems.py:15-24` with:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -v`
Expected: FAIL — `Entry` rejects `msc` as an unexpected field (`extra="forbid"`)

- [ ] **Step 4: Write the implementation**

Replace the `Entry` field block and add validators. Keep every existing field and validator (`_statement_only`, `_no_lean_command_injection`, `declaration`, `proposition`, `negation`) unchanged; `area` is removed.

```python
class Entry(FrozenModel):
    id: str = Field(pattern=SLUG.pattern)
    input: str = Field(min_length=1)
    name: str = Field(pattern=IDENT.pattern)
    title: str | None = None
    binders: str = ""
    conclusion: str = Field(min_length=1)
    imports: tuple[str, ...] = ("Mathlib",)
    expected: Literal["true", "false"]
    twin_of: str | None = None
    source: Literal["textbook", "classical", "mathlib-gap", "competition"]
    msc: tuple[str, ...] = Field(min_length=1)
    arxiv_override: str | None = None
    override_reason: str | None = None
    difficulty: Literal["routine", "substantial", "qualifying", "research-adjacent"]
    occurrences: tuple[Occurrence, ...] = ()
    rationale: str | None = None
    witness: str | None = None
    witness_note: str | None = None
    status: Literal["candidate", "active", "retired"] = "candidate"
    retired_reason: str | None = None
    review: Review | None = None
    audit: tuple[Audit, ...] = ()
    fixtures: tuple[str, ...] = ()

    @property
    def shard(self) -> str:
        """Derived, never stored: a stored shard is a derived value in the corpus."""
        return self.msc[0][:2]

    @model_validator(mode="after")
    def _codes_are_known_and_finer_than_their_class(self) -> Entry:
        for code in self.msc:
            if len(code) <= 2:
                raise ValueError(
                    f"{code!r} is no finer than its own 2-digit class: a bare class is what a "
                    "tagger writes when they did not look (spec §2)"
                )
            if not taxonomy.is_known(code):
                raise ValueError(f"unknown MSC2020 code: {code!r}")
        return self

    @model_validator(mode="after")
    def _reasons_accompany_the_states_that_need_them(self) -> Entry:
        if self.status == "retired" and not (self.retired_reason or "").strip():
            raise ValueError("a retired entry must record why")
        if self.arxiv_override is not None:
            if not (self.override_reason or "").strip():
                raise ValueError("an arxiv_override must record why")
            if self.arxiv_override not in taxonomy.arxiv_classes():
                raise ValueError(f"arxiv_override outside the mapping codomain: {self.arxiv_override!r}")
        if self.witness is None and not (self.witness_note or "").strip():
            raise ValueError("witness: null must record why no witness can be produced")
        return self

    @model_validator(mode="after")
    def _authored_entries_are_self_describing_and_carry_no_fixtures(self) -> Entry:
        if self.occurrences:
            return self
        if not (self.rationale or "").strip():
            raise ValueError("an entry with no occurrences must record a rationale (spec §2.2)")
        if self.fixtures:
            raise ValueError("an authored entry has no primary occurrence, so §9.0's antecedent "
                             "check cannot apply: it may not carry fixtures")
        return self

    @model_validator(mode="after")
    def _binders_never_carry_an_antecedent(self) -> Entry:
        """An antecedent in `binders` reaches the bare condition too (spec §9.1)."""
        for fixture in self.fixtures:
            if fixture in self.binders:
                raise ValueError(f"binders mention fixture {fixture!r}: an antecedent must never "
                                 "reach the bare condition")
        return self

    def statement_digest(self, fixture_digests: tuple[str, ...] = ()) -> str:
        return digests.statement_digest(
            name=self.name, binders=self.binders, conclusion=self.conclusion,
            imports=self.imports, witness=self.witness, witness_note=self.witness_note,
            fixture_digests=fixture_digests,
        )
```

Add to the module imports:

```python
from . import digests, taxonomy
```

Add to `taxonomy.py`:

```python
@cache
def arxiv_classes() -> frozenset[str]:
    """The mapping's codomain -- what an `arxiv_override` may name."""
    return frozenset(_mapping()["arxiv"].values())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/problems.py src/hardy/evals/taxonomy.py tests/unit/test_evals_problems.py
git commit -m "Grow the Entry schema: classification, lifecycle, provenance, witness"
```

---

### Task 6: ProblemSet scaling and the twin invariant

**Files:**
- Modify: `src/hardy/evals/problems.py:78-108` (`ProblemSet`)
- Test: `tests/unit/test_evals_problems.py` (append)

**Interfaces:**
- Consumes: `Entry` (Task 5).
- Produces: `ProblemSet.by_id` as an O(1) dict lookup; `ProblemSet.index` property.

- [ ] **Step 1: Write the failing test**

```python
def test_duplicate_detection_is_linear_not_quadratic():
    import time

    entries = [_entry(id=f"e-{i}", name=f"E{i}") for i in range(5000)]
    start = time.perf_counter()
    problems = ProblemSet(entries=tuple(Entry(**e) for e in entries))
    assert time.perf_counter() - start < 5.0
    assert problems.by_id("e-4999").name == "E4999"


def test_a_false_twin_inherits_its_targets_primary_msc():
    target = _entry(id="sq-ge", name="SqGe", msc=("26D",))
    twin = _entry(id="sq-le", name="SqLe", msc=("11A",), expected="false", twin_of="sq-ge")
    with pytest.raises(ValidationError):
        ProblemSet(entries=(Entry(**target), Entry(**twin)))

    ok = _entry(id="sq-le", name="SqLe", msc=("26D",), expected="false", twin_of="sq-ge")
    assert len(ProblemSet(entries=(Entry(**target), Entry(**ok))).entries) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -k "linear or twin_inherits" -v`
Expected: FAIL — the 5000-entry construction takes far longer than 5s, and the MSC drift is accepted

- [ ] **Step 3: Write the implementation**

Replace `ProblemSet._consistent` and `by_id`:

```python
    @model_validator(mode="after")
    def _consistent(self) -> ProblemSet:
        for label, seen in (("id", [e.id for e in self.entries]), ("name", [e.name for e in self.entries])):
            dupes = sorted(v for v, count in Counter(seen).items() if count > 1)
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(dupes)}")
        by_id = {e.id: e for e in self.entries}
        for entry in self.entries:
            if entry.expected == "true" and entry.twin_of is not None:
                raise ValueError(f"{entry.id}: a true entry has no twin_of")
            if entry.expected == "false":
                target = by_id.get(entry.twin_of or "")
                if target is None:
                    raise ValueError(f"{entry.id}: twin_of must name an entry in the list")
                if target.expected != "true":
                    raise ValueError(f"{entry.id}: twin_of must name a true entry, not a twin")
                if entry.msc[0] != target.msc[0]:
                    raise ValueError(
                        f"{entry.id}: a twin is in the same field as the statement it perturbs; "
                        f"{entry.msc[0]} drifts from {target.msc[0]}"
                    )
        return self

    @cached_property
    def index(self) -> dict[str, Entry]:
        return {e.id: e for e in self.entries}

    def by_id(self, id: str) -> Entry:
        try:
            return self.index[id]
        except KeyError:
            raise KeyError(id) from None
```

Add imports: `from collections import Counter` and `from functools import cached_property`. `FrozenModel` is `frozen=True`, so allow the cache with `model_config = ConfigDict(extra="forbid", frozen=True, ignored_types=(cached_property,))` on `ProblemSet`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_problems.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/evals/problems.py tests/unit/test_evals_problems.py
git commit -m "Make ProblemSet linear, and hold a twin to its target's field"
```

---

### Task 7: The sharded loader

**Files:**
- Create: `src/hardy/evals/corpus.py`
- Test: `tests/unit/test_evals_corpus.py`

**Interfaces:**
- Consumes: `ProblemSet`, `Entry` (Tasks 5-6).
- Produces: `load_corpus(root: Path) -> ProblemSet`, `shard_path(root, code) -> Path`, `CorpusError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

```python
"""Sharded loading: many files in, one validated set out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hardy.evals.corpus import CorpusError, load_corpus


def _write(root: Path, shard: str, entries: list[dict]) -> None:
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "problems" / f"{shard}.json").write_text(
        json.dumps({"schema_version": 1, "corpus_version": "0.1.0", "entries": entries}),
        encoding="utf-8",
    )


def test_shards_are_concatenated_into_one_set(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    _write(tmp_path, "20", [entry_dict(id="b", name="B", msc=("20D",))])
    problems = load_corpus(tmp_path)
    assert {e.id for e in problems.entries} == {"a", "b"}


def test_an_id_duplicated_across_two_shards_is_rejected(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    _write(tmp_path, "20", [entry_dict(id="a", name="B", msc=("20D",))])
    with pytest.raises(CorpusError, match="duplicate id"):
        load_corpus(tmp_path)


def test_an_entry_filed_in_the_wrong_shard_is_rejected(tmp_path, entry_dict):
    _write(tmp_path, "20", [entry_dict(id="a", name="A", msc=("13A15",))])
    with pytest.raises(CorpusError, match="belongs in shard 13"):
        load_corpus(tmp_path)
```

The third test is what keeps the derived shard honest: the filename must agree with `entry.shard`, or the derivation is a fiction.

- [ ] **Step 2: Add the shared fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def entry_dict():
    """A minimal valid corpus entry, overridable per test."""

    def make(**overrides) -> dict:
        base = {
            "id": "odd-squares", "input": "If $a$ and $b$ are odd, $a^2+b^2$ is not a square.",
            "name": "OddSquares", "binders": "(a b : ℤ)", "conclusion": "¬ IsSquare (a ^ 2 + b ^ 2)",
            "expected": "true", "source": "classical", "msc": ("11A",),
            "difficulty": "substantial", "status": "candidate", "witness": "⟨1, 1⟩",
            "occurrences": [{"source_id": "hardy-wright", "locator": [6, 1, 3]}],
        }
        base.update(overrides)
        return base

    return make
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.evals.corpus'`

- [ ] **Step 4: Write the implementation**

```python
"""The corpus as a directory: sharded loading, tombstones, manifest, checks.

`corpus/` is data. This module is its only reader and writer, so the rule that
the corpus holds statements only (spec §1) has one place to be enforced.
"""
from __future__ import annotations

import json
from pathlib import Path

from .problems import Entry, ProblemSet


class CorpusError(RuntimeError):
    """The corpus on disk is not one a consumer may trust."""


def shard_path(root: Path, code: str) -> Path:
    return root / "problems" / f"{code[:2]}.json"


def load_corpus(root: Path) -> ProblemSet:
    shards = sorted((root / "problems").glob("*.json"))
    if not shards:
        raise CorpusError(f"no problem shards under {root / 'problems'}")
    entries: list[Entry] = []
    seen: dict[str, Path] = {}
    for path in shards:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload["entries"]:
            entry = Entry.model_validate(raw)
            if entry.id in seen:
                raise CorpusError(f"duplicate id {entry.id!r} in {path.name} and {seen[entry.id].name}")
            if entry.shard != path.stem:
                raise CorpusError(f"{entry.id!r} is filed in {path.name} but belongs in shard {entry.shard}")
            seen[entry.id] = path
            entries.append(entry)
    return ProblemSet(entries=tuple(entries))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/corpus.py tests/unit/test_evals_corpus.py tests/conftest.py
git commit -m "Load the corpus from MSC-sharded files"
```

---

### Task 8: The tombstone registry

**Files:**
- Create: `corpus/tombstones.json`
- Modify: `src/hardy/evals/corpus.py`
- Test: `tests/unit/test_evals_corpus.py` (append)

**Interfaces:**
- Consumes: `load_corpus` (Task 7).
- Produces: `load_tombstones(root) -> dict[str, str]`, `tombstone_issues(problems, tombstones) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
def test_every_live_id_must_be_registered_and_reuse_of_a_freed_id_is_rejected(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"schema_version": 1, "issued": {"a": "2026-09-03", "gone": "2026-08-01"}}),
        encoding="utf-8",
    )
    from hardy.evals.corpus import load_tombstones, tombstone_issues

    problems = load_corpus(tmp_path)
    assert tombstone_issues(problems, load_tombstones(tmp_path)) == []

    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",)),
                            entry_dict(id="gone", name="Gone", msc=("13A15",))])
    issues = tombstone_issues(load_corpus(tmp_path), load_tombstones(tmp_path))
    assert any("gone" in issue and "retired" in issue for issue in issues)


def test_an_unregistered_id_is_reported(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="fresh", name="Fresh", msc=("13A15",))])
    (tmp_path / "tombstones.json").write_text(
        json.dumps({"schema_version": 1, "issued": {}}), encoding="utf-8")
    from hardy.evals.corpus import load_tombstones, tombstone_issues

    issues = tombstone_issues(load_corpus(tmp_path), load_tombstones(tmp_path))
    assert any("fresh" in issue and "not registered" in issue for issue in issues)
```

The registry holds *every id ever issued*, so a live entry appearing in it is correct and expected — what it forbids is a **new** entry claiming an id whose entry is no longer present as `retired`.

- [ ] **Step 2: Create the registry**

`corpus/tombstones.json`:

```json
{"schema_version": 1, "issued": {}}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -k tombstone -v`
Expected: FAIL with `ImportError: cannot import name 'load_tombstones'`

- [ ] **Step 4: Write the implementation**

```python
def load_tombstones(root: Path) -> dict[str, str]:
    path = root / "tombstones.json"
    if not path.exists():
        raise CorpusError(f"missing id registry: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["issued"]


def tombstone_issues(problems: ProblemSet, issued: dict[str, str]) -> list[str]:
    """Registered ids that are gone, and live ids that were never registered.

    The registry lists every id ever issued, so a live entry appearing in it is
    normal. What it prevents is a *new* entry claiming an id whose original
    entry was deleted rather than retired -- which the current-corpus
    uniqueness check cannot see (spec §2.2).
    """
    live = {entry.id: entry for entry in problems.entries}
    issues = [f"{id!r} is not registered in tombstones.json" for id in live if id not in issued]
    for id in issued:
        entry = live.get(id)
        if entry is not None and entry.status != "retired":
            continue
        if entry is None:
            issues.append(f"{id!r} was issued but is absent: a freed id must remain as retired")
    return sorted(issues)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add corpus/tombstones.json src/hardy/evals/corpus.py tests/unit/test_evals_corpus.py
git commit -m "Add the append-only id registry"
```

---

### Task 9: The manifest digest and the version gate

**Files:**
- Modify: `src/hardy/evals/corpus.py`
- Create: `corpus/CHANGELOG.md`
- Test: `tests/unit/test_evals_corpus.py` (append)

**Interfaces:**
- Consumes: `CorpusError` (Task 7).
- Produces: `manifest_digest(root) -> str`, `changelog_head(root) -> tuple[str, str]`, `version_issues(root) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_manifest_covers_every_content_file_and_ignores_measurements(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    (tmp_path / "tombstones.json").write_text('{"schema_version": 1, "issued": {"a": "2026-09-03"}}', encoding="utf-8")
    from hardy.evals.corpus import manifest_digest

    before = manifest_digest(tmp_path)
    (tmp_path / "measurements").mkdir()
    (tmp_path / "measurements" / "baseline-abc-host.json").write_text("{}", encoding="utf-8")
    assert manifest_digest(tmp_path) == before, "a baseline re-sweep must not demand a version bump"

    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",), conclusion="True")])
    assert manifest_digest(tmp_path) != before


def test_the_changelog_head_must_match_the_corpus_version(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    (tmp_path / "tombstones.json").write_text('{"schema_version": 1, "issued": {"a": "2026-09-03"}}', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0 - 2026-09-03\n\n- added `a`\n", encoding="utf-8")
    from hardy.evals.corpus import version_issues

    issues = version_issues(tmp_path)
    assert any("0.1.0" in issue and "0.2.0" in issue for issue in issues)
```

`measurements/` sits under `corpus/` but is **outside** the manifest: re-sweeping a baseline against a new Mathlib revision changes no content and must not manufacture a corpus release (spec §3).

- [ ] **Step 2: Create the changelog**

`corpus/CHANGELOG.md`:

```markdown
# Corpus changelog

All notable changes to the corpus. Entries cite ids.

## 0.1.0 - 2026-09-03

- Initial corpus: the twenty entries migrated from `evals/problems.json`.
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -k "manifest or changelog" -v`
Expected: FAIL with `ImportError: cannot import name 'manifest_digest'`

- [ ] **Step 4: Write the implementation**

```python
import hashlib
import re

CONTENT = ("problems", "taxonomy", "fixtures")
CONTENT_FILES = ("sources.json", "analysis-plan.json", "tombstones.json", "SCHEMA.md")
HEAD = re.compile(r"^## (\d+\.\d+\.\d+)\s+-\s+(\S+)", re.MULTILINE)


def _content_paths(root: Path) -> list[Path]:
    paths = [root / name for name in CONTENT_FILES if (root / name).exists()]
    for directory in CONTENT:
        paths.extend(sorted((root / directory).rglob("*.json")))
    return sorted(paths)


def manifest_digest(root: Path) -> str:
    """A hash over every content file. `measurements/` is deliberately absent."""
    hasher = hashlib.sha256()
    for path in _content_paths(root):
        hasher.update(str(path.relative_to(root)).encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def changelog_head(root: Path) -> tuple[str, str]:
    match = HEAD.search((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if match is None:
        raise CorpusError("CHANGELOG.md has no version heading")
    return match.group(1), match.group(2)


def corpus_version(root: Path) -> str:
    shards = sorted((root / "problems").glob("*.json"))
    versions = {json.loads(p.read_text(encoding="utf-8"))["corpus_version"] for p in shards}
    if len(versions) != 1:
        raise CorpusError(f"shards disagree on corpus_version: {sorted(versions)}")
    return versions.pop()


def version_issues(root: Path) -> list[str]:
    declared = corpus_version(root)
    head, _ = changelog_head(root)
    if declared != head:
        return [f"corpus_version {declared} does not match the changelog head {head}"]
    return []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add corpus/CHANGELOG.md src/hardy/evals/corpus.py tests/unit/test_evals_corpus.py
git commit -m "Bind the changelog head to a corpus manifest digest"
```

---

### Task 10: Migrate the twenty entries

**Files:**
- Create: `corpus/problems/{11,12,20,26}.json`, `corpus/sources.json`, `corpus/LICENSE`, `corpus/SCHEMA.md`
- Modify: `corpus/tombstones.json`, `corpus/CHANGELOG.md`
- Delete: `evals/problems.json`
- Modify: `src/hardy/evals/commands.py:68` (`DEFAULT_PROBLEMS`)
- Test: `tests/unit/test_evals_corpus.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: a real corpus the rest of the suite loads.

- [ ] **Step 1: Write the failing test**

```python
def test_the_shipped_corpus_loads_and_is_internally_consistent():
    from hardy.evals.corpus import load_corpus, load_tombstones, tombstone_issues, version_issues

    root = Path(__file__).resolve().parents[2] / "corpus"
    problems = load_corpus(root)
    assert len(problems.entries) == 20
    assert tombstone_issues(problems, load_tombstones(root)) == []
    assert version_issues(root) == []


def test_every_shipped_input_carries_latex_ready_prose():
    from hardy.evals.corpus import load_corpus

    root = Path(__file__).resolve().parents[2] / "corpus"
    for entry in load_corpus(root).entries:
        assert entry.input.strip(), entry.id
        assert entry.msc and len(entry.msc[0]) > 2, entry.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -k shipped -v`
Expected: FAIL — `corpus/problems/` holds no shards yet

- [ ] **Step 3: Write the migration script and run it once**

Create `scripts/migrate_corpus.py`. It is a one-shot: run it, commit the output, delete the script in the same commit.

```python
"""One-shot: evals/problems.json -> corpus/problems/<shard>.json.

MSC codes and difficulty are assigned by hand below because no rule derives
them from `area`; that hand mapping is the whole point of the migration.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# id -> (msc, difficulty, LaTeX-bearing input). Occurrences are left empty and
# the entries are marked authored with a rationale: the original twenty were
# written for the harness, not harvested, so inventing citations would be false.
ASSIGN: dict[str, tuple[str, str, str]] = {
    "two-plus-two": ("11A", "routine", "$2 + 2 = 4$."),
    "sq-sum-ge-two-mul": ("26D", "routine", "For real $x$ and $y$, $2xy \\le x^2 + y^2$."),
    "euler-polynomial-small": ("11A", "routine", "For every natural $n < 10$, $n^2 + n + 41$ is prime."),
    "sqrt-two-irrational": ("11J", "substantial", "$\\sqrt{2}$ is irrational."),
    "prime-order-cyclic": ("20D", "substantial", "A finite group of prime order is cyclic."),
    "sqrt-six-irrational": ("11J", "substantial", "$\\sqrt{6}$ is irrational."),
    "odd-sum": ("11A", "routine", "For every natural $n$, $\\sum_{i<n} (2i+1) = n^2$."),
    "six-divides-consecutive": ("11A", "routine", "For every natural $n$, $6 \\mid n(n+1)(n+2)$."),
    "exponent-two-abelian": ("20A", "substantial", "A group in which every element squares to the identity is abelian."),
    "sqrt-two-plus-sqrt-three": ("12F", "qualifying", "$\\sqrt{2} + \\sqrt{3}$ is irrational."),
    "cube-root-two-irrational": ("11J", "substantial", "$\\sqrt[3]{2}$ is irrational."),
    "sum-cubes-square": ("11A", "routine", "For every natural $n$, $\\sum_{i \\le n} i^3 = \\left(\\sum_{i \\le n} i\\right)^2$."),
    "pigeonhole-residues": ("11A", "substantial", "Among any $n+1$ integers, two are congruent modulo $n$."),
    "am-gm-two": ("26D", "routine", "For non-negative reals $a$ and $b$, $\\sqrt{ab} \\le (a+b)/2$."),
    "odd-squares-sum-not-square": ("11A", "substantial", "If $a$ and $b$ are odd, $a^2 + b^2$ is not a perfect square."),
    "order-four-cyclic": ("20D", "substantial", "Every group of order $4$ is cyclic."),
    "squares-sum-not-square": ("11A", "substantial", "For all integers $a$ and $b$, $a^2 + b^2$ is not a perfect square."),
    "sq-sum-le-two-mul": ("26D", "routine", "For real $x$ and $y$, $x^2 + y^2 \\le 2xy$."),
    "euler-polynomial-all": ("11A", "substantial", "For every natural $n$, $n^2 + n + 41$ is prime."),
    "sqrt-two-plus-sqrt-three-rational": ("12F", "qualifying", "$\\sqrt{2} + \\sqrt{3}$ is rational."),
}

RATIONALE = "Authored for the harness slice, not harvested from a text; kept as a smoke set."


def main() -> None:
    old = json.loads((ROOT / "evals" / "problems.json").read_text(encoding="utf-8"))
    shards: dict[str, list[dict]] = defaultdict(list)
    issued: dict[str, str] = {}
    for entry in old["entries"]:
        msc, difficulty, text = ASSIGN[entry["id"]]
        entry.pop("area", None)
        entry.update({
            "input": text, "msc": [msc], "difficulty": difficulty, "occurrences": [],
            "rationale": RATIONALE, "status": "candidate",
            "witness": None, "witness_note": "migrated before A6 existed; needs a witness or a note",
        })
        shards[msc[:2]].append(entry)
        issued[entry["id"]] = "2026-09-03"

    (ROOT / "corpus" / "problems").mkdir(parents=True, exist_ok=True)
    for shard, entries in shards.items():
        (ROOT / "corpus" / "problems" / f"{shard}.json").write_text(
            json.dumps({"schema_version": 1, "corpus_version": "0.1.0", "entries": entries},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "corpus" / "tombstones.json").write_text(
        json.dumps({"schema_version": 1, "issued": issued}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

Run: `uv run python scripts/migrate_corpus.py`

- [ ] **Step 4: Write `sources.json`, `LICENSE`, and `SCHEMA.md`**

`corpus/sources.json` — empty but well-formed; phase 3 fills it.

```json
{"schema_version": 1, "sources": {}}
```

`corpus/LICENSE` — CC-BY-4.0 full text (fetch from `https://creativecommons.org/licenses/by/4.0/legalcode.txt`).

`corpus/SCHEMA.md` — a reader's guide to the entry schema, the id policy, and the taxonomy rules, pointing at the spec for rationale. One page; the field table from spec §2 plus the four Global Constraints above.

- [ ] **Step 5: Re-point the defaults and delete the old file**

In `src/hardy/evals/commands.py`, change `DEFAULT_PROBLEMS` to `Path("corpus")` and have callers use `load_corpus`. Delete `evals/problems.json`. Delete `scripts/migrate_corpus.py`.

- [ ] **Step 6: Run the full suite**

Run: `uv run --extra test pytest`
Expected: PASS. Existing tests that read `evals/problems.json` now read the corpus; fix any that hard-code the old path.

- [ ] **Step 7: Commit**

```bash
git add corpus tests src/hardy/evals/commands.py
git rm evals/problems.json scripts/migrate_corpus.py
git commit -m "Migrate the twenty entries into the sharded corpus"
```

---

### Task 11: `corpus check` and `corpus report`

**Files:**
- Modify: `src/hardy/evals/corpus.py`, `src/hardy/evals/commands.py:64-93`
- Test: `tests/unit/test_evals_corpus.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `check_issues(root) -> list[str]`; CLI verbs `hardy evals corpus check` and `hardy evals corpus report`.

- [ ] **Step 1: Write the failing test**

```python
def test_check_gathers_every_class_of_issue(tmp_path, entry_dict):
    _write(tmp_path, "13", [entry_dict(id="a", name="A", msc=("13A15",))])
    (tmp_path / "tombstones.json").write_text('{"schema_version": 1, "issued": {}}', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9 - 2026-09-03\n\n- x\n", encoding="utf-8")
    from hardy.evals.corpus import check_issues

    issues = check_issues(tmp_path)
    assert any("not registered" in i for i in issues)
    assert any("changelog head" in i for i in issues)


def test_a_clean_corpus_reports_no_issues():
    from hardy.evals.corpus import check_issues

    assert check_issues(Path(__file__).resolve().parents[2] / "corpus") == []


def test_report_counts_by_group_and_status():
    from hardy.evals.corpus import report

    lines = report(Path(__file__).resolve().parents[2] / "corpus")
    assert any("candidate" in line for line in lines)
    assert any("number-theory" in line for line in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_corpus.py -k "check or report" -v`
Expected: FAIL with `ImportError: cannot import name 'check_issues'`

- [ ] **Step 3: Write the implementation**

```python
from collections import Counter

from . import taxonomy


def check_issues(root: Path) -> list[str]:
    """Every mechanical objection to the corpus on disk, gathered not raised."""
    try:
        problems = load_corpus(root)
    except CorpusError as exc:
        return [str(exc)]
    issues = tombstone_issues(problems, load_tombstones(root))
    issues.extend(version_issues(root))
    for entry in problems.entries:
        for code in entry.msc:
            if not taxonomy.is_known(code):
                issues.append(f"{entry.id!r}: unknown MSC code {code!r}")
    return sorted(issues)


def report(root: Path) -> list[str]:
    """Coverage: where the corpus actually is, by group, status and difficulty."""
    problems = load_corpus(root)
    groups = Counter(taxonomy.group_of(e.msc[0]) for e in problems.entries)
    statuses = Counter(e.status for e in problems.entries)
    difficulties = Counter(e.difficulty for e in problems.entries)
    twins = sum(1 for e in problems.entries if e.expected == "false")
    lines = [f"{len(problems.entries)} entries, {twins} twins", ""]
    for label, counter in (("group", groups), ("status", statuses), ("difficulty", difficulties)):
        lines.append(f"by {label}:")
        lines.extend(f"  {key:<24} {count}" for key, count in sorted(counter.items()))
        lines.append("")
    return lines
```

- [ ] **Step 4: Wire the CLI**

In `commands.py`'s `add_parser`, beside `baseline`/`run`/`check`:

```python
    corpus = verbs.add_parser("corpus", help="the corpus directory: mechanical checks and coverage")
    corpus_verbs = corpus.add_subparsers(dest="corpus_verb", required=True)
    for verb, helptext in (("check", "report every mechanical objection"), ("report", "coverage by group, status, difficulty")):
        sub = corpus_verbs.add_parser(verb, help=helptext)
        sub.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
```

And dispatch in `main`:

```python
    if args.verb == "corpus":
        from .corpus import check_issues, report

        if args.corpus_verb == "check":
            issues = check_issues(args.corpus)
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1 if issues else 0
        for line in report(args.corpus):
            print(line)
        return 0
```

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra test pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals tests/unit/test_evals_corpus.py
git commit -m "Add corpus check and corpus report"
```

---

### Task 12: A6, the non-vacuity witness check

**Files:**
- Modify: `src/hardy/evals/sweep.py`
- Test: `tests/unit/test_evals_sweep.py` (append)

**Interfaces:**
- Consumes: `Entry.witness` (Task 5), the existing `make_elaborate` from `commands.py:113`.
- Produces: `witness_source(entry) -> str | None`, `witness_verdict(entry, elaborate) -> str`.

- [ ] **Step 1: Write the failing test**

Hermetic: a fake elaborator, no Lean.

```python
def test_a_witness_is_checked_as_an_example_against_the_binders():
    from hardy.evals.sweep import witness_source

    entry = Entry(**_entry(binders="(n : ℕ) (h : n > 0)", conclusion="n ≥ 1", witness="⟨1, by norm_num⟩"))
    source = witness_source(entry)
    assert "example" in source and "⟨1, by norm_num⟩" in source
    assert "∃" in source, "the binders must be existentially closed, or nothing is proved"


def test_an_entry_with_no_witness_reports_unwitnessed_rather_than_failing():
    from hardy.evals.sweep import witness_verdict

    entry = Entry(**_entry(witness=None, witness_note="existence-heavy"))
    assert witness_verdict(entry, elaborate=lambda _: None) == "unwitnessed"


def test_a_witness_the_kernel_rejects_is_reported_as_broken():
    from hardy.evals.sweep import witness_verdict

    entry = Entry(**_entry(binders="(n : ℕ) (h : n > 0)", conclusion="n ≥ 1", witness="⟨0, by norm_num⟩"))
    failing = lambda source: Elaboration(ok=False, message="norm_num failed")
    assert witness_verdict(entry, elaborate=failing) == "broken"
```

A bare term proves nothing without a stated expected type: for `(n : ℕ) (h : n > 0)` the harness must build `example : ∃ n : ℕ, n > 0 := <witness>` and let the kernel check it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_evals_sweep.py -k witness -v`
Expected: FAIL with `ImportError: cannot import name 'witness_source'`

- [ ] **Step 3: Write the implementation**

```python
def witness_source(entry: Entry) -> str | None:
    """The Lean A6 compiles: the binders existentially closed, proved by the witness.

    A bare term establishes nothing without an expected type -- for
    `(n : ℕ) (h : n > 0)` merely elaborating the binders says nothing about
    whether compatible values exist (spec §7).
    """
    if entry.witness is None:
        return None
    binders = entry.binders.strip()
    if not binders:
        return f"example : True := trivial  -- {entry.id}: no hypotheses to satisfy"
    return f"example : ∃ {binders}, True := {entry.witness.strip()}"


def witness_verdict(entry: Entry, elaborate) -> str:
    source = witness_source(entry)
    if source is None:
        return "unwitnessed"
    result = elaborate(source)
    return "witnessed" if result is not None and result.ok else "broken"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_evals_sweep.py -v`
Expected: PASS

- [ ] **Step 5: Add the real-toolchain check**

```python
@pytest.mark.real_toolchain
def test_a_true_witness_passes_the_real_kernel(lean_elaborate):
    from hardy.evals.sweep import witness_verdict

    entry = Entry(**_entry(binders="(n : ℕ) (h : n > 0)", conclusion="n ≥ 1", witness="⟨1, by norm_num⟩"))
    assert witness_verdict(entry, elaborate=lean_elaborate) == "witnessed"
```

- [ ] **Step 6: Commit**

```bash
git add src/hardy/evals/sweep.py tests/unit/test_evals_sweep.py
git commit -m "Add A6: a kernel-checked non-vacuity witness"
```

---

## Self-Review

**Spec coverage.** §1 layout → Tasks 7-10. §2 schema and validators → Tasks 2, 3, 5. §2.1 occurrences and locators → Task 2. §2.2 lifecycle and tombstones → Tasks 3, 8. §3 digests, manifest, version gate → Tasks 4, 9. §4 scaling fixes → Task 6. §5 taxonomy → Task 1. §7 A6 → Task 12. §12.1 classification pane → Task 3 (the `Review` record carries `msc` and `group`; the editor itself is phase 3).

**Deliberately out of scope**, each belonging to a later phase per spec §11: selection filters, `export`, `describe_selection`, `FieldAggregate` and `compare` (phase 2); `sources.json` content, fixtures behaviour, the review editor (phase 3); the multi-backend runner (phase 4); discrimination and the audit queue (phase 5). `corpus/analysis-plan.json` and `corpus/fixtures/` are referenced by `manifest_digest` but created empty or absent until their phase.

**Known gap carried forward.** The migrated twenty get `witness: null` with a note, because they predate A6 and inventing witnesses during a mechanical migration would be worse than recording the debt. Phase 3 clears it.

---

## Deviations from the plan, as built

Recorded here because the plan is what a reviewer reads first, and each of
these is a place the shipped code deliberately does not match it.

**`statement_digest` sheds `fixture_digests`.** Task 4 gave it a
`fixture_digests` parameter, which contradicts the spec's own dependency table
(§3): A1–A3 and A6 depend on statement + environment + procedure, and only A4,
A5 and B4 depend on the fixture set. Because `prompt_digest` wraps the
statement digest, folding fixtures in would have staled B1–B3 too — every
condition that never loads a fixture. `Entry` now exposes `statement_digest()`,
`prompt_digest()` and `fixture_set_digest(resolved)` as three separate
components.

**The version gate binds the manifest digest.** Task 9 compared only the
declared `corpus_version` with the changelog head, which cannot see an
*unversioned* edit — the spec's stated reason for having a manifest at all. The
changelog head now reads `## <version> - <date> - manifest <digest>`, and
`version_issues` recomputes and compares it.

**`schema_version` is 2, not 1.** The entry format changed incompatibly (`area`
removed, several required fields added) and the container changed from one
`ProblemSet` file to shard envelopes. A schema-1 consumer would otherwise get
no signal. Shards are validated against a frozen `Shard` envelope model, and
every parse or validation failure becomes a `CorpusError` rather than escaping
as a raw `KeyError` into `corpus check`.

**`status: "active"` is gated on a current faithful review.** The spec makes
the review required for an active entry; the plan's validators did not enforce
it. An active entry now needs a `faithful` review whose statement digest,
prompt digest, `msc` and reporting group all match the entry as it stands, so
an edit or a re-tag drops it back to `candidate` rather than leaving a stale
approval standing.

**A6 is wired into the sweep.** Task 12 defined `witness_source` and
`witness_verdict` but nothing called them, so a kernel-rejected witness would
have completed a baseline unchanged. `EntryBaseline` now records
`witness: witnessed | broken | unwitnessed`, and a broken witness is a baseline
problem that makes `hardy evals baseline` exit non-zero.

**`environment_digest` and `procedure_digest` are persisted and enforced.**
Task 4 defined them as free helpers nothing stored. `Baseline` now records
both and `staleness` checks them, so a change to the sweep logic, the axiom
parser or the witness checker stales A-group measurements even when the tactic
constants did not move.

**Occurrence sources are validated.** `check_issues` loads `sources.json` and
reports every occurrence citing a text it does not carry — primary provenance
decides the field, the level C6 reports on, and the antecedent rule.

**Taxonomy roll-ups reject unknown full codes.** `field_of`, `group_of` and
`arxiv_of` resolved on the two-digit prefix alone, so `13ZZZ` came back as
valid commutative algebra. They now verify the whole code first, which is what
protects callers that are not an `Entry` — the editor, a report, a third
party's script over the published corpus.

### Known gaps, deliberately left to their phase

- **Candidate entries are not yet excluded from the headline.** All twenty
  migrated entries are `candidate`, and `select`/`aggregate` still include
  every selected entry by tier alone. Reporting is phase 2 (spec §11) and the
  `status` filter belongs with it; until then a headline computed from this
  corpus is a headline over unreviewed candidates.
- **`Review` does not bind the origin it was read against.** `occurrences` and
  `rationale` are in no component digest, so changing an entry's primary
  citation leaves an approval current although the reviewer no longer attests
  the stated origin. No review records exist yet; adding an origin digest is a
  spec change (§2.2) rather than an implementation fix.
- **A delete-and-reintroduce of one id inside a single commit is invisible** to
  a file-level check. The registry catches the deletion whenever it lands
  alone; catching both at once needs the merge-base diff the spec assigns to
  CI.
- **Scoreboard rows carry no prompt digest.** Nothing reuses a model run yet —
  every run is fresh — so there is no reuse decision for it to govern. It
  belongs with phase 2's reporting.
