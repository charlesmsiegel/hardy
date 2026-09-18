import KellerGroupoids.Core

/-!
# A Keller map is étale

The Keller condition says the Jacobian determinant of a polynomial self-map of
`A^n` is a nonzero constant. This file turns that into the statement algebraic
geometry uses: the comorphism `ℂ[y] → ℂ[x]`, `y_j ↦ P_j(x)`, is an étale ring
map, so the induced morphism of affine spaces is an étale morphism of schemes.

The proof is the graph presentation. Over `ℂ[y]`, the ring `ℂ[x]` is generated
by the coordinates `x_i` subject to exactly the relations `y_j - P_j(x)`:

* `KellerGroupoids.KellerEtale.ker_evalHom` is that statement, and it is the
  only real work here. Mathlib has the one-variable case
  (`Polynomial.ker_evalRingHom`) but no multivariate version.
* The Jacobian of that presentation is, up to sign, the Keller Jacobian, so
  `IsKeller` says exactly that the presentation is submersive of relative
  dimension zero, which is Mathlib's criterion for étale.

Nothing here is an axiom and nothing is a hole. The file is deliberately
separate from the rest of the tree so that later files can `import` it and use
`etale_comap` without carrying its proof.
-/

set_option autoImplicit false

open MvPolynomial

namespace KellerGroupoids
namespace KellerEtale

variable {n : ℕ}

/-- The coordinate ring `ℂ[x_1,…,x_n]` of affine `n`-space. Source and target of a polynomial
self-map have the same coordinate ring; which copy is meant is always clear from the map. -/
abbrev Coord (n : ℕ) : Type := MvPolynomial (Fin n) ℂ

/-- The comorphism of a polynomial self-map of `A^n`: the `ℂ`-algebra map `y_j ↦ P_j(x)` that
presents the source coordinate ring as an algebra over the target one. -/
noncomputable def comap (P : PolyMap n) : Coord n →+* Coord n :=
  (MvPolynomial.aeval P.coord : Coord n →ₐ[ℂ] Coord n).toRingHom

@[simp] lemma comap_C (P : PolyMap n) (c : ℂ) : comap P (MvPolynomial.C c) = MvPolynomial.C c := by
  simp [comap]

@[simp] lemma comap_X (P : PolyMap n) (j : Fin n) : comap P (MvPolynomial.X j) = P.coord j := by
  simp [comap]

/-- `ℂ[x] → ℂ[y][X]`: a polynomial in the source coordinates, read as a polynomial in the
presentation's variables with constant coefficients. -/
noncomputable def incl : Coord n →+* MvPolynomial (Fin n) (Coord n) :=
  MvPolynomial.map (MvPolynomial.C : ℂ →+* Coord n)

@[simp] lemma incl_C (c : ℂ) :
    (incl : Coord n →+* _) (MvPolynomial.C c) = MvPolynomial.C (MvPolynomial.C c) := by
  simp [incl]

@[simp] lemma incl_X (i : Fin n) :
    (incl : Coord n →+* _) (MvPolynomial.X i) = MvPolynomial.X i := by
  simp [incl]

/-- The presentation map `ℂ[y][X] → ℂ[x]`: the variables go to the source coordinates and the
coefficients along the comorphism. -/
noncomputable def evalHom (P : PolyMap n) : MvPolynomial (Fin n) (Coord n) →+* Coord n :=
  MvPolynomial.eval₂Hom (comap P) MvPolynomial.X

@[simp] lemma evalHom_C (P : PolyMap n) (a : Coord n) :
    evalHom P (MvPolynomial.C a) = comap P a := by simp [evalHom]

@[simp] lemma evalHom_X (P : PolyMap n) (i : Fin n) :
    evalHom P (MvPolynomial.X i) = MvPolynomial.X i := by simp [evalHom]

/-- Reading a source polynomial into the presentation and evaluating back is the identity. -/
@[simp] lemma evalHom_incl (P : PolyMap n) (p : Coord n) : evalHom P (incl p) = p := by
  have : (evalHom P).comp (incl : Coord n →+* _) = RingHom.id (Coord n) := by
    apply MvPolynomial.ringHom_ext <;> intro <;> simp
  exact DFunLike.congr_fun this p

theorem evalHom_surjective (P : PolyMap n) : Function.Surjective (evalHom P) :=
  Function.RightInverse.surjective (evalHom_incl P)

/-- The relation `y_j - P_j(x)` of the graph presentation. -/
noncomputable def rel (P : PolyMap n) (j : Fin n) : MvPolynomial (Fin n) (Coord n) :=
  MvPolynomial.C (MvPolynomial.X j) - incl (P.coord j)

@[simp] lemma evalHom_rel (P : PolyMap n) (j : Fin n) : evalHom P (rel P j) = 0 := by
  simp [rel]

/-- **The graph presentation.** `ℂ[x]` is `ℂ[y][X]` modulo exactly the relations `y_j - P_j(X)`.
Mathlib has the one-variable case only, so this is proved here: modulo the relations every
element of `ℂ[y][X]` is its own image read back through `incl`, because the two ring maps
`q ↦ q` and `q ↦ incl (evalHom q)` into the quotient agree on the variables and on the
coefficients. -/
theorem ker_evalHom (P : PolyMap n) :
    RingHom.ker (evalHom P) = Ideal.span (Set.range (rel P)) := by
  set I : Ideal (MvPolynomial (Fin n) (Coord n)) := Ideal.span (Set.range (rel P)) with hI
  have hrel : ∀ j, rel P j ∈ I := fun j => Ideal.subset_span ⟨j, rfl⟩
  -- Modulo `I`, a coefficient is its own image read back through `incl`.
  have hcoeff : (Ideal.Quotient.mk I).comp ((incl : Coord n →+* _).comp (comap P))
      = (Ideal.Quotient.mk I).comp (MvPolynomial.C : Coord n →+* _) := by
    apply MvPolynomial.ringHom_ext
    · intro c; simp
    · intro j
      have h0 : Ideal.Quotient.mk I (MvPolynomial.C (MvPolynomial.X j) - incl (P.coord j)) = 0 :=
        Ideal.Quotient.eq_zero_iff_mem.2 (hrel j)
      rw [map_sub, sub_eq_zero] at h0
      simpa using h0.symm
  -- Hence modulo `I` every element is its own image read back through `incl`.
  have hkey : (Ideal.Quotient.mk I).comp ((incl : Coord n →+* _).comp (evalHom P))
      = Ideal.Quotient.mk I := by
    apply MvPolynomial.ringHom_ext
    · intro a
      simpa using DFunLike.congr_fun hcoeff a
    · intro i; simp
  apply le_antisymm
  · intro p hp
    rw [RingHom.mem_ker] at hp
    have := DFunLike.congr_fun hkey p
    simp only [RingHom.coe_comp, Function.comp_apply, hp, map_zero] at this
    exact Ideal.Quotient.eq_zero_iff_mem.1 this.symm
  · rw [Ideal.span_le]
    rintro _ ⟨j, rfl⟩
    simp [RingHom.mem_ker]

/-! ### From a square graph presentation to étaleness

The presentation is assembled over an *opaque* base ring `R`. That is not
tidiness: the algebra structure a polynomial map induces on `ℂ[x]` over `ℂ[y]`
has the same two types on both sides, and if it were in scope as an instance
while the presentation's own polynomial ring `R[X]` was elaborated, `R[X]`
would pick it up as the structure on its *coefficients* instead of the
identity one. Keeping `R` a variable here, and passing the induced instance
explicitly at the one place it is used, keeps the two apart. -/

section Abstract

variable {R : Type*} [CommRing R] [Algebra R (Coord n)]

/-- The graph presentation map `R[X_1,…,X_n] → ℂ[x]`: the variables go to the coordinates and
the coefficients along the structure map. -/
noncomputable def presHom : MvPolynomial (Fin n) R →+* Coord n :=
  MvPolynomial.eval₂Hom (algebraMap R (Coord n)) MvPolynomial.X

lemma presHom_eq_aeval :
    ((MvPolynomial.aeval (R := R) (fun i => (MvPolynomial.X i : Coord n))) :
      MvPolynomial (Fin n) R →ₐ[R] Coord n).toRingHom = presHom (n := n) (R := R) := rfl

/-- **A square graph presentation with unit Jacobian is étale.** `ℂ[x]` is generated over `R` by
its `n` coordinates subject to `n` relations `v`, and the Jacobian of those relations is a unit,
so the presentation is submersive of relative dimension zero. -/
theorem etale_of_presentation (v : Fin n → MvPolynomial (Fin n) R)
    (hsurj : Function.Surjective (presHom (n := n) (R := R)))
    (hker : RingHom.ker (presHom (n := n) (R := R)) = Ideal.span (Set.range v))
    (hunit : IsUnit ((Matrix.of fun i j => MvPolynomial.pderiv i (v j)).det)) :
    Algebra.Etale R (Coord n) := by
  classical
  have hsurj' : Function.Surjective
      ⇑(MvPolynomial.aeval (R := R) (fun i => (MvPolynomial.X i : Coord n))) := hsurj
  let gens : Algebra.Generators R (Coord n) (Fin n) :=
    Algebra.Generators.ofSurjective (fun i => (MvPolynomial.X i : Coord n)) hsurj'
  have hkg : gens.ker = Ideal.span (Set.range v) := by
    rw [Algebra.Generators.ker_eq_ker_aeval_val]
    exact hker
  let pres : Algebra.Presentation R (Coord n) (Fin n) (Fin n) :=
    { toGenerators := gens
      relation := v
      span_range_relation_eq_ker := hkg.symm }
  let presub : Algebra.PreSubmersivePresentation R (Coord n) (Fin n) (Fin n) :=
    { toPresentation := pres
      map := id
      map_inj := Function.injective_id }
  have hjm : presub.jacobiMatrix = Matrix.of fun i j => MvPolynomial.pderiv i (v j) := by
    ext i j
    rw [Algebra.PreSubmersivePresentation.jacobiMatrix_apply]
    rfl
  let sub : Algebra.SubmersivePresentation R (Coord n) (Fin n) (Fin n) :=
    { toPreSubmersivePresentation := presub
      jacobian_isUnit := by
        rw [Algebra.PreSubmersivePresentation.jacobian_eq_jacobiMatrix_det, hjm]
        exact hunit.map _ }
  rw [Algebra.Etale.iff_isStandardSmoothOfRelativeDimension_zero]
  exact ⟨⟨Fin n, Fin n, inferInstance, inferInstance, sub,
    by simp [Algebra.Presentation.dimension]⟩⟩

end Abstract

/-! ### The Keller condition supplies the unit Jacobian -/

/-- The Jacobian matrix of the graph relations is minus the transpose of the Keller Jacobian,
read into the presentation ring. -/
theorem relMatrix_eq (P : PolyMap n) :
    (Matrix.of fun i j => MvPolynomial.pderiv i (rel P j))
      = -(Matrix.transpose ((incl : Coord n →+* _).mapMatrix P.jacobian)) := by
  ext i j
  simp [rel, incl, PolyMap.jacobian, MvPolynomial.pderiv_map, Matrix.transpose_apply]

/-- The Keller condition makes that determinant a unit. -/
theorem isUnit_relMatrix_det {P : PolyMap n} (h : P.IsKeller) :
    IsUnit ((Matrix.of fun i j => MvPolynomial.pderiv i (rel P j)).det) := by
  obtain ⟨c, hc0, hdet⟩ := h
  rw [relMatrix_eq, Matrix.det_neg, Matrix.det_transpose, ← RingHom.map_det, hdet]
  have hneg : IsUnit ((-1 : MvPolynomial (Fin n) (Coord n)) ^ Fintype.card (Fin n)) :=
    IsUnit.pow _ (IsUnit.of_mul_eq_one (-1) (by ring))
  refine IsUnit.mul hneg ?_
  refine IsUnit.of_mul_eq_one (incl (MvPolynomial.C c⁻¹ : Coord n)) ?_
  rw [← map_mul, ← map_mul, mul_inv_cancel₀ hc0]
  simp

/-- **Keller implies étale.** The comorphism `ℂ[y] → ℂ[x]`, `y_j ↦ P_j(x)`, of a Keller map is an
étale ring map, so the induced morphism of affine spaces is an étale morphism of schemes.

`ℂ[x]` is generated over `ℂ[y]` by the `n` coordinates subject to exactly the `n` relations
`y_j - P_j(x)` (`ker_evalHom`), and the Jacobian of those relations is, up to sign, the Keller
Jacobian, which the Keller condition makes a nonzero constant and hence a unit. -/
theorem etale_comap {P : PolyMap n} (h : P.IsKeller) : (comap P).Etale := by
  letI inst : Algebra (Coord n) (Coord n) := (comap P).toAlgebra
  have hpres : @presHom n (Coord n) _ inst = evalHom P := rfl
  exact @etale_of_presentation n (Coord n) _ inst (rel P)
    (hpres ▸ evalHom_surjective P) (hpres ▸ ker_evalHom P) (isUnit_relMatrix_det h)

end KellerEtale
end KellerGroupoids
