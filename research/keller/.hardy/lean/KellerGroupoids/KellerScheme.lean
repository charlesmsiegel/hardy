import KellerGroupoids.KellerEtale
import KellerGroupoids.ZariskiMain

/-!
# A Keller map as a morphism of schemes, and its Zariski-Main factorisation

`KellerEtale` proves the comorphism of a Keller map is an étale ring map and
`ZariskiMain` re-states Mathlib's Zariski's Main Theorem. This file joins them:
a Keller map is a morphism of schemes `A^n → A^n`, it is étale, hence
quasi-finite, and being affine it is separated and quasi-compact, so the
factorisation applies to it and the boundary `D = X̄ ∖ U` of the étale-maximality
reduction is a real object rather than a named field.
-/

set_option autoImplicit false

namespace KellerGroupoids
namespace KellerScheme

open AlgebraicGeometry CategoryTheory KellerEtale

variable {n : ℕ}

/-- Affine `n`-space over `ℂ`, as the spectrum of its coordinate ring. -/
noncomputable abbrev affineSpace (n : ℕ) : Scheme :=
  Spec (CommRingCat.of (Coord n))

/-- A polynomial self-map of affine `n`-space as a morphism of schemes. -/
noncomputable def map (P : PolyMap n) : affineSpace n ⟶ affineSpace n :=
  Spec.map (CommRingCat.ofHom (comap P))

instance (P : PolyMap n) : IsAffineHom (map P) := by
  unfold map; infer_instance

instance (P : PolyMap n) : IsSeparated (map P) := IsSeparated.of_isAffineHom _

/-- **A Keller map is étale.** The scheme-level form of `KellerEtale.etale_comap`. -/
theorem etale_map {P : PolyMap n} (h : P.IsKeller) : Etale (map P) := by
  rw [map, HasRingHomProperty.Spec_iff (P := @Etale)]
  exact etale_comap h

/-- A Keller map is quasi-finite: étale ring maps are. -/
theorem locallyQuasiFinite_map {P : PolyMap n} (h : P.IsKeller) : LocallyQuasiFinite (map P) := by
  rw [map, HasRingHomProperty.Spec_iff (P := @LocallyQuasiFinite)]
  have : (comap P).Etale := etale_comap h
  letI inst : Algebra (Coord n) (Coord n) := (comap P).toAlgebra
  haveI : @Algebra.Etale (Coord n) (Coord n) _ _ inst := this
  exact (inferInstance : @Algebra.QuasiFinite (Coord n) (Coord n) _ _ inst)

/-- **The Zariski-Main factorisation of a Keller map.** The source sits as an open subscheme of
the normalisation of the target in it, and the map out of the normalisation is integral. This is
the `A^n = U ↪ X̄ →^π A^n` picture the étale-maximality reduction is set up along, with no
assumption beyond the Keller condition. -/
theorem factorization {P : PolyMap n} (h : P.IsKeller) :
    IsOpenImmersion (map P).toNormalization ∧ IsIntegralHom (map P).fromNormalization ∧
      (map P).toNormalization ≫ (map P).fromNormalization = map P := by
  haveI : Etale (map P) := etale_map h
  haveI : LocallyQuasiFinite (map P) := locallyQuasiFinite_map h
  haveI : LocallyOfFiniteType (map P) := inferInstance
  exact ZariskiMain.factorization (map P)

end KellerScheme
end KellerGroupoids
