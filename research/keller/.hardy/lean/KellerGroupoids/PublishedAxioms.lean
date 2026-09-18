import KellerGroupoids.Interfaces

/-!
# Literature inputs, as axioms over the geometric interface

Each axiom here is a published theorem the planar chain uses, stated over a
`PlaneGeometry` record in the form the chain consumes it. They are `axiom`s
on purpose: Hardy's axiom audit then names exactly which of them a proof
rests on, and none is an approved assumption until a session admits it.

Only inputs whose statement can be written over the record are here. The
purity theorem, Zariski's Main Theorem, miracle flatness, Ramanujam–Morrow
and the Orevkov/Neumann–Norbury inputs need notions the record does not
carry (normal surfaces, boundary graphs, braid monodromy); the ledger lists
them as literature inputs with no Lean declaration.
-/

set_option autoImplicit false

namespace KellerGroupoids
namespace Published

variable {F : PlaneKellerMap}

/-- **Jelonek 1999, Theorem 4.12.** Every irreducible component of the nonproperness set of a
plane polynomial map is polynomially parametric. -/
axiom jelonekNonproperComponentsParametric (G : PlaneGeometry F) (C : G.Curve) :
    G.polynomiallyParametric C

/-- **Jelonek 1999, Theorem 4.6.** Horizontal boundary components of the normal affine extension
are copies of `A¹`. -/
axiom jelonekHorizontalBoundaryA1Disjoint (G : PlaneGeometry F) (E : G.Boundary) :
    G.boundaryIsAffineLine E

/-- **Nguyen Van Chau 2004.** A polynomially parametric component of the nonproperness curve of a
plane Keller map has one place at infinity, with normalisation `A¹`. -/
axiom chauOnePointAtInfinity (G : PlaneGeometry F) (C : G.Curve)
    (h : G.polynomiallyParametric C) : G.onePlaceAtInfinity C ∧ G.normalizationIsAffineLine C

/-- **Jelonek 2022.** If the nonproperness set is connected, its Euler characteristic is positive. -/
axiom jelonekConnectedNonproperEulerPositive (G : PlaneGeometry F) (h : G.connected) :
    0 < G.eulerCharNonproper

/-- **Assi 2012, Theorem 3.1.** For a rational one-place pencil, either the polynomial is
equivalent to a coordinate or the pencil has at most two rational members; the selected fibers,
being polynomially parametric, are rational members, so there are at most two of them. -/
axiom assiRationalOnePlacePencilDichotomy (G : PlaneGeometry F) (h : G.commonPencil) :
    G.pencilIsCoordinate ∨ G.selectedFibers ≤ 2

/-- **Assi 2012, Proposition 3.5.** Two distinct polynomially parametric plane curves are
translates in a coordinate pencil, or the two exceptional rational members of a noncoordinate
one-place pencil, or they meet in the affine plane. So two disjoint ones lie in one pencil, and
that pencil is a rational one-place pencil. -/
axiom assiDisjointParametricCurvesTrichotomy (G : PlaneGeometry F) (C C' : G.Curve)
    (hdisjoint : G.disjoint C C') (hC : G.polynomiallyParametric C)
    (hC' : G.polynomiallyParametric C') :
    G.samePencil C C' ∧ G.rationalOnePlace (G.pencilOf C)

/-- **Assi 2012, Proposition 3.3 with Lemma 3.2.** The two exceptional rational members of a
noncoordinate one-place pencil have the same positive number of singular points, each an
ordinary node. -/
axiom assiExceptionalTwoFibersEquinodal (G : PlaneGeometry F) (h : G.commonPencil)
    (h2 : G.selectedFibers = 2) (hnc : ¬ G.pencilIsCoordinate) : G.exactlyTwoNodalFibers

/-- **Braun–Dias–Venato-Santos 2018.** The nonproperness set of a Jacobian counterexample meets
every fiber of a polynomial submersion; so it cannot be a finite union of coordinate fibers. -/
axiom counterexampleMeetsEverySubmersionFiber (G : PlaneGeometry F) (hce : G.isCounterexample)
    (h : G.commonPencil) (hc : G.pencilIsCoordinate) : False

end Published
end KellerGroupoids
