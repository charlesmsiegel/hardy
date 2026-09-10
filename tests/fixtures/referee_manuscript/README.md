# Synthetic referee manuscript

These authored TeX files are data only: the acceptance test never compiles them.
The two background results are elementary synthetic statements, not an asserted
source from arXiv. `fixture-v1` identifies this local source revision; the test
records its content digest and exact statement locator.

The semantic reading in `tests/integration/test_referee_manuscript.py` is scripted.
It records the proof dependencies, the hidden finite-space hypothesis, the
shadowed M binding, the unproved compact-space transport, and which representation
each V passage uses. The unsupported convergence argument is reported by a
scripted adversarial reader with the alternating sequence as a counterexample.
No semantic extraction or live model performance is measured.

| Case | Expected workflow result |
| --- | --- |
| `correct` | No gap; verified only while synthetic proof receipt authenticates |
| `citation_good` | Exact finite-space contract; checked only after acceptance |
| `citation_missing` | Closedness is an open named hypothesis; acceptance refused |
| `cycle_a`, `cycle_b` | Structural circular dependency findings |
| `unsupported` | Persisted scripted informal-step finding |
| `side` | Inventoried and mapped, outside selected dependency path |
| `drift` | Proof context differs from the declared statement context |
| `shadow` | Proof M differs from the statement's exact notation binding |
| `wlog` | Transport has no authenticated preservation justification |
| `weak`, `strong` | Weak addition shorthand has no representation gap; later basis use does |

Missing model/formalization/kernel review layers and depth-zero citation coverage
remain explicit. Synthetic owner receipts exercise policy binding and revocation;
they are not real kernel verification or independent mathematical review.
