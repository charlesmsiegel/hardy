# Degree-six A6 attack

**Snapshot:** 2026-09-14

**Status:** structural reduction; \(A_6\) not yet eliminated.

Assume the current project-derived chain and the independent six-sheet frontier.  Then the
perfect-monodromy criterion removes \(S_6\), leaving \(A_6\) as the only degree-six residual
monodromy group.

## A6-1 — Forced boundary packet

The two Assi fibers have generic deficits \(b_0,b_1\) satisfying
\[
b_0+b_1=5,\qquad 2b_i\le6.
\]
Therefore \(\{b_0,b_1\}=\{3,2\}\).

In the natural six-point action of \(A_6\), no nontrivial element has support two.  Hence the
deficit-two fiber has trivial generic inertia and consists of two omitted unramified
normalization sheets.  Ramification must occur somewhere, so the deficit-three fiber has
generic inertia a single 3-cycle.  Thus, after relabeling,
\[
C_0:\quad b_0=3,\quad \sigma_0\sim(123),
\]
\[
C_1:\quad b_1=2,\quad \sigma_1=1,\quad t_1=2.
\]

The resulting boundary rows are
\[
\boxed{(3,1),(1,1),(1,1).}
\]
The refined degree-six boundary budget is saturated, so all excess terms vanish.

## A6-2 — Forced fiber census

Under the zero-excess local model:

- smooth point of \(C_0\): 3 affine preimages;
- node of \(C_0\): 0 affine preimages;
- smooth point of \(C_1\): 4 affine preimages;
- node of \(C_1\): 2 affine preimages.

Hence the omitted affine values are exactly the nodes of the ramified fiber \(C_0\).

## A6-3 — Node inertia

The finite normalization is branched only over \(C_0\).  At a node of \(C_0\), the two local
branch meridians commute and each has single-3-cycle inertia.  Their support packets exhaust
all six sheets, so after conjugacy the local pair is
\[
(123),(456),
\]
and the local inertia group is \(C_3\times C_3\).

## A6-4 — Plane-curve quotient target

To eliminate the residual degree-six case it suffices to rule out a surjection
\[
\rho:\pi_1(\mathbb A^2\setminus C_0)\twoheadrightarrow A_6
\]
for an Assi exceptional rational nodal one-place curve \(C_0\), subject to:

1. a geometric meridian maps to a single 3-cycle;
2. the two branch meridians at every node map to disjoint 3-cycles;
3. the image is transitive in the natural six-point action.

This is the present degree-six model exclusion problem.

## A6-5 — Positive-braid conditional exclusion

Neumann--Norbury identify the Orevkov invariant with the actual complement fundamental group
for nodal affine curves.  Orevkov's positive-braid result makes that group abelian in the
positive-braid-at-infinity case.  Therefore a positive-braid Assi fiber cannot admit the
required \(A_6\) quotient.

The open task is to determine whether Assi's exceptional two-rational-fiber geometry forces
positivity, or otherwise enumerate the non-positive splice diagrams and test the constrained
\(A_6\) representations.
