# Roy van Rijn / collision-geometry provenance note

**Snapshot:** 2026-09-14

This note is deliberately conservative.  The current Keller-groupoid project contains a planar secant/divided-difference projector criterion closely related to public collision-geometry work.  If that criterion, or a direct descendant of it, becomes load-bearing in a paper or proof, **contact Roy van Rijn before submission** and ask what prior artifact he considers the appropriate citation for his independent line of work.

## Exact secant-projector theorem

Roy van Rijn's later external audit explicitly attributes the planar secant-determinant/idempotent splitting to the manuscript *Collision Ideals and Off-Diagonal Sheets*, whose title page names Chloe van der Vlugt.  The public repository is now named `what-social-construct/jacobian-collision-geometry` (formerly referenced as `collision-ideals`).

Pinned source reviewed by Roy's audit:

- repository commit: `a409db9922279907493d96f691b5ea9eb71baaf9`
- durable tree: https://github.com/what-social-construct/jacobian-collision-geometry/tree/a409db9922279907493d96f691b5ea9eb71baaf9
- pinned PDF: https://github.com/what-social-construct/jacobian-collision-geometry/blob/a409db9922279907493d96f691b5ea9eb71baaf9/Collision%20Ideals%20and%20Off-Diagonal%20Sheets.pdf

The manuscript/audit establishes the interface

\[
I_R:I_\Delta=I_R+(\delta_F)=I_R:I_\Delta^\infty
\]

for planar Keller maps and the corresponding secant-determinant idempotent splitting.

## Roy's earlier overlapping off-diagonal work

Roy's repository had its own off-diagonal/saturation constructions before the external manuscript was added.  A durable early artifact is:

- Roy van Rijn, `DECORATED_NORMALIZATION_INVARIANT.md`
- commit `1caf2edb7cf5ade06078375dc4ccf1ee774ddbd9`, 2026-07-23
- durable file URL: https://github.com/royvanrijn/jacobian-research/blob/1caf2edb7cf5ade06078375dc4ccf1ee774ddbd9/extended-geometry/DECORATED_NORMALIZATION_INVARIANT.md

That note defines the closure of a genuine off-diagonal self-intersection scheme by saturation after removing the diagonal factor.  It is related machinery, but it is **not** presently being treated as the provenance source for the exact planar secant-projector theorem.

Roy's own later audit makes the same distinction and is useful as a provenance record:

- pinned audit snapshot: https://github.com/royvanrijn/jacobian-research/blob/ec240e43ce032e92f35b08826becc284d9711f2f/research/archive/non-elliptic/verified/COLLISION_IDEALS_EXTERNAL_AUDIT.md

The audit states both that the secant determinant/idempotent splitting is credited to *Collision Ideals and Off-Diagonal Sheets* and that Roy's repository had earlier off-diagonal and saturation constructions.

## Citation/contact policy

If the secant-projector criterion is merely an optional computational reformulation, cite the pinned *Collision Ideals and Off-Diagonal Sheets* source and mention independent overlap only when relevant.

If the criterion becomes essential to a theorem, search reduction, or final proof:

1. contact Roy van Rijn before submission;
2. describe exactly which statement we use;
3. ask whether he regards one of his July 23--30 artifacts as independent prior work that should be cited;
4. cite the external collision-ideals manuscript for the exact secant-projector theorem unless an earlier exact source is established;
5. avoid priority language unless the chronology has been explicitly checked.
