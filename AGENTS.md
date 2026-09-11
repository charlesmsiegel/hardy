# Agent instructions

This file is the contract for any coding agent working in this repository. It
applies whatever model or harness is driving the session; `CLAUDE.md` points
here and adds nothing.

## Read first

1. `README.md`, for what Hardy is and what it refuses to claim.
2. `docs/design/overview.md`, `docs/design/trust-boundary.md`, and
   `docs/design/output-contract.md`, for the rules every change must keep.
3. `docs/reference/` for what the commands, settings, and artifacts are today.
   The reference pages are tested against the code; trust them over memory.

`docs/design/decisions.md` records what was chosen over what and why. Read the
entry for an area before proposing to change it.

## Working on the code

```sh
scripts/install.sh                          # full environment from a clone
uv run --extra test pytest -m "not real_toolchain and not live"
uvx ruff check src tests
uv run hardy doctor                         # what this machine is still missing
```

A bare `pytest` on a machine with a configured Lean project runs a whole-corpus
real sweep; use the marker filter above. `CONTRIBUTING.md` describes the test
tiers, coverage floor, CI, and releases.

## Source ownership

Implementations live under `src/hardy/agents`, `algebra`, `app`, `corpus`,
`documents`, `evals`, `formal`, `foundation`, `literature`, `prompts` and
`workflows`. The package root holds only `__init__.py`, `__main__.py` and the
`cli.py`, `mcp_server.py`, `cas_driver.py` entry-point shims. Import from the
owning package; the former root modules are gone.

Dependencies point one way: `app` to `workflows` to the capability packages
(`formal`, `documents`, `algebra`, `literature`, `corpus`) to `foundation`, with
`agents`, `evals` and `prompts` beside them. `tests/unit/test_module_boundaries.py`
enforces the direction; `docs/design/module-boundaries.md` explains it. The
interactive coordinator is `workflows/interactive/session.py`; record, formal
save, admission, document, and turn responsibilities each have their own owner
beside it, and collaborators receive named operations and snapshots, never the
whole session.

## Repository rules

- The Lean kernel is the authority for formal verification. Preserve the frozen
  statement, audit axioms, and keep kernel verification distinct from heuristic
  review and document compilation.
- Partial results are valid only when their remaining holes and assumptions are
  explicit. Never silently weaken or strengthen a theorem to make it pass.
- Nothing confines generated Lean, TeX, downloaded papers, or helper processes.
  Never describe them as safe. Run only trusted output in disposable
  environments.
- Prefer the shortest vertical slice that tests a design assumption. Do not
  restore milestone machinery, a container sandbox, framework abstractions, or a
  warm worker pool unless current evidence requires them.
- When code is introduced, add the smallest tests and commands that reproduce
  the experiment. Record model, toolchain, configuration, and source identities
  when they can affect results.
- Status lives only in `docs/roadmap.md`. Reference pages under
  `docs/reference/` are tested against the code: when you change a command,
  flag, slash command, or setting, `tests/unit/test_docs.py` names the page to
  update. Design pages carry reasoning and no status markers. Planning
  artifacts, reports, and session notes do not belong in the tree.
- Add every new page under `docs/` to `docs/README.md`; the same test checks it.

## Branching: code on `main`, statements on `corpus/curation`

Two kinds of change live here and move at different speeds. The harness
(`src/`, `tests/`, tooling, docs) is code. The corpus is mathematical content:
`corpus/problems/*.json`, `corpus/CHANGELOG.md`, `corpus/sources.json`,
`corpus/tombstones.json`, and `corpus/EVALS.md`.

`main` carries everything that is not corpus content. `corpus/curation` branches
off `main` and carries only the statements and the measurements over them.
Never make a code change on the corpus branch:

```sh
git checkout main
# edit src/, tests/, docs
git commit
git checkout corpus/curation
git rebase main
```

A change that is both, such as a harvest that also improves the ingestion
skill, is split: the code half onto `main`, the statements onto the corpus
branch. A corpus harvest can be tens of thousands of JSON lines; a harness
change buried in one cannot be reviewed, and keeping them apart lets a code
change be tested against the base corpus before the statements that exercise it
exist. `corpus/EVALS.md` is on the corpus side even though it is generated: it
reports on the active corpus. `evals/` is ignored and holds no committed
evidence.

### Digest coupling

Two digests decide whether earlier measurements can be reused, and a rebase is
exactly when the edits that move them land:

- Editing any of the six deciding sources named in `src/hardy/evals/sweep.py`
  (the sweep itself, `formal/audit.py`, `formal/lean.py`, `formal/syntax.py`,
  `corpus/problems.py`, `corpus/identity.py`) moves `procedure_digest` and makes
  the whole tier file non-reusable; the next sweep re-elaborates every entry.
- Editing anything under `src/hardy/` not excluded by the denylist in
  `src/hardy/evals/identity.py` moves `run_procedure_digest` and orphans every
  scoreboard on disk, so boards stop pooling and `hardy evals todo` reports
  `boards_counted: 0`.

Neither is a reason not to make a change. Both are reasons to batch such edits
rather than trickle them, never to make one while a sweep or a run is in
flight, and never to restamp old evidence with a new digest to regain reuse.

The unit tests that assert the shipped corpus's counts
(`tests/unit/test_evals_corpus.py`, `test_evals_problems.py`,
`test_evals_viewer.py`) need their own values on each branch and are hand-edited
when the corpus grows; expect them red on the corpus branch between a harvest
and its release.
