import KellerGroupoids.Core

/-!
# The global Euler-deficit identity: the arithmetic core, proved

`KG-E02` says that for a plane Keller map of generic degree `d` with exact
fiber strata `U_m`, `d - 1 = Σ_{m<d} (d - m) χ(U_m)`. Its derivation in the
audit is two lines of arithmetic from two topological inputs: Euler
integration of the fiber cardinality over the source, `Σ_m m χ(U_m) = χ(A²) = 1`,
and additivity of `χ` over the target, `Σ_m χ(U_m) = 1`. The arithmetic is
proved here for any integer-valued `χ`; the two inputs are hypotheses and
remain the geometric content of the item.
-/

set_option autoImplicit false

open Finset

namespace KellerGroupoids
namespace ProjectStatements

theorem euler_deficit_identity (d : ℕ) (χ : ℕ → ℤ)
    (hsource : ∑ m ∈ range (d + 1), (m : ℤ) * χ m = 1)
    (htarget : ∑ m ∈ range (d + 1), χ m = 1) :
    (d : ℤ) - 1 = ∑ m ∈ range d, ((d : ℤ) - m) * χ m := by
  have hfull : ∑ m ∈ range (d + 1), ((d : ℤ) - m) * χ m = (d : ℤ) - 1 := by
    have : ∑ m ∈ range (d + 1), ((d : ℤ) - m) * χ m
        = (d : ℤ) * ∑ m ∈ range (d + 1), χ m - ∑ m ∈ range (d + 1), (m : ℤ) * χ m := by
      rw [mul_sum, ← sum_sub_distrib]
      refine sum_congr rfl fun m _ => ?_
      ring
    rw [this, hsource, htarget, mul_one]
  rw [sum_range_succ] at hfull
  simp only [sub_self, zero_mul, add_zero] at hfull
  exact hfull.symm

end ProjectStatements
end KellerGroupoids
