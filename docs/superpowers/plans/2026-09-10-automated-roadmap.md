# Automated roadmap implementation plan

**Goal:** Complete E4/E3/E1 and advance X4 with repeatable automated acceptance;
keep V0 current and recheck the S1 infrastructure prerequisite.

**Architecture:** Existing ledger views own status, the publication planner and
assembler own drafts, and Referee owns manuscript findings. Terminal adapters
select exact stored identities and invoke these owners. CAS completion must use
an ordered protocol rather than inferred quiet periods.

**Spec:** `docs/roadmap.md`, X4, E4, E3, E1, V0, S1/S2; user authorized these
items on 2026-09-10. E0/E2 remain deferred.

**Constraints:** Code branch based on main; corpus unchanged. Separate tested
commits per item. No model calls or broad real-toolchain sweep. Full hermetic
coverage gate before fast-forward landing. Tests use trusted disposable fixtures.

- [ ] X4: reproduce late-stderr/prompt sequences in CAS subprocess tests, fix
  ordering in the sentinel owner, run related CAS tests, record real-tool availability.
- [x] E4: add `interactive/project_summary.py` over one `LedgerSnapshot` and
  `LedgerViews`; incorporate sections into `MathematicsSession._summary`. Test
  context/hypothesis separation, conjectures, blocked approaches, exact scope,
  citation/transport blockers, stale prose, restart and corrupt-ledger refusal.
- [ ] E3: add `interactive/project.py` operations and `/project publish`,
  `/project link`, `/project mark` handlers. Require exact unambiguous item IDs
  and explicit scope for publication. Use `plan_publication` and
  `PublicationPublisher` for fresh guarded bundles. Test actual command dispatch,
  history-preserving edits, stale output refusal and partial draft disclosure.
- [ ] E1: commit the synthetic flawed manuscript and expected findings; run
  real Referee composition with attributed scripted semantic reads. Assert both
  detected defects and clean controls, source coverage and restart behavior.
- [ ] S1/S2: read-only capability recheck; record any unresolved host prerequisite.
- [ ] V0/docs: refresh fixture index, roadmap, TODO and the four overview docs.
- [ ] Review changes, run focused tests before each commit, then full hermetic
  coverage, Ruff and installed-wheel smoke before landing with `--ff-only`.

The status and publication adapters default to unauthenticated ledger evidence.
They must never turn a recorded acceptance field into proof. A later capability
reader can supply authenticated policy without changing the UI's data model.
