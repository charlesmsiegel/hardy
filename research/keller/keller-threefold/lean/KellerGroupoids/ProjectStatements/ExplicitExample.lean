import KellerGroupoids.Interfaces

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

/-- Explicit degree-three marked-cubic example: Conf_1=A3, Conf_2≅Conf_3≅Y, higher Conf empty. -/
axiom markedCubic_configurationCollapse : Prop

/-- Exact Stirling formula K_r ≅ A3 disjoint union ((3^r-3)/6) copies of Y. -/
axiom markedCubic_allNerveLevelsFormula : Prop

/-- Strong finite morphism type of the marked-cubic nerve. -/
axiom markedCubic_finiteMorphismType : Prop

end ProjectStatements
end KellerGroupoids
