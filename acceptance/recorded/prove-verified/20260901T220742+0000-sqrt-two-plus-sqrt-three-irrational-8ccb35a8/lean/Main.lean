import Mathlib

theorem irrational_sqrt_two_add_sqrt_three : Irrational (Real.sqrt 2 + Real.sqrt 3) :=
by
  rintro ⟨q, hq⟩
  have hs2 : Real.sqrt 2 ^ 2 = 2 := Real.sq_sqrt (by norm_num)
  have hs3 : Real.sqrt 3 ^ 2 = 3 := Real.sq_sqrt (by norm_num)
  have hpos : (0:ℝ) < Real.sqrt 2 + Real.sqrt 3 := by positivity
  have hq0 : (q:ℝ) ≠ 0 := by rw [hq]; exact ne_of_gt hpos
  have h3' : Real.sqrt 3 = (q:ℝ) - Real.sqrt 2 := by linarith [hq]
  have hexp : ((q:ℝ) - Real.sqrt 2) ^ 2 = 3 := by rw [← h3']; exact hs3
  have key : (2 * (q:ℝ)) * Real.sqrt 2 = (q:ℝ) ^ 2 - 1 := by nlinarith [hexp, hs2]
  refine irrational_sqrt_two ⟨(q ^ 2 - 1) / (2 * q), ?_⟩
  have : ((((q ^ 2 - 1) / (2 * q) : ℚ)) : ℝ) = ((q:ℝ) ^ 2 - 1) / (2 * (q:ℝ)) := by
    push_cast
    ring
  rw [this]
  field_simp
  linarith [key]

#print axioms irrational_sqrt_two_add_sqrt_three
