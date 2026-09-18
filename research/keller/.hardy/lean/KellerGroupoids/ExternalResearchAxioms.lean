import KellerGroupoids.PublishedAxioms

set_option autoImplicit false

namespace KellerGroupoids
namespace ExternalResearch

/-! Public current research inputs that are useful for comparison but are not being presented as
peer-reviewed/published axioms. -/

/-- Current exact six-sheet frontier in alok/jacobian-two: a hypothetical degree-six plane Keller
counterexample has monodromy A6 or S6. -/
axiom degreeSixMonodromyFrontier
    (F : PlaneKellerMap)
    (hdeg : genericDegree F = 6)
    (hce : IsPlaneCounterexample F) :
  MonodromyIsA6 F ∨ MonodromyIsS6 F

/-- Current refined degree-six Orevkov/Riemann--Hurwitz budget:
sum_E (e_E d_E + delta_E) = 5 with delta_E >= 0. -/
axiom refinedDegreeSixBoundaryBudget : Prop

end ExternalResearch
end KellerGroupoids
