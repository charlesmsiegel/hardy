# Corpus changelog

All notable changes to the corpus. Entries cite ids.

Each head line binds the **manifest digest** — a hash over every content file —
so an edit that leaves both version strings in place still fails
`hardy evals corpus check`.

## 0.1.1 - 2026-09-03 - manifest eff83575a64948834b698725c9e84a8b3e67f032b54901b8510cbdd9ae76bbc3

- `pigeonhole-residues`: restore the positivity premise in `input`. The prose
  read "among any $n+1$ integers", but the Lean carries `hn : 0 < n`; at
  $n = 0$ there is one indexed integer, so no two distinct indices exist and
  the prose asserted something false about a statement that is guarded. The
  prompt and the declaration must describe the same theorem, or a
  faithfulness review approves one while the model is shown the other.

Only `input` changed, so `prompt_digest` moves and `statement_digest` does
not: the A-group measurements for this entry survive the correction.

## 0.1.0 - 2026-09-03 - manifest 0556793f3926a5395553fcaad369a864ebc51209880ba5f12a8523eae77e8dbd

- Initial corpus: the twenty entries migrated from `evals/problems.json`,
  hand-assigned MSC codes and difficulty, `input` rewritten with inline LaTeX.
- Every migrated entry is `candidate` with `witness: null` and a note: they
  predate A6, and inventing witnesses during a mechanical migration would be
  worse than recording the debt.
