import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- Proposition 1.3 / general diagonal decomposition input. -/
axiom diagonalClopen {n : Nat} (F : KellerMap n) : Prop

/-- Corollary 1.4. -/
axiom automorphism_iff_conf2_empty {n : Nat} (F : KellerMap n) : Prop

/-- Proposition 2.2 + KG-G1 smooth affine part. -/
axiom everyLevelSmoothAffine {n : Nat} (F : KellerMap n) :
  SmoothAffineParallelizableLevels F

/-- Proposition 2.3: algebraic tangent bundles are trivial. -/
axiom canonicalAlgebraicParallelism {n : Nat} (F : KellerMap n) : Prop

/-- Proposition 2.4. -/
axiom nerveTwoCoskeletal {n : Nat} (F : KellerMap n) : NerveTwoCoskeletal F

/-- Theorem 3.2 / KG-G2. -/
axiom partitionDecomposition {n : Nat} (F : KellerMap n) : PartitionDecomposition F

/-- Lemma 4.1. -/
axiom fiberCard_le_genericDegree {n : Nat} (F : KellerMap n) (y : AffinePoint n) :
  fiberCard F y ≤ genericDegree F

/-- Corollary 4.2 / KG-G3. -/
axiom finiteConfigurationPalette {n : Nat} (F : KellerMap n) : FiniteConfigurationPalette F

/-- KG-G4 exact set-theoretic image criterion. -/
axiom confImage_iff_fiberCard_ge {n k : Nat} (F : KellerMap n) (y : AffinePoint n) :
  (∃ x : Fin k → AffinePoint n,
      PairwiseDistinct x ∧ ∀ i, F (x i) = y) ↔
    k ≤ fiberCard F y

/-- Proposition 5.3. -/
axiom fullFiberLocus_is_properLocus {n : Nat} (F : KellerMap n) :
  ProperLocusIsFullFiberLocus F

/-- Proposition 6.1: forgetting the last point has fiber cardinality m-k+1 over an m-point
fiber. -/
axiom forgetfulConfigurationFiberCount {n : Nat} (F : KellerMap n) : Prop

/-- Proposition 6.2 / KG-G5. -/
axiom topForgetfulOpenImmersion {n : Nat} (F : KellerMap n) : TopForgetfulOpenImmersion F

/-- Theorem 7.1 / KG-G6. -/
axiom monodromyOrbitTheorem {n : Nat} (F : KellerMap n) : MonodromyOrbitDescription F

/-- Corollary 7.2 / KG-G7. -/
axiom topConfigurationGaloisClosure {n : Nat} (F : KellerMap n) :
  TopConfigurationGaloisClosure F

/-- Corollary 7.3, modulo Campbell. -/
axiom normalExtensionForbiddenForCounterexample {n : Nat} (F : KellerMap n) : Prop

/-- Section 8 top symmetric quotient. -/
axiom topUnorderedConfigurationQuotient {n : Nat} (F : KellerMap n) : Prop

/-- KG-G8 + Corollaries 9.1--9.3. -/
axiom stirlingInvariantCalculus {n : Nat} (F : KellerMap n) : StirlingInvariantCalculus F

/-- Theorem 10.1. -/
axiom finiteGeometricPalette {n : Nat} (F : KellerMap n) : Prop

/-- Section 11 factorial moment identity (schematic Euler interface). -/
axiom configurationEulerFactorialMoment (F : PlaneKellerMap) : Prop

/-- Čech realization/descent statement. -/
axiom cechRealizationRecoversImage {n : Nat} (F : KellerMap n) :
  CechRealizationRecoversImage F

/-- Proposition 12.1. -/
axiom verticalHomologyBound {n : Nat} (F : KellerMap n) : VerticalHomologyBound F

/-- Corollary 12.2. -/
axiom finiteHomologicalPalette {n : Nat} (F : KellerMap n) : Prop

/-- Proposition 13.1. -/
axiom canonicalFrameLNDTrivialityCriterion {n : Nat} (F : KellerMap n) :
  CanonicalVectorFieldFrame F

end ProjectStatements
end KellerGroupoids
