# M2 — Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducibility-complete M2 evaluation harness and publish a
finalized certified pass@1 baseline for the M1 agent on the pinned 225-item
usable miniF2F `test` split.

**Architecture:** `hardy.eval` loads an exact-pinned portable corpus, runs each
item × attempt in a digest-pinned sandbox, independently revalidates every
kernel-complete proof, and records durable attempt/run manifests. Hard-pass
attempts containing `decide` or `native_decide` remain provisional until an
append-only human adjudication promotes or rejects them; only certified attempts
enter headline metrics. Tracking binds results to immutable code, corpus,
annotation, toolchain, worker, model, and configuration identities.

**Tech Stack:** Python 3.12+, pydantic v2, pytest + pytest-asyncio, `psutil`, and
`portalocker>=3.0` for crash-safe cross-platform OS locking; stdlib
`hashlib`/`json`/`math.comb`/`subprocess`.

**Execution prerequisite:** the M1 implementation plan has fully landed. Tasks
1–2 are M1-independent; later tasks must revalidate the exact M1 seams below
against landed code before execution.

## Global Constraints

- Vendor `minif2f_lean4.jsonl` and `LICENSE` from
  `yangky11/miniF2F-lean4` tag `v4.15.0`, exact commit
  `638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f`; mutable refs are refused.
- The pin must load exactly 229 usable `valid` and 225 usable `test` records.
  Commented error records are listed in `EXCLUSIONS.json`, never silently lost.
- Strip exactly one terminal `:= sorry` from active `formal_statement` values;
  preserve the remaining statement and `header` byte-for-byte.
- `domains.json` covers every usable item. Conservative annotation assigns only
  explicit `mathd_algebra*` and `mathd_numbertheory*` families to those domains;
  all other items begin as `mixed`, avoiding invented source-to-domain guesses.
- Anti-cheat checks every hard condition independently: byte reconstruction,
  lexical `sorry`/`admit`, fail-closed axioms, and live closer tokens.
- Every live `decide` and `native_decide` in source or recorded tactics is a
  flag. A hard-pass with flags is `provisional`, not solved, until adjudicated.
- Adjudications are append-only and bind to the full flag-set digest. The latest
  valid event supersedes earlier events without deleting history.
- Headline pass@1/pass@k and per-solve costs use `certified` attempts only.
  Pending attempts appear only in a separately named provisional upper bound.
- Official baseline: complete attempt matrix, no pending flags, clean Git tree,
  reproducible sandbox worker, exact corpus/annotation pins, and one canonical
  immutable model ID. Unpinned/direct/dirty/partial runs remain exploratory.
- `valid` is smoke/tuning only; the official run uses the complete usable `test`
  split. No tuning occurs after observing `test` outcomes.
- Model-generated Lean executes only in sandboxed workers pinned to one image
  digest. Trusted model-free tests may opt into direct workers.
- Failure is data: timeout, crash, or runtime error produces one terminal failed
  attempt; an interrupted orchestrator leaves a durable incomplete manifest.
- pass@k attempts are independent: same frozen config except recorded attempt
  index, fresh `ProofSession`, no shared context, memory, or unconfigured retry.
- Lean CPU is sampled in flight. Missing final samples use the last observation
  or an elapsed × CPU-cap upper bound marked estimated.
- Shared JSONL journals flush and fsync complete lines while holding an OS lock;
  process death releases the lock. Local manifests use atomic replace.
- Benchmark mode has no formalization, faithfulness phase, writeup, or informal
  completeness grade. PutnamBench/ProofNet, held-out contents, CI model runs,
  automated adjudication, and in-place resume remain out of scope.

## Decision Log (2026-07-23)

1. Exclude suspicious-closer attempts from headline metrics until human review.
2. Persist review decisions in an append-only adjudication journal.
3. Require a canonical pinned model identity for the official baseline.
4. Use `valid` for smoke/tuning and `test` for the official baseline.
5. Replace mutable-HEAD/monolithic-file vendoring with the exact v4.15.0 JSONL
   export after upstream inspection showed completed proofs and an unvendored
   import in `Valid.lean`/`Test.lean`.
6. Flag every `decide`/`native_decide`; reject the load-sensitive wall-clock
   threshold as a definition of “huge”.
7. Use a complete conservative domain manifest instead of calling competition
   sources mathematical domains.
8. Require full attempt completion and complete adjudication before finalizing.
9. Keep interrupted manifests and use crash-safe OS locking.
10. Bundle this spec refresh and plan update in the existing M2 PR by explicit
    user instruction.
11. Treat any missing response-level model revision as unpinned, including a
    partial omission within an otherwise consistent attempt matrix.
12. Bind the canonical exclusion records into the corpus digest.
13. Select the latest adjudication whose digest matches the current flags, so a
    stale later event cannot mask the latest valid decision.
14. Compare `SOURCE` and its four file digests to the reviewed canonical
    miniF2F manifest; eligibility also requires the approved semantic digest.
15. Replace regex model classification with an adapter-owned reviewed allowlist;
    Anthropic documents `claude-sonnet-5` as a pinned snapshot.
16. Preserve header bytes in `split_header` and compare exact import/preamble
    reconstruction without `strip()` normalization.
17. Define completion by equality with the exact expected item-attempt key set,
    rejecting duplicates even when the result count happens to match.
18. Require matching adjudication for every flagged attempt, including hard
    failures, before finalization.
19. Require one valid 64-hex worker image digest, at least one observed launch,
    and exact agreement across all observed launches.
20. Compare the complete Lean, Mathlib, and REPL pin mapping to approved values;
    empty or arbitrary mappings are ineligible.
21. Treat a lone CPU baseline with no successful follow-up as an estimated
    elapsed-times-cap upper bound, never measured zero.
22. Ignore one torn trailing JSONL fragment when reading and truncate it under
    the next exclusive append lock before writing a new durable record.
23. Keep adjudication/runner imports acyclic with `TYPE_CHECKING` and forward
    annotations, plus import-order regression coverage.
24. Require the worker digest to equal a committed approved image lock and
    attest the complete approved Lean/Mathlib/REPL pins inside that image.
25. Migrate the repository's current Lean 4.30 project and Nix image to the
    exact approved Lean 4.15, Mathlib, and REPL revisions before evaluation.
26. Capture clean source Git provenance before creating run output; finalization
    reuses that immutable snapshot rather than inspecting generated artifacts.
27. Require a baseline and follow-up sample for every CPU identity segment; a
    singleton replacement-worker segment forces the conservative estimate.
28. Emit one closer flag per live occurrence with stable location evidence so
    adding or moving a closer changes the full flag-set digest.

## Plan assumptions (re-validate before execution)

The following M1 interfaces are plan-only as of this review. Diff them against
landed M1 code before starting Task 3; drift changes implementation details, not
M2's approved contracts.

1. `RunConfig(model, max_turns, max_tokens_total, wall_clock_s,
   prompt_version, runtime="claude_sdk")`.
2. `TrajectoryEvent(kind, at, text, tool_name, arguments, content, is_error, input_tokens=0, output_tokens=0)` and
   `Trajectory(events, turns, tokens_used, wall_clock_s, final_text, stopped).to_jsonl()`; Task 3 adds optional response model identity.
3. `AgentRuntime.run(task, system_prompt, tools, config) -> Trajectory` plus
   `FakeRuntime` and `ClaudeSdkRuntime(client_factory=None)`.
4. `ReplPool.lease() -> ProofSession`; `ProofSession.check`, `command_in`, and
   worker retirement/accessors added by the CPU task.
5. `FrozenStatement(name, header).splice(body)` and
   `make_prove_registry(session, statement, attempts, wins)` where `wins` contains only
   kernel-complete `(source, env)` pairs.
6. `get_prompt("prove_v1")` with one `{statement}` placeholder.
7. `audit_axioms(session, name, env) -> AuditResult`, fail-closed with the M1
   allowed-axiom set.
8. M1's `model` marker and Claude Agent SDK dependency.

The runner intentionally composes M1's proof-phase primitives rather than
calling the five-phase `prove()` workflow. Benchmark mode is one phase and
publishes evaluation records, not the M1 artifact pair. Import lines live in the
pool base environment because the REPL cannot process `import` in a forked env;
anti-cheat verifies both the pool imports and reconstructed declaration.

## File Structure

```
src/hardy/eval/benchmark.py       — exact-pinned JSONL/custom loaders + digests
src/hardy/eval/anticheat.py       — hard checks, closer flags, flag digest
src/hardy/eval/journal.py         — portalocker-backed append + atomic JSON
src/hardy/eval/adjudication.py    — decisions and effective attempt status
src/hardy/eval/cpu.py             — in-flight Lean CPU sampling
src/hardy/eval/runner.py          — configs, provenance, manifests, orchestration
src/hardy/eval/metrics.py         — certified/provisional metrics
src/hardy/eval/tracking.py        — run index, eligibility, comparison
src/hardy/agent/runtime.py        — response model identity on usage events
src/hardy/agent/claude_sdk.py     — stamp observed model identity
src/hardy/lean/session.py         — public worker access/retirement
scripts/vendor_minif2f.py         — exact-pin vendoring and manifests
scripts/run_eval.py               — run/adjudicate/finalize/compare CLI
benchmarks/minif2f/               — JSONL, LICENSE, SOURCE, EXCLUSIONS, domains
pyproject.toml                    — portalocker dependency
tests/test_benchmark.py
tests/test_runtime_revisions.py
tests/test_anticheat.py
tests/test_adjudication.py
tests/test_cpu.py
tests/test_runner_config.py
tests/test_runner.py
tests/test_metrics.py
tests/test_tracking.py
tests/test_run_eval_cli.py
tests/test_integration_eval.py
eval_results/runs.jsonl
eval_results/adjudications.jsonl
```

---
### Task 1: Exact-corpus loader and integrity contracts

**Files:**
- Create: `src/hardy/eval/__init__.py`
- Create: `src/hardy/eval/benchmark.py`
- Test: `tests/test_benchmark.py`

**Interfaces:**
- Produces `BenchmarkItem`, `load_minif2f`, `load_custom`, `statement_name`,
  `split_header`, `proof_prefix`, and `corpus_digest`.
- `load_minif2f` consumes `SOURCE`, `minif2f_lean4.jsonl`,
  `EXCLUSIONS.json`, and `domains.json`; it fails closed on any digest, count,
  exclusion, domain, duplicate-ID, or placeholder mismatch.

- [ ] **Step 1: Write the failing loader tests**

```python
# tests/test_benchmark.py
import hashlib
import json
from pathlib import Path

import pytest

from hardy.eval.benchmark import (
    CANONICAL_MINIF2F_CORPUS_DIGEST,
    CANONICAL_MINIF2F_SOURCE,
    corpus_digest,
    load_minif2f,
    proof_prefix,
    split_header,
    statement_name,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(root: Path) -> dict:
    records = [
        {"id": "mathd_algebra_1", "split": "valid",
         "formal_statement": "theorem alg (x : ℝ) : x = x := sorry",
         "header": "import Mathlib\n\nopen Real"},
        {"id": "imo_1", "split": "test",
         "formal_statement": "theorem imo_one : True := sorry",
         "header": "import Mathlib\n\nopen Real"},
        {"id": "broken", "split": "test",
         "formal_statement": "-- Error: unavailable\n-- theorem broken : True := sorry",
         "header": "import Mathlib\n\nopen Real"},
    ]
    corpus = root / "minif2f_lean4.jsonl"
    corpus.write_text("".join(json.dumps(r) + "\n" for r in records),
                      encoding="utf-8")
    (root / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    (root / "EXCLUSIONS.json").write_text(json.dumps({
        "records": [{"id": "broken", "split": "test",
                     "reason": "upstream formal_statement is commented"}]
    }), encoding="utf-8")
    (root / "domains.json").write_text(json.dumps({
        "mathd_algebra_1": "algebra", "imo_1": "mixed"
    }), encoding="utf-8")
    files = ["minif2f_lean4.jsonl", "LICENSE", "EXCLUSIONS.json",
             "domains.json"]
    source = {
        "repo": "fixture", "revision": "a" * 40,
        "usable_counts": {"valid": 1, "test": 1},
        "files": {name: f"sha256:{sha(root / name)}" for name in files},
    }
    (root / "SOURCE").write_text(json.dumps(source), encoding="utf-8")
    return source


def test_loads_exact_active_records_and_preserves_bytes(tmp_path):
    expected = write_fixture(tmp_path)
    items = load_minif2f(tmp_path, expected_source=expected)
    assert [(i.id, i.split, i.domain) for i in items] == [
        ("mathd_algebra_1", "valid", "algebra"),
        ("imo_1", "test", "mixed"),
    ]
    assert items[0].statement == "theorem alg (x : ℝ) : x = x"
    assert items[0].header == "import Mathlib\n\nopen Real"
    assert items[0].declaration_name == "alg"
    assert statement_name(items[1].statement) == "imo_one"
    imports, preamble = split_header(items[0].header)
    assert imports == "import Mathlib\n\n"
    assert preamble == "open Real"
    assert imports + preamble == items[0].header
    assert proof_prefix(items[0]) == "open Real\n\ntheorem alg (x : ℝ) : x = x"


def test_default_loader_rejects_self_consistent_noncanonical_manifest(tmp_path):
    write_fixture(tmp_path)
    with pytest.raises(ValueError, match="noncanonical miniF2F SOURCE"):
        load_minif2f(tmp_path)


@pytest.mark.parametrize("mutate", ["digest", "domain", "exclusion", "count"])
def test_metadata_drift_fails_closed(tmp_path, mutate):
    expected = write_fixture(tmp_path)
    if mutate == "digest":
        (tmp_path / "LICENSE").write_text("changed", encoding="utf-8")
    elif mutate == "domain":
        (tmp_path / "domains.json").write_text("{}", encoding="utf-8")
    elif mutate == "exclusion":
        (tmp_path / "EXCLUSIONS.json").write_text(
            '{"records": []}', encoding="utf-8")
    else:
        source = json.loads((tmp_path / "SOURCE").read_text(encoding="utf-8"))
        source["usable_counts"]["test"] = 2
        (tmp_path / "SOURCE").write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError):
        load_minif2f(tmp_path, expected_source=expected)


def test_rejects_non_placeholder_body(tmp_path):
    write_fixture(tmp_path)
    corpus = tmp_path / "minif2f_lean4.jsonl"
    records = [json.loads(line) for line in corpus.read_text().splitlines()]
    records[0]["formal_statement"] = "theorem alg : True := by trivial"
    corpus.write_text("".join(json.dumps(r) + "\n" for r in records))
    source = json.loads((tmp_path / "SOURCE").read_text())
    source["files"]["minif2f_lean4.jsonl"] = f"sha256:{sha(corpus)}"
    (tmp_path / "SOURCE").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="terminal `:= sorry`"):
        load_minif2f(tmp_path, expected_source=source)


def test_corpus_digest_binds_items_revision_and_exclusions(tmp_path):
    expected = write_fixture(tmp_path)
    items = load_minif2f(tmp_path, expected_source=expected)
    exclusions = json.loads(
        (tmp_path / "EXCLUSIONS.json").read_text(encoding="utf-8")
    )["records"]
    assert corpus_digest(items, exclusions) == corpus_digest(
        list(reversed(items)), list(reversed(exclusions))
    )
    changed = items[0].model_copy(update={"source_revision": "b" * 40})
    assert corpus_digest(items, exclusions) != corpus_digest(
        [changed, items[1]], exclusions
    )
    changed_exclusions = [
        {**exclusions[0], "reason": "different reviewed exclusion reason"}
    ]
    assert corpus_digest(items, exclusions) != corpus_digest(
        items, changed_exclusions
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_benchmark.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hardy.eval'`.

- [ ] **Step 3: Implement the loader**

```python
# src/hardy/eval/benchmark.py
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Domain = Literal["algebra", "number_theory", "geometry", "analysis",
                 "combinatorics", "mixed"]
_NAME = re.compile(r"\Atheorem\s+([A-Za-z_][A-Za-z0-9_'.]*)")
_PLACEHOLDER = re.compile(r"\s*:=\s*sorry\s*\Z")
_ALLOWED_DOMAINS = {"algebra", "number_theory", "geometry", "analysis",
                    "combinatorics", "mixed"}
CANONICAL_MINIF2F_CORPUS_DIGEST = (
    "sha256:2b3093c2bbbc90e8186f693a1a102b61c722db241a91e666499c01e24d8976fc"
)
CANONICAL_MINIF2F_SOURCE = {
    "repo": "https://github.com/yangky11/miniF2F-lean4",
    "tag": "v4.15.0",
    "revision": "638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f",
    "usable_counts": {"valid": 229, "test": 225},
    "files": {
        "minif2f_lean4.jsonl": "sha256:29c76cd4f7164e9396edea9f1eb57dbb7c1577429bfdc35599c5c52f5386a5b2",
        "LICENSE": "sha256:63e8210e6bf3e8c032dc0c69b1d1d2e3ab72c14b02cabcc0dada2618bb188b97",
        "EXCLUSIONS.json": "sha256:7f647ff47d6153c89b10d9f35d88a83bf3bf2778f4005041d3240f5b94951b1a",
        "domains.json": "sha256:f78b41ffa678aa1b36afd07c96e295d26867c17f5d95f5b76ac96b67add44a36",
    },
}


class BenchmarkItem(BaseModel):
    id: str
    declaration_name: str
    statement: str
    header: str
    domain: Domain
    split: Literal["valid", "test"]
    source_revision: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement_name(statement: str) -> str:
    match = _NAME.match(statement)
    if match is None:
        raise ValueError(f"not a live theorem declaration: {statement[:80]!r}")
    return match.group(1)


def _bodyless(formal_statement: str) -> str:
    match = _PLACEHOLDER.search(formal_statement)
    if match is None:
        raise ValueError("active statement lacks one terminal `:= sorry`")
    statement = formal_statement[:match.start()].rstrip()
    statement_name(statement)
    return statement


def split_header(header: str) -> tuple[str, str]:
    lines = header.splitlines(keepends=True)
    cut = 0
    while cut < len(lines) and lines[cut].lstrip().startswith("import "):
        cut += 1
    while cut < len(lines) and not lines[cut].strip():
        cut += 1
    imports, preamble = "".join(lines[:cut]), "".join(lines[cut:])
    if any(line.lstrip().startswith("import ")
           for line in preamble.splitlines(keepends=True)):
        raise ValueError("imports must form one leading header block")
    if imports + preamble != header:
        raise AssertionError("header split changed bytes")
    return imports, preamble


def proof_prefix(item: BenchmarkItem) -> str:
    _, preamble = split_header(item.header)
    return f"{preamble}\n\n{item.statement}" if preamble else item.statement


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_files(root: Path, source: dict) -> None:
    for name, expected in source["files"].items():
        actual = f"sha256:{_sha(root / name)}"
        if actual != expected:
            raise ValueError(f"digest mismatch for {name}: {actual} != {expected}")


def load_minif2f(
    path: Path,
    *,
    expected_source: Mapping[str, object] = CANONICAL_MINIF2F_SOURCE,
) -> list[BenchmarkItem]:
    source = _load_json(path / "SOURCE")
    if source != dict(expected_source):
        raise ValueError("noncanonical miniF2F SOURCE manifest")
    _verify_files(path, source)
    exclusions = {(r["split"], r["id"]) for r in
                  _load_json(path / "EXCLUSIONS.json")["records"]}
    domains = _load_json(path / "domains.json")
    items, observed_exclusions = [], set()
    for line in (path / "minif2f_lean4.jsonl").read_text(
            encoding="utf-8").splitlines():
        record = json.loads(line)
        key = (record["split"], record["id"])
        formal = record["formal_statement"]
        if not formal.startswith("theorem "):
            observed_exclusions.add(key)
            continue
        domain = domains.get(record["id"])
        if domain not in _ALLOWED_DOMAINS:
            raise ValueError(f"missing/invalid domain for {record['id']}")
        statement = _bodyless(formal)
        items.append(BenchmarkItem(
            id=record["id"], declaration_name=statement_name(statement),
            statement=statement, header=record["header"], domain=domain,
            split=record["split"], source_revision=source["revision"],
        ))
    if observed_exclusions != exclusions:
        raise ValueError("EXCLUSIONS.json does not exactly match inactive records")
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate benchmark item id")
    counts = {split: sum(i.split == split for i in items)
              for split in ("valid", "test")}
    if counts != source["usable_counts"]:
        raise ValueError(f"usable count drift: {counts}")
    if set(domains) != set(ids):
        raise ValueError("domains.json must cover exactly the usable corpus")
    return items


def load_custom(path: Path) -> list[BenchmarkItem]:
    items = []
    for file in sorted(path.glob("*.json")):
        record = _load_json(file)
        statement = _bodyless(record["formal_statement"])
        items.append(BenchmarkItem(
            id=record["id"], declaration_name=statement_name(statement),
            statement=statement, header=record["header"],
            domain=record.get("domain", "mixed"), split=record["split"],
            source_revision=record["source_revision"],
        ))
    if len({i.id for i in items}) != len(items):
        raise ValueError("duplicate benchmark item id")
    return items


def corpus_digest(items: list[BenchmarkItem],
                  exclusions: list[dict]) -> str:
    payload = {
        "items": [
            i.model_dump()
            for i in sorted(items, key=lambda i: (i.split, i.id))
        ],
        "exclusions": sorted(
            exclusions,
            key=lambda record: (record["split"], record["id"]),
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"
```

Create an empty `src/hardy/eval/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_benchmark.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval tests/test_benchmark.py
git commit -m "feat: add fail-closed benchmark corpus loader"
```

---

### Task 2: Vendor the exact Lean-4.15 miniF2F export

**Files:**
- Create: `scripts/vendor_minif2f.py`
- Create by running it: `benchmarks/minif2f/{minif2f_lean4.jsonl,LICENSE,SOURCE,EXCLUSIONS.json,domains.json}`
- Test: append to `tests/test_benchmark.py`

**Interfaces:**
- The script accepts only a full 40-hex revision, defaults to the reviewed exact
  v4.15.0 commit, verifies the 229/225 active counts, and emits every manifest
  consumed by Task 1.

- [ ] **Step 1: Write the vendoring script**

```python
#!/usr/bin/env python3
# scripts/vendor_minif2f.py
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = "https://github.com/yangky11/miniF2F-lean4"
REVISION = "638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f"
EXPECTED = {"valid": 229, "test": 225}
EXPECTED_FILES = {
    "minif2f_lean4.jsonl": "sha256:29c76cd4f7164e9396edea9f1eb57dbb7c1577429bfdc35599c5c52f5386a5b2",
    "LICENSE": "sha256:63e8210e6bf3e8c032dc0c69b1d1d2e3ab72c14b02cabcc0dada2618bb188b97",
    "EXCLUSIONS.json": "sha256:7f647ff47d6153c89b10d9f35d88a83bf3bf2778f4005041d3240f5b94951b1a",
    "domains.json": "sha256:f78b41ffa678aa1b36afd07c96e295d26867c17f5d95f5b76ac96b67add44a36",
}
DEST = Path(__file__).resolve().parents[1] / "benchmarks" / "minif2f"
_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def domain(item_id: str) -> str:
    if item_id.startswith("mathd_algebra"):
        return "algebra"
    if item_id.startswith("mathd_numbertheory"):
        return "number_theory"
    return "mixed"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args(argv)
    if not _FULL_SHA.fullmatch(args.revision):
        raise SystemExit("--revision must be a full immutable 40-hex commit SHA")
    if args.revision != REVISION:
        raise SystemExit("M2 is frozen to REVISION; update the reviewed constant and manifests together")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        subprocess.run(["git", "clone", "--no-checkout", REPO, str(tmp)],
                       check=True)
        subprocess.run(["git", "-C", str(tmp), "checkout", "--detach",
                        args.revision], check=True)
        resolved = subprocess.run(
            ["git", "-C", str(tmp), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True).stdout.strip()
        if resolved != args.revision:
            raise SystemExit(f"resolved revision drift: {resolved}")
        DEST.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp / "minif2f_lean4.jsonl",
                        DEST / "minif2f_lean4.jsonl")
        shutil.copyfile(tmp / "LICENSE", DEST / "LICENSE")

    records = [json.loads(line) for line in
               (DEST / "minif2f_lean4.jsonl").read_text(
                   encoding="utf-8").splitlines()]
    active = [r for r in records if r["formal_statement"].startswith("theorem ")]
    counts = {split: sum(r["split"] == split for r in active)
              for split in ("valid", "test")}
    if counts != EXPECTED:
        raise SystemExit(f"usable-count drift: {counts} != {EXPECTED}")
    excluded = [{"id": r["id"], "split": r["split"],
                 "reason": "upstream formal_statement is commented"}
                for r in records if r not in active]
    (DEST / "EXCLUSIONS.json").write_text(
        json.dumps({"records": excluded}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (DEST / "domains.json").write_text(
        json.dumps({r["id"]: domain(r["id"]) for r in active},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    names = ["minif2f_lean4.jsonl", "LICENSE", "EXCLUSIONS.json",
             "domains.json"]
    actual_files = {
        name: f"sha256:{digest(DEST / name)}" for name in names
    }
    if actual_files != EXPECTED_FILES:
        raise SystemExit(f"canonical file digest drift: {actual_files}")
    source = {"repo": REPO, "tag": "v4.15.0",
              "revision": args.revision, "usable_counts": counts,
              "files": actual_files}
    (DEST / "SOURCE").write_text(
        json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"vendored miniF2F {args.revision}: valid=229 test=225")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the exact-pin vendor**

Run: `python scripts/vendor_minif2f.py`
Expected: `vendored miniF2F 638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f: valid=229 test=225`.

- [ ] **Step 3: Add and run the production integrity test**

Append:

```python
VENDORED = Path(__file__).resolve().parents[1] / "benchmarks" / "minif2f"


def test_vendored_pin_and_counts():
    items = load_minif2f(VENDORED)
    assert sum(i.split == "valid" for i in items) == 229
    assert sum(i.split == "test" for i in items) == 225
    source = json.loads((VENDORED / "SOURCE").read_text(encoding="utf-8"))
    assert source == CANONICAL_MINIF2F_SOURCE
    exclusions = json.loads(
        (VENDORED / "EXCLUSIONS.json").read_text(encoding="utf-8")
    )["records"]
    assert corpus_digest(items, exclusions) == CANONICAL_MINIF2F_CORPUS_DIGEST
    assert (VENDORED / "LICENSE").read_text(encoding="utf-8").strip()
```

Run: `pytest tests/test_benchmark.py -v`
Expected: all PASS, including the non-skipped production pin test.

- [ ] **Step 4: Commit**

```bash
git add scripts/vendor_minif2f.py benchmarks/minif2f tests/test_benchmark.py
git commit -m "feat: vendor exact Lean-4.15 miniF2F export"
```

---

### Task 2B: Migrate and attest the official Lean 4.15 worker image

**Files:**
- Modify: `lean_project/lean-toolchain`
- Modify: `lean_project/lakefile.toml`
- Regenerate: `lean_project/lake-manifest.json`
- Modify: `scripts/setup_lean.sh`
- Modify: `nix/lean-image.nix`
- Create: `nix/lean-image.lock.json`
- Test: `tests/test_toolchain_image.py`

**Interfaces:**
- Replace the repository's current Lean 4.30 project/image configuration with
  `leanprover/lean4:v4.15.0`, Mathlib commit
  `9837ca9d65d9de6fad1ef4381750ca688774e608`, and REPL commit
  `21966799da3691a0912b5a15193585bd2dd7165d`.
- `nix/lean-image.lock.json` is committed after the image build and records the
  actual `sha256:<64-hex>` image ID plus that exact three-entry pin mapping.
- A model run starts only after an in-container attestation reads Lean, Mathlib,
  and REPL identities and proves they equal the committed lock. A merely
  self-consistent runtime image digest is never approved provenance.

- [ ] **Step 1: Write the failing migration and attestation tests**

Assert that the toolchain file, lake configuration/manifest, setup script, and
Nix image contain no Lean 4.30 references; require the exact approved revisions;
and reject a missing lock, digest mismatch, or in-container pin mismatch.

Run: `pytest tests/test_toolchain_image.py -v`
Expected: FAIL while the repository still targets Lean 4.30.

- [ ] **Step 2: Migrate the project and image**

Update the five toolchain/build files, run `lake update` under Lean 4.15 to
regenerate `lake-manifest.json`, rebuild the REPL and Nix worker image, and run
the existing Lean tests against that image. Do not begin Task 3 while any
repository or generated manifest still resolves Lean 4.30.

- [ ] **Step 3: Lock and attest the built image**

Inspect the built image ID, write it with the approved pin mapping to
`nix/lean-image.lock.json`, then execute the attestation probe inside that exact
digest. The probe output is compared byte-for-byte with the committed mapping.

Run: `pytest tests/test_toolchain_image.py -v`
Expected: all PASS, including an actual in-container attestation when Docker is
available.

- [ ] **Step 4: Commit**

```bash
git add lean_project/lean-toolchain lean_project/lakefile.toml \
  lean_project/lake-manifest.json scripts/setup_lean.sh nix/lean-image.nix \
  nix/lean-image.lock.json tests/test_toolchain_image.py
git commit -m "build: pin and attest the M2 Lean 4.15 worker image"
```

---

### Task 3: Immutable model identity on trajectories

**Files:**
- Modify: `src/hardy/agent/runtime.py`
- Modify: `src/hardy/agent/claude_sdk.py`
- Modify: `tests/fake_runtime.py`
- Test: `tests/test_runtime_revisions.py`

**Interfaces:**
- Adds `TrajectoryEvent.model_revision: str | None` and
  `Trajectory.model_revisions() -> list[str | None]`.
- Adds `model_identity(configured_id, observed_ids, immutable_ids=...) ->
  Literal["pinned", "unpinned", "mismatch"]`. The Claude adapter owns a
  reviewed allowlist rather than inferring immutability from spelling. M2's
  official `claude-sonnet-5` ID is a pinned snapshot under Anthropic's current
  model-ID/versioning contract.

- [ ] **Step 1: Write failing identity tests**

```python
# tests/test_runtime_revisions.py
import pytest

from hardy.agent.claude_sdk import IMMUTABLE_MODEL_IDS
from hardy.agent.runtime import Trajectory, TrajectoryEvent, model_identity


def event(revision=None):
    return TrajectoryEvent(kind="usage", at=0.0, input_tokens=1,
                           output_tokens=1, model_revision=revision)


def trajectory(*events):
    return Trajectory(events=list(events), turns=1, tokens_used=2,
                      wall_clock_s=1.0, final_text="", stopped="complete")


def test_ordered_distinct_response_ids():
    assert trajectory(event("claude-sonnet-5"), event("claude-sonnet-5"),
                      event(None)).model_revisions() == ["claude-sonnet-5", None]


@pytest.mark.parametrize("configured,observed,expected", [
    ("claude-sonnet-5", [], "unpinned"),
    ("claude-sonnet-5", ["claude-sonnet-5"], "pinned"),
    ("claude-sonnet-5", [None], "unpinned"),
    ("claude-sonnet-5", ["claude-sonnet-5", None], "unpinned"),
    ("claude-sonnet-4-5-20250929", [], "unpinned"),
    ("claude-sonnet-4-5-20250929", ["claude-sonnet-4-5-20250929"], "unpinned"),
    ("claude-sonnet-4-5", [], "unpinned"),
    ("latest", [], "unpinned"),
    ("claude-sonnet-5", ["other"], "mismatch"),
    ("claude-sonnet-5", ["claude-sonnet-5", "other"], "mismatch"),
])
def test_model_identity(configured, observed, expected):
    assert model_identity(configured, observed, immutable_ids=IMMUTABLE_MODEL_IDS) == expected
```

Add SDK and fake-runtime tests proving each usage event copies the response's
reported model ID when present and preserves `None` when absent.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_revisions.py -v`
Expected: FAIL because `model_revision` and `model_identity` do not exist.

- [ ] **Step 3: Implement the identity helpers and adapter stamping**

```python
# additions to src/hardy/agent/runtime.py
from collections.abc import Collection
from typing import Literal

# in TrajectoryEvent
model_revision: str | None = None

# on Trajectory
def model_revisions(self) -> list[str | None]:
    return list(dict.fromkeys(
        event.model_revision for event in self.events
        if event.kind == "usage"
    ))


def model_identity(
    configured_id: str,
    observed_ids: list[str | None],
    *,
    immutable_ids: Collection[str],
) -> Literal["pinned", "unpinned", "mismatch"]:
    if not observed_ids or any(observed is None for observed in observed_ids):
        return "unpinned"
    distinct = list(dict.fromkeys(observed_ids))
    if len(distinct) != 1 or distinct[0] != configured_id:
        return "mismatch"
    return "pinned" if configured_id in immutable_ids else "unpinned"
```

In `claude_sdk.py`, define:

```python
# Provider-reviewed pinned IDs only. Source reviewed 2026-07-23:
# https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions
IMMUTABLE_MODEL_IDS = frozenset({"claude-sonnet-5"})
```

When `ClaudeSdkRuntime` appends each usage event, set
`model_revision=getattr(turn, "model_revision", None)`. Extend FakeRuntime's
`{"usage": {"input_tokens": 1, "output_tokens": 1, "model_revision": "claude-sonnet-5"}}` entry identically; this is the complete behavioral change to
those loops.

- [ ] **Step 4: Run the affected runtime suites**

Run: `pytest tests/test_runtime_revisions.py tests/test_runtime.py tests/test_claude_sdk.py tests/test_prove.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/agent tests/fake_runtime.py tests/test_runtime_revisions.py
git commit -m "feat: record and validate immutable model identity"
```

---

### Task 4: Hard anti-cheat checks and provisional closer flags

**Files:**
- Create: `src/hardy/eval/anticheat.py`
- Modify: `tests/fake_repl.py`
- Test: `tests/test_anticheat.py`

**Interfaces:**
- Produces `CheckStatus`, `Flag`, `AntiCheatReport`, `validate`,
  `strip_comments_and_strings`, `contains_token`, and `flag_digest`.
- `AntiCheatReport.passed` is only the conjunction of hard checks. Any flags
  make the later attempt status provisional.

- [ ] **Step 1: Write failing anti-cheat tests**

```python
# tests/test_anticheat.py (essential cases; each is its own test)
# 1. exact reconstructed source + clean audit -> passed, no flags
# 2. original statement only in comment/dead declaration -> statement failure
# 3. live sorry/admit -> failure; comment/string occurrences -> pass
# 4. missing env, garbled audit, sorryAx, Papers.* -> axiom failure
# 5. decide in source -> passed hard checks plus decide flag
# 6. native_decide in source -> passed hard checks plus native_decide flag
# 7. either closer only in run_tactic arguments -> trajectory flag
# 8. nested comments and escaped strings do not produce flags
# 9. changing any flag field changes flag_digest; ordering does not
# 10. any header comment, whitespace, or import-byte change fails reconstruction
# 11. a second live closer emits a second located flag and changes flag_digest

async def test_small_decide_is_provisional_not_silently_certified(session, item):
    report = await run_validate(session, item, "by decide")
    assert report.passed
    assert [(f.closer, f.where) for f in report.flags] == [("decide", "source")]
    assert len(flag_digest(report.flags)) == 64
```

Use the existing M1 `FakeRepl` fixtures for clean/sorried/garbled audit output;
add one `Papers.Example.result` response and assert all checks run even when the
statement check fails.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_anticheat.py -v`
Expected: FAIL because `hardy.eval.anticheat` does not exist.

- [ ] **Step 3: Implement the anti-cheat module**

```python
# src/hardy/eval/anticheat.py
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from hardy.agent.runtime import Trajectory
from hardy.eval.benchmark import BenchmarkItem, split_header, statement_name
from hardy.lean.session import ProofSession
from hardy.workflows.audit import audit_axioms

_IDENT = r"[A-Za-z0-9_'!?]"


class CheckStatus(BaseModel):
    ok: bool
    reason: str | None = None


class Flag(BaseModel):
    closer: Literal["native_decide", "decide"]
    where: Literal["source", "trajectory"]
    detail: str


class AntiCheatReport(BaseModel):
    passed: bool
    statement: CheckStatus
    sorry_free: CheckStatus
    axioms: CheckStatus
    audited_axioms: list[str] = Field(default_factory=list)
    papers_axioms: list[str] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)


def strip_comments_and_strings(source: str) -> str:
    out, i, depth, n = [], 0, 0, len(source)
    while i < n:
        two = source[i:i + 2]
        if depth:
            if two == "/-": depth, i = depth + 1, i + 2
            elif two == "-/": depth, i = depth - 1, i + 2
            else: i += 1
        elif two == "/-": depth, i = 1, i + 2
        elif two == "--":
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source[i] == '"':
            i += 1
            closed = False
            while i < n:
                if source[i] == "\\": i += 2
                elif source[i] == '"': i += 1; closed = True; break
                else: i += 1
            if not closed:
                raise ValueError("unclosed Lean string literal")
        else:
            out.append(source[i]); i += 1
    if depth:
        raise ValueError("unclosed Lean block comment")
    return "".join(out)


def token_offsets(text: str, token: str) -> list[int]:
    pattern = rf"(?<!{_IDENT}){re.escape(token)}(?!{_IDENT})"
    return [match.start() for match in re.finditer(pattern, text)]


def contains_token(text: str, token: str) -> bool:
    return bool(token_offsets(text, token))


def flag_digest(flags: list[Flag]) -> str:
    payload = sorted((f.model_dump() for f in flags),
                     key=lambda f: (f["closer"], f["where"], f["detail"]))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def proof_bodies(trajectory: Trajectory) -> list[str]:
    return [e.arguments["proof"] for e in trajectory.events
            if e.kind == "tool_call" and e.tool_name == "check_proof"
            and e.arguments and "proof" in e.arguments]


def _imports(header: str) -> str:
    return split_header(header)[0]


def rebuild(item: BenchmarkItem, body: str) -> str:
    _, preamble = split_header(item.header)
    prefix = f"{preamble}\n\n{item.statement}" if preamble else item.statement
    return f"{prefix} := {body}"


def _flags(source: str, trajectory: Trajectory) -> list[Flag]:
    flags = []
    stripped = strip_comments_and_strings(source)
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    for closer in ("native_decide", "decide"):
        for offset in token_offsets(stripped, closer):
            flags.append(Flag(
                closer=closer, where="source",
                detail=f"source-sha256:{source_digest}:live-offset:{offset}",
            ))
    for event_index, event in enumerate(trajectory.events):
        if event.kind != "tool_call" or event.tool_name != "run_tactic" or not event.arguments:
            continue
        tactic_source = str(event.arguments.get("tactic", ""))
        tactic = strip_comments_and_strings(tactic_source)
        tactic_digest = hashlib.sha256(tactic_source.encode("utf-8")).hexdigest()
        for closer in ("native_decide", "decide"):
            for offset in token_offsets(tactic, closer):
                flags.append(Flag(
                    closer=closer, where="trajectory",
                    detail=(f"event:{event_index}:tactic-sha256:{tactic_digest}:"
                            f"live-offset:{offset}"),
                ))
    return flags


async def validate(item: BenchmarkItem, submitted_source: str,
                   trajectory: Trajectory, session: ProofSession, *,
                   winning_env: int | None, pool_imports: str) -> AntiCheatReport:
    bodies = proof_bodies(trajectory)
    matched = next((body for body in reversed(bodies)
                    if rebuild(item, body) == submitted_source), None)
    statement = CheckStatus(ok=matched is not None and _imports(item.header) == pool_imports)
    if not statement.ok:
        statement.reason = "checked source/imports do not reconstruct from corpus + trajectory"
    try:
        stripped = strip_comments_and_strings(submitted_source)
        bad = [word for word in ("sorry", "admit") if contains_token(stripped, word)]
        sorry_free = CheckStatus(ok=not bad,
                                 reason=f"live tokens: {bad}" if bad else None)
    except ValueError as exc:
        sorry_free = CheckStatus(ok=False, reason=str(exc))
    audited, papers = [], []
    if winning_env is None:
        axioms = CheckStatus(ok=False, reason="missing winning env")
    else:
        audit = await audit_axioms(session, statement_name(item.statement), winning_env)
        audited = audit.axioms
        papers = [name for name in audited if name.startswith("Papers.")]
        axioms = CheckStatus(ok=audit.passed and not papers,
                             reason=(f"paper axioms: {papers}" if papers else audit.reason))
    flags = _flags(submitted_source, trajectory)
    return AntiCheatReport(
        passed=statement.ok and sorry_free.ok and axioms.ok,
        statement=statement, sorry_free=sorry_free, axioms=axioms,
        audited_axioms=audited, papers_axioms=papers, flags=flags,
    )
```

- [ ] **Step 4: Run anti-cheat and M1 audit suites**

Run: `pytest tests/test_anticheat.py tests/test_audit.py tests/test_session.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/anticheat.py tests/test_anticheat.py tests/fake_repl.py
git commit -m "feat: add fail-closed eval anti-cheat and closer flags"
```

---

### Task 4B: Crash-safe journals and append-only adjudication

**Files:**
- Modify: `pyproject.toml`
- Create: `src/hardy/eval/journal.py`
- Create: `src/hardy/eval/adjudication.py`
- Test: `tests/test_adjudication.py`

**Interfaces:**
- `append_jsonl`, `load_jsonl`, and `atomic_json` are shared by runner/tracking.
  Readers ignore one unterminated trailing record, and the next exclusive append
  truncates that torn tail before writing a new fsynced line.
- `AdjudicationEvent` binds to `(run_id, item_id, attempt_index, flag_digest)`;
  `effective_decisions` uses the latest timestamped event whose flag digest
  matches the attempt's current flag digest; `attempt_status` returns
  `failed|provisional|certified|rejected`.

- [ ] **Step 1: Add the dependency and failing tests**

Add `"portalocker>=3.0"` to project dependencies. Test concurrent appends from
two spawned processes, process exit while holding a lock followed by successful
acquisition, atomic JSON replacement, a crash-during-append file ending in a
partial JSON fragment (prior lines load and the next append repairs the tail),
superseding decisions, stale flag-digest rejection, and these status cases:

```python
assert attempt_status(hard_pass=False, flags=[], decision=None) == "failed"
assert attempt_status(hard_pass=True, flags=[], decision=None) == "certified"
assert attempt_status(hard_pass=True, flags=[flag], decision=None) == "provisional"
assert attempt_status(hard_pass=True, flags=[flag], decision="approve") == "certified"
assert attempt_status(hard_pass=True, flags=[flag], decision="reject") == "rejected"
```

Run: `pytest tests/test_adjudication.py -v`
Expected: FAIL because the modules do not exist.

- [ ] **Step 2: Implement the shared journal**

```python
# src/hardy/eval/journal.py
import json
import os
import tempfile
from pathlib import Path

import portalocker


def _complete_end(data: bytes) -> int:
    return data.rfind(b"\n") + 1


def _load_complete(data: bytes) -> list[dict]:
    complete = data[:_complete_end(data)]
    return [json.loads(line) for line in complete.splitlines() if line.strip()]


def append_jsonl(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with portalocker.Lock(path, mode="a+b", timeout=30,
                           flags=portalocker.LOCK_EX | portalocker.LOCK_NB) as handle:
        handle.seek(0)
        data = handle.read()
        end = _complete_end(data)
        if end != len(data):
            handle.seek(end)
            handle.truncate()
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with portalocker.Lock(path, mode="rb", timeout=30,
                           flags=portalocker.LOCK_SH | portalocker.LOCK_NB) as handle:
        return _load_complete(handle.read())


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)
```

- [ ] **Step 3: Implement adjudication**

```python
# src/hardy/eval/adjudication.py
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from hardy.eval.anticheat import Flag, flag_digest

Decision = Literal["approve", "reject"]
AttemptStatus = Literal["failed", "provisional", "certified", "rejected"]


class AdjudicationEvent(BaseModel):
    run_id: str
    item_id: str
    attempt_index: int
    flag_digest: str
    reviewer: str
    timestamp: datetime
    decision: Decision
    rationale: str = Field(min_length=1)


AttemptKey = tuple[str, str, int]


def effective_decisions(events: list[AdjudicationEvent],
                        current_flag_digests: dict[AttemptKey, str]):
    effective = {}
    for event in sorted(events, key=lambda event: event.timestamp):
        key = (event.run_id, event.item_id, event.attempt_index)
        if current_flag_digests.get(key) == event.flag_digest:
            effective[key] = event
    return effective


def attempt_status(*, hard_pass: bool, flags: list[Flag],
                   decision: Decision | None) -> AttemptStatus:
    if not hard_pass: return "failed"
    if not flags: return "certified"
    if decision is None: return "provisional"
    return "certified" if decision == "approve" else "rejected"


def decision_for(event: AdjudicationEvent | None, flags: list[Flag]) -> Decision | None:
    if event is None: return None
    return event.decision if event.flag_digest == flag_digest(flags) else None
```

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_adjudication.py -v`
Expected: all PASS.

```bash
git add pyproject.toml src/hardy/eval/journal.py src/hardy/eval/adjudication.py tests/test_adjudication.py
git commit -m "feat: add durable journals and closer adjudication"
```

---
### Task 5: Session accessors + Lean CPU measurement (`hardy/eval/cpu.py`)

**Files:**
- Modify: `src/hardy/lean/session.py` (`worker_spec()`, `worker_pid()`, `retire_worker()` — additions only)
- Create: `src/hardy/eval/cpu.py`
- Test: `tests/test_cpu.py`

**Interfaces:**
- Consumes: `ProofSession` internals per assumption 8 (`_worker.spec`, `_worker.repl.pid`, `_worker_died()`), `WorkerSpec` (M0 `src/hardy/lean/pool.py`), `psutil` (M0 dep).
- Produces (Task 7 relies on these exact signatures):
  - `ProofSession.worker_spec() -> WorkerSpec | None`, `ProofSession.worker_pid() -> int | None` — current leased worker's spec/pid, `None` after a death.
  - `ProofSession.retire_worker() -> None` (async) — discard the current worker as unusable (pool replaces it; next call re-acquires). Task 7 calls it after an `item_timeout_s` cancellation leaves the worker mid-command.
  - `CpuUsage(cpu_s: float | None, estimated: bool)` (pydantic).
  - `CpuMonitor(sampler, *, interval_s: float = 1.0)` with `async start()` (baseline sample + background loop) and `async stop(*, elapsed_s: float, cap_cpus: float) -> CpuUsage`. `sampler: Callable[[], Awaitable[tuple[str, float] | None]]` returns `(worker identity, cumulative cpu seconds)` or `None` when unsampleable. Usage sums per-identity deltas only when **every** identity segment has at least two successful samples (worker replacement starts a new segment); teardown keeps the last in-flight sample. No sample or any singleton segment returns `CpuUsage(cpu_s=elapsed_s * cap_cpus, estimated=True)` — the conservative upper bound, so replacement-worker work is never silently measured as zero.
  - `async sample_container(name: str) -> tuple[str, float] | None` — reads the container's cgroup CPU counter via `docker exec` (`cpu.stat usage_usec`, v1 `cpuacct.usage` fallback).
  - `sample_process(pid: int) -> tuple[str, float] | None` — `psutil` user+system CPU-times (direct workers).
  - `container_name(spec: WorkerSpec) -> str | None` — from `cleanup_argv == ["docker", "kill", name]` (how `sandboxed_worker_spec` makes the container addressable).
  - `make_session_sampler(session: ProofSession) -> sampler` — re-resolves the session's *current* worker on every sample (leases replace workers mid-attempt).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cpu.py
import asyncio
import os
import sys

import pytest

from hardy.eval.cpu import (
    CpuMonitor,
    CpuUsage,
    container_name,
    make_session_sampler,
    sample_process,
)
from hardy.lean.pool import ReplPool, WorkerSpec

FAKE = [sys.executable, "tests/fake_repl.py"]


def list_sampler(values: list[tuple[str, float] | None]):
    """Sampler yielding scripted values, repeating the last one forever."""
    state = {"i": 0}

    async def sampler():
        i = min(state["i"], len(values) - 1)
        state["i"] += 1
        return values[i]

    return sampler


async def drain(monitor: CpuMonitor, samples: int):
    # let the background loop take at least `samples` samples
    await asyncio.sleep(0.001 * samples + 0.05)


async def test_monitor_accumulates_single_worker_delta():
    monitor = CpuMonitor(
        list_sampler([("w1", 10.0), ("w1", 11.0), ("w1", 12.5)]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 5)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    assert usage.cpu_s == pytest.approx(2.5)
    assert usage.estimated is False


async def test_monitor_sums_segments_across_worker_replacement():
    monitor = CpuMonitor(
        list_sampler([("w1", 5.0), ("w1", 7.0), ("w2", 100.0),
                      ("w2", 101.5)]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 6)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    # (7.0 - 5.0) + (101.5 - 100.0): counters never conflated across workers
    assert usage.cpu_s == pytest.approx(3.5)
    assert usage.estimated is False


async def test_replacement_singleton_segment_charges_bound():
    monitor = CpuMonitor(
        list_sampler([("w1", 5.0), ("w1", 7.0), ("w2", 100.0), None]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 6)
    usage = await monitor.stop(elapsed_s=30.0, cap_cpus=2.0)
    assert usage == CpuUsage(cpu_s=60.0, estimated=True)


async def test_monitor_keeps_last_inflight_sample_after_worker_death():
    # sampler succeeds twice, then the worker is gone (None forever):
    # teardown must keep the last in-flight sample, not lose the attempt
    monitor = CpuMonitor(
        list_sampler([("w1", 3.0), ("w1", 9.0), None]),
        interval_s=0.001,
    )
    await monitor.start()
    await drain(monitor, 5)
    usage = await monitor.stop(elapsed_s=1.0, cap_cpus=2.0)
    assert usage.cpu_s == pytest.approx(6.0)
    assert usage.estimated is False


async def test_monitor_without_any_sample_charges_conservative_bound():
    monitor = CpuMonitor(list_sampler([None]), interval_s=0.001)
    await monitor.start()
    usage = await monitor.stop(elapsed_s=30.0, cap_cpus=2.0)
    assert usage == CpuUsage(cpu_s=60.0, estimated=True)


async def test_lone_baseline_then_sampling_loss_charges_bound():
    monitor = CpuMonitor(
        list_sampler([("w1", 10.0), None]), interval_s=0.001
    )
    await monitor.start()
    await drain(monitor, 3)
    usage = await monitor.stop(elapsed_s=30.0, cap_cpus=2.0)
    assert usage == CpuUsage(cpu_s=60.0, estimated=True)


async def test_monitor_sampler_exception_is_survived():
    async def exploding():
        raise RuntimeError("docker fell over")

    monitor = CpuMonitor(exploding, interval_s=0.001)
    await monitor.start()
    await asyncio.sleep(0.01)
    usage = await monitor.stop(elapsed_s=2.0, cap_cpus=1.5)
    assert usage == CpuUsage(cpu_s=3.0, estimated=True)


def test_sample_process_reads_own_pid():
    identity, cpu_s = sample_process(os.getpid())
    assert identity == f"pid:{os.getpid()}"
    assert cpu_s >= 0.0


def test_sample_process_dead_pid_returns_none():
    assert sample_process(2 ** 30) is None


def test_container_name_from_spec():
    spec = WorkerSpec(argv=["docker", "run", "img"],
                      cleanup_argv=["docker", "kill", "hardy-repl-abc123"])
    assert container_name(spec) == "hardy-repl-abc123"
    assert container_name(WorkerSpec(argv=["repl"])) is None


async def test_session_accessors_and_sampler_track_replacement():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            assert session.worker_pid() is not None
            assert session.worker_spec().argv == FAKE
            sampler = make_session_sampler(session)
            out = await sampler()
            assert out is not None and out[0].startswith("pid:")

            await session.check("DIE")            # kills the worker
            assert session.worker_pid() is None
            assert session.worker_spec() is None
            assert await sampler() is None        # unsampleable, not a crash

            await session.check("recovered")      # replacement acquired
            out2 = await sampler()
            assert out2 is not None and out2[0] != out[0]
    finally:
        await pool.close()


async def test_retire_worker_discards_and_recovers():
    pool = ReplPool(size=1, argv=FAKE, imports="import Fake")
    await pool.start()
    try:
        async with pool.lease() as session:
            await session.check("theorem t : True := by sorry")
            await session.retire_worker()
            assert session.worker_pid() is None
            assert session.states_lost
            out = await session.check("fine")     # fresh worker, works
            assert out.verdict.complete
        assert (await pool.check_proof("ok")).complete   # pool intact
    finally:
        await pool.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cpu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.cpu'`

- [ ] **Step 3: Add the session accessors**

In `src/hardy/lean/session.py`, add to `ProofSession` (after `known_states`):

```python
    def worker_spec(self):
        """WorkerSpec of the currently leased worker, None after a death.
        (M2 CPU sampling addresses the worker's container/pid through this
        instead of reaching into privates.)"""
        return None if self._worker is None else self._worker.spec

    def worker_pid(self) -> int | None:
        return None if self._worker is None else self._worker.repl.pid

    async def retire_worker(self) -> None:
        """Discard the current worker as unusable — e.g. a cancelled call
        left it mid-command, so requeueing it clean could desync the next
        lease. The pool replaces it; the next session call re-acquires."""
        await self._worker_died()
```

Run: `pytest tests/test_session.py -v` — M1's session suite must stay green, unmodified.

- [ ] **Step 4: Write `cpu.py`**

```python
# src/hardy/eval/cpu.py
"""Measured Lean CPU per attempt (DESIGN.md Component 8).

Wall time is not CPU time: parallel workers and host load make the two
incomparable, so the runner charges measured CPU. Sandboxed workers are
read via their container's cgroup counter (the container name minted by
sandboxed_worker_spec makes it addressable with docker exec); direct
workers via psutil CPU-times. Sampling happens DURING execution: on
timeout/crash/protocol error LeanRepl kills the container before the
failure propagates, so a read-after-command scheme would lose exactly the
most expensive failed attempts — the monitor keeps the last in-flight
sample, and when nothing was ever sampled the attempt is charged the
conservative upper bound (elapsed wall-clock x the sandbox CPU cap),
marked estimated.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import psutil
from pydantic import BaseModel

from hardy.lean.pool import WorkerSpec
from hardy.lean.session import ProofSession

Sampler = Callable[[], Awaitable[tuple[str, float] | None]]

_CGROUP_CMD = (
    "cat /sys/fs/cgroup/cpu.stat 2>/dev/null"
    " || cat /sys/fs/cgroup/cpuacct/cpuacct.usage"
)


class CpuUsage(BaseModel):
    cpu_s: float | None
    estimated: bool


class CpuMonitor:
    def __init__(self, sampler: Sampler, *, interval_s: float = 1.0):
        self._sampler = sampler
        self._interval_s = interval_s
        # identity -> [first cumulative reading, last cumulative reading, count]
        self._segments: dict[str, list[float]] = {}
        self._task: asyncio.Task | None = None

    async def _sample_once(self) -> None:
        try:
            out = await self._sampler()
        except Exception:
            return  # a failed sample must never break the attempt
        if out is None:
            return
        identity, cpu_s = out
        segment = self._segments.get(identity)
        if segment is None:
            self._segments[identity] = [cpu_s, cpu_s, 1]
        else:
            segment[1] = cpu_s
            segment[2] += 1

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await self._sample_once()

    async def start(self) -> None:
        await self._sample_once()  # baseline before any Lean work
        self._task = asyncio.create_task(self._loop())

    async def stop(self, *, elapsed_s: float, cap_cpus: float) -> CpuUsage:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # best-effort final read; the in-flight samples already suffice
        try:
            await asyncio.wait_for(self._sample_once(), timeout=2.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass
        if (not self._segments
                or any(samples < 2 for _, _, samples in self._segments.values())):
            return CpuUsage(cpu_s=elapsed_s * cap_cpus, estimated=True)
        total = sum(last - first for first, last, _ in self._segments.values())
        return CpuUsage(cpu_s=total, estimated=False)


async def sample_container(name: str) -> tuple[str, float] | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", name, "/bin/sh", "-c", _CGROUP_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    text = stdout.decode(errors="replace").strip()
    for line in text.splitlines():
        if line.startswith("usage_usec"):          # cgroup v2
            return name, int(line.split()[1]) / 1_000_000
    if text.isdigit():                             # cgroup v1, nanoseconds
        return name, int(text) / 1_000_000_000
    return None


def sample_process(pid: int) -> tuple[str, float] | None:
    try:
        times = psutil.Process(pid).cpu_times()
    except psutil.Error:
        return None
    return f"pid:{pid}", times.user + times.system


def container_name(spec: WorkerSpec) -> str | None:
    if spec.cleanup_argv and spec.cleanup_argv[:2] == ["docker", "kill"] \
            and len(spec.cleanup_argv) >= 3:
        return spec.cleanup_argv[2]
    return None


def make_session_sampler(session: ProofSession) -> Sampler:
    """Sampler bound to the session, not one worker: leases replace dead
    workers mid-attempt, and each replacement becomes its own segment."""

    async def sampler() -> tuple[str, float] | None:
        spec = session.worker_spec()
        if spec is not None:
            name = container_name(spec)
            if name is not None:
                return await sample_container(name)
        pid = session.worker_pid()
        if pid is not None:
            return sample_process(pid)
        return None

    return sampler
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cpu.py tests/test_session.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/hardy/lean/session.py src/hardy/eval/cpu.py tests/test_cpu.py
git commit -m "feat: in-flight Lean CPU monitor + public session worker accessors"
```

---

### Task 6: Eval config + worker provenance (`hardy/eval/runner.py`, part 1)

**Files:**
- Create: `src/hardy/eval/runner.py` (config/provenance half; Task 7 adds orchestration to the same file)
- Test: `tests/test_runner_config.py`

**Interfaces:**
- Consumes: `RunConfig` (assumption 1), `ReplPool`/`WorkerSpec` (M0), `sandboxed_worker_spec`/`REPL_BIN`/`repl_env` (M0 `src/hardy/lean/launch.py`), `split_header` (Task 1).
- Produces (Tasks 7, 9, 10 rely on these exact signatures):
  - `EvalConfig(run_config: RunConfig, attempts_per_item: int = 1, item_timeout_s: float = 600.0, parallelism: int = 4, benchmark: str = "minif2f", split: str = "valid")` (pydantic, fully serializable).
  - `config_hash(config: EvalConfig) -> str` — SHA-256 of the canonical JSON (sorted keys, compact separators).
  - `WorkerProvenance(kind: Literal["sandboxed", "direct"], image_digest: str | None = None, approved_image_digest: str | None = None, attested_pins: dict[str, str] = {}, binary_hashes: dict[str, str] = {}, reproducible: bool, observed_images: list[str] = [])` (pydantic).
  - `resolve_image_digest(image: str, *, run: Callable[[list[str]], str] = _docker_out) -> str` — `docker image inspect --format {{.Id}}`, must return `sha256:…`.
  - `eval_spec_factory(digest: str, provenance: WorkerProvenance) -> Callable[[], WorkerSpec]` — every minted spec launches **by the digest** and appends it to `provenance.observed_images` (the multiple-digest invariant is observable, not assumed).
  - `sandboxed_eval_pool(*, size: int, imports: str, image: str = "hardy-lean:dev", image_lock: WorkerImageLock | None = None, resolve: Callable[[str], str] | None = None, attest: Callable[[str], dict[str, str]] | None = None) -> tuple[ReplPool, WorkerProvenance]` — loads the committed Task-2B lock, resolves the digest **once at run start**, requires exact lock equality, attests the complete pins inside that digest, and spawns every initial or replacement worker from the digest rather than the mutable tag.
  - `direct_worker_provenance(binaries: dict[str, Path] | None = None) -> WorkerProvenance` — content hashes of the REPL binary + `lean` executable (`reproducible=True` only when both hash); default binaries come from M0's `launch.py`.
  - `shared_imports(items: list[BenchmarkItem]) -> str` — the corpus's single import block; `ValueError` when items disagree (mixed-import corpora cannot share one pool base env).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runner_config.py
import hashlib
from pathlib import Path

import pytest

from hardy.agent.runtime import RunConfig
from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.runner import (
    EvalConfig,
    WorkerImageLock,
    WorkerProvenance,
    config_hash,
    direct_worker_provenance,
    eval_spec_factory,
    resolve_image_digest,
    sandboxed_eval_pool,
    shared_imports,
)

IMAGE_DIGEST = "sha256:" + "a" * 64


def run_config(**kw) -> RunConfig:
    defaults = dict(model="m", max_turns=10, wall_clock_s=60.0,
                    prompt_version="prove_v1")
    defaults.update(kw)
    return RunConfig(**defaults)


def eval_config(**kw) -> EvalConfig:
    defaults = dict(run_config=run_config())
    defaults.update(kw)
    return EvalConfig(**defaults)


def test_eval_config_defaults_and_serializability():
    cfg = eval_config()
    assert cfg.attempts_per_item == 1
    assert cfg.parallelism == 4
    assert cfg.benchmark == "minif2f" and cfg.split == "valid"
    assert EvalConfig.model_validate_json(cfg.model_dump_json()) == cfg


def test_config_hash_stable_and_sensitive():
    a, b = eval_config(), eval_config()
    assert config_hash(a) == config_hash(b)
    assert len(config_hash(a)) == 64
    assert config_hash(eval_config(attempts_per_item=2)) != config_hash(a)
    assert config_hash(
        eval_config(run_config=run_config(model="other"))
    ) != config_hash(a)


def test_resolve_image_digest_parses_and_validates():
    calls = []

    def fake_run(argv):
        calls.append(argv)
        return f"{IMAGE_DIGEST}\n"

    digest = resolve_image_digest("hardy-lean:dev", run=fake_run)
    assert digest == IMAGE_DIGEST
    assert calls == [["docker", "image", "inspect", "--format", "{{.Id}}",
                      "hardy-lean:dev"]]
    with pytest.raises(RuntimeError, match="unexpected image id"):
        resolve_image_digest("x", run=lambda argv: "not-a-digest\n")


def test_eval_spec_factory_launches_by_digest_and_records_observations():
    provenance = WorkerProvenance(kind="sandboxed",
                                  image_digest=IMAGE_DIGEST, reproducible=True)
    factory = eval_spec_factory(IMAGE_DIGEST, provenance)
    spec1, spec2 = factory(), factory()
    assert IMAGE_DIGEST in spec1.argv          # digest, never the tag
    assert not any("hardy-lean:dev" in part for part in spec1.argv)
    assert spec1.cleanup_argv[:2] == ["docker", "kill"]
    assert spec1.cleanup_argv[2] != spec2.cleanup_argv[2]   # unique names
    assert provenance.observed_images == [IMAGE_DIGEST, IMAGE_DIGEST]


def test_sandboxed_eval_pool_resolves_once():
    resolutions = []

    def resolve(image):
        resolutions.append(image)
        return IMAGE_DIGEST

    lock = WorkerImageLock(image_digest=IMAGE_DIGEST, pins={"lean": "pinned"})
    pool, provenance = sandboxed_eval_pool(
        size=2, imports="import Mathlib", image_lock=lock, resolve=resolve,
        attest=lambda digest: {"lean": "pinned"},
    )
    assert resolutions == ["hardy-lean:dev"]   # once at run start
    assert provenance == WorkerProvenance(
        kind="sandboxed", image_digest=IMAGE_DIGEST,
        approved_image_digest=IMAGE_DIGEST,
        attested_pins={"lean": "pinned"}, reproducible=True,
        observed_images=[],
    )


def test_sandboxed_eval_pool_rejects_digest_or_attestation_mismatch():
    lock = WorkerImageLock(image_digest=IMAGE_DIGEST, pins={"lean": "pinned"})
    with pytest.raises(RuntimeError, match="approved lock"):
        sandboxed_eval_pool(
            size=1, imports="import Mathlib", image_lock=lock,
            resolve=lambda image: "sha256:" + "b" * 64,
            attest=lambda digest: {"lean": "pinned"},
        )
    with pytest.raises(RuntimeError, match="attestation"):
        sandboxed_eval_pool(
            size=1, imports="import Mathlib", image_lock=lock,
            resolve=lambda image: IMAGE_DIGEST,
            attest=lambda digest: {"lean": "wrong"},
        )


def test_direct_worker_provenance_hashes_binaries(tmp_path):
    repl = tmp_path / "repl"
    lean = tmp_path / "lean"
    repl.write_bytes(b"repl-bytes")
    lean.write_bytes(b"lean-bytes")
    provenance = direct_worker_provenance({"repl": repl, "lean": lean})
    assert provenance.kind == "direct"
    assert provenance.reproducible is True
    assert provenance.binary_hashes["repl"] == \
        "sha256:" + hashlib.sha256(b"repl-bytes").hexdigest()


def test_direct_worker_provenance_missing_binary_not_reproducible(tmp_path):
    provenance = direct_worker_provenance(
        {"repl": tmp_path / "missing", "lean": tmp_path / "also-missing"}
    )
    assert provenance.reproducible is False
    assert provenance.binary_hashes == {}


def item_with(header: str, name: str) -> BenchmarkItem:
    return BenchmarkItem(
        id=name, declaration_name=name, statement=f"theorem {name} : True",
        header=header, domain="mixed", split="valid", source_revision="f" * 40,
    )


def test_shared_imports_uniform_and_mixed():
    uniform = [item_with("import Mathlib\nimport Aesop\n\nopen Nat", "a"),
               item_with("import Mathlib\nimport Aesop\n\nopen Real", "b")]
    assert shared_imports(uniform) == "import Mathlib\nimport Aesop\n\n"
    mixed = uniform + [item_with("import Std", "c")]
    with pytest.raises(ValueError, match="share one import block"):
        shared_imports(mixed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hardy.eval.runner'`

- [ ] **Step 3: Write the implementation (part 1 of `runner.py`)**

```python
# src/hardy/eval/runner.py
"""Eval orchestration: items x attempts -> verified results (Component 8).

Benchmark mode is a splice of M1's prove-phase primitives, not a call to
prove(): the statement is given verbatim (formalizing it would violate
anti-cheat), faithfulness has nothing to review, and pure benchmark mode
is exempt from the output contract (no writeups). Model-generated
attempts run ONLY on sandboxed workers — a direct worker executes model
Lean as an ordinary host process, where elaborator-time IO (#eval,
spawned children) can touch the repository, credentials, and network;
hashing the binary measures it, it doesn't contain it. The worker image
is resolved to one immutable digest at run start and every worker —
including mid-run replacements — launches by that digest.
"""

import asyncio
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from hardy.agent.runtime import AgentRuntime, RunConfig, Trajectory
from hardy.eval.anticheat import AntiCheatReport, validate
from hardy.eval.benchmark import (
    BenchmarkItem,
    proof_prefix,
    split_header,
    statement_name,
)
from hardy.eval.cpu import CpuMonitor, CpuUsage, make_session_sampler
from hardy.lean.launch import REPL_BIN, repl_env, sandboxed_worker_spec
from hardy.lean.pool import ReplPool, WorkerSpec
from hardy.prompts import get_prompt
from hardy.tools.lean_tools import make_prove_registry
from hardy.tools.statement import FrozenStatement

EVAL_SYSTEM_PROMPT = (
    "You are proving theorems from a fixed benchmark. The statement is "
    "given verbatim and cannot be changed; submit only proof bodies via "
    "check_proof."
)


class EvalConfig(BaseModel):
    run_config: RunConfig
    attempts_per_item: int = 1
    item_timeout_s: float = 600.0
    parallelism: int = 4
    benchmark: str = "minif2f"
    split: str = "valid"


def config_hash(config: EvalConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkerImageLock(BaseModel):
    image_digest: str
    pins: dict[str, str]


class WorkerProvenance(BaseModel):
    kind: Literal["sandboxed", "direct"]
    image_digest: str | None = None
    approved_image_digest: str | None = None
    attested_pins: dict[str, str] = Field(default_factory=dict)
    binary_hashes: dict[str, str] = Field(default_factory=dict)
    reproducible: bool
    # every image reference actually used to mint a worker spec — the
    # multiple-digest invariant is checked against observations, not trust
    observed_images: list[str] = Field(default_factory=list)


def _docker_out(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_image_digest(
    image: str, *, run: Callable[[list[str]], str] = _docker_out
) -> str:
    digest = run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image]
    ).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise RuntimeError(f"unexpected image id for {image!r}: {digest!r}")
    return digest


def eval_spec_factory(
    digest: str, provenance: WorkerProvenance
) -> Callable[[], WorkerSpec]:
    def factory() -> WorkerSpec:
        provenance.observed_images.append(digest)
        return sandboxed_worker_spec(image=digest)

    return factory


def load_worker_image_lock(
    path: Path = Path("nix/lean-image.lock.json"),
) -> WorkerImageLock:
    return WorkerImageLock.model_validate_json(path.read_text(encoding="utf-8"))


def attest_worker_pins(digest: str) -> dict[str, str]:
    raw = _docker_out([
        "docker", "run", "--rm", digest, "/opt/hardy/attest-toolchain",
    ])
    return dict(json.loads(raw))


def sandboxed_eval_pool(
    *,
    size: int,
    imports: str,
    image: str = "hardy-lean:dev",
    image_lock: WorkerImageLock | None = None,
    resolve: Callable[[str], str] | None = None,
    attest: Callable[[str], dict[str, str]] | None = None,
) -> tuple[ReplPool, WorkerProvenance]:
    lock = image_lock or load_worker_image_lock()
    digest = (resolve or resolve_image_digest)(image)
    if digest != lock.image_digest:
        raise RuntimeError("resolved worker image does not match approved lock")
    attested_pins = (attest or attest_worker_pins)(digest)
    if attested_pins != lock.pins:
        raise RuntimeError("worker toolchain attestation does not match approved lock")
    provenance = WorkerProvenance(
        kind="sandboxed", image_digest=digest,
        approved_image_digest=lock.image_digest,
        attested_pins=attested_pins, reproducible=True,
    )
    pool = ReplPool(size=size, spec_factory=eval_spec_factory(digest, provenance),
                    imports=imports)
    return pool, provenance


def direct_worker_provenance(
    binaries: dict[str, Path] | None = None
) -> WorkerProvenance:
    """Byte-level identity for direct workers (no image to pin): content
    hashes of the REPL binary and the Lean executable. Missing either ->
    non-reproducible, and tracking comparisons segregate the run."""
    if binaries is None:
        binaries = {"repl": REPL_BIN}
        sysroot = repl_env().get("LEAN_SYSROOT", "")
        if sysroot:
            binaries["lean"] = Path(sysroot) / "bin" / "lean"
    hashes: dict[str, str] = {}
    for label, path in binaries.items():
        path = Path(path)
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[label] = f"sha256:{digest}"
    return WorkerProvenance(
        kind="direct", binary_hashes=hashes,
        reproducible={"repl", "lean"} <= set(hashes),
    )


def shared_imports(items: list[BenchmarkItem]) -> str:
    blocks = {split_header(item.header)[0] for item in items}
    if len(blocks) != 1:
        raise ValueError(
            f"eval items must share one import block; found {sorted(blocks)!r}"
        )
    return blocks.pop()
```

(The imports of `anticheat`/`cpu`/`prompts`/`lean_tools`/`statement`/`Trajectory` are used by Task 7's half of this file; keeping them in this step avoids an import-shuffle diff.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner_config.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/hardy/eval/runner.py tests/test_runner_config.py
git commit -m "feat: eval config hashing + digest-pinned worker provenance"
```

---

### Task 7: Durable eval orchestration and terminal attempt statuses

**Files:**
- Modify: `src/hardy/eval/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces `EvalResult`, `EvalRun`, `run_eval`, `write_attempt`, and
  `write_run_manifest`.
- `EvalResult.status` is initially `failed`, `provisional`, or `certified`;
  `rejected` appears only after adjudication.
- A manifest is `complete` iff it contains exactly one terminal result for every
  configured item × attempt.

- [ ] **Step 1: Write failing orchestration tests**

Cover these exact cases with `FakeRuntime` + `fake_repl`:

```python
async def test_clean_hard_pass_is_certified(eval_fixture):
    run = await eval_fixture.run(body="by trivial")
    assert run.results[0].status == "certified"

async def test_decide_hard_pass_is_provisional(eval_fixture):
    run = await eval_fixture.run(body="by decide")
    assert run.results[0].status == "provisional"
    assert not run.finalized

async def test_timeout_and_runtime_error_are_terminal_failed_results(eval_fixture):
    run = await eval_fixture.run_failure_matrix(
        failure_kinds=["timeout", "error:RuntimeError"]
    )
    assert len(run.results) == run.items_expected * run.attempts_per_item
    assert {r.failure_kind for r in run.results if r.status == "failed"} >= {
        "timeout", "error:RuntimeError"
    }

async def test_interruption_keeps_incomplete_manifest(eval_fixture, tmp_path):
    with pytest.raises(asyncio.CancelledError):
        await eval_fixture.cancel_after_first_result(out_dir=tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert not manifest["complete"]
    assert 0 < len(manifest["results"]) < (
        manifest["items_expected"] * manifest["attempts_per_item"]
    )
```

Also retain the existing tests for sandbox refusal, import mismatch, fresh session
per attempt, configured concurrency, attempt/trajectory streaming, in-flight CPU
accounting, mixed image invalidation, and response-model mismatch. Add a result
matrix with one duplicated `(item_id, attempt_index)` and one missing expected key;
assert that equal result count does not mark the manifest complete.

Run: `pytest tests/test_runner.py -v`
Expected: FAIL on missing `status`, manifest, and finalization fields.

- [ ] **Step 2: Add the result/run models and status helper**

```python
# additions/replacements in src/hardy/eval/runner.py
from typing import Literal
from pydantic import BaseModel, Field

from hardy.agent.claude_sdk import IMMUTABLE_MODEL_IDS
from hardy.agent.runtime import model_identity
from hardy.eval.adjudication import attempt_status
from hardy.eval.anticheat import AntiCheatReport
from hardy.eval.journal import atomic_json


class EvalResult(BaseModel):
    item_id: str
    attempt_index: int
    domain: str
    status: Literal["failed", "provisional", "certified", "rejected"]
    kernel_complete: bool
    failure_kind: str | None = None
    anticheat: AntiCheatReport | None = None
    tokens: int = 0
    lean_cpu_s: float | None = None
    lean_cpu_estimated: bool = False
    lean_wall_s: float = 0.0
    wall_clock_s: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    trajectory_path: str | None = None
    checked_source: str | None = None
    model_revisions: list[str | None] = Field(default_factory=list)


class EvalRun(BaseModel):
    run_id: str
    results: list[EvalResult]
    items_expected: int
    attempts_per_item: int
    makespan_s: float
    pool_imports: str
    model_identity: Literal["pinned", "unpinned", "mismatch"]
    model_revisions: list[str | None]
    complete: bool
    pending_adjudications: int = 0
    finalized: bool = False
    invalidated: str | None = None

    @property
    def attempts_expected(self) -> int:
        return self.items_expected * self.attempts_per_item


def initial_status(report: AntiCheatReport | None) -> str:
    return attempt_status(
        hard_pass=bool(report and report.passed),
        flags=[] if report is None else report.flags,
        decision=None,
    )


def write_run_manifest(out_dir: Path, run: EvalRun) -> None:
    atomic_json(out_dir / "manifest.json", run.model_dump(mode="json"))
```

- [ ] **Step 3: Update `_run_attempt` and `run_eval`**

Keep Task 6's digest-pinned pool/config helpers. Apply these exact orchestration
rules in the existing implementation:

```python
# after validate(), while the winning worker/env is still leased
status = initial_status(report)
result = EvalResult(
    item_id=item.id, attempt_index=attempt_index, domain=item.domain,
    status=status, kernel_complete=kernel_complete, anticheat=report,
    tokens=trajectory.tokens_used, checked_source=checked_source,
    model_revisions=trajectory.model_revisions(),
    # populate timing/cpu/paths from the existing Task-7 implementation
)
```

At run start, atomically write an `EvalRun` with `results=[]`,
`complete=False`, and `invalidated=None`. After each `write_attempt`, append the
result in memory and atomically rewrite the manifest. In `finally`, compute:

```python
expected_keys = {
    (item.id, attempt_index)
    for item in items
    for attempt_index in range(config.attempts_per_item)
}
result_keys = [(result.item_id, result.attempt_index) for result in results]
complete = (
    len(result_keys) == len(set(result_keys))
    and set(result_keys) == expected_keys
)
observed = list(dict.fromkeys(
    revision for result in results for revision in result.model_revisions
))
identity = model_identity(
    config.run_config.model, observed, immutable_ids=IMMUTABLE_MODEL_IDS
)
invalidated = None
if len(result_keys) != len(set(result_keys)):
    invalidated = "duplicate item-attempt keys"
elif len(set(provenance.observed_images)) > 1:
    invalidated = "multiple worker image digests"
elif identity == "mismatch":
    invalidated = "response model identity disagrees with configured model"
run = EvalRun(
    run_id=run_id, results=sorted(results, key=lambda r: (r.item_id, r.attempt_index)),
    items_expected=len(items), attempts_per_item=config.attempts_per_item,
    makespan_s=clock() - started, pool_imports=pool_imports,
    model_identity=identity, model_revisions=observed,
    complete=complete,
    pending_adjudications=sum(
        bool(result.anticheat and result.anticheat.flags) for result in results
    ),
    invalidated=invalidated,
)
write_run_manifest(out_dir, run)
```

Individual exceptions become failed results. Only cancellation/process death may
leave the matrix incomplete; do not synthesize samples after an interruption.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_runner.py tests/test_runner_config.py tests/test_adjudication.py -v`
Expected: all PASS.

```bash
git add src/hardy/eval/runner.py tests/test_runner.py
git commit -m "feat: durable eval runs with certified and provisional statuses"
```

---

### Task 8: Apply adjudications and compute certified metrics

**Files:**
- Modify: `src/hardy/eval/adjudication.py`
- Create: `src/hardy/eval/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- `apply_adjudications(run, events) -> EvalRun` validates flag digests and returns
  a copy with effective statuses and `finalized` set only when complete with no
  provisional attempts.
- `MetricsReport` contains certified pass rates and a separately named
  provisional upper bound.

- [ ] **Step 1: Write failing metric/finalization tests**

Use a two-item, two-attempt table containing clean certified, pending closer,
approved closer, rejected closer, and failed attempts. Assert:

```python
assert pending_report.pass_at_1 == 0.25          # certified attempts only
assert pending_report.provisional_pass_at_1 == 0.50
assert not pending_report.finalized
assert finalized_report.finalized
assert finalized_report.pending_attempts == 0
assert a flagged hard failure without a matching decision prevents finalization
assert rejected attempts never increase pass_at_k
assert cost denominators use unique certified solved items
assert zero certified solves yields None per-solve costs
assert makespan and utilization_attempt_s remain distinct
```

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL because the finalization/metrics functions do not exist.

- [ ] **Step 2: Implement adjudication application**

```python
# addition to src/hardy/eval/adjudication.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hardy.eval.runner import EvalRun


def apply_adjudications(run: "EvalRun",
                        events: list[AdjudicationEvent]) -> "EvalRun":
    current_flag_digests = {}
    for result in run.results:
        flags = [] if result.anticheat is None else result.anticheat.flags
        current_flag_digests[
            (run.run_id, result.item_id, result.attempt_index)
        ] = flag_digest(flags)
    effective = effective_decisions(events, current_flag_digests)
    updated = []
    pending = 0
    for result in run.results:
        report = result.anticheat
        flags = [] if report is None else report.flags
        event = effective.get((run.run_id, result.item_id, result.attempt_index))
        decision = decision_for(event, flags)
        if flags and decision is None:
            pending += 1
        updated.append(result.model_copy(update={
            "status": attempt_status(
                hard_pass=bool(report and report.passed),
                flags=flags, decision=decision,
            )
        }))
    finalized = run.complete and not run.invalidated and pending == 0
    return run.model_copy(update={
        "results": updated,
        "pending_adjudications": pending,
        "finalized": finalized,
    })
```

The shown `TYPE_CHECKING` guard and forward annotation avoid importing
`runner.py` at runtime; keep a regression test that imports both modules in
both orders.

- [ ] **Step 3: Implement metrics**

```python
# src/hardy/eval/metrics.py
from collections import defaultdict
from math import comb
from pydantic import BaseModel

from hardy.eval.benchmark import BenchmarkItem
from hardy.eval.runner import EvalResult, EvalRun


class MetricsReport(BaseModel):
    items_expected: int
    items_evaluated: int
    k: int
    pass_at_1: float
    pass_at_k: float
    provisional_pass_at_1: float
    provisional_pass_at_k: float
    unique_certified_solved: int
    pending_attempts: int
    finalized: bool
    zero_solves: bool
    tokens_total: int
    tokens_per_solve: float | None
    lean_cpu_s_total: float
    lean_cpu_per_solve: float | None
    makespan_s: float
    makespan_per_solve: float | None
    utilization_attempt_s: float
    per_domain: dict[str, dict[str, float | int]]
    failure_kinds: dict[str, int]


def pass_at_k(n: int, c: int, k: int) -> float:
    if k < 1 or k > n or c < 0 or c > n:
        raise ValueError("invalid pass@k inputs")
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def _rate(groups, k, accepted):
    values = []
    for attempts in groups.values():
        c = sum(result.status in accepted for result in attempts)
        values.append(pass_at_k(len(attempts), c, k))
    return sum(values) / len(values) if values else 0.0


def compute_metrics(run: EvalRun, items: list[BenchmarkItem], *, k: int) -> MetricsReport:
    groups = defaultdict(list)
    for result in run.results: groups[result.item_id].append(result)
    certified = {"certified"}
    upper = {"certified", "provisional"}
    solved_ids = {item_id for item_id, attempts in groups.items()
                  if any(r.status == "certified" for r in attempts)}
    tokens = sum(r.tokens for r in run.results)
    cpu = sum(r.lean_cpu_s or 0.0 for r in run.results)
    utilization = sum(r.wall_clock_s for r in run.results)
    denom = len(solved_ids)
    domains = {}
    by_domain = defaultdict(list)
    item_domain = {item.id: item.domain for item in items}
    for item_id, attempts in groups.items(): by_domain[item_domain[item_id]].extend(attempts)
    for domain, attempts in by_domain.items():
        domain_groups = defaultdict(list)
        for result in attempts: domain_groups[result.item_id].append(result)
        domains[domain] = {"items": len(domain_groups),
                           "pass_at_1": _rate(domain_groups, 1, certified),
                           f"pass_at_{k}": _rate(domain_groups, k, certified)}
    return MetricsReport(
        items_expected=run.items_expected, items_evaluated=len(groups), k=k,
        pass_at_1=_rate(groups, 1, certified), pass_at_k=_rate(groups, k, certified),
        provisional_pass_at_1=_rate(groups, 1, upper),
        provisional_pass_at_k=_rate(groups, k, upper),
        unique_certified_solved=denom,
        pending_attempts=run.pending_adjudications,
        finalized=run.finalized, zero_solves=denom == 0,
        tokens_total=tokens, tokens_per_solve=tokens / denom if denom else None,
        lean_cpu_s_total=cpu, lean_cpu_per_solve=cpu / denom if denom else None,
        makespan_s=run.makespan_s,
        makespan_per_solve=run.makespan_s / denom if denom else None,
        utilization_attempt_s=utilization, per_domain=domains,
        failure_kinds=dict(sorted((kind, sum(r.failure_kind == kind for r in run.results))
                                  for kind in {r.failure_kind for r in run.results if r.failure_kind})),
    )
```

`render_metrics` labels the first pair `certified pass@…`, the second pair
`provisional upper bound`, prints pending count/finalized state, and never calls
pending attempts solves.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_metrics.py tests/test_adjudication.py -v`
Expected: all PASS.

```bash
git add src/hardy/eval/adjudication.py src/hardy/eval/metrics.py tests/test_metrics.py
git commit -m "feat: certified metrics with adjudicated provisional results"
```

---

### Task 9: Provenance, eligibility, final run index, and comparisons

**Files:**
- Create: `src/hardy/eval/tracking.py`
- Test: `tests/test_tracking.py`

**Interfaces:**
- The `run` CLI captures `GitProvenance` before it creates `eval_results` or any
  run directory, passes that snapshot into `run_eval`, and stores it as
  `EvalRun.source_git`.
- `RunRecord` embeds full config/provenance/finalized metrics and
  `baseline_eligible` plus exact refusal reasons; its `git` field is copied from
  `EvalRun.source_git`, never recomputed after output exists.
- `finalize_record` refuses incomplete, pending, dirty, direct/nonreproducible,
  unpinned/mismatched, invalidated, or corpus/toolchain-unidentified runs.
- `append_run` uses Task 4B's journal; `compare_runs` refuses corpus/domain
  mismatch and non-finalized/invalid runs.

- [ ] **Step 1: Write failing provenance/eligibility tests**

Retain tests for Git SHA + dirty diff/untracked digests, toolchain pins, canonical
config/corpus digests, append/load round trip, and concurrent appends. Add a
sequence proving a clean pre-run snapshot remains eligible after the CLI creates
tracked or untracked run artifacts, while a pre-run dirty snapshot remains
ineligible. Finalization must not call `collect_git_provenance` again. Add a
parameterized test that flips each official gate independently:

```python
@pytest.mark.parametrize("field,reason", [
    ("complete", "incomplete attempt matrix"),
    ("finalized", "pending adjudications"),
    ("dirty", "dirty Git tree"),
    ("worker", "worker image provenance is not pinned"),
    ("worker_digest", "worker image provenance is not pinned"),
    ("worker_observations", "worker image provenance is not pinned"),
    ("worker_approval", "worker image provenance is not pinned"),
    ("worker_attestation", "worker image provenance is not pinned"),
    ("model_identity", "model identity is not pinned"),
    ("invalidated", "run invalidated"),
    ("split", "official baseline requires test split"),
    ("item_count", "official baseline requires 225 items"),
    ("model_id", "official baseline requires claude-sonnet-5"),
    ("corpus", "corpus identity does not match approved M2 corpus"),
    ("pins", "toolchain pins do not match approved M2 toolchain"),
])
def test_each_gate_blocks_baseline(field, reason, eligible_record):
    record = eligible_record.with_gate_disabled(field)
    assert reason in record.eligibility_reasons
    assert not record.baseline_eligible
```

Add comparisons refusing different corpus/domain digests and accepting a
finalized run compared with itself without warnings.

Run: `pytest tests/test_tracking.py -v`
Expected: FAIL because tracking does not exist.

- [ ] **Step 2: Implement the final record contract**

```python
# core models/helpers in src/hardy/eval/tracking.py
APPROVED_TOOLCHAIN_PINS = {
    "lean_toolchain": "leanprover/lean4:v4.15.0",
    "mathlib_revision": "9837ca9d65d9de6fad1ef4381750ca688774e608",
    "repl_revision": "21966799da3691a0912b5a15193585bd2dd7165d",
}
APPROVED_CORPUS_DIGEST = (
    "sha256:2b3093c2bbbc90e8186f693a1a102b61c722db241a91e666499c01e24d8976fc"
)
APPROVED_DOMAIN_DIGEST = (
    "sha256:f78b41ffa678aa1b36afd07c96e295d26867c17f5d95f5b76ac96b67add44a36"
)


def _sha256_digest(value: str | None) -> bool:
    if value is None or not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(char in "0123456789abcdef" for char in value[7:])


def _pinned_worker(
    worker: WorkerProvenance, approved_worker: WorkerImageLock,
) -> bool:
    return (
        worker.kind == "sandboxed"
        and worker.reproducible
        and _sha256_digest(worker.image_digest)
        and approved_worker.pins == APPROVED_TOOLCHAIN_PINS
        and worker.image_digest == approved_worker.image_digest
        and worker.approved_image_digest == approved_worker.image_digest
        and worker.attested_pins == APPROVED_TOOLCHAIN_PINS
        and bool(worker.observed_images)
        and all(image == worker.image_digest for image in worker.observed_images)
    )


class RunRecord(BaseModel):
    run_id: str
    timestamp: str
    config_hash: str
    config: EvalConfig
    git: GitProvenance
    pins: dict[str, str]
    worker: WorkerProvenance
    model_id: str
    model_identity: Literal["pinned", "unpinned", "mismatch"]
    model_revisions: list[str | None]
    corpus_digest: str
    domain_digest: str
    metrics: MetricsReport
    attempt_paths: list[str]
    complete: bool
    finalized: bool
    baseline_eligible: bool
    eligibility_reasons: list[str]
    invalidated: str | None = None


def eligibility(*, run: EvalRun, config: EvalConfig,
                worker: WorkerProvenance, approved_worker: WorkerImageLock,
                corpus_digest: str, domain_digest: str,
                pins: dict[str, str]) -> list[str]:
    reasons = []
    if not run.complete: reasons.append("incomplete attempt matrix")
    if not run.finalized: reasons.append("pending adjudications")
    if run.source_git.dirty: reasons.append("dirty Git tree")
    if not _pinned_worker(worker, approved_worker):
        reasons.append("worker image provenance is not pinned")
    if run.model_identity != "pinned": reasons.append("model identity is not pinned")
    if run.invalidated: reasons.append("run invalidated")
    if config.split != "test": reasons.append("official baseline requires test split")
    if run.items_expected != 225: reasons.append("official baseline requires 225 items")
    if config.run_config.model != "claude-sonnet-5":
        reasons.append("official baseline requires claude-sonnet-5")
    if (corpus_digest != APPROVED_CORPUS_DIGEST
            or domain_digest != APPROVED_DOMAIN_DIGEST):
        reasons.append("corpus identity does not match approved M2 corpus")
    if pins != APPROVED_TOOLCHAIN_PINS:
        reasons.append("toolchain pins do not match approved M2 toolchain")
    return reasons


def append_run(path: Path, record: RunRecord) -> None:
    append_jsonl(path, record.model_dump(mode="json"))


def load_runs(path: Path) -> list[RunRecord]:
    return [RunRecord.model_validate(value) for value in load_jsonl(path)]
```

Implement `GitProvenance`, `collect_git_provenance`, `read_pins`, and
`compare_runs` exactly as already described in the spec. Extend `EvalRun` with
`source_git: GitProvenance` using `TYPE_CHECKING` plus a forward annotation, make
`run_eval` require that pre-output snapshot, and make `finalize_record` copy it
into `RunRecord.git`. Finalization loads the committed `WorkerImageLock` and
passes it to eligibility; it never trusts `worker.approved_image_digest` alone.
`read_pins` resolves
Mathlib and REPL tags to commits and must reproduce `APPROVED_TOOLCHAIN_PINS`;
an empty or merely non-`unavailable` mapping is never sufficient. Dirty override records
content digests but never becomes official; comparison raises on differing
corpus/domain digests, invalid/incomplete/unfinalized records, and renders
certified metrics plus explicit provenance warnings. Use `journal.py`; do not
reintroduce an O_EXCL lockfile.

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_tracking.py tests/test_adjudication.py -v`
Expected: all PASS, including two-process journal tests.

```bash
git add src/hardy/eval/tracking.py tests/test_tracking.py
git commit -m "feat: baseline eligibility and provenance-complete run tracking"
```

---

### Task 10: Run, adjudicate, finalize, and compare CLI

**Files:**
- Create: `scripts/run_eval.py`
- Modify: `.gitignore`
- Test: `tests/test_run_eval_cli.py`

**Interfaces:**
- Subcommands: `run`, `adjudicate`, `finalize`, `compare`.
- `run` defaults to `valid` as a safety rail; the official command explicitly
  selects `test`.
- `finalize` recomputes statuses/metrics from immutable attempts + the append-only
  adjudication journal, then appends one `RunRecord`. It refuses ineligible
  official finalization unless `--exploratory` is explicit.

- [ ] **Step 1: Write failing CLI tests**

Assert parser defaults and round trips:

```python
assert parse("run").split == "valid"
assert parse("run --split test").split == "test"
assert parse("adjudicate --run-id r1 --item i1 --attempt 0 --flag-digest " + "a" * 64 + " --decision approve --reviewer alice --rationale small").decision == "approve"
assert parse("finalize --run-id r1").run_id == "r1"
assert parse("compare r1 r2").run_ids == ["r1", "r2"]
```

Test that `run` captures clean Git provenance before creating its output path and
passes the serialized snapshot into the manifest; generated artifacts do not
change it, while a dirty pre-run tree stays ineligible. Test that adjudication
requires nonempty reviewer/rationale and exact flag digest; finalization refuses
incomplete/pending/unpinned runs; an approved flag produces
a finalized eligible record; and compare self emits no warnings.

Run: `pytest tests/test_run_eval_cli.py -v`
Expected: FAIL because `scripts/run_eval.py` does not exist.

- [ ] **Step 2: Implement the four command paths**

Use `argparse` subparsers. Reuse Tasks 1–9 directly:

```python
# dispatcher in scripts/run_eval.py
COMMANDS = {
    "run": run_command,
    "adjudicate": adjudicate_command,
    "finalize": finalize_command,
    "compare": compare_command,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
```
The implementation must pass concrete dependencies rather than duplicate their
logic. `run_command` calls `collect_git_provenance` before creating the output
root or run directory, refuses a dirty tree unless exploratory mode is explicit,
and passes that immutable snapshot to `run_eval`. `finalize_command` reads the
stored `EvalRun.source_git` and never re-inspects the now-output-bearing tree.
`run_command` never enables direct workers. `adjudicate_command` records
UTC timestamp and refuses a decision whose supplied digest does not match the
attempt. `finalize_command` is idempotent by `run_id`: an identical existing
record is success; a different second record is an error.

Add to `.gitignore`:

```gitignore
# Large local trajectory streams; attempts/manifests/adjudications remain tracked.
eval_results/*/trajectories/
```

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/test_run_eval_cli.py tests/test_tracking.py tests/test_metrics.py -v`
Expected: all PASS.

```bash
git add scripts/run_eval.py .gitignore tests/test_run_eval_cli.py
git commit -m "feat: eval run adjudication finalization and comparison CLI"
```

---

### Task 11: Real-Lean integration tier

**Files:**
- Create: `tests/test_integration_eval.py`

- [ ] **Step 1: Add real-toolchain tests**

Under `pytest.mark.lean`, cover:

1. a clean theorem with a canned proof becomes `certified`;
2. `by decide` on a tiny theorem passes hard checks but becomes `provisional`;
3. approving its exact flag digest changes it to `certified` and finalizes the
   one-item run;
4. a real `#print axioms` result containing `sorryAx` fails closed;
5. one item loaded from the pinned corpus completes end to end with FakeRuntime
   and a real REPL (no model).

Do not test wall-clock thresholds; M2 deliberately removed them.

- [ ] **Step 2: Run integration and default-tier selection checks**

Run: `pytest -m lean tests/test_integration_eval.py -v`
Expected: all PASS on a configured Lean host.

Run: `pytest tests/test_integration_eval.py -m "not lean" -v`
Expected: all collected and deselected.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration_eval.py
git commit -m "test: real-Lean certified and provisional eval paths"
```

---

### Task 12: Finalized miniF2F `test` baseline

**Files:**
- Create through commands: `eval_results/runs.jsonl`,
  `eval_results/adjudications.jsonl`, and `eval_results/<run-id>/attempts/*.json`

- [ ] **Step 1: Freeze and verify the implementation**

Run: `pytest -m "not lean and not tex and not docker and not model"`
Expected: all PASS.

Run on a toolchain host: `pytest -m lean -v`
Expected: all PASS.

Run: `docker image inspect --format "{{.Id}}" hardy-lean:dev`
Expected: exactly one image ID beginning with `sha256:` followed by 64 hexadecimal characters.

- [ ] **Step 2: Run the five-item `valid` smoke test**

```bash
python scripts/run_eval.py run --model claude-sonnet-5 --split valid --limit 5 \
  --attempts 1 --max-turns 25 --wall-clock-s 600 --item-timeout-s 900 \
  --workers 2 --parallelism 2 --out eval_results
```

Expected: a complete smoke manifest, certified metrics plus any provisional
upper bound, exact pinned model identity, and no invalidation. This run is never
the official baseline.

- [ ] **Step 3: Freeze the config and run the full usable `test` split**

```bash
python scripts/run_eval.py run --model claude-sonnet-5 --split test \
  --attempts 1 --max-turns 25 --wall-clock-s 600 --item-timeout-s 900 \
  --workers 8 --parallelism 8 --out eval_results
```

Expected: `items_expected: 225`, 225 terminal attempts, complete manifest,
`model_identity: pinned`, one image digest, and zero missing records. Do not tune
configuration after observing these outcomes.

- [ ] **Step 4: Adjudicate every provisional attempt**

For each printed pending attempt, inspect source, trajectory, goal size, and axiom
report, then run one exact command:

```bash
python scripts/run_eval.py adjudicate --run-id <run-id> --item <item-id> \
  --attempt <index> --flag-digest <sha256> --decision approve \
  --reviewer <github-login> --rationale "small kernel-checked decide use"
```

Use `--decision reject` with the concrete reason when appropriate. Repeat
`finalize` only after the CLI reports zero pending attempts.

- [ ] **Step 5: Finalize and verify eligibility**

```bash
python scripts/run_eval.py finalize --run-id <run-id> --out eval_results
python scripts/run_eval.py compare <run-id> <run-id> --out eval_results
```

Expected: `baseline_eligible: true`, `finalized: true`, zero pending attempts,
no comparison warnings, corpus revision
`638c70ed4dfb28cac2d5bbbb43b6fc1fd2f7a40f`, test item count 225, clean Git
SHA captured before run output, approved image digest plus matching in-container toolchain attestation, `model_id: claude-sonnet-5`, and certified pass@1.

- [ ] **Step 6: Commit the baseline**

```bash
git add eval_results/runs.jsonl eval_results/adjudications.jsonl \
  eval_results/<run-id>/attempts
git commit -m "chore: record M2 certified test baseline — pass@1 <value> (<run-id>)"
```

M2 is complete only when this commit exists and the appended record is finalized
and baseline-eligible.

---

## Self-Review

1. **Spec coverage:** Tasks 1–2 pin and validate the exact portable corpus,
   exclusions, license, and domain manifest. Tasks 3–4 implement immutable model
   identity and all hard/closer checks. Task 4B supplies crash-safe journals and
   append-only decisions. Tasks 5–7 provide CPU metering, digest-pinned workers,
   durable complete attempt matrices, and sandbox-only orchestration. Tasks 8–10
   apply decisions, compute certified/provisional metrics, enforce eligibility,
   and expose run/adjudicate/finalize/compare. Tasks 11–12 cover real Lean and the
   finalized 225-item test baseline.
2. **Placeholder scan:** Angle-bracket values occur only in commands whose values
   are generated by the immediately preceding run (`run-id`, attempt identity,
   digest, reviewer, measured pass@1). No implementation API, behavior, error
   path, corpus revision, count, split, or model identity is unspecified.
3. **Type consistency:** `AntiCheatReport.flags -> flag_digest ->
   AdjudicationEvent.flag_digest -> apply_adjudications -> EvalResult.status ->
   compute_metrics -> RunRecord` is one chain. `EvalRun.complete/finalized` feed
   eligibility and CLI finalization under the same names.
4. **Known execution risks:** M1 interfaces must be revalidated after landing;
   provider response IDs may be absent, but canonical `claude-sonnet-5` remains a
   pinned configured identity; native-Windows M0 process-group tests have a known
   pre-existing `os.killpg` failure unrelated to this docs-only plan revision.

## Status

- [ ] Not started — revised spec and plan await review gates and PR review.