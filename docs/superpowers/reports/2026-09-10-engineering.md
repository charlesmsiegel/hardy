# Engineering verification report

X0, X3 and X5 are accepted in their tested scope. X6 completes the residual audit
and supported batch journaling/adjudication path, with identity gaps retained.
X4 remains ongoing. Integration `0063fd1` includes verified Core G/H/I and
hardening work; reviewed gate fixes extend through `f76cb18`. Core E remains
deferred; S1/S2 remain unaccepted. No execution
isolation, immutable remote model identity or hard provider spend cap is claimed.

## Tested item commits

| Item | Commit | Verification before commit |
| --- | --- | --- |
| X0 ordered formal-save operations | `ae80543` | 159 passed, 3 skipped; independent 20-case review |
| X3 existing assumption-prompt ordering | `bc3cd6e` | 23 tests; [acceptance report](2026-09-10-assumption-prompt.md) |
| X4 prompt-output preservation | `57b21f1` | 268 passed, 9 skipped |
| X4 CAS writer lease | `da6c57c` | 180 passed, 4 skipped; 55 passed, 2 skipped; 14 root review tests |
| X4 recovery refusal | `70a77c2` | 131 passed, 4 skipped |
| X5 expected-spend admission | `09fcc2d` | 310 passed, 4 skipped; 31 root review tests |
| X6 durable batch attempt journal | `e3523c4` | 137 related tests; 39 root review tests |
| X6 attributed artifact adjudication | `1953381` | 51 root verification tests |
| Integrated history/hardening | `0063fd1` | 168 related tests; repository Ruff |
| X6 authentic foreign-toolchain fixture | `033ce4c` | 59 scoreboard/batch-recording tests |
| X6 authentic sketch judgment fixtures | `1743688` | 113 tests |
| X4 terminal versus live recovery | `f76cb18` | 34 interrupt/sentinel tests; 9 independent recovery/reload tests |

## What the evidence establishes

X0 names existing preflight, staged checks and post-commit publication operations.
Composition tests preserve name-before-axiom refusal, shared-library build before
staging, cached builds, successful publication and stage/commit/discard failures.
The refactor does not make the whole workspace one atomic filesystem transaction.
X3 verifies the existing approval presentation owner with concurrent streaming;
it changes neither trust policy nor the human approval requirement.

X4 conservatively retains ambiguous nonempty prompt-shaped output. A lifetime
OS file lock prevents two processes from writing the same CAS journal; close is
idempotent and terminal, crash releases ownership, and deferred kernel setup does
not silently forfeit the lease. Durable nullable `kernel_lost` metadata separates
known terminal rollback from unaccepted live/unknown cell mutations. Known lost
interrupts and legacy timeout/kernel-death records may rebuild accepted state;
live interrupted cells, swallowed interrupts and clipped sentinel successes
cannot disappear on recovery. A later death does not clear an earlier unaccepted
live cell; legacy unknown survival remains conservative across reloads.
These fixes do not settle late-stderr attribution or prompt timing,
and real Macaulay2 platform acceptance remains outstanding. X4 stays unchecked.

X5 binds one immutable expected-spend policy to API chat/batch, including auxiliary
readers, continuations, compaction, model changes and restarts. Each actual
serialized request is identified and admitted before dispatch; raw reported usage
settles before reply parsing. Missing usage and interrupted calls retain liability,
and actual overruns deny later calls. A quote estimates input tokens and reserves
the actual output cap; it cannot guarantee a hard total-token ceiling. Optional
tariffs derive cost and do not establish the provider's invoice. SDK backends,
staged Prove and evaluation declarations refuse the unsupported budget path.
The [README configuration example](../../../README.md) uses the actual policy
schema and names models explicitly; no current provider prices are implied.

X6 reuses existing source/configuration/toolchain/corpus identities, pooling and
artifact revalidation. A real child process exiting abruptly after an observation
leaves an attributed incomplete batch attempt with raw actual usage. A completed
attempt binds its exact manifest, observations, trajectory and result; changed or
missing declared evidence is rejected, and an occupied output directory refuses
before another model call. Legacy unjournaled records remain explicitly unknown.

The adjudication API freezes complete journaled batch artifacts, then appends
actor/time/reason/decision records under an expected-prior-head check. Concurrent
writers cannot both append against one head. Changed artifacts mark old entries
stale while retaining history; corrupt/torn history refuses new appends. A real
child exit after append leaves a readable review. Canonical verdicts and kernel
evidence are untouched. Actor and problem/repeat labels are caller declarations,
not external identity authentication. This first owner refuses legacy/staged
attempts without the required batch identity.

The X6 audit leaves remote immutable model revision, installed provider SDK
identity and full local worker/runtime closure unestablished. On Windows,
`sys.executable` may name a venv launcher rather than the Python DLL or standard
library. Its byte digest and a model alias do not close those gaps. X6's checkmark
records the completed audit and supported path, not full immutable identity.

## Final landing gate

The first full hermetic gate on `0063fd1` failed: **4 failed, 4,499 passed,
158 skipped, 36 deselected; 90.14% coverage**, in **652.97 seconds**.
Log: `%TEMP%/hardy-engineering-final-hermetic.log`.

One failure exposed a production regression: the recovery refusal also rejected
an interrupt whose kernel was known to have died. `f76cb18` records the distinction
durably and verifies live/terminal/reload behavior. Two fixtures modified completed
artifacts after X6 authenticated their journals; a third expected to reuse a
completed attempt directory, which X6 deliberately refuses. `033ce4c` and
`1743688` now generate the intended conditions through actual attempt owners,
preserving strict journal validation.

The fresh full hermetic gate passed on frozen source `f76cb18`:
**4,511 passed, 158 skipped, 36 deselected; 90.15% coverage** (82% required),
in **674.26 seconds**. Log:
`%TEMP%/hardy-engineering-final-hermetic-r2.log`.

The [history mapping](2026-09-10-linear-history.md) records rewritten equivalent
`b1de4f8`; this gate ran on original source `f76cb18`, not on every rewritten
intermediate tree. The integrated evaluation gate remains a separate requirement.

```powershell
uv run --extra test pytest -q -m 'not real_toolchain and not live' --cov --cov-report=xml --cov-report=html --cov-report=term --tb=short
```

The fresh `f76cb18` wheel build and installed smoke passed under Python 3.13.11
outside the checkout, checking packaged assets, CLI help, deterministic runs,
the CAS helper and MCP stdio. This supersedes the earlier `0063fd1` wheel check.
The hermetic environment uses locked dependencies; wheel installation resolves
declared dependency ranges. Current wheel environment:
`%TEMP%/hardy-engineering-r2-wheel-4ab98b8f11d043c68e59954d4910f7d8`.
