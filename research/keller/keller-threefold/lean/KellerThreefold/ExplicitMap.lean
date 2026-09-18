import KellerGroupoids.Core

/-!
# The explicit degree-three Keller map: what the kernel can check about it

The manuscript's map `F : A³ → A³` is written out here as three polynomials
over `ℂ`, and the facts about it that are polynomial identities or
evaluations at explicit points are proved:

* `K06-01`: `det JF = -2` (so `F` is a Keller map) and the three-point collision
  `F(0,0,-1/4) = F(1,-3/2,13/2) = F(-1,3/2,13/2) = (-1/4,0,0)`, hence `F` is not injective;
* the binary-cubic identity behind `K06-02`: `[x : 1+xy]` is a projective root of
  `Φ_{F(x,y,z)}(S,T) = 2AS³ - BS²T + 2ST² - CT³`, which is what makes the incidence map
  `A³ → 𝓘` land in the incidence variety;
* the parametrisation of the triple-root curve `Γ` in `K07-01`;
* `K07-07`: at `(A,B,C) = (-8/27, 0, 1)` the elimination cubic factors as
  `-2/27 (2x+3)(4x-3)²` while the fiber has three distinct points, two of them involving `√3`.

Everything else the manuscript claims about `F` (irreducibility of the
discriminant, the fiber census, properness, monodromy) is not here.
-/

set_option autoImplicit false

open MvPolynomial

namespace KellerGroupoids
namespace Threefold

noncomputable section

/-- The three coordinate polynomials of `F`, in the variables `X 0 = x`, `X 1 = y`, `X 2 = z`. -/
def a : MvPolynomial (Fin 3) ℂ :=
  (1 + X 0 * X 1) ^ 3 * X 2 + X 1 ^ 2 * (1 + X 0 * X 1) * (C 4 + C 3 * X 0 * X 1)

def b : MvPolynomial (Fin 3) ℂ :=
  X 1 + C 3 * X 0 * (1 + X 0 * X 1) ^ 2 * X 2 + C 3 * X 0 * X 1 ^ 2 * (C 4 + C 3 * X 0 * X 1)

def c : MvPolynomial (Fin 3) ℂ :=
  C 2 * X 0 - C 3 * X 0 ^ 2 * X 1 - X 0 ^ 3 * X 2

/-- The polynomial map `F = (a, b, c)`. -/
def F0 : PolyMap 3 := ⟨![a, b, c]⟩

/-- The same map on points, written out. -/
def aF (x y z : ℂ) : ℂ := (1 + x * y) ^ 3 * z + y ^ 2 * (1 + x * y) * (4 + 3 * x * y)
def bF (x y z : ℂ) : ℂ := y + 3 * x * (1 + x * y) ^ 2 * z + 3 * x * y ^ 2 * (4 + 3 * x * y)
def cF (x y z : ℂ) : ℂ := 2 * x - 3 * x ^ 2 * y - x ^ 3 * z

theorem eval_F0 (x y z : ℂ) : F0.eval ![x, y, z] = ![aF x y z, bF x y z, cF x y z] := by
  funext i
  fin_cases i <;> simp [F0, PolyMap.eval, a, b, c, aF, bF, cF]

/-! ### K06-01: `det JF = -2` -/

set_option maxRecDepth 4000 in
theorem jacobian_det : F0.jacobian.det = C (-2) := by
  rw [Matrix.det_fin_three]
  simp only [F0, PolyMap.jacobian, a, b, c, Matrix.cons_val_zero, Matrix.cons_val_one,
    Matrix.cons_val_two, Matrix.head_cons, Matrix.tail_cons]
  simp only [map_add, map_sub, Derivation.leibniz, Derivation.leibniz_pow, pderiv_X, pderiv_C]
  simp only [Pi.single_apply, Fin.isValue, Fin.zero_eq_one_iff, Fin.one_eq_zero_iff]
  simp only [map_neg, map_ofNat]
  simp
  ring

/-- `F` is a Keller map: its Jacobian determinant is the nonzero constant `-2`. -/
def F : KellerMap 3 := ⟨F0, -2, by norm_num, jacobian_det⟩

/-! ### K06-01: the three-point collision, hence `F` is not injective -/

theorem collision_one : F ![0, 0, -1/4] = ![-1/4, 0, 0] := by
  show F0.eval _ = _
  rw [eval_F0]; simp [aF, bF, cF]

theorem collision_two : F ![1, -3/2, 13/2] = ![-1/4, 0, 0] := by
  show F0.eval _ = _
  rw [eval_F0]; simp [aF, bF, cF]; norm_num

theorem collision_three : F ![-1, 3/2, 13/2] = ![-1/4, 0, 0] := by
  show F0.eval _ = _
  rw [eval_F0]; simp [aF, bF, cF]; norm_num

theorem not_injective : ¬ Function.Injective (fun p : AffinePoint 3 => F p) := by
  intro h
  have := h (collision_one.trans collision_two.symm)
  have h0 := congrFun this 0
  simp at h0

/-- `F` has a fiber with at least three points: `Conf_3(F)` is nonempty. -/
theorem conf_three_nonempty : Nonempty (Conf F 3) := by
  let p : Fin 3 → AffinePoint 3 := ![![0, 0, -1/4], ![1, -3/2, 13/2], ![-1, 3/2, 13/2]]
  have hall : ∀ i, F (p i) = ![-1/4, 0, 0] := by
    intro i
    fin_cases i
    · exact collision_one
    · exact collision_two
    · exact collision_three
  refine ⟨p, ?_, fun i j => (hall i).trans (hall j).symm⟩
  intro i j hij h
  have h0 := congrFun h 0
  fin_cases i <;> fin_cases j <;> simp [p] at h0 hij <;> norm_num at h0

/-! ### The binary-cubic identity behind `K06-02` -/

/-- `[x : 1 + xy]` is a projective root of the binary cubic `2AS³ - BS²T + 2ST² - CT³` at
`(A,B,C) = F(x,y,z)`. -/
theorem binary_identity (x y z : ℂ) :
    2 * aF x y z * x ^ 3 - bF x y z * x ^ 2 * (1 + x * y) + 2 * x * (1 + x * y) ^ 2
      - cF x y z * (1 + x * y) ^ 3 = 0 := by
  simp only [aF, bF, cF]
  ring

/-- `x` and `1 + xy` never vanish together, so `[x : 1 + xy]` is a point of `P¹`. -/
theorem homogeneous_coordinates_nonzero (x y : ℂ) : x ≠ 0 ∨ 1 + x * y ≠ 0 := by
  by_cases hx : x = 0
  · right; simp [hx]
  · left; exact hx

/-! ### The discriminant and the triple-root curve `Γ` (`K07-01`, the explicit part) -/

/-- The normalised discriminant `Δ` of the target cubic. -/
def Δ (A B C : ℂ) : ℂ := B ^ 2 - 16 * A - B ^ 3 * C + 18 * A * B * C - 27 * A ^ 2 * C ^ 2

/-- The points `(1/(3t²), 2/t, 2t/3)` lie on `V(12A - B², 3BC - 4)`. -/
theorem gamma_parametrisation (t : ℂ) (ht : t ≠ 0) :
    12 * (1 / (3 * t ^ 2)) - (2 / t) ^ 2 = 0 ∧ 3 * (2 / t) * (2 * t / 3) - 4 = 0 := by
  constructor <;> field_simp <;> ring

/-- Points of `Γ` are on the discriminant hypersurface. -/
theorem gamma_on_discriminant (t : ℂ) (ht : t ≠ 0) : Δ (1 / (3 * t ^ 2)) (2 / t) (2 * t / 3) = 0 := by
  simp only [Δ]
  field_simp
  ring

/-! ### K07-07: a coordinate collision in the elimination cubic -/

/-- The signed discriminant `δ = -Δ` at `(-8/27, 0, 1)` is `-64/27`. -/
theorem delta_at_point : -Δ (-8/27) 0 1 = -64/27 := by
  simp only [Δ]; norm_num

/-- At `(A,B,C) = (-8/27, 0, 1)` the elimination cubic `δx³ + (4 - 3BC)x - 2C` is
`-2/27 (2x+3)(4x-3)²`, with the double root `x = 3/4`. -/
theorem elimination_cubic_factors (x : ℂ) :
    (-Δ (-8/27) 0 1) * x ^ 3 + (4 - 3 * 0 * 1) * x - 2 * 1 = -2/27 * (2 * x + 3) * (4 * x - 3) ^ 2 := by
  simp only [Δ]; ring

/-- The fiber over `(-8/27, 0, 1)` contains `p₁ = (-3/2, 4/3, 104/27)`. -/
theorem fiber_point_one : F ![-3/2, 4/3, 104/27] = ![-8/27, 0, 1] := by
  show F0.eval _ = _
  rw [eval_F0]; simp [aF, bF, cF]; norm_num

/-- For either square root `s` of `3`, the point `(3/4, -2/3 + 2s/3, 104/27 - 8s/3)` is in the
fiber over `(-8/27, 0, 1)`. Two values of `s` give two distinct fiber points over the same
double affine root `x = 3/4`. -/
theorem fiber_point_sqrt (s : ℂ) (hs : s ^ 2 = 3) :
    F ![3/4, -2/3 + 2 * s / 3, 104/27 - 8 * s / 3] = ![-8/27, 0, 1] := by
  show F0.eval _ = _
  rw [eval_F0]
  ext i
  fin_cases i
  · simp [aF]
    linear_combination (-4 * (2 * s + 3) / 27) * hs
  · simp [bF]
    linear_combination (-4/3 : ℂ) * hs
  · simp [cF]
    try ring

/-- The two square-root points are distinct, and distinct from `p₁`: three points in one fiber. -/
theorem three_points_distinct (s : ℂ) (hs : s ^ 2 = 3) :
    (![3/4, -2/3 + 2 * s / 3, 104/27 - 8 * s / 3] : AffinePoint 3) ≠ ![3/4, -2/3 - 2 * s / 3, 104/27 + 8 * s / 3]
      ∧ (![-3/2, 4/3, 104/27] : AffinePoint 3) ≠ ![3/4, -2/3 + 2 * s / 3, 104/27 - 8 * s / 3] := by
  have hs0 : s ≠ 0 := by
    intro h; rw [h] at hs; norm_num at hs
  constructor
  · intro h
    have h1 := congrFun h 1
    simp at h1
    apply hs0
    linear_combination (3/4 : ℂ) * h1
  · intro h
    have h0 := congrFun h 0
    norm_num at h0

end

end Threefold
end KellerGroupoids
