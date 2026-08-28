# Harness Steering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make five failure modes of an interactive Hardy session structurally unavailable: a search tool that reports a timeout as absence, `save_lean` used as a compile loop, a model that never sees its own aggregate state, an axiom request with no evidence of search and a vacuous conclusion, and guessed Mathlib imports on one monolithic file.

**Architecture:** All changes sit in the interactive session (`src/hardy/chat.py`), its search façade (`src/hardy/search_tools.py`), the Lean service (`src/hardy/lean.py`), the LaTeX helpers (`src/hardy/latex.py`), and the chat prompt template. Per-session steering state (streaks, tallies, inspected names, rejected statements) lives in memory on `MathematicsSession`, never in the manifest. Every refusal is a `ToolResult(False, ...)` the model can read; every warning to the human rides in the `request_assumption` proposal dict.

**Tech Stack:** Python 3.12, pytest, Pydantic `FrozenModel`s, Jinja2 prompt templates. Tests are hermetic: `tests/fake_lean.py` stands in for Lean; `tests/unit/conftest.py` provides `session`, `session_factory`, `approvals`, `fake_lean`.

**Spec:** `docs/superpowers/specs/2026-08-27-harness-steering-design.md`

## Global Constraints

- Never require WSL; nothing POSIX-only (see memory: no-wsl-platform-policy). Tests run on Windows.
- Do not touch the staged prompt set (`prompts/staged/*`) or `PROMPT_SET_VERSION`; `chat.md.j2` is outside the hash.
- No manifest (`session.json`) schema change; no `.local/state.json` change.
- `SAVE_STREAK_LIMIT = 3`, a class constant on `MathematicsSession`.
- Refusal / hint wording is copied verbatim from the spec sections quoted in each task.
- Run the unit suite with `uv run pytest tests/unit tests/test_chat*.py tests/test_latex_tree.py -q` before every commit in this plan. Integration tests (`tests/integration`) need real Lean and are run only where a task says so.
- Commit messages follow the repo style: an imperative first line that says what changed and why, no `feat:` prefix. End with the Co-Authored-By and Claude-Session trailers.

---

## File map

| File | Change |
|---|---|
| `src/hardy/lean.py` | `DeclarationInspection` gains `success`, `timed_out`; `inspect_declarations` populates them |
| `src/hardy/search_tools.py` | all-unavailable hint on `inspect_declarations`; multi-word hint on `search_modules` miss |
| `src/hardy/prompts/chat.md.j2` | `import Mathlib` default; decomposition sentence |
| `src/hardy/latex.py` | `unreached_fragments(sources) -> list[str]` |
| `src/hardy/chat.py` | streak brake, tool tally, steering block in `stream()`, `tex_unreached` in listing, `_inspected`/`_rejected` tracking, `_vacuity_probe`, `_strip_hypotheses` |
| `tests/unit/test_lean.py` | §2 service tests |
| `tests/unit/test_search_honesty.py` | §2 façade tests |
| `tests/unit/test_search_tools.py` | §6 `search_modules` miss test |
| `tests/unit/test_prompt_steering.py` (new) | §6 prompt text tests |
| `tests/unit/test_steering.py` (new) | §3 streak brake, §4 tally and steering block |
| `tests/test_latex_tree.py` | §4 `unreached_fragments` |
| `tests/unit/test_assumption_evidence.py` (new) | §5a/5c |
| `tests/unit/test_assumption_gates.py` + `tests/unit/conftest.py` | §5b fixture and probe tests |
| `tests/integration/test_lean_real.py` | §5b real-Lean vacuity test |

---

### Task 1: `DeclarationInspection` carries `success` and `timed_out`

**Files:**
- Modify: `src/hardy/lean.py:164-168` (class), `src/hardy/lean.py:652-687` (method)
- Test: `tests/unit/test_lean.py`

**Interfaces:**
- Produces: `DeclarationInspection.success: bool`, `DeclarationInspection.timed_out: bool`. Consumed by `_did_not_finish` in Task 2 via `getattr`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_lean.py`, after `test_inspect_declarations_returns_resolved_signatures`:

```python
def _inspecting_service(tmp_path, runner):
    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    return lean.LeanService(
        lake=tmp_path / 'lake.exe',
        lean_project=tmp_path,
        environment=_claim(domain).environment,
        limits=domain.RunLimits(),
        runner=runner,
    )


def test_an_inspection_lean_was_stopped_on_says_so(tmp_path) -> None:
    """Every name came back `unavailable` with nothing to distinguish
    "Lean said no" from "Lean was killed". A live session read the second
    as the first, about `IsCyclic` and `Subgroup.center`."""
    process = importlib.import_module('hardy.process')

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=None, stdout='', stderr='',
            timed_out=True, output_overflow=False, duration_ms=180_000,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('IsCyclic',))

    assert inspection.timed_out is True
    assert inspection.success is False
    assert inspection.unavailable == ('IsCyclic',)


def test_an_inspection_that_answered_with_unknown_names_is_a_success(tmp_path) -> None:
    """`#check Nope` is an error to Lean, but the batch *answered*."""
    process = importlib.import_module('hardy.process')
    message = json.dumps({
        'data': "unknown identifier 'Nope'", 'fileName': 'Inspect.lean',
        'pos': {'line': 3, 'column': 7}, 'severity': 'error',
    })

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout=message, stderr='',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is True
    assert inspection.timed_out is False
    assert inspection.unavailable == ('Nope',)


def test_an_inspection_that_failed_silently_is_not_a_success(tmp_path) -> None:
    process = importlib.import_module('hardy.process')

    def runner(spec):
        return process.ProcessResult(
            argv=spec.argv, cwd=spec.cwd, returncode=1, stdout='', stderr='crash',
            timed_out=False, output_overflow=False, duration_ms=1,
        )

    inspection = _inspecting_service(tmp_path, runner).inspect_declarations(('Nope',))

    assert inspection.success is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_lean.py -k "inspection" -v`
Expected: 3 FAIL — `AttributeError: 'DeclarationInspection' object has no attribute 'timed_out'` (or validation error on construction).

- [ ] **Step 3: Add the fields and populate them**

In `src/hardy/lean.py`, change the class:

```python
class DeclarationInspection(FrozenModel):
    resolved: tuple[DeclarationRecord, ...]
    unavailable: tuple[str, ...]
    # Whether Lean answered at all. A batch every name of which is
    # `unavailable` is evidence only when this is true: the same shape comes
    # back when the elaboration was stopped before it said anything, and a
    # live session read that as "Mathlib does not have `IsCyclic`".
    success: bool = True
    timed_out: bool = False
    observation_truncated: bool = False
    output_artifact: str | None = None
```

In `LeanService.inspect_declarations`, replace the final `return`:

```python
        return DeclarationInspection(
            resolved=tuple(resolved),
            unavailable=tuple(unavailable),
            # `#check Nope` is an error, so a batch with an unknown name has
            # `check.success=False` while having answered. What makes the
            # answer unusable is Lean saying nothing at all.
            success=check.success or bool(check.diagnostics),
            timed_out=check.process.timed_out,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_lean.py -v`
Expected: all PASS, including the pre-existing inspection test.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/lean.py tests/unit/test_lean.py
git commit -F - <<'EOF'
Let an inspection say whether Lean answered

Every name in a stopped `#check` batch came back `unavailable`, which a
session read as "Mathlib does not have IsCyclic".

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 2: The search façade refuses a stopped inspection and hints on an empty one

**Files:**
- Modify: `src/hardy/search_tools.py:169-181` (`SearchToolRuntime.inspect_declarations`)
- Test: `tests/unit/test_search_honesty.py`

**Interfaces:**
- Consumes: `DeclarationInspection.success/timed_out/resolved` (Task 1).
- Produces: `ToolResult` whose `output` starts with `SPELLINGS_HINT` when a completed batch resolved nothing. Constant `search_tools.SPELLINGS_HINT: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_search_honesty.py`:

```python
class Inspection:
    """Enough of a `DeclarationInspection` for the façade to judge it."""

    def __init__(self, *, resolved=(), unavailable=(), success=True, timed_out=False):
        self.resolved = resolved
        self.unavailable = unavailable
        self.success = success
        self.timed_out = timed_out

    def model_dump_json(self) -> str:
        return (
            f'{{"resolved": [], "unavailable": {list(self.unavailable)!r}, '
            f'"success": {str(self.success).lower()}}}'
        ).replace("'", '"')


class Service:
    def __init__(self, answer):
        self.answer = answer

    def inspect_declarations(self, names):
        return self.answer


def _inspect(answer, names=("IsCyclic",)):
    runtime = search_tools.SearchToolRuntime.__new__(search_tools.SearchToolRuntime)
    runtime.service = Service(answer)
    return runtime.inspect_declarations(list(names))


def test_a_stopped_inspection_is_refused_not_reported_as_absence() -> None:
    result = _inspect(Inspection(unavailable=("IsCyclic",), success=False, timed_out=True))

    assert not result.ok
    assert "NOT a report that nothing matched" in result.output


def test_a_completed_inspection_that_resolved_nothing_hints_at_spellings() -> None:
    result = _inspect(Inspection(unavailable=("IsCyclic",)))

    assert result.ok
    assert result.output.startswith(search_tools.SPELLINGS_HINT)
    assert '"unavailable": ["IsCyclic"]' in result.output


def test_a_completed_inspection_that_resolved_something_has_no_hint() -> None:
    result = _inspect(Inspection(resolved=("x",)))

    assert result.ok
    assert search_tools.SPELLINGS_HINT not in result.output
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_search_honesty.py -v`
Expected: the two hint tests FAIL with `AttributeError: module 'hardy.search_tools' has no attribute 'SPELLINGS_HINT'`; the stopped one PASSES already (Task 1 made `_did_not_finish` see the fields) — that is fine, keep it as a regression guard.

- [ ] **Step 3: Implement**

In `src/hardy/search_tools.py`, after `MAX_NAMES = 20`:

```python
# What a completed batch with nothing resolved is told. It IS evidence -- about
# the spellings. The failing run inspected `IsCyclic`, `Commute` and
# `Subgroup.center` in three batches, got nothing back each time, and wrote
# "Mathlib does not expose this" into three axiom requests.
SPELLINGS_HINT = (
    "none of these names exist under these spellings. That is evidence about the "
    "spellings, not about the result: try qualified or alternate forms "
    "(`Subgroup.center`, `IsPGroup.center_nontrivial`) before concluding anything "
    "is absent from Mathlib.\n"
)
```

Replace the body of `SearchToolRuntime.inspect_declarations` after the `MAX_NAMES` refusal:

```python
        result = self._answer(
            lambda: self.service.inspect_declarations(tuple(str(name) for name in names))
        )
        if result.ok and '"resolved": []' in result.output:
            return ToolResult(True, SPELLINGS_HINT + result.output)
        return result
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_search_honesty.py tests/test_chat_search.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/search_tools.py tests/unit/test_search_honesty.py
git commit -F - <<'EOF'
Tell an empty inspection apart from a stopped one, and say what an empty one means

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 3: `search_modules` explains a multi-word miss

**Files:**
- Modify: `src/hardy/search_tools.py:183-201` (`search_modules`)
- Test: `tests/unit/test_search_honesty.py`

**Interfaces:**
- Produces: constant `search_tools.CONCEPT_HINT: str` appended to the miss refusal when `query.split()` has more than one word.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_search_honesty.py`:

```python
class Modules:
    project = None

    def search(self, query, limit):
        return ()

    def names(self):
        return ("Mathlib.GroupTheory.Sylow",)


def _modules(query):
    runtime = search_tools.SearchToolRuntime.__new__(search_tools.SearchToolRuntime)
    runtime.modules = Modules()
    return runtime.search_modules(query)


def test_a_multi_word_module_miss_says_it_matches_names_not_concepts() -> None:
    """The failing run asked for `Sylow simple group` and `center normal group`."""
    result = _modules("Sylow simple group")

    assert not result.ok
    assert search_tools.CONCEPT_HINT in result.output
    assert "inspect_declarations" in result.output


def test_a_single_word_module_miss_is_unchanged() -> None:
    result = _modules("Sylwo")

    assert not result.ok
    assert search_tools.CONCEPT_HINT not in result.output
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_search_honesty.py -k module_miss -v`
Expected: FAIL, `AttributeError: ... CONCEPT_HINT`.

- [ ] **Step 3: Implement**

After `SPELLINGS_HINT` in `src/hardy/search_tools.py`:

```python
CONCEPT_HINT = (
    "\n`search_modules` matches module *names*, not concepts. For a theorem, use "
    "`inspect_declarations` with several candidate spellings."
)
```

In `search_modules`, change the `if not found:` branch:

```python
        if not found:
            where = f" under {self.modules.project}" if self.modules.project else ""
            message = (
                f"no module in this project has `{query}` in its name; "
                f"{len(self.modules.names())} modules were read from the package index{where}"
            )
            if len(query.split()) > 1:
                message += CONCEPT_HINT
            return ToolResult(False, message)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_search_honesty.py tests/unit/test_search_tools.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/search_tools.py tests/unit/test_search_honesty.py
git commit -F - <<'EOF'
Say that search_modules matches names when a concept is asked for

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 4: The prompt makes `import Mathlib` and decomposition the default

**Files:**
- Modify: `src/hardy/prompts/chat.md.j2`
- Create: `tests/unit/test_prompt_steering.py`

- [ ] **Step 1: Write the failing tests**

```python
"""What the chat prompt says about imports and file shape.

The failing run guessed granular module paths for fifteen calls and put 51
saves through one `Main.lean`; the succeeding run wrote `import Mathlib` in
five small files. The prompt now says to do the second.
"""

from __future__ import annotations

from hardy.prompts import CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_import_mathlib_and_nothing_narrower() -> None:
    assert "Write `import Mathlib` and nothing narrower." in CHAT_SYSTEM_PROMPT


def test_the_prompt_says_to_build_a_proof_as_several_small_files() -> None:
    assert "never as one growing `Main.lean`" in CHAT_SYSTEM_PROMPT
    assert "checked with `check_lean` and saved once it is green" in CHAT_SYSTEM_PROMPT


def test_the_prompt_still_tells_the_model_to_look_modules_up() -> None:
    """The workspace-module case still needs `search_modules`."""
    assert "search_modules" in CHAT_SYSTEM_PROMPT
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_prompt_steering.py -v`
Expected: first two FAIL on the assertion; third PASSES.

- [ ] **Step 3: Edit the template**

In `src/hardy/prompts/chat.md.j2`, the paragraph beginning `A module path you did not read out of Hardy is a memory, not a fact.` — replace its first two sentences (through `and is not one.`) with:

```
Write `import Mathlib` and nothing narrower. Granular imports save nothing here, and every remembered module path is a guess: Mathlib moves modules between versions, and Lean's answer for a path that no longer exists names a missing `.olean` file — which reads like a broken installation and is not one.
```

Keep the rest of the paragraph from `Call `search_modules` before you write an import` onward unchanged.

In the first bullet (`- Check and save Lean.`), after the sentence ending `across as many turns as it takes.`, insert:

```
A proof of any size is built as several small files in dependency order — helper lemmas in their own modules, each checked with `check_lean` and saved once it is green — never as one growing `Main.lean`.
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_prompt_steering.py tests/unit -q -k "prompt"`
Expected: PASS. Also run `uv run pytest tests/unit -q` to confirm no staged-hash test moved (chat is outside the hash; if a test fails on `PROMPT_SET_SHA256`, the edit landed in the wrong file).

- [ ] **Step 5: Commit**

```bash
git add src/hardy/prompts/chat.md.j2 tests/unit/test_prompt_steering.py
git commit -F - <<'EOF'
Tell the model to import Mathlib whole and to build a proof as small files

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 5: Per-session tool tally and the `save_lean` streak brake

**Files:**
- Modify: `src/hardy/chat.py` — `__init__` near line 407 (fields), `_dispatch` at 3419-3424 (tally), `_save_lean` at 1372 (brake), `_tool` at 2532-2535 (check reset), `stream` at 3176 (turn reset)
- Create: `tests/unit/test_steering.py`

**Interfaces:**
- Produces on `MathematicsSession`:
  - `SAVE_STREAK_LIMIT: int = 3`
  - `_save_streak: dict[str, int]`
  - `_tool_tally: dict[str, list[int]]` — `{"save_lean": [calls, accepted], "check_lean": [calls, passed]}`, never reset
  - `_tally(name: str, ok: bool) -> None`
  - `_streak_refusal(path: str) -> ToolResult | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_steering.py`:

```python
"""Steering: what a session learns from the shape of its own tool use.

A failing run made 53 `save_lean` calls, 21 of them in a row, and none landed.
Each refusal was local; nothing said "stop saving and check".
"""

from __future__ import annotations

UNREGISTERED = "import Mathlib\n\ntheorem Nobody : True := by exact True.intro\n"
GREEN = "import Mathlib\n\nlemma fine : True := by exact True.intro\n"


def _save(session, source=UNREGISTERED, path="Main.lean"):
    return session._dispatch("save_lean", {"source": source, "path": path})


def _check(session, source=GREEN, path="Main.lean"):
    return session._dispatch("check_lean", {"source": source, "path": path})


def test_a_refused_save_is_counted_against_its_path(session) -> None:
    _save(session)

    assert session._save_streak == {"Main.lean": 1}
    assert session._tool_tally["save_lean"] == [1, 0]


def test_the_fourth_consecutive_refused_save_is_braked(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        assert not _save(session).ok

    result = _save(session)

    assert not result.ok
    assert "3 consecutive saves of `Main.lean` have been refused" in result.output
    assert "check_lean" in result.output


def test_the_brake_does_not_climb_the_counter(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT + 2):
        _save(session)

    assert session._save_streak["Main.lean"] == session.SAVE_STREAK_LIMIT


def test_a_green_check_lifts_the_brake(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)
    assert _check(session).ok

    result = _save(session)

    assert "consecutive saves" not in result.output


def test_a_new_turn_clears_the_streak(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    list(session.stream("again"))

    assert session._save_streak == {}


def test_another_path_is_not_braked(session) -> None:
    for _ in range(session.SAVE_STREAK_LIMIT):
        _save(session)

    result = _save(session, path="Other.lean")

    assert "consecutive saves" not in result.output


def test_the_tally_survives_a_turn(session) -> None:
    _save(session)
    list(session.stream("again"))

    assert session._tool_tally["save_lean"] == [1, 0]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_steering.py -v`
Expected: FAIL — `AttributeError: 'MathematicsSession' object has no attribute '_save_streak'`.

- [ ] **Step 3: Implement**

In `src/hardy/chat.py`, class body near `PROBES`:

```python
    # Consecutive refused `save_lean` calls on one path before the next is
    # refused without running Lean. A failing run made 21 in a row.
    SAVE_STREAK_LIMIT = 3
```

In `__init__`, after `self._writes = threading.Lock()`:

```python
        # This session's own tool use, in memory only: it describes behaviour,
        # not the workspace, so it belongs in neither manifest.
        self._save_streak: dict[str, int] = {}
        self._tool_tally: dict[str, list[int]] = {"save_lean": [0, 0], "check_lean": [0, 0]}
```

Add the methods (near `_save_lean`):

```python
    def _tally(self, name: str, ok: bool) -> None:
        if name in self._tool_tally:
            self._tool_tally[name][0] += 1
            self._tool_tally[name][1] += int(ok)

    def _streak_refusal(self, path: str) -> ToolResult | None:
        if self._save_streak.get(path, 0) < self.SAVE_STREAK_LIMIT:
            return None
        return ToolResult(
            False,
            f"{self.SAVE_STREAK_LIMIT} consecutive saves of `{path}` have been refused. "
            "Hardy will not elaborate another until `check_lean` passes on this path. "
            "Check a smaller piece — split the file, or reduce it to what already "
            "compiles — then save.",
        )
```

Rename `_save_lean` to `_save_lean_unbraked` and add a wrapper:

```python
    def _save_lean(self, path: str, source: str) -> ToolResult:
        refusal = self._streak_refusal(path)
        if refusal is not None:
            return refusal
        result = self._save_lean_unbraked(path, source)
        if result.ok:
            self._save_streak.pop(path, None)
        else:
            self._save_streak[path] = self._save_streak.get(path, 0) + 1
        return result
```

In `_tool`, the `check_lean` branch:

```python
        if name == "check_lean":
            path = str(arguments.get("path") or DEFAULT_LEAN_PATH)
            result = self._check_lean(path, str(arguments["source"]))
            if result.ok:
                self._save_streak.pop(path, None)
            return result
```

In `_dispatch`, after `result = self._tool(...)` / the `except` block and before `self._record(...)`:

```python
            self._tally(name, result.ok)
```

In `stream()`, after `self._cancelled.clear()`:

```python
        # A new turn is a new chance; the tally is not reset, the streak is.
        self._save_streak.clear()
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_steering.py tests/test_chat.py tests/test_chat_audit.py tests/test_chat_ratchet.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/unit/test_steering.py
git commit -F - <<'EOF'
Brake a run of refused saves, and count what the session has tried

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 6: `unreached_fragments` in `latex.py`

**Files:**
- Modify: `src/hardy/latex.py` (after `_includes`)
- Test: `tests/test_latex_tree.py`

**Interfaces:**
- Produces: `latex.unreached_fragments(sources: Mapping[str, str]) -> list[str]` — keys are workspace-relative POSIX paths including `writeup.tex`; returns sorted paths not reachable from the root by `\input`/`\include`/`\subfile`, ignoring comments. If the root is absent, returns every path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_latex_tree.py`:

```python
from hardy.latex import unreached_fragments


def test_a_fragment_the_root_inputs_is_reached() -> None:
    sources = {"writeup.tex": "\\input{sections/one}", "sections/one.tex": "x"}

    assert unreached_fragments(sources) == []


def test_a_fragment_nothing_inputs_is_unreached() -> None:
    """The failing run wrote a 90-line `completion_status.tex` no document
    ever pulled in, and it appeared in no PDF."""
    sources = {"writeup.tex": "hello", "completion_status.tex": "done!"}

    assert unreached_fragments(sources) == ["completion_status.tex"]


def test_reachability_is_transitive_and_ignores_comments() -> None:
    sources = {
        "writeup.tex": "\\input{a}\n% \\input{ghost}",
        "a.tex": "\\input{b.tex}",
        "b.tex": "leaf",
        "ghost.tex": "never",
    }

    assert unreached_fragments(sources) == ["ghost.tex"]


def test_without_a_root_everything_is_unreached() -> None:
    assert unreached_fragments({"a.tex": "x"}) == ["a.tex"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_latex_tree.py -k unreached -v`
Expected: FAIL, `ImportError: cannot import name 'unreached_fragments'`.

- [ ] **Step 3: Implement**

In `src/hardy/latex.py`, after `_includes`:

```python
def unreached_fragments(sources: Mapping[str, str]) -> list[str]:
    r"""Writeup files no `\input` chain from the root reaches.

    A fragment nothing includes is in no PDF, whatever it says. A session once
    wrote itself a status report that way and nobody could have read it.
    Follows the same commands `_includes` does, through comments dropped the
    same way, and accepts a path with or without `.tex` and with either
    separator, as TeX does.
    """
    if ROOT_DOCUMENT not in sources:
        return sorted(sources)
    by_stem = {}
    for path in sources:
        normal = path.replace("\\", "/")
        by_stem[normal] = path
        if normal.endswith(".tex"):
            by_stem[normal[: -len(".tex")]] = path
    reached = {ROOT_DOCUMENT}
    frontier = [ROOT_DOCUMENT]
    while frontier:
        current = frontier.pop()
        for found in INCLUSION.findall(uncommented(sources[current])):
            target = by_stem.get(found.strip().replace("\\", "/"))
            if target is not None and target not in reached:
                reached.add(target)
                frontier.append(target)
    return sorted(path for path in sources if path not in reached)
```

Add `from collections.abc import Mapping` to the imports if not present.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_latex_tree.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/latex.py tests/test_latex_tree.py
git commit -F - <<'EOF'
Find the writeup files no input chain reaches

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 7: The steering block, in the turn and in `read_workspace`

**Files:**
- Modify: `src/hardy/chat.py` — new `_unreached_tex()`, `_steering_block()`, `stream()` at 3176-3200, `_workspace_listing` return dict at ~2330
- Test: `tests/unit/test_steering.py`

**Interfaces:**
- Consumes: `latex.unreached_fragments` (Task 6), `_tool_tally` (Task 5), `_obligations`, `_saved_theorems`, `files_under`, `read_text`, `self.tex_root`.
- Produces: `MathematicsSession._steering_block() -> str` (empty string when omitted); transcript event `{"type": "steering", "text": ...}` written immediately before the `user` event; `read_workspace` key `"tex_unreached": list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_steering.py`:

```python
import json


def _events(session):
    path = session.workspace / "transcript.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_fresh_workspace_with_no_tool_calls_gets_no_block(session) -> None:
    assert session._steering_block() == ""


def test_the_block_counts_theorems_assumptions_and_this_session(session) -> None:
    _save(session)

    block = session._steering_block()

    assert block.startswith("[Hardy workspace state — written by Hardy, not the user]")
    assert "saved theorems: 0 machine-checked, 0 open (resting on a hole)" in block
    assert "approved assumptions: 0" in block
    assert "this session: 1 save_lean calls, 0 accepted; 0 check_lean calls, 0 passed" in block


def test_the_block_names_tex_files_nothing_reaches(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "completion_status.tex").write_text("done", encoding="utf-8")

    block = session._steering_block()

    assert "tex files not reached from writeup.tex: completion_status.tex" in block


def test_the_block_line_is_omitted_when_every_tex_file_is_reached(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")

    assert "tex files not reached" not in session._steering_block()


def test_the_block_precedes_the_user_text_in_the_transcript(session) -> None:
    _save(session)

    list(session.stream("carry on"))

    kinds = [event["type"] for event in _events(session)]
    steering = kinds.index("steering")
    assert kinds[steering + 1] == "user"
    assert _events(session)[steering + 1]["message"]["content"] == "carry on"


def test_the_block_reaches_the_runtime_ahead_of_the_user_text(session_factory) -> None:
    seen = []

    class Runtime:
        model = "fake"

        def stream(self, text):
            seen.append(text)
            return iter(())

        def cancel(self):
            pass

    session = session_factory()
    session._make_runtime = lambda model=None, **context: Runtime()
    session.runtime = Runtime()
    _save(session)

    list(session.stream("carry on"))

    assert seen[-1].startswith("[Hardy workspace state")
    assert seen[-1].endswith("\n\ncarry on")


def test_read_workspace_lists_unreached_tex(session) -> None:
    (session.tex_root).mkdir(parents=True, exist_ok=True)
    (session.tex_root / "writeup.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
    (session.tex_root / "orphan.tex").write_text("x", encoding="utf-8")

    listing = json.loads(session._dispatch("read_workspace", {}).output)

    assert listing["tex_unreached"] == ["orphan.tex"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_steering.py -k "block or unreached" -v`
Expected: FAIL, `AttributeError: ... _steering_block`.

- [ ] **Step 3: Implement**

In `src/hardy/chat.py`, import `unreached_fragments` from `.latex` beside the existing latex imports. Add to the class:

```python
    def _unreached_tex(self) -> list[str]:
        """Writeup files no `\\input` chain from the root reaches."""
        if not self.tex_root.is_dir():
            return []
        try:
            paths = [relative.as_posix() for relative in files_under(self.tex_root, ".tex")]
            sources = {path: read_text(self.tex_root, path) for path in paths}
        except (OSError, WorkspacePathError):
            return []
        return unreached_fragments(sources)

    def _steering_block(self) -> str:
        """What the workspace and this session amount to, for the model.

        The end-of-turn notice tells the *user* that nothing is saved. A
        failing run was told eight times; the model saw none of them, and
        wrote itself a status report saying the work was done. This is the
        same arithmetic, put where the model reads, and nothing it wrote.
        """
        calls = self._tool_tally
        no_tools = all(count[0] == 0 for count in calls.values())
        if no_tools and not self.lean_workspace.sources() and not self.tex_root.is_dir():
            return ""
        owed = self._obligations()
        gaps = {item.subject for item in owed if item.kind == "lean"}
        opened = {item.subject for item in owed if item.kind == "open"}
        saved = self._saved_theorems()
        lines = [
            "[Hardy workspace state — written by Hardy, not the user]",
            f"saved theorems: {len(saved - gaps - opened)} machine-checked, "
            f"{len(saved & opened)} open (resting on a hole)",
            f"approved assumptions: {len(self.state['assumptions'])}",
            f"this session: {calls['save_lean'][0]} save_lean calls, "
            f"{calls['save_lean'][1]} accepted; {calls['check_lean'][0]} check_lean calls, "
            f"{calls['check_lean'][1]} passed",
        ]
        unreached = self._unreached_tex()
        if unreached:
            lines.append(f"tex files not reached from writeup.tex: {', '.join(unreached)}")
        return "\n".join(lines)
```

In `stream()`, replace the first `_record` line and the final `return`:

```python
        block = self._steering_block()
        if block:
            self._record({"type": "steering", "text": block})
        self._record({"type": "user", "message": {"role": "user", "content": text}})
        ...
        return self._stream(self.runtime.stream(f"{block}\n\n{text}" if block else text))
```

In `_workspace_listing`'s returned dict, after `"tex": tex,`:

```python
            # Files no `\input` chain from the root reaches: in no PDF,
            # whatever they say.
            "tex_unreached": self._unreached_tex(),
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_steering.py tests/test_chat.py tests/test_chat_workspace.py tests/test_chat_usage.py -q`
Expected: all PASS. If a usage/ledger test asserts on transcript offsets or event counts, the new `steering` event shifts them only when a block is emitted — a fresh workspace with no tool calls emits none, so existing single-turn tests should be unaffected; fix any that script tool calls then a second `stream` by accounting for the extra event.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/unit/test_steering.py
git commit -F - <<'EOF'
Put the workspace's own arithmetic where the model reads it

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 8: `request_assumption` requires a search since the last request

**Files:**
- Modify: `src/hardy/chat.py` — `__init__` (fields), `_search_tool` at ~3120 (tracking), `request_assumption` dispatch at ~2559 (gate + `searched`)
- Create: `tests/unit/test_assumption_evidence.py`

**Interfaces:**
- Produces: `_inspected: list[tuple[str, bool]]`, `_inspected_since_request: bool`, proposal key `searched: list[str]` formatted `"Name ✓"` / `"Name ✗"`. Gate applies only when `self.search is not None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_assumption_evidence.py`:

```python
"""What `request_assumption` shows the human, beyond the statement.

Three axioms were approved on a failing run with the reason "Mathlib does not
expose this". Nothing had been searched for; the reason was free text.
"""

from __future__ import annotations

import json

from hardy.models import ToolResult


class Search:
    """An `inspect_declarations` that answers with a scripted result."""

    def __init__(self, resolved=(), unavailable=(), ok=True):
        self.answer = ToolResult(
            ok,
            json.dumps({
                "resolved": [{"name": name} for name in resolved],
                "unavailable": list(unavailable),
            }),
        )

    def inspect_declarations(self, names):
        return self.answer

    def rank_premises(self, goal, limit=10):
        return ToolResult(True, "{}")

    def search_declarations(self, query, limit=10):
        return ToolResult(True, "{}")

    def search_modules(self, query, limit=20):
        return ToolResult(True, "{}")


def _request(**overrides):
    request = {
        "formal_name": "sylow",
        "lean_statement": "True",
        "latex_name": "Sylow",
        "informal_statement": "Sylow's theorems",
        "source": "Dummit and Foote",
        "reason": "not in Mathlib",
    }
    request.update(overrides)
    return request


def _searching_session(session_factory, approvals, search):
    def confirm(proposal):
        approvals.append(dict(proposal))
        return True

    return session_factory(confirm=confirm, search=search, search_detail="fake")


def test_a_request_with_no_search_behind_it_is_refused(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, Search())

    result = session._tool("request_assumption", _request())

    assert not result.ok
    assert "no `inspect_declarations` has been run" in result.output
    assert approvals == []


def test_a_completed_inspection_unlocks_one_request(session_factory, approvals, fake_lean) -> None:
    session = _searching_session(session_factory, approvals, Search(unavailable=("Sylwo",)))
    session._tool("inspect_declarations", {"names": ["Sylwo"]})

    first = session._tool("request_assumption", _request())
    second = session._tool("request_assumption", _request(formal_name="other", latex_name="o"))

    assert first.ok
    assert not second.ok


def test_the_human_sees_what_was_searched(session_factory, approvals, fake_lean) -> None:
    session = _searching_session(
        session_factory, approvals, Search(resolved=("IsCyclic",), unavailable=("Sylwo",))
    )
    session._tool("inspect_declarations", {"names": ["IsCyclic", "Sylwo"]})

    session._tool("request_assumption", _request())

    assert approvals[0]["searched"] == ["IsCyclic ✓", "Sylwo ✗"]


def test_a_stopped_inspection_does_not_count(session_factory, approvals) -> None:
    session = _searching_session(session_factory, approvals, Search(unavailable=("X",), ok=False))
    session._tool("inspect_declarations", {"names": ["X"]})

    result = session._tool("request_assumption", _request())

    assert not result.ok


def test_a_session_that_cannot_search_is_not_asked_to(session, approvals, fake_lean) -> None:
    """`search=None` is how every existing fixture is built."""
    result = session._tool("request_assumption", _request())

    assert result.ok
```

Note: `fake_lean` is requested in tests where the request reaches the probe, so no real Lean runs. The `session` fixture's `fake_lean` patches `_run_lean_source` on *that* session; for `_searching_session` tests the probe will run the fake Lean script via `_run_lean_source` unpatched — `axiom sylow : True` against `tests/fake_lean.py` returns "type mismatch" on every probe line, which the probe reads as "nothing closed it", so the request passes. Verify this in Step 4; if the fake script's answer does not place errors on the probe lines, monkeypatch `_run_lean_source` in `_searching_session` with the same `Fake` class the `fake_lean` fixture uses.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_assumption_evidence.py -v`
Expected: the refusal tests FAIL (request currently succeeds); `searched` test FAILS with KeyError.

- [ ] **Step 3: Implement**

In `__init__`, beside `_save_streak`:

```python
        # Every name a *completed* `inspect_declarations` batch asked about
        # this session, and whether one has run since the last axiom request.
        self._inspected: list[tuple[str, bool]] = []
        self._inspected_since_request = False
        self._searched_since_request: list[str] = []
```

In `_search_tool`, replace the final `return self.search.inspect_declarations(...)`:

```python
        result = self.search.inspect_declarations([str(item) for item in names])
        if result.ok:
            self._note_inspected([str(item) for item in names], result.output)
        return result
```

Add:

```python
    def _note_inspected(self, names: list[str], output: str) -> None:
        """Remember what a completed inspection asked, and what it found."""
        resolved: set[str] = set()
        try:
            payload = json.loads(output[output.index("{"):])
            resolved = {item["name"] for item in payload.get("resolved", [])}
        except (ValueError, KeyError, TypeError):
            pass
        for name in names:
            self._inspected.append((name, name in resolved))
            self._searched_since_request.append(f"{name} {'✓' if name in resolved else '✗'}")
        self._inspected_since_request = True
```

In the `request_assumption` dispatch, immediately after `proposal = {...}` and before `_assumption_shape`:

```python
            if self.search is not None and not self._inspected_since_request:
                return ToolResult(
                    False,
                    "no `inspect_declarations` has been run since the last assumption "
                    "request. Look for the result before assuming it: pass several "
                    "candidate spellings and let Lean say which exist.",
                )
```

Before `if not self.confirm(proposal):`:

```python
            proposal["searched"] = list(self._searched_since_request)
```

After the `confirm` call returns (both branches — put it right after `proposal["searched"] = ...` and before `confirm`, since the request is "spent" whether approved or not):

```python
            self._inspected_since_request = False
            self._searched_since_request = []
```

Add `"searched"` to the set of keys stripped from the durable record: `if key not in {"checked", "goal", "searched"}`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_assumption_evidence.py tests/unit/test_assumption_gates.py tests/test_chat.py tests/test_chat_audit.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/unit/test_assumption_evidence.py
git commit -F - <<'EOF'
Refuse an axiom request nothing was searched for, and show the human the search

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 9: The revision diff on a resubmitted name

**Files:**
- Modify: `src/hardy/chat.py` — `__init__`, `request_assumption` dispatch
- Test: `tests/unit/test_assumption_evidence.py`

**Interfaces:**
- Produces: `_rejected: dict[str, list[str]]`; proposal key `previous: str` when present.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_assumption_evidence.py`:

```python
def test_a_resubmitted_name_shows_the_human_the_previous_statement(
    session_factory, approvals, fake_lean
) -> None:
    """`sylow_unique_normal` lost its `Fintype.card P = p` conjunct between
    a refused request and an approved one, and nobody saw the change."""
    declined = []

    def confirm(proposal):
        declined.append(dict(proposal))
        return len(declined) > 1

    session = session_factory(confirm=confirm)
    session._tool("request_assumption", _request(lean_statement="True ∧ True"))

    session._tool("request_assumption", _request(lean_statement="True"))

    assert declined[1]["previous"] == "True ∧ True"
    assert "previous" not in declined[0]


def test_a_gate_refused_statement_is_also_remembered(session, approvals) -> None:
    session._tool("request_assumption", _request(lean_statement="axiom f : True"))

    assert session._rejected["sylow"] == ["axiom f : True"]


def test_previous_is_not_written_into_the_durable_record(session_factory, fake_lean) -> None:
    answers = iter([False, True])
    session = session_factory(confirm=lambda proposal: next(answers))
    session._tool("request_assumption", _request(lean_statement="True ∧ True"))
    session._tool("request_assumption", _request(lean_statement="True"))

    assert "previous" not in session.state["assumptions"][0]
```

Note: `session_factory` sessions have `search=None`, so Task 8's gate does not apply.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_assumption_evidence.py -k "previous or remembered" -v`
Expected: FAIL, KeyError `'previous'` / AttributeError `_rejected`.

- [ ] **Step 3: Implement**

In `__init__`:

```python
        # Prior statements this session requested under each name and did not
        # get approved, so a human sees a statement beside what it was
        # weakened from.
        self._rejected: dict[str, list[str]] = {}
```

In the `request_assumption` dispatch, wrap the existing gate returns. Restructure the branch as:

```python
        if name == "request_assumption":
            proposal = {key: str(arguments[key]) for key in (...)}
            result = self._request_assumption(proposal)
            if not result.ok:
                self._rejected.setdefault(proposal["formal_name"], []).append(
                    proposal["lean_statement"]
                )
            return result
```

and move the existing body into a new method `_request_assumption(self, proposal: dict[str, str]) -> ToolResult` unchanged, except that before `confirm`:

```python
        earlier = self._rejected.get(proposal["formal_name"])
        if earlier:
            proposal["previous"] = earlier[-1]
```

and `"previous"` joins the stripped keys: `{"checked", "goal", "searched", "previous"}`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_assumption_evidence.py tests/unit/test_assumption_gates.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/unit/test_assumption_evidence.py
git commit -F - <<'EOF'
Show the human what a resubmitted assumption was changed from

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 10: `_strip_hypotheses`

**Files:**
- Modify: `src/hardy/chat.py` (module-level function, beside `_probe_suggestion` at line 269)
- Test: `tests/unit/test_assumption_gates.py`

**Interfaces:**
- Produces: `chat._strip_hypotheses(statement: str) -> str | None`. Returns the statement with every Prop-hypothesis binder and every arrow premise removed; `None` when the statement has no `∀`/`forall` binder prefix and no top-level `→`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_assumption_gates.py` under a new heading `# --- Vacuity ---`:

```python
import importlib

_chat = importlib.import_module("hardy.chat")

SYLOW = (
    "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ) (hprime : Nat.Prime p) "
    "(h_order : p ∣ Fintype.card G), ∃ P : Subgroup G, P.Normal"
)


def test_stripping_drops_prop_hypotheses_and_keeps_data() -> None:
    assert _chat._strip_hypotheses(SYLOW) == (
        "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ), ∃ P : Subgroup G, P.Normal"
    )


def test_stripping_drops_arrow_premises() -> None:
    assert _chat._strip_hypotheses("∀ (n : ℕ), 0 < n → n ≠ 0") == "∀ (n : ℕ), n ≠ 0"


def test_a_binder_typed_by_an_earlier_binder_is_data() -> None:
    assert _chat._strip_hypotheses("∀ (G : Type*) (g : G), g = g") == "∀ (G : Type*) (g : G), g = g"


def test_a_statement_with_nothing_to_strip_is_none() -> None:
    assert _chat._strip_hypotheses("True") is None
    assert _chat._strip_hypotheses(TRIVIAL) is None


def test_a_forall_that_loses_every_binder_becomes_its_body() -> None:
    assert _chat._strip_hypotheses("∀ (h : True), False") == "False"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_assumption_gates.py -k strip -v`
Expected: FAIL, `AttributeError: module 'hardy.chat' has no attribute '_strip_hypotheses'`.

- [ ] **Step 3: Implement**

Module-level in `src/hardy/chat.py`, after `_probe_suggestion`:

```python
_BINDER = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|\((?:[^()]|\([^()]*\))*\)")
_DATA_TYPE = re.compile(r"^(?:Type|Sort|Prop)\b|^(?:ℕ|ℤ|ℚ|ℝ|ℂ|Nat|Int|Rat|Real|Complex|Bool|String)$")


def _split_top(text: str, separator: str) -> list[str]:
    """`text` split on `separator` outside every bracket."""
    parts, depth, start = [], 0, 0
    index = 0
    while index < len(text):
        character = text[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _strip_hypotheses(statement: str) -> str | None:
    """`statement` with its hypotheses removed, or None if it has none.

    What the vacuity probe elaborates. A binder is a hypothesis when its type
    is neither a universe, a known data type, nor a name bound earlier in the
    same statement; an arrow premise always is. Best effort: a statement this
    cannot read is probed only as the whole it was given.
    """
    text = " ".join(statement.split())
    binders, body = "", text
    for keyword in ("∀ ", "forall "):
        if text.startswith(keyword):
            head, comma, rest = text[len(keyword):], "", ""
            parts = _split_top(head, ", ")
            if len(parts) < 2:
                return None
            binders, body = parts[0], ", ".join(parts[1:])
            break
    kept, bound = [], set()
    for group in _BINDER.findall(binders):
        inner = group[1:-1]
        names, colon, typ = inner.partition(" : ")
        typ = typ.strip()
        if group[0] == "[" or not colon or _DATA_TYPE.match(typ) or typ in bound:
            kept.append(group)
            bound.update(names.split())
            continue
        # A hypothesis; dropped.
    premises = _split_top(body, " → ")
    conclusion = premises[-1].strip()
    if not binders and len(premises) == 1:
        return None
    if kept:
        return f"∀ {' '.join(kept)}, {conclusion}"
    return conclusion
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_assumption_gates.py -k strip -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/chat.py tests/unit/test_assumption_gates.py
git commit -F - <<'EOF'
Read a statement's hypotheses off it, for the vacuity probe

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 11: `_vacuity_probe` and the warning to the human

**Files:**
- Modify: `src/hardy/chat.py` — new method beside `_assumption_probe`; `_request_assumption` after the probe
- Modify: `tests/unit/conftest.py` — `fake_lean` fixture answers by counting `example` lines
- Test: `tests/unit/test_assumption_gates.py`

**Interfaces:**
- Consumes: `_strip_hypotheses` (Task 10), `_run_lean_source`, `PROBES`.
- Produces: `MathematicsSession.WITNESSES: tuple[str, ...]`, `_vacuity_probe(statement: str) -> str` returning the warning text or `""`; proposal `checked` carries the warning.

- [ ] **Step 1: Make the fixture layout-aware**

In `tests/unit/conftest.py`, replace the body of `Fake.__call__` so it no longer hard-codes `declaration_line`:

```python
        def __call__(self, source: str, timeout: float | None = None):
            self.last_source = source
            self.last_timeout = timeout
            self.sources.append(source)
            if self.raises is not None:
                raise self.raises
            diagnostics = []
            lines = source.splitlines()
            # Whatever the layout: an `example` line closes iff its tactic
            # is `closes_with`; a declaration line fails iff `elaborates`
            # is False. Both probe files put one example per line from 3.
            for number, line in enumerate(lines, start=1):
                if line.startswith("example"):
                    tactic = line.split(" := by ", 1)[1] if " := by " in line else line.split(" := ", 1)[1]
                    if tactic == self.closes_with:
                        if self.suggestion:
                            diagnostics.append(LeanDiagnostic(
                                severity="information", message=f"Try this: {self.suggestion}",
                                line=number, column=0,
                            ))
                        continue
                    diagnostics.append(LeanDiagnostic(
                        severity="error", message="unsolved goals", line=number, column=0
                    ))
                elif line.startswith("axiom") and not self.elaborates:
                    diagnostics.append(LeanDiagnostic(
                        severity="error", message=self.output, line=number, column=0
                    ))
            return LeanToolResult(not diagnostics, self.output, source, diagnostics=tuple(diagnostics))
```

and add `sources: list[str]` initialised in the class body as `sources = []` — set it per instance: in `fake = Fake()` add `fake.sources = []`.

Run: `uv run pytest tests/unit/test_assumption_gates.py -q` — Expected: all existing tests still PASS (the fixture answers the same for the existing layout).

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_assumption_gates.py`:

```python
def test_the_vacuity_probe_is_skipped_when_there_is_nothing_to_strip(session, fake_lean) -> None:
    warning = session._vacuity_probe("True")

    assert warning == ""
    assert fake_lean.sources == []


def test_the_vacuity_file_has_no_declaration_and_one_example_per_line(session, fake_lean) -> None:
    session._vacuity_probe(SYLOW)

    lines = fake_lean.last_source.splitlines()
    assert lines[0] == "import Mathlib"
    assert lines[1] == ""
    assert all(line.startswith("example : ") for line in lines[2:])
    assert not any(line.startswith("axiom") for line in lines)


def test_a_stripped_statement_a_probe_closes_is_a_warning(session, fake_lean) -> None:
    fake_lean.closes_with = "aesop"

    warning = session._vacuity_probe(SYLOW)

    assert "with every hypothesis removed" in warning
    assert "by aesop" in warning


def test_an_existential_is_tried_with_bottom_and_top(session, fake_lean) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    warning = session._vacuity_probe(SYLOW)

    assert "exact ⟨⊥, inferInstance⟩" in warning


def test_a_non_existential_gets_no_witness_lines(session, fake_lean) -> None:
    session._vacuity_probe("∀ (n : ℕ), 0 < n → n ≠ 0")

    assert "⟨⊥" not in fake_lean.last_source


def test_a_stripped_statement_nothing_closes_is_silent(session, fake_lean) -> None:
    assert session._vacuity_probe(SYLOW) == ""


def test_a_vacuity_probe_that_cannot_run_says_so(session, fake_lean) -> None:
    fake_lean.raises = TimeoutError("lean did not start")

    warning = session._vacuity_probe(SYLOW)

    assert "could not be run" in warning


def test_the_vacuity_warning_reaches_the_human(session, approvals, fake_lean) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert "may be vacuous" in approvals[0]["checked"]
    assert approvals  # warned, not refused


def test_a_whole_statement_close_is_still_a_refusal(session, approvals, fake_lean) -> None:
    fake_lean.closes_with = "simp"

    result = session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert not result.ok
    assert approvals == []
```

Note on the third test: with `closes_with = "aesop"`, the *first* probe (whole statement) also closes under this fixture, which `_assumption_probe` would refuse — but `_vacuity_probe` is being called directly here, so only the second file is elaborated. The last test covers the ordering through `_tool`.

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/unit/test_assumption_gates.py -k vacuity -v`
Expected: FAIL, `AttributeError: ... _vacuity_probe`.

- [ ] **Step 4: Implement**

In the class, after `PROBES`:

```python
    # Tried on the stripped statement when its conclusion is an existential.
    # `exact?` and `aesop` do not synthesise a witness, and the bad axiom the
    # failing run approved -- `∃ P : Subgroup G, P.Normal` -- is closed by the
    # first of these.
    WITNESSES = (
        "exact ⟨⊥, inferInstance⟩",
        "exact ⟨⊤, inferInstance⟩",
        "exact ⟨⊥, by simp⟩",
        "exact ⟨⊤, by simp⟩",
        "exact ⟨1, by simp⟩",
    )
```

After `_assumption_probe`:

```python
    def _vacuity_probe(self, statement: str) -> str:
        """Whether the conclusion holds with the hypotheses gone. A warning or "".

        Run only after `_assumption_probe` returned no refusal, as its own
        elaboration: nothing here needs the axiom in scope, and the first
        file's layout is pinned by its tests. A statement the stripper cannot
        read is not probed, and says so.
        """
        stripped = _strip_hypotheses(normalise_lean(statement).strip())
        if stripped is None:
            return ""
        tactics = list(self.PROBES)
        if stripped.split(", ")[-1].lstrip().startswith("∃"):
            tactics.extend(self.WITNESSES)
        examples = "\n".join(f"example : {stripped} := by {tactic}" for tactic in tactics)
        source = f"import Mathlib\n\n{examples}\n"
        try:
            result = self._run_lean_source(source, timeout=max(self.lean.timeout, PROBE_SECONDS))
        except Exception as error:  # noqa: BLE001 - a warning that cannot be computed is itself reported
            return f"The vacuity probe could not be run ({error})."
        if getattr(result, "timed_out", False) or getattr(result, "interrupted", False):
            return "The vacuity probe could not be run (the elaboration did not finish)."
        errors = [item for item in result.diagnostics if item.severity == "error"]
        if not result.ok and not errors:
            return "The vacuity probe could not be run (Lean failed without diagnostics)."
        if any(item.line is None for item in errors):
            return ""
        placed = {item.line for item in errors}
        for index, tactic in enumerate(tactics):
            line = 3 + index
            if line in placed:
                continue
            proof = _probe_suggestion(result, line) or f"by {tactic}"
            return (
                "Lean elaborated this statement and could not prove it as stated — but "
                f"proves it with every hypothesis removed (`{proof}`): the conclusion "
                f"`{stripped}` holds without the hypotheses. This assumption may be vacuous."
            )
        return ""
```

`WITNESSES` entries begin with `exact`, so the `by {tactic}` rendering yields `by exact ⟨⊥, inferInstance⟩`, which is valid Lean and matches the fixture's `split(" := by ")`.

In `_request_assumption`, after the existing probe refusal and before `proposal["checked"] = ...`:

```python
        warning = self._vacuity_probe(proposal["lean_statement"])
        proposal["checked"] = (
            caveat
            or warning
            or "Lean elaborated this statement and could not prove it."
        )
```

(replacing the existing `proposal["checked"] = caveat or "..."` line).

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/unit/test_assumption_gates.py tests/unit/test_assumption_evidence.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/chat.py tests/unit/conftest.py tests/unit/test_assumption_gates.py
git commit -F - <<'EOF'
Ask Lean whether an assumption's conclusion holds with its hypotheses gone

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 12: Real-Lean check of the vacuity probe

**Files:**
- Test: `tests/integration/test_lean_real.py`

No integration test builds a `MathematicsSession`; the module builds a
`LeanService` through `_service(_environment())` and skips when `lake` or the
pinned project is absent. The test below builds the same file `_vacuity_probe`
builds and reads it the same way, through the service.

- [ ] **Step 1: Write the tests**

Append to `tests/integration/test_lean_real.py`:

```python
BAD_SYLOW = (
    "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ) (hprime : Nat.Prime p) "
    "(h_order : p ∣ Fintype.card G), ∃ P : Subgroup G, P.Normal"
)
REAL_SYLOW = (
    "∀ {G : Type*} [Group G] [Finite G] (p : ℕ) [Fact p.Prime] (P : Sylow p G), "
    "(∀ Q : Sylow p G, Q = P) → (P : Subgroup G).Normal"
)


def _closed_by(statement: str) -> list[str]:
    """Which vacuity tactics close `statement` stripped, read as `_vacuity_probe` reads them."""
    from hardy.chat import MathematicsSession, _strip_hypotheses

    stripped = _strip_hypotheses(statement)
    assert stripped is not None
    tactics = list(MathematicsSession.PROBES) + list(MathematicsSession.WITNESSES)
    source = "import Mathlib\n\n" + "\n".join(
        f"example : {stripped} := by {tactic}" for tactic in tactics
    ) + "\n"
    check = _service(_environment())._check_source(source)
    errored = {item.line for item in check.diagnostics if item.severity == "error"}
    return [tactic for index, tactic in enumerate(tactics) if 3 + index not in errored]


def test_the_failing_runs_approved_axiom_is_closed_by_a_witness() -> None:
    """`sylow_unique_normal` as approved: its conclusion is `∃ P, P.Normal`."""
    assert "exact ⟨⊥, inferInstance⟩" in _closed_by(BAD_SYLOW)


def test_a_genuine_sylow_statement_is_closed_by_nothing() -> None:
    assert _closed_by(REAL_SYLOW) == []
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/integration/test_lean_real.py -k "closed_by or witness or nothing" -v`
Expected: PASS on a machine with the pinned toolchain; SKIP otherwise. Report which. If the first test fails because `⊥`'s normality needs a name rather than `inferInstance` in the pinned Mathlib, add `"exact ⟨⊥, Subgroup.bot_normal⟩"` to `WITNESSES` in `chat.py` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_lean_real.py src/hardy/chat.py
git commit -F - <<'EOF'
Check the vacuity probe against the axiom a live run approved

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```

---

### Task 13: Full suite, docs cross-check, and the Codex gates

- [ ] **Step 1: Run the whole hermetic suite**

Run: `uv run pytest -q --ignore=tests/integration`
Expected: all PASS.

- [ ] **Step 2: Check the prompt and docs still agree with the tools**

Run: `uv run pytest tests/unit/test_architecture_doc.py tests/unit/test_chat_wiring.py -q`
Expected: PASS. If `ARCHITECTURE.html` or `FEATURES.md` enumerates transcript event types or `read_workspace` keys, add `steering` and `tex_unreached` there in the same style as the neighbouring entries, and re-run.

- [ ] **Step 3: Run the review gates before handing back**

Per memory `codex-gates-before-user-review`: ask the user to run `/codex:adversarial-review` and `/codex:review` on the branch; resolve findings; then ask for review.

- [ ] **Step 4: Commit any doc adjustments**

```bash
git add -A
git commit -F - <<'EOF'
Name the steering event and the unreached-tex key where the docs list the others

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016DkxoXoV96DDGgCb9jGLjq
EOF
```
