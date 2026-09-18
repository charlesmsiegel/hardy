# Keller Groupoids and Fiber Configuration Geometry

**Working draft / research-state document**  
**Snapshot:** 2026-09-14

This document reframes the existing Keller-threefold work around the **Keller groupoid**
attached to an arbitrary Keller map.  The goal is to extract everything that is formal or
general from the one known nontrivial example before specializing to the marked-cubic
degree-three map.

The intended division is:

1. general machinery valid for every complex Keller map;
2. finite-degree and monodromy consequences;
3. topology of the Čech nerve;
4. finite-stabilization principles;
5. the marked-cubic example as the first worked nontrivial Keller groupoid;
6. a research program aimed particularly at dimension two.

The document distinguishes **proved here**, **standard imported machinery**, and **open program**.
Nothing in the open-program sections is being promoted to a theorem.

---

# 1. Keller maps and the relation groupoid

## Definition 1.1 — Keller map

A **complex Keller map** is a polynomial map

\[
f\colon X=\mathbb A^n_{\mathbb C}\longrightarrow
Y=\mathbb A^n_{\mathbb C}
\]

whose Jacobian determinant is a nonzero constant.

Equivalently, \(f\) is étale.  In particular it is flat, unramified, locally
quasi-finite, separated, and open.

Write

\[
U=f(X)\subseteq Y.
\]

Since an étale map is open, \(U\) is an open subvariety and

\[
f\colon X\longrightarrow U
\]

is a surjective étale morphism.

For a dominant Keller map, let

\[
d=[\mathbb C(X):\mathbb C(Y)]
\]

be its **generic degree**.

---

## Definition 1.2 — Keller relation groupoid

Define

\[
R_f=X\times_Y X
=\{(x_0,x_1):f(x_0)=f(x_1)\}.
\]

The two projections give source and target maps

\[
s,t\colon R_f\rightrightarrows X,
\qquad
s(x_0,x_1)=x_0,\quad t(x_0,x_1)=x_1.
\]

Together with

\[
e(x)=(x,x),\qquad
i(x_0,x_1)=(x_1,x_0),
\]

and

\[
(x_0,x_1)\circ(x_1,x_2)=(x_0,x_2),
\]

this is a **thin étale groupoid**: there is at most one arrow between any
ordered pair of objects, and an arrow exists exactly when the two points
have the same \(f\)-image.

We call this the **Keller groupoid** of \(f\), denoted \(\mathcal G_f\).

Because both maps \(X\to Y\) factor through \(U\),

\[
X\times_Y X=X\times_U X.
\]

Thus \(\mathcal G_f\) is exactly the equivalence-relation groupoid of the
surjective étale map \(X\to U\).

---

## Proposition 1.3 — The diagonal is a connected component

The identity section

\[
\Delta_X\colon X\hookrightarrow R_f
\]

is both open and closed.

### Proof

A Keller map is unramified, hence its diagonal is an open immersion.
It is also separated, hence its diagonal is a closed immersion.  Therefore
the diagonal is open and closed.

This is the standard unramified/separated diagonal theorem; see Stacks
Project Tags `02GE` and `024T`.

Consequently,

\[
R_f=\Delta_X\sqcup \operatorname{Conf}_2(f),
\]

where

\[
\operatorname{Conf}_2(f)=\{(x_0,x_1):f(x_0)=f(x_1),\ x_0\ne x_1\}.
\]

The space \(\operatorname{Conf}_2(f)\) is the first **fiber configuration variety**.

---

## Corollary 1.4 — Automorphisms are exactly the collision-free case

For a complex Keller map,

\[
\operatorname{Conf}_2(f)=\varnothing
\quad\Longleftrightarrow\quad
f\text{ is injective}
\quad\Longleftrightarrow\quad
f\in\operatorname{Aut}(\mathbb A^n).
\]

Hence the Jacobian conjecture in dimension \(n\) can be restated as:

> Every Keller groupoid on \(\mathbb A^n\) is the unit groupoid.

The last implication uses the standard characteristic-zero injective-polynomial-map
theorem (Ax–Grothendieck).

This is the most basic groupoid reformulation: **a counterexample is exactly a
Keller groupoid with nonempty fiber configuration space.**

---

# 2. The Čech nerve

## Definition 2.1 — Higher Keller fiber products

For \(r\ge1\), put

\[
K_r(f)
=
\underbrace{X\times_Y X\times_Y\cdots\times_Y X}_{r\text{ factors}}.
\]

Thus

\[
K_r(f)
=
\{(x_1,\dots,x_r):f(x_1)=\cdots=f(x_r)\}.
\]

We index this so that

\[
K_1(f)=X,\qquad K_2(f)=R_f.
\]

Deleting a coordinate gives a face map; repeating a coordinate gives a
degeneracy map.  These spaces form the Čech nerve of \(X\to U\), or
equivalently the nerve of the Keller groupoid.

---

## Proposition 2.2 — Every level is a smooth affine \(n\)-fold

For every \(r\ge1\):

1. \(K_r(f)\) is affine and of finite type;
2. every projection
   \[
   p_i\colon K_r(f)\to X
   \]
   is étale;
3. \(K_r(f)\) is smooth of pure dimension \(n\).

### Proof

Fiber products of affine schemes over an affine scheme are affine.
Each projection is obtained by repeated base change from the étale map
\(f\), hence is étale.  An étale map to the smooth \(n\)-fold \(X\) has
smooth \(n\)-dimensional source.

Thus the simplicial direction grows indefinitely while the algebraic
dimension never grows.

---

## Proposition 2.3 — Canonical algebraic parallelism

Every \(K_r(f)\), and every open-and-closed subvariety of it, has trivial
algebraic tangent bundle.

In particular, every fiber configuration variety introduced below is algebraically
parallelizable:

\[
T_{K_r(f)}\simeq\mathcal O_{K_r(f)}^{\oplus n}.
\]

### Proof

For any projection \(p_i\colon K_r(f)\to X\), étaleness gives

\[
T_{K_r(f)}\simeq p_i^*T_X.
\]

Since \(T_{\mathbb A^n}\) is trivial, so is \(T_{K_r(f)}\).

This is a useful additional restriction on every variety appearing in a Keller
groupoid: they are not merely smooth affine \(n\)-folds, but smooth affine
**parallelizable** \(n\)-folds.

---

## Proposition 2.4 — The infinite nerve is categorically finite data

The simplicial object \(K_\bullet(f)\) is the nerve of a groupoid and hence is
\(2\)-coskeletal in the standard categorical sense.

Informally:

> once \(K_1\), \(K_2\), and the source/target/composition/unit/inverse maps are
> known, every higher \(K_r\) and every simplicial structure map is determined
> by iterated fiber products.

This is a **categorical stabilization** valid for every Keller map.

It must not be confused with the much stronger statement that the higher
\(K_r\) have no new isomorphism types of connected components.  The latter is
a geometric question addressed below.

---

# 3. Collision varieties and partition decomposition

## Definition 3.1 — Ordered \(k\)-fiber configuration variety

For \(k\ge1\), define

\[
\operatorname{Conf}_k(f)=
\{(x_1,\dots,x_k)\in K_k(f):x_i\ne x_j\text{ for }i\ne j\}.
\]

Thus

\[
\operatorname{Conf}_1(f)=X,\qquad \operatorname{Conf}_2(f)=\text{ordered two-point configurations}.
\]

Because the conditions \(x_i=x_j\) are open-and-closed conditions in the
relevant fiber products, \(\operatorname{Conf}_k(f)\) is itself open and closed in \(K_k(f)\).

Hence every \(\operatorname{Conf}_k(f)\) is a smooth affine parallelizable \(n\)-fold, possibly
disconnected or empty.

---

## Theorem 3.2 — Partition decomposition

Let \(\Pi(r)\) denote the set of set partitions of \(\{1,\dots,r\}\).
For \(\pi\in\Pi(r)\), let \(|\pi|\) be its number of blocks.

Then

\[
\boxed{
K_r(f)\simeq
\coprod_{\pi\in\Pi(r)} \operatorname{Conf}_{|\pi|}(f).
}
\]

More precisely, the summand corresponding to \(\pi\) is the locus on which

\[
x_i=x_j
\quad\Longleftrightarrow\quad
i,j\text{ lie in the same block of }\pi.
\]

### Proof

For each pair \(i,j\), the equalizer condition \(x_i=x_j\) is the pullback of
the diagonal of the unramified separated map \(f\), and is therefore open and
closed.  Intersecting the required equalities and inequalities for a fixed set
partition produces an open-and-closed stratum.

If \(\pi\) has \(k\) blocks, order those blocks by their least elements.  Reading
the common coordinate value on each block gives an isomorphism with \(\operatorname{Conf}_k(f)\).

---

## Corollary 3.3 — Stirling-number form

Let \(S(r,k)\) be the Stirling number of the second kind. Then

\[
\boxed{
K_r(f)\simeq
\coprod_{k\ge1} S(r,k)\,\operatorname{Conf}_k(f),
}
\]

where \(mZ\) means the disjoint union of \(m\) copies of \(Z\).

This partition decomposition is completely general.  What is special in the
marked-cubic example is that several different \(\operatorname{Conf}_k\)'s turn out to be
isomorphic.

---

# 4. Generic degree bounds the configuration depth

The next point is crucial: the generic degree gives a *uniform maximum* on
fiber cardinality for an étale quasi-finite map.

## Lemma 4.1 — No fiber has more points than the generic degree

If \(f\) has generic degree \(d\), then

\[
\#f^{-1}(y)\le d
\]

for every \(y\in Y\).

### Proof sketch

Suppose \(y\) has \(m\) distinct preimages \(x_1,\dots,x_m\).
Because \(f\) is étale and quasi-finite, after shrinking around \(y\) one can
choose pairwise disjoint neighborhoods of the \(x_i\) that persist as \(m\)
separate étale local sheets over a common target neighborhood.  Hence a generic
point of that neighborhood has at least \(m\) preimages.  By definition the
generic number is \(d\), so \(m\le d\).

---

## Corollary 4.2 — Finite configuration palette

\[
\operatorname{Conf}_k(f)=\varnothing
\qquad(k>d).
\]

Therefore

\[
\boxed{
K_r(f)\simeq
\coprod_{k=1}^{\min(r,d)}
S(r,k)\,\operatorname{Conf}_k(f).
}
\]

In particular:

> **Every finite-degree Keller groupoid has only finitely many geometric
> fiber configuration spaces \(\operatorname{Conf}_1,\dots,\operatorname{Conf}_d\), even though its Čech nerve has infinitely
> many levels.**

This is stronger than the conditional stabilization principle we initially
expected: all connected-component isomorphism types occurring in the entire
Keller groupoid necessarily occur by level \(d\).

The special example below stabilizes *earlier* than this universal bound.

---

# 5. The fiber-cardinality filtration

## Definition 5.1

For \(1\le k\le d\), define

\[
U_{\ge k}
=
\{y\in Y:\#f^{-1}(y)\ge k\}.
\]

Also put

\[
U_m
=
\{y\in Y:\#f^{-1}(y)=m\}
\qquad(0\le m\le d).
\]

Thus \(U_{\ge1}=U=f(X)\), and \(U_0=Y\setminus U\).

---

## Proposition 5.2 — Collision images recover the filtration

The common-image map

\[
q_k\colon \operatorname{Conf}_k(f)\to Y,
\qquad
(x_1,\dots,x_k)\mapsto f(x_1),
\]

is étale and has image

\[
q_k(\operatorname{Conf}_k(f))=U_{\ge k}.
\]

Consequently \(U_{\ge k}\) is open and

\[
U_{\ge1}\supseteq U_{\ge2}\supseteq\cdots\supseteq U_{\ge d}.
\]

Over a point \(y\in U_m\), the fiber of \(q_k\) has cardinality

\[
(m)_k
=
m(m-1)\cdots(m-k+1)
=
\frac{m!}{(m-k)!}.
\]

### Proof

The map \(q_k=f\circ p_1\) is a composition of étale maps, hence étale and
therefore open.  Its geometric fiber is exactly the set of ordered \(k\)-tuples
of distinct elements of the finite set \(f^{-1}(y)\).

---

## Proposition 5.3 — The full-fiber locus is the proper locus

The locus \(U_{\ge d}=U_d\) is exactly the locus over which \(f\) is proper.
Equivalently, if \(S_f\) denotes the nonproperness set,

\[
\boxed{
S_f=Y\setminus U_d.
}
\]

### Proof sketch

If \(f\) is proper in a neighborhood of \(y\), then it is finite étale there.
Its degree is locally constant and equals the generic degree \(d\), so the fiber
has \(d\) points.

Conversely, suppose the fiber over \(y\) has \(d\) points.  Étaleness gives
\(d\) disjoint local sheets over a sufficiently small common target
neighborhood.  By Lemma 4.1 there can be no additional sheets anywhere over
that neighborhood.  Hence the full inverse image of that neighborhood is the
union of those \(d\) finite étale sheets, so \(f\) is finite, hence proper,
there.

Thus the groupoid recovers not just the image but the entire fiber-drop
filtration and the nonproperness locus.

---

# 6. The configuration tower

Deleting the last coordinate gives

\[
\rho_k\colon \operatorname{Conf}_k(f)\longrightarrow C_{k-1}(f).
\]

## Proposition 6.1

The map \(\rho_k\) is étale.  If a point of \(C_{k-1}\) lies over an
\(m\)-point fiber of \(f\), then its \(\rho_k\)-fiber contains exactly

\[
m-k+1
\]

points.

Generically,

\[
\deg(\rho_k)=d-k+1.
\]

---

## Proposition 6.2 — Top collision open immersion

The top forgetful map

\[
\rho_d\colon \operatorname{Conf}_d(f)\to \operatorname{Conf}_{d-1}(f)
\]

is an open immersion.

Its image is exactly the locus of ordered \((d-1)\)-tuples lying over a
\(d\)-point fiber.

Therefore:

\[
\boxed{
\operatorname{Conf}_d(f)\simeq \operatorname{Conf}_{d-1}(f)
\quad\Longleftrightarrow\quad
f\text{ has no fiber of cardinality }d-1.
}
\]

### Proof

The map is étale.  A \((d-1)\)-tuple can have at most one additional point,
because no fiber contains more than \(d\) points.  Thus \(\rho_d\) is
universally injective and étale, hence an open immersion.

It is surjective exactly when every fiber supporting a \((d-1)\)-tuple has a
remaining \(d\)-th point, i.e. exactly when there is no \((d-1)\)-point fiber.

This is the abstract mechanism behind the marked-cubic identity
\(\operatorname{Conf}_3\simeq\operatorname{Conf}_2\).

---

# 7. Monodromy and connected components

Let

\[
V=U_d=Y\setminus S_f.
\]

Then

\[
f^{-1}(V)\to V
\]

is a finite étale cover of degree \(d\).

Choose a geometric base point and write

\[
G\le S_d
\]

for its monodromy group.  Since \(f^{-1}(V)\) is a nonempty open subset of
the irreducible variety \(X\), it is irreducible, hence the action of \(G\) on
the \(d\) sheets is transitive.

---

## Theorem 7.1 — Collision components are monodromy orbits

Over \(V\),

\[
\operatorname{Conf}_k(f)|_V\to V
\]

is the finite étale cover associated with the \(G\)-set

\[
\operatorname{Inj}(\{1,\dots,k\},\{1,\dots,d\}).
\]

Consequently, connected components of \(\operatorname{Conf}_k(f)\) are in bijection with
\(G\)-orbits on ordered \(k\)-tuples of distinct sheets.

In particular:

\[
\operatorname{Conf}_k(f)\text{ is irreducible}
\quad\Longleftrightarrow\quad
G\text{ is }k\text{-transitive}.
\]

### Reason

Every global component meets the dense proper locus \(V\); on a smooth variety
connected components are irreducible components, and an open subset of an
irreducible component remains irreducible.  Thus the generic finite-cover
components and the global components correspond.

---

## Corollary 7.2 — The top configuration is the Galois closure

The fiber of \(\operatorname{Conf}_d|_V\) is the set of all orderings of the \(d\) sheets, hence
has \(d!\) elements.

The action of \(G\) on these orderings is free.  Therefore:

- \(\operatorname{Conf}_d|_V\) has
  \[
  \frac{d!}{|G|}
  \]
  connected components;
- each component is a finite étale Galois cover of \(V\) with deck group \(G\);
- each component is a geometric realization of the Galois closure of the
  function-field extension.

Thus the top fiber configuration space makes the monodromy/Galois closure intrinsic to
the Keller groupoid.

---

## Corollary 7.3 — Normal extensions are groupoid-simple and forbidden for counterexamples

If the original function-field extension is Galois, then the monodromy action
on the \(d\) sheets is regular, so \(|G|=d\).

Campbell's classical normal-extension criterion says that a complex Keller map
with normal function-field extension is invertible.  Therefore a nontrivial
Keller groupoid must have

\[
|G|>d.
\]

For \(d=2\), every transitive degree-two group is regular.  Hence there is no
degree-two complex Keller counterexample.

For \(d=3\), the first non-Galois transitive possibility is \(S_3\); this is
exactly the monodromy of the marked-cubic example.

This explains structurally why generic degree three is the first possible
degree for the known construction.

---

# 8. Quotients of fiber configuration spaces

The symmetric group \(S_k\) acts freely on \(\operatorname{Conf}_k(f)\) by permuting coordinates.

The affine quotient

\[
B_k(f)=\operatorname{Conf}_k(f)/S_k
\]

parametrizes unordered \(k\)-element subsets of a fiber.

Over \(U_m\), its fiber has cardinality

\[
\binom{m}{k}.
\]

At the top level there is a unique \(d\)-element subset of a \(d\)-element fiber,
so

\[
\boxed{
B_d(f)\simeq U_d=Y\setminus S_f.
}
\]

Thus the proper locus itself is the symmetric quotient of the top configuration
variety.

Over the proper locus there is also the usual complement duality

\[
B_k|_V\simeq B_{d-k}|_V,
\]

sending a subset of the \(d\) sheets to its complement.

---

# 9. Additive invariants and Stirling calculus

The partition decomposition immediately gives identities in the Grothendieck
ring of varieties:

\[
\boxed{
[K_r(f)]
=
\sum_{k=1}^d S(r,k)[\operatorname{Conf}_k(f)].
}
\]

No monodromy assumption is involved.

The same formula holds for every additive invariant \(I\) of complex algebraic
varieties:

\[
\boxed{
I(K_r)
=
\sum_{k=1}^d S(r,k)I(\operatorname{Conf}_k).
}
\]

Examples include Euler characteristic and any invariant obtained from a
Grothendieck-ring realization for which the required additivity is available.

---

## Corollary 9.1 — Exponential generating function

Formally,

\[
\sum_{r\ge1}I(K_r)\frac{t^r}{r!}
=
\sum_{k=1}^d
I(\operatorname{Conf}_k)\frac{(e^t-1)^k}{k!}.
\]

Thus the entire infinite sequence of levelwise additive invariants is determined
by the first \(d\) configuration invariants.

---

## Corollary 9.2 — Stirling inversion

The configuration invariants can conversely be recovered from the level invariants:

\[
I(\operatorname{Conf}_k)
=
\sum_{r=1}^k s(k,r)I(K_r),
\]

where \(s(k,r)\) are the signed Stirling numbers of the first kind.

So \(K_\bullet\) and \(C_\bullet\) carry the same additive information, expressed
in two natural bases.

---

## Corollary 9.3 — Linear recurrence

Since \(S(r,k)\) is a linear combination of

\[
1^r,2^r,\dots,k^r,
\]

the scalar sequence

\[
a_r=I(K_r)
\]

is a linear combination of

\[
1^r,2^r,\dots,d^r.
\]

Hence it satisfies the constant-coefficient recurrence whose characteristic
polynomial is

\[
\boxed{
\prod_{j=1}^d(T-j).
}
\]

Equivalently, its ordinary generating function is rational with denominator
dividing

\[
\prod_{j=1}^d(1-jz).
\]

This is one concrete sense in which the infinite Keller nerve is automatically
finite-state at the level of additive invariants.

---

# 10. Finite appearance of connected-component types

This section records the stabilization principle that motivated the present
reframing.

Let

\[
\operatorname{Conf}_k=\coprod_j a_{k,j}Z_j
\]

where the \(Z_j\)'s are representatives of connected-component isomorphism
types and \(a_{k,j}\) their multiplicities.

Because \(k\le d\), there are only finitely many \(Z_j\)'s.

## Theorem 10.1 — Finite geometric palette

Every connected component of every \(K_r(f)\) is isomorphic to a connected
component of one of

\[
\operatorname{Conf}_1(f),\dots,\operatorname{Conf}_d(f).
\]

Therefore all connected-component isomorphism types appear by finite step
\(d\).

More generally, suppose all component types happen already to appear by some
step \(m<d\).  Then every later level is a disjoint union of that finite palette.

If \(N_j(r)\) is the number of copies of \(Z_j\) in \(K_r\), then

\[
\boxed{
N_j(r)=\sum_{k=1}^d a_{k,j}S(r,k).
}
\]

Consequently every multiplicity sequence \(N_j(r)\):

- is a linear combination of \(1^r,\dots,d^r\);
- satisfies the recurrence \(\prod_{i=1}^d(T-i)\);
- has a rational ordinary generating function.

So an early geometric collapse has immediate, testable combinatorial
consequences at **all** later levels.

---

## Definition 10.2 — Geometric generation depth

Define the **geometric generation depth**

\[
g(f)
\]

to be the least \(m\) such that every connected-component isomorphism type
appearing in every \(\operatorname{Conf}_k\) already appears among \(\operatorname{Conf}_1,\dots,\operatorname{Conf}_m\).

Always

\[
1\le g(f)\le d.
\]

For an automorphism, \(g(f)=1\).

For the marked-cubic example, \(g(F)=2\).

This is a first candidate for a quantitative notion of **configuration complexity**.

---

## Stronger notion: finite morphism type

Object-type stabilization does not by itself determine all topology of the
realization, because the face maps matter.

A stronger condition is:

> up to chosen component isomorphisms, only finitely many morphism types occur
> among restrictions of face and degeneracy maps.

Call this **finite morphism type**.

Under finite morphism type, applying any functor such as homology turns the
entire infinite simplicial object into a finite-state system: finitely many
modules and finitely many linear maps occur, with combinatorially varying
multiplicities.

The marked-cubic groupoid satisfies an exceptionally strong version of this
condition; see Section 14.

This suggests a hierarchy:

\[
\text{finite generic degree}
\Rightarrow
\text{finite object palette}
\]

always, while

\[
\text{early palette stabilization}
\]

and

\[
\text{finite morphism type}
\]

measure extra simplicity.

---

# 11. Euler characteristics of fiber-cardinality strata

Let

\[
a_m=\chi(U_m),
\qquad
b_k=\chi(\operatorname{Conf}_k).
\]

Since

\[
\operatorname{Conf}_k|_{U_m}\to U_m
\]

is a finite topological covering of degree \((m)_k\),

\[
\boxed{
b_k
=
\sum_{m=k}^d (m)_k\,a_m.
}
\]

This is a triangular system with diagonal entries \(k!\).  Therefore the Euler
characteristics of the exact fiber-cardinality strata are recoverable from the
collision Euler characteristics.

Since

\[
\chi(Y)=\chi(\mathbb A^n)=1,
\]

we also have

\[
1
=
a_0+\sum_{m=1}^d a_m.
\]

And because

\[
b_1=\chi(X)=1
=
\sum_{m=1}^d m\,a_m,
\]

subtracting gives

\[
\boxed{
a_0
=
\sum_{m=1}^d (m-1)a_m.
}
\]

Thus the Euler characteristic of the omitted locus is encoded by the
fiber-drop strata.

This is potentially useful in dimension two, where the omitted locus of a
Keller map is finite.

---

# 12. Topological realization of the Keller groupoid

The following is standard descent/topological-groupoid machinery rather than a
new theorem.

Because

\[
f\colon X^{an}\to U^{an}
\]

is a surjective local homeomorphism, the topological Čech nerve

\[
K_\bullet(f)^{an}
\]

is a simplicial resolution of \(U^{an}\).

Its geometric realization satisfies, in the standard homotopy-descent sense,

\[
\boxed{
|K_\bullet(f)^{an}|\simeq U^{an}.
}
\]

Equivalently, the quotient of the étale equivalence-relation groupoid recovers
the image \(U\).

There is a homological descent spectral sequence

\[
\boxed{
E^1_{p,q}
=
H_q(K_{p+1}(f)^{an};\mathbb Z)
\Longrightarrow
H_{p+q}(U^{an};\mathbb Z),
}
\]

with \(d^1\) the alternating sum of the face maps.

---

## Proposition 12.1 — Vertical homological boundedness

Every \(K_r(f)\) is a smooth affine complex \(n\)-fold.  By the
Andreotti–Frankel theorem it has the homotopy type of a real CW complex of
dimension at most \(n\).

Therefore

\[
H_q(K_r;\mathbb Z)=0
\qquad(q>n).
\]

The descent spectral sequence is thus an infinite horizontal strip of only
\(n+1\) rows.

This is another general finite-complexity phenomenon.

---

## Corollary 12.2 — Finite homological palette

By partition decomposition,

\[
H_q(K_r)
\simeq
\bigoplus_{k=1}^d
H_q(\operatorname{Conf}_k)^{\oplus S(r,k)}
\]

as abelian groups.

Therefore the \(E^1\)-page is built from the homology of only the finitely many
fiber configuration varieties

\[
\operatorname{Conf}_1,\dots,\operatorname{Conf}_d.
\]

The difficult information is not the list of groups appearing at higher
simplicial degrees; it is the system of face maps among their copies.

This gives a concrete program: **compute the finite configuration palette and the
finite set of induced maps on homology, then compute the quotient topology.**

---

# 13. Canonical commuting vector fields

The étale structure gives more than a trivial tangent bundle.

Let \(y_1,\dots,y_n\) be the standard coordinates on the target.
There are canonical vector fields \(D_1,\dots,D_n\) on the source defined by

\[
D_i(f_j)=\delta_{ij}.
\]

Because the Jacobian determinant is a nonzero constant, the inverse Jacobian
has polynomial entries, so these are polynomial derivations.

They are the pullbacks of the coordinate vector fields on the target. Hence:

\[
[D_i,D_j]=0,
\]

and at every point they form a basis of the tangent space.

The same construction works on every configuration component via any étale
projection to \(\mathbb A^n\).

---

## Proposition 13.1 — Local-nilpotence criterion for triviality

If all canonical derivations \(D_1,\dots,D_n\) of a connected Keller
\(n\)-fold are locally nilpotent, then the variety is isomorphic to
\(\mathbb A^n\).

For the original Keller map \(f\), if all canonical \(D_i\)'s are locally
nilpotent, then \(f\) is a polynomial automorphism.

### Proof

Commuting locally nilpotent derivations integrate to an algebraic
\(\mathbb G_a^n\)-action.  Since the vector fields form a basis everywhere,
every orbit has dimension \(n\), hence is open.  Connectedness forces a single
orbit.  The stabilizer is a zero-dimensional algebraic subgroup of
\(\mathbb G_a^n\), hence trivial in characteristic zero.  Thus the action is
simply transitive and the variety is \(\mathbb G_a^n\simeq\mathbb A^n\).

For the original \(f\), the identities \(D_i(f_j)=\delta_{ij}\) make \(f\)
translation-equivariant, so it is an isomorphism.

This gives a second bridge between Keller geometry and the
Makar–Limanov/LND viewpoint.

A counterexample must possess a global algebraic frame that is **not complete
in the additive algebraic sense**.

---

# 14. Specialization: the marked-cubic Keller map

Now specialize to the explicit degree-three map \(F\) already studied in the
current manuscript.

The established properties are:

1. \(F\) has generic degree \(3\);
2. its fibers have cardinality exactly \(3,1,0\);
3. the nonproperness locus is the cubic discriminant hypersurface \(\mathcal S\);
4. the omitted locus is the triple-root curve \(\Gamma\);
5. the finite étale cover over \(\mathbb A^3\setminus\mathcal S\) has monodromy
   \(S_3\);
6. \(Y=\operatorname{Conf}_2(F)\) is a connected smooth affine threefold and a degree-six
   finite étale cover of the proper locus.

---

## Theorem 14.1 — Exceptional collision collapse

For this \(F\),

\[
\operatorname{Conf}_1(F)=\mathbb A^3,
\qquad
\operatorname{Conf}_2(F)=Y,
\qquad
\operatorname{Conf}_3(F)\simeq Y,
\qquad
\operatorname{Conf}_k(F)=\varnothing\ (k\ge4).
\]

### Proof

The only nontrivial point is \(\operatorname{Conf}_3\simeq\operatorname{Conf}_2\).

The top forgetful map

\[
\operatorname{Conf}_3\to\operatorname{Conf}_2
\]

is an open immersion by Proposition 6.2.  The fiber census has no
two-point fibers.  Therefore every ordered pair of distinct points in one
fiber lies in a three-point fiber and has a unique third point.  The open
immersion is surjective and hence an isomorphism.

Geometrically, this says that after two distinct roots of a cubic have been
chosen, the third is forced.

---

## Corollary 14.2 — Every level uses only \(\mathbb A^3\) and \(Y\)

For every \(r\ge1\),

\[
K_r(F)
\simeq
\mathbb A^3
\sqcup
\left(S(r,2)+S(r,3)\right)Y.
\]

Using

\[
S(r,2)+S(r,3)=\frac{3^r-3}{6},
\]

we obtain the especially simple formula

\[
\boxed{
K_r(F)
\simeq
\mathbb A^3
\sqcup
\frac{3^r-3}{6}\,Y.
}
\]

Thus the entire infinite Keller nerve has only **two** connected-component
isomorphism types.

For every additive invariant \(I\),

\[
\boxed{
I(K_r(F))
=
I(\mathbb A^3)
+
\frac{3^r-3}{6}\,I(Y).
}
\]

Since the existing calculation gives

\[
\chi(Y)=0,
\]

it follows immediately that

\[
\boxed{
\chi(K_r(F))=1
\qquad\text{for every }r\ge1.
}
\]

---

## Proposition 14.3 — Strong finite morphism type in the degree-three example

The nontrivial simplicial structure maps can be expressed using a finite
collection of morphisms involving \(Y\):

- the \(S_3\) deck transformations of the full-ordering cover
  \[
  Y\to\mathbb A^3\setminus\mathcal S;
  \]
- the three maps
  \[
  Y\to\mathbb A^3
  \]
  selecting the first, second, or uniquely determined third sheet;
- the identity/diagonal maps on \(\mathbb A^3\).

Indeed, every component of every \(K_r\) is specified by a partition with
one, two, or three blocks.  Deleting a coordinate either preserves at least
two distinct blocks, in which case the resulting map between \(Y\)-components
is a relabeling of the three sheets, or reduces to one block, in which case it
selects one sheet.

Thus this example is not merely finite-object-type; it is an unusually small
**finite-state simplicial system**.

This is the precise sense in which the symmetric marked-root geometry causes
the Keller groupoid to collapse.

---

# 15. Why the symmetric construction first appears in dimension three

The projective construction publicly proposed by Andy Jiang on July 20, 2026
is the marked-root insertion map

\[
\mathbb P^1\times\operatorname{Sym}^2(\mathbb P^1)
\longrightarrow
\operatorname{Sym}^3(\mathbb P^1),
\qquad
(p,\{q,r\})\mapsto\{p,q,r\}.
\]

It is generically three-to-one because a generic cubic has three choices of
marked root.

More generally, the analogous operation

\[
\mathbb P^1\times\operatorname{Sym}^{m-1}(\mathbb P^1)
\to
\operatorname{Sym}^m(\mathbb P^1)
\]

is generically degree \(m\) and lives in dimension \(m\).

For \(m=2\), the analogous marked-root map would have generic degree two.
But a degree-two function-field extension in characteristic zero is separable
and normal, and Campbell's normal-extension theorem excludes a noninvertible
degree-two Keller map.

So the marked-root mechanism cannot produce a planar counterexample in its
naïve dimension-two form.

Dimension three is the first place where:

- the natural marked-root degree is at least three; and
- the generic extension can be nonnormal;
- \(S_3\) provides the smallest nonregular transitive monodromy.

This does **not** prove the planar Jacobian conjecture: a hypothetical planar
counterexample could have generic degree \(d\ge3\) and arise from entirely
different geometry.

But it explains why the symmetric construction itself begins naturally in
dimension three.

For provenance, the symmetric-product interpretation should be attributed to
Andy Jiang's July 20, 2026 public post, with the same-day Secret Blogging
Seminar thread used as contemporaneous corroboration.  Later papers should be
cited for proof and exposition rather than retroactively for priority; see
`../../citation-hygiene.md`.

---

# 16. Imported global constraints on Keller groupoids

The following are useful existing results and should be treated as literature
inputs, not new theorems of this draft.

## 16.1 Image topology

Peretz–Van Chau–Campbell–Gutierrez record the classical facts that for a
complex Keller map

\[
f\colon\mathbb C^n\to\mathbb C^n,
\]

the image \(U=f(\mathbb C^n)\) is open and simply connected, while the
complement has complex codimension at least two.

In dimension two this means

\[
U=\mathbb C^2\setminus Z
\]

for a finite set \(Z\).

Reference:

R. Peretz, N. Van Chau, L. A. Campbell, C. Gutierrez,
*Iterated images and the plane Jacobian conjecture*,
Discrete Contin. Dyn. Syst. 16 (2006), 455–461,
DOI `10.3934/dcds.2006.16.455`.

---

## 16.2 Nonproperness hypersurface

Jelonek proved that for a dominant generically finite polynomial map of
\(\mathbb C^n\), the nonproperness set is either empty or a hypersurface (with
strong additional uniruledness properties).

Reference:

Z. Jelonek,
*The set of points at which a polynomial map is not proper*,
Ann. Polon. Math. 58 (1993), 259–266,
DOI `10.4064/ap-58-3-259-266`.

Hence a nonautomorphic Keller map has two distinct exceptional objects:

- the omitted set \(Y\setminus U\), of codimension at least two;
- the nonproperness hypersurface
  \[
  S_f=Y\setminus U_d.
  \]

The marked-cubic example displays this separation explicitly:
the omitted set is the curve \(\Gamma\), while \(S_f\) is the whole
discriminant hypersurface.

---

## 16.3 Further planar restrictions

Jelonek's 2020 note proves additional restrictions on the nonproperness set of
a hypothetical planar Keller counterexample, including that in dimension two
the nonproperness curve cannot be a curve without self-intersections.

Reference:

Z. Jelonek,
*A note on the Jacobian Conjecture*,
arXiv:2011.03472.

These results should be incorporated into the planar Keller-groupoid program
as constraints on the base \(V=\mathbb A^2\setminus S_f\) of the finite étale
monodromy cover.

---

# 17. A Keller-groupoid formulation of the planar problem

Suppose, hypothetically, that

\[
f\colon\mathbb A^2\to\mathbb A^2
\]

is a noninvertible Keller map of generic degree \(d\).

Then the Keller-groupoid framework forces the following package.

## 17.1 Immediate structure

1. \(d\ge3\) (degree one is birational/invertible; degree two is excluded by
   the normal-extension criterion).
2. Every fiber configuration space
   \[
   \operatorname{Conf}_k(f),\quad 1\le k\le d,
   \]
   is a smooth affine algebraically parallelizable **surface**.
3. The full Čech nerve has only the finite palette
   \[
   \operatorname{Conf}_1,\dots,\operatorname{Conf}_d.
   \]
4. The proper locus
   \[
   V=\mathbb A^2\setminus S_f
   \]
   carries a finite étale degree-\(d\) cover.
5. The top configuration \(\operatorname{Conf}_d|_V\) is the Galois closure and its components are
   governed by the monodromy group
   \[
   G\le S_d.
   \]
6. The image
   \[
   U=f(\mathbb A^2)
   \]
   is the complement of finitely many points and is simply connected.

This is already a highly constrained category of affine surfaces and maps.

---

# 18. Topological signal peculiar to dimension two

Let

\[
Z=\mathbb A^2\setminus U,
\qquad |Z|=\ell.
\]

Topologically,

\[
U^{an}=\mathbb C^2\setminus Z.
\]

For a finite set of \(\ell\) points, Alexander duality gives

\[
H_3(U;\mathbb Z)\simeq\mathbb Z^\ell,
\]

while

\[
H_1(U)=H_2(U)=0.
\]

On the other hand every level \(K_r(f)\) is a smooth affine complex surface, so

\[
H_q(K_r)=0\qquad(q>2).
\]

Therefore, in the descent spectral sequence

\[
E^1_{p,q}=H_q(K_{p+1})
\Longrightarrow H_{p+q}(U),
\]

any nonzero \(H_3(U)\) must be manufactured **entirely by the simplicial
direction** from the rows \(q=0,1,2\).

Thus:

> A hypothetical omitted point in the plane is visible as a genuinely
> higher-simplicial homology class of the Keller groupoid; it cannot occur
> inside the homology of any individual affine configuration surface.

This does not yet yield a contradiction, but it identifies an explicit
topological target.

A sufficient new theorem of the following form would eliminate omitted points:

> **Desired planar vanishing theorem.**  
> For every planar Keller groupoid satisfying the algebraic constraints above,
> the totalized collision complex has \(H_3=0\).

If proved, this would force \(Z=\varnothing\), i.e. surjectivity.

Surjectivity alone is not currently enough to conclude the planar Jacobian
conjecture, so a second step would still be required to eliminate nontrivial
surjective étale self-maps.

---

# 19. Stable images and a sharper planar reformulation

The 2006 theorem of Peretz–Van Chau–Campbell–Gutierrez gives a particularly
natural interface with the groupoid viewpoint.

For a planar Keller map, the iterated images

\[
\mathbb A^2\supseteq f(\mathbb A^2)\supseteq f^2(\mathbb A^2)\supseteq\cdots
\]

eventually stabilize to a cofinite, open, simply connected set

\[
\Omega.
\]

The restricted map

\[
f|_\Omega\colon\Omega\to\Omega
\]

is a **surjective étale endomorphism**.

They show that the planar Jacobian conjecture is equivalent to the assertion
that every such stabilized map is injective.

In Keller-groupoid language:

> **JC(2) is equivalent to saying that every stabilized planar Keller groupoid
> on a cofinite simply connected open subset of \(\mathbb A^2\) is the unit
> groupoid.**

This reframing does not prove the conjecture, but it converts the residual
problem into a statement about the nonexistence of a nontrivial étale
equivalence-relation groupoid on a very simple open surface.

This seems like the correct point at which to bring in:

- classification of smooth affine/quasi-affine surfaces;
- algebraic parallelizability of the fiber configuration spaces;
- topology of complements of plane curves;
- log Kodaira dimension;
- monodromy and Galois-closure surfaces.

---

# 20. Potential planar attack routes

The following are research directions, not established results.

## Route A — Classify parallelizable configuration surfaces

Every \(\operatorname{Conf}_k(f)\) is a smooth affine surface with

\[
T_{\operatorname{Conf}_k}\simeq\mathcal O^{\oplus2}.
\]

Moreover it admits several related étale maps arising from coordinate
projections and the common target.

A useful classification theorem of the form

> sufficiently constrained smooth affine parallelizable surfaces étale over
> \(\mathbb A^2\) must be \(\mathbb A^2\) or have prescribed boundary topology

could immediately constrain the possible \(\operatorname{Conf}_k\).

The marked-cubic threefold demonstrates that trivial tangent bundle alone is
far from enough in dimension three; dimension two may nevertheless be much
more rigid.

---

## Route B — Use the Galois-closure surface

The top configuration

\[
\operatorname{Conf}_d|_V
\]

is the Galois closure of the proper finite étale cover over the complement of
the nonproperness curve.

Thus a hypothetical planar counterexample produces a smooth affine
parallelizable surface carrying a finite étale Galois map

\[
Z\to\mathbb A^2\setminus S_f
\]

with nonregular transitive monodromy \(G\).

One can ask whether surface classification, log Kodaira dimension, or boundary
intersection theory permits such a \(Z\) to extend simultaneously to the
nonproper étale configuration surface required by the Keller groupoid.

---

## Route C — Homological descent obstruction

Compute the normalized total chain complex of

\[
H_q(K_r),\qquad q=0,1,2.
\]

The partition theorem means only the homology of \(\operatorname{Conf}_1,\dots,\operatorname{Conf}_d\) ever occurs.
If geometric/morphism stabilization forces this total complex to have
vanishing degree-three homology, omitted points are impossible.

For an early-collapsing Keller groupoid like the marked-cubic example, this
computation should be exceptionally explicit and can serve as a model
calculation.

---

## Route D — Euler moment constraints

The equations

\[
\chi(\operatorname{Conf}_k)
=
\sum_{m=k}^d(m)_k\chi(U_m)
\]

turn collision Euler characteristics into factorial moments of the
fiber-cardinality distribution.

In dimension two the omitted set is finite, so

\[
|U_0|=\chi(U_0)
\]

is an integer directly recoverable from these moments.

One can search for geometric sign or divisibility restrictions on
\(\chi(\operatorname{Conf}_k)\) for parallelizable affine surfaces that make the required moment
system impossible.

---

## Route E — Iteration amplification

If \(f\) has generic degree \(d>1\), then the iterate \(f^r\) has generic
degree \(d^r\).

The planar stable-image theorem eventually places all iterates inside the same
cofinite dynamical environment.

This suggests a general meta-strategy:

> Find a Keller-groupoid invariant that must grow with generic degree under
> iteration but is uniformly bounded by the topology/algebraic geometry of a
> fixed cofinite planar stable image.

Any such invariant would force \(d=1\).

Possible candidates include:

- complexity of Galois-closure configuration surfaces;
- boundary divisor complexity;
- ranks of selected homology groups;
- number or type of configuration components;
- log-geometric invariants of compactifications.

At present this is only a strategy template.

---

## Route F — Local inertia along the nonproperness curve

Over

\[
V=\mathbb A^2\setminus S_f,
\]

the finite cover has monodromy

\[
\pi_1(V)\to G\le S_d.
\]

All nontrivial monodromy is generated by topology around the plane curve
\(S_f\), because the full image \(U\) is simply connected.

Thus the groupoid translates the planar Jacobian conjecture into a question
about whether a plane-curve complement can support a transitive monodromy
representation whose associated collision/Galois-closure surfaces extend
étale across exactly the required fiber-drop strata.

Jelonek's restrictions on the topology/singularity of \(S_f\) should be
inserted at this point.

This looks especially compatible with braid-group and plane-curve techniques.

---

# 21. A general "configuration complexity" program

The degree \(d\) alone is crude.  The Keller groupoid suggests finer invariants.

Possible invariants include:

## 21.1 Geometric generation depth

\[
g(f)=
\min\{m:\text{all configuration component types appear by }C_m\}.
\]

Always \(g(f)\le d\).

For the known counterexample,

\[
g(F)=2<3.
\]

---

## 21.2 Component orbit profile

Let

\[
o_k(f)=\#\pi_0(\operatorname{Conf}_k(f)).
\]

Over the proper locus this is

\[
o_k(f)
=
\#\bigl(G\backslash\operatorname{Inj}([k],[d])\bigr).
\]

Thus the sequence \(o_k\) measures the transitivity profile of the monodromy
group.

---

## 21.3 Additive configuration profile

For an additive invariant \(I\), record

\[
(I(\operatorname{Conf}_1),\dots,I(\operatorname{Conf}_d)).
\]

This finite vector determines \(I(K_r)\) for every \(r\).

---

## 21.4 Morphism complexity

Record the isomorphism classes of configuration components together with the
restrictions of all forgetful/permutation maps between them.

This is the appropriate invariant if one wants to reconstruct the homotopy
type of the quotient rather than merely additive invariants of each level.

---

## 21.5 Discovery-complexity hypothesis

The marked-cubic counterexample has extraordinarily low configuration complexity:

\[
\operatorname{Conf}_1=\mathbb A^3,\qquad
\operatorname{Conf}_2\simeq\operatorname{Conf}_3\simeq Y.
\]

Its entire simplicial geometry is generated by two varieties and a finite
collection of maps.

This motivates, but does not prove, the heuristic:

> Keller maps arising from low configuration complexity may be disproportionately
> discoverable by symbolic/AI-guided search.

This should remain a stated research hypothesis rather than a historical
explanation unless additional examples support it.

---

# 22. Fillings attached to configuration covers

The existing threefold work contains one further piece that should now be
viewed as **extra structure attached to the groupoid**, rather than part of the
definition.

For the marked-cubic map:

- \(Y=\operatorname{Conf}_2(F)\) is the ordered-double configuration cover of the proper target locus;
- the \(S_3\) monodromy has a sign character;
- adjoining a square root of the cubic discriminant produces a natural
  two-sheeted cover;
- filling this cover across the discriminant divisor gives the smooth affine
  threefold \(X\);
- \(X\) is rational, factorial, rigid, simply connected, and noncontractible.

This suggests a broader question:

> Given a Keller groupoid and a finite representation or character of its
> monodromy group, when does the associated finite étale cover of the proper
> locus admit a natural affine filling across the nonproperness divisor?

The Rees-rigid Keller threefold would then be the first example of a
**representation-theoretic Keller-groupoid filling**.

This is currently an open program, but it gives the existing discriminant
filling a natural place in the generalized story.

---

# 23. Proposed paper architecture

A Keller-groupoid paper could now be organized as follows.

## Part I — General theory

1. Keller maps and the image \(U\).
2. The étale relation groupoid.
3. Čech nerve and categorical \(2\)-coskeletality.
4. Collision varieties.
5. Partition/Stirling decomposition.
6. Generic degree and finite configuration palette.
7. Fiber-cardinality filtration and nonproperness.
8. Collision tower and top open immersion.
9. Monodromy, transitivity, and Galois closure.
10. Additive invariants and recurrence.
11. Topological realization and descent.
12. Canonical parallelism and vector fields.

## Part II — The first nontrivial example

13. Provenance and the marked-root cubic construction.
14. Degree \(3\), \(3/1/0\) fiber census, \(S_3\) monodromy.
15. \(\operatorname{Conf}_2=\operatorname{Conf}_3=Y\).
16. Exact formula
    \[
    K_r=\mathbb A^3\sqcup\frac{3^r-3}{6}Y.
    \]
17. Finite-state simplicial maps.
18. The discriminant/sign cover and its affine filling.
19. Rees presentation, rigidity, and topology of the filling.

## Part III — Constraints and open problems

20. Collision complexity.
21. Stabilization questions.
22. Planar Keller groupoids and JC(2).
23. Classification of parallelizable configuration surfaces.
24. Iteration amplification.
25. Monodromy/inertia constraints.
26. Representation-theoretic fillings.

This architecture preserves all of the existing threefold results while making
the general theory the conceptual center.

---

# 24. Claims proved in this draft

The following are proposed as **LLM-proved** claims in the temporary mini-Hardy
registry:

- `KG-01`: Keller relation groupoid / Čech nerve construction.
- `KG-02`: every nerve level and fiber configuration space is a smooth affine
  parallelizable \(n\)-fold.
- `KG-03`: partition decomposition by fiber configuration spaces.
- `KG-04`: fiber cardinality is bounded by generic degree; \(\operatorname{Conf}_k=\varnothing\)
  for \(k>d\).
- `KG-05`: finite geometric palette; every component type appears by level
  \(d\).
- `KG-06`: fiber-cardinality filtration via images of fiber configuration spaces.
- `KG-07`: top forgetful map \(\operatorname{Conf}_d\to \operatorname{Conf}_{d-1}\) is an open immersion and is an
  isomorphism iff there are no \((d-1)\)-point fibers.
- `KG-08`: monodromy-orbit description of connected configuration components.
- `KG-09`: top configuration realizes the Galois closure.
- `KG-10`: Stirling/Grothendieck/additive-invariant formulas and recurrence.
- `KG-11`: Euler factorial-moment identities.
- `KG-12`: canonical commuting parallelizing vector fields and the LND
  triviality criterion.
- `KG-F01`: for the marked-cubic example,
  \(\operatorname{Conf}_1=\mathbb A^3,\ \operatorname{Conf}_2\simeq\operatorname{Conf}_3\simeq Y\).
- `KG-F02`: for the marked-cubic example,
  \[
  K_r\simeq\mathbb A^3\sqcup((3^r-3)/6)Y.
  \]
- `KG-F03`: the marked-cubic nerve has strong finite morphism type generated by
  the \(S_3\)-relabelings and three sheet projections.

The topological-descent theorem and the global image/nonproperness results are
standard/literature inputs rather than newly proved claims.

---

# 25. Open claims / research targets

- `KG-O01`: classify possible configuration complexity of Keller maps.
- `KG-O02`: determine whether early collision stabilization forces additional
  algebraic restrictions on \(f\).
- `KG-O03`: classify smooth affine parallelizable surfaces that can occur as
  planar Keller configuration components.
- `KG-O04`: prove a homological-descent obstruction strong enough to constrain
  planar Keller groupoids.
- `KG-O05`: find an invariant growing under iteration but bounded on a stable
  cofinite planar image.
- `KG-O06`: characterize monodromy representations realizable by planar Keller
  groupoids.
- `KG-O07`: develop representation-theoretic affine fillings of collision
  covers.
- `KG-O08`: test whether low configuration complexity correlates with
  discoverability or with special algebraic normal forms.

---

# References / source hygiene

The general scheme-theoretic arguments above use standard results including:

- Stacks Project Tag `02GE`: diagonal of an unramified morphism is open.
- Stacks Project Tag `024T`: sections of unramified separated morphisms are
  open and closed.

Global Keller-map inputs:

- L. A. Campbell, *A condition for a polynomial map to be invertible*,
  Math. Ann. 205 (1973), 243–248.
- Z. Jelonek, *The set of points at which a polynomial map is not proper*,
  Ann. Polon. Math. 58 (1993), 259–266.
- R. Peretz, N. Van Chau, L. A. Campbell, C. Gutierrez,
  *Iterated images and the plane Jacobian conjecture*,
  DCDS 16 (2006), 455–461.
- Z. Jelonek, *A note on the Jacobian Conjecture*, arXiv:2011.03472.

For the July 2026 marked-root example and priority-sensitive attributions, use
`../../citation-hygiene.md`.  In particular, the symmetric-product marked-root
interpretation should be attributed to Andy Jiang's July 20 public post as the
earliest public source currently identified, with the Secret Blogging Seminar
thread as contemporaneous corroboration and later papers cited for detailed
proofs/exposition.

---

# Immediate next calculations

1. Write the explicit groupoid structure maps on \(Y=\operatorname{Conf}_2(F)\), including the
   third-sheet map \(Y\to\mathbb A^3\) and the full \(S_3\) deck action.
2. Compute \(H_*(Y)\), not only \(\chi(Y)\).
3. Build the actual \(E^1\) page of the Keller-groupoid descent spectral
   sequence for \(F\).
4. Verify directly that the finite-state description reproduces the topology
   of
   \[
   U=\mathbb A^3\setminus\Gamma.
   \]
5. Investigate smooth affine parallelizable surfaces as candidate planar
   fiber configuration varieties.
6. Recast the Peretz–Van Chau–Campbell–Gutierrez stable-image theorem entirely
   in Keller-groupoid language and look for an iteration-growth obstruction.

---

# 26. Plane-specific configuration criteria

The general theory above becomes sharper in dimension two.  The detailed
statements and proofs are collected in `../../keller-groupoids-rank-two/notes/plane-configuration-criteria.md`.

The main new structural points are:

1. **fiber deficit decomposition**
   \[
   d-m_C=\operatorname{supp}(\sigma_C)+t_C;
   \]
2. **deficit one is purely non-maximal**
   — a \((d-1)\)-point curve stratum cannot come from branch ramification;
3. **étale-maximal top stabilization**
   \[
   \operatorname{Conf}_d(f)\simeq\operatorname{Conf}_{d-1}(f);
   \]
4. the configuration filtration recovers the generic deletion counts once
   inertia is known;
5. the complement divisors of configuration images give lower bounds on unit
   ranks of the configuration surfaces;
6. the first groupoid level admits an explicit divided-difference idempotent
   and ideal-membership criterion.

These are the groupoid-derived constraints to combine next with the
one-place-at-infinity and boundary-graph machinery.
