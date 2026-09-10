# Evaluation roadmap implementation plan

V1-V3 item implementation and independent review are complete in their documented
scope; V0 remains ongoing. Rebased source `29275af` includes the linear engineering
tree and passed 213 integration tests plus repository Ruff. Its full hermetic gate
passed: 4,569 tests, 158 skipped, 36 deselected, 90.08% coverage in 696.75 seconds.
Fresh installed-wheel smoke passed. See the
[evaluation report](../reports/2026-09-10-evaluation.md) for exact limits and
original-to-rebased commit identities.

Core E remains deferred. V0 acceptance fixtures accompany each primitive. V1
adds a read-only chronological view over authenticated scoreboards using X1's
existing exact-slot comparison and control audit. Differences remain descriptive;
historical movement never establishes attribution to a code or model change.
V2 depends on stable budget/identity receipts, and separates provisional from
certified fixed-budget pass@k with explicit measurement coverage. V3 imports
external benchmark source bytes with pinned upstream identity and provenance;
it does not add corpus statements to the code branch.

Each item receives meaningful tests and its own commit. Reuse current board
validation and identity owners, retain missing measurements, and run the full
hermetic coverage gate, lint and installed-wheel smoke before landing source.
