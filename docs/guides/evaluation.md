# Measuring a model against the corpus

This guide walks through `hardy evals`: checking and browsing the corpus,
sweeping the automation floor, running a model against a set of statements,
checking and pooling the boards that run writes, comparing boards, and
reading what the numbers do and do not say. It is for someone who already
has Hardy installed and wants to run one of these commands rather than read
about the whole system. For every flag, see
[the CLI reference](../reference/cli.md#hardy-evals). For what each file on
disk contains, see
[Artifacts](../reference/artifacts.md#scoreboards-baselines-pools) and
[On-disk layout](../reference/on-disk-layout.md#evaluation-artifacts-evals).
For the reasoning behind these measurements, see
[the evaluation design](../design/evaluation.md) and
[the corpus design](../design/corpus.md).

## The corpus and the branch it lives on

The corpus is `corpus/`: statements sharded by MSC class under
`corpus/problems/`, plus `corpus/sources.json`, `corpus/tombstones.json`,
`corpus/CHANGELOG.md`, and the generated `corpus/EVALS.md`. It holds
statements only, no tier, no solve rate, no discrimination score; what a
model or the automation ladder does with a statement is a measurement over
it, recorded elsewhere, not a property of it. See
[the corpus design](../design/corpus.md) for why that split matters.

Corpus content is committed on the `corpus/curation` branch; the harness
that measures it, `src/`, `tests/`, and this documentation, is committed on
`main`. `corpus/curation` branches off `main` and rebases onto it; a corpus
commit never carries a `src/` or `tests/` edit alongside it. If you are
adding statements rather than reading measurements over the existing ones,
follow [the corpus ingestion skill](../../.claude/skills/ingest-corpus/SKILL.md)
instead of improvising the field-by-field rules here.

## Checking and browsing the corpus

`hardy evals corpus` works on the corpus directory by itself, with no
scoreboard involved. Every verb takes `--corpus` (default `corpus`).

```sh
hardy evals corpus check
```

[`check`](../reference/cli.md#hardy-evals-corpus-check) reports every
mechanical objection to the corpus on disk and exits `1` if it finds one.
`--since-registry` takes a previous release's `tombstones.json` and
establishes that the id registry stayed append-only; `--since` takes a
previous release's `CHANGELOG.md` and refuses content that moved under a
version already released. CI passes the merge base's copy of each.

```sh
hardy evals corpus report
```

[`report`](../reference/cli.md#hardy-evals-corpus-report) prints coverage by
group, status, difficulty, and source.

```sh
hardy evals corpus serve
```

[`serve`](../reference/cli.md#hardy-evals-corpus-serve) browses the corpus
in a local page that re-reads from disk on every refresh: the statement, the
Lean, and the classification side by side, with the objections `check`
would raise shown against the entries that earned them. It binds
`127.0.0.1` by default (`--host`, `--port 8765`), because a working corpus
is not a published site, and the page is unauthenticated, so binding
`0.0.0.0` hands the whole corpus to anything that can reach the machine.

The viewer has exactly one write route: `POST /api/review` records a human
faithfulness read (the button labelled Faithful or Unfaithful) as a `Review`
bound to that entry's digests, computed on the server from the entry as it
stands on disk rather than accepted from the page, so a stale tab cannot
approve a statement that has since changed. That review is what promotes an
entry from `candidate` to `active`; nothing else about the corpus is edited
through the viewer, and an entry is still authored, corrected, and retired
in a text editor and checked with `evals corpus check`.

```sh
hardy evals corpus release --version 0.4.0 --note '...'
```

[`release`](../reference/cli.md#hardy-evals-corpus-release) bumps every
shard and writes the changelog head that binds them. `--version` is
required and must be three numbers greater than the last release;
`--note` adds a changelog bullet and is repeatable. A malformed release
is refused with exit `2`.

## The baseline sweep

```sh
hardy evals baseline --acknowledge-unsafe-execution
```

[`hardy evals baseline`](../reference/cli.md#hardy-evals-baseline) runs a
fixed ladder of tactics and tactic chains against every canonical
statement's own declaration and writes the tier file, `evals/baseline.json`
by default. A **tier** (0 through 3) records how much of that automation
closed the statement: tier 0 is a single ordinary tactic, tier 1 is one of
the searchers (`exact?`, `apply?`, `hint`), tier 2 needs a chain, and tier 3
is nothing on the ladder at all. It is a fact about one ladder against one
Mathlib revision on one machine, never a property of the theorem; see
[the evaluation design](../design/evaluation.md) for the full table and the
reasoning behind it. Rows are carried forward from an existing tier file
rather than re-swept, but only where the environment, the procedure, and
that entry's own statement digest all still agree; a corrected statement
re-sweeps only that entry.

`--problems` defaults to `corpus`, never `corpus/problems`, and `--out`
defaults to `evals/baseline.json`. `--only`, `--only-file`, and `--status`
narrow the sweep to selected entries; `--acknowledge-unsafe-execution` is
required, because the sweep elaborates Lean built from the problem file's
own imports, binders, and conclusion with no sandbox. `--workers` (default
`1`) is the one thing free to raise, since the sweep is CPU-bound on Lean
elaboration rather than waiting on a provider. The command exits `1` if the
sweep found problems with the corpus.

A full sweep touches every canonical statement, the same thing a bare
`pytest` does by accident on a machine with Lean configured; see
[running the test suite safely](proving.md#running-the-test-suite-safely)
in the proving guide for how to keep that out of an ordinary local run.

## Running a set

```sh
hardy evals run --label first-pass --acknowledge-unsafe-execution
```

[`hardy evals run`](../reference/cli.md#hardy-evals-run) runs every
selected entry through the batch or staged path and writes a scoreboard
under `evals/scoreboards/<label>/`. `--label` is required and names it.

`--mode` chooses `batch` (the default) or `staged`, with one exception: a
twin, an entry expected to be false, always runs `batch` regardless of
`--mode`, because the staged loop grades every unverified run `partial`,
which is the wrong reading of a correctly refused false claim. Its budget
is the separately recorded twin turn and wall-second pair, so `--no-twins`
drops twin rows from the run entirely rather than changing their budget.

Budgets differ by mode. In batch mode, `--max-turns` (default `60`) and
`--wall-seconds` (default `1800.0`) bound each attempt; both are refused
under `--mode staged`, whose own budgets are `active_seconds`,
`proof_seconds`, and `official_checks` instead, set through the staged
condition rather than the command line.

Selection narrows which entries run: `--only` and `--only-file` name entry
ids, `--status` filters by corpus status, and `--tiers` filters by the
baseline tier (`--tiers 2,3`). `--repeats` (default `1`, must be at least
`1`) runs each selected entry more than once. `--backend` accepts `claude`
or `codex` from the parser, but `codex` is refused at run time with exit
`2`: the batch runner, the canonical reader, and staged tool-event counting
are all Claude-shaped. `--workers` (default `1`) is concurrent rows;
`--acknowledge-unsafe-execution` is required, the same acknowledgment as
`evals baseline`, since every run is unsandboxed.

`--problems` defaults to `corpus`, `--baseline` to `evals/baseline.json`,
and `--scoreboards` to `evals/scoreboards`.

**`evals run` exits `0` once the board is written, whatever the rows say.**
An unsolved entry, or a twin the model proved, is a measurement and not a
failure of the command. Gate CI on `hardy evals check` instead of on this
exit status.

## Checking a board

```sh
hardy evals check evals/scoreboards/first-pass
```

[`hardy evals check`](../reference/cli.md#hardy-evals-check) re-derives
every figure in a committed scoreboard from its run directories and the
corpus and tier file it names by digest, both of which must be present: the
command refuses with exit `2` before reading the scoreboard if either is
missing. It prints the headline, the floor, and the per-tier aggregates
when nothing disagreed, and exits `1` on any inconsistency. `--problems`
(default `corpus`) and `--baseline` (default `evals/baseline.json`) name
what it checks against.

## What is left

```sh
hardy evals todo
```

[`hardy evals todo`](../reference/cli.md#hardy-evals-todo) reports, as JSON
on stdout, what remains to sweep or run under the pooling key this checkout
would produce. It takes the same budget, mode, strategy, history-mode, and
repeat flags as `evals run`, with the same types and defaults, because all
of them feed the pooling key: without them, `todo` would report the key of
a default run while an actual `evals run --max-turns 40` recorded a
different one, and `evals pool` would then refuse the very board `todo`
said was needed.

## Pooling

```sh
hardy evals pool first-pass second-pass
```

[`hardy evals pool`](../reference/cli.md#hardy-evals-pool) combines
scoreboards that share one pooling key into `evals/pools/<label>/pool.json`,
a derived, recomputable score. The pooling key is
`(run_procedure_digest, environment_digest)`: two boards pool only when both
say they measured the same procedure against the same environment.

`run_procedure_digest` moves whenever the code that decides a run's outcome
changes. Concretely, editing anything under `src/hardy/` that is not in the
denylist in `src/hardy/evals/identity.py` moves it, and a moved digest
orphans every scoreboard already on disk: boards stop pooling with each
other and `evals todo` reports `boards_counted: 0` for models that plainly
have boards. The corresponding digest for the baseline, `procedure_digest`,
moves the same way when one of the six `DECIDING_SOURCES` in
`src/hardy/evals/sweep.py` changes, and that stales the whole tier file
rather than one entry.

Neither is a reason not to make the change; both are a reason to batch such
edits together rather than trickle them in one at a time, and to never make
one while a sweep or a run is in flight. Recorded evidence is never
restamped to match a new digest after the fact: an old board stays what it
measured, or it stops pooling, and nothing recomputes its digest to make it
agree with code that has since moved on.

The sweep and run budgets themselves are frozen for the same reason:
`lean_timeout` stays `180` and the wall backstop at `600`, chosen once,
because raising either would invalidate the baseline carry-forward and
un-pool every prior run. `--workers` is the one knob free to vary from run
to run without moving anything. See
[the evaluation design](../design/evaluation.md) for the full account of
what stays in the key and what is deliberately kept out of it (the corpus
and baseline file hashes, and the source revision).

## Comparing

```sh
hardy evals compare left-board right-board --vary model
```

[`hardy evals compare`](../reference/cli.md#hardy-evals-compare) reads two
scoreboards, pairs their exact `(id, repeat)` slots, and reports the pairs
as JSON on stdout. It computes no mean, names no winner, runs no
significance test, and attributes nothing causally: it describes, and
naming a field with `--vary` records that you intended to vary it, so an
unintended difference is reported as one rather than left for you to
notice.

```sh
hardy evals history evals/scoreboards/before evals/scoreboards/after --vary model
```

[`hardy evals history`](../reference/cli.md#hardy-evals-history) reads
several boards chronologically without pooling them. `--vary` here carries
the same caveat: naming a control does not establish that it caused any
difference seen, and historical change over unpooled boards is not causal
evidence either way.

## Summary

```sh
hardy evals summary
```

[`hardy evals summary`](../reference/cli.md#hardy-evals-summary) writes a
Markdown report over every scoreboard under `--scoreboards`, one row per
model, to `--out` (default `corpus/EVALS.md`). It is read-only: nothing
about a scoreboard changes because a summary was written, and the file it
writes is corpus content, committed on `corpus/curation` alongside the
statements it reports on rather than on `main`.

## Importing benchmarks

```sh
hardy evals import-benchmark minif2f --archive miniF2F.tar.gz \
  --revision f0dcc8b59e630fba00ba9569ca6714700e0a8801 \
  --sha256 298cfb25e8f7c065cbdc87c2516214772241bad8b9818653a15069c8c8da95ca \
  --output imported-minif2f
```

[`import-benchmark`](../reference/cli.md#hardy-evals-import-benchmark)
preserves a pinned upstream archive from one of three datasets, `minif2f`,
`putnambench`, or `proofnet`, into a fresh local output directory (an
existing path is refused). `--revision` is the full upstream commit SHA,
not a branch or tag, and `--sha256` is checked against the archive's own
bytes.

An import establishes lexical coverage: the archive's original statement
bytes are retained and indexed, so a statement can be located and read. It
does not port the Lean, normalize a statement, adopt anything into the
corpus, or execute anything from the archive; the original miniF2F and
ProofNet datasets remain Lean 3 inputs regardless. A revision label is
checked only against the archive's own SHA-256, which is not the same as
independently proving that hash identifies the archive's remote origin.
See [Artifacts](../reference/artifacts.md#benchmark-imports) for the full
field list and the three datasets' recorded revisions and license files.

## Certification

`hardy evals`'s certified statistic is not pass@k. `certify` reads a board
and reports, per declared `k`, the observed success within each problem's
first `k` prospectively declared attempts, under a per-attempt cap on
independent verifier calls, against a frozen claim. It is not an IID
pass@k estimator, not a confidence interval, and not an independent kernel
replay: a certified attempt still rests on the same recorded Lean and
canonical trust boundary every other row does. Three figures are reported
rather than one, an observed lower bound, an observed upper bound that
keeps unauthenticated or unrun slots open, and a certified value that
appears only when every declared slot is present and eligible, because
missing evidence must not quietly shrink the denominator. See
[the evaluation design](../design/evaluation.md) for the full account,
including what batch receipts and the wall-clock figures cannot establish.

## Reading the numbers

Three things about a scoreboard's numbers are easy to lose track of once
they are just a solve rate on a page.

**Repeats are not sample size.** A board stores one row per repeat, so 125
entries run at 10 repeats each is 1,250 rows, not 1,250 independent
observations of 1,250 different problems. Before computing an interval or
comparing two conditions by hand, collapse each `(model, entry)` pair to
one declared outcome under a rule you fix in advance; treating raw rows as
independent samples narrows an interval by roughly the repeat count and can
manufacture a difference that is not there.

**Items cluster on the source text.** A field's entries are commonly drawn
from as few as two texts, so problems sharing a chapter, a prerequisite
chain, or an author's habits tend to succeed or fail together rather than
independently. A plain interval or paired test over such a set reads
narrower than the data actually supports; the honest statistic clusters on
the source text and reports its effective sample size beside the raw item
count. A field whose entries come from a single text carries no ranking
claim: one cluster is one observation, however many exercises it contains.

**A ranking claim is refused when its interval crosses zero.** Where a
paired comparison's confidence interval spans zero, the honest report is
that the data does not resolve a direction, not a number of additional runs
that would fix it: how many more pairs are needed depends on how they would
split between the two models, which cannot be known in advance, so the
honest substitute for "needs N more pairs" is a power curve stated under an
explicit assumed effect size, never a bare count.

`hardy evals compare` and `hardy evals history` do not perform this
collapsing, clustering, or refusal for you today: they pair or list rows
descriptively and leave the reading to you. Apply the three rules above by
hand before drawing a ranking conclusion from either command's output. See
[the evaluation design](../design/evaluation.md) for the full reasoning
and for why this reporting layer is smaller than the data it could
support.
