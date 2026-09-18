# Étale-Maximality Attack in Dimension Two

**Snapshot:** 2026-09-14  
**Goal:** prove, or reduce to a minimal residual obstruction, that a plane Keller map
\[
F:\mathbb A^2_\mathbb C\to\mathbb A^2_\mathbb C
\]
exhausts the étale locus of its finite Zariski--Main normalization.

## Status

A complete proof of étale-maximality is **not obtained** here.

What is obtained is a substantial reduction:

> Any failure of étale-maximality must be carried by an omitted boundary curve
> \(E\simeq\mathbb A^1\) which is étale over the target surface and maps
> isomorphically onto the normalization of a singular **multibranch**
> component of the nonproperness curve.

Thus unramified deletion is not arbitrary. It can occur only through conductor/self-identification
of a rational parametric target curve.

This removes isolated missing étale points, cusp/unibranch targets, and arbitrary deleted sheets
from the possible obstruction.

---

# 1. Finite normalization

Let

\[
A=\mathbb C[u,v],\qquad R=\mathbb C[x,y],
\]

with \(A\hookrightarrow R\) induced by \(F\).

Let \(L=\operatorname{Frac}R\), and let \(B\) be the integral closure of \(A\) in \(L\).
Put

\[
\overline X=\operatorname{Spec}B,
\qquad
\pi:\overline X\to\mathbb A^2.
\]

Zariski's Main Theorem gives an open immersion

\[
j:U=\mathbb A^2\hookrightarrow\overline X
\]

such that

\[
F=\pi\circ j.
\]

The finite normalization \(\overline X\) is irreducible, normal, affine, and rational.

Define the boundary

\[
D=\overline X\setminus U.
\]

The Keller map is **étale-maximal** iff

\[
U=\operatorname{Et}(\pi),
\]

equivalently iff \(D\) contains no point at which \(\pi\) is étale.

---

# 2. No isolated boundary points

## Proposition EM-1

The boundary \(D\) is pure of codimension one.

### Proof

Let \(D^{(1)}\) be the union of all one-dimensional irreducible components of \(D\), and set

\[
W=\overline X\setminus D^{(1)}.
\]

Then

\[
U\subseteq W
\]

and \(W\setminus U\) is finite.

Because \(W\) is normal and the omitted set has codimension two,

\[
\Gamma(W,\mathcal O_W)
=
\Gamma(U,\mathcal O_U)
=
\mathbb C[x,y].
\]

Thus the affinization map

\[
a:W\to\mathbb A^2=\operatorname{Spec}\mathbb C[x,y]
\]

restricts to the identity on \(U\simeq\mathbb A^2\).

Its fibers are finite: over any point there is the unique point belonging to \(U\), together
with at most finitely many points of \(W\setminus U\). Hence \(a\) is quasi-finite and birational.

By Zariski's Main Theorem, a quasi-finite birational morphism to the normal variety
\(\mathbb A^2\) is an open immersion.

But \(a(U)=\mathbb A^2\), so the image is already the entire target. Hence \(a\) is an
isomorphism and \(W=U\).

Therefore \(W\setminus U=\varnothing\), and every boundary point lies on a boundary divisor.

---

# 3. Geometry of boundary components

We now use Jelonek's affine-extension theorem.

For a normal affine extension of a smooth acyclic surface, every horizontal boundary component
is homeomorphic to \(\mathbb C\), and distinct horizontal components are disjoint.
For our finite map \(\pi\), a boundary curve cannot be vertical (a finite morphism cannot contract
a curve to a point). Therefore every irreducible boundary component is horizontal.

Hence:

\[
\boxed{
E_i\cap E_j=\varnothing\quad(i\neq j)
}
\]

for boundary prime divisors \(E_i\), and each has the topology of the affine line.

Reference: Jelonek, *Testing sets for properness of polynomial mappings*,
Math. Ann. 315 (1999), Theorem 4.6; the same paper explicitly applies the result to
normal affine extensions of smooth acyclic surfaces in §9.

---

# 4. A generically unramified boundary divisor is everywhere étale

## Proposition EM-2

Let \(E\subset D\) be a boundary prime divisor such that \(\pi\) is unramified at the generic
point of \(E\). Then \(\pi\) is étale at every point of \(E\).

In particular \(\overline X\) is smooth along \(E\), and

\[
E\simeq\mathbb A^1.
\]

### Proof

Suppose \(\pi\) fails to be étale at some \(p\in E\).

The target is the regular surface \(\mathbb A^2\). For a finite generically separable morphism
from a normal surface, purity of the branch locus implies that non-étaleness is detected
divisorially. Thus some ramification divisor \(R\) passes through \(p\).

The affine source \(U\) is Keller-étale, so every ramification divisor lies in the boundary
\(D\).

Since \(E\) is generically unramified,

\[
R\neq E.
\]

But then two distinct boundary curves \(R\) and \(E\) intersect at \(p\), contradicting
Jelonek's disjointness theorem.

Therefore \(\pi\) is étale everywhere along \(E\). Hence \(E\) is smooth. Since it is an
affine smooth curve homeomorphic to \(\mathbb C\), it is algebraically isomorphic to
\(\mathbb A^1\).

---

# 5. The target curve and its normalization

Let

\[
C=\pi(E)\subset\mathbb A^2.
\]

Then \(C\) is an irreducible component of the nonproperness curve \(S_F\).

By the plane nonproperness theorems, \(C\) is polynomial-parametric and its normalization is

\[
\widetilde C\simeq\mathbb A^1.
\]

The finite map \(E\to C\) factors uniquely through normalization:

\[
E\xrightarrow{g}\widetilde C\xrightarrow{\nu}C.
\]

## Proposition EM-3

The map

\[
g:E\to\widetilde C
\]

is an isomorphism.

### Proof

Since \(\pi\) is étale along \(E\), the differential of the map \(E\to\mathbb A^2\) is
everywhere nonzero.

If \(dg\) vanished at a point, then the differential of

\[
E\to\widetilde C\to C\hookrightarrow\mathbb A^2
\]

would vanish there. Hence \(g\) is unramified.

Thus \(g:\mathbb A^1\to\mathbb A^1\) is a finite étale morphism.
Over \(\mathbb C\), the affine line has no nontrivial connected finite étale covers.

Therefore \(\deg g=1\), so \(g\) is an isomorphism.

Consequently:

\[
\boxed{
E\simeq\widetilde C\simeq\mathbb A^1
}
\]

and the restriction \(E\to C\) is exactly the normalization morphism.

---

# 6. Unibranch singularities are impossible for an unramified deleted sheet

## Corollary EM-4

If \(E\) is a generically unramified boundary divisor as above, then the target curve
\(C=\pi(E)\) has no singular unibranch points.

### Proof

At every \(p\in E\), the surface map \(\pi\) is étale. Hence locally analytically it is an
isomorphism.

The smooth curve \(E\) is therefore carried immersively to a smooth local branch of \(C\).

If a singular point of \(C\) were unibranch, its normalization map would fail to be immersive
at its unique preimage; otherwise the plane-curve germ would be smooth.

But \(E\to C\) is the normalization and is immersive everywhere. Contradiction.

Thus every finite singularity of \(C\) must have at least two branches.

---

# 7. The target component must actually be singular

Nguyen Van Chau's affine-line component theorem says that if the nonproperness set of a
nonsingular polynomial map \(\mathbb C^2\to\mathbb C^2\) has an irreducible component
isomorphic to \(\mathbb A^1\), then the map must have a critical point.

A Keller map has no critical points.

Hence \(C\) cannot itself be smooth \(\mathbb A^1\).

Combining this with EM-4 gives:

## Theorem EM-5 — Residual obstruction to étale-maximality

If a plane Keller map is not étale-maximal, then its finite normalization contains an omitted
boundary divisor

\[
E\simeq\mathbb A^1
\]

which is everywhere étale over the target and maps as the normalization

\[
E\simeq\widetilde C\to C
\]

of an irreducible nonproperness component \(C\), where:

1. \(C\) is rational and polynomial-parametric;
2. \(C\) has one place at infinity;
3. \(C\) is singular;
4. **every singularity supporting this deleted normalization sheet is multibranch**.

Thus the only possible unramified deletion mechanism is conductor gluing / self-identification
of distinct points of \(\mathbb A^1\).

In particular:

\[
\boxed{
\text{if every component of }S_F\text{ is unibranch at its finite singularities, then }F
\text{ is étale-maximal.}
}
\]

This is a genuine conditional maximality criterion.

---

# 8. Fiber-length consequences at a conductor point

The normal surface \(\overline X\) is Cohen--Macaulay (normal surfaces satisfy \(S_2\)).
Since

\[
\pi:\overline X\to\mathbb A^2
\]

is finite onto a regular surface of the same dimension, miracle flatness implies that
\(\pi\) is finite flat of constant degree \(d\).

Let \(b\in C\) be a singular point with \(r\ge2\) analytic branches.

Because

\[
E\simeq\widetilde C,
\]

there are \(r\) distinct points

\[
p_1,\ldots,p_r\in E
\]

over \(b\). Each is an étale point of \(\pi\), hence contributes scheme length one to the
finite fiber of \(\pi\).

Therefore

\[
\boxed{
\#F^{-1}(b)\le d-r.
}
\]

More generally, if \(t_C\) distinct generically unramified boundary normalization sheets lie
over the same component \(C\), then

\[
\boxed{
\#F^{-1}(b)\le d-t_C r.
}
\]

This is visible in the Keller configuration filtration: conductor self-identification of the
deleted sheets forces a deeper fiber-cardinality drop at the singular point.

---

# 9. Reformulation in configuration-space language

Let \(m_C\) be the generic affine fiber cardinality along \(C\).

The configuration tower determines \(m_C\): it is the largest \(k\) such that a generic point
of \(C\) lies in the image closure of

\[
\operatorname{Conf}_k(F)\to\mathbb A^2.
\]

Let \(\sigma_C\) be generic inertia in the finite normalization and let \(t_C\) be the number
of generically omitted unramified sheets.

Then

\[
d-m_C
=
\operatorname{supp}(\sigma_C)+t_C.
\]

The present analysis identifies the geometric meaning of \(t_C\):

> Every unit of \(t_C\) is an actual omitted copy of
> \(\widetilde C\simeq\mathbb A^1\), and such a copy is possible only if \(C\) possesses
> multibranch conductor identifications.

Thus deletion data is converted into explicit conductor geometry of the plane curve.

---

# 10. Why the proof stops here

A rational one-place-at-infinity plane curve can certainly have only multibranch affine
singularities. For example, rational nodal curves with a single place at infinity exist.

At such a node, an étale neighborhood of one point of the normalization can map to one branch,
and another normalization point can map to the other branch. This is perfectly compatible with
local étaleness of the ambient surface map.

Therefore the argument does **not** eliminate the final conductor-gluing case.

This agrees with the current frontier in independent 2026 work: the remaining plane boundary
problem is precisely the singular multibranch/conductor case, not generic tameness.

So at present:

\[
\boxed{
\text{plane étale-maximality}
\quad\text{has been reduced to excluding unramified normalization sheets over
multibranch singularities.}
}
\]

---

# 11. Next targets

The remaining obstruction is far smaller than arbitrary deletion data.

## Target A — conductor budget

At a singularity \(b\) with \(r\) branches, combine

\[
\#F^{-1}(b)\le d-t_Cr
\]

with the generic fiber count along \(C\), local conductor length, and the degree-\(d\) flatness
budget.

Seek an inequality that becomes impossible after summing over all conductor points.

## Target B — genus budget

Since \(C\) has normalization \(\mathbb A^1\), all arithmetic genus is consumed by its finite
singularities and the infinity singularity.

The multibranch singularities required by unramified deletion consume at least

\[
\delta_b\ge r_b-1.
\]

Combine this lower bound with the delta-sequence at infinity.

## Target C — pullback divisor in the affine source

If \(h=0\) defines \(C\), then

\[
h\circ F=0
\]

is a reduced plane curve because \(F\) is étale.

At a multibranch conductor point of \(C\), every affine preimage is analytically a copy of the
same singular germ.

Meanwhile the missing normalization sheet appears only at infinity in the source.

Relate the genus/intersection data of the principal curve \(V(h\circ F)\subset\mathbb A^2\) to
the missing normalization branch.

## Target D — prove no multibranch survivor

A theorem excluding this final case would prove planar étale-maximality.

Then the entire deletion parameter \(\mathcal E\) disappears from the parameter-reduction program.

---

# 12. Literature boundary

Inputs used here:

- Zariski's Main Theorem.
- Purity of the branch locus.
- Miracle flatness for a finite Cohen--Macaulay surface over a regular surface.
- Z. Jelonek, *Testing sets for properness of polynomial mappings*,
  Math. Ann. 315 (1999), especially Theorem 4.6 and §9.
- Nguyen Van Chau's nonproperness-curve and affine-line-component results.

The reduction EM-1--EM-5 is assembled here for the Keller-groupoid program.
Before a priority claim is made, it should be checked against the current plane-JC boundary
literature and discussed with researchers working on finite-normalization approaches.
