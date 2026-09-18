import KellerGroupoids.ExternalResearchAxioms
import KellerPlanar.BranchCount
import KellerPlanar.Permutations
import KellerPlanar.EulerDeficit

/-!
# The planar chain, stated over the geometric interface

Each theorem here is one ledger item of `keller-groupoids-rank-two`, stated
over a `PlaneGeometry F` record in the exact shape the prose gives it:
the same hypotheses, the same conclusion, in terms of the same named
notions. A theorem proved here is proved *from those hypotheses and from
the literature axioms it names*; one that ends in `sorry` is a faithful
statement whose proof is still owed, and Hardy's audit reports it as an
open hole. Nothing here is a theorem about varieties until the record's
fields are given their geometric meaning.

The chain's structure is real: `nonmaximal_implies_exactlyTwoNodalFibers`
(`KG-EM10`) is derived from `KG-EM07`, `KG-EM08`, `KG-EM09` and Assi's
theorem exactly as the audit derives it, so its axiom report lists every
hole and every literature input the derivation rests on.
-/

set_option autoImplicit false

open Equiv Finset

namespace KellerGroupoids
namespace ProjectStatements

variable {F : PlaneKellerMap}

/-! ### Configuration criteria -/

/-- **KG-PC01.** Fiber deficit decomposes into inertia support plus deletion:
`d - m_C = |supp σ_C| + t_C`. Proved by counting the `d` sheets over a generic point of `C`:
the inertia moves `|supp σ_C|` of them, and the rest split into the `m_C` sheets that lie in
the affine source and the `t_C` unramified sheets deleted from it.

That no sheet the inertia moves lies in the affine source is the record's coherence field
`sheetInSource_disjoint_support`, which is the Keller condition doing its work: `F` is étale on
`A²` because its Jacobian determinant is a nonzero constant, so a point where the finite
normalisation ramifies cannot lie in the source. -/
theorem fiberDeficit_eq_inertiaSupport_add_deleted (G : PlaneGeometry F) (C : G.Curve) :
    G.deficit C = permSupportSize (G.inertia C) + G.deletedSheets C := by
  classical
  have hsub : G.sheetInSource C ⊆ ((G.inertia C).support)ᶜ := fun x hx =>
    Finset.mem_compl.2 (Finset.disjoint_left.1 (G.sheetInSource_disjoint_support C) hx)
  have hcompl : (((G.inertia C).support)ᶜ).card = G.degree - (G.inertia C).support.card := by
    rw [Finset.card_compl, Fintype.card_fin]
  have hsupp_le : (G.inertia C).support.card ≤ G.degree := by
    simpa using Finset.card_le_univ (G.inertia C).support
  have hm_le : (G.sheetInSource C).card ≤ (((G.inertia C).support)ᶜ).card :=
    Finset.card_le_card hsub
  have ht : ((((G.inertia C).support)ᶜ) \ G.sheetInSource C).card
      = (((G.inertia C).support)ᶜ).card - (G.sheetInSource C).card := by
    rw [Finset.card_sdiff, Finset.inter_eq_left.2 hsub]
  simp only [PlaneGeometry.deficit, PlaneGeometry.deletedSheets, PlaneGeometry.affineFiberCard,
    permSupportSize]
  omega

/-- **KG-PC02.** Deficit one detects one deleted unramified sheet. Proved from the deficit
decomposition (`KG-PC01`, now itself proved) and the fact that no permutation moves exactly
one point. -/
theorem deficitOne_forces_oneDeletedUnramifiedSheet (G : PlaneGeometry F) (C : G.Curve)
    (h : G.deficit C = 1) :
    permSupportSize (G.inertia C) = 0 ∧ G.deletedSheets C = 1 :=
  deficitOne_forces_deletion (G.inertia C) (G.deletedSheets C)
    ((fiberDeficit_eq_inertiaSupport_add_deleted G C).symm.trans h)

/-- **KG-PC03.** An étale-maximal map has no `(d-1)`-point fiber (so `Conf_d ≃ Conf_{d-1}`). -/
theorem etaleMaximal_topConfigurationStabilizes (G : PlaneGeometry F) (h : G.etaleMaximal)
    (y : AffinePoint 2) : fiberCard F y ≠ G.degree - 1 := by
  sorry

/-- **KG-PC08.** A `(d-1)`-point fiber witnesses non-maximality: the contrapositive of `KG-PC03`. -/
theorem topConfigurationFailure_detects_nonmaximal (G : PlaneGeometry F)
    (h : ∃ y, fiberCard F y = G.degree - 1) : ¬ G.etaleMaximal := by
  intro hmax
  obtain ⟨y, hy⟩ := h
  exact etaleMaximal_topConfigurationStabilizes G hmax y hy

/-! ### The étale-maximality reduction -/

/-- **KG-EM01.** The boundary of the finite normalisation is pure divisorial. -/
theorem normalizationBoundary_pureDivisorial (G : PlaneGeometry F) : G.boundaryPureDivisorial := by
  sorry

/-- **KG-EM02.** A generically unramified boundary divisor is étale everywhere, is `A¹`, and maps
as the normalisation of its image component. -/
theorem genericallyUnramifiedBoundary_everywhereEtale (G : PlaneGeometry F) (E : G.Boundary)
    (h : G.genericallyUnramified E) :
    G.everywhereEtale E ∧ G.boundaryIsAffineLine E ∧ G.mapsAsNormalization E ∧
      G.normalizationIsAffineLine (G.image E) := by
  sorry

/-- **KG-EM03.** A deleted unramified sheet lies over a component with a multibranch singularity. -/
theorem deletedUnramifiedSheet_forces_multibranch (G : PlaneGeometry F) (E : G.Boundary)
    (h : G.genericallyUnramified E) : G.hasMultibranchSingularity (G.image E) := by
  sorry

/-- **KG-EM04.** If every component is unibranch at every finite singularity, `F` is
étale-maximal. Proved as the audit derives it from `KG-EM03`, which is the hypothesis `hmb`
here: a non-maximal map omits an unramified normalisation sheet, and that sheet lies over a
component with a multibranch singularity, which unibranchness excludes. -/
theorem allComponentsUnibranch_implies_etaleMaximal (G : PlaneGeometry F)
    (hmb : ¬ G.etaleMaximal → ∃ C, G.hasMultibranchSingularity C)
    (h : ∀ C, G.unibranchEverywhere C) : G.etaleMaximal := by
  by_contra hmax
  obtain ⟨C, p, hp⟩ := hmb hmax
  have := h C p
  omega

/-- **KG-EM05.** Over a singular point with `r` branches under an omitted unramified sheet, the
affine fiber has at most `d - r` points. -/
theorem conductorPointFiberBound (G : PlaneGeometry F) (E : G.Boundary)
    (h : G.genericallyUnramified E) (p : G.SingularPoint (G.image E)) :
    fiberCard F (G.location p.1) ≤ G.degree - G.branches p := by
  sorry

/-- **KG-EM07.** Connected nonproperness implies étale-maximality. Proved as the audit argues
it, by contradiction from Jelonek's positivity. The argument's two prerequisites are explicit
hypotheses, in the shape the audit uses them:

* `hχ`, the Euler characteristic of `S_F` read off its normalisation. Every component has
  normalisation `A¹` (`KG-P12`), so additivity of the topological Euler characteristic over the
  normalisation map, which is bijective away from the listed points, gives
  `χ(S_F) = c - Σ_p (r_p - 1)`.
* `hmb`, the multibranch consequence of non-maximality (`KG-EM02` with `KG-EM03`): a
  non-maximal map omits an unramified normalisation sheet, which lies over a component with a
  multibranch self-identification.

From those, connectedness of `S_F` forces at least `c - 1` identifications and the
self-identification forces one more, so `χ(S_F) ≤ 0` (`euler_le_zero`), against Jelonek's
`χ(S_F) > 0`. -/
theorem connectedNonproper_implies_etaleMaximal (G : PlaneGeometry F) (h : G.connected)
    (hχ : G.eulerCharNonproper =
      (Fintype.card G.Curve : ℤ) - ∑ p, ((G.totalBranches p : ℤ) - 1))
    (hmb : ¬ G.etaleMaximal → ∃ C, G.hasMultibranchSingularity C) :
    G.etaleMaximal := by
  by_contra hmax
  obtain ⟨C, p, hp⟩ := hmb hmax
  have hle : (Fintype.card G.Curve : ℤ) - ∑ p, ((G.totalBranches p : ℤ) - 1) ≤ 0 :=
    euler_le_zero G.branchCount (fun p => G.one_le_totalBranches p) h ⟨C, p, hp⟩
  have hpos := Published.jelonekConnectedNonproperEulerPositive G h
  linarith [hχ ▸ hpos]

/-- The contrapositive of `KG-EM07`: non-maximality forces `S_F` disconnected. -/
theorem nonmaximal_implies_nonproperDisconnected (G : PlaneGeometry F)
    (hχ : G.eulerCharNonproper =
      (Fintype.card G.Curve : ℤ) - ∑ p, ((G.totalBranches p : ℤ) - 1))
    (hmb : ¬ G.etaleMaximal → ∃ C, G.hasMultibranchSingularity C) (h : ¬ G.etaleMaximal) :
    ¬ G.connected :=
  fun hc => h (connectedNonproper_implies_etaleMaximal G hc hχ hmb)

/-- Components in different connected components of `S_F` are disjoint: meeting generates the
connected components. -/
theorem disjoint_of_not_sameComponent (G : PlaneGeometry F) {C C' : G.Curve}
    (h : ¬ G.sameComponent C C') : G.disjoint C C' :=
  fun hm => h (Relation.EqvGen.rel _ _ hm)

/-- A disconnected `S_F` has two components in different connected components. -/
theorem exists_not_sameComponent_of_not_connected (G : PlaneGeometry F) (h : ¬ G.connected) :
    ∃ C C' : G.Curve, ¬ G.sameComponent C C' := by
  simpa only [PlaneGeometry.connected, not_forall] using h

/-- **KG-EM08.** A disconnected nonproperness curve lies in one rational one-place pencil, with
every connected component irreducible. Proved as the audit argues it: two components in
different connected components are disjoint and polynomially parametric (Jelonek), so Assi's
trichotomy puts them in one rational one-place pencil; fixing two such components puts every
component in that pencil; and distinct members of one pencil are disjoint (the record's
coherence field `pencilOf_disjoint`), so the equivalence relation generated by meeting is
equality and every connected component is irreducible. Chau's one-place input, which the prose
also cites, is not needed in the typed form of Assi's trichotomy. -/
theorem disconnectedNonproper_commonPencil (G : PlaneGeometry F) (h : ¬ G.connected) :
    G.commonPencil := by
  obtain ⟨C₀, C₁, h01⟩ := exists_not_sameComponent_of_not_connected G h
  have assi : ∀ D D' : G.Curve, G.disjoint D D' →
      G.pencilOf D = G.pencilOf D' ∧ G.rationalOnePlace (G.pencilOf D) := fun D D' hd =>
    Published.assiDisjointParametricCurvesTrichotomy G D D' hd
      (Published.jelonekNonproperComponentsParametric G D)
      (Published.jelonekNonproperComponentsParametric G D')
  obtain ⟨h01p, h0r⟩ := assi C₀ C₁ (disjoint_of_not_sameComponent G h01)
  -- Every component lies in the pencil of `C₀`.
  have hall : ∀ D, G.pencilOf D = G.pencilOf C₀ := by
    intro D
    by_cases hD : G.sameComponent D C₀
    · have hD1 : ¬ G.sameComponent D C₁ := fun h' =>
        h01 (Relation.EqvGen.trans _ _ _ (Relation.EqvGen.symm _ _ hD) h')
      exact (assi D C₁ (disjoint_of_not_sameComponent G hD1)).1.trans h01p.symm
    · exact (assi D C₀ (disjoint_of_not_sameComponent G hD)).1
  refine ⟨⟨G.pencilOf C₀, h0r, hall⟩, ?_⟩
  -- Distinct components are distinct members of one pencil, hence disjoint; so meeting is
  -- equality and every connected component is a single irreducible component.
  have hmeet : ∀ D D', G.meets D D' → D = D' := fun D D' hm =>
    by_contra fun hne => G.disjoint_of_pencilOf_eq ((hall D).trans (hall D').symm) hne hm
  intro D D' hs
  change Relation.EqvGen G.meets D D' at hs
  induction hs with
  | rel x y hxy => exact hmeet x y hxy
  | refl x => rfl
  | symm x y _ ih => exact ih.symm
  | trans x y z _ _ ih₁ ih₂ => exact ih₁.trans ih₂

/-- A disconnected `S_F` selects at least two fibers: its components in different connected
components are distinct. -/
theorem two_le_selectedFibers_of_not_connected (G : PlaneGeometry F) (h : ¬ G.connected) :
    2 ≤ G.selectedFibers := by
  obtain ⟨C₀, C₁, h01⟩ := exists_not_sameComponent_of_not_connected G h
  have hne : C₀ ≠ C₁ := fun heq => h01 (heq ▸ Relation.EqvGen.refl _)
  have : Nontrivial G.Curve := ⟨⟨C₀, C₁, hne⟩⟩
  exact Finite.one_lt_card_iff_nontrivial.mpr this

/-- **KG-EM09.** In the common-pencil case of a counterexample (the case `KG-EM08` reaches from a
disconnected `S_F`), exactly two fibers are selected and the pencil is not a coordinate. Proved
as the audit argues it: Assi's dichotomy gives a coordinate pencil or at most two selected
fibers; Braun–Dias–Venato-Santos excludes the coordinate case for a counterexample; and a
disconnected `S_F` selects at least two fibers (`two_le_selectedFibers_of_not_connected`). -/
theorem commonPencil_exactlyTwoFibers (G : PlaneGeometry F) (h : G.commonPencil)
    (hdis : ¬ G.connected) (hce : G.isCounterexample) :
    G.selectedFibers = 2 ∧ ¬ G.pencilIsCoordinate := by
  have hnc : ¬ G.pencilIsCoordinate := fun hc =>
    Published.counterexampleMeetsEverySubmersionFiber G hce h hc
  refine ⟨?_, hnc⟩
  have hle : G.selectedFibers ≤ 2 :=
    (Published.assiRationalOnePlacePencilDichotomy G h).resolve_left hnc
  have hge := two_le_selectedFibers_of_not_connected G hdis
  omega

/-- **KG-EM10, the two-nodal-fiber theorem.** A non-étale-maximal plane Keller counterexample has
`S_F = C₀ ⊔ C₁`, the two exceptional rational nodal members of a noncoordinate one-place pencil.
Derived from `KG-EM07`, `KG-EM08`, `KG-EM09` and Assi's equinodal theorem, carrying `KG-EM07`'s
two hypotheses (the Euler count and the multibranch consequence of non-maximality). -/
theorem nonmaximal_implies_exactlyTwoNodalFibers (G : PlaneGeometry F) (h : ¬ G.etaleMaximal)
    (hχ : G.eulerCharNonproper =
      (Fintype.card G.Curve : ℤ) - ∑ p, ((G.totalBranches p : ℤ) - 1))
    (hmb : ¬ G.etaleMaximal → ∃ C, G.hasMultibranchSingularity C)
    (hce : G.isCounterexample) : G.exactlyTwoNodalFibers := by
  have hdis := nonmaximal_implies_nonproperDisconnected G hχ hmb h
  have hpencil := disconnectedNonproper_commonPencil G hdis
  obtain ⟨h2, hnc⟩ := commonPencil_exactlyTwoFibers G hpencil hdis hce
  exact Published.assiExceptionalTwoFibersEquinodal G hpencil h2 hnc

/-- **KG-P12.** Each component has normalisation `A¹` and one place at infinity: Jelonek's
parametrisation with Chau's theorem. -/
theorem onePlace_irreducibleComponents (G : PlaneGeometry F) (C : G.Curve) :
    G.normalizationIsAffineLine C ∧ G.onePlaceAtInfinity C := by
  have hpar := Published.jelonekNonproperComponentsParametric G C
  obtain ⟨h1, h2⟩ := Published.chauOnePointAtInfinity G C hpar
  exact ⟨h2, h1⟩

/-! ### Perfect monodromy and degree six -/

/-- **KG-PM01.** In the two-nodal-fiber case the sheet-monodromy group is perfect. Proved from
the audit's own two load-bearing points, which are the hypothesis `hvc` here: the nodal
vanishing cycles generate the sheet monodromy, and the monodromy around each of them is
`σ₊σ₋⁻¹` for the two conjugate local branch inertias at that node. Such a ratio is a
commutator, so a generating set of them lies in the commutator subgroup and the group is
perfect (`perfect_of_conjugateRatio_generators`). -/
theorem nonmaximal_implies_perfectMonodromy (G : PlaneGeometry F) (h : G.exactlyTwoNodalFibers)
    (hvc : ∃ S : Set ↥G.monodromy, Subgroup.closure S = ⊤ ∧
      ∀ s ∈ S, ∃ a b : ↥G.monodromy, IsConj a b ∧ s = a * b⁻¹) :
    IsPerfectGroup G.monodromy := by
  obtain ⟨S, hgen, hS⟩ := hvc
  exact perfect_of_conjugateRatio_generators S hgen hS

/-- **KG-PM02.** A perfect monodromy group is not the full symmetric group; in particular `S₆`
is excluded in the residual non-maximal degree-six case. Proved. -/
theorem nonmaximal_not_S6 (G : PlaneGeometry F) (hperf : IsPerfectGroup G.monodromy)
    (hd : 2 ≤ G.degree) : G.monodromy ≠ ⊤ :=
  perfect_ne_symmetric hd G.monodromy hperf

/-- **KG-PM03.** Perfect monodromy forces every inertia permutation to have even `b + q`
(equivalently `b - q`), with `b` its total deficit and `q` its number of cycles. Proved. -/
theorem perfectMonodromy_inertiaEvenParity (G : PlaneGeometry F) (hperf : IsPerfectGroup G.monodromy)
    (C : G.Curve) (hC : G.inertia C ∈ G.monodromy) :
    Even ((G.inertia C).cycleType.sum + (G.inertia C).cycleType.card) :=
  deficit_add_rows_even_of_perfect G.monodromy hperf _ hC

/-- **KG-A600.** With the external six-sheet frontier, perfect monodromy in degree six is `A₆`.
Proved from the external input and `KG-PM02`. -/
theorem degreeSix_nonmaximal_frontier_forces_A6 (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hce : G.isCounterexample) (hperf : IsPerfectGroup G.monodromy) :
    G.monodromy = alternatingGroup (Fin G.degree) := by
  rcases ExternalResearch.degreeSixMonodromyFrontier G hdeg hce with h | h
  · exact h
  · exact absurd h (nonmaximal_not_S6 G hperf (by omega))

/-- **KG-A601.** In the residual degree-six `A₆` case the boundary packet is `(3,1),(1,1),(1,1)`.
Proved as the audit argues it. The external refined budget gives the two deficits `{3, 2}`; no
element of `A₆` has support one or two, so the deficit-two component is unramified with two
deleted sheets, and the deficit-three component has support zero or three; support zero on both
would leave the normalisation unramified, which `hram` excludes, so the deficit-three component
carries a single 3-cycle and no deleted sheet.

Two prerequisites are explicit hypotheses: `htwo`, that `S_F` is exactly two components
(`KG-EM10`), and `hram`, that the finite normalisation ramifies somewhere, which holds for a
counterexample because an everywhere-étale cover of the simply connected plane would be
trivial. The deficit decomposition (`KG-PC01`) and the membership of each inertia in the
monodromy group are taken from the proof and the record, not assumed here. -/
theorem degreeSix_nonmaximal_A6_forces_boundaryPacket (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hce : G.isCounterexample) (hnm : ¬ G.etaleMaximal)
    (hA6 : G.monodromy = alternatingGroup (Fin G.degree))
    (htwo : ∃ C₀ C₁ : G.Curve, C₀ ≠ C₁ ∧ ∀ C, C = C₀ ∨ C = C₁)
    (hram : ∃ C, G.inertia C ≠ 1) :
    G.a6BoundaryPacket := by
  obtain ⟨C₀, C₁, hne, hall⟩ := htwo
  obtain ⟨hsum, hb0, hb1⟩ :=
    ExternalResearch.refinedDegreeSixBoundaryBudget G hdeg hce hnm C₀ C₁ hne
  have hne1 : ∀ C, permSupportSize (G.inertia C) ≠ 1 := fun C => permSupportSize_ne_one _
  have hne2 : ∀ C, permSupportSize (G.inertia C) ≠ 2 := fun C =>
    alternating_support_ne_two _ (hA6 ▸ G.inertia_mem_monodromy C)
  have hdec : ∀ C, G.deficit C = permSupportSize (G.inertia C) + G.deletedSheets C :=
    fun C => fiberDeficit_eq_inertiaSupport_add_deleted G C
  -- Deficit two: support zero, so trivial inertia and two deleted sheets.
  have key2 : ∀ C, G.deficit C = 2 → G.inertia C = 1 ∧ G.deletedSheets C = 2 := by
    intro C h2
    have h := hdec C
    have h1 := hne1 C
    have h2' := hne2 C
    have hz : permSupportSize (G.inertia C) = 0 := by omega
    exact ⟨(permSupportSize_eq_zero_iff _).1 hz, by omega⟩
  -- Deficit three: support zero with three deleted sheets, or a single 3-cycle with none.
  have key3 : ∀ C, G.deficit C = 3 →
      (G.inertia C = 1 ∧ G.deletedSheets C = 3) ∨
        (permSupportSize (G.inertia C) = 3 ∧ G.deletedSheets C = 0) := by
    intro C h3
    have h := hdec C
    have h1 := hne1 C
    have h2' := hne2 C
    rcases Nat.lt_or_ge (permSupportSize (G.inertia C)) 3 with hlt | hge
    · have hz : permSupportSize (G.inertia C) = 0 := by omega
      exact Or.inl ⟨(permSupportSize_eq_zero_iff _).1 hz, by omega⟩
    · exact Or.inr (by omega)
  rcases deficits_three_two _ _ hsum hb0 hb1 with ⟨h0, h1⟩ | ⟨h0, h1⟩
  · obtain ⟨hi1, ht1⟩ := key2 C₁ h1
    rcases key3 C₀ h0 with ⟨hi0, -⟩ | ⟨hs0, ht0⟩
    · obtain ⟨C, hC⟩ := hram
      rcases hall C with h | h
      · rw [h] at hC; exact absurd hi0 hC
      · rw [h] at hC; exact absurd hi1 hC
    · exact ⟨C₀, C₁, hne, hall, hs0, ht0, hi1, ht1⟩
  · obtain ⟨hi0, ht0⟩ := key2 C₀ h0
    rcases key3 C₁ h1 with ⟨hi1, -⟩ | ⟨hs1, ht1⟩
    · obtain ⟨C, hC⟩ := hram
      rcases hall C with h | h
      · rw [h] at hC; exact absurd hi0 hC
      · rw [h] at hC; exact absurd hi1 hC
    · exact ⟨C₁, C₀, hne.symm, fun C => (hall C).symm, hs1, ht1, hi0, ht0⟩

/-- The deficit arithmetic inside `KG-A601`: from the refined budget the two fibers have deficits
`{3, 2}`. Proved from the external budget input. -/
theorem degreeSix_deficits_three_two (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hce : G.isCounterexample) (hnm : ¬ G.etaleMaximal) (C₀ C₁ : G.Curve) (hne : C₀ ≠ C₁) :
    (G.deficit C₀ = 3 ∧ G.deficit C₁ = 2) ∨ (G.deficit C₀ = 2 ∧ G.deficit C₁ = 3) := by
  obtain ⟨hsum, h₀, h₁⟩ := ExternalResearch.refinedDegreeSixBoundaryBudget G hdeg hce hnm C₀ C₁ hne
  exact deficits_three_two _ _ hsum h₀ h₁

/-- **KG-A603.** At every node of the ramified fiber the branch inertia permutations are disjoint
3-cycles. Proved as the audit argues it: the ramified component is the packet's `C₀`, whose
generic inertia has support three, and each branch meridian at a node is conjugate to it, so
each branch inertia also moves three of the six sheets; the node's two branch supports leave no
sheet in the affine fiber, so together they cover all six, and three plus three with nothing to
spare forces them disjoint.

Two prerequisites are explicit hypotheses: `hconj`, that the branch meridians at a node are
conjugate to the component's generic meridian; and `hcover`, that the node's two branch supports
exhaust the sheets, which is the zero-excess local census `KG-A602` (no affine preimage over a
node of the ramified fiber). -/
theorem degreeSix_A6_nodeInertia (G : PlaneGeometry F) (hdeg : G.degree = 6)
    (hpacket : G.a6BoundaryPacket)
    (hconj : ∀ (C : G.Curve) (p : G.Point), 2 ≤ G.branchCount C p → ∀ i,
      IsConj (G.branchInertia C p i) (G.inertia C))
    (hcover : ∀ (C : G.Curve) (p : G.Point), G.inertia C ≠ 1 → 2 ≤ G.branchCount C p →
      (Finset.univ : Finset (Fin G.degree)) ⊆
        (G.branchInertia C p 0).support ∪ (G.branchInertia C p 1).support) :
    G.a6NodeInertia := by
  obtain ⟨C₀, C₁, -, hall, hs0, -, hi1, -⟩ := hpacket
  intro C p hram hnode
  have hsupp : permSupportSize (G.inertia C) = 3 := by
    rcases hall C with h | h
    · rw [h]; exact hs0
    · rw [h] at hram; exact absurd hi1 hram
  have h0 : permSupportSize (G.branchInertia C p 0) = 3 := by
    rw [permSupportSize_of_isConj (hconj C p hnode 0), hsupp]
  have h1 : permSupportSize (G.branchInertia C p 1) = 3 := by
    rw [permSupportSize_of_isConj (hconj C p hnode 1), hsupp]
  exact ⟨h0, h1, perm_disjoint_of_cover _ _ (hcover C p hram hnode) (by omega)⟩

/-! ### The global Euler-deficit identity -/

/-- **KG-E02.** `d - 1 = Σ_{m<d} (d - m) χ(U_m)`, from Euler integration over the source and
additivity over the target, supplied as hypotheses. Proved. -/
theorem globalEulerDeficitIdentity (G : PlaneGeometry F) (χ : ℕ → ℤ)
    (hsource : ∑ m ∈ range (G.degree + 1), (m : ℤ) * χ m = 1)
    (htarget : ∑ m ∈ range (G.degree + 1), χ m = 1) :
    (G.degree : ℤ) - 1 = ∑ m ∈ range G.degree, ((G.degree : ℤ) - m) * χ m :=
  euler_deficit_identity G.degree χ hsource htarget

end ProjectStatements
end KellerGroupoids
