# Corpus changelog

All notable changes to the corpus. Entries cite ids.

Each head line binds the **manifest digest** — a hash over every content file —
so an edit that leaves both version strings in place still fails
`hardy evals corpus check`.

## 0.2.1 - 2026-09-04 - manifest bdf6ef50656e358b3261838d6a62866b6480cc02a6513884041f5dd27f2417d0

- All five irrationality entries are now `11J72` ("Irrationality; linear
  independence over a field"). `sqrt-two-plus-sqrt-three` and its twin were
  `12Fxx` while `sqrt-two-irrational`, `sqrt-six-irrational` and
  `cube-root-two-irrational` were `11Jxx` — the same kind of statement filed
  under two different fields, which is exactly the error the classification is
  supposed to be trusted about. `11J72` names the result rather than the proof
  technique, and its "linear independence over a field" covers the
  field-theoretic flavour of $\sqrt{2} + \sqrt{3}$.
- `problems/12.json` is gone: the two entries move to shard 11 with their
  code, and MSC 12 leaves the corpus. The `field-theory` reporting group
  existed only for them.

## 0.2.0 - 2026-09-04 - manifest a062377273b798e7b3dfd092ed32ce04095b43de6cdf8dc84716185704ac48d6

- **The MSC2020 table is complete**: all 6603 codes from
  `https://msc2020.org/MSC_2020.csv`, in the form MSC publishes them, each with
  its own name. It was nine hand-written codes before, which meant a correct
  tag outside that handful was reported as an unknown code.
  `scripts/vendor_msc2020.py` regenerates the taxonomy so a re-vendoring is
  reproducible rather than a hand edit.
- **Entries now carry published code forms.** `11A` was never an MSC2020 code;
  the section is `11Axx`. The twenty migrated entries move to `11Axx`, `11Jxx`,
  `12Fxx`, `20Axx`, `20Dxx`, `26Dxx`. A code must now name a section (`12Fxx`)
  or a subsection (`12F10`): `12-XX` is the bare class, and `12-01` classifies
  a publication type rather than mathematics.
- **The arXiv and reporting-group tables cover all 63 classes**, and resolve
  most-specific-first — whole code, then section, then class. MSC classes are
  not homogeneous under an arXiv reading, and MSC 12 is the worst case: it
  spans math.NT (Galois theory), math.AC (valuation theory), math.RA
  (near-fields, skew fields) and math.LO (model theory of fields). A
  class-only table filed a third of that class under the wrong archive.
- **MSC 12 derives `math.NT`, not `math.AC` or `math.RA`.** arXiv's own
  math.NT description names "Galois theory", and 12E/12F are the bulk of the
  class. 12D/12H/12J override to math.AC, 12K and 12E15 to math.RA, 12L to
  math.LO. (0.1.2 briefly set the whole class to math.RA; that was decided
  before reading arXiv's definitions and is superseded here.)
- Reporting groups are deliberately coarser than the classes — a ranking per
  2-digit class would be dozens of underpowered comparisons — while keeping
  the four the corpus targets distinct: commutative algebra, real analysis
  (MSC 26 and 28 together), group theory, linear algebra.

## 0.1.2 - 2026-09-04 - manifest 1da02be51aed5ac13d1d3092a4d9c9fab0b3c7c22b17a07cfd26989095d094cf

- MSC 12 (field theory and polynomials) now derives `math.RA`, not `math.AC`.
  math.AC's practical identity is Noetherian and homological commutative ring
  theory — local cohomology, Cohen–Macaulay, monomial ideals, Gröbner bases —
  and field theory is not that. math.RA is where 12G (Galois cohomology, Brauer
  groups) and 12K (near-fields, which are not even commutative) actually go.
  The derivation is a default, and 12 is unusually split: much 12E/12F content
  lands in math.NT in practice, and 12L belongs in math.LO. Those are what
  `arxiv_override` is for, and it will carry more weight for 12 than for any
  other class here.

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
