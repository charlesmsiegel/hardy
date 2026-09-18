import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- KG-P1. -/
axiom fiberDeficit_eq_inertiaSupport_add_deleted
    (F : PlaneKellerMap) (C : CurveComponent F) :
  genericDegree F - genericFiberCardOnCurve F C =
    permSupportSize (inertiaPerm F C) + deletedUnramifiedSheets F C

/-- KG-P2. -/
axiom deficitOne_forces_oneDeletedUnramifiedSheet
    (F : PlaneKellerMap) (C : CurveComponent F)
    (h : genericDegree F - genericFiberCardOnCurve F C = 1) :
  permSupportSize (inertiaPerm F C) = 0 ∧ deletedUnramifiedSheets F C = 1

/-- KG-P3. -/
axiom etaleMaximal_topConfigurationStabilizes
    (F : PlaneKellerMap) (h : IsEtaleMaximal F) : Prop

/-- KG-P4. -/
axiom configurationUnitRankBound (F : PlaneKellerMap) : Prop

/-- KG-P5; overlaps the external Collision Ideals result and should eventually be proved from
Published.planarSecantDeterminantIdempotent or independently. -/
axiom secantProjectorCriterion (F : PlaneKellerMap) : Prop

/-- Boolean equality projectors on every nerve level. -/
axiom booleanEqualityProjectors (F : PlaneKellerMap) : Prop

/-- KG-E2 global Euler-deficit identity. -/
axiom globalEulerDeficitIdentity (F : PlaneKellerMap) : Prop

end ProjectStatements
end KellerGroupoids
