import Mathlib.AlgebraicGeometry.ZariskisMainTheorem

/-!
# Zariski's Main Theorem, in the form the Keller chain uses

The étale-maximality reduction is set up along the factorisation

`A² = U ↪ X̄ →^π A²`

of a plane Keller map through the normalisation of the target in the source.
The ledger carried that factorisation as the literature input `PUB-AG-04`,
with no Lean, on the belief that Mathlib did not have it. Mathlib does have
it: `AlgebraicGeometry.Scheme.Hom.normalization` is the relative
normalisation, `fromNormalization` out of it is integral, and Zariski's Main
Theorem says the map into it is an open immersion as soon as the morphism is
quasi-finite, separated, quasi-compact and of finite type.

So this file is not an axiom. It re-states Mathlib's theorem in the shape the
chain consumes, and the ledger points `PUB-AG-04` here.

What Mathlib does not give, and the chain should not assume silently, is that
`fromNormalization` is *finite* rather than merely integral. That is the
finiteness of integral closure for finitely generated algebras over a field;
it is true in the Keller setting but is not available at this generality, so
`factorization` claims only integrality.
-/

set_option autoImplicit false

universe u

namespace KellerGroupoids
namespace ZariskiMain

open AlgebraicGeometry CategoryTheory

variable {X Y : Scheme.{u}} (f : X ⟶ Y)

/-- **`PUB-AG-04`, Zariski's Main Theorem.** A quasi-finite, separated, quasi-compact morphism of
finite type factors as an open immersion into an integral morphism: the normalisation of the
target in the source. This is Mathlib's theorem, not an assumption of this project. -/
theorem factorization [LocallyOfFiniteType f] [LocallyQuasiFinite f] [IsSeparated f]
    [QuasiCompact f] :
    IsOpenImmersion f.toNormalization ∧ IsIntegralHom f.fromNormalization ∧
      f.toNormalization ≫ f.fromNormalization = f :=
  ⟨inferInstance, inferInstance, f.toNormalization_fromNormalization⟩

/-- The open immersion half on its own, the form the boundary `D = X̄ ∖ U` is defined from. -/
theorem isOpenImmersion_toNormalization [LocallyOfFiniteType f] [LocallyQuasiFinite f]
    [IsSeparated f] [QuasiCompact f] : IsOpenImmersion f.toNormalization := inferInstance

/-- The boundary of the normalisation: the points of `X̄` not in the image of the source. The
étale-maximality reduction is a statement about this set. -/
def boundary [LocallyOfFiniteType f] [LocallyQuasiFinite f] [IsSeparated f] [QuasiCompact f] :
    Set f.normalization :=
  (Set.range f.toNormalization.base)ᶜ

end ZariskiMain
end KellerGroupoids
