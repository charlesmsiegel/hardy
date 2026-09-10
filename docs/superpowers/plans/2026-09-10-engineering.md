# Remaining independent engineering plan

The roadmap is approved and Core E remains deferred. This worktree starts from
verified Core G. Root owns commits, documentation and the final landing gate.

Item review is complete for X0, X3, X5 and X6's residual audit/supported batch
path. X4 remains ongoing for late-stderr attribution, prompt timing and real
Macaulay2 platform checks. Remote model revision, provider SDK and full runtime
identity remain unestablished. Integrated source `0063fd1` passed 168 related
tests and Ruff. Its full gate found one CAS recovery regression and three fixture
failures, resolved in reviewed commits through `f76cb18`. Its fresh full hermetic
gate passed: 4,511 tests, 158 skipped, 36 deselected and 90.15% coverage in
674.26 seconds. The corrected source also passed fresh installed-wheel smoke.
See the [engineering report](../reports/2026-09-10-engineering.md).

- [x] X0: expose the Lean save gates as named ordered operations, preserving
  refusal text, source/name/axiom policy, staging, commit and discard behavior.
  Test actual owner composition, including every refusal and commit failure.
  Do not broaden existing filesystem atomicity claims without implementing them.
- [x] X3: verify the current assumption-prompt concurrency fix against its closed
  issue and record tested status. Only change source for a reproduced residual.
- [ ] X4: reproduce remaining CAS findings in issues 36/37 against current code,
  preserving already-fixed behavior. Fix proven accounting, bounds, recovery,
  export/concurrency and provider-stage access failures in separately tested
  commits. Record platform-limited evidence explicitly.
- [x] X5: introduce reserve/settle expected-spend admission only at harness-owned
  provider decision points. Record reservations, actual spend, missing usage and
  the ending limit; no provider call may bypass the declared budget path.
- [x] X6: audit remaining configuration/source/runtime/toolchain/corpus identity,
  attempt journal durability and append-only adjudication. Reuse existing eval
  digests, pooling and artifact revalidation rather than replace them.

Each item and review fix gets a tested commit. Integrate prior verified branches,
then run repository Ruff, full hermetic coverage and wheel smoke on frozen source
before landing. Update roadmap/TODO and all four overview documents together.
S/V retain their own acceptance and platform requirements and follow eligible work.
