import Mathlib

theorem irrational_sqrt_two_add_sqrt_three : ∀ q : ℚ, (q : ℝ) ≠ Real.sqrt 2 + Real.sqrt 3 :=
by
  intro q h
  have h6 : Irrational (Real.sqrt 6) := by norm_num
  have s2 : Real.sqrt 2 ^ 2 = 2 := Real.sq_sqrt (by norm_num)
  have s3 : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  have hmul : Real.sqrt 2 * Real.sqrt 3 = Real.sqrt 6 := by
    rw [← Real.sqrt_mul (by norm_num : (0:ℝ) ≤ 2)]
    norm_num
  have hsq : (q:ℝ)^2 = 5 + 2 * Real.sqrt 6 := by
    rw [h]; nlinarith [s2, s3, hmul]
  exact h6 ⟨(q^2-5)/2, by push_cast; linarith⟩

#print axioms irrational_sqrt_two_add_sqrt_three
