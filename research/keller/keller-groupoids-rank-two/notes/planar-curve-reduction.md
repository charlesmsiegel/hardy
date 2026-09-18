# Planar Curve Reduction Program for Keller Groupoids

**Snapshot:** 2026-09-14  
**Status:** research program / reduction architecture

The working hypothesis of this note is:

> The two-dimensional Jacobian conjecture should be attacked by translating the
> existence of a nontrivial planar Keller groupoid into the existence of a
> highly constrained **decorated plane curve**, then classifying or eliminating
> those decorated curves.

This is not yet a proof strategy with a closed final step.  It is an attempt to
move the unknown object from "arbitrary polynomial maps \(\mathbb A^2\to\mathbb
A^2\)" into a class where resolution graphs, parametrizations, singularities,
fundamental groups, braid monodromy, and finite covers are available.

---

# 1. The right object: a decorated nonproperness curve

Let

\[
f=(P,Q)\colon\mathbb A^2\to\mathbb A^2
\]

be a hypothetical nonautomorphic complex Keller map of generic degree \(d\).

Let

\[
S=S_f
\]

be its nonproperness (Jelonek) curve and

\[
V=\mathbb A^2\setminus S.
\]

Over \(V\), the map is finite étale of degree \(d\):

\[
f^{-1}(V)\to V.
\]

Hence it is determined topologically/algebraically by a transitive finite
monodromy representation

\[
\rho:\pi_1(V)\to G\le S_d.
\]

Thus the primary object should not be the bare curve \(S\), but

\[
\boxed{(S,\rho,\text{local extension/deletion data}).}
\]

Call this a **decorated Keller curve datum**.

The role of the extra local data is important: the finite normalization may
have ramified points over \(S\), and the original affine-plane source may also
omit points or divisors in the normalization.  In the étale-maximal case the
monodromy/inertia data suffices; in general one must also remember which
unramified pieces of the finite normalization are absent from the source.

---

# 2. Why this really is a reduction to curves

Given \((S,\rho)\):

1. \(V=\mathbb A^2\setminus S\) is known.
2. By the Riemann existence theorem, \(\rho\) determines the corresponding
   finite étale cover
   \[
   W_\rho\to V.
   \]
3. Its function field determines the normalization
   \[
   \overline Z(S,\rho)\to\mathbb A^2.
   \]
4. The étale locus
   \[
   \widehat Z(S,\rho)\subset\overline Z(S,\rho)
   \]
   is the canonical étale filling.

The reconstruction theorem already developed in the Keller project says, in
dimension two, that a Jacobian counterexample is equivalent to the existence of
a nontrivial active datum for which

\[
\widehat Z(S,\rho)
\]

contains a dense Zariski-open subset isomorphic to \(\mathbb A^2\).

Thus, abstractly,

\[
\boxed{
JC(2)
\Longleftrightarrow
\text{no nontrivial decorated plane-curve datum has an } \mathbb A^2
\text{ open chart in its canonical étale filling}.
}
\]

The missing step is to replace "contains an \(\mathbb A^2\)-chart" by finite,
computable conditions on the decorated curve.

---

# 3. Existing curve-theoretic restrictions

The classical nonproperness-set literature already moves strongly in this
direction.

## 3.1 Polynomially parametrized rational components

Jelonek's work implies that for a generically finite polynomial map
\(\mathbb C^2\to\mathbb C^2\), the nonproperness set, if nonempty, is a union of
affine polynomial-parametric curves.

Hence every irreducible component has rational normalization.

Primary sources:

- Z. Jelonek, *The set of points at which a polynomial map is not proper*,
  Ann. Polon. Math. 58 (1993), 259–266.
- Z. Jelonek, *Note about the set \(S_f\) for a polynomial mapping
  \(f:\mathbb C^2\to\mathbb C^2\)*, Bull. Polish Acad. Sci. Math. 49 (2001).

For a plane Keller counterexample we therefore search among **rational plane
curves**, not arbitrary curves.

---

## 3.2 One point at infinity

Nguyen Van Chau proved that the nonproper value set of a nonsingular
polynomial map \(\mathbb C^2\to\mathbb C^2\), if nonempty, is a plane curve
with one point at infinity.

Reference:

- Nguyen Van Chau, *Note on the Jacobian condition and the non-proper value
  set*, Ann. Polon. Math. 84 (2004), 203–210.

Thus the projective closure

\[
\overline S\subset\mathbb P^2
\]

is extremely special: all of the nonproperness geometry reaches infinity
through one projective point.

---

## 3.3 Parametrization-degree ratio

The same circle of results gives polynomial parametrizations of irreducible
components

\[
t\mapsto(\alpha(t),\beta(t))
\]

whose degree ratio is constrained by the polynomial degrees of the Keller pair:

\[
\frac{\deg\alpha}{\deg\beta}
=
\frac{\deg P}{\deg Q}
\]

after the standard coordinate normalization used in the theorem.

This strongly constrains the Newton data and the unique branch at infinity.

---

## 3.4 Components cannot be embedded affine lines

The plane Keller nonproperness literature further shows that a nonproperness
component cannot simply be a smooth copy of \(\mathbb A^1\).  Thus candidate
rational components must acquire singularity.

This converts rationality into a **singularity budget**.

For an irreducible projective component \(\overline C\) of degree \(m\), with
normalization \(\mathbb P^1\),

\[
\sum_{p\in\operatorname{Sing}\overline C}\delta_p
=
p_a(\overline C)
=
\frac{(m-1)(m-2)}2.
\]

The singularities at finite points and at the unique point at infinity must
consume exactly this budget.

---

## 3.5 Degree bounds

Jelonek gives degree bounds for the nonproperness set in terms of the polynomial
degrees and geometric degree of the map.  Jelonek–Lasoń give related
quantitative parametric-curve bounds.

Hence fixing even coarse degree data for \((P,Q)\) gives a bounded plane-curve
search.

---

# 4. First proposed finite data structure for a candidate

For each irreducible component \(S_i\subset S\), record:

1. degree \(m_i=\deg\overline S_i\);
2. a polynomial parametrization
   \[
   (\alpha_i(t),\beta_i(t));
   \]
3. Puiseux/semigroup data of its unique branch at infinity;
4. affine singularity types and their \(\delta,\mu\), and branch numbers;
5. intersections with every other \(S_j\);
6. local monodromy conjugacy class
   \[
   \sigma_i=\rho(\text{meridian around }S_i)\subset S_d;
   \]
7. generic number of surviving affine sheets above \(S_i\);
8. whether \(S_i\) is an active branch component of the finite normalization or
   a deletion/nonproperness component with trivial generic inertia.

At singular intersection points of \(S\), record the braid/local fundamental
group data relating the meridians.

At the unique point at infinity, record the full embedded resolution graph and
the induced monodromy around its exceptional components.

This is finite combinatorial/algebraic information.

---

# 5. The embedded-resolution graph as the main interface

Resolve

\[
\overline S\cup L_\infty\subset\mathbb P^2
\]

to a simple normal-crossings divisor

\[
D\subset Y.
\]

The result is a weighted dual graph

\[
\Gamma(S)
\]

whose vertices record:

- strict transforms of curve components;
- the line at infinity;
- exceptional curves;
- self-intersection numbers;
- multiplicities/valuations of the defining equations.

Because the candidate cover is determined over the complement by \(\rho\),
local inertia decorates the vertices/branches of this graph.

The proposed pipeline is:

\[
\boxed{
\text{plane curve}
\to
\text{embedded resolution graph}
\to
\text{inertia-decorated graph}
\to
\text{normalized finite-cover graph}
\to
\text{candidate }\mathbb A^2\text{ boundary graph}.
}
\]

This is the point where the problem becomes substantially more discrete.

---

# 6. From local monodromy to the normalized cover

At a generic smooth point of a component \(S_i\), choose a meridian
\(\gamma_i\).  Its permutation

\[
\sigma_i=\rho(\gamma_i)
\]

has a cycle decomposition.

For the finite normalization, those cycles govern points and ramification over
the generic point of \(S_i\):

- a \(1\)-cycle corresponds to an unramified sheet;
- an \(e\)-cycle corresponds locally to ramification index \(e\).

The original Keller source is étale, so ramified points of the finite
normalization cannot belong to the source chart.

Thus the cycle type of every meridian tells us which generic sheets necessarily
disappear from the affine-plane source as one approaches \(S_i\).

In an étale-maximal Keller map, the surviving affine fiber over a generic point
of \(S_i\) should be exactly the set of fixed sheets of \(\sigma_i\).

For a general Keller map one must allow additional deleted unramified sheets;
eliminating or controlling this possibility is an important subproblem.

---

# 7. Groupoid collision data becomes curve-monodromy data

Over

\[
V=\mathbb A^2\setminus S,
\]

every fiber configuration space is the associated cover of \(\rho\) on ordered tuples:

\[
\operatorname{Conf}_k|_V
\leftrightarrow
\operatorname{Inj}([k],[d]).
\]

Therefore once \((S,\rho)\) is fixed:

- the number of components of every \(\operatorname{Conf}_k|_V\);
- their covering degrees;
- their Galois closures;
- the entire permutation structure of face maps

are finite group theory.

The difficult geometry is pushed to **extension across the curve** and **the
existence of the \(\mathbb A^2\)-chart**.

This is precisely the desired reduction.

---

# 8. Compactification and canonical-divisor constraints

Let

\[
\overline Z\to\mathbb P^2
\]

be an appropriate compactified normalization/Galois closure after resolving the
branch curve.

For a finite cover, the canonical divisor satisfies a Riemann–Hurwitz formula

\[
K_{\overline Z}
=
\pi^*K_{\mathbb P^2}
+
R,
\]

with ramification divisor \(R\) computable from local inertia after resolving
singular branch points.

Equivalently, in a Galois/orbifold presentation one expects the branch
contribution

\[
K_{\mathbb P^2}
+
\sum_i\left(1-\frac1{e_i}\right)D_i
\]

to control the canonical geometry upstairs, with corrections at singular
branch configurations handled on the resolution.

But every Keller configuration surface is algebraically parallelizable:

\[
T_{\operatorname{Conf}_k}\simeq\mathcal O^{\oplus2},
\qquad
K_{\operatorname{Conf}_k}\simeq\mathcal O.
\]

Thus any compactification of a configuration component has a nowhere-vanishing
algebraic two-form on the affine part whose zero/pole divisor is supported
entirely on the boundary.

This gives a direct interface between:

- degree/singularity data of the plane curve;
- inertia/ramification indices;
- the weighted boundary divisor upstairs.

It is a promising source of numerical contradictions.

---

# 9. The strongest boundary condition: the source chart is \(\mathbb A^2\)

Suppose the canonical filling contains

\[
X_0\simeq\mathbb A^2.
\]

Choose a smooth projective completion.  The boundary of an affine plane has very
special birational geometry.

After resolving, its boundary is a rational SNC tree which can be transformed
by boundary blowups/blowdowns to the single line at infinity in \(\mathbb P^2\).

Therefore a candidate decorated curve must produce, upstairs, a boundary graph
containing a subgraph whose complement is the source chart and whose weighted
intersection matrix has the birational contraction properties of an
\(\mathbb A^2\)-boundary.

This suggests an algorithmic test:

1. resolve the plane curve downstairs;
2. lift the graph using inertia;
3. resolve the resulting cover singularities;
4. enumerate allowed deleted/unramified pieces;
5. test whether the resulting boundary graph can be an SNC completion of
   \(\mathbb A^2\).

If no choice works, that decorated curve cannot come from a Keller
counterexample.

This may be the most concrete route from Keller groupoids to plane-curve
classification.

---

# 10. Bézout and the single point at infinity

Because the whole nonproperness curve has only one point at infinity, the
components have severely constrained pairwise intersection.

For two projective components \(\overline S_i,\overline S_j\),

\[
\sum_p I_p(\overline S_i,\overline S_j)
=
m_i m_j.
\]

A potentially large fraction of this intersection multiplicity is forced into
the common point at infinity.  The remainder must be realized by affine
intersections/singularities.

Thus:

- Puiseux data at infinity gives a lower bound for the intersection there;
- Bézout fixes the total;
- affine singularity data must realize exactly the difference.

This is a very finite Diophantine constraint once the degrees and semigroups at
infinity are fixed.

---

# 11. Genus and singularity budgets

Each irreducible component is rational. Therefore

\[
\frac{(m_i-1)(m_i-2)}2
=
\sum_p\delta_p(S_i).
\]

The unique branch at infinity contributes an explicitly computable
\(\delta_\infty\) from its Puiseux semigroup.

Hence the total affine singularity budget is

\[
\delta_{\mathrm{aff}}
=
\frac{(m_i-1)(m_i-2)}2-\delta_\infty.
\]

If independent Keller conditions imply a minimum amount of affine collision or
self-intersection, while the one-place-at-infinity data consumes too much of
the genus budget, the component is impossible.

This is exactly the kind of "pile up conditions until no curve remains"
mechanism we want.

---

# 12. Fundamental group and braid monodromy

The cover over \(V\) is specified by

\[
\rho:\pi_1(\mathbb A^2\setminus S)\to S_d.
\]

For an explicit plane curve, \(\pi_1\) can in principle be obtained from braid
monodromy / Zariski–van Kampen.

Therefore candidate elimination can proceed group-theoretically:

1. enumerate candidate curve type;
2. obtain a presentation of \(\pi_1(\mathbb A^2\setminus S)\);
3. enumerate transitive representations into \(S_d\);
4. impose local inertia cycle types;
5. discard normal/regular representations, which would force invertibility;
6. construct associated configuration covers;
7. test extension and \(\mathbb A^2\)-boundary conditions.

For bounded curve degree and bounded \(d\), this is a finite computation.

---

# 13. Factorial-moment constraints from collision Euler characteristics

Let

\[
U_m=\{y:\#f^{-1}(y)=m\}.
\]

Then

\[
\chi(\operatorname{Conf}_k)
=
\sum_{m=k}^d(m)_k\chi(U_m).
\]

In dimension two, each \(U_m\) for \(m<d\) is built from constructible subsets
of the plane curve \(S\), so its Euler characteristic can be expressed from:

- normalizations of curve components;
- deleted singular/intersection points;
- finite exceptional strata.

Thus the groupoid Euler invariants become explicit functions of curve
singularity data.

Conversely, any independent restriction on \(\chi(\operatorname{Conf}_k)\) for affine
parallelizable surfaces produces arithmetic constraints on the curve.

This is a second path from surface geometry back down to plane-curve
combinatorics.

---

# 14. The omitted finite set

For a planar Keller map the image is cofinite:

\[
f(\mathbb A^2)=\mathbb A^2\setminus Z
\]

with \(Z\) finite.

The omitted points lie on the deepest fiber-drop strata.  They are separate
from the nonproperness curve \(S\): \(S\) records where sheets escape to
infinity; \(Z\) records points where all affine sheets disappear.

Topologically,

\[
H_3(\mathbb C^2\setminus Z;\mathbb Z)
\simeq
\mathbb Z^{|Z|}.
\]

But every individual configuration surface has homology only through degree \(2\).
Thus omitted points appear as higher simplicial homology of the Keller
groupoid.

The goal is to translate this \(H_3\)-class into a curve-theoretic expression,
possibly via braid monodromy or the resolution graph.

If the resulting expression must vanish for every allowable decorated plane
curve, then \(Z=\varnothing\).

---

# 15. Iteration turns one curve problem into a family of curve problems

For iterates

\[
f^r:\mathbb A^2\to\mathbb A^2,
\]

the generic degree is

\[
d^r.
\]

The 2006 stable-image theorem says the iterated images eventually stabilize.

Thus, after stabilization, one has maps of growing generic degree living in the
same cofinite planar environment.

Their nonproperness curves, monodromy data, fiber configuration spaces, and boundary
graphs cannot vary arbitrarily.

A powerful target would be:

> Find a curve invariant \(J(S,\rho)\) that is forced to grow with \(d^r\), but
> is bounded by the fixed stable planar geometry.

This would force \(d=1\).

The curve-reduction framework gives many candidate growth quantities:

- total ramification/inertia;
- degrees of nonproperness curves;
- number of branches or singularity budget;
- complexity of resolution graphs;
- orbifold/log Chern numbers;
- size of the Galois group;
- boundary determinant data upstairs.

---

# 16. A finite-at-fixed-degree search program

Fix:

- generic degree \(d\);
- polynomial bidegree \((\deg P,\deg Q)\).

Then:

## Stage A — curve enumeration

Enumerate rational one-place-at-infinity plane curves satisfying:

- the degree-ratio condition;
- degree bounds;
- genus formula;
- allowed Puiseux semigroups;
- required singularity;
- Bézout intersection constraints.

## Stage B — monodromy enumeration

For each curve configuration:

- compute/present \(\pi_1(\mathbb A^2\setminus S)\);
- enumerate transitive \(\rho\) into \(S_d\);
- impose local inertia and nonnormality.

## Stage C — finite-cover reconstruction

For each \((S,\rho)\):

- construct the finite normalization;
- resolve cover singularities;
- determine the canonical étale filling.

## Stage D — affine-plane chart test

Search the boundary graph for an \(\mathbb A^2\) open chart.

If no candidate survives, there is no Keller counterexample with those degree
data.

If a candidate survives all conditions, we have produced an explicit
geometric target from which a map may potentially be reconstructed.

---

# 17. What would constitute a proof of JC(2) along this route?

Any one of the following would suffice.

## A. Curve impossibility theorem

Prove that no rational one-place-at-infinity plane curve can satisfy all Keller
inertia/boundary constraints.

## B. Cover impossibility theorem

Allow candidate curves, but prove that no transitive nonnormal monodromy cover
of their complements has a canonical étale filling containing \(\mathbb A^2\).

## C. Boundary-graph impossibility theorem

Show that the resolution graph forced by any active Keller decorated curve can
never contain an \(\mathbb A^2\)-boundary configuration.

## D. Iteration contradiction

Show some curve/cover complexity must grow under iteration while stable-image
geometry bounds it.

## E. Constructive alternative

If the constraints do not become inconsistent, explicitly build a decorated
curve and cover satisfying all of them.  That would identify a concrete target
for a planar counterexample rather than searching blindly through polynomial
pairs.

Either outcome is progress because plane curves are classifiable objects.

---

# 18. Priority research questions

1. **Étale maximality in dimension two.**  
   Can every planar Keller counterexample be replaced by, or shown to be,
   étale-maximal?  If yes, generic fiber cardinalities along \(S_i\) become
   fixed-point counts of inertia permutations, removing deletion data.

2. **Boundary graph criterion.**  
   Give a necessary and sufficient weighted-graph criterion for the canonical
   filling to contain a dense \(\mathbb A^2\) chart.

3. **Inertia versus Puiseux data.**  
   Relate allowed permutation cycle types around \(S_i\) to the Puiseux pairs
   of the branch at infinity.

4. **Collision-surface Chern constraints.**  
   Translate algebraic parallelizability of \(\operatorname{Conf}_k\) into log-Chern equations on
   its compactification and hence into equations on \(S\) and inertia.

5. **Euler moment signs/divisibilities.**  
   Find constraints on \(\chi(\operatorname{Conf}_k)\) specific to parallelizable affine
   surfaces.

6. **Braid-monodromy search.**  
   For low-degree rational one-place curves, enumerate transitive nonregular
   representations of complement groups into \(S_d\).

7. **Iteration invariant.**  
   Find a resolution-graph or orbifold invariant amplified by degree under
   iteration.

---

# 19. Citation hygiene

Use primary sources for the classical constraints.

- Z. Jelonek, *The set of points at which a polynomial map is not proper*,
  Ann. Polon. Math. 58 (1993), 259–266.
- Z. Jelonek, *Note about the set \(S_f\) for a polynomial mapping
  \(f:\mathbb C^2\to\mathbb C^2\)*, Bull. Polish Acad. Sci. Math. 49 (2001).
- Nguyen Van Chau, *Note on the Jacobian condition and the non-proper value
  set*, Ann. Polon. Math. 84 (2004), 203–210.
- R. Peretz, N. Van Chau, L. A. Campbell, C. Gutierrez,
  *Iterated images and the plane Jacobian conjecture*, DCDS 16 (2006),
  455–461.
- Z. Jelonek, M. Lasoń, *Quantitative properties of the non-properness set of a
  polynomial map*, Manuscripta Math. 156 (2018), 383–397.

Recent 2026 exploratory work on curve atlases, pencils, or valuative
constraints should be treated as **search leads** until individually audited;
do not silently import its claims into the theorem registry.

---

# 20. Exact boundary recognition now available

The final affine-plane recognition step has now been isolated in
`boundary-graph-criterion.md`.

The main result is the Ramanujam–Morrow boundary-birational criterion:
a completed candidate source is \(\mathbb A^2\) exactly when its boundary pair
is related by boundary blowups/blowdowns to \((\mathbb P^2,L_\infty)\).

This yields immediate necessary conditions

\[

ho(ar X)=r,\qquad
\det Q_E=(-1)^{r-1},\qquad
\operatorname{signature}(Q_E)=(1,r-1),\qquad
K_{ar X}^2=10-r,
\]

and the plumbing boundary at infinity must be \(S^3\).

Therefore the planar program has a concrete endpoint:

\[
(S,
ho,	ext{deletion data})
\longrightarrow
(ar X,E)
\longrightarrow
	ext{boundary certificate or contradiction}.
\]

The next high-value calculation is to express \(K_{ar X}^2\), \(
ho(ar X)\),
and \(Q_E\) directly in terms of the resolved plane curve and inertia labels.
