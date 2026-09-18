# Perfect monodromy and the degree-six frontier

**Snapshot:** 2026-09-14

**Status:** project-derived chain, not yet human or Lean verified.  This file deliberately
separates the statements we currently believe from the audit status they actually have.

## PM-1 — Perfect monodromy in the residual non-maximal case

Assume the current planar reduction is correct: a non-étale-maximal Keller map has
nonproperness set equal to the two exceptional rational nodal fibers
\(C_0,C_1\) of an Assi one-place pencil.  The vanishing cycles of the pencil give an
integral homology basis of a generic fiber.  The monodromy permutation around a nodal
vanishing cycle has the form
\[
\sigma_+\sigma_-^{-1},
\]
where \(\sigma_+,\sigma_-\) are conjugate local branch-inertia permutations.  Hence its
image is trivial in the abelianization of the sheet-monodromy group.  If the generic-fiber
cover generates the full sheet monodromy, all generators vanish in the abelianization.

**Candidate theorem.**
\[
\boxed{F\text{ non-étale-maximal}\Longrightarrow G_{\rm mon}\text{ perfect}.}
\]

This is a **highest-priority audit statement**.  The two load-bearing points to formalize are:

1. the relevant vanishing cycles generate \(H_1\) of the generic Assi fiber; and
2. monodromy of the pulled-back cover over that generic fiber surjects onto the full sheet
   monodromy group.

## PM-2 — Conditional exclusion of S6

The symmetric group \(S_6\) has a nontrivial sign quotient \(S_6\to C_2\), so it is not
perfect.  Consequently, assuming PM-1,
\[
G_{\rm mon}=S_6
\]
is impossible in the non-maximal residual case.

If one also imports the current independent six-sheet frontier
\[
G_{\rm mon}\in\{A_6,S_6\},
\]
then PM-1 forces
\[
\boxed{G_{\rm mon}=A_6.}
\]

The independent \(A_6/S_6\) frontier is tracked as an **external research input**, not a
published axiom.

## PM-3 — Parity of inertia support

A perfect permutation group has trivial sign character.  Hence every generic inertia
permutation occurring in the residual case must be even.  If a boundary packet over a curve
has transverse indices \(e_j\), with total sheet deficit \(b=\sum_j e_j\) and \(q\) boundary
rows, then
\[
\operatorname{sgn}(\sigma)=(-1)^{\sum_j(e_j-1)}=(-1)^{b-q},
\]
so
\[
\boxed{b-q\equiv0\pmod2.}
\]

This is the elementary parity filter used in degree six.
