# Interactive publication verification

E3 adds explicit terminal publication operations over the existing ledger,
publication planner and document compiler. It does not configure ledger evidence
authentication: a compiled draft still reports unestablished mathematics and
other publication gaps. E0/E2 human-guided trials are outside this change.

## Commands and result

```text
/project link Example illustrates Main
/project link Paragraph documents Main
/project mark Helper internal
/project publish Main --scope scope --output first-draft
```

Item and scope selectors are stable IDs or `ID@FULL_SHA256`, never guessed names.
`--scope` and `--output` are required. Output is a single validated child name
under the active workspace's `publications/` directory. A fresh bundle contains
the frozen plan/draft in `publication.json`, `writeup.tex`, and `compile.log`;
the existing document owner publishes its PDF on successful compilation.
Compilation failure retains the draft and diagnostics. An existing bundle is
refused, including after restart, so later publication cannot overwrite edits.

`mark` accepts `internal`, `public`, or `omitted`. Changing the visibility of a
currently admitted background/interface item is refused because the existing
trust policy pins its exact digest; this command does not migrate trust.
Repeated identical marks and links do not append redundant transactions, including
links whose source or target moved only through presentation metadata. A changed
prose statement remains distinct and retains its exact new source reference.
`documents` requires exposition/document-fragment source, and `illustrates`
requires an example. Both preserve exact endpoint references.

The existing `/project new`, `switch`, and `list` behavior remains covered, and
`/publish` remains the prompt shortcut. The command reports mathematical
readiness separately from compilation and prints the draft's explicit gaps.

## Identity and ownership

The TUI parses commands, session gates exclude active conversation turns and
provider workers, and the new interactive project owner receives only workspace
and a named publication operation. It delegates to `LedgerStore`,
`plan_publication` and `PublishWorkflow`; it never receives the session.

The planner applies current visibility/role metadata to an older exact item only
when every other model field matches. Statements, context, provenance, evidence,
dependencies and scope references remain exact. `presentation_revisions` freezes
the old item, applied metadata reference, and effective visibility/role values in
the plan; the assembler renders the applied role. A changed statement
never supplies metadata for old mathematics.

For a bare publication selector, a semantically identical scope-pinned target
retains its exact reference even when presentation metadata advanced. Explicit
digest selectors retain their exact meaning. Prose/example links made before or
after a metadata edit can document the same mathematical subject; applied exact
relations are frozen in `attachments`, and prose retains both its documented
target and selected target. Newly attached examples contribute their exact
prerequisites to the shared evidence audit and container placement.

Cancellation signals tracked compiler processes and keeps the command and its
Esc control active until the worker releases the session gates. Repeated stops
escalate. The next conversation cannot encounter a publication worker left behind
after the handler has returned.
The shell owns resume at command admission: publication does not clear an Esc or
second-Esc escalation already entered in the same input batch. Plain mode resumes
at its direct command entry because it has no batch key dispatcher.

Theory: presentation selects how exact mathematics is displayed, while capability
evidence continues to authenticate its original references. The equality rule
reuses immutable project records instead of introducing another metadata store;
a future selection flag belongs in the existing publication request. The model
assumes recorded semantic links are meaningful and does not infer missing prose,
dependencies, or mathematical faithfulness from text.

## Verification

The first test gate observed the missing operations and linked-helper visibility
failure. Later red tests reproduced admitted-item trust invalidation, Windows
backslash parsing, missing-session handling, early cancellation return, and
mark-then-link attachment loss. Implementations were added only after those
failures were observed.

The broader scoped gate covered existing project commands and module boundaries:

```powershell
uv run --extra test pytest tests/test_project_publication.py tests/tui/test_project_publication.py tests/unit/test_publication.py tests/unit/test_publication_structure.py tests/unit/test_publish.py tests/tui/test_project_command.py tests/unit/test_module_boundaries.py -q --tb=short
```

**135 passed in 10.06 seconds.** Later review fixes covered effective-role
rendering and repeated links across source/target metadata revisions. Their final affected gate:

```powershell
uv run --extra test pytest tests/test_project_publication.py tests/tui/test_project_publication.py tests/unit/test_publication.py tests/unit/test_publication_structure.py tests/unit/test_publish.py -q --tb=short
```

**73 passed in 5.21 seconds.** Ruff passed on the two Python files changed for the
final source-side idempotency fix. Tests use actual session, ledger, planner,
assembly and document-gate owners with trusted scripted compiler/provider
fixtures. This is not a full hermetic-suite, real TeX, live model, or confinement
result. `git diff --check` found no whitespace errors in the tracked E3 edits.

Independent review reproduced attachment-order, cancellation-teardown, repeated
link and role-rendering failures; each received a failing regression before its
fix. Final review and repository-wide landing checks are owned by the
coordinating task.

The subsequent full Windows coverage run exposed a timing issue in three existing
CAS Escape tests. Each exited on Ctrl+C as soon as the interrupt counter changed,
before the command returned, so legitimate app-exit cancellation delivered a second
interrupt. The three tests now wait for explicit command completion before exiting;
production CAS cancellation was unchanged. A control run reproduced the issue with
the three tests alone under coverage, independently of E3.

A real-shell regression additionally found that publication's former handler-level
resume downgraded two same-batch Escape presses from kill to interrupt. Removing
that late resume for the shell preserves both stop levels; a separate regression
retains direct-entry resume in plain mode. Final bounded coverage replay:

```powershell
$env:COVERAGE_FILE = Join-Path $env:TEMP ('hardy-e3-turns-' + [guid]::NewGuid().ToString('N') + '.coverage')
uv run --extra test pytest tests/tui/test_project_publication.py tests/tui/test_turns.py -q --tb=short --cov --cov-report= --cov-fail-under=0 -p no:cacheprovider
```

**43 passed in 14.11 seconds.** Coverage data used a unique temporary file and did
not overwrite the coordinator's full-run data or reports. Ruff and whitespace
checks passed for the three affected Python files.
