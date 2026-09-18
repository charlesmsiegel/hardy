import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- EM-1. -/
axiom normalizationBoundary_pureDivisorial (F : PlaneKellerMap) : boundaryPureDivisorial F

/-- EM-2. -/
axiom genericallyUnramifiedBoundary_everywhereEtale
    (F : PlaneKellerMap) (E : BoundaryComponent F)
    (h : boundaryGenericallyUnramified F E) :
  boundaryEverywhereEtale F E

/-- EM-3. -/
axiom unramifiedBoundary_isA1_and_normalization
    (F : PlaneKellerMap) (E : BoundaryComponent F)
    (h : boundaryGenericallyUnramified F E) :
  boundaryIsA1 F E ∧ curveNormalizationIsA1 F (boundaryImageCurve F E) ∧
    boundaryMapsAsNormalization F E

/-- EM-4. -/
axiom deletedUnramifiedSheet_forces_multibranch
    (F : PlaneKellerMap) (E : BoundaryComponent F)
    (h : boundaryGenericallyUnramified F E) :
  curveHasMultibranchFiniteSingularity F (boundaryImageCurve F E)

/-- EM-5. -/
axiom allComponentsUnibranch_implies_etaleMaximal
    (F : PlaneKellerMap)
    (h : ∀ C : CurveComponent F, curveOnlyUnibranchFiniteSingularities F C) :
  IsEtaleMaximal F

/-- EM-6. -/
axiom conductorPointFiberBound (F : PlaneKellerMap) : Prop

/-- EM-7.  Highest-priority audit. -/
axiom connectedNonproper_implies_etaleMaximal
    (F : PlaneKellerMap) (h : nonproperConnected F) :
  IsEtaleMaximal F

/-- Contrapositive of EM-7. -/
axiom nonmaximal_implies_nonproperDisconnected
    (F : PlaneKellerMap) (h : ¬ IsEtaleMaximal F) :
  nonproperDisconnected F

/-- EM-8. -/
axiom disconnectedNonproper_commonPencil
    (F : PlaneKellerMap) (h : nonproperDisconnected F) :
  NonproperIsCommonPencil F

/-- EM-10.  Assi supplies the classification once its hypotheses are reached; the Keller-to-Assi
reduction is the project theorem. -/
axiom nonmaximal_implies_exactlyTwoNodalFibers
    (F : PlaneKellerMap) (h : ¬ IsEtaleMaximal F) :
  NonproperIsExactlyTwoNodalFibers F

end ProjectStatements
end KellerGroupoids
