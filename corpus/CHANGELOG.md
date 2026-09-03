# Corpus changelog

All notable changes to the corpus. Entries cite ids.

Each head line binds the **manifest digest** — a hash over every content file —
so an edit that leaves both version strings in place still fails
`hardy evals corpus check`.

## 0.1.0 - 2026-09-03 - manifest 0556793f3926a5395553fcaad369a864ebc51209880ba5f12a8523eae77e8dbd

- Initial corpus: the twenty entries migrated from `evals/problems.json`,
  hand-assigned MSC codes and difficulty, `input` rewritten with inline LaTeX.
- Every migrated entry is `candidate` with `witness: null` and a note: they
  predate A6, and inventing witnesses during a mechanical migration would be
  worse than recording the debt.
