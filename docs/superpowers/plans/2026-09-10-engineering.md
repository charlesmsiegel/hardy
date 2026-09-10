# Remaining independent engineering plan

The roadmap is approved and Core E remains deferred. This worktree starts from
verified Core G. Root owns commits, documentation and the final landing gate.

- [ ] X0: expose the Lean save gates as named ordered operations, preserving
  refusal text, source/name/axiom policy, staging, commit and discard behavior.
  Test actual owner composition, including every refusal and commit failure.
  Do not broaden existing filesystem atomicity claims without implementing them.
- [ ] X3: verify the current assumption-prompt concurrency fix against its closed
  issue and record tested status. Only change source for a reproduced residual.
- [ ] X4: reproduce remaining CAS findings in issues 36/37 against current code,
  preserving already-fixed behavior. Fix proven accounting, bounds, recovery,
  export/concurrency and provider-stage access failures in separately tested
  commits. Record platform-limited evidence explicitly.
- [ ] X5: introduce reserve/settle token and cost limits only at harness-owned
  provider decision points. Record reservations, actual spend, missing usage and
  the ending limit; no provider call may bypass the declared budget path.
- [ ] X6: audit remaining configuration/source/runtime/toolchain/corpus identity,
  attempt journal durability and append-only adjudication. Reuse existing eval
  digests, pooling and artifact revalidation rather than replace them.

Each item and review fix gets a tested commit. Integrate prior verified branches,
then run repository Ruff, full hermetic coverage and wheel smoke on frozen source
before landing. Update roadmap/TODO and all four overview documents together.
S/V retain their own acceptance and platform requirements and follow eligible work.
