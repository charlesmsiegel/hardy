# Mathlib search implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hardy find the Mathlib lemma a weak model needs — by offering search on the interactive surface at all, asking each engine the question it is good at, adding a natural-language engine, and giving the model Lean's own tactic search.

**Architecture:** `rank_premises` keeps its provenance contract but derives several *query shapes* from one goal and asks each engine only the shapes it accepts, fusing over `(engine, shape)` pairs while scoring each engine once. A new `try_tactics` tool runs Lean's own search tactics and is kept architecturally separate from the ranking because its results have a different evidential status. An `eval/` harness measures retrieval quality against checked-in cassettes.

**Tech Stack:** Python 3.11+, pydantic v2 (`FrozenModel` = frozen + `extra="forbid"`), pytest, `uv`. No new runtime dependencies: HTTP is `urllib` through the existing `_fetch_url`.

**Spec:** `docs/superpowers/specs/2026-08-25-mathlib-search-design.md`

## Global Constraints

- **Test command:** `uv run --extra test pytest`. Coverage floor is enforced from `pyproject.toml`; `--cov` writes `coverage.xml` and `htmlcov/index.html`.
- **`eval/` is not production code.** It lives outside `src/`, is not imported by anything under `src/`, and is excluded from the coverage floor. Never add an `eval/` import to `src/hardy/`.
- **Every value type is a `FrozenModel`** (`domain.py:20`): frozen, `extra="forbid"`. Adding a field to a persisted shape is a breaking read and moves `RunManifest.schema_version`.
- **A ranking is never evidence.** Only the Lean kernel verifies. Nothing in this plan may weaken `PremiseRanking.reproducible`, `complete`, or the provenance digest, or let a tool result read as a verified proof.
- **A source that did not answer says why.** Never return an empty list where a failure occurred — `SourceOutcome.a_source_that_did_not_answer_says_why` enforces this and must keep passing.
- **Test style, matched to `tests/unit/test_retrieval.py`:** single-quoted strings, `importlib.import_module('hardy.retrieval')` inside the test body, test names that read as full sentences, and a docstring saying *why* the test exists when the reason is not obvious from the name.
- **Commit message style, matched to `git log`:** a declarative sentence saying what the code now does, no `feat:`/`fix:` prefixes. Example from history: `Match the whole lake command, not its first word`.
- **Never describe generated Lean or helper processes as safe** (`AGENTS.md`). The sandbox is absent and that is a known risk.
- **Keep `README.md`, `DESIGN.md`, `FEATURES.md` and `ARCHITECTURE.html` consistent** with what the code does. `FEATURES.md:419` ("Retrieval and memory") and `README.md`'s `rank_premises` paragraph both describe today's two-source behaviour and must move when it does.

---

# Slice 1 — Search on the interactive surface

Cause 1 of the spec, and the largest improvement for the least code. `CHAT_TOOLS` (`chat.py:68`) offers nine tools and none of them search.

### Task 1: Give `lean.py` a way to build an environment identity from a project

`cli._environment_identity` builds the `EnvironmentIdentity` a `LeanService` needs, but it lives in `cli.py`, which chat wiring cannot import without a cycle. It takes a whole `Config` and reads one field off it. Move it to `lean.py`, which already imports `EnvironmentIdentity`, and narrow it to what it uses.

**Files:**
- Modify: `src/hardy/lean.py` (add `environment_identity`, immediately before `class LeanTools:` around line 296)
- Modify: `src/hardy/cli.py:371-388` (delete `_environment_identity`, call the new function at line 401)
- Test: `tests/unit/test_lean_environment_identity.py` (create)

**Interfaces:**
- Consumes: `EnvironmentIdentity` from `hardy.domain`, already imported at `lean.py:24`.
- Produces: `hardy.lean.environment_identity(lean_project: Path | None) -> EnvironmentIdentity`. Raises `ValueError` when `lean_project` is `None` or its `lake-manifest.json` is missing. Used by Task 2 and by `cli.build_prove_workflow`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_lean_environment_identity.py`:

```python
"""Where the identity a run is frozen under comes from.

It used to live in `cli.py` and take a whole `Config` to read one field off
it, which put it out of reach of anything that is not the command line --
including the interactive session, which needs the same identity to search.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

MANIFEST = {'packages': [{'name': 'mathlib', 'rev': '81a5d257' + '0' * 32}]}


def _project(tmp_path: Path) -> Path:
    (tmp_path / 'lake-manifest.json').write_text(json.dumps(MANIFEST), encoding='utf-8')
    return tmp_path


def test_the_identity_names_the_mathlib_the_manifest_resolved(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')

    identity = lean.environment_identity(_project(tmp_path))

    assert identity.mathlib_revision == MANIFEST['packages'][0]['rev']
    assert identity.imports == ('Mathlib',)


def test_the_manifest_digest_is_taken_over_the_bytes_on_disk(tmp_path) -> None:
    """Not over a re-serialisation of the parsed JSON.

    `LeanFindSource._manifest_matches` compares this number against a fresh
    hash of the same file, so a digest taken over anything but those exact
    bytes would report every pinned environment as unpinned.
    """
    lean = importlib.import_module('hardy.lean')
    project = _project(tmp_path)

    identity = lean.environment_identity(project)

    expected = hashlib.sha256((project / 'lake-manifest.json').read_bytes()).hexdigest()
    assert identity.lake_manifest_sha256 == expected


def test_no_project_is_an_error_naming_what_is_missing() -> None:
    lean = importlib.import_module('hardy.lean')

    with pytest.raises(ValueError, match='lean_project'):
        lean.environment_identity(None)


def test_a_project_without_a_manifest_names_the_file_it_wanted(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')

    with pytest.raises(ValueError, match='lake-manifest.json'):
        lean.environment_identity(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_lean_environment_identity.py -v`
Expected: FAIL with `AttributeError: module 'hardy.lean' has no attribute 'environment_identity'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/hardy/lean.py`, immediately before `class LeanTools:`:

```python
def environment_identity(lean_project: Path | None) -> EnvironmentIdentity:
    """Identify the exact Lean environment a run is frozen against.

    Here rather than in `cli.py` because the command line is not the only
    thing that needs it: premise retrieval has to name the corpus it
    searched, and the interactive session builds a `LeanService` for exactly
    that reason. It took a whole `Config` to read one field, which is what
    put it out of reach.

    The manifest digest is taken over the bytes on disk, not over a
    re-serialisation of the parsed JSON, because `LeanFindSource
    ._manifest_matches` compares it against a fresh hash of the same file.
    """
    if lean_project is None:
        raise ValueError("a pinned Lean environment needs lean_project set to a built Lake project")
    manifest_path = lean_project / "lake-manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{manifest_path} is missing; run the installer to build the project")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mathlib = next(item for item in manifest["packages"] if item["name"] == "mathlib")
    return EnvironmentIdentity(
        lean_version="4.32.0",
        lean_commit="8c9756b28d64dab099da31a4c09229a9e6a2ef35",
        mathlib_revision=mathlib["rev"],
        lake_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        imports=("Mathlib",),
    )
```

`hashlib` and `json` are already imported at `lean.py:15-16`.

Do **not** fix the hardcoded `lean_version` / `lean_commit` literals here. They are asserted rather than measured, `retrieval.py:386` already documents that it cannot trust them, and correcting them is a separate change with its own evidence story. Moving code and changing its behaviour in one commit is how a reviewer loses the thread.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_lean_environment_identity.py -v`
Expected: 4 passed

- [ ] **Step 5: Point `cli.py` at it**

Delete `_environment_identity` (`cli.py:371-388`). At its call site (line 401), inside `build_prove_workflow`:

```python
    environment = lean_module.environment_identity(config.lean_project)
```

The surrounding function already does `from .lean import LeanService` at line 394; add `from . import lean as lean_module` beside it.

Then remove `import hashlib` and `import json` from `cli.py` **only if** nothing else in the file uses them — check with `grep -n 'hashlib\.\|json\.' src/hardy/cli.py` before deleting either.

- [ ] **Step 6: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS, with no new failures. `tests/unit/test_batch_cli.py` and anything touching `build_prove_workflow` exercise the moved code.

- [ ] **Step 7: Commit**

```bash
git add src/hardy/lean.py src/hardy/cli.py tests/unit/test_lean_environment_identity.py
git commit -m "Put the environment identity where more than the CLI can reach it"
```

---

### Task 2: A search runtime the interactive session can hold

Mirror `cas_tools.build_runtime` (`cas_tools.py:79`): discover what is needed, return a runtime or `None` and the reason. Chat holds a `LeanTools` with a placeholder `Request` and a `_environment` **string**, so it cannot build a `LeanService` itself.

**Files:**
- Create: `src/hardy/search_tools.py`
- Test: `tests/unit/test_search_tools.py` (create)

**Interfaces:**
- Consumes: `hardy.lean.environment_identity` (Task 1), `hardy.lean.LeanService`, `hardy.retrieval.build_retriever`, `hardy.config.Config`.
- Produces:
  - `SearchToolRuntime` with `.service: LeanService`, `.retriever: PremiseRetriever`, and three methods returning `ToolResult`: `rank_premises(goal: str, limit: int = 10)`, `search_declarations(query: str, limit: int = 10)`, `inspect_declarations(names: list[str])`.
  - `SEARCH_TOOL_NAMES: frozenset[str]` = `{'rank_premises', 'search_declarations', 'inspect_declarations'}`.
  - `build_runtime(config) -> tuple[SearchToolRuntime | None, str]`.
  - Task 3 consumes all of these; Task 8 adds a `description` parameter; Task 11 adds `try_tactics` to the same runtime.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_search_tools.py`:

```python
"""Search on the interactive surface, and what it says when it cannot run.

The session's own Lean access is a `LeanTools` built around a placeholder
`Request`, and its `_environment` is a cache-invalidation string rather than
an `EnvironmentIdentity`. Neither can be handed to a `LeanService`, so the
runtime is assembled from the `Config` instead.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

MANIFEST = {'packages': [{'name': 'mathlib', 'rev': '81a5d257' + '0' * 32}]}


def _project(tmp_path: Path) -> Path:
    project = tmp_path / 'lean'
    project.mkdir()
    (project / 'lake-manifest.json').write_text(json.dumps(MANIFEST), encoding='utf-8')
    (project / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0\n', encoding='utf-8')
    return project


def _config(tmp_path: Path, project: Path | None):
    configuration = importlib.import_module('hardy.config')
    return configuration.Config(
        workspace=tmp_path / 'workspace',
        lean_project=project,
    )


def test_a_configured_project_yields_a_runtime_that_can_search(tmp_path) -> None:
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    assert runtime is not None
    assert runtime.service.environment.mathlib_revision == MANIFEST['packages'][0]['rev']
    assert 'Mathlib' in detail


def test_no_lake_project_yields_no_runtime_and_the_reason_why(tmp_path) -> None:
    """The reason travels, because it is what the tools will refuse with.

    A model told the tool does not exist concludes Hardy cannot search. A
    model told no Lake project is configured can say so to the user, which is
    the outcome that gets it fixed.
    """
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, None))

    assert runtime is None
    assert 'lean_project' in detail


def test_a_project_without_a_manifest_is_a_reason_and_not_a_crash(tmp_path) -> None:
    bare = tmp_path / 'bare'
    bare.mkdir()
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, bare))

    assert runtime is None
    assert 'lake-manifest.json' in detail


def test_a_ranking_comes_back_as_json_a_model_can_read(tmp_path, monkeypatch) -> None:
    search_tools = importlib.import_module('hardy.search_tools')
    retrieval = importlib.import_module('hardy.retrieval')
    runtime, _ = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    provenance = retrieval.RetrievalProvenance(
        premises_sha256=retrieval.premises_digest(()),
        budget_seconds=600,
        prior_seconds_spent=0.0,
        goal_sha256='0' * 64,
        query_sha256='0' * 64,
        ranker=retrieval.RANKER,
        sources=(),
    )
    ranking = retrieval.PremiseRanking(
        goal='⊢ _ + _ = _ + _',
        query='_ + _ = _ + _',
        premises=(),
        provenance=provenance,
        provenance_sha256=provenance.digest,
        complete=True,
        reproducible=True,
        seconds_spent=0.0,
        run_seconds_remaining=600.0,
        budget_exhausted=False,
    )
    monkeypatch.setattr(runtime.retriever, 'rank', lambda goal, limit=10: ranking)

    result = runtime.rank_premises('⊢ _ + _ = _ + _', limit=5)

    assert result.ok
    assert json.loads(result.output)['query'] == '_ + _ = _ + _'


def test_a_lean_command_that_is_not_the_configured_lake_yields_no_runtime(tmp_path) -> None:
    """Otherwise the model searches one environment and checks in another.

    Chat elaborates through `lean_command`; search would run `config.lake`.
    Under the default they are one program, and a custom wrapper is exactly
    the configuration where a name found in one Lean does not elaborate in
    the other.
    """
    configuration = importlib.import_module('hardy.config')
    search_tools = importlib.import_module('hardy.search_tools')
    config = configuration.Config(
        workspace=tmp_path / 'workspace',
        lean_project=_project(tmp_path),
        lean_command=('/opt/wrapper/lean-shim',),
    )

    runtime, detail = search_tools.build_runtime(config)

    assert runtime is None
    assert 'lean-shim' in detail


def test_a_lake_elsewhere_on_disk_is_caught_even_though_the_names_agree(tmp_path) -> None:
    """`HARDY_LAKE=/opt/pinned/lake` against the default `lake env lean`.

    Both basenames are `lake`, so a name comparison calls them equivalent
    while `PATH` resolves chat's to something else -- searching one Lean and
    checking in another, under a provenance naming neither discrepancy.
    """
    configuration = importlib.import_module('hardy.config')
    search_tools = importlib.import_module('hardy.search_tools')
    elsewhere = tmp_path / 'pinned' / 'lake'
    elsewhere.parent.mkdir()
    elsewhere.write_text('#!/bin/sh\n', encoding='utf-8')
    config = configuration.Config(
        workspace=tmp_path / 'workspace',
        lean_project=_project(tmp_path),
        lake=elsewhere,
    )

    runtime, detail = search_tools.build_runtime(config)

    assert runtime is None
    assert 'lake' in detail


def test_a_bad_goal_is_refused_as_an_answer_rather_than_an_exception(tmp_path) -> None:
    """The dispatchers catch `ValueError`, but a refusal the model can read
    beats a generic `invalid tool call`."""
    search_tools = importlib.import_module('hardy.search_tools')
    runtime, _ = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    result = runtime.rank_premises('', limit=5)

    assert not result.ok
    assert 'characters' in result.output
```

If `Config(...)` rejects those keyword arguments, run `uv run python -c "import hardy.config as c; print(c.Config.__dataclass_fields__.keys())"` and pass the required fields it names — do not change `Config`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_search_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.search_tools'`

- [ ] **Step 3: Write minimal implementation**

Create `src/hardy/search_tools.py`:

```python
"""Premise search for the interactive session.

`chat.py` reaches Lean through a `LeanTools` built around a placeholder
`Request`, and its `_environment` is a cache-invalidation stamp rather than an
`EnvironmentIdentity`. Neither can be handed to a `LeanService`, which is why
this assembles one from the `Config` instead and hands the session a runtime
rather than parts it would have to wire up itself.

The shape follows `cas_tools.build_runtime`: discover first, and return either
a runtime or the reason there is none. What differs is what the caller does
with a `None`. A missing CAS backend means the `cas_*` tools are not offered,
because a tool that can only fail costs the model a turn to discover what
Hardy already knew. A missing Lake project means the search tools are offered
*and refuse with the reason*, because those two absences are not the same
thing: a CAS backend is optional, and a Lean project is what Hardy is for. A
model handed no search tool concludes Hardy cannot search -- which is the
defect this module exists to fix -- while a model told "no Lake project is
configured" can put that in front of the user, who can fix it.
"""

from __future__ import annotations

import json
from typing import Any

import os
import shutil
from pathlib import Path

from .config import Config
from .lean import LeanService, environment_identity
from .models import ToolResult
from .retrieval import PremiseRetriever, build_retriever

SEARCH_TOOL_NAMES = frozenset({"rank_premises", "search_declarations", "inspect_declarations"})

# What one `inspect_declarations` call may resolve. The session bounds a model
# observation elsewhere; this bounds the work before it is done.
MAX_NAMES = 20


class SearchToolRuntime:
    """The search tools, bound to one pinned environment."""

    def __init__(self, service: LeanService, retriever: PremiseRetriever) -> None:
        self.service = service
        self.retriever = retriever

    def rank_premises(self, goal: str, limit: int = 10) -> ToolResult:
        return self._answer(lambda: self.retriever.rank(goal, limit))

    def search_declarations(self, query: str, limit: int = 10) -> ToolResult:
        return self._answer(lambda: self.service.search_declarations(query, limit))

    def inspect_declarations(self, names: list[str]) -> ToolResult:
        return self._answer(
            lambda: self.service.inspect_declarations(
                tuple(str(name) for name in names[:MAX_NAMES])
            )
        )

    def _answer(self, call: Any) -> ToolResult:
        """Every refusal is an answer the model can read.

        A `ValueError` from a bound -- a goal that is too long, a limit out of
        range -- is the tool saying what it will accept, and reaches the model
        as that sentence rather than as the dispatcher's generic "invalid tool
        call". A transport failure is likewise an outcome: the ranking already
        records which source did not answer, so only a failure that took the
        whole call needs catching here.
        """
        try:
            value = call()
        except ValueError as error:
            return ToolResult(False, str(error))
        except Exception as error:  # noqa: BLE001 - a failed search is an answer, not a crash
            return ToolResult(False, f"{type(error).__name__}: {error}")
        return ToolResult(True, value.model_dump_json())


# Chat elaborates through `config.lean_command` (default `lake env lean`),
# while `LeanService` runs `config.lake` as `lake env lean --json`. Under the
# default those are one program. Under a customised `HARDY_LEAN_COMMAND` -- a
# wrapper script, a bare `lean`, a second Lake binary -- they are not, and the
# model would search one environment and check the name it found in another,
# reading a provenance that names neither discrepancy.
LAKE_ENV_LEAN = ("lake", "env", "lean")


def _same_toolchain(config: Config) -> bool:
    """Whether searching and checking would run the same program.

    Resolved through `PATH` and compared by inode, not by basename. Comparing
    names was the first attempt and does not work: with
    `HARDY_LAKE=/opt/pinned/lake` and the default `lean_command`, both reduce
    to `lake` while `PATH` resolves chat's to an unrelated binary -- the exact
    split this exists to catch, passing it.

    Anything that cannot be resolved is refused rather than assumed equal. A
    wrapper that execs the right Lake is refused too, and that is the right
    trade: no check short of running it could tell, and a false equivalence
    here hands the model a declaration its own Lean cannot elaborate.
    """
    command = tuple(config.lean_command)
    if command[1:] != LAKE_ENV_LEAN[1:]:
        return False
    resolved = shutil.which(command[0])
    lake = shutil.which(str(config.lake))
    if resolved is None or lake is None:
        return False
    try:
        return os.path.samefile(resolved, lake)
    except OSError:
        return False


def build_runtime(config: Config) -> tuple[SearchToolRuntime | None, str]:
    """A search runtime for this configuration, or None and the reason why."""
    if not _same_toolchain(config):
        return None, (
            f"lean_command is {' '.join(config.lean_command)!r} but search would run "
            f"{config.lake}; searching one toolchain and checking in another would hand the "
            "model a declaration its own Lean cannot elaborate"
        )
    try:
        environment = environment_identity(config.lean_project)
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        return None, str(error) or f"the Lake project could not be read: {type(error).__name__}"
    assert config.lean_project is not None  # environment_identity refuses None
    service = LeanService(
        lake=config.lake,
        lean_project=config.lean_project,
        environment=environment,
        limits=config.limits,
    )
    return (
        SearchToolRuntime(service, build_retriever(service, config.limits)),
        f"Mathlib {environment.mathlib_revision[:12]} in {config.lean_project}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_search_tools.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/hardy/search_tools.py tests/unit/test_search_tools.py
git commit -m "Assemble a search runtime the interactive session can hold"
```

---

### Task 3: Offer the search tools in the interactive session

**Files:**
- Modify: `src/hardy/chat.py:68` (`CHAT_TOOLS`), `chat.py:179` (`__init__`), `chat.py:1164` (`_tool`)
- Modify: `src/hardy/cli.py:86` (`_chat`, build the runtime beside the CAS one)
- Modify: `src/hardy/prompts/chat.md.j2`
- Modify: `README.md`, `FEATURES.md`
- Test: `tests/test_chat_search.py` (create), `tests/conftest.py` (add a fixture)

**Interfaces:**
- Consumes: `SearchToolRuntime`, `SEARCH_TOOL_NAMES`, `build_runtime` from Task 2.
- Produces: `MathematicsSession(..., search=..., search_detail=...)` keyword arguments, defaulting to `None` and `""` so every existing construction keeps working.

- [ ] **Step 1: Write the failing test**

First read how the existing chat tests build a session, because the fixture must reuse that construction rather than invent a second one:

```bash
grep -n 'MathematicsSession(' tests/test_chat.py | head -5
```

Create `tests/test_chat_search.py`:

```python
"""The interactive session can search, and says so when it cannot.

`CHAT_TOOLS` offered nine tools and none of them searched, while
`chat.md.j2` told the model to "search and check first". A model in that
position guesses names into `check_lean`, which is the slowest possible way
to discover that a lemma is called something else.
"""

from __future__ import annotations

import importlib
import json

from hardy.models import ToolResult


class FakeSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def rank_premises(self, goal: str, limit: int = 10) -> ToolResult:
        self.calls.append(('rank_premises', {'goal': goal, 'limit': limit}))
        return ToolResult(True, json.dumps({'premises': [{'name': 'Nat.add_comm'}]}))

    def search_declarations(self, query: str, limit: int = 10) -> ToolResult:
        self.calls.append(('search_declarations', {'query': query, 'limit': limit}))
        return ToolResult(True, json.dumps({'results': []}))

    def inspect_declarations(self, names: list[str]) -> ToolResult:
        self.calls.append(('inspect_declarations', {'names': names}))
        return ToolResult(True, json.dumps({'found': []}))


def test_the_session_advertises_the_search_tools() -> None:
    chat = importlib.import_module('hardy.chat')

    offered = {spec['function']['name'] for spec in chat.CHAT_TOOLS}

    assert {'rank_premises', 'search_declarations', 'inspect_declarations'} <= offered


def test_a_ranking_asked_for_reaches_the_search_runtime(session_factory) -> None:
    search = FakeSearch()
    session = session_factory(search=search, search_detail='Mathlib abcdef in /lean')

    result = session._tool('rank_premises', {'goal': '⊢ _ + _ = _ + _', 'limit': 5})

    assert result.ok
    assert search.calls == [('rank_premises', {'goal': '⊢ _ + _ = _ + _', 'limit': 5})]


def test_without_a_lake_project_the_tool_refuses_with_the_reason(session_factory) -> None:
    """Advertised and refusing, not absent.

    This is deliberately unlike the `cas_*` tools, which are withheld when no
    backend was found. A CAS backend is optional; a Lean project is the thing
    Hardy is for, and a model handed no search tool concludes Hardy cannot
    search rather than that this machine is not set up.
    """
    session = session_factory(search=None, search_detail='lean_project is not set')

    result = session._tool('rank_premises', {'goal': '⊢ True'})

    assert not result.ok
    assert 'lean_project is not set' in result.output


def test_the_refusal_is_recorded_in_the_transcript_like_any_other_answer(session_factory) -> None:
    session = session_factory(search=None, search_detail='lean_project is not set')

    session._dispatch('rank_premises', {'goal': '⊢ True'})

    recorded = [
        entry
        for entry in session.transcript_path.read_text(encoding='utf-8').splitlines()
        if json.loads(entry).get('name') == 'rank_premises'
    ]
    assert len(recorded) == 1
```

Add a `session_factory` fixture to `tests/conftest.py`, modelled on the construction the grep above showed, with `search` and `search_detail` passed through.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/test_chat_search.py -v`
Expected: FAIL — the advertisement test fails on the set comparison, and the others fail because `MathematicsSession` takes no `search` argument.

- [ ] **Step 3: Add the tool specs**

Append to `CHAT_TOOLS` in `src/hardy/chat.py`, after the `request_assumption` entry:

```python
    {"type": "function", "function": {"name": "rank_premises", "description": "Rank the Mathlib declarations most likely to help with one goal. Paste the goal exactly as Lean printed it, hypotheses and all. Optionally pass `description`: one English sentence saying what the goal is about, which is what the natural-language search is given. A ranking is a heuristic, never evidence — confirm any name with inspect_declarations before relying on it.", "parameters": {"type": "object", "properties": {"goal": {"type": "string"}, "description": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["goal"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_declarations", "description": "Search the pinned Lean environment for declarations whose result type matches a pattern, with `_` for holes. Use rank_premises instead when you do not already know the shape you are looking for.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_declarations", "description": "Resolve exact Lean declaration names to their signatures in the pinned environment. This is how a name from a ranking is confirmed before it is used.", "parameters": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}}, "required": ["names"], "additionalProperties": False}}},
```

The `description` property is accepted now and ignored until Task 8. The tool schema is what the model reads, and adding a property later would mean two prompt-set identities for one capability.

- [ ] **Step 4: Hold the runtime on the session**

Add two keyword parameters to `MathematicsSession.__init__` (`chat.py:179`), after `cas_detail: str = ""`:

```python
    search: Any = None, search_detail: str = ""
```

and in the body, beside where `self.cas` is set (line 184):

```python
        # None when no pinned Lake project was found. Unlike `cas`, the tools
        # are still advertised: see `search_tools` for why absence is reported
        # rather than hidden.
        self.search = search
        self.search_detail = search_detail
```

- [ ] **Step 5: Dispatch them**

In `_tool` (`chat.py:1164`), add before the `read_workspace` branch:

```python
        if name in SEARCH_TOOL_NAMES:
            return self._search_tool(name, arguments)
```

and add the method beside `_cas_tool`:

```python
    def _search_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Search, or the reason this machine cannot.

        The refusal carries `search_detail` verbatim because that string is
        the actionable part -- "lean_project is not set" is something the user
        can fix, and the model can only relay what it was told.
        """
        if self.search is None:
            return ToolResult(False, f"search is unavailable: {self.search_detail}")
        if name == "rank_premises":
            return self.search.rank_premises(
                str(arguments["goal"]), int(arguments.get("limit") or 10)
            )
        if name == "search_declarations":
            return self.search.search_declarations(
                str(arguments["query"]), int(arguments.get("limit") or 10)
            )
        names = arguments.get("names") or []
        if not isinstance(names, list):
            return ToolResult(False, "names must be a list of declaration names")
        return self.search.inspect_declarations([str(item) for item in names])
```

Import at the top of `chat.py`, beside the `cas_tools` import at line 19:

```python
from .search_tools import SEARCH_TOOL_NAMES
```

- [ ] **Step 6: Build it in `_chat`**

In `src/hardy/cli.py:86`, after the `cas_tools.build_runtime(...)` call:

```python
    # Built here for the same reason the CAS runtime is: `run_session` can
    # call its factory twice when the interactive shell falls back to the
    # plain one, and reading the manifest and hashing it twice is waste.
    search, search_detail = search_tools.build_runtime(config)
```

Pass `search=search, search_detail=search_detail` to the `MathematicsSession(...)` construction inside `build`, and add `from . import search_tools` to the imports at the top of `cli.py`.

- [ ] **Step 7: Run the tests**

Run: `uv run --extra test pytest tests/test_chat_search.py tests/test_chat.py -v`
Expected: PASS

- [ ] **Step 8: Update the prompt**

In `src/hardy/prompts/chat.md.j2`, replace the sentence at line 19 beginning "If a needed theorem is not in Mathlib or the user's imports, search and check first." with:

```
Search before you guess. Mathlib is far larger than your memory of it, and a
lemma you cannot name is usually there under a name you would not have
predicted. Call rank_premises with the goal exactly as Lean printed it,
hypotheses included, and pass description when you can say in one English
sentence what the goal is about. Confirm any name it offers with
inspect_declarations before you use it: a ranking is a heuristic and only the
kernel verifies anything. Use search_declarations when you already know the
shape of the result type you want. If a needed theorem is genuinely not in
Mathlib or the user's imports, and only then, it must be assumed: call
request_assumption with the exact Lean statement, informal statement, source
identity, and reason.
```

Keep the rest of that paragraph — the assumption-approval rules — unchanged.

- [ ] **Step 9: Update the docs that describe the surfaces**

```bash
grep -n 'staged tools and the MCP\|rank_premises' README.md FEATURES.md
```

Both state that retrieval is offered on the staged tools and the MCP server. Both must now say the interactive session offers it too, and that the tools refuse with a reason when no Lake project is configured.

- [ ] **Step 10: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS. If `tests/unit/test_prompts.py` pins `PROMPT_SET_SHA256`, update the expected digest to what the run reports.

- [ ] **Step 11: Commit**

```bash
git add src/hardy/chat.py src/hardy/cli.py src/hardy/prompts/chat.md.j2 tests/test_chat_search.py tests/conftest.py README.md FEATURES.md
git commit -m "Let the interactive session search, instead of telling it to"
```

---

# Slice 2 — Measure what retrieval finds today

Before any retrieval change. Slices 3-5 are judged against this baseline, and a baseline taken afterwards measures nothing.

### Task 4: The evaluation fixture and its cassettes

**Files:**
- Create: `eval/README.md`, `eval/premises/cases.json`, `eval/cassette.py`
- Test: `eval/test_cassette.py` (create — run by the same pytest invocation)

**Interfaces:**
- Produces:
  - `eval.cassette.Cassette(directory)` with `.replay(engine: str, query: str) -> list[dict]` (raises `CassetteMiss` when unrecorded) and `.record(engine: str, query: str, hits: list[dict]) -> None`.
  - `eval.cassette.CassetteMiss(KeyError)`.
  - `eval.cassette.key(engine: str, query: str) -> str`.
  - Task 5 consumes all of these.

- [ ] **Step 1: Write `eval/README.md`**

````markdown
# Retrieval evaluation

Not production code. Nothing under `src/hardy/` may import from here, and this
directory is excluded from the coverage floor.

## What it measures

Whether `rank_premises` returns the lemma that actually closes a goal — and,
just as importantly, **which query shape found it**. If a shape never wins a
case another shape loses, it did not earn its complexity and should come out.

Reported per run: recall@1, recall@5, recall@10, MRR, seconds spent, and a
per-shape attribution table.

## Running it

```sh
uv run python eval/run_premise_eval.py
```

Hermetic by default: engine responses are replayed from `premises/cassettes/`,
so no network and no Lean toolchain are needed. `--live` re-records them
against the real services, which needs both.

## Adding a case

A case is a goal as Lean printed it, an optional English description, and the
declaration names that should appear in the ranking. Take them from real
Mathlib proofs: pick a lemma a proof applies, and the goal state it was
applied to. `why` says what the case is testing, so a future reader can tell a
deliberately hard case from an accidental one.
````

- [ ] **Step 2: Write the fixture**

Create `eval/premises/cases.json`. Start with these five, which cover the shapes the spec says fail today; grow it to ~25 in Step 8.

```json
{
  "cases": [
    {
      "id": "nat-add-comm",
      "goal": "n m : ℕ\n⊢ n + m = m + n",
      "description": "addition of natural numbers is commutative",
      "expect": ["Nat.add_comm", "add_comm"],
      "provenance": "Mathlib.Algebra.Group.Basic",
      "why": "the easy case; if this regresses, something is badly wrong"
    },
    {
      "id": "list-reverse-length",
      "goal": "α : Type u_1\nxs : List α\n⊢ xs.reverse.length = xs.length",
      "description": "the length of a reversed list equals the length of the list",
      "expect": ["List.length_reverse"],
      "provenance": "Mathlib.Data.List.Basic",
      "why": "dot notation: the conclusion shape is unusable today and both sources reject it. The constants shape recovers only `List` from the hypothesis, not `List.reverse` -- whether that is enough is what the per-shape metric decides"
    },
    {
      "id": "compact-continuous-max",
      "goal": "f : X → ℝ\ns : Set X\nhs : IsCompact s\nhf : ContinuousOn f s\nhne : s.Nonempty\n⊢ ∃ x ∈ s, ∀ y ∈ s, f y ≤ f x",
      "description": "a continuous function on a nonempty compact set attains a maximum",
      "expect": ["IsCompact.exists_isMaxOn", "IsCompact.exists_forall_ge"],
      "provenance": "Mathlib.Topology.Order.Compact",
      "why": "the signal is entirely in the hypotheses, which today's query discards"
    },
    {
      "id": "finset-sum-congr",
      "goal": "s : Finset α\nf g : α → β\nh : ∀ x ∈ s, f x = g x\n⊢ ∑ x ∈ s, f x = ∑ x ∈ s, g x",
      "description": "two sums over a finite set are equal when the summands agree pointwise",
      "expect": ["Finset.sum_congr"],
      "provenance": "Mathlib.Algebra.BigOperators.Basic",
      "why": "big-operator notation the pattern search handles poorly"
    },
    {
      "id": "le-antisymm",
      "goal": "a b : α\nh₁ : a ≤ b\nh₂ : b ≤ a\n⊢ a = b",
      "description": "a partial order is antisymmetric",
      "expect": ["le_antisymm"],
      "provenance": "Mathlib.Order.Basic",
      "why": "the conclusion `_ = _` matches everything; only the hypotheses discriminate"
    }
  ]
}
```

- [ ] **Step 3: Write the failing cassette test**

Create `eval/test_cassette.py`:

```python
"""Recorded engine answers, so the evaluation runs without network or Lean."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    'eval_cassette', Path(__file__).parent / 'cassette.py'
)
cassette = importlib.util.module_from_spec(_spec)
sys.modules['eval_cassette'] = cassette
_spec.loader.exec_module(cassette)


def test_a_recorded_answer_comes_back_verbatim(tmp_path) -> None:
    tape = cassette.Cassette(tmp_path)
    hits = [{'name': 'Nat.add_comm', 'signature': 'n + m = m + n'}]

    tape.record('loogle', '_ + _ = _ + _', hits)

    assert cassette.Cassette(tmp_path).replay('loogle', '_ + _ = _ + _') == hits


def test_two_engines_asked_the_same_query_do_not_share_a_recording(tmp_path) -> None:
    """The key covers the engine as well as the query.

    `#find` and Loogle are asked the same conclusion pattern by design, and a
    key over the query alone would have let one engine's answer stand in for
    the other's -- which is exactly the confusion the evaluation exists to
    detect.
    """
    tape = cassette.Cassette(tmp_path)
    tape.record('loogle', '_ = _', [{'name': 'from-loogle'}])
    tape.record('lean-find', '_ = _', [{'name': 'from-find'}])

    assert tape.replay('loogle', '_ = _') == [{'name': 'from-loogle'}]
    assert tape.replay('lean-find', '_ = _') == [{'name': 'from-find'}]


def test_an_unrecorded_query_is_a_miss_naming_what_was_asked(tmp_path) -> None:
    """Not an empty list. An empty list reads as "the engine found nothing",
    which is the one thing a missing recording does not mean."""
    tape = cassette.Cassette(tmp_path)

    with pytest.raises(cassette.CassetteMiss, match='loogle'):
        tape.replay('loogle', 'never recorded')
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run --extra test pytest eval/test_cassette.py -v`
Expected: FAIL with `FileNotFoundError` for `eval/cassette.py`

- [ ] **Step 5: Write the implementation**

Create `eval/cassette.py`:

```python
"""Recorded engine answers.

The evaluation must run in CI, which has no network and no Mathlib build. So
every engine answer is recorded once against the real service and replayed
from disk thereafter. One file per (engine, query) rather than one large
tape, so re-recording a single query is a one-file diff a reviewer can read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class CassetteMiss(KeyError):
    """No recording for this (engine, query).

    Distinct from an engine that answered with nothing: an empty list is a
    result, and returning one here would file a missing recording under
    "found nothing" and quietly score the case as a miss.
    """


def key(engine: str, query: str) -> str:
    """Covers the engine as well as the query.

    `#find` and Loogle are asked the same conclusion pattern by design, so a
    key over the query alone would let one engine's answer stand in for the
    other's.
    """
    return hashlib.sha256(f"{engine}\n{query}".encode("utf-8")).hexdigest()[:32]


class Cassette:
    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def _path(self, engine: str, query: str) -> Path:
        return self._directory / f"{key(engine, query)}.json"

    def record(self, engine: str, query: str, hits: list[dict]) -> None:
        self._path(engine, query).write_text(
            json.dumps(
                {"engine": engine, "query": query, "hits": hits},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def replay(self, engine: str, query: str) -> list[dict]:
        path = self._path(engine, query)
        if not path.exists():
            raise CassetteMiss(f"{engine} was never recorded answering {query!r}")
        return json.loads(path.read_text(encoding="utf-8"))["hits"]
```

- [ ] **Step 5b: Record which corpus answered**

`record` takes a `corpus: str` -- the source's own `identity.corpus`, which
already names the endpoint for a remote engine and the toolchain pin plus
manifest digest for `#find` -- and stores it beside the hits. Add
`identities() -> dict[str, set[str]]`, mapping each engine to every corpus
string recorded for it. The runner prints them beside the metrics and refuses
to replay a set where one engine shows more than one.

The key stays `(engine, query)`. Without the recorded identity, a re-recording
against a moved Mathlib keeps the same filenames and the same case ids while
measuring a different corpus, and `eval/README.md`'s baseline would go on being
compared against numbers that no longer mean the same thing. Test it:

```python
def test_a_set_recorded_against_two_corpora_is_refused_rather_than_replayed(tmp_path) -> None:
    """Same filenames, same case ids, different Mathlib. Replaying it would
    compare a baseline against numbers that no longer mean the same thing."""
    tape = cassette.Cassette(tmp_path)
    tape.record('lean-find', '_ = _', [{'name': 'A'}], corpus='Mathlib aaaa')
    tape.record('lean-find', '_ + _', [{'name': 'B'}], corpus='Mathlib bbbb')

    assert tape.identities()['lean-find'] == {'Mathlib aaaa', 'Mathlib bbbb'}
```

`RecordingSource.search` in Task 5 passes `self._inner.identity.corpus` when it
records. `Cassette` also gains `recorded_sources() -> list[tuple[str, str, str,
frozenset[str]]]` -- name, kind, corpus and accepted shapes per engine, read
back off the tape -- because a replay run has no live source to ask, and asking
one is what made the hermetic path crash.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --extra test pytest eval/test_cassette.py -v`
Expected: 3 passed

- [ ] **Step 7: Keep `eval/` out of the coverage floor**

```bash
grep -n 'cov\|omit\|source\|testpaths' pyproject.toml
```

Ensure `--cov` targets `src/hardy` (or that `eval` is in `omit`), and that `testpaths` — if set — includes `eval` so `eval/test_cassette.py` runs. If `testpaths` is `tests` only, add `eval` beside it.

- [ ] **Step 8: Grow the fixture to ~25 cases**

Add twenty more cases spanning arithmetic, lists, topology, algebra and order. Draw each from a real Mathlib proof and fill `provenance` with the module it came from. At least six must be goals whose discriminating signal is in the hypotheses, and at least four must use dot notation — those are the two shapes the spec says fail today, and a fixture that under-samples them cannot show the ladder working.

- [ ] **Step 9: Run and commit**

```bash
uv run --extra test pytest eval/
git add eval/
git commit -m "Record what a search engine answered, so the evaluation needs neither"
```

---

### Task 5: The evaluation runner

**Files:**
- Create: `eval/run_premise_eval.py`, `eval/_adapter.py`
- Test: `eval/test_run_premise_eval.py` (create)

**Interfaces:**
- Consumes: `eval.cassette.Cassette`, `hardy.retrieval`, `hardy.domain.RunLimits`, `hardy.lean.DeclarationRecord`.
- Produces: `evaluate(cases: list[dict], retriever_for) -> Report`; `Ranking(premises, shapes_by_premise, seconds)`; `Row(case_id, found_rank, found_by, seconds)`; `Report.recall_at(k)`, `.mrr`, `.by_shape`, `.seconds`, `.rows`.

Until Slice 3 lands there is one shape (`conclusion`), so every `by_shape` entry lands there. That is the baseline, and it is what makes the ladder's contribution legible afterwards.

- [ ] **Step 1: Write the failing test**

Create `eval/test_run_premise_eval.py`:

```python
"""The metrics, over a retriever with scripted answers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'eval_runner', Path(__file__).parent / 'run_premise_eval.py'
)
runner = importlib.util.module_from_spec(_spec)
sys.modules['eval_runner'] = runner
_spec.loader.exec_module(runner)

CASES = [
    {'id': 'hit-first', 'goal': '⊢ a', 'expect': ['A']},
    {'id': 'hit-third', 'goal': '⊢ b', 'expect': ['C']},
    {'id': 'miss', 'goal': '⊢ c', 'expect': ['Z']},
]


class ScriptedRetriever:
    def rank(self, goal, limit=10, description=None):
        names = ['A', 'B', 'C'][:limit]
        return runner.Ranking(
            premises=tuple(names),
            shapes_by_premise={name: ('conclusion',) for name in names},
            seconds=0.5,
        )


def test_recall_at_one_counts_only_the_case_ranked_first() -> None:
    report = runner.evaluate(CASES, lambda: ScriptedRetriever())

    assert report.recall_at(1) == 1 / 3


def test_recall_at_ten_counts_every_case_whose_lemma_appeared_at_all() -> None:
    report = runner.evaluate(CASES, lambda: ScriptedRetriever())

    assert report.recall_at(10) == 2 / 3


def test_the_reciprocal_rank_of_a_miss_is_zero_rather_than_dropped() -> None:
    """Averaging only over the hits would make a run that finds one lemma
    perfectly look better than one that finds twenty well."""
    report = runner.evaluate(CASES, lambda: ScriptedRetriever())

    assert report.mrr == (1.0 + 1 / 3 + 0.0) / 3


def test_a_case_records_which_shape_surfaced_its_lemma() -> None:
    report = runner.evaluate(CASES, lambda: ScriptedRetriever())

    assert report.by_shape == {'conclusion': 2}


def test_the_expected_lemma_matches_on_any_of_the_names_a_case_accepts() -> None:
    """`Nat.add_comm` and `add_comm` both close the same goal, and which one a
    ranking surfaces is not what is being measured."""
    cases = [{'id': 'either', 'goal': '⊢ a', 'expect': ['Z', 'B']}]

    report = runner.evaluate(cases, lambda: ScriptedRetriever())

    assert report.recall_at(5) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest eval/test_run_premise_eval.py -v`
Expected: FAIL with `FileNotFoundError` for `eval/run_premise_eval.py`

- [ ] **Step 3: Write the runner**

Create `eval/run_premise_eval.py`:

```python
"""Does retrieval return the lemma that closes the goal, and which shape found it?

Not production code. Nothing under `src/hardy/` imports this.

The second question is the one that pays for itself. Recall says whether the
ladder helped; per-shape attribution says *which rung* helped, and a rung that
never wins a case another rung loses is complexity with nothing behind it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
CASES_PATH = HERE / "premises" / "cases.json"


@dataclass(frozen=True)
class Ranking:
    """What the evaluation needs out of a `PremiseRanking`.

    A narrow shape rather than the real one, so a scripted retriever in a test
    does not have to build a whole provenance record to answer one question.
    """

    premises: tuple[str, ...]
    shapes_by_premise: dict[str, tuple[str, ...]]
    seconds: float


@dataclass(frozen=True)
class Row:
    case_id: str
    found_rank: int | None
    found_by: tuple[str, ...]
    seconds: float


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def recall_at(self, k: int) -> float:
        if not self.rows:
            return 0.0
        hits = sum(1 for row in self.rows if row.found_rank is not None and row.found_rank <= k)
        return hits / len(self.rows)

    @property
    def mrr(self) -> float:
        """A miss contributes zero rather than being dropped.

        Averaging over the hits alone would rank a run that finds one lemma
        perfectly above one that finds twenty well.
        """
        if not self.rows:
            return 0.0
        total = sum(0.0 if row.found_rank is None else 1 / row.found_rank for row in self.rows)
        return total / len(self.rows)

    @property
    def by_shape(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            for shape in row.found_by:
                counts[shape] = counts.get(shape, 0) + 1
        return counts

    @property
    def seconds(self) -> float:
        return sum(row.seconds for row in self.rows)


def evaluate(cases: list[dict], retriever_for) -> Report:
    report = Report()
    for case in cases:
        ranking = retriever_for().rank(
            case["goal"], limit=10, description=case.get("description")
        )
        expected = set(case["expect"])
        rank = next(
            (index for index, name in enumerate(ranking.premises, 1) if name in expected),
            None,
        )
        found_by = (
            ()
            if rank is None
            else ranking.shapes_by_premise.get(ranking.premises[rank - 1], ())
        )
        report.rows.append(Row(case["id"], rank, tuple(found_by), ranking.seconds))
    return report


def render(report: Report) -> str:
    lines = [
        f"cases          {len(report.rows)}",
        f"recall@1       {report.recall_at(1):.3f}",
        f"recall@5       {report.recall_at(5):.3f}",
        f"recall@10      {report.recall_at(10):.3f}",
        f"MRR            {report.mrr:.3f}",
        f"seconds        {report.seconds:.1f}",
        "",
        "found by shape",
    ]
    for shape, count in sorted(report.by_shape.items(), key=lambda item: -item[1]):
        lines.append(f"  {shape:<14} {count}")
    lines.append("")
    lines.append("misses")
    for row in report.rows:
        if row.found_rank is None:
            lines.append(f"  {row.case_id}")
    return "\n".join(lines)


def _retriever_factory(*, live: bool):
    """A retriever over the real engines, or over the recorded tape.

    Imported here rather than at module scope so `evaluate` stays testable
    with a scripted retriever and no Hardy import at all.
    """
    import _adapter  # noqa: PLC0415

    return _adapter.live_retriever if live else _adapter.cassette_retriever


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="ask the real services and re-record the cassettes; needs network and a built Mathlib",
    )
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    arguments = parser.parse_args()
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(HERE))
    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))["cases"]
    print(render(evaluate(cases, _retriever_factory(live=arguments.live))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest eval/test_run_premise_eval.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the adapter**

Create `eval/_adapter.py`. It wraps whatever sources `build_retriever` chose — rather than listing them itself, so the evaluation cannot drift from production — and converts a `PremiseRanking` into the runner's `Ranking`:

```python
"""Between the evaluation's narrow `Ranking` and Hardy's real retrieval.

Separate from `run_premise_eval.py` so the metrics can be tested without
importing Hardy at all, and so the day retrieval's shape changes, one file
moves.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from typing import NamedTuple  # noqa: E402

from cassette import Cassette  # noqa: E402
from hardy import retrieval  # noqa: E402
from hardy.domain import RunLimits  # noqa: E402
from hardy.lean import DeclarationRecord  # noqa: E402


class Ranking(NamedTuple):
    """The runner's `Ranking`, redeclared rather than imported.

    `run_premise_eval` imports this module, and when it is run as a script it
    is `__main__` -- so importing it back would load a *second* module object
    with a second `Ranking` class. `evaluate` only reads these three
    attributes, so declaring them here is both correct and one less cycle.
    Keep the field names in step with `run_premise_eval.Ranking`.
    """

    premises: tuple
    shapes_by_premise: dict
    seconds: float


TAPE = Cassette(Path(__file__).parent / "premises" / "cassettes")


class ReplaySource:
    """A recorded answer under the identity that was recorded with it.

    Not a wrapper around a live source. `build_retriever(None, ...)` builds a
    `LeanFindSource(None, ...)`, and `PremiseRetriever` reads `source.identity`
    before it ever calls `search` -- which dereferences
    `self._service.environment` and raises `AttributeError` on the first case.
    The hermetic path is the documented default, so that is every case.

    The identity comes from the cassette instead, which is the honest source
    for it anyway: the replayed answer was produced by that corpus, not by
    whatever this machine is configured for now.
    """

    def __init__(self, name, kind, corpus, accepts, tape: Cassette) -> None:
        self._identity = retrieval.SourceIdentity(
            name=name, kind=kind, corpus=corpus, pinned=False
        )
        self._accepts = frozenset(accepts)
        self._tape = tape

    @property
    def identity(self):
        # Always unpinned. A replay cannot promise the corpus is still there,
        # and the evaluation measures ranking quality rather than replayability.
        return self._identity

    @property
    def accepts(self) -> frozenset[str]:
        return self._accepts

    @property
    def worst_case_seconds(self) -> float:
        # A replayed source costs nothing, so the budget never refuses one and
        # the evaluation measures retrieval rather than metering.
        return 0.0

    def query_for(self, shape):
        return shape.query

    def search(self, goal: str, limit: int):
        return tuple(
            DeclarationRecord(name=hit["name"], signature=hit.get("signature", ""))
            for hit in self._tape.replay(self._identity.name, goal)[:limit]
        )


class RecordingSource:
    """A real source, taping what it answers. Only reached under `--live`."""

    def __init__(self, inner, tape: Cassette) -> None:
        self._inner = inner
        self._tape = tape

    @property
    def identity(self):
        return self._inner.identity

    @property
    def accepts(self) -> frozenset[str]:
        # Before Slice 3 lands, sources have no `accepts`; there is one shape.
        return getattr(self._inner, "accepts", frozenset({"conclusion"}))

    @property
    def worst_case_seconds(self) -> float:
        return self._inner.worst_case_seconds

    def query_for(self, shape):
        return self._inner.query_for(shape)

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        identity = self._inner.identity
        found = self._inner.search(goal, limit)
        self._tape.record(
            identity.name,
            goal,
            [{"name": r.name, "signature": r.signature} for r in found],
            corpus=identity.corpus,
        )
        return found


def _service():
    """The real `LeanService`, only reached under `--live`."""
    from hardy.lean import LeanService, environment_identity  # noqa: PLC0415

    project = Path(os.environ["HARDY_LEAN_PROJECT"])
    return LeanService(
        lake=Path(os.environ.get("HARDY_LAKE", "lake")),
        lean_project=project,
        environment=environment_identity(project),
        limits=RunLimits(),
    )


def _as_ranking(result) -> Ranking:
    return Ranking(
        premises=tuple(premise.name for premise in result.premises),
        shapes_by_premise={
            premise.name: tuple(sorted({item.source.split("/")[-1] for item in premise.ranks}))
            for premise in result.premises
        },
        seconds=result.seconds_spent,
    )


def _factory(*, live: bool):
    def make():
        if live:
            # Wrapping what `build_retriever` chose, rather than listing sources
            # here: the evaluation must measure the production source set, and a
            # second list would silently drift from it.
            chosen = retrieval.build_retriever(_service(), RunLimits())._sources  # noqa: SLF001
            sources = [RecordingSource(source, TAPE) for source in chosen]
        else:
            # Rebuilt from the tape, never from a live source over a `None`
            # service -- see `ReplaySource`. The set is whatever was recorded,
            # which is by construction the production set at recording time.
            sources = [
                ReplaySource(name, kind, corpus, accepts, TAPE)
                for name, kind, corpus, accepts in TAPE.recorded_sources()
            ]
            if not sources:
                raise SystemExit(
                    "no cassettes recorded; run with --live once against a built Mathlib"
                )

        class Adapted:
            def rank(self, goal, limit=10, description=None):
                retriever = retrieval.PremiseRetriever(sources=sources, limits=RunLimits())
                try:
                    return _as_ranking(retriever.rank(goal, limit, description=description))
                except TypeError:
                    # Before Slice 3, `rank` takes no description.
                    return _as_ranking(retriever.rank(goal, limit))

        return Adapted()

    return make


cassette_retriever = _factory(live=False)
live_retriever = _factory(live=True)
```

Reaching into `_sources` is deliberate and marked. If that private access proves brittle, add a public `sources` property to `PremiseRetriever` rather than duplicating the list here.

- [ ] **Step 6: Record the baseline**

With a built Mathlib and network available:

```bash
HARDY_LEAN_PROJECT=<path to the built Lake project> uv run python eval/run_premise_eval.py --live
uv run python eval/run_premise_eval.py
```

Both must print the same numbers. Save the hermetic output verbatim into `eval/README.md` under a new `## Baseline (before the query ladder)` heading, with the date. This is the number Slices 3-5 are judged against.

If a live run is not possible in this environment, **say so and stop** rather than committing cassettes recorded from guessed data. A fabricated baseline is worse than no baseline, because every later slice would be measured against it.

- [ ] **Step 7: Commit**

```bash
git add eval/
git commit -m "Measure what retrieval finds, before changing what it asks"
```

---

# Slice 3 — Ask each engine the question it is good at

Causes 3, 4 and 5. One goal becomes several shapes; each engine is asked only the shapes it accepts.

### Task 6: Derive several query shapes from one goal

**Files:**
- Modify: `src/hardy/retrieval.py` (add `QueryShape`, `search_queries`, `_global_constants`; leave `search_query` untouched)
- Test: `tests/unit/test_retrieval_queries.py` (create)

**Interfaces:**
- Consumes: existing `search_query`, `_local_names`, `TURNSTILE`, `MAX_QUERY_CHARACTERS`.
- Produces:
  - `QueryShape(FrozenModel)` with `name: Literal['conclusion', 'constants', 'description']`, `query: str`, `derived: str`.
  - `search_queries(goal: str, description: str | None = None) -> tuple[QueryShape, ...]`.
  - Task 7 consumes both.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_retrieval_queries.py`:

```python
"""What a goal is turned into before anything is asked.

One conclusion pattern was the whole of it, which meant a goal whose signal
sat in its hypotheses, or whose conclusion used dot notation, produced a query
every source rejected -- and an empty ranking that read as "no such lemma".
"""

from __future__ import annotations

import importlib


def _shapes(goal, description=None):
    retrieval = importlib.import_module('hardy.retrieval')
    return {shape.name: shape.query for shape in retrieval.search_queries(goal, description)}


def test_the_conclusion_shape_is_what_it_always_was() -> None:
    """Unchanged on purpose: it is the pinned source's only query, and the
    tests that pin its behaviour are not being renegotiated here."""
    retrieval = importlib.import_module('hardy.retrieval')
    goal = 'n m : ℕ\n⊢ n + m = m + n'

    assert _shapes(goal)['conclusion'] == retrieval.search_query(goal)


def test_the_constants_shape_reads_the_hypotheses_the_conclusion_threw_away() -> None:
    goal = (
        'f : X → ℝ\ns : Set X\nhs : IsCompact s\nhf : ContinuousOn f s\n'
        '⊢ ∃ x ∈ s, ∀ y ∈ s, f y ≤ f x'
    )

    constants = _shapes(goal)['constants'].split(', ')

    assert 'IsCompact' in constants
    assert 'ContinuousOn' in constants


def test_a_goal_written_with_dot_notation_still_yields_a_constants_query() -> None:
    """The case the conclusion shape cannot serve at all.

    `xs.reverse.length` wildcards to `_.reverse.length`, which both sources
    reject. The hypothesis line still says `List α`, and that is a name a
    search can use.
    """
    goal = 'α : Type u_1\nxs : List α\n⊢ xs.reverse.length = xs.length'

    assert 'List' in _shapes(goal)['constants'].split(', ')


def test_local_binder_names_are_never_offered_as_constants() -> None:
    """`n` and `m` mean nothing outside this goal, and Loogle answers a query
    naming them with `Unknown identifier`."""
    constants = _shapes('n m : ℕ\nhn : 0 < n\n⊢ n + m = m + n')['constants'].split(', ')

    assert 'n' not in constants
    assert 'm' not in constants
    assert 'hn' not in constants


def test_a_goal_naming_no_constants_produces_no_constants_shape() -> None:
    """An empty query is not a question, and offering one would spend a
    source's admission on a call that cannot answer."""
    assert 'constants' not in _shapes('n m : ℕ\n⊢ n = m')


def test_a_description_becomes_its_own_shape_and_only_when_given() -> None:
    goal = 'n m : ℕ\n⊢ n + m = m + n'

    assert 'description' not in _shapes(goal)
    assert _shapes(goal, 'addition is commutative')['description'] == 'addition is commutative'


def test_every_shape_says_how_it_was_taken_from_the_goal() -> None:
    """The ranking reports the query beside the goal; a reader who cannot tell
    which of three queries produced a premise cannot audit the ranking."""
    retrieval = importlib.import_module('hardy.retrieval')

    shapes = retrieval.search_queries('xs : List α\n⊢ xs.length = xs.length', 'lengths agree')

    assert all(shape.derived for shape in shapes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_retrieval_queries.py -v`
Expected: FAIL with `AttributeError: module 'hardy.retrieval' has no attribute 'search_queries'`

- [ ] **Step 3: Write the implementation**

Add `QueryShape` beside the other value types in `src/hardy/retrieval.py` (after `SourceIdentity`, around line 182):

```python
class QueryShape(FrozenModel):
    """One question taken from a goal, and how it was taken.

    A goal admits more than one useful question, and the sources are good at
    different ones: `#find` takes a result-type pattern and nothing else,
    Loogle takes that *or* a list of constants every result must mention, and
    a natural-language service takes a sentence. Asking all of them the same
    string -- which is what happened -- wasted two of the three.

    `derived` travels because the ranking reports the query beside the goal,
    and a reader looking at three queries needs to know which is which.
    """

    name: Literal["conclusion", "constants", "description"]
    query: str
    derived: str
```

and the functions immediately after `search_query` (around line 774):

```python
# A token that could name a Mathlib declaration rather than a local. Mathlib
# capitalises types and namespaces, so an uppercase initial or a dotted path
# is the signal; `n`, `xs` and `hf` are not. Textual, not elaborated: asking
# Lean would be exact and would cost a process per search, which is not a
# price this shape is worth. A wrong guess is survivable and already handled
# -- a query naming a non-constant comes back as a source that did not answer,
# which the provenance records.
CONSTANT_TOKEN = re.compile(
    r"[A-Z][A-Za-z0-9_']*(?:\.[A-Za-z0-9_']+)*|[a-z][A-Za-z0-9_']*(?:\.[A-Za-z0-9_']+)+"
)

# How many constants a query may name. Loogle intersects them, so a long list
# matches nothing; the first few carry the discrimination.
MAX_CONSTANTS = 4


def search_queries(goal: str, description: str | None = None) -> tuple[QueryShape, ...]:
    """Every question worth asking about this goal.

    Ordered by what the budget should spend on first: the conclusion pattern
    is the only thing the pinned source takes, so it leads.

    A shape is omitted rather than emitted empty. An empty query is not a
    question, and offering one would spend a source's admission -- which is
    charged against the run's whole retrieval budget -- on a call that cannot
    answer.
    """
    shapes: list[QueryShape] = []
    conclusion = search_query(goal)
    if conclusion:
        shapes.append(
            QueryShape(
                name="conclusion",
                query=conclusion,
                derived="the first goal's conclusion, with local names wildcarded",
            )
        )
    constants = _global_constants(goal)
    if constants:
        shapes.append(
            QueryShape(
                name="constants",
                query=", ".join(constants),
                derived="the global constants named anywhere in the goal, hypotheses included",
            )
        )
    if description and description.strip():
        shapes.append(
            QueryShape(
                name="description",
                query=description.strip()[:MAX_QUERY_CHARACTERS],
                derived="the caller's own sentence about what the goal is about",
            )
        )
    return tuple(shapes)


def _global_constants(goal: str) -> tuple[str, ...]:
    """The names in this goal that mean something outside it.

    Hypotheses included, which is the point: `hK : IsCompact K` names
    `IsCompact`, and the conclusion may not. The binder names themselves are
    excluded because they mean nothing outside this goal -- Loogle answers a
    query naming one with `Unknown identifier`, measured rather than predicted.

    Order is first-appearance rather than sorted, so the constants a reader
    sees first in the goal are the ones the query leads with.
    """
    lines = [line.strip() for line in goal.splitlines() if line.strip()]
    locals_ = _local_names(lines)
    seen: list[str] = []
    for line in lines:
        # The names side of a hypothesis binds locals; the type side is where
        # the constants are. `n m : ℕ` must not offer `n` or `m`.
        _, separator, tail = line.partition(" : ")
        text = tail if separator and not line.startswith(TURNSTILE) else line
        for token in CONSTANT_TOKEN.findall(text):
            if token.split(".")[0] in locals_ or token in locals_ or token in seen:
                continue
            seen.append(token)
    return tuple(seen[:MAX_CONSTANTS])
```

Add `Literal` to the `typing` import at the top of `retrieval.py` if it is not already there.

Note what the dot-notation test does *not* claim. `xs.reverse.length` is one
token whose head `xs` is a local, so it is dropped entirely; only the
hypothesis line's `List` survives. Recovering `List.reverse` would need the
type of `xs` and a model of how Lean elaborates projections. `List` alone is a
weak query, and it may turn out not to be worth the call -- Task 7 Step 8 is
where that gets decided, on evidence rather than on this paragraph.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest tests/unit/test_retrieval_queries.py -v`
Expected: 7 passed

If the compact-continuous case fails because `MAX_CONSTANTS` truncated `IsCompact` or `ContinuousOn` away — `Set`, `X` and `ℝ` appear earlier — raise `MAX_CONSTANTS` to 6 rather than reordering. A Loogle constant list is an intersection, and preferring later tokens would be a guess about which names discriminate.

- [ ] **Step 5: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS. `search_query` is untouched, so `tests/unit/test_retrieval.py` must be entirely unaffected — if anything there moved, revert and find out why.

- [ ] **Step 6: Commit**

```bash
git add src/hardy/retrieval.py tests/unit/test_retrieval_queries.py
git commit -m "Take more than one question from a goal, since the sources want different ones"
```

---

### Task 7: Fuse over (engine, shape) pairs, scoring each engine once

**Files:**
- Modify: `src/hardy/retrieval.py` — `PremiseSource` protocol, both sources (`accepts`, `query_for`), `SourceOutcome` (add `shape`), `PremiseRetriever.rank`/`_ranked`, `RANKER`
- Test: `tests/unit/test_retrieval_fusion.py` (create); update `tests/unit/test_retrieval.py`'s `FakeSource`

**Interfaces:**
- Consumes: `QueryShape`, `search_queries` from Task 6.
- Produces:
  - `PremiseSource.accepts -> frozenset[str]`; `PremiseSource.query_for(shape: QueryShape) -> str`.
  - `SourceOutcome.shape: str | None`.
  - `PremiseRetriever.rank(goal: str, limit: int = 10, description: str | None = None) -> PremiseRanking`.
  - `SourceRank.source` now carries `"<engine>/<shape>"`.
  - Task 8 consumes the `description` parameter; Task 9 adds a third source under the same protocol.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_retrieval_fusion.py`:

```python
"""Fusing over pairs of engine and question, rather than over engines alone.

The sources are asked different questions now, so "which source voted" is no
longer the same as "which query found it" -- and the score has to keep meaning
what it meant, which is that independent engines agreed.
"""

from __future__ import annotations

import importlib

from hardy.lean import DeclarationRecord


def _record(name: str) -> DeclarationRecord:
    return DeclarationRecord(name=name, signature=f'{name} : True')


class PerShapeSource:
    """A source with a scripted answer per shape, under the real protocol."""

    def __init__(self, identity, by_shape, accepts, worst_case_seconds=1.0):
        self.identity = identity
        self.accepts = frozenset(accepts)
        self.worst_case_seconds = worst_case_seconds
        self._by_shape = by_shape
        self.shapes_asked: list[str] = []
        self._current = None

    def query_for(self, shape) -> str:
        self._current = shape.name
        self.shapes_asked.append(shape.name)
        return shape.query

    def search(self, goal: str, limit: int):
        return tuple(_record(name) for name in self._by_shape.get(self._current, ())[:limit])


def _identity(retrieval, name, kind, pinned):
    return retrieval.SourceIdentity(name=name, kind=kind, corpus=f'{name} corpus', pinned=pinned)


def _retriever(retrieval, sources, seconds=300):
    domain = importlib.import_module('hardy.domain')
    return retrieval.PremiseRetriever(
        sources=sources,
        limits=domain.RunLimits(retrieval_seconds=seconds),
        clock=lambda: 0.0,
    )


GOAL = 'xs : List α\n⊢ xs.length = xs.length'


def test_a_source_is_asked_only_the_shapes_it_accepts() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    find = PerShapeSource(_identity(retrieval, 'lean-find', 'lean_search', True), {}, {'conclusion'})
    loogle = PerShapeSource(
        _identity(retrieval, 'loogle', 'loogle', False), {}, {'conclusion', 'constants'}
    )

    _retriever(retrieval, [find, loogle]).rank(GOAL, limit=5)

    assert find.shapes_asked == ['conclusion']
    assert loogle.shapes_asked == ['conclusion', 'constants']


def test_an_engine_answering_two_shapes_still_votes_once() -> None:
    """Otherwise the engine with the most shapes outweighs the pinned one.

    A constants query returns a superset of what the conclusion query
    returned often enough that double-counting would systematically promote
    Loogle -- and the pinned source's rendering of a signature is the one the
    model should be reading.
    """
    retrieval = importlib.import_module('hardy.retrieval')
    find = PerShapeSource(
        _identity(retrieval, 'lean-find', 'lean_search', True),
        {'conclusion': ['Other', 'List.length_reverse']},
        {'conclusion'},
    )
    loogle = PerShapeSource(
        _identity(retrieval, 'loogle', 'loogle', False),
        {'conclusion': ['List.length_reverse'], 'constants': ['List.length_reverse']},
        {'conclusion', 'constants'},
    )

    ranking = _retriever(retrieval, [find, loogle]).rank(GOAL, limit=5)

    premise = next(item for item in ranking.premises if item.name == 'List.length_reverse')
    assert premise.score == 1 / (retrieval.RRF_K + 2) + 1 / (retrieval.RRF_K + 1)


def test_every_pair_that_voted_is_still_named_even_though_it_scored_once() -> None:
    """The score collapses; the record does not. A reader auditing a ranking
    needs to see that two different questions found the same premise."""
    retrieval = importlib.import_module('hardy.retrieval')
    loogle = PerShapeSource(
        _identity(retrieval, 'loogle', 'loogle', False),
        {'conclusion': ['List.length_reverse'], 'constants': ['List.length_reverse']},
        {'conclusion', 'constants'},
    )

    ranking = _retriever(retrieval, [loogle]).rank(GOAL, limit=5)

    assert {item.source for item in ranking.premises[0].ranks} == {
        'loogle/conclusion',
        'loogle/constants',
    }


def test_each_pair_names_the_shape_it_was_asked() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    loogle = PerShapeSource(
        _identity(retrieval, 'loogle', 'loogle', False), {}, {'conclusion', 'constants'}
    )

    ranking = _retriever(retrieval, [loogle]).rank(GOAL, limit=5)

    assert [outcome.shape for outcome in ranking.provenance.sources] == ['conclusion', 'constants']


def test_a_shape_no_source_accepts_costs_nothing_and_is_not_reported() -> None:
    """A description with no natural-language engine configured is not a
    source that failed -- it is a question nobody was asked."""
    retrieval = importlib.import_module('hardy.retrieval')
    find = PerShapeSource(_identity(retrieval, 'lean-find', 'lean_search', True), {}, {'conclusion'})

    ranking = _retriever(retrieval, [find]).rank(GOAL, limit=5, description='lengths agree')

    assert [outcome.shape for outcome in ranking.provenance.sources] == ['conclusion']
    assert ranking.complete


def test_one_pair_failing_leaves_the_other_pair_of_the_same_engine_intact() -> None:
    retrieval = importlib.import_module('hardy.retrieval')

    class HalfBroken(PerShapeSource):
        def search(self, goal, limit):
            if self._current == 'conclusion':
                raise retrieval.RetrievalError('loogle: 503')
            return super().search(goal, limit)

    loogle = HalfBroken(
        _identity(retrieval, 'loogle', 'loogle', False),
        {'constants': ['List.length_reverse']},
        {'conclusion', 'constants'},
    )

    ranking = _retriever(retrieval, [loogle]).rank(GOAL, limit=5)

    assert [premise.name for premise in ranking.premises] == ['List.length_reverse']
    assert not ranking.complete
    assert any('503' in (outcome.detail or '') for outcome in ranking.provenance.sources)


def test_the_ranker_names_a_version_that_moved_when_the_fusion_did() -> None:
    """A ranking recorded under the old constant fused differently and would
    not replay."""
    retrieval = importlib.import_module('hardy.retrieval')

    assert retrieval.RANKER != 'rrf-60'
```

Check the current value of `RANKER` before writing the last test — `grep -n '^RANKER' src/hardy/retrieval.py` — and assert against whatever it is today.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_retrieval_fusion.py -v`
Expected: FAIL — `query_for` receives a `QueryShape` the current sources cannot use, and `SourceOutcome` has no `shape`.

- [ ] **Step 3: Add `shape` to the outcome and `accepts` to the protocol**

Add to `SourceOutcome` (around line 183), beside `query`:

```python
    shape: str | None = None
```

In the `PremiseSource` protocol (around line 358), replace `query_for` and add `accepts`:

```python
    @property
    def accepts(self) -> frozenset[str]:
        """The shape names this source can be asked.

        A source that cannot take a question must not be spent on it: the
        admission check charges the run's budget before the call, so a query
        the endpoint will refuse costs exactly as much as one it will answer.
        """
        ...

    def query_for(self, shape: QueryShape) -> str:
        """This source's spelling of one shape."""
        ...
```

On the `#find` source (`LeanSearchSource`, renamed in Task 9):

```python
    @property
    def accepts(self) -> frozenset[str]:
        # `#find t` matches the result type against `t`. It takes no constant
        # list and no sentence, so the conclusion pattern is the whole of what
        # it can be asked.
        return frozenset({"conclusion"})

    def query_for(self, shape: QueryShape) -> str:
        # `#find` does not take Loogle's `⊢` conclusion filter -- passing it
        # through failed the pinned source on exactly the input this tool
        # exists to take. The two spellings mean the same search.
        query = shape.query
        return query[len(TURNSTILE) :].strip() if query.startswith(TURNSTILE) else query
```

On `LoogleSource`:

```python
    @property
    def accepts(self) -> frozenset[str]:
        # Loogle reads `⊢ p` as a conclusion filter and a comma-separated list
        # of names as "every result mentions all of these". The second is its
        # strongest mode and was never issued.
        return frozenset({"conclusion", "constants"})

    def query_for(self, shape: QueryShape) -> str:
        return shape.query
```

- [ ] **Step 4: Rewrite the fusion loop**

Replace `PremiseRetriever.rank`:

```python
    def rank(
        self, goal: str, limit: int = 10, description: str | None = None
    ) -> PremiseRanking:
        if not 1 <= len(goal) <= MAX_GOAL_CHARACTERS:
            raise ValueError(f"a retrieval goal must be 1 to {MAX_GOAL_CHARACTERS} characters")
        shapes = tuple(
            shape
            for shape in search_queries(goal, description)
            if 1 <= len(shape.query) <= MAX_QUERY_CHARACTERS
        )
        if not shapes:
            raise ValueError(
                f"no searchable query of at most {MAX_QUERY_CHARACTERS} characters could be "
                "taken from this goal"
            )
        if not 1 <= limit <= 50:
            raise ValueError("a premise ranking holds between 1 and 50 premises")

        with self._admission:
            return self._ranked(goal, shapes, limit)
```

In `_ranked`, change the signature to `(self, goal: str, shapes: tuple[QueryShape, ...], limit: int)` and replace the source loop with a pair loop. Everything else in the body stays as it is except the five marked points:

```python
        # name -> {engine: best rank}, beside the existing `found`
        best: dict[str, dict[str, int]] = {}

        pairs = [
            (source, shape)
            for source in self._sources
            for shape in shapes
            if shape.name in source.accepts
        ]

        for source, shape in pairs:
            identity = source.identity
            label = f"{identity.name}/{shape.name}"
            # (1) admission check unchanged, but every `SourceOutcome(...)`
            #     construction in this loop gains `shape=shape.name`
            asked = source.query_for(shape)
            # (2) search, timing and outcome recording unchanged
            for record in results:
                # (3) dedup within the pair unchanged
                found.setdefault(record.name, []).append(
                    SourceRank(source=label, rank=position)
                )
                engines = best.setdefault(record.name, {})
                previous = engines.get(identity.name)
                if previous is None or position < previous:
                    engines[identity.name] = position
                # (4) signature preference unchanged

        premises = [
            RankedPremise(
                name=name,
                signature=records[name].signature,
                # (5) One vote per engine, at its best rank across the shapes
                # it answered. Fusion earns its keep by rewarding
                # *independent* sources agreeing; a constants query returns a
                # superset of the conclusion query often enough that a vote
                # per shape would quietly promote whichever engine takes the
                # most questions.
                score=sum(1.0 / (RRF_K + rank) for rank in best[name].values()),
                ranks=tuple(ranks),
            )
            for name, ranks in found.items()
        ]
```

Then the two fields that name the question. **This cannot be done by hashing
the shape list into the existing `query_sha256`**: the validator at
`retrieval.py:322` recomputes that digest from `self.query` and would raise
`ValueError` on every ranking constructed. And keeping one of three questions
in a field named `query` would be a smaller lie told more quietly. So the pair
is replaced.

On `RetrievalProvenance`, `query_sha256: str` becomes:

```python
    queries_sha256: str
```

On `PremiseRanking`, `query: str` becomes:

```python
    queries: tuple[QueryShape, ...]
```

with one canonical rendering both sides agree on, beside `premises_digest`:

```python
def queries_digest(shapes: Sequence[QueryShape]) -> str:
    """The questions a ranking asked, in the order it asked them.

    Shape name as well as text, because `⊢ p` sent as a conclusion filter and
    the same string sent as a constant list are two different searches, and a
    digest that could not tell them apart would let one ranking's record
    validate against the other's questions.
    """
    return hashlib.sha256(
        "\n".join(f"{shape.name}\t{shape.query}" for shape in shapes).encode("utf-8")
    ).hexdigest()
```

and in `claims_match_the_record_they_are_taken_over`, replace the `query` check:

```python
        if queries_digest(self.queries) != self.provenance.queries_sha256:
            raise ValueError("queries do not hash to the queries_sha256 its provenance records")
```

The construction is then `queries=shapes` and
`queries_sha256=queries_digest(shapes)`. A reader still meets the conclusion
query first, because that is the order `search_queries` returns.

Add the test that pins it, since this is the failure that would have reached
every single call:

```python
def test_a_ranking_that_asked_three_questions_records_all_three() -> None:
    """The validator recomputes this digest on every read. Hashing the
    questions while naming only one of them raised on construction."""
    retrieval = importlib.import_module('hardy.retrieval')
    loogle = PerShapeSource(
        _identity(retrieval, 'loogle', 'loogle', False), {}, {'conclusion', 'constants'}
    )

    ranking = _retriever(retrieval, [loogle]).rank(GOAL, limit=5, description='lengths agree')

    assert [shape.name for shape in ranking.queries] == ['conclusion', 'constants', 'description']
    assert retrieval.PremiseRanking.model_validate_json(ranking.model_dump_json())
```

Bump the constant near line 104:

```python
RANKER = "rrf-60-per-engine-v2"
```

- [ ] **Step 5: Update the existing fusion tests**

`tests/unit/test_retrieval.py`'s `FakeSource.query_for` takes a string. Give it the new protocol — an `accepts` parameter defaulting to all three shapes, so existing constructions keep working:

```python
    def __init__(self, identity, results=(), error=None, worst_case_seconds=5.0, seconds=1.0,
                 accepts=frozenset({'conclusion', 'constants', 'description'})):
        ...
        self.accepts = frozenset(accepts)

    def query_for(self, shape) -> str:
        return shape.query
```

Then fix the assertions that read source labels — `('lean-find', 2)` becomes `('lean-find/conclusion', 2)`, and so on.

**Do not** relax an assertion to make it pass. Each of those tests pins a property named in its own docstring; if one genuinely no longer holds, stop and say so rather than editing it.

- [ ] **Step 6: Run the retrieval tests**

Run: `uv run --extra test pytest tests/unit/test_retrieval.py tests/unit/test_retrieval_fusion.py tests/unit/test_retrieval_queries.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS. `tests/unit/test_retrieval_wiring.py` and `tests/integration/test_mcp_stdio.py` both exercise the tool path and may need the same `query_for` update.

- [ ] **Step 8: Re-measure**

```bash
uv run python eval/run_premise_eval.py
```

Cassettes are keyed per (engine, query), so the new constants queries will be **misses** until re-recorded. Re-record with `--live` and compare against the baseline in `eval/README.md`. Record the new numbers under `## After the query ladder`.

**If the constants shape wins no case the conclusion shape loses, say so and propose removing it** rather than keeping complexity the measurement does not support. That is what the per-shape metric is for.

- [ ] **Step 9: Commit**

```bash
git add src/hardy/retrieval.py tests/unit/ eval/
git commit -m "Ask each source the question it can answer, and let each engine vote once"
```

---

### Task 8: Carry a description from the model to the ranking

**Files:**
- Modify: `src/hardy/mcp_server.py:57` and `mcp_server.py:322`, `staged.py:96` and `staged.py:260`, `search_tools.py`, `chat.py` (`_search_tool`), `prompts/staged/proof.md.j2`
- Test: `tests/unit/test_retrieval_wiring.py` (extend)

**Interfaces:**
- Consumes: `PremiseRetriever.rank(..., description=...)` from Task 7.
- Produces: `description: str | None` accepted on every `rank_premises` surface.

- [ ] **Step 1: Write the failing test**

First read how the runtime stores its retriever: `grep -n 'retriever' src/hardy/mcp_server.py`. Then append to `tests/unit/test_retrieval_wiring.py`:

```python
def test_a_description_the_model_wrote_reaches_the_retriever() -> None:
    """Hardy cannot turn a goal into English; the model can, and it is the one
    component that already knows what the goal is about."""
    import importlib

    mcp_server = importlib.import_module('hardy.mcp_server')
    seen = {}

    class Retriever:
        def rank(self, goal, limit=10, description=None):
            seen.update(goal=goal, limit=limit, description=description)
            raise RuntimeError('stop here; the arguments are what is being tested')

    runtime = mcp_server.LeanToolRuntime.__new__(mcp_server.LeanToolRuntime)
    runtime._retriever = Retriever()

    try:
        runtime.rank_premises('⊢ True', 5, 'the trivial proposition')
    except RuntimeError:
        pass

    assert seen == {'goal': '⊢ True', 'limit': 5, 'description': 'the trivial proposition'}
```

Match `_retriever` to the real attribute name the grep showed.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_retrieval_wiring.py -v`
Expected: FAIL with a `TypeError` about too many positional arguments.

- [ ] **Step 3: Thread it through**

`src/hardy/mcp_server.py:57`:

```python
    def rank_premises(
        self, goal: str, limit: int, description: str | None = None
    ) -> PremiseRanking:
        return self._retriever.rank(goal, limit, description=description)
```

`src/hardy/mcp_server.py:322` — read lines 322-332 first and preserve the existing `_configured()` / `bound_ranking` structure, adding:

```python
def rank_premises(goal: str, limit: int = 10, description: str = "") -> PremiseRanking:
```

and passing `description or None` through.

`src/hardy/staged.py:96`, in the `rank_premises` spec:

```python
            "description": "Rank the declarations most likely to help with one goal, fusing Lean's own search with Loogle and a natural-language search. Paste the goal exactly as Lean printed it, hypotheses and all. Pass `description` — one English sentence saying what the goal is about — whenever you can; it is the only thing the natural-language search is given. The answer names every source it asked and says whether the ranking can be replayed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "description": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
```

`src/hardy/staged.py:260` — read lines 260-263 first and preserve whatever `bound_ranking` wrapping is there:

```python
                elif name == "rank_premises":
                    result = lean_runtime.rank_premises(
                        str(arguments["goal"]),
                        int(arguments.get("limit") or 10),
                        str(arguments.get("description") or "") or None,
                    )
```

`src/hardy/search_tools.py`:

```python
    def rank_premises(
        self, goal: str, limit: int = 10, description: str | None = None
    ) -> ToolResult:
        return self._answer(lambda: self.retriever.rank(goal, limit, description=description))
```

`src/hardy/chat.py`, in `_search_tool`:

```python
            return self.search.rank_premises(
                str(arguments["goal"]),
                int(arguments.get("limit") or 10),
                str(arguments.get("description") or "") or None,
            )
```

Task 3's `FakeSearch` takes two arguments and its assertion expects two, so
this call breaks it. Widen both in `tests/test_chat_search.py`:

```python
    def rank_premises(self, goal: str, limit: int = 10, description: str | None = None):
        self.calls.append(
            ('rank_premises', {'goal': goal, 'limit': limit, 'description': description})
        )
        return ToolResult(True, json.dumps({'premises': [{'name': 'Nat.add_comm'}]}))
```

and the assertion in `test_a_ranking_asked_for_reaches_the_search_runtime`:

```python
    assert search.calls == [
        ('rank_premises', {'goal': '⊢ _ + _ = _ + _', 'limit': 5, 'description': None})
    ]
```

Add a test there for the new argument, since the chat surface is where a
description is most likely to be written:

```python
def test_a_description_the_model_wrote_reaches_the_search_runtime(session_factory) -> None:
    search = FakeSearch()
    session = session_factory(search=search, search_detail='Mathlib abcdef in /lean')

    session._tool('rank_premises', {'goal': '⊢ True', 'description': 'the trivial proposition'})

    assert search.calls[0][1]['description'] == 'the trivial proposition'
```

- [ ] **Step 4: Run the tests**

Run: `uv run --extra test pytest tests/unit/test_retrieval_wiring.py tests/test_chat_search.py -v`
Expected: PASS

- [ ] **Step 5: Update the proving prompt**

In `src/hardy/prompts/staged/proof.md.j2`, replace the `rank_premises` bullet:

```
- rank_premises when you do not know what the relevant lemma is called. Paste
  the goal exactly as Lean printed it, hypotheses and all — the hypotheses are
  searched, not discarded — and pass description, one English sentence saying
  what the goal is about, which is the only thing the natural-language search
  receives. Reach for it before guessing a name: Mathlib is much larger than
  your memory of it. Its results are a heuristic ordering, not evidence:
  confirm any name it offers with lean_inspect_declarations before relying on
  it.
```

- [ ] **Step 6: Run the whole suite and commit**

```bash
uv run --extra test pytest
git add src/hardy/ tests/
git commit -m "Let the model say what the goal is about, since it is the only one who knows"
```

---

# Slice 4 — A natural-language engine

### Task 9: Rename the `#find` source, then add LeanSearch

**Files:**
- Modify: `src/hardy/retrieval.py` (rename `LeanSearchSource` → `LeanFindSource`; add `LeanSearchNetSource`; extend `build_retriever`)
- Modify: `README.md`, `FEATURES.md`
- Test: `tests/unit/test_retrieval_leansearch.py` (create)

**Interfaces:**
- Consumes: `QueryShape`, `PremiseSource` from Task 7; `_fetch_url`, `RetrievalError`, `RetrievalTransportError`, `MAX_HITS`, `MAX_RESPONSE_BYTES`, `DECLARATION_NAME`.
- Produces: `LeanFindSource` (renamed) and `LeanSearchNetSource(endpoint=..., timeout=..., fetch=...)` with `identity.name == 'leansearch'`, `identity.kind == 'leansearch'`, `pinned=False`, `accepts == frozenset({'description'})`.

- [ ] **Step 1: Do the rename first, alone**

```bash
grep -rn 'LeanSearchSource' src tests
```

Rename every occurrence to `LeanFindSource`. Nothing else in this step.

Run: `uv run --extra test pytest`
Expected: PASS

```bash
git add -A && git commit -m "Call the source that runs #find by the name its identity already uses"
```

A rename and a new class sharing a name in one commit is how a reviewer misses which is which.

- [ ] **Step 2: Confirm the service contract before writing against it**

The parameter and field names below are taken from LeanSearchClient's usage and **must be checked against the running service first**:

```bash
curl -s 'https://leansearch.net/search?query=addition+is+commutative&num_results=3' | head -c 2000
```

Write the test payload and the parser against what that actually returns. If the endpoint has moved or the shape differs, that is a finding to report, not something to guess around — and if the service is unreachable from this environment, say so and stop rather than shipping a parser nothing has exercised.

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_retrieval_leansearch.py`, with `PAYLOAD` replaced by what Step 2 observed:

```python
"""A natural-language engine, and what it can and cannot promise.

The gap the module documented at the top: no source could take a sentence, so
a model that knew the mathematics but not the pattern had nothing to ask.
"""

from __future__ import annotations

import importlib
import json

import pytest

PAYLOAD = json.dumps(
    [
        {'formal_name': 'Nat.add_comm', 'formal_type': '∀ (n m : ℕ), n + m = m + n'},
        {'formal_name': 'add_comm', 'formal_type': '∀ (a b : α), a + b = b + a'},
    ]
).encode('utf-8')


def _source(retrieval, body, **kwargs):
    def fetch(url, timeout):
        fetch.url = url
        return body

    source = retrieval.LeanSearchNetSource(fetch=fetch, **kwargs)
    source.fetch = fetch
    return source


def _shape(retrieval, text):
    return retrieval.QueryShape(name='description', query=text, derived='the caller sentence')


def test_it_takes_a_sentence_and_nothing_else() -> None:
    retrieval = importlib.import_module('hardy.retrieval')

    assert _source(retrieval, PAYLOAD).accepts == frozenset({'description'})


def test_the_sentence_is_what_reaches_the_service() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    source = _source(retrieval, PAYLOAD)

    source.search(source.query_for(_shape(retrieval, 'addition is commutative')), 5)

    assert 'addition' in source.fetch.url


def test_results_come_back_as_declarations_with_their_types() -> None:
    retrieval = importlib.import_module('hardy.retrieval')

    found = _source(retrieval, PAYLOAD).search('addition is commutative', 5)

    assert [record.name for record in found] == ['Nat.add_comm', 'add_comm']
    assert 'n + m = m + n' in found[0].signature


def test_it_is_never_pinned_and_says_so_in_its_corpus() -> None:
    """A live service tracking a Mathlib it does not name. A ranking it shaped
    cannot be replayed, and `PremiseRanking.reproducible` must see that."""
    retrieval = importlib.import_module('hardy.retrieval')

    identity = _source(retrieval, PAYLOAD).identity

    assert identity.pinned is False
    assert identity.corpus


def test_a_body_that_is_not_json_is_a_source_that_failed() -> None:
    """Not one that found nothing. An empty list reads as "no such lemma",
    which is the one thing a broken response does not mean."""
    retrieval = importlib.import_module('hardy.retrieval')

    with pytest.raises(retrieval.RetrievalError):
        _source(retrieval, b'<html>502</html>').search('anything', 5)


def test_a_response_too_large_to_be_a_result_list_is_refused_before_parsing() -> None:
    retrieval = importlib.import_module('hardy.retrieval')
    huge = b'[' + b'{"formal_name": "A"},' * 400_000 + b'{"formal_name": "B"}]'

    with pytest.raises(retrieval.RetrievalError, match='too large'):
        _source(retrieval, huge).search('anything', 5)


def test_the_default_source_set_includes_it_after_the_pinned_one() -> None:
    """Order is what the budget spends on first, and the pinned source's
    rendering of a signature is the one the model should read."""
    retrieval = importlib.import_module('hardy.retrieval')
    domain = importlib.import_module('hardy.domain')

    class NoService:
        environment = None
        lean_project = None

    retriever = retrieval.build_retriever(NoService(), domain.RunLimits())

    names = [source.identity.name for source in retriever._sources]
    assert 'leansearch' in names
    assert names.index('lean-find') < names.index('leansearch')
```

If `identity` on the `#find` source raises against `NoService`, read the source list without touching identities — compare class names instead.

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_retrieval_leansearch.py -v`
Expected: FAIL with `AttributeError: module 'hardy.retrieval' has no attribute 'LeanSearchNetSource'`

- [ ] **Step 5: Write the implementation**

Add near the other endpoint constants (around line 59):

```python
# LeanSearch's public instance. Configurable for the same reason Loogle's is:
# a project that cares about reproducibility will want its own against a
# pinned Mathlib.
DEFAULT_LEANSEARCH_ENDPOINT = "https://leansearch.net/search"
DEFAULT_LEANSEARCH_TIMEOUT = 30.0
```

Add the class after `LoogleSource`:

```python
class LeanSearchNetSource:
    """LeanSearch, over its JSON API.

    The one source that takes a sentence. `#find` and Loogle both want the
    shape of the answer; this wants a description of the question, which is
    what a model has when it knows the mathematics and not the library.

    Unpinned, and not incidentally: the public instance follows Mathlib and
    reports no revision, so nothing here can say which corpus answered. The
    reproducible version of this capability is the embedding index `DESIGN.md`
    describes, which is a different project.

    Named `LeanSearchNetSource` rather than `LeanSearchSource` because that
    name meant Lean's own `#find` until this landed, and reusing it with a
    changed meaning is a diff a reviewer skims past.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_LEANSEARCH_ENDPOINT,
        *,
        timeout: float = DEFAULT_LEANSEARCH_TIMEOUT,
        fetch: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._fetch = fetch or _fetch_url

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(
            name="leansearch", kind="leansearch", corpus=self._endpoint, pinned=False
        )

    @property
    def accepts(self) -> frozenset[str]:
        return frozenset({"description"})

    @property
    def worst_case_seconds(self) -> float:
        """Twice the deadline, for the reason `LoogleSource` states at length:
        the read the deadline lands inside is bounded only by the socket
        timeout, so one more socket operation can elapse after it passes."""
        return 2 * self._timeout

    def query_for(self, shape: QueryShape) -> str:
        return shape.query

    def search(self, goal: str, limit: int) -> tuple[DeclarationRecord, ...]:
        url = f"{self._endpoint}?{urlencode({'query': goal, 'num_results': min(limit, MAX_HITS)})}"
        try:
            body = self._fetch(url, self._timeout)
        except RetrievalError:
            raise
        except Exception as error:  # noqa: BLE001 - any transport failure is one outcome
            raise RetrievalTransportError(f"LeanSearch request failed: {error}") from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise RetrievalError(f"LeanSearch response too large: over {MAX_RESPONSE_BYTES} bytes")
        try:
            # Strictly, for the reason `LoogleSource.search` gives: a lenient
            # decode hands back a signature that reads as ordinary Lean while
            # naming something else.
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise RetrievalError(f"LeanSearch response was not valid UTF-8: {error}") from error
        except json.JSONDecodeError as error:
            raise RetrievalError(f"LeanSearch response was not JSON: {error}") from error
        results = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(results, list):
            raise RetrievalError("LeanSearch response carried no result list")
        return _records_from_leansearch(results, limit)


def _records_from_leansearch(results: list, limit: int) -> tuple[DeclarationRecord, ...]:
    """Bounded on the way in and again per field, like Loogle's hits.

    Every value is data off the internet. A name longer than a Lean
    declaration could be is discarded rather than truncated: a cut name is a
    different name, and would crowd genuine premises out of the observation
    budget on its way to meaning nothing.
    """
    records: list[DeclarationRecord] = []
    for item in results[:MAX_HITS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("formal_name") or item.get("name") or "")
        if not DECLARATION_NAME.fullmatch(name):
            continue
        signature = str(item.get("formal_type") or item.get("type") or "")
        records.append(
            DeclarationRecord(
                name=name,
                signature=f"{name} : {signature}"[:MAX_SIGNATURE_CHARACTERS],
                source_file=None,
            )
        )
        if len(records) >= limit:
            break
    return tuple(records)
```

Read `_records_from_hits` first and reuse whatever name-length and signature-length constants it uses rather than the names above.

Extend `build_retriever`:

```python
def build_retriever(service: object, limits: RunLimits) -> PremiseRetriever:
    """The default source set, in the order the budget should spend on them.

    The pinned environment goes first, which decides two things when the
    budget is tight: its rendering of a signature is the one a model reads,
    and the source dropped for want of time is an unpinned one. LeanSearch
    goes last because it answers only a shape the caller may not have
    supplied -- with no description there is no pair to run, and it costs
    nothing.
    """
    return PremiseRetriever(
        sources=(
            LeanFindSource(service, limits=limits),
            LoogleSource(),
            LeanSearchNetSource(),
        ),
        limits=limits,
    )
```

`SourceKind` is `Literal["lean_search", "loogle", "embedding"]` at
`retrieval.py:128`; add `"leansearch"`. This is load-bearing, not cosmetic:
`retrieval.py:1005` keys local-signature precedence on
`identity.kind == "lean_search"`, so reusing that kind would let an unpinned
remote rendering override the signature the model's own Lean is about to
elaborate. Add a test for it to `tests/unit/test_retrieval_leansearch.py`:

```python
def test_a_remote_signature_never_displaces_the_pinned_environments() -> None:
    """Precedence is keyed on the kind, so a new remote source sharing
    `lean_search` would silently take the local authority's place."""
    retrieval = importlib.import_module('hardy.retrieval')

    assert _source(retrieval, PAYLOAD).identity.kind != 'lean_search'
```

- [ ] **Step 6: Run the tests**

Run: `uv run --extra test pytest tests/unit/test_retrieval_leansearch.py -v`
Expected: 7 passed

- [ ] **Step 7: Re-measure and update the docs**

```bash
HARDY_LEAN_PROJECT=<path> uv run python eval/run_premise_eval.py --live
uv run python eval/run_premise_eval.py
```

Add the numbers to `eval/README.md` under `## After LeanSearch`. Then:

```bash
grep -n 'Loogle' README.md FEATURES.md
```

Both name two sources and must now name three, saying that the natural-language source is unpinned and that a ranking it shaped cannot be replayed.

- [ ] **Step 8: Commit**

```bash
git add src/hardy/retrieval.py tests/unit/test_retrieval_leansearch.py eval/ README.md FEATURES.md
git commit -m "Give the model a search that takes the sentence it can already write"
```

---

# Slice 5 — Lean's own tactic search

### Task 10: Run a menu of search tactics against a statement

**Files:**
- Modify: `src/hardy/lean.py` (add `TACTIC_MENU`, `DEFAULT_TACTICS`, `TRY_THIS`, `TacticAttempt`, `TacticSearch`, `LeanService.try_tactics`, `_first_error`)
- Modify: `src/hardy/domain.py` (add `RunLimits.tactic_search_seconds`, bump `RunManifest.schema_version` to 4)
- Test: `tests/unit/test_lean_try_tactics.py` (create)

**Interfaces:**
- Consumes: `LeanService.check_scratch`, `LeanCheckResult`, `RunLimits`.
- Produces:
  - `TACTIC_MENU: frozenset[str]`; `DEFAULT_TACTICS: tuple[str, ...]` = `("exact?", "apply?", "hint", "simp?")`.
  - `TacticAttempt(FrozenModel)`: `tactic`, `closed`, `suggestions`, `detail`, `seconds`.
  - `TacticSearch(FrozenModel)`: `statement`, `statement_sha256`, `attempts`, `closed_by`, `budget_exhausted`, `seconds_spent`.
  - `LeanService.try_tactics(statement, tactics=None, stop_on_first=True) -> TacticSearch`.
  - Task 11 consumes all of these; Task 12 consumes `TacticSearch`.

- [ ] **Step 1: Read the shapes the test must build**

```bash
sed -n '71,116p' src/hardy/lean.py
grep -n 'class ProcessResult' -A 12 src/hardy/process.py
grep -rn 'LeanService(' tests/ | head
```

Write `_service` and `_result` in the test against what those show, not against the sketch below.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_lean_try_tactics.py`:

```python
"""Lean's own search tactics, run for a model that cannot name the lemma.

Unlike `#find`, these see the local context, and what they return is a term
Lean elaborated. That is a much stronger signal than a ranked name -- and
still not a proof of anything Hardy grades, because the statement they were
run against is one the model wrote.
"""

from __future__ import annotations

import hashlib
import importlib

SUGGESTION = 'Try this: exact Nat.add_comm n m'


def test_the_first_tactic_that_closes_the_statement_ends_the_search() -> None:
    """A weak model wants an answer, not a survey. `stop_on_first` is the
    default for that reason, and each tactic is a whole Lean process."""
    lean = importlib.import_module('hardy.lean')
    service = _service(lean, {
        'exact?': _result(lean, success=True, messages=[SUGGESTION]),
        'apply?': _result(lean, success=True),
    })

    found = service.try_tactics('theorem tmp (n m : ℕ) : n + m = m + n')

    assert found.closed_by == 'exact?'
    assert [attempt.tactic for attempt in found.attempts] == ['exact?']


def test_the_suggestion_is_parsed_out_of_what_lean_printed() -> None:
    lean = importlib.import_module('hardy.lean')
    service = _service(lean, {'exact?': _result(lean, success=True, messages=[SUGGESTION])})

    found = service.try_tactics('theorem tmp (n m : ℕ) : n + m = m + n', tactics=['exact?'])

    assert found.attempts[0].suggestions == ('exact Nat.add_comm n m',)


def test_a_tactic_that_failed_is_recorded_rather_than_dropped() -> None:
    """A model that cannot see what was already tried tries it again."""
    lean = importlib.import_module('hardy.lean')
    service = _service(lean, {
        'exact?': _result(lean, success=False, messages=['no applicable lemma']),
        'hint': _result(lean, success=True, messages=[SUGGESTION]),
    })

    found = service.try_tactics('theorem tmp : True', tactics=['exact?', 'hint'])

    assert [attempt.closed for attempt in found.attempts] == [False, True]


def test_a_tactic_outside_the_menu_is_refused_before_lean_runs() -> None:
    """Not a new capability -- the model can write any tactic into
    lean_check_scratch -- but this tool means "Lean's own search", and an
    unbounded menu makes its cost unbounded too."""
    import pytest

    lean = importlib.import_module('hardy.lean')
    service = _service(lean, {})

    with pytest.raises(ValueError, match='sorry'):
        service.try_tactics('theorem tmp : True', tactics=['sorry'])


def test_the_statement_it_proved_something_about_travels_with_the_answer() -> None:
    """The model wrote this statement, and it may not be the goal it is stuck
    on. A result that did not carry it would read as a proof of something
    nobody checked."""
    lean = importlib.import_module('hardy.lean')
    statement = 'theorem tmp : True'
    service = _service(lean, {'exact?': _result(lean, success=True)})

    found = service.try_tactics(statement, tactics=['exact?'])

    assert found.statement == statement
    assert found.statement_sha256 == hashlib.sha256(statement.encode('utf-8')).hexdigest()


def test_the_budget_is_spent_across_the_run_rather_than_refilled_per_call() -> None:
    """Otherwise a model calling the tool twice gets the whole allowance
    twice, and `budget_exhausted` never fires however many Lean processes a
    run launches. The same defect `PremiseRetriever` documents about its own
    budget."""
    lean = importlib.import_module('hardy.lean')
    service = _service(
        lean,
        {'exact?': _result(lean, success=False, duration_ms=30_000)},
        tactic_search_seconds=30,
    )

    service.try_tactics('theorem tmp : True', tactics=['exact?'])
    second = service.try_tactics('theorem tmp : True', tactics=['exact?'])

    assert second.budget_exhausted
    assert second.attempts == ()


def test_the_budget_refuses_the_next_tactic_rather_than_interrupting_one() -> None:
    """The same discipline the official checks and premise retrieval follow."""
    lean = importlib.import_module('hardy.lean')
    service = _service(
        lean,
        {'exact?': _result(lean, success=False), 'apply?': _result(lean, success=False)},
        tactic_search_seconds=0,
    )

    found = service.try_tactics('theorem tmp : True', tactics=['exact?', 'apply?'])

    assert found.budget_exhausted
    assert found.attempts == ()


def test_a_statement_carrying_its_own_proof_is_refused() -> None:
    """This tool supplies the proof body. One already there would be silently
    replaced, and the result would describe a search nobody ran."""
    import pytest

    lean = importlib.import_module('hardy.lean')
    service = _service(lean, {})

    with pytest.raises(ValueError):
        service.try_tactics('theorem tmp : True := trivial')
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_lean_try_tactics.py -v`
Expected: FAIL with `AttributeError: 'LeanService' object has no attribute 'try_tactics'`

- [ ] **Step 4: Add the limit and bump the schema**

In `src/hardy/domain.py`, after `retrieval_seconds` (line 56):

```python
    # Tactic search is metered separately from premise retrieval, not out of
    # the same purse. They answer different questions and a model leaning on
    # one must not starve the other -- a run that spent its whole budget on
    # `exact?` would arrive at the goal it could not close with nothing left
    # to search for the lemma. Each tactic is a whole Lean process, so this
    # buys roughly ten at `lean_process_seconds`.
    tactic_search_seconds: int = 300
```

`RunManifest` gains **both** version-4 fields here, in one commit: the limit,
and `automation: AutomationAttribution | None = None` with the
`AutomationAttribution` type from Task 12. Adding the field in Task 10 and its
value in Task 12 keeps one schema version describing one shape. Splitting them
would leave a reader built at this commit rejecting a Task 12 manifest that
claims the same version 4 — `extra="forbid"` makes every added field a breaking
read, which is the whole reason the version moves.

Extend the note at `domain.py:229` and bump the version:

```python
    # ... 3 added `limits.retrieval_seconds`, so a version-2 reader would
    # reject every manifest written since premise retrieval landed; leaving
    # the version at 2 would have let one number name two incompatible
    # shapes. 4 added `limits.tactic_search_seconds` and `automation`, for
    # the same reason.
    schema_version: Literal[4] = 4
```

Then find every fixture that pins version 3:

```bash
grep -rn 'schema_version' tests/ src/ | grep -v domain.py
```

- [ ] **Step 5: Write the implementation**

Add to `src/hardy/lean.py`, after `DeclarationSearch` (around line 116):

```python
# What `try_tactics` will run. An allowlist rather than free text, and the
# reason is not capability: a model can already write any tactic into
# `lean_check_scratch`. It is meaning and cost. This tool is "Lean's own
# search", each entry costs a whole Lean process, and a menu the caller
# chooses freely is a budget nobody can size.
TACTIC_MENU = frozenset(
    {
        "exact?", "apply?", "hint", "simp?", "rw?",
        "omega", "norm_num", "decide", "aesop", "positivity",
    }
)

# Cheap first, and `hint` early because it runs several tactics itself. The
# rest of the menu is reached by naming it.
DEFAULT_TACTICS = ("exact?", "apply?", "hint", "simp?")

# How Lean offers a term it found. `exact?` and `rw?` print one; `simp?`
# prints the simp set it used; `hint` prints one per tactic that worked.
TRY_THIS = re.compile(r"Try this:\s*(?P<tactic>.+)")

MAX_SUGGESTIONS = 8


class TacticAttempt(FrozenModel):
    tactic: str
    closed: bool
    suggestions: tuple[str, ...] = ()
    detail: str = ""
    seconds: float = 0.0


class TacticSearch(FrozenModel):
    """What Lean's own search made of a statement.

    `statement` and its digest travel at the top level because they are the
    thing a suggestion is about. The model wrote this statement, and it may
    not be the frozen claim or the goal it is actually stuck on -- a result
    that did not carry it would read as a proof of something nobody checked.
    A suggestion is a candidate. Only Hardy's verifier accepts anything.
    """

    statement: str
    statement_sha256: str
    attempts: tuple[TacticAttempt, ...]
    closed_by: str | None
    budget_exhausted: bool
    seconds_spent: float
    # Like `LeanCheckResult`, `DeclarationSearch`, `DeclarationInspection` and
    # `PremiseRanking`. Suggestions are not length-bounded and a failed `aesop`
    # prints freely, so a result can exceed `model_observation_bytes` and
    # `bound_tactic_search` needs somewhere to say so. Bounding drops whole
    # attempts from the tail, never parts of one: a prefix of what happened,
    # not an edited version of it.
    observation_truncated: bool = False
    output_artifact: str | None = None
```

Add to `LeanService`, after `search_declarations`:

```python
    def try_tactics(
        self,
        statement: str,
        tactics: Sequence[str] | None = None,
        stop_on_first: bool = True,
    ) -> TacticSearch:
        """Run Lean's own search tactics against a statement the caller wrote.

        Metered like the official checks: the budget refuses the next tactic
        rather than interrupting one in flight, because a tactic cut off
        halfway has spent the time and answered nothing.
        """
        if not 1 <= len(statement) <= 4_096 or ":=" in statement:
            raise ValueError(
                "a tactic search takes one theorem signature of at most 4096 characters, "
                "without a proof body"
            )
        chosen = tuple(tactics) if tactics is not None else DEFAULT_TACTICS
        unlisted = [tactic for tactic in chosen if tactic not in TACTIC_MENU]
        if unlisted:
            raise ValueError(
                f"not tactics this tool runs: {', '.join(sorted(unlisted))}; "
                f"choose from {', '.join(sorted(TACTIC_MENU))}"
            )
        attempts: list[TacticAttempt] = []
        closed_by: str | None = None
        spent = 0.0
        exhausted = False
        # Across the service's whole life, not this call. A model calls this as
        # often as it likes, so a budget reset per call is no budget at all --
        # the same defect `PremiseRetriever` documents about `retrieval_seconds`,
        # and the same fix: cumulative spend on the runtime, and one admission
        # at a time so two concurrent calls are not both admitted against a
        # figure the other is already spending.
        with self._tactic_admission:
            return self._try_tactics(statement, chosen, stop_on_first)

    def _try_tactics(self, statement, chosen, stop_on_first) -> TacticSearch:
        attempts: list[TacticAttempt] = []
        closed_by: str | None = None
        spent = 0.0
        exhausted = False
        for tactic in chosen:
            remaining = self._limits.tactic_search_seconds - self._tactic_spent - spent
            if self._limits.lean_process_seconds > remaining:
                exhausted = True
                break
            check = self.check_scratch(f"{statement} := by\n  {tactic}\n")
            seconds = check.process.duration_ms / 1000
            spent += seconds
            suggestions = tuple(
                match.group("tactic").strip()
                for match in TRY_THIS.finditer(
                    "\n".join(diagnostic.message for diagnostic in check.diagnostics)
                )
            )[:MAX_SUGGESTIONS]
            attempts.append(
                TacticAttempt(
                    tactic=tactic,
                    closed=check.success,
                    suggestions=suggestions,
                    detail="" if check.success else _first_error(check),
                    seconds=seconds,
                )
            )
            if check.success:
                closed_by = tactic
                if stop_on_first:
                    break
        self._tactic_spent += spent
        return TacticSearch(
            statement=statement,
            statement_sha256=hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            attempts=tuple(attempts),
            closed_by=closed_by,
            budget_exhausted=exhausted,
            seconds_spent=spent,
        )
```

And the helper beside the other module functions:

```python
def _first_error(check: LeanCheckResult) -> str:
    """Why a tactic did not close the goal, in one line.

    A model that cannot see what failed tries the same thing again, so the
    reason travels -- bounded, because a failed `aesop` can print a great deal.
    """
    for diagnostic in check.diagnostics:
        if diagnostic.severity == "error":
            return diagnostic.message[:400]
    return ""
```

`Sequence` is imported at `lean.py:19`, `re` at `lean.py:17`, `hashlib` at `lean.py:15`.

- [ ] **Step 6: Run the tests**

Run: `uv run --extra test pytest tests/unit/test_lean_try_tactics.py -v`
Expected: 7 passed

- [ ] **Step 7: Run the whole suite**

Run: `uv run --extra test pytest`
Expected: PASS, once the `schema_version` fixtures from Step 4 are updated.

- [ ] **Step 8: Commit**

```bash
git add src/hardy/lean.py src/hardy/domain.py tests/
git commit -m "Ask Lean's own search, which sees the context #find cannot"
```

---

### Task 11: Offer `try_tactics` on all three surfaces

**Files:**
- Modify: `src/hardy/staged.py` (constant, `TOOLS`, dispatcher), `mcp_server.py` (`bound_tactic_search`, tool), `search_tools.py`, `chat.py`, `prompts/staged/proof.md.j2`, `prompts/chat.md.j2`
- Test: `tests/unit/test_try_tactics_wiring.py` (create)

**Interfaces:**
- Consumes: `LeanService.try_tactics`, `TacticSearch` from Task 10.
- Produces: `staged.TRY_TACTICS_DESCRIPTION` (imported by the other two surfaces so the three cannot drift); a `try_tactics` tool on `staged.TOOLS`, the MCP server and `CHAT_TOOLS`; `SearchToolRuntime.try_tactics(statement, tactics=None, stop_on_first=True) -> ToolResult`; `LeanToolRuntime.bound_tactic_search`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_try_tactics_wiring.py`:

```python
"""The tool reaches every surface, so `prove` does not have what `hardy` lacks.

That asymmetry is the defect this whole change started from, and
reintroducing it with a new tool would be the same mistake twice.
"""

from __future__ import annotations

import importlib


def test_the_staged_tools_offer_it() -> None:
    staged = importlib.import_module('hardy.staged')

    assert 'try_tactics' in {spec['function']['name'] for spec in staged.TOOLS}


def test_the_interactive_tools_offer_it() -> None:
    chat = importlib.import_module('hardy.chat')

    assert 'try_tactics' in {spec['function']['name'] for spec in chat.CHAT_TOOLS}


def test_the_mcp_server_offers_it() -> None:
    mcp_server = importlib.import_module('hardy.mcp_server')

    assert hasattr(mcp_server, 'try_tactics')


def test_all_three_surfaces_describe_it_with_the_same_words() -> None:
    """Three descriptions drift; a model reading one surface then another
    would learn two different limits on what a suggestion means."""
    staged = importlib.import_module('hardy.staged')
    chat = importlib.import_module('hardy.chat')

    staged_spec = next(item for item in staged.TOOLS if item['function']['name'] == 'try_tactics')
    chat_spec = next(
        item for item in chat.CHAT_TOOLS if item['function']['name'] == 'try_tactics'
    )

    assert staged_spec['function']['description'] == chat_spec['function']['description']


def test_the_tool_description_says_a_suggestion_is_not_a_checked_proof() -> None:
    """The one thing a model must not conclude from a `Try this:` line."""
    staged = importlib.import_module('hardy.staged')

    spec = next(item for item in staged.TOOLS if item['function']['name'] == 'try_tactics')

    assert 'not a checked proof' in spec['function']['description']
    assert 'statement you wrote' in spec['function']['description']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_try_tactics_wiring.py -v`
Expected: 5 failed

- [ ] **Step 3: Write the shared description**

In `src/hardy/staged.py`, above `TOOLS`:

```python
# One description, imported by the other two surfaces. Three copies drift, and
# a model reading one surface then another would learn two different limits on
# what a suggestion means.
TRY_TACTICS_DESCRIPTION = (
    "Run Lean's own search tactics against a statement you write, and report what each one "
    "found. Unlike lean_search_declarations these see the local context, and a suggestion is a "
    "term Lean actually elaborated. It is still not a checked proof of anything Hardy grades: "
    "it is about the statement you wrote, which may not be the goal you are stuck on. Submit "
    "the proof itself through the normal check before relying on it. Default tactics are "
    "exact?, apply?, hint and simp?; rw?, omega, norm_num, decide, aesop and positivity can be "
    "named."
)
```

- [ ] **Step 4: Add it to the staged tools**

Append to `TOOLS`:

```python
    {
        "type": "function",
        "function": {
            "name": "try_tactics",
            "description": TRY_TACTICS_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "tactics": {"type": "array", "items": {"type": "string"}},
                    "stop_on_first": {"type": "boolean"},
                },
                "required": ["statement"],
                "additionalProperties": False,
            },
        },
    },
```

and to the dispatcher at `staged.py:233`:

```python
                elif name == "try_tactics":
                    tactics = arguments.get("tactics")
                    result = lean_runtime.bound_tactic_search(
                        lean_runtime.service.try_tactics(
                            str(arguments["statement"]),
                            [str(item) for item in tactics] if isinstance(tactics, list) else None,
                            bool(arguments.get("stop_on_first", True)),
                        )
                    )
```

- [ ] **Step 5: Add the bound and the MCP tool**

Read `bound_search` (`mcp_server.py:113`) first and follow it exactly — it truncates against `model_observation_bytes` and writes the full result to disk via `_write_full_result`. A tactic search returning ten `aesop` failures is precisely the shape that bound exists for. Add `bound_tactic_search` beside it, then the tool:

```python
@mcp.tool()
def try_tactics(
    statement: str, tactics: list[str] | None = None, stop_on_first: bool = True
) -> TacticSearch:
    """Run Lean's own search tactics against a statement you write."""
    runtime = _configured()
    return runtime.bound_tactic_search(
        runtime.service.try_tactics(statement, tactics, stop_on_first)
    )
```

- [ ] **Step 6: Add it to chat**

In `src/hardy/search_tools.py`, add `"try_tactics"` to `SEARCH_TOOL_NAMES` and:

```python
    def try_tactics(
        self, statement: str, tactics: list[str] | None = None, stop_on_first: bool = True
    ) -> ToolResult:
        return self._answer(
            lambda: self.service.try_tactics(
                statement,
                tactics,
                stop_on_first,
                imports=self._workspace_imports(),
                lean_path=self._lean_path,
            )
        )
```

`SearchToolRuntime` gains `workspace: Any = None` in its constructor (Task 2
passes `None`; `cli._chat` passes the session's `LeanWorkspace`), with
`_workspace_imports()` returning its module names and `_lean_path` returning
`workspace.lean_path()` — both empty when there is no workspace, which is the
staged and MCP case. The chat dispatcher passes nothing extra; the runtime
already holds what it needs.

**The scratch file must carry the session's own environment.**
`LeanService.check_scratch` prepends `import Mathlib` and nothing else, while
chat's `check_lean` runs with `env={"LEAN_PATH": lean_workspace.lean_path()}`
(`chat.py:450`) so a file can import modules the session saved earlier. A
`try_tactics` without that reports unknown identifiers for declarations the
model just wrote — searching a different environment from the one it is working
in, which is worse than not searching.

So `LeanService.try_tactics` takes two more keyword arguments,
`imports: Sequence[str] = ()` and `lean_path: str | None = None`, threads them
into the scratch source and the process environment, and `SearchToolRuntime`
passes the session's. On the staged and MCP surfaces there is no workspace and
both stay empty, which is already what `lean_check_scratch` gives that model.

Add to `tests/test_chat_search.py`, with `FakeSearch.try_tactics` recording the
keyword arguments it was given:

```python
def test_tactic_search_in_a_session_sees_the_modules_the_session_saved(session_factory) -> None:
    """A goal about a declaration saved a minute ago is the ordinary case
    here, and `import Mathlib` alone cannot elaborate it."""
    search = FakeSearch()
    session = session_factory(search=search, search_detail='Mathlib abcdef in /lean')

    session._tool('try_tactics', {'statement': 'theorem tmp : True'})

    assert search.calls[0][1]['lean_path']
```

In `chat.py`'s `_search_tool`, before the `inspect_declarations` fallthrough:

```python
        if name == "try_tactics":
            tactics = arguments.get("tactics")
            return self.search.try_tactics(
                str(arguments["statement"]),
                [str(item) for item in tactics] if isinstance(tactics, list) else None,
                bool(arguments.get("stop_on_first", True)),
            )
```

and add the spec to `CHAT_TOOLS`, importing `TRY_TACTICS_DESCRIPTION` from `staged` so the descriptions cannot diverge. If that import would make a cycle, move the constant to `search_tools.py` and have `staged.py` import it from there instead.

- [ ] **Step 7: Run the tests**

Run: `uv run --extra test pytest tests/unit/test_try_tactics_wiring.py tests/test_chat_search.py tests/integration/test_mcp_stdio.py -v`
Expected: PASS

- [ ] **Step 8: Update the prompts**

Add to `src/hardy/prompts/staged/proof.md.j2`, after the `rank_premises` bullet:

```
- try_tactics when a goal looks like one Mathlib should already close. It runs
  Lean's own search — exact?, apply?, hint, simp? — which see your hypotheses
  in a way lean_search_declarations cannot. What comes back is a term Lean
  elaborated against the statement you wrote, so check it with
  lean_check_proof before submitting: a suggestion is a candidate, not a
  verified proof.
```

Add the equivalent to `chat.md.j2`, naming `check_lean` rather than `lean_check_proof`.

- [ ] **Step 9: Run the whole suite and commit**

```bash
uv run --extra test pytest
git add src/hardy/ tests/
git commit -m "Offer tactic search everywhere, rather than only where prove runs"
```

---

### Task 12: Say how much of a proof came from automation

**Files:**
- Modify: `src/hardy/domain.py` (add `AutomationAttribution`, `RunManifest.automation`)
- Modify: whichever module assembles the run record
- Modify: `README.md`, `FEATURES.md`
- Test: `tests/unit/test_automation_attribution.py` (create)

**Interfaces:**
- Consumes: `TacticSearch` from Task 10.
- Produces: `AutomationAttribution(FrozenModel)` with `tactic_search_calls`, `tactic_search_successes`, `suggestions_offered`, `suggestions_reused`; and `attribution(searches, proof_body) -> AutomationAttribution`.

`TacticAttempt` and `TacticSearch` live in `lean.py`. Check for a cycle before importing them into `domain.py`:

```bash
grep -n '^from \.\|^import ' src/hardy/domain.py
```

If `domain.py` imports nothing from `lean.py` today, keep it that way: put `attribution` in `lean.py` and let `domain.AutomationAttribution` be the plain value type. Adjust the test's imports to match wherever it lands.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_automation_attribution.py`:

```python
"""How much of a proof Lean's own automation found.

A proof `exact?` located is still a proof -- the verifier does not care who
found the term. But "weak model X proved theorem Y with Hardy" and "`exact?`
proved theorem Y" are different results, and an experiment harness should not
make you read transcripts to tell them apart.
"""

from __future__ import annotations

import importlib


def _search(lean, *, closed_by, suggestions):
    return lean.TacticSearch(
        statement='theorem tmp : True',
        statement_sha256='0' * 64,
        attempts=(
            lean.TacticAttempt(
                tactic='exact?', closed=closed_by is not None, suggestions=tuple(suggestions)
            ),
        ),
        closed_by=closed_by,
        budget_exhausted=False,
        seconds_spent=1.0,
    )


def test_a_suggestion_the_proof_repeats_verbatim_is_counted_as_reused() -> None:
    lean = importlib.import_module('hardy.lean')
    search = _search(lean, closed_by='exact?', suggestions=['exact Nat.add_comm n m'])

    figure = lean.attribution([search], 'by exact Nat.add_comm n m')

    assert figure.suggestions_reused == 1


def test_a_lemma_reached_by_a_different_route_is_not_counted() -> None:
    """This counts text, not influence, and says so.

    A model that read `exact Nat.add_comm n m` and wrote `by rw [Nat.add_comm]`
    was plainly helped, and this figure will not say so. Overcounting would be
    worse: it would let the harness claim to know something it cannot.
    """
    lean = importlib.import_module('hardy.lean')
    search = _search(lean, closed_by='exact?', suggestions=['exact Nat.add_comm n m'])

    figure = lean.attribution([search], 'by rw [Nat.add_comm]')

    assert figure.suggestions_reused == 0


def test_calls_and_successes_are_counted_apart() -> None:
    """A model calling tactic search ten times and closing nothing is a
    different run from one calling it once and closing the goal."""
    lean = importlib.import_module('hardy.lean')
    searches = [
        _search(lean, closed_by=None, suggestions=[]),
        _search(lean, closed_by='exact?', suggestions=['exact trivial']),
    ]

    figure = lean.attribution(searches, 'by exact trivial')

    assert (figure.tactic_search_calls, figure.tactic_search_successes) == (2, 1)


def test_a_run_that_never_asked_reports_zeros_rather_than_nothing() -> None:
    """Absent and zero are different claims. A manifest with no attribution
    says nobody measured; one with zeros says the model did it itself."""
    lean = importlib.import_module('hardy.lean')

    figure = lean.attribution([], 'by simp')

    assert figure.tactic_search_calls == 0
    assert figure.suggestions_reused == 0


def test_a_manifest_can_carry_the_figure_and_still_read_back() -> None:
    import datetime
    import uuid

    domain = importlib.import_module('hardy.domain')
    lean = importlib.import_module('hardy.lean')
    figure = lean.attribution([], 'by simp')

    manifest = domain.RunManifest(
        run_id=uuid.uuid4(),
        created_at=datetime.datetime.now(datetime.timezone.utc),
        phase=next(iter(domain.RunPhase)),
        model='test',
        prompt_set_sha256='0' * 64,
        automation=figure,
    )

    assert domain.RunManifest.model_validate_json(manifest.model_dump_json()).automation == figure
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest tests/unit/test_automation_attribution.py -v`
Expected: FAIL with `AttributeError: module 'hardy.lean' has no attribute 'attribution'`

- [ ] **Step 3: Write the implementation**

`AutomationAttribution` and the `RunManifest.automation` field were added in
Task 10, with the schema bump they share. What lands here is the function that
computes the figure and the wiring that fills the field.

For reference, the type Task 10 added:

```python
class AutomationAttribution(FrozenModel):
    """How much of a proof came from Lean's own search rather than the model.

    `suggestions_reused` is a **textual** match: a suggestion string occurring
    as a substring of the accepted proof body. That is what it measures and
    all it measures. A model that copies `exact Nat.add_comm n m` out of a
    suggestion is counted; one that reads the same suggestion and writes
    `rw [Nat.add_comm]` is not. It is evidence of reuse, not a claim about
    causation, and a reader should take it as the floor it is.

    On the manifest rather than in `Grades` on purpose. This is a measurement
    of how a run went, not a grade, and `Grades.require_verification_evidence`
    enforces a biconditional between the formal grade and its evidence that a
    figure like this has no business being drawn into.
    """

    tactic_search_calls: int = 0
    tactic_search_successes: int = 0
    suggestions_offered: int = 0
    suggestions_reused: int = 0
```

and on `RunManifest`, beside `grades`:

```python
    automation: AutomationAttribution | None = None
```

Optional and defaulting to `None` because absent and zero are different claims: a manifest with no attribution says nobody measured, and one with zeros says the model did it unaided.

Add to `src/hardy/lean.py`:

```python
def attribution(
    searches: Sequence[TacticSearch], proof_body: str
) -> AutomationAttribution:
    """The figure for one run's tactic searches against its accepted proof."""
    offered = [
        suggestion
        for search in searches
        for attempt in search.attempts
        for suggestion in attempt.suggestions
    ]
    return AutomationAttribution(
        tactic_search_calls=len(searches),
        tactic_search_successes=sum(1 for search in searches if search.closed_by is not None),
        suggestions_offered=len(offered),
        suggestions_reused=sum(1 for suggestion in set(offered) if suggestion in proof_body),
    )
```

Add `AutomationAttribution` to the `from .domain import ...` line at `lean.py:24`.

- [ ] **Step 4: Run the tests**

Run: `uv run --extra test pytest tests/unit/test_automation_attribution.py -v`
Expected: 5 passed

- [ ] **Step 5: Populate it where the manifest is written**

```bash
grep -rn 'RunManifest(' src/hardy/
```

The manifest is built at `workflow.py:465`, in the **parent** process. But
`codex_runtime.py:85` serves Hardy's tools by launching `python -m hardy.mcp_server`
over stdio, so on that backend `try_tactics` runs in a child the parent never
shares memory with. A list held on `LeanToolRuntime` would be lost, and a Codex
run that leaned heavily on `exact?` would finalize reporting zeros — a figure
reading as "the model did it unaided" precisely when it did not, which is the
one way this measurement can be actively misleading.

So the record goes through a log on disk rather than through memory — but
**not one inside the run directory**. `codex_runtime.py:99` sets the agent's
`cwd` to the run directory and grants `Sandbox.workspace_write`, so a log
written there is a log the model can rewrite, truncate or delete. The entire
value of this figure is that it does not rest on the model's account of itself;
a run that could zero its own attribution while leaning on `exact?` throughout
would be worse than one that never measured.

1. The parent chooses a path **beside** the run directory, outside the subtree
   `workspace_write` grants — `run_dir.parent / f"{run_dir.name}.attribution.jsonl"`
   — and passes it to the MCP process as `HARDY_ATTRIBUTION_LOG`, the way
   `HARDY_RUN_DIR` and `HARDY_CONFIG` already travel (`mcp_server.py:229`).
   Add it to that `required` tuple.
2. `bound_tactic_search` appends one JSON line per search, using the protocol
   `cas.py:919-1010` implements and not merely its name: one write of
   `record + "\n"`, then `flush()`, then `os.fsync(fileno())`.
3. `workflow._finalize` reads the log through the same torn-record repair
   `CasSession._repair_interrupted_append` performs — read bytes, drop a
   trailing fragment that is not a complete line, then parse. An MCP process
   killed mid-append must not leave finalization unable to write a manifest at
   all, which is what a bare `json.loads` per line would do.
4. It then rebuilds the `TacticSearch` values and calls `attribution(...)`
   before hashing artifacts and writing the manifest.

Both in-process surfaces write to the same log, so one path produces the figure
rather than two that can disagree.

**A missing or unreadable log is `automation=None`, not zeros.** Absent means
nobody measured; zeros mean the model worked unaided. Writing the second from
the first is precisely the fabrication this arrangement exists to prevent, and
`RunManifest.automation` is optional for that reason.

Add two tests beside the existing manifest tests: one that a run which called
`try_tactics` and accepted a proof reusing its suggestion reports
`suggestions_reused == 1`, and one that pins the process boundary —

```python
def test_a_search_recorded_by_a_child_process_still_reaches_the_manifest(tmp_path) -> None:
    """On the Codex backend `try_tactics` runs inside `hardy.mcp_server`,
    which finalization never shares memory with. A figure counting only
    in-process calls would report zeros for exactly the runs that leaned
    hardest on automation."""


def test_the_attribution_log_sits_outside_what_the_agent_may_write(tmp_path) -> None:
    """`codex_runtime.py:99` gives the agent `workspace_write` over the run
    directory. A log inside it is one the model can rewrite to claim it worked
    unaided."""


def test_a_torn_final_record_is_dropped_rather_than_failing_the_run(tmp_path) -> None:
    """An MCP process killed mid-append must not leave finalization unable to
    write a manifest. `cas.py` already solves this; the point is to use its
    protocol and not just its name."""


def test_a_missing_log_is_absent_attribution_rather_than_zeros(tmp_path) -> None:
    """Absent says nobody measured; zeros say the model worked unaided.
    Deriving the second from a missing file is the fabrication the log's
    placement exists to prevent."""
```

driven by writing the log file directly, since that file *is* the contract
between the two processes.

- [ ] **Step 6: Document it**

Add to `FEATURES.md` (retrieval section) and `README.md` (beside the `rank_premises` paragraph): what the figure counts, and — in the same sentence — that it counts text rather than influence. A figure documented as stronger than it is would be the exact defect this codebase's provenance discipline exists to prevent.

- [ ] **Step 7: Run everything and commit**

```bash
uv run --extra test pytest --cov
git add src/hardy/ tests/ README.md FEATURES.md
git commit -m "Say how much of a proof Lean's own search found, and no more than that"
```

---

## Final verification

- [ ] `uv run --extra test pytest --cov` passes and meets the coverage floor
- [ ] `uv run python eval/run_premise_eval.py` runs with no network and no Lean toolchain
- [ ] `eval/README.md` carries baseline and post-change numbers for every slice that moved retrieval
- [ ] `README.md`, `FEATURES.md`, `DESIGN.md` and `ARCHITECTURE.html` describe three sources, four tool surfaces and the attribution figure
- [ ] The search tools refuse with a reason on a machine with no Lake project, rather than being absent
- [ ] No module under `src/hardy/` imports anything from `eval/`


> **Note for Task 10, Step 5:** `LeanService.__init__` (`lean.py:472`) gains
> `self._tactic_spent = 0.0` and `self._tactic_admission = threading.Lock()`,
> and `lean.py` gains `import threading`. The cumulative figure lives on the
> service because that is what has the run's lifetime; a local in `try_tactics`
> resets on every call.
