import KellerGroupoids.Interfaces
import KellerGroupoids.ExternalResearchAxioms

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- A6-1. -/
axiom degreeSix_nonmaximal_A6_forces_boundaryPacket
    (F : PlaneKellerMap)
    (hdeg : genericDegree F = 6)
    (hce : IsPlaneCounterexample F)
    (hnm : ¬ IsEtaleMaximal F)
    (hA6 : MonodromyIsA6 F) :
  DegreeSixBoundaryPacketA6 F

/-- A6-2. -/
axiom degreeSix_A6_forcedFiberCensus
    (F : PlaneKellerMap)
    (hpacket : DegreeSixBoundaryPacketA6 F) : Prop

/-- A6-3. -/
axiom degreeSix_A6_nodeInertia
    (F : PlaneKellerMap)
    (hpacket : DegreeSixBoundaryPacketA6 F) :
  DegreeSixA6NodeInertia F

/-- A6-5 conditional positive-braid elimination. -/
axiom positiveBraid_excludes_A6NodalRepresentation : Prop

/-- Open final model target. -/
axiom noAssiNodalA6Quotient_target : Prop

end ProjectStatements
end KellerGroupoids
