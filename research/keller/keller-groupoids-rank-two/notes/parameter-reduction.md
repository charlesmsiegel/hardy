# Parameter Reduction for the Planar Keller Search

**Snapshot:** 2026-09-14  
**Goal:** reduce a hypothetical planar Keller counterexample to the smallest finite/discrete
search datum possible before attempting either exhaustive elimination or construction.

This note corrects one point in the previous planar draft:

> Nguyen Van Chau proves that the *whole nonproperness curve* has one **point** at infinity.
> That statement alone does not say "one place."

However, a separate argument using polynomial parametrization shows that **each irreducible
component** has normalization \(\mathbb A^1\), hence one **place** at infinity.  Therefore
Suzuki/Abhyankar–Moh one-place machinery applies componentwise.

---

# 1. Minimal search datum

For a hypothetical nonautomorphic Keller map

\[
f:\mathbb A^2\to\mathbb A^2
\]

of generic degree \(d\), search over the tuple

\[
\boxed{
\mathfrak D=
\left(
d,\,
G,\,
\{\Delta_i\}_{i=1}^c,\,
\{\sigma_i\}_{i=1}^c,\,
\mathcal A,\,
\mathcal E
\right).
}
\]

The intended meaning is:

- \(d\): generic degree;
- \(G\le S_d\): transitive nonregular monodromy group;
- \(c\): number of irreducible components of the nonproperness curve;
- \(\Delta_i\): a finite one-place-at-infinity \(\delta\)-sequence for component \(S_i\);
- \(\sigma_i\in G\): generic inertia permutation around \(S_i\), considered up to global
  simultaneous conjugacy;
- \(\mathcal A\): residual affine singularity/intersection data not forced by the infinity
  sequences and Bézout/genus constraints;
- \(\mathcal E\): deletion data, only if planar étale-maximality cannot be proved.

The point of the reduction is that **curve coefficients are not search variables** unless a
discrete skeleton survives all tests.

---

# 2. Generic degree and monodromy first

Before looking at curves:

## 2.1 Generic degree

A counterexample has

\[
d>1.
\]

Degree \(2\) is excluded by the classical normal-extension criterion: a separable quadratic
field extension is Galois, and a Keller map with normal/Galois function-field extension is
invertible.

Therefore the first classical possibility is

\[
d\ge3.
\]

There is a 2024 preprint claiming that prime field-extension degree is impossible for planar
Keller maps.  Because it is currently a preprint, treat that as an **optional search filter**,
not part of the core certified reduction until independently audited.

## 2.2 Monodromy

The finite cover over the proper locus has transitive monodromy

\[
G\le S_d.
\]

A regular/transitive action corresponds to a Galois extension and is therefore excluded by the
same normal-extension theorem.

Thus search only

\[
\boxed{G\le S_d\ \text{transitive and nonregular}.}
\]

This is a finite group-theoretic list for fixed \(d\).

All collision-component counts are then derived from \(G\)-orbits on ordered tuples.

---

# 3. Why each irreducible nonproperness component is one-place-at-infinity

Jelonek's nonproperness results imply that in dimension two each irreducible component is
dominated by a polynomial-parametric curve

\[
\phi:\mathbb A^1\to S_i\subset\mathbb A^2.
\]

Let

\[
\nu:\widetilde S_i\to S_i
\]

be the normalization.  Since \(\mathbb A^1\) is normal, the dominant map lifts to
\(\widetilde S_i\).  Complete the curves:

\[
\mathbb P^1\to\overline{\widetilde S_i}.
\]

The target has genus zero, because it is dominated by \(\mathbb P^1\).

Every point of
\(\overline{\widetilde S_i}\setminus\widetilde S_i\)
has a preimage in \(\mathbb P^1\).  But every finite point of the source \(\mathbb A^1\)
maps into the affine curve \(S_i\), so only the single source point \(\infty\in\mathbb P^1\)
can lie over the boundary.

Hence

\[
\overline{\widetilde S_i}\setminus\widetilde S_i
\]

has exactly one point, and therefore

\[
\boxed{\widetilde S_i\simeq\mathbb A^1.}
\]

So every irreducible component is a rational affine curve with one **place** at infinity.

Separately, Nguyen Van Chau proves that the entire nonproperness curve has one **projective
point** at infinity.  Therefore the unique places of all components lie over the same point of
the plane line at infinity.

This is stronger than either input by itself.

---

# 4. Replace the branch at infinity by a delta-sequence

For a one-place-at-infinity plane curve, Suzuki packages the infinity data into a finite
\(\delta\)-sequence

\[
\Delta_i=(\delta_{i,0},\delta_{i,1},\ldots,\delta_{i,h_i}).
\]

The sequence satisfies explicit semigroup/gcd conditions of Abhyankar–Moh type.

Most importantly for us:

\[
\boxed{
\Delta_i\ \text{determines the weighted dual graph of the minimal resolution at infinity}.
}
\]

Conversely, Suzuki records an inverse existence theorem for sequences satisfying the required
integer conditions.

Therefore, for the **infinity part** of the search, do not enumerate:

- polynomial coefficients;
- arbitrary Puiseux series;
- arbitrary resolution trees;
- arbitrary self-intersection weights.

Enumerate only allowed finite integer \(\delta\)-sequences.

The infinity resolution graph is then generated deterministically.

---

# 5. What is derived from the delta-sequence

For each component \(S_i\), derive from \(\Delta_i\):

1. degree data in a canonical coordinate system;
2. pole orders of coordinate functions;
3. Puiseux/characteristic data at infinity;
4. value semigroup at infinity;
5. the entire minimal weighted resolution graph at infinity;
6. the local \(\delta\)-invariant contribution at infinity;
7. intersection/contact orders with the line at infinity.

Thus those are **not independent search parameters**.

---

# 6. Affine singularity budget is not free

Let \(\overline S_i\subset\mathbb P^2\) have degree \(m_i\).

Because its normalization is \(\mathbb P^1\),

\[
p_a(\overline S_i)
=
\frac{(m_i-1)(m_i-2)}2
=
\sum_{p\in\operatorname{Sing}\overline S_i}\delta_p.
\]

The infinity sequence determines \(\delta_{i,\infty}\).  Hence

\[
\boxed{
\delta_{i,\mathrm{aff}}
=
\frac{(m_i-1)(m_i-2)}2
-
\delta_{i,\infty}.
}
\]

So the total affine singularity complexity of each component is a **derived integer budget**.

The residual datum \(\mathcal A\) need only partition/realize that fixed budget among admissible
affine singularities.

If the budget is negative, reject immediately.

If it is zero, the affine part is normal/smooth; known nonproperness restrictions may then reject
the candidate without any further search.

---

# 7. Pairwise intersections are mostly determined

For distinct components \(S_i,S_j\), Bézout gives

\[
\sum_p I_p(\overline S_i,\overline S_j)
=
m_i m_j.
\]

Their common infinity point and their individual infinity sequences determine, or at least sharply
constrain, the intersection multiplicity at infinity

\[
I_\infty(S_i,S_j).
\]

Therefore the total affine intersection number is

\[
\boxed{
I_{\mathrm{aff}}(S_i,S_j)
=
m_i m_j-I_\infty(S_i,S_j).
}
\]

Again, do not search over arbitrary affine intersections.  Search only over singularity/gluing
patterns realizing this fixed total.

A next reduction target is to express \(I_\infty(S_i,S_j)\) directly from the two
\(\delta\)-sequences plus one finite contact parameter, and then eliminate that contact parameter
where possible.

---

# 8. Inertia parameters should be only the original branch meridians

Assign to each irreducible component a generic inertia permutation

\[
\sigma_i\in G.
\]

Do **not** independently label every exceptional divisor created in the embedded resolution.

The exceptional inertia is derived recursively.

Locally:

- blowing up a smooth point of one branch transports the same meridional inertia;
- blowing up a transverse intersection of two SNC divisors with commuting inertia
  \(\sigma,\tau\) gives the new exceptional meridian corresponding, up to orientation convention,
  to the product
  \[
  \sigma\tau.
  \]

Thus after the original \(\sigma_i\)'s and the local branch structure are fixed, all inertia
labels on the resolution tree are generated.

This removes a large redundant group-theoretic search.

---

# 9. Group-theoretic pruning before geometry

For each fixed \(d\):

1. enumerate transitive nonregular subgroups \(G\le S_d\);
2. enumerate simultaneous-conjugacy classes of tuples
   \[
   (\sigma_1,\ldots,\sigma_c)\in G^c;
   \]
3. require that the subgroup generated by the geometric monodromy is \(G\);
4. impose all local relations from affine singularities and the common infinity link;
5. compute the induced orbit profiles on ordered \(k\)-tuples;
6. discard tuples incompatible with required collision connectedness/fiber strata.

Only surviving group data proceeds to cover construction.

For small \(d\), this stage is completely finite.

---

# 10. Boundary/Chern data is then deterministic or bounded

Given

\[
(d,G,\{\Delta_i\},\{\sigma_i\},\mathcal A,\mathcal E),
\]

the pipeline from `boundary-graph-criterion.md` produces:

- the downstairs SNC graph;
- all resolved inertia labels;
- the normalized cover;
- toric/Hirzebruch–Jung resolution chains;
- the candidate source boundary graph \(E\);
- its intersection matrix \(Q_E\);
- number \(r=\#E\);
- canonical self-intersection \(K_{\bar X}^2\), once the finite-cover discrepancy formula is
  implemented;
- the plumbing \(3\)-manifold at infinity.

Then impose the affine-plane conditions

\[
\rho(\bar X)=r,
\]

\[
\det Q_E=(-1)^{r-1},
\]

\[
\operatorname{signature}(Q_E)=(1,r-1),
\]

\[
K_{\bar X}^2=10-r,
\]

and finally the exact Ramanujam–Morrow boundary certificate.

At this stage there should be essentially **no continuous parameters left** unless genuine
equisingular moduli survive.

---

# 11. The reduced search hierarchy

The intended order is:

## Level 0 — one integer

\[
d.
\]

## Level 1 — finite group data

\[
G\le S_d,\qquad \text{transitive nonregular}.
\]

## Level 2 — finite integer sequences

\[
\Delta_1,\ldots,\Delta_c.
\]

These encode all infinity branches.

## Level 3 — finite permutation tuple

\[
\sigma_1,\ldots,\sigma_c.
\]

All exceptional inertia is derived.

## Level 4 — residual finite singularity/gluing data

\[
\mathcal A.
\]

Its total budgets are already fixed by genus and Bézout.

## Level 5 — deletion data

\[
\mathcal E,
\]

only if étale-maximality cannot be proved.

## Level 6 — deterministic verification

Construct the cover/boundary and either reject it or obtain an actual affine-plane candidate.

This is the parameter-minimized target.

---

# 12. What should be eliminated next

In priority order:

## A. Eliminate deletion data

Prove planar Keller counterexamples are étale-maximal, or prove a finite canonical bound on
possible unramified deletions.

If successful, remove \(\mathcal E\) entirely.

## B. Eliminate affine singularity choices

Derive local monodromy/Chern constraints forcing the affine singularity type from the
\(\delta\)-sequences and inertia tuple.

If successful, remove most or all of \(\mathcal A\).

## C. Bound or eliminate the number of components

Use the single common point at infinity, Bézout, topology of the complement, and monodromy to
bound \(c\), ideally forcing \(c=1\) or a small finite list.

## D. Bound delta-sequence length

Use the affine-plane boundary equation

\[
K_{\bar X}^2=10-r
\]

to bound the number of blowups in each infinity resolution, hence the lengths \(h_i\).

If successful, the \(\delta\)-sequence search becomes finite even before fixing polynomial degree.

## E. Bound generic degree

Use known field-extension constraints and the cover equations to reduce \(d\) to a finite list,
or find an iteration argument that excludes all \(d>1\) simultaneously.

---

# 13. Search versus proof

Once all reductions above are implemented, there are only two legitimate outcomes.

## Empty parameter space

Prove, analytically or by exhaustive certified enumeration, that no tuple

\[
\mathfrak D
\]

passes all constraints.

That proves the planar Jacobian conjecture.

## Surviving point

Produce one explicit discrete datum

\[
\mathfrak D
\]

passing every curve, monodromy, cover, Chern, topology, and boundary test.

Then reconstruct the corresponding finite normalization and search for the polynomial Keller map.

Either result is far more informative than a direct blind search over polynomial coefficients.

---

# 14. Citation/status notes

- Nguyen Van Chau's 2004 theorem is cited only for **one point at infinity** of the whole
  nonproperness curve.
- The **one place per irreducible component** statement is a separate deduction from the
  polynomial-parametric property.
- Suzuki's 1999 paper gives the \(\delta\)-sequence / resolution-graph machinery and an
  algorithmic classification framework for one-place-at-infinity curves.
- Jelonek–Lasoń give effective degree bounds for parametric curves covering a nonproperness set.
- A 2024 preprint by Moskowicz claims prime extension degrees cannot occur for planar Keller maps;
  keep this as a provisional search filter until independently checked.
- The withdrawn 2020/2021 Jelonek note should not be used as a foundational theorem without
  tracing the desired statement to a correct version or another source.
