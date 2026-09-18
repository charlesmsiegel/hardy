import KellerGroupoids.Interfaces
import KellerGroupoids.ExternalResearchAxioms

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- PM-1.  Candidate theorem; highest-priority audit. -/
axiom nonmaximal_implies_perfectMonodromy
    (F : PlaneKellerMap) (h : ¬ IsEtaleMaximal F) :
  IsPerfectMonodromy F

/-- Elementary group-theoretic fact to prove in Lean rather than leave external:
S6 has the nontrivial sign quotient and is not perfect. -/
axiom s6_notPerfect
    (F : PlaneKellerMap) (h : MonodromyIsS6 F) :
  ¬ IsPerfectMonodromy F

/-- Conditional S6 exclusion from PM-1. -/
axiom nonmaximal_not_S6
    (F : PlaneKellerMap) (hnm : ¬ IsEtaleMaximal F) :
  ¬ MonodromyIsS6 F

/-- Combining the external degree-six frontier with perfectness leaves A6. -/
axiom degreeSix_nonmaximal_frontier_forces_A6
    (F : PlaneKellerMap)
    (hdeg : genericDegree F = 6)
    (hce : IsPlaneCounterexample F)
    (hnm : ¬ IsEtaleMaximal F) :
  MonodromyIsA6 F

/-- PM-3 parity filter on the boundary packet. -/
axiom perfectMonodromy_inertiaEvenParity (F : PlaneKellerMap) : Prop

end ProjectStatements
end KellerGroupoids
