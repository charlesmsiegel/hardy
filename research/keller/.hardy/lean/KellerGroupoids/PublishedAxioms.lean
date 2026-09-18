import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace Published

/-! Every declaration in this file is intended to represent an imported published/classical
result.  Comments identify the source used by the prose packet. -/

/-- Stacks/standard AG: Keller condition implies étaleness. -/
axiom keller_isEtale {n : Nat} (F : KellerMap n) : IsEtaleMap F

/-- Stacks: diagonal of an unramified morphism is open. -/
axiom unramifiedDiagonalOpen : Prop

/-- Stacks: diagonal of a separated morphism is closed. -/
axiom separatedDiagonalClosed : Prop

/-- Stacks: Zariski Main factorization through a finite normalization. -/
axiom zariskiMainFiniteNormalization (F : PlaneKellerMap) : Prop

/-- Purity of branch locus for the finite-normalization setting used here. -/
axiom branchPurityFiniteNormalSurface (F : PlaneKellerMap) : Prop

/-- Proper quasi-finite morphisms are finite. -/
axiom properQuasiFiniteFinite : Prop

/-- Ax--Grothendieck. -/
axiom axGrothendieck : Prop

/-- Campbell: normal/Galois function-field extension for a complex Keller map forces
invertibility. -/
axiom campbellNormalExtensionAutomorphism : Prop

/-- Jelonek 1999 Theorem 4.6: horizontal boundary curves are copies of C and are pairwise
disjoint in the normal affine-extension setup. -/
axiom jelonekHorizontalBoundaryA1Disjoint (F : PlaneKellerMap) : Prop

/-- Jelonek 1999 Theorem 4.12: irreducible nonproperness components are affine parametric
lines / polynomially parametrized rational curves. -/
axiom jelonekNonproperComponentsParametric
    (F : PlaneKellerMap) (C : CurveComponent F) :
  curvePolynomiallyParametric F C

/-- Nguyen Van Chau 1999: an exceptional/nonproper value curve homeomorphic to C forces a
critical point. -/
axiom chauAffineLineExceptionalValueForcesCriticalPoint (F : PlaneKellerMap) : Prop

/-- Nguyen Van Chau 2004: a nonempty plane Keller nonproperness curve has one projective point
at infinity. -/
axiom chauOnePointAtInfinity (F : PlaneKellerMap) : Prop

/-- Jelonek 2022: connected nonproperness set has positive Euler characteristic. -/
axiom jelonekConnectedNonproperEulerPositive
    (F : PlaneKellerMap) (h : nonproperConnected F) : Prop

/-- Jelonek 2022: in dimension two the nonproperness set cannot be a curve without
self-intersections. -/
axiom jelonekPlaneNonproperHasSelfIntersection (F : PlaneKellerMap) : Prop

/-- Assi 2012: coordinate pencil or at most two rational members for a rational one-place
polynomial. -/
axiom assiRationalOnePlacePencilDichotomy : Prop

/-- Assi Proposition 3.5: disjoint polynomial-parametric curves fall into the coordinate or
exceptional two-rational-fiber alternatives. -/
axiom assiDisjointParametricCurvesTrichotomy : Prop

/-- Assi Proposition 3.3 + Lemma 3.2: the exceptional two rational fibers have equal positive
numbers of two-branch Milnor-one singularities (ordinary nodes). -/
axiom assiExceptionalTwoFibersEquinodal : Prop

/-- Braun--Dias--Venato-Santos 2018: the nonproperness set of a counterexample meets every
A^(n-1)-fiber of a polynomial submersion. -/
axiom counterexampleMeetsEverySubmersionFiber
    (F : PlaneKellerMap) (h : IsPlaneCounterexample F) : Prop

/-- Ramanujam--Morrow recognition of A2 by boundary modifications. -/
axiom ramanujamMorrowBoundaryRecognition : Prop

/-- Suzuki / Abhyankar--Moh: delta-sequence data determines the one-place resolution graph. -/
axiom suzukiDeltaSequenceResolution : Prop

/-- Andreotti--Frankel affine CW-dimension bound. -/
axiom andreottiFrankel : Prop

/-- Algebraic/topological Riemann existence interface used for finite étale monodromy covers. -/
axiom riemannExistenceFiniteEtale : Prop

/-- Connected finite étale covers of A1_C are trivial. -/
axiom affineLineFiniteEtaleRigidity : Prop

/-- Normal complex surfaces are Cohen--Macaulay in the form used with miracle flatness. -/
axiom normalSurfaceCohenMacaulay : Prop

/-- Miracle flatness / finite-normalization flatness in the planar setting. -/
axiom finiteNormalizationFlatness (F : PlaneKellerMap) : Prop

/-- Neumann--Norbury: for nodal affine curves, the Orevkov invariant agrees with the actual
complement fundamental group. -/
axiom neumannNorburyNodalOrevkovEqualsPiOne : Prop

/-- Orevkov positive-braid input: positive braid at infinity gives abelian Orevkov invariant
in the form used by Neumann--Norbury. -/
axiom orevkovPositiveBraidAbelian : Prop

/-- Orevkov exact boundary degree/multiplicity budget, in the form used by the degree-six
analysis.  Primary bibliographic lemma pin should be audited before publication. -/
axiom orevkovBoundaryBudget : Prop

/-- External collision-ideals theorem: planar secant determinant gives the clopen diagonal /
off-diagonal idempotent splitting.  Source: pinned Collision Ideals manuscript/repository. -/
axiom planarSecantDeterminantIdempotent : Prop

end Published
end KellerGroupoids
