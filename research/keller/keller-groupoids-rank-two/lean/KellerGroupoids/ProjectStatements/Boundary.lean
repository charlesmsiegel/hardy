import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- BG-1. -/
axiom boundaryBirationalRecognitionA2 : Prop

/-- BG-2. -/
axiom weightedGraphCertificateA2 : Prop

/-- BG-3(a-f) numerical/topological obstruction package. -/
axiom A2BoundaryNecessaryConditions : Prop

/-- BG-4. -/
axiom topologicalRecognitionA2 : Prop

/-- Inertia-decorated SNC cover reconstruction. -/
axiom inertiaDecoratedSNCReconstruction : Prop

end ProjectStatements
end KellerGroupoids
