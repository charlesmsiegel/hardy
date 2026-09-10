# Corpus changelog

All notable changes to the corpus. Entries cite ids.

Each head line binds the **manifest digest** — a hash over every content file —
so an edit that leaves both version strings in place still fails
`hardy evals corpus check`.

## 0.5.0 - 2026-09-08 - manifest 1fbd40238110339edf3e0efdcf96998ded4bbefd669810184266f491efd3f4e1

- Merge the 48 entries from corpus/putnam at 4159410 into corpus/curation at 108e573: 37 true statements and 11 false twins, with eight competition-paper sources from 2018 through 2025. Entry IDs and original source release notes are retained in the Putnam branch history below.
- The combined corpus contains 1,214 entries: 1,125 true statements and 89 false twins. All 1,166 pre-merge curation records, including their review and audit fields, are unchanged. All 48 Putnam additions remain unreviewed candidates; the existing 74 active entries retain their recorded reviews.
- Bind all merged shards and registries to version 0.5.0 and its manifest. This also records the curation harvests and faithfulness reads accumulated after its previous release. Earlier branch-specific versions and manifest digests remain historical identities, not identities of this merged dataset.
- Validation for this merge is mechanical: schema, references, permanent IDs, shard placement, manifest, and corpus/viewer regression checks. No Lean elaboration, witness verification, live model evaluation, or new human faithfulness review was performed for the merge. Existing corpus/EVALS.md is retained as its original recorded measurement snapshot; it contains no new Putnam results.

## 0.3.0 - 2026-09-05 - manifest 4c58a5d7911a0e9533fe5393e76815b78695cf6fa45615d5a93471dbc574d741

- First harvested entries, all commutative algebra from Atiyah-Macdonald chapters 1-9: 253 true statements and 28 false twins in `problems/13.json`, plus two vector-space items from chapter 6 under 15A03 in the new `problems/15.json`. Every entry carries a full five-character MSC2020 primary code. Body propositions are entries beside the exercises so the antecedent gate can find them by locator. Every statement elaborates against the pinned Mathlib (Lean 4.32.0, Mathlib 81a5d257); `zmod-tensor-coprime` carries a witness and the rest quantify over rings or modules and record why none of the checked shape exists. Twins carry a rationale naming the perturbation and no occurrence. `sources.json` registers `atiyah-macdonald`, `reid-ucai`, `matsumura-crt`, `eisenbud-ca` and `dummit-foote` with AMS citation fields, an alpha `citation_key` and a `locator_style` naming how each book's locators read; 74 secondary citations were added, numbered items only and only where the text states the entry or a stronger form. Two entries from exercise 1.10 add `Nontrivial`, which the book omits and the zero ring needs.

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

## Imported Putnam branch history

The following release notes are retained from `corpus/putnam` at `4159410`.
Their versions, manifest digests, toolchain checks, and counts describe that
branch before the merge, not the combined corpus. Its 0.3.0 and curation
0.3.0 were independent releases; 0.5.0 identifies the combined content.

### Putnam branch 0.4.0 - 2026-09-08 - manifest 1d4694acc86ccb112d8452dcbc383ce2b1615f9927a598003a477dce779f91c1

- The 2025 paper is now complete: all twelve problems are present. Twenty-five entries join the twenty-three of 0.3.0. New from 2025: `putnam-2025-a1`, `putnam-2025-a3`, `putnam-2025-a4`, `putnam-2025-a5`, `putnam-2025-a6`, `putnam-2025-b1`, `putnam-2025-b2`, `putnam-2025-b3`, `putnam-2025-b4`, `putnam-2025-b5`, `putnam-2025-b6`. A2 was already here as two entries, because it asks for two constants.
- Coverage of the earlier papers widens with `putnam-2024-a2`, `putnam-2024-b1`, `putnam-2024-b3`, `putnam-2023-b5`, `putnam-2023-b6`, `putnam-2022-b6`, `putnam-2021-a3`, `putnam-2020-a6`, `putnam-2019-a5` and `putnam-2019-b2`. Four new twins: `putnam-2025-a4-twin`, `putnam-2025-a6-twin`, `putnam-2025-b3-twin`, `putnam-2025-b6-twin`. `putnam-2025-b3-twin` drops nonemptiness, which is the premise that puts $1$ into $S$ and starts the induction; the other three move a constant or an exponent by the smallest amount that still makes the claim false.
- Three more shards: `39` (functional equations), `51` (elementary Euclidean geometry) and `91` (combinatorial games), bringing three reporting groups the corpus had not reached. `putnam-2025-a3` carries `arxiv_override: math.CO`, because MSC 91A derives math.OC -- right for the optimisation and economics that dominate game theory, wrong for a combinatorial game on $\{0,1,2\}^n$.
- `putnam-2025-a3` is the one entry whose Lean is a bespoke encoding rather than a transcription. A two-player game needs a strategy quantifier, and a strategy returning illegal moves satisfies a naive encoding vacuously -- so the statement carries an explicit legality clause beside the winning one. Its conventions were checked in Lean against the $n = 1$ game, whose only play is $0 \to 1 \to 2$: that list satisfies every legality clause, is stuck, and has odd length, which is the encoding saying Bob wins. A reviewer should look hardest here.
- All sixty-eight entries elaborate against Mathlib (`leanprover/lean4:v4.34.0-rc2`, mathlib4 master) with no errors, and all thirty witnesses kernel-check with `#print axioms` naming only `propext`, `Classical.choice` and `Quot.sound`. The eighteen `witness: null` entries each say why: most have no binders, `putnam-2019-b3` has an implicit `{n : ℕ}`, `putnam-2018-a4` an instance `[Group G]`, and the rest pin a function or sequence to a recursion or an enumeration that cannot be exhibited by a term without constructing it.
- Newly checked numerically: the 2025 A5 maximisers over $n \le 7$, the 2025 A6 valuations over $k \le 5$, the 2023 B6 determinant over $n \le 8$, the 2024 B1 rule exhaustively over $n \le 7$, the 2022 B6 functional equation, the 2021 A3 tetrahedra over $N \le 40$, the 2023 B5 rule over $n \le 8$ and the 2019 A5 divisibility orders for the first seven odd primes.
- Still `candidate`, still unreviewed. The papers cover 2018 through 2025 but only 2025 is complete; the sampled years are sampled because a statement was written only where it could be stated faithfully in one Lean expression. 2024 A5, B2 and B4 are absent for that reason -- geometric probability, congruence of quadrilaterals, and a Markov chain, none of which fits `binders` and `conclusion` without a definition the schema has no place for.

### Putnam branch 0.3.0 - 2026-09-08 - manifest feda7f08cb14f3fb7b8c0fac79c7dab4179e44ee50eb509950d64b75bead2284

- Twenty-three entries from the William Lowell Putnam Mathematical Competition, 2018 through 2025, and the eight papers they cite in `sources.json`. Sixteen are statements: `putnam-2018-a1`, `putnam-2018-a3`, `putnam-2018-a4`, `putnam-2019-a1`, `putnam-2019-b3`, `putnam-2020-a2`, `putnam-2020-a3`, `putnam-2020-b1`, `putnam-2020-b5`, `putnam-2020-b6`, `putnam-2021-a5`, `putnam-2022-b2`, `putnam-2023-b2`, `putnam-2024-a1`, `putnam-2025-a2-lower`, `putnam-2025-a2-upper`. Seven are twins: `putnam-2019-a1-twin`, `putnam-2020-a2-twin`, `putnam-2020-b5-twin`, `putnam-2020-b6-twin`, `putnam-2023-b2-twin`, `putnam-2024-a1-twin`, `putnam-2025-a2-lower-twin`.
- A competition paper is a source the corpus had no shape for. One sitting is one source -- `putnam-2018` through `putnam-2025` -- because a problem is numbered within its year and nothing outside that year is in scope for a locator. The locators are `(1, 0, n)` for `An` and `(2, 0, n)` for `Bn`, so the lexicographic order on them is the order the problems were sat, and the viewer prints `[Putnam 2024, A1]` once it carries the `competition-problem` style.
- Four shards are new: `05` (enumerative combinatorics), `15` (basic linear algebra), `30` (functions of a complex variable) and `40` (convergence and divergence). The corpus had four reporting groups and now has seven; `combinatorics`, `linear-algebra` and `complex-analysis` are each below the size a ranking needs, and are here to be grown rather than reported on.
- Every statement was elaborated against Mathlib (`leanprover/lean4:v4.34.0-rc2`, mathlib4 master) and every witness kernel-checked: the fourteen entries with `∃`-closable binders record `witness` terms whose `#print axioms` names only `propext`, `Classical.choice` and `Quot.sound`. The nine with `witness: null` say why -- seven have no binders at all, `putnam-2019-b3` has an implicit `{n : ℕ}` and `putnam-2018-a4` an instance `[Group G]`, and `∃` binds none of those. Both answers and twins were also checked numerically, which is how `putnam-2020-b6-twin` is known to fail first at $n = 4$ rather than merely believed to.
- Every entry is `candidate`. The answers are our reading of the papers and of the solutions Kedlaya, Bhargava and Ng publish beside them; `input` is a restatement in our own words throughout, because the MAA holds copyright in the problem text and the solutions are the authors'. Nothing here has had a human faithfulness read.
