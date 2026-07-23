# M3 — Literature Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M3 from `docs/superpowers/specs/2026-07-21-m3-literature-layer-design.md` — arXiv search/fetch/read tools behind a rate-limited, cached client; a version-keyed immutable paper store with safe extraction and a crash-safe digest journal; a machine-maintained `references.bib` whose sole write path is the `cite` tool; and citations wired through the writeup pipeline — ending at M3's exit criterion: a published writeup that cites fetched papers with a valid, compile-checked bibliography.

**Architecture:** A synchronous `hardy.literature` package (network + disk work runs in `asyncio.to_thread` from tool handlers) with four core modules — `arxiv.py` (client façade over the `arxiv` PyPI package, interprocess rate limiter, disk query cache, capped streaming downloads), `store.py` (safe tar extraction, fsynced JSONL digest journal, atomic journaled admission), `bibliography.py` (pydantic `BibEntry` model, order-independent cite-key minting, locked atomic-durable `.bib` transactions), `reading.py` (section/page-chunked serving through resource-limited subprocess extraction) — plus `hardy/tools/literature_tools.py` exposing `arxiv_search` / `fetch_paper` / `read_paper` / `cite` as ordinary M1 `ToolDef`s. The M0/M1 LaTeX pipeline gains bibliography passes (undefined citations are compile *failures*), and the Prove workflow's writeup phase gains the literature tools when literature services are supplied.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio (all M0-pinned); new dependencies `filelock` (interprocess locks), `arxiv` (Atom API client, wrapped behind our façade), `bibtexparser` v1 (`.bib` read/write), `pypdf` (PDF text extraction, subprocess-confined); the M0 REPL pool, sandbox runner, and TeX Live `lualatex`+`bibtex` compile pipeline as-is.

**Scope note:** M3 only. No `assume_paper`/axiomatization or `list_assumptions` (M4), no semantic retrieval over papers (M8), no citation-graph chasing, no non-arXiv sources, no OCR for scanned PDFs, no bibliography styles beyond the template default (`plain`).

## Global Constraints

(from the M3 spec — every task's requirements implicitly include these)

- **Version-keyed immutable store:** `papers/<arxiv-id>v<N>/`; an unversioned fetch resolves to the latest revision and is stored under its resolved version; cached entries are immutable; later revisions are distinct entries; `admit` refuses to overwrite; a digest mismatch on re-download of the same version is an error surfaced to the user.
- **Downloads are untrusted:** archive extraction is path-normalized and symlink-safe, with byte (default 512 MB) and file-count (default 10 000) quotas, an isolated temp dir, and atomic admission; downloads stream to temp storage under explicit per-payload byte caps checked on compressed bytes as they arrive — never materialized whole as `bytes`.
- **Polite API usage:** 1 request / 3 s, single connection, coordinated **across processes** (interprocess lock + shared last-request timestamp); search responses cached on disk keyed by canonicalized query with a TTL (default 24 h); never re-fetch a stored version.
- **Digest journal is an event journal:** `papers/DIGESTS.json` is complete, individually-fsynced JSONL appends (*pending* then *committed*), never an in-place rewrite; admit order under the journal lock is pending → fsync staged tree → rename → fsync parent → committed; recovery under the same lock reconciles (pending + entry present → committed; pending + no entry → superseded); every refetch verifies against committed digests.
- **One canonical `references.bib`**, committed to git, machine-maintained; the `cite` tool (`bibliography.add_or_get`) is the **only** write path — `fetch_paper` delegates to it; save is atomic **and durable** (temp write, fsync, rename, parent fsync) and the whole load → dedup/mint → save transaction runs under an interprocess file lock.
- **Cite keys are a pure function of the paper:** `<surname><year><word>-<id-fragment>` always carries the arXiv-id fragment (e.g. `smith2023modular-2301.12345`); distinct versions get version-qualified keys (`…V3`); no ordinals, no first-inserted-wins, no re-keying; DOI dedup applies **only** to entries with no arXiv identity.
- **Network-controlled text is confined before persistence:** arXiv titles/authors/journal fields pass through the M1 allowlist (`confine.violations`) and are per-character escaped when not admissible — successful BibTeX parsing is not sanitization.
- **Undefined citations are compile failures**, not warnings: the log is scanned after every compile and `Citation … undefined` feeds back to the agent like any TeX error.
- **The compile sandbox sees only the needed fragment** of `references.bib`, filtered to the cited keys and staged — never the whole project file.
- **PDF/LaTeX parsing is subprocess-confined:** extraction runs in a resource-limited subprocess (rss/cpu rlimits where the platform supports them, wall-clock kill, bounded input) with output streamed through a hard byte quota before the derived-data cache admits it; per-call tool output is capped (Component 2 rules).
- **PDF-only admission on source problems:** a missing, over-quota, or hostile e-print never fails the fetch — the entry is admitted PDF-only with the failure recorded in the extraction report.
- **`papers/` is data, not git content:** gitignored except `papers/DIGESTS.json`; `references.bib` is committed; per-result manifests record the digests of the papers they used.
- **Test tiers:** unit (default, CI), `lean`, `tex`, `docker`, `model` as in M0/M1, plus new `network` (live arXiv; never CI). `scripts/validate_bib.py` joins the CI unit job.

## Plan assumptions (re-validate before execution)

Per `docs/superpowers/specs/README.md`, a milestone's plan is re-reviewed against reality when it starts. **Every interface below is consumed from the M1 *plan*** (`docs/superpowers/plans/2026-07-22-m1-minimal-agent.md`, status "Not started" at the time of writing) — none of it exists in `src/` yet. Before executing any M3 task, confirm each signature against the code M1 actually landed; where M1 drifted, adapt the consuming M3 task to the landed code (the M1 code wins over both this plan's assumption and the M3 spec's paraphrase).

1. **`hardy/tools/registry.py`** (M1 plan Task 1): `ToolResult(content: str, is_error: bool = False)`; `ToolDef(name, description, input_model: type[BaseModel], handler)` with `json_schema()` and `async call(arguments: dict) -> ToolResult`; `ToolRegistry(tools: list[ToolDef] | None = None)` with `add(tool)`, `get(name)`, `names()`, iteration, duplicate-name `ValueError`. Tasks 10 and 13 build and merge registries against exactly these.
2. **`hardy/latex/confine.py`** (M1 plan Task 5): `violations(text: str) -> list[str]`; `ALLOWED_COMMANDS: frozenset[str]` (allowlist extended by editing this constant, nowhere else). Task 12 adds `cite` to it; Task 7 calls `violations` for bib-field confinement.
3. **`hardy/latex/template.py`** M1 extensions (M1 plan Task 5): `escape_text(text) -> str`, `escape_listing(text) -> str`, and `render_writeup(*, title, statement, informal_proof, formalization_status, lean_file=None, lean_statement=None, statement_is_verbatim_user_claim=False)` with the `<<TOKEN>>`-substitution mechanism. Task 12 adds a keyword-only `bibliography: bool = False` and a `<<BIBLIOGRAPHY_BLOCK>>` slot on top of that signature. **Spec conflict, resolved in M1's favor:** the spec says "`render_writeup` gains `bibliography: bool` behavior: when the document body contains `\cite` …" — detection of `\cite` lives in the `write_latex` handler (Task 12), which passes the boolean; the template itself stays logic-free, matching M1's template design.
4. **`hardy/tools/latex_tools.py`** (M1 plan Task 6): `make_writeup_registry(*, statement_text, lean_statement, formalization_status, lean_file, compile_fn: Callable[[str, Path], CompileResult], staging: Path, published: list[str]) -> ToolRegistry` with the single `write_latex(title, informal_proof)` tool. Task 12 adds keyword-only `bib_path: Path | None = None` and calls `compile_fn(source, staging, bibliography=fragment)` — the injected fakes in M1's `tests/test_latex_tools.py` must grow a `bibliography=None` keyword (extension; M1 assertions unchanged).
5. **`hardy/workflows/persist.py`** (M1 plan Task 13): `Manifest` pydantic model (claim, statement, statement_sha256, formalization_status, informal_completeness, faithfulness, audit, budgets, prompt_versions, outcome, trajectory_file) and `publish(results_dir, slug, run_id, files) -> Path`. Task 13 adds a `papers: list[dict] = []` field to `Manifest`.
6. **`hardy/workflows/prove.py`** (M1 plan Task 14): `async prove(claim, *, pool, runtime, config, results_dir, run_id) -> ProveResult`; `ProveConfig` with `prompt_versions: dict[str, str]` defaulting to `_DEFAULT_PROMPTS`; the writeup phase builds `make_writeup_registry(...)` and loops `max_writeup_retries` times; `sandbox_tex` selects `_compile_fn_local()` / `_compile_fn_sandboxed()`. Task 13 adds a keyword-only `literature: LiteratureServices | None = None` parameter, merges the literature registry into the writeup registry, switches the writeup prompt to `writeup_cited_v1` when literature is enabled, and records `literature.used_papers` in the manifest. **Spec conflict, resolved in the M1 plan's favor:** the spec's context section calls this "an optional literature phase"; the spec's own architecture section and the M1 plan's phase structure put the literature tools *inside the writeup phase's registry* — no new phase is added.
7. **`hardy/prompts`** (M1 plan Task 10): `get_prompt(name) -> str`, `_PROMPTS` registry in `src/hardy/prompts/__init__.py`, templates as plain `.format()` strings. Task 13 registers `writeup_cited_v1` (placeholders `{statement, status}`, same as `writeup_v1`).
8. **`tests/fake_runtime.py`** (M1 plan Task 7): `FakeRuntime(scripts: list[list[dict]])` executing `{"tool": …, "arguments": …}` entries through real handlers and recording `self.calls`. Tasks 13–14 script the writeup phase with it.
9. **`hardy/latex/compile.py` `CompileResult`/`TexError`** exist in M0 (real, verified) — but M1's Task 6 established the `compile_fn` injection seam whose call signature Task 12 extends. M0's `compile_tex_sandboxed` mounts an inputs dir containing **only `main.tex`**; Task 11 must extend that dir (and the in-container copy step) with `references.bib`.
10. **Markers and CI** (M1 plan Task 15): M1 adds a `model` marker to `pyproject.toml` but does **not** modify `.github/workflows/test.yml` (its CI-equivalent command is documentation only). Task 8 therefore updates `test.yml` to exclude **both** `model` and the new `network` marker — if M1 landed differently, reconcile there.
11. **Engine reality vs. spec:** the spec's parenthetical "(Tectonic runs the bib pass automatically)" is stale — M0's landed engine is self-contained TeX Live `lualatex` (`src/hardy/latex/compile.py`, `DEFAULT_ENGINE`), which does **not** run BibTeX. Task 11 adds an explicit `lualatex → bibtex → lualatex → lualatex` pass sequence (local and sandboxed), and assumes `bibtex` is on PATH wherever `tex`-marked tests run and inside the `hardy-tex:dev` image (both ship it in a full TeX Live install — verify on the image before Task 11).
12. **Spec file-list deltas (focused-file decomposition, M1's `persist.py` precedent):** the spec's architecture lists only `arxiv.py`/`store.py`/`bibliography.py`/`reading.py`; this plan adds `hardy/literature/fsutil.py` (durable-write/lock primitives shared by the journal, the bibliography, and the rate limiter) and `hardy/literature/extract_worker.py` (the resource-limited subprocess entry point). Also, `ArxivClient.download` takes a `PaperMeta` rather than the spec's bare `arxiv_id_v` string — the admitted entry needs the metadata anyway, and metadata-for-an-old-version is not a separate arXiv API surface; the id_v is derived from the meta.

---

## File Structure

```
src/hardy/literature/__init__.py       — empty package marker
src/hardy/literature/fsutil.py         — fsync/atomic/append durable primitives + file_lock (filelock)
src/hardy/literature/arxiv.py          — ArxivQuery/PaperMeta/DownloadedPaper, RateLimiter,
                                         ArxivTransport protocol + LibraryTransport (the only code
                                         importing the `arxiv` package), ArxivClient (cache, caps)
src/hardy/literature/store.py          — ExtractionReport + extract_tar_safe, DigestJournal,
                                         StoredPaper, PaperStore (journaled atomic admission)
src/hardy/literature/bibliography.py   — BibEntry, confine_bib_text, mint_key, Bibliography,
                                         add_or_get (the sole .bib write path), validate
src/hardy/literature/extract_worker.py — `python -m` subprocess: pdf-text (pypdf) / tex-index modes
src/hardy/literature/reading.py        — _run_worker (rlimits + wall-clock + output cap),
                                         PaperReader (TOC, section/page chunking, derived cache)
src/hardy/tools/literature_tools.py    — LiteratureServices + arxiv_search/fetch_paper/read_paper/cite
src/hardy/latex/compile.py             — MODIFY: bibliography param, bibtex passes, citation scan
src/hardy/latex/template.py            — MODIFY: <<BIBLIOGRAPHY_BLOCK>> slot, bibliography kwarg
src/hardy/latex/confine.py             — MODIFY: add `cite` to ALLOWED_COMMANDS
src/hardy/tools/latex_tools.py         — MODIFY: bib_path kwarg, cite-key extraction, fragment staging
src/hardy/workflows/persist.py         — MODIFY: Manifest.papers field
src/hardy/workflows/prove.py           — MODIFY: literature kwarg, merged writeup registry, cited prompt
src/hardy/prompts/literature_v1.py     — WRITEUP_CITED_V1
src/hardy/prompts/__init__.py          — MODIFY: register writeup_cited_v1
references.bib                         — NEW, committed: the canonical (initially empty) bibliography
scripts/validate_bib.py                — CI check: parses, no duplicate keys, entries well-formed
scripts/prove_cited.py                 — M3 exit criterion (model + network; never CI)
.gitignore                             — MODIFY: papers/* except DIGESTS.json
.github/workflows/test.yml             — MODIFY: exclude model+network markers; run validate_bib
pyproject.toml                         — MODIFY: filelock/arxiv/bibtexparser/pypdf deps, network marker
tests/fake_arxiv.py                    — FakeTransport, make_tar, TINY_PDF, sample_meta fixtures
tests/fake_bibtex.py                   — fake bibtex engine for multi-pass compile unit tests
tests/fake_tectonic.py                 — MODIFY: citeundef + calls-log modes (extensions only)
tests/test_fsutil.py
tests/test_ratelimit.py
tests/test_arxiv_client.py
tests/test_extract.py
tests/test_digest_journal.py
tests/test_store.py
tests/test_bibliography.py
tests/test_validate_bib.py
tests/test_reading.py
tests/test_literature_tools.py
tests/test_compile_bib.py
tests/test_writeup_citations.py
tests/test_prove_literature.py
tests/test_integration_cited_writeup.py — @pytest.mark.tex (deterministic exit-criterion rehearsal)
tests/test_network_arxiv.py            — @pytest.mark.network (live arXiv; never CI)
```

**Test tiers:** unit (default, CI), `lean`, `tex`, `docker`, `model` as inherited, plus new `network`.

---

### Task 1: Durable-filesystem primitives (`fsutil.py`)

**Files:**
- Create: `src/hardy/literature/__init__.py` (empty)
- Create: `src/hardy/literature/fsutil.py`
- Modify: `pyproject.toml` (add `"filelock>=3.12"` to `[project] dependencies`)
- Test: `tests/test_fsutil.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + `filelock`).
- Produces (Tasks 2, 5, 6, 7 rely on these exact signatures):
  - `write_durable(path: Path, data: str | bytes) -> None` — write + flush + fsync (no rename; for files inside a staging tree that is renamed as a unit).
  - `atomic_replace(path: Path, data: str | bytes) -> None` — temp file in the same directory, written complete, fsynced, `os.replace`d over `path`, parent dir fsynced. The bibliography save and query-cache writes use this.
  - `append_line_durable(path: Path, line: str) -> None` — open `"ab"`, write `line + "\n"` as one complete write, flush, fsync. The digest journal's only write primitive.
  - `fsync_dir(path: Path) -> None` — directory fsync; no-op on non-POSIX (the durability guarantee is a POSIX-host property, same guard as M1's `persist.py`).
  - `fsync_tree(root: Path) -> None` — fsync every regular file under `root`, then every directory bottom-up, then `root` itself. `PaperStore.admit` calls this on the staged entry before rename.
  - `file_lock(path: Path, timeout: float = 60.0) -> FileLock` — an interprocess lock at `str(path) + ".lock"`. All lock files in this milestone are named by suffixing the protected file, exactly as the spec names `references.bib.lock` and `DIGESTS.json.lock`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, append `"filelock>=3.12"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_fsutil.py
import subprocess
import sys
import time

import pytest
from filelock import Timeout

from hardy.literature.fsutil import (
    append_line_durable,
    atomic_replace,
    file_lock,
    fsync_tree,
    write_durable,
)


def test_write_durable_str_and_bytes(tmp_path):
    write_durable(tmp_path / "a.txt", "hello")
    assert (tmp_path / "a.txt").read_text() == "hello"
    write_durable(tmp_path / "b.bin", b"\x00\x01")
    assert (tmp_path / "b.bin").read_bytes() == b"\x00\x01"


def test_atomic_replace_overwrites_and_leaves_no_temp(tmp_path):
    target = tmp_path / "refs.bib"
    atomic_replace(target, "v1")
    atomic_replace(target, "v2")
    assert target.read_text() == "v2"
    # no stray temp files beside the target
    assert [p.name for p in tmp_path.iterdir()] == ["refs.bib"]


def test_append_line_durable_appends_complete_lines(tmp_path):
    journal = tmp_path / "j.jsonl"
    append_line_durable(journal, '{"event": "pending"}')
    append_line_durable(journal, '{"event": "committed"}')
    lines = journal.read_text().splitlines()
    assert lines == ['{"event": "pending"}', '{"event": "committed"}']


def test_append_never_rewrites_existing_content(tmp_path):
    journal = tmp_path / "j.jsonl"
    append_line_durable(journal, "one")
    before = journal.read_bytes()
    append_line_durable(journal, "two")
    after = journal.read_bytes()
    assert after.startswith(before)          # strictly append-only


def test_fsync_tree_handles_nested_dirs(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "sub" / "f.txt").write_text("x")
    (tmp_path / "sub" / "deeper" / "g.txt").write_text("y")
    fsync_tree(tmp_path)                     # must not raise on any platform


def test_file_lock_names_the_lock_beside_the_file(tmp_path):
    lock = file_lock(tmp_path / "DIGESTS.json")
    assert lock.lock_file == str(tmp_path / "DIGESTS.json") + ".lock"


HOLDER = """
import sys, time
from pathlib import Path
from hardy.literature.fsutil import file_lock
with file_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text("locked")
    time.sleep(10)
"""


def test_file_lock_excludes_other_processes(tmp_path):
    # filelock makes same-path locks reentrant within one process, so real
    # exclusion must be proven cross-process: a child grabs the lock, and
    # this process's acquire must time out while the child holds it.
    target = tmp_path / "guarded.bib"
    marker = tmp_path / "marker"
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(target), str(marker)]
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline, "child never acquired the lock"
            time.sleep(0.05)
        with pytest.raises(Timeout):
            with file_lock(target, timeout=0.3):
                pass
    finally:
        child.kill()
        child.wait()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_fsutil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.literature'`

- [ ] **Step 4: Write the implementation**

```python
# src/hardy/literature/fsutil.py
"""Durable-filesystem primitives for the literature layer (M3 spec).

Three write disciplines, each matching one spec requirement:
- write_durable: files inside a staging tree (the tree is renamed as a
  unit; each file must be durable before the rename is).
- atomic_replace: whole-file replacement readers may never see partial
  (the bibliography, the query cache).
- append_line_durable: the digest journal — complete, individually
  fsynced appends, NEVER an in-place rewrite, which a crash could
  truncate into losing the durable history for every stored version.

file_lock standardizes interprocess locks as `<protected-file>.lock`
(the spec's references.bib.lock / DIGESTS.json.lock discipline).
"""

import os
import tempfile
from pathlib import Path

from filelock import FileLock


def fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return  # directory fsync is a POSIX-host durability property
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def write_durable(path: Path, data: str | bytes) -> None:
    payload = data.encode() if isinstance(data, str) else data
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_replace(path: Path, data: str | bytes) -> None:
    payload = data.encode() if isinstance(data, str) else data
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def append_line_durable(path: Path, line: str) -> None:
    with open(path, "ab") as handle:
        handle.write(line.encode() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def fsync_tree(root: Path) -> None:
    dirs: list[Path] = []
    for current, subdirs, files in os.walk(root, topdown=False):
        base = Path(current)
        for name in files:
            _fsync_file(base / name)
        dirs.append(base)
    for directory in dirs:  # bottom-up: os.walk(topdown=False) yields leaves first
        fsync_dir(directory)


def file_lock(path: Path, timeout: float = 60.0) -> FileLock:
    return FileLock(str(path) + ".lock", timeout=timeout)
```

Also create empty `src/hardy/literature/__init__.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fsutil.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/literature/ tests/test_fsutil.py pyproject.toml
git commit -m "feat: durable-filesystem primitives + interprocess file_lock for the literature layer"
```

---

### Task 2: arXiv models, query canonicalization, interprocess rate limiter

**Files:**
- Create: `src/hardy/literature/arxiv.py` (models + limiter; the client arrives in Task 3)
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: `file_lock` (Task 1).
- Produces (Tasks 3, 6, 7, 10 rely on these exact signatures):
  - `ArxivError(Exception)`; `DownloadError(ArxivError)`.
  - `ArxivQuery(text: str | None = None, author: str | None = None, category: str | None = None, id_list: list[str] = [], date_from: str | None = None, date_to: str | None = None, max_results: int = 10)` (pydantic).
  - `canonical_query_key(query: ArxivQuery) -> str` — SHA-256 hex of the canonical JSON dump (sorted keys, defaults materialized): equal queries produce equal cache keys regardless of construction order.
  - `PaperMeta(arxiv_id: str, version: int, title: str, authors: list[str], abstract: str, categories: list[str] = [], doi: str | None = None, journal_ref: str | None = None, published: str | None = None)` with property `id_v -> str` (`f"{arxiv_id}v{version}"`). `arxiv_id` is stored **unversioned** (`"2301.12345"` or old-style `"math/0211159"`).
  - `DownloadedPaper(meta: PaperMeta, pdf_path: Path, source_tar_path: Path | None = None, source_error: str | None = None)` (pydantic) — the source's absence is recorded, never an error.
  - `RateLimiter(state_dir: Path, *, interval_s: float = 3.0, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep)` with context manager `slot()`: acquires the interprocess lock, waits out the shared last-request interval, yields (the request runs **while holding the lock** — that is the single-connection lease), then records the shared timestamp. Wall time (`time.time`), not monotonic — the timestamp is shared across processes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ratelimit.py
import json

from hardy.literature.arxiv import (
    ArxivQuery,
    DownloadedPaper,
    PaperMeta,
    RateLimiter,
    canonical_query_key,
)


def meta(**kw) -> PaperMeta:
    defaults = dict(
        arxiv_id="2301.12345", version=2, title="A Modular Approach",
        authors=["Ada Smith", "Bob Jones"], abstract="We prove things.",
    )
    defaults.update(kw)
    return PaperMeta(**defaults)


def test_paper_meta_id_v():
    assert meta().id_v == "2301.12345v2"
    assert meta(arxiv_id="math/0211159", version=1).id_v == "math/0211159v1"


def test_canonical_query_key_is_order_and_default_insensitive():
    a = canonical_query_key(ArxivQuery(text="sqrt", category="math.NT"))
    b = canonical_query_key(ArxivQuery(category="math.NT", text="sqrt"))
    c = canonical_query_key(
        ArxivQuery(text="sqrt", category="math.NT", max_results=10)
    )
    assert a == b == c
    assert len(a) == 64                      # sha256 hex


def test_canonical_query_key_differs_on_content():
    a = canonical_query_key(ArxivQuery(text="sqrt"))
    b = canonical_query_key(ArxivQuery(text="cbrt"))
    assert a != b


def test_downloaded_paper_records_source_absence():
    d = DownloadedPaper(
        meta=meta(), pdf_path="x/paper.pdf", source_error="404 not found"
    )
    assert d.source_tar_path is None
    assert "404" in d.source_error


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_limiter(tmp_path, clock: FakeClock) -> RateLimiter:
    return RateLimiter(tmp_path, interval_s=3.0, clock=clock, sleep=clock.sleep)


def test_first_slot_does_not_sleep(tmp_path):
    clock = FakeClock()
    limiter = make_limiter(tmp_path, clock)
    with limiter.slot():
        pass
    assert clock.sleeps == []


def test_back_to_back_slots_wait_out_the_interval(tmp_path):
    clock = FakeClock()
    limiter = make_limiter(tmp_path, clock)
    with limiter.slot():
        pass
    with limiter.slot():
        pass
    assert len(clock.sleeps) == 1
    assert abs(clock.sleeps[0] - 3.0) < 1e-9


def test_slot_after_long_gap_does_not_sleep(tmp_path):
    clock = FakeClock()
    limiter = make_limiter(tmp_path, clock)
    with limiter.slot():
        pass
    clock.now += 100.0
    with limiter.slot():
        pass
    assert clock.sleeps == []


def test_timestamp_is_shared_across_limiter_instances(tmp_path):
    # Two instances over the same state dir stand in for two processes:
    # the second must honor the first's last-request timestamp.
    clock = FakeClock()
    with make_limiter(tmp_path, clock).slot():
        pass
    other = make_limiter(tmp_path, clock)
    with other.slot():
        pass
    assert len(clock.sleeps) == 1 and abs(clock.sleeps[0] - 3.0) < 1e-9


def test_corrupt_state_file_treated_as_no_history(tmp_path):
    (tmp_path / "arxiv-rate").write_text("not a float")
    clock = FakeClock()
    with make_limiter(tmp_path, clock).slot():
        pass
    assert clock.sleeps == []
    assert float((tmp_path / "arxiv-rate").read_text()) == clock.now
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.literature.arxiv'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/literature/arxiv.py
"""arXiv client façade (M3 spec): models, interprocess rate limiter,
and (Task 3) the cached, capped client.

Rate limiting honors arXiv's published guidance — 1 request / 3 s,
single connection — coordinated ACROSS processes: the interval and the
connection lease live behind an interprocess lock with a shared
last-request timestamp, because concurrent Hardy runs are a supported
scenario and per-process limiters would multiply the real request rate.
The request itself runs while the lock is held (the connection lease).
"""

import hashlib
import json
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field

from .fsutil import file_lock


class ArxivError(Exception):
    pass


class DownloadError(ArxivError):
    pass


class ArxivQuery(BaseModel):
    text: str | None = None
    author: str | None = None
    category: str | None = None
    id_list: list[str] = Field(default_factory=list)
    date_from: str | None = None   # YYYY-MM-DD
    date_to: str | None = None     # YYYY-MM-DD
    max_results: int = 10


def canonical_query_key(query: ArxivQuery) -> str:
    canonical = json.dumps(query.model_dump(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PaperMeta(BaseModel):
    arxiv_id: str                  # unversioned: "2301.12345" / "math/0211159"
    version: int
    title: str
    authors: list[str]
    abstract: str
    categories: list[str] = Field(default_factory=list)
    doi: str | None = None
    journal_ref: str | None = None
    published: str | None = None   # ISO date of this version, when known

    @property
    def id_v(self) -> str:
        return f"{self.arxiv_id}v{self.version}"


class DownloadedPaper(BaseModel):
    meta: PaperMeta
    pdf_path: Path
    source_tar_path: Path | None = None
    source_error: str | None = None    # absence/failure recorded, not raised


class RateLimiter:
    """1 request / interval_s, single connection, across processes."""

    def __init__(
        self,
        state_dir: Path,
        *,
        interval_s: float = 3.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._state = state_dir / "arxiv-rate"
        self._interval = interval_s
        self._clock = clock
        self._sleep = sleep

    def _last_request(self) -> float:
        try:
            return float(self._state.read_text())
        except (OSError, ValueError):
            return 0.0  # missing/corrupt state: no history, no wait

    @contextmanager
    def slot(self):
        self._state.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self._state):
            wait = self._last_request() + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            try:
                yield  # the request runs here, holding the connection lease
            finally:
                # Record when the request FINISHED: the polite interval is
                # measured from the end of one request to the start of the
                # next, and a slow response must not eat into it.
                self._state.write_text(repr(self._clock()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ratelimit.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/literature/arxiv.py tests/test_ratelimit.py
git commit -m "feat: arXiv models, canonical query keys, interprocess rate limiter"
```

---
### Task 3: `ArxivClient` — cached search, version resolution, capped streaming downloads

**Files:**
- Modify: `src/hardy/literature/arxiv.py` (append the transport protocol, `LibraryTransport`, `ArxivClient`)
- Modify: `pyproject.toml` (add `"arxiv>=2.1"` to `[project] dependencies`)
- Create: `tests/fake_arxiv.py` (shared fixtures: `FakeTransport`, `make_tar`, `TINY_PDF`, `sample_meta`)
- Test: `tests/test_arxiv_client.py`

**Interfaces:**
- Consumes: everything from Task 2, `atomic_replace` (Task 1).
- Produces (Tasks 6, 10, 14 rely on these exact signatures):
  - `ArxivTransport` — `typing.Protocol`: `search(query: ArxivQuery) -> list[PaperMeta]`; `latest_meta(arxiv_id: str) -> PaperMeta`; `download_file(url: str, dest: Path, cap_bytes: int) -> None` (raises `DownloadError` on failure or cap breach; must stream, never buffer whole payloads).
  - `LibraryTransport` — the real implementation; **the only code importing the `arxiv` package** (inside methods, M1's `claude_sdk.py` discipline) plus stdlib `urllib` streaming for files.
  - `ArxivClient(work_dir: Path, *, transport: ArxivTransport | None = None, limiter: RateLimiter | None = None, cache_ttl_s: float = 86_400.0, pdf_cap_bytes: int = 104_857_600, source_cap_bytes: int = 209_715_200, clock: Callable[[], float] = time.time)`:
    - `search(query: ArxivQuery) -> list[PaperMeta]` — disk query cache under `work_dir/.query_cache/<key>.json` with TTL; cache hit issues no network call.
    - `meta(arxiv_id: str) -> PaperMeta` — latest revision's metadata, cached through the same query cache (keyed as an `id_list` query).
    - `resolve_version(arxiv_id: str) -> int` — `meta(arxiv_id).version` (the spec's `resolve_version`, returning the int; `id_v` strings are composed by callers via `PaperMeta.id_v`).
    - `download(meta: PaperMeta) -> DownloadedPaper` — PDF from `https://arxiv.org/pdf/<id_v>`, source from `https://arxiv.org/e-print/<id_v>`, both into a fresh temp dir under `work_dir/.downloads/`; PDF failure raises `ArxivError`; source failure is recorded as `source_error` (PDF-only path). Every network call runs inside `limiter.slot()`.
  - `tests/fake_arxiv.py`: `TINY_PDF: bytes`; `sample_meta(arxiv_id="2301.12345", version=2, **kw) -> PaperMeta`; `make_tar(dir: Path, files: dict[str, bytes]) -> Path` (plain tar.gz of regular files); `FakeTransport(papers: dict[str, dict])` where each value is `{"meta": PaperMeta, "pdf": bytes, "source": bytes | None}` — counts `search_calls`, `meta_calls`, and appends every fetched url to `downloads`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, append `"arxiv>=2.1"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the shared fixtures**

```python
# tests/fake_arxiv.py
"""Shared literature-layer test fixtures: an in-memory ArxivTransport,
a tarball builder, a byte-level fake PDF, and a PaperMeta factory.
No network anywhere."""

import io
import tarfile
from pathlib import Path

from hardy.literature.arxiv import ArxivQuery, DownloadError, PaperMeta

TINY_PDF = b"%PDF-1.4\n% hardy test fixture, not a real document\n%%EOF\n"


def sample_meta(arxiv_id: str = "2301.12345", version: int = 2, **kw) -> PaperMeta:
    defaults = dict(
        arxiv_id=arxiv_id,
        version=version,
        title="A Modular Approach to Widget Theory",
        authors=["Ada Smith", "Bob Jones"],
        abstract="We develop a modular theory of widgets.",
        categories=["math.NT"],
        published="2023-01-15",
    )
    defaults.update(kw)
    return PaperMeta(**defaults)


def make_tar(directory: Path, files: dict[str, bytes]) -> Path:
    """A benign source tarball of regular files (gzip, like arXiv e-prints)."""
    tar_path = directory / "source.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return tar_path


class FakeTransport:
    """papers: arxiv_id -> {"meta": PaperMeta (latest), "pdf": bytes,
    "source": bytes | None}. Serves /pdf/ and /e-print/ URLs by suffix."""

    def __init__(self, papers: dict[str, dict]):
        self.papers = papers
        self.search_calls = 0
        self.meta_calls = 0
        self.downloads: list[str] = []

    def search(self, query: ArxivQuery) -> list[PaperMeta]:
        self.search_calls += 1
        if query.id_list:
            return [
                self.papers[i]["meta"] for i in query.id_list if i in self.papers
            ]
        text = (query.text or "").lower()
        return [
            entry["meta"]
            for entry in self.papers.values()
            if text and text in entry["meta"].title.lower()
        ][: query.max_results]

    def latest_meta(self, arxiv_id: str) -> PaperMeta:
        self.meta_calls += 1
        if arxiv_id not in self.papers:
            raise DownloadError(f"no such paper: {arxiv_id}")
        return self.papers[arxiv_id]["meta"]

    def download_file(self, url: str, dest: Path, cap_bytes: int) -> None:
        self.downloads.append(url)
        for arxiv_id, entry in self.papers.items():
            id_v = entry["meta"].id_v
            if url.endswith(f"/pdf/{id_v}"):
                payload = entry["pdf"]
            elif url.endswith(f"/e-print/{id_v}"):
                if entry["source"] is None:
                    raise DownloadError("404 not found")
                payload = entry["source"]
            else:
                continue
            if len(payload) > cap_bytes:
                raise DownloadError(f"payload exceeded {cap_bytes} byte cap")
            dest.write_bytes(payload)
            return
        raise DownloadError(f"404 not found: {url}")
```

- [ ] **Step 3: Write the failing client tests**

```python
# tests/test_arxiv_client.py
from hardy.literature.arxiv import ArxivClient, ArxivError, ArxivQuery, RateLimiter
from tests.fake_arxiv import TINY_PDF, FakeTransport, sample_meta


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, papers=None, clock=None, **kw):
    clock = clock or FakeClock()
    transport = FakeTransport(papers or {
        "2301.12345": {"meta": sample_meta(), "pdf": TINY_PDF, "source": b"x" * 64},
    })
    limiter = RateLimiter(tmp_path, clock=clock, sleep=clock.sleep)
    client = ArxivClient(
        tmp_path, transport=transport, limiter=limiter, clock=clock, **kw
    )
    return client, transport, clock


def test_search_returns_metadata(tmp_path):
    client, transport, _ = make_client(tmp_path)
    hits = client.search(ArxivQuery(text="modular"))
    assert [h.id_v for h in hits] == ["2301.12345v2"]
    assert transport.search_calls == 1


def test_search_cache_hit_issues_no_network_call(tmp_path):
    client, transport, _ = make_client(tmp_path)
    query = ArxivQuery(text="modular")
    first = client.search(query)
    second = client.search(query)
    assert transport.search_calls == 1
    assert [h.id_v for h in second] == [h.id_v for h in first]


def test_search_cache_expires_after_ttl(tmp_path):
    clock = FakeClock()
    client, transport, clock = make_client(tmp_path, clock=clock, cache_ttl_s=100.0)
    client.search(ArxivQuery(text="modular"))
    clock.now += 101.0
    client.search(ArxivQuery(text="modular"))
    assert transport.search_calls == 2


def test_cache_survives_client_restart(tmp_path):
    client, transport, clock = make_client(tmp_path)
    client.search(ArxivQuery(text="modular"))
    client2, transport2, _ = make_client(tmp_path, clock=clock)
    client2.search(ArxivQuery(text="modular"))
    assert transport2.search_calls == 0      # served from the shared disk cache


def test_resolve_version_returns_latest(tmp_path):
    client, transport, _ = make_client(tmp_path)
    assert client.resolve_version("2301.12345") == 2
    client.resolve_version("2301.12345")     # cached: still one transport call
    assert transport.search_calls + transport.meta_calls == 1


def test_download_fetches_pdf_and_source(tmp_path):
    client, transport, _ = make_client(tmp_path)
    downloaded = client.download(sample_meta())
    assert downloaded.pdf_path.read_bytes() == TINY_PDF
    assert downloaded.source_tar_path is not None
    assert downloaded.source_error is None
    assert any(u.endswith("/pdf/2301.12345v2") for u in transport.downloads)
    assert any(u.endswith("/e-print/2301.12345v2") for u in transport.downloads)


def test_missing_source_is_recorded_not_raised(tmp_path):
    papers = {"2301.12345": {"meta": sample_meta(), "pdf": TINY_PDF, "source": None}}
    client, _, _ = make_client(tmp_path, papers=papers)
    downloaded = client.download(sample_meta())
    assert downloaded.source_tar_path is None
    assert "404" in downloaded.source_error


def test_oversize_source_is_pdf_only(tmp_path):
    papers = {"2301.12345": {
        "meta": sample_meta(), "pdf": TINY_PDF, "source": b"x" * 4096,
    }}
    client, _, _ = make_client(tmp_path, papers=papers, source_cap_bytes=1024)
    downloaded = client.download(sample_meta())
    assert downloaded.source_tar_path is None
    assert "cap" in downloaded.source_error


def test_oversize_pdf_raises(tmp_path):
    papers = {"2301.12345": {
        "meta": sample_meta(), "pdf": b"x" * 4096, "source": None,
    }}
    client, _, _ = make_client(tmp_path, papers=papers, pdf_cap_bytes=1024)
    try:
        client.download(sample_meta())
        raise AssertionError("expected ArxivError")
    except ArxivError as exc:
        assert "cap" in str(exc)


def test_every_network_call_goes_through_the_limiter(tmp_path):
    client, transport, clock = make_client(tmp_path)
    client.search(ArxivQuery(text="modular"))    # 1 network call
    client.download(sample_meta())               # 2 more (pdf + source)
    # back-to-back calls at a frozen clock: each subsequent slot sleeps
    assert len(clock.sleeps) == 2
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_arxiv_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'ArxivClient'`

- [ ] **Step 5: Append the client to `arxiv.py`**

```python
# appended to src/hardy/literature/arxiv.py
import shutil
import urllib.request
import uuid
from typing import Protocol

from .fsutil import atomic_replace

PDF_URL = "https://arxiv.org/pdf/{id_v}"
SOURCE_URL = "https://arxiv.org/e-print/{id_v}"
_USER_AGENT = "hardy/0.1 (theorem-proving harness; mailto:charles.m.siegel@gmail.com)"
_CHUNK = 65536


class ArxivTransport(Protocol):
    def search(self, query: ArxivQuery) -> list[PaperMeta]: ...
    def latest_meta(self, arxiv_id: str) -> PaperMeta: ...
    def download_file(self, url: str, dest: Path, cap_bytes: int) -> None: ...


class LibraryTransport:
    """The real transport: the `arxiv` package for the Atom API (imported
    lazily — this is the only code in hardy that imports it), urllib for
    file downloads, streamed under the caller's byte cap. The cap is
    checked on the compressed bytes AS THEY ARRIVE and the response is
    aborted the moment it is exceeded — materializing whole responses as
    bytes would let one hostile payload exhaust host memory before any
    later quota runs."""

    def _api_client(self):
        import arxiv  # noqa: PLC0415 — the only import of the library

        return arxiv

    @staticmethod
    def _meta_from_result(result) -> PaperMeta:
        # result.entry_id: "http://arxiv.org/abs/2301.12345v2"
        raw = result.entry_id.rsplit("/", 1)[-1]
        arxiv_id, _, version = raw.rpartition("v")
        return PaperMeta(
            arxiv_id=arxiv_id,
            version=int(version),
            title=result.title,
            authors=[a.name for a in result.authors],
            abstract=result.summary,
            categories=list(result.categories),
            doi=result.doi,
            journal_ref=result.journal_ref,
            published=result.published.date().isoformat()
            if result.published else None,
        )

    def search(self, query: ArxivQuery) -> list[PaperMeta]:
        arxiv = self._api_client()
        terms: list[str] = []
        if query.text:
            terms.append(f"all:{query.text}")
        if query.author:
            terms.append(f'au:"{query.author}"')
        if query.category:
            terms.append(f"cat:{query.category}")
        if query.date_from or query.date_to:
            start = (query.date_from or "1991-01-01").replace("-", "")
            end = (query.date_to or "2999-12-31").replace("-", "")
            terms.append(f"submittedDate:[{start}0000 TO {end}2359]")
        search = arxiv.Search(
            query=" AND ".join(terms),
            id_list=query.id_list,
            max_results=query.max_results,
        )
        client = arxiv.Client(page_size=query.max_results, num_retries=2)
        try:
            return [self._meta_from_result(r) for r in client.results(search)]
        except Exception as exc:  # network/HTTP/parse: one structured error type
            raise ArxivError(f"arXiv search failed: {exc}") from exc

    def latest_meta(self, arxiv_id: str) -> PaperMeta:
        hits = self.search(ArxivQuery(id_list=[arxiv_id], max_results=1))
        if not hits:
            raise ArxivError(f"no arXiv record for {arxiv_id!r}")
        return hits[0]

    def download_file(self, url: str, dest: Path, cap_bytes: int) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = 0
                with open(dest, "wb") as out:
                    while chunk := response.read(_CHUNK):
                        total += len(chunk)
                        if total > cap_bytes:
                            raise DownloadError(
                                f"payload exceeded {cap_bytes} byte cap: {url}"
                            )
                        out.write(chunk)
        except DownloadError:
            dest.unlink(missing_ok=True)
            raise
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise DownloadError(f"download failed: {url}: {exc}") from exc


class ArxivClient:
    """Search/metadata/download façade. Policy lives HERE, not in the
    transport: rate limiting on every network call, a TTL'd disk query
    cache (searches are cheap to redo but the agent may loop), and
    per-payload download caps."""

    def __init__(
        self,
        work_dir: Path,
        *,
        transport: ArxivTransport | None = None,
        limiter: RateLimiter | None = None,
        cache_ttl_s: float = 86_400.0,
        pdf_cap_bytes: int = 104_857_600,
        source_cap_bytes: int = 209_715_200,
        clock: Callable[[], float] = time.time,
    ):
        self._work_dir = work_dir
        self._transport = transport or LibraryTransport()
        self._limiter = limiter or RateLimiter(work_dir, clock=clock)
        self._cache_dir = work_dir / ".query_cache"
        self._cache_ttl_s = cache_ttl_s
        self._pdf_cap = pdf_cap_bytes
        self._source_cap = source_cap_bytes
        self._clock = clock

    def _cached(self, key: str) -> list[PaperMeta] | None:
        path = self._cache_dir / f"{key}.json"
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if self._clock() - payload["at"] > self._cache_ttl_s:
            return None
        return [PaperMeta.model_validate(m) for m in payload["results"]]

    def _store_cache(self, key: str, results: list[PaperMeta]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_replace(
            self._cache_dir / f"{key}.json",
            json.dumps({
                "at": self._clock(),
                "results": [m.model_dump() for m in results],
            }),
        )

    def search(self, query: ArxivQuery) -> list[PaperMeta]:
        key = canonical_query_key(query)
        cached = self._cached(key)
        if cached is not None:
            return cached
        with self._limiter.slot():
            results = self._transport.search(query)
        self._store_cache(key, results)
        return results

    def meta(self, arxiv_id: str) -> PaperMeta:
        hits = self.search(ArxivQuery(id_list=[arxiv_id], max_results=1))
        if not hits:
            raise ArxivError(f"no arXiv record for {arxiv_id!r}")
        return hits[0]

    def resolve_version(self, arxiv_id: str) -> int:
        return self.meta(arxiv_id).version

    def download(self, meta: PaperMeta) -> DownloadedPaper:
        dest = self._work_dir / ".downloads" / f"{meta.id_v.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
        dest.mkdir(parents=True, exist_ok=True)
        pdf_path = dest / "paper.pdf"
        try:
            with self._limiter.slot():
                self._transport.download_file(
                    PDF_URL.format(id_v=meta.id_v), pdf_path, self._pdf_cap
                )
        except DownloadError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            raise ArxivError(f"PDF download failed for {meta.id_v}: {exc}") from exc
        source_path: Path | None = dest / "source.tar"
        source_error: str | None = None
        try:
            with self._limiter.slot():
                self._transport.download_file(
                    SOURCE_URL.format(id_v=meta.id_v), source_path, self._source_cap
                )
        except DownloadError as exc:
            source_path, source_error = None, str(exc)   # PDF-only, recorded
        return DownloadedPaper(
            meta=meta,
            pdf_path=pdf_path,
            source_tar_path=source_path,
            source_error=source_error,
        )
```

Note for the implementer: `test_every_network_call_goes_through_the_limiter` counts **2** sleeps for 3 back-to-back network calls (search, pdf, source) because the first slot has no history. The search in `test_resolve_version_returns_latest` routes through `search()`'s cache, so the transport sees exactly one call however `FakeTransport` counts it — the assertion sums both counters to stay implementation-neutral.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_arxiv_client.py tests/test_ratelimit.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/literature/arxiv.py tests/fake_arxiv.py tests/test_arxiv_client.py pyproject.toml
git commit -m "feat: ArxivClient — cached search, version resolution, capped streaming downloads"
```

---

### Task 4: Safe tar extraction (`store.py`, part 1)

**Files:**
- Create: `src/hardy/literature/store.py` (extraction only; journal and store follow in Tasks 5–6)
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: nothing hardy-internal (stdlib + pydantic).
- Produces (Tasks 6, 9 rely on these exact signatures):
  - `DEFAULT_MAX_EXTRACT_BYTES = 536_870_912` (512 MB), `DEFAULT_MAX_EXTRACT_FILES = 10_000`.
  - `ExtractionReport(ok: bool, files: int = 0, bytes_extracted: int = 0, skipped: list[str] = [], aborted: str | None = None)` (pydantic) — `skipped` names every rejected member with its reason; `aborted` is set on quota breach or tar corruption (and `ok` is False).
  - `extract_tar_safe(tar_path: Path, dest: Path, *, max_bytes: int = DEFAULT_MAX_EXTRACT_BYTES, max_files: int = DEFAULT_MAX_EXTRACT_FILES) -> ExtractionReport` — the caller owns cleanup of `dest` on abort (Task 6's `admit` deletes it and goes PDF-only).

**Behavior contract (from the spec, each clause carries a test):**
1. Only regular files and directories are extracted; symlinks, hardlinks, devices, and FIFOs are skipped and logged in the report.
2. Paths are normalized and confined under `dest`: absolute paths and any path escaping via `..` are skipped and logged — hostile *names* never abort the whole extraction.
3. Quotas — total bytes and file count — abort extraction; the report records the breach.
4. A corrupt archive aborts with the tar error recorded; nothing raises.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract.py
import io
import tarfile

from hardy.literature.store import ExtractionReport, extract_tar_safe


def build_tar(path, members):
    """members: list of (TarInfo, bytes | None)."""
    with tarfile.open(path, "w:gz") as tar:
        for info, content in members:
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    return path


def reg(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    return info, content


def special(name: str, kind: bytes, linkname: str = "") -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info, None


def test_benign_tarball_extracts_fully(tmp_path):
    tar = build_tar(tmp_path / "t.tar.gz", [
        reg("main.tex", b"\\documentclass{article}"),
        reg("sections/intro.tex", b"\\section{Intro}"),
    ])
    report = extract_tar_safe(tar, tmp_path / "out")
    assert report.ok and report.aborted is None
    assert report.files == 2
    assert (tmp_path / "out" / "main.tex").read_bytes().startswith(b"\\document")
    assert (tmp_path / "out" / "sections" / "intro.tex").exists()


def test_traversal_and_absolute_paths_skipped_not_extracted(tmp_path):
    outside = tmp_path / "escaped.txt"
    tar = build_tar(tmp_path / "t.tar.gz", [
        reg("../escaped.txt", b"evil"),
        reg("/abs/escaped.txt", b"evil"),
        reg("nested/../../escaped.txt", b"evil"),
        reg("ok.tex", b"fine"),
    ])
    report = extract_tar_safe(tar, tmp_path / "out")
    assert report.ok
    assert report.files == 1                       # only ok.tex
    assert len(report.skipped) == 3
    assert not outside.exists()
    # nothing anywhere outside dest
    assert not (tmp_path / "abs").exists()


def test_symlink_hardlink_device_fifo_skipped(tmp_path):
    tar = build_tar(tmp_path / "t.tar.gz", [
        special("link", tarfile.SYMTYPE, linkname="../../target"),
        special("hard", tarfile.LNKTYPE, linkname="main.tex"),
        special("dev", tarfile.CHRTYPE),
        special("fifo", tarfile.FIFOTYPE),
        reg("main.tex", b"fine"),
    ])
    report = extract_tar_safe(tar, tmp_path / "out")
    assert report.ok and report.files == 1
    assert len(report.skipped) == 4
    assert not (tmp_path / "out" / "link").exists()
    joined = " ".join(report.skipped)
    for tag in ("symlink", "hardlink", "device", "fifo"):
        assert tag in joined


def test_byte_quota_breach_aborts(tmp_path):
    tar = build_tar(tmp_path / "t.tar.gz", [
        reg("big.bin", b"x" * 4096),
    ])
    report = extract_tar_safe(tar, tmp_path / "out", max_bytes=1024)
    assert not report.ok
    assert "byte" in report.aborted


def test_file_count_quota_breach_aborts(tmp_path):
    tar = build_tar(tmp_path / "t.tar.gz", [
        reg(f"f{i}.txt", b"x") for i in range(5)
    ])
    report = extract_tar_safe(tar, tmp_path / "out", max_files=3)
    assert not report.ok
    assert "count" in report.aborted


def test_corrupt_tar_aborts_with_reason(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"this is not a tarball at all")
    report = extract_tar_safe(bad, tmp_path / "out")
    assert not report.ok
    assert "tar" in report.aborted.lower()


def test_report_model_round_trips():
    report = ExtractionReport(ok=False, files=1, aborted="byte quota")
    assert ExtractionReport.model_validate_json(report.model_dump_json()) == report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.literature.store'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/literature/store.py
"""Version-keyed immutable paper store (M3 spec).

This file grows in three layers, matching the spec's admission pipeline:
1. extract_tar_safe — downloads are untrusted; only regular files and
   directories, path-normalized and confined, under byte and file-count
   quotas. (this task)
2. DigestJournal — the fsynced JSONL event journal behind
   papers/DIGESTS.json. (Task 5)
3. PaperStore — journaled, atomic, crash-safe admission. (Task 6)
"""

import os
import tarfile
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_MAX_EXTRACT_BYTES = 536_870_912   # 512 MB
DEFAULT_MAX_EXTRACT_FILES = 10_000

_SPECIAL_KINDS = {
    tarfile.SYMTYPE: "symlink",
    tarfile.LNKTYPE: "hardlink",
    tarfile.CHRTYPE: "device",
    tarfile.BLKTYPE: "device",
    tarfile.FIFOTYPE: "fifo",
}


class ExtractionReport(BaseModel):
    ok: bool
    files: int = 0
    bytes_extracted: int = 0
    skipped: list[str] = Field(default_factory=list)
    aborted: str | None = None


def _confined_path(dest: Path, name: str) -> Path | None:
    """The member's target under dest, or None if the name escapes."""
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None  # absolute POSIX or Windows-drive path
    normalized = os.path.normpath(name).replace("\\", "/")
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        return None
    target = dest / normalized
    try:
        target.resolve().relative_to(dest.resolve())
    except ValueError:
        return None
    return target


def extract_tar_safe(
    tar_path: Path,
    dest: Path,
    *,
    max_bytes: int = DEFAULT_MAX_EXTRACT_BYTES,
    max_files: int = DEFAULT_MAX_EXTRACT_FILES,
) -> ExtractionReport:
    report = ExtractionReport(ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            for member in tar:
                kind = _SPECIAL_KINDS.get(member.type)
                if kind is not None:
                    report.skipped.append(f"{kind}: {member.name}")
                    continue
                if not (member.isreg() or member.isdir()):
                    report.skipped.append(f"unsupported type: {member.name}")
                    continue
                target = _confined_path(dest, member.name)
                if target is None:
                    report.skipped.append(f"path escape: {member.name}")
                    continue
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if report.files + 1 > max_files:
                    report.ok = False
                    report.aborted = (
                        f"file-count quota exceeded ({max_files} files)"
                    )
                    return report
                if report.bytes_extracted + member.size > max_bytes:
                    report.ok = False
                    report.aborted = f"byte quota exceeded ({max_bytes} bytes)"
                    return report
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = tar.extractfile(member)
                if handle is None:
                    report.skipped.append(f"unreadable member: {member.name}")
                    continue
                with open(target, "wb") as out:
                    while chunk := handle.read(65536):
                        out.write(chunk)
                report.files += 1
                report.bytes_extracted += member.size
    except tarfile.TarError as exc:
        report.ok = False
        report.aborted = f"tar error: {exc}"
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/literature/store.py tests/test_extract.py
git commit -m "feat: safe extraction of untrusted e-print tarballs with quotas and confinement"
```

---
### Task 5: The digest event journal (`store.py`, part 2)

**Files:**
- Modify: `src/hardy/literature/store.py` (append `JournalError`, `DigestJournal`)
- Test: `tests/test_digest_journal.py`

**Interfaces:**
- Consumes: `append_line_durable`, `file_lock` (Task 1).
- Produces (Task 6 relies on these exact signatures):
  - `JournalError(Exception)`.
  - `DigestJournal(papers_dir: Path)` with:
    - `path: Path` — `papers_dir / "DIGESTS.json"` (the spec's name; the *content* is JSONL events, per the spec's "fsynced JSONL event journal" clause).
    - `lock() -> FileLock` — `file_lock(self.path)`, i.e. `DIGESTS.json.lock`. `PaperStore.admit` holds this for the whole admission.
    - `read_events() -> list[dict]` — parses every line; a malformed **final** line is ignored (a torn append from a crash mid-write before fsync returned); a malformed line anywhere else raises `JournalError` (real corruption, surfaced loudly).
    - `committed_digest(id_v: str) -> str | None` — the digest of the last `committed` event for `id_v`, else None.
    - `append(event: str, id_v: str, sha256: str) -> None` — one complete, individually-fsynced JSONL line `{"event", "id_v", "sha256", "at"}`. **The only write primitive** — there is no rewrite path at all.
    - `recover(entry_exists: Callable[[str], bool]) -> dict[str, str]` — call **only while holding `lock()`**: for every id_v whose last event is `pending`, appends `committed` (entry present) or `superseded` (entry absent), returning `{id_v: action}` for the caller's logs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_digest_journal.py
import json

import pytest

from hardy.literature.store import DigestJournal, JournalError


def test_append_and_committed_digest(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "2301.12345v2", "a" * 64)
    journal.append("committed", "2301.12345v2", "a" * 64)
    assert journal.committed_digest("2301.12345v2") == "a" * 64
    assert journal.committed_digest("9999.99999v1") is None


def test_pending_without_committed_yields_no_digest(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "2301.12345v2", "a" * 64)
    assert journal.committed_digest("2301.12345v2") is None


def test_events_carry_all_fields(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "x", "b" * 64)
    [event] = journal.read_events()
    assert event["event"] == "pending"
    assert event["id_v"] == "x"
    assert event["sha256"] == "b" * 64
    assert isinstance(event["at"], float)


def test_journal_is_append_only(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "x", "b" * 64)
    before = journal.path.read_bytes()
    journal.append("committed", "x", "b" * 64)
    assert journal.path.read_bytes().startswith(before)


def test_torn_final_line_is_ignored(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "x", "b" * 64)
    journal.append("committed", "x", "b" * 64)
    with open(journal.path, "ab") as handle:
        handle.write(b'{"event": "pending", "id_v": "y", "sha')   # torn write
    assert journal.committed_digest("x") == "b" * 64
    assert len(journal.read_events()) == 2


def test_malformed_interior_line_raises(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.path.write_text('garbage line\n{"event": "pending", "id_v": "x", "sha256": "c", "at": 1.0}\n')
    with pytest.raises(JournalError):
        journal.read_events()


def test_recover_commits_pending_with_entry_present(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "2301.12345v2", "a" * 64)
    with journal.lock():
        actions = journal.recover(lambda id_v: True)
    assert actions == {"2301.12345v2": "committed"}
    assert journal.committed_digest("2301.12345v2") == "a" * 64


def test_recover_supersedes_pending_with_no_entry(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "2301.12345v2", "a" * 64)
    with journal.lock():
        actions = journal.recover(lambda id_v: False)
    assert actions == {"2301.12345v2": "superseded"}
    assert journal.committed_digest("2301.12345v2") is None
    # a superseded version can be re-admitted later: a fresh pending works
    journal.append("pending", "2301.12345v2", "a" * 64)
    with journal.lock():
        assert journal.recover(lambda id_v: True) == {"2301.12345v2": "committed"}


def test_recover_is_noop_on_clean_journal(tmp_path):
    journal = DigestJournal(tmp_path)
    journal.append("pending", "x", "a" * 64)
    journal.append("committed", "x", "a" * 64)
    with journal.lock():
        assert journal.recover(lambda id_v: True) == {}


def test_last_committed_wins(tmp_path):
    # A superseded admission followed by a successful one: the digest of
    # the LAST committed event answers.
    journal = DigestJournal(tmp_path)
    journal.append("pending", "x", "a" * 64)
    journal.append("superseded", "x", "a" * 64)
    journal.append("pending", "x", "d" * 64)
    journal.append("committed", "x", "d" * 64)
    assert journal.committed_digest("x") == "d" * 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_digest_journal.py -v`
Expected: FAIL — `ImportError: cannot import name 'DigestJournal'`

- [ ] **Step 3: Append the implementation to `store.py`**

```python
# appended to src/hardy/literature/store.py
import json
import time
from collections.abc import Callable

from filelock import FileLock

from .fsutil import append_line_durable, file_lock


class JournalError(Exception):
    pass


class DigestJournal:
    """papers/DIGESTS.json: the durable record of every stored version's
    content digest — the one piece of manifest data that must survive a
    fresh clone (the store itself is gitignored data).

    Physically an event journal: complete, individually fsynced JSONL
    appends (pending / committed / superseded), NEVER an in-place
    rewrite — the lock serializes writers but protects nothing against a
    crash mid-write, and a truncated rewrite would lose the durable
    history for every stored version at once."""

    def __init__(self, papers_dir: Path):
        self.path = papers_dir / "DIGESTS.json"

    def lock(self) -> FileLock:
        return file_lock(self.path)

    def append(self, event: str, id_v: str, sha256: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_line_durable(
            self.path,
            json.dumps(
                {"event": event, "id_v": id_v, "sha256": sha256, "at": time.time()},
                sort_keys=True,
            ),
        )

    def read_events(self) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()
        events: list[dict] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError as exc:
                if index == len(lines) - 1:
                    break  # torn final append from a crash: ignorable
                raise JournalError(
                    f"corrupt journal line {index + 1} in {self.path}: {line[:80]!r}"
                ) from exc
        return events

    def committed_digest(self, id_v: str) -> str | None:
        digest: str | None = None
        for event in self.read_events():
            if event.get("id_v") == id_v and event.get("event") == "committed":
                digest = event.get("sha256")
        return digest

    def recover(self, entry_exists: Callable[[str], bool]) -> dict[str, str]:
        """Reconcile journal state against the store. Caller holds lock().

        pending + entry present -> committed (the crash landed between the
        rename and the committed append; the entry is real and durable).
        pending + no entry -> superseded (the crash landed before the
        rename; nothing was admitted)."""
        last: dict[str, dict] = {}
        for event in self.read_events():
            last[event["id_v"]] = event
        actions: dict[str, str] = {}
        for id_v, event in last.items():
            if event["event"] != "pending":
                continue
            action = "committed" if entry_exists(id_v) else "superseded"
            self.append(action, id_v, event["sha256"])
            actions[id_v] = action
        return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_digest_journal.py tests/test_extract.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/literature/store.py tests/test_digest_journal.py
git commit -m "feat: fsynced JSONL digest event journal with crash reconciliation"
```

---

### Task 6: `PaperStore` — journaled, atomic, crash-safe admission (`store.py`, part 3)

**Files:**
- Modify: `src/hardy/literature/store.py` (append `PaperStoreError`, `StoredPaper`, `PaperStore`)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `extract_tar_safe`/`ExtractionReport` (Task 4), `DigestJournal` (Task 5), `write_durable`/`fsync_tree`/`fsync_dir` (Task 1), `DownloadedPaper`/`PaperMeta` (Task 2).
- Produces (Tasks 9, 10, 13, 14 rely on these exact signatures):
  - `PaperStoreError(Exception)`.
  - `StoredPaper(id_v: str, path: Path, meta: PaperMeta, pdf_sha256: str, source_available: bool, source_error: str | None = None, extraction: ExtractionReport | None = None)` (pydantic).
  - `PaperStore(papers_dir: Path, *, max_extract_bytes: int = DEFAULT_MAX_EXTRACT_BYTES, max_extract_files: int = DEFAULT_MAX_EXTRACT_FILES, fault: Callable[[str], None] | None = None)`:
    - `get(id_v: str) -> StoredPaper | None` — reads `papers/<id_v-with-/-as-->/meta.json`; no lock needed (entries are immutable once visible).
    - `admit(downloaded: DownloadedPaper) -> StoredPaper` — the full journaled admission under `journal.lock()` (order per spec: recover → digest check → stage → **pending** → fsync tree → rename → fsync parent → **committed**). Idempotent when the same version with the same digest is re-admitted; `PaperStoreError` on digest mismatch (upstream mutation of a published version); a version directory is never overwritten.
    - `entry_dir(id_v: str) -> Path` — `papers_dir / id_v.replace("/", "-")` (old-style ids like `math/0211159v1` must not create nested directories).
  - `fault` is a test-only crash-injection hook called at named points `"after-pending"` and `"after-rename"`; production leaves it None.

**Entry layout** (per spec): `papers/<id_v>/paper.pdf`, `papers/<id_v>/source/` (only when extraction succeeded), `papers/<id_v>/meta.json` = `{"meta": …, "fetched_at": …, "pdf_sha256": …, "source_error": …, "extraction": …}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
import hashlib
import json

import pytest

from hardy.literature.arxiv import DownloadedPaper
from hardy.literature.store import DigestJournal, PaperStore, PaperStoreError
from tests.fake_arxiv import TINY_PDF, make_tar, sample_meta


class SimulatedCrash(BaseException):
    """BaseException so no internal except-Exception swallows the 'crash'."""


def downloaded(tmp_path, meta=None, source_files=None, source_error=None):
    meta = meta or sample_meta()
    pdf = tmp_path / "dl-paper.pdf"
    pdf.write_bytes(TINY_PDF)
    tar = None
    if source_files is not None:
        tar = make_tar(tmp_path, source_files)
    return DownloadedPaper(
        meta=meta, pdf_path=pdf, source_tar_path=tar, source_error=source_error
    )


def test_admit_and_get_round_trip(tmp_path):
    store = PaperStore(tmp_path / "papers")
    d = downloaded(tmp_path, source_files={"main.tex": b"\\documentclass{article}"})
    stored = store.admit(d)
    assert stored.id_v == "2301.12345v2"
    assert stored.source_available
    assert stored.pdf_sha256 == hashlib.sha256(TINY_PDF).hexdigest()
    assert (stored.path / "paper.pdf").read_bytes() == TINY_PDF
    assert (stored.path / "source" / "main.tex").exists()
    again = store.get("2301.12345v2")
    assert again is not None
    assert again.pdf_sha256 == stored.pdf_sha256
    assert again.meta.title == d.meta.title


def test_get_missing_returns_none(tmp_path):
    assert PaperStore(tmp_path / "papers").get("9999.00000v1") is None


def test_admit_publishes_pending_then_committed(tmp_path):
    store = PaperStore(tmp_path / "papers")
    store.admit(downloaded(tmp_path, source_files={}))
    journal = DigestJournal(tmp_path / "papers")
    kinds = [e["event"] for e in journal.read_events()
             if e["id_v"] == "2301.12345v2"]
    assert kinds == ["pending", "committed"]
    assert journal.committed_digest("2301.12345v2") == \
        hashlib.sha256(TINY_PDF).hexdigest()


def test_admit_without_source_is_pdf_only(tmp_path):
    store = PaperStore(tmp_path / "papers")
    stored = store.admit(downloaded(tmp_path, source_error="404 not found"))
    assert not stored.source_available
    assert "404" in stored.source_error
    assert not (stored.path / "source").exists()


def test_hostile_source_admits_pdf_only_with_report(tmp_path):
    store = PaperStore(tmp_path / "papers", max_extract_bytes=64)
    stored = store.admit(
        downloaded(tmp_path, source_files={"huge.bin": b"x" * 4096})
    )
    assert not stored.source_available
    assert stored.extraction is not None and not stored.extraction.ok
    assert "byte" in stored.extraction.aborted
    assert not (stored.path / "source").exists()
    assert (stored.path / "paper.pdf").exists()


def test_readmit_same_digest_is_idempotent(tmp_path):
    store = PaperStore(tmp_path / "papers")
    first = store.admit(downloaded(tmp_path, source_files={}))
    second = store.admit(downloaded(tmp_path, source_files={}))
    assert second.path == first.path
    # no duplicate committed events piled up
    journal = DigestJournal(tmp_path / "papers")
    kinds = [e["event"] for e in journal.read_events()]
    assert kinds == ["pending", "committed"]


def test_digest_mismatch_is_an_error(tmp_path):
    store = PaperStore(tmp_path / "papers")
    store.admit(downloaded(tmp_path, source_files={}))
    mutated = downloaded(tmp_path, source_files={})
    mutated.pdf_path.write_bytes(b"%PDF-1.4 DIFFERENT CONTENT\n%%EOF\n")
    with pytest.raises(PaperStoreError, match="digest"):
        store.admit(mutated)


def test_digest_check_survives_fresh_clone(tmp_path):
    # Journal committed, entry deleted (fresh clone keeps DIGESTS.json,
    # gitignores the entries): a refetch with mutated content must fail.
    store = PaperStore(tmp_path / "papers")
    stored = store.admit(downloaded(tmp_path, source_files={}))
    import shutil
    shutil.rmtree(stored.path)
    mutated = downloaded(tmp_path, source_files={})
    mutated.pdf_path.write_bytes(b"%PDF-1.4 DIFFERENT\n%%EOF\n")
    with pytest.raises(PaperStoreError, match="digest"):
        PaperStore(tmp_path / "papers").admit(mutated)


def test_refetch_after_clone_with_matching_digest_succeeds(tmp_path):
    store = PaperStore(tmp_path / "papers")
    stored = store.admit(downloaded(tmp_path, source_files={}))
    import shutil
    shutil.rmtree(stored.path)
    stored2 = PaperStore(tmp_path / "papers").admit(
        downloaded(tmp_path, source_files={})
    )
    assert stored2.pdf_sha256 == stored.pdf_sha256
    assert stored2.path.exists()


def test_versions_are_distinct_entries(tmp_path):
    store = PaperStore(tmp_path / "papers")
    v2 = store.admit(downloaded(tmp_path, source_files={}))
    v3 = store.admit(
        downloaded(tmp_path, meta=sample_meta(version=3), source_files={})
    )
    assert v2.path != v3.path
    assert store.get("2301.12345v2") is not None
    assert store.get("2301.12345v3") is not None


def test_old_style_id_maps_to_flat_dir(tmp_path):
    store = PaperStore(tmp_path / "papers")
    stored = store.admit(downloaded(
        tmp_path, meta=sample_meta(arxiv_id="math/0211159", version=1),
        source_files={},
    ))
    assert stored.path.name == "math-0211159v1"
    assert store.get("math/0211159v1") is not None


def test_crash_after_pending_leaves_no_entry_and_recovers(tmp_path):
    def crash(point: str) -> None:
        if point == "after-pending":
            raise SimulatedCrash

    store = PaperStore(tmp_path / "papers", fault=crash)
    with pytest.raises(SimulatedCrash):
        store.admit(downloaded(tmp_path, source_files={}))
    # nothing visible: a partially admitted entry can never be observed
    fresh = PaperStore(tmp_path / "papers")
    assert fresh.get("2301.12345v2") is None
    assert not list((tmp_path / "papers").glob(".staging-*"))
    # recovery (run inside the next admit) supersedes, then re-admits
    stored = fresh.admit(downloaded(tmp_path, source_files={}))
    assert stored.path.exists()
    journal = DigestJournal(tmp_path / "papers")
    kinds = [e["event"] for e in journal.read_events()]
    assert kinds == ["pending", "superseded", "pending", "committed"]


def test_crash_after_rename_recovers_to_committed(tmp_path):
    def crash(point: str) -> None:
        if point == "after-rename":
            raise SimulatedCrash

    store = PaperStore(tmp_path / "papers", fault=crash)
    with pytest.raises(SimulatedCrash):
        store.admit(downloaded(tmp_path, source_files={}))
    # entry is visible but the committed event never landed
    fresh = PaperStore(tmp_path / "papers")
    assert fresh.get("2301.12345v2") is not None
    # next admit's recovery blesses it: pending + entry present -> committed
    stored = fresh.admit(downloaded(tmp_path, source_files={}))
    assert stored.path.exists()
    journal = DigestJournal(tmp_path / "papers")
    kinds = [e["event"] for e in journal.read_events()]
    assert kinds == ["pending", "committed"]
    assert journal.committed_digest("2301.12345v2") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'PaperStore'`

- [ ] **Step 3: Append the implementation to `store.py`**

```python
# appended to src/hardy/literature/store.py
import hashlib
import shutil
import uuid

from .arxiv import DownloadedPaper, PaperMeta
from .fsutil import fsync_dir, fsync_tree, write_durable


class PaperStoreError(Exception):
    pass


class StoredPaper(BaseModel):
    id_v: str
    path: Path
    meta: PaperMeta
    pdf_sha256: str
    source_available: bool
    source_error: str | None = None
    extraction: ExtractionReport | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


class PaperStore:
    """papers/<id_v>/ — immutable version-keyed entries, admitted atomically
    under the digest journal's lock.

    Admission order (spec): recover -> digest check -> stage in an isolated
    temp dir -> append PENDING -> fsync the staged tree -> rename into
    place -> fsync the parent -> append COMMITTED. A crash at any point
    either leaves nothing visible (pre-rename: recovery supersedes the
    pending event) or leaves a complete, durable entry whose committed
    event recovery replays (post-rename)."""

    def __init__(
        self,
        papers_dir: Path,
        *,
        max_extract_bytes: int = DEFAULT_MAX_EXTRACT_BYTES,
        max_extract_files: int = DEFAULT_MAX_EXTRACT_FILES,
        fault: Callable[[str], None] | None = None,
    ):
        self.papers_dir = papers_dir
        self.journal = DigestJournal(papers_dir)
        self._max_bytes = max_extract_bytes
        self._max_files = max_extract_files
        self._fault = fault or (lambda point: None)

    def entry_dir(self, id_v: str) -> Path:
        return self.papers_dir / id_v.replace("/", "-")

    def get(self, id_v: str) -> StoredPaper | None:
        meta_path = self.entry_dir(id_v) / "meta.json"
        if not meta_path.exists():
            return None
        payload = json.loads(meta_path.read_text())
        extraction = payload.get("extraction")
        return StoredPaper(
            id_v=id_v,
            path=self.entry_dir(id_v),
            meta=PaperMeta.model_validate(payload["meta"]),
            pdf_sha256=payload["pdf_sha256"],
            source_available=(self.entry_dir(id_v) / "source").is_dir(),
            source_error=payload.get("source_error"),
            extraction=(
                ExtractionReport.model_validate(extraction) if extraction else None
            ),
        )

    def admit(self, downloaded: DownloadedPaper) -> StoredPaper:
        id_v = downloaded.meta.id_v
        entry = self.entry_dir(id_v)
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        with self.journal.lock():
            self.journal.recover(lambda v: self.entry_dir(v).exists())
            pdf_digest = _sha256_file(downloaded.pdf_path)
            committed = self.journal.committed_digest(id_v)
            if committed is not None and committed != pdf_digest:
                raise PaperStoreError(
                    f"digest mismatch for {id_v}: the committed ledger records "
                    f"{committed[:12]}…, this download is {pdf_digest[:12]}… — "
                    "upstream mutation of a published version is anomalous; "
                    "refusing to admit"
                )
            if entry.exists():
                existing = self.get(id_v)
                if existing is not None and existing.pdf_sha256 != pdf_digest:
                    raise PaperStoreError(
                        f"digest mismatch for stored {id_v}; refusing to admit"
                    )
                return existing  # idempotent: never overwrite, never re-fetch

            staging = self.papers_dir / f".staging-{entry.name}-{uuid.uuid4().hex[:8]}"
            staging.mkdir()
            try:
                write_durable(
                    staging / "paper.pdf", downloaded.pdf_path.read_bytes()
                )
                extraction: ExtractionReport | None = None
                if downloaded.source_tar_path is not None:
                    extraction = extract_tar_safe(
                        downloaded.source_tar_path,
                        staging / "source",
                        max_bytes=self._max_bytes,
                        max_files=self._max_files,
                    )
                    if not extraction.ok:
                        # PDF-only admission: the abort stays visible in the
                        # report; the fetch itself never fails for this.
                        shutil.rmtree(staging / "source", ignore_errors=True)
                write_durable(
                    staging / "meta.json",
                    json.dumps(
                        {
                            "meta": downloaded.meta.model_dump(),
                            "fetched_at": time.time(),
                            "pdf_sha256": pdf_digest,
                            "source_error": downloaded.source_error,
                            "extraction": (
                                extraction.model_dump() if extraction else None
                            ),
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                )
                self.journal.append("pending", id_v, pdf_digest)
                self._fault("after-pending")
                fsync_tree(staging)
                os.rename(staging, entry)
                fsync_dir(self.papers_dir)
                self._fault("after-rename")
                self.journal.append("committed", id_v, pdf_digest)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        stored = self.get(id_v)
        assert stored is not None
        return stored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py tests/test_digest_journal.py tests/test_extract.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/literature/store.py tests/test_store.py
git commit -m "feat: PaperStore — journaled, atomic, crash-safe immutable admission"
```

---
### Task 7: Bibliography — model, confinement, key minting, locked transactions

**Files:**
- Create: `src/hardy/literature/bibliography.py`
- Modify: `pyproject.toml` (add `"bibtexparser>=1.4,<2"` to `[project] dependencies`)
- Test: `tests/test_bibliography.py`

**Interfaces:**
- Consumes: `PaperMeta` (Task 2), `atomic_replace`/`file_lock` (Task 1), `hardy.latex.confine.violations` + `hardy.latex.template.escape_text` (**M1 plan Task 5 — plan-assumption 2/3**).
- Produces (Tasks 8, 10, 12, 13, 14 rely on these exact signatures):
  - `BibliographyError(Exception)`.
  - `BibEntry(key: str, entry_type: Literal["article", "misc"], title: str, author: str, year: str, eprint: str | None = None, archive_prefix: str = "arXiv", primary_class: str | None = None, doi: str | None = None, journal: str | None = None, note: str | None = None)` (pydantic). `author` is the BibTeX `" and "`-joined string; `eprint` carries the **version-qualified** id (`"2301.12345v2"`) so dedup-by-version needs no side table; `note` is `"arXiv <id_v>"` per spec.
  - `confine_bib_text(text: str) -> str` — returns `text` unchanged when `violations(text)` is empty (benign math/formatting preserved), else `escape_text(text)` (per-character escaped). Network-controlled text is TeX that the writeup pipeline will later execute; parsing is not sanitization.
  - `mint_key(meta: PaperMeta) -> str` — `<surname><year><word>-<id-fragment>` (+ `V<n>` when `version != 1`): ASCII-folded, lowercased; fragment is `arxiv_id` with `/` → `-`. A **pure function of the paper** — identical across any fetch order or rebuild.
  - `entry_from_meta(meta: PaperMeta) -> BibEntry` — mints the key, confines title/author/journal, picks `article` (journal_ref or DOI present) vs `misc`.
  - `Bibliography(entries: dict[str, BibEntry] | None = None)` with classmethod `load(path: Path) -> Bibliography` (missing file → empty), `save(path: Path) -> None` (normalized: entries sorted by key, fields sorted; via `atomic_replace`), `entry_for(meta: PaperMeta) -> BibEntry | None` (dedup rule), `add(entry: BibEntry) -> None`, `keys() -> list[str]`, `fragment(keys: list[str]) -> str` (serialized subset — the staged-fragment source for Task 12).
  - `add_or_get(path: Path, meta: PaperMeta) -> str` — **the single write path** to the `.bib`: the whole load → dedup/mint → save transaction under `file_lock(path)` (`references.bib.lock`).
  - `validate(path: Path) -> list[str]` — parse errors, duplicate keys, entries missing required fields; empty list means valid. Task 8 wires it into CI.

**Dedup rule (spec, each clause carries a test):** match by exact `eprint == meta.id_v`; the DOI fallback applies **only** to entries with no arXiv identity (`eprint is None`) — revisions of one arXiv paper share a DOI and must never collapse through it. Distinct versions always get distinct entries and version-qualified keys.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, append `"bibtexparser>=1.4,<2"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_bibliography.py
from hardy.literature.bibliography import (
    BibEntry,
    Bibliography,
    add_or_get,
    confine_bib_text,
    entry_from_meta,
    mint_key,
    validate,
)
from tests.fake_arxiv import sample_meta


def test_mint_key_shape_and_fragment():
    key = mint_key(sample_meta(version=1))
    assert key == "smith2023modular-2301.12345"


def test_mint_key_version_qualified_for_non_v1():
    assert mint_key(sample_meta(version=3)) == "smith2023modular-2301.12345V3"


def test_mint_key_is_pure_function_of_the_paper():
    assert mint_key(sample_meta()) == mint_key(sample_meta())


def test_mint_key_old_style_id_fragment():
    meta = sample_meta(arxiv_id="math/0211159", version=1)
    assert mint_key(meta).endswith("-math-0211159")


def test_mint_key_ascii_folds_and_skips_stopwords():
    meta = sample_meta(
        authors=["Ólafur Þórsson"], title="On the Éclat of Números", version=1
    )
    key = mint_key(meta)
    assert key.startswith("thorsson2023eclat-") or key.startswith("orsson2023eclat-")
    assert key.isascii()


def test_mint_key_comma_author_form():
    meta = sample_meta(authors=["Smith, Ada"], version=1)
    assert mint_key(meta).startswith("smith2023")


def test_mint_key_year_falls_back_to_arxiv_id():
    meta = sample_meta(published=None, version=1)      # id 2301.* -> 2023
    assert "2023" in mint_key(meta)


def test_confine_benign_math_preserved():
    text = r"An $O(n^2)$ bound via $\frac{p}{q}$ arguments"
    assert confine_bib_text(text) == text


def test_confine_hostile_title_escaped():
    hostile = r"Nice title \input{/etc/passwd} \end{document}"
    out = confine_bib_text(hostile)
    assert "\\input" not in out
    assert "\\end{document}" not in out
    assert "textbackslash" in out          # per-character escaped, content kept


def test_entry_from_meta_fields():
    entry = entry_from_meta(sample_meta())
    assert entry.eprint == "2301.12345v2"
    assert entry.note == "arXiv 2301.12345v2"
    assert entry.archive_prefix == "arXiv"
    assert entry.entry_type == "misc"      # no journal_ref, no doi
    assert " and " in entry.author


def test_entry_from_meta_article_when_published():
    meta = sample_meta(journal_ref="Ann. Math. 100 (2030)", doi="10.1000/x")
    entry = entry_from_meta(meta)
    assert entry.entry_type == "article"
    assert entry.journal == "Ann. Math. 100 (2030)"


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "references.bib"
    bib = Bibliography()
    bib.add(entry_from_meta(sample_meta()))
    bib.save(path)
    loaded = Bibliography.load(path)
    assert loaded.keys() == bib.keys()
    [entry] = [loaded.entries[k] for k in loaded.keys()]
    assert entry.eprint == "2301.12345v2"
    assert entry.title == sample_meta().title


def test_load_missing_file_is_empty(tmp_path):
    assert Bibliography.load(tmp_path / "nope.bib").keys() == []


def test_save_is_normalized_and_sorted(tmp_path):
    path = tmp_path / "references.bib"
    bib = Bibliography()
    bib.add(entry_from_meta(sample_meta(arxiv_id="2401.00001", title="Zeta functions")))
    bib.add(entry_from_meta(sample_meta()))
    bib.save(path)
    text = path.read_text()
    assert text.index("smith2023modular") < text.index("smith2023zeta")


def test_add_or_get_mints_then_dedups(tmp_path):
    path = tmp_path / "references.bib"
    key1 = add_or_get(path, sample_meta())
    key2 = add_or_get(path, sample_meta())
    assert key1 == key2 == "smith2023modular-2301.12345V2"
    assert Bibliography.load(path).keys() == [key1]


def test_distinct_versions_get_distinct_entries(tmp_path):
    path = tmp_path / "references.bib"
    key2 = add_or_get(path, sample_meta(version=2))
    key3 = add_or_get(path, sample_meta(version=3))
    assert key2 != key3
    assert key3.endswith("V3")
    assert len(Bibliography.load(path).keys()) == 2


def test_keys_are_fetch_order_independent(tmp_path):
    a = tmp_path / "a.bib"
    b = tmp_path / "b.bib"
    m1, m2 = sample_meta(version=1), sample_meta(arxiv_id="2401.00001", version=1)
    keys_ab = {add_or_get(a, m1), add_or_get(a, m2)}
    keys_ba = {add_or_get(b, m2), add_or_get(b, m1)}
    assert keys_ab == keys_ba


def test_doi_never_collapses_arxiv_versions(tmp_path):
    path = tmp_path / "references.bib"
    key2 = add_or_get(path, sample_meta(version=2, doi="10.1000/shared"))
    key3 = add_or_get(path, sample_meta(version=3, doi="10.1000/shared"))
    assert key2 != key3                       # shared DOI must not dedup


def test_doi_fallback_only_without_arxiv_identity(tmp_path):
    path = tmp_path / "references.bib"
    bib = Bibliography()
    bib.add(BibEntry(
        key="legacy2020thing", entry_type="article", title="Legacy",
        author="Old Author", year="2020", doi="10.1000/legacy",
    ))
    bib.save(path)
    key = add_or_get(path, sample_meta(doi="10.1000/legacy"))
    # entry HAS no arXiv identity and shares the DOI: reuse it
    assert key == "legacy2020thing"


def test_transaction_runs_under_the_file_lock(tmp_path, monkeypatch):
    import hardy.literature.bibliography as bib_mod

    acquired: list[str] = []
    real = bib_mod.file_lock

    def spying(path, timeout=60.0):
        acquired.append(str(path))
        return real(path, timeout)

    monkeypatch.setattr(bib_mod, "file_lock", spying)
    path = tmp_path / "references.bib"
    add_or_get(path, sample_meta())
    assert acquired == [str(path)]


def test_fragment_contains_only_requested_keys(tmp_path):
    bib = Bibliography()
    bib.add(entry_from_meta(sample_meta()))
    bib.add(entry_from_meta(sample_meta(arxiv_id="2401.00001", title="Zeta stuff")))
    frag = bib.fragment(["smith2023modular-2301.12345V2"])
    assert "smith2023modular-2301.12345V2" in frag
    assert "zeta" not in frag.lower()


def test_validate_clean_file(tmp_path):
    path = tmp_path / "references.bib"
    add_or_get(path, sample_meta())
    assert validate(path) == []


def test_validate_flags_parse_error(tmp_path):
    path = tmp_path / "references.bib"
    path.write_text("@misc{broken,\n  title = {unclosed")
    problems = validate(path)
    assert problems and "parse" in problems[0].lower()


def test_validate_flags_duplicate_keys(tmp_path):
    path = tmp_path / "references.bib"
    entry = (
        "@misc{dup2020key-1234.5678,\n  title = {A},\n"
        "  author = {X},\n  year = {2020},\n}\n"
    )
    path.write_text(entry + "\n" + entry)
    assert any("duplicate" in p.lower() for p in validate(path))


def test_validate_flags_missing_required_fields(tmp_path):
    path = tmp_path / "references.bib"
    path.write_text("@misc{nofields2020x-1.2,\n  title = {Only title},\n}\n")
    assert any("year" in p or "author" in p for p in validate(path))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_bibliography.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.literature.bibliography'`

- [ ] **Step 4: Write the implementation**

```python
# src/hardy/literature/bibliography.py
"""One canonical references.bib, machine-maintained (M3 spec).

The single write path is add_or_get (the cite tool; fetch_paper
delegates here). The whole load -> dedup/mint -> save transaction runs
under an interprocess file lock: atomic rename protects readers from
partial files but does not serialize concurrent writers — two parallel
runs would otherwise each load, mint, and rename, and the last rename
would silently discard the other's entry.

Cite keys embed the paper's arXiv-id fragment ALWAYS, so a key is a pure
function of the paper — no ordinals, no first-inserted-wins, no
re-keying, identical across any fetch order or fresh rebuild. The
readability cost of the fragment is the accepted price (two prior
schemes fell to ordering dependence).

Field values are confined before persistence: arXiv metadata is
network-controlled text the writeup pipeline will later execute as TeX —
successful BibTeX parsing is not sanitization."""

import re
import unicodedata
from pathlib import Path
from typing import Literal

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
from pydantic import BaseModel

from hardy.latex.confine import violations
from hardy.latex.template import escape_text

from .arxiv import PaperMeta
from .fsutil import atomic_replace, file_lock


class BibliographyError(Exception):
    pass


class BibEntry(BaseModel):
    key: str
    entry_type: Literal["article", "misc"]
    title: str
    author: str                     # BibTeX " and "-joined
    year: str
    eprint: str | None = None       # version-qualified: "2301.12345v2"
    archive_prefix: str = "arXiv"
    primary_class: str | None = None
    doi: str | None = None
    journal: str | None = None
    note: str | None = None


def confine_bib_text(text: str) -> str:
    return text if not violations(text) else escape_text(text)


_STOPWORDS = frozenset(
    "a an the on of in for and to with from at by über sur une la le les"
    " el il un une der die das".split()
)


def _ascii_fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in folded if ch.isascii() and ch.isalnum()).lower()


def _surname(author: str) -> str:
    if "," in author:
        return _ascii_fold(author.split(",", 1)[0])
    parts = author.split()
    return _ascii_fold(parts[-1]) if parts else "anon"


def _year(meta: PaperMeta) -> str:
    if meta.published and len(meta.published) >= 4:
        return meta.published[:4]
    match = re.match(r"(\d{2})(\d{2})\.", meta.arxiv_id)
    if match:
        return f"20{match.group(1)}"
    match = re.search(r"/(\d{2})", meta.arxiv_id)   # old-style math/0211159
    if match:
        century = "19" if int(match.group(1)) >= 91 else "20"
        return f"{century}{match.group(1)}"
    return "0000"


def _first_content_word(title: str) -> str:
    for token in title.split():
        folded = _ascii_fold(token)
        if folded and folded not in _STOPWORDS:
            return folded
    return "paper"


def mint_key(meta: PaperMeta) -> str:
    surname = _surname(meta.authors[0]) if meta.authors else "anon"
    fragment = meta.arxiv_id.replace("/", "-")
    base = f"{surname or 'anon'}{_year(meta)}{_first_content_word(meta.title)}-{fragment}"
    return base if meta.version == 1 else f"{base}V{meta.version}"


def entry_from_meta(meta: PaperMeta) -> BibEntry:
    return BibEntry(
        key=mint_key(meta),
        entry_type="article" if (meta.journal_ref or meta.doi) else "misc",
        title=confine_bib_text(meta.title),
        author=confine_bib_text(" and ".join(meta.authors)),
        year=_year(meta),
        eprint=meta.id_v,
        primary_class=meta.categories[0] if meta.categories else None,
        doi=meta.doi,
        journal=confine_bib_text(meta.journal_ref) if meta.journal_ref else None,
        note=f"arXiv {meta.id_v}",
    )


_FIELD_MAP = {   # BibEntry field -> bibtex field name
    "title": "title",
    "author": "author",
    "year": "year",
    "eprint": "eprint",
    "archive_prefix": "archiveprefix",
    "primary_class": "primaryclass",
    "doi": "doi",
    "journal": "journal",
    "note": "note",
}
_REVERSE_MAP = {v: k for k, v in _FIELD_MAP.items()}


class Bibliography:
    def __init__(self, entries: dict[str, BibEntry] | None = None):
        self.entries: dict[str, BibEntry] = dict(entries or {})

    @classmethod
    def load(cls, path: Path) -> "Bibliography":
        if not path.exists():
            return cls()
        try:
            database = bibtexparser.loads(path.read_text())
        except Exception as exc:
            raise BibliographyError(f"cannot parse {path}: {exc}") from exc
        entries: dict[str, BibEntry] = {}
        for raw in database.entries:
            fields = {
                _REVERSE_MAP[k]: v for k, v in raw.items() if k in _REVERSE_MAP
            }
            entries[raw["ID"]] = BibEntry(
                key=raw["ID"],
                entry_type="article" if raw["ENTRYTYPE"] == "article" else "misc",
                **fields,
            )
        return cls(entries)

    def save(self, path: Path) -> None:
        database = BibDatabase()
        for key in sorted(self.entries):
            entry = self.entries[key]
            raw = {"ID": entry.key, "ENTRYTYPE": entry.entry_type}
            for field, bibtex_name in _FIELD_MAP.items():
                value = getattr(entry, field)
                if value is not None:
                    raw[bibtex_name] = value
            database.entries.append(raw)
        writer = BibTexWriter()
        writer.indent = "  "
        writer.order_entries_by = ("ID",)
        atomic_replace(path, bibtexparser.dumps(database, writer))

    def keys(self) -> list[str]:
        return sorted(self.entries)

    def add(self, entry: BibEntry) -> None:
        self.entries[entry.key] = entry

    def entry_for(self, meta: PaperMeta) -> BibEntry | None:
        for entry in self.entries.values():
            if entry.eprint == meta.id_v:
                return entry
        if meta.doi:
            for entry in self.entries.values():
                # DOI fallback ONLY for entries with no arXiv identity:
                # revisions of one arXiv paper share a DOI, and collapsing
                # v3 into v1's entry would point citations (and the M4
                # namespaces keyed from them) at the wrong version.
                if entry.eprint is None and entry.doi == meta.doi:
                    return entry
        return None

    def fragment(self, keys: list[str]) -> str:
        subset = Bibliography(
            {k: self.entries[k] for k in keys if k in self.entries}
        )
        database = BibDatabase()
        for key in sorted(subset.entries):
            entry = subset.entries[key]
            raw = {"ID": entry.key, "ENTRYTYPE": entry.entry_type}
            for field, bibtex_name in _FIELD_MAP.items():
                value = getattr(entry, field)
                if value is not None:
                    raw[bibtex_name] = value
            database.entries.append(raw)
        writer = BibTexWriter()
        writer.indent = "  "
        writer.order_entries_by = ("ID",)
        return bibtexparser.dumps(database, writer)


def add_or_get(path: Path, meta: PaperMeta) -> str:
    """THE single write path to references.bib (the cite tool's backend;
    fetch_paper delegates here)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        bibliography = Bibliography.load(path)
        existing = bibliography.entry_for(meta)
        if existing is not None:
            return existing.key
        entry = entry_from_meta(meta)
        bibliography.add(entry)
        bibliography.save(path)
        return entry.key


_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
_REQUIRED_FIELDS = ("title", "author", "year")


def validate(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text()
    except OSError as exc:
        return [f"cannot read {path}: {exc}"]
    try:
        database = bibtexparser.loads(text)
    except Exception as exc:
        return [f"parse error in {path}: {exc}"]
    raw_keys = _KEY_RE.findall(text)
    seen: set[str] = set()
    for key in raw_keys:
        if key in seen:
            problems.append(f"duplicate key: {key}")
        seen.add(key)
    if len(database.entries) < len(seen):
        problems.append(
            "parse error: some entries were dropped by the parser "
            f"({len(database.entries)} parsed, {len(seen)} distinct keys found)"
        )
    for raw in database.entries:
        for field in _REQUIRED_FIELDS:
            if field not in raw or not raw[field].strip():
                problems.append(f"entry {raw['ID']}: missing required field {field}")
        if raw.get("eprint") and "arXiv" not in raw.get("note", ""):
            problems.append(
                f"entry {raw['ID']}: eprint entry missing the version note"
            )
    return problems
```

Implementation notes for this step:
- `bibtexparser` v1 lowercases field names on load — `_REVERSE_MAP` keys are already lowercase, so round-trips are stable. If v1's parser silently *drops* a malformed entry instead of raising, `validate` still reports it via the parsed-vs-raw-key count check; keep that check even if a newer bibtexparser starts raising.
- The unclosed-brace fixture in `test_validate_flags_parse_error` may parse as *zero entries with one raw key* rather than raising — the count check yields the `parse error:` message either way; the test only requires "parse" to appear.
- `test_mint_key_ascii_folds_and_skips_stopwords` accepts both `thorsson` and `orsson` because NFKD does not decompose `Þ`; either fold is deterministic, which is what matters.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bibliography.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/literature/bibliography.py tests/test_bibliography.py pyproject.toml
git commit -m "feat: machine-maintained bibliography — pure-function keys, locked transactions, confined fields"
```

---

### Task 8: `validate_bib.py`, the committed `references.bib`, gitignore, CI wiring

**Files:**
- Create: `scripts/validate_bib.py`
- Create: `references.bib` (committed, initially header-only)
- Modify: `.gitignore` (papers-store rules)
- Modify: `pyproject.toml` (register the `network` marker)
- Modify: `.github/workflows/test.yml` (exclude `model`+`network`; run the validator)
- Test: `tests/test_validate_bib.py`

**Interfaces:**
- Consumes: `validate` (Task 7).
- Produces: `scripts/validate_bib.py <path=references.bib>` — exit 0 and silence when valid; exit 1 with one problem per line when not. CI runs it in the default unit job (no network — spec requirement).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_validate_bib.py
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "validate_bib.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_valid_file_exits_zero(tmp_path):
    good = tmp_path / "good.bib"
    good.write_text(
        "@misc{smith2023modular-2301.12345,\n"
        "  title = {A Modular Approach},\n"
        "  author = {Ada Smith},\n"
        "  year = {2023},\n"
        "  eprint = {2301.12345v1},\n"
        "  note = {arXiv 2301.12345v1},\n"
        "}\n"
    )
    result = run(str(good))
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_file_exits_one_and_names_problems(tmp_path):
    bad = tmp_path / "bad.bib"
    bad.write_text("@misc{x2020y-1.2,\n  title = {Only title},\n}\n")
    result = run(str(bad))
    assert result.returncode == 1
    assert "missing required field" in result.stdout


def test_default_target_is_the_repo_references_bib():
    result = run()
    assert result.returncode == 0, (
        "the committed references.bib must always validate:\n"
        + result.stdout + result.stderr
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validate_bib.py -v`
Expected: FAIL — the script does not exist (`FileNotFoundError`/non-zero from a missing file)

- [ ] **Step 3: Create the script and the seed bibliography**

```python
#!/usr/bin/env python3
# scripts/validate_bib.py
"""CI gate for the canonical bibliography (M3 spec): parses, no duplicate
keys, entries well-formed. Runs in the default unit-test tier — no
network. Exit 0 silent when valid; exit 1 with one problem per line."""

import argparse
import sys
from pathlib import Path

from hardy.literature.bibliography import validate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path", nargs="?", type=Path,
        default=Path(__file__).resolve().parent.parent / "references.bib",
    )
    args = parser.parse_args()
    problems = validate(args.path)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `references.bib` at the repo root with exactly:

```
% Machine-maintained by Hardy. The sole write path is the cite tool
% (hardy.literature.bibliography.add_or_get) -- do not edit by hand.
```

(A comment-only file parses as zero entries and validates clean; `Bibliography.save` preserves nothing but entries, which is fine — the header exists for the human reading the first diff, and the first `add_or_get` replaces the file wholesale, which the spec's "machine-maintained" contract expects.)

- [ ] **Step 4: gitignore + marker + CI**

Append to `.gitignore`:

```
papers/*
!papers/DIGESTS.json
```

In `pyproject.toml` `[tool.pytest.ini_options] markers`, add:

```toml
    "network: talks to the live arXiv API (never runs in CI; be polite)",
```

Replace `.github/workflows/test.yml`'s job steps' final run line and add the validator (full file after the edit):

```yaml
name: test
on: [push, pull_request]
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .[dev]
      - run: pytest -m "not lean and not tex and not docker and not model and not network"
      - run: python scripts/validate_bib.py
```

(Plan-assumption 10: if M1 already added `and not model` — or a `model` marker exclusion by other means — reconcile rather than duplicate.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_validate_bib.py -v`
Expected: all PASS
Also run: `python scripts/validate_bib.py` — exit 0, no output.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate_bib.py references.bib .gitignore pyproject.toml .github/workflows/test.yml tests/test_validate_bib.py
git commit -m "feat: committed references.bib + CI validator, papers-store gitignore, network marker"
```

---
### Task 9: Bounded extraction subprocess + `PaperReader` (`reading.py`)

**Files:**
- Create: `src/hardy/literature/extract_worker.py`
- Create: `src/hardy/literature/reading.py`
- Modify: `pyproject.toml` (add `"pypdf>=4"` to `[project] dependencies`)
- Test: `tests/test_reading.py`

**Interfaces:**
- Consumes: `StoredPaper` (Task 6).
- Produces (Task 10 relies on these exact signatures):
  - `extract_worker.py` — `python -m hardy.literature.extract_worker <mode> <path>`:
    - mode `pdf-text`: pypdf extraction, pages joined by `\f` on stdout, `--max-pages` (default 200); any parse failure → message on stderr, exit 1.
    - mode `tex-index`: streams the file, emits one JSON line per `\section`/`\subsection` occurrence — `{"kind", "title", "offset"}` — after a first line `{"kind": "meta", "size": <bytes>}`. Lines are inspected only up to a 4096-byte cap after the marker, so a pathological single-line file cannot balloon the worker.
  - `reading.ReadError(Exception)`.
  - `reading.run_worker(args: list[str], *, timeout_s: float = 60.0, output_cap: int = 4_000_000, rss_mb: int = 1024, cpu_s: int = 60) -> bytes` — runs the worker subprocess with POSIX rlimits (`RLIMIT_AS`, `RLIMIT_CPU`) where available, a wall-clock kill, and stdout streamed through the hard output cap **as it arrives** (a bounded-size PDF can still expand into an enormous text stream); raises `ReadError` on timeout, cap breach, or non-zero exit.
  - `reading.ReadResult(kind: Literal["latex", "pdf-text"], toc: list[str] = [], section: str | None = None, content: str, truncated: bool = False)` (pydantic). `toc` is populated only when `section is None` (the first-call navigation contract).
  - `reading.PaperReader(papers_dir: Path, *, chunk_limit: int = 6000)` with `read(stored: StoredPaper, section: str | None = None, offset: int = 0, limit: int = 6000) -> ReadResult`:
    - LaTeX source available → section-chunked: `section` matches a TOC entry by case-insensitive substring or by index string (`"3"`); `section=None` serves the TOC plus the document head. **Indexing runs in the worker subprocess** — the source is admitted untrusted-archive content.
    - No source → PDF text, page-chunked: `section="page:N"`; `section=None` serves the page-count TOC plus page 1.
    - Derived data cached under `papers_dir/.derived/<entry-name>/` (entries themselves are immutable — derived data never lives inside them); the parse happens once per paper.
    - Per-call output hard-capped at `chunk_limit` chars (`truncated=True` when cut).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml` `[project] dependencies`, append `"pypdf>=4"`. Run `pip install -e .[dev]`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_reading.py
import json
import sys

import pytest

from hardy.literature.reading import PaperReader, ReadError, run_worker
from hardy.literature.store import StoredPaper
from tests.fake_arxiv import sample_meta

LATEX = r"""\documentclass{article}
\begin{document}
Preamble prose.
\section{Introduction}
Intro text with $x^2$ math.
\subsection{Motivation}
Why we care.
\section{Main Theorem}
The theorem text.
\end{document}
"""


def stored_with_source(tmp_path) -> StoredPaper:
    entry = tmp_path / "papers" / "2301.12345v2"
    (entry / "source").mkdir(parents=True)
    (entry / "source" / "main.tex").write_text(LATEX)
    (entry / "paper.pdf").write_bytes(b"%PDF-1.4 unused\n%%EOF\n")
    return StoredPaper(
        id_v="2301.12345v2", path=entry, meta=sample_meta(),
        pdf_sha256="0" * 64, source_available=True,
    )


def stored_pdf_only(tmp_path) -> StoredPaper:
    from pypdf import PdfWriter

    entry = tmp_path / "papers" / "2301.12345v2"
    entry.mkdir(parents=True)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(entry / "paper.pdf", "wb") as handle:
        writer.write(handle)
    return StoredPaper(
        id_v="2301.12345v2", path=entry, meta=sample_meta(),
        pdf_sha256="0" * 64, source_available=False,
    )


# --- worker driver -----------------------------------------------------

def test_run_worker_returns_stdout(tmp_path):
    out = run_worker(
        [sys.executable, "-c", "print('hello worker')"], timeout_s=10.0
    )
    assert b"hello worker" in out


def test_run_worker_timeout_raises(tmp_path):
    with pytest.raises(ReadError, match="timed out"):
        run_worker(
            [sys.executable, "-c", "import time; time.sleep(3600)"],
            timeout_s=0.5,
        )


def test_run_worker_output_cap_raises(tmp_path):
    with pytest.raises(ReadError, match="cap"):
        run_worker(
            [sys.executable, "-c", "print('x' * 1000000)"],
            timeout_s=10.0, output_cap=1000,
        )


def test_run_worker_nonzero_exit_raises_with_stderr(tmp_path):
    with pytest.raises(ReadError, match="boom"):
        run_worker(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"],
            timeout_s=10.0,
        )


# --- tex-index worker mode ---------------------------------------------

def test_tex_index_emits_sections(tmp_path):
    tex = tmp_path / "main.tex"
    tex.write_text(LATEX)
    out = run_worker(
        [sys.executable, "-m", "hardy.literature.extract_worker",
         "tex-index", str(tex)],
        timeout_s=30.0,
    )
    lines = [json.loads(l) for l in out.decode().splitlines() if l.strip()]
    assert lines[0]["kind"] == "meta"
    titles = [(l["kind"], l["title"]) for l in lines[1:]]
    assert titles == [
        ("section", "Introduction"),
        ("subsection", "Motivation"),
        ("section", "Main Theorem"),
    ]
    assert all(isinstance(l["offset"], int) for l in lines[1:])


# --- PaperReader: latex path -------------------------------------------

def test_first_call_serves_toc(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    result = reader.read(stored_with_source(tmp_path))
    assert result.kind == "latex"
    assert result.toc == ["1. Introduction", "1.1. Motivation", "2. Main Theorem"]
    assert "Preamble prose" in result.content


def test_read_section_by_name(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    result = reader.read(stored_with_source(tmp_path), section="main theorem")
    assert "The theorem text" in result.content
    assert "Intro text" not in result.content
    assert result.toc == []                     # TOC only on the first call
    assert result.section == "Main Theorem"


def test_read_section_by_index(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    result = reader.read(stored_with_source(tmp_path), section="1")
    assert "Intro text" in result.content


def test_unknown_section_raises_with_choices(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    with pytest.raises(ReadError, match="Introduction"):
        reader.read(stored_with_source(tmp_path), section="conclusions")


def test_offset_and_limit_chunking(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    full = reader.read(stored_with_source(tmp_path), section="Introduction")
    part = reader.read(
        stored_with_source(tmp_path), section="Introduction", offset=5, limit=10
    )
    assert part.content == full.content[5:15]
    assert part.truncated


def test_limit_is_capped_by_chunk_limit(tmp_path):
    reader = PaperReader(tmp_path / "papers", chunk_limit=50)
    result = reader.read(
        stored_with_source(tmp_path), section="Introduction", limit=10_000
    )
    assert len(result.content) <= 50


def test_latex_index_is_cached(tmp_path, monkeypatch):
    import hardy.literature.reading as reading_mod

    calls: list[list[str]] = []
    real = reading_mod.run_worker

    def counting(args, **kw):
        calls.append(args)
        return real(args, **kw)

    monkeypatch.setattr(reading_mod, "run_worker", counting)
    reader = PaperReader(tmp_path / "papers")
    stored = stored_with_source(tmp_path)
    reader.read(stored)
    reader.read(stored, section="Introduction")
    assert len(calls) == 1                      # indexed once, served twice


# --- PaperReader: pdf path ---------------------------------------------

def test_pdf_only_paper_serves_page_chunks(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    result = reader.read(stored_pdf_only(tmp_path))
    assert result.kind == "pdf-text"
    assert result.toc == ["page 1"]             # blank page: 1 page, no text


def test_pdf_text_is_cached(tmp_path, monkeypatch):
    import hardy.literature.reading as reading_mod

    calls: list[list[str]] = []
    real = reading_mod.run_worker

    def counting(args, **kw):
        calls.append(args)
        return real(args, **kw)

    monkeypatch.setattr(reading_mod, "run_worker", counting)
    reader = PaperReader(tmp_path / "papers")
    stored = stored_pdf_only(tmp_path)
    reader.read(stored)
    reader.read(stored, section="page:1")
    assert len(calls) == 1


def test_pdf_unknown_page_raises(tmp_path):
    reader = PaperReader(tmp_path / "papers")
    with pytest.raises(ReadError, match="page"):
        reader.read(stored_pdf_only(tmp_path), section="page:99")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_reading.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.literature.reading'`

- [ ] **Step 4: Write the worker**

```python
# src/hardy/literature/extract_worker.py
"""Subprocess entry point for untrusted-content parsing (M3 spec).

Run as `python -m hardy.literature.extract_worker <mode> <path>` from
reading.run_worker, which wraps this process in rlimits, a wall-clock
kill, and a streamed output cap. Both inputs are untrusted: the PDF came
from the network chosen by the agent, and the LaTeX source is admitted
untrusted-archive content — a malformed or decompression-bomb PDF can
burn unbounded CPU/memory INSIDE the parser, so the parse must happen
here, not in the harness process.

pdf-text : page texts joined by \\f on stdout.
tex-index: one JSON line per \\section/\\subsection ({kind,title,offset}),
           after a {"kind": "meta", "size": N} header line.
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SECTION_RE = re.compile(rb"\\(section|subsection)\*?\{([^}\n]{0,200})\}")
_LINE_CAP = 4096
_CHUNK = 65536


def pdf_text(path: Path, max_pages: int) -> int:
    from pypdf import PdfReader

    reader = PdfReader(path, strict=False)
    pages = []
    for index, page in enumerate(reader.pages):
        if index >= max_pages:
            break
        pages.append(page.extract_text() or "")
    sys.stdout.write("\f".join(pages))
    return 0


def tex_index(path: Path) -> int:
    size = path.stat().st_size
    print(json.dumps({"kind": "meta", "size": size}))
    offset = 0
    carry = b""
    carry_start = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            buffer = carry + chunk
            for match in _SECTION_RE.finditer(buffer):
                title = match.group(2)[:_LINE_CAP].decode("utf-8", errors="replace")
                print(json.dumps({
                    "kind": match.group(1).decode(),
                    "title": title,
                    "offset": carry_start + match.start(),
                }))
            # keep a tail so a marker split across chunks is still found
            keep = min(len(buffer), 512)
            carry = buffer[-keep:]
            carry_start += len(buffer) - keep
            offset += len(chunk)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["pdf-text", "tex-index"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-pages", type=int, default=200)
    args = parser.parse_args()
    try:
        if args.mode == "pdf-text":
            return pdf_text(args.path, args.max_pages)
        return tex_index(args.path)
    except Exception as exc:  # any parse failure: message out, nonzero exit
        sys.stderr.write(f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

Note for the implementer: the chunk-overlap scheme can report a marker twice when it lands wholly inside the 512-byte carry; dedupe by offset in the *reader* (it builds a dict keyed by offset). Section titles containing `}` or newlines are cut at the regex boundary — acceptable for navigation text.

- [ ] **Step 5: Write the reader**

```python
# src/hardy/literature/reading.py
"""Section-chunked serving of stored papers (M3 spec).

All parsing of stored-paper content runs in the extract_worker
subprocess under run_worker's containment: POSIX rlimits (rss/cpu) when
the platform has them, a wall-clock kill on every platform, and stdout
streamed through a hard byte quota BEFORE the derived cache admits it —
a bounded-size PDF can still expand into an enormous text stream, and
the parser's own rlimits can't bound its output. Derived text/index data
is cached OUTSIDE the immutable entries (papers/.derived/<entry>/) so
the parse happens once per paper. Per-call output is capped (compact,
high-signal — Component 2 rules), with a table of contents served on the
first call so the agent can navigate."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .store import StoredPaper


class ReadError(Exception):
    pass


def _limit_resources(rss_mb: int, cpu_s: int):
    def apply() -> None:
        import resource

        limit = rss_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))

    return apply


def run_worker(
    args: list[str],
    *,
    timeout_s: float = 60.0,
    output_cap: int = 4_000_000,
    rss_mb: int = 1024,
    cpu_s: int = 60,
) -> bytes:
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _limit_resources(rss_mb, cpu_s)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )
    deadline = time.monotonic() + timeout_s
    chunks: list[bytes] = []
    total = 0
    os.set_blocking(proc.stdout.fileno(), False)
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if chunk is None:
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    raise ReadError(f"extraction timed out after {timeout_s}s")
                time.sleep(0.02)
                continue
            if chunk == b"":
                break
            total += len(chunk)
            if total > output_cap:
                proc.kill()
                proc.wait()
                raise ReadError(
                    f"extraction output exceeded the {output_cap} byte cap"
                )
            chunks.append(chunk)
        remaining = max(0.1, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ReadError(f"extraction timed out after {timeout_s}s")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if proc.returncode != 0:
        stderr = proc.stderr.read() or b""
        raise ReadError(
            f"extraction failed (exit {proc.returncode}): "
            f"{stderr[-500:].decode(errors='replace')}"
        )
    return b"".join(chunks)


class ReadResult(BaseModel):
    kind: Literal["latex", "pdf-text"]
    toc: list[str] = Field(default_factory=list)
    section: str | None = None
    content: str
    truncated: bool = False


def _worker_argv(mode: str, path: Path) -> list[str]:
    return [sys.executable, "-m", "hardy.literature.extract_worker", mode, str(path)]


class PaperReader:
    def __init__(self, papers_dir: Path, *, chunk_limit: int = 6000):
        self._derived = papers_dir / ".derived"
        self._chunk_limit = chunk_limit

    # -- derived-data layer (immutable entries never hold derived data) --

    def _derived_dir(self, stored: StoredPaper) -> Path:
        directory = self._derived / stored.path.name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _main_tex(self, stored: StoredPaper) -> Path | None:
        source = stored.path / "source"
        if not source.is_dir():
            return None
        candidates = sorted(source.rglob("*.tex"))
        if not candidates:
            return None
        for candidate in candidates:
            try:
                head = candidate.read_bytes()[:65536]
            except OSError:
                continue
            if b"\\documentclass" in head:
                return candidate
        return max(candidates, key=lambda p: p.stat().st_size)

    def _tex_sections(self, stored: StoredPaper, tex: Path) -> list[dict]:
        cache = self._derived_dir(stored) / "toc.json"
        if cache.exists():
            return json.loads(cache.read_text())
        out = run_worker(_worker_argv("tex-index", tex))
        by_offset: dict[int, dict] = {}
        for line in out.decode(errors="replace").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["kind"] in ("section", "subsection"):
                by_offset[record["offset"]] = record
        sections = [by_offset[k] for k in sorted(by_offset)]
        cache.write_text(json.dumps(sections))
        return sections

    def _pdf_pages(self, stored: StoredPaper) -> list[str]:
        cache = self._derived_dir(stored) / "text.txt"
        if cache.exists():
            return cache.read_text(errors="replace").split("\f")
        out = run_worker(_worker_argv("pdf-text", stored.path / "paper.pdf"))
        text = out.decode(errors="replace")
        cache.write_text(text)
        return text.split("\f")

    # -- serving ---------------------------------------------------------

    def _chunk(self, text: str, offset: int, limit: int) -> tuple[str, bool]:
        limit = min(limit, self._chunk_limit)
        piece = text[offset : offset + limit]
        return piece, (offset > 0 or offset + limit < len(text))

    def read(
        self,
        stored: StoredPaper,
        section: str | None = None,
        offset: int = 0,
        limit: int = 6000,
    ) -> ReadResult:
        tex = self._main_tex(stored)
        if tex is not None:
            return self._read_latex(stored, tex, section, offset, limit)
        return self._read_pdf(stored, section, offset, limit)

    def _numbered_toc(self, sections: list[dict]) -> list[str]:
        toc: list[str] = []
        major = minor = 0
        for record in sections:
            if record["kind"] == "section":
                major += 1
                minor = 0
                toc.append(f"{major}. {record['title']}")
            else:
                minor += 1
                toc.append(f"{major}.{minor}. {record['title']}")
        return toc

    def _read_latex(
        self, stored: StoredPaper, tex: Path,
        section: str | None, offset: int, limit: int,
    ) -> ReadResult:
        sections = self._tex_sections(stored, tex)
        source = tex.read_text(errors="replace")
        toc = self._numbered_toc(sections)
        if section is None:
            content, truncated = self._chunk(source, offset, limit)
            return ReadResult(
                kind="latex", toc=toc, content=content, truncated=truncated
            )
        chosen: int | None = None
        if section.strip().isdigit():
            index = int(section.strip()) - 1
            majors = [i for i, s in enumerate(sections) if s["kind"] == "section"]
            if 0 <= index < len(majors):
                chosen = majors[index]
        else:
            needle = section.strip().lower()
            for i, record in enumerate(sections):
                if needle in record["title"].lower():
                    chosen = i
                    break
        if chosen is None:
            raise ReadError(
                f"unknown section {section!r}; available: {toc}"
            )
        start = sections[chosen]["offset"]
        end = (
            sections[chosen + 1]["offset"]
            if chosen + 1 < len(sections) else len(source.encode())
        )
        body = source.encode()[start:end].decode(errors="replace")
        content, truncated = self._chunk(body, offset, limit)
        return ReadResult(
            kind="latex", section=sections[chosen]["title"],
            content=content, truncated=truncated,
        )

    def _read_pdf(
        self, stored: StoredPaper,
        section: str | None, offset: int, limit: int,
    ) -> ReadResult:
        pages = self._pdf_pages(stored)
        toc = [f"page {i + 1}" for i in range(len(pages))]
        if section is None:
            content, truncated = self._chunk(pages[0], offset, limit)
            return ReadResult(
                kind="pdf-text", toc=toc, content=content, truncated=truncated
            )
        match = section.strip().lower()
        if not match.startswith("page:"):
            raise ReadError(
                f"this paper has no LaTeX source; address pages as 'page:N' "
                f"(1..{len(pages)})"
            )
        number = int(match.removeprefix("page:"))
        if not 1 <= number <= len(pages):
            raise ReadError(f"page {number} out of range (1..{len(pages)})")
        content, truncated = self._chunk(pages[number - 1], offset, limit)
        return ReadResult(
            kind="pdf-text", section=f"page {number}",
            content=content, truncated=truncated,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_reading.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/hardy/literature/extract_worker.py src/hardy/literature/reading.py tests/test_reading.py pyproject.toml
git commit -m "feat: subprocess-confined paper extraction + section/page-chunked PaperReader"
```

---

### Task 10: The literature tool set (`literature_tools.py`)

**Files:**
- Create: `src/hardy/tools/literature_tools.py`
- Test: `tests/test_literature_tools.py`

**Interfaces:**
- Consumes: `ToolDef`/`ToolResult`/`ToolRegistry` (**M1 plan Task 1 — plan-assumption 1**), `ArxivClient`/`ArxivQuery`/`ArxivError` (Task 3), `PaperStore`/`PaperStoreError` (Task 6), `add_or_get`/`Bibliography` (Task 7), `PaperReader`/`ReadError` (Task 9).
- Produces (Tasks 13, 14 rely on these exact signatures):
  - `LiteratureServices(client: ArxivClient, store: PaperStore, reader: PaperReader, bib_path: Path)` — plain class (not pydantic; it holds live service objects); `used_papers: list[dict]` accumulates `{"arxiv_id", "version", "cite_key", "pdf_sha256"}` records (deduped by id_v; `pdf_sha256` is None for cite-without-fetch) — the manifest's paper-provenance record (spec: "per-result manifests additionally record the digests of the papers they used").
  - `make_literature_services(papers_dir: Path, bib_path: Path, *, transport: ArxivTransport | None = None) -> LiteratureServices` — production wiring (client work dir = papers dir; reader over the same dir).
  - `make_literature_registry(services: LiteratureServices) -> ToolRegistry` — exactly the four tools `arxiv_search`, `fetch_paper`, `read_paper`, `cite`. All handlers are async and run the synchronous literature layer via `asyncio.to_thread` (network waits and the interprocess rate limiter must never block the event loop).
- Tool behavior (spec table, each row carries tests):
  - `arxiv_search(query, author?, category?, max_results=10)` → compact per-hit lines: id_v, title, first three authors, abstract truncated to 300 chars.
  - `fetch_paper(arxiv_id, version?)` → resolves an unversioned fetch to the latest revision; **idempotent on stored versions** (no re-download); `download` → `store.admit` → **delegates registration to `add_or_get`**; returns store path, cite key, and LaTeX-source availability.
  - `read_paper(arxiv_id, version, section?, offset=0, limit=6000)` → `reader.read`; a paper not in the store is an actionable error ("fetch_paper first"), not a fetch.
  - `cite(arxiv_id, version?)` → looks up or adds via `add_or_get` (metadata-only when not stored — no download); returns the cite key. The only `.bib` writer.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_literature_tools.py
from pathlib import Path

from hardy.literature.bibliography import Bibliography
from hardy.tools.literature_tools import (
    make_literature_registry,
    make_literature_services,
)
from tests.fake_arxiv import TINY_PDF, FakeTransport, sample_meta


def tar_bytes(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


LATEX = b"\\documentclass{article}\n\\begin{document}\n\\section{Intro}\nHi.\n\\end{document}\n"


def services(tmp_path, papers=None):
    transport = FakeTransport(papers or {
        "2301.12345": {
            "meta": sample_meta(),
            "pdf": TINY_PDF,
            "source": tar_bytes({"main.tex": LATEX}),
        },
    })
    svc = make_literature_services(
        tmp_path / "papers", tmp_path / "references.bib", transport=transport
    )
    return svc, transport


async def test_registry_has_exactly_the_four_tools(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    assert sorted(reg.names()) == [
        "arxiv_search", "cite", "fetch_paper", "read_paper",
    ]


async def test_arxiv_search_renders_compact_hits(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("arxiv_search").call({"query": "modular"})
    assert not result.is_error
    assert "2301.12345v2" in result.content
    assert "Modular Approach" in result.content
    assert "Ada Smith" in result.content


async def test_arxiv_search_no_results_is_not_an_error(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("arxiv_search").call({"query": "nonexistent topic"})
    assert not result.is_error
    assert "No results" in result.content


async def test_fetch_paper_stores_registers_and_reports(tmp_path):
    svc, transport = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    assert not result.is_error
    assert "smith2023modular-2301.12345V2" in result.content   # the cite key
    assert "source: available" in result.content
    assert svc.store.get("2301.12345v2") is not None
    assert Bibliography.load(svc.bib_path).keys() == [
        "smith2023modular-2301.12345V2"
    ]
    # unversioned fetch resolved to the latest revision (v2)
    assert "2301.12345v2" in result.content


async def test_fetch_paper_is_idempotent_on_stored_versions(tmp_path):
    svc, transport = services(tmp_path)
    reg = make_literature_registry(svc)
    await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    downloads_after_first = len(transport.downloads)
    result = await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    assert not result.is_error
    assert len(transport.downloads) == downloads_after_first   # no re-fetch


async def test_fetch_paper_records_used_papers_with_digest(tmp_path):
    import hashlib

    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    [record] = svc.used_papers                                # deduped
    assert record["arxiv_id"] == "2301.12345"
    assert record["version"] == 2
    assert record["cite_key"] == "smith2023modular-2301.12345V2"
    assert record["pdf_sha256"] == hashlib.sha256(TINY_PDF).hexdigest()


async def test_fetch_unknown_paper_is_tool_error(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("fetch_paper").call({"arxiv_id": "9999.00000"})
    assert result.is_error


async def test_read_paper_before_fetch_is_actionable_error(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("read_paper").call(
        {"arxiv_id": "2301.12345", "version": 2}
    )
    assert result.is_error
    assert "fetch_paper" in result.content


async def test_read_paper_serves_toc_then_sections(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    first = await reg.get("read_paper").call(
        {"arxiv_id": "2301.12345", "version": 2}
    )
    assert not first.is_error
    assert "Intro" in first.content                     # the TOC names it
    section = await reg.get("read_paper").call(
        {"arxiv_id": "2301.12345", "version": 2, "section": "intro"}
    )
    assert not section.is_error
    assert "Hi." in section.content


async def test_cite_without_fetch_mints_from_metadata_only(tmp_path):
    svc, transport = services(tmp_path)
    reg = make_literature_registry(svc)
    result = await reg.get("cite").call({"arxiv_id": "2301.12345"})
    assert not result.is_error
    assert "smith2023modular-2301.12345V2" in result.content
    assert transport.downloads == []                    # no download happened
    assert svc.store.get("2301.12345v2") is None
    [record] = svc.used_papers
    assert record["pdf_sha256"] is None                 # nothing fetched


async def test_cite_after_fetch_returns_same_key(tmp_path):
    svc, _ = services(tmp_path)
    reg = make_literature_registry(svc)
    fetch = await reg.get("fetch_paper").call({"arxiv_id": "2301.12345"})
    cite = await reg.get("cite").call({"arxiv_id": "2301.12345", "version": 2})
    key = "smith2023modular-2301.12345V2"
    assert key in fetch.content and key in cite.content
    assert Bibliography.load(svc.bib_path).keys() == [key]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_literature_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.tools.literature_tools'`

- [ ] **Step 3: Write the implementation**

```python
# src/hardy/tools/literature_tools.py
"""The M3 literature tool set (spec tool table).

fetch_paper is idempotent on stored versions and DELEGATES bibliography
registration to cite's backend (add_or_get) — the .bib has exactly one
write path. Handlers run the synchronous literature layer via
asyncio.to_thread: the interprocess rate limiter sleeps while holding
its lock, and that wait must never block the event loop."""

import asyncio
from pathlib import Path

from pydantic import BaseModel

from hardy.literature.arxiv import (
    ArxivClient,
    ArxivError,
    ArxivQuery,
    ArxivTransport,
)
from hardy.literature.bibliography import add_or_get
from hardy.literature.reading import PaperReader, ReadError
from hardy.literature.store import PaperStore, PaperStoreError, StoredPaper
from hardy.tools.registry import ToolDef, ToolRegistry, ToolResult

_ABSTRACT_CAP = 300


class LiteratureServices:
    def __init__(
        self,
        client: ArxivClient,
        store: PaperStore,
        reader: PaperReader,
        bib_path: Path,
    ):
        self.client = client
        self.store = store
        self.reader = reader
        self.bib_path = bib_path
        self.used_papers: list[dict] = []

    def record_use(
        self, arxiv_id: str, version: int, cite_key: str, pdf_sha256: str | None
    ) -> None:
        for record in self.used_papers:
            if record["arxiv_id"] == arxiv_id and record["version"] == version:
                if record["pdf_sha256"] is None and pdf_sha256 is not None:
                    record["pdf_sha256"] = pdf_sha256   # upgrade cite -> fetch
                return
        self.used_papers.append({
            "arxiv_id": arxiv_id,
            "version": version,
            "cite_key": cite_key,
            "pdf_sha256": pdf_sha256,
        })


def make_literature_services(
    papers_dir: Path,
    bib_path: Path,
    *,
    transport: ArxivTransport | None = None,
) -> LiteratureServices:
    client = ArxivClient(papers_dir, transport=transport)
    store = PaperStore(papers_dir)
    reader = PaperReader(papers_dir)
    return LiteratureServices(client, store, reader, bib_path)


class ArxivSearchInput(BaseModel):
    query: str
    author: str | None = None
    category: str | None = None
    max_results: int = 10


class FetchPaperInput(BaseModel):
    arxiv_id: str
    version: int | None = None


class ReadPaperInput(BaseModel):
    arxiv_id: str
    version: int
    section: str | None = None
    offset: int = 0
    limit: int = 6000


class CiteInput(BaseModel):
    arxiv_id: str
    version: int | None = None


def _render_hit(meta) -> str:
    authors = ", ".join(meta.authors[:3])
    if len(meta.authors) > 3:
        authors += " et al."
    abstract = meta.abstract[:_ABSTRACT_CAP]
    if len(meta.abstract) > _ABSTRACT_CAP:
        abstract += "…"
    return f"{meta.id_v} | {meta.title} | {authors}\n  {abstract}"


def _fetch_sync(services: LiteratureServices, args: FetchPaperInput) -> str:
    version = args.version or services.client.resolve_version(args.arxiv_id)
    id_v = f"{args.arxiv_id}v{version}"
    stored: StoredPaper | None = services.store.get(id_v)
    if stored is None:
        meta = services.client.meta(args.arxiv_id).model_copy(
            update={"version": version}
        )
        downloaded = services.client.download(meta)
        stored = services.store.admit(downloaded)
    key = add_or_get(services.bib_path, stored.meta)   # sole .bib write path
    services.record_use(args.arxiv_id, version, key, stored.pdf_sha256)
    source = (
        "source: available"
        if stored.source_available
        else f"source: unavailable ({stored.source_error or 'no LaTeX source'})"
    )
    return (
        f"Stored {id_v} at {stored.path}. Cite key: {key}. {source}. "
        "Use read_paper to read it; use \\cite{" + key + "} in the writeup."
    )


def _cite_sync(services: LiteratureServices, args: CiteInput) -> str:
    version = args.version or services.client.resolve_version(args.arxiv_id)
    id_v = f"{args.arxiv_id}v{version}"
    stored = services.store.get(id_v)
    if stored is not None:
        meta = stored.meta
        digest: str | None = stored.pdf_sha256
    else:
        meta = services.client.meta(args.arxiv_id).model_copy(
            update={"version": version}
        )
        digest = None
    key = add_or_get(services.bib_path, meta)
    services.record_use(args.arxiv_id, version, key, digest)
    return f"Cite key: {key} (for arXiv {id_v}). Use \\cite{{{key}}}."


def make_literature_registry(services: LiteratureServices) -> ToolRegistry:
    async def arxiv_search(args: ArxivSearchInput) -> ToolResult:
        query = ArxivQuery(
            text=args.query,
            author=args.author,
            category=args.category,
            max_results=args.max_results,
        )
        try:
            hits = await asyncio.to_thread(services.client.search, query)
        except ArxivError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if not hits:
            return ToolResult(content="No results.")
        return ToolResult(content="\n".join(_render_hit(h) for h in hits))

    async def fetch_paper(args: FetchPaperInput) -> ToolResult:
        try:
            content = await asyncio.to_thread(_fetch_sync, services, args)
        except (ArxivError, PaperStoreError) as exc:
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=content)

    async def read_paper(args: ReadPaperInput) -> ToolResult:
        id_v = f"{args.arxiv_id}v{args.version}"
        stored = services.store.get(id_v)
        if stored is None:
            return ToolResult(
                content=(
                    f"{id_v} is not in the paper store — call fetch_paper "
                    "first (read_paper never downloads)."
                ),
                is_error=True,
            )
        try:
            result = await asyncio.to_thread(
                services.reader.read, stored, args.section, args.offset, args.limit
            )
        except ReadError as exc:
            return ToolResult(content=str(exc), is_error=True)
        parts: list[str] = []
        if result.toc:
            parts.append("Contents: " + "; ".join(result.toc))
        if result.section:
            parts.append(f"[{result.section}]")
        parts.append(result.content)
        if result.truncated:
            parts.append("… [truncated — use offset/limit to page]")
        return ToolResult(content="\n".join(parts))

    async def cite(args: CiteInput) -> ToolResult:
        try:
            content = await asyncio.to_thread(_cite_sync, services, args)
        except (ArxivError, PaperStoreError) as exc:
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=content)

    return ToolRegistry([
        ToolDef(
            name="arxiv_search",
            description=(
                "Search arXiv. Returns id+version, title, authors, and a "
                "truncated abstract per hit. Results are cached; searching "
                "is cheap, fetching is not."
            ),
            input_model=ArxivSearchInput,
            handler=arxiv_search,
        ),
        ToolDef(
            name="fetch_paper",
            description=(
                "Download a paper into the immutable store (PDF + LaTeX "
                "source when available) and register it in the bibliography. "
                "Omit version to fetch the latest revision. Idempotent: a "
                "stored version is never re-downloaded."
            ),
            input_model=FetchPaperInput,
            handler=fetch_paper,
        ),
        ToolDef(
            name="read_paper",
            description=(
                "Read a fetched paper, section-chunked (LaTeX source) or "
                "page-chunked ('page:N', PDF text). First call returns a "
                "table of contents. Use offset/limit to page through."
            ),
            input_model=ReadPaperInput,
            handler=read_paper,
        ),
        ToolDef(
            name="cite",
            description=(
                "Look up or mint the cite key for an arXiv paper (no "
                "download). The ONLY way entries enter references.bib. "
                "Returns the key to use in \\cite{...}."
            ),
            input_model=CiteInput,
            handler=cite,
        ),
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_literature_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/tools/literature_tools.py tests/test_literature_tools.py
git commit -m "feat: arxiv_search/fetch_paper/read_paper/cite tools over the literature services"
```

---
### Task 11: Bibliography passes in the compile pipeline (`compile.py`)

**Files:**
- Modify: `src/hardy/latex/compile.py` (bibliography param, `bibtex` pass sequence, citation scan — local and sandboxed)
- Modify: `tests/fake_tectonic.py` (extensions only: `citeundef` mode + calls log; M0 tests stay green)
- Create: `tests/fake_bibtex.py`
- Test: `tests/test_compile_bib.py` (unit with fake engines; two `tex`-marked real compiles)

**Interfaces:**
- Consumes: M0's `compile_tex`/`compile_tex_sandboxed`/`_run_capped`/`_parse_errors`/`_log_tail` (real code, verified — see `src/hardy/latex/compile.py`).
- Produces (Task 12 relies on these exact signatures):
  - `compile_tex(source: str, staging: Path, *, engine: list[str] | None = None, extra_env: dict[str, str] | None = None, timeout: float = 120.0, bibliography: str | None = None, bib_engine: list[str] | None = None) -> CompileResult` — when `bibliography` is set: stage it as `references.bib`, run engine → `bibtex main` → engine → engine (all under the single shared `timeout`); when None: the M0 single pass, byte-identical behavior.
  - `compile_tex_sandboxed(source: str, staging: Path, *, image: str = "hardy-tex:dev", timeout: float = 120.0, bibliography: str | None = None) -> CompileResult` — `references.bib` joins `main.tex` in the read-only inputs mount; the in-container script runs the same pass chain.
  - `_citation_errors(log_text: str) -> list[TexError]` — every `Citation \`<key>' … undefined` line (plus the summary `There were undefined citations`) as structured errors. **Run unconditionally after every compile** (local and sandboxed): a `\cite` with no bibliography configured must fail too, not warn. A successful compile requires exit 0, a PDF, **and** zero citation errors.
- Stale-artifact hygiene extends to the bib pass: `main.aux`, `main.bbl`, `main.blg` join `main.pdf`/`main.log` in the pre-run deletion list (a stale `.bbl` could resolve citations from a previous run).
- Plan-assumption 11: the engine is TeX Live `lualatex` (not tectonic); `bibtex` must be on PATH for `tex`-marked tests and present in `hardy-tex:dev`.

- [ ] **Step 1: Extend the fake engines**

Append to the top of `tests/fake_tectonic.py`, right after `mode = os.environ.get(...)` (extensions only — every existing mode keeps its behavior):

```python
# Optional cross-engine call log, so multi-pass tests can assert sequence.
calls_log = os.environ.get("FAKE_TEX_CALLS")
if calls_log:
    with open(calls_log, "a") as fh:
        fh.write("tex\n")
```

and add one new mode branch before the final `else`:

```python
elif mode == "citeundef":
    # Exit 0 with a PDF, but the log reports an undefined citation — the
    # harness must treat this as a FAILURE, not a warning to ignore.
    Path("main.log").write_text(
        "LaTeX Warning: Citation `missing2020key-1.2' on page 1 "
        "undefined on input line 5.\n"
        "LaTeX Warning: There were undefined citations.\n"
    )
    Path("main.pdf").write_bytes(b"%PDF-1.4 fake")
    sys.exit(0)
```

Create `tests/fake_bibtex.py`:

```python
"""Fake bibtex for unit tests. Mode via FAKE_BIBTEX_MODE env var:
  ok (default) — writes main.bbl, exit 0
  fail         — bibtex-style error to stdout + main.blg, exit 2
Appends "bibtex" to the FAKE_TEX_CALLS log when set."""

import os
import sys
from pathlib import Path

calls_log = os.environ.get("FAKE_TEX_CALLS")
if calls_log:
    with open(calls_log, "a") as fh:
        fh.write("bibtex\n")

mode = os.environ.get("FAKE_BIBTEX_MODE", "ok")

if mode == "fail":
    sys.stdout.write('I couldn\'t open database file references.bib\n')
    Path("main.blg").write_text("This is BibTeX\nI couldn't open database file\n")
    sys.exit(2)
else:
    Path("main.bbl").write_text("\\begin{thebibliography}{1}\\end{thebibliography}\n")
    sys.exit(0)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_compile_bib.py
import sys
from pathlib import Path

import pytest

from hardy.latex.compile import _citation_errors, compile_tex

FAKE_ENGINE = [sys.executable, str(Path(__file__).parent / "fake_tectonic.py")]
FAKE_BIBTEX = [sys.executable, str(Path(__file__).parent / "fake_bibtex.py")]

SOURCE = r"\documentclass{article}\begin{document}hi \cite{k}\end{document}"
BIB = "@misc{k,\n  title = {T},\n  author = {A},\n  year = {2020},\n}\n"


def run(tmp_path, *, bibliography=None, extra_env=None, **kw):
    env = {"FAKE_TEX_CALLS": str(tmp_path / "calls.log")}
    env.update(extra_env or {})
    return compile_tex(
        SOURCE, tmp_path / "staging",
        engine=FAKE_ENGINE, bib_engine=FAKE_BIBTEX,
        bibliography=bibliography, extra_env=env, **kw,
    )


def calls(tmp_path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().split() if log.exists() else []


def test_no_bibliography_is_single_pass(tmp_path):
    result = run(tmp_path)
    assert result.success
    assert calls(tmp_path) == ["tex"]
    assert not (tmp_path / "staging" / "references.bib").exists()


def test_bibliography_runs_engine_bibtex_engine_engine(tmp_path):
    result = run(tmp_path, bibliography=BIB)
    assert result.success
    assert calls(tmp_path) == ["tex", "bibtex", "tex", "tex"]
    assert (tmp_path / "staging" / "references.bib").read_text() == BIB


def test_undefined_citation_fails_despite_exit_zero(tmp_path):
    result = run(
        tmp_path, bibliography=BIB, extra_env={"FAKE_TEX_MODE": "citeundef"}
    )
    assert not result.success
    assert result.pdf_path is None
    assert any("missing2020key-1.2" in e.message for e in result.errors)


def test_cite_without_bibliography_still_fails(tmp_path):
    # No bibliography configured, but the log reports undefined citations:
    # the scan is unconditional.
    result = run(tmp_path, extra_env={"FAKE_TEX_MODE": "citeundef"})
    assert not result.success
    assert any("undefined" in e.message for e in result.errors)


def test_bibtex_failure_stops_the_chain(tmp_path):
    result = run(
        tmp_path, bibliography=BIB, extra_env={"FAKE_BIBTEX_MODE": "fail"}
    )
    assert not result.success
    assert calls(tmp_path) == ["tex", "bibtex"]      # later passes never ran
    assert result.errors                             # structured, non-empty


def test_stale_bib_artifacts_deleted_before_run(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    (staging / "main.bbl").write_text("stale")
    (staging / "main.aux").write_text("stale")
    result = run(tmp_path)                           # no bibliography
    assert result.success
    assert not (staging / "main.bbl").exists()
    assert not (staging / "main.aux").exists()


def test_citation_errors_parser():
    log = (
        "LaTeX Warning: Citation `a2020x-1.2' on page 1 undefined on input line 3.\n"
        "LaTeX Warning: Citation `b2021y-3.4' on page 2 undefined on input line 9.\n"
        "LaTeX Warning: There were undefined citations.\n"
    )
    errors = _citation_errors(log)
    assert [e.message for e in errors[:2]] == [
        "Citation `a2020x-1.2' undefined",
        "Citation `b2021y-3.4' undefined",
    ]


def test_citation_errors_summary_only():
    errors = _citation_errors("LaTeX Warning: There were undefined citations.\n")
    assert len(errors) == 1 and "undefined citations" in errors[0].message


def test_clean_log_has_no_citation_errors():
    assert _citation_errors("all good, nothing cited\n") == []


REAL_DOC = r"""\documentclass{article}
\begin{document}
As shown in \cite{good2020ref-1.2}, things hold.
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""
REAL_BIB = (
    "@misc{good2020ref-1.2,\n  title = {A Reference},\n"
    "  author = {Some Author},\n  year = {2020},\n"
    "  note = {arXiv 1.2v1},\n}\n"
)


@pytest.mark.tex
def test_real_cited_document_compiles(tmp_path):
    result = compile_tex(REAL_DOC, tmp_path / "staging", bibliography=REAL_BIB)
    assert result.success, [e.message for e in result.errors]
    assert result.pdf_path.exists()


@pytest.mark.tex
def test_real_unknown_key_is_a_failure(tmp_path):
    doc = REAL_DOC.replace("good2020ref-1.2", "nosuchkey2020-9.9")
    result = compile_tex(doc, tmp_path / "staging", bibliography=REAL_BIB)
    assert not result.success
    assert any("nosuchkey2020-9.9" in e.message for e in result.errors)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_compile_bib.py -v`
Expected: FAIL — `TypeError: compile_tex() got an unexpected keyword argument 'bibliography'` (and `ImportError` for `_citation_errors`)

- [ ] **Step 4: Implement the compile changes**

In `src/hardy/latex/compile.py`, add the citation scanner near `_parse_errors`:

```python
_CITATION_RE = re.compile(r"Citation `([^']+)'.*undefined")
_UNDEF_SUMMARY_RE = re.compile(r"There were undefined citations")


def _citation_errors(log_text: str) -> list[TexError]:
    """Undefined citations are errors, not warnings to ignore (M3 spec)."""
    errors = [
        TexError(message=f"Citation `{match.group(1)}' undefined")
        for match in _CITATION_RE.finditer(log_text)
    ]
    if not errors and _UNDEF_SUMMARY_RE.search(log_text):
        errors.append(TexError(message="undefined citations reported by LaTeX"))
    return errors
```

Extend the stale-artifact list used by both entry points (module constant, replacing the inline tuples):

```python
_STALE_ARTIFACTS = ("main.pdf", "main.log", "main.aux", "main.bbl", "main.blg")
```

Replace `compile_tex` with the multi-pass version (everything it calls — `_run_capped`, `_parse_errors`, `_log_tail`, `DEFAULT_ENGINE` — is unchanged):

```python
def compile_tex(
    source: str,
    staging: Path,
    *,
    engine: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: float = 120.0,
    bibliography: str | None = None,
    bib_engine: list[str] | None = None,
) -> CompileResult:
    """Compile with a local engine argv. With `bibliography`, stages it as
    references.bib and runs engine -> bibtex -> engine -> engine under the
    single shared timeout (lualatex does not run the bib pass itself).
    Undefined citations always fail the compile — scanned unconditionally.
    For sandboxed compilation use compile_tex_sandboxed."""
    staging.mkdir(parents=True, exist_ok=True)
    for stale in _STALE_ARTIFACTS:
        # Never grade this run against a previous run's artifacts — a stale
        # .bbl could resolve citations a fresh run would leave undefined.
        (staging / stale).unlink(missing_ok=True)
    (staging / "main.tex").write_text(source)
    if bibliography is not None:
        (staging / "references.bib").write_text(bibliography)
    engine_argv = list(engine or DEFAULT_ENGINE) + ["main.tex"]
    if bibliography is None:
        passes = [engine_argv]
    else:
        bibtex_argv = list(bib_engine or ["bibtex"]) + ["main"]
        passes = [engine_argv, bibtex_argv, engine_argv, engine_argv]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(staging)}
    env.update(extra_env or {})
    deadline = time.monotonic() + timeout
    outputs: list[str] = []
    code: int | None = None
    for argv in passes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return CompileResult(
                success=False,
                errors=[TexError(message=f"compile aborted: timed out after {timeout}s")],
                log_tail="\n".join(outputs)[-2000:],
            )
        try:
            code, output, over_cap = _run_capped(argv, staging, env, remaining)
        except OSError as exc:
            return CompileResult(
                success=False,
                errors=[TexError(message=f"could not launch compiler: {exc}")],
            )
        outputs.append(output)
        if code is None:
            reason = (
                f"output exceeded {_OUTPUT_CAP} byte cap"
                if over_cap
                else f"timed out after {timeout}s"
            )
            return CompileResult(
                success=False,
                errors=[TexError(message=f"compile aborted: {reason}")],
                log_tail="\n".join(outputs)[-2000:],
            )
        if code != 0:
            break  # stop the chain; errors are parsed below
    output = "\n".join(outputs)
    log_tail = _log_tail(staging / "main.log")
    errors = _parse_errors(output, log_tail)
    cite_errors = _citation_errors(log_tail)
    pdf = staging / "main.pdf"
    success = code == 0 and pdf.exists() and not cite_errors
    if not success:
        errors = errors + cite_errors
        if not errors:
            errors = [TexError(message=(output.strip() or "compile failed")[-500:])]
    return CompileResult(
        success=success,
        errors=[] if success else errors,
        log_tail=output[-2000:],
        pdf_path=pdf if success else None,
    )
```

In `compile_tex_sandboxed`, make four edits:
1. Signature gains `bibliography: str | None = None`.
2. The stale-deletion loop iterates `_STALE_ARTIFACTS`.
3. After writing `inputs / "main.tex"`, add:

```python
        if bibliography is not None:
            (inputs / "references.bib").write_text(bibliography)
            os.chmod(inputs / "references.bib", 0o644)
```

4. Build the in-container script conditionally (replacing the fixed `script = (...)`):

```python
        copy_cmd = "cp /staging/main.tex /scratch/"
        if bibliography is not None:
            copy_cmd += " && cp /staging/references.bib /scratch/"
        engine_cmd = (
            "lualatex -interaction=nonstopmode -halt-on-error "
            "--no-shell-escape main.tex"
        )
        if bibliography is None:
            compile_cmd = engine_cmd
        else:
            compile_cmd = (
                f"{engine_cmd} && bibtex main && {engine_cmd} && {engine_cmd}"
            )
        script = (
            f"{copy_cmd} && cd /scratch && "
            "export HOME=/scratch TEXMFVAR=/scratch/texmf-var && "
            f"timeout {_sandbox_timeout_arg(timeout)} "
            f"sh -c '{compile_cmd}' >&2; "
            "status=$?; tar -cf - main.pdf main.log 2>/dev/null; exit $status"
        )
```

and extend its verdict to include the citation scan (the lines computing `errors`/`success` after `_extract_artifacts`):

```python
    _extract_artifacts(tar_bytes, staging)
    log_tail = _log_tail(staging / "main.log")
    errors = _parse_errors(stderr_text, log_tail)
    cite_errors = _citation_errors(log_tail)
    pdf = staging / "main.pdf"
    success = code == 0 and pdf.exists() and not cite_errors
    if not success:
        errors = errors + cite_errors
        if not errors:
            errors = [TexError(message=(stderr_text.strip() or "compile failed")[-500:])]
    return CompileResult(
        success=success,
        errors=[] if success else errors,
        log_tail=stderr_text[-2000:],
        pdf_path=pdf if success else None,
    )
```

- [ ] **Step 5: Run tests — new and M0's — to verify they pass**

Run: `pytest tests/test_compile_bib.py -m "not tex" tests/test_compile.py -v`
Expected: all PASS (`tests/test_compile.py` unmodified — the no-bibliography path is byte-identical M0 behavior)

On a host with TeX Live (`lualatex` + `bibtex` on PATH), also run:
`pytest tests/test_compile_bib.py -m tex -v`
Expected: both `tex`-marked tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/latex/compile.py tests/fake_tectonic.py tests/fake_bibtex.py tests/test_compile_bib.py
git commit -m "feat: bibliography passes in the compile pipeline; undefined citations are failures"
```

---

### Task 12: Citations through the writeup pipeline (`confine.py`, `template.py`, `latex_tools.py`)

**Files:**
- Modify: `src/hardy/latex/confine.py` (add `cite` to the allowlist)
- Modify: `src/hardy/latex/template.py` (`<<BIBLIOGRAPHY_BLOCK>>` slot + `bibliography` kwarg)
- Modify: `src/hardy/tools/latex_tools.py` (`bib_path` kwarg, key extraction, fragment staging)
- Modify: `tests/test_latex_tools.py` (M1 file: fakes grow `bibliography=None` — extension only)
- Test: `tests/test_writeup_citations.py` (M0's `tests/test_template.py` and M1's `tests/test_template_m1.py`/`tests/test_confine.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `violations`/`ALLOWED_COMMANDS` and `render_writeup` (**M1 plan Task 5 — plan-assumptions 2/3**), `make_writeup_registry` (**M1 plan Task 6 — plan-assumption 4**), `Bibliography` (Task 7), `compile_tex`'s `bibliography` kwarg (Task 11).
- Produces (Task 13 relies on these exact signatures):
  - `confine.ALLOWED_COMMANDS` now contains `cite` — `\cite{key}` is admissible in model-authored fields; everything else about the allowlist is untouched.
  - `template.render_writeup(..., bibliography: bool = False)` — keyword-only addition; when True the document ends with `\bibliographystyle{plain}` + `\bibliography{references}` before `\end{document}`; when False (default) the slot renders empty and the output is byte-identical to M1's.
  - `latex_tools.make_writeup_registry(..., bib_path: Path | None = None)` — keyword-only addition. The `write_latex` handler now: (1) confines fields (unchanged); (2) extracts cited keys from `informal_proof` (`\cite{a}` and `\cite{a,b}` forms); (3) with keys but `bib_path=None` → tool error "citations are not available in this run"; (4) keys missing from the bibliography → tool error naming them and pointing at the `cite` tool; (5) renders with `bibliography=bool(keys)` and calls `compile_fn(source, staging, bibliography=fragment_or_None)` where the fragment contains **only the cited keys** (the disclosure rule: the sandboxed compiler never sees the whole project `.bib`).
  - `latex_tools.extract_cite_keys(text: str) -> list[str]` — exported for reuse and direct testing; sorted, deduped.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_writeup_citations.py
from pathlib import Path

from hardy.latex.compile import CompileResult
from hardy.latex.confine import violations
from hardy.latex.template import render_writeup
from hardy.literature.bibliography import add_or_get
from hardy.tools.latex_tools import extract_cite_keys, make_writeup_registry
from tests.fake_arxiv import sample_meta

KEY = "smith2023modular-2301.12345V2"


def test_cite_is_allowlisted():
    assert violations(r"As shown in \cite{smith2023modular-2301.12345V2}.") == []


def test_other_bib_commands_still_rejected():
    assert violations(r"\bibliography{other}")
    assert violations(r"\bibliographystyle{alpha}")
    assert violations(r"\nocite{*}")


def test_extract_cite_keys_single_multi_and_dedup():
    text = r"See \cite{a2020x-1.2} and \cite{b2021y-3.4, a2020x-1.2}."
    assert extract_cite_keys(text) == ["a2020x-1.2", "b2021y-3.4"]
    assert extract_cite_keys("no citations here") == []


def test_render_writeup_bibliography_block():
    doc = render_writeup(
        title="T", statement="s", informal_proof=r"p \cite{k}",
        formalization_status="verified", bibliography=True,
    )
    assert "\\bibliographystyle{plain}" in doc
    assert "\\bibliography{references}" in doc
    assert doc.index("\\bibliography{references}") < doc.index("\\end{document}")


def test_render_writeup_default_has_no_bibliography():
    doc = render_writeup(
        title="T", statement="s", informal_proof="p",
        formalization_status="verified",
    )
    assert "\\bibliography" not in doc


def bib_with_key(tmp_path) -> Path:
    path = tmp_path / "references.bib"
    assert add_or_get(path, sample_meta()) == KEY
    add_or_get(path, sample_meta(arxiv_id="2401.00001", title="Zeta stuff"))
    return path


def make(tmp_path, compile_fn, bib_path=None):
    published: list[str] = []
    reg = make_writeup_registry(
        statement_text="claim text",
        lean_statement="theorem t : True",
        formalization_status="verified",
        lean_file="t.lean",
        compile_fn=compile_fn,
        staging=tmp_path / "staging",
        published=published,
        bib_path=bib_path,
    )
    return reg, published


async def test_cited_writeup_stages_only_the_needed_fragment(tmp_path):
    seen: list = []

    def spy(source, staging, bibliography=None):
        seen.append((source, bibliography))
        return CompileResult(success=True, pdf_path=staging / "main.pdf")

    reg, published = make(tmp_path, spy, bib_path=bib_with_key(tmp_path))
    result = await reg.get("write_latex").call({
        "title": "T",
        "informal_proof": rf"By \cite{{{KEY}}} we conclude.",
    })
    assert not result.is_error
    [(source, fragment)] = seen
    assert "\\bibliography{references}" in source
    assert KEY in fragment
    assert "zeta" not in fragment.lower()       # disclosure rule: cited keys only
    assert published and KEY in published[-1]


async def test_uncited_writeup_passes_no_bibliography(tmp_path):
    seen: list = []

    def spy(source, staging, bibliography=None):
        seen.append(bibliography)
        return CompileResult(success=True, pdf_path=staging / "main.pdf")

    reg, _ = make(tmp_path, spy, bib_path=bib_with_key(tmp_path))
    result = await reg.get("write_latex").call(
        {"title": "T", "informal_proof": "No citations."}
    )
    assert not result.is_error
    assert seen == [None]


async def test_cite_without_bib_path_is_rejected(tmp_path):
    calls: list = []

    def spy(source, staging, bibliography=None):
        calls.append(source)
        return CompileResult(success=True)

    reg, published = make(tmp_path, spy, bib_path=None)
    result = await reg.get("write_latex").call({
        "title": "T", "informal_proof": rf"\cite{{{KEY}}}",
    })
    assert result.is_error
    assert "not available" in result.content
    assert calls == [] and published == []


async def test_unknown_cite_key_is_rejected_before_compiling(tmp_path):
    calls: list = []

    def spy(source, staging, bibliography=None):
        calls.append(source)
        return CompileResult(success=True)

    reg, _ = make(tmp_path, spy, bib_path=bib_with_key(tmp_path))
    result = await reg.get("write_latex").call({
        "title": "T", "informal_proof": r"\cite{made2019up-0.0}",
    })
    assert result.is_error
    assert "made2019up-0.0" in result.content
    assert "cite" in result.content             # points at the cite tool
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_writeup_citations.py -v`
Expected: FAIL — `violations(r"\cite{...}")` non-empty, `ImportError: cannot import name 'extract_cite_keys'`

- [ ] **Step 3: One-word allowlist edit**

In `src/hardy/latex/confine.py`, add `cite` to the `ALLOWED_COMMANDS` triple-quoted word list (one word, anywhere in the string — e.g. after `end`). `\bibliography`, `\bibliographystyle`, and `\nocite` stay out: the *template* emits the bibliography block; the model only cites.

- [ ] **Step 4: Template slot**

In `src/hardy/latex/template.py`:

1. In `_TEMPLATE`, insert the slot on its own line immediately before `\end{document}`:

```latex
<<BIBLIOGRAPHY_BLOCK>>
\end{document}
```

2. `render_writeup` gains the keyword-only parameter `bibliography: bool = False` and its substitution map gains:

```python
        "<<BIBLIOGRAPHY_BLOCK>>": (
            "\\bibliographystyle{plain}\n\\bibliography{references}\n"
            if bibliography
            else ""
        ),
```

(M0's `tests/test_template.py` and M1's `tests/test_template_m1.py` stay green: the default renders the slot empty.)

- [ ] **Step 5: `write_latex` citation support**

In `src/hardy/tools/latex_tools.py`, add at module level:

```python
import re

from hardy.literature.bibliography import Bibliography

_CITE_RE = re.compile(r"\\cite\{([^}]*)\}")


def extract_cite_keys(text: str) -> list[str]:
    keys: set[str] = set()
    for group in _CITE_RE.findall(text):
        for key in group.split(","):
            if key.strip():
                keys.add(key.strip())
    return sorted(keys)
```

`make_writeup_registry` gains the keyword-only parameter `bib_path: Path | None = None`, and the `write_latex` handler body becomes (confinement block unchanged; everything after it replaced):

```python
    async def write_latex(args: WriteLatexInput) -> ToolResult:
        problems: list[str] = []
        for field, value in (("title", args.title),
                             ("informal_proof", args.informal_proof)):
            problems += [f"{field}: {v}" for v in violations(value)]
        if problems:
            return ToolResult(
                content="rejected by the field allowlist:\n" + "\n".join(problems),
                is_error=True,
            )
        keys = extract_cite_keys(args.informal_proof)
        fragment: str | None = None
        if keys:
            if bib_path is None:
                return ToolResult(
                    content=(
                        "citations are not available in this run — remove the "
                        "\\cite commands (no literature services configured)"
                    ),
                    is_error=True,
                )
            bibliography = Bibliography.load(bib_path)
            unknown = [k for k in keys if k not in bibliography.entries]
            if unknown:
                return ToolResult(
                    content=(
                        f"unknown cite keys: {', '.join(unknown)} — mint keys "
                        "with the cite tool (or fetch_paper) first and use "
                        "exactly the key it returns"
                    ),
                    is_error=True,
                )
            fragment = bibliography.fragment(keys)
        source = render_writeup(
            title=args.title,
            statement=statement_text,
            informal_proof=args.informal_proof,
            formalization_status=formalization_status,
            lean_file=lean_file,
            lean_statement=lean_statement,
            statement_is_verbatim_user_claim=lean_statement is None,
            bibliography=fragment is not None,
        )
        result = compile_fn(source, staging, bibliography=fragment)
        if not result.success:
            lines = ["compile failed:"]
            for err in result.errors:
                pos = f"line {err.line}: " if err.line else ""
                lines.append(f"  {pos}{err.message}")
            return ToolResult(content="\n".join(lines), is_error=True)
        published.append(source)
        return ToolResult(content="Writeup compiled successfully.")
```

Update the tool's description string to retire M1's no-citations rule:

```python
            description=(
                "Draft the writeup: provide a title and the informal proof "
                "text (plain text + standard math; cite fetched papers with "
                "\\cite{key} using keys from the cite tool; the harness owns "
                "the theorem statement, grades, and bibliography block). The "
                "document is compile-checked — including the bibliography — "
                "before this tool reports success."
            ),
```

In M1's `tests/test_latex_tools.py`, extend the injected fakes' signatures (behavior unchanged): `def ok_compiler(source, staging, bibliography=None)`, `def failing_compiler(source, staging, bibliography=None)`, and the inline `spy_compiler(source, staging, bibliography=None)` — assertions untouched.

- [ ] **Step 6: Run tests — new plus every guarded suite**

Run: `pytest tests/test_writeup_citations.py tests/test_latex_tools.py tests/test_confine.py tests/test_template.py tests/test_template_m1.py -v`
Expected: all PASS (`test_template.py` unmodified; `test_confine.py`/`test_template_m1.py` unmodified)

- [ ] **Step 7: Commit**

```bash
git add src/hardy/latex/confine.py src/hardy/latex/template.py src/hardy/tools/latex_tools.py tests/test_writeup_citations.py tests/test_latex_tools.py
git commit -m "feat: \\cite through the writeup pipeline — allowlisted, fragment-staged, compile-gated"
```

---
### Task 13: Literature in the Prove workflow (`prove.py`, prompts, manifest)

**Files:**
- Create: `src/hardy/prompts/literature_v1.py`
- Modify: `src/hardy/prompts/__init__.py` (register `writeup_cited_v1`)
- Modify: `src/hardy/workflows/persist.py` (`Manifest.papers`)
- Modify: `src/hardy/workflows/prove.py` (`literature` kwarg, merged writeup registry, prompt selection, manifest papers)
- Test: `tests/test_prove_literature.py` (M1's `tests/test_prove.py` must stay green, unmodified)

**Interfaces:**
- Consumes: `prove`/`ProveConfig`/`_DEFAULT_PROMPTS`/writeup-phase structure (**M1 plan Task 14 — plan-assumption 6**), `Manifest` (**M1 plan Task 13 — plan-assumption 5**), `get_prompt` registry (**M1 plan Task 10 — plan-assumption 7**), `LiteratureServices`/`make_literature_registry` (Task 10), `make_writeup_registry(bib_path=…)` (Task 12), `FakeRuntime` (**plan-assumption 8**).
- Produces (Task 14 relies on these exact signatures):
  - `get_prompt("writeup_cited_v1")` — placeholders `{statement, status}` (same as `writeup_v1`); instructs the agent to search/fetch/read, mint keys via `cite`, and use `\cite{key}` in the proof text.
  - `Manifest` gains `papers: list[dict] = []` — each record `{"arxiv_id", "version", "cite_key", "pdf_sha256"}` (Task 10's `used_papers` shape).
  - `prove(claim, *, pool, runtime, config, results_dir, run_id, literature: LiteratureServices | None = None)` — with literature: the writeup registry is `make_writeup_registry(..., bib_path=literature.bib_path)` **plus** the four literature tools merged in; the writeup phase uses prompt key `"writeup_cited"`; the manifest records `literature.used_papers`. Without literature: behavior is M1's, byte-identical (`bib_path=None`, prompt key `"writeup"`, `papers=[]`) — M1's "no citations" rule is retired *by configuration*, not by fork.
  - `_DEFAULT_PROMPTS` gains `"writeup_cited": "writeup_cited_v1"` (so `ProveConfig.prompt_versions` carries it by default and M2-style config hashing sees it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prove_literature.py
import json
import sys
from pathlib import Path

import pytest

from hardy.latex.compile import CompileResult
from hardy.lean.pool import ReplPool
from hardy.prompts import get_prompt
from hardy.tools.literature_tools import make_literature_services
from hardy.workflows import prove as prove_mod
from hardy.workflows.persist import Manifest
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_arxiv import TINY_PDF, FakeTransport, sample_meta
from tests.fake_runtime import FakeRuntime

FAKE = [sys.executable, "tests/fake_repl.py"]
CLAIM = "every widget is self-identical"
STMT = "theorem widget_refl : True"
KEY = "smith2023modular-2301.12345V2"


def tar_bytes(files):
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def make_services(tmp_path):
    transport = FakeTransport({
        "2301.12345": {
            "meta": sample_meta(),
            "pdf": TINY_PDF,
            "source": tar_bytes({"main.tex": b"\\documentclass{article}x"}),
        },
    })
    return make_literature_services(
        tmp_path / "papers", tmp_path / "references.bib", transport=transport
    )


@pytest.fixture
def ok_compile(monkeypatch):
    def fake_compile(source: str, staging: Path, bibliography=None) -> CompileResult:
        return CompileResult(success=True, pdf_path=staging / "main.pdf")

    monkeypatch.setattr(prove_mod, "_compile_fn_local", lambda: fake_compile)
    return fake_compile


def cfg(**kw) -> ProveConfig:
    defaults = dict(model="m", max_turns=100, wall_clock_s=600.0,
                    sandbox_tex=False)
    defaults.update(kw)
    return ProveConfig(**defaults)


async def run_prove(runtime, tmp_path, literature=None, **kw):
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        return await prove(
            CLAIM, pool=pool, runtime=runtime, config=cfg(**kw),
            results_dir=tmp_path / "results", run_id="r1",
            literature=literature,
        )
    finally:
        await pool.close()


def cited_scripts():
    return [
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "proposed"}],
        [{"text": "VERDICT: faithful"}],
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        [{"tool": "fetch_paper", "arguments": {"arxiv_id": "2301.12345"}},
         {"tool": "write_latex",
          "arguments": {"title": "Widgets",
                        "informal_proof": rf"By \cite{{{KEY}}}, trivial."}},
         {"text": "written"}],
    ]


def test_writeup_cited_prompt_registered():
    text = get_prompt("writeup_cited_v1")
    assert len(text) > 100
    text.format(statement="s", status="verified")
    for tool in ("fetch_paper", "cite", "write_latex"):
        assert tool in text


def test_manifest_papers_field_round_trips():
    manifest = Manifest(
        claim="c", formalization_status="verified", outcome="proved",
        papers=[{"arxiv_id": "2301.12345", "version": 2,
                 "cite_key": KEY, "pdf_sha256": "ab" * 32}],
    )
    again = Manifest.model_validate_json(manifest.model_dump_json())
    assert again.papers[0]["cite_key"] == KEY
    assert Manifest(claim="c", formalization_status="x", outcome="o").papers == []


async def test_cited_prove_publishes_bibliography_and_provenance(
    tmp_path, ok_compile
):
    services = make_services(tmp_path)
    fake = FakeRuntime(scripts=cited_scripts())
    result = await run_prove(fake, tmp_path, literature=services)
    assert result.outcome == "proved"
    out = result.published_path
    tex = (out / "every-widget-is-self-identical.tex").read_text()
    assert rf"\cite{{{KEY}}}" in tex
    assert r"\bibliography{references}" in tex
    manifest = json.loads((out / "manifest.json").read_text())
    [record] = manifest["papers"]
    assert record["cite_key"] == KEY
    assert record["pdf_sha256"] is not None


async def test_writeup_phase_carries_literature_tools_and_prompt(
    tmp_path, ok_compile
):
    services = make_services(tmp_path)
    fake = FakeRuntime(scripts=cited_scripts())
    await run_prove(fake, tmp_path, literature=services)
    writeup_call = fake.calls[3]
    for name in ("arxiv_search", "fetch_paper", "read_paper", "cite",
                 "write_latex"):
        assert name in writeup_call["tool_names"]
    # the cited prompt, not M1's
    assert "cite" in writeup_call["system_prompt"]
    assert writeup_call["system_prompt"] == get_prompt("writeup_cited_v1").format(
        statement=STMT, status="verified"
    )


async def test_without_literature_behavior_is_m1(tmp_path, ok_compile):
    scripts = cited_scripts()
    scripts[3] = [
        {"tool": "write_latex",
         "arguments": {"title": "Widgets", "informal_proof": "Trivial."}},
        {"text": "written"},
    ]
    fake = FakeRuntime(scripts=scripts)
    result = await run_prove(fake, tmp_path, literature=None)
    assert result.outcome == "proved"
    writeup_call = fake.calls[3]
    assert writeup_call["tool_names"] == ["write_latex"]
    manifest = json.loads(
        (result.published_path / "manifest.json").read_text()
    )
    assert manifest["papers"] == []


async def test_earlier_phases_never_see_literature_tools(tmp_path, ok_compile):
    services = make_services(tmp_path)
    fake = FakeRuntime(scripts=cited_scripts())
    await run_prove(fake, tmp_path, literature=services)
    for index in (0, 1, 2):                 # formalize, skeptic, prove
        assert "fetch_paper" not in fake.calls[index]["tool_names"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prove_literature.py -v`
Expected: FAIL — `KeyError: 'writeup_cited_v1'`, then `TypeError: prove() got an unexpected keyword argument 'literature'`

- [ ] **Step 3: The cited-writeup prompt**

```python
# src/hardy/prompts/literature_v1.py
"""M3 prompt templates, version 1. Same rules as prove_v1: plain
strings, .format() placeholders, no logic. Braces LaTeX needs are
doubled ({{ }})."""

WRITEUP_CITED_V1 = """Write the human-facing writeup for this result, with citations.

Theorem (fixed, rendered by the harness — do not restate it): {statement}
Formalization status (set by the harness): {status}

You have literature tools: arxiv_search finds papers; fetch_paper
downloads one into the store and returns its cite key; read_paper reads
a fetched paper section by section; cite looks up or mints a cite key
without downloading. Cite the papers your writeup actually relies on —
reference them in the proof text as \\cite{{key}}, using EXACTLY the keys
those tools return (never invent a key). The harness owns the document
shell, the statement, the verification grades, and the bibliography
block; write_latex compile-checks the bibliography, and an undefined
citation is an error you must fix.

Call write_latex with a title and the informal proof text. If it reports
compile errors, allowlist rejections, or unknown cite keys, fix the
fields and call it again."""
```

In `src/hardy/prompts/__init__.py`, extend the registry:

```python
from . import literature_v1, prove_v1

_PROMPTS: dict[str, str] = {
    "formalize_v1": prove_v1.FORMALIZE_V1,
    "prove_v1": prove_v1.PROVE_V1,
    "faithfulness_v1": prove_v1.FAITHFULNESS_V1,
    "writeup_v1": prove_v1.WRITEUP_V1,
    "writeup_cited_v1": literature_v1.WRITEUP_CITED_V1,
}
```

- [ ] **Step 4: Manifest field**

In `src/hardy/workflows/persist.py`, add to `Manifest` (after `audit`):

```python
    papers: list[dict] = []
```

- [ ] **Step 5: Wire literature into `prove.py`**

In `src/hardy/workflows/prove.py`:

1. Imports gain:

```python
from hardy.tools.literature_tools import LiteratureServices, make_literature_registry
```

2. `_DEFAULT_PROMPTS` gains `"writeup_cited": "writeup_cited_v1"`.

3. `prove`'s signature gains the trailing keyword-only parameter `literature: LiteratureServices | None = None`.

4. In the writeup phase, replace the registry construction and prompt selection with:

```python
        writeup_registry = make_writeup_registry(
            statement_text=claim,
            lean_statement=statement_header,
            formalization_status=formalization_status,
            lean_file=f"{slug}.lean" if wins else None,
            compile_fn=compile_fn,
            staging=staging,
            published=published_sources,
            bib_path=literature.bib_path if literature is not None else None,
        )
        if literature is not None:
            for tool in make_literature_registry(literature):
                writeup_registry.add(tool)
        writeup_key = "writeup_cited" if literature is not None else "writeup"
        prompt = get_prompt(config.prompt_versions[writeup_key]).format(
            statement=statement_header or claim, status=formalization_status
        )
        for _ in range(config.max_writeup_retries):
            ran = await phase_run(
                writeup_key, "Write up the result.", prompt, writeup_registry
            )
            if ran is None or published_sources:
                break
```

(`phase_run` resolves `config.prompt_versions[phase]` for the trajectory's `prompt_version`, so `writeup_key` doubles as the phase name — the manifest's `prompt_versions` already carries both keys via `_DEFAULT_PROMPTS`.)

5. The `Manifest(...)` construction gains:

```python
        papers=list(literature.used_papers) if literature is not None else [],
```

- [ ] **Step 6: Run tests — new plus M1's workflow suite**

Run: `pytest tests/test_prove_literature.py tests/test_prove.py tests/test_prompts.py -v`
Expected: all PASS (`tests/test_prove.py` unmodified — the `literature=None` default preserves M1 behavior)

- [ ] **Step 7: Commit**

```bash
git add src/hardy/prompts/literature_v1.py src/hardy/prompts/__init__.py src/hardy/workflows/persist.py src/hardy/workflows/prove.py tests/test_prove_literature.py
git commit -m "feat: literature tools in the Prove writeup phase; paper provenance in the manifest"
```

---

### Task 14: Exit criterion — cited writeup end to end (`tex` rehearsal, live `network` test, `scripts/prove_cited.py`)

**Files:**
- Create: `tests/test_integration_cited_writeup.py` (`tex` marker — deterministic, no network, real compile)
- Create: `tests/test_network_arxiv.py` (`network` marker — live arXiv, never CI)
- Create: `scripts/prove_cited.py` (model + network; never CI)

**Interfaces:**
- Consumes: the full stack.
- Produces: the M3 exit-criterion script and the deterministic rehearsal that CI-adjacent hosts can run. **M3 is not complete until the spec's exit criterion is met: a writeup that cites fetched papers with a valid bibliography.**

- [ ] **Step 1: The deterministic exit-criterion rehearsal (`tex` marker)**

```python
# tests/test_integration_cited_writeup.py
"""M3 exit-criterion rehearsal, deterministic: FakeRuntime + FakeTransport
(no model, no network), but the REAL compile pipeline — lualatex + bibtex
over a staged bibliography fragment — and the real store/bibliography/
journal plumbing. Asserts exactly the exit criterion: a published writeup
that cites fetched papers with a valid bibliography."""

import json
import sys

import pytest

from hardy.literature.bibliography import validate
from hardy.lean.pool import ReplPool
from hardy.tools.literature_tools import make_literature_services
from hardy.workflows.prove import ProveConfig, prove
from tests.fake_arxiv import TINY_PDF, FakeTransport, sample_meta
from tests.fake_runtime import FakeRuntime

pytestmark = pytest.mark.tex

FAKE = [sys.executable, "tests/fake_repl.py"]
CLAIM = "widgets and zeta functions commute"
STMT = "theorem widgets_zeta : True"
KEY1 = "smith2023modular-2301.12345V2"
KEY2 = "smith2023zeta-2401.00001"


def tar_bytes(files):
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def make_services(tmp_path):
    transport = FakeTransport({
        "2301.12345": {
            "meta": sample_meta(),
            "pdf": TINY_PDF,
            "source": tar_bytes({"main.tex": b"\\documentclass{article}x"}),
        },
        "2401.00001": {
            "meta": sample_meta(
                arxiv_id="2401.00001", version=1, title="Zeta stuff"
            ),
            "pdf": TINY_PDF,
            "source": None,
        },
    })
    return make_literature_services(
        tmp_path / "papers", tmp_path / "references.bib", transport=transport
    )


async def test_cited_writeup_end_to_end(tmp_path):
    services = make_services(tmp_path)
    proof_text = (
        rf"Combining the widget theory of \cite{{{KEY1}}} with the zeta "
        rf"estimates of \cite{{{KEY2}}}, the claim is immediate."
    )
    fake = FakeRuntime(scripts=[
        [{"tool": "propose_statement", "arguments": {"statement": STMT}},
         {"text": "proposed"}],
        [{"text": "VERDICT: faithful"}],
        [{"tool": "check_proof", "arguments": {"proof": "trivial"}},
         {"text": "proved"}],
        [{"tool": "fetch_paper", "arguments": {"arxiv_id": "2301.12345"}},
         {"tool": "fetch_paper", "arguments": {"arxiv_id": "2401.00001"}},
         {"tool": "write_latex",
          "arguments": {"title": "Widgets and Zeta",
                        "informal_proof": proof_text}},
         {"text": "written"}],
    ])
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        result = await prove(
            CLAIM, pool=pool, runtime=fake,
            config=ProveConfig(model="none", max_turns=100,
                               wall_clock_s=600.0, sandbox_tex=False),
            results_dir=tmp_path / "results", run_id="exit1",
            literature=services,
        )
    finally:
        await pool.close()

    # --- the exit criterion, clause by clause -------------------------
    assert result.outcome == "proved"
    out = result.published_path
    tex = (out / "widgets-and-zeta-functions-commute.tex").read_text()
    assert rf"\cite{{{KEY1}}}" in tex          # cites fetched papers …
    assert rf"\cite{{{KEY2}}}" in tex
    assert r"\bibliography{references}" in tex
    assert validate(services.bib_path) == []   # … with a valid bibliography
    manifest = json.loads((out / "manifest.json").read_text())
    fetched = {p["cite_key"]: p for p in manifest["papers"]}
    assert set(fetched) == {KEY1, KEY2}
    assert all(p["pdf_sha256"] for p in fetched.values())
    # both papers really are in the immutable store
    assert services.store.get("2301.12345v2") is not None
    assert services.store.get("2401.00001v1") is not None
```

Run: `pytest -m tex tests/test_integration_cited_writeup.py -v` (host with TeX Live: `lualatex` + `bibtex`)
Expected: PASS — a real PDF compiled against the staged two-entry fragment; the compile would *fail* on any undefined key, so passing proves the bibliography actually resolved.

- [ ] **Step 2: The live-arXiv test (`network` marker)**

```python
# tests/test_network_arxiv.py
"""Live arXiv (spec test tier `network` — never CI): one real search +
fetch of a small canonical paper (Shor 1995, old-style id — exercises
the / -> - fragment mapping), rate limiting, and idempotent re-fetch."""

import time

import pytest

from hardy.literature.arxiv import ArxivQuery
from hardy.literature.bibliography import Bibliography, validate
from hardy.literature.store import DigestJournal
from hardy.tools.literature_tools import (
    make_literature_registry,
    make_literature_services,
)

pytestmark = pytest.mark.network

ARXIV_ID = "quant-ph/9508027"


async def test_live_search_fetch_idempotence_and_rate_limit(tmp_path):
    services = make_literature_services(
        tmp_path / "papers", tmp_path / "references.bib"
    )
    registry = make_literature_registry(services)

    # live metadata lookup
    hits = services.client.search(ArxivQuery(id_list=[ARXIV_ID], max_results=1))
    assert hits and hits[0].arxiv_id == ARXIV_ID
    version = hits[0].version

    # fetch through the real tool path
    fetched = await registry.get("fetch_paper").call({"arxiv_id": ARXIV_ID})
    assert not fetched.is_error, fetched.content
    id_v = f"{ARXIV_ID}v{version}"
    stored = services.store.get(id_v)
    assert stored is not None
    assert (stored.path / "paper.pdf").stat().st_size > 10_000

    # bibliography: pure-function key carries the folded fragment; file valid
    [key] = Bibliography.load(services.bib_path).keys()
    assert "quant-ph-9508027" in key
    assert validate(services.bib_path) == []

    # idempotent re-fetch: no new journal events, no re-download
    again = await registry.get("fetch_paper").call({"arxiv_id": ARXIV_ID})
    assert not again.is_error
    events = [e["event"] for e in DigestJournal(tmp_path / "papers").read_events()
              if e["id_v"] == id_v]
    assert events == ["pending", "committed"]

    # reading works end to end on real content
    read = await registry.get("read_paper").call(
        {"arxiv_id": ARXIV_ID, "version": version}
    )
    assert not read.is_error and len(read.content) > 0


async def test_live_rate_limit_spaces_uncached_requests(tmp_path):
    services = make_literature_services(
        tmp_path / "papers", tmp_path / "references.bib"
    )
    start = time.monotonic()
    services.client.search(ArxivQuery(text="prime gaps", max_results=1))
    services.client.search(ArxivQuery(text="zeta zeros", max_results=1))
    # two distinct uncached queries: the second waits out the 3 s interval
    assert time.monotonic() - start >= 3.0
```

Run: `pytest -m network tests/test_network_arxiv.py -v` (network host, politely — this is the one live test)
Expected: PASS

- [ ] **Step 3: The exit-criterion script**

```python
#!/usr/bin/env python3
# scripts/prove_cited.py
"""M3 exit criterion: a prove run whose writeup cites fetched papers
with a valid bibliography.

Needs: setup_lean.sh completed, TeX (sandbox images or --no-sandbox-tex
with lualatex+bibtex on PATH), network access to arXiv, and model
credentials for claude-agent-sdk. Never runs in CI (model + network)."""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from hardy.agent.claude_sdk import ClaudeSdkRuntime
from hardy.lean.launch import LEAN_PROJECT, repl_argv, repl_env
from hardy.lean.pool import ReplPool
from hardy.literature.bibliography import validate
from hardy.tools.literature_tools import make_literature_services
from hardy.workflows.persist import slugify
from hardy.workflows.prove import ProveConfig, prove

CLAIM = "there are infinitely many prime numbers"
REPO = Path(__file__).resolve().parent.parent


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--wall-clock-s", type=float, default=2400.0)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--no-sandbox-tex", action="store_true")
    args = parser.parse_args()

    literature = make_literature_services(
        REPO / "papers", REPO / "references.bib"
    )
    pool = ReplPool(size=1, argv=repl_argv(), cwd=LEAN_PROJECT,
                    env=repl_env(), imports="import Mathlib")
    print("warming the pool (Mathlib import)…", flush=True)
    await pool.start()
    try:
        result = await prove(
            CLAIM,
            pool=pool,
            runtime=ClaudeSdkRuntime(),
            config=ProveConfig(
                model=args.model, max_turns=args.max_turns,
                wall_clock_s=args.wall_clock_s,
                sandbox_tex=not args.no_sandbox_tex,
            ),
            results_dir=args.results_dir,
            run_id=uuid.uuid4().hex[:8],
            literature=literature,
        )
    finally:
        await pool.close()

    print(f"outcome: {result.outcome}")
    print(f"formalization: {result.formalization_status}")
    print(f"published: {result.published_path}")
    ok = False
    if result.published_path is not None:
        tex_path = result.published_path / f"{slugify(CLAIM)}.tex"
        manifest_path = result.published_path / "manifest.json"
        if tex_path.exists() and manifest_path.exists():
            tex = tex_path.read_text()
            papers = json.loads(manifest_path.read_text()).get("papers", [])
            bib_problems = validate(REPO / "references.bib")
            print(f"papers fetched/cited: {[p['cite_key'] for p in papers]}")
            print(f"bibliography problems: {bib_problems or 'none'}")
            ok = (
                "\\cite{" in tex                  # the writeup cites …
                and "\\bibliography" in tex       # … through a real bibliography
                and len(papers) > 0               # … papers it fetched
                and bib_problems == []            # … and the .bib is valid
            )
    print("EXIT CRITERION:", "MET" if ok else "NOT MET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 4: Run the full suite + the exit criterion**

```bash
pytest -m "not lean and not tex and not docker and not model and not network"  # CI-equivalent: PASS
python scripts/validate_bib.py                                                 # exit 0
pytest -m tex -v                                                               # TeX host: PASS (incl. the rehearsal)
pytest -m network -v                                                           # network host: PASS (politely)
python scripts/prove_cited.py                                                  # model+network: EXIT CRITERION: MET
```

M3 is **not complete** until `scripts/prove_cited.py` prints `EXIT CRITERION: MET` — a published writeup whose `\cite`s resolve against a compile-checked bibliography built from really-fetched papers, with the paper digests recorded in the manifest and `references.bib` validating clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/prove_cited.py tests/test_integration_cited_writeup.py tests/test_network_arxiv.py
git commit -m "feat: M3 exit criterion — cited writeup end to end, tex rehearsal + live network tier"
```

---

## Self-Review

Checked against the spec after drafting:

1. **Spec coverage.** Version-keyed immutable store with content digests → Tasks 5–6; unversioned-fetch-resolves-latest + no-refetch idempotence → Tasks 3, 10; safe extraction (traversal, symlink/hardlink/device, quotas, isolated temp, atomic admission) → Tasks 4, 6; streaming download caps on arriving bytes → Task 3; interprocess rate limiter with shared timestamp + connection lease → Task 2; query cache with TTL → Task 3; fsynced JSONL event journal, pending/committed ordering, recovery rules, `DIGESTS.json.lock` → Tasks 5–6; digest verification on refetch surviving a fresh clone → Task 6; `BibEntry` fields incl. version note → Task 7; field confinement of network-controlled text (allowlist, escape fallback) → Task 7; atomic **and durable** bib save under `references.bib.lock` → Tasks 1, 7; pure-function cite keys with id fragment + version qualification, DOI fallback only without arXiv identity → Task 7; `validate` + `scripts/validate_bib.py` in the CI unit job → Tasks 7–8; store-is-data/gitignore-except-ledger/committed-`references.bib` → Task 8; subprocess-confined PDF **and** LaTeX-index parsing, output byte quota, derived-data cache, TOC-on-first-call, per-call caps → Task 9; the four tools with the spec's exact backing table (fetch delegates to cite; read never fetches) → Task 10; bibliography compile passes with undefined-citations-as-errors, local + sandboxed, fragment-only staging → Tasks 11–12; `\cite` allowlisted, `render_writeup` bibliography behavior, no-citations rule retired, manifest paper digests → Tasks 12–13; testing strategy's tiers (unit fixtures with crafted tarballs, `tex` cited-compile + undefined-citation failure, one live `network` test with rate limiting and idempotent re-fetch, CI validator) → Tasks 4–14; exit criterion as the final task → Task 14. Out-of-scope list respected (no assume_paper, no semantic retrieval, no citation graph, no non-arXiv, no OCR, `plain` style only).
2. **Spec deviations, all flagged in "Plan assumptions":** explicit bibtex passes instead of the stale "tectonic runs the bib pass" parenthetical (assumption 11); `\cite`-detection in `write_latex` rather than inside `render_writeup` (assumption 3); `download(meta)` instead of `download(arxiv_id_v)` (assumption 12); `fsutil.py`/`extract_worker.py` as focused-file additions (assumption 12); writeup-phase registry rather than a separate literature phase (assumption 6).
3. **Type consistency.** `PaperMeta.id_v` (Task 2) flows through store keys (Task 6), `eprint` dedup (Task 7), and tool ids (Task 10); `ExtractionReport` (Task 4) → `StoredPaper.extraction` (Task 6); `add_or_get(path, meta) -> str` is the one bib writer used by Tasks 10 and 12's fragment source is `Bibliography.fragment(keys) -> str`; `compile_fn(source, staging, bibliography=…)` is consistent across Tasks 11 → 12 → 13 (and M1's fakes are extended in Task 12, the fixture fake in Task 13 already matches); `LiteratureServices.used_papers` (Task 10) is exactly `Manifest.papers`'s element shape (Task 13); `KEY1`/`KEY2` in Tasks 12–14 are the deterministic outputs of Task 7's `mint_key` on Task 3's `sample_meta` fixtures (`version=2` ⇒ `V2` qualifier; `version=1` ⇒ bare).
4. **Placeholder scan.** Every step carries concrete code, exact commands, and expected outcomes. The two places intentionally specified as *edits against M1-planned code* (Task 12 Step 5's handler body, Task 13 Step 5's writeup-phase block) contain the complete replacement code, not descriptions — they are edits only in the sense that surrounding M1 code stays as M1 wrote it, and both are guarded by the plan-assumptions section if M1 drifted.
5. **Guard suites named per task:** M0's `test_compile.py`/`test_template.py` and M1's `test_latex_tools.py`/`test_confine.py`/`test_template_m1.py`/`test_prove.py`/`test_prompts.py` are re-run in the tasks that touch their subjects, unmodified except where explicitly extended (Task 12's compiler-fake signatures).

## Status

- [ ] Not started — plan awaits review gates and PR.
