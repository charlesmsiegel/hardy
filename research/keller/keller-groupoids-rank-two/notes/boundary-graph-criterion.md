# Boundary-Graph Criterion for a Planar Keller Source

**Snapshot:** 2026-09-14  
**Purpose:** make the final “does this decorated plane curve produce an \(\mathbb A^2\) source?” test exact.

This note sharpens the planar curve-reduction program.  It separates:

1. an exact classical criterion for a smooth open surface to be \(\mathbb A^2\);
2. cheap numerical and topological rejection tests;
3. the finite-cover/resolution procedure that turns decorated plane-curve data into the
   boundary pair to which the criterion applies.

The genuinely unresolved part is no longer the *final* recognition problem once a candidate
boundary pair is known.  The unresolved part is proving that the Keller data force a candidate
pair which passes that recognition test, or showing no decorated curve can do so.

---

# 1. Boundary modifications

Let \(Y\) be a smooth projective surface and

\[
D=\bigcup_{i=1}^r D_i
\]

a reduced simple-normal-crossings divisor.

A **boundary blowup** is either:

1. the blowup of a smooth point of one boundary component; or
2. the blowup of a node \(D_i\cap D_j\).

Its exceptional curve is added to the boundary.

A **boundary blowdown** is the inverse operation, contracting a boundary
\((-1)\)-curve when the contraction preserves a smooth SNC boundary.

Every boundary modification is an isomorphism away from the boundary, so

\[
Y\setminus D
\]

is unchanged.

At the weighted-dual-graph level:

- blowing up a smooth point on a vertex of weight \(a\) replaces
  \[
  a
  \quad\text{by}\quad
  (a-1)-(-1);
  \]
- blowing up a node joining weights \(a,b\) replaces
  \[
  a-b
  \quad\text{by}\quad
  (a-1)-(-1)-(b-1).
  \]

Boundary blowdowns reverse these moves.

---

# 2. Exact Ramanujam–Morrow recognition criterion

## Theorem BG-1 — Boundary-birational recognition of \(\mathbb A^2\)

Let \(U\) be a smooth complex algebraic surface and let

\[
U=Y\setminus D
\]

be a smooth projective SNC completion.

Then

\[
\boxed{
U\simeq\mathbb A^2
}
\]

if and only if the pair \((Y,D)\) is connected to

\[
(\mathbb P^2,L_\infty)
\]

by a finite sequence of boundary blowups and boundary blowdowns.

### Proof

**Necessity.**
This is the classical compactification theorem of Ramanujam–Morrow.  A convenient
published formulation is Suzuki, *Affine plane curves with one place at infinity*,
§1.4: an algebraic compactification \((Y,D)\) of \(\mathbb C^2\) can be transformed
into \((\mathbb P^2,L)\) by finitely many blowups and blowdowns along the boundary.

**Sufficiency.**
Every allowed modification is an isomorphism off the boundary.  Therefore the open
complement is preserved throughout the sequence.  The complement of a line in
\(\mathbb P^2\) is \(\mathbb A^2\).

Thus the recognition problem is exact once the completed boundary pair is known.

---

# 3. Weighted-graph certificate

The previous theorem is a theorem about pairs, but for an SNC divisor of rational
curves its birational operations have a concrete weighted-graph form.

## Theorem BG-2 — Graph certificate criterion

Suppose:

1. \(Y\) is a smooth projective **rational** surface;
2. \(D\) is an SNC divisor whose irreducible components are smooth rational curves;
3. the dual graph \(\Gamma(D)\) is connected;
4. the number of boundary components equals the Picard rank:
   \[
   r=\rho(Y);
   \]
5. there is a finite legal sequence of weighted boundary blowup/down moves carrying
   \(\Gamma(D)\) to the one-vertex graph
   \[
   [+1].
   \]

Then

\[
Y\setminus D\simeq\mathbb A^2.
\]

Conversely, every SNC completion of \(\mathbb A^2\) satisfies these conditions and
admits such a graph certificate.

### Proof

Execute the graph moves geometrically.  A boundary \((-1)\)-vertex is an actual
smooth rational \((-1)\)-curve and can be contracted by Castelnuovo; the inverse
moves are ordinary boundary blowups.

The quantity

\[
\rho(Y)-\#\{\text{boundary components}\}
\]

is invariant under every boundary blowup or blowdown.  Hence after reaching one
\(+1\)-curve \(L\subset Y'\),

\[
\rho(Y')=1.
\]

The surface remains rational, so a smooth projective rational surface of Picard
rank one is \(\mathbb P^2\).  The rational curve \(L\) has self-intersection \(1\),
hence is a line.  The complement is \(\mathbb A^2\).

The converse follows from BG-1.

### Why the Picard-rank hypothesis is included

A weighted graph by itself does not record hidden divisor classes elsewhere on the
projective surface.  The equality \(r=\rho(Y)\) rules out such hidden Picard
directions.  For a genuine completion of \(\mathbb A^2\), the boundary classes
generate \(H_2(Y,\mathbb Z)\) and \(\operatorname{Pic}(Y)\).

---

# 4. Cheap necessary tests

Before searching for a full boundary-move certificate, a candidate completion of
\(\mathbb A^2\) must pass all of the following.

## BG-3(a) — Rational tree

Every boundary component is \(\mathbb P^1\), and the dual graph is a tree.

Reason: start from the single line at infinity and apply boundary blowups.

---

## BG-3(b) — Picard rank

If \(D\) has \(r\) irreducible components, then

\[
\boxed{\rho(Y)=r.}
\]

Boundary blowups increase both quantities by one.

---

## BG-3(c) — Unimodular intersection matrix

Let

\[
Q_D=(D_i\cdot D_j)_{i,j}.
\]

Then

\[
\boxed{|\det Q_D|=1.}
\]

More precisely, since \(Q_D\) is obtained from the \([1]\) form by blowups,

\[
\operatorname{signature}(Q_D)=(1,r-1),
\qquad
\det Q_D=(-1)^{r-1}.
\]

Suzuki explicitly records the unimodularity consequence of the
Ramanujam–Morrow theorem.

This is an extremely cheap rejection test.

---

## BG-3(d) — Rational-surface Chern number

A rational surface has

\[
K_Y^2+\rho(Y)=10.
\]

Therefore a candidate \(\mathbb A^2\) completion with \(r\) boundary components must satisfy

\[
\boxed{K_Y^2=10-r.}
\]

This is especially useful for Keller covers because \(K_Y^2\) can also be
calculated from finite-cover Riemann–Hurwitz plus the resolution corrections.
It therefore becomes a numerical equation on the downstairs branch curve and
its inertia data.

---

## BG-3(e) — Euler characteristic

For a rational surface of Picard rank \(r\),

\[
\chi_{\mathrm{top}}(Y)=2+r.
\]

For a rational tree of \(r\) curves,

\[
\chi_{\mathrm{top}}(D)=r+1.
\]

Hence

\[
\chi_{\mathrm{top}}(Y\setminus D)=1.
\]

A candidate failing this is impossible.

---

## BG-3(f) — Boundary at infinity

A tubular boundary of \(D\) is the plumbed oriented \(3\)-manifold determined by
the weighted graph \(\Gamma(D)\).

For \(\mathbb A^2\), the boundary at infinity is

\[
S^3.
\]

Thus a candidate must satisfy

\[
\boxed{M(\Gamma(D))\simeq S^3.}
\]

The determinant condition only implies that this plumbing is an integral
homology sphere.  That is **not enough**: exotic contractible affine surfaces
such as the Ramanujam surface are precisely a warning that an integral-homology
sphere at infinity can have nontrivial fundamental group.

So one should test the actual plumbing \(3\)-manifold / \(\pi_1^\infty\), not only
\(\det Q_D\).

---

# 5. Topological recognition variant

Ramanujam's topological characterization gives another exact route.

## Theorem BG-4 — Topological recognition

A smooth complex affine surface \(U\) is isomorphic to \(\mathbb A^2\) if it is

1. contractible; and
2. simply connected at infinity.

Conversely \(\mathbb A^2\) has these properties.

For a candidate Keller source, this gives the following workflow.

If the boundary is a rational tree whose components generate \(H_2(Y)\), then
the complement is acyclic by the Ramanujam–Fujita homology calculation.
If in addition

\[
\pi_1(U)=1,
\]

then the affine CW-dimension bound (\(\le2\)) and Whitehead/Hurewicz imply
contractibility.

The remaining test is

\[
\pi_1^\infty(U)=1,
\]

which is computable from the plumbing at infinity.

Thus a second recognition package is:

\[
\boxed{
\text{acyclic}
+\pi_1(U)=1
+\pi_1^\infty(U)=1
\Longrightarrow
U\simeq\mathbb A^2.
}
\]

For a weighted rational tree, the last group is a graph-manifold/plumbing group,
so this is again a finite combinatorial-topological calculation.

---

# 6. From a decorated plane curve to the upstairs boundary

Now return to a hypothetical planar Keller datum.

Let

\[
S\subset\mathbb A^2
\]

be the nonproperness curve and

\[
\rho:\pi_1(\mathbb A^2\setminus S)\to G\le S_d
\]

the finite monodromy representation over the proper locus.

Compactify and resolve

\[
\overline S\cup L_\infty\subset\mathbb P^2.
\]

Let

\[
\mu:(B,D)\to(\mathbb P^2,\overline S\cup L_\infty)
\]

be an embedded log resolution, with

\[
D=\bigcup_v D_v
\]

SNC.

The monodromy representation pulls back to the complement \(B\setminus D\).

---

# 7. Inertia decoration on the resolved graph

For every component \(D_v\), choose a small meridian and write

\[
\sigma_v\in G
\]

for its monodromy.

At a node

\[
D_v\cap D_w,
\]

the local complement is analytically

\[
(\Delta^*)^2,
\]

whose fundamental group is

\[
\mathbb Z^2.
\]

Therefore

\[
\boxed{\sigma_v\sigma_w=\sigma_w\sigma_v.}
\]

This is already a finite group-theoretic restriction on every decorated
resolution graph.

At a smooth generic point of \(D_v\), the cycle decomposition of \(\sigma_v\)
records the ramification indices in the finite normalization.

A cycle of length \(e\) corresponds to ramification index \(e\).

---

# 8. Local cover is combinatorial after SNC resolution

Characteristic zero makes all finite ramification tame.

Abhyankar's lemma implies that, étale-locally along a regular branch component,
a tame finite cover is Kummer/monomial in form.

At a normal-crossing point the two local meridians commute.  After passing to
the Galois closure, the local cover is governed by the finite abelian inertia
subgroup

\[
I_{vw}=\langle\sigma_v,\sigma_w\rangle.
\]

Thus the normalized local cover is toroidal.  Its surface singularities and
their minimal resolutions are computable from finite lattice data; the
two-dimensional quotient pieces are resolved by the usual
Hirzebruch–Jung/toric continued-fraction procedure.

Consequently:

> Once the downstairs embedded resolution graph and its inertia labels are
> fixed, the weighted boundary graph of the resolved finite Galois cover is
> algorithmically determined.

The degree-\(d\) normalization corresponding to the original sheet is obtained
from the Galois cover by the sheet stabilizer and can likewise be normalized and
resolved.

This is the crucial bridge from a **decorated plane curve** to the
**upstairs boundary graph**.

---

# 9. The deletion-data issue

The finite normalization is not automatically the original Keller source.

Zariski's Main Theorem gives an open immersion

\[
\mathbb A^2\hookrightarrow \overline Z
\]

into the finite normalization.

Ramified points are automatically excluded from the source because the source
map is étale.  However, the finite normalization may also contain unramified
points not belonging to the original source.

Therefore the decorated plane curve \((S,\rho)\) alone need not specify the
source boundary unless the Keller map is **étale-maximal**.

There are two routes.

### Route 1 — Prove planar étale-maximality

If every hypothetical planar Keller counterexample is étale-maximal, then the
boundary is determined canonically by the decorated curve and inertia data.

This would make the curve reduction dramatically cleaner.

### Route 2 — Include deletion data

Otherwise augment the datum by a finite description of which unramified pieces
of the normalization are omitted by the source.

After blowing up any omitted isolated points, all missing loci can be represented
as boundary divisors.

For **specified** deletion data, the final boundary recognition problem is exact
by BG-1/BG-2.

Thus the current logical reduction is:

\[
\boxed{
\text{decorated plane curve}
+\text{deletion data}
\longrightarrow
\text{resolved source boundary pair}
\longrightarrow
\text{exact }\mathbb A^2\text{ test}.
}
\]

---

# 10. The finite rejection pipeline

For a candidate \((S,\rho,\text{deletion data})\):

## Step 1 — Resolve downstairs

Compute the weighted embedded-resolution graph of

\[
\overline S\cup L_\infty.
\]

Record Puiseux data, multiplicities, and intersections.

## Step 2 — Decorate by inertia

Assign each resolved component its permutation \(\sigma_v\), and verify
commutation at every node.

Reject if the local group relations are incompatible.

## Step 3 — Normalize the finite cover

Construct the normalization (preferably first the Galois closure), and resolve
the toroidal quotient singularities.

This produces the upstairs weighted graph and cover invariants.

## Step 4 — Insert source deletions

Remove all ramified boundary components and the specified additional unramified
loci; blow up isolated missing points so the source complement is an SNC divisor
\(E\).

## Step 5 — Cheap tests

Reject immediately unless:

\[
E\text{ is a rational tree},
\]

\[
\rho(\bar X)=\#E,
\]

\[
\det Q_E=(-1)^{\#E-1},
\]

\[
\operatorname{signature}(Q_E)=(1,\#E-1),
\]

\[
K_{\bar X}^2=10-\#E.
\]

Optionally compute the plumbing group and reject unless its boundary manifold is
\(S^3\).

## Step 6 — Exact boundary test

Search for / verify a boundary-modification certificate

\[
(\bar X,E)
\dashrightarrow
(\mathbb P^2,L_\infty).
\]

If none exists, the candidate is impossible.

If it exists, the candidate open surface is \(\mathbb A^2\).

At that point one has produced a genuine geometric candidate for a planar
Keller source; the remaining step is reconstructing/checking the polynomial map.

---

# 11. Why this is stronger than “the curve is rational”

The earlier curve program gave restrictions such as:

- rational components;
- one point at infinity;
- degree/Puiseux restrictions;
- genus and Bézout budgets.

The boundary criterion adds global constraints coupling those curve data to
the finite monodromy:

\[
\boxed{
\text{branch singularities}
+\text{inertia}
\Longrightarrow
Q_E,\ K^2,\ \pi_1^\infty,\ \Gamma(E).
}
\]

The required values for an affine-plane source are extraordinarily rigid.

The most promising numerical equation is

\[
K_{\bar X}^2=10-r,
\]

because the left side is computable from Riemann–Hurwitz and resolution
discrepancies, while \(r\) is the number of boundary components created by the
same branch/inertia data.

This is a direct Diophantine constraint on decorated plane curves.

---

# 12. A particularly strong graph invariant: the end

Boundary blowups and blowdowns do not change the oriented boundary
\(3\)-manifold of a tubular neighborhood of infinity.

Starting from

\[
(\mathbb P^2,L_\infty),
\]

that manifold is \(S^3\).

Therefore every affine-plane boundary graph represents \(S^3\) under plumbing.

This gives a useful hierarchy of tests:

1. \(\det Q=\pm1\): integral homology sphere;
2. plumbing \(\pi_1=1\): homotopy \(3\)-sphere;
3. by the Poincaré theorem, the oriented manifold is \(S^3\);
4. boundary-birational graph certificate: algebraic realization as an
   \(\mathbb A^2\) completion.

Tests (1)–(3) are topological and often cheaper than full algebraic
boundary factorization.

---

# 13. What can now be called a theorem versus an open problem

## Theorem-level / imported-standard

- BG-1: exact boundary-birational recognition.
- BG-2: graph-certificate criterion with rationality and Picard-rank condition.
- BG-3: rational-tree, Picard-rank, unimodular/signature, \(K^2\), Euler, and
  \(S^3\)-at-infinity necessary conditions.
- BG-4: topological recognition via Ramanujam.
- commuting inertia at SNC nodes.
- tame/Kummer local form after resolution, via Abhyankar's lemma.
- algorithmic construction of normalized/resolved local cover graphs from finite
  inertia data.

## Open

- prove planar Keller maps are étale-maximal, or otherwise bound the deletion data;
- derive closed-form constraints on \(Q_E\) and \(K^2\) directly from Puiseux and
  permutation-cycle data;
- classify decorated one-place rational curves passing all cheap tests;
- prove none pass the full boundary certificate;
- or construct one which does.

---

# 14. Immediate next theorem target

The next concrete target should be a **cover Chern-number formula**.

Input:

- degree and Puiseux graph of each component of \(S\);
- local inertia orders along every resolved boundary component;
- commuting inertia at nodes.

Output:

\[
K_{\bar X}^2
\quad\text{and}\quad
\rho(\bar X)
\]

for the resolved sheet normalization / Galois closure.

Then impose

\[
K_{\bar X}^2+\rho(\bar X)=10.
\]

If this equation already fails for broad classes of decorated plane curves, the
search space collapses before any difficult topology is needed.

After that, compute the intersection determinant and plumbing group.

This is now a concrete algebraic-geometry calculation rather than a vague
Jacobian-conjecture strategy.

---

# 15. Sources

Primary/classical:

- C. P. Ramanujam, *A topological characterisation of the affine plane as an
  algebraic variety*, Ann. of Math. 94 (1971), 69–88.
- J. A. Morrow, *Compactifications of \(\mathbb C^2\)*, Bull. Amer. Math. Soc.
  78 (1972), 813–816.
- M. Suzuki, *Affine plane curves with one place at infinity*, Ann. Inst.
  Fourier 49 (1999), 375–404.  Section 1.4 explicitly records the
  Ramanujam–Morrow boundary-modification statement and unimodularity.
- Stacks Project, Tag `0EYG`, Abhyankar's lemma for tame ramification along a
  regular divisor.

Project-specific background:

- `planar-curve-reduction.md`
- `../../keller-groupoids/notes/keller-groupoids-draft.md`
- `../../citation-hygiene.md`
