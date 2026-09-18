import Mathlib.Combinatorics.SimpleGraph.Acyclic
import Mathlib.Combinatorics.SimpleGraph.Finite
import Mathlib.Logic.Relation

/-!
# Branch counting on a connected curve

The combinatorial core of `KG-EM07`. A curve with irreducible components
`α` and listed singular points `β` is described by the number `b C p` of
normalisation branches of the component `C` over the point `p`. Its
Euler characteristic, when every normalisation is `A¹`, is
`|α| - Σ_p (r_p - 1)` with `r_p = Σ_C b C p`. The theorem here is the
counting fact the audit uses: if the curve is connected (the components
are linked through the points) and some component has two branches
through one point, then `|α| + |β| ≤ Σ_p r_p`, so the Euler
characteristic is at most zero.

The proof passes through the incidence graph on `α ⊕ β`: connectedness
gives it at least `|α| + |β| - 1` edges (Mathlib's spanning-tree bound),
and the edges inject into the pairs `(C, p)` with `b C p ≥ 1`, of which the
double branch shows there are at most `Σ b - 1`.
-/

set_option autoImplicit false

namespace KellerGroupoids
namespace ProjectStatements

open Finset

section BranchCount

variable {α β : Type} [Fintype α] [Fintype β] (b : α → β → ℕ)

/-- The incidence graph: a component is adjacent to each listed point it passes through. -/
def incidenceGraph : SimpleGraph (α ⊕ β) :=
  SimpleGraph.fromRel fun x y => ∃ C p, x = Sum.inl C ∧ y = Sum.inr p ∧ 1 ≤ b C p

theorem incidenceGraph_adj_inl_inr (C : α) (p : β) :
    (incidenceGraph b).Adj (Sum.inl C) (Sum.inr p) ↔ 1 ≤ b C p := by
  simp [incidenceGraph, SimpleGraph.fromRel_adj]

/-- Components linked through the points are linked in the incidence graph. -/
theorem reachable_of_eqvGen {C C' : α}
    (h : Relation.EqvGen (fun C C' : α => ∃ p, 1 ≤ b C p ∧ 1 ≤ b C' p) C C') :
    (incidenceGraph b).Reachable (Sum.inl C) (Sum.inl C') := by
  induction h with
  | rel x y hxy =>
    obtain ⟨p, h1, h2⟩ := hxy
    exact ((incidenceGraph_adj_inl_inr b x p).mpr h1).reachable.trans
      ((incidenceGraph_adj_inl_inr b y p).mpr h2).reachable.symm
  | refl x => exact SimpleGraph.Reachable.refl _
  | symm x y _ ih => exact ih.symm
  | trans x y z _ _ ih₁ ih₂ => exact ih₁.trans ih₂

/-- A listed point lying on the curve lies on some component. -/
theorem exists_one_le_of_one_le_sum (p : β) (hp : 1 ≤ ∑ C, b C p) : ∃ C, 1 ≤ b C p := by
  by_contra hne
  push_neg at hne
  have : ∑ C, b C p = 0 := Finset.sum_eq_zero fun C _ => by have := hne C; omega
  omega

/-- If the curve is connected and every listed point lies on it, the incidence graph is
connected. -/
theorem incidenceGraph_connected [Nonempty α] (hpt : ∀ p, 1 ≤ ∑ C, b C p)
    (hconn : ∀ C C', Relation.EqvGen (fun C C' : α => ∃ p, 1 ≤ b C p ∧ 1 ≤ b C' p) C C') :
    (incidenceGraph b).Connected := by
  obtain ⟨C₀⟩ := ‹Nonempty α›
  rw [SimpleGraph.connected_iff_exists_forall_reachable]
  refine ⟨Sum.inl C₀, fun w => ?_⟩
  rcases w with C | p
  · exact reachable_of_eqvGen b (hconn C₀ C)
  · obtain ⟨C, hC⟩ := exists_one_le_of_one_le_sum b p (hpt p)
    exact (reachable_of_eqvGen b (hconn C₀ C)).trans
      ((incidenceGraph_adj_inl_inr b C p).mpr hC).reachable

/-- Every edge of the incidence graph is a component–point pair with a branch. -/
theorem exists_pair_of_mem_edgeSet (e : Sym2 (α ⊕ β)) (he : e ∈ (incidenceGraph b).edgeSet) :
    ∃ cp : α × β, 1 ≤ b cp.1 cp.2 ∧ e = s(Sum.inl cp.1, Sum.inr cp.2) := by
  induction e using Sym2.ind with
  | h x y =>
    rw [SimpleGraph.mem_edgeSet, incidenceGraph, SimpleGraph.fromRel_adj] at he
    obtain ⟨-, ⟨C, p, rfl, rfl, hb⟩ | ⟨C, p, rfl, rfl, hb⟩⟩ := he
    · exact ⟨(C, p), hb, rfl⟩
    · exact ⟨(C, p), hb, Sym2.eq_swap⟩

/-- The edges inject into the pairs with a branch. -/
theorem card_edgeSet_le_card_pairs :
    Nat.card (incidenceGraph b).edgeSet ≤ Nat.card {cp : α × β // 1 ≤ b cp.1 cp.2} := by
  classical
  let f : (incidenceGraph b).edgeSet → {cp : α × β // 1 ≤ b cp.1 cp.2} := fun e =>
    ⟨Classical.choose (exists_pair_of_mem_edgeSet b e.1 e.2),
     (Classical.choose_spec (exists_pair_of_mem_edgeSet b e.1 e.2)).1⟩
  refine Nat.card_le_card_of_injective f fun e e' hee' => ?_
  have h := congrArg Subtype.val hee'
  apply Subtype.ext
  rw [(Classical.choose_spec (exists_pair_of_mem_edgeSet b e.1 e.2)).2,
    (Classical.choose_spec (exists_pair_of_mem_edgeSet b e'.1 e'.2)).2]
  simp only [f] at h
  rw [h]

/-- With a double branch somewhere, the pairs with a branch number at most `Σ b - 1`. -/
theorem card_pairs_add_one_le (hself : ∃ C p, 2 ≤ b C p) :
    Nat.card {cp : α × β // 1 ≤ b cp.1 cp.2} + 1 ≤ ∑ C, ∑ p, b C p := by
  classical
  have hprod : ∑ cp : α × β, b cp.1 cp.2 = ∑ C, ∑ p, b C p := Fintype.sum_prod_type _
  rw [Nat.card_eq_fintype_card, Fintype.card_subtype, Finset.card_filter, ← hprod]
  -- Split each branch count into the one branch its pair accounts for and the excess; the
  -- double branch makes one excess positive, so the pair count falls short of the total.
  have hsplit : ∀ cp : α × β, b cp.1 cp.2 =
      (b cp.1 cp.2 - if 1 ≤ b cp.1 cp.2 then 1 else 0) + (if 1 ≤ b cp.1 cp.2 then 1 else 0) := by
    intro cp
    split_ifs with h <;> omega
  rw [Finset.sum_congr rfl fun cp _ => hsplit cp, Finset.sum_add_distrib]
  have h1 : 1 ≤ ∑ cp : α × β, (b cp.1 cp.2 - if 1 ≤ b cp.1 cp.2 then 1 else 0) := by
    obtain ⟨C, p, h2⟩ := hself
    have hone : 1 ≤ b C p - if 1 ≤ b C p then 1 else 0 := by split_ifs <;> omega
    exact hone.trans (Finset.single_le_sum
      (f := fun cp : α × β => b cp.1 cp.2 - if 1 ≤ b cp.1 cp.2 then 1 else 0)
      (fun _ _ => Nat.zero_le _) (Finset.mem_univ (C, p)))
  omega

/-- **Branch count of a connected curve with a double branch.** -/
theorem card_add_card_le_sum_branches (hpt : ∀ p, 1 ≤ ∑ C, b C p)
    (hconn : ∀ C C', Relation.EqvGen (fun C C' : α => ∃ p, 1 ≤ b C p ∧ 1 ≤ b C' p) C C')
    (hself : ∃ C p, 2 ≤ b C p) :
    Fintype.card α + Fintype.card β ≤ ∑ C, ∑ p, b C p := by
  have : Nonempty α := let ⟨C, _, _⟩ := hself; ⟨C⟩
  have hedges := (incidenceGraph_connected b hpt hconn).card_vert_le_card_edgeSet_add_one
  have hinj := card_edgeSet_le_card_pairs b
  have hpairs := card_pairs_add_one_le b hself
  rw [Nat.card_sum, Nat.card_eq_fintype_card, Nat.card_eq_fintype_card] at hedges
  omega

/-- **The Euler characteristic bound.** With every normalisation `A¹`, `χ = |α| - Σ_p (r_p - 1)`;
under connectedness and a double branch it is at most zero. -/
theorem euler_le_zero (hpt : ∀ p, 1 ≤ ∑ C, b C p)
    (hconn : ∀ C C', Relation.EqvGen (fun C C' : α => ∃ p, 1 ≤ b C p ∧ 1 ≤ b C' p) C C')
    (hself : ∃ C p, 2 ≤ b C p) :
    (Fintype.card α : ℤ) - ∑ p, (((∑ C, b C p : ℕ) : ℤ) - 1) ≤ 0 := by
  have hmain := card_add_card_le_sum_branches b hpt hconn hself
  have hcomm : ∑ C, ∑ p, b C p = ∑ p, ∑ C, b C p := Finset.sum_comm
  have hcast : ((∑ p, ∑ C, b C p : ℕ) : ℤ) = ∑ p, ((∑ C, b C p : ℕ) : ℤ) := by push_cast; rfl
  rw [Finset.sum_sub_distrib, Finset.sum_const, Finset.card_univ, nsmul_eq_mul, mul_one]
  omega

end BranchCount

end ProjectStatements
end KellerGroupoids
