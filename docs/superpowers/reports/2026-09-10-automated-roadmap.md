# Automated roadmap acceptance

Implemented E1, E3 and E4, completed the current X4 residual acceptance, and
extended V0 fixtures. TODO records 58 of 63 implementation/lane items. E0/E2
remain deferred for human mathematical input; the S1 capability recheck still
does not establish confinement, S2 depends on it, and V0 remains ongoing.

## Source and item gates

The frozen implementation is `51f1aa061c2d2c728d37f6f2582de6478bd2b75e`, based
on main `ec628f18cf8e67cf10b33124a896e0f54dae0112`. Subsequent acceptance-report
edits do not change implementation or tests. Each item was committed separately
after its focused tests; review fixes retained separate E3/X4 commits.

| Item | Result and focused evidence |
| --- | --- |
| E4 | Persisted context, scoped research, blockers, trust and readiness in full status and compaction; 105 tests passed |
| E1 | Synthetic manuscript with ten planted scenarios and clean controls, exact coverage and restart; 60 tests passed |
| V0 regression | Filesystem refusal survives transcript refresh; 46 passed, 4 native-link skips on Windows |
| E3 | Publication, links and visibility commands; 135-test broad gate, then 73 passed after final presentation/link regressions |
| X4 | Ordered diagnostics, split marker handling, legacy capture refusal and standalone disclosure; broad gate 286 passed/6 skipped, final disclosure gate 56 passed/2 skipped including documentation checks |

Reports: [E4](2026-09-10-project-status.md),
[E1](2026-09-10-synthetic-referee.md),
[E3](2026-09-10-project-publication.md),
[X4](2026-09-10-cas-residuals.md),
[transcript refusal](2026-09-10-transcript-refusal.md),
[terminal cancellation](2026-09-10-terminal-cancellation.md),
[S1/S2 recheck](2026-09-10-isolation-recheck.md), and
[V0 fixture index](2026-09-10-acceptance-index.md).

## Integrated gates

The final Windows hermetic suite passed: **4,642 passed, 159 skipped,
38 deselected**, **90.52% coverage**, 736.53 seconds. The local command was:

```powershell
uv run --extra test pytest -q -m 'not real_toolchain and not live' --cov --cov-report=xml --cov-report=html --cov-report=term --tb=short
```

The first Linux full run found one prompt-ownership failure (4,777 other tests
passed). Moving shared project help to `prompts/terminal.py` preserved its
commands and passed 97 prompt, terminal and module-boundary tests.
The first completed Windows coverage run found three existing terminal
fixtures exiting before their Escape-interrupted worker settled (4,636 passed,
159 skipped, 38 deselected; 90.48% coverage). Explicit command-completion waits
fixed the fixtures. Additional actual-shell regressions preserved same-batch
publication stop escalation and plain-mode resume; the final focused coverage
gate passed 43 tests. Repository Ruff passed. A freshly built wheel installed into a new environment
outside the checkout passed assets, entry-point help, deterministic runs, the
SymPy helper and MCP stdio on the frozen implementation. It uses trusted
scripted fixtures; this smoke test does not invoke Lean, TeX or a model.

The final-source [Tests workflow](https://github.com/charlesmsiegel/hardy/actions/runs/34491150557),
[CAS workflow](https://github.com/charlesmsiegel/hardy/actions/runs/34491150574), and
[installer workflow](https://github.com/charlesmsiegel/hardy/actions/runs/34491150514)
all passed on the frozen implementation:

- Linux hermetic suite: **4,786 passed, 45 skipped**, **90.62% coverage**, 539.93 seconds.
- Real Lean audit: **6 passed** in 2.86 seconds.
- Real Singular/Macaulay2: **12 passed** in 12.76 seconds.
- Installed-wheel packaging and all Linux/macOS/Windows installer checks passed.

Independent review of the final cancellation change found no blockers. The
final documentation update receives its own architecture-document test gate;
implementation and test trees remain identical to the fully tested revision.

## Local evaluation artifacts

The first local full-suite attempt found an ignored baseline for a different
corpus (`f2f22622...` versus this branch's `bdf6ef50...`) and obsolete procedure
identity. Its optional local-evidence tests correctly refused it. No recorded
digests or measurements were rewritten, and no broad Lean sweep was started.

The unchanged baseline is preserved at
`evals/archived/baseline-before-roadmap-20260910.json`, SHA-256
`823cf28507cb983a0262748f896160a656c9b0e49dc2f15f646e6fdeedc4d3fb`.
Local scoreboards remain untouched. Without a current `evals/baseline.json`, the
suite skips optional local-measurement validation exactly as a fresh checkout
does; committed corpus checks still run. An initial interrupted clean rerun
was superseded by the final run after the last review fixes.

## Acceptance limits

Terminal ledger evidence authentication remains unavailable: stored acceptance
is not proof. Publication reports mathematical readiness separately from
compilation, and the referee fixture supplies scripted semantic readings and
synthetic authority. These tests do not establish live model performance,
real-paper faithfulness, or a new confinement boundary. Merged CAS capture loses
stream origin and assumes synchronous flushed output before completion markers.

No corpus content was changed. Release milestones R1-R3 retain their full
integration and human acceptance requirements; item counts do not accept them.
