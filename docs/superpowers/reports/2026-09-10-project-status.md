# E4 ledger-aware status

`MathematicsSession.summary` reads one immutable ledger snapshot and appends
context, declarations, local hypotheses, notation, scoped open research,
approaches/dead ends, concepts/representations, blockers, item identities,
external permissions, citations, historical stale links and publication readiness.
The same summary reaches `/status --full` and compaction. Empty projects keep the
legacy summary. Corrupt ledgers refuse; omitted publication roots report their
refusal without hiding the rest of the project.

The renderer reuses LedgerViews and the publication planner. Stored acceptance
does not authenticate itself: the terminal currently has no configured ledger
capability reader, and says so. Scope permission is separate from actual used
external trust; every scope/root is identified exactly. Historical stale links
are labeled; the planner decides current publication readiness.

E4 regression gate: **105 passed** across project summary, real session summary,
compaction, terminal status, pure summary and ledger views. This exercises fake
external tools, real persistence/restart, stale citations/prose and corrupt-ledger
refusal. No live-model or new kernel result follows. Integrated full gate pending.
