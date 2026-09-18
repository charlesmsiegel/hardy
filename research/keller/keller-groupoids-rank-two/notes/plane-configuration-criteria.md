# Keller-Groupoid Criteria for Plane Keller Maps

**Snapshot:** 2026-09-14  
**Notation:** \(\operatorname{Conf}_k(f)\) is the ordered \(k\)-point fiber
configuration variety.

This note deliberately focuses on constraints that become visible from the
Keller groupoid and its fiber configuration spaces.  Classical plane-curve
restrictions (Jelonek, Chau, Moh, Suzuki, etc.) are treated as separate inputs.

The purpose is to produce genuinely new interfaces between:

\[
\text{Keller map}
\longrightarrow
\text{configuration tower}
\longrightarrow
\text{finite monodromy / deletion data}
\longrightarrow
\text{plane-curve restrictions}.
\]

---

# 1. The configuration tower

For a Keller map

\[
f:\mathbb A^2\to\mathbb A^2
\]

of generic degree \(d\), define

\[
\operatorname{Conf}_k(f)
=
\{(x_1,\ldots,x_k):
f(x_1)=\cdots=f(x_k),\ x_i\ne x_j\}.
\]

Deleting the last point gives an étale map

\[
\rho_k:
\operatorname{Conf}_k(f)\to
\operatorname{Conf}_{k-1}(f).
\]

If the common target fiber has cardinality \(m\), then the fiber of \(\rho_k\)
has cardinality

\[
m-k+1.
\]

Thus the configuration tower records exact fiber cardinalities geometrically.

---

# 2. Fiber deficit versus inertia and deleted sheets

Let

\[
\bar Z\to\mathbb A^2
\]

be the finite normalization of the target in the source function field.

Fix an irreducible component \(C\) of the nonproperness curve and work over a
generic smooth point of \(C\).

Let

\[
\sigma_C\in S_d
\]

be the generic inertia permutation in the finite normalization.

Write

\[
\operatorname{fix}(\sigma_C)
\]

for its number of fixed sheets and

\[
\operatorname{supp}(\sigma_C)
=
d-\operatorname{fix}(\sigma_C)
\]

for the number of moved sheets.

Let

\[
m_C
\]

be the generic number of **affine source points** over \(C\).

Among the unramified/fixed sheets of the normalization, let

\[
t_C\ge0
\]

be the number generically omitted from the affine source.

Then

\[
\boxed{
m_C=\operatorname{fix}(\sigma_C)-t_C
}
\]

and therefore the generic fiber deficit

\[
r_C=d-m_C
\]

satisfies

\[
\boxed{
r_C
=
\operatorname{supp}(\sigma_C)+t_C.
}
\]

This equation cleanly separates two mechanisms:

- **ramification loss**, measured by the support of inertia;
- **unramified deletion**, measured by \(t_C\).

The configuration tower recovers \(m_C\): it is the largest \(k\) for which the
generic point of \(C\) lies in the closure of the image of
\(\operatorname{Conf}_k(f)\).

Hence, once inertia is known,

\[
\boxed{
t_C
=
d-m_C-\operatorname{supp}(\sigma_C)
}
\]

is not independent deletion data.

This is a direct parameter reduction supplied by the Keller groupoid.

---

# 3. Deficit-one criterion

A nonidentity permutation cannot move exactly one sheet.

Therefore

\[
r_C=1
\]

forces

\[
\operatorname{supp}(\sigma_C)=0,
\qquad
t_C=1.
\]

Equivalently:

\[
\boxed{
\text{a generic }(d-1)\text{-point fiber can only come from deletion of one
unramified sheet.}
}
\]

It cannot arise from ordinary branch ramification.

Thus a codimension-one \(d-1\)-fiber stratum is a certificate of
non-étale-maximality.

This is a particularly clean groupoid-derived diagnostic.

---

# 4. Étale-maximal top stabilization

Assume now that \(f\) is **étale-maximal**, i.e. the affine source is the
entire étale locus of the finite normalization.

Then

\[
t_C=0
\]

for every branch component, so

\[
r_C=\operatorname{supp}(\sigma_C).
\]

At every target point:

- if the finite normalization is étale, all \(d\) sheets belong to the source;
- if it ramifies, at least two units of degree are consumed by ramification,
  so at most \(d-2\) affine source points remain.

Hence there are **no \(d-1\)-point fibers anywhere**.

Therefore the forgetful map

\[
\rho_d:
\operatorname{Conf}_d(f)
\longrightarrow
\operatorname{Conf}_{d-1}(f)
\]

is an isomorphism:

\[
\boxed{
\operatorname{Conf}_d(f)
\simeq
\operatorname{Conf}_{d-1}(f)
\qquad
\text{for every étale-maximal Keller map.}
}
\]

This is the general source of the
\(\operatorname{Conf}_3\simeq\operatorname{Conf}_2\)
collapse in the explicit degree-three example.

The marked-cubic geometry explains that example concretely, but the
configuration collapse itself is a manifestation of étale maximality.

---

# 5. Hidden symmetric-group structure

The top configuration space

\[
\operatorname{Conf}_d(f)
\]

is canonically the frame torsor of the degree-\(d\) finite étale cover over the
proper locus

\[
V=\mathbb A^2\setminus S_f.
\]

Therefore

\[
\operatorname{Conf}_d(f)\to V
\]

is a finite étale \(S_d\)-torsor (possibly disconnected as a total space).

Under étale maximality,

\[
\operatorname{Conf}_{d-1}(f)
\simeq
\operatorname{Conf}_d(f),
\]

so \(\operatorname{Conf}_{d-1}\), which visibly has only coordinate
\(S_{d-1}\)-symmetry, acquires a canonical **hidden \(S_d\)-action**.

Its quotient is the proper locus:

\[
\boxed{
\operatorname{Conf}_{d-1}(f)/S_d
\simeq
\mathbb A^2\setminus S_f.
}
\]

For \(d=3\), this recovers the full \(S_3\)-symmetry of the ordered-pair
threefold \(Y\).

Potential use: bound possible degree \(d\) using finite-group actions on
compactifications of configuration surfaces.

---

# 6. Generic configuration thresholds record inertia support

In the étale-maximal case, let \(C\subset S_f\) be a branch component with
generic inertia \(\sigma_C\).

Then the generic affine fiber size along \(C\) is

\[
m_C=\operatorname{fix}(\sigma_C),
\]

so

\[
d-m_C=\operatorname{supp}(\sigma_C).
\]

Thus the last configuration space whose image reaches a generic point of \(C\)
is

\[
\operatorname{Conf}_{\operatorname{fix}(\sigma_C)}.
\]

The configuration filtration

\[
U_{\ge1}\supseteq U_{\ge2}\supseteq\cdots\supseteq U_{\ge d}
\]

therefore records the support sizes of the inertia permutations component by
component.

This converts geometric fiber-loss data directly into finite permutation data.

---

# 7. Unit-rank criterion on configuration surfaces

Let

\[
U_{\ge k}
=
\operatorname{im}\bigl(
\operatorname{Conf}_k(f)\to\mathbb A^2
\bigr).
\]

Let the codimension-one part of

\[
\mathbb A^2\setminus U_{\ge k}
\]

have irreducible components

\[
D_{k,1},\ldots,D_{k,c_k}
\]

with irreducible equations \(h_{k,j}\).

Let \(Z\) be any connected component of
\(\operatorname{Conf}_k(f)\) that dominates \(U_{\ge k}\).

Then each

\[
h_{k,j}\circ q_k
\]

is a nonconstant unit on \(Z\).

Moreover these units are multiplicatively independent modulo constants.
Therefore

\[
\boxed{
\operatorname{rank}
\bigl(
\mathcal O(Z)^*/\mathbb C^*
\bigr)
\ge c_k.
}
\]

In particular, for the top configuration space,

\[
U_{\ge d}=\mathbb A^2\setminus S_f.
\]

If \(S_f\) has \(c\) irreducible components, every connected Galois-closure
component \(Z\subset\operatorname{Conf}_d(f)\) satisfies

\[
\boxed{
\operatorname{rank}
\bigl(
\mathcal O(Z)^*/\mathbb C^*
\bigr)
\ge c.
}
\]

This turns the number of nonproperness-curve components into an intrinsic
algebraic invariant of the top configuration surface.

Possible next step: combine this with classification/log geometry of smooth
affine parallelizable surfaces.

---

# 8. Normal generation by inertia

Over the proper locus \(V=\mathbb A^2\setminus S_f\), let

\[
G\le S_d
\]

be the monodromy group.

Passing from \(V\) back to simply connected \(\mathbb A^2\) kills meridians
around the branch curve.

Therefore the generic inertia subgroups normally generate the monodromy group:

\[
\boxed{
G=
\left\langle\!\left\langle
I_{C_1},\ldots,I_{C_c}
\right\rangle\!\right\rangle.
}
\]

Consequences:

- every nontrivial abelian quotient of \(G\) is detected by some branch
  meridian;
- for a one-component active branch curve, one inertia conjugacy class must
  normally generate \(G\);
- candidate pairs \((G,\{\sigma_i\})\) failing this condition can be discarded
  before any surface construction.

This is standard branched-cover topology, but the groupoid makes it a natural
finite-group filter in the planar search.

---

# 9. The secant-projector criterion

This criterion is algebraic rather than curve-theoretic.

Write source variables

\[
x=(x_1,x_2),\qquad y=(y_1,y_2)
\]

and \(F=(P,Q)\).

Choose a polynomial divided-difference matrix

\[
D_F(x,y)
\]

such that

\[
F(x)-F(y)
=
D_F(x,y)(x-y)
\]

and

\[
D_F(x,x)=JF(x).
\]

Let

\[
c=\det JF\in\mathbb C^*,
\qquad
\delta_F(x,y)=\det D_F(x,y).
\]

On the self-fiber-product

\[
K_2(F)=\{F(x)=F(y)\},
\]

we have:

- on the diagonal \(x=y\),
  \[
  \delta_F=c;
  \]
- on \(\operatorname{Conf}_2(F)\), the nonzero vector \(x-y\) lies in
  \(\ker D_F\), so
  \[
  \delta_F=0.
  \]

Since \(K_2(F)\) is smooth and reduced,

\[
\boxed{
e_\Delta=\delta_F/c
}
\]

is the idempotent cutting out the diagonal component.

Therefore

\[
\boxed{
\delta_F(\delta_F-c)
\in
(P(x)-P(y),\,Q(x)-Q(y)).
}
\]

The off-diagonal configuration factor is cut out by

\[
\boxed{
I_{\operatorname{Conf}_2}
=
(P(x)-P(y),\,Q(x)-Q(y),\,\delta_F).
}
\]

Consequently:

\[
\boxed{
F\text{ is injective/invertible}
\iff
(P(x)-P(y),Q(x)-Q(y),\delta_F)=(1).
}
\]

This turns the first nontrivial Keller-groupoid level into an explicit ideal
membership criterion on the polynomial coefficients.

A recent independent collision-geometry project obtains essentially the same
planar secant-projector decomposition.  See `roy-van-rijn-provenance.md` for
the pinned external manuscript, Roy van Rijn's earlier overlapping
off-diagonal/saturation work, and the explicit contact-before-submission flag.
Do not make an unqualified priority claim for this mechanism.

---

# 10. Boolean equality projectors at all levels

For every pair \(i,j\) in \(K_r(f)\), pull back the diagonal idempotent:

\[
e_{ij}\in\mathcal O(K_r).
\]

Set-theoretically,

\[
e_{ij}=
\begin{cases}
1,&x_i=x_j,\\
0,&x_i\ne x_j.
\end{cases}
\]

They satisfy Boolean equivalence-relation identities, including

\[
e_{ij}^2=e_{ij},
\qquad
e_{ij}=e_{ji},
\]

and

\[
e_{ij}e_{jk}(1-e_{ik})=0.
\]

Primitive products of the \(e_{ij}\)'s and \(1-e_{ij}\)'s are exactly the
idempotents cutting out the set-partition components of \(K_r\).

Thus every Keller map carries an explicit finite Boolean algebra of polynomial
projectors at every nerve level.

Potential use: derive degree/coefficient constraints from the resulting ideal
identities without first passing to compactification or plane-curve
classification.

---

# 11. A configuration criterion for non-maximality

The map

\[
\rho_d:
\operatorname{Conf}_d(f)\to
\operatorname{Conf}_{d-1}(f)
\]

is always an open immersion.

Therefore the divisor/closed set

\[
E_{\mathrm{miss}}
=
\operatorname{Conf}_{d-1}(f)
\setminus
\rho_d(\operatorname{Conf}_d(f))
\]

is exactly the locus of ordered \((d-1)\)-tuples lying over \((d-1)\)-point
fibers.

By the deficit-one criterion:

\[
\boxed{
E_{\mathrm{miss}}\ne\varnothing
\Longrightarrow
f\text{ is not étale-maximal}.
}
\]

Thus the configuration tower gives an intrinsic geometric witness of hidden
unramified sheets missing from the affine source.

This is useful because étale maximality is otherwise phrased using the finite
normalization, an external construction.

---

# 12. Degree-three consequence

Let \(d=3\).

A nonnormal transitive monodromy group must be \(S_3\).

If the map is étale-maximal, then

\[
\operatorname{Conf}_3(f)
\simeq
\operatorname{Conf}_2(f).
\]

Therefore every étale-maximal degree-three Keller map has the same **top-level
configuration collapse** as the known marked-cubic map.

What remains special about the marked-cubic map is not merely the equality of
these two configuration varieties; it is that this equality is explicitly
realized by “the ordered pair determines the third root,” and that the entire
fiber census is globally \(3/1/0\).

This corrects the earlier impression that
\(\operatorname{Conf}_3\simeq\operatorname{Conf}_2\)
was unique to the symmetric cubic construction.

---

# 13. Criteria we should now try to turn into exclusions

The strongest groupoid-derived candidates are:

## A. Étale-maximality obstruction

Try to prove that planar Keller maps must be étale-maximal.

Then:

\[
\operatorname{Conf}_d\simeq\operatorname{Conf}_{d-1}
\]

is mandatory, and all generic fiber deficits are supports of nontrivial
permutations.

## B. Unit-rank plus parallelizability

Each top configuration component is a smooth affine parallelizable surface with

\[
\operatorname{rank}\mathcal O^*/\mathbb C^*
\ge \#\{\text{components of }S_f\}.
\]

Classify or bound such surfaces under the additional finite-Galois-cover
structure.

## C. Large finite-group action

Under étale maximality,
\(\operatorname{Conf}_{d-1}\) is the \(S_d\)-frame torsor of the proper cover.

Use bounds on finite automorphism groups of log-general-type surface
compactifications to constrain \(d\) in terms of the log geometry of
\(\mathbb A^2\setminus S_f\).

## D. Secant-projector degree identities

Exploit

\[
\delta_F(\delta_F-c)\in(P(x)-P(y),Q(x)-Q(y))
\]

with degree bounds / Gröbner elimination to obtain coefficient or Newton-polygon
restrictions on a planar Keller pair.

## E. Configuration deletion formula

Replace arbitrary deletion data in the decorated-curve search by integers

\[
t_C=d-m_C-\operatorname{supp}(\sigma_C)
\]

read directly from the configuration filtration.

This should substantially shrink the plane-curve parameter space.

---

# 14. Provenance note

The configuration-space framing, top-stabilization/maximality criterion, and
fiber-deficit decomposition are being developed in the present project.

The planar secant/projector algebra overlaps with a recent independent
2026 collision-geometry development.  Cite that work for independent
confirmation and do not make an unqualified priority claim.

Classical inputs such as normal-extension rigidity, Zariski Main,
ramification/inertia theory, and plane nonproperness-curve theorems remain
literature results.
