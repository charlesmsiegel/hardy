# Interactive state owners: implementation report

Task 4 of the modular refactor plan is implemented behind the existing
`MathematicsSession` caller surface. No corpus files or artifact schemas changed.
The classes are ordinary collaborators, with no mixins, dynamic forwarding, or
stored `MathematicsSession` reference.

## Ownership

- `workflows/interactive/record.py`: `SessionRecord` owns version-2 state loading,
  local state, guarded writes, transcript events and prefix identity, resumable
  thread binding, ledger cursors, crash-tail recovery, and detached snapshots.
  Named operations publish audit, automation, writeup and spend values; admit,
  revoke and locate approved assumptions; and record quarantine. Values entering
  mathematical state are copied so a capability cannot mutate the record through
  an alias it retained. `state` and `local` on the coordinator remain compatibility
  accessors for the orchestration that still assembles the record.
- `workflows/interactive/formal.py`: `FormalWorkspaceService` owns the Lean
  workspace reference, check/save streak state, source-identity vouchers, the
  staged save/commit/discard operation, dependent rebuilds, source admission
  gates, axiom-audit decisions, audit freshness, automation probing and cached
  disclosure expiry. `SavePolicy` names cross-capability gates and publication
  callbacks. The source is committed before evidence is published; failed builds
  or audits leave the live tree untouched.
- `workflows/interactive/admission.py`: `AssumptionAdmission` owns inspection
  attempts/results, rejected proposals, consuming evidence once per request,
  ordinary approval and paper admission, reader-disagreement quarantine, and
  provisional admission rollback when the generated module save refuses.
  `AdmissionOperations` supplies the specific probe, reader, approval, library,
  and record operations. No paper is minted when the reader is unavailable.
- `workflows/interactive/documents.py`: `DocumentService` owns guarded source
  discovery, normalized paths, compilation/save publication, compiler labels,
  bibliography key checks, tree/signature computation, writeup stamp publication,
  and completion aggregation. It receives bibliography keys and named operations;
  it holds no paper client. `FormalDocumentFacts` carries the formal evidence
  needed for completion checks. The source-write callback still runs before PDF
  and auxiliary publication.
- `workflows/interactive/turns.py`: `TurnCoordinator` owns the tool gate,
  cancellation/result events, spend lock, tool tally, synchronous turn reset,
  stream teardown, cancellation/escalation, serialized dispatch and result
  recording, exactly-once usage folding, and compaction cut/provenance recording.
  `TurnPersistence` supplies only history and usage operations. Closing a consumer
  shuts the tool gate before closing the provider iterator.

`chat.py` now imports `ChatRuntime`, `final_text`, and `provenance` from
`agents.contracts`; their old imports remain available as compatibility exports.

The coordinator still assembles shared-library/environment inputs, supplies
formal probe execution and independent-reader callbacks, handles workspace
listing/import/export and report orchestration, and connects cross-capability
authorship/documentation gates. These are explicit dependencies passed to an
operation, rather than an unrestricted object retained by a collaborator.

## Concurrency and identity

The existing lock objects moved to their respective owners and are exposed by
compatibility references on the session. Their order is unchanged: dispatch holds
the tool gate before an operation may acquire the record write lock; usage holds
the spend lock before persisting the ledger/cursor. No independent collaborator
lock was added around a formerly atomic save. Cancellation events remain shared
with provider/tool threads, and per-turn reset stays synchronous with `stream()`.

These source moves invalidate source-based experimental identities. No old
evidence was rewritten or restamped. No live model, Lean sweep, or real-toolchain
test was run by this task. Execution remains unsandboxed under the existing limits.

## Verification

Environment: `.venv-refactor/Scripts/python.exe`, CPython 3.14, `PYTHONUTF8=1`.
Each command used a unique `--basetemp` under the writable Windows Temp directory.
Live and real-toolchain cases were excluded with
`-m "not real_toolchain and not live"`.

- Record/thread/cancellation milestone: 62 existing tests plus 2 initial owner
  tests passed.
- Admission milestone: 138 tests passed across `test_chat_assume.py`,
  `unit/test_assumption_evidence.py`, and `unit/test_assumption_gates.py`.
- Document milestone: 86 passed, 8 skipped, 11 pre-existing completion failures.
- Turn milestone: 102 tests passed across usage, cancellation races, compaction,
  steering, and fresh-thread tests.
- Broad interactive regression: 474 passed, 15 skipped, 19 baseline failures.
  All 19 were separately reproduced with the untouched original `chat.py` loaded
  as `hardy.chat`: 4 audit and 11 completion stale-writeup cases, and 4
  project-context cases asserting LF-only text/byte counts on Windows.
- Formal freshness/disclosure follow-up: 90 tests passed across owner contracts,
  automation disclosure, workspace and steering tests.
- The final direct owner suite has 7 passing tests. It exercises detached nested
  snapshots and admitted values, crash-tail recovery, an altered-assumption
  refusal, failed source publication, generated-save approval rollback, and a
  cancelled dispatch that records its refusal without running the tool.
- Final combined verification after the last code commit: 115 passed across the
  owner, automation disclosure, usage, cancellation, and compaction suites.

`tests/conftest.py` now redirects the paper-tool throttle to each test's temporary
directory. The original paper fixtures otherwise tried to write the operator's
real `~/.hardy/papers` throttle; this was diagnosed by reading the failed
`fetch_paper` result. The steering unreadable-source test now patches the document
owner's reader, where that responsibility lives after extraction.

Full-suite coverage, wheel verification, global dependency fences, and the Windows
baseline repairs belong to the parent integration task. During concurrent
literature extraction, the old `hardy.assume.MAX_STATEMENTS` monkeypatch stopped
controlling statement inventory; the parent has moved that patch to the canonical
`literature.statements` owner.

## Theory note

Theory: services decide bounded operations; the record owns durable facts and
turns own sequencing. Instead of: moving methods into a session-shaped proxy.
Reused: `WriteGuard`, `LeanWorkspace`, `LatexTools`, `Usage`, completion and
compaction. New concepts: named operation sets and detached formal/document facts.
Assumes: the existing one-interpreter process register and external-file race
policy. Cost: copies of mathematical record values at capability boundaries.
Watch: compatibility accessors still allow coordinator mutation; extending a
service should add a named operation or value rather than a session reference.

Commits: `c4a74c0`, `56ce53c`, `f69db99`, `39ffb7c`, `bea3776`, `21f6cb3`,
`ab4a7cd` (plus this report).
