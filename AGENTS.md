# Codex startup context

## Read first

Hardy restarted from a documentation-only reset and now carries one thin
interactive slice; nothing promises compatibility with the deleted prototype.
Before designing or coding, read `README.md`, `DESIGN.md`, and `FEATURES.md`; use
`ARCHITECTURE.html` as the visual overview and `docs/INSTALL.md` for how a machine
is brought up.

To work on the code: `scripts/install.sh` sets up a full environment,
`uv run --extra test pytest` runs the hermetic suite, and `hardy doctor` reports
what a machine is still missing. Add `--cov` to measure what the suite reaches;
it writes `coverage.xml` and `htmlcov/index.html`, and fails below the floor in
`pyproject.toml`. CI runs the same command on every pull request and keeps the
report.

## Source ownership

Implementations live under `src/hardy/agents`, `algebra`, `app`, `corpus`,
`documents`, `evals`, `formal`, `foundation`, `literature`, `prompts` and
`workflows`. The package root has only `__init__.py`, `__main__.py` and the
`cli.py`, `mcp_server.py`, `cas_driver.py` entry-point shims. Use canonical package
imports; the former root implementation modules, including `domain.py` and
`models.py`, have been removed.

The interactive coordinator is `workflows/interactive/session.py`; record,
formal save, admission, document and turn responsibilities have separate owners
beside it. Collaborators receive named operations and snapshots, not the whole
session. Application construction and terminal adapters live in `app/`, including
`app/tui/`, `app/evals.py` and `app/corpus_viewer.py`.

Shared primitives are in `foundation/values.py`, `files.py`, `locking.py` and
`paths.py`; capability and run values live in `formal/contracts.py`,
`documents/contracts.py`, `workflows/contracts.py` and
`workflows/batch_contracts.py`. Keep dependency direction from application to
workflow to capabilities to foundations. `corpus/` under the Python package is
code; the repository-level corpus content still follows the branch rules below.

## Repository rules

- Keep `README.md`, `DESIGN.md`, `FEATURES.md`, and `ARCHITECTURE.html` consistent.
- Prefer the shortest vertical slice that tests a design assumption. Do not restore
  the old milestone machinery, container sandbox, framework abstractions, or warm
  worker pool unless current evidence requires them.
- The absent sandbox is a known temporary risk. Never describe generated Lean,
  TeX, downloaded papers, or helper processes as safe. Run only trusted output in
  disposable development environments until isolation is deliberately restored.
- The Lean kernel is the authority for formal verification. Preserve the original
  statement, audit axioms, and distinguish kernel verification from heuristic
  review and document compilation.
- Partial results are valid only when their remaining holes and assumptions are
  explicit. Never silently weaken or strengthen a theorem to make it pass.
- When code is introduced, add the smallest tests and commands needed to reproduce
  the experiment. Record model, toolchain, configuration, and source identities
  when they can affect results.

## Current direction

Build the “First experiment acceptance test” in `FEATURES.md` before expanding the
architecture: one model loop, direct Lean feedback, structured tools, a saved
trajectory, a checked Lean artifact, and an honestly graded writeup.

## Branching: code on `main`, statements on `corpus/curation`

Two kinds of change live in this repository and they move at different speeds.
The harness -- `src/`, `tests/`, tooling, docs -- is code. The corpus is
mathematical content: `corpus/problems/*.json`, `corpus/CHANGELOG.md`,
`corpus/sources.json`, `corpus/tombstones.json`, and `corpus/EVALS.md`.

**`main` carries everything that is not corpus content.** **`corpus/curation`
branches off `main` and carries only the statements and the measurements over
them.**

### The rule

Never make a code change on the corpus branch.

```
git checkout main
# ... edit src/, tests/, docs ...
git commit
git checkout corpus/curation
git rebase main
```

Corpus work -- harvesting statements, recording faithfulness reads through the
viewer, cutting a corpus release -- is committed on `corpus/curation` and never
on `main`.

A change that is genuinely both, such as a harvest that also improves the
ingestion skill, is split: the code half onto `main`, the statements onto the
corpus branch. Do not let a corpus commit carry a `src/` or `tests/` edit along
with it.

### Why the split

A corpus branch accumulates enormous JSON diffs: one harvest was 39,000 lines.
Reviewing a harness change buried in that is not review. Keeping them apart also
means a code change is testable against the base corpus on `main` before the
statements that exercise it exist.

`corpus/EVALS.md` is on the corpus side even though it is generated rather than
authored: it reports on the *active* corpus, so on `main` it would describe
entries `main` does not carry.

`evals/` is ignored and holds no committed evidence. `evals/baseline.json` and
the scoreboards are local artifacts, regenerable with `hardy evals baseline` and
`hardy evals run`.

### Two things the split does not resolve

`tests/unit/test_evals_corpus.py`, `test_evals_problems.py` and
`test_evals_viewer.py` assert the shipped corpus's counts, so each branch needs
its own values and they must be hand-edited whenever the corpus grows. Deriving
the counts from the shards would end this; until then, expect them red on the
corpus branch between a harvest and its release.

Digest coupling makes some code edits expensive, and a rebase is exactly when
they land:

- Editing a deciding source listed in `src/hardy/evals/sweep.py` -- the sweep,
  `formal/audit.py`, `formal/lean.py`, `formal/syntax.py`, `corpus/problems.py`
  or `corpus/identity.py` -- moves `procedure_digest` and makes the entire tier
  file non-reusable; the next sweep re-elaborates every entry.
- Editing anything under `src/hardy/` that is not excluded by
  `RUN_SOURCE_EXCLUDED_FILES` or `RUN_SOURCE_EXCLUDED_DIRS` in `evals/identity.py`
  moves `run_procedure_digest` and orphans every scoreboard on disk, so boards
  stop pooling and `evals todo` reports `boards_counted: 0`.

Neither is a reason not to make the change. Both are a reason to batch such
edits rather than trickle them, and never to make one while a sweep or a run is
in flight.
