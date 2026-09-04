# The Hardy corpus

A classified set of mathematical statements, formalised in Lean 4 against
Mathlib, for measuring what automated provers and language models can do — and
where the library they depend on falls short.

Licensed CC-BY-4.0 (see `LICENSE`). The design and its reasoning are in
`docs/superpowers/specs/2026-09-03-corpus-design.md` in the Hardy repository;
this file is the reader's guide to the data.

## What is here

```
problems/<NN>.json     entries, sharded by MSC 2-digit class
sources.json           the texts entries are drawn from
taxonomy/              all 6603 MSC2020 codes; the MSC→arXiv map and groups
tombstones.json        every entry id ever issued
measurements/          Lean measurements, keyed by entry id and digest
CHANGELOG.md           what changed, per version, citing ids
```

## Two rules the whole design rests on

**The corpus holds statements only.** No tier, no solve rate, no
discrimination, no `shard` field — nothing measured and nothing derived. A tier
is a fact about one tactic ladder against one Mathlib revision on one machine,
not a property of a theorem. Measurements live in `measurements/`, keyed by id,
so the statements stay portable.

**A result is one entry with many occurrences.** The Nullstellensatz appears in
most algebraic geometry texts; it is one entry citing several, not several
entries. The first occurrence is *primary* and governs the field, the level, and
the antecedent rule.

## Entry fields

| Field | Meaning |
|---|---|
| `id` | permanent, never reused; retired entries stay in place |
| `input` | the statement in prose, carrying inline LaTeX. This is what a model is shown |
| `name` | the Lean identifier; reaches the model inside the declaration |
| `title` | the result's common name, when it has one. **Display only — never shown to a model**, because a named theorem is a retrieval cue |
| `binders`, `conclusion`, `imports` | assembled into the Lean declaration by string concatenation; never parsed here |
| `expected` | `true`, or `false` for a deliberate perturbation |
| `twin_of` | for a false entry, the true entry it perturbs. A twin shares its target's field |
| `msc` | MSC2020 codes, primary first. A code names a section (`12Fxx`) or a subsection (`12F10`); `12-XX` is the bare class and `12-01` classifies a publication type, so neither is accepted |
| `arxiv_override` | when the derived arXiv class is wrong; needs `override_reason` |
| `difficulty` | `routine` / `substantial` / `qualifying` / `research-adjacent` |
| `occurrences` | `(source_id, locator)` pairs; `locator` is `(chapter, section, item)` |
| `rationale` | required when there are no occurrences — what an authored entry states and why |
| `witness` | a Lean term instantiating the hypotheses, kernel-checked; `null` needs a `witness_note` |
| `status` | `candidate` / `active` / `retired`. Only `active` entries reach a headline, and `active` requires a faithful `review` bound to the entry as it now stands |
| `review` | the recorded human read that promoted the entry, bound to its digests *and* its classification |
| `audit` | spot-audit verdicts, each bound to the measurement panel that raised it |
| `fixtures` | ids of antecedents injected only in the fixtured condition |

## Writing a witness

`witness` is checked as `theorem <Name>Witness : ∃ <binders>, True := <witness>`
against the entry's own imports, with `#print axioms` appended, so it is
written against that goal rather than against the theorem. Elaborating is not
enough: `sorry` is a *warning*, so the axiom report is what separates a witness
from a hole. A witness naming anything beyond `propext`, `Classical.choice` and
`Quot.sound` is recorded `broken`. For `binders: "(n : ℕ) (h : n > 0)"` the term is
`⟨1, by norm_num, trivial⟩`: a value for `n`, a proof of `n > 0`, and
`trivial` for the `True`. An entry with no binders needs no witness of this
shape and records `trivial`.

An entry with **no binders at all** is reported *unwitnessed* rather than
trivially witnessed: a premise may live inside `conclusion` (as in
`∀ n < 10, …`), and nothing here parses Lean to find it. Put hypotheses in
`binders` if the entry should have A6 coverage.

Binders that `∃` cannot bind — implicit `{α : Type*}` or instance `[Group G]` —
have no witness in this form. Such an entry records `witness: null` with a
`witness_note` saying so, and is reported *unwitnessed* rather than passed: the
non-vacuity check did not run, and nothing but the human read stands between a
vacuous statement and a field headline.

## Classification

`taxonomy/msc2020.json` is the whole of MSC2020 as published at
[msc2020.org](https://msc2020.org/) — every code, in the form MSC publishes it,
with its own name. `scripts/vendor_msc2020.py` regenerates it.

`taxonomy/msc-to-arxiv.json` is *editorial*: arXiv publishes no MSC crosswalk,
so the arXiv class and the reporting group are judgements against
[arxiv.org/archive/math](https://arxiv.org/archive/math), versioned with the
corpus and open to disagreement. Both resolve **most specific first** — whole
code, then section, then class — because MSC classes are not homogeneous under
an arXiv reading. MSC 12 is the worst case: Galois theory (`12F`) is math.NT,
valuation theory (`12J`) is math.AC, near-fields (`12K`) are math.RA, and model
theory of fields (`12L`) is math.LO. Where even that is wrong for one entry,
`arxiv_override` carries the exception and its reason.

Reporting groups are deliberately coarser than the classes — a ranking per
2-digit class would be dozens of underpowered comparisons — while keeping the
fields the corpus targets distinct. MSC 26 and 28 are one group, `analysis`.

## The shard is derived

`problems/13.json` holds entries whose primary MSC code begins `13`. There is no
`shard` field: a stored shard would be a derived value inside a corpus that
holds no derived values. Sharding is a filing decision and says nothing about
how precisely an entry is classified — which is why a code must be finer than
the shard it lands in. `13` is itself a valid MSC2020 entry, so a bare class is
what a tagger writes when they did not look.

## Versions and the release gate

Each shard declares `schema_version` (the *format*, currently **2**) and
`corpus_version` (the *content*, three-level: patch corrects, minor adds, major
breaks). Every shard must agree on `corpus_version`, and the changelog head
must name it.

The head also binds the **manifest digest** — a hash over every content file
(the shards, the taxonomy tables, `sources.json`, `tombstones.json`, fixtures,
and the analysis plan):

```
## 0.1.0 - 2026-09-03 - manifest 0556793f…
```

Comparing version *strings* alone cannot see an unversioned edit: a shard
changes, both strings stay put, and the gate passes on a version that is no
longer reproducible. `measurements/`, `CHANGELOG.md` and this file are outside
the manifest — a baseline re-sweep or a documentation edit must not manufacture
a release.

`hardy evals corpus check` reports every mechanical objection: unregistered
ids, occurrences citing a text `sources.json` does not carry, unknown MSC
codes, entries filed in the wrong shard, and a manifest that no longer matches
what the changelog binds.

## Ids are permanent

`tombstones.json` records every id ever issued. A retired entry keeps its id and
stays in its shard with `status: "retired"`; it is never deleted. External
citations therefore stay valid, and no id is ever reused for a different
statement.

## Provenance and copyright

The mathematics in a theorem is not copyrightable, but a book's prose is.
`input` is always our own restatement, never the source's wording, and no
solution-manual text is reproduced. `occurrences` records a citation — the same
thing any paper does.
