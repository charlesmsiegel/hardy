# Lean partial results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an interactive session save a Lean file that still has holes, so a
proof that takes thousands of lines and many turns has somewhere to live before
it is finished — without letting anything unfinished be reported as done.

**Architecture:** The kernel already answers "is this really finished"
transitively: `#print axioms` reports `sorryAx` for any declaration resting on a
hole, through imports. So the refusal moves from the door (`save_lean`) to the
claim (`report_result`, the obligations, the banner), and `sorryAx` becomes a
recorded audit status rather than a rejection. Separately, `theorem` becomes a
reserved word — registered via `record_name` before it may be stated — so that
`lemma` is the cheap path by construction rather than by request.

**Tech Stack:** Python 3, `uv`, pytest. Lean 4 / Mathlib in production; the
hermetic suite drives `tests/fake_lean.py`, which models `#print axioms` through
`-- axioms: …` markers propagated into its fake oleans.

**Spec:** `docs/superpowers/specs/2026-08-27-lean-partial-results-design.md`

## Global Constraints

- Run the suite with `uv run --extra test pytest`. Coverage floor is enforced
  from `pyproject.toml`; add `--cov` when measuring.
- `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` must stay
  consistent with each other and with the code (repository rule, `AGENTS.md`).
- The Lean kernel is the authority. Never weaken or strengthen a theorem to make
  something pass. Partial results are valid only when their remaining holes and
  assumptions are explicit.
- The `batch` / `prove` / one-shot paths (`verifier.py`, `runner.py`,
  `acceptance.py`) keep refusing holes outright. They have no human to ask. Only
  the interactive session in `chat.py` may hold a partial result.
- Comments in this codebase explain *why*, and frequently name the concrete
  failure a rule prevents. Match that register; do not add narration comments.
- The string `sorryAx` is Lean's, and the only hole axiom. `audit.FORBIDDEN` is
  `frozenset({"sorryAx"})` and stays that way in this change.

---

### Task 1: `sorryAx` becomes a status, not a rejection

`audit.py` is pure — no subprocess, no filesystem. It keeps reporting `sorryAx`
as a finding, because its docstring's rule holds: the callers differ in what they
*do* about a finding, not in what counts as one. What changes is that a verdict
whose only problem is a hole gets its own name, so a caller can tell "unfinished"
from "unacceptable" without knowing the axiom's spelling.

`runner.py:61` and `verifier.py:208` both accept only `status == "clean"`, so a
new status leaves them refusing exactly as they do now. That is the property that
makes this safe.

**Files:**
- Modify: `src/hardy/audit.py:35` (the `status` comment), `:113-123`
  (`classify`), `:147-174` (`summarise`)
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `audit.classify(reports, approved) -> Verdict` where `Verdict.status` is now
    one of `"clean" | "modulo" | "open" | "rejected"`.
  - `audit.open_declarations(record: Mapping[str, Any]) -> tuple[str, ...]` —
    the declarations a stored audit record says rest on a hole, sorted.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit.py`:

```python
def test_a_hole_is_open_rather_than_rejected():
    """An unfinished proof is not an unacceptable one, and the two need names."""
    verdict = audit.classify([audit.AxiomReport("thm", ("propext", "sorryAx"))], ())
    assert verdict.status == "open"
    assert verdict.forbidden == ("sorryAx",)


def test_a_hole_beside_an_unapproved_axiom_is_still_rejected():
    """The unapproved axiom is the actionable half, and it does not become one."""
    verdict = audit.classify([audit.AxiomReport("thm", ("sorryAx", "Smith.main"))], ())
    assert verdict.status == "rejected"
    assert verdict.unapproved == ("Smith.main",)


def test_a_hole_beside_an_approved_assumption_is_open():
    """Open outranks modulo: the proof is unfinished whatever it also assumes."""
    verdict = audit.classify(
        [audit.AxiomReport("thm", ("sorryAx", "Smith.main"))], ("Smith.main",)
    )
    assert verdict.status == "open"
    assert verdict.assumed == ("Smith.main",)


def test_an_open_verdict_reads_as_a_hole():
    verdict = audit.classify([audit.AxiomReport("thm", ("sorryAx",))], ())
    assert "hole" in audit.describe(verdict)
    assert "['thm']" in audit.describe(verdict)


def test_open_declarations_names_what_rests_on_a_hole():
    record = audit.classify(
        [audit.AxiomReport("open_one", ("sorryAx",)), audit.AxiomReport("done", ("propext",))],
        (),
    ).as_dict()
    assert audit.open_declarations(record) == ("open_one",)


def test_a_record_that_never_graded_has_no_open_declarations():
    """`unestablished` and `not audited` carry no declarations to read."""
    assert audit.open_declarations(audit.unestablished("nothing to grade")) == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --extra test pytest tests/test_audit.py -k "open or hole" -v`
Expected: FAIL — `status` is `"rejected"`, and `open_declarations` does not exist.

- [ ] **Step 3: Implement**

In `src/hardy/audit.py`, change the `Verdict.status` comment at line 35:

```python
    status: str  # "clean" | "modulo" | "open" | "rejected"
```

Replace the status computation at the end of `classify` (currently lines
118-123):

```python
    # No reports at all is a rejection, not a clean sweep. A caller that audited
    # nothing has established nothing, and grading that as clean is the exact
    # shape of the bug this module exists to end.
    if not reports:
        status = "rejected"
    elif unapproved:
        # Ahead of the hole: an unapproved axiom is the half a caller can act
        # on, and a save carrying both must be told about that one.
        status = "rejected"
    elif forbidden:
        # A hole is not an assumption and no human may approve one -- but it is
        # a proof that is not finished yet, which is a different fact from a
        # proof that may not be accepted. Callers with nobody to ask refuse
        # anything that is not "clean" and so still refuse this.
        status = "open"
    else:
        status = "modulo" if assumed else "clean"
    return Verdict(status, tuple(reports), forbidden, unapproved, assumed)
```

Add a branch to `summarise`, immediately after the `not established` branch:

```python
    if status == "open":
        return f"open -- {list(_open(record))} rest on a hole"
```

Add, below `dependents`:

```python
def _open(record: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(entry["name"])
            for entry in record.get("declarations", ())
            if any(axiom in FORBIDDEN for axiom in entry.get("axioms", ()))
        )
    )


def open_declarations(record: Mapping[str, Any]) -> tuple[str, ...]:
    """The declarations a stored record says rest on a hole.

    Read from the record rather than from a `Verdict`, because every caller that
    needs this holds one read back from `session.json` -- and a record that never
    graded anything carries no declarations, so it answers nothing rather than
    answering "none rest on a hole".
    """
    return _open(record)
```

Add `Mapping` to the `collections.abc` import at the top of the file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_audit.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Check nothing downstream loosened**

Run: `uv run --extra test pytest tests/unit/test_verifier.py tests/test_hardy.py -v`
Expected: PASS. These are the one-shot paths; they must still refuse `sorryAx`.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/audit.py tests/test_audit.py
git commit -m "Tell an unfinished proof apart from an unacceptable one"
```

---

### Task 2: the save accepts a hole

`_final_gates` loses its textual hole refusal, and `_audit_tree` refuses on the
verdict's *status* rather than on the presence of a forbidden axiom. Everything
else about the save is unchanged: an error still refuses, a private theorem still
refuses, an unapproved axiom still refuses, and a save that breaks a dependent is
still refused whole.

Four existing tests assert the old behaviour and are rewritten here, not deleted:
each one's subject still matters, and only its expected answer moved.

**Files:**
- Modify: `src/hardy/chat.py:589-600` (`_final_gates`), `:1398-1416` (the
  refusal branches of `_audit_tree`)
- Test: `tests/test_chat.py:141-147`, `tests/test_chat_audit.py:38-55`,
  `tests/test_chat_workspace.py:134-145`

**Interfaces:**
- Consumes: `audit.classify`'s `"open"` status from Task 1.
- Produces: `save_lean` accepts a source containing `sorry`; the module's stored
  audit record has `status == "open"` and lists the open declarations.

- [ ] **Step 1: Rewrite the four tests that assert the old rule**

In `tests/test_chat.py`, replace `test_saved_lean_must_be_hole_free` with:

```python
def test_saved_lean_may_carry_a_hole(tmp_path: Path):
    """A proof that takes many turns has to survive between them.

    The refusal this replaces meant an in-progress development existed only in
    the model's context and was re-sent in full on every check.
    """
    runtime = FakeChatRuntime([
        call("save_lean", {"source": "import Mathlib\nlemma step : True := by sorry"}),
        {"role": "assistant", "content": "Saved with one hole left."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save the skeleton.")
    assert (tmp_path / "lean" / "Main.lean").exists()
```

In `tests/test_chat_audit.py`, replace `test_a_proof_resting_on_sorry_ax_is_refused`
and `test_a_hole_is_never_offered_for_approval` with:

```python
def test_a_proof_resting_on_sorry_ax_is_saved_and_recorded_open(tmp_path: Path):
    """The hole is kept, and named. Refusing it left nowhere to build a proof."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    saved_result = results(tmp_path, "save_lean")[-1]
    assert saved_result["ok"]
    assert saved(tmp_path).exists()
    record = state(tmp_path)["audit"]["Main"]
    assert record["status"] == "open"
    assert "HardyTarget" in str(record["declarations"])


def test_a_hole_is_never_offered_for_approval(tmp_path: Path):
    """A human cannot approve a hole, so nothing may ask them to.

    Still true, and now the interesting case: the save succeeds, so a design
    that reached for approval on the way past would have had one to reach for.
    """
    asked = []
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.confirm = lambda proposal: asked.append(proposal) or True
    chat.send("Save it.")
    assert asked == []
    assert saved(tmp_path).exists()
```

In `tests/test_chat_workspace.py`, replace the source in
`test_an_unelaborable_save_never_reaches_lean` — a hole is no longer a textual
refusal, so the test needs one that still is:

```python
def test_an_unelaborable_save_never_reaches_lean(tmp_path: Path):
    """The textual gates cost nothing, so they run before the minute-long one."""
    runtime = FakeChatRuntime([
        call("save_lean", {"path": "Basic.lean", "source": "import Mathlib\naxiom Sneaky : False\n"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    reached = []
    chat.lean.compile_module = lambda *a, **k: reached.append(1)
    chat.send("Save an unapproved axiom.")
    assert results(tmp_path)[-1]["ok"] is False
    assert reached == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_chat.py::test_saved_lean_may_carry_a_hole tests/test_chat_audit.py -v`
Expected: FAIL — the save is refused, nothing is written.

- [ ] **Step 3: Drop the textual hole gate**

In `src/hardy/chat.py`, `_final_gates`, delete these two lines (currently 597-598):

```python
        if self.lean.has_holes(source):
            return ToolResult(False, "saved Lean artifacts may not contain sorry or admit", source)
```

and rewrite the docstring's second paragraph, which justified them:

```python
        """What disqualifies a source from being saved, before Lean is asked.

        All of it is textual, so it costs nothing and runs first: there is no
        point spending a minute elaborating a file an unapproved axiom already
        rules out.

        A hole is not here. `sorry` is how a proof of any size gets built, and
        refusing it meant the unfinished part of a development could never
        reach disk. What a hole costs is charged where a claim is made: the
        audit records it, the obligations name it, and `report_result` grades
        it partial.
        """
```

- [ ] **Step 4: Refuse on the status, not on the axiom**

In `_audit_tree`, replace the two refusal branches (currently lines 1399-1416)
with:

```python
        if verdict.status == "rejected":
            if verdict.unapproved:
                needed = {
                    axiom: list(audit.dependents(reports, axiom)) for axiom in verdict.unapproved
                }
                return ToolResult(
                    False,
                    f"the axiom audit refused this save: {audit.describe(verdict)}. "
                    f"These assumptions reached through imports have not been approved: {needed}. "
                    "Call request_assumption for each before saving work that rests on it.",
                )
            return ToolResult(
                False,
                f"the axiom audit refused this save: {audit.describe(verdict)}. "
                f"{list(audit.dependents(reports, verdict.forbidden[0]))} depend on a hole, "
                "which cannot be approved.",
            )
```

The order is inverted from the old code deliberately: a save carrying both a
hole and an unapproved axiom is refused for the axiom, which is the half the
model can do something about. A hole on its own is no longer a refusal at all,
so the second branch is now reached only when a verdict is rejected for a
forbidden axiom that is not accompanied by an unapproved one — which, while
`FORBIDDEN` is exactly `{"sorryAx"}`, `classify` never produces. It is kept
because deleting it would make the message vanish the moment `FORBIDDEN` grows,
and `verdict.forbidden[0]` would then be read off a branch nobody wrote.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat.py tests/test_chat_audit.py tests/test_chat_workspace.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite to find what else assumed the old rule**

Run: `uv run --extra test pytest`
Expected: PASS. If a test fails, read it before changing it: a test asserting
that a hole cannot reach a *published* artifact is still right and must keep
passing; only tests asserting a hole cannot reach the *workspace* are stale.

- [ ] **Step 7: Commit**

```bash
git add src/hardy/chat.py tests/test_chat.py tests/test_chat_audit.py tests/test_chat_workspace.py
git commit -m "Let the unfinished half of a proof reach disk"
```

---

### Task 3: an open theorem is an obligation

A hole that nobody is told about is worse than one that is refused. This puts the
open set on all three surfaces at once — the note appended to every save,
`/status`, and the end-of-turn line — by adding it to `_obligations()`, which all
three already share.

Two consequences are load-bearing and must land in the same task, or the design
defeats itself. An open theorem owes no writeup yet: `completion.outstanding` is
given only the closed theorems, so a skeleton does not instantly owe a paragraph
about a theorem that is not proved. And the catch-up ratchet ignores `open`
obligations, so a development may hold two open results at once.

The obligations name open *theorems*. Open lemmas are reported to the model by
the save's own audit note (`audit.describe` now says which declarations rest on a
hole), which is the surface that knows about them; the obligations are about
what stands between the workspace and a report, and only a theorem is reportable.

**Files:**
- Modify: `src/hardy/completion.py:85` (`KINDS`)
- Modify: `src/hardy/chat.py` — new `_open_declarations` and `_open_theorems`
  beside `_audit_gaps` (~`:2449`); `_saved_statements` (`:1592`); `_obligations`
  (`:1672-1707`); `_documentation_gate` (`:1779`)
- Test: `tests/test_chat_audit.py`

**Interfaces:**
- Consumes: `audit.open_declarations(record)` from Task 1; the saved records from
  Task 2.
- Produces:
  - `completion.KINDS` begins with `"open"`.
  - `Chat._open_theorems() -> set[str]` — saved theorem names currently resting
    on a hole, from non-stale audit records.
  - An `Obligation(kind="open", subject=<name>, detail="still open -- rests on a hole")`
    per open theorem, first in `_obligations()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_audit.py`:

```python
OPEN_LEMMA = "import Mathlib\n\nlemma hardyStep : True := by exact True.intro -- axioms: sorryAx\n"


def test_an_open_theorem_is_named_in_what_the_workspace_owes(tmp_path: Path):
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    owed = chat.obligations()
    assert [item.kind for item in owed][0] == "open"
    assert any(item.subject == "HardyTarget" and "hole" in item.detail for item in owed)


def test_an_open_theorem_owes_no_writeup_yet(tmp_path: Path):
    """Otherwise a skeleton owes a paragraph about a theorem nobody has proved."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    owed = chat.obligations()
    assert {item.kind for item in owed} == {"open"}


def test_closing_the_hole_moves_the_obligation_to_the_writeup(tmp_path: Path):
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"source": CLEAN}, "lean"),
        ]),
    )
    chat.send("Save it, then close it.")
    kinds = {item.kind for item in chat.obligations()}
    assert "open" not in kinds
    assert "record" in kinds


def test_an_open_theorem_does_not_block_the_next_one(tmp_path: Path):
    """The catch-up ratchet is about writeups, and an open theorem owes none."""
    second = "import Mathlib\n\ntheorem HardySecond : True := by exact True.intro -- axioms: sorryAx\n"
    chat = session(
        tmp_path,
        FakeChatRuntime([
            call("save_lean", {"source": HOLED}, "lean"),
            call("save_lean", {"path": "Second.lean", "source": second}, "lean"),
        ]),
    )
    chat.send("Save two skeletons.")
    assert results(tmp_path, "save_lean")[-1]["ok"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_chat_audit.py -k "open or closing" -v`
Expected: FAIL — no `open` obligation exists, and the second save is refused by
the ratchet.

- [ ] **Step 3: Add the kind**

In `src/hardy/completion.py`, replace `KINDS` and extend the comment above it:

```python
#: The kinds an obligation comes in, worst first. Order is what `describe`
#: reports in and what a caller showing only the first should show.
#:
#: `open` sits first: a theorem whose proof still has a hole is not a theorem
#: yet, and every other obligation is about describing one that is.
#:
#: `theorem` sits third: a document asserting a claim nothing backs is worse
#: than one that backs its claims imprecisely, and not as bad as having no Lean
#: at all.
KINDS = ("open", "lean", "theorem", "statement", "record", "label", "appendix", "assumption")
```

- [ ] **Step 4: Read the open set out of the stored records**

In `src/hardy/chat.py`, add beside `_audit_gaps`:

```python
    def _open_declarations(self) -> set[str]:
        """Every saved declaration Lean reported resting on a hole.

        Read from the stored audit records, which are stamped with the build
        signature they were established under, and skipping the ones that no
        longer hold: a stale record is not evidence that a theorem is open, and
        it is not evidence that it is closed either. `_audit_gaps` already
        reports a stale record as its own obligation, so nothing is lost here.
        """
        try:
            signatures = self.lean_workspace.current_signatures()
        except ImportCycle:
            # `_audit_gaps` reports the cycle. Answering "nothing is open" to a
            # tree that does not order would be a claim, and this has none.
            return set()
        found: set[str] = set()
        for module, record in self.state.get("audit", {}).items():
            current = self._still_current(module, record, signatures)
            if not current.get("stale"):
                found.update(audit.open_declarations(current))
        return found

    def _open_theorems(self) -> set[str]:
        """The open declarations that are theorems, which is what is reportable.

        An open `lemma` is reported to the model by the save's own audit note,
        which names every declaration in the rebuilt modules that rests on a
        hole. The obligations answer a narrower question -- what stands between
        this workspace and a report -- and a lemma was never reportable.
        """
        return self._open_declarations() & self._saved_theorems()
```

- [ ] **Step 5: Exempt open theorems from the writeup obligations**

In `_saved_statements`, filter the open ones out and say why:

```python
    def _saved_statements(self) -> dict[str, str]:
        """Every *closed* saved theorem, with the exact statement Lean was given.

        Theorems only. A `lemma` is scaffolding and owes nothing, which is the
        same line `_saved_theorems` draws and has to stay the same line: a
        writeup gate that demanded a paragraph for every helper would make
        splitting a proof into helpers the expensive way to work.

        Open theorems are left out for the same reason in the other direction.
        A theorem whose proof still has a hole is not a result yet; demanding
        that the document carry it would ask for a paragraph asserting
        something nobody has proved, and would block the next save behind it.
        Its obligation is that it is open, and the writeup obligations attach
        the moment the hole closes.
        """
        opened = self._open_theorems()
        found: dict[str, str] = {}
        for source in self.lean_workspace.sources().values():
            theorems = set(declarations(source)["theorem"]) - opened
            found.update(
                {name: text for name, text in statements(source).items() if name in theorems}
            )
        return found
```

- [ ] **Step 6: Put the open set into the obligations**

In `_obligations`, replace the final `return` (currently line 1707):

```python
        opened = tuple(
            completion.Obligation("open", name, "still open -- rests on a hole")
            for name in sorted(self._open_theorems())
        )
        return (
            *opened,
            *shared,
            *self._audit_gaps(self._saved_theorems() - self._open_theorems()),
            *self._stale_writeup(),
            *owed,
        )
```

`_audit_gaps` is asked only about closed theorems: an open one has a current
audit record — that record is *how* Hardy knows it is open — so asking would
report nothing, and asking about a theorem whose only problem is its hole would
say the same thing twice.

- [ ] **Step 7: Make the ratchet ignore an open theorem**

In `_documentation_gate`, replace the first two statements of the body:

```python
        owed = [item for item in self._obligations() if item.kind != "open"]
        if not owed:
            return None
```

and extend the docstring with a third paragraph:

```
        `open` obligations are not counted. They are not a writeup this save is
        running ahead of -- an open theorem owes no writeup at all yet -- and
        counting them would stop a development the moment it held one
        unfinished result, which is the state a long proof is in for most of
        its life.
```

`completion.describe(owed)` further down takes a list rather than a tuple now;
no change is needed, it iterates.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_audit.py -v`
Expected: PASS.

- [ ] **Step 9: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/hardy/chat.py src/hardy/completion.py tests/test_chat_audit.py
git commit -m "Say which theorems are still open, on every surface that answers"
```

---

### Task 4: `theorem` must be registered before it may be stated

In live sessions the model writes `theorem` for every intermediate step and never
`lemma`, so the scaffolding exemption never fires and the ratchet blocks the next
save. Asking again in the prompt is not a fix; `completion.py`'s own rule is that
a rule a model can talk its way past is not a rule.

So `theorem` becomes reserved: a save may not introduce one whose name is not
already in the `record_name` registry. Registration costs something — a
`latex_name` and a description, and a promise the writeup ratchet then collects
on — which is what makes `lemma` the cheap path by construction.

One existing guard has to be adjusted first or this deadlocks: registering a name
and then saving anything before the file that declares it would be refused.

**Files:**
- Modify: `src/hardy/chat.py:1538-1555` (`_missing_registered_names`),
  `:1223-1233` (`_save_lean`'s gate order), new `_result_gate` beside
  `_documentation_gate` (`:1770`)
- Test: `tests/test_chat_workspace.py`

**Interfaces:**
- Consumes: `Chat._saved_theorems()`, `Chat.state["names"]`, `Chat._resolves`.
- Produces:
  - `Chat._result_gate(source: str) -> str | None` — the refusal for an
    unregistered theorem, or `None`.
  - `Chat._missing_registered_names(sources, before)` — now takes the committed
    sources as a second argument and reports only names that vanished.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_workspace.py`:

```python
def test_an_unregistered_theorem_is_refused_and_told_about_lemma(tmp_path: Path):
    """The keyword is the ratchet's hinge, and the model never reaches for it."""
    runtime = FakeChatRuntime([
        call("save_lean", {"source": "import Mathlib\ntheorem hardyStep : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save a step as a theorem.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "lemma" in refusal["output"] and "record_name" in refusal["output"]
    assert not (tmp_path / "lean" / "Main.lean").exists()


def test_a_registered_theorem_saves(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "hardyStep", "latex_name": "thm:step", "description": "The step."}),
        call("save_lean", {"source": "import Mathlib\ntheorem hardyStep : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Register it, then save it.")
    assert results(tmp_path, "save_lean")[-1]["ok"]


def test_a_lemma_needs_no_registration(tmp_path: Path):
    runtime = FakeChatRuntime([
        call("save_lean", {"source": "import Mathlib\nlemma hardyStep : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save a lemma.")
    assert results(tmp_path, "save_lean")[-1]["ok"]


def test_registering_a_name_does_not_block_an_unrelated_save(tmp_path: Path):
    """Registration comes first, so the tree is briefly behind the registry."""
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "hardyMain", "latex_name": "thm:main", "description": "The result."}),
        call("save_lean", {"path": "Helper.lean", "source": "import Mathlib\nlemma hardyHelp : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Saved."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Register the result, then save a helper.")
    assert results(tmp_path, "save_lean")[-1]["ok"]


def test_a_registered_theorem_that_disappears_is_still_refused(tmp_path: Path):
    """The guard exists so a mapped declaration cannot vanish. It still does."""
    runtime = FakeChatRuntime([
        call("record_name", {"formal_name": "hardyStep", "latex_name": "thm:step", "description": "The step."}),
        call("save_lean", {"source": "import Mathlib\ntheorem hardyStep : True := by exact True.intro\n"}),
        call("save_lean", {"source": "import Mathlib\nlemma hardyOther : True := by exact True.intro\n"}),
        {"role": "assistant", "content": "Refused."},
    ])
    chat = session(tmp_path, runtime)
    chat.send("Save it, then overwrite it away.")
    refusal = results(tmp_path, "save_lean")[-1]
    assert not refusal["ok"]
    assert "hardyStep" in refusal["output"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_chat_workspace.py -k "registered or registration or lemma_needs" -v`
Expected: FAIL — the unregistered theorem saves, and the registration test is
refused by the vanish guard.

- [ ] **Step 3: Fix the vanish guard first**

In `src/hardy/chat.py`, change `_missing_registered_names` to take what the tree
held before the save and report only what the save would remove:

```python
    def _missing_registered_names(
        self, sources: dict[str, str], before: dict[str, str]
    ) -> list[str]:
        """Registered formal names that this save would remove from the tree.

        An approved assumption is exempt. This guard exists so a *workspace
        declaration* cannot vanish while the registry still points at it, and an
        axiom reached through an import was never a workspace declaration --
        `request_assumption` registers the name a human approved, nothing writes
        it into a file, and demanding one refused every later save with no tool
        to undo it. The exemption is deliberately narrow: a registered theorem
        that disappears is still caught.

        Judged against `before` as well as against the staged tree, because a
        name may also be registered *ahead* of the declaration: `theorem` is
        reserved to registered results, so the order is `record_name` and then
        the save that declares it, and in between the registry names something
        the tree does not have yet. A name that never existed has not vanished.
        """
        approved = self._approved_assumptions()
        return [
            item["formal_name"]
            for item in self.state["names"]
            if item["formal_name"] not in approved
            and self._resolves(item["formal_name"], before)
            and not self._resolves(item["formal_name"], sources)
        ]
```

In `_save_lean`, capture the committed sources before staging and pass them
through. Add above `self.build_shared()`:

```python
        committed = self.lean_workspace.sources()
```

and change the call (currently line 1269):

```python
            lost = self._missing_registered_names(shadow.sources(), committed)
```

- [ ] **Step 4: Add the reservation gate**

Add beside `_documentation_gate` in `src/hardy/chat.py`:

```python
    def _result_gate(self, source: str) -> str | None:
        """`theorem` is reserved to results a human will be shown.

        The writeup ratchet turns on the keyword: a `theorem` owes a paragraph
        and a `lemma` owes nothing, so that splitting a proof into helpers is
        the cheap way to work. In practice the model states every intermediate
        step as a `theorem`, the exemption never fires, and the ratchet stops a
        development that has done nothing wrong. Asking for `lemma` in the
        prompt did not change that, and a rule a model can talk its way past is
        not a rule.

        So the keyword is not a matter of style here. A result is something
        `record_name` has already mapped to a place in the document, which
        costs a `latex_name` and a description and is a promise the ratchet
        then collects on -- and everything else is a `lemma`, which is free.

        Only what this save *introduces*, so a workspace written before this
        rule can still be repaired, restated, or deleted.
        """
        registered = {item["formal_name"] for item in self.state["names"]}
        existing = self._saved_theorems()
        unregistered = [
            name
            for name in declarations(source)["theorem"]
            if name not in existing and name not in registered
        ]
        if not unregistered:
            return None
        return (
            f"`{unregistered[0]}` is not a registered result, so it may not be stated as a "
            "`theorem`. State it as a `lemma` if it is scaffolding or an intermediate step -- "
            "a lemma owes no writeup and is free to save. If it is a result you will write "
            "up, call record_name for it first."
        )
```

Call it in `_save_lean`, before the documentation gate (currently line 1228):

```python
        gate = self._result_gate(source) or self._documentation_gate(source)
        if gate is not None:
            return ToolResult(False, gate, source)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_workspace.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite and repair the fixtures it breaks**

Run: `uv run --extra test pytest`
Expected: FAIL in tests whose fixtures save an unregistered `theorem`. For each
one, decide which the declaration actually is: if the test is about a *result*
(it is written up, reported, or labelled), add a `record_name` call before the
save; if it is scaffolding, change `theorem` to `lemma`. Do not weaken the gate
to accommodate a fixture.

- [ ] **Step 7: Commit**

```bash
git add src/hardy/chat.py tests/
git commit -m "Reserve theorem for results, so that lemma is the cheap path"
```

---

### Task 5: `report_result` grades partial, and the banner counts what is open

One reporting path, one set of gates. The report gains a status: `partial` when
anything it claims rests on a hole, naming exactly which declarations those are.
Everything else it checks stays — the summary, the theorem saved and audited, the
label LaTeX really created, the statement quoted verbatim, the appendix.

The banner has to move with it. `checked` currently counts every saved theorem
with no audit gap, and an open theorem has no audit gap — its record is current
and says it is open — so without this the document would call a theorem with a
hole in it machine-checked. That is the exact overstatement `_stamp`'s docstring
refuses to make.

**Files:**
- Modify: `src/hardy/chat.py:2216-…` (`_report_result`), `:2359-2377` (`_stamp`),
  `:2418-2421` (`_stamp_inputs`), `:115-130` (the `report_result` tool
  description)
- Test: `tests/test_chat_audit.py`, `tests/test_chat_ratchet.py`

**Interfaces:**
- Consumes: `Chat._open_theorems()` from Task 3.
- Produces: a `report_result` output containing `partial` and the open names when
  anything claimed is open; `_stamp` excluding open theorems from `checked`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_audit.py`:

```python
def test_a_report_naming_an_open_theorem_is_graded_partial(tmp_path: Path):
    """It is a real result to have got this far, and it is not a proof."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    reported = chat._tool("report_result", {"theorems": ["HardyTarget"], "summary": "As far as I got."})
    assert "partial" in reported.output
    assert "HardyTarget" in reported.output


def test_an_open_theorem_is_not_counted_as_machine_checked(tmp_path: Path):
    """A banner that calls a holed proof checked is worse than no banner."""
    chat = session(tmp_path, FakeChatRuntime([call("save_lean", {"source": HOLED}, "lean")]))
    chat.send("Save it.")
    assert "0 theorems machine-checked" in chat._stamp()
    assert "1 theorem still open" in chat._stamp()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_chat_audit.py -k "partial or machine_checked" -v`
Expected: FAIL — no status is reported and the banner counts the open theorem as
checked.

- [ ] **Step 3: Grade the report**

In `_report_result`, after the claimed names are resolved and every existing
check has passed, compute the status from the open set and put it in the output.
Find the successful return at the end of the method and replace it with:

```python
        opened = sorted(self._open_theorems() & set(resolved))
        status = "partial" if opened else "clean"
        note = (
            f" These rest on a hole and are not proved: {opened}."
            if opened
            else ""
        )
```

and include `status` and `note` in the recorded report and in the returned
`ToolResult` message. Extend the method's docstring with:

```
        A report may be partial. A development that has closed nine lemmas and
        left one hole in the tenth has established something real, and had
        nowhere to say so: the only alternatives were to claim a proof it does
        not have, or to say nothing. What it may never do is present an open
        theorem as a closed one, which is why the status is computed from the
        audit records rather than taken from the model, and why the writeup
        still has to carry an open theorem on exactly the same terms -- a
        reader who cannot see the statement cannot tell which half was done.
```

- [ ] **Step 4: Fix the banner**

In `_stamp`, replace the counting lines (currently 2359-2362):

```python
        owed = self._obligations()
        unbacked = sum(1 for item in owed if item.kind == "theorem")
        gaps = {item.subject for item in owed if item.kind == "lean"}
        opened = {item.subject for item in owed if item.kind == "open"}
        checked = len(self._saved_theorems() - gaps - opened)
```

and add a clause after the `unbacked` one:

```python
        if opened:
            count = len(opened)
            parts.append(
                f"{count} theorem{'' if count == 1 else 's'} here "
                f"{'is' if count == 1 else 'are'} still open"
            )
```

Extend `_stamp`'s docstring with:

```
        An open theorem is not machine-checked. Its audit record is current --
        being current is how Hardy knows it is open -- so `_audit_gaps` reports
        nothing about it, and counting it would put a theorem with a hole in it
        under the word "machine-checked".
```

In `_stamp_inputs`, add the open set, with the reason:

```python
        return {
            "goal": self.goal(),
            "assumptions": sorted(str(item["formal_name"]) for item in self.state["assumptions"]),
            # A theorem that was closed when the PDF was compiled and has since
            # been reopened is the overstating direction: the banner goes on
            # calling it machine-checked. The other direction -- a hole closed
            # after the compile -- understates, and the ratchet already forces
            # the writeup to carry it before anything is reportable.
            "open": sorted(self._open_theorems()),
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --extra test pytest tests/test_chat_audit.py tests/test_chat_ratchet.py -v`
Expected: PASS.

- [ ] **Step 6: Update the tool description**

In `src/hardy/chat.py`, the `report_result` entry in `CHAT_TOOLS` (line 125),
append to its `description`:

```
A report is graded partial, not refused, when a theorem it names still rests on a hole: it names which, and the writeup must still carry the statement so a reader can see what was and was not proved.
```

- [ ] **Step 7: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/hardy/chat.py tests/
git commit -m "Grade a report partial rather than refusing what was really done"
```

---

### Task 6: say all of this where the model and the reader will see it

The prompt currently promises *"Partial work is welcome when holes and
assumptions are explicit"* and nothing implements it. After Task 5 it is true,
and the mechanism has to be described — along with the reservation of `theorem`,
which changes what the model may write.

**Files:**
- Modify: `src/hardy/prompts/chat.md.j2`
- Modify: `README.md`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html`
- Test: `tests/test_chat_wiring.py` or wherever the prompt is asserted on

- [ ] **Step 1: Find what asserts on the prompt**

Run: `uv run --extra test pytest --collect-only -q | grep -i prompt`
and `grep -rn "Partial work is welcome" src tests`
Expected: the set of tests that read the rendered prompt.

- [ ] **Step 2: Rewrite the prompt's Lean paragraph**

In `src/hardy/prompts/chat.md.j2`, in the bullet describing `check_lean` and
`save_lean`, replace the last sentence (*"Saved work you describe as complete
must contain no sorry and no admit."*) with:

```
A file may be saved with `sorry` in it. That is how a long proof is built: save the skeleton, keep the parts that work, and fill the holes one at a time across as many turns as it takes. Hardy asks Lean what every saved declaration rests on, records which ones rest on a hole, and names them after every save and on the user's screen. Nothing resting on a hole is proved, and a report that names one is graded partial rather than complete.
```

- [ ] **Step 3: State the reservation of `theorem`**

In the paragraph beginning *"Every `theorem` you save owes a writeup"*, replace
the closing sentence (*"The corollary is the rule that matters…"*) with:

```
So `theorem` is reserved: Hardy refuses to save one whose name `record_name` has not already mapped to a place in the document. State as a `theorem` only what you would report to the user as a result, register it before you state it, and state everything else -- every intermediate step, every helper, every piece of scaffolding -- as a `lemma`, which owes nothing and is free to save.
```

- [ ] **Step 4: Update the documents**

- `FEATURES.md`: under "Search and orchestration", record that the hole-carrying
  skeleton now exists — *Sketch and discharge* is listed there as **Later** and
  this is its first prerequisite. Under the end-to-end behaviour list, record
  that an interactive partial result is now a thing the workspace can hold and
  the report can grade.
- `DESIGN.md` and `README.md`: describe the invariant in its new form — a saved
  Lean file elaborates and Hardy knows which of its declarations rest on a hole —
  and the reservation of `theorem`.
- `ARCHITECTURE.html`: keep the visual overview in step with whichever boxes
  changed.
- `AGENTS.md`: no change. It already says what this builds.

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/prompts/chat.md.j2 README.md DESIGN.md FEATURES.md ARCHITECTURE.html tests/
git commit -m "Say where a hole may live, and what theorem is now reserved for"
```

---

## Self-review notes

**Spec coverage.** §2.1 → Task 2 Step 3. §2.2 → Task 1 and Task 2 Step 4. §2.3 →
Task 1 Step 5 (the one-shot paths are asserted unchanged, not modified). §3 →
Task 3. §3.1 → Task 3 Step 5. §3.2 → Task 3 Step 7. §4 → Task 4 Step 4. §4.1 →
Task 4 Step 3. §5 → Task 5. §6 → the tests in Tasks 1-5. §7 → Task 6.

**One spec test is dropped.** The spec's test 12, "a forbidden axiom other than
`sorryAx` still refuses the save", cannot be written: `FORBIDDEN` is exactly
`{"sorryAx"}`, so `classify` has no way to produce that verdict, and a test that
mutated the frozenset would be asserting on a configuration the product does not
have. The branch is kept in `_audit_tree` (Task 2 Step 4) with the reason stated
there.
