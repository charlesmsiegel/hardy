import Mathlib.GroupTheory.Commutator.Basic
import KellerGroupoids.Core

/-!
# Permutation-group and arithmetic cores of the planar chain, proved

The perfect-monodromy and degree-six arguments of `keller-groupoids-rank-two`
rest on a few facts about permutation groups and on small arithmetic. Those
facts are proved here from Mathlib, with no axioms. Each docstring names the
ledger item whose core it is; none of these theorems is the geometric claim
itself, and the ledger records them as `supports` edges, not as proofs of the
claims.
-/

set_option autoImplicit false

open Equiv Finset
open scoped commutatorElement

namespace KellerGroupoids
namespace ProjectStatements

/-- A group is perfect when it equals its own commutator subgroup. -/
def IsPerfectGroup (G : Type*) [Group G] : Prop := commutator G = ⊤

/-! ### KG-PM02: `S_n` is not perfect for `n ≥ 2`, so `S_6` is excluded by perfectness -/

theorem symmetricGroup_not_perfect (n : ℕ) (hn : 2 ≤ n) : ¬ IsPerfectGroup (Perm (Fin n)) := by
  intro h
  have hnt : Nontrivial (Fin n) := Fin.nontrivial_iff_two_le.2 hn
  have hle : commutator (Perm (Fin n)) ≤ (Perm.sign : Perm (Fin n) →* ℤˣ).ker :=
    Abelianization.commutator_subset_ker _
  rw [IsPerfectGroup] at h
  rw [h, top_le_iff, MonoidHom.ker_eq_top_iff] at hle
  obtain ⟨σ, hσ⟩ := Perm.sign_surjective (Fin n) (-1 : ℤˣ)
  have := DFunLike.congr_fun hle σ
  simp only [MonoidHom.one_apply] at this
  rw [this] at hσ
  exact absurd hσ (by decide)

/-- The sixth symmetric group is not perfect: the form the degree-six exclusion uses. -/
theorem s6_notPerfect : ¬ IsPerfectGroup (Perm (Fin 6)) :=
  symmetricGroup_not_perfect 6 (by norm_num)

/-- A perfect subgroup of a symmetric group consists of even permutations. -/
theorem sign_eq_one_of_perfect {d : ℕ} (G : Subgroup (Perm (Fin d))) (hG : IsPerfectGroup G) :
    ∀ g ∈ G, Perm.sign g = 1 := by
  intro g hg
  let φ : G →* ℤˣ := Perm.sign.comp G.subtype
  have hle : commutator G ≤ φ.ker := Abelianization.commutator_subset_ker φ
  rw [IsPerfectGroup] at hG
  rw [hG, top_le_iff, MonoidHom.ker_eq_top_iff] at hle
  have := DFunLike.congr_fun hle ⟨g, hg⟩
  simpa [φ] using this

/-- If the monodromy group is perfect it cannot be the whole symmetric group on `d ≥ 2` letters. -/
theorem perfect_ne_symmetric {d : ℕ} (hd : 2 ≤ d) (G : Subgroup (Perm (Fin d)))
    (hG : IsPerfectGroup G) : G ≠ ⊤ := by
  intro htop
  have hnt : Nontrivial (Fin d) := Fin.nontrivial_iff_two_le.2 hd
  obtain ⟨σ, hσ⟩ := Perm.sign_surjective (Fin d) (-1 : ℤˣ)
  have := sign_eq_one_of_perfect G hG σ (htop ▸ Subgroup.mem_top σ)
  rw [this] at hσ
  exact absurd hσ (by decide)

/-! ### KG-PM03: parity of inertia support

`sign σ = (-1)^(Σ_j (e_j - 1))` where the `e_j` are the cycle lengths; with
`b = Σ e_j` the total deficit and `q` the number of cycles, `b - q` is the
exponent, so a perfect monodromy group forces `b - q` even. -/

/-- `sign σ = (-1)^(b + q)` with `b = Σ_j e_j` the total deficit and `q` the number of cycles;
`b + q ≡ b - q (mod 2)`, so this is the packet's `(-1)^(Σ(e_j - 1))`. -/
theorem sign_eq_neg_one_pow_deficit_add_rows {d : ℕ} (σ : Perm (Fin d)) :
    Perm.sign σ = (-1) ^ (σ.cycleType.sum + σ.cycleType.card) :=
  Perm.sign_of_cycleType σ

/-- In a perfect monodromy group every inertia permutation has `b + q` (equivalently `b - q`) even. -/
theorem deficit_add_rows_even_of_perfect {d : ℕ} (G : Subgroup (Perm (Fin d)))
    (hG : IsPerfectGroup G) (σ : Perm (Fin d)) (hσ : σ ∈ G) :
    Even (σ.cycleType.sum + σ.cycleType.card) := by
  have h1 := sign_eq_one_of_perfect G hG σ hσ
  rw [sign_eq_neg_one_pow_deficit_add_rows] at h1
  by_contra hodd
  rw [Nat.not_even_iff_odd] at hodd
  rw [hodd.neg_one_pow] at h1
  exact absurd h1 (by decide)

/-! ### KG-PC02: deficit one forces one deleted unramified sheet

A permutation never moves exactly one point (`Equiv.Perm.card_support_ne_one`),
so a fiber deficit `d - m = supp σ + t` equal to `1` forces `supp σ = 0` and
`t = 1`. This is the combinatorial content; that the deficit decomposes as
`supp σ + t` is the geometric input `KG-PC01`. -/

theorem permSupportSize_ne_one {d : ℕ} (σ : Perm (Fin d)) : permSupportSize σ ≠ 1 :=
  Perm.card_support_ne_one σ

theorem deficitOne_forces_deletion {d : ℕ} (σ : Perm (Fin d)) (t : ℕ)
    (h : permSupportSize σ + t = 1) : permSupportSize σ = 0 ∧ t = 1 := by
  have := permSupportSize_ne_one σ
  omega

/-! ### KG-A601: the forced degree-six boundary packet, arithmetic and group parts

Two fibers with generic deficits `b₀ + b₁ = 5` and `2 bᵢ ≤ 6` have deficits
`{3, 2}`; and no nontrivial element of `A_6` (or of any alternating group)
has support of size two, since a permutation moving exactly two points is a
transposition, which is odd. -/

theorem deficits_three_two (b₀ b₁ : ℕ) (hsum : b₀ + b₁ = 5) (h₀ : 2 * b₀ ≤ 6) (h₁ : 2 * b₁ ≤ 6) :
    (b₀ = 3 ∧ b₁ = 2) ∨ (b₀ = 2 ∧ b₁ = 3) := by
  omega

theorem alternating_support_ne_two {d : ℕ} (σ : Perm (Fin d)) (hσ : σ ∈ alternatingGroup (Fin d)) :
    permSupportSize σ ≠ 2 := by
  intro h2
  have hswap : σ.IsSwap := Perm.card_support_eq_two.1 h2
  obtain ⟨x, y, hxy, rfl⟩ := hswap
  rw [Perm.mem_alternatingGroup, Perm.sign_swap hxy] at hσ
  exact absurd hσ (by decide)

/-! ### KG-PM01: perfectness from conjugate-ratio generators

The vanishing-cycle monodromy around a node is `σ₊σ₋⁻¹` with `σ₊`, `σ₋` the
two conjugate branch inertias. Such a ratio is literally a commutator: if
`b = c a c⁻¹` then `a b⁻¹ = a c a⁻¹ c⁻¹ = ⁅a, c⁆`. A group generated by
elements of that form therefore has its whole generating set inside the
commutator subgroup, so it is perfect. -/

/-- A ratio of conjugate elements is a commutator. -/
theorem conjugateRatio_eq_commutatorElement {H : Type*} [Group H] {a b c : H}
    (hc : c * a * c⁻¹ = b) : a * b⁻¹ = ⁅a, c⁆ := by
  rw [← hc, commutatorElement_def]
  group

/-- **KG-PM01 core.** A group generated by ratios of conjugate elements is perfect. -/
theorem perfect_of_conjugateRatio_generators {H : Type*} [Group H] (S : Set H)
    (hgen : Subgroup.closure S = ⊤)
    (hS : ∀ s ∈ S, ∃ a b : H, IsConj a b ∧ s = a * b⁻¹) :
    IsPerfectGroup H := by
  rw [IsPerfectGroup, eq_top_iff, ← hgen, Subgroup.closure_le]
  intro s hs
  obtain ⟨a, b, hconj, rfl⟩ := hS s hs
  obtain ⟨c, hc⟩ := isConj_iff.1 hconj
  rw [conjugateRatio_eq_commutatorElement hc]
  exact Subgroup.commutator_mem_commutator (Subgroup.mem_top a) (Subgroup.mem_top c)

/-! ### KG-A603: supports that exhaust the sheets are disjoint

At a node of the ramified fiber the two branch inertias each move three of the
six sheets and between them leave no sheet in the affine fiber, so their
supports cover all six. Three plus three with nothing to spare forces the two
supports to be disjoint. -/

/-- **KG-A603 core.** Two permutations whose supports cover every letter, and whose support
sizes together do not exceed the number of letters, have disjoint supports. -/
theorem disjoint_support_of_cover {d : ℕ} (σ τ : Perm (Fin d))
    (hcover : (Finset.univ : Finset (Fin d)) ⊆ σ.support ∪ τ.support)
    (hcard : permSupportSize σ + permSupportSize τ ≤ d) :
    Disjoint σ.support τ.support := by
  have hu : σ.support ∪ τ.support = Finset.univ := Finset.univ_subset_iff.1 hcover
  have hui := Finset.card_union_add_card_inter σ.support τ.support
  rw [hu, Finset.card_univ, Fintype.card_fin] at hui
  rw [Finset.disjoint_iff_inter_eq_empty, ← Finset.card_eq_zero]
  unfold permSupportSize at hcard
  omega

/-- The same conclusion as `Equiv.Perm.Disjoint`, the form the inertia statement uses. -/
theorem perm_disjoint_of_cover {d : ℕ} (σ τ : Perm (Fin d))
    (hcover : (Finset.univ : Finset (Fin d)) ⊆ σ.support ∪ τ.support)
    (hcard : permSupportSize σ + permSupportSize τ ≤ d) : Perm.Disjoint σ τ :=
  Perm.disjoint_iff_disjoint_support.2 (disjoint_support_of_cover σ τ hcover hcard)

/-- Trivial support means the trivial permutation: the `supp σ = 0` reading of a boundary row. -/
theorem permSupportSize_eq_zero_iff {d : ℕ} (σ : Perm (Fin d)) :
    permSupportSize σ = 0 ↔ σ = 1 := by
  unfold permSupportSize
  rw [Finset.card_eq_zero, Perm.support_eq_empty_iff]

/-- Conjugate permutations move the same number of letters: the branch meridians at a node are
conjugate to the component's generic meridian, so they have its support size. -/
theorem permSupportSize_of_isConj {d : ℕ} {σ τ : Perm (Fin d)} (h : IsConj σ τ) :
    permSupportSize σ = permSupportSize τ := by
  obtain ⟨c, hc⟩ := isConj_iff.1 h
  unfold permSupportSize
  rw [← hc, Perm.card_support_conj]

end ProjectStatements
end KellerGroupoids
