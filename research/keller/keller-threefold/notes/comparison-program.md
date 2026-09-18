# Comparison Program: Separating the Three Axes

**Snapshot:** 2026-09-14

This note is a research/comparison work queue, not part of the proved-claim registry yet.
Literature-derived claims below should be audited against primary sources before being promoted
into the manuscript or the ledger (`ledger/`).

## The three conceptual axes

For comparison purposes, separate the Keller threefold package into:

- **R — birational simplicity:** \(X\) is rational.
- **A — additive rigidity:** \(\mathrm{ML}(X)=\mathcal O(X)\), equivalently no nontrivial algebraic \(\mathbb G_a\)-actions.
- **T — topological near-miss to affine space:** \(\pi_1(X)=1\), but \(X\) is noncontractible.

The Keller filling currently claims all three, plus the sharper conditions

\[
\mathrm{Cl}(X)=\mathrm{Pic}(X)=0,\qquad
\mathcal O(X)^*=\mathbb C^*,\qquad
\chi(X)=1,\qquad
H_2(X;\mathbb Z)\cong H_3(X;\mathbb Z)\cong\mathbb Z.
\]

The comparison section should distinguish the broad \(R+A+T\) phenomenon from this sharper package.

---

## Near-miss A: affine 3-spheres — fail additive rigidity

### Basic example

\[
SL_2(\mathbb C)=\{ad-bc=1\}\subset\mathbb A^4.
\]

Expected comparison profile:

| Property | Status |
|---|---|
| smooth affine threefold | yes |
| rational | yes |
| factorial / Picard-trivial | yes (standard for simply connected semisimple group; verify preferred citation) |
| constant units | yes |
| simply connected | yes |
| noncontractible | yes; homotopy type of \(S^3\) |
| LND-rigid | **no** |
| topology | \(H_3\cong\mathbb Z,\ H_2=0,\ \chi=0\) |

The failure of rigidity is not subtle: left/right multiplication by unipotent subgroups gives
nontrivial \(\mathbb G_a\)-actions.

### Stronger comparison

Dubouloz–Finston's exotic affine 3-spheres are smooth affine threefolds with the same
differentiable/topological flavor as the affine 3-sphere, while carrying abundant additive
geometry; their paper explicitly discusses examples with trivial Makar-Limanov invariant.

**Use in paper:** this is the cleanest dimension-three family showing that

\[
R+T \not\Rightarrow A.
\]

Primary literature target:

- A. Dubouloz, D. Finston, *On exotic affine 3-spheres*, arXiv:1106.2900.

---

## Near-miss T: complements of high-degree hypersurfaces in projective 3-space — fail simple connectivity

Let \(D_d\subset\mathbb P^3\) be a smooth hypersurface of degree \(d\ge 5\), and put

\[
U_d=\mathbb P^3\setminus D_d.
\]

Profile:

| Property | Status |
|---|---|
| smooth affine threefold | yes; \(D_d\) is ample |
| rational | yes; \(U_d\) has the function field of \(\mathbb P^3\) |
| log Kodaira dimension | \(3\) for \(d\ge5\), since \(K_{\mathbb P^3}+D_d=(d-4)H\) |
| LND-rigid | yes, assuming the standard implication nontrivial \(\mathbb G_a\)-action \(\Rightarrow\mathbb A^1\)-uniruled \(\Rightarrow\bar\kappa=-\infty\) |
| simply connected | **no** |
| fundamental group | classically \(\pi_1(U_d)\cong\mathbb Z/d\mathbb Z\) |

Thus this family gives a very clean

\[
R+A \not\Rightarrow T.
\]

It is *not* a full-package near miss: for example the complement generally has nontrivial
Picard/class-group torsion. That is useful rather than embarrassing: it shows why the factorial
and \(\mathrm{Pic}=0\) conditions should remain visible in the comparison table.

Literature targets:

- classical Zariski/Lefschetz results on complements of smooth hypersurfaces;
- A. Libgober, *Homotopy groups of complements to ample divisors*;
- Dubouloz–Kishimoto / standard log-uniruledness results for
  \(\mathbb A^1\)-uniruled affine varieties.

---

## Near-miss R: quartic Fano minus a hyperplane K3 — fail rationality

This is the most interesting comparison candidate found in this pass.

Let

\[
V_4\subset\mathbb P^4
\]

be a smooth quartic threefold, let \(D=V_4\cap H\) be a smooth hyperplane section
(a quartic K3 surface), and set

\[
U=V_4\setminus D.
\]

### Birational axis

Iskovskikh–Manin proved every smooth quartic threefold is nonrational. Since \(U\) has the same
function field as \(V_4\),

\[
U\ \text{is nonrational}.
\]

Thus **R fails**.

### Additive-rigidity axis

Adjunction gives

\[
K_{V_4}=-H,\qquad D\sim H,
\]

hence

\[
K_{V_4}+D\sim 0.
\]

Therefore

\[
\bar\kappa(U)=0.
\]

A nontrivial \(\mathbb G_a\)-action would make \(U\) \(\mathbb A^1\)-uniruled, which for a smooth
affine variety forces log Kodaira dimension \(-\infty\). Hence \(U\) should be LND-rigid.

This reference chain should be checked carefully before manuscript insertion, but it is conceptually
very clean.

### Fundamental group

By Lefschetz, \(V_4\) is simply connected and

\[
\operatorname{Pic}(V_4)\cong\mathbb Z[H].
\]

Libgober's theorem for complements of smooth ample divisors in simply connected projective
manifolds implies that \(\pi_1(U)\) is abelian. The meridian relation is controlled by the
divisibility of \([D]\); here \(D=H\) is primitive. Equivalently, the Gysin sequence gives
\(H_1(U;\mathbb Z)=0\). Thus the expected conclusion is

\[
\pi_1(U)=1.
\]

This derivation should be written out and independently audited before being treated as a
literature-verified fact.

### Noncontractibility: Euler characteristic check

For a smooth degree-\(d\) hypersurface threefold in \(\mathbb P^4\),

\[
c(TV_d)=\frac{(1+H)^5}{1+dH}.
\]

For \(d=4\),

\[
c_3(TV_4)=-14H^3,\qquad \int_{V_4}H^3=4,
\]

so

\[
\chi(V_4)=-56.
\]

The hyperplane section \(D\) is a K3 surface, hence \(\chi(D)=24\). Therefore

\[
\chi(U)=\chi(V_4)-\chi(D)=-80.
\]

So \(U\) is certainly noncontractible.

### Factoriality and units

The divisor exact sequence should give

\[
\operatorname{Pic}(U)=
\operatorname{Pic}(V_4)/\mathbb Z[D]=0
\]

because \(D=H\) generates \(\operatorname{Pic}(V_4)\). Since \(U\) is smooth affine,

\[
\operatorname{Cl}(U)=\operatorname{Pic}(U)=0,
\]

so \(U\) is factorial.

The same divisor sequence suggests

\[
\mathcal O(U)^*=\mathbb C^*
\]

because a rational function whose divisor is supported on \(D\) would give a relation
\(m[D]=0\) in the torsion-free group \(\operatorname{Pic}(V_4)\).

### Provisional profile

| Property | Keller \(X\) | Quartic complement \(U\) |
|---|---:|---:|
| smooth affine 3-fold | yes | yes |
| rational | **yes** | **no** |
| factorial | yes | expected yes |
| constant units | yes | expected yes |
| LND-rigid | yes | expected yes |
| simply connected | yes | expected yes |
| noncontractible | yes | yes |
| Euler characteristic | \(1\) | \(-80\) |
| \(H_2,H_3\) | \(\mathbb Z,\mathbb Z\) | to compute |

If all checks survive, this is an unusually good "one conceptual axis fails" example:

\[
A+T \not\Rightarrow R.
\]

Primary literature targets:

- Iskovskikh–Manin: irrationality / birational superrigidity of smooth quartic threefolds.
- Lefschetz hyperplane theorem for \(\pi_1\) and Picard rank.
- A. Libgober, *Homotopy groups of complements to ample divisors*.
- Standard \(\mathbb A^1\)-uniruled \(\Rightarrow\bar\kappa=-\infty\) results.

---

# Theorem-level comparison principles to add

Rather than only listing examples, the manuscript should display mechanisms that manufacture
two axes without the third.

## Principle 1: log Kodaira dimension obstructs additive actions

For smooth affine varieties, a nontrivial \(\mathbb G_a\)-action gives an
\(\mathbb A^1\)-uniruled structure; smooth \(\mathbb A^1\)-uniruled varieties have
\(\bar\kappa=-\infty\).

So

\[
\bar\kappa(X)\ge0 \quad\Longrightarrow\quad X\text{ is LND-rigid}
\]

under the standard hypotheses.

This is broader than the special surface obstruction used in the Rees theorem. The surface-specific
input in the Rees proof is the converse-style step that a *generically finite curve-cylinder covering*
forces negative log Kodaira dimension.

## Principle 2: ample-divisor complements constrain \(\pi_1\)

If \(V\) is simply connected projective and \(D\) is a smooth ample divisor, results of
Lefschetz/Libgober strongly constrain \(\pi_1(V\setminus D)\); in the one-component case it is
abelian, and primitive divisor classes can force it to vanish.

This gives a systematic way to manufacture simply connected affine threefolds independently of
their birational type.

## Principle 3: rationality is largely orthogonal

Rationality is a function-field statement. The quartic-complement construction above is designed
to keep the rigidity/topology mechanism while replacing a rational projective compactification by
a birationally superrigid one.

Conversely, rational log-general-type complements such as
\(\mathbb P^3\setminus D_d\) give rational rigid affine threefolds with nontrivial fundamental group.

## Principle 4: additive actions plus contractibility point in another direction

Koras–Russell threefolds are smooth topologically contractible affine threefolds and admit
nontrivial \(\mathbb G_a\)-actions. They are a useful warning that being extremely close to
\(\mathbb A^3\) topologically does not imply additive rigidity.

---

# Consequence for the novelty claim

The paper should **not** make the broad slogan

> rational + rigid + simply connected + noncontractible

carry the full priority burden until we have searched rational index-one Fano complements and
similar log-Calabi–Yau constructions. There may well be earlier examples satisfying those four
coarse properties.

The safer and more mathematically informative target is the sharper conjunction:

\[
\begin{gathered}
\text{smooth affine rational factorial rigid},\\
\mathcal O(X)^*=\mathbb C^*,\quad
\operatorname{Pic}(X)=0,\quad
\pi_1(X)=1,\\
H_2(X;\mathbb Z)\cong H_3(X;\mathbb Z)\cong\mathbb Z,\quad
\chi(X)=1.
\end{gathered}
\]

The comparison program should actively search for earlier examples satisfying progressively larger
subsets of this list.

---

# Work queue

1. **Audit the quartic-complement example** from primary sources:
   - \(\pi_1(U)=1\);
   - \(\operatorname{Pic}(U)=0\);
   - \(\mathcal O(U)^*=\mathbb C^*\);
   - rigidity from \(\bar\kappa=0\);
   - compute full integral homology, not just \(\chi=-80\).

2. **Search rational prime Fano index-one complements.**
   A rational smooth Fano threefold \(V\) with \(\operatorname{Pic}(V)=\mathbb Z[-K_V]\), together
   with a smooth anticanonical K3 divisor \(D\), would produce another plausible rational,
   factorial, rigid, simply-connected noncontractible affine threefold \(V\setminus D\).
   If such examples exist, they show decisively that the novelty is in the *sharper homology/Euler
   package*, not the coarse \(R+A+T\) triad.

3. **Make a property lattice rather than a prose list.**
   Track examples against:
   rational / factorial / constant units / Picard zero / LND-rigid /
   simply connected / contractible / \(H_2\) / \(H_3\) / Euler characteristic.

4. **Add literature theorems as external nodes** once each source has been checked.
   Do not label them `llm proved`; they should be represented as imported/literature dependencies
   when the registry schema is expanded beyond the current four proof statuses.
