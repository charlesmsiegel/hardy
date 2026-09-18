# External theorem / source index

This file is the source of truth for external theorem interfaces in the Lean packet.

## Published / classical inputs

1. **Stacks Project / standard AG**
   - étale maps are open;
   - diagonal of an unramified morphism is open;
   - diagonal of a separated morphism is closed;
   - Zariski Main factorization;
   - purity of branch locus in the regular/normal finite setting;
   - proper + quasi-finite = finite.

2. **Ax--Grothendieck**
   Injective polynomial self-maps of affine space over characteristic zero are automorphisms.

3. **Campbell normal-extension criterion**
   A complex Keller map whose induced function-field extension is normal/Galois is invertible.

4. **Jelonek 1999**, *Testing sets for properness of polynomial mappings*, Math. Ann. 315,
   DOI 10.1007/s002080050316.
   - Theorem 4.6: horizontal boundary components in the relevant normal affine extension of
     an acyclic surface are copies of C and are pairwise disjoint.
   - Theorem 4.12: the nonproperness set is a finite union of affine parametric lines.

5. **Nguyen Van Chau 1999**, *A remark on Vitushkin's covering*, Acta Math. Vietnam. 24.
   A polynomial plane map whose exceptional/nonproper value set is homeomorphic to C must
   have a critical point.  Used to exclude a smooth A1 nonproperness component for a Keller
   map.

6. **Nguyen Van Chau 2004**, *Note on the Jacobian condition and the non-proper value set*,
   Ann. Polon. Math. 84, DOI 10.4064/ap84-3-2.
   A nonempty nonproperness curve of a plane Keller map has one projective point at infinity.

7. **Jelonek 2022**, *A note on the Jacobian Conjecture*, Colloq. Math. 170,
   DOI 10.4064/cm8671-12-2021.
   If the nonproperness set is connected, its Euler characteristic is positive; in dimension
   two it cannot be a curve without self-intersections.

8. **Abdallah Assi 2012**, *Rational curves with one place at infinity*, arXiv:1206.6189.
   - coordinate pencil or at most two rational fibers;
   - disjoint polynomial-parametric curve trichotomy;
   - exceptional two-rational-fiber case has equal numbers of two-branch Milnor-one
     singularities (ordinary transverse nodes).

9. **Braun--Dias--Venato-Santos 2018**, *On Topological Approaches to the Jacobian
   Conjecture in C^n*, arXiv:1807.03782.
   For a counterexample, the nonproperness set meets every hypersurface h^{-1}(0)
   biregular to C^{n-1} arising from a polynomial submersion.

10. **Ramanujam--Morrow compactification/recognition of A2**
    An SNC completion of A2 is boundary-birational to (P2, line at infinity), and conversely.

11. **Suzuki / Abhyankar--Moh one-place curve machinery**
    A one-place-at-infinity curve is encoded at infinity by a finite delta-sequence satisfying
    explicit semigroup conditions; the sequence determines the minimal weighted resolution
    graph at infinity.

12. **Andreotti--Frankel**
    A smooth affine complex n-fold has the homotopy type of a real CW complex of dimension at
    most n.

13. **Riemann existence / finite étale cover classification**
    Finite étale covers of a complex variety correspond to finite topological monodromy data in
    the uses made here.

14. **Finite étale rigidity of A1_C**
    Connected finite étale covers of the complex affine line are trivial.

15. **Neumann--Norbury 2003**, *The Orevkov invariant of an affine plane curve*, Trans. AMS
    355 (2003), 519--538.
    For nodal affine curves the Orevkov invariant agrees with the actual complement
    fundamental group; the paper also records/uses the positive-braid theory needed below.

16. **Orevkov positive-braid / boundary-budget inputs**
    - positive-braid-at-infinity cases have abelian Orevkov invariant in the form used by
      Neumann--Norbury;
    - the boundary multiplicity identity used in current degree-six work is Orevkov's exact
      degree budget.  Primary bibliographic pinning of the exact lemma number should be
      completed before publication.

## External public research input (not promoted to published axiom)

17. **alok/jacobian-two**, `docs/refined-six-sheet-budget.md` (public repository, checked
    2026-09-14): an exact current analysis claims the necessary degree-six monodromy frontier
    \(G=A_6\) or \(G=S_6\), and refines Orevkov's budget to
    \(\sum_E(e_E d_E+\delta_E)=5\) with \(\delta_E\ge0\).
    This is represented in `ExternalResearchAxioms.lean`, not `PublishedAxioms.lean`.

## Collision/secant provenance

18. *Collision Ideals and Off-Diagonal Sheets*, attributed on the manuscript title page to
    Chloe van der Vlugt; public repository at pinned commit
    `a409db9922279907493d96f691b5ea9eb71baaf9` (currently exposed as
    `what-social-construct/jacobian-collision-geometry`).  This is the external source for the
    planar secant-determinant/idempotent splitting if that criterion becomes load-bearing.

19. Roy van Rijn, `royvanrijn/jacobian-research`, contains earlier independent off-diagonal and
    saturation constructions.  Contact Roy before submission if the overlap is important; see
    `roy-van-rijn-provenance.md`.
