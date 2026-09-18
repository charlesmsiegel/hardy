import KellerGroupoids.Core

/-!
# The geometric interface

The planar chain talks about objects Mathlib does not have for varieties over
`ℂ`: the finite normalisation of a plane Keller map, the irreducible
components of its nonproperness curve, generic inertia along a component,
étale-maximality, the monodromy group of the proper locus. Rather than
declaring these as global constants (which the packet did with the Lean 3
keyword `constant`, and which would hide assumptions from an axiom audit)
they are bundled here as *data a theorem takes*: a `PlaneGeometry F` is a
record of the geometric notions attached to a plane Keller map `F`, with no
axioms relating its fields to `F`. The sheets of the normalisation that lie
in the affine source are primitive (`sheetInSource`), so the generic fiber
count `m_C`, the deleted-sheet count `t_C` and the fiber deficit are
definitions rather than independent data. Two fields relate other fields to
each other and record what the fields mean rather than any fact about `F`:
`pencilOf_disjoint` (distinct members of one pencil share no point) and
`branchCount_pos` (every listed point of `S_F` lies on some component).
Each is named in the docstring of every theorem that uses it.

The incidence of components and points is primitive: `branchCount C p` is
the number of normalisation branches of the component `C` over the point
`p`. Meeting, connectedness, membership of a common pencil, unibranch and
multibranch singularities are then definitions below rather than fields,
so a theorem about them unfolds to the branch counts.

A theorem stated over `G : PlaneGeometry F` is therefore a faithful
transcription of the claim's *shape*: which hypotheses it takes and what it
concludes, in terms of the same named notions the prose uses. It is not a
theorem about varieties until the fields are given their geometric meaning
and the literature inputs relating them are supplied; `PublishedAxioms`
states those inputs over the same record, so an axiom audit shows exactly
which ones a proof rests on.
-/

set_option autoImplicit false

open Equiv

namespace KellerGroupoids

/-- The geometric notions attached to a plane Keller map, as the planar chain names them.
Nothing here is derived from `F`; every field is uninterpreted data. -/
structure PlaneGeometry (F : PlaneKellerMap) where
  /-- The generic degree `d` of `F`. -/
  degree : ℕ
  /-- The irreducible components `C` of the nonproperness curve `S_F`. -/
  Curve : Type
  [fintypeCurve : Fintype Curve]
  /-- The boundary prime divisors `E` of the finite (Zariski Main) normalisation of `F`. -/
  Boundary : Type
  [finiteBoundary : Finite Boundary]
  /-- The component `π(E)` a boundary divisor lies over. -/
  image : Boundary → Curve
  /-- The generic inertia permutation `σ_C` along a component, on the `d` sheets. -/
  inertia : Curve → Perm (Fin degree)
  /-- The normalisation sheets over a generic point of `C` that lie in the affine source `A²`.
  Its size is the generic affine fiber cardinality `m_C`; the unramified sheets outside it are
  the `t_C` deleted ones. -/
  sheetInSource : Curve → Finset (Fin degree)
  /-- Coherence of `sheetInSource` with `inertia`, forced by the Keller condition: `F` is étale
  on `A²`, because its Jacobian determinant is a nonzero constant, so a point where the finite
  normalisation ramifies cannot lie in the source, and no sheet the inertia moves lies in
  `sheetInSource`. This is what the fields mean, not a theorem about `F`. -/
  sheetInSource_disjoint_support : ∀ C, Disjoint (sheetInSource C) (inertia C).support
  /-- The finite points of `S_F` where normalisation branches are identified: every finite
  singular point of a component and every point where two components cross. -/
  Point : Type
  [fintypePoint : Fintype Point]
  /-- Where a listed point sits in the plane. -/
  location : Point → AffinePoint 2
  /-- The number of branches of the normalisation `C̃ ≅ A¹` of the component `C` lying over the
  point `p`; zero when `p` is not on `C`. This is the incidence data the Euler-characteristic
  count of `S_F` reads. -/
  branchCount : Curve → Point → ℕ
  /-- The local inertia of each of the two branches of `C` at a node, on the `d` sheets. Only the
  values at a point with two branches are used. -/
  branchInertia : Curve → Point → Fin 2 → Perm (Fin degree)
  /-- Coherence: every listed point lies on `S_F`, so some component has a branch over it. This
  is what the fields mean, not a theorem about `F`. -/
  branchCount_pos : ∀ p, ∃ C, 1 ≤ branchCount C p
  /-- The pencils an irreducible plane curve can belong to, a pencil being the affine
  equivalence class `{λh + μ}` of a reduced polynomial `h`; a curve `V(h - a)` lies in exactly
  one, `pencilOf`. -/
  Pencil : Type
  pencilOf : Curve → Pencil
  /-- Coherence of `pencilOf` with the branch counts: two distinct members `V(h - a)`,
  `V(h - a')` of one pencil are disjoint, so no point carries a branch of both. This is what the
  fields mean, not a theorem about `F`. -/
  pencilOf_disjoint : ∀ C C' p, pencilOf C = pencilOf C' → C ≠ C' →
    ¬ (1 ≤ branchCount C p ∧ 1 ≤ branchCount C' p)
  /-- The pencil is a rational one-place pencil: `h` has one place at infinity and the members in
  question are rational (Assi's setting; a coordinate pencil is one). -/
  rationalOnePlace : Pencil → Prop
  /-- The pencil's polynomial is equivalent to a coordinate. -/
  isCoordinate : Pencil → Prop
  /-- The number of affine nodes of a component. -/
  nodeCount : Curve → ℕ
  /-- `E` is generically unramified over the target. -/
  genericallyUnramified : Boundary → Prop
  /-- `π` is étale at every point of `E`. -/
  everywhereEtale : Boundary → Prop
  /-- `E ≅ A¹`. -/
  boundaryIsAffineLine : Boundary → Prop
  /-- `E → π(E)` is the normalisation morphism. -/
  mapsAsNormalization : Boundary → Prop
  /-- The normalisation of `C` is `A¹`. -/
  normalizationIsAffineLine : Curve → Prop
  /-- `C` has exactly one place at infinity. -/
  onePlaceAtInfinity : Curve → Prop
  /-- `C` is the image of a polynomial parametrisation of `A¹`. -/
  polynomiallyParametric : Curve → Prop
  /-- Every finite singularity of `C` is an ordinary node. -/
  isNodal : Curve → Prop
  /-- `C` is rational. -/
  isRational : Curve → Prop
  /-- The boundary `D = X̄ ∖ U` of the finite normalisation is pure divisorial (no isolated points). -/
  boundaryPureDivisorial : Prop
  /-- `F` is étale-maximal: the source exhausts the étale locus of its finite normalisation. -/
  etaleMaximal : Prop
  /-- The Euler characteristic `χ(S_F)`. -/
  eulerCharNonproper : ℤ
  /-- `S_F = C₀ ⊔ C₁` is the two exceptional rational nodal members of a noncoordinate one-place
  pencil, with the same positive number of nodes. -/
  exactlyTwoNodalFibers : Prop
  /-- The sheet-monodromy group over the proper locus, as a subgroup of `S_d`. -/
  monodromy : Subgroup (Perm (Fin degree))
  /-- Coherence: a generic inertia permutation is by construction an element of the
  sheet-monodromy group. This is what the fields mean, not a theorem about `F`. -/
  inertia_mem_monodromy : ∀ C, inertia C ∈ monodromy
  /-- `F` is a counterexample to the Jacobian conjecture (a Keller map that is not an automorphism). -/
  isCounterexample : Prop

attribute [instance] PlaneGeometry.fintypeCurve PlaneGeometry.fintypePoint
  PlaneGeometry.finiteBoundary

namespace PlaneGeometry

variable {F : PlaneKellerMap} (G : PlaneGeometry F)

/-- The generic affine fiber cardinality `m_C` along a component. -/
def affineFiberCard (C : G.Curve) : ℕ := (G.sheetInSource C).card

/-- The number `t_C` of generically unramified normalisation sheets omitted from the source: the
sheets the inertia fixes that do not lie in the affine source. -/
def deletedSheets (C : G.Curve) : ℕ := ((G.inertia C).supportᶜ \ G.sheetInSource C).card

/-- The fiber deficit `d - m_C` along a component. -/
def deficit (C : G.Curve) : ℕ := G.degree - G.affineFiberCard C

/-- The total number `r_p` of normalisation branches of `S_F` over a point. -/
def totalBranches (p : G.Point) : ℕ := ∑ C, G.branchCount C p

/-- Two components meet in the affine plane: some listed point carries a branch of each. -/
def meets (C C' : G.Curve) : Prop := ∃ p, 1 ≤ G.branchCount C p ∧ 1 ≤ G.branchCount C' p

/-- Two components are disjoint in the affine plane. -/
def disjoint (C C' : G.Curve) : Prop := ¬ G.meets C C'

/-- The finite singular points of a component: the listed points over which its normalisation
has more than one branch. -/
def SingularPoint (C : G.Curve) : Type := {p : G.Point // 2 ≤ G.branchCount C p}

/-- The number of branches of `C` at one of its singular points. -/
def branches {C : G.Curve} (p : G.SingularPoint C) : ℕ := G.branchCount C p.1

/-- Every finite singularity of `C` is unibranch: no listed point carries two branches of `C`. -/
def unibranchEverywhere (C : G.Curve) : Prop := ∀ p, G.branchCount C p ≤ 1

/-- `C` has a multibranch finite singularity: some listed point carries two branches of `C`,
a self-identification of its normalisation. -/
def hasMultibranchSingularity (C : G.Curve) : Prop := ∃ p, 2 ≤ G.branchCount C p

/-- Two components lie in the same connected component of `S_F`. The irreducible components are
closed and connected and finitely many, so the connected components of `S_F` are the classes of
the equivalence relation generated by meeting. -/
def sameComponent : G.Curve → G.Curve → Prop := Relation.EqvGen G.meets

/-- The nonproperness curve `S_F` is connected: all its irreducible components lie in one
connected component. -/
def connected : Prop := ∀ C C', G.sameComponent C C'

/-- Two components are fibers `V(h - a)`, `V(h - a')` of one polynomial `h`. -/
def samePencil (C C' : G.Curve) : Prop := G.pencilOf C = G.pencilOf C'

/-- All components of `S_F` are fibers `V(h - aᵢ)` of one polynomial `h` with a rational one-place
pencil, and every connected component of `S_F` is irreducible. -/
def commonPencil : Prop :=
  (∃ p, G.rationalOnePlace p ∧ ∀ C, G.pencilOf C = p) ∧ ∀ C C', G.sameComponent C C' → C = C'

/-- `h` is equivalent to a coordinate: the pencil of a component of `S_F` is a coordinate pencil
(in the common-pencil case there is one such pencil). -/
def pencilIsCoordinate : Prop := ∃ C, G.isCoordinate (G.pencilOf C)

/-- The number `q` of selected fibers of the pencil: in the common-pencil case every component
of `S_F` is one fiber `V(h - aᵢ)`. -/
noncomputable def selectedFibers : ℕ := Nat.card G.Curve

/-- The forced degree-six `A₆` boundary packet `(3,1),(1,1),(1,1)` with zero excess: of the two
components of `S_F`, one is ramified, with generic inertia of support three (a single 3-cycle in
the natural six-point action) and no deleted sheet, and the other is unramified with two deleted
sheets. -/
def a6BoundaryPacket : Prop :=
  ∃ C₀ C₁ : G.Curve, C₀ ≠ C₁ ∧ (∀ C, C = C₀ ∨ C = C₁) ∧
    permSupportSize (G.inertia C₀) = 3 ∧ G.deletedSheets C₀ = 0 ∧
    G.inertia C₁ = 1 ∧ G.deletedSheets C₁ = 2

/-- At every node of a ramified component the two branch inertia permutations are disjoint
3-cycles, so the local inertia group is `C₃ × C₃`. -/
def a6NodeInertia : Prop :=
  ∀ (C : G.Curve) (p : G.Point), G.inertia C ≠ 1 → 2 ≤ G.branchCount C p →
    permSupportSize (G.branchInertia C p 0) = 3 ∧ permSupportSize (G.branchInertia C p 1) = 3 ∧
      Perm.Disjoint (G.branchInertia C p 0) (G.branchInertia C p 1)

/-- Two distinct members of one pencil are disjoint, from the coherence field
`pencilOf_disjoint`. -/
theorem disjoint_of_pencilOf_eq {C C' : G.Curve} (h : G.pencilOf C = G.pencilOf C')
    (hne : C ≠ C') : G.disjoint C C' :=
  fun ⟨p, hp⟩ => G.pencilOf_disjoint C C' p h hne hp

/-- Every listed point lies on `S_F`, so the total branch count over it is positive. -/
theorem one_le_totalBranches (p : G.Point) : 1 ≤ G.totalBranches p := by
  obtain ⟨C, hC⟩ := G.branchCount_pos p
  exact hC.trans (Finset.single_le_sum (f := fun C => G.branchCount C p)
    (fun _ _ => Nat.zero_le _) (Finset.mem_univ C))

end PlaneGeometry

/-- Geometric notions attached to a Keller map in any dimension, used by the general theory
where it goes beyond sets of points. -/
structure Geometry {n : ℕ} (F : KellerMap n) where
  /-- The generic degree `d`. -/
  degree : ℕ
  /-- Every fiber has at most `d` points. -/
  fiberCard_le : ∀ y, fiberCard F y ≤ degree
  /-- Every fiber is finite. -/
  finiteFiber : ∀ y, Finite (Fiber F y)
  /-- The monodromy group of the finite étale cover over the proper locus, on the `d` sheets. -/
  monodromy : Subgroup (Perm (Fin degree))
  /-- `F` is an automorphism of affine space. -/
  isAutomorphism : Prop
  /-- The function-field extension of `F` is Galois. -/
  extensionIsGalois : Prop

end KellerGroupoids
