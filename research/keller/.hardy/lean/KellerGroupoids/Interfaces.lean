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
axioms relating its fields to `F`. The one field that relates fields to
each other, `disjoint_of_pencilOf_eq`, records what the fields mean (two
distinct members of one pencil are disjoint) and is named in the docstring
of every theorem that uses it. Connectedness of `S_F` and membership of a
common pencil are not fields but definitions below, in terms of which
components meet and which pencil each belongs to, so a theorem about them
unfolds to those notions.

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
  [finiteCurve : Finite Curve]
  /-- The boundary prime divisors `E` of the finite (Zariski Main) normalisation of `F`. -/
  Boundary : Type
  [finiteBoundary : Finite Boundary]
  /-- The component `π(E)` a boundary divisor lies over. -/
  image : Boundary → Curve
  /-- The generic inertia permutation `σ_C` along a component, on the `d` sheets. -/
  inertia : Curve → Perm (Fin degree)
  /-- The generic affine fiber cardinality `m_C` along a component. -/
  affineFiberCard : Curve → ℕ
  /-- The number `t_C` of generically unramified normalisation sheets omitted from the source. -/
  deletedSheets : Curve → ℕ
  /-- The finite singular points of a component, where they sit, and their branch counts. -/
  SingularPoint : Curve → Type
  location : (C : Curve) → SingularPoint C → AffinePoint 2
  branches : (C : Curve) → SingularPoint C → ℕ
  /-- Two components are disjoint in the affine plane. -/
  disjoint : Curve → Curve → Prop
  /-- The pencils an irreducible plane curve can belong to, a pencil being the affine
  equivalence class `{λh + μ}` of a reduced polynomial `h`; a curve `V(h - a)` lies in exactly
  one, `pencilOf`. -/
  Pencil : Type
  pencilOf : Curve → Pencil
  /-- Coherence of `disjoint` with `pencilOf`: two distinct members `V(h - a)`, `V(h - a')` of one
  pencil are disjoint. This is what the fields mean, not a theorem about `F`. -/
  disjoint_of_pencilOf_eq : ∀ C C', pencilOf C = pencilOf C' → C ≠ C' → disjoint C C'
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
  /-- Every finite singularity of `C` is unibranch. -/
  unibranchEverywhere : Curve → Prop
  /-- `C` has a multibranch finite singularity. -/
  hasMultibranchSingularity : Curve → Prop
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
  /-- `F` is a counterexample to the Jacobian conjecture (a Keller map that is not an automorphism). -/
  isCounterexample : Prop
  /-- The forced degree-six `A₆` boundary packet `(3,1),(1,1),(1,1)` with zero excess. -/
  a6BoundaryPacket : Prop
  /-- At every node of the ramified fiber the two branch inertia permutations are disjoint 3-cycles. -/
  a6NodeInertia : Prop

attribute [instance] PlaneGeometry.finiteCurve PlaneGeometry.finiteBoundary

namespace PlaneGeometry

variable {F : PlaneKellerMap} (G : PlaneGeometry F)

/-- The fiber deficit `d - m_C` along a component. -/
def deficit (C : G.Curve) : ℕ := G.degree - G.affineFiberCard C

/-- Two components meet in the affine plane. -/
def meets (C C' : G.Curve) : Prop := ¬ G.disjoint C C'

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
