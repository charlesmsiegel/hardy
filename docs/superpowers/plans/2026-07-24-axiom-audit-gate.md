# Axiom Audit Gate Implementation Plan

> **Status: implemented, with deviations. Read this note before the plan.**
>
> The gate is in: `src/hardy/audit.py` parses and classifies, `lean.py` emits the
> audit lines, and `runner.py`, `chat.py`, and `verifier.py` all grade on it.
> Tasks 1, 2, 3, 4, and 7 landed close to what is written below. The rest did
> not, and the plan is left unedited rather than rewritten so the difference is
> visible:
>
> - **The workspace changed shape underneath Task 5.** This plan was written
>   against a session that saved one `Main.lean` at the workspace root. Lean
>   sources now live in a tree under `lean/`, saves go through a shadow build,
>   and a save rebuilds every dependent. So the audit could not ride on the
>   check's elaboration: the declarations to audit span modules. It runs as one
>   extra elaboration over the built shadow tree instead, importing the oleans
>   the build just produced.
> - **Audit scope is the declarations, not the naming registry.** Every theorem
>   and lemma in the modules the save rebuilt is audited. The plan's rule that an
>   empty registry refuses the save is not implemented; `record_name` is not a
>   precondition of saving, and the existing test ordering stands.
> - **The interactive path fails closed rather than prompting.** An unapproved
>   axiom refuses the save and names both the axiom and what needs it, so the
>   model goes through the existing `request_assumption`. The at-audit approval
>   prompt (Task 6, `tests/test_approval.py`, `_admit`, `audit.printed`, the
>   `discovered_statement` lookup and its truncation handling) is **not built**.
> - **Not built, and still open:** the `save_latex` disclosure gate, the stored
>   verdict's `source_sha256` binding, registry-change invalidation, and drift
>   detection on an approved assumption's statement. All of them layer on top of
>   the gate rather than being part of it.
> - **Known limit — a guillemet axiom name containing a comma or a bracket.**
>   `audit.parse` splits the reported list on `,` and bounds it with `[…]`, so an
>   axiom named `«paper,main»` reads as two axioms and `«paper]main»` matches no
>   report at all. Both fail closed. Not fixed here, because a guillemet-named
>   *axiom* does not work anywhere else either: `_final_gates`' approval scan is
>   ASCII-only (`chat.py:354`) and never sees one, so a nesting-aware parser in
>   the most safety-critical function in the audit would fix one link of a chain
>   that is broken further up. Making guillemet axioms work end to end is its own
>   change; guillemet *declarations* are supported and tested.
>
> `TerminalReason` gained no `axioms_rejected` member: that string is `batch`'s
> own terminal reason, and `prove` keeps `UNEXPECTED_AXIOM` for both a hole and
> an assumption, because it is written to disk and read by things that never
> import Hardy.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hardy's formalization grade a consequence of an audited Lean axiom set rather than of a process exit code, in both the interactive and unattended paths.

**Architecture:** A new pure module `src/hardy/audit.py` parses `#print axioms` output and classifies the result against a standard axiom set and a set of human-approved assumptions. `LeanTools` learns to append audit lines to the source it checks, on the same Lean invocation. `runner.py` (unattended) fails closed on any non-clean verdict; `chat.py` (interactive) prompts a human about an unapproved axiom and hard-gates the save on a refusal. `sorryAx` is fatal in both and can never be approved.

**Tech Stack:** Python 3.11+, stdlib only (`re`, `dataclasses`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-24-axiom-audit-gate-design.md`

## Global Constraints

- Python 3.11+; every module starts with `from __future__ import annotations`.
- No new runtime dependencies. `audit.py` uses stdlib only.
- The whole suite stays hermetic: no Lean, no LaTeX, no model, no network. Run with `uv run --extra test pytest`.
- Fail closed at every uncertainty. An audit that cannot be established is a rejection, never a pass.
- `sorryAx` is fatal unconditionally. It is never offered for approval, even if its name appears in the approved set.
- Never describe generated Lean or TeX as safe (`AGENTS.md`).
- `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` must stay consistent with the code (`AGENTS.md:17`). Task 7 does this; do not skip it.
- Match the existing code style: dense, comments explain *why* rather than *what*, no docstring on obvious helpers.

## File Structure

| File | Responsibility |
|---|---|
| `src/hardy/audit.py` (create) | Pure parse + classify. No I/O, no subprocess. |
| `tests/test_audit.py` (create) | Table-driven unit tests for the above. |
| `src/hardy/lean.py` (modify) | Append `#print axioms` lines; expose the target declaration name. |
| `src/hardy/models.py` (modify) | `RunResult.axioms` becomes structured. |
| `src/hardy/runner.py` (modify) | Unattended fail-closed audit; `axioms_rejected` terminal reason. |
| `src/hardy/chat.py` (modify) | Interactive audit, prompt on unapproved, gate the save. |
| `src/hardy/cli.py` (modify) | Render the audit-finding approval prompt. |
| `tests/fake_lean.py` (modify) | Emit a test-chosen axiom set for `#print axioms`. |
| `tests/test_approval.py` (create) | The CLI prompt's two shapes. |

---

### Task 1: The audit parser

**Files:**
- Create: `src/hardy/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AxiomReport(declaration: str, axioms: tuple[str, ...])` and
  `parse(output: str, expected: Sequence[str]) -> tuple[AxiomReport, ...] | None`.
  Returns `None` when any expected declaration has no report or has more than one.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit.py`:

```python
from __future__ import annotations

from hardy.audit import AxiomReport, parse


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
    assert parse(output, ("T",)) == (AxiomReport("T", ("propext", "Classical.choice", "Quot.sound")),)


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


def test_reports_follow_the_requested_order():
    output = "'B' depends on axioms: [propext]\n'A' does not depend on any axioms"
    assert [report.declaration for report in parse(output, ("A", "B"))] == ["A", "B"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.audit'`

- [ ] **Step 3: Write the implementation**

Create `src/hardy/audit.py`:

```python
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# The axioms every ordinary Mathlib proof rests on. Their presence is not news.
STANDARD = frozenset({"propext", "Classical.choice", "Quot.sound"})
# A hole wearing an axiom's clothes. No human may approve this one.
FORBIDDEN = frozenset({"sorryAx"})

@dataclass(frozen=True)
class AxiomReport:
    declaration: str
    axioms: tuple[str, ...]


def _reports_for(output: str, name: str) -> list[tuple[str, ...]]:
    """Every report Lean printed for exactly this name.

    Searched name by name rather than by capturing whatever sits between two
    apostrophes: Lean names may contain apostrophes themselves, and `add_comm'`
    is ordinary in Mathlib. DOTALL because a long axiom list is wrapped across
    lines at the formatter's width.
    """
    quoted = re.escape(f"'{name}'")
    listed = re.findall(rf"{quoted}\s+depends on axioms:\s*\[(.*?)\]", output, re.DOTALL)
    empty = re.findall(rf"{quoted}\s+does not depend on any axioms", output)
    return [tuple(item.strip() for item in body.split(",") if item.strip()) for body in listed] + [() for _ in empty]


def parse(output: str, expected: Sequence[str]) -> tuple[AxiomReport, ...] | None:
    """What Lean said each declaration rests on, or None if it did not say.

    Returns None rather than a partial answer, because the caller's next move
    is to grade an artifact and a missing report must not read as a clean one.
    """
    reports = []
    for name in expected:
        entries = _reports_for(output, name)
        # Two reports for one name means something else printed one. Hardy
        # appends its audit lines last, but choosing a winner by position would
        # make the audit depend on output ordering rather than on Lean.
        if len(entries) != 1:
            return None
        reports.append(AxiomReport(name, entries[0]))
    return tuple(reports)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_audit.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/audit.py tests/test_audit.py
git commit -m "feat: parse what Lean says a declaration depends on"
```

---

### Task 2: The classifier

**Files:**
- Modify: `src/hardy/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `AxiomReport`, `STANDARD`, `FORBIDDEN` from Task 1.
- Produces:
  - `Verdict(status, reports, forbidden, unapproved, assumed)` with
    `status: str` in `{"clean", "modulo", "rejected"}`, all other fields
    `tuple[str, ...]` except `reports: tuple[AxiomReport, ...]`, plus
    `Verdict.as_dict() -> dict[str, Any]` returning JSON-plain values.
  - `classify(reports: Sequence[AxiomReport], approved: Iterable[str]) -> Verdict`
  - `dependents(reports: Sequence[AxiomReport], axiom: str) -> tuple[str, ...]`
  - `describe(verdict: Verdict) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit.py`:

```python
from hardy.audit import Verdict, classify, dependents, describe


def report(*axioms: str, name: str = "T") -> AxiomReport:
    return AxiomReport(name, axioms)


def test_standard_axioms_alone_are_clean():
    verdict = classify([report("propext", "Classical.choice", "Quot.sound")], ())
    assert verdict.status == "clean"
    assert verdict.assumed == () and verdict.unapproved == ()


def test_no_axioms_at_all_is_clean():
    assert classify([report()], ()).status == "clean"


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
    assert "Papers.Smith.main" in describe(classify([report("Papers.Smith.main")], {"Papers.Smith.main"}))


def test_as_dict_is_json_plain():
    import json

    verdict = classify([report("propext", "X")], {"X"})
    assert json.loads(json.dumps(verdict.as_dict())) == {
        "status": "modulo",
        "declarations": [{"name": "T", "axioms": ["propext", "X"]}],
        "forbidden": [],
        "unapproved": [],
        "assumed": ["X"],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_audit.py -v`
Expected: FAIL — `ImportError: cannot import name 'Verdict' from 'hardy.audit'`

- [ ] **Step 3: Write the implementation**

Append to `src/hardy/audit.py` (and add `from collections.abc import Iterable, Sequence` and `from typing import Any` to the imports):

```python
@dataclass(frozen=True)
class Verdict:
    status: str  # "clean" | "modulo" | "rejected"
    reports: tuple[AxiomReport, ...]
    forbidden: tuple[str, ...]
    unapproved: tuple[str, ...]
    assumed: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "declarations": [{"name": item.declaration, "axioms": list(item.axioms)} for item in self.reports],
            "forbidden": list(self.forbidden),
            "unapproved": list(self.unapproved),
            "assumed": list(self.assumed),
        }


def classify(reports: Sequence[AxiomReport], approved: Iterable[str]) -> Verdict:
    """Grade an audited axiom set against what a human has sanctioned.

    Order of judgement matters: a forbidden axiom is fatal before approval is
    consulted at all, so that no approved-list entry can launder a hole.
    """
    sanctioned = set(approved)
    names: list[str] = []
    for item in reports:
        names.extend(axiom for axiom in item.axioms if axiom not in names)
    forbidden = tuple(name for name in names if name in FORBIDDEN)
    extra = [name for name in names if name not in FORBIDDEN and name not in STANDARD]
    assumed = tuple(name for name in extra if name in sanctioned)
    unapproved = tuple(name for name in extra if name not in sanctioned)
    status = "rejected" if forbidden or unapproved else "modulo" if assumed else "clean"
    return Verdict(status, tuple(reports), forbidden, unapproved, assumed)


def dependents(reports: Sequence[AxiomReport], axiom: str) -> tuple[str, ...]:
    return tuple(item.declaration for item in reports if axiom in item.axioms)


_AXIOM_OF = "(?ms)^axiom\\s+{name}\\s*:\\s*(.*?)(?=^\\S|\\Z)"


def printed(output: str, name: str) -> str | None:
    """The statement Lean printed for an axiom, whitespace-normalised.

    Used to notice that an assumption a human approved is no longer the
    assumption Lean resolves under that name — after a Mathlib or project
    upgrade, say. A name alone is not an identity.
    """
    found = re.search(_AXIOM_OF.format(name=re.escape(name)), output)
    return " ".join(found.group(1).split()) if found else None


def unestablished(reason: str) -> dict[str, Any]:
    """The record for an audit that was attempted and could not be completed.

    Distinct from "not audited": that is reserved for a run where nothing ever
    reached the audit. An attempt that failed is a different fact, and reporting
    it as no attempt would misdescribe the run.
    """
    return {"status": "not established", "reason": reason, "declarations": [], "forbidden": [], "unapproved": [], "assumed": []}


def describe(verdict: Verdict) -> str:
    parts = []
    if verdict.forbidden:
        parts.append(f"forbidden {list(verdict.forbidden)}")
    if verdict.unapproved:
        parts.append(f"unapproved {list(verdict.unapproved)}")
    if verdict.assumed:
        parts.append(f"approved assumptions {list(verdict.assumed)}")
    return "; ".join(parts) or "standard axioms only"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_audit.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/audit.py tests/test_audit.py
git commit -m "feat: grade an audited axiom set against approved assumptions"
```

---

### Task 3: Lean emits the audit, the fake Lean answers it

**Files:**
- Modify: `src/hardy/lean.py:26-32` (`source`), `src/hardy/lean.py:61-63` (`run_source`)
- Modify: `tests/fake_lean.py` (whole file)
- Test: `tests/test_hardy.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces:
  - `LeanTools.target_name -> str | None` — the declaration name, `None` for an anonymous `example`.
  - `LeanTools.with_audit(source: str, names: Sequence[str]) -> str`
  - `LeanTools.run_source(source: str, *, audit: Sequence[str] = ()) -> ToolResult`
  - `tests/fake_lean.py` answers `#print axioms N` from a `-- axioms: a, b` marker in the source, and emits the "does not depend on any axioms" form when the marker is absent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hardy.py`:

```python
def test_audit_lines_are_appended_for_each_requested_target(lean: LeanTools):
    source = lean.with_audit("import Mathlib\n\ntheorem A : True := trivial\n", ("axioms A", "axioms B", "Papers.Smith.main"))
    assert source.endswith("#print axioms A\n#print axioms B\n#print Papers.Smith.main\n")
    assert "theorem A : True := trivial" in source


def test_an_anonymous_example_has_no_auditable_name(proof_request: Request):
    named = LeanTools(proof_request, ("true",))
    assert named.target_name == "HardyTarget"
    anonymous = LeanTools(Request.from_dict({"declaration": "example : True", "informal_claim": "x"}), ("true",))
    assert anonymous.target_name is None


def test_the_target_name_survives_a_missing_space_before_the_colon():
    request = Request.from_dict({"declaration": "theorem Tight: True", "informal_claim": "x"})
    assert LeanTools(request, ("true",)).target_name == "Tight"


def test_explicit_universe_binders_are_not_part_of_the_name():
    """`#print axioms Foo.` is not a command, so `Foo.{u}` must yield `Foo`."""
    request = Request.from_dict({"declaration": "theorem Foo.{u} (a : Sort u) : True", "informal_claim": "x"})
    assert LeanTools(request, ("true",)).target_name == "Foo"


def test_a_qualified_primed_name_survives_intact():
    request = Request.from_dict({"declaration": "lemma Nat.add_comm' (a : Nat) : True", "informal_claim": "x"})
    assert LeanTools(request, ("true",)).target_name == "Nat.add_comm'"


def test_unicode_declaration_names_are_auditable():
    """Lean identifiers are not ASCII; `theorem α : True` is a valid request."""
    for declaration, expected in [("theorem α : True", "α"), ("theorem x₁ : True", "x₁"), ("lemma α.β : True", "α.β")]:
        request = Request.from_dict({"declaration": declaration, "informal_claim": "x"})
        assert LeanTools(request, ("true",)).target_name == expected


def test_search_declaration_rejects_a_malformed_qualified_name(lean: LeanTools):
    """`Foo..bar` and `Foo.` are not names, though the old pattern allowed them."""
    assert not lean.search_declaration("Foo..bar").ok
    assert not lean.search_declaration("Foo.").ok
    assert lean.search_declaration("Nat.add_comm'").ok


def test_the_fake_lean_reports_the_axioms_a_test_asked_for(lean: LeanTools):
    result = lean.run_source("theorem A : True := by exact True.intro -- axioms: propext, sorryAx\n", audit=("A",))
    assert result.ok
    assert "'A' depends on axioms: [propext, sorryAx]" in result.output


def test_the_fake_lean_reports_no_axioms_without_a_marker(lean: LeanTools):
    result = lean.run_source("theorem A : True := by exact True.intro\n", audit=("A",))
    assert "'A' does not depend on any axioms" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_hardy.py -v -k "audit or anonymous or target or fake_lean"`
Expected: FAIL — `AttributeError: 'LeanTools' object has no attribute 'with_audit'`

- [ ] **Step 3: Write the implementation**

In `src/hardy/lean.py`, replace the `source` method (lines 26-32) with:

```python
    @property
    def target_name(self) -> str | None:
        """The declaration Lean can be asked about, or None for an `example`.

        An anonymous example has no name, so nothing can print its axioms —
        which is why it cannot be graded rather than graded leniently.

        Matched rather than split apart: splitting on `(`, `{`, and `:` turns
        `theorem Foo.{u} (a : Sort u) : True` into `Foo.`, and `#print axioms
        Foo.` is not a command, so a universe-polymorphic request could never
        verify.
        """
        found = re.match(rf"\s*(?:theorem|lemma)\s+({DECLARATION.pattern})", self.request.declaration)
        return found.group(1) if found else None

    @staticmethod
    def with_audit(source: str, targets: Sequence[str]) -> str:
        """Ask Lean about each target, in the same elaboration.

        A target is whatever follows `#print`: `axioms Foo` for an axiom set,
        or a bare name to print a declaration. Appended last so the answers
        survive the tail truncation in `_run`, and so a proof's own output
        cannot follow them.
        """
        if not targets:
            return source
        lines = "\n".join(f"#print {target}" for target in targets)
        return f"{source.rstrip()}\n\n{lines}\n"

    def source(self, proof: str, *, audit: bool = False) -> str:
        imports = "\n".join(f"import {name}" for name in self.request.imports)
        body = f"{imports}\n\n{self.request.declaration} := {proof.strip()}\n"
        name = self.target_name
        return self.with_audit(body, (f"axioms {name}",)) if audit and name else body
```

Add `from collections.abc import Sequence` to the imports at the top of
`lean.py`, and this constant beside `HOLE` at line 11:

```python
# A Lean declaration name: identifier components joined by dots. Stricter than
# `[A-Za-z_][A-Za-z0-9_'.]*`, which admits `Foo..bar` and `Foo.`, and wider —
# `\w` is Unicode-aware here, so `α`, `x₁`, and `Nat.add_comm'` all pass, as
# Lean identifiers are not ASCII. An approximation of Lean's grammar, not a
# reimplementation of it: French-quoted «escaped identifiers» are refused.
_COMPONENT = r"[^\W\d][\w']*"
DECLARATION = re.compile(rf"{_COMPONENT}(?:\.{_COMPONENT})*")
```

Then replace `search_declaration` (lines 74-78) so both places agree on what a
name is, and so a caller can look one up under imports other than the request's:

```python
    def search_declaration(self, name: str, *, imports: Sequence[str] | None = None) -> ToolResult:
        if not DECLARATION.fullmatch(name):
            return ToolResult(False, "search_declaration accepts one Lean declaration name")
        lines = "\n".join(f"import {item}" for item in (imports if imports is not None else self.request.imports))
        return self._run(f"{lines}\n\n#check {name}\n#print {name}\n")
```

The interactive session's `LeanTools` carries a placeholder request whose imports
are only `Mathlib` (`chat.py:65`), so without this override an axiom reached
through the saved source's own imports could never be shown to the human.

Replace `run_source` (lines 61-63) with:

```python
    def run_source(self, source: str, *, audit: Sequence[str] = ()) -> ToolResult:
        """Run a complete Lean source file, without claiming it is hole-free.

        `audit` holds `#print` targets, so a caller can ask for both an axiom
        set (`axioms Foo`) and a declaration (`Bar`) in one elaboration.
        """
        return self._run(self.with_audit(source, audit))
```

Replace the whole of `tests/fake_lean.py` with:

```python
#!/usr/bin/env python3
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[-1]).read_text()
# Word boundaries, like the real hole check: `sorryAx` is an axiom name, not a hole.
HOLE = re.compile(r"\b(sorry|admit)\b")


def report_axioms() -> None:
    """Stand in for `#print axioms`. A test picks the answer with a marker."""
    marker = re.search(r"--\s*axioms:\s*(.*)", source)
    listed = [item.strip() for item in marker.group(1).split(",") if item.strip()] if marker else []
    for name in re.findall(r"#print axioms (\S+)", source):
        if listed:
            print(f"'{name}' depends on axioms: [{', '.join(listed)}]")
        else:
            print(f"'{name}' does not depend on any axioms")
    # `#print <name>` on its own prints the declaration. A test changes what an
    # assumption resolves to with a `-- restated:` marker.
    restated = re.search(r"--\s*restated:\s*(.*)", source)
    for name in re.findall(r"(?m)^#print (?!axioms )(\S+)", source):
        print(f"axiom {name} : {restated.group(1).strip() if restated else 'True'}")


if "exact True.intro" in source and not HOLE.search(source):
    report_axioms()
    raise SystemExit(0)
if "trace_state" in source:
    print("⊢ True")
    raise SystemExit(0)
if "#check True.intro" in source:
    print("True.intro : True")
    raise SystemExit(0)
lookup = re.search(r"#check (\S+)", source)
if lookup:
    print(f"{lookup.group(1)} : (statement from fake Lean)")
    raise SystemExit(0)
print("Main.lean:3:28: error: type mismatch")
raise SystemExit(1)
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run --extra test pytest -v`
Expected: PASS. The five new tests pass and every pre-existing test still passes — in particular `test_successful_loop_saves_checked_linked_artifacts`, which asserts `#print axioms HardyTarget` appears in `proof.lean`. If that one fails, `with_audit` changed the trailing whitespace of the generated source; compare against `git show HEAD:src/hardy/lean.py`.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/lean.py tests/fake_lean.py tests/test_hardy.py
git commit -m "feat: ask Lean for an axiom report in the same elaboration"
```

---

### Task 4: `prove` fails closed on a bad audit

**Files:**
- Modify: `src/hardy/models.py:43` (`RunResult.axioms`)
- Modify: `src/hardy/runner.py` (imports, `dispatch`, terminal reason, result construction)
- Test: `tests/test_hardy.py`

**Interfaces:**
- Consumes: `audit.parse`, `audit.classify`, `audit.describe`, `audit.Verdict` (Tasks 1-2); `LeanTools.target_name` (Task 3); the `-- axioms:` marker in `fake_lean.py` (Task 3).
- Produces: terminal reason `"axioms_rejected"`; `RunResult.axioms` as `dict[str, Any]` shaped like `Verdict.as_dict()`, or `{"status": "not audited"}` when no proof was accepted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hardy.py`:

```python
def test_a_kernel_accepted_proof_with_sorry_ax_is_not_verified(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """The exit code says elaboration succeeded. The axiom set says it is a hole."""
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: sorryAx"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    assert result.formalization == "not formalized"
    assert result.proof is None
    assert not (tmp_path / "proof.lean").exists()
    # The audit ran and refused. Recording "not audited" would say it never ran.
    recorded = json.loads((tmp_path / "result.json").read_text())["axioms"]
    assert recorded["status"] == "rejected"
    assert recorded["forbidden"] == ["sorryAx"]


def test_an_unapproved_axiom_is_refused_unattended(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """No human is here to widen the trust base, so nothing widens it."""
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: Papers.Smith.main"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    assert result.axioms["unapproved"] == ["Papers.Smith.main"]


def test_the_model_is_told_which_axiom_was_refused(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """A refusal it cannot act on is a dead end rather than feedback."""
    run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: Papers.Smith.main"}),
    ]), lean, tmp_path, max_turns=2)
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    refusal = [event for event in trajectory["events"] if event["type"] == "tool"][-1]["result"]
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]


def test_a_clean_audit_still_verifies_and_is_recorded(tmp_path: Path, proof_request: Request, lean: LeanTools):
    result = run(proof_request, factory([
        call("submit_proof", {"proof": "by exact True.intro -- axioms: propext, Classical.choice"}),
    ]), lean, tmp_path, max_turns=2)
    assert result.terminal_reason == "verified"
    assert result.formalization == "kernel verified"
    recorded = json.loads((tmp_path / "result.json").read_text())["axioms"]
    assert recorded["status"] == "clean"
    assert recorded["declarations"] == [{"name": "HardyTarget", "axioms": ["propext", "Classical.choice"]}]


def test_a_run_with_no_submission_is_still_no_proof_submitted(tmp_path: Path, proof_request: Request, lean: LeanTools):
    """`axioms_rejected` must not swallow the case where nothing was offered."""
    result = run(proof_request, factory([{"role": "assistant", "content": "I think it works."}]), lean, tmp_path, max_turns=1)
    assert result.terminal_reason == "no_proof_submitted"
    assert json.loads((tmp_path / "result.json").read_text())["axioms"] == {"status": "not audited"}


def test_an_anonymous_example_cannot_be_verified(tmp_path: Path, lean: LeanTools):
    """Nothing can print an example's axioms, so nothing can grade it."""
    request = Request.from_dict({"declaration": "example : True", "informal_claim": "True is true."})
    anonymous = LeanTools(request, lean.lean_command)
    result = run(request, factory([call("submit_proof", {"proof": "by exact True.intro"})]), anonymous, tmp_path, max_turns=2)
    assert result.terminal_reason == "axioms_rejected"
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    assert "named theorem" in trajectory["events"][-1]["result"]["output"]
    # An audit that could not run is not the same fact as no audit at all.
    assert result.axioms["status"] == "not established"
    assert "example" in result.axioms["reason"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_hardy.py -v -k "sorry_ax or unapproved or refused or clean_audit or no_submission or anonymous_example"`
Expected: FAIL — the first asserts `axioms_rejected` but gets `verified`.

- [ ] **Step 3: Write the implementation**

In `src/hardy/models.py`, change the `RunResult.axioms` field (line 43) from `axioms: str` to:

```python
    axioms: dict[str, Any]
```

In `src/hardy/runner.py`, add to the imports:

```python
from . import audit
```

Add this module-level helper above `run`:

```python
def _audited(result: ToolResult, lean: LeanTools) -> tuple[ToolResult, audit.Verdict | None, dict[str, Any] | None]:
    """A kernel-accepted proof is not yet a verified one.

    `prove` has nobody to approve an assumption, so anything beyond the standard
    axioms is refused rather than recorded and shipped. The third element is
    what the record should say when the proof is refused: an audit that could
    not run is a different fact from one that ran and found something, and both
    differ from never having audited anything.
    """
    name = lean.target_name
    if name is None:
        why = "an anonymous `example` cannot be audited; state the claim as a named theorem or lemma"
        return ToolResult(False, why, result.source), None, audit.unestablished(why)
    reports = audit.parse(result.output, (name,))
    if reports is None:
        why = f"the axiom audit for `{name}` could not be established; remove any #print axioms from the proof, Hardy adds its own"
        return ToolResult(False, why, result.source), None, audit.unestablished(why)
    verdict = audit.classify(reports, ())
    if verdict.status != "clean":
        return ToolResult(False, f"Lean accepted the proof but the axiom audit refused it: {audit.describe(verdict)}", result.source), verdict, verdict.as_dict()
    return result, verdict, None
```

In `run`, change the `found` initialiser (line 40) to:

```python
    found: dict[str, Any] = {"result": None, "proof": None, "verdict": None}
    # A submission Lean accepted and the audit then refused. Kept so the terminal
    # reason can say what happened instead of "nothing was submitted", and so the
    # verdict that refused it survives into the record.
    refused: dict[str, Any] = {"axioms": False, "record": None}
```

Replace the `submit_proof` branch of `dispatch` (lines 59-69) with:

```python
            elif name == "submit_proof":
                proof = str(arguments["proof"])
                result = lean.check_proof(proof, final=True)
                verdict = None
                if result.ok:
                    result, verdict, record = _audited(result, lean)
                    if not result.ok:
                        refused["axioms"], refused["record"] = True, record
                # Judged against the clock rather than a flag: a check that was
                # still running when the budget expired cannot count, and one
                # that finished before it can.
                late = closed.is_set() or time.monotonic() > deadline.get("at", float("inf"))
                if result.ok and not late:
                    found["result"], found["proof"], found["verdict"] = result, proof, verdict
                elif result.ok:
                    events.append({"type": "discarded", "name": name, "why": "completed after the wall-clock budget expired"})
```

Replace the terminal-reason block (lines 105-108) with:

```python
    if final:
        reason = "verified"
    elif reason == "completed":
        # A proof that elaborated and was then refused is not "no proof submitted".
        reason = "axioms_rejected" if refused["axioms"] else "no_proof_submitted"
```

Replace the result construction (lines 113-115) with:

```python
    # What the audit decided: the verdict that verified the run, or failing that
    # the record of what refused it — which distinguishes an audit that ran and
    # found something from one that could not be established. "not audited" is
    # reserved for a run where no submission ever reached the audit at all.
    verdict = found["verdict"]
    formal = "kernel verified" if final else "not formalized"
    informal = "not assessed"
    axioms = verdict.as_dict() if final and verdict is not None else refused["record"] or {"status": "not audited"}
    result = RunResult(reason, formal, informal, proof if final else None, final.output if final else "No hole-free proof was accepted.", axioms, turns, [WARNING])
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run --extra test pytest -v`
Expected: PASS. Note that `test_successful_loop_saves_checked_linked_artifacts` and `test_a_proof_accepted_inside_the_budget_still_counts` both submit `by exact True.intro` with no marker, so the fake Lean reports no axioms, the verdict is `clean`, and they still reach `verified`.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/models.py src/hardy/runner.py tests/test_hardy.py
git commit -m "feat: prove grades on an audited axiom set, not an exit code"
```

---

### Task 5: `save_lean` audits, asks, and gates

**Files:**
- Modify: `src/hardy/chat.py` (imports, `_run_lean_source` at 169-181, `record_name` at 209-217)
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `audit.parse`, `audit.classify`, `audit.describe`, `audit.dependents` (Tasks 1-2); `LeanTools.run_source(..., audit=...)` and `LeanTools.search_declaration` (Task 3).
- Produces: the `confirm` callback may now receive a proposal carrying
  `kind="audit-finding"` with keys `formal_name`, `discovered_statement`,
  `reason`, `source`, and empty `lean_statement`, `latex_name`,
  `informal_statement`. Task 6 renders it.

- [ ] **Step 1: Write the failing tests**

Add `import hashlib` to `tests/test_chat.py`, then append:

```python
NAME = {"formal_name": "HardyTarget", "latex_name": "thm:true", "description": "True is true."}


def registered(source: str) -> FakeChatRuntime:
    """A script that names the declaration before saving work that uses it."""
    return FakeChatRuntime([
        call("record_name", dict(NAME), "name"),
        call("save_lean", {"source": source}, "lean"),
    ])


def last_tool(tmp_path: Path) -> dict:
    """What Hardy's execution of the last tool call produced.

    Read from the transcript rather than from the FakeChatRuntime handed to
    `session`: that helper rebuilds the runtime from its script, so the instance
    the test holds is not the one that ran.
    """
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    return [event for event in events if event["type"] == "tool"][-1]["result"]


def test_a_saved_proof_resting_on_sorry_ax_is_refused(tmp_path: Path):
    """`sorryAx` passes the word-boundary regex and is caught by the audit."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: sorryAx"
    chat = session(tmp_path, registered(lean))
    chat.send("Save it.")
    assert not (tmp_path / "Main.lean").exists()
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "sorryAx" in refusal["output"]


def test_sorry_ax_is_never_offered_for_approval(tmp_path: Path):
    """A human cannot approve a hole, so the prompt must not appear at all."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: sorryAx"
    asked = []
    chat = MathematicsSession(
        tmp_path, factory(FakeChatRuntime, registered(lean).script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: asked.append(proposal) or True,
    )
    chat.send("Save it.")
    assert asked == []
    assert not (tmp_path / "Main.lean").exists()


def test_an_unapproved_axiom_is_saved_once_the_human_approves(tmp_path: Path):
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    chat = session(tmp_path, registered(lean), approvals=[True])
    chat.send("Save it.")
    assert (tmp_path / "Main.lean").exists()
    assert last_tool(tmp_path)["ok"]
    state = json.loads((tmp_path / "session.json").read_text())
    entry = state["assumptions"][0]
    assert entry["formal_name"] == "Papers.Smith.main"
    assert entry["status"] == "user-approved-at-audit"
    assert "HardyTarget" in entry["reason"]
    # Recorded as an assumption only: it has no LaTeX label, and inventing one
    # would make the next save_latex demand a \label nobody chose.
    assert [item["formal_name"] for item in state["names"]] == ["HardyTarget"]


def test_declining_an_audited_axiom_gates_the_save(tmp_path: Path):
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    chat = session(tmp_path, registered(lean), approvals=[False])
    chat.send("Save it.")
    assert not (tmp_path / "Main.lean").exists()
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]
    assert json.loads((tmp_path / "session.json").read_text())["assumptions"] == []


def test_an_already_approved_axiom_saves_without_asking_again(tmp_path: Path):
    """The 'verified modulo' path: approved once, then not re-litigated."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    asked = []
    chat = MathematicsSession(
        tmp_path, factory(FakeChatRuntime, registered(lean).script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: asked.append(proposal) or True,
    )
    chat.send("Save it.")
    assert len(asked) == 1
    chat.send("Save it again.")
    assert len(asked) == 1, "an approved assumption must not be asked about twice"
    result = last_tool(tmp_path)
    assert result["ok"]
    assert "approved assumptions ['Papers.Smith.main']" in result["output"]


def test_the_prompt_carries_the_statement_and_who_needs_it(tmp_path: Path):
    """Approving a bare name is the failure the approval exists to prevent."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    seen = []
    chat = MathematicsSession(
        tmp_path, factory(FakeChatRuntime, registered(lean).script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: seen.append(proposal) or True,
    )
    chat.send("Save it.")
    assert seen[0]["kind"] == "audit-finding"
    assert seen[0]["formal_name"] == "Papers.Smith.main"
    assert "statement from fake Lean" in seen[0]["discovered_statement"]
    assert "HardyTarget" in seen[0]["reason"]


def test_a_clean_audit_reports_itself_in_the_tool_result(tmp_path: Path):
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
    chat = session(tmp_path, registered(lean))
    chat.send("Save it.")
    result = last_tool(tmp_path)
    assert result["ok"]
    assert "standard axioms only" in result["output"]


def test_an_empty_registry_refuses_the_save(tmp_path: Path):
    """Nothing to audit is not a clean audit, or the gate would be optional."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: sorryAx"
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": lean}, "lean")]))
    chat.send("Save it.")
    assert not (tmp_path / "Main.lean").exists()
    result = last_tool(tmp_path)
    assert not result["ok"]
    assert "record_name at least one declaration" in result["output"]


def test_the_saved_verdict_is_persisted_for_later_grading(tmp_path: Path):
    """A grade that lives only in a ToolResult cannot contradict a writeup."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    chat = session(tmp_path, registered(lean), approvals=[True])
    chat.send("Save it.")
    stored = json.loads((tmp_path / "session.json").read_text())["audit"]
    assert stored["status"] == "modulo"
    assert stored["assumed"] == ["Papers.Smith.main"]
    assert stored["declarations"] == [{"name": "HardyTarget", "axioms": ["Papers.Smith.main"]}]


def test_a_writeup_must_disclose_the_assumptions_the_artifact_rests_on(tmp_path: Path):
    """save_latex checked only labels, so a modulo artifact could be written up
    as plainly verified with nothing to contradict it."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    silent = "\\documentclass{article}\\begin{document}True.\\label{thm:true}\\end{document}"
    honest = "\\documentclass{article}\\begin{document}True, assuming Papers.Smith.main.\\label{thm:true}\\end{document}"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", dict(NAME), "name"),
        call("save_lean", {"source": lean}, "lean"),
        call("save_latex", {"source": silent}, "silent"),
    ]), approvals=[True])
    chat.send("Save both.")
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]
    assert not (tmp_path / "writeup.tex").exists()

    chat.runtime.script = [call("save_latex", {"source": honest}, "honest")]
    chat.send("Disclose it.")
    assert last_tool(tmp_path)["ok"]
    assert (tmp_path / "writeup.tex").exists()


def test_registering_a_declaration_invalidates_the_stored_audit(tmp_path: Path):
    """A verdict covering one registry must not grade a wider one."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
    latex = "\\documentclass{article}\\begin{document}True.\\label{thm:true}\\label{thm:two}\\end{document}"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", dict(NAME), "name"),
        call("save_lean", {"source": lean}, "lean"),
        call("record_name", {"formal_name": "Second", "latex_name": "thm:two", "description": "another"}, "late"),
        call("save_latex", {"source": latex}, "latex"),
    ]))
    chat.send("Save it, then register another.")
    assert "audit" not in json.loads((tmp_path / "session.json").read_text())
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "registry has changed" in refusal["output"]


def test_an_assumption_named_only_in_a_tex_comment_is_not_disclosure(tmp_path: Path):
    """`% Papers.Smith.main` discloses nothing to a reader of the PDF."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    hidden = "\\documentclass{article}\\begin{document}True.\\label{thm:true}\n% Papers.Smith.main\n\\end{document}"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", dict(NAME), "name"),
        call("save_lean", {"source": lean}, "lean"),
        call("save_latex", {"source": hidden}, "latex"),
    ]), approvals=[True])
    chat.send("Save both.")
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "Papers.Smith.main" in refusal["output"]
    assert not (tmp_path / "writeup.tex").exists()


def test_a_multiline_axiom_cannot_strengthen_an_approved_one(tmp_path: Path):
    """`axiom Foo :\\n  False` must not slip past the statement comparison."""
    approved = "import Mathlib\n\naxiom Foo : True\n\ntheorem HardyTarget : True := by exact True.intro"
    strengthened = "import Mathlib\n\naxiom Foo :\n  False\n\ntheorem HardyTarget : True := by exact True.intro"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", dict(NAME), "name"),
        call("request_assumption", {"formal_name": "Foo", "lean_statement": "True", "latex_name": "ax:foo", "informal_statement": "trivially true", "source": "test", "reason": "test"}, "ask"),
        call("save_lean", {"source": approved}, "first"),
    ]), approvals=[True])
    chat.send("Assume it.")
    assert last_tool(tmp_path)["ok"]

    chat.runtime.script = [call("save_lean", {"source": strengthened}, "second")]
    chat.send("Now change it.")
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "unapproved or altered assumption `Foo`" in refusal["output"]


def test_the_statement_lookup_uses_the_sources_own_imports(tmp_path: Path):
    """The session's placeholder request imports only Mathlib (chat.py:65)."""
    lean = "import Mathlib\nimport Papers.Smith\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    seen = []
    chat = MathematicsSession(
        tmp_path, factory(FakeChatRuntime, registered(lean).script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: seen.append(proposal) or False,
    )
    recorded = []
    original = chat.lean.search_declaration
    chat.lean.search_declaration = lambda name, *, imports=None: recorded.append(imports) or original(name, imports=imports)
    chat.send("Save it.")
    assert recorded == [("Mathlib", "Papers.Smith")]


def test_the_verdict_is_published_only_after_main_lean_exists(tmp_path: Path):
    """A verdict written first would outlive a failed write and describe a
    Main.lean that was never saved."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
    chat = session(tmp_path, registered(lean))
    chat.send("Save it.")
    stored = json.loads((tmp_path / "session.json").read_text())["audit"]
    saved = (tmp_path / "Main.lean").read_bytes()
    assert stored["source_sha256"] == hashlib.sha256(saved).hexdigest()


def test_a_writeup_is_refused_when_main_lean_changed_under_the_audit(tmp_path: Path):
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro"
    latex = "\\documentclass{article}\\begin{document}True.\\label{thm:true}\\end{document}"
    chat = session(tmp_path, registered(lean))
    chat.send("Save it.")
    (tmp_path / "Main.lean").write_text("import Mathlib\n\ntheorem HardyTarget : True := by sorry\n")
    chat.runtime.script = [call("save_latex", {"source": latex}, "latex")]
    chat.send("Write it up.")
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "no current audit" in refusal["output"]


def test_a_primed_registered_name_is_found_in_the_source(tmp_path: Path):
    """`\\bNat.add_comm'\\b` never matches, so this refused every save."""
    lean = "import Mathlib\n\ntheorem Nat.add_comm' : True := by exact True.intro"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", {"formal_name": "Nat.add_comm'", "latex_name": "thm:ac", "description": "primed"}, "name"),
        call("save_lean", {"source": lean}, "lean"),
    ]))
    chat.send("Save it.")
    assert last_tool(tmp_path)["ok"]
    assert (tmp_path / "Main.lean").exists()


def test_an_assumption_for_an_already_registered_name_does_not_duplicate_it(tmp_path: Path):
    """Two mappings for one name emit two audit lines, and the workspace can
    then never be saved again."""
    lean = "import Mathlib\n\naxiom Foo : True\n\ntheorem Foo' : True := by exact True.intro"
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", {"formal_name": "Foo", "latex_name": "ax:foo", "description": "an axiom"}, "name"),
        call("request_assumption", {"formal_name": "Foo", "lean_statement": "True", "latex_name": "ax:foo", "informal_statement": "an axiom", "source": "test", "reason": "test"}, "ask"),
        call("record_name", {"formal_name": "Foo'", "latex_name": "thm:x", "description": "a theorem"}, "name2"),
        call("save_lean", {"source": lean}, "lean"),
    ]), approvals=[True])
    chat.send("Register, assume, then save.")
    names = [item["formal_name"] for item in json.loads((tmp_path / "session.json").read_text())["names"]]
    assert names == ["Foo", "Foo'"]
    assert last_tool(tmp_path)["ok"]


def test_an_explicit_request_upgrades_an_audit_discovered_record(tmp_path: Path):
    """Otherwise the record keeps an empty statement and every later save is
    compared against it and refused, with no way to correct it."""
    discovered = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Foo"
    declared = "import Mathlib\n\naxiom Foo : True\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Foo"
    chat = session(tmp_path, registered(discovered), approvals=[True, True])
    chat.send("Save it.")
    assert json.loads((tmp_path / "session.json").read_text())["assumptions"][0]["lean_statement"] == ""

    chat.runtime.script = [
        call("request_assumption", {"formal_name": "Foo", "lean_statement": "True", "latex_name": "ax:foo", "informal_statement": "an axiom", "source": "test", "reason": "test"}, "ask"),
        call("save_lean", {"source": declared}, "again"),
    ]
    chat.send("Now declare it.")
    entry = json.loads((tmp_path / "session.json").read_text())["assumptions"][0]
    assert entry["lean_statement"] == "True"
    assert last_tool(tmp_path)["ok"]


def test_an_approved_assumption_that_changed_meaning_is_refused(tmp_path: Path):
    """A name is not an identity: after an upgrade the same name can resolve to
    a different statement than the one a human approved."""
    first = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Foo"
    changed = first + "\n-- restated: False"
    chat = session(tmp_path, registered(first), approvals=[True])
    chat.send("Save it.")
    assert json.loads((tmp_path / "session.json").read_text())["assumptions"][0]["printed_statement"] == "True"

    chat.runtime.script = [call("save_lean", {"source": changed}, "again")]
    chat.send("Save it again.")
    refusal = last_tool(tmp_path)
    assert not refusal["ok"]
    assert "no longer the statements Lean resolves" in refusal["output"]


def test_the_prompt_says_when_a_statement_was_cut_off(tmp_path: Path):
    """A tail presented as the whole statement is an approval given blind."""
    lean = "import Mathlib\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    seen = []
    chat = MathematicsSession(
        tmp_path, factory(FakeChatRuntime, registered(lean).script),
        (sys.executable, str(Path(__file__).with_name("fake_lean.py"))),
        (sys.executable, str(Path(__file__).with_name("fake_latex.py"))),
        lambda proposal: seen.append(proposal) or False,
    )
    chat.lean.output_limit = 4  # anything Lean says now reaches the limit
    chat.send("Save it.")
    assert seen[0]["statement_status"] == "truncated"
    assert seen[0]["discovered_statement"] == ""


def test_the_recorded_assumption_names_what_supplied_it(tmp_path: Path):
    """`discovered by the axiom audit` says how it was found, not what supplied it."""
    lean = "import Mathlib\nimport Papers.Smith\n\ntheorem HardyTarget : True := by exact True.intro -- axioms: Papers.Smith.main"
    chat = session(tmp_path, registered(lean), approvals=[True])
    chat.send("Save it.")
    entry = json.loads((tmp_path / "session.json").read_text())["assumptions"][0]
    assert "Papers.Smith" in entry["source"]
    assert "fake_lean.py" in entry["source"]


def test_a_registered_name_that_is_not_a_lean_identifier_is_refused(tmp_path: Path):
    """Registered names are interpolated into `#print axioms`, so they must be names."""
    chat = session(tmp_path, FakeChatRuntime([
        call("record_name", {"formal_name": "A\n#eval 1", "latex_name": "thm:x", "description": "x"}, "name"),
    ]))
    chat.send("Register it.")
    assert not last_tool(tmp_path)["ok"]
    assert json.loads((tmp_path / "session.json").read_text())["names"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_chat.py -v -k "sorry_ax or unapproved or declining or prompt_carries or clean_audit or empty_registry or lean_identifier"`
Expected: FAIL — the first saves `Main.lean` because nothing audits it.

- [ ] **Step 3: Write the implementation**

In `src/hardy/chat.py`, add to the imports:

```python
from . import audit
from .lean import DECLARATION, LeanTools
```

Replace `_run_lean_source` (lines 169-181) with:

```python
    def _audit_names(self) -> tuple[str, ...]:
        # Deduplicated: two mappings for one name would emit two `#print axioms`
        # lines, which audit.parse treats as an unestablished audit — leaving a
        # workspace that can never be saved again.
        return tuple(dict.fromkeys(item["formal_name"] for item in self.state["names"]))

    def _admit(self, axiom: str, reports: tuple[audit.AxiomReport, ...], source: str) -> bool:
        """Ask a human about an axiom the audit found and nobody approved.

        The statement is fetched because approving a bare name is exactly the
        failure the approval exists to prevent. A lookup that fails, or that
        comes back truncated, still leads to a prompt; the human is told what is
        missing and decides.
        """
        imports = re.findall(r"(?m)^\s*import\s+(\S+)", source) or ["(no imports)"]
        # Looked up under the audited source's own imports, not the session
        # placeholder's bare `Mathlib`: an axiom supplied by `import Papers.Smith`
        # would otherwise come back unavailable, and the human would approve the
        # bare name — the exact outcome this lookup exists to prevent.
        lookup = self.lean.search_declaration(axiom, imports=tuple(imports))
        # `_run` returns only the trailing `output_limit` characters. A statement
        # long enough to hit that would show the human its tail while reading as
        # complete, which is an approval given for something never fully seen.
        truncated = lookup.ok and len(lookup.output) >= self.lean.output_limit
        proposal = {
            "kind": "audit-finding",
            "formal_name": axiom,
            # Nothing was declared in this source, so there is no declared
            # statement to match against later; what Lean reported is kept apart.
            "lean_statement": "",
            "discovered_statement": "" if truncated or not lookup.ok else lookup.output,
            "statement_status": "truncated" if truncated else "retrieved" if lookup.ok else "unavailable",
            "latex_name": "",
            "informal_statement": "",
            # What supplied it, not how it was found: a widened trust base has to
            # be traceable if the imported declaration later changes.
            "source": f"imported by {' '.join(imports)} via {' '.join(self.lean.lean_command)}",
            "reason": f"required by {', '.join(audit.dependents(reports, axiom))}",
        }
        if not self.confirm(proposal):
            return False
        proposal["status"] = "user-approved-at-audit"
        self.state["assumptions"].append(proposal)
        _atomic_json(self.state_path, self.state)
        return True

    def _run_lean_source(self, source: str, *, final: bool) -> tuple[ToolResult, dict[str, Any] | None]:
        """Returns the result, and the verdict to publish once the file is saved.

        The verdict is handed back rather than persisted here: `session.json`
        must not claim an audited artifact before that artifact is on disk.
        """
        if final and self.lean.has_holes(source):
            return ToolResult(False, "saved Lean artifacts may not contain sorry or admit", source), None
        if final:
            approved = {item["formal_name"]: " ".join(item["lean_statement"].split()) for item in self.state["assumptions"]}
            # Runs to the next top-level declaration rather than to end of line:
            # a line-oriented match misses `axiom Foo :\n  False`, so an
            # approved `Foo : True` could be silently strengthened while the
            # audit still saw the approved bare name and called it modulo.
            declarations = re.findall(
                rf"(?ms)^\s*(?:axiom|constant)\s+({DECLARATION.pattern})\s*:\s*(.*?)(?=^\s*(?:axiom|constant|theorem|lemma|example|def|instance|abbrev|namespace|end|open|import|#|@\[)|\Z)",
                source,
            )
            for name, statement in declarations:
                if approved.get(name) != " ".join(statement.split()):
                    return ToolResult(False, f"unapproved or altered assumption `{name}`; use request_assumption first", source), None
            # Identifier-aware rather than `\b`-delimited: a trailing apostrophe
            # is a non-word character, so `\bNat.add_comm'\b` never matches and
            # every save of a primed declaration would be refused.
            missing_names = [name for name in self._audit_names() if not re.search(rf"(?<![\w'.]){re.escape(name)}(?![\w'])", source)]
            if missing_names:
                return ToolResult(False, f"Lean source is missing registered names: {missing_names}", source), None
        audited = self._audit_names() if final else ()
        # Nothing to audit is not a clean audit. Saving here would make the gate
        # optional: never call record_name, never get audited.
        if final and not audited:
            return ToolResult(False, "record_name at least one declaration before saving. Hardy audits the axioms of registered declarations, and will not save work it cannot audit. Use check_lean for scratch work.", source), None
        # Approved assumptions are re-printed in the same run, so an approval is
        # checked against the declaration Lean resolves now rather than reused on
        # the strength of a matching name.
        sanctioned = sorted(self._audit_approved())
        result = self.lean.run_source(source, audit=tuple(f"axioms {name}" for name in audited) + tuple(sanctioned))
        if not final or not result.ok:
            return result, None
        reports = audit.parse(result.output, audited)
        if reports is None:
            return ToolResult(False, "the axiom audit could not be established; remove any #print axioms from your source, Hardy adds its own", source), None
        drifted = self._drifted(result.output, sanctioned)
        if drifted:
            return ToolResult(False, f"these approved assumptions are no longer the statements Lean resolves under their names: {drifted}. The approval was granted for something else; request it again.", source), None
        verdict = audit.classify(reports, self._audit_approved())
        # Before any prompt: a hole is not an assumption and is never offered.
        if verdict.forbidden:
            return ToolResult(False, f"the axiom audit refused this source: {audit.describe(verdict)}. A hole cannot be approved.", source), None
        for axiom in verdict.unapproved:
            if not self._admit(axiom, reports, source):
                return ToolResult(False, f"the user declined the assumption `{axiom}`, which this source depends on. Do not save work that requires it.", source), None
        verdict = audit.classify(reports, self._audit_approved())
        # Stamped with what it covered: registering another declaration later
        # widens the registry without re-auditing, and a verdict that no longer
        # describes the current registry must not be trusted by save_latex.
        return ToolResult(True, f"{result.output}\n\naxiom audit: {audit.describe(verdict)}", source), {**verdict.as_dict(), "audited_names": list(audited)}

    def _drifted(self, output: str, sanctioned: list[str]) -> list[str]:
        """Approved assumptions that are no longer the statement approved.

        The printed statement is recorded the first time it is seen, so an
        approval granted before this check existed acquires an identity rather
        than being refused for lacking one.
        """
        changed, learned = [], False
        for entry in self.state["assumptions"]:
            if entry["formal_name"] not in sanctioned:
                continue
            statement = audit.printed(output, entry["formal_name"])
            if statement is None:
                continue
            if not entry.get("printed_statement"):
                entry["printed_statement"], learned = statement, True
            elif entry["printed_statement"] != statement:
                changed.append(entry["formal_name"])
        if learned:
            _atomic_json(self.state_path, self.state)
        return changed

    def _audit_approved(self) -> set[str]:
        return {item["formal_name"] for item in self.state["assumptions"]}
```

Replace the `check_lean` and `save_lean` branches (lines 184-191) so the verdict
is published only once the artifact it describes exists on disk, bound to a
digest of that artifact:

```python
        if name == "check_lean":
            return self._run_lean_source(str(arguments["source"]), final=False)[0]
        if name == "save_lean":
            source = str(arguments["source"])
            result, verdict = self._run_lean_source(source, final=True)
            if result.ok and verdict is not None:
                saved = source.rstrip() + "\n"
                (self.workspace / "Main.lean").write_text(saved, encoding="utf-8")
                # After the write, and tied to what was written: a verdict
                # published first would survive a failed write or a crash and
                # describe a Main.lean that was never saved.
                self.state["audit"] = {**verdict, "source_sha256": hashlib.sha256(saved.encode("utf-8")).hexdigest()}
                _atomic_json(self.state_path, self.state)
            return result
```

Add `import hashlib` to `chat.py`.

Replace the label check in the `save_latex` branch of `_tool` (line 196) with one
that also demands disclosure of whatever the saved Lean rests on:

```python
        if name == "save_latex":
            source = str(arguments["source"])
            missing_labels = [item["latex_name"] for item in self.state["names"] if f"\\label{{{item['latex_name']}}}" not in source]
            if missing_labels:
                return ToolResult(False, f"LaTeX source is missing registered labels: {missing_labels}", source)
            # A writeup for a modulo artifact must say so. Hardy holds the grade,
            # so the model cannot describe assumed work as plainly verified.
            stored = self.state.get("audit")
            saved = self.workspace / "Main.lean"
            digest = hashlib.sha256(saved.read_bytes()).hexdigest() if saved.exists() else None
            if stored is None or stored.get("audited_names") != list(self._audit_names()) or stored.get("source_sha256") != digest:
                return ToolResult(False, "no current audit covers the saved Lean — the registry or Main.lean has changed since it was audited. Call save_lean again so the writeup is graded against what is actually on disk.", source)
            # Searched outside TeX comments: `% Papers.Smith.main` discloses
            # nothing to a reader of the compiled document.
            visible = re.sub(r"(?m)(?<!\\)%.*$", "", source)
            undisclosed = [axiom for axiom in stored["assumed"] if axiom not in visible]
            if undisclosed:
                return ToolResult(False, f"the saved Lean is verified only modulo these assumptions, which the writeup must disclose in its rendered text: {undisclosed}", source)
            result = self.latex.check(source, output_dir=self.workspace)
            if result.ok:
                (self.workspace / "writeup.tex").write_text(source.rstrip() + "\n", encoding="utf-8")
            return result
```

Replace the `record_name` branch of `_tool` (lines 209-217) with:

```python
        if name == "record_name":
            entry = {key: str(arguments[key]) for key in ("formal_name", "latex_name", "description")}
            # Registered names are interpolated into `#print axioms`, so a name
            # that is not a Lean identifier would produce an unanswerable audit.
            # Persisted immediately, so `Foo..bar` would break every later save.
            if not DECLARATION.fullmatch(entry["formal_name"]):
                return ToolResult(False, f"formal_name must be a Lean declaration name: {entry['formal_name']!r}")
            existing = next((item for item in self.state["names"] if item["formal_name"] == entry["formal_name"] or item["latex_name"] == entry["latex_name"]), None)
            if existing and existing != entry:
                return ToolResult(False, f"name conflicts with existing mapping: {existing}")
            if not existing:
                self.state["names"].append(entry)
                # The stored verdict described the old registry. Dropping it
                # here means save_latex asks for a fresh save rather than
                # grading a wider artifact against a narrower audit.
                self.state.pop("audit", None)
                _atomic_json(self.state_path, self.state)
            return ToolResult(True, f"recorded mapping: {entry}")
```

Finally, replace the recording step inside the `request_assumption` branch
(lines 222-227), which currently skips both the assumption and its mapping when
the name is already known:

```python
            proposal["status"] = "user-approved"
            existing = next((item for item in self.state["assumptions"] if item["formal_name"] == proposal["formal_name"]), None)
            if existing is None:
                self.state["assumptions"].append(proposal)
            elif not existing.get("lean_statement"):
                # An audit-discovered record carries no declared statement.
                # Skipping the update would leave every later save comparing the
                # declared statement against "" and refusing it, with no way out.
                existing.update(proposal)
            mapping = {"formal_name": proposal["formal_name"], "latex_name": proposal["latex_name"], "description": proposal["informal_statement"]}
            # Only when the name is not already registered: a second mapping for
            # one name emits a second `#print axioms` line, which audit.parse
            # reads as an unestablished audit — an unsaveable workspace.
            if not any(item["formal_name"] == mapping["formal_name"] for item in self.state["names"]):
                self.state["names"].append(mapping)
                self.state.pop("audit", None)
            _atomic_json(self.state_path, self.state)
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run --extra test pytest -v`
Expected: PASS — **after one required edit to an existing test.**
`test_chat_checks_and_saves_linked_artifacts` (`tests/test_chat.py:74-79`) scripts
`save_lean` before `record_name`, which is now refused: there is nothing to audit
at that point. Swap the two calls so the declaration is registered first:

```python
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "HardyTarget", "latex_name": "thm:true", "description": "True is true."}, "name"),
        call("save_lean", {"source": lean}, "lean"),
        call("save_latex", {"source": latex}, "latex"),
        {"role": "assistant", "content": "Lean checked the theorem and the writeup compiles."},
    ])
```

and update its ordering assertion to `["record_name", "save_lean", "save_latex"]`.
That reordering is the point of the change, not a workaround for it: registering
a declaration is now a precondition of saving work that mentions it.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/test_chat.py
git commit -m "feat: audit saved Lean and ask before widening the trust base"
```

---

### Task 6: The approval prompt tells a human what they are approving

**Files:**
- Modify: `src/hardy/cli.py:19-31` (`_confirm_assumption` only — it is passed to `MathematicsSession` by name at `cli.py:116`, and gaining keyword-only defaults leaves that call unchanged)
- Test: `tests/test_approval.py` (create)

**Interfaces:**
- Consumes: the `kind="audit-finding"` proposal shape from Task 5.
- Produces: `_confirm_assumption(proposal, *, ask=input, out=print) -> bool`, matching the injectable-IO style already used by `model_command` in the same file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_approval.py`:

```python
from __future__ import annotations

from hardy.cli import _confirm_assumption

REQUEST = {
    "kind": "model-request",
    "formal_name": "Smith2020",
    "lean_statement": "∀ n : ℕ, P n",
    "informal_statement": "Every natural satisfies P.",
    "source": "arXiv:2001.00001v2",
    "reason": "not in Mathlib",
}

FINDING = {
    "kind": "audit-finding",
    "formal_name": "Papers.Smith.main",
    "discovered_statement": "Papers.Smith.main : ∀ n : ℕ, P n",
    "statement_status": "retrieved",
    "reason": "required by HardyTarget",
    "source": "imported by Papers.Smith via lake env lean",
    "lean_statement": "",
    "informal_statement": "",
    "latex_name": "",
}


def render(proposal: dict, answers: list[str]) -> tuple[bool, str]:
    spoken: list[str] = []
    replies = iter(answers)
    approved = _confirm_assumption(proposal, ask=lambda _prompt: next(replies), out=spoken.append)
    return approved, "\n".join(spoken)


def test_a_model_request_shows_the_statement_it_proposed():
    approved, shown = render(REQUEST, ["y"])
    assert approved
    assert "∀ n : ℕ, P n" in shown
    assert "arXiv:2001.00001v2" in shown


def test_an_audit_finding_shows_the_statement_lean_reported_and_who_needs_it():
    approved, shown = render(FINDING, ["y"])
    assert approved
    assert "Papers.Smith.main : ∀ n : ℕ, P n" in shown
    assert "required by HardyTarget" in shown
    assert "audit" in shown.lower()


def test_a_missing_statement_is_said_rather_than_hidden():
    """An approval offered without a statement must look like one."""
    _approved, shown = render({**FINDING, "discovered_statement": "", "statement_status": "unavailable"}, ["n"])
    assert "could not be retrieved" in shown


def test_a_truncated_statement_is_not_passed_off_as_the_whole_one():
    _approved, shown = render({**FINDING, "discovered_statement": "", "statement_status": "truncated"}, ["n"])
    assert "NOT the whole statement" in shown


def test_the_finding_names_what_supplied_the_axiom():
    _approved, shown = render(FINDING, ["n"])
    assert "Papers.Smith" in shown


def test_the_default_answer_is_no():
    assert render(REQUEST, [""])[0] is False
    assert render(FINDING, [""])[0] is False


def test_an_unreadable_answer_is_asked_again():
    assert render(FINDING, ["maybe", "yes"])[0] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_approval.py -v`
Expected: FAIL — `TypeError: _confirm_assumption() got an unexpected keyword argument 'ask'`

- [ ] **Step 3: Write the implementation**

In `src/hardy/cli.py`, replace `_confirm_assumption` (lines 19-31) with:

```python
def _confirm_assumption(proposal: dict[str, str], *, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> bool:
    """The one place a human widens the formal trust base.

    Two things arrive here: an assumption the model asked for, and one the axiom
    audit found a proof already resting on. They read differently because the
    second is a discovery rather than a request.
    """
    if proposal.get("kind") == "audit-finding":
        missing = {
            "truncated": "(too long to show in full — Lean's output was cut off, so this is NOT the whole statement)",
            "unavailable": "(could not be retrieved from Lean)",
        }
        out("\nThe axiom audit found an assumption nobody has approved:")
        out(f"  Axiom: {proposal['formal_name']}")
        out(f"  Statement: {proposal['discovered_statement'] or missing[proposal['statement_status']]}")
        out(f"  Supplied by: {proposal['source']}")
        out(f"  Needed because: {proposal['reason']}")
        out("  Approving widens the trust base of everything that depends on it.")
    else:
        out("\nHardy wants to introduce an assumption:")
        out(f"  Informal: {proposal['informal_statement']}")
        out(f"  Lean: axiom {proposal['formal_name']} : {proposal['lean_statement']}")
        out(f"  Source: {proposal['source']}")
        out(f"  Reason: {proposal['reason']}")
    while True:
        answer = ask("Approve this explicit assumption? [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        out("Please answer y or n.")
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run --extra test pytest -v`
Expected: PASS. `Callable` is already imported at `cli.py:7`; if the import was removed, restore `from typing import Any, Callable`.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/cli.py tests/test_approval.py
git commit -m "feat: an audit finding reads as a discovery, not a request"
```

---

### Task 7: Bring the documents back into agreement

**Files:**
- Modify: `DESIGN.md` (the "Output contract" section, and §3 Tool layer / §4 Lean interaction)
- Modify: `FEATURES.md:50` and the interactive-exploration section
- Modify: `ARCHITECTURE.html:54` and the trust panel at line 59
- Modify: `README.md:125-126`
- Test: none — these are prose. `AGENTS.md:17` requires them to match the code.

**Interfaces:**
- Consumes: the behaviour built in Tasks 1-6.
- Produces: nothing code depends on.

- [ ] **Step 1: Update `FEATURES.md`**

Under "Lean interaction and proof tools", replace the `**Next:**` bullet at line 50:

```markdown
- **Now (implemented):** audit `#print axioms` for every graded declaration,
  distinguishing standard axioms, forbidden `sorryAx`, and human-approved
  assumptions. The formalization grade follows the audited axiom set rather
  than a process exit code. `prove` fails closed on anything beyond the
  standard axioms; the interactive path asks a human, and a refusal blocks the
  save.
```

Under "Interactive exploration", append:

```markdown
- **Now (implemented):** saving Lean audits every declaration in the naming
  registry. An axiom nobody approved pauses for the same human approval that
  `request_assumption` uses, and a refusal blocks the save. `sorryAx` is never
  offered for approval.
```

- [ ] **Step 2: Update `DESIGN.md`**

In the "Output contract" section, after the two grade bullets, add:

```markdown
These grades are produced by an audit of the axioms Lean reports for each graded
declaration, not by a process exit code. `sorryAx` is fatal and cannot be
approved. "Verified modulo listed paper assumptions" is reachable only in the
interactive path, where a human is present to approve an assumption; `prove`
runs unattended and so fails closed instead.
```

In §4 "Lean interaction", after "rejects `sorry` in completed proofs, and audits
dependencies", add:

```markdown
The audit is asked for in the same elaboration as the check, so a save costs one
Mathlib import rather than two, and its answers are appended last so they survive
output truncation. A report that is missing, duplicated, or unparseable is a
rejection rather than a pass.
```

- [ ] **Step 3: Update `ARCHITECTURE.html`**

At line 54, remove "axiom audits" from the Next card, leaving:

```html
<article class="card"><h3><span class="tag next">Next</span> Honest experiments</h3><p>Faithfulness checks, budgets, evaluation provenance, critique/repair, and literature-backed citations.</p></article>
```

In the trust panel at line 59, replace "subject to its audited axiom set" with a
statement of what now enforces it:

```html
<div class="trust"><strong>The kernel is the formal authority, and Hardy grades on the axiom set Lean reports rather than on an exit code.</strong> <code>sorryAx</code> is fatal and cannot be approved; any other non-standard axiom is refused unattended, and asked about interactively. The naming manifest connects Lean and LaTeX for later review but does not prove faithfulness. User-approved axioms are visible additions to trust. Until confinement is restored, generated Lean and TeX and downloaded archives must be treated as unsafe and run only in disposable environments.</div>
```

- [ ] **Step 4: Correct the README's assumption contract**

`README.md:125-126` says a recorded assumption carries an exact Lean statement,
an informal rendering, a reason, and a source. That describes an assumption the
model requested. An assumption the audit discovers has no informal rendering and
no declared Lean statement — and when the lookup fails or comes back truncated,
no retrieved statement either. Leaving the claim as it stands would overstate
what `session.json` holds. Amend that passage to distinguish the two:

```markdown
An assumption Hardy records comes from one of two places. One the model requests
carries an exact Lean statement, an informal rendering, a reason, and a source.
One the axiom audit discovers — an axiom a saved proof turned out to rest on —
carries the axiom's name, what supplied it, which declarations need it, and the
statement Lean reported for it. That statement is absent when Lean could not be
asked or answered at more length than Hardy can show, and the approval prompt
says so rather than presenting a fragment as the whole. Hardy must ask before
adding either; declining does not widen the formal trust base.
```

- [ ] **Step 5: Record the limit the audit cannot close**

Add to `DESIGN.md`, in the "Trust boundary and safety" section beside the existing
sandbox warning:

```markdown
The audit runs inside the environment it audits: `#print axioms` is elaborated by
a Lean environment the submitted source has already had the chance to extend, and
a source that registers its own elaborator for that syntax can answer the audit
itself. The audit therefore establishes that an artifact is not *accidentally*
unsound; it is not a defence against a source written to subvert elaboration, and
cannot be one while Lean runs unconfined. Closing this belongs with the deferred
process isolation above.
```

Then open an issue against the anti-cheat work in `FEATURES.md` recording it, so
it is tracked rather than only documented.

- [ ] **Step 6: Verify the documents and the suite agree**

Run: `uv run --extra test pytest -v`
Expected: PASS, whole suite.

Then read `FEATURES.md`, `DESIGN.md`, and `ARCHITECTURE.html` once more and check
that nothing still claims the axiom audit is unimplemented or that verification
follows from compilation.

- [ ] **Step 7: Commit**

```bash
git add README.md FEATURES.md DESIGN.md ARCHITECTURE.html
git commit -m "docs: the axiom audit is implemented, and the grade follows it"
```

---

## Out of scope

Named here so nobody adds them mid-plan:

- `allowed_axioms` in the request file, which would let `prove` reach "verified
  modulo". The spec records this as a known limit and the obvious future move.
- Defending the audit against a source that redefines the `#print axioms`
  elaborator. Recorded as a stated limit in `DESIGN.md` and filed as an issue;
  closing it needs the deferred process isolation, not a change here.
- Grading the writeup beyond requiring that it disclose the assumptions the saved
  Lean rests on. Full informal-completeness grading is a separate feature.
- The statement-faithfulness gate (`FEATURES.md:36`), critique/repair, and cost
  accounting (issue #30). Separate specs.
