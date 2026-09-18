import Mathlib

/-!
# Keller maps and their fiber configuration spaces: the definitions

Everything here is a definition over Mathlib, with no axioms and no
interfaces. A *polynomial map* of affine `n`-space is an `n`-tuple of
polynomials in `n` variables over `ℂ`; its Jacobian matrix is the matrix of
partial derivatives; it is a *Keller map* when the determinant of that matrix
is a nonzero constant. The fiber product levels `KLevel`, the ordered
configuration spaces `Conf`, the fibers and their cardinalities are all
plain sets of points.

Nothing geometric (smoothness, étaleness, properness, monodromy) is defined
here. Those notions enter through `KellerGroupoids.Interfaces`, as data a
theorem takes, and never as global constants.
-/

set_option autoImplicit false

open MvPolynomial

namespace KellerGroupoids

/-- A point of complex affine `n`-space. -/
abbrev AffinePoint (n : ℕ) := Fin n → ℂ

/-- A polynomial self-map of affine `n`-space: one polynomial per target coordinate. -/
structure PolyMap (n : ℕ) where
  coord : Fin n → MvPolynomial (Fin n) ℂ

namespace PolyMap

variable {n : ℕ}

/-- The map on points. -/
noncomputable def eval (P : PolyMap n) (x : AffinePoint n) : AffinePoint n :=
  fun i => MvPolynomial.eval x (P.coord i)

noncomputable instance : CoeFun (PolyMap n) (fun _ => AffinePoint n → AffinePoint n) := ⟨eval⟩

/-- The Jacobian matrix `∂P_i/∂x_j`, with polynomial entries. -/
noncomputable def jacobian (P : PolyMap n) : Matrix (Fin n) (Fin n) (MvPolynomial (Fin n) ℂ) :=
  fun i j => pderiv j (P.coord i)

/-- The Keller condition: the Jacobian determinant is a nonzero constant. -/
def IsKeller (P : PolyMap n) : Prop :=
  ∃ c : ℂ, c ≠ 0 ∧ P.jacobian.det = C c

end PolyMap

/-- A Keller map: a polynomial self-map with nonzero constant Jacobian determinant. -/
structure KellerMap (n : ℕ) extends PolyMap n where
  keller : toPolyMap.IsKeller

namespace KellerMap

variable {n : ℕ}

noncomputable instance : CoeFun (KellerMap n) (fun _ => AffinePoint n → AffinePoint n) :=
  ⟨fun F => F.toPolyMap.eval⟩

end KellerMap

/-- A plane Keller map. -/
abbrev PlaneKellerMap := KellerMap 2

/-- A tuple with pairwise distinct entries. -/
def PairwiseDistinct {α : Type*} {k : ℕ} (x : Fin k → α) : Prop :=
  ∀ i j, i ≠ j → x i ≠ x j

theorem pairwiseDistinct_iff_injective {α : Type*} {k : ℕ} (x : Fin k → α) :
    PairwiseDistinct x ↔ Function.Injective x := by
  constructor
  · intro h i j hij
    by_contra hne
    exact h i j hne hij
  · intro h i j hne hij
    exact hne (h hij)

variable {n : ℕ}

/-- The `r`-fold self fiber product `K_r(F)`: `r`-tuples of points with one common image.
This is the `r`-th level of the Čech nerve of `F`. -/
def KLevel (F : KellerMap n) (r : ℕ) :=
  {x : Fin r → AffinePoint n // ∀ i j, F (x i) = F (x j)}

/-- The ordered `k`-point fiber configuration variety `Conf_k(F)`: `k` pairwise distinct
points with one common image. -/
def Conf (F : KellerMap n) (k : ℕ) :=
  {x : Fin k → AffinePoint n // PairwiseDistinct x ∧ ∀ i j, F (x i) = F (x j)}

/-- The fiber of `F` over a target point. -/
def Fiber (F : KellerMap n) (y : AffinePoint n) :=
  {x : AffinePoint n // F x = y}

/-- The number of points in a fiber (`0` when the fiber is infinite, by `Nat.card`'s convention). -/
noncomputable def fiberCard (F : KellerMap n) (y : AffinePoint n) : ℕ :=
  Nat.card (Fiber F y)

/-- The target points with at least `k` preimages, `U_{≥k}`. -/
def FiberAtLeast (F : KellerMap n) (k : ℕ) : Set (AffinePoint n) :=
  {y | k ≤ fiberCard F y}

/-- The target points with exactly `m` preimages, `U_m`. -/
def FiberExactly (F : KellerMap n) (m : ℕ) : Set (AffinePoint n) :=
  {y | fiberCard F y = m}

/-- The number of points a permutation moves. -/
def permSupportSize {d : ℕ} (σ : Equiv.Perm (Fin d)) : ℕ :=
  σ.support.card

/-- The falling factorial `m (m-1) ⋯ (m-k+1)`, Mathlib's `Nat.descFactorial`. -/
abbrev fallingFactorial (m k : ℕ) : ℕ := Nat.descFactorial m k

end KellerGroupoids
