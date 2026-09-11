# Contributing to Hardy

This page covers the development setup, the test tiers, the branching rule,
and the documentation rules for anyone contributing code, statements, or docs
to Hardy.

## Development setup

Clone the repository, then run the installer from the clone:

```sh
scripts/install.sh --yes
```

This provisions a virtual environment with `uv`, installs the pinned Lean
toolchain through elan, and writes a project configuration. Once it finishes,
confirm the machine is ready:

```sh
hardy doctor
```

Run the hermetic test suite with the real-toolchain and live tiers excluded:

```sh
uv run --extra test pytest -q -m "not real_toolchain and not live"
```

Measure what the suite covers by adding `--cov`:

```sh
uv run --extra test pytest -q --cov --cov-report=xml --cov-report=html \
  --cov-report=term -m "not real_toolchain and not live"
```

This writes `coverage.xml` and `htmlcov/index.html` and fails the run if
coverage drops below the floor set by `fail_under` in `pyproject.toml`. That
floor is a backstop against regressions, not a target to chase; it sits just
under what the suite measures today.

Lint with ruff:

```sh
uvx ruff check src tests
```

`pyproject.toml` lists the selected rule families and the two the project
ignores deliberately, each with a comment saying why.

## Test tiers

Hardy's tests are split by what they need to run, using the `real_toolchain`
and `live` markers declared in `pyproject.toml`.

**Hermetic** tests need nothing beyond the Python environment `uv` builds.
They are the default: a bare `pytest -m "not real_toolchain and not live"`
runs only these. CI's `Tests` workflow runs them with coverage on every push
to `main` and every pull request, then lints with ruff, and for pull requests
also checks any corpus release against the merge base.

**Real toolchain** tests invoke a real installed binary: Lean, Tectonic,
Singular, or Macaulay2. They carry the `real_toolchain` marker and skip
themselves when the binary or project they need is not there. CI's `Tests`
workflow installs Lean at the `stable` release (deliberately unpinned, so a
change in Lean's own wording is caught rather than hidden) and runs the Lean
real-toolchain tests; `CAS backends` installs pinned Singular and Macaulay2
packages on Ubuntu 24.04 and runs the CAS real-toolchain tests. `CAS backends`
only runs on a pull request that touches the source tree, a CAS test file, or
its own workflow file, since it would otherwise repeat the hermetic suite on
every unrelated change.

**Live** tests invoke a billable model, an installer, or a third-party
service over the network. They opt in through environment variables read
only by the test suite: `HARDY_LIVE=1` for model calls, `HARDY_ARXIV_LIVE`
and `HARDY_LOOGLE_LIVE` for the literature and search backends, and
`HARDY_RECORD_DIR` for where such a test writes what it recorded. No workflow
in this repository runs the live tier; it is for a contributor's own machine
and their own credentials.

A bare `pytest`, with no marker filter, on a machine with a configured Lean
project runs `tests/integration/test_evals_real.py`'s
`test_every_canonical_statement_elaborates`, which sweeps the whole corpus
through a real Lean elaboration. [The proving guide](docs/guides/proving.md)
describes this sweep and the invocation that excludes it; run that
invocation locally, since plain `pytest` is neither fast nor what most local
runs want.

## Branching: code on `main`, statements on `corpus/curation`

Two kinds of change live in this repository and they move at different
speeds. The harness, meaning `src/`, `tests/`, tooling, and docs, is code.
The corpus is mathematical content: `corpus/problems/*.json`,
`corpus/CHANGELOG.md`, `corpus/sources.json`, `corpus/tombstones.json`, and
`corpus/EVALS.md`.

`main` carries everything that is not corpus content. `corpus/curation`
branches off `main` and carries only the statements and the measurements over
them. Never make a code change on the corpus branch:

```sh
git checkout main
# edit src/, tests/, docs
git commit
git checkout corpus/curation
git rebase main
```

Corpus work, meaning harvesting statements, recording faithfulness reads
through the viewer, and cutting a corpus release, is committed on
`corpus/curation` and never on `main`. A change that is genuinely both, such
as a harvest that also improves the ingestion skill, is split: the code half
onto `main`, the statements onto the corpus branch. Do not let a corpus
commit carry a `src/` or `tests/` edit along with it.

[The corpus design page](docs/design/corpus.md) explains why the split
exists and what it buys.

## Digest coupling

A rebase from `main` onto `corpus/curation` is exactly when a costly edit
lands, so batch these rather than trickle them, and never make one while a
sweep or a run is in flight.

Editing any of six deciding sources moves `procedure_digest` and makes the
entire tier file non-reusable, so the next sweep re-elaborates every entry:
the sweep itself (`src/hardy/evals/sweep.py`), `formal/audit.py`, `formal/lean.py`, `formal/syntax.py`,
`corpus/problems.py`, and `corpus/identity.py`.

Editing anything under `src/hardy/` that is not covered by the denylist in
`src/hardy/evals/identity.py` moves `run_procedure_digest` and orphans every
scoreboard on disk, so boards stop pooling and `evals todo` reports
`boards_counted: 0`.

Neither is a reason not to make the change.
[The module boundaries page](docs/design/module-boundaries.md) covers the
reasoning behind these boundaries in more depth.

## Issues and the roadmap

GitHub issues are Hardy's defect ledger, not its backlog. File an issue when
you can point at current code, output, or documentation and describe what is
presently wrong, with reproduction steps and expected behavior. Planned
capabilities, refactors, optimizations, and future deployment modes belong in
[the roadmap](docs/roadmap.md) instead.

## Documentation rules

Every fact has one home. Status of any kind, meaning what is planned, in
progress, or done, lives only in [the roadmap](docs/roadmap.md); no other
page carries a milestone marker.

Reference pages under `docs/reference/` are checked against the code by
`tests/unit/test_docs.py`: `test_cli_reference_names_every_command_and_option`
checks `docs/reference/cli.md`,
`test_session_reference_names_every_slash_command` checks
`docs/reference/session-commands.md`, and
`test_configuration_reference_names_every_setting` checks
`docs/reference/configuration.md` against the code that defines each command,
option, slash command, and setting. Design pages carry no status markers;
they explain why the system is shaped the way it is, not what stage it is at.

Every new page added under `docs/` is listed in
[the documentation index](docs/README.md); the same test module checks that
the index names every page.

Prefer linking an existing page over restating what it already says. Do not
use em-dashes, issue numbers, or task IDs anywhere outside `docs/roadmap.md`.
Process artifacts and session notes are not kept in the tree.

## Line endings

`.gitattributes` forces LF on every `*.sh` file regardless of platform or
`core.autocrlf`, since a CRLF checkout fails a shell script at its shebang
with "bad interpreter: bash^M". `acceptance/recorded/**`, `evals/**`, and
`corpus/**` are marked `-text`, since each is bound by a digest over its raw
bytes; a line-ending conversion on any platform would make a published
version fail to verify.

## Releases

A release is cut by pushing a tag matching `v<version>`, where `<version>` is
the `project.version` declared in `pyproject.toml`; the `Release` workflow
refuses to publish when the tag and the declared version disagree. It builds
the wheel and source distribution, bundles `scripts/` into
`hardy-installers.tar.gz` so a standalone installer can fetch the rest of
itself, and publishes all three alongside a `SHA256SUMS` manifest as four
release assets. A published release is never rewritten: the workflow refuses
to upload over a release that already has assets, so cutting a new release
means bumping `pyproject.toml`'s version and tagging that.
