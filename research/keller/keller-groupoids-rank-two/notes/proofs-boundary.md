# Keller-groupoid planar boundary additions

Carried over from the tail of the packet's `../../keller-threefold/notes/proofs.md`; the four claims below are
`KG-P07`..`KG-P10` in this problem's ledger.

## KG-P07 — Ramanujam–Morrow boundary recognition

**Status:** llm proved, modulo the classical Ramanujam–Morrow compactification theorem.

**Claim.** For an SNC completion \(U=Y\setminus D\),
\(U\simeq\mathbb A^2\) iff \((Y,D)\) is connected to
\((\mathbb P^2,L_\infty)\) by boundary blowups/blowdowns.

**Proof.** Necessity is the classical theorem. Sufficiency follows because every
boundary modification is an isomorphism on the complement.

---

## KG-P08 — Boundary obstruction package

Starting from the line \(L_\infty^2=1\), a boundary blowup adds one rational
component, raises \(\rho\) by one, and adds one negative direction to the
intersection form. Hence for \(r\) components the intersection lattice has
signature \((1,r-1)\) and determinant \((-1)^{r-1}\); also \(r=\rho(Y)\).
For a rational surface \(K_Y^2+\rho(Y)=10\), giving \(K_Y^2=10-r\).
Boundary modifications preserve the end, whose model for
\(\mathbb P^2\setminus L_\infty\) is \(S^3\).

---

## KG-P09 — SNC inertia reconstruction

At an SNC node the local complement is \((\Delta^*)^2\), so the two meridian
images commute. Characteristic-zero ramification is tame. Abhyankar's lemma
gives the Kummer local form after étale localization. Passing to the Galois
closure reduces the local normalization to finite abelian/toroidal data; the
resulting surface quotient singularities have standard toric/Hirzebruch–Jung
resolutions. Hence the resolved cover graph is computable from the SNC graph
and inertia labels.

---

## KG-P10 — Exact decision with deletion data

Once deletion data specifies the open source inside the normalization, resolve
all missing isolated points into boundary components. This produces an SNC pair
\((\bar X,E)\). Apply KG-P07. Therefore the final recognition problem is exact;
the remaining research problem is constraining which decorated plane curves and
deletion data can arise from Keller maps.

---
