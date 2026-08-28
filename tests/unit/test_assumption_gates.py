"""What `request_assumption` must settle before a human is asked anything.

The graded run's trap: the model passed a whole `axiom NAME (binders) : ...`
declaration as the *statement*, Hardy wrapped it into
`axiom NAME : axiom NAME (binders) : ...`, and told the model to declare
exactly that. `save_lean` then refused every spelling -- matching the approval
required binders its parser rejects, and satisfying the parser produced a
statement that no longer matched the approval. Ten turns, records 27 through
106, and Lean was abandoned at the end of them.

And nothing ever elaborated the axioms, so `exists a b : G, a * b = b * a`
reached the appendix standing for "abelian". It says some pair commutes, which
holds in every group.
"""

from __future__ import annotations

import importlib

TRIVIAL = "exists a b : G, a * b = b * a"


def _request(**overrides):
    request = {
        "formal_name": "sylow",
        "lean_statement": "True",
        "latex_name": "Sylow",
        "informal_statement": "Sylow's theorems",
        "source": "Dummit and Foote",
        "reason": "not in Mathlib",
    }
    request.update(overrides)
    return request


# --- Shape --------------------------------------------------------------------


def test_a_statement_that_is_itself_a_declaration_is_refused(session) -> None:
    refusal = session._assumption_shape(
        "cyclic_of_prime_order",
        "axiom cyclic_of_prime_order (G : Type*) [Group G] (p : Nat) : True",
    )

    assert refusal is not None
    assert "only the statement" in refusal


def test_the_refusal_names_the_fix(session) -> None:
    refusal = session._assumption_shape("f", "theorem f : True")

    assert refusal is not None
    assert "axiom f :" in refusal


def test_a_second_declaration_smuggled_onto_a_new_line_is_refused(session) -> None:
    """`ASSUMPTION` reads both lines happily, so without this the request
    round-trips and an approval granted for `True` carries `axiom extra`."""
    refusal = session._assumption_shape("f", "True\naxiom extra : False")

    assert refusal is not None
    assert "one line" in refusal


def test_a_statement_carrying_universe_parameters_is_refused(session) -> None:
    """`unreadable_assumptions` is what `save_lean` calls. Asking it here is
    what stops the two ends drifting apart again."""
    assert session._assumption_shape("f.{u}", "Sort u") is not None


def test_a_binder_only_statement_is_not_caught_here(session) -> None:
    """Documented, not hidden. `axiom f : (G : Type*) : True` parses; only
    elaboration can say it is not Lean, and that is the probe's job."""
    assert session._assumption_shape("f", "(G : Type*) : True") is None


def test_an_ordinary_statement_passes(session) -> None:
    assert session._assumption_shape("comm", "forall a b : Nat, a + b = b + a") is None


def test_the_human_is_never_asked_about_a_refused_shape(session, approvals) -> None:
    result = session._tool(
        "request_assumption", _request(lean_statement="axiom f (n : Nat) : True")
    )

    assert not result.ok
    assert approvals == []


# --- Elaboration and triviality ------------------------------------------------


def test_a_statement_lean_proves_is_refused_as_a_theorem(session, fake_lean) -> None:
    fake_lean.closes_with = "exact?"

    refusal, caveat = session._assumption_probe(f"axiom abelian : {TRIVIAL}")

    assert refusal is not None
    assert "theorem, not an assumption" in refusal
    assert caveat == ""


def test_the_proof_is_handed_back(session, fake_lean) -> None:
    """A refusal that leaves the model where it was buys nothing."""
    fake_lean.closes_with = "exact?"
    fake_lean.suggestion = "exact ⟨1, 1, rfl⟩"

    refusal, _ = session._assumption_probe(f"axiom abelian : {TRIVIAL}")

    assert refusal is not None
    assert "exact ⟨1, 1, rfl⟩" in refusal
    assert "save_lean" in refusal


def test_a_probe_with_no_suggestion_names_the_tactic(session, fake_lean) -> None:
    fake_lean.closes_with = "simp"

    refusal, _ = session._assumption_probe("axiom f : True")

    assert refusal is not None
    assert "by simp" in refusal


def test_a_statement_lean_cannot_elaborate_is_refused_with_lean_s_message(
    session, fake_lean
) -> None:
    fake_lean.elaborates = False
    fake_lean.output = "error: unknown identifier 'Sylwo'"

    refusal, _ = session._assumption_probe("axiom f : Sylwo")

    assert refusal is not None
    assert "unknown identifier 'Sylwo'" in refusal


def test_a_genuine_assumption_passes_with_no_caveat(session, fake_lean) -> None:
    refusal, caveat = session._assumption_probe("axiom sylow : True")

    assert refusal is None
    assert caveat == ""


def test_a_probe_that_cannot_run_reaches_the_human_with_a_caveat(session, fake_lean) -> None:
    """Neither silently approving nor refusing everything. A machine that
    cannot run Lean must not be one on which every axiom is waved through, nor
    one on which no work can be done at all."""
    fake_lean.raises = TimeoutError("lean did not start")

    refusal, caveat = session._assumption_probe("axiom sylow : True")

    assert refusal is None
    assert "could not be checked" in caveat


def test_an_error_lean_could_not_place_counts_against_the_declaration(
    session, fake_lean, monkeypatch
) -> None:
    """"No error on that line" must mean the tactic closed the goal, not that
    Hardy could not tell where the error was."""
    from hardy.lean import LeanDiagnostic, LeanToolResult

    def unplaced(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "error: something went wrong",
            source,
            diagnostics=(LeanDiagnostic(severity="error", message="boom", line=None),),
        )

    monkeypatch.setattr(session, "_run_lean_source", unplaced)

    refusal, _ = session._assumption_probe("axiom f : True")

    assert refusal is not None
    assert "does not accept this statement" in refusal


def test_an_error_before_the_probes_is_refused_not_credited_as_a_proof(
    session, fake_lean, monkeypatch
) -> None:
    """`import Mathlib` failing on line 1 leaves every probe line without an
    error of its own, which used to read as every probe having closed the
    goal -- Lean never reached any of them."""
    from hardy.lean import LeanDiagnostic, LeanToolResult

    def stray(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "error: unknown module Mathlib",
            source,
            diagnostics=(
                LeanDiagnostic(severity="error", message="unknown module Mathlib", line=1),
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", stray)

    refusal, _ = session._assumption_probe("axiom f : True")

    assert refusal is not None
    assert "does not accept this statement" in refusal
    assert "proves this outright" not in refusal


def test_the_probe_source_puts_one_example_per_line(session, fake_lean) -> None:
    """The line arithmetic is this layout and nothing else."""
    session._assumption_probe("axiom f : True")

    lines = fake_lean.last_source.splitlines()
    assert lines[0] == "import Mathlib"
    probes = lines[2 : 2 + len(session.PROBES)]
    assert [line.split(" := by ")[1] for line in probes] == list(session.PROBES)
    assert lines[-1] == "axiom f : True"


def test_the_axiom_is_declared_after_the_probes_not_before(session, fake_lean) -> None:
    """Lean resolves names in order, and an axiom in scope answers its own
    question. Declared first, `exact?` closed every statement by citing the
    axiom being proposed -- a live run refused seven honest requests that way,
    Sylow's theorems among them, each "proved" from itself."""
    session._assumption_probe("axiom sylow_first : True")

    source = fake_lean.last_source
    assert source.index("example") < source.index("axiom sylow_first")


def test_a_multiline_statement_is_collapsed_before_probing(session, fake_lean) -> None:
    """Enforced by `_assumption_shape`, and belt-and-braces here: the
    arithmetic breaks silently if a declaration ever spans two lines."""
    session._assumption_probe("axiom f : forall a\n  b : Nat, a = a")

    assert fake_lean.last_source.splitlines()[-1] == "axiom f : forall a b : Nat, a = a"


# --- What reaches the human ----------------------------------------------------


def test_the_caveat_reaches_the_approval_prompt(session, approvals, fake_lean) -> None:
    fake_lean.raises = TimeoutError("lean did not start")

    session._tool("request_assumption", _request())

    assert "could not be checked" in approvals[0]["checked"]


def test_a_checked_assumption_says_so_at_the_prompt(session, approvals, fake_lean) -> None:
    session._tool("request_assumption", _request())

    assert "elaborated" in approvals[0]["checked"]


def test_the_probe_note_is_not_written_into_the_durable_record(
    session, approvals, fake_lean
) -> None:
    """It describes one probe, not the assumption."""
    session._tool("request_assumption", _request())

    stored = session.state["assumptions"][0]
    assert "checked" not in stored
    assert "goal" not in stored
    assert stored["status"] == "user-approved"


def test_a_lean_that_fails_without_readable_diagnostics_is_a_caveat(
    session, monkeypatch
) -> None:
    """Every conclusion the probe draws is from which line an error landed on.
    With no errors to place, "no error on line 5" would read as "`trivial`
    closed the goal" -- an unusable answer turned into a confident refusal."""
    from hardy.lean import LeanToolResult

    def mute(source: str, timeout: float | None = None):
        return LeanToolResult(False, "something went wrong", source, diagnostics=())

    monkeypatch.setattr(session, "_run_lean_source", mute)

    refusal, caveat = session._assumption_probe("axiom f : True")

    assert refusal is None
    assert "could not be checked" in caveat


# --- Vacuity ---


_chat = importlib.import_module("hardy.chat")

SYLOW = (
    "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ) (hprime : Nat.Prime p) "
    "(h_order : p ∣ Fintype.card G), ∃ P : Subgroup G, P.Normal"
)


def test_stripping_drops_prop_hypotheses_and_keeps_data() -> None:
    assert _chat._strip_hypotheses(SYLOW) == (
        "∀ {G : Type*} [Group G] [Fintype G] (p : ℕ), ∃ P : Subgroup G, P.Normal"
    )


def test_stripping_drops_arrow_premises() -> None:
    assert _chat._strip_hypotheses("∀ (n : ℕ), 0 < n → n ≠ 0") == "∀ (n : ℕ), n ≠ 0"


def test_a_binder_typed_by_an_earlier_binder_is_data() -> None:
    assert _chat._strip_hypotheses("∀ (G : Type*) (g : G), g = g") == "∀ (G : Type*) (g : G), g = g"


def test_a_statement_with_nothing_to_strip_is_none() -> None:
    assert _chat._strip_hypotheses("True") is None
    assert _chat._strip_hypotheses(TRIVIAL) is None


def test_a_forall_that_loses_every_binder_becomes_its_body() -> None:
    assert _chat._strip_hypotheses("∀ (h : True), False") == "False"


def test_a_data_binder_the_conclusion_uses_is_kept() -> None:
    """`H` looks like a hypothesis by its own rules -- a `Subgroup G` is not
    a universe, a known data type, or an earlier binder's exact name -- but
    dropping it leaves `H.index` dangling. The conclusion names it, so it is
    data too."""
    statement = (
        "∀ {G : Type*} [Group G] (H : Subgroup G) (hH : H ≠ ⊤), "
        "Nat.card G ∣ (H.index)!"
    )
    assert _chat._strip_hypotheses(statement) == (
        "∀ {G : Type*} [Group G] (H : Subgroup G), Nat.card G ∣ (H.index)!"
    )


def test_a_hypothesis_nothing_mentions_is_still_dropped() -> None:
    statement = (
        "∀ {G : Type*} [Group G] [Finite G] (p : ℕ) [Fact p.Prime] "
        "(P : Sylow p G), (∀ Q : Sylow p G, Q = P) → (P : Subgroup G).Normal"
    )
    assert _chat._strip_hypotheses(statement) == (
        "∀ {G : Type*} [Group G] [Finite G] (p : ℕ) [Fact p.Prime] "
        "(P : Sylow p G), (P : Subgroup G).Normal"
    )


def test_a_string_literal_with_doubled_whitespace_is_unreadable() -> None:
    """A bare `" ".join(statement.split())` collapsed `"a  b"` to `"a b"` --
    a different Lean string reported as if it were the one the statement
    actually has -- and then split the premise on the literal's own
    separator-shaped contents. `normalise_lean` leaves the literal alone;
    this still refuses, because the splits below cannot tell a `, ` or ` → `
    inside the literal from one outside it."""
    assert _chat._strip_hypotheses('∀ (h : "a  b" = "a b"), True') is _chat.UNREADABLE


def test_a_guillemet_name_with_doubled_whitespace_is_unreadable() -> None:
    assert _chat._strip_hypotheses("∀ («weird  name» : ℕ) (h : 0 < 1), True") is _chat.UNREADABLE


def test_doubled_whitespace_outside_any_literal_still_strips() -> None:
    assert _chat._strip_hypotheses("∀ (n : ℕ)  (h : 0 < n),  True") == "∀ (n : ℕ), True"


def test_the_vacuity_probe_is_skipped_when_there_is_nothing_to_strip(session, fake_lean) -> None:
    warning = session._vacuity_probe("True")

    assert warning == ""
    assert fake_lean.sources == []


def test_the_vacuity_file_has_no_declaration_and_one_example_per_line(session, fake_lean) -> None:
    session._vacuity_probe(SYLOW)

    lines = fake_lean.last_source.splitlines()
    assert lines[0] == "import Mathlib"
    assert lines[1] == ""
    assert all(line.startswith("example : ") for line in lines[2:])
    assert not any(line.startswith("axiom") for line in lines)


NOTHING_TO_STRIP_EXISTENTIAL = (
    "∀ {G : Type*} [Group G] (P : Subgroup G), ∃ Q : Subgroup G, Q ≤ P"
)


def test_a_statement_with_nothing_to_strip_gets_no_probes_only_witnesses(session, fake_lean) -> None:
    """Every binder here is data -- `P` is kept because the conclusion
    names it -- so nothing is actually stripped, and `PROBES` would only
    repeat the question `_assumption_probe` already asked against this
    exact text and failed."""
    session._vacuity_probe(NOTHING_TO_STRIP_EXISTENTIAL)

    lines = fake_lean.last_source.splitlines()
    tactics = [line.split(" := by ", 1)[1] for line in lines if line.startswith("example : ")]
    assert not any(tactic in session.PROBES for tactic in tactics)
    assert tactics == list(session.WITNESSES)


def test_a_witness_close_with_nothing_stripped_names_a_theorem_not_a_vacuity(
    session, fake_lean
) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    warning = session._vacuity_probe(NOTHING_TO_STRIP_EXISTENTIAL)

    assert "theorem, not an assumption" in warning
    assert "hypothesis removed" not in warning


def test_a_stripped_statement_a_probe_closes_is_a_warning(session, fake_lean) -> None:
    fake_lean.closes_with = "aesop"

    warning = session._vacuity_probe(SYLOW)

    assert "with every hypothesis removed" in warning
    assert "by aesop" in warning


def test_an_existential_is_tried_with_bottom_and_top(session, fake_lean) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    warning = session._vacuity_probe(SYLOW)

    assert "exact ⟨⊥, inferInstance⟩" in warning


def test_a_non_existential_gets_no_witness_lines(session, fake_lean) -> None:
    session._vacuity_probe("∀ (n : ℕ), 0 < n → n ≠ 0")

    assert "⟨⊥" not in fake_lean.last_source


def test_a_unique_existential_gets_no_witness_lines() -> None:
    """Nit (d), second brutal review: `∃!` is unique existence, and a bare
    `⊥`/`⊤` `WITNESSES` guess can prove something exists but never that it
    is the only one -- trying it against `∃!` can only ever fail."""
    source, tactics = _chat._vacuity_source("∃! n : ℕ, n = 0", include_probes=False)

    assert tactics == []
    assert "⟨⊥" not in source


def test_a_plain_existential_still_gets_witness_lines() -> None:
    source, tactics = _chat._vacuity_source("∃ n : ℕ, n = 0", include_probes=False)

    assert tactics == list(_chat.MathematicsSession.WITNESSES)
    assert "⟨⊥" in source


def test_a_stripped_statement_nothing_closes_is_silent(session, fake_lean) -> None:
    assert session._vacuity_probe(SYLOW) == ""


def test_a_vacuity_probe_that_cannot_run_says_so(session, fake_lean) -> None:
    fake_lean.raises = TimeoutError("lean did not start")

    warning = session._vacuity_probe(SYLOW)

    assert "could not be run" in warning


def test_an_error_before_the_probes_is_not_read_as_the_probe_closing_the_goal(
    session, fake_lean, monkeypatch
) -> None:
    """`import Mathlib` failing on line 1 leaves every `example` line without
    an error of its own, which used to read as the probe having closed the
    goal -- Lean never reached it."""
    from hardy.lean import LeanDiagnostic, LeanToolResult

    def stray(source: str, timeout: float | None = None):
        return LeanToolResult(
            False,
            "error: unknown module Mathlib",
            source,
            diagnostics=(
                LeanDiagnostic(severity="error", message="unknown module Mathlib", line=1),
            ),
        )

    monkeypatch.setattr(session, "_run_lean_source", stray)

    warning = session._vacuity_probe(SYLOW)

    assert "before reaching" in warning


def test_the_vacuity_warning_reaches_the_human(session, approvals, fake_lean) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert "may be vacuous" in approvals[0]["checked"]
    # The elaboration sentence leads; the warning is appended, not swapped in.
    assert approvals[0]["checked"].startswith(
        "Lean elaborated this statement and could not prove it. "
    )
    assert approvals  # warned, not refused


def test_a_caveat_from_the_first_probe_skips_the_second_lean_run(session, approvals, fake_lean) -> None:
    """A caveat already means Lean could not be asked anything, so a second
    full elaboration for the vacuity probe would only spend up to
    PROBE_SECONDS on an answer `caveat or ...` throws away."""
    fake_lean.raises = TimeoutError("lean did not start")

    session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert len(fake_lean.sources) == 1
    assert "could not be checked" in approvals[0]["checked"]


def test_a_whole_statement_close_is_still_a_refusal(session, approvals, fake_lean) -> None:
    fake_lean.closes_with = "simp"

    result = session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert not result.ok
    assert approvals == []


# --- Fail-closed stripping (findings #2 and #3) --------------------------------
#
# `_strip_hypotheses` must never hand the vacuity probe text that is not an
# honest weakening of the statement it was given. A binder `_BINDER` cannot
# fully consume, or a strict-implicit binder it does not know at all, used to
# be silently dropped instead -- garbage text elaborated as though it meant
# something. These pin the four confirmed inputs from finding #3's table, the
# bare-binder case from finding #2, and the two shapes the fix must still get
# right: a colon with no surrounding spaces, and every statement already
# proven to strip correctly before this change.

_DOUBLY_NESTED_HYPOTHESIS = "∀ (n : ℕ) (h : Nat.Prime (Nat.succ (Nat.succ n))), 2 ≤ n + 2"
_STRICT_IMPLICIT_BINDER = (
    "∀ {G : Type*} [Group G] ⦃H : Subgroup G⦄ (hH : H.Normal), Nonempty (G ⧸ H)"
)
_BARE_BINDER = "∀ id : Nat → Nat, (∀ n, id (id n) = n) → ∀ n : Nat, id n = n"


def test_a_binder_too_deeply_nested_for_one_level_is_refused() -> None:
    """`_BINDER`'s paren alternative handles one level of nesting; `h`'s type
    here has two, so the regex matches only the inner `(Nat.succ (Nat.succ
    n))` and leaves `h : Nat.Prime` unconsumed -- exactly the text that used
    to go missing from the stripped statement. The binder list is bracketed
    (`(n : ℕ) (h : ...)`), so this is `UNREADABLE`, not `None`: real
    hypothesis text is being lost, not merely a shape this function never
    parses."""
    assert _chat._strip_hypotheses(_DOUBLY_NESTED_HYPOTHESIS) is _chat.UNREADABLE


def test_a_strict_implicit_binder_is_refused() -> None:
    """`⦃H : Subgroup G⦄` is not a bracket `_BINDER` knows, so `H` used to be
    dropped and left free in the stripped text -- read by Lean's
    `autoImplicit`, not bound by the statement any more."""
    assert _chat._strip_hypotheses(_STRICT_IMPLICIT_BINDER) is _chat.UNREADABLE


def test_a_bare_binder_with_no_bracket_is_refused() -> None:
    """`id : Nat → Nat` -- the ordinary Lean spelling of a binder, with no
    wrapping `(...)`/`{...}` -- matches nothing in `_BINDER`, so the whole
    binder text is unconsumed. Unlike an unparseable bare binder with
    nothing behind it, this one has a genuine top-level premise arrow in its
    body (`(∀ n, …) → ∀ n : Nat, …`), so failing to read it is failing to
    read a real hypothesis, and it is `UNREADABLE`."""
    assert _chat._strip_hypotheses(_BARE_BINDER) is _chat.UNREADABLE


def test_a_bare_typed_binder_with_no_premise_is_nothing_to_strip() -> None:
    """Finding #5 (second brutal review): `n : ℕ` matches nothing in
    `_BINDER` either, but there is no top-level arrow behind it to lose --
    nothing here was ever going to be stripped, so this is `None` (nothing
    to strip), not a fail-closed refusal that throws away Lean's own
    elaboration for an entirely ordinary quantifier."""
    assert _chat._strip_hypotheses("∀ n : ℕ, Nat.Prime (2 ^ (2 ^ n) + 1)") is None


def test_a_bounded_quantifier_with_no_premise_is_nothing_to_strip() -> None:
    """The `∀ x ∈ s, …` row of finding #5's table: a bounded quantifier is
    not binder-list syntax `_BINDER` knows either, and again there is no
    top-level arrow behind it, so this is `None`."""
    assert _chat._strip_hypotheses("∀ x ∈ Set.Icc (0:ℝ) 1, x ≤ 1") is None


def test_an_iff_before_the_first_top_level_arrow_is_refused() -> None:
    """Finding #1 (second brutal review), confirmed against real Lean:
    `↔` binds looser than `→`, so in `A ↔ B → C` the arrow is not a premise
    separator -- it sits inside the equivalence's own right-hand side, `A ↔
    (B → C)`. The old split read it as one anyway and reported `B` as a
    hypothesis of a statement that has none, turning a false axiom into one
    described as merely vacuous."""
    assert _chat._strip_hypotheses("∀ (n : Nat), n = 1 ↔ n ≠ 0 → 0 ≤ n") is _chat.UNREADABLE


def test_an_iff_after_the_first_top_level_arrow_still_strips() -> None:
    """`A → B ↔ C` is `A → (B ↔ C)`: the arrow precedes the `↔`, so it is a
    genuine premise separator and the existing split already gets it
    right."""
    assert _chat._strip_hypotheses("∀ (n : Nat), 0 < n → n = 1 ↔ n ≠ 0") == (
        "∀ (n : Nat), n = 1 ↔ n ≠ 0"
    )


def test_an_arrow_free_iff_with_no_binders_is_nothing_to_strip() -> None:
    """Brutal review pass 3, finding #1: the `arrow_index == -1` disjunct in
    the old guard fired on any `↔`, refusing even a statement with no arrow
    and no binders to mis-split in the first place. `Nat.Prime 7 ↔ True` has
    nothing whose binders could fail to be read, so this is `None`, not
    `UNREADABLE`."""
    assert _chat._strip_hypotheses("Nat.Prime 7 ↔ True") is None


def test_an_arrow_free_iff_with_a_hypothesis_still_strips() -> None:
    """The same regression's binder-bearing case: with no top-level arrow at
    all, there is no premise chain for the `↔` to corrupt, so the ordinary
    hypothesis strip should still run."""
    assert _chat._strip_hypotheses("∀ (n : ℕ) (h : 0 < n), n = 1 ↔ n ≠ 0") == (
        "∀ (n : ℕ), n = 1 ↔ n ≠ 0"
    )


def test_a_colon_with_no_surrounding_spaces_is_still_read() -> None:
    """`(hp:Nat.Prime 2)` used to be read as an untyped, always-kept binder,
    because the old parser looked for a literal `" : "`. The fixed colon
    search finds it and drops `hp` as the hypothesis it is."""
    assert _chat._strip_hypotheses("∀ (n : ℕ) (hp:Nat.Prime 2), True") == "∀ (n : ℕ), True"


def test_an_arrow_inside_a_quantifier_s_own_binder_type_is_not_a_premise() -> None:
    """`∃ f : α → Prop, …` holds an arrow that types `f`, not a premise
    boundary. The old unconditional top-level split cut the statement there
    and threw the `∃` away, leaving a `Prop, ∀ x, …` fragment Lean could not
    parse. Now nothing past the first top-level quantifier in `body` is
    treated as a candidate split point, so `h` is read as the one genuine
    hypothesis and dropped, and the existential survives intact."""
    statement = (
        "∀ {α : Type*} (s : Set α) (h : s.Nonempty), ∃ f : α → Prop, ∀ x, f x ↔ x ∈ s"
    )
    assert _chat._strip_hypotheses(statement) == (
        "∀ {α : Type*} (s : Set α), ∃ f : α → Prop, ∀ x, f x ↔ x ∈ s"
    )


def test_a_colon_with_no_spaces_beside_other_binders_still_strips() -> None:
    """The third row of finding #3's table: with the colon fix, `hp` is read
    correctly and dropped alongside the ordinary bracketed binders `G` and
    `[Group G]`, rather than being wrongly kept as data (the old "unchanged"
    behaviour)."""
    statement = "∀ {G : Type*} [Group G] (hp:Nat.Prime 2), Nonempty G"
    assert _chat._strip_hypotheses(statement) == "∀ {G : Type*} [Group G], Nonempty G"


def test_the_checked_text_for_a_bailed_statement_says_stripping_was_not_attempted(
    session, fake_lean
) -> None:
    warning = session._vacuity_probe(_STRICT_IMPLICIT_BINDER)

    assert "not attempted" in warning
    # Not a vacuity warning: a reader (and a test) must never mistake "the
    # question was never asked" for "the question was asked and is concerning".
    assert "vacuous" not in warning
    assert fake_lean.sources == []


def test_a_statement_with_genuinely_nothing_to_strip_stays_silent(session, fake_lean) -> None:
    """`True` never had a hypothesis to lose; the escape-hatch message is for
    a statement stripping was owed and did not get, not every plain one."""
    assert session._vacuity_probe("True") == ""
    assert fake_lean.sources == []


# --- The four rows of finding #5's table (second brutal review) ---------------
#
# `fake_lean`'s default (`closes_with = None`) elaborates every declaration
# and closes no probe, so each of these reaches `checked` by way of a genuine
# "Lean tried and could not prove it" rather than a caveat or a warning --
# the only thing under test here is whether a strip note is wrongly shown, or
# wrongly withheld.

_ORDINARY_ROW = "∀ (p : ℕ) (hp : Nat.Prime p), p ≠ 1"
_BARE_ROW = "∀ n : ℕ, Nat.Prime (2 ^ (2 ^ n) + 1)"
_BOUNDED_ROW = "∀ x ∈ Set.Icc (0:ℝ) 1, x ≤ 1"
_DOUBLY_NESTED_ROW = "∀ {G : Type*} [Group G] (h : Nat.card (Subgroup.center (G)) = 1), True"

_ELABORATED = "Lean elaborated this statement and could not prove it."


def test_the_four_rows_of_finding_5_show_the_checked_text_the_finding_wants(
    session, approvals, fake_lean
) -> None:
    for index, statement in enumerate(
        (_ORDINARY_ROW, _BARE_ROW, _BOUNDED_ROW, _DOUBLY_NESTED_ROW)
    ):
        result = session._tool(
            "request_assumption",
            _request(formal_name=f"row{index}", latex_name=f"Row{index}", lean_statement=statement),
        )
        assert result.ok

    # Row 1: an ordinary statement that strips cleanly and warns of nothing.
    assert approvals[0]["checked"] == _ELABORATED
    # Rows 2 and 3: bare/bounded binders with no premise to lose -- nothing
    # to strip, so no strip note, exactly as an ordinary `Nat.Prime 7` stays
    # silent.
    assert approvals[1]["checked"] == _ELABORATED
    assert approvals[2]["checked"] == _ELABORATED
    # Row 4: a bracketed binder nested too deeply to read -- real hypothesis
    # text was lost, so the elaboration sentence is followed by the note.
    assert approvals[3]["checked"] == f"{_ELABORATED} {_chat.VACUITY_STRIP_REFUSED}"
