import KellerGroupoids.PublishedAxioms

/-!
# Public current research inputs

Results from public but unrefereed research (`alok/jacobian-two`, checked
2026-09-14), kept in their own namespace so an axiom audit never blurs them
with published inputs. They are stated over the geometric interface in the
form the degree-six chain consumes.
-/

set_option autoImplicit false

namespace KellerGroupoids
namespace ExternalResearch

variable {F : PlaneKellerMap}

/-- **The six-sheet frontier.** A degree-six plane Keller counterexample has monodromy `A₆` or
`S₆`. -/
axiom degreeSixMonodromyFrontier (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hce : G.isCounterexample) :
    G.monodromy = alternatingGroup (Fin G.degree) ∨ G.monodromy = ⊤

/-- **The refined degree-six boundary budget**, in the form the `A₆` packet argument uses: the two
Assi fibers of a non-maximal degree-six counterexample have generic deficits summing to `5`,
each at most `3`. -/
axiom refinedDegreeSixBoundaryBudget (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hce : G.isCounterexample) (hnm : ¬ G.etaleMaximal) (C₀ C₁ : G.Curve) (hne : C₀ ≠ C₁) :
    G.deficit C₀ + G.deficit C₁ = 5 ∧ 2 * G.deficit C₀ ≤ 6 ∧ 2 * G.deficit C₁ ≤ 6

end ExternalResearch
end KellerGroupoids
