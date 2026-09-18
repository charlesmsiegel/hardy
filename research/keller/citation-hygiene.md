# Citation Hygiene and Provenance Policy

**Snapshot:** 2026-09-14

This project has an unusually compressed discovery timeline with multiple same-day public
descriptions, AI-assisted derivations, independent audits, and later expository writeups.
Priority and proof citations must therefore be separated carefully.

## Core rule

For every structural claim, distinguish at least three roles:

1. **Earliest public provenance** — who first publicly stated the idea/construction we are using.
2. **Contemporaneous corroboration/context** — public discussion showing how the idea was understood at the time.
3. **Later proof/exposition** — a later source that proves, coordinates, or develops the idea rigorously.

A later rigorous exposition does **not** inherit priority for an idea that was already public.

---

## Explicit Keller map

### Formula / counterexample announcement

Use Levent Alpöge's July 20, 2026 X post for the public announcement of the explicit polynomial map,
constant Jacobian determinant, and explicit collision.

Role:
- first public formula announcement located by the current audits;
- provenance of the explicit map;
- not the source of the later symmetric-product interpretation.

---

## Symmetric-product / marked-root construction

### Earliest public source currently identified

**Andy Jiang (@davikrehalt), X post, July 20, 2026**

Post:
`https://x.com/davikrehalt/status/2079175065695035442`

Content:
the construction
\[
\mathbb P^1\times \operatorname{Sym}^2(\mathbb P^1)
\longrightarrow
\operatorname{Sym}^3(\mathbb P^1),
\qquad
(p,\{q,r\})\mapsto \{p,q,r\},
\]
with deletion of the ramification divisor and a suitable tangent/non-osculating hyperplane to obtain
the affine Keller counterexample.

The post explicitly presents the construction as GPT output.

**Attribution rule:** if we say that the counterexample comes from / is explained by the
symmetric-product insert map, cite Jiang for provenance unless an earlier public source is found.

### Contemporaneous corroboration

**David Speyer, Secret Blogging Seminar, July 20, 2026**

Post:
`https://sbseminar.wordpress.com/2026/07/20/the-new-counterexample-to-the-jacobian-conjecture/`

In the comment thread, Will Sawin records essentially the same construction and writes that
"ChatGPT prompted by Andy Jiang" produced it. The thread then develops immediate reactions,
checks, and geometric questions.

Use this source for:
- contemporaneous documentation of how the construction entered public discussion;
- early mathematical reactions;
- historical context about which issues remained unclear on July 20.

Do **not** use the blog thread to displace Jiang's priority for the symmetric-product formulation.

---

## Same-day and later structural descriptions

### Aaron Lou

Use Aaron Lou's same-day derivation for the factorization/resultant gauge and global affine chart
when those exact formulas or derivations are used.

Role:
- independent reconstruction / explicit affine derivation;
- not origin of the symmetric-product idea.

### Daniel Litt

Use Litt's July 20--21 geometric explanation when discussing the affine-line-bundle / pencil-bundle
interpretation.

Role:
- later conceptual geometric digestion;
- not origin of the symmetric-product insert map.

### Naskrecki audit / "One Map, Three Descriptions"

Use the audit for:
- exact comparison of the public presentations;
- verification that the formula, Jiang projective model, and Lou chart describe isomorphic
  presentations of the same morphism;
- a documented provenance audit.

Role:
- independent audit and synthesis;
- not primary priority source for the formula or symmetric-product construction.

### Later AGNT / other expository papers

Use later papers for:
- rigorous proofs;
- coordinated formulas;
- machine-checked identities;
- detailed development of the symmetric-product origin.

Do **not** cite a later paper as though it originated the construction if Jiang's July 20 post
already contains it.

---

## Recommended citation pattern in prose

When discussing both origin and proof, cite both roles explicitly. For example:

> The projective marked-root interpretation was publicly proposed by Andy Jiang on July 20, 2026;
> a contemporaneous discussion appears in Speyer's Secret Blogging Seminar thread. A later
> writeup gives a detailed coordinate proof that this symmetric-product construction recovers the
> announced Keller map.

Then cite:
- Jiang for the first clause;
- SBS for historical context if relevant;
- the later rigorous source for the proof claim.

---

## Priority-sensitive statements

Before writing any of the following, perform a dedicated provenance check:

- "first observed";
- "discovered by";
- "introduced by";
- "originates in";
- "first proof";
- "first geometric explanation";
- "independently discovered";
- "new theorem";
- "new example".

If the evidence only establishes "earliest source currently located," use that weaker phrase.

---

## Mini-Hardy policy

Historical/provenance claims are not mathematical theorem-status claims. They should not be marked
`llm proved`, `human verified`, or `lean verified`.

Instead, record:
- source;
- timestamp/date;
- role;
- exact claim of priority;
- confidence level;
- whether an earlier-source search has been performed.

This should eventually become a separate provenance table in Hardy.

---

## Collision/configuration algebra: special contact flag

If the planar secant-projector / divided-difference criterion becomes load-bearing, consult `keller-groupoids-rank-two/notes/roy-van-rijn-provenance.md` and **contact Roy van Rijn before submission**.  His repository contains earlier off-diagonal/saturation work, while his own later audit credits the exact secant-idempotent theorem to *Collision Ideals and Off-Diagonal Sheets*.  Do not collapse those two provenance roles.
