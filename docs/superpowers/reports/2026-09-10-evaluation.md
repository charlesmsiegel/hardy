# Evaluation verification report

V1, V2 and V3 have tested commits; the integrated landing gate on `29275af`
passed. V0 is an ongoing acceptance obligation, indexed in
the [acceptance fixture map](2026-09-10-acceptance-index.md). Core E remains
deferred. This report records software behavior and source-import coverage, not
model improvement, mathematical benchmark success or execution isolation.

## Tested item commits

| Item | Commit | Verification before commit |
| --- | --- | --- |
| V1 chronological regression view | `794ca29` | 140 author tests; 15 root review tests |
| V2 prospective observed-first-k certification | `894765b` | 302 root related tests; 28 independent review tests; 43 root certification/history tests |
| V3 pinned external benchmark imports | `07d8e67` | 111 author tests; 79 root review tests; three real pinned archive imports |

The table preserves the original tested commit identities. After rebasing onto
the tree-equivalent linear engineering history, V1 is `13b6ae9`, V3 is `7c79a53`
and V2 is `29275af`; the V0 index is `88642b4`. Original histories are retained
in archival refs. The [history report](2026-09-10-linear-history.md) records exact
tree/patch correspondence. Rewriting parentage is not a new test run.

## V1 chronological comparisons

`evals/history.py` reuses X1's artifact authentication, exact problem/repeat
pairing, control checks and usage coverage. It orders recorded timestamps and
compares adjacent observations without pooling them. Missing/unreadable boards,
unknown chronology, overlapping run intervals and uncontrolled differences remain
visible. Duplicate paths or identical scoreboard bytes cannot count as independent
observations. True-statement proof gains/losses remain separate from false-statement
outcomes. Bounds cover board count and input bytes, and changed inputs refuse.

Timestamps describe the records rather than an independently authenticated clock.
A historical delta cannot establish attribution to a code, prompt or model change
without a contemporaneous control. These deterministic fixtures establish the
comparison reader's behavior, not a measured improvement in theorem proving.

## V2 certification acceptance

`run_set(certification=CertificationBudget(independent_verifier_calls=N,
ks=(1, k)))` seals the selected universe, conditions, budgets and slots before
execution. Per-attempt context is persisted before provider work and bound to
the actual staged or batch record. The read-only `certify` operation requires
the exact declared corpus and baseline; changed inputs raise
`CertificationInputsChanged` rather than silently shrink the denominator.

The certified statistic is observed success within each problem's first k
declared attempts under a per-attempt frozen-claim independent-verifier-call cap.
It is not an IID pass@k estimator, confidence interval, cap on every Lean process,
or independent kernel replay. Eligible staged attempts retain the existing
recorded Lean/canonical trust boundary. Batch receipts cannot establish this
independent-verifier-call cap and remain provisional. Missing prospective context,
incomplete or reused attempts and missing evidence remain explicit. An actual
completed unsolved attempt contributes zero rather than disappearing.

Reports retain provisional reasons, observed bounds, failure categories,
per-domain results and usage coverage, separating proof and canonical-review
usage. Lean CPU time remains unknown. Scheduler makespan covers producer start
through board seal; worker occupancy is executor time, not CPU utilization.
Hard provider token/invoice limits remain unestablished: requesting such a
certificate yields explicit provisional reasons, even if X5 expected-spend
reservations exist. These tests establish prospective recording and audit
behavior; no new live-model benchmark performance result is claimed.

## V3 pinned source import experiments

The importer stores exact archive/source bytes and indexes original lexical
statement spans or explicitly decoded JSON values. It retains upstream imports,
support files, toolchain metadata, split labels and license-file digests. It does
not port Lean, normalize statements, adopt corpus entries, execute source or
certify a runnable environment. Commit attribution is caller-supplied; the archive
SHA-256 is checked. A supplied revision label alone is not independently proved
to identify the archive's remote origin.

Three pinned upstream archives were imported into disposable local storage:

| Profile / repository | Supplied revision | Files | Indexed statements / splits | Declared Lean |
| --- | --- | --- | --- | --- |
| miniF2F / `openai/miniF2F` | `f0dcc8b59e630fba00ba9569ca6714700e0a8801` | 1,315 | 488: test 244, valid 244 | 3.42.1 |
| PutnamBench / `trishullab/PutnamBench` | `b3e08943b1728842194fe2df693f02c763da4294` | 1,764 | 672: split unspecified | 4.27.0 |
| ProofNet / `zhangir-azerbayev/ProofNet` | `509ad79710ed4f46ff5c282ed5640c1aa9ac3f30` | 316 | 371: test 186, valid 185 | 3.50.3 |

| Profile | Archive bytes | Archive SHA-256 | Declared Mathlib revision |
| --- | --- | --- | --- |
| miniF2F | 188,715 | `298cfb25e8f7c065cbdc87c2516214772241bad8b9818653a15069c8c8da95ca` | `cb2b02fff213ed6f65bebd64446baac64137dcda` |
| PutnamBench | 990,483 | `8fe9f01232748328e524c334e61948b9914fa494508c5ec14df3b96da81d1b2a` | `a3a10db0e9d66acbebf76c5e6a135066525ac900` |
| ProofNet | 13,525,433 | `89672a919e334d1eef897581f3edb81fb189373c1ce78bde473fffffecd22bb8` | `cc8e88c7c8c7bc80f91f84d11adb584bf9bd658f` |

Each final import reported zero lexical coverage issues, semantic coverage
`unverified` and verification `not-run`. These are indexing counts, not solved
counts or evidence that the original statements elaborate in Hardy's toolchain.
The original Lean 3 datasets remain Lean 3 inputs.

The miniF2F and PutnamBench profiles declare Apache-2.0 and retain their respective
`lean/LICENSE` and `lean4/LICENSE` files, both with SHA-256
`cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
The ProofNet profile declares MIT and retains `LICENSE`, SHA-256
`01846beaa8adeceed591e968f4a878cc61eaafe2ccebad87c481c76fd43e83e2`.
These are recorded upstream declarations/file identities, not a separate legal
assessment. ProofNet initially refused a 38,403,285-byte ancillary training file
under an 8 MiB per-file storage bound; the accepted importer separates bounded
archive storage from the smaller benchmark index and the pinned retry succeeded.

Evidence directory: `%TEMP%/hardy-benchmark-integration-hwommcns`. Each dataset has
a `manifest.json` containing provenance, file and statement identities. The two
result files are `integration-results.json` and `proofnet-integration-result.json`.
All imported bytes remain in temporary storage; no corpus content was committed.

## Final landing gate

Integrated source `29275af` passed **213 related tests** in **53.79 seconds**
and repository Ruff. Its full hermetic coverage gate passed:
**4,569 passed, 158 skipped, 36 deselected; 90.08% coverage** (82% required),
in **696.75 seconds**. Log:
`%TEMP%/hardy-evaluation-final-hermetic.log`.

```powershell
uv run --extra test pytest -q -m 'not real_toolchain and not live' --cov --cov-report=xml --cov-report=html --cov-report=term --tb=short
```

Fresh wheel build and installed-wheel smoke passed under Python 3.13.11 outside
the checkout, checking packaged assets, CLI help, deterministic runs, the CAS
helper and MCP stdio. The hermetic environment uses locked dependencies; wheel
installation resolves declared dependency ranges. Wheel environment:
`%TEMP%/hardy-evaluation-wheel-723de004d6a541139aeccd9e12fa84e0`.

Item review and focused tests do not substitute for the full gate or establish
live-model gains, independent kernel replay or process confinement.
