---
name: ingest-corpus
description: Use when adding mathematical statements to the Hardy corpus from a textbook or other source - harvesting exercises and theorems into corpus/problems/, registering sources and ids, and cutting the release. Covers the field-by-field rules, the twin convention, and the checks that must pass before the work is done.
---

# Adding entries to the Hardy corpus

The corpus measures what provers and models can do, sliced by mathematical
field. Its value rests entirely on the entries being *right*: a statement whose
Lean says something other than its prose, or a result filed under the wrong
field, is worse than a missing entry, because it silently corrupts the claim
the whole dataset exists to make.

Read `corpus/SCHEMA.md` before starting. This is the procedure.

## The loop

```
hardy evals corpus check          # must exit 0 before you are done
hardy evals corpus serve          # look at what you wrote, rendered
```

Run `check` after every few entries, not once at the end. It names the entry
and the problem, and fixing five objections is much easier than fifty.

## One entry, field by field

Add it to `corpus/problems/<NN>.json`, where `<NN>` is the first two characters
of its primary MSC code. Create the shard if it does not exist, copying the
envelope (`schema_version`, `corpus_version`, `entries`) from a sibling.

| Field | Rule |
|---|---|
| `id` | lowercase slug, hyphens, permanent. Never reused, even after retirement. |
| `input` | **Your own restatement**, never the book's wording. Inline LaTeX. |
| `name` | Lean identifier, PascalCase, **unique across the whole corpus**, not just this shard. |
| `binders` | Hypotheses go *here*, not in `conclusion` — see A6 below. No `:=`, no newlines. |
| `conclusion` | The proposition alone. No `:=`, no newlines. |
| `imports` | Usually `["Mathlib"]`. |
| `expected` | `"true"`, or `"false"` for a deliberate perturbation — see twins. |
| `msc` | Published MSC2020 forms only: `11Axx` (section) or `11J72` (subsection). Primary first. |
| `difficulty` | `routine` / `substantial` / `qualifying` / `research-adjacent`. Your honest read. |
| `occurrences` | `[{"source_id": ..., "locator": [chapter, section, item]}]`, first is primary. |
| `rationale` | Required **only** when there are no occurrences; says what the entry is for. |
| `witness` | See A6. `null` needs a `witness_note` saying why. |
| `status` | Always `"candidate"`. Never write `active` or a `review` record. |

### `input` must state every hypothesis the Lean carries

This is the most common real error. If `binders` has `(hn : 0 < n)`, the prose
must say so. A prompt that omits a premise the declaration requires is asking
the model to prove a *different theorem* — and a reviewer comparing them will
approve the mismatch, because the prose reads fine on its own.

One entry in this corpus already had this bug: "among any $n+1$ integers, two
are congruent modulo $n$" beside a Lean statement carrying `0 < n`. At $n = 0$
the prose is false and the Lean is guarded.

### Copyright: restate, never copy

The mathematics in a theorem is not copyrightable; a book's prose is. Write
`input` yourself. `occurrences` records a citation, which is what any paper
does. Never reproduce the source's sentences, and never copy from a solutions
manual.

### A6: hypotheses belong in `binders`

A `witness` is checked as `theorem <Name>Witness : ∃ <binders>, True := <witness>`
with `#print axioms` appended. It proves the hypotheses are *satisfiable* —
without it, a statement whose premises are impossible is vacuously true and
nothing catches it.

- Put premises in `binders`, so they can be existentially closed. A premise
  hidden inside `conclusion` (`∀ n < 10, ...` with empty binders) leaves the
  entry `unwitnessed`, because nothing here parses Lean to find it.
- Write the term against `∃ <binders>, True`: for `(n : ℕ) (h : n > 0)` that is
  `⟨1, by norm_num, trivial⟩`.
- `sorry` is a **warning**, not an error. A witness using it is recorded
  `broken`. Do not use it to make the check pass.
- Implicit `{α : Type*}` and instance `[Group G]` binders cannot be closed by
  `∃`. Set `witness: null` with a `witness_note` saying exactly that.

### Twins

A twin is a *false* entry that perturbs a true one — the soundness instrument
that catches a model agreeing with anything. It needs `expected: "false"`,
`twin_of` naming the true entry, and **the same primary MSC code** as its
target. A good twin is plausible: flip an inequality, drop a hypothesis, weaken
a bound. Not obviously absurd.

## Registering what an entry refers to

Both are separate files and both are checked.

**Every new source** goes in `corpus/sources.json` before any entry cites it:

```json
"atiyah-macdonald": {
  "citation_key": "AM69",
  "authors": ["M. F. Atiyah", "I. G. Macdonald"],
  "title": "Introduction to commutative algebra",
  "publisher": "Addison-Wesley Publishing Co.",
  "address": "Reading, Mass.-London-Don Mills, Ont.",
  "year": 1969,
  "level": "graduate",
  "locator_style": "chapter-item",
  "locator_convention": "(chapter, 0, n) is body item chapter.n; (chapter, 1, n) is exercise n."
}
```

The citation fields are the AMS book fields (`authors`, `title`, `edition`,
`note`, `series`, `volume`, `publisher`, `address`, `year`), taken from the
book's own title and copyright pages, not from memory. `locator_style` is one
of `chapter-item`, `section-item`, `numbered-section`, `paragraph` — see
`SCHEMA.md` for what each makes of a triple — and decides how the viewer
prints `[AM69, 1.11]`. Pick the style that matches how the book is actually
cited, and write the prose convention beside it.

**Every new id** goes in `corpus/tombstones.json` under `issued`, with today's
date. The registry is append-only: never remove a key, never change a date. An
id that vanishes takes any external citation with it, and CI compares this file
against the merge base.

## Cutting the release

Do **not** edit `corpus_version` or `CHANGELOG.md` by hand. The manifest digest
covers the content, so it can only be computed after the entries land, and
every shard must agree on the version. One command does all of it in the order
that works:

```
hardy evals corpus release --version 0.3.0 \
  --note '`nilpotent-in-every-prime`, `zorn-maximal-ideal`: first entries from Atiyah-Macdonald.'
```

Versions: **patch** corrects (a fixed statement, a wrong MSC tag), **minor**
adds entries, **major** breaks the schema. It only ever goes up. The command
prints whatever `check` would still object to, and exits non-zero if anything
remains.

## Before you say you are done

1. `hardy evals corpus check` exits 0.
2. `hardy evals corpus report` shows the entries where you expect them.
3. You have looked at a sample in `hardy evals corpus serve` — the rendered
   prose beside the Lean is where a mismatch becomes obvious. The **Faithful**
   button there is for a human's read, not yours: do not press it for
   entries you wrote.

## What not to do

- Do not set `status: "active"` or write a `review` record. Promotion is a
  human faithfulness read, and the record binds digests you cannot forge.
- Do not invent MSC codes. The vendored table is the whole of MSC2020; if a
  code is rejected it does not exist. `hardy evals corpus serve` shows each
  code with its official name — use it to check you picked the right one.
- Do not edit `evals/baseline.json`. It is measurement, not content.
- Do not delete or retag an existing entry to make room. Retiring is a status
  change with a reason, and the id stays.
- Do not guess a `witness` to clear the unwitnessed count. `null` with an
  honest note is correct and expected.
