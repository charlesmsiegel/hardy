import KellerGroupoids.Interfaces

/-!
# The general theory: the set-level results, proved

Every theorem in this file is about the plain sets of points `KLevel`, `Conf`
and `Fiber` of `KellerGroupoids.Core`, and is proved from Mathlib with no
axioms beyond Lean's standard ones. Each is the exact combinatorial content
of a ledger item of `keller-groupoids`; the docstring names the item and says
how much of it the theorem is. Where an item has geometric content (a
scheme is smooth, a map is an open immersion, components are monodromy
orbits) that content is not here, and the ledger says so.
-/

set_option autoImplicit false

open Function MulAction Equiv

namespace KellerGroupoids
namespace ProjectStatements

variable {n : ℕ} (F : KellerMap n)

/-! ### KG-14: automorphisms are the collision-free case (set-theoretic half)

`Conf_2(F) = ∅ ↔ F injective`. The step `injective → automorphism` is
Ax–Grothendieck, a literature input, and is not part of this theorem. -/

theorem conf_two_isEmpty_iff_injective :
    IsEmpty (Conf F 2) ↔ Injective (fun x : AffinePoint n => F x) := by
  constructor
  · intro h x y hxy
    by_contra hne
    refine h.false ⟨![x, y], ?_, ?_⟩
    · intro i j hij
      fin_cases i <;> fin_cases j <;> simp_all [Ne.symm hne]
    · intro i j
      fin_cases i <;> fin_cases j <;> simp [hxy]
  · intro hinj
    refine ⟨fun ⟨x, hx, hF⟩ => ?_⟩
    exact hx 0 1 (by decide) (hinj (hF 0 1))

/-! ### KG-06: the image of `Conf_k` is the fiber-cardinality filtration `U_{≥k}`

A `k`-point configuration over `y` exists exactly when the fiber over `y`
has at least `k` points. This is the set-theoretic statement
`confImage_iff_fiberCard_ge` of the packet, now proved; the finiteness
hypothesis is what `Nat.card` needs to mean cardinality. -/

/-- The configurations over one target point. -/
def ConfOver (y : AffinePoint n) (k : ℕ) :=
  {x : Fin k → AffinePoint n // PairwiseDistinct x ∧ ∀ i, F (x i) = y}

/-- A configuration over `y` is the same thing as an injection of `Fin k` into the fiber. -/
def confOverEquivEmbedding (y : AffinePoint n) (k : ℕ) :
    ConfOver F y k ≃ (Fin k ↪ Fiber F y) where
  toFun x := ⟨fun i => ⟨x.1 i, x.2.2 i⟩, fun i j h =>
    (pairwiseDistinct_iff_injective x.1).1 x.2.1 (congrArg Subtype.val h)⟩
  invFun e := ⟨fun i => (e i).1,
    (pairwiseDistinct_iff_injective _).2 (fun i j h => e.injective (Subtype.ext h)),
    fun i => (e i).2⟩
  left_inv x := Subtype.ext (funext fun _ => rfl)
  right_inv e := by ext i; rfl

theorem confImage_iff_fiberCard_ge (y : AffinePoint n) (k : ℕ) [Finite (Fiber F y)] :
    (∃ x : Fin k → AffinePoint n, PairwiseDistinct x ∧ ∀ i, F (x i) = y) ↔
      k ≤ fiberCard F y := by
  constructor
  · rintro ⟨x, hx, hy⟩
    have e := confOverEquivEmbedding F y k ⟨x, hx, hy⟩
    have := Nat.card_le_card_of_injective e e.injective
    simpa [fiberCard] using this
  · intro hk
    have := Fintype.ofFinite (Fiber F y)
    have hcard : Fintype.card (Fin k) ≤ Fintype.card (Fiber F y) := by
      simpa [fiberCard, Nat.card_eq_fintype_card] using hk
    obtain ⟨e⟩ := Function.Embedding.nonempty_of_card_le hcard
    exact ⟨_, ((confOverEquivEmbedding F y k).symm e).2⟩

/-! ### KG-04: the generic degree bounds the configuration depth

If no fiber has more than `d` points then `Conf_k(F)` is empty for `k > d`.
The bound `#F⁻¹(y) ≤ d` itself is the geometric input (a finite map of
generic degree `d` has fibers of at most `d` points) and is a hypothesis here. -/

theorem conf_isEmpty_of_fiberCard_le (d k : ℕ) (hfin : ∀ y, Finite (Fiber F y))
    (hd : ∀ y, fiberCard F y ≤ d) (hk : d < k) : IsEmpty (Conf F k) := by
  refine ⟨fun ⟨x, hx, hF⟩ => ?_⟩
  have hk0 : 0 < k := by omega
  let i₀ : Fin k := ⟨0, hk0⟩
  have := hfin (F (x i₀))
  have := (confImage_iff_fiberCard_ge F (F (x i₀)) k).1 ⟨x, hx, fun i => hF i i₀⟩
  have := hd (F (x i₀))
  omega

/-! ### KG-11 (pointwise form): over an `m`-point fiber, `Conf_k` has `(m)_k` points

The factorial-moment identity `χ(Conf_k) = Σ_m (m)_k χ(U_m)` is Euler
integration of this count; the count itself is what is proved here. -/

theorem card_confOver (y : AffinePoint n) (k : ℕ) [Finite (Fiber F y)] :
    Nat.card (ConfOver F y k) = Nat.descFactorial (fiberCard F y) k := by
  have := Fintype.ofFinite (Fiber F y)
  rw [Nat.card_congr (confOverEquivEmbedding F y k), Nat.card_eq_fintype_card,
    Fintype.card_embedding_eq, Fintype.card_fin, fiberCard, Nat.card_eq_fintype_card]

/-! ### KG-16: the forgetful map `Conf_k → Conf_{k-1}` has fibers of `m - (k-1)` points

Given `k-1` distinct points of an `m`-point fiber, the ways to add a `k`-th
are the `m - (k-1)` remaining points of the fiber. -/

theorem card_extensions (y : AffinePoint n) (k : ℕ) [Finite (Fiber F y)]
    (x : Fin k ↪ Fiber F y) :
    Nat.card {z : Fiber F y // z ∉ Set.range x} = fiberCard F y - k := by
  have := Fintype.ofFinite (Fiber F y)
  classical
  rw [Nat.card_eq_fintype_card, Fintype.card_subtype_compl, Fintype.card_range, Fintype.card_fin,
    fiberCard, Nat.card_eq_fintype_card]

/-! ### KG-18: unordered configurations

`B_k = Conf_k / S_k` over `y` is the set of `k`-element subsets of the fiber,
of which there are `C(m, k)`; over a `d`-point fiber there is exactly one
`d`-element subset, which is the identification `B_d ≃ U_d`. -/

theorem card_unordered (y : AffinePoint n) (k : ℕ) [Finite (Fiber F y)] :
    Nat.card {S : Finset (Fiber F y) // S.card = k} = (fiberCard F y).choose k := by
  have := Fintype.ofFinite (Fiber F y)
  classical
  rw [Nat.card_eq_fintype_card, Fintype.card_finset_len, fiberCard, Nat.card_eq_fintype_card]

theorem card_unordered_top (y : AffinePoint n) [Finite (Fiber F y)] :
    Nat.card {S : Finset (Fiber F y) // S.card = fiberCard F y} = 1 := by
  rw [card_unordered, Nat.choose_self]

/-! ### KG-03: partition decomposition of the nerve

A point of `K_r(F)` is an `r`-tuple with one common image. Recording which
coordinates coincide gives an equivalence relation on `Fin r`, and the tuple
descends to an *injective* tuple indexed by its classes, still with one
common image. Conversely an injective class-indexed tuple lifts. So
`K_r(F) ≃ Σ_{s : Setoid (Fin r)} Conf_{Fin r / s}(F)`, the set-theoretic form of
`K_r = ⊔_π Conf_{|π|}`; counting setoids with `k` classes gives the Stirling
form `K_r ≅ ⊔_k S(r,k) Conf_k`. -/

/-- Configurations indexed by an arbitrary index type rather than `Fin k`. -/
def ConfIndexed (ι : Type) :=
  {y : ι → AffinePoint n // Injective y ∧ ∀ a b, F (y a) = F (y b)}

/-- The piece of `K_r(F)` on which the coincidence pattern of the coordinates is exactly `s`. -/
def KLevelPiece (r : ℕ) (s : Setoid (Fin r)) :=
  {x : Fin r → AffinePoint n // (∀ i j, x i = x j ↔ s i j) ∧ ∀ i j, F (x i) = F (x j)}

/-- `K_r(F)` is the disjoint union of its pieces, one per equivalence relation on `Fin r`. -/
def kLevelEquivSigmaPieces (r : ℕ) : KLevel F r ≃ Σ s : Setoid (Fin r), KLevelPiece F r s where
  toFun x := ⟨Setoid.ker x.1, ⟨x.1, fun _ _ => Iff.rfl, x.2⟩⟩
  invFun p := ⟨p.2.1, p.2.2.2⟩
  left_inv x := rfl
  right_inv p := by
    obtain ⟨s, x, hs, hF⟩ := p
    have hk : Setoid.ker x = s := Setoid.ext fun a b => hs a b
    subst hk
    rfl

/-- Each piece is the configuration space indexed by the classes of `s`: the tuple descends
to an injective tuple on `Fin r / s` and lifts back. -/
def pieceEquivConfIndexed (r : ℕ) (s : Setoid (Fin r)) :
    KLevelPiece F r s ≃ ConfIndexed F (Quotient s) where
  toFun x := ⟨Quotient.lift x.1 (fun a b h => (x.2.1 a b).2 h),
    fun a b h => by
      induction a using Quotient.inductionOn with | _ a => ?_
      induction b using Quotient.inductionOn with | _ b => ?_
      exact Quotient.sound ((x.2.1 a b).1 h),
    fun a b => by
      induction a using Quotient.inductionOn with | _ a => ?_
      induction b using Quotient.inductionOn with | _ b => ?_
      exact x.2.2 a b⟩
  invFun y := ⟨fun i => y.1 (Quotient.mk s i), fun a b => by
      constructor
      · intro h
        exact Quotient.exact (y.2.1 h)
      · intro h
        exact congrArg y.1 (Quotient.sound h),
    fun i j => y.2.2 _ _⟩
  left_inv x := Subtype.ext (funext fun _ => rfl)
  right_inv y := Subtype.ext (funext fun q => by
    induction q using Quotient.inductionOn with | _ a => ?_
    rfl)

/-- **KG-03, partition decomposition.** `K_r(F) ≃ Σ_s Conf_{Fin r / s}(F)`: an `r`-tuple with
one common image is the same as a partition of `{1..r}` together with a configuration of
pairwise distinct points indexed by its blocks. -/
def partitionDecomposition (r : ℕ) :
    KLevel F r ≃ Σ s : Setoid (Fin r), ConfIndexed F (Quotient s) :=
  (kLevelEquivSigmaPieces F r).trans (Equiv.sigmaCongrRight fun s => pieceEquivConfIndexed F r s)

/-- The partition decomposition as a proposition, so the audit can grade it. -/
theorem partitionDecomposition_exists (r : ℕ) :
    Nonempty (KLevel F r ≃ Σ s : Setoid (Fin r), ConfIndexed F (Quotient s)) :=
  ⟨partitionDecomposition F r⟩

/-! ### KG-14, the full statement conditional on the literature

With Ax–Grothendieck (`injective → automorphism`) and the trivial converse supplied as
hypotheses on the geometric record, `Conf_2(F) = ∅ ↔ F is an automorphism`. -/

theorem conf_two_isEmpty_iff_automorphism (G : Geometry F)
    (hAx : Injective (fun x : AffinePoint n => F x) → G.isAutomorphism)
    (hinj : G.isAutomorphism → Injective (fun x : AffinePoint n => F x)) :
    IsEmpty (Conf F 2) ↔ G.isAutomorphism := by
  rw [conf_two_isEmpty_iff_injective]
  exact ⟨hAx, hinj⟩

/-! ### KG-04 over the geometric record -/

theorem conf_isEmpty_of_degree_lt (G : Geometry F) (k : ℕ) (hk : G.degree < k) : IsEmpty (Conf F k) :=
  conf_isEmpty_of_fiberCard_le F G.degree k G.finiteFiber G.fiberCard_le hk

/-! ### KG-08 and KG-09: the monodromy description of configuration components (the group-set core)

Over the proper locus, `Conf_k` is the cover attached to the `G`-set of injective `k`-tuples of
sheets, so its connectedness is `k`-transitivity of `G`, and the top configuration `Conf_d` is
the cover attached to the free `G`-set of orderings of the `d` sheets, which has `d!/|G|`
orbits. Identifying connected components of a finite étale cover with orbits of its monodromy
is Riemann existence, a literature input; the statements about the `G`-sets are what is proved. -/

/-- The `G`-set of ordered `k`-tuples of distinct sheets is transitive exactly when `G` is
`k`-transitive (`Mathlib` defines `k`-transitivity this way). -/
theorem configurations_transitive_iff (d k : ℕ) (G : Subgroup (Perm (Fin d))) :
    IsPretransitive G (Fin k ↪ Fin d) ↔ IsMultiplyPretransitive G (Fin d) k :=
  Iff.rfl

/-- A free action of a finite group on a finite set has `|X| / |G|` orbits. -/
theorem card_orbits_mul_card_of_free (G X : Type*) [Group G] [MulAction G X] [Finite G] [Finite X]
    (h : ∀ x : X, stabilizer G x = ⊥) :
    Nat.card (orbitRel.Quotient G X) * Nat.card G = Nat.card X := by
  rw [Nat.card_congr (selfEquivOrbitsQuotientProd h), Nat.card_prod]

/-- The orderings of the `d` sheets form a free `G`-set for any `G ≤ S_d`, with `d! / |G|`
orbits: the number of connected components of the top configuration over the proper locus. -/
theorem card_top_orbits_mul_card (d : ℕ) (G : Subgroup (Perm (Fin d))) :
    Nat.card (orbitRel.Quotient G (Perm (Fin d))) * Nat.card G = d.factorial := by
  rw [card_orbits_mul_card_of_free G (Perm (Fin d)) fun σ => ?_, Nat.card_perm, Nat.card_eq_fintype_card,
    Fintype.card_fin]
  ext g
  simp only [mem_stabilizer_iff, Subgroup.mem_bot]
  constructor
  · intro hg
    have : (g : Perm (Fin d)) * σ = σ := hg
    exact Subtype.ext (mul_right_cancel (this.trans (one_mul σ).symm))
  · rintro rfl
    exact one_smul _ _

/-! ### KG-17 (the group-theoretic core): a regular action has `|G| = d` -/

theorem card_eq_of_regular (G X : Type*) [Group G] [MulAction G X] [Finite G] [Finite X] [Nonempty X]
    [IsPretransitive G X] (h : ∀ x : X, stabilizer G x = ⊥) : Nat.card G = Nat.card X := by
  have := card_orbits_mul_card_of_free G X h
  have hone : Nat.card (orbitRel.Quotient G X) = 1 := by
    rw [Nat.card_eq_one_iff_unique]
    refine ⟨⟨fun a b => ?_⟩, ⟨Quotient.mk'' (Classical.arbitrary X)⟩⟩
    induction a using Quotient.inductionOn' with | _ x => ?_
    induction b using Quotient.inductionOn' with | _ y => ?_
    exact Quotient.sound' (orbitRel_apply.mpr (mem_orbit_iff.mpr (exists_smul_eq G y x)))
  rw [hone, one_mul] at this
  exact this

end ProjectStatements
end KellerGroupoids
