# Hardy: working agreements

Read `AGENTS.md` first; everything there applies here. This file adds only the
branching rule, which is repeated in both so neither can be read alone and get
it wrong.

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

- Editing `sweep.py`, `audit.py`, `lean.py` or `evals/problems.py` moves
  `procedure_digest` and makes the entire tier file non-reusable -- the next
  sweep re-elaborates every entry.
- Editing anything under `src/hardy/` that is not in `RUN_SOURCE_EXCLUDED_FILES`
  moves `run_procedure_digest` and orphans every scoreboard on disk, so boards
  stop pooling and `evals todo` reports `boards_counted: 0`.

Neither is a reason not to make the change. Both are a reason to batch such
edits rather than trickle them, and never to make one while a sweep or a run is
in flight.
