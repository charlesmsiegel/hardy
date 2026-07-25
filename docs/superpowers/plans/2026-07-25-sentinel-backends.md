# Sentinel CAS Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Singular and Macaulay2 backends verified code rather than plausible code, and give the repository a way to keep them that way.

**Architecture:** The length-framed SymPy path is implemented and covered. The sentinel-framed path — every line of `_SentinelBackend`, `SingularBackend`, `Macaulay2Backend`, and the `framing == "sentinel"` branches of `CasSession` — has never executed under test, because `tests/fake_cas.py` speaks only the length protocol and neither binary exists on the development machine. This plan closes that in two independent halves: hermetic coverage of the *protocol*, which needs no binaries and is where the predictable bugs are, and a Linux CI job that runs the real binaries, which is the only place the *adapters* can be honestly verified.

**Tech Stack:** Python 3.11+, pytest, uv, GitHub Actions, Singular 4.x (apt), Macaulay2 (apt).

## Global Constraints

- Hardy must never require WSL. Macaulay2 has no native Windows build; Singular reaches Windows only through Cygwin. Neither may become a requirement for running Hardy or its hermetic suite.
- The hermetic suite must pass with no CAS binary installed. Tests needing a real binary carry `@pytest.mark.real_toolchain` and skip when absent.
- Ruff: `target-version = "py311"`, `line-length = 100`, `select = ["E", "F", "I", "UP", "B", "SIM"]`, `ignore = ["E501", "UP042"]`. Run `uvx ruff check src tests` before every commit.
- Any string literal over 200 characters must live in `src/hardy/prompts/` as a `.md.j2` template, not in Python. Enforced by `tests/unit/test_prompts.py::test_no_prompt_text_is_left_behind_in_the_code`.
- The cell log is append-only and single-schema: every line is a `CellRecord`. Resets increment `segment`; nothing is ever deleted or rewritten.
- No CAS result may influence a formalization grade. `verifier.py` must not gain a CAS import.
- Never describe generated Lean, TeX, or CAS cells as safe (`AGENTS.md:21-23`).

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/fake_sentinel_cas.py` | **Create.** A stand-in interpreter speaking the sentinel protocol: reads lines, echoes a prompt, emits the marker, can produce errors, floods, silence, and inter-cell noise. |
| `tests/unit/conftest.py` | **Modify.** Add a `sentinel_session` fixture beside the existing `cas_session`. |
| `tests/unit/test_cas_sentinel.py` | **Create.** Hermetic coverage of the sentinel framing path. |
| `src/hardy/cas.py` | **Modify.** Fix the residue-between-cells defect; make error classification testable. |
| `tests/unit/test_cas_classify.py` | **Create.** Error-banner classification against recorded real output. |
| `tests/fixtures/cas/` | **Create.** Recorded Singular and Macaulay2 banner samples. |
| `.github/workflows/cas-backends.yml` | **Create.** The repository's first CI workflow: Linux, real binaries, `real_toolchain` tests. |
| `FEATURES.md` | **Modify.** Record what CI now covers; correct the acceptance-run status. |

---

### Task 1: A fake sentinel interpreter, and hermetic coverage of the framing path

The sentinel path has no coverage at all. This task gives it a real child process to talk to, the way `fake_cas.py` does for the length path.

**Files:**
- Create: `tests/fake_sentinel_cas.py`
- Modify: `tests/unit/conftest.py`
- Test: `tests/unit/test_cas_sentinel.py`

**Interfaces:**
- Consumes: `hardy.cas.CasSession`, `hardy.cas._SentinelBackend`, `hardy.domain.RunLimits`.
- Produces: `tests/unit/conftest.py::sentinel_session` — a pytest fixture returning `make(**limits) -> CasSession` whose backend has `framing == "sentinel"`, `script_suffix == ".fake"`, `comment == "//"`, `preamble == ""`, `language == "fake"`, `kernel_name == "fake"`.

- [ ] **Step 1: Write the fake sentinel interpreter**

Create `tests/fake_sentinel_cas.py`:

```python
#!/usr/bin/env python3
"""A stand-in interpreter framed by an echoed marker, not by byte counts.

Singular and Macaulay2 are line-oriented interpreters that cannot be spoken to
in frames, so Hardy asks them to echo a per-cell nonce and reads until it
appears. That path has different failure modes from the driver protocol -- an
unterminated statement swallows the echo, a prompt arrives between cells -- and
this reproduces them without needing either binary.
"""

import sys

PROMPT = "fake> "


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 256 * 1024
    pending = ""
    for line in sys.stdin:
        line = line.rstrip("\n")
        # An unterminated statement means the interpreter is still waiting, so
        # the marker line that follows is swallowed as part of it -- exactly
        # what a missing semicolon does in Singular.
        if pending or (line and not line.endswith((";", "»"))):
            pending += line
            if not line.endswith(";"):
                continue
            line, pending = pending, ""
        if "«hardy-end:" in line:
            sys.stdout.write(line.split('"')[1] if '"' in line else line)
            sys.stdout.write("\n" + PROMPT)
            sys.stdout.flush()
            continue
        if line.startswith("error"):
            sys.stdout.write("   ? this is an error\n")
        elif line.startswith("flood"):
            sys.stdout.write("z" * min(400_000, limit * 4) + "\n")
        elif line.startswith("silent"):
            pass
        else:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the fixture**

Append to `tests/unit/conftest.py`:

```python
from hardy.cas import _SentinelBackend

FAKE_SENTINEL = Path(__file__).parents[1] / "fake_sentinel_cas.py"


class FakeSentinelBackend(_SentinelBackend):
    """Sentinel framing against a scripted line-oriented interpreter."""

    name = "singular"          # a real backend name, so config paths accept it
    script_suffix = ".fake"
    language = "fake"
    kernel_name = "fake"
    comment = "//"
    preamble = ""
    version_source = "version;"
    echo = 'print("{marker}");'
    error_pattern = re.compile(r"(?m)^\s{0,3}\? ")

    def argv(self, command, max_output_bytes: int = 256 * 1024) -> tuple[str, ...]:
        return (sys.executable, "-u", str(FAKE_SENTINEL), str(max_output_bytes))


@pytest.fixture
def sentinel_session(tmp_path):
    sessions: list[CasSession] = []

    def make(**limits) -> CasSession:
        session = CasSession(
            backend=FakeSentinelBackend(),
            command=None,
            log_path=tmp_path / "cells.jsonl",
            limits=RunLimits(**limits),
            cwd=tmp_path,
        )
        sessions.append(session)
        return session

    yield make
    for session in sessions:
        session.close()
```

Add `import re` to the conftest imports.

- [ ] **Step 3: Write the failing tests**

Create `tests/unit/test_cas_sentinel.py`:

```python
"""The sentinel framing path: everything Singular and Macaulay2 rely on."""

from __future__ import annotations

import pytest

from hardy.cas import CasError


def test_a_cell_is_answered_and_the_marker_is_not_in_the_output(sentinel_session) -> None:
    session = sentinel_session()
    record = session.execute("hello;")
    assert record.status == "ok"
    assert "hello" in record.stdout
    assert "hardy-end" not in record.stdout


def test_an_error_banner_is_classified_as_an_error(sentinel_session) -> None:
    session = sentinel_session()
    record = session.execute("error;")
    assert record.status == "error"
    assert record.accepted is False


def test_state_is_not_polluted_by_the_previous_cells_prompt(sentinel_session) -> None:
    """A line-oriented interpreter prints a prompt after every cell.

    It arrives after the marker, so it belongs to no cell. If it leaks into the
    next cell's buffer, every recorded output is wrong by one prompt and the
    export cannot reproduce.
    """
    session = sentinel_session()
    session.execute("first;")
    second = session.execute("second;")
    assert "fake>" not in second.stdout
    assert second.stdout.strip() == "second;"


def test_a_cell_that_swallows_the_marker_is_a_timeout_not_a_wrong_answer(
    sentinel_session,
) -> None:
    """An unterminated statement consumes the echo line as its own input."""
    session = sentinel_session(cas_cell_seconds=2)
    record = session.execute("unterminated")
    assert record.status == "timeout"
    assert record.accepted is False


def test_output_larger_than_the_cap_still_returns_an_answer(sentinel_session) -> None:
    """Scanning continues past the retention cap, or a big answer reads as death."""
    session = sentinel_session(cas_output_bytes=4_096, cas_cell_seconds=10)
    record = session.execute("flood;")
    assert record.status == "ok"
    assert record.capture_truncated is True
    assert len(record.stdout) <= 8_192


def test_a_silent_cell_still_completes(sentinel_session) -> None:
    session = sentinel_session()
    assert session.execute("silent;").status == "ok"
```

- [ ] **Step 4: Run them and watch which fail**

Run: `uv run --extra test pytest tests/unit/test_cas_sentinel.py -v`

Expected: `test_state_is_not_polluted_by_the_previous_cells_prompt` FAILS — the prompt written after the marker is still in the pipe when the next cell clears the buffer, and races into it. The others should pass; if more fail, they are real defects and are fixed in Step 5.

- [ ] **Step 5: Fix residue between cells**

In `src/hardy/cas.py`, `_Kernel`, replace wholesale clearing with consuming the frame. Add to `_Kernel`:

```python
    def consume(self, upto: int) -> None:
        """Drop the bytes belonging to the cell just answered, keep the rest.

        Clearing the whole buffer instead would discard a prompt that has
        already arrived but race with one still in flight, so the next cell
        would sometimes open with the previous cell's trailing output.
        """
        with self._changed:
            del self.out[:upto]
            self.truncated = False
            self._marker = b""
            self.marker_seen = False
            self._tail = b""
```

In `CasSession._extractor`, have the sentinel extractor report how much it consumed by returning a `(outcome, consumed)` pair, and in `_send` call `kernel.consume(consumed)` after a successful read instead of relying on the next `clear()`. Keep `clear()` for the length path, where the driver emits exactly one frame and nothing else.

- [ ] **Step 6: Run the tests again**

Run: `uv run --extra test pytest tests/unit/test_cas_sentinel.py -v`
Expected: 6 passed.

- [ ] **Step 7: Run the whole suite and lint**

Run: `uv run --extra test pytest -q && uvx ruff check src tests`
Expected: 219 passed (213 + 6), 46 skipped, and `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add tests/fake_sentinel_cas.py tests/unit/conftest.py tests/unit/test_cas_sentinel.py src/hardy/cas.py
git commit -m "test: cover the sentinel framing path, and stop prompts leaking between cells"
```

---

### Task 2: Error classification against real banners

`SingularBackend.error_pattern` and `Macaulay2Backend.error_pattern` were written from memory. A misclassified error is accepted into replayable state, and the session then rebuilds from a cell that never worked.

**Files:**
- Create: `tests/fixtures/cas/singular-errors.txt`, `tests/fixtures/cas/macaulay2-errors.txt`, `tests/fixtures/cas/singular-clean.txt`, `tests/fixtures/cas/macaulay2-clean.txt`
- Test: `tests/unit/test_cas_classify.py`

**Interfaces:**
- Consumes: `hardy.cas.backend_for(name).classify(text) -> Literal["ok", "error"]`.
- Produces: nothing new. This task only constrains existing behaviour.

- [ ] **Step 1: Record real banners**

These are transcribed from the documented output of each system. Create `tests/fixtures/cas/singular-errors.txt`:

```text
   ? `thisIsNotDefined` is not defined
   ? error occurred in or before STDIN line 1: `thisIsNotDefined;`
```

Create `tests/fixtures/cas/singular-clean.txt`:

```text
x2+y2
// ** redefining f **
_[1]=x2+y2
```

Create `tests/fixtures/cas/macaulay2-errors.txt`:

```text
stdio:1:1:(3): error: no method for adjacent objects:
stdio:2:14:(3): error: expected a ring
```

Create `tests/fixtures/cas/macaulay2-clean.txt`:

```text
o1 = R
o1 : PolynomialRing
     2    2
o2 = x  + y
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_cas_classify.py`:

```python
"""Error classification for the interpreters that cannot report status.

A misclassified error is worse than a missed one: it is accepted into
replayable state, and the session then rebuilds from a cell that never worked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hardy.cas import backend_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "cas"


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_real_error_banners_are_classified_as_errors(backend) -> None:
    text = (FIXTURES / f"{backend}-errors.txt").read_text(encoding="utf-8")
    assert backend_for(backend).classify(text) == "error"


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_each_error_line_is_recognised_on_its_own(backend) -> None:
    text = (FIXTURES / f"{backend}-errors.txt").read_text(encoding="utf-8")
    for line in text.strip().splitlines():
        assert backend_for(backend).classify(line) == "error", line


@pytest.mark.parametrize("backend", ["singular", "macaulay2"])
def test_ordinary_output_is_not_mistaken_for_an_error(backend) -> None:
    text = (FIXTURES / f"{backend}-clean.txt").read_text(encoding="utf-8")
    assert backend_for(backend).classify(text) == "ok"


def test_a_singular_comment_is_not_an_error() -> None:
    """`// ** redefining f **` is routine and must not poison a session."""
    assert backend_for("singular").classify("// ** redefining f **") == "ok"
```

- [ ] **Step 3: Run and fix the patterns**

Run: `uv run --extra test pytest tests/unit/test_cas_classify.py -v`

If a case fails, adjust the `error_pattern` on the backend in `src/hardy/cas.py` until all pass. Do not loosen a pattern to the point where the clean fixtures fail — a false positive costs a working cell, a false negative costs the session's integrity.

- [ ] **Step 4: Run the suite and lint**

Run: `uv run --extra test pytest -q && uvx ruff check src tests`
Expected: 226 passed, 46 skipped, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/cas tests/unit/test_cas_classify.py src/hardy/cas.py
git commit -m "test: pin CAS error classification to real interpreter banners"
```

---

### Task 3: A Linux CI job that runs the real binaries

Neither binary runs natively on the maintainer's Windows machine, and neither may become a requirement. CI on Linux is therefore the only place these adapters can be honestly verified. This is the repository's first workflow.

**Files:**
- Create: `.github/workflows/cas-backends.yml`

**Interfaces:**
- Consumes: `tests/integration/test_cas_real.py`, which already exists and is marked `real_toolchain`.
- Produces: nothing importable.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/cas-backends.yml`:

```yaml
# Singular and Macaulay2 have no native Windows build between them, and Hardy
# must never require WSL. Linux CI is where those adapters get verified.
name: CAS backends

on:
  push:
    branches: [main]
  pull_request:
    paths:
      - "src/hardy/cas*.py"
      - "tests/**/test_cas*.py"
      - ".github/workflows/cas-backends.yml"

jobs:
  hermetic:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Hermetic suite, with no CAS binary installed
        run: uv run --extra test pytest -q
      - name: Lint
        run: uvx ruff check src tests

  real-backends:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Install Singular and Macaulay2
        run: |
          sudo add-apt-repository -y ppa:macaulay2/macaulay2
          sudo apt-get update
          sudo apt-get install -y singular macaulay2
      - name: Confirm both are reachable
        run: |
          Singular --version
          M2 --version
      - name: Real-backend tests
        run: uv run --extra test pytest tests/integration/test_cas_real.py -v -m real_toolchain
```

- [ ] **Step 2: Push and read the result**

```bash
git add .github/workflows/cas-backends.yml
git commit -m "ci: verify the Singular and Macaulay2 backends on Linux"
git push
gh run watch
```

Expected on the first run: `hermetic` passes. `real-backends` may fail — that is the point of the task, and Task 4 fixes what it reports. Do not delete or skip a failing real-backend test to make CI green.

- [ ] **Step 3: Commit only if hermetic passed**

If `hermetic` failed, the workflow itself is wrong; fix it before proceeding. If `real-backends` failed, record the exact failure output in the Task 4 notes and continue.

---

### Task 4: Correct the adapters from CI evidence

The `argv` flags, the version sources, and the echo syntax for both backends were written without ever running them. This task replaces guesses with observed behaviour.

**Files:**
- Modify: `src/hardy/cas.py` — `SingularBackend`, `Macaulay2Backend`
- Test: `tests/integration/test_cas_real.py`

**Interfaces:**
- Consumes: the CI failure output from Task 3.
- Produces: `SingularBackend` and `Macaulay2Backend` with verified `argv`, `echo`, `version_source`, and `error_pattern`.

- [ ] **Step 1: Reproduce each failure locally against the CI log**

For each failing test, read the assertion and the captured output in the CI log. The likely candidates, in order:

- **Macaulay2 prompts.** M2 prints `i1 : ` before each input and `o1 = ` before each output even when stdin is not a terminal. If those appear in `record.stdout`, the adapter needs `--no-prompts` if that flag exists in the installed version, or the prompt prefix stripped in `classify`/extraction. Check `M2 --help` in CI output before choosing.
- **`-s` is not a Macaulay2 flag.** It was guessed. Remove it if `M2 --version` in Step 2 of Task 3 errored.
- **`version_source`.** Singular's `system("version");` returns an integer like `4310`; M2's `version#"VERSION"` returns a string. If either errors, substitute what the installed version accepts.
- **The echo statement.** Singular's `print("...")` and M2's `<< "..." << endl;` must each be valid at top level in a fresh session.

- [ ] **Step 2: Apply the corrections**

Edit the two backend classes in `src/hardy/cas.py`. Every changed value must be one observed to work in CI, not one that looks right.

- [ ] **Step 3: Push and confirm CI is green**

```bash
git add src/hardy/cas.py
git commit -m "fix: correct the Singular and Macaulay2 invocations against real binaries"
git push
gh run watch
```

Expected: both jobs pass.

- [ ] **Step 4: Record that they are now verified**

In `FEATURES.md`, replace the bullet reading "**Later:** Singular and Macaulay2 adapters are written against the sentinel protocol but unverified until CI runs somewhere those binaries exist" with:

```markdown
- **Now (implemented):** Singular and Macaulay2 adapters, verified on Linux CI
  against the real binaries. They remain unavailable natively on Windows —
  Macaulay2 has no Windows build and Singular arrives through Cygwin — which is
  why SymPy is the default.
```

- [ ] **Step 5: Commit**

```bash
git add FEATURES.md
git commit -m "docs: record that the sentinel backends are verified in CI"
```

---

### Task 5: Make the acceptance-run status true

`FEATURES.md:209-213` still says the first-experiment acceptance run is outstanding. The maintainer reports it is complete. A document that contradicts the state of the project is worse than one that admits ignorance, and `AGENTS.md` requires these four documents stay consistent.

**Files:**
- Modify: `FEATURES.md`

**Interfaces:** none.

- [ ] **Step 1: Get the run's identities from the maintainer**

This task cannot be completed by inference. Ask for, and do not invent: the model identity, the Lean toolchain and Mathlib revision, the theorem proved, the resulting grades, and where the run's artifacts are stored. If any is unavailable, say so in the text rather than omitting it.

- [ ] **Step 2: Rewrite the section**

Replace the paragraph beginning "The retained one-shot harness and its fake-process tests exercise this contract" with a statement of what was actually run, naming the identities from Step 1. Keep the sentence describing what the fake-process tests cover — that remains true and is a different claim.

- [ ] **Step 3: Check the other three documents agree**

Run: `grep -n "acceptance" README.md DESIGN.md FEATURES.md ARCHITECTURE.html`

Any sentence describing the acceptance run as pending must be updated in the same commit.

- [ ] **Step 4: Commit**

```bash
git add FEATURES.md README.md DESIGN.md ARCHITECTURE.html
git commit -m "docs: record the completed first-experiment acceptance run"
```

---

## Out of scope

- **Interrupt (#33).** A runaway cell is still stopped only by its timeout, which kills the kernel and loses session state. The Windows signalling detail is the hard part and deserves its own plan.
- **A bounded artifact reader.** Binding the last value to `_` is the current answer to an over-large result. Only build a reader if that is observed to be insufficient in real use.
- **Installing Singular on Windows through Cygwin.** Worth doing only if you actually want to run these backends locally; CI verification does not depend on it.

## Self-review

**Spec coverage.** The spec's sequencing step 5 was "Singular and Macaulay2 adapters" — Tasks 1–4. Step 6 was documentation — Task 4 Step 4 and Task 5. Steps 1–4 of the spec's sequencing are implemented and merged in `70e4b57`; this plan does not restate them.

**Placeholders.** Task 4 deliberately does not pre-write the corrected flag values, because inventing them is precisely the failure this plan exists to correct; Step 1 names the specific candidates to check and the evidence to check them against. Task 5 Step 1 requires information only the maintainer holds, and says so rather than fabricating identities.

**Type consistency.** `classify(text) -> Literal["ok", "error"]` is used identically in Tasks 1 and 2. `consume(upto: int)` in Task 1 Step 5 is the only new method; it is added to `_Kernel`, which already owns the buffer it mutates. The `sentinel_session` fixture signature matches the existing `cas_session` fixture's `make(**limits)` shape.
