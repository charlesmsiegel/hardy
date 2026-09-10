# E1 synthetic referee manuscript acceptance

E1 is implemented as a bounded automated workflow fixture under V0. This accepts
software composition over authored semantic readings; it does not establish
automatic mathematical reading or live-model referee performance.

## Fixture and result

The [manuscript](../../../tests/fixtures/referee_manuscript/manuscript.tex) contains
12 mapped claims: 11 on the selected main-result/dependency paths and one unused
side lemma. The [background source](../../../tests/fixtures/referee_manuscript/background.tex)
contains the two exact synthetic citation statements. The
[integration tests](../../../tests/integration/test_referee_manuscript.py) exercise
real Referee, inventory, Critique, ledger persistence, graph and acceptance policy.

| E1 case | Observed acceptance behavior |
| --- | --- |
| Correct theorem | No defect; synthetic exact proof authority makes only this claim verified; revocation removes verification |
| Correct citation | Exact finite-space source digest/locator/version retained; checked only after synthetic source/faithfulness authority and a policy decision |
| Missing citation hypothesis | Closedness remains a named open child obligation; attempted acceptance is refused |
| Circular proof | Both members receive a structural informal-step obligation naming the exact cyclic component |
| Unsupported prose | Scripted adversarial finding records the alternating bounded sequence counterexample and persists as ordinary work |
| Irrelevant side theorem | Inventoried/mapped, explicitly unselected and outside the critical path |
| Silent local hypothesis drift | Finite-space proof context differs from the original arbitrary-space context |
| Notation shadowing | Proof M binds the singleton zero instead of the declared natural numbers |
| Unjustified WLOG/transport | Compact replacement has no authenticated preservation justification |
| Harmless weak shorthand then stronger use | Binary-operation passage has no representation gap; later basis passage requires the undeclared vector-space representation |

Before synthetic acceptance, the report has 11 selected, zero verified, zero
formalization probes and 11 unresolved claims. No inventoried claim/citation is
unmapped; no mathematical dependency claim is unreviewed. The sole unselected
claim is `side`. Kernel and formalization review are skipped. The scripted
adversarial operation is explicitly injected. Without it, all three review layers
are skipped, structural findings remain structural, and no unsupported-prose
finding is manufactured. Citation depth is zero and recursive completeness is
false. The report states semantic reading completeness is unverified.

The restart test preserves accepted exact contracts, pending closedness and
findings without adding duplicate ledger transactions. Removing the synthetic
evidence reader's receipts removes both theorem verification and citation checking.

## Change and verification

The new fixture exposed a missing structural check: Referee selected cyclic
dependencies but did not name them as findings. The only production change is
in `src/hardy/workflows/referee.py`: reuse the ledger graph's strongly connected
components and recognize singleton self-edges. Existing Critique persistence and
coverage attribution handle the resulting ordinary informal-step findings.

Red phase: after correcting a test-only restart-path typo, the four new tests
produced **3 failed, 1 passed**. The failures were the absent two-member cycle
findings, absent cycle findings without a semantic reader, and absent self-cycle
finding. The minimal production change then passed the complete focused set:

```powershell
uv run --extra test pytest tests/integration/test_referee_manuscript.py tests/unit/test_referee.py tests/unit/test_referee_recursive.py tests/unit/test_critique.py -q -m 'not real_toolchain and not live' --tb=short
# 60 passed in 16.11s
uvx ruff check src/hardy/workflows/referee.py tests/integration/test_referee_manuscript.py
# All checks passed!
```

After import formatting and test type-annotation cleanup, the new integration
file was rerun separately: **4 passed in 5.43s**. The production diff also passed
`git diff --check`.

Tests used the existing uv environment with execution approval because restricted
processes could not access its Python installation/cache. No environment rebuild,
lockfile edit, real model call, Lean invocation, TeX compilation or paper download
was performed. uv emitted the existing mismatched `VIRTUAL_ENV` warning and used
the project's environment. Source checkout before this work:
`ec628f18cf8e67cf10b33124a896e0f54dae0112`.

Fixture file SHA-256 values at creation (UTF-8, LF):

- `manuscript.tex`: `949a63bafac0a416e654093fa7d529e42145fa6741d6a5cda29fe2b132b911f6`
- `background.tex`: `8da4ac1821f5d8355a3f204a54fa17511cc52c2f6934e98bacf74b04c68e11a8`

## Theory and remaining limits

Theory: exact authored readings link manuscript spans to project mathematics;
Referee checks their recorded structure and reports authority/coverage separately.
Reused: inventory, LedgerGraph SCC, Critique, LedgerStore and LedgerPolicy.
New concept: none; the fixture supplies existing boundary contracts.
Assumes: the scripted readings faithfully describe these authored passages.
Cost: one additional linear SCC traversal per selected claim, within existing
per-claim graph/store traversals; no new external operations.
Watch: lexical inventory and recorded graph completeness cannot establish that a
real manuscript has no additional semantic defects or hidden dependencies.

The source/faithfulness reader and proof owner in this fixture are synthetic
authority doubles. The test never asserts that a real Lean kernel checked the
theorem. It also does not evaluate automatic citation comparison, automatic
context/representation extraction, live reviewer accuracy, execution isolation,
or the E0/E2 real-paper trials. Those are separate measurements.
