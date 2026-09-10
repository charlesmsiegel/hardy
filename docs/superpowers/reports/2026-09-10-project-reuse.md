# H1: bounded durable-reuse measurement

The existing ledger, derived index and authenticated retrieval cover the six
scripted discovery/delivery cases tested here. This fixture does not justify a
separate proof-memory database. It does not measure live-model performance or
prove that every future reuse need is covered.

The fixture in `tests/unit/test_project_reuse.py` constructs a project, reopens
its LedgerStore, rebuilds and serializes its index, then compares retrieval off
and on with the same deterministic visible-context consumer. Capability-reader
tables remain scripted in the test process; this is a ledger/index restart,
not a whole-process or provider restart.

| Category | Retrieved material | Off | On |
| --- | --- | ---: | ---: |
| Lemma | Exact checked declaration, source artifact, environment and current policy acceptance | 0 | 1 |
| Concept | Stable concept through an active scoped alias | 0 | 1 |
| Representation | Exact representation with its semantic description | 0 | 1 |
| Context | Exact context, parent and scoped members before activation | 0 | 1 |
| Goal | Open research target, without a proof claim | 0 | 1 |
| Approach | Goal-linked current approach and an explicitly historical blocked reason | 0 | 1 |

Counts indicate whether the expected item reached the consumer. Goal and approach
share a query: these are six category expectations, not six independent theorem
trials. The fixture also checks alias shadowing, refusal of an out-of-context
local declaration, refusal after proof-evidence revocation, unchanged ledger
state during reads and reproducibility in two fresh directories. Provider and
Lean process calls are zero; no cost or theorem-success improvement is inferred.

[Machine-readable results](2026-09-10-project-reuse.json) retain exact targets,
delivered references, source/index identities and fixture/retrieval source hashes.
The JSON is regenerated after retrieval review fixes; the committed evidence must
match those hashes (UTF-8 text with newlines normalized to LF, so Git checkout
line endings do not change source identity).

Portable tactic/solver lessons already have F3's run-artifact and replay API.
That lifetime differs from semantic concepts, contexts, goals and approaches, but
the fixture reveals no additional category requiring a new subsystem. Broader
model trials may revise this decision. H2 records local exposure separately so
reusing prior results cannot masquerade as held-out performance.

```powershell
uv run --extra test pytest tests/unit/test_project_reuse.py -q
```
