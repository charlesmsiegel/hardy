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


def test_a_stripped_statement_nothing_closes_is_silent(session, fake_lean) -> None:
    assert session._vacuity_probe(SYLOW) == ""


def test_a_vacuity_probe_that_cannot_run_says_so(session, fake_lean) -> None:
    fake_lean.raises = TimeoutError("lean did not start")

    warning = session._vacuity_probe(SYLOW)

    assert "could not be run" in warning


def test_the_vacuity_warning_reaches_the_human(session, approvals, fake_lean) -> None:
    fake_lean.closes_with = "exact ⟨⊥, inferInstance⟩"

    session._tool("request_assumption", _request(lean_statement=SYLOW))

    assert "may be vacuous" in approvals[0]["checked"]
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
